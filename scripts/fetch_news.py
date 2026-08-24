# -*- coding: utf-8 -*-
"""
ニュース自動配信bot(北本市安全安心情報・地域/主要ニュース・株価/経済ニュース)

30分ごとに実行される想定の「準リアルタイム」配信。前回までに送信済みの記事URL/IDは
state/news_seen.json に記録し、そのURL/IDと重複するものはスキップして
「新しく見つかった記事のみ」を都度Discordへ送信する(株価は都度変動するため
重複排除の対象外で、実行するたびに最新値を送る)。

配信内容:
  DISCORD_WEBHOOK_NEWS   : 北本市安全安心情報(新着) + 埼玉地域ニュース + 主要ニュース
  DISCORD_WEBHOOK_MARKET : 株価(日経平均/ドル円) + 経済ニュース

情報源:
  - 北本市安全安心情報: あんぜんねっと(https://anzn.net/sp/?11217F&r1=1)
    ※このページはEUC-JPエンコーディングなので明示的にデコードする
  - 埼玉地域ニュース: Google ニュース検索RSS(個人利用目的)
  - 主要ニュース/経済ニュース: Yahoo!ニュース トピックスRSS
  - 日経平均株価: Yahoo!ファイナンスの銘柄ページに埋め込まれたJSONを抽出
  - ドル円: open.er-api.com(無料・APIキー不要の為替レートAPI)

環境変数:
  DISCORD_WEBHOOK_NEWS   (必須)
  DISCORD_WEBHOOK_MARKET (必須)
"""
import datetime
import json
import os
import re
import sys

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
GOOGLE_NEWS_SAITAMA_RSS = (
    "https://news.google.com/rss/search"
    "?q=%E5%9F%BC%E7%8E%89%E7%9C%8C%20when:1d&hl=ja&gl=JP&ceid=JP:ja"
)
YAHOO_TOP_PICKS_RSS = "https://news.yahoo.co.jp/rss/topics/top-picks.xml"
YAHOO_BUSINESS_RSS = "https://news.yahoo.co.jp/rss/categories/business.xml"
YAHOO_FINANCE_NIKKEI225_URL = "https://finance.yahoo.co.jp/quote/998407.O"
EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"

# 重複排除に使う「見た記事」の記録先。取得件数を少し多めに見ておき、
# 30分間隔のポーリングで新着を取りこぼしにくくする。
ANZN_ITEM_LIMIT = 10
RSS_ITEM_LIMIT = 10

STATE_PATH = "state/news_seen.json"
STATE_RETENTION_DAYS = 14  # 古い記録は掃除して肥大化を防ぐ


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


# ============================================================
# メッセージ組み立て・Discord送信
# ============================================================

def build_news_message(state, now):
    """新着が1件も無ければNoneを返す(Discordへ空更新を送らないため)。"""
    sections = []
    has_any_new = False

    anzn_all = fetch_anzn_new_arrivals()
    anzn_new = dedupe_new_items(anzn_all, "url", state, now)
    if anzn_new:
        has_any_new = True
        lines = ["## 🚨 北本市安全安心情報(新着)"]
        for item in anzn_new:
            line = f"- **{item['datetime']}** [{item['city']}] {item['summary']}"
            if item["url"]:
                line += f" — <{item['url']}>"
            lines.append(line)
        sections.append("\n".join(lines))

    saitama_all = fetch_rss_items(GOOGLE_NEWS_SAITAMA_RSS)
    saitama_new = dedupe_new_items(saitama_all, "url", state, now)
    if saitama_new:
        has_any_new = True
        lines = ["## 🗾 埼玉地域ニュース"]
        for item in saitama_new:
            lines.append(f"- [{item['title']}]({item['url']})")
        sections.append("\n".join(lines))

    top_all = fetch_rss_items(YAHOO_TOP_PICKS_RSS)
    top_new = dedupe_new_items(top_all, "url", state, now)
    if top_new:
        has_any_new = True
        lines = ["## 🌐 主要ニュース"]
        for item in top_new:
            lines.append(f"- [{item['title']}]({item['url']})")
        sections.append("\n".join(lines))

    if not has_any_new:
        return None

    now_jst = now.strftime("%Y-%m-%d %H:%M")
    header = f"# 📰 新着ニュース ({now_jst} JST時点)"
    return "\n\n".join([header] + sections)


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
            lines.append(f"- [{item['title']}]({item['url']})")

    return "\n".join(lines)


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


def main():
    news_webhook = os.environ.get("DISCORD_WEBHOOK_NEWS")
    market_webhook = os.environ.get("DISCORD_WEBHOOK_MARKET")

    if not news_webhook and not market_webhook:
        print("[ERROR] DISCORD_WEBHOOK_NEWS / DISCORD_WEBHOOK_MARKET のどちらも設定されていません。")
        sys.exit(1)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    state = load_seen_state()
    state = prune_old_entries(state, now)

    had_error = False

    if news_webhook:
        news_message = build_news_message(state, now)
        if news_message:
            print("=== ニュースメッセージ(新着あり) ===")
            print(news_message)
            if not send_to_discord(news_webhook, news_message):
                had_error = True
        else:
            print("[INFO] 新着ニュースはありませんでした。送信をスキップします。")
    else:
        print("[WARN] DISCORD_WEBHOOK_NEWS が未設定のため、ニュース配信をスキップします。")

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
