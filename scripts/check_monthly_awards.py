#!/usr/bin/env python3
"""文学賞の新刊（最新受賞作）を検知し、honozassi.html へ安全に追記する。

- 対象: 本屋大賞 / 直木賞 / 山本周五郎賞 / 大藪春彦賞（Wikipedia受賞一覧を各1リクエストのみ取得）
- 取得エラーは「異常」（終了コード2）として扱い、空の結果と混同しない
- 未登録の最新受賞作がある場合のみ追記。既存レコードは変更せず、追加分のみ。
- 検証（既存件数維持・削除なし・重複IDなし・JavaScript構文）に合格した場合のみ書き換える。
- 新規作なし・検証失敗は、ファイルを変更せず終了コード0。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (honozassi-monthly-awards-check)"
TIMEOUT = 10
EXIT_PARSE_ERROR = 2   # 取得失敗・構造不一致（PARSE_ERROR）は異常終了
EXIT_ANOMALY = EXIT_PARSE_ERROR
AWARDS = [
    {"key": "honya", "name": "本屋大賞", "wiki": "本屋大賞", "genre": "honya-taisho"},
    {"key": "naoki", "name": "直木賞", "wiki": "直木三十五賞", "genre": "naoki"},
    {"key": "yamamoto", "name": "山本周五郎賞", "wiki": "山本周五郎賞", "genre": "yamamoto"},
    {"key": "oyabu", "name": "大藪春彦賞", "wiki": "大藪春彦賞", "genre": "mystery"},
    {"key": "eikawa", "name": "吉川英治文学新人賞", "wiki": "吉川英治文学新人賞", "genre": "otherlit"},
    {"key": "jsa", "name": "日本推理作家協会賞", "wiki": "日本推理作家協会賞", "genre": "mystery"},
]
# 日本推理作家協会賞: 完全一致のAllowlist（短編・評論・翻訳などは除外）
JSA_ALLOW = {"長編部門", "長編および連作短編集部門"}
# 参考情報のみ（honozassi.htmlには登録しない）
REFERENCE_AWARD = {"key": "akutagawa", "name": "芥川賞", "wiki": "芥川龍之介賞"}


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


def col(head, name, exact=False):
    """ヘッダーから列位置を返す（見つからなければ None）。exact=True は完全一致のみ。"""
    for i, h in enumerate(head):
        if (h == name) if exact else (name in h):
            return i
    return None


def winner_tables(soup, is_candidate, required):
    """候補表（受賞表）を列挙する。候補表なのに必須列が欠ける場合は PARSE_ERROR。
    返り値: [(head, rows, table), ...]。候補表が1つもなければ PARSE_ERROR（構造変更の可能性）。"""
    found = []
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if not rows:
            continue
        head = cells_of(rows[0])
        if not is_candidate(head):
            continue
        missing = [name for name, exact in required if col(head, name, exact) is None]
        if missing:
            raise Anomaly(f"PARSE_ERROR: 受賞表の必須列が欠落: {missing} / 検出ヘッダー={head}")
        found.append((head, rows[1:], t))
    if not found:
        raise Anomaly("PARSE_ERROR: 受賞作の表が見つかりません（構造変更の可能性）")
    return found


def parse_naoki(soup):
    out = []
    req = [("回", False), ("受賞者", False), ("受賞作", False)]
    for head, rows, tbl in winner_tables(soup, lambda h: bool(h) and (h[0] == "回" or "受賞作" in h), req):
        ci, ai, ti, yi = col(head, "回"), col(head, "受賞者"), col(head, "受賞作"), col(head, "対象期間")
        for tr in rows:
            c = cells_of(tr)
            m = re.search(r"第(\d+)回", c[ci]) if len(c) > max(ci, ai, ti) else None
            if not m:
                continue
            years = re.findall(r"(\d{4})年", c[yi]) if yi is not None and len(c) > yi else []
            none = "該当" in c[ti] or "該当" in c[ai]          # 公式に受賞作なし
            out.append({"kai": int(m.group(1)), "year": int(years[0]) if years else None,
                        "title": "" if none else clean_title(c[ti]),
                        "author": "" if none else clean_author(c[ai]), "none": none})
    return out


def parse_yamamoto(soup):
    """山本周五郎賞・吉川英治文学新人賞: 回（年）/賞/受賞・候補者/受賞・候補作"""
    out = []
    req = [("回", False), ("賞", True), ("受賞・候補者", False), ("受賞・候補作", False)]
    for head, rows, tbl in winner_tables(soup, lambda h: bool(h) and (h[0].startswith("回") or "受賞・候補作" in h), req):
        ki, si, ai, ti = col(head, "回"), col(head, "賞", exact=True), col(head, "受賞・候補者"), col(head, "受賞・候補作")
        for tr in rows:
            c = cells_of(tr)
            if len(c) <= max(ki, si, ai, ti):
                continue
            m = re.search(r"第(\d+)回", c[ki])
            if not m or c[si] != "受賞":
                continue
            years = re.findall(r"(\d{4})年", c[ki])
            out.append({"kai": int(m.group(1)), "year": int(years[0]) if years else None,
                        "title": clean_title(c[ti]), "author": clean_author(c[ai])})
    return out


def parse_oyabu(soup):
    """大藪春彦賞: 回（年）/（賞欄）/受賞・候補作/著者"""
    out = []
    req = [("回", False), ("受賞・候補作", False), ("著者", False)]
    for head, rows, tbl in winner_tables(soup, lambda h: bool(h) and h[0].startswith("回"), req):
        ki, ti, ai = col(head, "回"), col(head, "受賞・候補作"), col(head, "著者")
        for tr in rows:
            c = cells_of(tr)
            if len(c) <= max(ki, ti, ai, 1):
                continue
            m = re.search(r"第(\d+)回", c[ki])
            if not m or c[1] != "受賞":
                continue
            years = re.findall(r"(\d{4})年", c[ki])
            out.append({"kai": int(m.group(1)), "year": int(years[0]) if years else None,
                        "title": clean_title(c[ti]), "author": clean_author(c[ai])})
    return out


def parse_honya(soup):
    """本屋大賞: 「部門」が本屋大賞で順位1の行を受賞作とし、直前の見出しから年を取得する。"""
    out = []
    req = [("順位", False), ("受賞作", False), ("著者", False)]
    for head, rows, tbl in winner_tables(soup, lambda h: "順位" in h, req):
        # 年は表の直前の見出しから取得（見出しがなければ抽出対象外）
        year = None
        for h in tbl.find_all_previous(["h2", "h3", "h4"]):
            m = re.search(r"(\d{4})年", h.get_text(" ", strip=True))
            if m:
                year = int(m.group(1))
                break
        ri, ti, ai, di = col(head, "順位"), col(head, "受賞作"), col(head, "著者"), col(head, "部門")
        for tr in rows:
            c = cells_of(tr)
            if len(c) != len(head):                  # rowspanで欠けた行は除外
                continue
            if di is not None and c[di] != "本屋大賞":   # 翻訳・発掘など別部門は除外
                continue
            if c[ri] == "1" and year:
                out.append({"kai": None, "year": year, "title": clean_title(c[ti]), "author": clean_author(c[ai])})
    return out


def parse_jsa(soup):
    """日本推理作家協会賞: 回ごとの見出し（第N回（YYYY年））配下の「部門 - 著者 『題名』」を抽出。
    Allowlist（JSA_ALLOW）と完全一致する部門のみ採用する。"""
    body = soup.find("div", class_="mw-parser-output") or soup
    text = " ".join(body.get_text("\n", strip=True).split("\n"))
    text = re.sub(r"\[\s*\d+\s*\]", "", text)
    marks = list(re.finditer(r"第(\d+)回\s*（\s*(\d{4})年[^）]*）", text))
    if not marks:
        raise Anomaly("PARSE_ERROR: 日本推理作家協会賞: 回の見出しを1件も抽出できませんでした")
    out = []
    for i, m in enumerate(marks):
        if int(m.group(1)) != max(int(x.group(1)) for x in marks):
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        seg = text[m.end():end]
        for cut in ("脚注", "注釈", "出典"):
            if cut in seg:
                seg = seg[:seg.index(cut)]
        # 部門ラベル = 各「 - 」の直前の語句（直前の『』「」より後）を、丸括弧注記を含む全文のまま取得する。
        # 注記を除去せず stripped 文字列で Allowlist と完全一致比較する（「長編部門（試行）」「長編部門A」等は拒否）。
        hy = list(re.finditer(r"\s-\s", seg))

        def label_at(k):
            before = seg[:hy[k].start()]
            cut_pos = max(before.rfind("』"), before.rfind("」")) + 1
            return re.sub(r"^[\s、]+", "", before[cut_pos:]).strip(), cut_pos

        for k in range(len(hy)):
            label, _ = label_at(k)
            if label not in JSA_ALLOW:     # 完全一致のみ採用（未知・括弧注記付き・類似名称は全て拒否）
                continue
            start = hy[k].end()
            stop = label_at(k + 1)[1] if k + 1 < len(hy) else len(seg)
            for am in re.finditer(r"([^、『「]+?)\s*『\s*([^』]+?)\s*』", seg[start:stop]):
                out.append({"kai": int(m.group(1)), "year": int(m.group(2)), "dept": label,
                            "title": am.group(2).strip(), "author": clean_author(am.group(1))})
    return out


PARSERS = {"honya": parse_honya, "naoki": parse_naoki, "yamamoto": parse_yamamoto,
           "oyabu": parse_oyabu, "eikawa": parse_yamamoto, "jsa": parse_jsa}


def latest_winners(winners):
    """最新回の受賞作を返す。最新回が『該当作品なし』の場合は空リスト（受賞なし）。"""
    if not winners:
        return []
    key = lambda w: (w["kai"] or 0, w["year"] or 0)
    top = max(key(w) for w in winners)
    return [w for w in winners if key(w) == top and not w.get("none")]


def label_for(award, w):
    if award["key"] == "honya":
        return f"{w['year']}年 本屋大賞"
    if award["key"] == "jsa":
        return f"第{w['kai']}回 日本推理作家協会賞（{w['dept']}）"
    return f"第{w['kai']}回 {award['name']}"


def parse_existing(html):
    """既存データの作品（id/title/author）を抽出する（照合用）。"""
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


def make_entry(bid, award, w, label=None):
    label = label or label_for(award, w)
    return (
        f'        {{ id:"{bid}", genre:"{award["genre"]}", awardLabel:{json.dumps(label, ensure_ascii=False)}, score:null,\n'
        f'          title:{json.dumps(w["title"], ensure_ascii=False)}, author:{json.dumps(w["author"], ensure_ascii=False)}, age:null, wikipediaExists:false,\n'
        f'          synopsis:"あらすじ未確認", needsCheck:true }},\n'
    )


def insert_entries(html, plan):
    """plan: [(award, entry_text)] を同じジャンルの最後の作品の直後へ挿入する（既存行は変更しない）。"""
    for award, text in plan:
        pos = [m.start() for m in re.finditer(r'genre:"%s"' % re.escape(award["genre"]), html)]
        anchor = pos[-1] if pos else html.rfind('\n        { id:"')
        nxt = html.find('\n        { id:"', anchor)
        if nxt == -1:
            raise Anomaly("挿入位置を特定できません")
        html = html[:nxt + 1] + text + html[nxt + 1:]
    return html


NODE_LOAD = r"""
const fs = require('fs');
const h = fs.readFileSync(process.argv[2], 'utf8');
const s = h.indexOf('const booksData = [');
const e = s < 0 ? -1 : h.indexOf('\n    ];', s);
if (s < 0 || e < 0) { console.error('booksData not found'); process.exit(3); }
const arr = (new Function('return ' + h.slice(s + 'const booksData = '.length, e + 6)))();
process.stdout.write(JSON.stringify(arr));
"""


def load_records(path):
    """node で booksData を評価して、全レコードをJSONとして取得する（構文・構造の検証を兼ねる）。"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(NODE_LOAD)
        script = f.name
    try:
        r = subprocess.run(["node", script, path], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise ValidationError("node が見つからないため検証できません")
    finally:
        os.unlink(script)
    if r.returncode != 0:
        raise ValidationError(f"booksData の評価に失敗: {r.stderr.strip()[:200]}")
    data = json.loads(r.stdout)
    if not isinstance(data, list):
        raise ValidationError("booksData が配列ではありません")
    return data


def canon(rec):
    return json.dumps(rec, sort_keys=True, ensure_ascii=False)


def validate_new(old_path, new_path, added):
    """既存レコードをID単位で完全一致比較し、追加分のみであることを検証する。"""
    old = load_records(old_path)
    new = load_records(new_path)
    if len(new) != len(old) + added:
        raise ValidationError(f"件数不一致: {len(old)} + {added} != {len(new)}")
    ids_new = [r.get("id") for r in new]
    if len(ids_new) != len(set(ids_new)):
        raise ValidationError("重複IDあり")
    new_by_id = {r["id"]: r for r in new}
    for r in old:
        if r["id"] not in new_by_id or canon(new_by_id[r["id"]]) != canon(r):
            raise ValidationError(f"既存レコードが変化: {r.get('id')}")
    old_ids = {r["id"] for r in old}
    added_ids = [i for i in ids_new if i not in old_ids]
    if len(added_ids) != added:
        raise ValidationError("追加件数が想定と一致しません")
    return added_ids


def atomic_replace(path, text):
    """一時ファイルへ書き出してから置換する。失敗時は一時ファイルを消し、元ファイルは変更しない。"""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".honozassi-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def merge_new_items(new_items):
    """同一作品（正規化した書名・著者が一致）の複数受賞を1レコードにまとめる。
    賞ラベルは「／」ではなく " / " で連結し、同一ラベルは重ねない（再実行でも二重連結しない）。"""
    merged = {}
    for a, w in new_items:
        key = (norm(w["title"]), norm(w["author"]))
        g = merged.setdefault(key, {"award": a, "w": w, "labels": []})
        label = label_for(a, w)
        if label not in g["labels"]:
            g["labels"].append(label)
    return list(merged.values())


def write_step_summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def akutagawa_summary(soup):
    """芥川賞: 参考情報としてStep Summaryに出すための最新の受賞作（登録はしない）。"""
    rows = [w for w in parse_naoki(soup) if "該当" not in w["title"] and "該当" not in w["author"]]
    if not rows:
        return None
    w = max(rows, key=lambda x: x["kai"])
    return {"kai": w["kai"], "author": w["author"], "title": w["title"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="honozassi.html")
    ap.add_argument("--dry-run", action="store_true", help="ファイルを書き換えず結果のみ表示")
    args = ap.parse_args(argv)

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
        except Anomaly as e:
            print(f"[異常] {award['name']}: {e}")
            return EXIT_ANOMALY
        top = latest_winners(winners)   # 0件は「最新回は受賞なし」（異常ではない）
        for w in top:
            latest.append((award, w))
        print(f"[取得] {award['name']}: 最新 {len(top)}件 / 抽出 {len(winners)}件")

    # 参考情報（芥川賞）: 失敗してもhonozassi.htmlの処理・終了コードには影響させない
    try:
        ak = akutagawa_summary(BeautifulSoup(fetch(REFERENCE_AWARD["wiki"]), "html.parser"))
        if ak:
            write_step_summary([
                "### 参考情報: 芥川賞",
                f"- 第{ak['kai']}回: {ak['author']} 『{ak['title']}』",
                "- honozassi.html には登録していません（参考表示のみ）。",
            ])
            print(f"[参考] 芥川賞 第{ak['kai']}回 {ak['author']} 『{ak['title']}』")
    except Exception as e:
        print(f"[参考] 芥川賞の取得を省略: {type(e).__name__}")

    new_items = [(a, w) for a, w in latest if not is_registered(w, existing)]
    if not new_items:
        print("新規の受賞作はありません。ファイルは変更しません。")
        return 0
    for a, w in new_items:
        print(f"[新規] {label_for(a, w)} 『{w['title']}』 {w['author']}")
    if args.dry_run:
        return 0

    groups = merge_new_items(new_items)
    ids = next_ids(html, len(groups))
    plan = [(g["award"], make_entry(bid, g["award"], g["w"], " / ".join(g["labels"])))
            for bid, g in zip(ids, groups)]
    try:
        new_html = insert_entries(html, plan)
        # 一時ファイルに書き出して検証（元ファイルはまだ変更しない）
        d = os.path.dirname(os.path.abspath(args.html))
        fd, tmp = tempfile.mkstemp(prefix=".honozassi-check-", suffix=".html", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new_html)
        try:
            validate_new(args.html, tmp, len(groups))
            if not js_syntax_ok(new_html):
                raise ValidationError("JavaScript構文チェック失敗")
        finally:
            os.unlink(tmp)
    except (ValidationError, Anomaly) as e:
        print(f"[検証失敗] {e}。ファイルは変更しません。")
        return 0
    atomic_replace(args.html, new_html)
    print(f"[更新] {len(groups)}件追加: {', '.join(ids)}")
    return 0


class ValidationError(Exception):
    pass


def js_syntax_ok(html):
    m = max(re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.S), key=len, default=None)
    if not m:
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(m)
        path = f.name
    try:
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except FileNotFoundError:
        return False
    finally:
        os.unlink(path)


if __name__ == "__main__":
    sys.exit(main())
