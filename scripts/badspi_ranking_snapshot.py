# -*- coding: utf-8 -*-
"""
badspi.jp の種目別ランキングを「月次スナップショット」として保存する新規モジュール。

- 既存の wr_rankings（バドナビ由来）とは混在させない。保存先は専用ディレクトリ。
- 年は推測しない。呼び出し側が指定した年月と、ページの基準日（M月D日付）の月が一致する場合のみ保存する。
- 行の連番（1..N）に欠番がある場合は rank_continuous=False / complete=False とし、完全なランキングとして扱わない。
- 取得URLは config/badminton_results.json の badspi_ranking_urls。null の種目は取得しない。
- 既に保存済みの月は上書きしない（ただし既存が complete=False で、今回が complete=True のときのみ置換）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "badminton_results.json"
DEFAULT_SNAPSHOT_DIR = ROOT / "state_branch" / "state" / "badspi_ranking"
REQUEST_TIMEOUT = 20
USER_AGENT = "ricky-kogyo-ranking-snapshot/1.0 (personal use)"

DISCIPLINE_NAMES = {
    "MS": "男子シングルス", "WS": "女子シングルス",
    "MD": "男子ダブルス", "WD": "女子ダブルス", "XD": "混合ダブルス",
}

BASIS_RE = re.compile(r"(\d{1,2})月(\d{1,2})日付")
NUM_RE = re.compile(r"^[\d,]+$")


class ParseError(ValueError):
    pass


def html_to_lines(text: str) -> list[str]:
    """HTMLを、空行を除いたテキスト行の並びにする（タグは改行に置き換える）。"""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [html_lib.unescape(x).strip() for x in text.splitlines()]
    return [x for x in lines if x]


def parse_basis_month_day(lines: list[str]) -> tuple[int, int]:
    for line in lines:
        m = BASIS_RE.search(line)
        if m:
            return int(m.group(1)), int(m.group(2))
    raise ParseError("基準日（M月D日付）が見つかりません")


def parse_rows(lines: list[str]) -> list[dict]:
    """「順位・国・氏名・ポイント・大会数」の5行組を拾う。組の形が合わない行は読み飛ばす。"""
    rows = []
    i = 0
    while i + 4 < len(lines):
        rank, country, name, points, tournaments = lines[i:i + 5]
        if rank.isdigit() and NUM_RE.match(points) and tournaments.isdigit():
            rows.append({
                "rank": int(rank),
                "country": country,
                "name": name,
                "points": int(points.replace(",", "")),
                "tournaments": int(tournaments),
            })
            i += 5
        else:
            i += 1
    return rows


def build_snapshot(html_text: str, *, year: int, month: int, discipline_code: str,
                   source_url: str, fetched_at: str) -> dict:
    if discipline_code not in DISCIPLINE_NAMES:
        raise ParseError(f"未知の種目コード: {discipline_code}")
    lines = html_to_lines(html_text)
    basis_month, basis_day = parse_basis_month_day(lines)
    if basis_month != month:
        raise ParseError(f"基準月（{basis_month}月）が指定月（{month}月）と一致しません")
    try:
        basis_date = dt.date(year, basis_month, basis_day).isoformat()
    except ValueError as err:
        raise ParseError(f"基準日が不正です: {err}") from err
    rows = parse_rows(lines)
    ranks = [r["rank"] for r in rows]
    continuous = bool(rows) and ranks == list(range(1, len(rows) + 1))
    return {
        "schema_version": 1,
        "discipline_code": discipline_code,
        "discipline": DISCIPLINE_NAMES[discipline_code],
        "year_month": f"{year:04d}-{month:02d}",
        "basis_date": basis_date,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "row_count": len(rows),
        "rank_continuous": continuous,
        "complete": continuous,
        "rows": rows,
    }


def save_snapshot(snapshot_dir: Path, snap: dict) -> str:
    """戻り値: "saved" / "exists"（保存済みのため据え置き）/ "replaced"（不完全→完全に置換）"""
    path = snapshot_dir / f"{snap['year_month']}_{snap['discipline_code']}.json"
    action = "saved"
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = {}
        if old.get("complete") or not snap["complete"]:
            return "exists"
        action = "replaced"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return action


def fetch_html(url: str) -> str:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def run_snapshot(*, year_month: str, urls: dict, snapshot_dir: Path, fetch=fetch_html,
                 now_utc: dt.datetime | None = None) -> int:
    """urls: {種目コード: URL}。URL が空の種目は取得しない。戻り値は終了コード。"""
    try:
        year_s, month_s = year_month.split("-")
        year, month = int(year_s), int(month_s)
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        print(f"[ERROR] 年月の形式が不正です（YYYY-MM）: {year_month}")
        return 1
    targets = {code: url for code, url in urls.items() if code in DISCIPLINE_NAMES and url}
    if not targets:
        print("[SKIP] badspi の取得URLが未設定です（config/badminton_results.json）。取得していません。")
        return 0
    fetched_at = (now_utc or dt.datetime.now(dt.timezone.utc)).isoformat()
    rc = 0
    for code, url in targets.items():
        try:
            snap = build_snapshot(fetch(url), year=year, month=month, discipline_code=code,
                                  source_url=url, fetched_at=fetched_at)
        except (requests.RequestException, OSError, ParseError) as err:
            print(f"[ERROR] {code}: 取得・解析に失敗（{type(err).__name__}: {err}）")
            rc = 1
            continue
        action = save_snapshot(snapshot_dir, snap)
        print(f"[{action.upper()}] {code} {snap['year_month']} 行数{snap['row_count']} complete={snap['complete']}")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="badspi 月次ランキングのスナップショット保存")
    ap.add_argument("--year-month", required=True, help="YYYY-MM（ページの基準月と一致させる）")
    ap.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    args = ap.parse_args(argv)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return run_snapshot(year_month=args.year_month, urls=config.get("badspi_ranking_urls", {}),
                        snapshot_dir=Path(args.snapshot_dir))


if __name__ == "__main__":
    sys.exit(main())
