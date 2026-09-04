# -*- coding: utf-8 -*-
"""
Discordログ収集 & Issues自動起票(Step 2)

処理内容:
  1. Discord APIから当日(JST)の「#インプット」「#ヘルス・日報」の
     メッセージを取得し、それぞれ logs/daily/YYYY-MM-DD.md ・
     logs/health/YYYY-MM-DD.md にMarkdown形式で保存する。
  2. メッセージ本文が「TODO」または「BUY」で始まる投稿を、
     GitHub Issuesとして自動起票する(GITHUB_TOKENを使用)。
     同じDiscordメッセージから重複起票しないよう、Issue本文に
     埋め込んだメッセージIDで既存Issueを検索してから作成する。

このステップでは日報生成(Claude API連携によるログの要約)は行わない。
ログの保存とIssue起票のみで完結させる。

環境変数:
  DISCORD_BOT_TOKEN          (必須) Discord Botのトークン
  DISCORD_CHANNEL_ID_INPUT   (任意) 「#インプット」チャンネルのID
  DISCORD_CHANNEL_ID_HEALTH  (任意) 「#ヘルス・日報」チャンネルのID
  GITHUB_TOKEN               (必須) Issue作成用(Actions既定のGITHUB_TOKENを使う想定)
  GITHUB_REPOSITORY          (Actions実行時は自動設定される。"owner/repo"形式)

いずれかのチャンネルID未設定・取得失敗(権限不足によるDiscord APIの403等)が
あっても、このステップ自体は失敗させない(exit 0で終える)。後続の日刊/週刊
レポート生成ステップは、ログの有無に関わらず必ず実行されるべきため。

【フォーラムチャンネル対応】
「#ヘルス・日報」(モーニングジャーナル)は通常のテキストチャンネルではなく
Discordの「フォーラム」チャンネル。フォーラムには直接メッセージが無く、
投稿1件ごとに独立した「スレッド」が作られる構造のため、通常チャンネル用の
GET /channels/{id}/messages では常に0件になる(エラーにもならず静かに空振り
する)。そのためフォーラム型のチャンネルは、
  1. ギルド内のアクティブスレッド一覧 + このチャンネル配下のアーカイブ済み
     公開スレッド一覧を取得し、
  2. スレッドID(Discordスノーフレーク)から作成日時を復元して当日作成分に絞り、
  3. 各スレッドのメッセージ(投稿本文+返信)を取得
という手順で当日分の投稿を集める(fetch_forum_threads_today)。
"""
import datetime
import os
import sys

import requests

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000  # 2015-01-01T00:00:00Z (Discordスノーフレークの起点)
JST = datetime.timezone(datetime.timedelta(hours=9))
REQUEST_TIMEOUT = 15

CHANNELS = [
    {"label": "インプット", "env_id": "DISCORD_CHANNEL_ID_INPUT", "log_dir": "logs/daily", "type": "text"},
    {"label": "ヘルス・日報", "env_id": "DISCORD_CHANNEL_ID_HEALTH", "log_dir": "logs/health", "type": "forum"},
]

ISSUE_PREFIXES = ("TODO", "BUY")
ISSUE_MARKER_TEMPLATE = "<!-- discord_message_id: {message_id} -->"


# ============================================================
# Discordメッセージ取得
# ============================================================

def fetch_today_messages(channel_id, bot_token):
    """
    当日(JST 0:00以降)のメッセージを、Discordのページネーション(before)を使って
    さかのぼりながら取得し、古い順に並べ替えて返す。
    """
    headers = {"Authorization": f"Bot {bot_token}"}
    now_jst = datetime.datetime.now(JST)
    start_of_day_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)

    messages = []
    before = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before

        resp = requests.get(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break

        reached_before_today = False
        for msg in batch:
            ts_jst = datetime.datetime.fromisoformat(msg["timestamp"]).astimezone(JST)
            if ts_jst < start_of_day_jst:
                reached_before_today = True
                continue
            messages.append(msg)

        before = batch[-1]["id"]
        if reached_before_today or len(batch) < 100:
            break

    messages.reverse()  # 古い順に並べ替え
    return messages


def message_author_name(msg):
    author = msg.get("author", {}) or {}
    return author.get("global_name") or author.get("username") or "unknown"


def snowflake_to_datetime_utc(snowflake_id):
    """DiscordスノーフレークID(文字列/整数)からUTC作成日時を復元する。"""
    ms = (int(snowflake_id) >> 22) + DISCORD_EPOCH_MS
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)


