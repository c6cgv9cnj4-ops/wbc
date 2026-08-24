# -*- coding: utf-8 -*-
"""
ニュース自動配信bot(先行実装: 北本市安全安心情報・地域/主要ニュース・株価/経済ニュース)

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

ANZN_ITEM_LIMIT = 5
RSS_ITEM_LIMIT = 5


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
# 株価(日経平均・ドル円)
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

def build_news_message():
    lines = []
    today = datetime.date.today().strftime("%Y-%m-%d")
    lines.append(f"# 📰 ニュースまとめ ({today})")

    lines.append("\n## 🚨 北本市安全安心情報(新着)")
    anzn_items = fetch_anzn_new_arrivals()
    if anzn_items:
        for item in anzn_items:
            line = f"- **{item['datetime']}** [{item['city']}] {item['summary']}"
            if item["url"]:
                line += f" — <{item['url']}>"
            lines.append(line)
    else:
        lines.append("- 取得できませんでした")

    lines.append("\n## 🗾 埼玉地域ニュース")
    saitama_items = fetch_rss_items(GOOGLE_NEWS_SAITAMA_RSS)
    if saitama_items:
        for item in saitama_items:
            lines.append(f"- [{item['title']}]({item['url']})")
    else:
        lines.append("- 取得できませんでした")

    lines.append("\n## 🌐 主要ニュース")
    top_items = fetch_rss_items(YAHOO_TOP_PICKS_RSS)
    if top_items:
        for item in top_items:
            lines.append(f"- [{item['title']}]({item['url']})")
    else:
        lines.append("- 取得できませんでした")

    return "\n".join(lines)


def build_market_message():
    lines = []
    today = datetime.date.today().strftime("%Y-%m-%d")
    lines.append(f"# 💹 マーケット・経済ニュース ({today})")

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

    lines.append("\n## 💰 経済ニュース")
    biz_items = fetch_rss_items(YAHOO_BUSINESS_RSS)
    if biz_items:
        for item in biz_items:
            lines.append(f"- [{item['title']}]({item['url']})")
    else:
        lines.append("- 取得できませんでした")

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

    had_error = False

    if news_webhook:
        news_message = build_news_message()
        print("=== ニュースメッセージ ===")
        print(news_message)
        if not send_to_discord(news_webhook, news_message):
            had_error = True
    else:
        print("[WARN] DISCORD_WEBHOOK_NEWS が未設定のため、ニュース配信をスキップします。")

    if market_webhook:
        market_message = build_market_message()
        print("=== マーケットメッセージ ===")
        print(market_message)
        if not send_to_discord(market_webhook, market_message):
            had_error = True
    else:
        print("[WARN] DISCORD_WEBHOOK_MARKET が未設定のため、マーケット配信をスキップします。")

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
