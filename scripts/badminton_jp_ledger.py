# -*- coding: utf-8 -*-
"""
日本人選手ID台帳の作成支援（候補抽出 → APIの検索で照合 → 人が確認して確定）。

確定ルール（自動確定はしない）:
- 候補の出典: 日本バドミントン協会（JBA）の代表選手ページ（男女・U24の4ページ）。
  各候補は JBA の選手詳細ID（/national/player/view/N）で重複排除する。
- 候補 → 選手ID: Badminton API の /v1/search（Free）で照合する。検索結果は「候補の提示」にとどめる。
  名前が一致・類似していても自動では確定しない。
- 確定（confirm）は人が実行する。選手IDは、その候補の検索結果に含まれるものだけ受け付ける。
  JP_CONFIRMED は検索結果の country が "JPN" の場合のみ。NON_JP_CONFIRMED は country が JPN 以外の場合のみ。
  出典URL・確認者・確認日（YYYY-MM-DD）が無い確定は拒否する。
- 検索はAPIキーが必要。キーが無い場合は search を実行せず、候補は「未照合」のまま残す。
- APIの制限（Free: 50回/日・10回/分）に合わせ、--max-calls で1回の上限を設け、途中結果を保存して翌日に再開できる。
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "badminton_results.json"
CANDIDATES_PATH = ROOT / "config" / "badminton_jp_candidates.json"
REVIEW_PATH = ROOT / "config" / "badminton_jp_candidates_review.md"
ROSTER_PATH = ROOT / "config" / "badminton_jp_players.json"

API_BASE = "https://api.badmintonapi.com"
SEARCH_PATH = "/v1/search"
REQUEST_TIMEOUT = 20
SEARCH_INTERVAL_SEC = 6.0   # 10回/分の制限に合わせる
SEARCH_MAX_PER_RUN = 20     # 1回の実行で検索する上限（Free: 50回/日。朝の結果取得・再試行・ページングの枠を残す）
JBA_CARD_RE = re.compile(r'<li class="p-player__item">([\s\S]*?)</li>')
JBA_HREF_RE = re.compile(r'href="(https://www\.badminton\.or\.jp/national/player/view/(\d+))"')
JBA_NAME_RE = re.compile(r'<p class="p-player__name">([^<]+)</p>')
JBA_PROF_RE = re.compile(r'<p class="p-player__prof">([\s\S]*?)</p>')
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LedgerError(ValueError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_jba_cards(html_text: str, page_label: str, source_url: str) -> list[dict]:
    """JBA代表選手ページの選手カードを抽出する。カードが1件も無ければ LedgerError（誤ページの取り込み防止）。"""
    cards = []
    for block in JBA_CARD_RE.findall(html_text):
        href = JBA_HREF_RE.search(block)
        name = JBA_NAME_RE.search(block)
        prof = JBA_PROF_RE.search(block)
        if not (href and name):
            continue
        prof_text = html_lib.unescape(re.sub(r"<[^>]+>", "\n", prof.group(1))) if prof else ""
        first = [x.strip() for x in prof_text.splitlines() if x.strip()]
        team, pref = "", ""
        if first:
            parts = [p.strip() for p in first[0].split("/")]
            team = parts[0] if parts else ""
            pref = parts[1] if len(parts) > 1 else ""
        cards.append({
            "name": html_lib.unescape(name.group(1)).strip(),
            "jba_detail_id": href.group(2),
            "jba_detail_url": href.group(1),
            "team": team,
            "prefecture": pref,
            "page_label": page_label,
            "source_url": source_url,
        })
    if not cards:
        raise LedgerError(f"選手カードが見つかりません（{page_label}）。ページ構造が変わった可能性があります")
    return cards


def build_candidates(pages: list[tuple[str, str, str]], generated_at: str | None = None) -> dict:
    """pages: [(ページ名, 取得URL, HTML)]。JBA詳細IDで重複排除した候補一覧を作る。"""
    merged: dict[str, dict] = {}
    for label, url, text in pages:
        for card in parse_jba_cards(text, label, url):
            cur = merged.get(card["jba_detail_id"])
            if cur is None:
                merged[card["jba_detail_id"]] = {
                    "jba_detail_id": card["jba_detail_id"],
                    "jba_detail_url": card["jba_detail_url"],
                    "name": card["name"],
                    "team": card["team"],
                    "prefecture": card["prefecture"],
                    "pages": [card["page_label"]],
                    "source_urls": [card["source_url"]],
                    "status": "UNMATCHED",
                    "search": None,
                }
            else:
                if card["page_label"] not in cur["pages"]:
                    cur["pages"].append(card["page_label"])
                if card["source_url"] not in cur["source_urls"]:
                    cur["source_urls"].append(card["source_url"])
    candidates = sorted(merged.values(), key=lambda c: c["jba_detail_id"])
    names = [c["name"] for c in candidates]
    for c in candidates:
        c["same_name_count"] = names.count(c["name"])   # 同名が複数いる場合は人が必ず区別する
    return {"schema_version": 1, "generated_at": generated_at or _now(),
            "source": "日本バドミントン協会 代表選手ページ（候補であり、確定ではない）",
            "candidates": candidates}


def fetch_search(api_key: str, q: str) -> tuple[int, object]:
    resp = requests.get(
        API_BASE + SEARCH_PATH,
        headers={"Authorization": f"Bearer {api_key}"},
        params={"q": q, "kind": "player", "limit": 5},
        timeout=REQUEST_TIMEOUT,
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return resp.status_code, payload


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def search_candidates(doc: dict, api_key: str, fetch, *, max_calls: int = SEARCH_MAX_PER_RUN,
                      sleep=time.sleep, now_iso: str | None = None) -> dict:
    """未検索の候補だけを検索する（1回最大 SEARCH_MAX_PER_RUN 件。超過分は次回に回す）。
    429 を受けたら、その日の検索を停止する（無理な再試行はしない）。
    戻り値: {"searched": n, "stopped": 理由 or None, "remaining": 未検索の件数}"""
    max_calls = min(int(max_calls), SEARCH_MAX_PER_RUN)
    done = 0
    stopped = None
    for c in doc["candidates"]:
        if c.get("search") is not None:
            continue
        if done >= max_calls:
            break
        if done > 0:
            sleep(SEARCH_INTERVAL_SEC)
        code, payload = fetch(api_key, c["name"])
        done += 1
        if code == 429:
            stopped = "429（分または日次の上限）: 本日の検索を停止しました。翌日に未検索分から再開してください"
            break
        if code != 200 or not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            stopped = f"HTTP {code} または形式不一致（検索を中止。キー・上限を確認）"
            break
        results = []
        for item in payload["data"]:
            if not isinstance(item, dict) or item.get("kind") != "player" or not item.get("id"):
                continue
            country = item.get("country")
            results.append({"id": str(item["id"]), "name": str(item.get("name") or ""),
                            "country": country if isinstance(country, str) else None,
                            "score": item.get("score") if isinstance(item.get("score"), (int, float)) else None})
        results.sort(key=lambda r: (-_num(r["score"]), r["id"]))
        c["search"] = {"searched_at": now_iso or _now(), "results": results[:5]}
    remaining = sum(1 for c in doc["candidates"] if c.get("search") is None)
    return {"searched": done, "stopped": stopped, "remaining": remaining}


def _find(doc: dict, detail_id: str) -> dict:
    for c in doc["candidates"]:
        if c["jba_detail_id"] == detail_id:
            return c
    raise LedgerError(f"候補が見つかりません: {detail_id}")


def confirm(doc: dict, roster: dict, *, detail_id: str, player_id: str, decision: str,
            source_url: str, confirmed_by: str, confirmed_at: str) -> dict:
    """人が確認した結果だけを台帳に入れる。戻り値: 台帳に追加した選手エントリ。"""
    if decision not in ("JP", "NON_JP"):
        raise LedgerError("decision は JP か NON_JP")
    if not (isinstance(source_url, str) and source_url.startswith("http")):
        raise LedgerError("出典URLが必要です")
    if not (confirmed_by or "").strip():
        raise LedgerError("確認者が必要です")
    if not DATE_RE.match(confirmed_at or ""):
        raise LedgerError("確認日は YYYY-MM-DD 形式です")
    cand = _find(doc, detail_id)
    if not cand.get("search"):
        raise LedgerError("先に検索（search）で候補を照合してください")
    hit = next((r for r in cand["search"]["results"] if r["id"] == player_id), None)
    if hit is None:
        raise LedgerError("その選手IDは、この候補の検索結果に含まれていません（自由入力は不可）")
    country = hit.get("country")
    if decision == "JP" and country != "JPN":
        raise LedgerError(f"国コードが JPN ではないため、日本人として確定できません（country={country}）")
    if decision == "NON_JP" and (not country or country == "JPN"):
        raise LedgerError(f"非日本人の根拠が不足（country={country}）")
    status = "JP_CONFIRMED" if decision == "JP" else "NON_JP_CONFIRMED"
    entry = {
        "player_id": player_id,
        "name": hit["name"],
        "status": status,
        "country_from_api": country,
        "jba_detail_id": detail_id,
        "source_url": source_url,
        "confirmed_by": confirmed_by.strip(),
        "confirmed_at": confirmed_at,
    }
    players = [p for p in roster.get("players", []) if p.get("player_id") != player_id]
    players.append(entry)
    roster["players"] = players
    cand["status"] = status
    return entry


def review_markdown(doc: dict) -> str:
    lines = [
        "# 日本人選手ID 候補一覧（人が確認する用）",
        "",
        f"- 生成: {doc.get('generated_at')}",
        f"- 出典: {doc.get('source')}",
        "- 自動確定はしていません。下表の「照合結果」を見て、人が confirm で確定してください。",
        "- 同名が複数いる候補、country が JPN でない候補は特に注意。",
        "",
        "| JBA詳細ID | 氏名 | 所属 | 都道府県 | ページ | 照合（API検索） | 状態 |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in doc["candidates"]:
        if c.get("search") is None:
            hit = "未照合（検索未実施）"
        elif not c["search"]["results"]:
            hit = "検索結果なし"
        else:
            hit = "<br>".join(f"{r['id']} {r['name']} [{r.get('country')}] score={r.get('score')}"
                              for r in c["search"]["results"][:3])
        flag = "（同名あり）" if c.get("same_name_count", 1) > 1 else ""
        lines.append(f"| {c['jba_detail_id']} | {c['name']}{flag} | {c['team']} | {c['prefecture']} | "
                     f"{' / '.join(c['pages'])} | {hit} | {c['status']} |")
    return "\n".join(lines) + "\n"


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _jba_urls() -> list[tuple[str, str]]:
    labels = ["JBA男子・全体", "JBA女子・全体", "JBA男子・U24", "JBA女子・U24"]
    urls = _load_json(CONFIG_PATH, {}).get("jp_roster_source_urls", [])
    return list(zip(labels, urls))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="日本人選手ID台帳の候補作成・照合・確定")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="JBA代表選手ページから候補一覧を作る（公開ページの取得）")
    s = sub.add_parser("search", help="候補をAPI検索で照合（APIキーが必要。1回最大20件）")
    s.add_argument("--max-calls", type=int, default=SEARCH_MAX_PER_RUN)
    c = sub.add_parser("confirm", help="人が確認した結果を台帳に入れる")
    c.add_argument("--detail-id", required=True)
    c.add_argument("--player-id", required=True)
    c.add_argument("--decision", required=True, choices=["JP", "NON_JP"])
    c.add_argument("--source-url", required=True)
    c.add_argument("--confirmed-by", required=True)
    c.add_argument("--confirmed-at", required=True)
    sub.add_parser("review", help="人が確認する一覧（Markdown）を書き出す")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        pages = []
        for label, url in _jba_urls():
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "ricky-kogyo-ledger/1.0"})
            resp.raise_for_status()
            pages.append((label, url, resp.content.decode("utf-8")))
        doc = build_candidates(pages)
        _write_json(CANDIDATES_PATH, doc)
        REVIEW_PATH.write_text(review_markdown(doc), encoding="utf-8")
        print(f"[OK] 候補 {len(doc['candidates'])} 件を書き出しました")
        return 0

    doc = _load_json(CANDIDATES_PATH, None)
    if doc is None:
        print("[ERROR] 候補ファイルがありません。先に build を実行してください")
        return 1

    if args.cmd == "search":
        api_key = os.environ.get("BADMINTON_API_KEY", "").strip()
        if not api_key:
            print("::warning::BADMINTON_API_KEY が未設定のため、照合（search）は実行しません。候補は未照合のままです。")
            return 0
        if args.max_calls > SEARCH_MAX_PER_RUN:
            print(f"[WARN] --max-calls {args.max_calls} は上限超過のため {SEARCH_MAX_PER_RUN} に制限します")
        result = search_candidates(doc, api_key, fetch_search, max_calls=args.max_calls)
        _write_json(CANDIDATES_PATH, doc)
        REVIEW_PATH.write_text(review_markdown(doc), encoding="utf-8")
        print(f"[OK] 検索 {result['searched']} 件 / 未検索の残り {result['remaining']} 件"
              + (f"（中止: {result['stopped']}）" if result["stopped"] else ""))
        return 1 if result["stopped"] else 0

    if args.cmd == "confirm":
        roster = _load_json(ROSTER_PATH, {"schema_version": 1, "players": []})
        try:
            entry = confirm(doc, roster, detail_id=args.detail_id, player_id=args.player_id,
                            decision=args.decision, source_url=args.source_url,
                            confirmed_by=args.confirmed_by, confirmed_at=args.confirmed_at)
        except LedgerError as err:
            print(f"[ERROR] {err}")
            return 1
        _write_json(ROSTER_PATH, roster)
        _write_json(CANDIDATES_PATH, doc)
        REVIEW_PATH.write_text(review_markdown(doc), encoding="utf-8")
        print(f"[OK] 台帳に追加: {entry['player_id']} {entry['name']} {entry['status']}")
        return 0

    if args.cmd == "review":
        REVIEW_PATH.write_text(review_markdown(doc), encoding="utf-8")
        print(f"[OK] {REVIEW_PATH}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
