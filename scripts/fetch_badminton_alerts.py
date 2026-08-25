# -*- coding: utf-8 -*-
"""
バドミントン注目選手の試合結果配信(シンプル版) (#webhook_sports_culture 向け)

情報源: スポ速(sposoku.com) の大会別記事(実データで動作確認済み)。
1試合ずつ「〇選手名　2－0　×対戦相手名(国名)」の形式で1行に記載されている
ことを、実際に取得したHTMLから正規表現で確認済み。

対象は以下の指定選手を含む試合のみ(シングルスは完全一致、ダブルスは
記事側が姓のみで表記されるため、指定選手名にその表記が含まれるかで判定)。

【実装済み】種目(男子シングルス等)・ラウンド(1回戦〜決勝)は、記事内の
見出し行(「男子シングルス」「■1回戦」等)を実際に確認し、順に走査しながら
各試合結果に紐づける形で実装した。

【推定表示】試合日は、記事冒頭の日程表(例:「1月6日(火)｜1回戦 10:00～」)
からラウンド名で対応する日付を逆引きしている。これは「その大会の予定表」
からの推定であり、順延等があった場合の実際の消化日とは異なる可能性がある
(そのため常に「推定」であることを踏まえた表示にしている)。

【未実装・既知の制約】世界ランキング(BWF)・大会グレード(Super 1000等)・
ゲームごとの得点(21-18等)は、sposoku.com側に一切掲載されておらず、BWF公式
サイト(bwfbadminton.com)も403で直接アクセスできないため実装していない
(実際に再確認済み。状況が変わり次第、再調査する)。

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

# 指定注目選手(現役選手に確定。引退選手は含めない)。
# この文字列が記事側の選手名表記に含まれる場合にマッチとする。
TARGET_PLAYERS = [
    "奥原希望",
    "渡辺勇大",
    "田口真彩",
    "志田千陽",
    "東野有紗", "五十嵐有紗",  # 東野有紗(旧姓:五十嵐有紗)。ダブルスでは旧姓表記の場合があるため両方登録
    "中西貴映",  # 岩永/中西ペア
    "高橋明日香",  # ※高橋沙也加(引退)とは別人。フルネームで区別する
    "福島由紀",  # 松本/福島ペア等
    "松山奈未",
    "松友美佐紀",  # 混合ダブルス等の出場も含めて捕捉
]

# 1番目の選手にも国名が付くケース(例: 日本人同士の対戦「熊谷・西(日本) 2-0 霜上・野村(日本)」)
# が実データで見つかったため、両方の選手名に(...)をオプションで許容する。
MATCH_LINE_RE = re.compile(
    r"^([〇×])(.+?)(?:\(([^)]+)\))?　(\d+)[－\-](\d+)　([〇×])(.+?)(?:\(([^)]+)\))?$"
)

# 種目見出し(記事内で単独行として出現する。実データで確認済み)
EVENT_HEADINGS = [
    "男子シングルス", "女子シングルス", "男子ダブルス", "女子ダブルス",
    "混合ダブルス", "混合ミックスダブルス",
]

# ラウンド見出し(「■」始まりの単独行。実データで確認済み)
ROUND_HEADING_RE = re.compile(r"^■(.+?)(?:\(.*\))?$")
# 「■最終成績」は個々の試合結果ではなくサマリー見出しなので、ラウンドとしては扱わない
NON_ROUND_HEADINGS = {"最終成績"}

# 大会日程表の行(例: "1月6日(火)｜1回戦 10:00～")からラウンド→日付を逆引きするための正規表現。
# ラウンド名自体に数字が含まれる(「1回戦」等)ため、数字除外はせず、｜の直後の
# 最初のトークン(次の空白まで)をそのままラウンド名として扱う。
SCHEDULE_LINE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日\([月火水木金土日]\)[｜|]\s*([^\s　]+)")

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
    # ダブルスは記事側が「姓・姓」の形式(例: 岩永・中西)で表記されるため、
    # 「・」で分割してペアの片方だけでも指定選手と一致すれば検出する。
    parts = re.split("[・･]", name) + [name]
    return any(
        target in part or part in target
        for part in parts
        for target in TARGET_PLAYERS
    )


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


def build_round_to_date_map(lines):
    """記事冒頭の日程表(例: "1月6日(火)｜1回戦 10:00～")から、
    ラウンド名(例: "1回戦")→日付("1/6")の対応表を作る。
    同じラウンドが複数日にまたがる場合(例: 1回戦が2日間)は、最初に
    出現した日付(=そのラウンドの開始日)を採用する。
    見つからないラウンドは単に対応が無い(=推定表示できない)ものとして扱う。
    """
    mapping = {}
    for line in lines:
        m = SCHEDULE_LINE_RE.search(line)
        if m:
            month, day, round_label = m.groups()
            mapping.setdefault(round_label, f"{int(month)}/{int(day)}")
    return mapping


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

    lines = soup.get_text("\n", strip=True).split("\n")
    round_date_map = build_round_to_date_map(lines)

    matches = []
    current_event = None
    current_round = None

    for line in lines:
        if line in EVENT_HEADINGS:
            current_event = line
            current_round = None  # 種目が変わったらラウンドをリセット
            continue

        round_m = ROUND_HEADING_RE.match(line)
        if round_m:
            round_label = round_m.group(1)
            if round_label not in NON_ROUND_HEADINGS:
                current_round = round_label
            else:
                current_round = None
            continue

        m = MATCH_LINE_RE.match(line)
        if not m:
            continue
        w_mark, w_name, w_country, w_set, l_set, l_mark, l_name, l_country = m.groups()
        if not (is_target_player(w_name) or is_target_player(l_name)):
            continue

        # 記事の表記ルール: 通常は1番目(w_name)に国名は付かず(日本選手側)、
        # 2番目(l_name)にだけ国名が付くが、日本人同士の対戦では両方に
        # 「(日本)」が付くケースも実データで確認したため、両方を個別に
        # キャプチャして、それぞれの選手名にそのまま紐づける
        # (以前は2番目の国名を常に敗者側に付けており、日本選手が負けた
        # 試合では対戦相手の国名が誤って日本選手の方に表示されるバグがあった)。
        is_w_winner = w_mark == "〇"
        matches.append({
            "tournament": tournament_title,
            "event": current_event,
            "round": current_round,
            "date_estimate": round_date_map.get(current_round) if current_round else None,
            "winner": w_name if is_w_winner else l_name,
            "winner_country": (w_country or "") if is_w_winner else (l_country or ""),
            "loser": l_name if is_w_winner else w_name,
            "loser_country": (l_country or "") if is_w_winner else (w_country or ""),
            "score": f"{max(w_set, l_set)}-{min(w_set, l_set)}",
            "url": url,
            "raw_line": line,
        })
    return matches


def build_badminton_embeds(matches):
    embeds = []
    for m in matches:
        winner_display = m["winner"] + (f"({m['winner_country']})" if m["winner_country"] else "")
        loser_display = m["loser"] + (f"({m['loser_country']})" if m["loser_country"] else "")

        # 記事タイトル(例: "【バドミントンマレーシアオープン2026】日本代表の試合結果速報、組み合わせ")
        # から、既に【】で囲まれている大会名部分だけを取り出す(無いければ記事タイトル全体を使う)。
        tournament_m = re.match(r"【(.+?)】", m["tournament"])
        tournament_short = tournament_m.group(1) if tournament_m else m["tournament"]

        title_parts = [f"🏸 【{tournament_short}】"]
        if m["event"]:
            title_parts.append(m["event"])
        if m["round"]:
            title_parts.append(m["round"])
        title = " ".join(title_parts)
        if m["date_estimate"]:
            title += f"（{m['date_estimate']}・推定）"

        embeds.append({
            "title": title[:256],  # Discord Embedのtitle上限
            "description": f"**{winner_display}** {m['score']} {loser_display}" +
                            f"\n[詳細を見る](<{m['url']}>)",
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
        print(f"  [{m['event']}/{m['round']}] {m['tournament']}: {m['winner']} {m['score']} {m['loser']}")

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
