# -*- coding: utf-8 -*-
"""
ニュース自動配信bot(北本市安全安心情報・地域/主要ニュース・株価/経済ニュース)

30分ごとに実行される想定の「準リアルタイム」配信。前回までに送信済みの記事URL/IDは
state/news_seen.json に記録し、そのURL/IDと重複するものはスキップして
「新しく見つかった記事のみ」を都度Discordへ送信する(株価は都度変動するため
重複排除の対象外で、実行するたびに最新値を送る)。

配信内容(2026-08-26、ローカル/全国を別Webhookに完全分離):
  DISCORD_WEBHOOK_LOCAL  : 北本市安全安心情報(新着) + 埼玉・県央ローカルニュース
  DISCORD_WEBHOOK_NEWS   : 全国・総合主要ニュース(Yahoo!トップピックス)
  DISCORD_WEBHOOK_MARKET : 株価(日経平均/ドル円) + 経済ニュース

ローカル系(あんぜんねっと・埼玉ローカルニュース)と全国系(主要ニュース)は、
1つのDiscordメッセージに混在させず、送信先Webhookも完全に分離している。

情報源:
  - 北本市安全安心情報: あんぜんねっと(https://anzn.net/sp/?11217F&r1=1)
    ※このページはEUC-JPエンコーディングなので明示的にデコードする
  - 埼玉・県央ローカルニュース: Google ニュース検索RSS(個人利用目的)。
    「埼玉県」丸ごとの検索だと県全体の広域予算ニュースや、埼玉県民が他県で
    巻き込まれた事故・事件、ドラマのロケ地情報等のノイズが多かったため、
    北本市・桶川市・鴻巣市・上尾市・さいたま市大宮区の市区町村名によるOR検索
    + ノイズ除外キーワード(is_saitama_local_noise、2026-08-26追加)に変更した。
  - 主要ニュース/経済ニュース: Yahoo!ニュース トピックスRSS
  - 日経平均株価: Yahoo!ファイナンスの銘柄ページに埋め込まれたJSONを抽出
  - ドル円: open.er-api.com(無料・APIキー不要の為替レートAPI)

環境変数:
  DISCORD_WEBHOOK_LOCAL  (必須)
  DISCORD_WEBHOOK_NEWS   (必須)
  DISCORD_WEBHOOK_MARKET (必須)
"""
import datetime
import json
import os
import re
import sys
import urllib.parse

import feedparser
import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15
DISCORD_CHUNK_LIMIT = 1900  # Discordの2000文字制限に対する安全マージン

ANZN_URL = "https://anzn.net/sp/?11217F&r1=1"

# 「埼玉県」丸ごとでの検索は、県全体の広域予算ニュースや、埼玉県民が他県で
# 巻き込まれた事故・事件、ドラマのロケ地情報等のノイズが多かったため、
# 北本市を中心とした生活圏(県央エリア)の市区町村名だけをOR検索する
# クエリに変更した(2026-08-26)。
SAITAMA_LOCAL_AREAS = ["北本市", "桶川市", "鴻巣市", "上尾市", "大宮区"]


def _build_saitama_local_rss_url():
    query = "(" + " OR ".join(SAITAMA_LOCAL_AREAS) + ") when:1d"
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"


GOOGLE_NEWS_SAITAMA_RSS = _build_saitama_local_rss_url()

# 上記のOR検索だけでは除外しきれない広域政治・予算ニュースや、地域名の
# 偶然の一致(例: 「北本市の男性が(他県)で事故」のような他県発の事件・事故に
# 埼玉県央の居住者が巻き込まれただけの記事)を弾くための追加フィルタ。
SAITAMA_NOISE_KEYWORDS = [
    "予算案", "補正予算", "当初予算", "県議会", "県知事", "県政",
    "ロケ地", "撮影地", "撮影スポット", "聖地巡礼",
]
SAITAMA_INCIDENT_KEYWORDS = ["死亡", "死去", "事故", "事件", "逮捕", "重体", "重傷", "遺体", "行方不明"]
# 県央エリア以外の主要地名(これらと事件・事故キーワードが同時に出てくる場合、
# 「埼玉県央の住民が他県で事故等に遭った」という実質県外ニュースとみなして除外する)
NON_LOCAL_PLACE_HINTS = [
    "伊豆", "静岡", "山梨", "長野", "新潟", "群馬", "栃木", "茨城",
    "千葉", "東京", "神奈川", "北海道", "沖縄", "大阪", "京都", "兵庫",
    "福岡", "愛知", "岐阜", "三重", "滋賀", "奈良", "和歌山",
]


