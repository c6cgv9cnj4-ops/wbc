# -*- coding: utf-8 -*-
"""
週次スプレッドシート同期

直近7日分の以下の生ログ(discord_logs.yml が日次で蓄積)を、ISO週タグ
(例: 2026-W36)付きで Google スプレッドシートへ upsert 同期する。「週完結」の
区切りとして外部シートに集約コピーを作るのが目的で、ローカルの生ログ
(logs/daily・logs/health)は削除せず保持する(同期失敗時のデータ喪失を避けるため。
削除運用に変えたい場合は要相談)。

  - logs/daily/YYYY-MM-DD.md   … #インプット(日々のメモ)
  - logs/health/YYYY-MM-DD.md  … #ヘルス・日報(モーニングジャーナル・運動/習慣の記録)

送信先は gas/journal_log_sheet.gs をデプロイした Web App(新規・価格トラッカー用
GASとは別物)。1日1種別につき1行、key=`日付|種別` で upsert されるため、
同じ週を何度再実行しても重複行は生まれない。

【GETベースにしている理由】
price_bot/scripts/push_to_sheets.py と同じ既知の不具合(このウェブアプリの
デプロイでPOSTリクエストのみが常にHTTP 405で拒否される。GAS側の実行数ログには
「完了」と記録されるのにHTTP応答だけ405になる)を本デプロイでも実機確認したため、
確実に動作するGET(doGet)のクエリパラメータ経由で送信する方式に統一している。
secret・itemsをURLのクエリパラメータとして渡すためURL長を安全な範囲に収める
必要があり、1リクエスト=1件(BATCH_SIZE=1)で送信する(ジャーナル本文は
価格データより長くなりがちなため)。

【リトライについて】
実機検証で、GAS側の実行自体は成功している(スプレッドシートには正しく書き込まれる)
のにHTTPレスポンスだけ404等で返ってくる、Google側インフラの一過性の揺れを複数回
確認した。1回失敗しただけで exit 1(失敗通知)にすると誤検知が多くなるため、
軽いリトライ(GAS_MAX_ATTEMPTS回、GAS_RETRY_WAIT秒間隔)を挟む。それでも失敗する
場合のみ実際の失敗として扱う。

環境変数:
  JOURNAL_GAS_WEB_APP_URL   (任意。未設定なら同期をスキップし exit 0)
  JOURNAL_GAS_SHARED_SECRET (任意。URL設定時は実質必須)
"""
import datetime
import json
import os
import sys
import time
import urllib.parse

import requests

JST = datetime.timezone(datetime.timedelta(hours=9))
REQUEST_TIMEOUT = 30
BATCH_SIZE = 1  # GET方式のためURL長を安全な範囲に収める(理由は上記docstring参照)
GAS_MAX_ATTEMPTS = 3
GAS_RETRY_WAIT = 3  # 秒

LOG_SOURCES = [
    ("daily", "logs/daily", "#インプット(メモ)"),
    ("health", "logs/health", "#ヘルス・日報(モーニングジャーナル)"),
]
PAST_DAYS = 7


def collect_week_items(today):
    """直近 PAST_DAYS 日分の #インプット / #ヘルス・日報 を1日1種別=1item で返す。"""
    items = []
    for i in range(PAST_DAYS):
        day = today - datetime.timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        iso_year, iso_week, _ = day.isocalendar()
        week_label = f"{iso_year}-W{iso_week:02d}"
        for type_key, dir_path, _label in LOG_SOURCES:
            path = os.path.join(dir_path, f"{date_str}.md")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if not text.strip():
                continue
            items.append(
                {
                    "key": f"{date_str}|{type_key}",
                    "week": week_label,
                    "date": date_str,
                    "type": type_key,
                    "text": text,
                }
            )
    items.sort(key=lambda it: it["key"])
    return items


def request_gas(url):
    """GAS Web Appへ GET し、JSONを返す。GAS側は成功しているのにHTTP応答だけ
    失敗する一過性の揺れを吸収するため、軽くリトライする。"""
    last_err = None
    for attempt in range(1, GAS_MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json(), None
        except Exception as err:  # noqa: BLE001
            last_err = err
            if attempt < GAS_MAX_ATTEMPTS:
                print(f"[WARN] リクエスト失敗(試行{attempt}/{GAS_MAX_ATTEMPTS}): {err} → {GAS_RETRY_WAIT}秒後に再試行")
                time.sleep(GAS_RETRY_WAIT)
    return None, last_err


def send_upsert(gas_url, gas_secret, items):
    ok_all = True
    total_written = 0
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i : i + BATCH_SIZE]
        query = urllib.parse.urlencode(
            {
                "mode": "upsert",
                "secret": gas_secret,
                "items": json.dumps(batch, ensure_ascii=False),
            }
        )
        full_url = f"{gas_url}?{query}"
        data, err = request_gas(full_url)
        if data is None:
            print(f"[ERROR] バッチ{i // BATCH_SIZE + 1}の送信に失敗しました({GAS_MAX_ATTEMPTS}回試行): {err}")
            ok_all = False
            continue
        if not data.get("ok"):
            print(f"[ERROR] GAS側で処理に失敗しました: {data.get('error')}")
            ok_all = False
            continue
        total_written += data.get("written", 0)
        print(f"[OK] バッチ{i // BATCH_SIZE + 1}({len(batch)}件) → {data.get('written', 0)}行 upsert")
    print(f"[INFO] 合計 {total_written}行をスプレッドシートへ書き込みました。")
    return ok_all


def main():
    gas_url = os.environ.get("JOURNAL_GAS_WEB_APP_URL")
    gas_secret = os.environ.get("JOURNAL_GAS_SHARED_SECRET")

    today = datetime.datetime.now(JST).date()
    items = collect_week_items(today)
    print(f"[INFO] 直近{PAST_DAYS}日分中、{len(items)}件の日次ログ(メモ/ジャーナル)を検出しました。")

    if not items:
        print("[INFO] 同期対象がありません。")
        return

    if not gas_url or not gas_secret:
        keys = ", ".join(it["key"] for it in items)
        print(
            "[WARN] JOURNAL_GAS_WEB_APP_URL / JOURNAL_GAS_SHARED_SECRET が未設定のため、"
            "スプレッドシートへの同期をスキップします(ローカルログは保持されています)。"
        )
        print(f"[WARN] 未同期のまま残る項目: {keys}")
        return

    if not send_upsert(gas_url, gas_secret, items):
        sys.exit(1)


if __name__ == "__main__":
    main()
