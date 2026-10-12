#!/usr/bin/env python3
"""文学賞の新刊（最新受賞作）を検知し、honozassi.html へ安全に追記する。

- 対象: 本屋大賞 / 直木賞 / 山本周五郎賞 / 大藪春彦賞（Wikipedia受賞一覧を各1リクエストのみ取得）
- 取得エラーは「異常」（終了コード2）として扱い、空の結果と混同しない
- 未登録の最新受賞作がある場合のみ追記。既存レコードは変更せず、追加分のみ。
- 検証（既存件数維持・削除なし・重複IDなし・JavaScript構文）に合格した場合のみ書き換える。
- 新規作なし・検証失敗は、ファイルを変更せず終了コード0。
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (honozassi-monthly-awards-check)"
TIMEOUT = 10
EXIT_ANOMALY = 2
AWARDS = [
    {"key": "honya", "name": "本屋大賞", "wiki": "本屋大賞", "genre": "honya-taisho"},
    {"key": "naoki", "name": "直木賞", "wiki": "直木三十五賞", "genre": "naoki"},
    {"key": "yamamoto", "name": "山本周五郎賞", "wiki": "山本周五郎賞", "genre": "yamamoto"},
    {"key": "oyabu", "name": "大藪春彦賞", "wiki": "大藪春彦賞", "genre": "mystery"},
]


class Anomaly(Exception):
    pass


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\[\s*\d+\s*\]", "", s)  # 脚注番号
    return re.sub(r"[\s　「」『』・:：、。,.!！?？\-]", "", s).lower()


def clean_author(s):
    return re.split(r"[（(]", s)[0].strip()


def clean_title(s):
    s = re.sub(r"\[\s*\d+\s*\]", "", s)
    m = re.search(r"[『「]([^』」]+)[』」]", s)
    return (m.group(1) if m else s).strip()


def fetch(wiki_title):
    url = "https://ja.wikipedia.org/wiki/" + urllib.parse.quote(wiki_title)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def cells_of(tr):
    return [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]


def parse_naoki(soup):
    out = []
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if not rows:
            continue
        head = cells_of(rows[0])
        if len(head) < 4 or head[0] != "回" or "受賞作" not in head[3]:
            continue
        for tr in rows[1:]:
            c = cells_of(tr)
            m = re.search(r"第(\d+)回", c[0]) if c else None
            if not m or len(c) < 4:
                continue
            years = re.findall(r"(\d{4})年", c[1])
            out.append({"kai": int(m.group(1)), "year": int(years[0]) if years else None,
                        "title": clean_title(c[3]), "author": clean_author(c[2])})
    return out


def parse_yamamoto(soup):
    out = []
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if not rows:
            continue
        head = cells_of(rows[0])
        if len(head) < 4 or not head[0].startswith("回") or "受賞・候補作" not in head[3]:
            continue
        for tr in rows[1:]:
            c = cells_of(tr)
            m = re.search(r"第(\d+)回", c[0]) if len(c) >= 4 else None
            if not m or c[1] != "受賞":
                continue
            years = re.findall(r"(\d{4})年", c[0])
            out.append({"kai": int(m.group(1)), "year": int(years[0]) if years else None,
                        "title": clean_title(c[3]), "author": clean_author(c[2])})
    return out


def parse_oyabu(soup):
    out = []
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if not rows:
            continue
        head = cells_of(rows[0])
        if len(head) < 4 or not head[0].startswith("回") or "受賞・候補作" not in head[2]:
            continue
        for tr in rows[1:]:
            c = cells_of(tr)
            m = re.search(r"第(\d+)回", c[0]) if len(c) >= 4 else None
            if not m or c[1] != "受賞":
                continue
            years = re.findall(r"(\d{4})年", c[0])
            out.append({"kai": int(m.group(1)), "year": int(years[0]) if years else None,
                        "title": clean_title(c[2]), "author": clean_author(c[3])})
    return out


def parse_honya(soup):
    """本屋大賞: 「部門」が本屋大賞で順位1の行を受賞作とし、直前の見出しから年を取得する。"""
    out = []
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if not rows:
            continue
        head = cells_of(rows[0])
        if "受賞作" not in head or "順位" not in head or "部門" not in head:
            continue
        year = None
        for h in t.find_all_previous(["h2", "h3", "h4"]):
            m = re.search(r"(\d{4})年", h.get_text(" ", strip=True))
            if m:
                year = int(m.group(1))
                break
        idx, aidx, ridx = head.index("受賞作"), head.index("著者"), head.index("順位")
        for tr in rows[1:]:
            c = cells_of(tr)
            if len(c) != len(head) or c[0] != "本屋大賞":   # 翻訳・発掘など別部門と、rowspanで欠けた行は除外
                continue
            if c[ridx] == "1" and year:
                out.append({"kai": None, "year": year, "title": clean_title(c[idx]),
                            "author": clean_author(c[aidx])})
    return out


PARSERS = {"honya": parse_honya, "naoki": parse_naoki, "yamamoto": parse_yamamoto, "oyabu": parse_oyabu}


def latest_winners(winners):
    if not winners:
        raise Anomaly("受賞作を1件も抽出できませんでした（構造変更の可能性）")
    key = lambda w: (w["kai"] or 0, w["year"] or 0)
    top = max(key(w) for w in winners)
    return [w for w in winners if key(w) == top]


def label_for(award, w):
    if award["key"] == "honya":
        return f"{w['year']}年 本屋大賞"
    return f"第{w['kai']}回 {award['name']}"


def parse_existing(html):
    """既存データの作品（title/author/id/genre）を抽出する。"""
    return [{"id": m.group(1), "title": m.group(2), "author": m.group(3)} for m in re.finditer(
        r'\{ id:"([^"]+)".*?title:"([^"]*)", author:"([^"]*)"', html, re.S)]


def is_registered(w, existing):
    nt = norm(w["title"])
    na = norm(w["author"])
    for e in existing:
        if norm(e["title"]) == nt and (not na or na == norm(e["author"]) or na in norm(e["author"])):
            return True
    return False


def next_ids(html, n):
    nums = [int(m) for m in re.findall(r'\{ id:"b(\d+)"', html)]
    start = max(nums) + 1 if nums else 0
    return [f"b{start + i}" for i in range(n)]


def make_entry(bid, award, w):
    return (
        f'        {{ id:"{bid}", genre:"{award["genre"]}", awardLabel:{json.dumps(label_for(award, w), ensure_ascii=False)}, score:null,\n'
        f'          title:{json.dumps(w["title"], ensure_ascii=False)}, author:{json.dumps(w["author"], ensure_ascii=False)}, age:null, wikipediaExists:false,\n'
        f'          synopsis:"あらすじ未確認", needsCheck:true }},\n'
    )


def insert_entries(html, plan):
    """plan: [(award, entry_text)] を、同じジャンルの最後の作品の直後へ挿入する。"""
    for award, text in plan:
        pos = [m.start() for m in re.finditer(r'genre:"%s"' % re.escape(award["genre"]), html)]
        anchor = pos[-1] if pos else html.rfind('\n        { id:"')
        nxt = html.find('\n        { id:"', anchor)
        if nxt == -1:
            raise Anomaly("挿入位置を特定できません")
        html = html[:nxt + 1] + text + html[nxt + 1:]
    return html


def js_syntax_ok(html):
    m = max(re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.S), key=len, default=None)
    if not m:
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(m)
        path = f.name
    try:
        if not shutil.which("node"):
            return False          # node が無い環境では検証不能として書き換えない
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    finally:
        os.unlink(path)


def validate(old, new, added):
    old_n = len(re.findall(r'\{ id:"', old))
    new_n = len(re.findall(r'\{ id:"', new))
    if new_n != old_n + added:
        return f"件数不一致: {old_n} + {added} != {new_n}"
    ids = re.findall(r'\{ id:"([^"]+)"', new)
    if len(ids) != len(set(ids)):
        return "重複IDあり"
    removed = [l for l in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
               if l.startswith("-") and not l.startswith("---")]
    if removed:
        return f"既存行の削除/改変を検出: {len(removed)}行"
    if not js_syntax_ok(new):
        return "JavaScript構文チェック失敗（またはnode未インストール）"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="honozassi.html")
    ap.add_argument("--dry-run", action="store_true", help="ファイルを書き換えず結果のみ表示")
    args = ap.parse_args()

    html = open(args.html, encoding="utf-8").read()
    existing = parse_existing(html)

    latest = []
    for award in AWARDS:
        try:
            page = fetch(award["wiki"])
        except Exception as e:
            print(f"[異常] {award['name']} の取得に失敗: {type(e).__name__}: {e}")
            return EXIT_ANOMALY
        try:
            winners = PARSERS[award["key"]](BeautifulSoup(page, "html.parser"))
            top = latest_winners(winners)
        except Anomaly as e:
            print(f"[異常] {award['name']}: {e}")
            return EXIT_ANOMALY
        for w in top:
            latest.append((award, w))
        print(f"[取得] {award['name']}: 最新 {len(top)}件 / 抽出 {len(winners)}件")

    new_items = [(a, w) for a, w in latest if not is_registered(w, existing)]
    if not new_items:
        print("新規の受賞作はありません。ファイルは変更しません。")
        return 0
    for a, w in new_items:
        print(f"[新規] {label_for(a, w)} 『{w['title']}』 {w['author']}")
    if args.dry_run:
        return 0

    ids = next_ids(html, len(new_items))
    plan = [(a, make_entry(bid, a, w)) for bid, (a, w) in zip(ids, new_items)]
    try:
        new_html = insert_entries(html, plan)
    except Anomaly as e:
        print(f"[異常] {e}")
        return EXIT_ANOMALY
    err = validate(html, new_html, len(new_items))
    if err:
        print(f"[検証失敗] {err}。ファイルは変更しません。")
        return 0
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(new_html)
    print(f"[更新] {len(new_items)}件追加: {', '.join(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