def is_saitama_local_noise(title):
    """埼玉地域ニュースの候補記事のうち、県央の生活圏ニュースとして
    ふさわしくないもの(広域政治・予算ニュース、他県で起きた事故・事件に
    県央住民が巻き込まれただけの記事、ドラマのロケ地情報等)をTrueで返す。
    """
    if any(kw in title for kw in SAITAMA_NOISE_KEYWORDS):
        return True
    if any(kw in title for kw in SAITAMA_INCIDENT_KEYWORDS) and \
            any(place in title for place in NON_LOCAL_PLACE_HINTS):
        return True
    return False


YAHOO_TOP_PICKS_RSS = "https://news.yahoo.co.jp/rss/topics/top-picks.xml"
YAHOO_BUSINESS_RSS = "https://news.yahoo.co.jp/rss/categories/business.xml"
YAHOO_FINANCE_NIKKEI225_URL = "https://finance.yahoo.co.jp/quote/998407.O"
EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"

# 米国株価指数(株探・米国株版。サーバーサイドレンダリングで静的HTMLから
# 取得可能なことを実際に確認済み)。
KABUTAN_US_INDICES = [
    {"label": "S&P500", "url": "https://us.kabutan.jp/indexes/%5ESPX"},
    {"label": "NASDAQ総合", "url": "https://us.kabutan.jp/indexes/%5EIXIC"},
    {"label": "SOX半導体指数", "url": "https://us.kabutan.jp/indexes/%5ESOX"},
]
# WTI原油先物は、finance.yahoo.co.jpに先物そのもののページが無く(ETF/投資信託
# のページしか無い)、みんかぶ先物(fu.minkabu.jp)はJS動的レンダリングのため
# 静的取得できないことを確認済み。無理に不正確なデータ(ETF価格等)を「原油
# 先物」として出すより、未実装のままにする方が安全と判断した。

# 重複排除に使う「見た記事」の記録先。取得件数を少し多めに見ておき、
# 30分間隔のポーリングで新着を取りこぼしにくくする。
ANZN_ITEM_LIMIT = 10
RSS_ITEM_LIMIT = 10

STATE_PATH = "state/news_seen.json"
STATE_RETENTION_DAYS = 14  # 古い記録は掃除して肥大化を防ぐ

# 一般ニュースフィード(Yahoo!トップピックス等)にスポーツ記事が混入した場合、
# #webhook_news ではなく #webhook_sports_culture へ振り分けるためのキーワード。
# タイトルにこれらの語が含まれていれば「スポーツニュース」とみなす。
SPORTS_KEYWORDS = [
    "野球", "プロ野球", "セ・リーグ", "パ・リーグ", "甲子園", "MLB", "大谷翔平",
    "西武", "日本ハム", "ドーム", "1軍", "2軍", "内野手", "外野手", "投手",
    "負傷交代", "スタメン", "先発", "本塁打", "打点", "防御率",
    "阪神", "巨人", "読売ジャイアンツ", "ロッテ", "ソフトバンク", "楽天イーグルス",
    "オリックス", "DeNA", "ベイスターズ", "ヤクルト", "広島東洋カープ", "中日ドラゴンズ",
    "Jリーグ", "サッカー", "J1", "J2", "バドミントン", "F1", "MotoGP", "モータースポーツ",
]


def is_sports_related(title):
    return any(kw in title for kw in SPORTS_KEYWORDS)


# ============================================================
# 既送信記事の状態管理(state/news_seen.json)
# ============================================================

def load_seen_state():
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as err:
        print(f"[WARN] {STATE_PATH} の読み込みに失敗したため、空の状態から開始します: {err}")
        return {}


def save_seen_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def prune_old_entries(state, now):
    cutoff = (now - datetime.timedelta(days=STATE_RETENTION_DAYS)).isoformat()
    return {key: seen_at for key, seen_at in state.items() if seen_at >= cutoff}


