"""北本・鴻巣周辺（約20km圏＋指定自治体）のバドミントン大会を収集し、Discord #webhook_local へカード通知する。

方針（細川さん指定）:
  - 種目優先度: 団体戦 > ダブルス（男子複・混合複・シニア複）
  - 対象レベル: 3部（初中級・Cクラス相当）・シニア初中級。1部/2部限定・強豪オープンは除外
  - 最重要項目: 申込受付期間（開始〜締切）。要項に記載がなければ「記載なし」と正直に出す

ハルシネーション対策（実在しない大会・日付を通知しない）:
  1. 大会情報は「実際に取得できたページ/PDFの本文」だけをGeminiに渡して構造化する
     （検索は候補URLの発見にのみ使い、検索回答の文章は事実として採用しない）
  2. 抽出された日付（開催日・申込期間）が本文中に実在する数字か機械的に照合し、
     照合できない申込期間は「記載なし」に落とす。開催日が照合できない大会は破棄する

収集ソース:
  A. 鴻巣市バドミントン連盟HP（要項PDFまで辿る）
  B. minton.jp（Web申込受付中の埼玉県大会）
  C. saibad.jp RSS（埼玉県バドミントン協会）
  D. Gemini + Google検索グラウンディングによる自治体別の候補URL探索
     （北本市広報・スポーツ協会、近隣市町の連盟/体育館告知など）

環境変数:
  DISCORD_WEBHOOK_LOCAL (必須。#webhook_local)
  GEMINI_API_KEY        (必須)

使い方:
  python scripts/fetch_local_badminton_tournaments.py           # 通常運用（新着＋締切間近リマインド）
  python scripts/fetch_local_badminton_tournaments.py --test    # テスト送信（締切済みも「参考」として表示、既送信記録は更新しない）
  python scripts/fetch_local_badminton_tournaments.py --dry-run # 送信せず標準出力のみ
"""
import argparse
import io
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
GEMINI_MODEL_NAME = "gemini-3.6-flash"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "local_badminton_seen.json"
LAST_RUN_PATH = Path(__file__).resolve().parent.parent / "state" / "local_badminton_last_run.json"

MAX_DOC_CHARS = 14000
MAX_SEARCH_DOCS = 40
REMIND_DAYS = 3  # 締切のN日前以内になったら一度だけリマインド

# ---------------------------------------------------------------- 対象エリア
# 自治体名（会場文字列・本文への一致判定に使う）。グループは探索クエリの単位も兼ねる。
REGION_GROUPS = {
    "中核": ["北本市", "鴻巣市"],
    "中南部": ["桶川市", "上尾市", "伊奈町", "蓮田市", "白岡市"],
    "さいたま": ["さいたま市"],
    "北部": ["行田市", "熊谷市", "加須市", "羽生市", "久喜市", "深谷市"],
    "西部": ["吉見町", "東松山市", "川島町", "坂戸市", "鶴ヶ島市", "川越市", "滑川町", "嵐山町", "鳩山町", "日高市"],
}
SAITAMA_WARDS = ["大宮", "西区", "北区", "見沼", "岩槻"]  # さいたま市は対象5区のみ
TARGET_MUNICIPALITIES = [m for ms in REGION_GROUPS.values() for m in ms]

