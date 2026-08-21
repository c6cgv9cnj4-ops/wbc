import os
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
from youtube_transcript_api import YouTubeTranscriptApi

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def get_anzn_info():
    """anzn.net 北本市ページから生活・地域情報を取得"""
    url = "https://anzn.net/sp/?11217F"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        
        # 不要なタグを除去
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
            
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        clean_text = "\n".join(lines[:60])
        return clean_text
    except Exception as e:
        print(f"anzn.net 取得エラー: {e}")
        return "情報の取得に失敗しました。"

def get_cnbc_transcripts():
    """CNBC公式YouTubeから直近動画の文字起こしを取得"""
    cnbc_feed_url = "https://www.youtube.com/feeds/videos.xml?user=CNBCtelevision"
    feed = feedparser.parse(cnbc_feed_url)
    
    transcripts_summary = []
    for entry in feed.entries[:5]:
        video_id = getattr(entry, "yt_videoid", None)
        title = entry.title
        if video_id:
            try:
                transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
                full_text = " ".join([t['text'] for t in transcript_list])
                transcripts_summary.append(f"【動画タイトル: {title}】\n文字起こし: {full_text[:2000]}\n")
            except Exception:
                transcripts_summary.append(f"【動画タイトル: {title}】\n")
        else:
            transcripts_summary.append(f"【動画タイトル: {title}】\n")
            
    return "\n".join(transcripts_summary)

def get_news_feeds():
    """国内・地域・カルチャー・海外ニュースを網羅的に収集"""
    feed_urls = [
        "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/NATION?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=%E5%9F%BC%E7%8E%89%E6%96%B0%E8%81%9E+OR+%E5%9F%BC%E7%8E%89%E7%9C%8C&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=%E5%86%99%E7%9C%9F%E5%B1%95+OR+%E3%82%AD%E3%83%A4%E3%83%8E%E3%83%B3+OR+%E3%82%B7%E3%82%B0%E3%83%9E&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=%E6%9D%BE%E5%8F%8B%E7%BE%8E%E4%BD%90%E7%B4%80+OR+%E3%83%90%E3%83%89%E3%83%9F%E3%83%B3%E3%83%88%E3%83%B3+OR+%E6%B5%A6%E5%92%8C%E3%83%AC%E3%83%83%E3%82%BA&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=%E3%82%B5%E3%82%AB%E3%83%8A%E3%82%AF%E3%82%B7%E3%83%A7%E3%83%B3&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=U2+band+OR+Bono+OR+The+Edge&hl=en-US&gl=US&ceid=US:en",
    ]
    
    collected_articles = []
    seen_titles = set()

    for url in feed_urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:8]:
            if entry.title not in seen_titles:
                seen_titles.add(entry.title)
                collected_articles.append(f"- {entry.title} / {entry.link}")
    
    return "\n".join(collected_articles)

def generate_morning_briefing(news_text, cnbc_text, anzn_text):
    """Gemini 3.6 Flashを使って全セクションを生成"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
あなたはトップクラスの金融・時事モーニングアナリストです。
提供された各データをもとに、詳細で極めて読み応えのある朝のデイリーサマリーを作成してください。

以下の各セクションを明確に分け、省略せずに十分な情報量で記述してください。

==================================================
【1. 熊谷の気象・コンディション】
・当日の天気、最高/最低気温、時間帯別の降水確率、風速、体感温度、外出時のアドバイス

【2. 北本市 生活・地域情報（anzn.netより）】
・提供されたanzn.netのテキストデータをもとに、北本市の当日の収集・分別情報、地域からのお知らせや注意事項をわかりやすく要約

【3. 前日CNBC徹底分析（番組文字起こしより要約）】
CNBC文字起こしデータを精査し、以下の項目に分けて日本語でプロの視点から詳しく解説してください：
・米国マクロ経済・FRB利下げ／利上げ観測・金利・為替動向
・ハイテク・半導体・主要銘柄の議論動向（アナリストの強気／弱気見通し）
・市場関係者・著名コメンテーターの注目発言・重要示唆

【4. グローバル・株式マーケット指数分析】
・主要指数：SOX指数、台湾加権指数、韓国KOSPI、NYダウ、ナスダック、日経平均先物、ドル円
・騰落背景、モメンタム、本日発表予定の指標・注目イベント

【5. 写真・アート＆カメラ機材情報】
・首都圏（東京・埼玉）の注目写真展・ギャラリー企画展情報
・キヤノン（EOS/RF/EF）、シグマ等の新製品発表、ファームウェア更新、業界の動向

【6. スポーツ速報（バドミントン・サッカー・野球）】
・バドミントン：松友美佐紀選手の動向・最新結果を最優先。世界選手権・ツアーの日本勢（山口茜、奥原希望、奈良岡功大など）
・サッカー：浦和レッズの試合結果・最新動向・次節カード
・野球：NPB主要試合結果、MLB日本人選手（大谷翔平、岡本和真、村上宗隆、今井達也、佐々木朗希など）の成績

【7. 音楽・カルチャー（サカナクション ＆ U2海外動向）】
・サカナクション：ツアー・新曲・山口一郎氏の動向
・U2：海外の最新ニュース・リリース・ライブ動向を【日本語に翻訳して要約】

【8. 埼玉ローカルニュース（埼玉新聞より厳選5項目）】
・県内の事件・行政・話題を必ず5項目抽出し、見出し・1行要約・元記事URLを記載

【9. 本日の主要・時事ニュース厳選（7〜8本）】
・国内外の最重要ニュース7〜8本（見出し＋1〜2行要約＋元記事URL）
==================================================

【北本市 anzn.net 取得データ】
{anzn_text}

【前日CNBC動画文字起こしデータ】
{cnbc_text}

【収集ニュース一覧データ】
{news_text}
"""
    models = ['gemini-3.6-flash', 'gemini-3.1-pro-preview']
    
    for model_name in models:
        for attempt in range(1, 4):
            try:
                print(f"[{model_name}] 生成実行中 (試行 {attempt}/3)...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                print(f"エラー発生: {e}")
                time.sleep(10)
                
    raise RuntimeError("AIの生成リトライ上限に達しました。")

def send_discord_split(message):
    """2000文字制限を回避して確実に分割送信"""
    max_len = 1800
    paragraphs = message.split("\n\n")
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 > max_len:
            if current_chunk.strip():
                requests.post(DISCORD_WEBHOOK_URL, json={"content": current_chunk.strip()})
                time.sleep(1)
            current_chunk = para + "\n\n"
        else:
            current_chunk += para + "\n\n"

    if current_chunk.strip():
        requests.post(DISCORD_WEBHOOK_URL, json={"content": current_chunk.strip()})

def main():
    print("1/5: 北本市生活情報取得...")
    anzn_text = get_anzn_info()

    print("2/5: CNBC文字起こし収集...")
    cnbc_text = get_cnbc_transcripts()
    
    print("3/5: Google/地域ニュース収集...")
    news_text = get_news_feeds()
    
    print("4/5: Geminiによるサマリー生成...")
    briefing = generate_morning_briefing(news_text, cnbc_text, anzn_text)
    
    print("5/5: Discordへ分割送信...")
    send_discord_split(briefing)
    print("完了しました。")

if __name__ == "__main__":
    main()