def dedupe_new_items(items, key_field, state, now):
    """
    itemsのうちstateに無いもの(=未送信)だけを残して返す。
    stateには副作用として今回分のキーを書き込む(呼び出し側でsave_seen_stateすること)。
    """
    new_items = []
    for item in items:
        key = item.get(key_field)
        if not key:
            # URL等が取れない項目は重複判定できないため、常に「新着」として扱う
            new_items.append(item)
            continue
        if key in state:
            continue
        state[key] = now.isoformat()
        new_items.append(item)
    return new_items


# ============================================================
# 北本市安全安心情報(あんぜんねっと)
# ============================================================

def fetch_anzn_new_arrivals(limit=ANZN_ITEM_LIMIT):
    """あんぜんねっとの新着(鴻巣市・桶川市・北本市の消防出動情報等)を取得する。"""
    try:
        resp = requests.get(ANZN_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        # ページのmeta charsetがEUC-JPなので明示的にデコードする
        html = resp.content.decode("euc-jp", errors="replace")
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] あんぜんねっとの取得に失敗しました: {err}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []
    for block in soup.select('div[data-role="collapsible"]')[:limit]:
        h3 = block.find("h3")
        if not h3:
            continue
        small_tags = h3.find_all("small")
        datetime_text = small_tags[0].get_text(strip=True) if len(small_tags) > 0 else ""
        city_text = small_tags[1].get_text(strip=True) if len(small_tags) > 1 else ""

        # h3内、small/img以外のテキスト(種別・場所)を抽出
        for tag in h3.find_all(["small", "img"]):
            tag.extract()
        summary_text = h3.get_text(" ", strip=True)

        permalink_tag = block.select_one("a.cPLnk")
        permalink = permalink_tag["href"] if permalink_tag and permalink_tag.has_attr("href") else ""

        items.append({
            "datetime": datetime_text,
            "city": city_text,
            "summary": summary_text,
            "url": permalink,
        })
    return items


# ============================================================
# RSSフィード(Google News / Yahoo!ニュース)
# ============================================================

def format_published_jst(entry):
    """feedparserのエントリから公開日時を取得し、JSTの "MM/DD HH:MM" 形式で返す。
    取得できない場合(フィード側に日時情報が無い等)は "-" を返す。
    """
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return "-"
    try:
        dt_utc = datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return "-"
    dt_jst = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    return dt_jst.strftime("%m/%d %H:%M")


def fetch_rss_items(url, limit=RSS_ITEM_LIMIT):
    try:
        feed = feedparser.parse(url)
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] RSS取得に失敗しました({url}): {err}")
        return []

    items = []
    for entry in feed.entries[:limit]:
        items.append({
            "title": entry.get("title", "(タイトル不明)"),
            "url": entry.get("link", ""),
            "published": format_published_jst(entry),
        })
    return items


# ============================================================
# 株価(日経平均・ドル円) — 重複排除の対象外(常に最新値を送る)
# ============================================================

def fetch_nikkei225():
    try:
        resp = requests.get(
            YAHOO_FINANCE_NIKKEI225_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] 日経平均の取得に失敗しました: {err}")
        return None

    m = re.search(
        r'"name":"日経平均株価".*?"price":"([0-9,.]+)".*?"changePrice":"(-?[0-9,.]+)".*?"changePriceRate":"(-?[0-9,.]+)"',
        html,
    )
    if not m:
        print("[WARN] 日経平均の値を抽出できませんでした(ページ構造が変わった可能性があります)。")
        return None

    return {
        "price": m.group(1),
        "change": m.group(2),
        "change_rate": m.group(3),
    }


def fetch_usdjpy():
    try:
        resp = requests.get(EXCHANGE_RATE_API_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        rate = data.get("rates", {}).get("JPY")
        if rate is None:
            raise ValueError("レスポンスにJPYレートが含まれていません")
        return round(rate, 2)
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] ドル円レートの取得に失敗しました: {err}")
        return None


