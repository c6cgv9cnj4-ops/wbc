# -*- coding: utf-8 -*-
"""
週次マインドマップ生成

直近7日分の以下の生ログ(discord_logs.yml が日次で蓄積)を読み込み、
「3大ブランチ構造」の週次振り返りマインドマップを生成する。

  - logs/daily/YYYY-MM-DD.md   … #インプット(日々のメモ)
  - logs/health/YYYY-MM-DD.md  … #ヘルス・日報(モーニングジャーナル・運動/習慣の記録)

3大ブランチ(第1階層・名前と並び順は固定):
  1. 現実・行動 (Fact / Log)          : 行動実績、運動・習慣の消化状況
  2. 思考・内面 (Mind / Journal)      : 感情、思考の癖、モーニングジャーナルの気づき
  3. 課題と翌週のアクション (Next / Focus): ボトルネックと次週の最重要タスク

出力:
  reports/weekly/YYYY-Wxx_mindmap.md
    - Mermaid mindmap 記法のマップ
    - Markdown ツリー(ネスト箇条書き)
  DISCORD_WEBHOOK_WEEKLY が有効な URL の場合は Discord へも投稿(任意・失敗しても続行)。

設計方針:
  既存の generate_weekly_report.py(週刊レポート)とは出力ファイル名を分けており
  共存できる。入力元は同じ生ログ。ワークフローを確実にパスさせるため、以下は
  「警告のみ・exit 0」で継続する:
    * 直近7日分のログが1件も無い       → データなしの雛形マップを出力
    * Gemini API 呼び出しに失敗         → 生ログを埋め込んだ雛形マップを出力
    * Discord Webhook 未設定 / 不正 URL → 投稿をスキップ
  設定不備として exit 1 にするのは GEMINI_API_KEY 未設定のときのみ。

環境変数:
  GEMINI_API_KEY         (必須)
  DISCORD_WEBHOOK_WEEKLY (任意)
"""
import datetime
import os
import sys

import requests

JST = datetime.timezone(datetime.timedelta(hours=9))
REQUEST_TIMEOUT = 15
DISCORD_CHUNK_LIMIT = 1900

# 他スクリプト(generate_weekly_report.py 等)と同一の現行モデルに揃えている。
GEMINI_MODEL_NAME = "gemini-3.6-flash"

LOG_DAILY_DIR = "logs/daily"
LOG_HEALTH_DIR = "logs/health"
REPORT_WEEKLY_DIR = "reports/weekly"
PAST_DAYS = 7

# 3大ブランチ名。Mermaid 出力では ["..."] で囲んで使うため丸括弧を含んでも安全。
BRANCH_1 = "現実・行動 (Fact / Log)"
BRANCH_2 = "思考・内面 (Mind / Journal)"
BRANCH_3 = "課題と翌週のアクション (Next / Focus)"


def _read_if_exists(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return None


def collect_logs(today):
    """直近 PAST_DAYS 日分の #インプット / #ヘルス・日報 生ログを古い順で返す。"""
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
            parts.append(f"【#インプット(メモ)】\n{daily_log}")
        if health_log:
            parts.append(f"【#ヘルス・日報(モーニングジャーナル)】\n{health_log}")
        collected.append((date_str, "\n\n".join(parts)))
    collected.sort(key=lambda x: x[0])
    return collected


def build_prompt(week_label, logs):
    body = "\n\n".join(f"### {date_str}\n{content}" for date_str, content in logs)
    return f"""あなたは記録者本人の思考パートナーです。以下は {week_label}(直近7日間)の
Discord 生ログ(#インプット=日々のメモ / #ヘルス・日報=モーニングジャーナル・運動記録)です。

{body}

このログだけを根拠に、週次の振り返りマインドマップを作成してください。
迎合的な相槌・定型挨拶は不要。抽象論を避け、ログに実在する固有名詞・数値で書くこと。
ログに無いことは推測せず「記録なし」と明記し、絶対に捏造しないこと。

出力は必ず次の2部構成・この見出し順で、Markdown で返してください。

## Mermaidマインドマップ
```mermaid
mindmap
  root((今週の振り返り))
    b1["{BRANCH_1}"]
      ここに行動実績・運動/習慣の消化状況を3〜6ノード
    b2["{BRANCH_2}"]
      ここに感情・思考の癖・ジャーナルの気づきを3〜6ノード
    b3["{BRANCH_3}"]
      ここにボトルネックと翌週の最重要タスクを3〜6ノード
```
規則:
  - 第1階層ブランチの3行 `b1["{BRANCH_1}"]` `b2["{BRANCH_2}"]` `b3["{BRANCH_3}"]`
    は角括弧と二重引用符ごとそのままコピーし、順番も変えない。
  - 子ノードは素のテキスト(短い体言止め)。丸括弧・角括弧・コロンは使わない。
  - インデントは半角スペース(root=2 / ブランチ=4 / 子=6)。mindmap 構文を厳守。

## Markdownツリー
- 今週の振り返り
  - {BRANCH_1}
    - (3〜6項目)
  - {BRANCH_2}
    - (3〜6項目)
  - {BRANCH_3}
    - (3〜6項目)
"""


def call_gemini(api_key, prompt):
    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt)
    return resp.text.strip()


