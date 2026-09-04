# -*- coding: utf-8 -*-
"""
Discordチャンネル一覧調査(診断用・手動実行専用)

DISCORD_BOT_TOKEN を使い、Botが参加している全サーバーのチャンネル名・IDを
一覧表示する。DISCORD_CHANNEL_ID_HEALTH 等のチャンネルIDをGitHub Secretsに
登録する際、Discordアプリを開いて手動でIDをコピーする代わりに使う診断ツール。
list-discord-channels.yml も workflow_dispatch 専用で、schedule実行はしない。

注意: チャンネル一覧の取得(このスクリプト)に必要な権限と、実際にそのチャンネルの
メッセージを読む権限(export_discord_logs.py が使う)は別。一覧に出てきても、
Botにそのチャンネルの「メッセージ履歴を読む」権限が無ければ discord_logs.yml は
403で失敗し続けるので、その場合はDiscord側でBotのロール権限を見直すこと。

環境変数:
  DISCORD_BOT_TOKEN (必須)
"""
import os
import sys

import requests

DISCORD_API_BASE = "https://discord.com/api/v10"
REQUEST_TIMEOUT = 15

# 「モーニングジャーナル」関連チャンネルを見つけやすくするためのキーワード。
HIGHLIGHT_KEYWORDS = ["モーニング", "ジャーナル", "ヘルス", "日報", "journal", "health"]

CHANNEL_TYPE_LABELS = {
    0: "テキスト",
    2: "ボイス",
    4: "カテゴリ",
    5: "アナウンス",
    15: "フォーラム",
}


def api_get(path, token):
    headers = {"Authorization": f"Bot {token}"}
    resp = requests.get(f"{DISCORD_API_BASE}{path}", headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("[ERROR] 環境変数 DISCORD_BOT_TOKEN が設定されていません。")
        sys.exit(1)

    try:
        guilds = api_get("/users/@me/guilds", token)
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] サーバー一覧の取得に失敗しました: {err}")
        sys.exit(1)

    if not guilds:
        print("[WARN] Botが参加しているサーバーが1つもありません。")
        return

    print(f"[INFO] Botは{len(guilds)}個のサーバーに参加しています。\n")

    matches = []
    for guild in guilds:
        guild_id = guild["id"]
        guild_name = guild.get("name", "(名前不明)")
        print(f"=== サーバー: {guild_name} (id={guild_id}) ===")
        try:
            channels = api_get(f"/guilds/{guild_id}/channels", token)
        except Exception as err:  # noqa: BLE001
            print(f"  [WARN] チャンネル一覧の取得に失敗しました: {err}")
            continue
        for ch in channels:
            ch_type = ch.get("type")
            type_label = CHANNEL_TYPE_LABELS.get(ch_type, f"type={ch_type}")
            name = ch.get("name", "")
            ch_id = ch.get("id")
            marker = ""
            if any(kw.lower() in name.lower() for kw in HIGHLIGHT_KEYWORDS):
                marker = "  ← 候補"
                matches.append((guild_name, name, ch_id))
            print(f"  [{type_label}] #{name}  id={ch_id}{marker}")
        print()

    if matches:
        print("=== 「モーニングジャーナル」候補チャンネル ===")
        for guild_name, name, ch_id in matches:
            print(f"  {guild_name} / #{name} -> DISCORD_CHANNEL_ID_HEALTH={ch_id}")
    else:
        print("[WARN] キーワードに一致するチャンネルが見つかりませんでした。上記一覧から目視で確認してください。")


if __name__ == "__main__":
    main()
