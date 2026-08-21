import os
import requests
import feedparser
from google import genai

# 環境変数からキーを取得
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def get_news_feeds():
    """複数のRSSから大量の元データを収集"""
    feed_urls = [
        # 主要トピックス・国内・経済・国際
        "https://news.yahoo.co.jp/rss/topics/top-picks.xml",
        "https://news.yahoo.co.jp/rss/topics/domestic.xml",
        "https://news.yahoo.co.jp/rss/topics/business.xml",
        "https://news.yahoo.co.jp/rss/topics/world.xml",
        # スポーツ（サッカー・野球・バドミントン関連）
        "https://news.yahoo.co.jp/rss/topics/sports.xml",
        # 写真・カメラ・アート関連検索フィード
        "https://news.google.com/rss/search?q=%E5%86%99%E7%9C%9F%E5%B1%95+OR+%E3%82%AD%E3%83%A4%E3%83%8E%E3%83%B3+OR+%E3%82%B7%E3%82%B0%E3%83%9E+%E3%82%AB%E3%83%A1%E3%83%A9&hl=ja&gl=JP&ceid=JP:ja",
    ]
    
    collected_articles = []
    seen_titles = set()

    for url in feed_urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:8]:
            if entry.title not in seen_titles:
                seen_titles.add(entry.title)
                collected_articles.append(f"- タイトル: {entry.title} / URL: {entry.link}")
    
    return "\n".join(collected_articles)

def generate_morning_briefing(news_text):
    """Gemini 3.6 Flashを使って充実したサマリーを作成"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
あなたは優秀なモーニングアナリストです。
提供された最新ニュースデータや現在知りうる市場・気象・イベントデータをもとに、詳細で読み応えのある朝のデイリーサマリーを作成してください。

以下の各セクションを明確に分け、省略せずに十分な情報量で記述してください。

==================================================
【1. 熊谷の気象・コンディション】
・当日の天気、最高/最低気温、時間帯別の降水確率、風速、体感温度、外出・コンディションの補足

【2. グローバル・株式マーケット詳細分析】
・主要指数：SOX指数（フィラデルフィア半導体株指数）、台湾加権指数（TAIEX）、韓国総合株価指数（KOSPI）、NYダウ、ナスダック、日経平均先物、ドル円為替
・各指数の騰落背景、モメンタム・過熱感（テクニカル動向）、半導体セクターの地合い
・本日発表予定の重要経済指標、注目の市場材料

【3. 写真・アート＆カメラ機材情報】
・首都圏（東京・埼玉）で開催中または近日スタートする注目写真展・企画展
・キヤノン（EOS/RF/EF）、シグマなどの新製品発表、ファームウェア更新、業界動向

【4. スポーツハイライト（サッカー・野球・バドミントン）】
・サッカー：浦和レッズの最新動向・試合結果・次節カード、Jリーグ主要トピックス
・野球：NPB主要試合結果、MLB日本人選手の詳細スタッツ
・バドミントン：BWFワールドツアー等の日本人選手速報、試合日程

【5. 本日の厳選・主要時事ニュース（7〜8本）】
以下の提供ニュース一覧から重要度の高いニュースを7〜8本厳選し、見出し・1〜2行の要約・元記事URLを必ず明記してください。
==================================================

【収集ニュース一覧データ】
{news_text}
"""
    response = client.models.generate_content(
        model='gemini-3.6-flash',
        contents=prompt,
    )
    return response.text

def send_discord_split(message):
    """Discordの2000文字制限を回避するため、安全な長さに分割して送信"""
    max_len = 1900
    paragraphs = message.split("\n\n")
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 > max_len:
            payload = {"content": current_chunk}
            res = requests.post(DISCORD_WEBHOOK_URL, json=payload)
            res.raise_for_status()
            current_chunk = para + "\n\n"
        else:
            current_chunk += para + "\n\n"

    if current_chunk.strip():
        payload = {"content": current_chunk}
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        res.raise_for_status()

def main():
    print("ニュース・元データを収集中...")
    news_text = get_news_feeds()
    
    print("Gemini 3.6 Flashで詳細サマリーを生成中...")
    briefing = generate_morning_briefing(news_text)
    
    print("Discordへ分割送信中...")
    send_discord_split(briefing)
    print("すべての送信が完了しました！")

if __name__ == "__main__":
    main()
