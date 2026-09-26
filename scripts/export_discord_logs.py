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

【Discord Developer Portal側の必須設定】
Bot設定の "Privileged Gateway Intents" で "MESSAGE CONTENT INTENT" を有効化
すること。これが無効だと、メッセージ自体は取得できてもcontentフィールドが
常に空文字列で返る(添付件数等のメタ情報は見えるのに本文だけ空、という分かり
にくい形で失敗する)。実機調査でこれが原因だったことを確認済み。

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
  2. 各スレッドを「スレッド名の日付(YYYY/MM/DD)」で日付ごとに振り分け
     (名前から読めなければ作成日JST)、
  3. 直近7日分の logs/health/YYYY-MM-DD.md を毎回すべて再生成する(冪等)
という手順で集める(fetch_forum_threads_by_date)。
※スレッド名の日付と作成日はズレることが多く(例: 「2026/09/15」が9/18作成)、
  cronも遅延で日付をまたぐため、「本日作成分」だけを見る方式では常に空になる。
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

TEXT_LOOKBACK_DAYS = 3  # テキストチャンネルは直近何日分(JST)を毎回再生成するか


def fetch_messages_since(channel_id, bot_token, start_date):
    """start_date(JST 0:00)以降のメッセージを、ページネーション(before)でさかのぼりながら
    全件取得し、古い順に返す。呼び出し側で日付ごとに振り分けて日次ログを再生成する。

    2026-09-21修正: 従来の fetch_today_messages は「実行時点の今日0:00以降」だけを取得して
    いたため、GitHub Actionsのcron遅延で実行が日付をまたぐと、前日分が「今日」の
    ファイルに混ざる/前日分を取りこぼす問題があった(フォーラムと同じ問題)。
    """
    headers = {"Authorization": f"Bot {bot_token}"}
    start_dt = datetime.datetime.combine(start_date, datetime.time(0, 0), tzinfo=JST)

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

        reached_older = False
        for msg in batch:
            ts_jst = datetime.datetime.fromisoformat(msg["timestamp"]).astimezone(JST)
            if ts_jst < start_dt:
                reached_older = True
                continue
            messages.append(msg)

        before = batch[-1]["id"]
        if reached_older or len(batch) < 100:
            break

    messages.reverse()  # 古い順に並べ替え
    return messages


def message_date_jst(msg):
    return datetime.datetime.fromisoformat(msg["timestamp"]).astimezone(JST).date()


def message_author_name(msg):
    author = msg.get("author", {}) or {}
    return author.get("global_name") or author.get("username") or "unknown"


FORUM_LOOKBACK_DAYS = 7  # フォーラムは直近何日分(スレッド名の日付基準)を毎回再生成するか


def fetch_forum_threads_by_date(channel_id, bot_token, start_date, end_date):
    """フォーラム配下のスレッドを「スレッド名の日付(YYYY/MM/DD)」で日付ごとに振り分けて返す。

    返り値: {date: [(スレッド名, そのスレッドの全メッセージ古い順, スレッドURL), ...]}
    (start_date〜end_date の範囲のみ。名前から日付が読めないスレッドは作成日(JST)を使う)

    2026-09-21修正: 従来は「本日0:00以降に作成されたスレッド」だけを対象にしていたため、
      ① スレッド名の日付と作成日がズレる(例: 「2026/09/15」が9/18作成)、
      ② cronの遅延で実行が日付をまたぐ、
    のどちらでも当日分が拾えず、logs/health が毎日「投稿なし」になっていた。
    さらに、スレッド内メッセージも「今日0:00以降」で再度絞っていたため、名前が当日でも
    作成日が違えば本文が0件になっていた。ここでは日付では絞らず、全メッセージを取得する。
    """
    from journal_review import (JST as _JST, _list_forum_threads, _thread_messages,
                                parse_thread_date, snowflake_to_dt_utc, thread_url)

    guild_id, raw = _list_forum_threads(channel_id, bot_token)
    seen = set()
    by_date = {}
    for t in raw:
        tid = t.get("id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        name = t.get("name", "(無題)")
        d = parse_thread_date(name) or snowflake_to_dt_utc(tid).astimezone(_JST).date()
        if not (start_date <= d <= end_date):
            continue
        by_date.setdefault(d, []).append((name, tid))

    result = {}
    for d, items in by_date.items():
        entries = []
        for name, tid in sorted(items, key=lambda x: int(x[1])):
            messages = _thread_messages(tid, bot_token)
            entries.append((name, messages, thread_url(guild_id, tid)))
        result[d] = entries
    return result


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

    for entry in thread_entries:
        thread_name, messages = entry[0], entry[1]
        url = entry[2] if len(entry) > 2 else ""
        lines.append(f"## {thread_name}")
        if url:
            lines.append(f"[スレッドを開く]({url})")
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
    """ランナー内の作業ファイルとして保存する(Gitにはコミットしない。.gitignore 済み)。
    同じジョブ内で sync_weekly_sheet.py が読んで Google スプレッドシートへ送る。"""
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"{date_str}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] 作業ファイルを作成: {path}")
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
        print(f"[OK] Issueを作成しました: #{issue['number']}")   # タイトル(本文)は公開ログに出さない
        return issue
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Issue作成に失敗しました(message_id={message_id}): {err}")
        return None


