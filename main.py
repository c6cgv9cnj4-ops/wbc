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
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# 日本時間の現在日時を取得
JST = timezone(timedelta(hours=9))
CURRENT_NOW_JST = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")

def get_anzn_info():
    """anzn.net から取得。403等の場合は北本市公式HPから新着生活情報を取得"""
    url = "https://anzn.net/sp/?11217F"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
    }
    
    # 1. anzn.net へのアクセス試行
    try:
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

    # 2. 403等の場合のフォールバック: 北本市公式ホームページ新着情報
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
    except Exception as e:
        print(f"北本市情報取得フォールバックエラー: {e}")

    return "北本市の最新生活情報（ゴミ収集日程等）：平常通りの収集スケジュールです。"

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
                transcripts_summary.append(f"【番組名: {title}】(放送日時: {published})\n文字起こし内容: {full_text[:1500]}\n")
            except Exception:
                transcripts_summary.append(f"【番組名: {title}】(放送日時: {published})\n")
        else:
            transcripts_summary.append(f"【番組名: {title}】(放送日時: {published})\n")
            
    return "\n".join(transcripts_summary)

def get_news_feeds():
    """Yahoo!ニュースおよび主要RSSから最新情報を収集"""
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
    
    collected_articles = []
    seen_titles = set()

    for url in feed_urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:10]:
            if entry.title not in seen_titles:
                seen_titles.add(entry.title)
                pub = getattr(entry, 'published', '日時不明')
                clean_link = entry.link.split('?')[0]
                collected_articles.append(f"- 【{pub}】{entry.title} / URL: {clean_link}")
    
    return "\n".join(collected_articles)

def generate_morning_briefing(news_text, cnbc_text, anzn_text):
    """プロンプト（完全指定版）"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
あなたはトップクラスの金融・時事・防災速報アナリストです。
提供されたデータをもとに、Discord上で極めて視認性が高く、正確で実用的なサマリーを作成してください。

【基準日時】
現在時刻（日本時間 JST）: {CURRENT_NOW_JST}
※すべての市況・ニュースの時間は、この日本時間を基準にして矛盾のない正確な時間を明記してください。

【リンクと表示の絶対ルール】
1. 記事や公式HPのリンクはすべて `[記事を読む](URL)` や `[公式サイト](URL)` のMarkdownリンク形式にすること。
2. Googleマップは `[Googleマップで確認](https://www.google.com/maps/search/?api=1&query=場所名)` の形式にすること。
3. すべてのセクションを省略せず、最後の【10. 本日の主要・時事ニュース厳選】まで確実に出力し切ること。

==================================================
【1. 北本市 生活・地域情報】※最上部に配置
・提供された北本市の生活・収集・新着情報を要約

【2. 🚨 火災・防災・事故速報】
・埼玉および全国の直近火災・消防出動（発生・発表日時明記）

【3. 熊谷の気象・コンディション】
・発表日時、天気、気温、降水確率、風速、体感温度、外出アドバイス

【4. 📊 前日CNBC徹底分析 ＆ 重要経済指標】
※CNBC分析は以下の見やすいレイアウトで整理すること：
◆ 米国マクロ・FRB金融政策動向
・金利・為替・インフレ議論の要点
◆ ハイテク・半導体・注目個別銘柄
・主要銘柄の強気/弱気見通し
◆ 著名コメンテーター・市場関係者の発言
・重要示唆
◆ 直近の重要経済指標結果（発表日時入りテーブル: 指標名 / 予想 / 結果 / 変動幅）

【5. 📈 グローバル・株式マーケット指数詳細分析】
※基準時間は「日本時間 {CURRENT_NOW_JST} 時点（または直近終値）」とし、データソース元（Yahoo!ファイナンス/Nikkei等）を明記すること。
・NYダウ: 数値（前日比 値幅 / ％）[NY終値] ＋ 分析
・ナスダック総合: 数値（前日比 値幅 / ％）[NY終値] ＋ 分析
・SOX指数（半導体）: 数値（前日比 値幅 / ％）[NY終値] ＋ 分析
・日経平均先物(CME) / 日経平均: 数値（前日比 値幅 / ％） ＋ 分析
・ドル/円（USD/JPY）: レート（前日比） ＋ 分析
・台湾加権 / 韓国KOSPI: 動向

【6. 📷 写真・アート＆カメラ機材情報（国内・首都圏限定）】
・注目写真展・企画展（会期、会場名、住所、[公式サイト](公式URL)、[Googleマップで確認](URL)、北本駅からの所要時間）
・キヤノン・シグマ新製品動向

【7. 🏸⚽⚾ スポーツ速報・全試合結果＆順位表】
・バドミントン：松友美佐紀選手（ペア相手選手名、種目、大会名、何回戦か、詳細スコア、情報ソース）
・サッカー（J1）：直近試合の全対戦スコア、浦和レッズ動向、J1最新順位表（上位・注目チーム）
・野球（NPB・MLB）：NPB直近全試合スコアと最新順位表、MLB日本人選手（大谷翔平等、所属球団名と成績）

【8. 🎵 音楽・カルチャー情報】
・サカナクション最新動向（発表日時）
・U2海外最新動向（発表日時・日本語訳要約）
・その他カルチャーニュース（藤井風の公演情報など注目エンタメ・文化トピックスを2〜3件）

【9. 埼玉ローカルニュース（埼玉新聞より厳選5項目）】
・配信日時、見出し、1行要約、[記事を読む](URL)

【10. 本日の主要・時事ニュース厳選（7〜8本）】
・1番から7〜8番まで漏れなく出力（配信日時、見出し、1〜2行要約、[記事を読む](URL)）
==================================================

【北本市 取得データ】
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
    print("データ収集中（北本市生活情報 / CNBC / ニュース）...")
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
