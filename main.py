import os
import time
from datetime import datetime, timezone, timedelta
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

DEFAULT_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
WEBHOOK_MARKET = os.environ.get("WEBHOOK_MARKET") or DEFAULT_WEBHOOK
WEBHOOK_LOCAL = os.environ.get("WEBHOOK_LOCAL") or DEFAULT_WEBHOOK
WEBHOOK_SPORTS_CULTURE = os.environ.get("WEBHOOK_SPORTS_CULTURE") or DEFAULT_WEBHOOK
WEBHOOK_NEWS = os.environ.get("WEBHOOK_NEWS") or DEFAULT_WEBHOOK

JST = timezone(timedelta(hours=9))
CURRENT_NOW_JST = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")

def get_anzn_info():
    """北本市生活・地域情報を取得"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        url = "https://anzn.net/sp/?11217F"
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            res.encoding = res.apparent_encoding
            soup = BeautifulSoup(res.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines[:60])
    except Exception:
        pass

    try:
        city_url = "https://www.city.kitamoto.lg.jp/"
        res_city = requests.get(city_url, headers=headers, timeout=8)
        res_city.encoding = res_city.apparent_encoding
        soup_city = BeautifulSoup(res_city.text, "html.parser")
        news_items = []
        for a in soup_city.find_all("a", href=True):
            title = a.get_text().strip()
            if len(title) > 10 and any(k in title for k in ["ごみ", "収集", "健康", "防災", "お知らせ", "募集"]):
                news_items.append(f"- {title} (https://www.city.kitamoto.lg.jp{a['href'] if a['href'].startswith('/') else '/' + a['href']})")
        if news_items:
            return "【北本市公式HP 新着・生活情報】\n" + "\n".join(news_items[:8])
    except Exception:
        pass

    return "北本市の最新生活情報（ゴミ収集日程等）：平常通りの収集スケジュールです。"

def get_cnbc_transcripts():
    """CNBC公式YouTubeから直近動画の文字起こしを取得"""
    cnbc_feed_url = "https://www.youtube.com/feeds/videos.xml?user=CNBCtelevision"
    feed = feedparser.parse(cnbc_feed_url)
    transcripts = []
    for entry in feed.entries[:4]:
        video_id = getattr(entry, "yt_videoid", None)
        title = entry.title
        published = getattr(entry, "published", "日時不明")
        if video_id:
            try:
                t_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
                full_text = " ".join([t['text'] for t in t_list])
                transcripts.append(f"【番組: {title}】({published})\n文字起こし: {full_text[:1500]}\n")
            except Exception:
                transcripts.append(f"【番組: {title}】({published})\n")
        else:
            transcripts.append(f"【番組: {title}】({published})\n")
    return "\n".join(transcripts)

def get_news_feeds():
    """短縮直リンク付きRSSフィードから最新ニュースを収集"""
    feed_urls = [
        "https://news.yahoo.co.jp/rss/topics/top-picks.xml",
        "https://news.yahoo.co.jp/rss/topics/business.xml",
        "https://news.yahoo.co.jp/rss/topics/domestic.xml",
        "https://news.yahoo.co.jp/rss/topics/world.xml",
        "https://news.yahoo.co.jp/rss/topics/sports.xml",
        "https://news.yahoo.co.jp/rss/categories/local.xml",
        "https://news.yahoo.co.jp/rss/media/saitama/all.xml",
        "https://news.yahoo.co.jp/rss/topics/entertainment.xml",
    ]
    articles = []
    seen = set()
    for url in feed_urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:10]:
            if entry.title not in seen:
                seen.add(entry.title)
                pub = getattr(entry, 'published', '日時不明')
                clean_link = entry.link.split('?')[0]
                articles.append(f"- 【{pub}】{entry.title} / URL: {clean_link}")
    return "\n".join(articles)

def send_discord_split(webhook_url, message):
    """指定チャンネルへ送信"""
    if not webhook_url or not isinstance(webhook_url, str) or not webhook_url.startswith("http"):
        return
    if not message or not message.strip():
        return

    max_len = 1800
    paragraphs = message.split("\n\n")
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 > max_len:
            if current_chunk.strip():
                try:
                    requests.post(webhook_url, json={"content": current_chunk.strip()}, timeout=10)
                    time.sleep(1)
                except Exception:
                    pass
            current_chunk = para + "\n\n"
        else:
            current_chunk += para + "\n\n"
    if current_chunk.strip():
        try:
            requests.post(webhook_url, json={"content": current_chunk.strip()}, timeout=10)
        except Exception:
            pass

def generate_section_content(prompt):
    """Gemini APIで各チャンネル用コンテンツを生成"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    config = types.GenerateContentConfig(max_output_tokens=4096, temperature=0.3)
    models = ['gemini-3.6-flash', 'gemini-3.1-pro-preview']
    for model_name in models:
        for _ in range(3):
            try:
                res = client.models.generate_content(model=model_name, contents=prompt, config=config)
                if res and res.text:
                    return res.text
            except Exception:
                time.sleep(5)
    return "情報の生成に失敗しました。"

