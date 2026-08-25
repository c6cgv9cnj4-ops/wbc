# -*- coding: utf-8 -*-
"""
「#推し」チャンネル向け キーワード追跡ニュース配信スクリプト

指定したキーワード(推し)ごとにGoogle News RSSで最新ニュースを取得し、
新着のみをDiscordへ配信する。キーワード一覧は OSHI_KEYWORDS (このファイル内、
下記参照)で一元管理し、今後の追加・削除はここを編集するだけで完結する。

環境変数:
  DISCORD_WEBHOOK_OSHI (必須)
"""
import datetime
import json
import os
import sys
import time
import urllib.parse

import feedparser
import requests

# ============================================================
# 追跡キーワード設定(ここに追加・削除するだけで対象を変更できる)
# ============================================================
OSHI_KEYWORDS = [
    "U2",
    "サカナクション",
    "伊藤若冲",
]

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "oshi_news_seen.json")
STATE_RETENTION_DAYS = 30
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
ITEMS_PER_KEYWORD = 5  # 1キーワードあたり取得する最新記事数


def load_seen_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_seen_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def prune_old_entries(state, now):
    cutoff = now - datetime.timedelta(days=STATE_RETENTION_DAYS)
    seen = state.get("seen_urls", {})
    pruned = {}
    for url, iso_ts in seen.items():
        try:
            ts = datetime.datetime.fromisoformat(iso_ts)
        except ValueError:
            continue
        if ts >= cutoff:
            pruned[url] = iso_ts
    state["seen_urls"] = pruned
    return state


def fetch_keyword_news(keyword, retries=2):
    """指定キーワードのGoogle News RSSを取得する。
    短時間の連続リクエストによるレート制限(503)を避けるため、
    リクエスト間隔を空け、失敗時は1回リトライする
    (fetch_culture_news.pyで実際に503が発生した実績を踏まえた対策)。
    """
    q = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"
    time.sleep(2.0)

    resp = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] Google News RSS取得に失敗(試行{attempt + 1}/{retries})({keyword}): {err}")
            resp = None
            time.sleep(5.0)

    if resp is None:
        print(f"[ERROR] Google News RSS取得に失敗しました({keyword})")
        return []

    feed = feedparser.parse(resp.content)
    return [{"title": e.title, "url": e.link} for e in feed.entries[:ITEMS_PER_KEYWORD]]


def build_oshi_message(state, now):
    """新着が1件も無ければNoneを返す。"""
    seen = state.setdefault("seen_urls", {})
    sections = []
    has_any_new = False

    for keyword in OSHI_KEYWORDS:
        items = fetch_keyword_news(keyword)
        new_items = [item for item in items if item["url"] not in seen]
        for item in new_items:
            seen[item["url"]] = now.isoformat()

        if new_items:
            has_any_new = True
            lines = [f"## 🌟 {keyword}"]
            for item in new_items:
                lines.append(f"- [{item['title']}](<{item['url']}>)")
            sections.append("\n".join(lines))

    if not has_any_new:
        return None

    now_jst = now.strftime("%Y-%m-%d %H:%M")
    header = f"# 🌟 推し新着ニュース ({now_jst} JST時点)"
    return "\n\n".join([header] + sections)


def chunk_message(text, limit=1900):
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_to_discord(webhook_url, message):
    if not webhook_url:
        print("[ERROR] Webhook URLが設定されていないため送信をスキップします。")
        return False
    ok = True
    for chunk in chunk_message(message):
        try:
            resp = requests.post(webhook_url, json={"content": chunk}, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 300:
                print(f"[ERROR] Discord送信に失敗しました(HTTP {resp.status_code}): {resp.text[:300]}")
                ok = False
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] Discord送信中に例外が発生しました: {err}")
            ok = False
    return ok


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_OSHI")
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_OSHI が設定されていません。")
        sys.exit(1)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    state = load_seen_state()
    state = prune_old_entries(state, now)

    message = build_oshi_message(state, now)
    had_error = False
    if message:
        print("=== 推しニュースメッセージ(新着あり) ===")
        print(message)
        if not send_to_discord(webhook, message):
            had_error = True
    else:
        print("[INFO] 新着の推しニュースはありませんでした。")

    save_seen_state(state)

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
