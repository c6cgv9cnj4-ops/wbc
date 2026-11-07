# -*- coding: utf-8 -*-
"""
試合結果ハンドオフ（ローカル生成 → 既存 Discord 配信が読む）の共通処理。

役割分担（最終方針）:
- Discord へ投稿するのは既存の .github/workflows/badminton_alerts.yml（06:05 JST）だけ。
- 新ローカルジョブ（Mac）は JSON を生成するだけ。Discord へは一切送らない。
- 既存配信は、このJSONが無い・壊れている・古い・未検証・取得失敗のときは、新規結果を無視して従来どおり動く。

このモジュールは標準ライブラリのみ（既存 CI の依存に影響しない）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

SCHEMA_VERSION = 1
MAX_AGE = dt.timedelta(hours=26)          # 生成から26時間を超えたデータは使わない（古いデータの混入防止）
FUTURE_TOLERANCE = dt.timedelta(minutes=5)
MAX_EMBEDS_PER_RUN = 20                   # 1回の配信で新規結果を送る上限（洪水防止）
SEEN_KEY_PREFIX = "intl::"
SEEN_RETENTION_DAYS = 30
COLOR_INTL = 0x38A169
SOURCE_LABEL = "badmintonapi.com（Free）"
DATA_FILENAME = "badminton_results_handoff.json"
DISCIPLINE_LABEL = {"MS": "男子シングルス", "WS": "女子シングルス", "MD": "男子ダブルス",
                    "WD": "女子ダブルス", "XD": "混合ダブルス"}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# 国内大会（JBA 結果ページの既存配信が担当）を示す語。1つでも含めば除外する（安全側）。
DOMESTIC_MARKERS = ("全日本", "日本", "全国", "国民体育", "国体", "インターハイ", "インカレ", "実業団",
                    "全中", "ジュニア", "都道府県", "県", "市民", "JBA", "ランキング大会")
# 国際大会と確認できる語。どれも含まない試合は除外する（API実データ未検証のため「不明は配信しない」）。
INTERNATIONAL_MARKERS = ("BWF", "World", "Open", "Masters", "Super", "Asia", "Thomas", "Uber",
                         "Sudirman", "Olympic", "Championships", "Grand Prix", "International", "Challenge")


def is_international_tournament(name) -> bool:
    """大会名から国際大会と確認できるものだけ True。国内の語を含むものは必ず False。"""
    n = str(name or "").strip()
    if not n or any(m in n for m in DOMESTIC_MARKERS):
        return False
    low = n.lower()
    return any(m.lower() in low for m in INTERNATIONAL_MARKERS)


def expected_api_day_utc(now_utc: dt.datetime) -> str:
    """結果の取得対象日（UTC）。results_free.compute_window と同じ式（実行時刻のUTC日の前日）。"""
    return (now_utc.astimezone(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()


def _clean(text, limit: int = 60) -> str:
    s = _CONTROL_RE.sub("", str(text or "")).strip()
    return s[:limit]


def content_hash(m: dict) -> str:
    """同一試合の内容（訂正の検出用）。ID以外の結果内容が変われば別の値になる。"""
    body = {"tournament": m.get("tournament"), "discipline": m.get("discipline"),
            "side_a": m.get("side_a"), "side_b": m.get("side_b"),
            "games": m.get("games"), "winner_side": m.get("winner_side")}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def validate_match(m, api_day_utc: str) -> str | None:
    """1試合の検証。問題があれば理由を返す（None は有効）。推測による補完はしない。"""
    if not isinstance(m, dict):
        return "not_object"
    mid = m.get("match_id")
    if not isinstance(mid, str) or not mid or len(mid) > 80:
        return "bad_match_id"
    if not isinstance(m.get("tournament"), str) or not m["tournament"].strip():
        return "bad_tournament"
    if m.get("discipline") not in DISCIPLINE_LABEL:
        return "bad_discipline"
    for side in ("side_a", "side_b"):
        players = m.get(side)
        if not isinstance(players, list) or not (1 <= len(players) <= 2):
            return f"bad_{side}"
        for p in players:
            if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"].strip():
                return f"bad_{side}_name"
    games = m.get("games")
    if not isinstance(games, list) or not (1 <= len(games) <= 7):
        return "bad_games"
    wins_a = wins_b = 0
    for g in games:
        if (not isinstance(g, list) or len(g) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 50 for x in g)):
            return "bad_game_score"
        if g[0] > g[1]:
            wins_a += 1
        elif g[1] > g[0]:
            wins_b += 1
    if m.get("winner_side") == "a":
        if not wins_a > wins_b:
            return "winner_contradicts_games"
    elif m.get("winner_side") == "b":
        if not wins_b > wins_a:
            return "winner_contradicts_games"
    else:
        return "bad_winner"
    if m.get("status") != "finished":
        return "not_finished"
    start = m.get("start_utc")
    try:
        s = dt.datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return "bad_start"
    if s.tzinfo is None:
        return "start_without_timezone"
    if s.astimezone(dt.timezone.utc).date().isoformat() != api_day_utc:
        return "outside_window"
    return None


def build_payload(*, generated_at_utc: dt.datetime, api_day_utc: str, format_verified: bool,
                  status: str, matches: list) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at_utc.astimezone(dt.timezone.utc).isoformat(),
        "api_day_utc": api_day_utc,
        "source": SOURCE_LABEL,
        "format_verified": bool(format_verified),
        "status": status,
        "matches": matches,
    }


def write_payload_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_payload(path: Path):
    """戻り値: (payload | None, 理由)。理由は ok / missing / unreadable / corrupt / schema_mismatch。"""
    if not path.exists():
        return None, "missing"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, "unreadable"
    try:
        payload = json.loads(text)
    except ValueError:
        return None, "corrupt"
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return None, "schema_mismatch"
    return payload, "ok"


def check_payload(payload: dict, now_utc: dt.datetime) -> str:
    """ファイル全体の可否。ok 以外は新規結果を全て無視する。"""
    if payload.get("status") != "ok":
        return f"status_{payload.get('status')}"
    if payload.get("format_verified") is not True:
        return "format_unverified"
    try:
        generated = dt.datetime.fromisoformat(payload.get("generated_at"))
    except (TypeError, ValueError):
        return "bad_generated_at"
    if generated.tzinfo is None:
        return "bad_generated_at"
    age = now_utc.astimezone(dt.timezone.utc) - generated.astimezone(dt.timezone.utc)
    if age > MAX_AGE:
        return "expired"
    if age < -FUTURE_TOLERANCE:
        return "generated_in_future"
    if payload.get("api_day_utc") != expected_api_day_utc(now_utc):
        return "wrong_api_day"
    return "ok"


def _build_embed(m: dict, correction: bool) -> dict:
    a = " / ".join(_clean(p["name"]) for p in m["side_a"])
    b = " / ".join(_clean(p["name"]) for p in m["side_b"])
    winner, loser = (a, b) if m["winner_side"] == "a" else (b, a)
    score = " ".join(f"{g[0]}-{g[1]}" for g in m["games"])
    title = f"🏸 【{_clean(m['tournament'], 40)}】 {DISCIPLINE_LABEL[m['discipline']]}"
    if correction:
        title += "（訂正）"
    description = (f"**{winner}** {score} {loser}\n"
                   f"勝者：{winner}\n"
                   f"出典：{SOURCE_LABEL}")
    return {"title": title[:256], "description": description[:4000], "color": COLOR_INTL}


def collect_embeds(path: Path, now_utc: dt.datetime, seen: dict, exclude_player=None):
    """既存配信へ追加する新規結果を集める。戻り値: (embeds, marks, 理由)。
    exclude_player(氏名)->bool が真の選手を含む試合は除外する（既存の大会記事経由の配信が担当し、二重投稿を防ぐため）。
    marks は送信成功後にだけ apply_marks で既送信に登録する（送信失敗時は再試行される）。"""
    path = Path(path)
    payload, reason = read_payload(path)
    if payload is None:
        return [], [], reason
    reason = check_payload(payload, now_utc)
    if reason != "ok":
        return [], [], reason
    api_day = payload["api_day_utc"]
    embeds, marks, skipped = [], [], {}
    for m in payload.get("matches") or []:
        problem = validate_match(m, api_day)
        if problem:
            skipped[problem] = skipped.get(problem, 0) + 1
            continue
        if not is_international_tournament(m.get("tournament")):
            skipped["not_international_or_unverified"] = skipped.get("not_international_or_unverified", 0) + 1
            continue
        if exclude_player and any(exclude_player(p["name"]) for p in m["side_a"] + m["side_b"]):
            skipped["covered_by_existing"] = skipped.get("covered_by_existing", 0) + 1
            continue
        h = content_hash(m)
        key = SEEN_KEY_PREFIX + m["match_id"]
        prev = seen.get(key)
        if prev and prev.get("hash") == h:
            continue  # 既送信（同一試合・同一内容）
        if len(embeds) >= MAX_EMBEDS_PER_RUN:
            skipped["over_limit"] = skipped.get("over_limit", 0) + 1
            continue
        embeds.append(_build_embed(m, correction=prev is not None))
        marks.append((key, h))
    print(f"[INFO] 試合結果ハンドオフ: 新規{len(embeds)}件、無視{sum(skipped.values())}件 {skipped or ''}")
    return embeds, marks, "ok"


def apply_marks(seen: dict, marks, now_utc: dt.datetime) -> None:
    now_iso = now_utc.astimezone(dt.timezone.utc).isoformat()
    for key, h in marks:
        seen[key] = {"hash": h, "sent_at": now_iso}


def prune_seen(seen: dict, now_utc: dt.datetime, days: int = SEEN_RETENTION_DAYS) -> None:
    cutoff = now_utc.astimezone(dt.timezone.utc) - dt.timedelta(days=days)
    for key in list(seen):
        if not key.startswith(SEEN_KEY_PREFIX):
            continue
        try:
            sent = dt.datetime.fromisoformat(seen[key]["sent_at"])
        except (KeyError, TypeError, ValueError):
            del seen[key]
            continue
        if sent < cutoff:
            del seen[key]


PUBLISH_MAX_AGE = dt.timedelta(hours=2)   # 生成から2時間以内のJSONだけを公開対象にする（再試行 05:30〜05:55 を含む）


def publish_decision(payload, now_utc: dt.datetime, *, enabled: bool, key_present: bool,
                     last_digest: str | None, digest: str) -> str:
    """公開するかどうかの判定。戻り値は理由（publish のみ公開する）。古いJSONは再公開しない。"""
    if not enabled:
        return "disabled"
    if payload is None:
        return "no_payload"
    if payload.get("status") != "ok" or payload.get("format_verified") is not True:
        return "not_ok"
    try:
        generated = dt.datetime.fromisoformat(payload.get("generated_at"))
    except (TypeError, ValueError):
        return "bad_generated_at"
    if generated.tzinfo is None:
        return "bad_generated_at"
    age = now_utc.astimezone(dt.timezone.utc) - generated.astimezone(dt.timezone.utc)
    if age > PUBLISH_MAX_AGE or age < -FUTURE_TOLERANCE:
        return "stale"
    if not key_present:
        return "no_key"
    if last_digest == digest:
        return "unchanged"
    return "publish"
