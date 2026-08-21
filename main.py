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
    """国内・地域・火災・市況・カルチャーの最新ニュースを網羅的に収集"""
    feed_urls = [
        # 市況・株価・為替・前日比
        "https://news.google.com/rss/search?q=%E6%97%A5%E7%B5%8C%E5%B9%B3%E5%9D%87+OR+SOX%E6%8C%87%E6%95%B0+OR+NY%E3%83%80%E3%82%A6+OR+%E3%83%89%E3%83%AB%E5%86%86+%E5%89%8D%E6%97%A5%E6%AF%94&hl=ja&gl=JP&ceid=JP:ja",
        # 火災・消防速報
        "https://news.google.com/rss/search?q=%E7%81%AB%E7%81%BD+OR+%E7%81%AB%E4%BA%8B+OR+%E5%BB%B6%E7%84%BC+OR+%E6%B6%88%E9%98%B2%E5%87%BA%E5%8B%95&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=%E5%9F%BC%E7%8E%89+%E7%81%AB%E4%BA%8B+OR+%E5%8C%97%E6%9C%AC+%E7%81%AB%E4%BA%8B&hl=ja&gl=JP&ceid=JP:ja",
        # 経済指標・マクロ速報
        "https://news.google.com/rss/search?q=%E5%B0%8F%E5%A3%B2%E5%A3%B2%E4%B8%8A%E9%AB%98+OR+CPI+OR+GDP+OR+%E7%B5%8C%E6%B8%88%E6%8C%87%E6%A8%99+%E9%80%9F%E5%A0%B1&hl=ja&gl=JP&ceid=JP:ja",
        # 主要ニュース
        "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/NATION?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=ja&gl=JP&ceid=JP:ja",
        # 埼玉新聞・地域
        "https://news.google.com/rss/search?q=%E5%9F%BC%E7%8E%89%E6%96%B0%E8%81%9E+OR+%E5%9F%BC%E7%8E%89%E7%9C%8C&hl=ja&gl=JP&ceid=JP:ja",
        # 写真展・カメラ機材
        "https://news.google.com/rss/search?q=%E5%86%99%E7%9C%9F%E5%B1%95+OR+%E3%82%AD%E3%83%A4%E3%83%8E%E3%83%B3+OR+%E3%82%B7%E3%82%B0%E3%83%9E&hl=ja&gl=JP&ceid=JP:ja",
        # バドミントン（松友美佐紀優先）＆浦和レッズ
        "https://news.google.com/rss/search?q=%E6%9D%BE%E5%8F%8B%E7%BE%8E%E4%BD%90%E7%B4%80+OR+%E3%83%90%E3%83%89%E3%83%9F%E3%83%B3%E3%83%88%E3%83%B3+OR+%E6%B5%A6%E5%92%8C%E3%83%AC%E3%83%83%E3%82%BA&hl=ja&gl=JP&ceid=JP:ja",
        # サカナクション ＆ U2
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
    """市況の前日比・時間表記を厳格化したプロンプト"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
あなたはトップクラスの金融・時事・防災速報アナリストです。
提供されたデータをもとに、Discord上で視認性が高く、極めて読み応えのある最新速報サマリーを作成してください。

【出力フォーマット・演出の重要指示】
・重大速報には `🚨【超速報】` や `⚠️【緊急】` を付けて目立たせること。
・直近の重要経済指標（英小売売上高、米CPI、米雇用統計など）は、以下のような見やすいMarkdownテーブルで出力すること：
  | 項目 | 予想 | 結果 |
  | :--- | :--- | :--- |
・【市況指数の絶対ルール】各指数・為替は、必ず「取得・基準時間（例: NY終値、15:00時点、大引け等）」と「前日比（値幅および＋/ーの％）」を明記すること。

==================================================
【1. 北本市 生活・地域情報（anzn.netより）】※必ず最上部に配置
・本日のゴミ収集・分別、市からのお知らせ、地域注意事項

【2. 🚨 火災・防災・事故速報】
・埼玉および関東・全国の直近の建物火災、延焼状況、消防出動情報、交通影響

【3. 熊谷の気象・コンディション】
・天気、気温、降水確率、風速、体感温度、外出時のアドバイス

【4. 📊 重要経済指標・速報テーブル ＆ 前日CNBC徹底分析】
・直近発表の経済指標結果テーブル（予想・結果・変動幅）
・CNBC文字起こしからの米国マクロ、FRB動向、ハイテク・半導体個別銘柄議論、著名コメンテーター発言要約

【5. 📈 グローバル・株式マーケット指数詳細分析】
※以下の各項目について、必ず【基準時間】と【前日比（値幅・％）】を明記して整理すること：
・NYダウ: 数値（前日比 +○○ドル / +○.○%）[NY終値] ＋ 背景要約
・ナスダック総合: 数値（前日比 -○○pt / -○.○%）[NY終値] ＋ 背景要約
・SOX指数（半導体）: 数値（前日比 +○○pt / +○.○%）[NY終値] ＋ 背景要約
・日経平均先物（CME）: 数値（大証終値比 +○○円 / +○.○%）[早朝時点] ＋ 背景要約
・ドル/円（USD/JPY）: レート（前日比 +○.○○円 / 円安・円高動向）[直近時点] ＋ 背景要約
・台湾加権指数（TAIEX）: 数値・前日比 ＋ 動向
・韓国総合株価指数（KOSPI）: 数値・前日比 ＋ 動向

【6. 写真・アート＆カメラ機材情報】
・首都圏注目写真展、キヤノン・シグマ新製品動向

【7. スポーツ速報（バドミントン・サッカー・野球）】
・松友美佐紀選手情報最優先、日本代表勢、浦和レッズ、MLB日本人選手スタッツ

【8. 音楽・カルチャー（サカナクション ＆ U2海外動向）】
・サカナクション動向、U2海外最新動向（日本語訳）

【9. 埼玉ローカルニュース（埼玉新聞より厳選5項目）】
・見出し・1行要約・元記事URL

【10. 本日の主要・時事ニュース厳選（7〜8本）】
・国内外の最重要ニュース（見出し＋1〜2行要約＋元記事URL）
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
                print(f"[{model_name}] 生成中 (試行 {attempt}/3)...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
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
    print("データ収集中...")
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