# 会場名だけで市名が書かれていないケースを拾う施設→市町マップ
FACILITY_TO_MUNI = {
    "コスモスアリーナふきあげ": "鴻巣市", "鴻巣市総合体育館": "鴻巣市", "鴻巣市立総合体育館": "鴻巣市",
    "北本市体育センター": "北本市", "北本市総合体育館": "北本市", "北本市文化センター": "北本市",
    "桶川市民体育館": "桶川市", "上尾運動公園体育館": "上尾市", "上尾市民体育館": "上尾市", "上尾市体育館": "上尾市",
    "伊奈町総合体育館": "伊奈町", "伊奈町民体育館": "伊奈町",
    "蓮田市総合市民体育館": "蓮田市", "パルシー": "蓮田市", "白岡市総合体育館": "白岡市", "白岡市民体育館": "白岡市",
    "行田市総合体育館": "行田市", "熊谷市民体育館": "熊谷市", "熊谷スポーツ文化公園": "熊谷市", "くまぴあ": "熊谷市",
    "加須市民体育館": "加須市", "ふじアリーナ": "加須市", "羽生市民体育館": "羽生市", "久喜総合体育館": "久喜市",
    "菖蒲総合体育館": "久喜市", "鷲宮体育館": "久喜市", "栗橋": "久喜市",
    "吉見町民体育館": "吉見町", "東松山市民体育館": "東松山市", "川島町総合体育館": "川島町",
    "坂戸市民総合運動場": "坂戸市", "鶴ヶ島市民体育館": "鶴ヶ島市", "鶴ヶ島市総合体育館": "鶴ヶ島市",
    "川越運動公園": "川越市", "川越市民体育館": "川越市", "川越水上公園": "川越市",
    "滑川町総合体育館": "滑川町", "嵐山町総合体育館": "嵐山町", "鳩山町民体育館": "鳩山町",
    "深谷ビッグタートル": "深谷市", "ビッグタートル": "深谷市", "深谷市民体育館": "深谷市",
    "ひだかアリーナ": "日高市", "日高アリーナ": "日高市", "日高市総合体育館": "日高市",
    "大宮体育館": "さいたま市", "浦和駒場体育館": "", "さいたま市記念総合体育館": "",  # 空=対象外(南部)
    "西区民体育館": "さいたま市", "北区民体育館": "さいたま市", "見沼区民体育館": "さいたま市", "岩槻武道館": "さいたま市",
    "岩槻文化公園": "さいたま市", "宮原": "さいたま市", "大宮武道館": "さいたま市",
}

SEED_PAGES = [
    ("鴻巣市バドミントン連盟", "https://www.r-kounosubad.com/"),
    ("鴻巣市バドミントン連盟(スケジュール)", "https://www.r-kounosubad.com/schedule"),
]

SKIP_DOMAINS = ("youtube.com", "youtu.be", "twitter.com", "x.com", "facebook.com", "instagram.com",
                "amazon.", "rakuten.", "yahoo.co.jp/shopping", "note.com/search", "tiktok.com")

TEAM_RE = re.compile(r"団体|チーム|対抗|リーグ戦")
DOUBLES_RE = re.compile(r"ダブルス|複|ペア|ミックス")
HIGH_ONLY_RE = re.compile(r"1部|２部|2部|Ａ級|A級|A・B|上級|オープン|OP|強豪|県大会|選手権")
LOW_OK_RE = re.compile(r"3部|３部|Ｃ|C|初中級|初級|初心者|中級|シニア|ミドル|レディース|ビギナー|40|50|60|４０|５０|６０|Ｂ|B|エンジョイ|市民|親睦")


# ---------------------------------------------------------------- 取得系
def http_get(url, timeout=25):
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ja"}, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        return r
    except requests.RequestException:
        return None


def pdf_text(content):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:3])
    except Exception:
        return ""


def html_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    return re.sub(r"[ \t　]+", " ", re.sub(r"\n\s*\n+", "\n", soup.get_text("\n"))).strip()


def fetch_doc(url):
    """URLの本文テキストを返す。到達できない/空は None。 -> dict(url,text,is_pdf,links)"""
    if any(d in url for d in SKIP_DOMAINS):
        return None
    r = http_get(url)
    if r is None:
        return None
    ctype = r.headers.get("Content-Type", "").lower()
    if "pdf" in ctype or r.url.lower().endswith(".pdf"):
        text = pdf_text(r.content)
        return {"url": r.url, "text": text[:MAX_DOC_CHARS], "is_pdf": True, "links": []} if len(text) > 80 else None
    if "html" not in ctype and "text" not in ctype:
        return None
    r.encoding = r.apparent_encoding or r.encoding
    html = r.text
    links = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        links.append((a.get_text(" ", strip=True), urljoin(r.url, a["href"])))
    text = html_text(html)
    return {"url": r.url, "text": text[:MAX_DOC_CHARS], "is_pdf": False, "links": links} if len(text) > 80 else None


