# -*- coding: utf-8 -*-
"""
badspi.jp の種目別ランキングを「月次スナップショット」として保存する新規モジュール。

注意（重要）:
- 実HTMLでは未検証。解析は「順位・国・氏名・ポイント・大会数の5行組」「基準日（M月D日付）」「総件数（全N件）」の
  想定に基づく。実ページで確認するまで、このモジュールの解析結果を正として扱わないこと。
- 年は推測しない。呼び出し側が指定した年月と、ページの基準日の月が一致する場合のみ保存する。
- 完全判定: 順位が 1..N で連続し、かつページに記載された総件数と行数が一致する場合のみ complete=True。
  総件数が確認できない場合は complete=False（不完全扱い）。
- 不完全データは「.incomplete.json」に別保存し、完全版（.json）は上書きしない。
  既に完全版がある月は、取得し直しても置き換えない。
- 文字コード: charset 指定があればそれを使い、無ければ UTF-8 → CP932 の順で判定する。
- 取得URLは config/badminton_results.json の badspi_ranking_urls。null の種目は取得しない（未設定は警告付きで終了）。
- 既存の wr_rankings（バドナビ由来）とは混在させない。保存先は専用ディレクトリ。
- 月次の自動取得 workflow は .github/workflows/badspi_ranking_monthly.yml（URL未設定なら何もせず終了）。
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
# 総件数の表記は未確認（想定）。見つからなければ complete にならない。
TOTAL_RE = re.compile(r"全\s*([\d,]+)\s*件")
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


def parse_total(lines: list[str]) -> int | None:
    """ページに記載された総件数（想定表記）。見つからなければ None。"""
    for line in lines:
        m = TOTAL_RE.search(line)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def parse_rows(lines: list[str]) -> list[dict]:
    """「順位・国・氏名・ポイント・大会数」の5行組を拾う。組の形が合わない行は読み飛ばす。
    注意：氏名の欄がペア名（ダブルス）の場合、構成選手への分解は行わない（1文字列として保存）。"""
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


def decode_body(content: bytes, header_charset: str | None) -> str:
    """応答本体を文字列にする。charset 指定があればそれを優先し、無ければ UTF-8 → CP932 の順。"""
    # requests は charset 無しの text/* を ISO-8859-1 とみなすため、それは「未指定」として扱う
    if header_charset and header_charset.lower() not in ("iso-8859-1", "latin-1", "us-ascii", "ascii"):
        try:
            return content.decode(header_charset)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return content.decode("cp932")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")


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
    total = parse_total(lines)
    ranks = [r["rank"] for r in rows]
    continuous = bool(rows) and ranks == list(range(1, len(rows) + 1))
    if total is None:
        reason = "総件数が確認できない（不完全扱い）"
    elif total != len(rows):
        reason = f"総件数{total}と取得行数{len(rows)}が不一致"
    elif not continuous:
        reason = "順位に欠番がある"
    else:
        reason = None
    complete = reason is None
    return {
        "schema_version": 2,
        "verified_against_live_html": False,   # 実HTML未検証（想定の構造で解析）
        "discipline_code": discipline_code,
        "discipline": DISCIPLINE_NAMES[discipline_code],
        "year_month": f"{year:04d}-{month:02d}",
        "basis_date": basis_date,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "row_count": len(rows),
        "total_found": total,
        "rank_continuous": continuous,
        "complete": complete,
        "incomplete_reason": reason,
        "rows": rows,
    }


def save_snapshot(snapshot_dir: Path, snap: dict) -> str:
    """戻り値:
    - "saved" / "replaced"（不完全→完全に置換は行わない。完全版は新規保存のみ）
    - "exists"（完全版が既にあるため据え置き）
    - "incomplete_saved"（不完全版として別ファイルに保存）"""
    base = f"{snap['year_month']}_{snap['discipline_code']}"
    complete_path = snapshot_dir / f"{base}.json"
    incomplete_path = snapshot_dir / f"{base}.incomplete.json"
    if snap["complete"]:
        if complete_path.exists():
            return "exists"          # 前回の正常版は上書きしない
        path, action = complete_path, "saved"
    else:
        path, action = incomplete_path, "incomplete_saved"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return action


def fetch_html(url: str) -> str:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    m = re.search(r"charset=([\w.-]+)", resp.headers.get("Content-Type", ""), re.I)
    return decode_body(resp.content, m.group(1) if m else None)


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
        print("::warning::badspi の取得URLが未設定です（config/badminton_results.json の badspi_ranking_urls）。"
              "取得せず終了します。")
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
        print(f"[{action.upper()}] {code} {snap['year_month']} 行数{snap['row_count']} "
              f"総件数{snap['total_found']} complete={snap['complete']}"
              + (f" 理由: {snap['incomplete_reason']}" if snap["incomplete_reason"] else ""))
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="badspi 月次ランキングのスナップショット保存（実HTML未検証）")
    ap.add_argument("--year-month", required=True, help="YYYY-MM（ページの基準月と一致させる）")
    ap.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    args = ap.parse_args(argv)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return run_snapshot(year_month=args.year_month, urls=config.get("badspi_ranking_urls", {}),
                        snapshot_dir=Path(args.snapshot_dir))


if __name__ == "__main__":
    sys.exit(main())