def fetch_kabutan_us_index(url, label):
    """株探・米国株版(us.kabutan.jp)から米国株価指数を取得する。
    ページ内のdata属性・クラス名からのパースであり、サイト側のマークアップ
    変更で失敗する可能性があるため、抽出できなければNoneを返し推測で埋めない。
    """
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        html = resp.text
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] {label}の取得に失敗しました: {err}")
        return None

    price_m = re.search(r'text-3xl mr-1">([\d,]+\.\d+)</div>', html)
    change_block_m = re.search(
        r'前日比.*?</div>\s*<div class="flex justify-center">(.*?)</div>\s*</div>',
        html, re.DOTALL,
    )
    if not price_m or not change_block_m:
        print(f"[WARN] {label}の値を抽出できませんでした(ページ構造が変わった可能性があります)。")
        return None

    change_nums = re.findall(r"[+-]?[\d,]+\.\d+", change_block_m.group(1))
    if len(change_nums) < 2:
        print(f"[WARN] {label}の前日比を抽出できませんでした。")
        return None

    return {"price": price_m.group(1), "change": change_nums[0], "change_rate": change_nums[1]}


# ============================================================
# メッセージ組み立て・Discord送信
# ============================================================

def fetch_anzn_new_items(state, now):
    """あんぜんねっと(北本市安全安心情報)の新着だけを切り出す。
    最上部に赤枠強調(Discord Embed)で単独送信するため、他セクションとは分離している。
    """
    anzn_all = fetch_anzn_new_arrivals()
    return dedupe_new_items(anzn_all, "url", state, now)


def build_local_news_message(state, now):
    """新着が1件も無ければ(None, [])を返す(Discordへ空更新を送らないため)。
    あんぜんねっとの新着はここには含めない(build_anzn_alert_embed()で別送するため)。
    戻り値は (message_or_None, sports_items) のタプル。
    sports_itemsは、埼玉ローカルニュースに混入していたスポーツ記事(#webhook_local
    ではなく#webhook_sports_cultureへ回すため、ここでは除外して別途返す)。
    全国ニュース(build_national_news_message)とは完全に別メッセージ・別Webhookで
    送信するため、ここでは混在させない。
    """
    sports_items = []

    saitama_all = fetch_rss_items(GOOGLE_NEWS_SAITAMA_RSS)
    saitama_new = dedupe_new_items(saitama_all, "url", state, now)
    saitama_general = []
    for item in saitama_new:
        if is_saitama_local_noise(item["title"]):
            continue
        if is_sports_related(item["title"]):
            sports_items.append(item)
        else:
            saitama_general.append(item)

    if not saitama_general:
        return None, sports_items

    now_jst = now.strftime("%Y-%m-%d %H:%M")
    lines = [f"# 🗾 埼玉・県央ローカルニュース ({now_jst} JST時点)"]
    for item in saitama_general:
        lines.append(f"- [{item['title']}](<{item['url']}>) `[{item['published']}]`")
    return "\n".join(lines), sports_items


def build_national_news_message(state, now):
    """新着が1件も無ければ(None, [])を返す(Discordへ空更新を送らないため)。
    戻り値は (message_or_None, sports_items) のタプル。
    sports_itemsは、一般ニュースフィードに混入していたスポーツ記事(#webhook_news
    ではなく#webhook_sports_cultureへ回すため、ここでは除外して別途返す)。
    ローカルニュース(build_local_news_message)とは完全に別メッセージ・別Webhookで
    送信するため、ここでは混在させない。
    """
    sports_items = []

    top_all = fetch_rss_items(YAHOO_TOP_PICKS_RSS)
    top_new = dedupe_new_items(top_all, "url", state, now)
    top_general = []
    for item in top_new:
        if is_sports_related(item["title"]):
            sports_items.append(item)
        else:
            top_general.append(item)

    if not top_general:
        return None, sports_items

    now_jst = now.strftime("%Y-%m-%d %H:%M")
    lines = [f"# 🌐 主要ニュース ({now_jst} JST時点)"]
    for item in top_general:
        lines.append(f"- [{item['title']}](<{item['url']}>)")
    return "\n".join(lines), sports_items