def collect_seed_docs(log):
    docs = []
    for name, url in SEED_PAGES:
        d = fetch_doc(url)
        if not d:
            log.append(f"[seed] 取得失敗: {name} {url}")
            continue
        d["source"] = name
        docs.append(d)
        pdfs = []
        for label, href in d["links"]:
            if href.lower().split("?")[0].endswith(".pdf") and href not in pdfs:
                pdfs.append(href)
        for href in pdfs[:8]:
            pd_ = fetch_doc(href)
            if pd_:
                pd_["source"] = f"{name}(PDF)"
                docs.append(pd_)
        log.append(f"[seed] {name}: ページ1件 + PDF{min(len(pdfs), 8)}件を確認")
    return docs


def collect_minton_docs(log):
    docs = []
    seen = set()
    for page in range(1, 6):
        r = http_get(f"https://minton.jp/Competition/search?area=3&status=1&page={page}")
        if r is None:
            break
        items = re.findall(r'<article class="listUnit">(.*?)</article>', r.text, re.S)
        if not items:
            break
        for b in items:
            d = re.search(r'href="(/Competition/(?:detail|other)/\d+)"', b)
            v = re.search(r"会場：([^<]*)<", b)
            venue = (v.group(1).strip() if v else "")
            if not d or d.group(1) in seen:
                continue
            seen.add(d.group(1))
            if not region_of(venue):
                continue  # 対象自治体外は取得しない
            doc = fetch_doc("https://minton.jp" + d.group(1))
            if doc:
                doc["source"] = "minton"
                docs.append(doc)
    log.append(f"[minton] Web申込受付中リストから対象自治体の会場 {len(docs)}件")
    return docs


def collect_saibad_docs(log):
    import feedparser
    docs = []
    try:
        feed = feedparser.parse("https://saibad.jp/feed/")
    except Exception:
        log.append("[saibad] RSS取得失敗")
        return docs
    for e in feed.entries[:60]:
        blob = f"{e.get('title', '')} {e.get('summary', '')}"
        if not re.search(r"大会|要項|申込|募集", blob):
            continue
        if not (any(m[:-1] in blob for m in TARGET_MUNICIPALITIES) or "埼玉県" in blob or "県" in blob):
            continue
        doc = fetch_doc(e.get("link", ""))
        if doc:
            doc["source"] = "saibad.jp"
            docs.append(doc)
    log.append(f"[saibad] RSS由来 {len(docs)}件")
    return docs


# ---------------------------------------------------------------- Gemini
def gemini_client():
    from google import genai
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    return genai.Client(api_key=key)


def gemini_search_urls(client, log):
    """自治体グループごとにGoogle検索グラウンディングで候補URLだけを集める（回答文は信用しない）。"""
    from google.genai import types
    urls = []
    queries = []
    for gname, munis in REGION_GROUPS.items():
        area = "・".join(munis)
        if gname == "さいたま":
            area = "さいたま市（大宮区・西区・北区・見沼区・岩槻区）"
        queries.append(f"{area} バドミントン 大会 団体戦 ダブルス 2026年 参加申込 要項（市の連盟・スポーツ協会・体育館・広報の告知ページ）")
        queries.append(f"{area} バドミントン ダブルス シニア 初中級 3部 大会 2026年 募集 申込期間")
    queries.append("北本市 広報きたもと バドミントン 大会 参加者募集 2026")
    queries.append("北本市スポーツ協会 バドミントン大会 2026 要項")
    for q in queries:
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL_NAME,
                contents=f"次の条件に合う埼玉県のバドミントン大会の告知ページを検索してください: {q}",
                config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
            )
            n = 0
            for cand in (resp.candidates or []):
                gm = getattr(cand, "grounding_metadata", None)
                for ch in (getattr(gm, "grounding_chunks", None) or []):
                    uri = getattr(getattr(ch, "web", None), "uri", None)
                    if uri and uri not in urls:
                        urls.append(uri)
                        n += 1
            log.append(f"[search] {q[:34]}… → 候補URL {n}件")
        except Exception as ex:
            log.append(f"[search] 失敗 {q[:24]}…: {type(ex).__name__}: {str(ex)[:120]}")
    return urls


