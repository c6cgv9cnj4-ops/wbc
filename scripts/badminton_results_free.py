# -*- coding: utf-8 -*-
"""
日本人選手の前日試合結果を #推し（Discord）へ独立配信するモジュール（新規・既存とは独立）。

確定仕様の要点:
- 取得元は Badminton API Free の /v1/results/yesterday（UTC基準の前日）。
- 取得: タイムアウト・HTTP 5xx のみ最大3回リトライ。401/403 等はリトライしない。
  取得失敗（0件ではない）のときは既存データを一切更新しない（prune も行わない）。
- 日本人判定は選手IDのみ。JP_CONFIRMED / NON_JP_CONFIRMED / UNKNOWN の3値。
  JP_CONFIRMED / NON_JP_CONFIRMED は出典URL・確認者・確認日が揃う場合のみ（欠ければ UNKNOWN）。
- 日付: 予定実行（schedule）が UTC 日付をまたいで遅延した場合、予定対象日を確定できないため
  その取得分は日付未確定（unconfirmed）として保存し、通知しない。欠落期間をヘッダーに明示する。
  試合の開始日時が取得窓の日付と一致しないものは outside、読めないものは unknown。
- 通知: 送信「前」に attempt を保存し、送信後に結果を保存する。結果が不明（例外・送信後の中断）の
  試合は次回再送する（通知漏れ防止を優先）。再試行履歴に「重複の可能性」を残す。
- Discord出力は config/badminton_results.json の discord_output_enabled（既定 false）。OFF中は送信も通知済み化もしない。
- 保存: 専用ブランチの checkout 配下（BADMINTON_STATE_PATH）。persisted_at が読めない試合は削除せず quarantine に隔離。
- 訂正（同一ID・内容変更）は別バージョンとして保存し自動再送しない。correction_pending は
  人が --ack-correction で解除する。
- APIキー（BADMINTON_API_KEY）が無ければ、警告を出して何もせず終了する。
- 既存の scripts/fetch_badminton_alerts.py や既存の状態ファイルには依存しない。
- 文書確認（OpenAPI・マニュアル、2026-11-06）と未確認事項:
  - /v1/results/yesterday はパラメータなし。UTC の前日の結果。ページングは next_cursor を cursor で渡し null で終了（一覧の共通仕様）
  - discipline は MS/WS/MD/WD/XD。side1/side2 は「players and seed」（キー名は未記載）。winner は「1 or 2、または選手」（型未記載）
  - start の形式・タイムゾーンは未記載 → タイムゾーンの無い値は日付不明（通知対象外）
  - 実データでの検証は未実施（APIキー未設定）
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

from badspi_ranking_snapshot import DISCIPLINE_NAMES as RANK_DISCIPLINES
from badspi_ranking_snapshot import find_rank, load_latest_rankings
import badminton_handoff as H

API_BASE = "https://api.badmintonapi.com"
RESULTS_YESTERDAY_PATH = "/v1/results/yesterday"
SOURCE_DOC_URL = "https://badmintonapi.com/documentation"
REQUEST_TIMEOUT = 20
DISCORD_BATCH = 10
RETRY_MAX = 3
RETRY_WAIT_SEC = (2, 4, 8)
SCHEDULE_HOUR_UTC = 21   # JST 06:05 = UTC 21:05
SCHEDULE_MINUTE_UTC = 5

JST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "badminton_results.json"
ROSTER_PATH = ROOT / "config" / "badminton_jp_players.json"
# 状態ファイルは専用ブランチの checkout 配下。workflow が BADMINTON_STATE_PATH で指定する。
DEFAULT_STATE_PATH = ROOT / "state_branch" / "state" / "badminton_results_state.json"

JP_CONFIRMED = "JP_CONFIRMED"
NON_JP_CONFIRMED = "NON_JP_CONFIRMED"
UNKNOWN = "UNKNOWN"
JP_LABEL = {
    JP_CONFIRMED: "日本人・確認済",
    NON_JP_CONFIRMED: "非日本人・確認済",
    UNKNOWN: "判定不能",
}

# API側の種目表記 → 配信用表記。未知の表記は対象外（推測しない）。
DISCIPLINE_MAP = {
    "MS": "男子シングルス", "WS": "女子シングルス",
    "MD": "男子ダブルス", "WD": "女子ダブルス", "XD": "混合ダブルス",
    "男子シングルス": "男子シングルス", "女子シングルス": "女子シングルス",
    "男子ダブルス": "男子ダブルス", "女子ダブルス": "女子ダブルス", "混合ダブルス": "混合ダブルス",
}
TARGET_DISCIPLINES = {"男子シングルス", "女子シングルス", "男子ダブルス", "女子ダブルス", "混合ダブルス"}

# 内容の同一性判定に使う項目（訂正検知）。選手は表示名ではなくIDで比較する。
CONTENT_KEYS = ("discipline", "side1", "side2", "games", "winner", "status", "start")

# 文書（OpenAPI・マニュアル 2026-11-06）で確認できなかった項目。実データ（APIキー取得後）まで推測で補わない。
# 形式が想定と違う試合は正規化されず通知されない（推測して埋めない）。
UNVERIFIED_API_ITEMS = (
    "start: 形式・タイムゾーン未記載（タイムゾーン無しは日付不明＝通知対象外）",
    "side1/side2: キー名（players・id・name）未記載（想定。不一致は正規化不可＝通知しない）",
    "winner: 型未記載（整数の 1 / 2 のみ採用。それ以外は未確定と表示）",
    "games: 型未記載（整数2要素の配列のみ採用。欠損は取得不可と表示）",
    "status: 値の意味未記載（保存のみ。判定には使わない）",
    "ページング: next_cursor→cursor は一覧の共通仕様から推定（実データ未検証）",
)

DEFAULT_CONFIG = {
    "discord_output_enabled": False,
    "backlog_days": 3,
    "max_backlog_per_run": 30,
    "state_retention_days": 30,
    "run_log_keep": 90,
    "max_api_pages": 5,
    "api_min_interval_sec": 6.0,
    "data_publish_enabled": False,  # 専用ブランチへの自動公開（Deploy Key の範囲をブランチ制限で証明するまで False）
    "api_format_verified": False,  # 実API応答で形式を確認するまで False（False の間は試合を書き出さない）
}

# Discord 送信はこのジョブでは行わない（投稿は既存 badminton_alerts.yml の1本に統一）。
# この定数を True にする変更は、既存配信との二重投稿防止が証明されるまで禁止。
DISCORD_DELIVERY_ENABLED = False


# ------------------------------------------------------------
# 0. 設定
# ------------------------------------------------------------
def load_config(path: Path = CONFIG_PATH) -> dict:
    """設定を読む。読めない場合は既定値（Discord OFF）。"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return cfg
    if isinstance(data, dict):
        for key in DEFAULT_CONFIG:
            if key in data:
                cfg[key] = data[key]
    cfg["discord_output_enabled"] = cfg["discord_output_enabled"] is True  # true のときだけ有効
    return cfg


