import os
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
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
    for entry in feed.entries[:4]:
        video_id = getattr(entry, "yt_videoid", None)
        title = entry.title
        published = getattr(entry, "published", "日時不明")
        if video_id:
            try:
                transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
                full_text = " ".join([t['text'] for t in transcript_list])
                transcripts_summary.append(f"【動画: {title}】({published})\n文字起こし: {full_text[:1500]}\n")
            except Exception:
                transcripts_summary.append(f"【動画: {title}】({published})\n")
        else:
            transcripts_summary.append(f"【動画: {title}】({published})\n")
            
    return "\n".join(transcripts_summary)

def get_news_feeds():
    """短く確実に開く直リンク付きRSSフィードから最新ニュースを収集"""
    feed_urls = [
        # Yahoo!ニュース（主要・経済・国内・国際・スポーツ・地域）※短く確実なURL
        "https://news.yahoo.co.jp/rss/topics/top-picks.xml",
        "https://news.yahoo.co.jp/rss/topics/business.xml",
        "https://news.yahoo.co.jp/rss/topics/domestic.xml",
        "https://news.yahoo.co.jp/rss/topics/world.xml",
        "https://news.yahoo.co.jp/rss/topics/sports.xml",
        "https://news.yahoo.co.jp/rss/categories/local.xml",
        # 埼玉ローカル・火災・事故
        "https://news.yahoo.co.jp/rss/media/saitama/all.xml",
        # カルチャー・音楽
        "https://news.yahoo.co.jp/rss/topics/entertainment.xml",
    ]
    
    collected_articles = []
    seen_titles = set()

    for url in feed_urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:10]:
            if entry.title not in seen_titles:
                seen_titles.add(entry.title)
                pub = getattr(entry, 'published', '日時不明')
                # Yahoo等の直リンク（40〜50文字程度で確実に開く）
                clean_link = entry.link.split('?')[0]
                collected_articles.append(f"- 【{pub}】{entry.title} / URL: {clean_link}")
    
    return "\n".join(collected_articles)

def generate_morning_briefing(news_text, cnbc_text, anzn_text):
    """短縮直リンク・全セクション出力プロンプト"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
あなたはトップクラスの金融・時事・防災速報アナリストです。
提供されたデータをもとに、Discord上で極めて視認性が高く、実用的な最新速報サマリーを作成してください。
【最重要】文章を途切れさせず、最後の【10. 本日の主要・時事ニュース厳選】まで必ずすべて出力し切ってください。

【URLとリンクの絶対ルール】
1. すべての記事URLは、提供された短いURLをそのまま `<URL>` の形式で記載すること。
2. GoogleマップURLは `<https://www.google.com/maps/search/?api=1&query=場所名>` の形式で記載すること。
3. すべての項目に「配信日時・更新日時」を明記すること。

==================================================
【1. 北本市 生活・地域情報（anzn.netより）】※最上部
・本日のゴミ収集・分別、市からのお知らせ、注意事項

【2. 🚨 火災・防災・事故速報】
・埼玉および関東近郊の火災、事故、消防出動情報（発生・発表日時明記）

【3. 熊谷の気象・コンディション】
・発表日時、天気、気温、降水確率、風速、体感温度、外出アドバイス

【4. 📊 重要経済指標・速報テーブル ＆ 前日CNBC徹底分析】
・発表日時入り経済指標テーブル（予想・結果・変動幅）
・CNBC文字起こしからの米国マクロ、FRB動向、個別銘柄議論要約（放送日時明記）

【5. 📈 グローバル・株式マーケット指数詳細分析】
※必ず【基準時間】と【前日比（値幅・％）】を明記：
・NYダウ、ナスダック、SOX指数、日経先物(CME)、ドル円、台湾加権、韓国KOSPI

【6. 📷 写真・アート＆カメラ機材情報（国内・首都圏限定）】
・注目写真展（会期日時、会場名、住所、Googleマップ `<URL>`、北本駅からのアクセス所要時間）
・キヤノン・シグマ新製品動向

【7. 🏸⚽⚾ スポーツ速報・全試合結果】
・バドミントン：松友美佐紀選手最優先、日本代表勢（BWF大会グレード明記）
・サッカー：Jリーグ全試合結果、浦和レッズの直近試合結果と次節日程
・野球：NPB全試合結果、MLB日本人選手（所属球団名と正確なスタッツ）

【8. 🎵 音楽・カルチャー（サカナクション ＆ U2動向）】
・サカナクション最新動向（発表日時）
・U2最新動向（発表日時・日本語訳要約）

【9. 埼玉ローカルニュース（埼玉新聞より厳選5項目）】
・配信日時、見出し、1行要約、元記事 `<URL>`

【10. 本日の主要・時事ニュース厳選（7〜8本）】
・必ず1番から7〜8番まで漏れなく出力（配信日時、見出し、1〜2行要約、元記事 `<URL>`）
==================================================

【北本市 anzn.net 取得データ】
{anzn_text}

【前日CNBC動画文字起こしデータ】
{cnbc_text}

【収集ニュース一覧データ】
{news_text}
"""
    models = ['gemini-3.6-flash', 'gemini-3.1-pro-preview']
    
    config = types.GenerateContentConfig(
        max_output_tokens=8192,
        temperature=0.3
    )
    
    for model_name in models:
        for attempt in range(1, 4):
            try:
                print(f"[{model_name}] 生成中 (試行 {attempt}/3)...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                print(f"エラー: {e}")
                time.sleep(10)
                
    raise RuntimeError("AI生成が完了しませんでした。")

def send_discord_split(message):
    """Discordの2000文字制限を回避して分割送信"""
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
    print("データ収集中（直リンクRSS / CNBC / anzn.net）...")
    anzn_text = get_anzn_info()
    cnbc_text = get_cnbc_transcripts()
    news_text = get_news_feeds()
    
    print("サマリー生成中...")
    briefing = generate_morning_briefing(news_text, cnbc_text, anzn_text)
    
    print("Discord送信中...")
    send_discord_split(briefing)
    print("送信完了しました。")

if __name__ == "__main__":
    main()