# ============================================================
# main
# ============================================================

def _lookback_override():
    """LOG_LOOKBACK_DAYS が正の整数なら、手動の過去分復元(バックフィル)として両チャンネルの
    遡り日数をこの値に上書きする。バックフィル時は、過去のTODO/BUY投稿から意図せず大量に
    Issueを起票してしまわないよう、Issue起票は行わない。"""
    raw = (os.environ.get("LOG_LOOKBACK_DAYS") or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def main():
    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    github_token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not bot_token:
        print("[ERROR] 環境変数 DISCORD_BOT_TOKEN が設定されていません。")
        sys.exit(1)
    issue_count = 0
    override = _lookback_override()
    if override is None and (not github_token or not repo):
        print("[ERROR] GITHUB_TOKEN / GITHUB_REPOSITORY が設定されていません。"
              "(通常はGitHub Actions実行時に自動設定されます)")
        sys.exit(1)
    forum_days = override or FORUM_LOOKBACK_DAYS
    text_days = override or TEXT_LOOKBACK_DAYS
    file_issues = override is None
    if override:
        print(f"[INFO] バックフィルモード: 直近{override}日を再生成(Issue起票なし)")

    for channel in CHANNELS:
        channel_id = os.environ.get(channel["env_id"])
        if not channel_id:
            print(f"[WARN] {channel['env_id']} が未設定のため #{channel['label']} をスキップします。")
            continue

        is_forum = channel.get("type") == "forum"
        print(f"=== #{channel['label']} の{'投稿(スレッド)' if is_forum else 'メッセージ'}を取得します ===")

        if is_forum:
            today = datetime.datetime.now(JST).date()
            start = today - datetime.timedelta(days=forum_days - 1)
            try:
                by_date = fetch_forum_threads_by_date(channel_id, bot_token, start, today)
            except Exception as err:  # noqa: BLE001
                print(f"[WARN] #{channel['label']} の取得に失敗したためスキップします: {err}")
                continue
            total_threads = sum(len(v) for v in by_date.values())
            print(f"[INFO] 直近{forum_days}日: {total_threads}件のスレッド(投稿)を取得しました。")
            # 直近7日を毎回すべて再生成する(冪等)。実行が日付をまたいでも、後から
            # 書かれた過去日のスレッドでも取りこぼさない。
            for offset in range(forum_days):
                d = start + datetime.timedelta(days=offset)
                entries = by_date.get(d, [])
                markdown = build_forum_markdown(entries, d.strftime("%Y-%m-%d"), channel["label"])
                save_markdown(channel["log_dir"], d.strftime("%Y-%m-%d"), markdown)
                # 日記(フォーラム)は公開リポジトリの Issue にしない(2026-09-26 プライバシー対応)
            continue

        today = datetime.datetime.now(JST).date()
        start = today - datetime.timedelta(days=text_days - 1)
        try:
            messages = fetch_messages_since(channel_id, bot_token, start)
        except Exception as err:  # noqa: BLE001
            # 権限不足(403)やチャンネルID誤りなど、このチャンネル固有の問題で
            # ジョブ全体(後続の日刊/週刊レポート生成)を止めないよう、警告に留めて次へ進む。
            print(f"[WARN] #{channel['label']} の取得に失敗したためスキップします: {err}")
            continue
        print(f"[INFO] 直近{text_days}日: {len(messages)}件のメッセージを取得しました。")
        # 直近N日を毎回すべて再生成する(冪等)。実行が日付をまたいでも取りこぼさない。
        for offset in range(text_days):
            d = start + datetime.timedelta(days=offset)
            day_msgs = [m for m in messages if message_date_jst(m) == d]
            save_markdown(channel["log_dir"], d.strftime("%Y-%m-%d"),
                          build_markdown(day_msgs, d.strftime("%Y-%m-%d"), channel["label"]))
        for msg in (messages if file_issues else []):
            if maybe_create_issue_for_message(msg, channel["label"], repo, github_token):
                issue_count += 1

    print(f"=== 完了: Issue新規作成 {issue_count}件 ===")


if __name__ == "__main__":
    main()