def skeleton_report(message, raw_logs=None):
    """Gemini を使わず 3大ブランチの雛形を返す(データなし / API 失敗時)。"""
    # 葉ノードは素のテキスト。丸括弧・角括弧を含めると mindmap 構文が壊れるため除去。
    leaf = message.replace("(", "（").replace(")", "）").replace("[", "").replace("]", "")
    mermaid = (
        "```mermaid\n"
        "mindmap\n"
        "  root((今週の振り返り))\n"
        f'    b1["{BRANCH_1}"]\n'
        f"      {leaf}\n"
        f'    b2["{BRANCH_2}"]\n'
        f"      {leaf}\n"
        f'    b3["{BRANCH_3}"]\n'
        f"      {leaf}\n"
        "```"
    )
    tree = (
        "- 今週の振り返り\n"
        f"  - {BRANCH_1}\n    - {message}\n"
        f"  - {BRANCH_2}\n    - {message}\n"
        f"  - {BRANCH_3}\n    - {message}\n"
    )
    md = f"## Mermaidマインドマップ\n\n{mermaid}\n\n## Markdownツリー\n\n{tree}"
    if raw_logs:
        md += "\n\n## 参考: 取得した生ログ(未整形)\n\n" + raw_logs
    return md


def chunk_message(text, limit=DISCORD_CHUNK_LIMIT):
    chunks = []
    current = ""
    for line in text.split("\n"):
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


def post_to_discord(webhook_url, message):
    if not webhook_url:
        print("[WARN] DISCORD_WEBHOOK_WEEKLY 未設定のため Discord 投稿をスキップします。")
        return
    if not webhook_url.lower().startswith(("http://", "https://")):
        print("[WARN] DISCORD_WEBHOOK_WEEKLY が http(s):// で始まらないため Discord 投稿をスキップします。")
        return
    for chunk in chunk_message(message):
        try:
            resp = requests.post(webhook_url, json={"content": chunk}, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 300:
                print(f"[WARN] Discord送信に失敗しました (HTTP {resp.status_code}): {resp.text[:200]}")
                return
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] Discord送信中に例外が発生しました: {err}")
            return
    print("[OK] Discord へ投稿しました。")


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    webhook_url = os.environ.get("DISCORD_WEBHOOK_WEEKLY")

    if not api_key:
        print("[ERROR] 環境変数 GEMINI_API_KEY が設定されていません。")
        sys.exit(1)

    today = datetime.datetime.now(JST).date()
    iso_year, iso_week, _ = today.isocalendar()
    week_label = f"{iso_year}-W{iso_week:02d}"

    logs = collect_logs(today)
    print(f"[INFO] 直近{PAST_DAYS}日分中、{len(logs)}件の日次ログを取得しました。")

    if not logs:
        print("[WARN] ログが1件も見つかりません。データなしの雛形マップを出力します。")
        body = skeleton_report("記録なし — 今週は Discord ログが取得されていません")
    else:
        try:
            body = call_gemini(api_key, build_prompt(week_label, logs))
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] Gemini API 呼び出しに失敗。雛形マップにフォールバックします: {err}")
            raw = "\n\n".join(f"### {d}\n{c}" for d, c in logs)
            body = skeleton_report("Gemini 生成に失敗。以下の生ログを参照", raw_logs=raw)

    os.makedirs(REPORT_WEEKLY_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_WEEKLY_DIR, f"{week_label}_mindmap.md")
    header = (
        f"# 週次マインドマップ {week_label}\n\n"
        f"> 生成: {datetime.datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}  \n"
        f"> 入力: 直近{PAST_DAYS}日分の #インプット / #ヘルス・日報 ログ {len(logs)}件  \n"
        f"> ブランチ: 1.現実・行動(Fact/Log) / 2.思考・内面(Mind/Journal) / 3.課題と翌週のアクション(Next/Focus)\n\n"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(header + body + "\n")
    print(f"[OK] 保存しました: {report_path}")

    post_to_discord(webhook_url, f"# 🧠 週次マインドマップ ({week_label})\n\n" + body)


if __name__ == "__main__":
    main()
