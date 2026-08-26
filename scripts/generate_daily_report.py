# -*- coding: utf-8 -*-
"""
日刊レポート自動生成

logs/daily/YYYY-MM-DD.md ・ logs/health/YYYY-MM-DD.md (当日分、discord_logs.yml
が日次で蓄積)を読み込み、Anthropic API(Claude)で「日刊サマリー」と
「ミニMermaidマップ」を生成し、reports/daily/YYYY-MM-DD.md に保存したうえで
Discordへ送信する。

2026-08-26に一度廃止され、週刊レポート(generate_weekly_report.py)は
無料枠のGoogle GenAI SDK(Gemini)へ移行済みだが、日刊レポートは要望により
Anthropic API(Claude)のまま復元している。週刊レポートの入力元は
本レポート(reports/daily)ではなく logs/daily・logs/health の生ログを直接
参照する設計のままなので、本スクリプトが仮に失敗・スキップされても
週刊レポートには影響しない。

環境変数:
  ANTHROPIC_API_KEY    (必須)
  ANTHROPIC_MODEL       (任意。既定値は下記DEFAULT_MODEL参照)
  DISCORD_WEBHOOK_DAILY (必須)
"""
import datetime
import os
import sys

import anthropic
import requests

JST = datetime.timezone(datetime.timedelta(hours=9))
REQUEST_TIMEOUT = 15
DISCORD_CHUNK_LIMIT = 1900

DEFAULT_MODEL = "claude-sonnet-5"

LOG_DAILY_DIR = "logs/daily"
LOG_HEALTH_DIR = "logs/health"
REPORT_DAILY_DIR = "reports/daily"


def read_log(dir_path, date_str):
    path = os.path.join(dir_path, f"{date_str}.md")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def build_prompt(date_str, daily_log, health_log):
    daily_text = daily_log if daily_log else "(本日のインプットログはありません)"
    health_text = health_log if health_log else "(本日のヘルス・日報ログはありません)"

    return f"""あなたは記録者本人の思考パートナーです。以下は{date_str}のDiscordログです。

【#インプット ログ】
{daily_text}

【#ヘルス・日報 ログ】
{health_text}

上記を踏まえて、Markdown形式で以下の2つを作成してください。
迎合的な相槌や定型的な挨拶は省き、本質を突いた鋭い視点で記述してください。
ログが実質的に空の場合は、その旨を素直に記載してください(内容を捏造しないこと)。

## 日刊サマリー
今日の思考・行動・気づきを箇条書きで簡潔にまとめる。

## ミニMermaidマップ
今日の主要テーマを mermaid mindmap記法 で可視化する。
```mermaid
mindmap
  root((今日のテーマ))
```
の形式で、実際のログ内容に基づいたノードに置き換えてください。
"""


def call_claude(api_key, model, prompt):
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2000,
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
    webhook_url = os.environ.get("DISCORD_WEBHOOK_DAILY")

    if not api_key:
        print("[ERROR] 環境変数 ANTHROPIC_API_KEY が設定されていません。")
        sys.exit(1)
    if not webhook_url:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_DAILY が設定されていません。")
        sys.exit(1)

    date_str = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    daily_log = read_log(LOG_DAILY_DIR, date_str)
    health_log = read_log(LOG_HEALTH_DIR, date_str)

    if not daily_log and not health_log:
        print(f"[INFO] {date_str} 分のログが見つかりません。空のレポートとして処理を継続します。")

    prompt = build_prompt(date_str, daily_log, health_log)

    try:
        report_text = call_claude(api_key, model, prompt)
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Claude APIの呼び出しに失敗しました: {err}")
        sys.exit(1)

    os.makedirs(REPORT_DAILY_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DAILY_DIR, f"{date_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 日刊レポート {date_str}\n\n{report_text}\n")
    print(f"[OK] 保存しました: {report_path}")

    header = f"# 📓 日刊レポート ({date_str})\n\n"
    if not send_to_discord(webhook_url, header + report_text):
        sys.exit(1)


if __name__ == "__main__":
    main()