def collect_search_docs(client, log):
    docs = []
    seen = set()
    for uri in gemini_search_urls(client, log)[:MAX_SEARCH_DOCS * 2]:
        if len(docs) >= MAX_SEARCH_DOCS:
            break
        doc = fetch_doc(uri)  # vertexaisearchのリダイレクトはここで実URLへ解決される
        if not doc or doc["url"] in seen:
            continue
        seen.add(doc["url"])
        doc["source"] = "検索(" + urlparse(doc["url"]).netloc + ")"
        docs.append(doc)
    log.append(f"[search] 実在確認できたページ {len(docs)}件")
    return docs


EXTRACT_PROMPT = """あなたはバドミントン大会情報の抽出器です。以下は実際に取得したWebページ/PDFの本文です。
本文に書かれている大会のみを抽出し、JSON配列で返してください。大会が無ければ [] を返します。

厳守ルール:
- 本文に明記されていない値は絶対に推測せず null にする（申込期間・参加費・参加資格は特に注意）。
- 日付は YYYY-MM-DD。年が本文に無い場合は 2026 年（令和8年）とみなす。
- 開催日が過ぎた過去の大会・結果報告・練習会・講習会は含めない。
- 各要素のキー:
  name(大会名), date(開催日), venue(会場名。所在市町がわかれば含める), entry_start(申込開始日), entry_end(申込締切日),
  events(種目の配列。例:["男子ダブルス","混合ダブルス","団体戦"]), classes(クラス/レベルの配列。例:["3部","シニア40歳以上"]),
  eligibility(参加資格の要約), fee(参加費), organizer(主催)
- 出力はJSONのみ。説明文やコードフェンスは不要。

URL: {url}
--- 本文 ---
{text}
"""


def extract_tournaments(client, doc):
    from google.genai import types
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=EXTRACT_PROMPT.format(url=doc["url"], text=doc["text"]),
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(resp.text)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    return [x for x in data if isinstance(x, dict) and x.get("name")]


# ---------------------------------------------------------------- 検証・フィルタ
def norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def md_in_text(iso, text_n):
    """ISO日付の月日が本文（正規化済み）に現れるか。"""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    m, dd = d.month, d.day
    pats = [f"{m}月{dd}日", f"{m}月{dd}", f"{m}/{dd}", f"{m}．{dd}", f"{m}.{dd}", f"{d.year}年{m}月{dd}", f"{d.year}-{m:02d}-{dd:02d}"]
    return any(p in text_n for p in pats)


def region_of(venue_or_text):
    """対象自治体名（該当なしは ''）。"""
    s = venue_or_text or ""
    for fac, muni in FACILITY_TO_MUNI.items():
        if fac in s and muni:
            return muni
    for m in TARGET_MUNICIPALITIES:
        if m == "さいたま市":
            if "さいたま市" in s and any(w in s for w in SAITAMA_WARDS):
                return "さいたま市"
            continue
        if m in s:
            return m
    return ""