# ------------------------------------------------------------
# 1. 取得窓と予定実行のずれ
# ------------------------------------------------------------
def compute_window(run_utc: dt.datetime) -> dict:
    """実行時刻から、/v1/results/yesterday が返す UTC 日と JST 換算期間を返す（API日付は実行時点のサーバー時刻に依存）。"""
    api_day_utc = run_utc.astimezone(UTC).date() - dt.timedelta(days=1)
    start_utc = dt.datetime(api_day_utc.year, api_day_utc.month, api_day_utc.day, tzinfo=UTC)
    end_utc = start_utc + dt.timedelta(days=1)
    return {
        "api_day_utc": api_day_utc.isoformat(),
        "utc_range": [start_utc.isoformat(), end_utc.isoformat()],
        "jst_range": [
            start_utc.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
            (end_utc.astimezone(JST) - dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"),
        ],
    }


def schedule_drift(now_utc: dt.datetime, event: str) -> dict:
    """予定実行（schedule）の予定時刻を求め、UTC日付をまたいで遅延したかを判定する。
    手動実行（workflow_dispatch 等）は予定時刻を持たないため、ずれの判定対象外。
    予定時刻は now に最も近い 21:05 UTC（早着でも前日扱いにしない）。
    注意: 判定は「UTC日付をまたいだか」で行う（仕様の3時間より厳しい。またぐには約2時間55分以上の遅延が必要）。"""
    now_utc = now_utc.astimezone(UTC)
    base = now_utc.replace(hour=SCHEDULE_HOUR_UTC, minute=SCHEDULE_MINUTE_UTC, second=0, microsecond=0)
    candidates = [base - dt.timedelta(days=1), base, base + dt.timedelta(days=1)]
    slot = min(candidates, key=lambda c: abs((now_utc - c).total_seconds()))
    intended_api_day = slot.date() - dt.timedelta(days=1)
    crossed = now_utc.date() != slot.date()
    return {
        "slot_utc": slot.isoformat(),
        "delay_min": int((now_utc - slot).total_seconds() // 60),
        "intended_api_day_utc": intended_api_day.isoformat(),
        "shifted": event == "schedule" and crossed,
    }


# ------------------------------------------------------------
# 2. 取得（リトライ・ページング・0件と失敗の区別）
# ------------------------------------------------------------
def classify_response(status_code: int, payload) -> tuple[str, object]:
    """戻り値: ("ok", items) / ("empty", []) / ("error", 理由)"""
    if status_code != 200:
        hint = "（キー不正・権限の可能性）" if status_code in (401, 403) else ""
        return "error", f"HTTP {status_code}{hint}"
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return "error", "レスポンス形式が想定と異なる（data が配列ではない）"
    items = payload["data"]
    if not items:
        return "empty", []
    return "ok", items


def next_cursor(payload) -> str | None:
    """次ページのカーソル。文書上の meta.next_cursor のみを見る（実データ未確認）。"""
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if isinstance(meta, dict):
        value = meta.get("next_cursor")
        if isinstance(value, str) and value:
            return value
    return None


def _fetch_with_retry(api_key, cursor, fetch, sleep):
    """タイムアウト・接続失敗・HTTP 5xx のみ最大 RETRY_MAX 回再試行する。401/403 等は即失敗。
    戻り値: (code, payload, 通信例外名 or None, 呼び出し回数)"""
    attempts = 0
    while True:
        try:
            code, payload = fetch(api_key, cursor)
            err_name = None
        except (requests.Timeout, requests.ConnectionError) as err:
            code, payload, err_name = None, None, type(err).__name__
        transient = err_name is not None or (code is not None and code >= 500)
        if not transient or attempts >= RETRY_MAX:
            return code, payload, err_name, attempts + 1
        sleep(RETRY_WAIT_SEC[attempts])
        attempts += 1


def fetch_all_pages(api_key: str, fetch, cfg: dict, sleep=time.sleep) -> tuple[str, list, str | None, int]:
    """全ページを取得する。戻り値: (status, items, 失敗理由, API呼び出し回数（再試行を含む）)"""
    items: list = []
    cursor = None
    calls = 0
    max_pages = int(cfg["max_api_pages"])
    for page in range(max_pages):
        if page > 0:
            sleep(float(cfg["api_min_interval_sec"]))  # 10回/分の制限を守る
        code, payload, err_name, n = _fetch_with_retry(api_key, cursor, fetch, sleep)
        calls += n
        if err_name is not None:
            return "error", [], f"通信エラー（{err_name}、再試行{RETRY_MAX}回後も失敗）", calls
        status, data = classify_response(code, payload)
        if status == "error":
            return "error", [], str(data), calls
        if status == "ok":
            items.extend(data)
        nxt = next_cursor(payload)
        if not nxt:
            return ("ok" if items else "empty"), items, None, calls
        cursor = nxt
    # ページ上限に達しても次ページがある → 取得不完全。0件扱いにも、部分成功にもしない。
    return "error", [], f"ページ上限{max_pages}回に達したため取得が不完全", calls


def fetch_page(api_key: str, cursor: str | None) -> tuple[int, object]:
    """文書（OpenAPI・マニュアル）では /v1/results/yesterday はパラメータなし。
    per_page 等を送ると 422 unknown_parameter になり得るため、初回は無パラメータで呼ぶ。
    next_cursor が返った場合のみ cursor を付ける（一覧の共通ページング仕様。実データ未検証）。"""
    params: dict = {}
    if cursor:
        params["cursor"] = cursor
    resp = requests.get(
        API_BASE + RESULTS_YESTERDAY_PATH,
        headers={"Authorization": f"Bearer {api_key}"},
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return resp.status_code, payload


# ------------------------------------------------------------
# 3. 試合の正規化（欠損は None。推測で埋めない）
# ------------------------------------------------------------
def _players_of(side) -> list[dict] | None:
    if not isinstance(side, dict):
        return None
    players = side.get("players")
    if not isinstance(players, list) or not players:
        return None
    out = []
    for p in players:
        if not isinstance(p, dict) or not p.get("id"):
            return None
        out.append({"id": str(p["id"]), "name": str(p.get("name") or "")})
    return out


def _games_of(item) -> list[list[int]] | None:
    games = item.get("games")
    if not isinstance(games, list):
        return None
    out = []
    for g in games:
        if (isinstance(g, list) and len(g) == 2
                and all(isinstance(x, int) and not isinstance(x, bool) for x in g)):
            out.append([g[0], g[1]])
        else:
            return None
    return out or None


def normalize_match(item) -> dict | None:
    """必須項目（ID・種目・両サイドの選手ID）が揃わない試合は None。"""
    if not isinstance(item, dict) or not item.get("id"):
        return None
    discipline = DISCIPLINE_MAP.get(str(item.get("discipline", "")).strip())
    side1 = _players_of(item.get("side1"))
    side2 = _players_of(item.get("side2"))
    if discipline is None or side1 is None or side2 is None:
        return None
    tournament = item.get("tournament") if isinstance(item.get("tournament"), dict) else {}
    winner = item.get("winner")
    start = item.get("start") if isinstance(item.get("start"), str) else None
    return {
        "id": str(item["id"]),
        "tournament": {"name": str(tournament.get("name") or "") or None, "year": tournament.get("year")},
        "discipline": discipline,
        "side1": side1,
        "side2": side2,
        "games": _games_of(item),
        "winner": winner if winner in (1, 2) else None,
        "status": item.get("status") if isinstance(item.get("status"), str) else None,
        "start": start,
    }


def parse_start(value) -> dt.datetime | None:
    """開始日時を UTC の aware datetime として返す。
    - "Z" 付き・UTCオフセット付きは受け付ける。
    - タイムゾーンが無い日時は None（UTCとは決めつけない。文書は start の形式・タイムゾーンを明記していない）。
    - 文字列でない・読めない値は None。
    戻り値が None の試合は日付不明（通知対象外）として扱う。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


# ------------------------------------------------------------
# 4. 日本人判定（3値・選手IDのみ・根拠必須）
# ------------------------------------------------------------
def load_roster(path: Path = ROSTER_PATH) -> dict:
    """{player_id: {"name", "status", "source_url", "confirmed_by", "confirmed_at"}}。
    JP_CONFIRMED / NON_JP_CONFIRMED のどちらも、出典URL・確認者・確認日が揃わない行は載せない（＝UNKNOWN）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    players = data.get("players") if isinstance(data, dict) else None
    if not isinstance(players, list):
        return {}
    roster = {}
    for p in players:
        if not isinstance(p, dict):
            continue
        pid, status = p.get("player_id"), p.get("status")
        if not pid or status not in (JP_CONFIRMED, NON_JP_CONFIRMED):
            continue
        source_url = p.get("source_url")
        confirmed_by = p.get("confirmed_by")
        confirmed_at = p.get("confirmed_at")
        if not (isinstance(source_url, str) and source_url.startswith("http")
                and isinstance(confirmed_by, str) and confirmed_by.strip()
                and isinstance(confirmed_at, str) and confirmed_at.strip()):
            continue  # 根拠が欠ける → UNKNOWN
        roster[str(pid)] = {"name": str(p.get("name") or ""), "status": status,
                            "source_url": source_url, "confirmed_by": confirmed_by,
                            "confirmed_at": confirmed_at}
    return roster


def judge_side(players: list[dict], roster: dict) -> list[dict]:
    """サイドの構成選手ごとに判定を付ける。未登録・根拠不足は UNKNOWN。"""
    judged = []
    for p in players:
        entry = roster.get(p["id"])
        judged.append({"id": p["id"], "name": p["name"],
                       "status": entry["status"] if entry else UNKNOWN})
    return judged


def classify_match(entry: dict, roster: dict) -> str:
    """試合の区分: "jp"（日本人確認済みを含む） / "non_jp"（全員が非日本人確認済み） / "unknown"（それ以外）"""
    statuses = [p["status"] for p in judge_side(entry["side1"], roster) + judge_side(entry["side2"], roster)]
    if JP_CONFIRMED in statuses:
        return "jp"
    if statuses and all(s == NON_JP_CONFIRMED for s in statuses):
        return "non_jp"
    return "unknown"


def is_notifiable(entry: dict, roster: dict) -> bool:
    """通知対象: 日本人確認済みを含み、かつ日付が取得窓と確認できたもの。"""
    return entry.get("date_status") == "confirmed" and classify_match(entry, roster) == "jp"


# ------------------------------------------------------------
# 5. 記録（内容ハッシュ・日付判定）
# ------------------------------------------------------------
def content_hash(rec: dict) -> str:
    payload = {k: rec.get(k) for k in CONTENT_KEYS}
    for side in ("side1", "side2"):
        payload[side] = [p["id"] for p in (rec.get(side) or [])]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def date_status_for(start_value, api_day_utc: str | None) -> str:
    if api_day_utc is None:
        return "unconfirmed"          # 予定対象日を確定できない
    start = parse_start(start_value)
    if start is None:
        return "unknown"              # 開始日時が読めない
    return "confirmed" if start.date().isoformat() == api_day_utc else "outside"


def build_record(norm: dict, api_day_utc: str | None) -> dict:
    """保存用の最小限の記録。生の応答は保存しない。"""
    def _side(side):
        return [{"id": p["id"], "name": p["name"]} for p in side]
    rec = {
        "id": norm["id"],
        "api_day_utc": api_day_utc,
        "date_status": date_status_for(norm.get("start"), api_day_utc),
        "tournament": norm["tournament"],
        "discipline": norm["discipline"],
        "side1": _side(norm["side1"]),
        "side2": _side(norm["side2"]),
        "games": norm["games"],
        "winner": norm["winner"],
        "status": norm["status"],
        "start": norm.get("start"),
    }
    rec["content_hash"] = content_hash(rec)
    return rec


# ------------------------------------------------------------
# 6. 状態（保存・読み込み・取り込み・整理・訂正の解除）
# ------------------------------------------------------------
class StateError(RuntimeError):
    pass


def empty_state() -> dict:
    return {"schema_version": 2, "matches": {}, "quarantine": {}, "runs": []}


def load_state(path: Path) -> dict:
    if not path.exists():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise StateError(f"状態ファイルを読めません（上書きしません）: {err}") from err
    if not isinstance(data, dict) or not isinstance(data.get("matches"), dict):
        raise StateError("状態ファイルの形式が想定と異なります（上書きしません）")
    data.setdefault("runs", [])
    data.setdefault("quarantine", {})
    return data


def save_state(path: Path, state: dict) -> None:
    """一時ファイルに書いてから置き換える（書き込み途中での破損を防ぐ）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def parse_iso(value) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def ingest(state: dict, records: list[dict], now_iso: str) -> dict:
    """取得結果を取り込む。新規は保存、内容が変わった既存は別バージョンとして残す（自動再送しない）。"""
    counts = {"new": 0, "corrected": 0, "unchanged": 0}
    matches = state["matches"]
    for rec in records:
        cur = matches.get(rec["id"])
        if cur is None:
            matches[rec["id"]] = {
                **rec,
                "fetched_at": now_iso,
                "last_fetched_at": now_iso,
                "persisted_at": now_iso,
                "notified_at": None,
                "notify_error": None,
                "notify_attempts": [],
                "correction_pending": False,
                "versions": [],
            }
            counts["new"] += 1
        elif cur.get("content_hash") != rec["content_hash"]:
            cur.setdefault("versions", []).append(
                {**{k: cur.get(k) for k in CONTENT_KEYS}, "content_hash": cur.get("content_hash"),
                 "superseded_at": now_iso})
            for k in CONTENT_KEYS + ("api_day_utc", "date_status"):
                cur[k] = rec[k]
            cur["content_hash"] = rec["content_hash"]
            cur["correction_pending"] = True
            cur["last_fetched_at"] = now_iso
            counts["corrected"] += 1
        else:
            cur["last_fetched_at"] = now_iso
            counts["unchanged"] += 1
    return counts


def prune(state: dict, now_utc: dt.datetime, retention_days: int, run_keep: int) -> int:
    """保存期間を過ぎた試合と古い実行ログを削除する。persisted_at が読めない試合は削除せず quarantine に隔離する。
    戻り値は削除件数。"""
    cutoff = now_utc - dt.timedelta(days=int(retention_days))
    removed = 0
    for mid in list(state["matches"]):
        entry = state["matches"][mid]
        persisted = parse_iso(entry.get("persisted_at"))
        if persisted is None:
            state["quarantine"][mid] = entry   # 削除しない（人が確認する）
            del state["matches"][mid]
            continue
        if persisted < cutoff:
            del state["matches"][mid]
            removed += 1
    state["runs"] = state["runs"][-int(run_keep):]
    return removed


def pending_notifications(state: dict, roster: dict, now_utc: dt.datetime,
                          backlog_days: int, cap: int) -> tuple[list[dict], int]:
    """未通知・保存期間内・通知対象。送信結果が不明（最後の attempt の result が None）のものも含める。
    戻り値: (今回通知分, 残件数)"""
    cutoff = now_utc - dt.timedelta(days=int(backlog_days))
    cands = []
    for entry in state["matches"].values():
        if entry.get("notified_at"):
            continue
        persisted = parse_iso(entry.get("persisted_at"))
        if persisted is None or persisted < cutoff:
            continue
        if not is_notifiable(entry, roster):
            continue
        cands.append(entry)
    cands.sort(key=lambda e: (e.get("api_day_utc") or "", e["id"]))
    cap = int(cap)
    return cands[:cap], max(0, len(cands) - cap)


def ack_correction(state: dict, match_id: str, by: str, at_iso: str) -> bool:
    """人が訂正内容を確認したあとに correction_pending を解除する。解除できたら True。"""
    entry = state["matches"].get(match_id)
    if not entry or not entry.get("correction_pending"):
        return False
    entry["correction_pending"] = False
    entry["correction_acknowledged"] = {"by": by, "at": at_iso}
    return True


# ------------------------------------------------------------
# 7. 配信文（Discord embed）
# ------------------------------------------------------------
def _fmt_jst(iso_value) -> str:
    parsed = parse_iso(iso_value)
    return parsed.astimezone(JST).strftime("%Y-%m-%d %H:%M") + " JST" if parsed else "取得不可"


def _team_text(judged: list[dict]) -> str:
    return " ／ ".join(f"{p['name'] or p['id']}（{JP_LABEL[p['status']]}）" for p in judged)


def _count_line(j1: list[dict], j2: list[dict]) -> str:
    allp = j1 + j2
    jp = sum(p["status"] == JP_CONFIRMED for p in allp)
    non = sum(p["status"] == NON_JP_CONFIRMED for p in allp)
    unk = sum(p["status"] == UNKNOWN for p in allp)
    return f"判定内訳：日本人確認{jp}名／非日本人確認{non}名／判定不能{unk}名"


def _side_rank(side: list[dict], snap: dict | None) -> str:
    if snap is None:
        return "未取得"
    if len(side) not in (1, 2):
        return "順位不明"
    rank = find_rank(snap, [p["name"] for p in side])
    return f"{rank}位" if rank is not None else "順位不明"


def rank_line(entry: dict, rankings: dict | None) -> str:
    """保存済みランキングを参照するだけ（取得はしない）。氏名の完全一致のみ。一致しなければ順位不明。
    表示は「上位15位＋掲載日本選手のみ」であることを必ず併記する。"""
    code = next((c for c, n in RANK_DISCIPLINES.items() if n == entry["discipline"]), None)
    snap = (rankings or {}).get(code) if code else None
    if snap is None:
        return "世界ランキング：保存データなし（順位不明）"
    head = (f"世界ランキング（badspi・基準 {snap.get('basis_label', '年未確認')}・"
            "上位15位＋掲載日本選手のみ。全順位ではありません）")
    return f"{head}：{_side_rank(entry['side1'], snap)} ／ {_side_rank(entry['side2'], snap)}"


def build_embed(entry: dict, roster: dict, window: dict, attempt_no: int = 1, retry_note: bool = False,
                rankings: dict | None = None) -> dict:
    j1 = judge_side(entry["side1"], roster)
    j2 = judge_side(entry["side2"], roster)
    games = entry.get("games")
    score = "、".join(f"{a}-{b}" for a, b in games) if games else "取得不可（欠損）"
    if entry.get("winner") is None:
        result = "勝者：未確定（取得不可）"
    else:
        result = f"勝者：{_team_text(j1 if entry['winner'] == 1 else j2)}"
    lines = [
        f"種目：{entry['discipline']}",
        f"対戦：{_team_text(j1)} vs {_team_text(j2)}",
        _count_line(j1, j2),
        f"ゲーム別得点：{score}",
        result,
        f"取得対象期間：JST {window['jst_range'][0]} 〜 {window['jst_range'][1]}（API前日はUTC {window['api_day_utc']}）",
        f"情報取得時刻：{_fmt_jst(entry.get('fetched_at'))}",
        f"出典：{SOURCE_DOC_URL}",
    ]
    lines.append(rank_line(entry, rankings))
    if retry_note:
        lines.append("※ 前回の送信結果が未確定のため、重複して届く可能性があります。")
    tname = (entry.get("tournament") or {}).get("name") or "大会名：取得不可"
    return {"title": f"{tname}（{entry['discipline']}）"[:250],
            "description": "\n".join(lines)[:4000],
            "color": 0x2E86C1,
            "footer": {"text": f"試合ID {entry['id']} ／ 送信試行 {attempt_no}回目"}}


def build_header(window: dict, status: str, *, reason: str | None = None, jp: int = 0,
                 notified: int = 0, unknown: int = 0, roster_size: int = 0,
                 corrections: int = 0, pending_extra: int = 0, date_counts: dict | None = None,
                 shifted_day: str | None = None, start_range: str | None = None,
                 counts: dict | None = None, anomalies: list[str] | None = None) -> dict:
    period = (f"取得対象期間：JST {window['jst_range'][0]} 〜 {window['jst_range'][1]}"
              f"（API前日はUTC {window['api_day_utc']}）")
    if status == "error":
        return {"title": "バドミントン結果：取得失敗",
                "description": f"取得失敗：{reason}\n{period}\n※ 0件ではなく、取得自体が失敗しています。既存データは更新していません。",
                "color": 0x7F8C8D}
    lines = []
    if status == "empty":
        lines.append("対象試合なし（期間内の結果は0件）")
    lines.append(period)
    if counts is not None:
        lines.append(f"取得 {counts.get('fetched', 0)}件 ／ 正規化 {counts.get('normalized', 0)}件 ／ "
                     f"対象 {counts.get('target', 0)}件 ／ 未知種目 {counts.get('unmapped', 0)}件")
    for note in anomalies or []:
        lines.append(f"⚠ 注意：{note}")
    if shifted_day:
        lines.append(f"⚠ 予定対象日 UTC {shifted_day} の結果は取得できませんでした（欠落）。"
                     "実行が UTC 日付をまたいで遅延したためです。取得分は日付未確定として保存し、通知していません。")
    if start_range:
        lines.append(f"取得した試合の開始時刻の範囲：{start_range}")
    if roster_size == 0:
        lines.append("※ 日本人確認済みの選手IDが0件のため、日本人の判定は行っていません（全件 判定不能）。")
    lines.append(f"日本人確認済みの試合：{jp}件（今回通知：{notified}件、未通知の過去分を含む）")
    lines.append(f"判定不能を含む試合：{unknown}件（日本人である可能性を否定できない試合を含みます）")
    dc = date_counts or {}
    if dc.get("outside") or dc.get("unknown") or dc.get("unconfirmed"):
        lines.append(f"日付の確認：取得窓の外 {dc.get('outside', 0)}件／開始時刻不明 {dc.get('unknown', 0)}件／"
                     f"日付未確定 {dc.get('unconfirmed', 0)}件（いずれも通知していません）")
    if corrections:
        lines.append(f"訂正候補：{corrections}件（自動再送していません。人が確認して解除してください）")
    if pending_extra:
        lines.append(f"未通知の残り{pending_extra}件は次回以降に送信します")
    return {"title": "バドミントン結果（前日取得分）", "description": "\n".join(lines), "color": 0x7F8C8D}


def send_batches(webhook: str, header: dict, items: list[tuple[str, dict]], post) -> dict:
    """ヘッダーを送ったあと、試合embedを10件ずつ送る。
    戻り値: {"ok": 送信成功ID, "failed": HTTPエラーで不達のID, "unknown": 例外で結果不明のID}"""
    try:
        r = post(webhook, json={"embeds": [header]}, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 300:
            print(f"[WARN] ヘッダー送信に失敗（HTTP {r.status_code}）")
    except requests.RequestException as err:
        print(f"[WARN] ヘッダー送信の結果が不明（{type(err).__name__}）")
    result = {"ok": set(), "failed": set(), "unknown": set()}
    for i in range(0, len(items), DISCORD_BATCH):
        chunk = items[i:i + DISCORD_BATCH]
        ids = {mid for mid, _ in chunk}
        try:
            r = post(webhook, json={"embeds": [embed for _, embed in chunk]}, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as err:
            print(f"[ERROR] Discord送信の結果が不明（{type(err).__name__}）。次回再送します")
            result["unknown"] |= ids
            continue
        if r.status_code < 300:
            result["ok"] |= ids
        else:
            print(f"[ERROR] Discord送信に失敗（HTTP {r.status_code}）")
            result["failed"] |= ids
    return result


def handoff_matches(state: dict, roster: dict, api_day_utc: str) -> list[dict]:
    """ハンドオフ用の試合: 日本人確認済みを含み、日付が取得窓と確認でき、結果が確定した試合のみ。"""
    out = []
    for e in state["matches"].values():
        if e.get("date_status") != "confirmed" or classify_match(e, roster) != "jp":
            continue
        winner = e.get("winner")
        start = parse_start(e.get("start"))
        if winner not in (1, 2) or start is None or e.get("games") is None:
            continue
        m = {
            "match_id": str(e["id"]),
            "tournament": str((e.get("tournament") or {}).get("name") or ""),
            "discipline": e.get("discipline"),
            "side_a": [{"name": p["name"]} for p in e["side1"]],
            "side_b": [{"name": p["name"]} for p in e["side2"]],
            "games": e["games"],
            "winner_side": "a" if winner == 1 else "b",
            "status": e.get("status"),
            "start_utc": start.astimezone(UTC).isoformat(),
        }
        if not H.is_international_tournament(m["tournament"]):
            continue  # 国内大会は既存のJBA結果配信が担当（重複防止・安全側で除外）
        if H.validate_match(m, api_day_utc) is None:
            out.append(m)
    return out


def _write_handoff_safely(handoff_path, now_utc, window, config, status, state, roster) -> None:
    """結果JSONの書き出し。失敗してもこのジョブの終了コードや既存配信には影響させない。"""
    if handoff_path is None:
        return
    try:
        if status == "ok" and config.get("api_format_verified") is not True:
            status = "format_unverified"   # 応答形式が未確認の間は、試合を1件も書き出さない
        matches = handoff_matches(state, roster, window["api_day_utc"]) if status == "ok" else []
        payload = H.build_payload(generated_at_utc=now_utc, api_day_utc=window["api_day_utc"],
                                  format_verified=config.get("api_format_verified") is True,
                                  status=status, matches=matches)
        H.write_payload_atomic(Path(handoff_path), payload)
        print(f"[INFO] 試合結果JSONを書き出しました（status={status}, 試合{len(matches)}件）")
    except Exception as err:  # noqa: BLE001 - 書き出し失敗は既存配信へ波及させない
        print(f"[WARN] 試合結果JSONの書き出しに失敗（既存配信へは影響しません）: {type(err).__name__}")


# ------------------------------------------------------------
# 8. 実行本体
# ------------------------------------------------------------
def run_once(*, api_key: str, webhook: str, now_utc: dt.datetime, state_path: Path, config: dict,
             roster: dict, fetch, post, sleep=time.sleep, event: str = "manual",
             rankings: dict | None = None, handoff_path: Path | None = None) -> int:
    """1回分の処理。戻り値は終了コード（0: 正常、1: 失敗）。handoff_path があれば試合結果JSONを書き出す（Discordへは送らない）。"""
    if not api_key:
        print("::warning::BADMINTON_API_KEY が未設定のため、取得・保存・配信は行いません（未稼働）")
        return 0
    try:
        state = load_state(state_path)
    except StateError as err:
        print(f"[ERROR] {err}")
        return 1

    now_utc = now_utc.astimezone(UTC)
    now_iso = now_utc.isoformat()
    window = compute_window(now_utc)
    drift = schedule_drift(now_utc, event)
    discord_on = bool(config["discord_output_enabled"]) and DISCORD_DELIVERY_ENABLED

    try:
        status, items, reason, calls = fetch_all_pages(api_key, fetch, config, sleep)
    except requests.RequestException as err:
        status, items, reason, calls = "error", [], f"通信エラー（{type(err).__name__}）", 0

    run_log = {"run_at": now_iso, "event": event, "api_day_utc": window["api_day_utc"],
               "api_calls": calls, "discord_output_enabled": discord_on,
               "schedule_slot_utc": drift["slot_utc"], "delay_min": drift["delay_min"],
               "shifted": drift["shifted"]}

    if status == "error":
        # 取得失敗: 既存の試合データには触れない（prune も行わない）。実行ログのみ追記する。
        run_log.update({"status": "error", "reason": reason})
        state["runs"].append(run_log)
        save_state(state_path, state)
        _write_handoff_safely(handoff_path, now_utc, window, config, "fetch_failed", state, roster)
        print(f"[ERROR] 取得失敗：{reason}")
        if discord_on and webhook:
            send_batches(webhook, build_header(window, "error", reason=reason), [], post)
        return 1

    # --- 取得（fetched）→ 保存（persisted）---
    api_day = None if drift["shifted"] else window["api_day_utc"]
    normalized = [m for m in (normalize_match(i) for i in items) if m is not None]
    targets = [m for m in normalized if m["discipline"] in TARGET_DISCIPLINES]
    records = [build_record(m, api_day) for m in targets]
    counts = ingest(state, records, now_iso)
    verdicts = {"jp": 0, "non_jp": 0, "unknown": 0}
    date_counts = {"confirmed": 0, "outside": 0, "unknown": 0, "unconfirmed": 0}
    for rec in records:
        verdicts[classify_match(rec, roster)] += 1
        date_counts[rec["date_status"]] += 1
    starts = sorted(s for s in (parse_start(i.get("start")) for i in items if isinstance(i, dict)) if s)
    start_range = (f"{_fmt_jst(starts[0].isoformat())} 〜 {_fmt_jst(starts[-1].isoformat())}"
                   if starts else None)
    removed = prune(state, now_utc, config["state_retention_days"], config["run_log_keep"])
    run_log.update({
        "status": status, "fetched_items": len(items), "normalized": len(normalized),
        "target": len(targets), "new": counts["new"], "corrected": counts["corrected"],
        "unchanged": counts["unchanged"], "jp_confirmed": verdicts["jp"],
        "non_jp_confirmed": verdicts["non_jp"], "unknown": verdicts["unknown"],
        "date_status": date_counts, "roster_size": len(roster), "pruned": removed,
        # 種目表記が想定外の件数（API文書に種目の語彙が無いため、ずれを検知できるよう記録する）
        "unmapped_discipline": sum(1 for i in items if not isinstance(i, dict)
                                   or DISCIPLINE_MAP.get(str(i.get("discipline", "")).strip()) is None),
        "quarantined_total": len(state["quarantine"]),
        "api_unverified": list(UNVERIFIED_API_ITEMS),
    })
    save_state(state_path, state)  # 保存完了後にのみ通知へ進む
    _write_handoff_safely(handoff_path, now_utc, window, config, "ok", state, roster)

    corrections = sum(1 for e in state["matches"].values() if e.get("correction_pending"))
    pending, extra = pending_notifications(state, roster, now_utc,
                                           config["backlog_days"], config["max_backlog_per_run"])
    counts_summary = {"fetched": len(items), "normalized": len(normalized),
                      "target": len(targets), "unmapped": run_log["unmapped_discipline"]}
    anomalies = []
    if items and not normalized:
        anomalies.append("取得件数はあるが正規化できた試合が0件（応答形式が想定と異なる可能性）。対象試合なしとは扱いません")
    elif len(normalized) < len(items):
        anomalies.append(f"正規化できない試合 {len(items) - len(normalized)}件（必須項目の欠損または形式不一致）")
    if run_log["unmapped_discipline"]:
        anomalies.append(f"未知の種目表記 {run_log['unmapped_discipline']}件（対象外。種目の語彙を確認してください）")
    if date_counts["unknown"]:
        anomalies.append(f"開始日時を読めない試合 {date_counts['unknown']}件（日付不明のため通知対象外）")
    run_log["anomalies"] = anomalies
    for note in anomalies:
        print(f"::warning::{note}")
    header_kwargs = dict(jp=verdicts["jp"], unknown=verdicts["unknown"], roster_size=len(roster),
                         corrections=corrections, date_counts=date_counts,
                         shifted_day=drift["intended_api_day_utc"] if drift["shifted"] else None,
                         start_range=start_range, counts=counts_summary, anomalies=anomalies)

    # --- 通知（notified）---
    rc = 0
    if not discord_on:
        print(f"[INFO] Discord出力OFF：通知は行いません（未通知の対象候補 {len(pending) + extra}件は保存済み）")
    elif not webhook:
        print("[ERROR] Discord出力はONですが DISCORD_WEBHOOK_OSHI が未設定です（通知していません）")
        rc = 1
    else:
        now_send = dt.datetime.now(UTC).isoformat()
        # 1) 送信前に attempt を保存する（送信後に中断しても、試行の事実が残る）
        prior_unknown = {}
        for e in pending:
            attempts = e.setdefault("notify_attempts", [])
            # 結果が不明（未記録・unknown）の過去試行があれば、重複の可能性を明記して再送する
            prior_unknown[e["id"]] = any(a.get("result") in (None, "unknown") for a in attempts)
            attempts.append({"run_at": now_send, "result": None})
        save_state(state_path, state)
        # 2) 送信
        header = build_header(window, status, notified=len(pending), **header_kwargs)
        items_out = [(e["id"], build_embed(e, roster, window, attempt_no=len(e["notify_attempts"]),
                                           retry_note=prior_unknown[e["id"]], rankings=rankings))
                     for e in pending]
        res = send_batches(webhook, header, items_out, post)
        # 3) 結果を保存する
        sent_at = dt.datetime.now(UTC).isoformat()
        for e in pending:
            last = e["notify_attempts"][-1]
            if e["id"] in res["ok"]:
                last["result"] = "ok"
                e["notified_at"] = sent_at
                e["notify_error"] = None
            elif e["id"] in res["failed"]:
                last["result"] = "failed"
                e["notify_error"] = f"送信失敗（HTTP）{sent_at}"
            elif e["id"] in res["unknown"]:
                last["result"] = "unknown"
                e["notify_error"] = f"送信結果不明 {sent_at}（次回再送。重複の可能性あり）"
        run_log["notified"] = len(res["ok"])
        run_log["notify_failed"] = len(res["failed"])
        run_log["notify_unknown"] = len(res["unknown"])
        run_log["duplicate_possible_resends"] = sum(1 for e in pending if prior_unknown[e["id"]])

    state["runs"].append(run_log)
    save_state(state_path, state)
    print(f"[OK] 取得{len(items)}件 / 対象{len(targets)}件 / 新規{counts['new']}件 / "
          f"訂正候補{counts['corrected']}件 / 日本人確認{verdicts['jp']}件 / 判定不能{verdicts['unknown']}件 / "
          f"日付未確定{date_counts['unconfirmed']}件")
    return rc


def run(event: str | None = None) -> int:
    api_key = os.environ.get("BADMINTON_API_KEY", "").strip()
    webhook = os.environ.get("DISCORD_WEBHOOK_OSHI", "").strip()
    state_path = Path(os.environ.get("BADMINTON_STATE_PATH", "").strip() or DEFAULT_STATE_PATH)
    return run_once(
        api_key=api_key,
        webhook=webhook,
        now_utc=dt.datetime.now(UTC),
        state_path=state_path,
        config=load_config(),
        roster=load_roster(),
        fetch=fetch_page,
        post=requests.post,
        event=event or os.environ.get("GITHUB_EVENT_NAME", "manual"),
        rankings=load_latest_rankings(Path(os.environ.get("BADSPI_SNAPSHOT_DIR", "").strip()
                                           or ROOT / "state_branch" / "state" / "badspi_ranking")),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="日本人選手の前日結果の独立配信")
    ap.add_argument("--ack-correction", metavar="MATCH_ID", help="訂正候補を人が確認して解除する")
    ap.add_argument("--by", help="--ack-correction の確認者名（必須）")
    ap.add_argument("--state", default=os.environ.get("BADMINTON_STATE_PATH") or str(DEFAULT_STATE_PATH))
    args = ap.parse_args(argv)
    if args.ack_correction:
        if not args.by:
            print("[ERROR] --by（確認者名）が必要です")
            return 1
        state_path = Path(args.state)
        try:
            state = load_state(state_path)
        except StateError as err:
            print(f"[ERROR] {err}")
            return 1
        if not ack_correction(state, args.ack_correction, args.by, dt.datetime.now(UTC).isoformat()):
            print("[ERROR] 訂正候補として見つからないため、解除していません")
            return 1
        save_state(state_path, state)
        print(f"[OK] 訂正候補を解除しました: {args.ack_correction}")
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
