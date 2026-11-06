# -*- coding: utf-8 -*-
"""
Mac mini 上で毎朝 06:05（launchd）に動かすローカル実行エントリ。

方針（社長の決定）:
- APIキーは GitHub Secrets に置かない。macOS キーチェーンから読む（ソース・設定・ログ・GitHub には保存しない）。
- キーが無ければ、キーの取得を試みず安全に停止する（終了コード 78）。badspi（キー不要）は継続する。
- 既存の朝配信（badminton_alerts / GitHub Actions）とは独立。既存の状態・ランキング・workflowには触れない。
- 二重起動は排他ロック（flock）で防ぐ。当日分の結果取得が成功済みなら再取得しない（API枠の節約）。
- badspi は週1回（7日以上古い、または未取得のとき）だけ取得する。
- 日本人ID台帳の検索は1日の上限（ledger の SEARCH_DAILY_BUDGET）内で行う。確定は人が行う。
- Discord は config の discord_output_enabled（既定 false）に従う。このランナーは Discord の認証情報を読まない。

終了コード: 0 正常 / 1 失敗 / 75 二重起動で未実行 / 78 APIキー未設定（停止）
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import badminton_results_free as R  # noqa: E402
import badminton_jp_ledger as L  # noqa: E402
import badspi_ranking_snapshot as BS  # noqa: E402
import hashlib  # noqa: E402
import badminton_handoff as BH  # noqa: E402

KEYCHAIN_SERVICE = "ricky-kogyo-badminton-api-free"
KEYCHAIN_ACCOUNT = "BADMINTON_API_KEY"
DEFAULT_LOCAL_DIR = Path.home() / "Library" / "Application Support" / "ricky-kogyo" / "badminton"
BADSPI_INTERVAL_DAYS = 7
EX_OK, EX_FAIL, EX_LOCKED, EX_NO_KEY = 0, 1, 75, 78


def read_keychain_key(service: str = KEYCHAIN_SERVICE, account: str = KEYCHAIN_ACCOUNT,
                      runner=subprocess.run) -> str | None:
    """macOS キーチェーンから読む。失敗・未登録は None。標準エラーや値はどこにも出力しない。"""
    try:
        r = runner(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                   capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    key = (r.stdout or "").strip()
    return key or None


class RunLock:
    """二重起動防止。取得できなければ None。解放は OS がプロセス終了時に行う。"""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            return None
        return self

    def __exit__(self, *exc):
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
        return False


def results_already_ok_today(state_path: Path, api_day_utc: str) -> bool:
    """同じ取得対象日（UTC）の取得が成功済みか。成功済みなら再取得しない（API枠の節約・二重投稿の防止）。"""
    try:
        state = R.load_state(state_path)
    except R.StateError:
        return False
    return any(run.get("status") == "ok" and run.get("api_day_utc") == api_day_utc
               for run in state.get("runs", []))


def badspi_due(snapshot_dir: Path, now_utc: dt.datetime, days: int = BADSPI_INTERVAL_DAYS) -> bool:
    """いずれかの種目で、完全版が無い、または最新の取得が days 日より古ければ True（週1回の更新）。"""
    latest = BS.load_latest_rankings(snapshot_dir)
    for code in BS.DISCIPLINE_NAMES:
        snap = latest.get(code)
        if snap is None:
            return True
        fetched = R.parse_iso(snap.get("fetched_at"))
        if fetched is None or now_utc - fetched.astimezone(dt.timezone.utc) >= dt.timedelta(days=days):
            return True
    return False


def search_step(local_dir: Path, api_key: str, *, fetch_search, now_utc: dt.datetime,
                sleep=time.sleep) -> dict:
    """台帳の候補をAPI検索で照合する（確定はしない）。日次上限・再開は ledger 側の仕組みに従う。"""
    cand_path = local_dir / "badminton_jp_candidates.json"
    if not cand_path.exists():
        repo_copy = ROOT / "config" / "badminton_jp_candidates.json"
        if not repo_copy.exists():
            return {"status": "no_candidates"}
        cand_path.write_text(repo_copy.read_text(encoding="utf-8"), encoding="utf-8")
    doc = json.loads(cand_path.read_text(encoding="utf-8"))
    result = L.search_candidates(doc, api_key, fetch_search, sleep=sleep,
                                 today_utc=now_utc.date().isoformat())
    L._write_json(cand_path, doc)
    L.REVIEW_PATH.write_text(L.review_markdown(doc), encoding="utf-8")
    L.QUEUE_PATH.write_text(L.confirm_queue_markdown(doc), encoding="utf-8")
    return result


PUBLISH_SCRIPT = ROOT / "scripts" / "publish_badminton_handoff.sh"
PUBLISH_LOG = "publish.log"


def _redact(text: str, env: dict) -> str:
    """ログに鍵の場所（パス）を出さない。"""
    key = (env or {}).get("BADMINTON_DATA_DEPLOY_KEY", "")
    return text.replace(key, "<DEPLOY_KEY>") if key else text


def publish_handoff(*, outbox_path: Path, local_dir: Path, config: dict, now_utc: dt.datetime,
                    env: dict, runner=subprocess.run) -> str:
    """結果JSONを専用ブランチへ公開する（既定は無効）。戻り値は判定理由または 'published' / 'failed'。
    失敗は隠さず publish.log に残す。公開済みの内容は再公開しない（内容ハッシュで判定）。"""
    try:
        payload, _ = BH.read_payload(outbox_path)
        data = outbox_path.read_bytes() if outbox_path.exists() else b""
    except OSError:
        payload, data = None, b""
    digest = hashlib.sha256(data).hexdigest() if data else ""
    marker = local_dir / "publish_last.json"
    last = None
    try:
        last = json.loads(marker.read_text(encoding="utf-8")).get("digest")
    except (OSError, ValueError, AttributeError):
        last = None
    decision = BH.publish_decision(payload, now_utc, enabled=config.get("data_publish_enabled") is True,
                                   key_present=bool(env.get("BADMINTON_DATA_DEPLOY_KEY")),
                                   last_digest=last, digest=digest)
    if decision != "publish":
        print(f"[PUBLISH] 公開しません（{decision}）")
        return decision
    (local_dir / "logs").mkdir(parents=True, exist_ok=True)
    try:
        r = runner(["bash", str(PUBLISH_SCRIPT), str(outbox_path)], env=env,
                   capture_output=True, text=True, timeout=180)
        out = _redact((r.stdout or "") + (r.stderr or ""), env)
    except (OSError, subprocess.SubprocessError) as err:
        out, r = f"実行できませんでした（{type(err).__name__}）", None
    with open(local_dir / "logs" / PUBLISH_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{now_utc.isoformat()} rc={getattr(r, 'returncode', 'n/a')}\n{out}\n")
    if r is not None and r.returncode == 0:
        marker.write_text(json.dumps({"digest": digest, "published_at": now_utc.isoformat()}), encoding="utf-8")
        print("[PUBLISH] 公開しました")
        return "published"
    print(f"[PUBLISH][ERROR] 公開に失敗しました（{PUBLISH_LOG} を参照。Discord送信には影響しません）")
    return "failed"


def run_local(*, local_dir: Path, env: dict, now_utc: dt.datetime | None = None,
              keychain=None, fetch=None, post=None, fetch_search=None, badspi_fetch=None,
              sleep=time.sleep) -> int:
    # 既定の実装は呼び出し時に解決する（テストで差し替え可能にするため）
    keychain = keychain or read_keychain_key
    fetch = fetch or R.fetch_page
    post = post or requests.post
    fetch_search = fetch_search or L.fetch_search
    badspi_fetch = badspi_fetch or BS.fetch_html
    now_utc = (now_utc or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    local_dir.mkdir(parents=True, exist_ok=True)
    # 台帳の生成物は、このローカルディレクトリに閉じる（リポジトリ内 config には書かない）
    L.CANDIDATES_PATH = local_dir / "badminton_jp_candidates.json"
    L.REVIEW_PATH = local_dir / "badminton_jp_candidates_review.md"
    L.QUEUE_PATH = local_dir / "badminton_jp_confirm_queue.md"
    with RunLock(local_dir / "run.lock") as lock:
        if lock is None:
            print("[SKIP] 別の実行が進行中のため、今回は実行しません（二重起動の防止）")
            return EX_LOCKED

        snap_dir = local_dir / "badspi_ranking"
        state_path = local_dir / "badminton_results_state.json"
        roster_path = local_dir / "badminton_jp_players.json"
        config = R.load_config()
        # badspi の取得URLは別キーのため、設定ファイルから直接読む（R.load_config は既定キーのみを返す）
        badspi_urls = json.loads(R.CONFIG_PATH.read_text(encoding="utf-8")).get("badspi_ranking_urls", {})
        rc = EX_OK

        # 1) 試合結果（キーが必要）
        api_key = keychain()
        if not api_key:
            print("[STOP] キーチェーンに BADMINTON_API_KEY が無いため、試合結果の取得を停止します。"
                  "キーの取得や登録は行っていません。")
            rc = EX_NO_KEY
        else:
            window = R.compute_window(now_utc)
            if results_already_ok_today(state_path, window["api_day_utc"]):
                print(f"[SKIP] 取得対象日 UTC {window['api_day_utc']} は取得済みのため、再取得しません")
            else:
                roster = R.load_roster(roster_path)
                # 週次のランキングは配信に添える。保存済みのものを参照する
                rankings = BS.load_latest_rankings(snap_dir)
                # Discord の認証情報はこのランナーでは読まない（webhook は空）。出力は設定（既定 OFF）に従う
                r1 = R.run_once(api_key=api_key, webhook="",
                                now_utc=now_utc, state_path=state_path, config=config, roster=roster,
                                fetch=fetch, post=post, sleep=sleep, event="schedule", rankings=rankings,
                                handoff_path=local_dir / "outbox" / BH.DATA_FILENAME)
                rc = max(rc, r1)
                # 試合結果JSONの公開（この実行で生成された新しいJSONだけ。既定は無効）
                pub = publish_handoff(outbox_path=local_dir / "outbox" / BH.DATA_FILENAME, local_dir=local_dir,
                                      config=config, now_utc=now_utc, env=env)
                if pub == "failed":
                    rc = max(rc, EX_FAIL)
            # 2) 台帳の照合（確定はしない・日次上限あり）
            s = search_step(local_dir, api_key, fetch_search=fetch_search, now_utc=now_utc, sleep=sleep)
            print(f"[INFO] 台帳の照合: {s}")

        # 3) badspi（キー不要・週1回）
        if badspi_due(snap_dir, now_utc):
            print("[INFO] badspi 世界ランキングを取得します（週1回の更新）")
            if BS.run_snapshot(urls=badspi_urls, snapshot_dir=snap_dir,
                               fetch=badspi_fetch, now_utc=now_utc) != 0:
                rc = max(rc, EX_FAIL)
        else:
            print("[SKIP] badspi は直近7日以内に取得済みのため、取得しません")
        return rc


def check() -> int:
    """環境の自己確認。ネットワーク・キーチェーン・Discord には触れない。"""
    from importlib.metadata import version
    print(f"OK: python={sys.version.split()[0]} requests={version('requests')}")
    return EX_OK


def main(argv: list[str] | None = None) -> int:
    if argv and argv[0] == "--check":
        return check()
    local_dir = Path(os.environ.get("BADMINTON_LOCAL_DIR", "").strip() or DEFAULT_LOCAL_DIR)
    return run_local(local_dir=local_dir, env=dict(os.environ))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
