# -*- coding: utf-8 -*-
"""
badspi.jp の「世界ランキング」（種目別）を月次スナップショットとして保存するモジュール。

実HTML確認（2026-11-06、5種目すべて。robots.txt は Allow: /）:
- 取得URLは 301 で正規URLへ転送される。config に正規URLを設定する（推測で作っていない）。
- ページのタイトルは「世界ランキング 男子シングルス（11月3日付）」形式。基準日は月日のみで年は無い。
  → 年はページに無い。基準年は (a) HTTPの Last-Modified の年月が一致 または (b) 人の確認で独立に確認できた場合のみ保存する。
    どちらも無ければ [HOLD]（保存しない。年を推測しない）。
- 表示範囲は「上位15位」＋「上位15位より下の日本選手」。全ランキングはページに無い（full_ranking_available=false）。
  → 完全性は「1〜15位が欠けなく揃うこと」で判定する。総件数の表記は確認できなかった。
- 行は「順位・国・氏名・ポイント・大会数」の5行組。ダブルスの氏名は「A／B」（構成2名）。
- 上位15位外の日本選手は、実際の世界順位つきで表示される。

保存ルール:
- 完全（complete=true）のみ .json に保存し、既存の完全版は上書きしない。
- 不完全（上位15位が欠ける、タイトルの種目が一致しない等）は .incomplete.json に別保存し、完全版を置き換えない。
- 種目ごとに独立。1種目の失敗は他種目を止めない。URL未設定の種目は個別にSKIPし、理由をログに残す。
- 既存の wr_rankings（バドナビ由来）とは混在させない。保存先は専用ディレクトリ。
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

STRUCTURE_VERIFIED_ON = "2026-11-06"  # 5種目の実HTMLで行構造・タイトル・基準日を確認した日
TOP_N = 15                            # ページの上位表示範囲（実HTMLで確認）
TITLE_PREFIX = "世界ランキング"

DISCIPLINE_NAMES = {
    "MS": "男子シングルス", "WS": "女子シングルス",
    "MD": "男子ダブルス", "WD": "女子ダブルス", "XD": "混合ダブルス",
}
# ページタイトルに含まれる種目名（実HTMLの表記。XD は「ミックスダブルス」）
TITLE_KEYWORD = {
    "MS": "男子シングルス", "WS": "女子シングルス",
    "MD": "男子ダブルス", "WD": "女子ダブルス", "XD": "ミックスダブルス",
}

BASIS_RE = re.compile(r"(\d{1,2})月(\d{1,2})日付")
NUM_RE = re.compile(r"^[\d,]+$")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
PAIR_SEP = "／"


class ParseError(ValueError):
    pass


class BasisMismatch(ParseError):
    """ページの基準月が、保存対象の年月と一致しない（ページ未更新など）。失敗ではなく「待ち」。"""


def html_to_lines(text: str) -> list[str]:
    """HTMLを、空行を除いたテキスト行の並びにする（タグは改行に置き換える）。"""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [html_lib.unescape(x).strip() for x in text.splitlines()]
    return [x for x in lines if x]


def parse_title(text: str) -> str:
    m = TITLE_RE.search(text)
    if not m:
        raise ParseError("<title> が見つかりません")
    return html_lib.unescape(m.group(1)).strip()


def parse_basis_month_day(title: str) -> tuple[int, int]:
    m = BASIS_RE.search(title)
    if not m:
        raise ParseError("基準日（M月D日付）がタイトルにありません")
    return int(m.group(1)), int(m.group(2))


def parse_rows(lines: list[str]) -> list[dict]:
    """「順位・国・氏名・ポイント・大会数」の5行組を拾う。組の形が合わない行は読み飛ばす。"""
    rows = []
    i = 0
    while i + 4 < len(lines):
        rank, country, name, points, tournaments = lines[i:i + 5]
        if rank.isdigit() and NUM_RE.match(points) and tournaments.isdigit():
            members = [x.strip() for x in name.split(PAIR_SEP)] if PAIR_SEP in name else [name]
            rows.append({
                "rank": int(rank),
                "country": country,
                "name": name,
                "members": members,          # ダブルスは構成2名（「／」区切りを分解。分解できない場合は1名）
                "points": int(points.replace(",", "")),
                "tournaments": int(tournaments),
            })
            i += 5
        else:
            i += 1
    return rows


def decode_body(content: bytes, header_charset: str | None) -> str:
    """応答本体を文字列にする。charset 指定があればそれを優先し、無ければ UTF-8 → CP932 の順。
    requests が charset 無しの text/* に当てる ISO-8859-1 は「未指定」として扱う。"""
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
    title = parse_title(html_text)
    if not title.startswith(TITLE_PREFIX) or TITLE_KEYWORD[discipline_code] not in title:
        raise ParseError(f"ページの種目が一致しない（{discipline_code}を期待、タイトル: {title[:40]}）")
    basis_month, basis_day = parse_basis_month_day(title)
    if basis_month != month:
        raise BasisMismatch(f"基準月（{basis_month}月）が指定月（{month}月）と一致しない")
    try:
        basis_date = dt.date(year, basis_month, basis_day).isoformat()   # 年は指定値。推測しない
    except ValueError as err:
        raise ParseError(f"基準日が不正: {err}") from err

    rows = parse_rows(html_to_lines(html_text))
    for r in rows:
        r["section"] = "top" if r["rank"] <= TOP_N else "japan_listed"
    ranks = [r["rank"] for r in rows]
    top_ranks = sorted(r["rank"] for r in rows if r["rank"] <= TOP_N)
    no_dup = len(ranks) == len(set(ranks))
    scope_complete = top_ranks == list(range(1, TOP_N + 1)) and no_dup
    if not no_dup:
        reason = "順位が重複"
    elif not scope_complete:
        missing = sorted(set(range(1, TOP_N + 1)) - set(top_ranks))
        reason = f"上位{TOP_N}位の欠け: {missing}"
    else:
        reason = None
    return {
        "schema_version": 3,
        "scope": f"世界ランキング（上位{TOP_N}位＋上位外の日本選手）",
        "full_ranking_available": False,       # ページは全件を表示しない（実HTMLで確認）
        "top_n": TOP_N,
        "structure_verified_on": STRUCTURE_VERIFIED_ON,
        "discipline_code": discipline_code,
        "discipline": DISCIPLINE_NAMES[discipline_code],
        "year_month": f"{year:04d}-{month:02d}",
        "basis_date": basis_date,
        "page_title": title,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "row_count": len(rows),
        "japan_listed_count": sum(1 for r in rows if r["section"] == "japan_listed"),
        "complete": reason is None,
        "incomplete_reason": reason,
        "rows": rows,
    }


def save_snapshot(snapshot_dir: Path, snap: dict) -> str:
    """戻り値: "saved"（完全版を新規保存）/ "exists"（完全版が既にあり据え置き）/ "incomplete_saved"（不完全版を別保存）"""
    base = f"{snap['year_month']}_{snap['discipline_code']}"
    complete_path = snapshot_dir / f"{base}.json"
    incomplete_path = snapshot_dir / f"{base}.incomplete.json"
    if snap["complete"]:
        if complete_path.exists():
            return "exists"
        path, action = complete_path, "saved"
    else:
        path, action = incomplete_path, "incomplete_saved"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return action


def fetch_html(url: str) -> tuple[str, str, str | None]:
    """戻り値: (本文, 転送後の最終URL, Last-Modified ヘッダー or None)。転送は許可（正規URLへの301）。"""
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
    resp.raise_for_status()
    m = re.search(r"charset=([\w.-]+)", resp.headers.get("Content-Type", ""), re.I)
    return decode_body(resp.content, m.group(1) if m else None), resp.url, resp.headers.get("Last-Modified")


def year_from_last_modified(value: str | None) -> tuple[int, int] | None:
    """Last-Modified（RFC 1123）の (年, 月) を返す。無い・読めない場合は None。
    注意: 公開ページの更新時刻であり、基準日の年を直接示すものではない。"""
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return (d.year, d.month) if d else None


def year_evidence(*, year: int, month: int, last_modified: str | None,
                  human: dict | None) -> dict | None:
    """基準年を独立に確認できる根拠を返す。無ければ None（＝保存を保留する）。
    根拠は次のどちらか:
      (a) HTTPの Last-Modified の年月が、保存対象の年月と一致する（サーバーの更新時刻）
      (b) 人が確認した根拠（出典と年）。年が保存対象の年と一致する場合のみ
    ページ本文の「11月3日付」には年が無いため、それだけでは根拠にならない。"""
    lm = year_from_last_modified(last_modified)
    if lm == (year, month):
        return {"kind": "http_last_modified", "value": last_modified}
    if human and human.get("year") == year and str(human.get("source", "")).strip():
        return {"kind": "human_confirmed", "value": str(human["source"]).strip(), "year": year}
    return None


def run_snapshot(*, year_month: str, urls: dict, snapshot_dir: Path, fetch=fetch_html,
                 now_utc: dt.datetime | None = None, human_year: dict | None = None) -> int:
    """種目ごとに独立して取得・保存する。戻り値: 1つでも失敗があれば 1、無ければ 0。
    全種目がURL未設定のときは警告を出して 0 で終了する。"""
    try:
        year_s, month_s = year_month.split("-")
        year, month = int(year_s), int(month_s)
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        print(f"[ERROR] 年月の形式が不正です（YYYY-MM）: {year_month}")
        return 1
    fetched_at = (now_utc or dt.datetime.now(dt.timezone.utc)).isoformat()
    configured = {c: urls.get(c) for c in DISCIPLINE_NAMES}
    if not any(configured.values()):
        print("::warning::badspi の取得URLが未設定です（config/badminton_results.json の badspi_ranking_urls）。"
              "取得せず終了します。")
        return 0
    rc = 0
    for code in DISCIPLINE_NAMES:
        url = configured[code]
        if not url:
            print(f"[SKIP] {code} {DISCIPLINE_NAMES[code]}: URL未設定のため取得しない")
            continue
        try:
            text, final_url, last_modified = fetch(url)
            snap = build_snapshot(text, year=year, month=month, discipline_code=code,
                                  source_url=final_url, fetched_at=fetched_at)
            evidence = year_evidence(year=year, month=month, last_modified=last_modified, human=human_year)
            if evidence is None:
                # 年を独立に確認できない（ページに年が無く、更新時刻の年月も一致しない）→ 保存を保留
                print(f"[HOLD] {code} {DISCIPLINE_NAMES[code]} {snap['year_month']}: 基準年を独立に確認できないため保存を保留"
                      f"（ページは「{snap['basis_date'][5:]}」の月日のみ。Last-Modified={last_modified}）")
                continue
            snap["year_evidence"] = evidence
        except BasisMismatch as err:
            # 保存しない。ページが対象月に更新されるまで待つ（前月データを推測して保存しない）
            print(f"[WAIT] {code} {DISCIPLINE_NAMES[code]}: {err}。保存せず次回の実行で再確認します")
            continue
        except (requests.RequestException, OSError, ParseError) as err:
            print(f"[ERROR] {code} {DISCIPLINE_NAMES[code]}: 取得・解析に失敗（{type(err).__name__}: {err}）")
            rc = 1
            continue
        action = save_snapshot(snapshot_dir, snap)
        print(f"[{action.upper()}] {code} {DISCIPLINE_NAMES[code]} {snap['year_month']} "
              f"行数{snap['row_count']}（日本選手（上位外）{snap['japan_listed_count']}）complete={snap['complete']}"
              + (f" 理由: {snap['incomplete_reason']}" if snap["incomplete_reason"] else ""))
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="badspi 世界ランキングのスナップショット保存")
    ap.add_argument("--year-month", required=True, help="YYYY-MM（ページの基準月と一致させる）")
    ap.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    ap.add_argument("--confirm-year-source", default=None,
                    help="人が基準年を確認した根拠（出典URLや説明）。--confirm-year と併用")
    ap.add_argument("--confirm-year", type=int, default=None, help="人が確認した基準年（YYYY）")
    args = ap.parse_args(argv)
    human = None
    if args.confirm_year_source or args.confirm_year:
        if not (args.confirm_year_source and args.confirm_year):
            print("[ERROR] --confirm-year-source と --confirm-year は両方指定してください")
            return 1
        human = {"source": args.confirm_year_source, "year": args.confirm_year}
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return run_snapshot(year_month=args.year_month, urls=config.get("badspi_ranking_urls", {}),
                        snapshot_dir=Path(args.snapshot_dir), human_year=human)


if __name__ == "__main__":
    sys.exit(main())
