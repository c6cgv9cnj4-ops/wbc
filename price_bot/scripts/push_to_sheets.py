# -*- coding: utf-8 -*-
"""
scraper.py の出力(downloads/results.json)を、GAS(Google Apps Script)の
ウェブアプリ(doPost)へHTTP POSTで送信する。GCPのサービスアカウントは使用しない。

環境変数:
  GAS_WEB_APP_URL    : Apps Scriptを「ウェブアプリ」としてデプロイしたときのURL(必須)
  GAS_SHARED_SECRET  : Code.gs側のスクリプトプロパティ SHARED_SECRET と同じ値(必須)
"""
import json
import os
import sys
import urllib.error
import urllib.request

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "downloads")


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

    payload = json.dumps({"secret": secret, "items": items}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        print(f"[ERROR] GASウェブアプリがエラーを返しました(HTTP {err.code}): {body}")
        sys.exit(1)
    except urllib.error.URLError as err:
        print(f"[ERROR] GASウェブアプリへの接続に失敗しました: {err}")
        sys.exit(1)

    print(f"送信件数: {len(items)}")
    print(f"GASからの応答: {body}")

    try:
        parsed = json.loads(body)
        if not parsed.get("ok"):
            print(f"[ERROR] GAS側で処理に失敗しました: {parsed.get('error')}")
            sys.exit(1)
    except json.JSONDecodeError:
        print("[WARN] GASからの応答をJSONとして解釈できませんでした(内容は上記参照)。")


if __name__ == "__main__":
    main()
