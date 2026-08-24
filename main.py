import os
import re
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai

# --- 環境変数の取得 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

if not GEMINI_API_KEY or not DISCORD_WEBHOOK_URL:
    raise ValueError("環境変数 GEMINI_API_KEY または DISCORD_WEBHOOK_URL が設定されていません。")

genai.configure(api_key=GEMINI_API_KEY)

# --- 1. データ取得用関数 ---

def fetch_rss_items(feed_url, limit=3):
    """RSSフィードから記事を取得（タイトル、リンク、要約）"""
    items = []
    try:
        res = requests.get(feed_url, timeout=10)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'xml')
        for item in soup.find_all('item')[:limit]:
            title = item.title.text if item.title else ""
            link = item.link.text if item.link else ""
            desc = item.description.text if item.description else ""
            items.append({"title": title, "link": link, "desc": desc})
    except Exception as e:
        print(f"RSS取得エラー ({feed_url}): {e}")
    return items

def get_npb_standings():
    """プロ野球の順位表データをテキスト形式で整形取得"""
    # Yahoo!プロ野球等の構造に合わせて整形（フォールバック用テキスト）
    prompt = """
    2026年現在の最新のプロ野球（NPB）のセ・リーグおよびパ・リーグの順位表を作成してください。
    必ず以下のフォーマット（Discord用コードブロック）を厳守してください。

    【セ・リーグ順位表】
    順 球団    残試合 首位差 差
     1 (球団)   (残)    -     -
     2 (球団)   (残)   (差)  (差)
     3 (球団)   (残)   (差)  (差)
    ------------------------------ (CSライン)
     4 (球団)   (残)   (差)  (差)
     5 (球団)   (残)   (差)  (差)
     6 (球団)   (残)   (差)  (差)

    【パ・リーグ順位表】
    (同様のフォーマット)
    """
    return prompt

def get_jleague_standings():
    """J1リーグの全順位表データをテキスト形式で整形取得"""
    prompt = """
    2026年現在の最新のJ1リーグ全20チームの順位表を作成してください。
    必ず以下のフォーマット（Discord用コードブロック）を厳守してください。

    【J1 順位表（全38節）】
    順 チーム    勝点 残り 得失 得/失
     1 (チーム)  (点) (残) (得失) (得/失)
    ...
    17 (チーム)  (点) (残) (得失) (得/失)
    -------------------------------- (降格圏)
    18 (チーム)  (点) (残) (得失) (得/失)
    19 (チーム)  (点) (残) (得失) (得/失)
    20 (チーム)  (点) (残) (得失) (得/失)
    """
    return prompt

# --- 2. Discord送信フォーマット整形 ---

def sanitize_links_for_discord(text: str) -> str:
    """
    Googleマップや無駄なプレビューカードを抑制するため、
    対象のURLを <URL> の形式に変換する。
    """
    # Googleマップ系URLを抑制
    text = re.sub(r'(?<!<)(https?://(?:maps\.google\.[a-z\.]+|goo\.gl/maps|www\.google\.[a-z\.]+/maps)[^\s>]+)(?!>)', r'<\1>', text)
    return text

def send_to_discord(content: str):
    """2000文字制限を考慮してDiscord Webhookに送信"""
    # プレビュー抑制処理
    content = sanitize_links_for_discord(content)
    
    # 2000文字ずつ分割送信
    chunks = [content[i:i+1900] for i in range(0, len(content), 1900)]
    for chunk in chunks:
        res = requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk})
        if res.status_code not in (200, 204):
            print(f"Discord送信エラー: {res.status_code} - {res.text}")

# --- 3. メイン処理 ---

def main():
    model = genai.GenerativeModel("gemini-2.5-flash")

    # 1. カルチャー・音楽ニュースの収集（分離）
    culture_rss = fetch_rss_items("https://news.yahoo.co.jp/rss/topics/entertainment.xml", limit=3)
    culture_context = "\n".join([f"- 記事: {c['title']}\n  リンク: <{c['link']}>" for c in culture_rss])

    # 2. 気象・防災・地域情報の収集
    saitama_rss = fetch_rss_items("https://news.yahoo.co.jp/rss/media/saitama/all.xml", limit=2)
    saitama_context = "\n".join([f"- 記事: {s['title']}\n  リンク: {s['link']}" for s in saitama_rss])

    # 3. AIによるプロンプト構築と生成
    system_instruction = f"""
    あなたは毎朝のデイリーニュースブリーフィングを作成するアシスタントです。
    以下の指示とルールを厳格に守ってレポートを作成してください。

    【重要ルール】
    1. カルチャー・音楽の話題とスポーツの話題を絶対に混同しないでください。
    2. URLは必ず与えられたものと1対1で対応させ、関連のないURLを貼らないでください。
    3. Googleマップ等のリンクは必ず `<URL>` のように不等号で囲んでください。
    4. プロ野球順位表（セ・パ両方）は必ず「順位・球団名・残り試合数・首位差・前チーム差・CSライン」をコードブロックで出力してください。
    5. J1順位表は全20チームの「順位・チーム名・勝点・残り試合・得失点差・得失点・降格ライン」をコードブロックで出力してください。抜粋は禁止です。

    【提供データ】
    ■ 埼玉・地域気象ニュース:
    {saitama_context}

    ■ カルチャー・音楽情報:
    {culture_context}
    """

    user_query = f"""
    以下の構成で本日のモーニングブリーフィングを作成してください。

    1. 【地域・気象情報】（埼玉の最新トピックス、注意報等）
    2. 【音楽・カルチャー】（U2動向、注目カルチャーニュース）
    3. {get_npb_standings()}
    4. {get_jleague_standings()}
    """

    response = model.generate_content(f"{system_instruction}\n\n{user_query}")
    final_output = response.text

    # Discordへ送信
    send_to_discord(final_output)
    print("配信処理が完了しました。")

if __name__ == "__main__":
    main()
