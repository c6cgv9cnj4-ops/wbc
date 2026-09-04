# -*- coding: utf-8 -*-
"""
月次統合マインドマップ生成(客観視の鏡)

weekly-sheet-sync.yml がGoogleスプレッドシート(gas/journal_log_sheet.gs 経由)へ
週次同期した当月分の全ログ(#インプット/#ヘルス・日報)をまとめて取得し、
Gemini API でキーワード抽出→3大ブランチ構造の月次マインドマップを生成する。

3大ブランチ(第1階層・名前と並び順は固定):
  1. 事実 (Fact)               : 客観的な行動実績・運動/習慣の消化状況。感情語を含めない。
  2. 感情・思考ループ (Emotion / Loop): 感情の記述、および月内で複数回繰り返された
                                  「思考のループ」(足踏み・言い訳・ボトルネック)を明示的に特定する。
  3. 翌月の一手 (Next Action)   : 分散させず、最もレバレッジの効く最重要項目1つに絞る。

「事実」と「感情」を同じノードに混在させないことが本スクリプトの分析要件の核。

出力:
  reports/monthly/YYYY-MM_mindmap.md
    - Mermaid mindmap 記法のマップ
    - Markdown ツリー
    - 検出された思考のループ(一覧)
    - 翌月の一手(単一項目)

実行タイミング:
  schedule(月末判定): cron は毎月28〜31日に発火するが、「翌日が1日」の日=月末
  以外は何もせず exit 0 でスキップする。
  workflow_dispatch(手動実行): 月末判定をスキップし、常に当月分で実際に生成処理を
  走らせる(動作検証のため)。GITHUB_EVENT_NAME で判定。

設計方針(ワークフローを確実にパスさせるため、以下は警告のみ・exit 0):
  * スプレッドシート未設定 / 取得失敗   → データなしの雛形マップを出力
  * 当月分のログが1件も無い             → データなしの雛形マップを出力
  * Gemini API 呼び出しに失敗            → 生ログを埋め込んだ雛形マップを出力
  設定不備として exit 1 にするのは GEMINI_API_KEY 未設定のときのみ。

【リトライについて】
実機検証で、GAS側の実行自体は成功しているのにHTTPレスポンスだけ失敗する
一過性の揺れを複数回確認した(sync_weekly_sheet.py と共通の事象)。1回の
失敗だけで「スプレッドシート未設定/取得失敗」扱いにすると誤検知が多くなる
ため、軽いリトライ(GAS_MAX_ATTEMPTS回)を挟んでから諦める。

環境変数:
  GEMINI_API_KEY             (必須)
  JOURNAL_GAS_WEB_APP_URL    (任意)
  JOURNAL_GAS_SHARED_SECRET  (任意)
"""
import datetime
import os
import sys
import time

import requests

JST = datetime.timezone(datetime.timedelta(hours=9))
REQUEST_TIMEOUT = 30
GAS_MAX_ATTEMPTS = 3
GAS_RETRY_WAIT = 3  # 秒

# 他スクリプト(generate_weekly_mindmap.py 等)と同一の現行モデルに揃えている。
GEMINI_MODEL_NAME = "gemini-3.6-flash"

REPORT_MONTHLY_DIR = "reports/monthly"

BRANCH_FACT = "事実 (Fact)"
BRANCH_EMOTION = "感情・思考ループ (Emotion / Loop)"
BRANCH_ACTION = "翌月の一手 (Next Action)"


def is_month_end(today):
    return (today + datetime.timedelta(days=1)).day == 1


