# -*- coding: utf-8 -*-
"""
あんぜんねっと(県央の災害発生情報)を公式Mastodon経由で配信する軽量入口(2026-11-06追加)。

news.yml(4時間ごと・全体取得・Gemini使用)とは別に、30分ごとに動く専用ワークフロー(anzn-mastodon.yml)から呼ぶ。
- Gemini は使わない(無料枠の制約を受けない)。
- 既送信記録・カーソルは state/news_seen.json を news.yml と共有する(同じ記事を二重送信しない)。
- 自宅Mac(anzn_local.py)が止まっていても、この経路で数十分以内に届く。

使い方(リポジトリ直下で):
  python scripts/anzn_mastodon_only.py
環境変数: DISCORD_WEBHOOK_LOCAL(#webhook_local のWebhook。ログには出さない)
"""
import datetime
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import fetch_news  # noqa: E402
import news_alerts  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))


def main():
    now = datetime.datetime.now(JST)
    webhook = os.environ.get("DISCORD_WEBHOOK_LOCAL", "").strip()
    if not webhook:
        print("[ERROR] DISCORD_WEBHOOK_LOCAL が設定されていません。配信を中止します。")
        return 1
    try:
        state = fetch_news.prune_old_entries(fetch_news.load_seen_state(), now)
    except fetch_news.StateCorruptError as err:
        # 壊れた既送信記録を空として扱うと全件を再送するため、送信も保存もせず止める
        print(f"[ERROR] {err}。配信を中止します(既送信記録は変更していません)。")
        return 1

    had_error = fetch_news.run_mastodon_anzn(state, now, webhook)
    news_alerts.flush({"local": webhook}, fetch_news.send_to_discord, state, now,
                      embed_fn=fetch_news.send_embed_to_discord)
    fetch_news.save_seen_state(state)
    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