def main():
    print("1/5: 各種データ収集中...")
    anzn_text = get_anzn_info()
    cnbc_text = get_cnbc_transcripts()
    news_text = get_news_feeds()

    common_rule = f"""
基準日時（日本時間 JST）: {CURRENT_NOW_JST}
【リンクのルール】
・記事リンクは必ず `[記事を読む](URL)`
・Googleマップは必ず `[Googleマップで確認](https://www.google.com/maps/search/?api=1&query=場所名)`
・公式サイトは `[公式サイト](URL)`
"""

    # --- 1. 市況・経済指標 ---
    print("2/5: 市況・経済指標サマリー生成＆送信...")
    prompt_market = f"""{common_rule}
あなたは金融・経済マーケットアナリストです。以下のデータをもとに「市況・経済指標」レポートを作成してください。

構成：
# 📈 グローバル・株式市況＆経済指標詳細分析 ({CURRENT_NOW_JST} 時点)
## 1. 📊 重要経済指標 速報テーブル
・発表日時、指標名（重要度★★★）、予想、結果、変動幅をMarkdownテーブルで記載

## 2. 📺 CNBC番組徹底分析（文字起こしより）
◆ 米国マクロ・FRB金融政策動向
◆ ハイテク・半導体・注目個別銘柄の議論動向
◆ 著名コメンテーター・市場関係者発言要約

## 3. 📈 主要指数・為替詳細動向（ソース明記）
※必ず【基準時間】と【前日比（値幅・％）】を明記：
・NYダウ / ナスダック / SOX指数 / 日経平均先物(CME) / ドル円 / 台湾加権 / 韓国KOSPI

【データ】
CNBC文字起こし:
{cnbc_text}
ニュース:
{news_text}
"""
    market_content = generate_section_content(prompt_market)
    send_discord_split(WEBHOOK_MARKET, market_content)

    # --- 2. 地域・防災・天気 ---
    print("3/5: 地域・防災・天気サマリー生成＆送信...")
    prompt_local = f"""{common_rule}
あなたは地域安全・気象アナリストです。以下のデータをもとに「地域・防災・天気」レポートを作成してください。

構成：
# 🚨 地域・防災・天気情報 ({CURRENT_NOW_JST})
## 1. 🏡 北本市 生活・地域情報
・本日のゴミ収集、分別、市からのお知らせ、注意事項

## 2. 🚨 火災・防災・事故速報
・埼玉および関東・全国の直近火災・消防出動（発生日時・状況）

## 3. ☀️ 熊谷の気象・コンディション
・発表日時、天気、気温、降水確率、風速、体感温度、外出アドバイス

## 4. 📰 埼玉ローカルニュース厳選5項目（埼玉新聞等）
・配信日時、見出し、1行要約、[記事を読む](URL)

【データ】
北本市データ:
{anzn_text}
ニュース:
{news_text}
"""
    local_content = generate_section_content(prompt_local)
    send_discord_split(WEBHOOK_LOCAL, local_content)

    # --- 3. スポーツ・カルチャー ---
    print("4/5: スポーツ・カルチャーサマリー生成＆送信...")
    prompt_sports_culture = f"""{common_rule}
あなたはスポーツ＆カルチャーアナリストです。以下のデータをもとに「スポーツ・カルチャー」レポートを作成してください。

構成：
# 🏸 スポーツ＆カルチャー最新情報 ({CURRENT_NOW_JST})
## 1. 🏸 バドミントン速報
・松友美佐紀選手（ペア相手選手名、種目、大会名、BWF大会グレード、何回戦か、詳細スコア、ソース）
・日本代表勢（山口茜、奥原希望、奈良岡功大など）

## 2. ⚽ サッカー（Jリーグ・浦和レッズ）
・直近のJ1全試合対戦スコア、浦和レッズ動向・次節予定、J1最新順位表

## 3. ⚾ 野球（NPB・MLB）
・NPB直近全試合スコア＆順位表、MLB日本人選手（大谷翔平選手等の球団名と成績）

## 4. 📷 写真展・アート＆カメラ機材（国内・首都圏限定）
・注目写真展（会期、会場名、住所、[公式サイト](URL)、[Googleマップで確認](URL)、北本駅からの目安所要時間）
・キヤノン・シグマ新製品・業界動向

## 5. 🎵 音楽・カルチャー動向
・サカナクション最新動向
・U2海外最新動向（発表日時・日本語訳要約）
・注目カルチャーニュース2〜3件（藤井風などのエンタメ・文化トピック）

【データ】
ニュース:
{news_text}
"""
    sports_content = generate_section_content(prompt_sports_culture)
    send_discord_split(WEBHOOK_SPORTS_CULTURE, sports_content)

    # --- 4. 一般ニュース ---
    print("5/5: 一般ニュースサマリー生成＆送信...")
    prompt_news = f"""{common_rule}
あなたは時事ニュースデスクです。以下のデータをもとに「一般ニュース」レポートを作成してください。

構成：
# 🌍 本日の主要・時事ニュース厳選8本 ({CURRENT_NOW_JST})
国内外の最重要ニュースを必ず1番〜8番まで漏れなく記載：
・【配信日時】報道元 見出し
・要約（1〜2行）
・[記事を読む](URL)

【データ】
ニュース:
{news_text}
"""
    news_content = generate_section_content(prompt_news)
    send_discord_split(WEBHOOK_NEWS, news_content)

    print("全処理が完了しました。")

if __name__ == "__main__":
    main()
