import os
import requests
import feedparser
from google import genai

# 環境変数からキーを取得
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def get_news_feeds():
    """国内・地域・カルチャー・海外ニュースを網羅的に収集"""
    feed_urls = [
        # Googleニュース 各主要ジャンル
        "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja", # トップ
        "https://news.google.com/rss/headlines/section/topic/NATION?hl=ja&gl=JP&ceid=JP:ja", # 国内
        "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ja&gl=JP&ceid=JP:ja", # 国際
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ja&gl=JP&ceid=JP:ja", # ビジネス
        "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=ja&gl=JP&ceid=JP:ja", # スポーツ
        # 埼玉新聞・地域
        "https://news.google.com/rss/search?q=%E5%9F%BC%E7%8E%89%E6%96%B0%E8%81%9E+OR+%E5%9F%BC%E7%8E%89%E7%9C%8C&hl=ja&gl=JP&ceid=JP:ja",
        # 写真展・カメラ機材
        "https://news.google.com/rss/search?q=%E5%86%99%E7%9C%9F%E5%B1%95+OR+%E3%82%AD%E3%83%A4%E3%83%8E%E3%83%B3+OR+%E3%82%B7%E3%82%B0%E3%83%9E&hl=ja&gl=JP&ceid=JP:ja",
        # バドミントン（松友美佐紀・日本代表）＆サッカー
        "https://news.google.com/rss/search?q=%E6%9D%BE%E5%8F%8B%E7%BE%8E%E4%BD%90%E7%B4%80+OR+%E3%83%90%E3%83%89%E3%83%9F%E3%83%B3%E3%83%88%E3%83%B3+OR+%E6%B5%A6%E5%92%8C%E3%83%AC%E3%83%83%E3%82%BA&hl=ja&gl=JP&ceid=JP:ja",
        # サカナクション＆U2（国内情報）
        "https://news.google.com/rss/search?q=%E3%82%サ%E3%82%AB%E3%83%8A%E3%82%AF%E3%82%B7%E3%83%A7%E3%83%B3+OR+U2&hl=ja&gl=JP&ceid=JP:ja",
        # U2海外公式・英語音楽メディアニュース
        "https://news.google.com/rss/search?q=U2+band+OR+Bono+OR+The+Edge&hl=en-US&gl=US&ceid=US:en",
    ]
    
    collected_articles = []
    seen_titles = set()

    for url in feed_urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:10]:
            if entry.title not in seen_titles:
                seen_titles.add(entry.title)
                collected_articles.append(f"- {entry.title} / {entry.link}")
    
    return "\n".join(collected_articles)

def generate_morning_briefing(news_text):
    """Gemini 3.6 Flashを使ってすべての要求セクションを詳細生成"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
あなたは優秀なモーニングアナリストです。
提供された最新ニュースデータや現在知りうる市場・気象・イベントデータをもとに、詳細で読み応えのある朝のデイリーサマリーを作成してください。

以下の【1】〜【7】の各セクションを明確に分け、省略せずに十分な情報量で記述してください。

==================================================
【1. 熊谷の気象・コンディション】
・当日の天気、最高/最低気温、時間帯別の降水確率、風速、体感温度、外出・屋外活動のアドバイス

【2. グローバル・株式マーケット詳細分析】
・主要指数：SOX指数（フィラデルフィア半導体株指数）、台湾加権指数（TAIEX）、韓国総合株価指数（KOSPI）、NYダウ、ナスダック、日経平均先物、ドル円為替
・各指数の騰落背景、モメンタム・過熱感（テクニカル動向）、半導体セクターの地合い
・本日発表予定の重要経済指標、注目の市場材料

【3. 写真・アート＆カメラ機材情報】
・首都圏（東京・埼玉）で開催中または近日スタートする注目写真展・ギャラリー企画展
・キヤノン（EOS/RF/EF）、シグマなどの新製品発表、ファームウェア更新、業界動向

【4. スポーツ速報（バドミントン・サッカー・野球）】
・バドミントン：松友美佐紀選手の最新動向・試合結果を最優先に記載。世界選手権やBWFツアー等の日本勢（山口茜、奥原希望、奈良岡功大など）の速報
・サッカー：浦和レッズの試合結果・最新動向・次節カード、Jリーグ主要トピックス
・野球：NPB主要試合結果、MLB日本人選手（大谷翔平、岡本和真、村上宗隆、今井達也、佐々木朗希など）の成績・詳細スタッツ

【5. 音楽・カルチャー（サカナクション ＆ U2海外最新動向）】
・サカナクション：ツアー・ライブ・新曲・山口一郎氏の最新情報
・U2：海外の最新ニュース・リリース・ライブ動向を【日本語に翻訳して要約】。ボノやジ・エッジのコメントや近況も含めて詳しく記述すること

【6. 埼玉ローカルニュース（埼玉新聞より厳選5項目）】
・提供データおよび埼玉の地域ニュースから、県内の事件・事故・行政・話題を必ず5項目抽出し、見出し・1行要約・元記事URLを記載すること。

【7. 本日の主要・時事ニュース厳選（7〜8本）】
・日本国内・政治・社会・国際の最重要ニュースを7〜8本厳選し、見出し・1〜2行の要約・元記事URLを必ず明記すること。
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
