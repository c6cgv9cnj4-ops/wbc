# -*- coding: utf-8 -*-
"""
#モーニングジャーナル(Discord フォーラム)の「投稿ガイドライン」に、書き始めの足場になる
テンプレートを設定する(2026-09-26)。

Discord のフォーラムには「新規投稿の本文を自動で埋める」機能が無いため、公式機能の
投稿ガイドライン(チャンネルの topic。フォーラム上部と投稿作成画面に表示される)を使う。
テンプレートは表示されるだけで、入力の強制・項目の必須化・文字数制限は一切しない
(空欄のまま・長文・脱線、すべて自由。投稿そのものの仕様は何も変えない)。

  既定は dry-run: Discord へは送らず、設定予定の内容だけを表示する。
    python scripts/set_journal_forum_guidelines.py
  実際に設定する(チャンネル管理権限のある Bot トークンが必要):
    DISCORD_BOT_TOKEN=... DISCORD_CHANNEL_ID_MORNING_JOURNAL=... \\
      python scripts/set_journal_forum_guidelines.py --apply

変更するのはチャンネルの topic だけ。既存の投稿・タグ・権限・他チャンネルには触れない。
日記本文は読まない・保存しない。
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

DISCORD_API_BASE = "https://discord.com/api/v10"
FORUM_TYPES = {15, 16}          # GUILD_FORUM / GUILD_MEDIA
TOPIC_MAX = 4096                # フォーラムの投稿ガイドラインの上限文字数

JOURNAL_TEMPLATE = """【モーニングジャーナル】

■ 今の頭の中
・

■ 昨日から残っていること
・

■ 気になっていること
・

■ 本当はどうしたい？
・

■ 最近よく考えること
・

■ 今日思いついたこと
・

■ ここから連想したこと
・

■ その他
・"""


def build_payload() -> dict:
    assert len(JOURNAL_TEMPLATE) <= TOPIC_MAX
    return {"topic": JOURNAL_TEMPLATE}


def apply(token: str, channel_id: str) -> int:
    headers = {"Authorization": f"Bot {token}", "User-Agent": "wbc-journal-guidelines/1.0"}
    url = f"{DISCORD_API_BASE}/channels/{channel_id}"
    cur = requests.get(url, headers=headers, timeout=20)
    if cur.status_code != 200:
        print(f"[ERROR] チャンネル情報を取得できません(HTTP {cur.status_code})")
        return 1
    meta = cur.json()
    if meta.get("type") not in FORUM_TYPES:
        print(f"[ERROR] フォーラムチャンネルではありません(type={meta.get('type')})。中止します。")
        return 1
    before = meta.get("topic") or ""
    if before == JOURNAL_TEMPLATE:
        print("[INFO] 既に同じテンプレートが設定されています(変更なし)")
        return 0
    print(f"[INFO] 対象: #{meta.get('name')} / 現在のガイドライン {len(before)}字 → {len(JOURNAL_TEMPLATE)}字")
    resp = requests.patch(url, headers=headers, json=build_payload(), timeout=20)
    if resp.status_code >= 300:
        print(f"[ERROR] 設定に失敗しました(HTTP {resp.status_code})。Bot にチャンネル管理権限があるか確認してください。")
        return 1
    after = (resp.json().get("topic") or "")
    ok = after == JOURNAL_TEMPLATE
    print("[OK] 投稿ガイドラインにテンプレートを設定しました" if ok else "[WARN] 設定後の内容が一致しません")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="#モーニングジャーナルの投稿ガイドラインにテンプレートを設定")
    ap.add_argument("--apply", action="store_true", help="実際に Discord へ設定する(既定は dry-run)")
    ap.add_argument("--channel-id", default="", help="既定は DISCORD_CHANNEL_ID_MORNING_JOURNAL")
    args = ap.parse_args()

    if not args.apply:
        print("[DRY-RUN] Discord へは送信しません。設定予定の投稿ガイドライン:\n")
        print(JOURNAL_TEMPLATE)
        print(f"\n({len(JOURNAL_TEMPLATE)}字 / 上限 {TOPIC_MAX}字)")
        return 0

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = (args.channel_id or os.environ.get("DISCORD_CHANNEL_ID_MORNING_JOURNAL", "")).strip()
    if not token or not channel_id:
        print("[ERROR] DISCORD_BOT_TOKEN と チャンネルID(--channel-id または DISCORD_CHANNEL_ID_MORNING_JOURNAL)が必要です")
        return 1
    return apply(token, channel_id)


if __name__ == "__main__":
    sys.exit(main())
