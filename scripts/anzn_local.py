# -*- coding: utf-8 -*-
"""
あんぜんねっと(北本市安全安心情報)を自宅Macから配信する専用入口(2026-09-26追加)。

GitHub ActionsのIPはあんぜんねっと側で約9割403になる(自宅Macからは200)ため、
あんぜんねっとの新着Embedだけを launchd から15分ごとに実行する。
Actions側(fetch_news.py)は ANZN_SOURCE=mac で、あんぜんねっとの取得を止める。

取得・新着判定・Embed生成・送信・異常通知は fetch_news.py / news_alerts.py の既存関数を
そのまま使う。stateはリポジトリ外のMac専用ファイルで、Actionsの state/news_seen.json とは共有しない。

使い方(専用cloneの直下で。手順は docs/ANZN_LOCAL_SETUP.md):
  .venv/bin/python scripts/anzn_local.py              # 本番実行(launchdから)
  .venv/bin/python scripts/anzn_local.py --dry-run    # 送信せず、stateも保存しない
  .venv/bin/python scripts/anzn_local.py --import-state state/news_seen.json
      # Actions側stateから、あんぜんねっとの既送信キーだけをMac側stateへ引き継ぐ(送信しない)

設定:
  .env(専用cloneの直下・権限600)に DISCORD_WEBHOOK_LOCAL=... を1行。値はログに出さない。
"""
import argparse
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import fetch_news  # noqa: E402
import news_alerts  # noqa: E402

DEFAULT_STATE_PATH = os.path.expanduser("~/Library/Application Support/anzn-local/anzn_seen.json")
DEFAULT_ENV_PATH = os.path.join(REPO_DIR, ".env")
WEBHOOK_ENV = "DISCORD_WEBHOOK_LOCAL"


def load_webhook(env_path):
    """環境変数 → .env の順に DISCORD_WEBHOOK_LOCAL を探す(値は返すだけで表示しない)。"""
    if os.environ.get(WEBHOOK_ENV):
        return os.environ[WEBHOOK_ENV]
    if not os.path.exists(env_path):
        return None
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line.startswith(WEBHOOK_ENV + "="):
                return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


def import_state(src_path, state):
    """Actions側stateから、あんぜんねっとの既送信キーだけをコピーする(既存キーは上書きしない)。"""
    with open(src_path, encoding="utf-8") as f:
        src = json.load(f)
    added = 0
    for key, seen_at in src.items():
        if "anzn.net" in key and key not in state:
            state[key] = seen_at
            added += 1
    return added


def run(args):
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    fetch_news.STATE_PATH = args.state  # 既存の load/save_seen_state をMac専用stateに向ける
    state = fetch_news.prune_old_entries(fetch_news.load_seen_state(), now)

    if args.import_state:
        added = import_state(args.import_state, state)
        fetch_news.save_seen_state(state)
        print(f"[INFO] Actions側stateから、あんぜんねっとの既送信キーを{added}件引き継ぎました({args.state})。")
        return 0

    webhook = load_webhook(args.env_file)
    if not webhook and not args.dry_run:
        print(f"[ERROR] {WEBHOOK_ENV} が環境変数にも {args.env_file} にもありません。")
        return 1

    new_items = fetch_news.fetch_anzn_new_items(state, now)  # 新着キーをstateへ仮登録する
    print(f"[INFO] {now:%Y-%m-%d %H:%M} 取得{'成功' if fetch_news.ANZN_LAST_FETCH_OK else '失敗'} / 新着{len(new_items)}件")
    for item in new_items:
        print(f"  - {item.get('datetime')} [{item.get('city')}] {item.get('summary', '')[:40]}")

    if args.dry_run:
        issues = news_alerts.issues()
        if issues:
            print(f"[DRY-RUN] 記録された異常: {[i['kind'] for i in issues]}")
        print("[DRY-RUN] Discordへは送信せず、stateも保存しません。")
        return 0

    exit_code = 0
    embed = fetch_news.build_anzn_alert_embed(new_items, now)
    if embed and not fetch_news.send_embed_to_discord(webhook, embed):
        # 送信に失敗した回の新着キーは確定させず、次回(15分後)に再送できる状態へ戻す
        for item in new_items:
            state.pop(item["dedupe_key"], None)
        news_alerts.record("discord_send_anzn_local", "local", "Discord送信(あんぜんねっと新着・自宅Mac)",
                           "送信失敗", f"スキップ(新着{len(new_items)}件は次回に再送)")
        exit_code = 1
    elif embed:
        print(f"[INFO] あんぜんねっと新着{len(new_items)}件を送信しました。")

    news_alerts.flush({"local": webhook}, fetch_news.send_to_discord, state, now)
    fetch_news.save_seen_state(state)
    return exit_code


def main(argv=None):
    ap = argparse.ArgumentParser(description="あんぜんねっと新着を自宅Macから配信する")
    ap.add_argument("--dry-run", action="store_true", help="送信せず、stateも保存しない")
    ap.add_argument("--state", default=DEFAULT_STATE_PATH, help="Mac専用stateのパス")
    ap.add_argument("--env-file", default=DEFAULT_ENV_PATH, help=".envのパス")
    ap.add_argument("--import-state", metavar="PATH", help="Actions側stateから既送信キーを引き継ぐ")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