def build_market_message(state, now):
    """株価は常に送る。経済ニュースは新着があるときだけ追記する。"""
    lines = []
    now_jst = now.strftime("%Y-%m-%d %H:%M")
    lines.append(f"# 💹 マーケット情報 ({now_jst} JST時点)")

    lines.append("\n## 📈 株価")
    nikkei = fetch_nikkei225()
    if nikkei:
        arrow = "🔺" if not nikkei["change"].startswith("-") else "🔻"
        lines.append(f"- 日経平均株価: **{nikkei['price']}円** {arrow} {nikkei['change']} ({nikkei['change_rate']}%)")
    else:
        lines.append("- 日経平均株価: 取得できませんでした")

    for idx_conf in KABUTAN_US_INDICES:
        idx_data = fetch_kabutan_us_index(idx_conf["url"], idx_conf["label"])
        if idx_data:
            arrow = "🔺" if not idx_data["change"].startswith("-") else "🔻"
            lines.append(
                f"- {idx_conf['label']}: **{idx_data['price']}** "
                f"{arrow} {idx_data['change']} ({idx_data['change_rate']}%)"
            )
        else:
            lines.append(f"- {idx_conf['label']}: 取得できませんでした")

    usdjpy = fetch_usdjpy()
    if usdjpy is not None:
        lines.append(f"- ドル円: **{usdjpy}円**")
    else:
        lines.append("- ドル円: 取得できませんでした")

    biz_all = fetch_rss_items(YAHOO_BUSINESS_RSS)
    biz_new = dedupe_new_items(biz_all, "url", state, now)
    if biz_new:
        lines.append("\n## 💰 経済ニュース(新着)")
        for item in biz_new:
            lines.append(f"- [{item['title']}](<{item['url']}>)")

    return "\n".join(lines)


SPORTS_LEAK_COLOR = 0x2C7A7B


def build_sports_leak_message(sports_items, now):
    """一般ニュースフィードに混入していたスポーツ記事を、#webhook_news ではなく
    #webhook_sports_culture 側へ回すためのメッセージを組み立てる。
    """
    if not sports_items:
        return None
    now_jst = now.strftime("%Y-%m-%d %H:%M")
    lines = [f"# ⚾ 一般ニュースフィードで検知したスポーツ記事 ({now_jst} JST時点)"]
    for item in sports_items:
        lines.append(f"- [{item['title']}](<{item['url']}>)")
    return "\n".join(lines)


ANZN_EMBED_COLOR = 0xE53E3E  # 赤枠(Discord Embedの左側カラーバー)
ANZN_EMBED_FIELD_LIMIT = 25  # Discord Embedのfields上限


def build_anzn_alert_embed(anzn_new, now):
    """あんぜんねっとの新着を、最上部に表示される赤色のDiscord Embedとして組み立てる。
    火災・消防出動情報という速報性・緊急性の高い情報のため、他の一般ニュースとは
    視覚的に分離し、常に最優先(最上部)で配信する。
    """
    if not anzn_new:
        return None

    fields = []
    for item in anzn_new[:ANZN_EMBED_FIELD_LIMIT]:
        value = item["summary"]
        if item["url"]:
            value += f"\n[詳細を見る]({item['url']})"
        fields.append({
            "name": f"🚒 {item['datetime']}［{item['city']}］",
            "value": value[:1024],
            "inline": False,
        })

    now_jst = now.strftime("%Y-%m-%d %H:%M")
    return {
        "embeds": [{
            "title": "🚨 北本市安全安心情報(新着) — 火災・消防出動速報",
            "color": ANZN_EMBED_COLOR,
            "fields": fields,
            "footer": {"text": f"あんぜんねっと(埼玉県央) / {now_jst} JST時点"},
        }]
    }


