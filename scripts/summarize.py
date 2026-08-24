import os
import datetime
import requests
import google.generativeai as genai

# 環境変数の取得
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_INPUT = os.getenv("DISCORD_CHANNEL_ID_INPUT")
CHANNEL_HEALTH = os.getenv("DISCORD_CHANNEL_ID_HEALTH")
WEBHOOK_DAILY = os.getenv("DISCORD_WEBHOOK_DAILY")
WEBHOOK_WEEKLY = os.getenv("DISCORD_WEBHOOK_WEEKLY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODE = os.getenv("RUN_MODE", "daily") # "daily" または "weekly"

# Geminiの設定
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

def fetch_messages(channel_id, hours=24):
    """指定チャンネルから過去X時間分のメッセージを取得"""
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=100"
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        print(f"Error fetching channel {channel_id}: {res.text}")
        return []
    
    messages = res.json()
    cutoff_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    
    valid_logs = []
    for msg in messages:
        # ISO8601形式の日時パース
        msg_time = datetime.datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
        if msg_time >= cutoff_time and not msg.get("author", {}).get("bot", False):
            valid_logs.append(f"[{msg['timestamp'][:10]}] {msg['content']}")
    
    return valid_logs[::-1] # 古い順に並び替え

def generate_summary(input_logs, health_logs, mode="daily"):
    """Geminiで要約を生成"""
    logs_text = f"【インプット・メモ】\n" + ("\n".join(input_logs) if input_logs else "記録なし")
    logs_text += f"\n\n【ヘルス・運動・生活ログ】\n" + ("\n".join(health_logs) if health_logs else "記録なし")
    
    if mode == "daily":
        prompt = f"""
以下のログは個人の1日のメモと活動記録です。
これをもとに、見やすく構造化された「日報サマリー」を作成してください。
無駄な前置きは省き、Markdown形式（箇条書きや太字）で端的に整理してください。

【構成】
1. 💡 本日の主要インプット・気付き
2. 🏃 活動・運動・コンディション
3. 📝 総括・明日に向けた一言

---
{logs_text}
"""
    else:
        prompt = f"""
以下のログは個人の1週間のメモと活動記録です。
これをもとに、振り返りと次週の指針となる「週刊レポート」を作成してください。
無駄な前置きは省き、Markdown形式（箇条書きや太字）で端的に整理してください。

【構成】
1. 📈 今週のハイライト・トピック
2. 🏃 運動・生活リズムの傾向
3. 🎯 次週のアクションプラン・課題

---
{logs_text}
"""
    
    response = model.generate_content(prompt)
    return response.text

def send_discord_webhook(webhook_url, content):
    """Discord Webhookに送信（2000文字制限対策含む）"""
    if not webhook_url:
        print("Webhook URL is not configured.")
        return
    
    # 2000文字ごとに分割して送信
    for i in range(0, len(content), 1900):
        chunk = content[i:i+1900]
        payload = {"content": chunk}
        requests.post(webhook_url, json=payload)

def main():
    hours = 24 if MODE == "daily" else 168 # 24時間 or 7日間(168時間)
    input_logs = fetch_messages(CHANNEL_INPUT, hours=hours)
    health_logs = fetch_messages(CHANNEL_HEALTH, hours=hours)
    
    if not input_logs and not health_logs:
        print("新規のログがありませんでした。")
        return
    
    summary = generate_summary(input_logs, health_logs, mode=MODE)
    
    target_webhook = WEBHOOK_DAILY if MODE == "daily" else WEBHOOK_WEEKLY
    header = "📅 **【日刊まとめレポート】**\n\n" if MODE == "daily" else "📊 **【週刊まとめレポート】**\n\n"
    send_discord_webhook(target_webhook, header + summary)
    print("レポート送信完了")

if __name__ == "__main__":
    main()
