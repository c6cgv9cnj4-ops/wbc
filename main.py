import os
import requests
import feedparser
from google import genai

# 環境変数からキーを取得
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def get_latest_news():
    # Yahoo!ニュースの主要トピックスRSS
    rss_url = "https://news.yahoo.co.jp/rss/topics/top-picks.xml"
    feed = feedparser.parse(rss_url)
    
    articles = []
    for entry in feed.entries[:5]:  # 最新5件を取得
        articles.append(f"- {entry.title} ({entry.link})")
    
    return "\n".join(articles)

def summarize_news(news_text):
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
以下の最新ニュース一覧をもとに、Discord通知用の朝のニュースサマリーを作成してください。

【条件】
- 読みやすいように絵文字や箇条書きを適度に使ってください。
- 各トピックの要点をわかりやすく1〜2行程度で要約してください。
- 冒頭に爽やかな朝の挨拶（「おはようございます！」等）を入れてください。

【ニュース一覧】
{news_text}
"""
    response = client.models.generate_content(
        model='gemini-3.6-flash',
        contents=prompt,
    )
    return response.text

def send_discord(message):
    payload = {
        "content": message
    }
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    response.raise_for_status()

def main():
    print("ニュースを取得中...")
    news_text = get_latest_news()
    
    print("Geminiで要約中...")
    summary = summarize_news(news_text)
    
    print("Discordへ送信中...")
    send_discord(summary)
    print("送信完了しました！")

if __name__ == "__main__":
    main()
