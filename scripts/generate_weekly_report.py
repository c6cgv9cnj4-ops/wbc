# -*- coding: utf-8 -*-
"""
Step 3: 週刊レポート自動生成

過去7日分の reports/daily/YYYY-MM-DD.md を読み込み、Anthropic API(Claude)で
「週間統合マインドマップ(Mermaid)」「思考変遷」「身体相関分析」「ネクストアクション」
を生成し、reports/weekly/YYYY-Wxx.md に保存したうえでDiscordへ送信する。

環境変数:
  ANTHROPIC_API_KEY     (必須)
  ANTHROPIC_MODEL        (任意。既定値は下記MODEL参照)
  DISCORD_WEBHOOK_WEEKLY (必須)
"""
import datetime
import os
import sys

import anthropic
import requests

JST = datetime.timezone(datetime.timedelta(hours=9))
REQUEST_TIMEOUT = 15
DISCORD_CHUNK_LIMIT = 1900

# generate_daily_report.py と同じ理由でモデル名を環境変数で上書き可能にしている。
DEFAULT_MODEL = "claude-sonnet-5"

REPORT_DAILY_DIR = "reports/daily"
REPORT_WEEKLY_DIR = "reports/weekly"
PAST_DAYS = 7


def collect_past_daily_reports(today):
    collected = []
    for i in range(PAST_DAYS):
        day = today - datetime.timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        path = os.path.join(REPORT_DAILY_DIR, f"{date_str}.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                collected.append((date_str, f.read()))
    collected.sort(key=lambda x: x[0])  # 古い順
    return collected


def build_prompt(week_label, daily_reports):
    if not daily_reports:
        body = "(過去7日分の日刊レポートが見つかりませんでした)"
    else:
        parts = []
        for date_str, content in daily_reports:
            parts.append(f"### {date_str}\n{content}")
        body = "\n\n".join(parts)

    return f"""あなたは記録者本人の思考パートナーです。以下は{week_label}の日刊レポート群です。

{body}

上記を踏まえて、Markdown形式で以下の4つを作成してください。
迎合的な相槌や定型的な挨拶は省き、本質を突いた鋭い視点と建設的な分析を提供してください。
抽象論を避け、具体的かつ検証可能な言葉で記述してください。
日刊レポートが実質的に無い場合は、その旨を素直に記載し、内容を捏造しないでください。

## 週間統合マインドマップ
```mermaid
mindmap
  root((今週のテーマ))
```
の形式で、実際の日刊レポートの内容に基づいたノードに置き換えてください。

## 思考変遷
月曜〜日曜にかけての思考の深化・見解の変化を箇条書きで比較整理する。

## 身体相関分析
ヘルスケア関連の記述と、その日のアウトプット量・思考の明晰さとの関係を客観的に分析する。
該当する記述が乏しい場合は、その旨を明記する。

## ネクストアクション
翌週に検証すべき「重要な問い」を3点、「具体的ToDo」を3点、提示する。
"""


def call_claude(api_key, model, prompt):
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


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
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    webhook_url = os.environ.get("DISCORD_WEBHOOK_WEEKLY")

    if not api_key:
        print("[ERROR] 環境変数 ANTHROPIC_API_KEY が設定されていません。")
        sys.exit(1)
    if not webhook_url:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_WEEKLY が設定されていません。")
        sys.exit(1)

    today = datetime.datetime.now(JST).date()
    iso_year, iso_week, _ = today.isocalendar()
    week_label = f"{iso_year}-W{iso_week:02d}"

    daily_reports = collect_past_daily_reports(today)
    print(f"[INFO] 過去{PAST_DAYS}日分中、{len(daily_reports)}件の日刊レポートが見つかりました。")

    prompt = build_prompt(week_label, daily_reports)

    try:
        report_text = call_claude(api_key, model, prompt)
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Claude APIの呼び出しに失敗しました: {err}")
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
