# -*- coding: utf-8 -*-
"""
badspi.jp の「世界ランキング」（種目別）を保存し、結果通知から参照できるようにするモジュール。

実HTML確認（2026-11-06、5種目すべて。robots.txt は Allow: /）:
- 取得URLは 301 で正規URLへ転送される。config に正規URLを設定（推測で作っていない）。
- タイトルは「世界ランキング 男子シングルス（11月3日付）」。基準日は月日のみで、年は無い。
- 表示範囲は「上位15位」＋「上位15位より下の日本選手」。全ランキングではない。
- 行は「順位・国・氏名・ポイント・大会数」の5行組。ダブルスの氏名は「A／B」。

年の扱い（重要）:
- 年は確定できない。ページ更新時刻（Last-Modified 等）は基準日の年の証明にはならないため使わない。
- 保存する基準日は「11月3日付（年未確認）」のように年不明のまま記録し、年を捏造しない。
- 順位データ自体は有効（月日・取得時刻・取得元を併記して保存する）。古さは取得時刻と基準月日で判断できる。

保存ルール:
- 完全（上位1〜15位が欠けなく揃い、重複なし）なものは「<MM-DD>_<種目>.json」。
  同じ基準月日の完全版が既にあれば上書きしない。
- 不完全は「.incomplete.json」に別保存し、完全版を置き換えない。
- 種目ごとに独立。1種目の失敗は他種目を止めない。URL未設定の種目は個別にSKIP。
- 通知側は load_latest_rankings() で完全版のみを読み、取得時刻が最新のものを使う。
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
PAIR_SEP = "／"

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


class ParseError(ValueError):
    pass


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
                "members": members,
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


def build_snapshot(html_text: str, *, discipline_code: str, source_url: str, fetched_at: str) -> dict:
    """ページを解析する。年は読まない・推測しない（year は None、year_status は unconfirmed）。"""
    if discipline_code not in DISCIPLINE_NAMES:
        raise ParseError(f"未知の種目コード: {discipline_code}")
    title = parse_title(html_text)
    if not title.startswith(TITLE_PREFIX) or TITLE_KEYWORD[discipline_code] not in title:
        raise ParseError(f"ページの種目が一致しない（{discipline_code}を期待、タイトル: {title[:40]}）")
    basis_month, basis_day = parse_basis_month_day(title)
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
    basis_md = f"{basis_month:02d}-{basis_day:02d}"
    return {
        "schema_version": 4,
        "scope": f"世界ランキング（上位{TOP_N}位＋ページ掲載の日本選手のみ。全ランキングではない）",
        "full_ranking_available": False,
        "top_n": TOP_N,
        "structure_verified_on": STRUCTURE_VERIFIED_ON,
        "discipline_code": discipline_code,
        "discipline": DISCIPLINE_NAMES[discipline_code],
        "basis_month_day": basis_md,
        "year": None,                       # 年は確定できないため記録しない
        "year_status": "unconfirmed",
        "basis_label": f"{basis_month}月{basis_day}日付（年未確認）",
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
    """戻り値: "saved"（完全版を新規保存）/ "exists"（同じ基準月日の完全版が既にある）/ "incomplete_saved"（不完全版）"""
    base = f"{snap['basis_month_day']}_{snap['discipline_code']}"
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


def load_latest_rankings(snapshot_dir: Path) -> dict:
    """種目コード → 取得時刻が最も新しい完全版。不完全版・壊れたファイルは読まない。"""
    latest: dict[str, dict] = {}
    if not snapshot_dir.is_dir():
        return latest
    for path in snapshot_dir.glob("*.json"):
        if path.name.endswith(".incomplete.json"):
            continue
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(snap, dict) or not snap.get("complete") or snap.get("discipline_code") not in DISCIPLINE_NAMES:
            continue
        code = snap["discipline_code"]
        if code not in latest or str(snap.get("fetched_at", "")) > str(latest[code].get("fetched_at", "")):
            latest[code] = snap
    return latest


def find_rank(snap: dict, player_names: list[str]) -> int | None:
    """選手（シングルス）または構成2名（ダブルス）の順位を返す。
    誤結合を防ぐため、氏名の完全一致のみ。候補が複数・0件・名前が空の場合は None（順位不明）。"""
    names = [n.strip() for n in player_names]
    if not names or any(not n for n in names):
        return None
    hits = []
    for row in snap.get("rows", []):
        if len(names) == 1:
            if row["name"] == names[0] and len(row["members"]) == 1:
                hits.append(row["rank"])
        elif len(names) == 2 and len(row["members"]) == 2:
            if sorted(row["members"]) == sorted(names):
                hits.append(row["rank"])
    return hits[0] if len(hits) == 1 else None


def fetch_html(url: str) -> tuple[str, str]:
    """戻り値: (本文, 転送後の最終URL)。転送は許可（正規URLへの301）。"""
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
    resp.raise_for_status()
    m = re.search(r"charset=([\w.-]+)", resp.headers.get("Content-Type", ""), re.I)
    return decode_body(resp.content, m.group(1) if m else None), resp.url


def run_snapshot(*, urls: dict, snapshot_dir: Path, fetch=None,
                 now_utc: dt.datetime | None = None) -> int:
    """種目ごとに独立して取得・保存する。戻り値: 1つでも失敗があれば 1、無ければ 0。
    全種目がURL未設定のときは警告を出して 0 で終了する。fetch 未指定時は呼び出し時に fetch_html を解決する。"""
    fetch = fetch or fetch_html
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
            text, final_url = fetch(url)
            snap = build_snapshot(text, discipline_code=code, source_url=final_url, fetched_at=fetched_at)
        except (requests.RequestException, OSError, ParseError) as err:
            print(f"[ERROR] {code} {DISCIPLINE_NAMES[code]}: 取得・解析に失敗（{type(err).__name__}: {err}）")
            rc = 1
            continue
        action = save_snapshot(snapshot_dir, snap)
        print(f"[{action.upper()}] {code} {DISCIPLINE_NAMES[code]} 基準 {snap['basis_label']} "
              f"行数{snap['row_count']}（日本選手（上位外）{snap['japan_listed_count']}）complete={snap['complete']}"
              + (f" 理由: {snap['incomplete_reason']}" if snap["incomplete_reason"] else ""))
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="badspi 世界ランキングの保存（基準年は確定しない）")
    ap.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    args = ap.parse_args(argv)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    # 取得関数は呼び出し時に解決する（既定引数は定義時に束縛され、テストでの差し替えが効かないため）
    return run_snapshot(urls=config.get("badspi_ranking_urls", {}), snapshot_dir=Path(args.snapshot_dir),
                        fetch=fetch_html)


if __name__ == "__main__":
    sys.exit(main())
