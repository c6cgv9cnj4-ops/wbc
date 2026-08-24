# -*- coding: utf-8 -*-
"""
GASウェブアプリへの疎通確認用: ダミーデータ1件を書き込んでみるテストスクリプト。
手動でブラウザURLを開かなくても、GitHub Actions(workflow_dispatch)経由で
GAS_WEB_APP_URL / GAS_SHARED_SECRET を使った実際の書き込みを検証できる。

確認後は、ダッシュボード・価格履歴（ログ）に追加された
「【テスト店舗】(ダミーデータ確認用)」の行を手動で削除してください。
"""
import json
import os

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "downloads")

DUMMY_ITEM = {
    "product_name": "キッコーマン 濃いだし本つゆ (1L基準)",
    "store": "テスト店舗(ダミーデータ確認用)",
    "price_yen": 100,
    "unit_price": 10.0,
    "amazon_base_price": 39.8,
    "diff": -29.8,
    "deadline": None,
    "signal": "🟢 店舗買い推奨（底値圏）",
}


def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    results_path = os.path.join(DOWNLOAD_DIR, "results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump([DUMMY_ITEM], f, ensure_ascii=False, indent=2)
    print(f"ダミーデータを書き出しました: {results_path}")

    import push_to_sheets
    push_to_sheets.main()


if __name__ == "__main__":
    main()