def fetch_month_items(gas_url, gas_secret, month_label):
    """スプレッドシートから当月分の全ログを取得する。
    未設定/失敗時は None、設定済みで0件なら空リストを返す(呼び出し側で区別する)。
    """
    if not gas_url or not gas_secret:
        return None
    data = None
    last_err = None
    for attempt in range(1, GAS_MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                gas_url,
                params={"mode": "fetch", "secret": gas_secret, "month": month_label},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as err:  # noqa: BLE001
            last_err = err
            if attempt < GAS_MAX_ATTEMPTS:
                print(f"[WARN] 取得に失敗(試行{attempt}/{GAS_MAX_ATTEMPTS}): {err} → {GAS_RETRY_WAIT}秒後に再試行")
                time.sleep(GAS_RETRY_WAIT)
    if data is None:
        print(f"[WARN] スプレッドシートからの取得に{GAS_MAX_ATTEMPTS}回失敗しました: {last_err}")
        return None
    if not data.get("ok"):
        print(f"[WARN] GAS側で取得に失敗しました: {data.get('error')}")
        return None
    items = data.get("items", [])
    items.sort(key=lambda it: (it.get("date", ""), it.get("type", "")))
    return items


def build_prompt(month_label, items):
    body = "\n\n".join(
        f"### {it.get('date')} [{it.get('type')}]\n{it.get('text')}" for it in items
    )
    return f"""あなたは記録者本人の「客観視の鏡」です。誰からも指摘されない環境下で、
本人の代わりに冷徹かつ客観的に1ヶ月間({month_label})を観測する役割を担います。
以下は{month_label}のスプレッドシート集約ログ(#インプット=メモ / #ヘルス・日報=
モーニングジャーナル・運動記録)です。

{body}

このログだけを根拠に、月次統合マインドマップを作成してください。
迎合的な相槌・定型挨拶・気休めは一切不要。忖度せず、本質を突いた鋭い分析を行うこと。
ログに無いことは推測せず「記録なし」と明記し、絶対に捏造しないこと。

最重要ルール:
  1. 「事実」と「感情」を絶対に同じノードに混在させない。行動・回数・頻度などの
     客観的事実は必ずブランチ1、感情表現・心理状態は必ずブランチ2に分離すること。
  2. ブランチ2では、月内の複数の日にまたがって繰り返し現れているパターン
     (足踏み・言い訳・堂々巡りの思考・先延ばし等の「思考のループ」)を最低1つは
     具体的に特定し、該当する日付を添えてラベル付けすること。本当に繰り返しが
     無ければ「思考のループは検出されませんでした」と明記する。
  3. ブランチ3(翌月の一手)は複数を並べて拡散させず、最もレバレッジの効く
     ただ1つの項目に絞り込むこと。

出力は必ず次の4部構成・この見出し順で、Markdown で返してください。

## Mermaidマインドマップ
```mermaid
mindmap
  root((月次統合マインドマップ))
    b1["{BRANCH_FACT}"]
      ここに客観的事実・行動実績を3〜6ノード(感情語を含めない)
    b2["{BRANCH_EMOTION}"]
      ここに感情表現と、検出した思考のループを3〜6ノード
    b3["{BRANCH_ACTION}"]
      ここに翌月の一手を1ノードのみ
```
規則:
  - 第1階層ブランチの3行 `b1["{BRANCH_FACT}"]` `b2["{BRANCH_EMOTION}"]`
    `b3["{BRANCH_ACTION}"]` は角括弧と二重引用符ごとそのままコピーし、順番も変えない。
  - 子ノードは素のテキスト(短い体言止め)。丸括弧・角括弧・コロンは使わない。
  - インデントは半角スペース(root=2 / ブランチ=4 / 子=6)。mindmap 構文を厳守。
  - b3 の子ノードは1個だけにすること。

## Markdownツリー
- 月次統合マインドマップ
  - {BRANCH_FACT}
    - (3〜6項目、事実のみ)
  - {BRANCH_EMOTION}
    - (3〜6項目、感情・ループのみ)
  - {BRANCH_ACTION}
    - (1項目のみ)

## 検出された思考のループ
(繰り返しパターンごとに「パターン名: 該当日付・要約」の箇条書き。無ければ
「思考のループは検出されませんでした」の1行のみ)

## 翌月の一手(最重要1項目)
(太字で1項目のみ。理由を1〜2文で添える)
"""


def call_gemini(api_key, prompt):
    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt)
    return resp.text.strip()