def verify_and_normalize(t, doc, today):
    text_n = norm(doc["text"])
    if not md_in_text(t.get("date"), text_n):
        return None, "開催日が本文で確認できない"
    try:
        date = datetime.strptime(t["date"], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, "開催日の形式不正"
    if date < today:
        return None, "開催済み"
    for k in ("entry_start", "entry_end"):
        if t.get(k) and not md_in_text(t[k], text_n):
            t[k] = None  # 本文で照合できない日付は採用しない
    return t, ""


def classify(t):
    """(通過?, 理由, 優先度0=団体/1=ダブルス, 種目ラベル)"""
    events = " ".join(t.get("events") or [])
    classes = " ".join(t.get("classes") or [])
    blob = f"{t.get('name', '')} {events} {classes}"
    if TEAM_RE.search(blob):
        prio = 0
    elif DOUBLES_RE.search(blob):
        prio = 1
    else:
        return False, "種目が団体戦/ダブルスでない", 9
    if classes and not LOW_OK_RE.search(classes) and HIGH_ONLY_RE.search(classes):
        return False, "上級・オープンのみ", prio
    return True, "", prio


def entry_status(t, today):
    def d(k):
        try:
            return datetime.strptime(t[k], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            return None
    s, e = d("entry_start"), d("entry_end")
    if e and e < today:
        return "締切済"
    if s and s > today:
        return "受付前"
    if e:
        return "受付中"
    return "期間未確認"


# ---------------------------------------------------------------- 通知
def build_embed(t, today, test=False, reminder=False):
    prio = t["_prio"]
    status = t["_status"]
    color = {0: 0xE74C3C, 1: 0x3498DB}.get(prio, 0x95A5A6)
    if status == "締切済":
        color = 0x7F8C8D
    period = "記載なし（要項未掲載 or 本文に明記なし）"
    if t.get("entry_start") or t.get("entry_end"):
        period = f"{t.get('entry_start') or '?'} 〜 **{t.get('entry_end') or '記載なし'}**"
    left = ""
    if t.get("entry_end") and status in ("受付中",):
        days = (datetime.strptime(t["entry_end"], "%Y-%m-%d").date() - today).days
        left = f"（あと{days}日）" if days >= 0 else ""
    badge = {"受付中": "🟢受付中", "受付前": "🟡受付前", "締切済": "⚫締切済(参考)", "期間未確認": "⚪申込期間未確認"}[status]
    kind = "🏆団体戦あり" if prio == 0 else "🏸ダブルス"
    title = ("【テスト】" if test else "") + ("⏰締切間近 " if reminder else "") + t["name"]
    fields = [
        {"name": "📝 申込受付期間 ★最重要", "value": f"{period} {left}\n{badge}", "inline": False},
        {"name": "📅 開催日", "value": t["date"], "inline": True},
        {"name": "📍 会場", "value": f"{t.get('venue') or '記載なし'}（{t['_muni']}）", "inline": True},
        {"name": "🎯 種目・クラス", "value": f"{kind}: " + "、".join(t.get("events") or []) + "\n" + ("クラス: " + "、".join(t.get("classes") or []) if t.get("classes") else "クラス: 記載なし"), "inline": False},
    ]
    if t.get("eligibility"):
        fields.append({"name": "👥 参加資格", "value": str(t["eligibility"])[:300], "inline": False})
    if t.get("fee"):
        fields.append({"name": "💴 参加費", "value": str(t["fee"])[:100], "inline": True})
    fields.append({"name": "🔗 要項・情報元", "value": t["_url"][:900], "inline": False})
    return {"title": title[:250], "url": t["_url"], "color": color, "fields": fields,
            "footer": {"text": f"出典: {t['_source']} ／ 本文照合済み"}}


def send_embeds(webhook, embeds, content=None, batch=10):
    ok = True
    first = True
    for i in range(0, len(embeds), batch):
        payload = {"embeds": embeds[i:i + batch]}
        if first and content:
            payload["content"] = content
        first = False
        r = requests.post(webhook, json=payload, timeout=30)
        if r.status_code >= 300:
            print(f"[ERROR] Discord送信失敗 {r.status_code}: {r.text[:200]}")
            ok = False
    return ok


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"notified": {}, "reminded": {}}


def key_of(t):
    return f"{t['date']}|{norm(t['name'])[:24]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = datetime.now(JST).date()
    log = []
    client = gemini_client()
    if client is None:
        print("[ERROR] GEMINI_API_KEY が未設定です。")
        return 1

    docs = collect_seed_docs(log) + collect_minton_docs(log) + collect_saibad_docs(log) + collect_search_docs(client, log)
    # 同一URLの重複除去
    uniq, seen_urls = [], set()
    for d in docs:
        if d["url"] not in seen_urls:
            seen_urls.add(d["url"])
            uniq.append(d)
    log.append(f"[docs] 抽出対象ページ合計 {len(uniq)}件")

    merged, dropped = {}, []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        extracted = list(ex.map(lambda d: extract_tournaments(client, d), uniq))
    for doc, tournaments in zip(uniq, extracted):
        for t in tournaments:
            t, _why = verify_and_normalize(t, doc, today) if t.get("date") else (None, "開催日なし")
            if t is None:
                continue
            muni = region_of(t.get("venue")) or region_of(t.get("name"))
            if not muni:
                dropped.append((t.get("name"), "対象自治体外/会場不明"))
                continue
            ok, why, prio = classify(t)
            if not ok:
                dropped.append((t.get("name"), why))
                continue
            t.update({"_muni": muni, "_prio": prio, "_url": doc["url"], "_source": doc["source"]})
            t["_status"] = entry_status(t, today)
            k = key_of(t)
            old = merged.get(k)
            score = lambda x: sum(1 for f in ("entry_start", "entry_end", "fee", "eligibility") if x.get(f))
            if old is None or score(t) > score(old):
                merged[k] = t

    items = sorted(merged.values(), key=lambda t: (t["_prio"], t["date"]))
    state = load_state()
    embeds, sent_keys, remind_keys = [], [], []
    for t in items:
        k = key_of(t)
        if args.test:
            embeds.append(build_embed(t, today, test=True))
            continue
        if t["_status"] == "締切済":
            continue
        if k not in state["notified"]:
            embeds.append(build_embed(t, today))
            sent_keys.append(k)
        elif t["_status"] == "受付中" and t.get("entry_end") and k not in state["reminded"]:
            days = (datetime.strptime(t["entry_end"], "%Y-%m-%d").date() - today).days
            if 0 <= days <= REMIND_DAYS:
                embeds.append(build_embed(t, today, reminder=True))
                remind_keys.append(k)

    # 実行サマリ（デバッグ用に保存）
    summary = {"run_at": datetime.now(JST).isoformat(timespec="seconds"), "log": log,
               "matched": [{"name": t["name"], "date": t["date"], "muni": t["_muni"], "status": t["_status"],
                            "entry": [t.get("entry_start"), t.get("entry_end")], "events": t.get("events"),
                            "classes": t.get("classes"), "url": t["_url"], "source": t["_source"]} for t in items],
               "dropped": [{"name": n, "reason": w} for n, w in dropped]}
    LAST_RUN_PATH.parent.mkdir(exist_ok=True)
    LAST_RUN_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(log))
    print(f"[result] 条件一致 {len(items)}件 / 除外 {len(dropped)}件 / 送信カード {len(embeds)}件")
    for t in items:
        print(f"  - {t['date']} {t['name']} [{t['_muni']}] {t['_status']} 申込={t.get('entry_start')}〜{t.get('entry_end')} {t['_url']}")

    if args.dry_run:
        return 0
    webhook = os.environ.get("DISCORD_WEBHOOK_LOCAL")
    if not webhook:
        print("[ERROR] DISCORD_WEBHOOK_LOCAL が未設定です。")
        return 1

    if args.test:
        head = (f"🏸 **バドミントン大会通知 テスト送信**（{today}）\n"
                f"対象: 北本・鴻巣周辺 約{len(TARGET_MUNICIPALITIES)}市町 ／ 条件一致 {len(items)}件（締切済みは「参考」表示）")
        if not embeds:
            head += "\n今回は条件に合致する大会を本文確認つきで検出できませんでした（捏造はしていません）。"
        ok = send_embeds(webhook, embeds[:20], content=head)
        return 0 if ok else 1

    if embeds:
        ok = send_embeds(webhook, embeds)
        if ok:
            for k in sent_keys:
                state["notified"][k] = today.isoformat()
            for k in remind_keys:
                state["reminded"][k] = today.isoformat()
            STATE_PATH.parent.mkdir(exist_ok=True)
            STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0 if ok else 1
    print("[info] 新着・リマインド対象なし（送信なし）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