def fetch_forum_threads_today(channel_id, bot_token):
    """フォーラムチャンネル配下で「本日(JST)作成されたスレッド(投稿)」を集め、
    (スレッド名, そのスレッドの全メッセージ古い順) のリストを名前順で返す。
    """
    headers = {"Authorization": f"Bot {bot_token}"}
    now_jst = datetime.datetime.now(JST)
    start_of_day_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)

    # チャンネル情報からguild_idを取得(アクティブスレッド一覧の取得に必要)
    resp = requests.get(
        f"{DISCORD_API_BASE}/channels/{channel_id}", headers=headers, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    guild_id = resp.json().get("guild_id")

    threads = []

    if guild_id:
        resp = requests.get(
            f"{DISCORD_API_BASE}/guilds/{guild_id}/threads/active",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        threads.extend(
            t for t in resp.json().get("threads", []) if t.get("parent_id") == channel_id
        )

    # アーカイブ済み公開スレッド(このチャンネル配下のみ、ページングあり)。
    # 既定のフォーラム自動アーカイブ時間(最短でも1時間)を踏まえ、当日作成分が
    # 既にアーカイブされているケースも拾えるようにする。
    before = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        resp = requests.get(
            f"{DISCORD_API_BASE}/channels/{channel_id}/threads/archived/public",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("threads", [])
        threads.extend(batch)
        archive_ts = batch[-1].get("thread_metadata", {}).get("archive_timestamp") if batch else None
        if not data.get("has_more") or not batch or not archive_ts:
            break
        before = archive_ts

    # 当日(JST)作成分だけに絞る。重複除去(アクティブ/アーカイブ両方に
    # 出現することは無いはずだが念のため)。
    seen_ids = set()
    today_threads = []
    for t in threads:
        thread_id = t.get("id")
        if not thread_id or thread_id in seen_ids:
            continue
        seen_ids.add(thread_id)
        created_jst = snowflake_to_datetime_utc(thread_id).astimezone(JST)
        if created_jst >= start_of_day_jst:
            today_threads.append(t)

    results = []
    for t in today_threads:
        thread_id = t["id"]
        thread_name = t.get("name", "(無題)")
        # スレッド自体が当日作成のため、日付フィルタは不要(全メッセージが対象)。
        messages = fetch_today_messages(thread_id, bot_token)
        results.append((thread_name, messages))

    results.sort(key=lambda item: item[0])
    return results


# ============================================================
# Markdown保存
# ============================================================

def build_markdown(messages, date_str, channel_label):
    lines = [f"# {date_str} #{channel_label}", ""]
    if not messages:
        lines.append("(この日の投稿はありませんでした)")
        return "\n".join(lines) + "\n"

    for msg in messages:
        content = (msg.get("content") or "").strip()
        if not content:
            continue  # 画像/添付のみでテキストが無い投稿はスキップ
        ts_jst = datetime.datetime.fromisoformat(msg["timestamp"]).astimezone(JST)
        time_str = ts_jst.strftime("%H:%M")
        author = message_author_name(msg)
        # Markdown内で改行を保つため、本文内改行はそのまま埋め込む
        lines.append(f"- **{time_str}** ({author}): {content}")
    return "\n".join(lines) + "\n"


def build_forum_markdown(thread_entries, date_str, channel_label):
    """フォーラムチャンネル用。スレッド(投稿)ごとに見出しを立てて本文をまとめる。"""
    lines = [f"# {date_str} #{channel_label}", ""]
    if not thread_entries:
        lines.append("(この日の投稿はありませんでした)")
        return "\n".join(lines) + "\n"

    for thread_name, messages in thread_entries:
        lines.append(f"## {thread_name}")
        has_content = False
        for msg in messages:
            content = (msg.get("content") or "").strip()
            if not content:
                continue  # 画像/添付のみでテキストが無い投稿はスキップ
            has_content = True
            ts_jst = datetime.datetime.fromisoformat(msg["timestamp"]).astimezone(JST)
            time_str = ts_jst.strftime("%H:%M")
            author = message_author_name(msg)
            lines.append(f"- **{time_str}** ({author}): {content}")
        if not has_content:
            lines.append("(本文なし)")
        lines.append("")
    return "\n".join(lines) + "\n"


def save_markdown(log_dir, date_str, content):
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"{date_str}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] 保存しました: {path}")
    return path


# ============================================================
# GitHub Issues自動起票
# ============================================================

def find_existing_issue_by_message_id(repo, token, message_id):
    """同じDiscordメッセージから既にIssueが作られていないか検索する。"""
    marker = ISSUE_MARKER_TEMPLATE.format(message_id=message_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    query = f'repo:{repo} "{marker}" in:body'
    resp = requests.get(
        "https://api.github.com/search/issues",
        headers=headers,
        params={"q": query},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        print(f"[WARN] Issue重複チェックに失敗しました(HTTP {resp.status_code}): {resp.text[:200]}")
        return None
    items = resp.json().get("items", [])
    return items[0] if items else None


def create_github_issue(repo, token, title, body):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers=headers,
        json={"title": title, "body": body},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def maybe_create_issue_for_message(msg, channel_label, repo, github_token):
    content = (msg.get("content") or "").strip()
    if not content:
        return None

    upper_content = content.upper()
    matched_prefix = next((p for p in ISSUE_PREFIXES if upper_content.startswith(p)), None)
    if not matched_prefix:
        return None

    message_id = msg["id"]
    existing = find_existing_issue_by_message_id(repo, github_token, message_id)
    if existing:
        print(f"[SKIP] 既にIssue化済みです: #{existing['number']} (message_id={message_id})")
        return None

    author = message_author_name(msg)
    ts_jst = datetime.datetime.fromisoformat(msg["timestamp"]).astimezone(JST)
    title = f"[{matched_prefix}] {content}"[:200]  # GitHubのIssueタイトル長制限への配慮
    body = (
        f"{content}\n\n"
        f"---\n"
        f"投稿者: {author} / 投稿日時: {ts_jst.strftime('%Y-%m-%d %H:%M')} JST / "
        f"チャンネル: #{channel_label}\n"
        f"{ISSUE_MARKER_TEMPLATE.format(message_id=message_id)}"
    )

    try:
        issue = create_github_issue(repo, github_token, title, body)
        print(f"[OK] Issueを作成しました: #{issue['number']} {title}")
        return issue
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Issue作成に失敗しました(message_id={message_id}): {err}")
        return None


# ============================================================
# main
# ============================================================

def main():
    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    github_token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not bot_token:
        print("[ERROR] 環境変数 DISCORD_BOT_TOKEN が設定されていません。")
        sys.exit(1)
    if not github_token or not repo:
        print("[ERROR] GITHUB_TOKEN / GITHUB_REPOSITORY が設定されていません。"
              "(通常はGitHub Actions実行時に自動設定されます)")
        sys.exit(1)

    date_str = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    issue_count = 0

    for channel in CHANNELS:
        channel_id = os.environ.get(channel["env_id"])
        if not channel_id:
            print(f"[WARN] {channel['env_id']} が未設定のため #{channel['label']} をスキップします。")
            continue

        is_forum = channel.get("type") == "forum"
        print(f"=== #{channel['label']} の{'投稿(スレッド)' if is_forum else 'メッセージ'}を取得します ===")
        try:
            if is_forum:
                thread_entries = fetch_forum_threads_today(channel_id, bot_token)
                messages = [msg for _, msgs in thread_entries for msg in msgs]
            else:
                messages = fetch_today_messages(channel_id, bot_token)
        except Exception as err:  # noqa: BLE001
            # 権限不足(403)やチャンネルID誤りなど、このチャンネル固有の問題で
            # ジョブ全体(後続の日刊/週刊レポート生成)を止めないよう、警告に留めて次へ進む。
            print(f"[WARN] #{channel['label']} の取得に失敗したためスキップします: {err}")
            continue

        if is_forum:
            print(f"[INFO] {len(thread_entries)}件のスレッド(投稿)、計{len(messages)}件のメッセージを取得しました。")
            markdown = build_forum_markdown(thread_entries, date_str, channel["label"])
        else:
            print(f"[INFO] {len(messages)}件のメッセージを取得しました。")
            markdown = build_markdown(messages, date_str, channel["label"])
        save_markdown(channel["log_dir"], date_str, markdown)

        for msg in messages:
            issue = maybe_create_issue_for_message(msg, channel["label"], repo, github_token)
            if issue:
                issue_count += 1

    print(f"=== 完了: Issue新規作成 {issue_count}件 ===")


if __name__ == "__main__":
    main()