def skeleton_report(message, raw_logs=None):
    """Gemini を使わず 3大ブランチの雛形を返す(データなし / API 失敗時)。"""
    leaf = message.replace("(", "（").replace(")", "）").replace("[", "").replace("]", "")
    mermaid = (
        "```mermaid\n"
        "mindmap\n"
        "  root((月次統合マインドマップ))\n"
        f'    b1["{BRANCH_FACT}"]\n'
        f"      {leaf}\n"
        f'    b2["{BRANCH_EMOTION}"]\n'
        f"      {leaf}\n"
        f'    b3["{BRANCH_ACTION}"]\n'
        f"      {leaf}\n"
        "```"
    )
    tree = (
        "- 月次統合マインドマップ\n"
        f"  - {BRANCH_FACT}\n    - {message}\n"
        f"  - {BRANCH_EMOTION}\n    - {message}\n"
        f"  - {BRANCH_ACTION}\n    - {message}\n"
    )
    md = (
        f"## Mermaidマインドマップ\n\n{mermaid}\n\n"
        f"## Markdownツリー\n\n{tree}\n"
        "## 検出された思考のループ\n\n思考のループは検出されませんでした(データなしのため判定不能)。\n\n"
        "## 翌月の一手(最重要1項目)\n\n**判定不能** — 十分なログが無いため提示できません。"
    )
    if raw_logs:
        md += "\n\n## 参考: 取得した生ログ(未整形)\n\n" + raw_logs
    return md


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    gas_url = os.environ.get("JOURNAL_GAS_WEB_APP_URL")
    gas_secret = os.environ.get("JOURNAL_GAS_SHARED_SECRET")

    if not api_key:
        print("[ERROR] 環境変数 GEMINI_API_KEY が設定されていません。")
        sys.exit(1)

    today = datetime.datetime.now(JST).date()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    if event_name == "schedule" and not is_month_end(today):
        print(f"[INFO] 本日({today})は月末ではないためスキップします(schedule実行)。")
        return

    if event_name != "schedule" and not is_month_end(today):
        print(f"[INFO] 手動実行のため月末判定をスキップし、当月({today.strftime('%Y-%m')})で検証生成します。")

    month_label = today.strftime("%Y-%m")
    items = fetch_month_items(gas_url, gas_secret, month_label)

    if items is None:
        print("[WARN] スプレッドシート未設定または取得失敗。データなしの雛形マップを出力します。")
        body = skeleton_report("記録なし — スプレッドシート未設定または取得失敗のため月次データがありません")
    elif not items:
        print(f"[INFO] {month_label}分の記録はスプレッドシートに0件でした。")
        body = skeleton_report(f"記録なし — {month_label}分のログがスプレッドシートにありません")
    else:
        print(f"[INFO] {month_label}分のログを{len(items)}件取得しました。")
        try:
            body = call_gemini(api_key, build_prompt(month_label, items))
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] Gemini API 呼び出しに失敗。雛形マップにフォールバックします: {err}")
            raw = "\n\n".join(f"### {it.get('date')} [{it.get('type')}]\n{it.get('text')}" for it in items)
            body = skeleton_report("Gemini 生成に失敗。以下の生ログを参照", raw_logs=raw)

    os.makedirs(REPORT_MONTHLY_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_MONTHLY_DIR, f"{month_label}_mindmap.md")
    header = (
        f"# 月次統合マインドマップ {month_label}\n\n"
        f"> 生成: {datetime.datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}  \n"
        f"> 入力: {month_label}分の #インプット / #ヘルス・日報 ログ "
        f"{len(items) if items else 0}件(スプレッドシート集約)\n\n"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(header + body + "\n")
    print(f"[OK] 保存しました: {report_path}")


if __name__ == "__main__":
    main()
