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
        published = getattr(entry, "published", "日時不明")
        if video_id:
            try:
                transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
                full_text = " ".join([t['text'] for t in transcript_list])
                transcripts_summary.append(f"【動画タイトル: {title}】(公開日時: {published})\n文字起こし: {full_text[:2000]}\n")
            except Exception:
                transcripts_summary.append(f"【動画タイトル: {title}】(公開日時: {published})\n")
        else:
            transcripts_summary.append(f"【動画タイトル: {title}】(公開日時: {published})\n")
            
    return "\n".join(transcripts_summary)

def get_news_feeds():
    """国内・地域・火災・市況・カルチャーの最新ニュースを日時付きで収集"""
    feed_urls = [
        # 市況・株価・為替
        "https://news.google.com/rss/search?q=%E6%97%A5%E7%B5%8C%E5%B9%B3%E5%9D%87+OR+SOX%E6%8C%87%E6%95%B0+OR+NY%E3%83%80%E3%82%A6+OR+%E3%83%89%E3%83%AB%E5%86%86+%E5%89%8D%E6%97%A5%E6%AF%94&hl=ja&gl=JP&ceid=JP:ja",
        # 火災・消防速報
        "https://news.google.com/rss/search?q=%E7%81%AB%E7%81%BD+OR+%E7%81%AB%E4%BA%8B+OR+%E5%BB%B6%E7%84%BC+OR+%E6%B6%88%E9%98%B2%E5%87%BA%E5%8B%95&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=%E5%9F%BC%E7%8E%89+%E7%81%AB%E4%BA%8B+OR+%E5%8C%97%E6%9C%AC+%E7%81%AB%E4%BA%8B&hl=ja&gl=JP&ceid=JP:ja",
        # 経済指標・マクロ速報
        "https://news.google.com/rss/search?q=%E5%B0%8F%E5%A3%B2%E5%A3%B2%E4%B8%8A%E9%AB%98+OR+CPI+OR+GDP+OR+%E7%B5%8C%E6%B8%88%E6%8C%87%E6%A8%99+%E9%80%9F%E5%A0%B1&hl=ja&gl=JP&ceid=JP:ja",
        # 各種主要ニュース
        "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/NATION?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=ja&gl=JP&ceid=JP:ja",
        # 埼玉新聞・地域
        "https://news.google.com/rss/search?q=%E5%9F%BC%E7%8E%89%E6%96%B0%E8%81%9E+OR+%E5%9F%BC%E7%8E%89%E7%9C%8C&hl=ja&gl=JP&ceid=JP:ja",
        # 写真展・カメラ機材（国内限定）
        "https://news.google.com/rss/search?q=%E5%86%99%E7%9C%9F%E5%B1%95+%E6%9D%B1%E4%BA%AC+OR+%E5%86%99%E7%9C%9F%E5%B1%95+%E5%9F%BC%E7%8E%89+OR+%E3%82%AD%E3%83%A4%E3%83%8E%E3%83%B3+OR+%E3%82%B7%E3%82%B0%E3%83%9E&hl=ja&gl=JP&ceid=JP:ja",
        # バドミントン（松友美佐紀優先）＆浦和レッズ
        "https://news.google.com/rss/search?q=%E6%9D%BE%E5%8F%8B%E7%BE%8E%E4%BD%90%E7%B4%80+OR+%E3%83%90%E3%83%89%E3%83%9F%E3%83%B3%E3%83%88%E3%83%B3+OR+%E6%B5%A6%E5%92%8C%E3%83%AC%E3%83%83%E3%82%BA&hl=ja&gl=JP&ceid=JP:ja",
        # プロ野球(NPB)全試合結果
        "https://news.google.com/rss/search?q=%E3%83%97%E3%83%AD%E9%87%8E%E7%90%83+%E8%A9%A6%E5%90%88%E7%B5%90%E6%9E%9C+OR+%E3%82%BB%E3%83%BB%E3%83%AA%E3%83%BC%E3%82%B0+OR+%E3%83%91%E3%83%BB%E3%83%AA%E3%83%BC%E3%82%B0&hl=ja&gl=JP&ceid=JP:ja",
        # Jリーグ全試合結果
        "https://news.google.com/rss/search?q=J1+%E8%A9%A6%E5%90%88%E7%B5%90%E6%9E%9C+OR+J%E3%83%AA%E3%83%BC%E3%82%B0+%E7%B5%90%E6%9E%9C&hl=ja&gl=JP&ceid=JP:ja",
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
                pub = getattr(entry, 'published', '日時不明')
                collected_articles.append(f"- 【{pub}】 {entry.title} / {entry.link}")
    
    return "\n".join(collected_articles)

def generate_morning_briefing(news_text, cnbc_text, anzn_text):
    """日時明記・URL埋め込み無効化・全試合網羅・国内限定写真展を含む完全生成"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
あなたはトップクラスの金融・時事・防災速報アナリストです。
提供されたデータをもとに、Discord上で極めて視認性が高く、実用的な最新速報サマリーを作成してください。

【出力時の厳格な必須ルール】
1. **URLのリンク形式（重要）**:
   Discordで巨大なプレビューカードが表示されるのを防ぐため、すべてのURLは必ず `<https://...>` のように `< >`（不等号）で囲んで出力すること。
   例: `<https://news.google.com/...>`
2. **日時の完全明記**:
   各項目（ニュース、気象、火災、スポーツ、カルチャー等）には、必ず「何月何日 何時何分 発表/開催/実施」の日時情報を明記すること。
3. **写真展は日本国内（首都圏中心）のみ**:
   海外の写真展は除外し、日本国内（東京・埼玉・神奈川・千葉等）の注目写真展のみを記載。
   ・会場名、住所、会期日時
   ・公式URL（`<URL>`形式）
   ・GoogleマップURL（`<https://maps.google.com/?q=会場名>` 形式）
   ・北本駅からの目安所要時間
4. **スポーツ結果の網羅**:
   ・NPB（プロ野球）および Jリーグは「何月何日時点」かを明記し、行われた試合結果を全カード網羅すること。
   ・MLB日本人選手は「等」などの曖昧な言葉を使わず、大谷翔平選手はじめ所属球団と具体的な個人成績（打率・本塁打・投球回など）を記載。
   ・バドミントンは松友美佐紀選手の動向を最優先し、BWFワールドツアーがある場合は大会グレード（Super 1000/750/500/300/100）を必ず明記。
5. **市況・経済指標**:
   ・各指数は「取得・基準時間」と「前日比（値幅・％）」を必ず記載。
   ・直近の重要経済指標は以下のMarkdownテーブル形式で出力すること：
     | 項目 | 予想 | 結果 |
     | :--- | :--- | :--- |

==================================================
【1. 北本市 生活・地域情報（anzn.netより）】※最上部に配置
・本日のゴミ収集・分別、市からのお知らせ、注意事項

【2. 🚨 火災・防災・事故速報】
・埼玉および全国の直近火災・事故・消防出動（発生・発表日時を必ず記載）

【3. 熊谷の気象・コンディション】
・発表日時、天気、気温、降水確率、風速、体感温度、外出アドバイス

【4. 📊 重要経済指標・速報テーブル ＆ 前日CNBC徹底分析】
・発表日時入り経済指標テーブル（予想・結果・変動幅）
・CNBC文字起こしからの米国マクロ、FRB動向、個別銘柄議論、著名コメンテーター発言要約（放送日記載）

【5. 📈 グローバル・株式マーケット指数詳細分析】
※必ず【基準時間】と【前日比（値幅・％）】を明記：
・NYダウ、ナスダック、SOX指数、日経先物(CME)、ドル円、台湾加権、韓国KOSPI

【6. 📷 写真・アート＆カメラ機材情報（国内・首都圏限定）】
・注目写真展（会期、会場、住所、Googleマップ `<URL>`、北本駅からのアクセス所要時間）
・キヤノン・シグマ新製品動向

【7. 🏸⚽⚾ スポーツ速報・全試合結果】
・バドミントン：松友美佐紀選手最優先、日本代表勢（BWF大会グレード明記）
・サッカー：Jリーグ全試合結果（◯月◯日開催分）、浦和レッズ動向
・野球：NPB全試合結果（◯月◯日開催分）、MLB日本人選手（大谷翔平等、所属球団と正確なスタッツ）

【8. 🎵 音楽・カルチャー（サカナクション ＆ U2海外動向）】
・サカナクション最新動向（発言日・発表日時）
・U2海外動向（記事発表日時・日本語翻訳要約）

【9. 埼玉ローカルニュース（埼玉新聞より厳選5項目）】
・配信日時、見出し、1行要約、元記事 `<URL>`

【10. 本日の主要・時事ニュース厳選（7〜8本）】
・配信日時、見出し、1〜2行要約、元記事 `<URL>`
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
    print("データ収集中（anzn.net / CNBC / 各種最新ニュース）...")
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
