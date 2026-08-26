# -*- coding: utf-8 -*-
"""
Step 3: 週刊レポート自動生成

過去7日分の logs/daily/YYYY-MM-DD.md (#インプット) ・ logs/health/YYYY-MM-DD.md
(#ヘルス・日報) の生ログ(discord_logs.ymlが日次で蓄積)を読み込み、Gemini API
(gemini-3.6-flash)で「週間統合マインドマップ(Mermaid)」「思考変遷」
「身体相関分析」「ネクストアクション」を生成し、reports/weekly/YYYY-Wxx.md に
保存したうえでDiscordへ送信する。

日刊レポート(daily_summary.yml)が要約済みレポート(reports/daily)を
生成する構成になった後も、そちらへの依存を避けるため、生ログを直接
週間分析の入力とする設計を維持している(日刊側が失敗・未生成でも
週刊レポートがデータ切れで空にならないようにするため)。
また、無料枠で運用するためAPIをAnthropic(Claude)からGoogle GenAI SDK
(Gemini)へ移行し、ANTHROPIC_API_KEYへの依存は排除している
(日刊レポート側は要望によりAnthropic APIを使用)。

環境変数:
  GEMINI_API_KEY         (必須)
  DISCORD_WEBHOOK_WEEKLY (必須)
"""
import datetime
import os
import sys

import requests

JST = datetime.timezone(datetime.timedelta(hours=9))
REQUEST_TIMEOUT = 15
DISCORD_CHUNK_LIMIT = 1900

# 依頼当初はgemini-2.5-flashを指定されていたが、Gemini API側で廃止済み
# (404 NOT_FOUND、"models/gemini-3.6-flashを使ってください"と応答)だったため、
# 他スクリプト(fetch_injury_alerts.py等)と同じ現行モデルに合わせている。
GEMINI_MODEL_NAME = "gemini-3.6-flash"

LOG_DAILY_DIR = "logs/daily"
LOG_HEALTH_DIR = "logs/health"
REPORT_WEEKLY_DIR = "reports/weekly"
PAST_DAYS = 7


def _read_if_exists(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return None


def collect_past_daily_reports(today):
    """過去PAST_DAYS日分の #インプット / #ヘルス・日報 の生ログをまとめて返す。
    どちらか一方でも存在する日のみ収集対象とする。
    """
    collected = []
    for i in range(PAST_DAYS):
        day = today - datetime.timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        daily_log = _read_if_exists(os.path.join(LOG_DAILY_DIR, f"{date_str}.md"))
        health_log = _read_if_exists(os.path.join(LOG_HEALTH_DIR, f"{date_str}.md"))
        if daily_log is None and health_log is None:
            continue

        parts = []
        if daily_log:
            parts.append(f"【#インプット】\n{daily_log}")
        if health_log:
            parts.append(f"【#ヘルス・日報】\n{health_log}")
        collected.append((date_str, "\n\n".join(parts)))

    collected.sort(key=lambda x: x[0])  # 古い順
    return collected


def build_prompt(week_label, daily_reports):
    if not daily_reports:
        body = "(過去7日分のログが見つかりませんでした)"
    else:
        parts = []
        for date_str, content in daily_reports:
            parts.append(f"### {date_str}\n{content}")
        body = "\n\n".join(parts)

    return f"""あなたは記録者本人の思考パートナーです。以下は{week_label}のDiscord生ログ(#インプット・#ヘルス・日報)です。

{body}

上記を踏まえて、Markdown形式で以下の4つを作成してください。
迎合的な相槌や定型的な挨拶は省き、本質を突いた鋭い視点と建設的な分析を提供してください。
抽象論を避け、具体的かつ検証可能な言葉で記述してください。
ログが実質的に無い場合は、その旨を素直に記載し、内容を捏造しないでください。

## 週間統合マインドマップ
```mermaid
mindmap
  root((今週のテーマ))
```
の形式で、実際のログの内容に基づいたノードに置き換えてください。

## 思考変遷
月曜〜日曜にかけての思考の深化・見解の変化を箇条書きで比較整理する。

## 身体相関分析
ヘルスケア関連の記述と、その日のアウトプット量・思考の明晰さとの関係を客観的に分析する。
該当する記述が乏しい場合は、その旨を明記する。

## ネクストアクション
翌週に検証すべき「重要な問い」を3点、「具体的ToDo」を3点、提示する。
"""


def call_gemini(api_key, prompt):
    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt)
    return resp.text.strip()


def chunk_message(text, limit=DISCORD_CHUNK_LIMIT):
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_to_discord(webhook_url, message):
    if not webhook_url:
        print("[ERROR] Webhook URLが設定されていないため送信をスキップします。")
        return False
    ok = True
    for chunk in chunk_message(message):
        try:
            resp = requests.post(webhook_url, json={"content": chunk}, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 300:
                print(f"[ERROR] Discord送信に失敗しました(HTTP {resp.status_code}): {resp.text[:300]}")
                ok = False
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] Discord送信中に例外が発生しました: {err}")
            ok = False
    return ok


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    webhook_url = os.environ.get("DISCORD_WEBHOOK_WEEKLY")

    if not api_key:
        print("[ERROR] 環境変数 GEMINI_API_KEY が設定されていません。")
        sys.exit(1)
    if not webhook_url:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_WEEKLY が設定されていません。")
        sys.exit(1)

    today = datetime.datetime.now(JST).date()
    iso_year, iso_week, _ = today.isocalendar()
    week_label = f"{iso_year}-W{iso_week:02d}"

    daily_reports = collect_past_daily_reports(today)
    print(f"[INFO] 過去{PAST_DAYS}日分中、{len(daily_reports)}件の日次ログが見つかりました。")

    prompt = build_prompt(week_label, daily_reports)

    try:
        report_text = call_gemini(api_key, prompt)
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Gemini APIの呼び出しに失敗しました: {err}")
        sys.exit(1)

    os.makedirs(REPORT_WEEKLY_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_WEEKLY_DIR, f"{week_label}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 週刊レポート {week_label}\n\n{report_text}\n")
    print(f"[OK] 保存しました: {report_path}")

    header = f"# 🗓️ 週刊レポート ({week_label})\n\n"
    if not send_to_discord(webhook_url, header + report_text):
        sys.exit(1)


if __name__ == "__main__":
    main()
