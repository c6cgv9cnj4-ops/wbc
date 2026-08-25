# -*- coding: utf-8 -*-
"""
日本人選手の怪我・離脱・復帰アラート配信 (#webhook_sports_culture 向け)

Yahoo!ニュース スポーツカテゴリのRSS(実際にRSSが配信されていることを確認済み:
https://news.yahoo.co.jp/rss/categories/sports.xml)を巡回し、「怪我」「離脱」
「登録抹消」「故障」「手術」「欠場」等のキーワードにヒットした記事だけを
Geminiに渡し、選手名・所属・怪我部位・全治(分かる場合)を構造化して抽出、
赤色のEmbedカードとして配信する。

構造化APIではなくニュース記事からの抽出のため、Gemini側の判定に自信が
持てない場合は「不明」のまま出力し、存在しない情報を推測で埋めない。

環境変数:
  DISCORD_WEBHOOK_SPORTS_CULTURE (必須)
  GEMINI_API_KEY (必須)
"""
import datetime
import json
import os
import re
import sys

import feedparser
import requests

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "injury_alerts_seen.json")
STATE_RETENTION_DAYS = 14
REQUEST_TIMEOUT = 15
GEMINI_MODEL_NAME = "gemini-3.6-flash"

YAHOO_SPORTS_RSS = "https://news.yahoo.co.jp/rss/categories/sports.xml"
INJURY_KEYWORDS = ["怪我", "けが", "離脱", "登録抹消", "故障", "手術", "欠場", "戦線離脱", "全治"]

COLOR_INJURY = 0xE53E3E


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


def fetch_injury_candidates(state):
    try:
        resp = requests.get(YAHOO_SPORTS_RSS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Yahoo!ニュース スポーツRSSの取得に失敗しました: {err}")
        return []

    feed = feedparser.parse(resp.content)
    seen = state.get("seen_urls", {})
    candidates = []
    for entry in feed.entries:
        url = entry.get("link")
        title = entry.get("title", "")
        if not url or url in seen:
            continue
        if any(kw in title for kw in INJURY_KEYWORDS):
            candidates.append({"title": title, "url": url})
    return candidates


def extract_injury_info(client, candidates):
    """記事タイトルから、選手名・所属・怪我部位・全治(分かる場合)を構造化する。
    確信が持てない項目はnullのまま返すようGeminiに指示し、推測で埋めない。
    """
    if not candidates:
        return []

    titles_text = "\n".join(f"{i}. {c['title']}" for i, c in enumerate(candidates))
    prompt = f"""以下は日本のスポーツニュースの見出し一覧です。それぞれについて、
「選手の怪我・離脱・故障・登録抹消・手術・欠場」に関する実質的な内容かどうかを
判定してください(単なる「絶好調」「怪我から復帰して活躍」のようなポジティブな
内容や、怪我と無関係な見出しは除外してください)。

該当するものだけ、以下の形式のJSON配列で出力してください:
[{{"index": 0, "player": "選手名", "team": "所属チーム(不明ならnull)",
   "body_part": "怪我の部位(不明ならnull)", "recovery": "全治期間(不明ならnull)"}}]

見出し一覧:
{titles_text}

タイトルに明記されていない情報は絶対に推測せず、必ずnullにしてください。
該当する見出しが無ければ空配列[]を返してください。説明文は不要です。"""

    try:
        resp = client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt)
        text = resp.text.strip()
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(text)
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] Gemini構造化抽出に失敗したため、このバッチは0件扱いにします: {err}")
        return []

    results = []
    for item in parsed:
        idx = item.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
            continue
        results.append({
            "player": item.get("player") or "不明",
            "team": item.get("team") or "不明",
            "body_part": item.get("body_part") or "不明",
            "recovery": item.get("recovery") or "不明",
            "title": candidates[idx]["title"],
            "url": candidates[idx]["url"],
        })
    return results


def build_injury_embeds(injuries):
    embeds = []
    for inj in injuries:
        embeds.append({
            "title": f"🚨 {inj['player']}選手 負傷情報",
            "description": (
                f"**所属**: {inj['team']}\n"
                f"**部位**: {inj['body_part']}\n"
                f"**全治**: {inj['recovery']}\n\n"
                f"[{inj['title']}]({inj['url']})"
            ),
            "color": COLOR_INJURY,
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
    api_key = os.environ.get("GEMINI_API_KEY")
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_SPORTS_CULTURE が設定されていません。")
        sys.exit(1)
    if not api_key:
        print("[ERROR] 環境変数 GEMINI_API_KEY が設定されていません。")
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=api_key)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    state = load_seen_state()
    state = prune_old_entries(state, now)

    candidates = fetch_injury_candidates(state)
    print(f"=== キーワードヒット候補: {len(candidates)}件 ===")

    injuries = extract_injury_info(client, candidates) if candidates else []
    print(f"=== Gemini構造化後の負傷情報: {len(injuries)}件 ===")

    seen = state.setdefault("seen_urls", {})
    for c in candidates:
        seen[c["url"]] = now.isoformat()

    had_error = False
    embeds = build_injury_embeds(injuries)
    if embeds:
        if not send_embeds_to_discord(webhook, embeds):
            had_error = True
    else:
        print("[INFO] 配信対象の負傷情報はありませんでした。")

    save_seen_state(state)

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
