# -*- coding: utf-8 -*-
"""
バドミントン注目選手の試合結果配信(シンプル版) (#webhook_sports_culture 向け)

情報源: スポ速(sposoku.com) の大会別記事(実データで動作確認済み)。
1試合ずつ「〇選手名　2－0　×対戦相手名(国名)」の形式で1行に記載されている
ことを、実際に取得したHTMLから正規表現で確認済み。

対象は以下の指定選手を含む試合のみ(シングルスは完全一致、ダブルスは
記事側が姓のみで表記されるため、指定選手名にその表記が含まれるかで判定)。

【スコープについて】
このソースには、世界ランキング・大会グレード(Super 1000等)・ゲームごとの
得点(21-18等)・高校生選手の年齢は掲載されていない。無理に他ソース
(BWF公式は403でブロック済み)を混ぜて精度を落とすより、まずは「選手名・
所属国・対戦結果・セット数」のシンプルな実データ配信を確実に動かすことを
優先した(ユーザーとの合意による設計判断)。

環境変数:
  DISCORD_WEBHOOK_SPORTS_CULTURE (必須)
"""
import datetime
import json
import os
import re
import sys

import requests
from bs4 import BeautifulSoup

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "badminton_alerts_seen.json")
STATE_RETENTION_DAYS = 30
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TAG_PAGE_URL = "https://www.sposoku.com/tag/badminton/"

# 指定注目選手(この文字列が記事側の選手名表記に含まれる場合にマッチとする。
# ダブルスは記事側が姓のみで表記されるため、部分一致で判定する)
TARGET_PLAYERS = [
    "奥原希望", "渡辺勇大", "田口真彩", "志田千陽", "東野有紗",
    "中出", "高橋", "福島由紀", "松山奈未", "松友美佐紀",
]

MATCH_LINE_RE = re.compile(r"^([〇×])(.+?)　(\d+)[－\-](\d+)　([〇×])(.+?)(?:\(([^)]+)\))?$")

COLOR_BADMINTON = 0x38A169


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
    seen = state.get("seen_matches", {})
    pruned = {}
    for key, iso_ts in seen.items():
        try:
            ts = datetime.datetime.fromisoformat(iso_ts)
        except ValueError:
            continue
        if ts >= cutoff:
            pruned[key] = iso_ts
    state["seen_matches"] = pruned
    return state


def is_target_player(name):
    return any(target in name or name in target for target in TARGET_PLAYERS)


def fetch_tournament_article_urls():
    try:
        resp = requests.get(TAG_PAGE_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] バドミントンタグページの取得に失敗しました: {err}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    urls = set()
    for a in soup.select("article a[href]"):
        href = a.get("href", "")
        if href.startswith("https://www.sposoku.com/") and href.count("/") == 4:
            urls.add(href)
    return list(urls)


def fetch_matches_from_article(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] 大会記事の取得に失敗しました({url}): {err}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    tournament_title = title_tag.get_text(strip=True) if title_tag else url

    text = soup.get_text("\n", strip=True)
    matches = []
    for line in text.split("\n"):
        m = MATCH_LINE_RE.match(line)
        if not m:
            continue
        w_mark, w_name, w_set, l_set, l_mark, l_name, opponent_country = m.groups()
        if is_target_player(w_name) or is_target_player(l_name):
            matches.append({
                "tournament": tournament_title,
                "winner": w_name if w_mark == "〇" else l_name,
                "loser": l_name if w_mark == "〇" else w_name,
                "score": f"{max(w_set, l_set)}-{min(w_set, l_set)}",
                "country": opponent_country or "",
                "url": url,
                "raw_line": line,
            })
    return matches


def build_badminton_embeds(matches):
    embeds = []
    for m in matches:
        embeds.append({
            "title": f"🏸 {m['tournament']}",
            "description": f"**{m['winner']}** {m['score']} {m['loser']}" +
                            (f"({m['country']})" if m["country"] else "") +
                            f"\n[詳細を見る]({m['url']})",
            "color": COLOR_BADMINTON,
        })
    return embeds


def send_embeds_to_discord(webhook_url, embeds, batch_size=10):
    if not webhook_url:
        print("[ERROR] DISCORD_WEBHOOK_SPORTS_CULTURE が設定されていないため送信をスキップします。")
        return False
    ok = True
    for i in range(0, len(embeds), batch_size):
        batch = embeds[i:i + batch_size]
        try:
            resp = requests.post(webhook_url, json={"embeds": batch}, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 300:
                print(f"[ERROR] Discord送信に失敗しました(HTTP {resp.status_code}): {resp.text[:300]}")
                ok = False
            else:
                print(f"[OK] Discord送信成功(HTTP {resp.status_code}, {len(batch)}件)")
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] Discord送信中に例外が発生しました: {err}")
            ok = False
    return ok


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_SPORTS_CULTURE")
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_SPORTS_CULTURE が設定されていません。")
        sys.exit(1)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    state = load_seen_state()
    state = prune_old_entries(state, now)
    seen = state.setdefault("seen_matches", {})

    article_urls = fetch_tournament_article_urls()
    print(f"=== 大会記事: {len(article_urls)}件を巡回します ===")

    new_matches = []
    for url in article_urls:
        for match in fetch_matches_from_article(url):
            key = f"{match['url']}::{match['raw_line']}"
            if key in seen:
                continue
            new_matches.append(match)
            seen[key] = now.isoformat()

    print(f"=== 指定選手の新着試合結果: {len(new_matches)}件 ===")
    for m in new_matches:
        print(f"  {m['tournament']}: {m['winner']} {m['score']} {m['loser']}")

    had_error = False
    embeds = build_badminton_embeds(new_matches)
    if embeds:
        if not send_embeds_to_discord(webhook, embeds):
            had_error = True
    else:
        print("[INFO] 配信対象の新着試合結果はありませんでした。")

    save_seen_state(state)

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
