# -*- coding: utf-8 -*-
"""
scraper.py の出力(downloads/results.json)を、GAS(Google Apps Script)の
ウェブアプリへ送信する。GCPのサービスアカウントは使用しない。

【GETベースにしている理由】
このウェブアプリのデプロイで、POSTリクエストのみが常にHTTP 405で拒否される
(GAS側の実行数ログには「完了」と記録されるのにHTTP応答だけ405になる)という
Google側の既知の不具合に遭遇したため、確実に動作するGET(doGet)のクエリ
パラメータ経由でデータを送信する方式に統一した。secret・itemsをURLの
クエリパラメータとして渡す。1リクエストのURLが長くなりすぎないよう、
itemsは複数件まとめて1回で送るのではなくバッチ分割して送信する。

環境変数:
  GAS_WEB_APP_URL    : Apps Scriptを「ウェブアプリ」としてデプロイしたときのURL(必須)
  GAS_SHARED_SECRET  : Code.gs側のスクリプトプロパティ SHARED_SECRET と同じ値(必須)
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "downloads")
BATCH_SIZE = 5  # 1回のGETリクエストで送るitems件数(URL長を安全な範囲に収めるため)


def send_batch(url, secret, items):
    query = urllib.parse.urlencode({
        "secret": secret,
        "items": json.dumps(items, ensure_ascii=False),
    })
    full_url = f"{url}?{query}"

    try:
        with urllib.request.urlopen(full_url, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return True, body
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        return False, f"HTTP {err.code}: {body[:500]}"
    except urllib.error.URLError as err:
        return False, str(err)


def main():
    url = os.environ.get("GAS_WEB_APP_URL")
    secret = os.environ.get("GAS_SHARED_SECRET")
    if not url or not secret:
        print("[ERROR] GAS_WEB_APP_URL / GAS_SHARED_SECRET が設定されていません。")
        sys.exit(1)

    results_path = os.path.join(DOWNLOAD_DIR, "results.json")
    if not os.path.exists(results_path):
        print(f"[INFO] {results_path} が見つかりません。送信対象なしとして終了します。")
        return

    with open(results_path, encoding="utf-8") as f:
        items = json.load(f)

    if not items:
        print("[INFO] 送信対象0件です。")
        return

    total_written = 0
    total_notified = 0
    had_error = False

    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        ok, body = send_batch(url, secret, batch)
        if not ok:
            print(f"[ERROR] バッチ{i // BATCH_SIZE + 1}の送信に失敗しました: {body}")
            had_error = True
            continue

        print(f"バッチ{i // BATCH_SIZE + 1}({len(batch)}件) GASからの応答: {body}")
        try:
            parsed = json.loads(body)
            if not parsed.get("ok"):
                print(f"[ERROR] GAS側で処理に失敗しました: {parsed.get('error')}")
                had_error = True
            else:
                total_written += parsed.get("written", 0)
                total_notified += parsed.get("notified", 0)
        except json.JSONDecodeError:
            print("[WARN] GASからの応答をJSONとして解釈できませんでした(内容は上記参照)。")
            had_error = True

    print(f"=== 完了: 合計{total_written}件書き込み, {total_notified}件をToDo自動通知 ===")
    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
