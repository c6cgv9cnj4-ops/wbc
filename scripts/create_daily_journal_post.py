# -*- coding: utf-8 -*-
"""
#モーニングジャーナル(Discord フォーラム)に、当日の投稿「YYYY/MM/DD｜モーニングジャーナル」を
Bot が作成し、本文にテンプレートを入れる(2026-09-27)。細川さんはこの投稿への「返信」として日記を書く。

- 同じ日付の投稿が既にあれば作らない(手動の「2026/09/27」形式も同じ日付として扱う)
- Bot が書いたテンプレート本文は、journal_review.py の取得で除外される(author.bot)ため、
  週次観測・シート同期には細川さんの返信だけが入る
- 日記本文は読まない・保存しない。ログに出すのはタイトルと結果だけ

使い方:
  # dry-run(既定): 投稿はしない。認証情報があれば既存投稿を確認して「作る/作らない」まで判定
  python scripts/create_daily_journal_post.py [--env-file PATH]
  # 本番: 当日の投稿を作成
  python scripts/create_daily_journal_post.py --apply --env-file PATH

認証情報(値はログに出さない):
  DISCORD_BOT_TOKEN                   (必須) フォーラムに投稿できる Bot のトークン
  DISCORD_CHANNEL_ID_MORNING_JOURNAL  (任意) 未設定なら DISCORD_GUILD_ID 内の
                                      フォーラム「モーニングジャーナル」を名前で1件に特定する
  --env-file で KEY=VALUE 形式のファイル(例: 04_Stocks/.env)から読み込める。
  既に環境変数にある値は上書きしない。
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journal_review import DISCORD_API_BASE, JST, _discord_get, _list_forum_threads, parse_thread_date  # noqa: E402

FORUM_NAME = "モーニングジャーナル"
FORUM_TYPES = {15, 16}

JOURNAL_POST_TEMPLATE = """# 今日のモーニングジャーナル

思いつくまま自由に書いてください。
短文・箇条書きでOKです。
全部の項目を書く必要はありません。

### 昨日・今日の出来事
印象に残ったこと、あったこと。

### やったこと・やること
昨日やったこと、今日やりたいこと。

### 気づき・発見
何か気づいたこと、考えたこと。

### 今の気分・感覚
嬉しい、楽しい、疲れた、面倒、気になるなど。

### 最近気になっていること
何度も考えていること、興味を持っていること。

### 未解決・気になっていること
まだ答えが出ていないこと、後で考えたいこと。

### 今日につなげたいこと
今日やってみたいこと、覚えておきたいこと。

---

※全部埋めなくてOK
※一言だけでもOK
※文章をきれいにする必要はありません
※思いついたことをそのまま書いてください"""


def post_title(day: datetime.date) -> str:
    return f"{day.strftime('%Y/%m/%d')}｜{FORUM_NAME}"


def load_env_file(path: str) -> None:
    """KEY=VALUE の行を環境変数へ(既存の値は上書きしない)。値は表示しない。"""
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def resolve_forum_id(token: str) -> str | None:
    cid = os.environ.get("DISCORD_CHANNEL_ID_MORNING_JOURNAL", "").strip()
    if cid:
        return cid
    gid = os.environ.get("DISCORD_GUILD_ID", "").strip()
    if not gid:
        return None
    forums = [c for c in _discord_get(f"/guilds/{gid}/channels", token)
              if c.get("type") in FORUM_TYPES and c.get("name") == FORUM_NAME]
    if len(forums) != 1:
        print(f"[ERROR] フォーラム「{FORUM_NAME}」を1件に特定できません(候補 {len(forums)}件)")
        return None
    return forums[0]["id"]


def existing_post_for(day: datetime.date, threads: list[dict]) -> dict | None:
    """同じ日付(スレッド名の日付)の投稿があれば返す。手動の「YYYY/MM/DD」も対象。"""
    for t in threads:
        if parse_thread_date(t.get("name", "")) == day:
            return t
    return None


def create_post(token: str, forum_id: str, day: datetime.date) -> dict:
    resp = requests.post(
        f"{DISCORD_API_BASE}/channels/{forum_id}/threads",
        headers={"Authorization": f"Bot {token}", "User-Agent": "wbc-daily-journal-post/1.0"},
        json={"name": post_title(day),
              "message": {"content": JOURNAL_POST_TEMPLATE, "allowed_mentions": {"parse": []}}},
        timeout=20,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"投稿の作成に失敗しました(HTTP {resp.status_code})")
    return resp.json()


def main() -> int:
    ap = argparse.ArgumentParser(description="当日のモーニングジャーナル投稿を作成")
    ap.add_argument("--apply", action="store_true", help="実際に投稿する(既定は dry-run)")
    ap.add_argument("--env-file", default="", help="認証情報を読む KEY=VALUE ファイル")
    ap.add_argument("--date", default="", help="対象日 YYYY-MM-DD(既定は今日 JST。検証用)")
    args = ap.parse_args()

    day = (datetime.date.fromisoformat(args.date) if args.date
           else datetime.datetime.now(JST).date())
    title = post_title(day)
    assert len(title) <= 100 and len(JOURNAL_POST_TEMPLATE) <= 2000

    if args.env_file:
        load_env_file(args.env_file)
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

    if not args.apply:
        print(f"[DRY-RUN] 投稿はしません。作成予定のタイトル: {title}\n--- 本文 ---\n{JOURNAL_POST_TEMPLATE}\n---")
        if not token:
            print("[DRY-RUN] 認証情報が無いため、既存投稿の確認は省略しました")
            return 0

    if not token:
        print("[ERROR] DISCORD_BOT_TOKEN がありません(--env-file か環境変数で指定)")
        return 1
    forum_id = resolve_forum_id(token)
    if not forum_id:
        print("[ERROR] 対象フォーラムを特定できません。中止します")
        return 1
    meta = _discord_get(f"/channels/{forum_id}", token)
    if meta.get("type") not in FORUM_TYPES:
        print(f"[ERROR] フォーラムではありません(type={meta.get('type')})。中止します")
        return 1
    _, threads = _list_forum_threads(forum_id, token)
    found = existing_post_for(day, threads)
    if found:
        print(f"[SKIP] {day} の投稿は既にあります: {found.get('name')}(二重作成しません)")
        return 0
    if not args.apply:
        print(f"[DRY-RUN] #{meta.get('name')} に「{title}」はまだありません → --apply で作成されます")
        return 0
    created = create_post(token, forum_id, day)
    print(f"[OK] 作成しました: #{meta.get('name')} / {created.get('name')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