def send_embed_to_discord(webhook_url, embed_payload):
    if not webhook_url:
        print("[ERROR] Webhook URLが設定されていないため送信をスキップします。")
        return False
    try:
        resp = requests.post(webhook_url, json=embed_payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 300:
            print(f"[ERROR] Discord Embed送信に失敗しました(HTTP {resp.status_code}): {resp.text[:300]}")
            return False
        return True
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Discord Embed送信中に例外が発生しました: {err}")
        return False


def chunk_message(text, limit=DISCORD_CHUNK_LIMIT):
    """Discordの1メッセージ2000文字制限に収まるよう、改行単位で分割する。"""
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_to_discord(webhook_url, message):
    if not webhook_url:
        print("[ERROR] Webhook URLが設定されていないため送信をスキップします。")
        return False

    ok = True
    for chunk in chunk_message(message):
        try:
            resp = requests.post(webhook_url, json={"content": chunk}, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 300:
                print(f"[ERROR] Discord送信に失敗しました(HTTP {resp.status_code}): {resp.text[:300]}")
                ok = False
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] Discord送信中に例外が発生しました: {err}")
            ok = False
    return ok


def _handle_sports_leak(sports_items, sports_webhook, now, source_label):
    """ローカル/全国いずれかのフィードに混入していたスポーツ記事を
    #webhook_sports_culture へ振り分ける共通処理。戻り値はhad_errorか否か。
    """
    if not sports_items:
        return False
    print(f"=== {source_label}からスポーツ記事を検知: {len(sports_items)}件 ===")
    for item in sports_items:
        print(f"  {item['title']}")
    sports_message = build_sports_leak_message(sports_items, now)
    if not sports_webhook:
        print("[WARN] DISCORD_WEBHOOK_SPORTS_CULTURE が未設定のため、"
              "検知したスポーツ記事の振り分け送信をスキップします(誤配信は防止済み)。")
        return False
    return not send_to_discord(sports_webhook, sports_message)


def main():
    local_webhook = os.environ.get("DISCORD_WEBHOOK_LOCAL")
    news_webhook = os.environ.get("DISCORD_WEBHOOK_NEWS")
    market_webhook = os.environ.get("DISCORD_WEBHOOK_MARKET")
    sports_webhook = os.environ.get("DISCORD_WEBHOOK_SPORTS_CULTURE")

    if not local_webhook and not news_webhook and not market_webhook:
        print("[ERROR] DISCORD_WEBHOOK_LOCAL / DISCORD_WEBHOOK_NEWS / DISCORD_WEBHOOK_MARKET "
              "のいずれも設定されていません。")
        sys.exit(1)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    state = load_seen_state()
    state = prune_old_entries(state, now)

    had_error = False

    # ローカル系(#webhook_local): あんぜんねっと(北本市安全安心情報) + 埼玉・県央ローカルニュース。
    # 全国ニュースとは完全に別メッセージ・別Webhookで送信し、混在させない。
    if local_webhook:
        anzn_new = fetch_anzn_new_items(state, now)
        anzn_embed = build_anzn_alert_embed(anzn_new, now)
        if anzn_embed:
            print("=== あんぜんねっと新着(赤枠強調・最優先送信) ===")
            print(anzn_new)
            if not send_embed_to_discord(local_webhook, anzn_embed):
                had_error = True
        else:
            print("[INFO] あんぜんねっとの新着はありませんでした。")

        local_message, local_sports_items = build_local_news_message(state, now)
        if local_message:
            print("=== ローカルニュースメッセージ(新着あり) ===")
            print(local_message)
            if not send_to_discord(local_webhook, local_message):
                had_error = True
        else:
            print("[INFO] 新着のローカルニュースはありませんでした。送信をスキップします。")

        if _handle_sports_leak(local_sports_items, sports_webhook, now, "埼玉ローカルニュース"):
            had_error = True
    else:
        print("[WARN] DISCORD_WEBHOOK_LOCAL が未設定のため、ローカルニュース配信をスキップします。")

    # 全国系(#webhook_news): 主要ニュース(Yahoo!トップピックス)のみ。
    if news_webhook:
        national_message, national_sports_items = build_national_news_message(state, now)
        if national_message:
            print("=== 全国ニュースメッセージ(新着あり) ===")
            print(national_message)
            if not send_to_discord(news_webhook, national_message):
                had_error = True
        else:
            print("[INFO] 新着の全国ニュースはありませんでした。送信をスキップします。")

        if _handle_sports_leak(national_sports_items, sports_webhook, now, "一般ニュースフィード"):
            had_error = True
    else:
        print("[WARN] DISCORD_WEBHOOK_NEWS が未設定のため、全国ニュース配信をスキップします。")

    if market_webhook:
        market_message = build_market_message(state, now)
        print("=== マーケットメッセージ ===")
        print(market_message)
        if not send_to_discord(market_webhook, market_message):
            had_error = True
    else:
        print("[WARN] DISCORD_WEBHOOK_MARKET が未設定のため、マーケット配信をスキップします。")

    save_seen_state(state)

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
