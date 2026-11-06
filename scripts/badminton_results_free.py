# -*- coding: utf-8 -*-
"""
日本人選手の前日試合結果を #推し（Discord）へ独立配信するモジュール（新規・既存とは独立）。

確定仕様の要点:
- 取得元は Badminton API Free の /v1/results/yesterday（UTC基準の前日）。
- 日本人判定は選手IDのみ。判定は3値: JP_CONFIRMED / NON_JP_CONFIRMED / UNKNOWN。
  未登録・情報不足は UNKNOWN（非日本人とみなさない）。
- ダブルスは構成選手ごとに判定を保持する。
- 取得（fetched）・保存（persisted）・通知（notified）の時刻を試合ごとに別管理する。
- Discord出力は config/badminton_results.json の discord_output_enabled で制御（既定 false）。
  OFF中は通知済みにしない。ONにすると、保存済みで未通知の試合を通知する（バックログ日数で上限）。
- 訂正（同一試合IDで内容が変わった）は別バージョンとして保存し、自動再送しない。
- 状態は専用ブランチの checkout 配下（BADMINTON_STATE_PATH）に保存する。
- APIキー（BADMINTON_API_KEY）が無ければ、何も取得・保存・送信せず終了する。
- 既存の scripts/fetch_badminton_alerts.py や既存の状態ファイルには依存しない。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

API_BASE = "https://api.badmintonapi.com"
RESULTS_YESTERDAY_PATH = "/v1/results/yesterday"
SOURCE_DOC_URL = "https://badmintonapi.com/documentation"
REQUEST_TIMEOUT = 20
DISCORD_BATCH = 10

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

# 内容の同一性判定に使う項目（訂正検知）
CONTENT_KEYS = ("discipline", "side1", "side2", "games", "winner", "status")

DEFAULT_CONFIG = {
    "discord_output_enabled": False,
    "backlog_days": 3,
    "max_backlog_per_run": 30,
    "state_retention_days": 30,
    "run_log_keep": 90,
    "max_api_pages": 5,
    "api_min_interval_sec": 6.0,
}


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
# 1. 取得窓（UTC基準・JST換算）
# ------------------------------------------------------------
def compute_window(run_utc: dt.datetime) -> dict:
    """実行時刻から、/v1/results/yesterday が返す UTC 日と JST 換算期間を返す。"""
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


# ------------------------------------------------------------
# 2. 取得（ページング・0件と失敗の区別）
# ------------------------------------------------------------
def classify_response(status_code: int, payload) -> tuple[str, object]:
    """戻り値: ("ok", items) / ("empty", []) / ("error", 理由)"""
    if status_code != 200:
        hint = "（キー不正の可能性）" if status_code == 401 else ""
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


def fetch_all_pages(api_key: str, fetch, cfg: dict, sleep=time.sleep) -> tuple[str, list, str | None, int]:
    """全ページを取得する。戻り値: (status, items, 失敗理由, API呼び出し回数)"""
    items: list = []
    cursor = None
    calls = 0
    max_pages = int(cfg["max_api_pages"])
    for page in range(max_pages):
        if page > 0:
            sleep(float(cfg["api_min_interval_sec"]))  # 10回/分の制限を守る
        code, payload = fetch(api_key, cursor)
        calls += 1
        status, data = classify_response(code, payload)
        if status == "error":
            return "error", [], str(data), calls
        if status == "ok":
            items.extend(data)
        nxt = next_cursor(payload)
        if not nxt:
            return ("ok" if items else "empty"), items, None, calls
        cursor = nxt
    # ページ上限に達しても次ページがある → 取得不完全。0件扱いにしない。
    return "error", [], f"ページ上限{max_pages}回に達したため取得が不完全", calls


def fetch_page(api_key: str, cursor: str | None) -> tuple[int, object]:
    params: dict = {"per_page": 50}
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
    return {
        "id": str(item["id"]),
        "tournament": {"name": str(tournament.get("name") or "") or None, "year": tournament.get("year")},
        "discipline": discipline,
        "side1": side1,
        "side2": side2,
        "games": _games_of(item),
        "winner": winner if winner in (1, 2) else None,
        "status": item.get("status") if isinstance(item.get("status"), str) else None,
    }


# ------------------------------------------------------------
# 4. 日本人判定（3値・選手IDのみ）
# ------------------------------------------------------------
def load_roster(path: Path = ROSTER_PATH) -> dict:
    """{player_id: {"name", "status"}}。読めない・不正なエントリは載せない（＝判定不能になる）。"""
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
        if pid and status in (JP_CONFIRMED, NON_JP_CONFIRMED):
            roster[str(pid)] = {"name": str(p.get("name") or ""), "status": status}
    return roster


def judge_side(players: list[dict], roster: dict) -> list[dict]:
    """サイドの構成選手ごとに判定を付ける。未登録は UNKNOWN。"""
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


# ------------------------------------------------------------
# 5. 記録（内容ハッシュ・訂正検知）
# ------------------------------------------------------------
def content_hash(rec: dict) -> str:
    """内容の同一性。選手は表示名ではなくIDで比較する（表記揺れで訂正扱いにしない）。"""
    payload = {k: rec.get(k) for k in CONTENT_KEYS}
    for side in ("side1", "side2"):
        payload[side] = [p["id"] for p in (rec.get(side) or [])]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_record(norm: dict, api_day_utc: str) -> dict:
    """保存用の最小限の記録。生の応答は保存しない。"""
    def _side(side):
        return [{"id": p["id"], "name": p["name"]} for p in side]
    rec = {
        "id": norm["id"],
        "api_day_utc": api_day_utc,
        "tournament": norm["tournament"],
        "discipline": norm["discipline"],
        "side1": _side(norm["side1"]),
        "side2": _side(norm["side2"]),
        "games": norm["games"],
        "winner": norm["winner"],
        "status": norm["status"],
    }
    rec["content_hash"] = content_hash(rec)
    return rec


# ------------------------------------------------------------
# 6. 状態（保存・読み込み・取り込み・整理）
# ------------------------------------------------------------
class StateError(RuntimeError):
    pass


def empty_state() -> dict:
    return {"schema_version": 1, "matches": {}, "runs": []}


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
    except ValueError:
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
                "correction_pending": False,
                "versions": [],
            }
            counts["new"] += 1
        elif cur.get("content_hash") != rec["content_hash"]:
            cur.setdefault("versions", []).append(
                {**{k: cur.get(k) for k in CONTENT_KEYS}, "content_hash": cur.get("content_hash"),
                 "superseded_at": now_iso})
            for k in CONTENT_KEYS:
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
    """保存期間を過ぎた試合と古い実行ログを削除する。戻り値は削除件数。"""
    cutoff = now_utc - dt.timedelta(days=int(retention_days))
    removed = 0
    for mid in list(state["matches"]):
        persisted = parse_iso(state["matches"][mid].get("persisted_at"))
        if persisted is None or persisted < cutoff:
            del state["matches"][mid]
            removed += 1
    state["runs"] = state["runs"][-int(run_keep):]
    return removed


def pending_notifications(state: dict, roster: dict, now_utc: dt.datetime,
                          backlog_days: int, cap: int) -> tuple[list[dict], int]:
    """未通知で、保存期間内（バックログ日数内）かつ日本人確認済みを含む試合。戻り値: (今回通知分, 残件数)"""
    cutoff = now_utc - dt.timedelta(days=int(backlog_days))
    cands = []
    for entry in state["matches"].values():
        if entry.get("notified_at"):
            continue
        persisted = parse_iso(entry.get("persisted_at"))
        if persisted is None or persisted < cutoff:
            continue
        if classify_match(entry, roster) != "jp":
            continue
        cands.append(entry)
    cands.sort(key=lambda e: (e.get("api_day_utc", ""), e["id"]))
    cap = int(cap)
    return cands[:cap], max(0, len(cands) - cap)


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


def build_embed(entry: dict, roster: dict, window: dict) -> dict:
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
    tname = (entry.get("tournament") or {}).get("name") or "大会名：取得不可"
    return {"title": f"{tname}（{entry['discipline']}）"[:250],
            "description": "\n".join(lines)[:4000],
            "color": 0x2E86C1,
            "footer": {"text": f"試合ID {entry['id']}"}}


def build_header(window: dict, status: str, *, reason: str | None = None, jp: int = 0,
                 notified: int = 0, unknown: int = 0, roster_size: int = 0,
                 corrections: int = 0, pending_extra: int = 0) -> dict:
    period = (f"取得対象期間：JST {window['jst_range'][0]} 〜 {window['jst_range'][1]}"
              f"（API前日はUTC {window['api_day_utc']}）")
    if status == "error":
        return {"title": "バドミントン結果：取得失敗",
                "description": f"取得失敗：{reason}\n{period}\n※ 0件ではなく、取得自体が失敗しています。",
                "color": 0x7F8C8D}
    lines = []
    if status == "empty":
        lines.append("対象試合なし（期間内の結果は0件）")
    lines.append(period)
    if roster_size == 0:
        lines.append("※ 日本人確認済みの選手IDが0件のため、日本人の判定は行っていません（全件 判定不能）。")
    lines.append(f"日本人確認済みの試合：{jp}件（今回通知：{notified}件、未通知の過去分を含む）")
    lines.append(f"判定不能を含む試合：{unknown}件（日本人である可能性を否定できない試合を含みます）")
    if corrections:
        lines.append(f"訂正候補：{corrections}件（自動再送していません。保存版を確認してください）")
    if pending_extra:
        lines.append(f"未通知の残り{pending_extra}件は次回以降に送信します")
    return {"title": "バドミントン結果（前日取得分）", "description": "\n".join(lines), "color": 0x7F8C8D}


def send_batches(webhook: str, header: dict, items: list[tuple[str, dict]], post) -> tuple[set, set]:
    """ヘッダーを送ったあと、試合embedを10件ずつ送る。戻り値: (送信成功ID, 送信失敗ID)"""
    try:
        r = post(webhook, json={"embeds": [header]}, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 300:
            print(f"[WARN] ヘッダー送信に失敗（HTTP {r.status_code}）")
    except requests.RequestException as err:
        print(f"[WARN] ヘッダー送信に失敗（{type(err).__name__}）")
    ok, failed = set(), set()
    for i in range(0, len(items), DISCORD_BATCH):
        chunk = items[i:i + DISCORD_BATCH]
        ids = {mid for mid, _ in chunk}
        try:
            r = post(webhook, json={"embeds": [embed for _, embed in chunk]}, timeout=REQUEST_TIMEOUT)
            if r.status_code < 300:
                ok |= ids
                continue
            print(f"[ERROR] Discord送信に失敗（HTTP {r.status_code}）")
        except requests.RequestException as err:
            print(f"[ERROR] Discord送信に失敗（{type(err).__name__}）")
        failed |= ids
    return ok, failed


# ------------------------------------------------------------
# 8. 実行本体
# ------------------------------------------------------------
def run_once(*, api_key: str, webhook: str, now_utc: dt.datetime, state_path: Path, config: dict,
             roster: dict, fetch, post, sleep=time.sleep) -> int:
    """1回分の処理。戻り値は終了コード（0: 正常、1: 失敗）。"""
    if not api_key:
        print("[SKIP] BADMINTON_API_KEY 未設定のため、取得・保存・配信は行いません。")
        return 0
    try:
        state = load_state(state_path)
    except StateError as err:
        print(f"[ERROR] {err}")
        return 1

    now_utc = now_utc.astimezone(UTC)
    now_iso = now_utc.isoformat()
    window = compute_window(now_utc)
    discord_on = bool(config["discord_output_enabled"])

    try:
        status, items, reason, calls = fetch_all_pages(api_key, fetch, config, sleep)
    except requests.RequestException as err:
        status, items, reason, calls = "error", [], f"通信エラー（{type(err).__name__}）", 0

    if status == "error":
        state["runs"].append({"run_at": now_iso, "api_day_utc": window["api_day_utc"],
                              "status": "error", "reason": reason, "api_calls": calls,
                              "discord_output_enabled": discord_on})
        prune(state, now_utc, config["state_retention_days"], config["run_log_keep"])
        save_state(state_path, state)
        print(f"[ERROR] 取得失敗：{reason}")
        if discord_on and webhook:
            send_batches(webhook, build_header(window, "error", reason=reason), [], post)
        return 1

    # --- 取得（fetched）→ 保存（persisted）---
    normalized = [m for m in (normalize_match(i) for i in items) if m is not None]
    targets = [m for m in normalized if m["discipline"] in TARGET_DISCIPLINES]
    records = [build_record(m, window["api_day_utc"]) for m in targets]
    counts = ingest(state, records, now_iso)
    verdicts = {"jp": 0, "non_jp": 0, "unknown": 0}
    for rec in records:
        verdicts[classify_match(rec, roster)] += 1
    removed = prune(state, now_utc, config["state_retention_days"], config["run_log_keep"])
    save_state(state_path, state)  # 保存完了後にのみ通知へ進む

    run_log = {
        "run_at": now_iso, "api_day_utc": window["api_day_utc"], "status": status,
        "api_calls": calls, "discord_output_enabled": discord_on,
        "fetched_items": len(items), "normalized": len(normalized), "target": len(targets),
        "new": counts["new"], "corrected": counts["corrected"], "unchanged": counts["unchanged"],
        "jp_confirmed": verdicts["jp"], "non_jp_confirmed": verdicts["non_jp"],
        "unknown": verdicts["unknown"], "roster_size": len(roster), "pruned": removed,
    }
    corrections = sum(1 for e in state["matches"].values() if e.get("correction_pending"))
    pending, extra = pending_notifications(state, roster, now_utc,
                                           config["backlog_days"], config["max_backlog_per_run"])

    # --- 通知（notified）：Discord OFF中は一切送らず、通知済みにもしない ---
    rc = 0
    if not discord_on:
        print(f"[INFO] Discord出力OFF：通知は行いません（未通知の対象候補 {len(pending) + extra}件は保存済み）")
    elif not webhook:
        print("[ERROR] Discord出力はONですが DISCORD_WEBHOOK_OSHI が未設定です（通知していません）")
        rc = 1
    else:
        header = build_header(window, status, jp=verdicts["jp"], notified=len(pending),
                              unknown=verdicts["unknown"], roster_size=len(roster),
                              corrections=corrections, pending_extra=extra)
        items_out = [(e["id"], build_embed(e, roster, window)) for e in pending]
        ok_ids, failed_ids = send_batches(webhook, header, items_out, post)
        sent_at = dt.datetime.now(UTC).isoformat()
        for mid in ok_ids:
            state["matches"][mid]["notified_at"] = sent_at
            state["matches"][mid]["notify_error"] = None
        for mid in failed_ids:
            state["matches"][mid]["notify_error"] = f"送信失敗 {sent_at}"
        run_log["notified"] = len(ok_ids)
        run_log["notify_failed"] = len(failed_ids)

    state["runs"].append(run_log)
    save_state(state_path, state)
    print(f"[OK] 取得{len(items)}件 / 対象{len(targets)}件 / 新規{counts['new']}件 / "
          f"訂正候補{counts['corrected']}件 / 日本人確認{verdicts['jp']}件 / 判定不能{verdicts['unknown']}件")
    return rc


def run() -> int:
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
    )


if __name__ == "__main__":
    sys.exit(run())
