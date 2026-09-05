# -*- coding: utf-8 -*-
"""
バドミントン速報配信(#webhook_sports_culture 向け)

情報源は2系統(2026-09-06、バド×スピを追加して二本立てに変更):

0. 配信対象・除外方針(2026-09-06、細川さんの指定で確定):
   【欲しい】インターハイ(高校総体)・全国高校選抜・全中・インカレ・全日本総合・
   全日本ジュニア等の全国規模大会、日本代表(BIRD JAPAN)の国際大会速報。
   【除外】S/Jリーグ(実業団リーグ)の全試合・速報・告知は完全除外する。
   タイトル・本文のいずれかに「S/Jリーグ」「SJリーグ」「実業団」を含む記事は
   is_league_excluded() で判定しスキップする(sposoku.com側は記事タイトル、
   badspi.jp側はRSSのタイトル+summaryの両方をチェック)。

1. スポ速(sposoku.com) の大会別記事から (a) 指定選手の試合結果、
   (b) 全国規模大会の種目別結果サマリー(優勝/準優勝/第三位) を抽出する。
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
   サイト(bwfbadminton.com)も403で直接アクセスできないため実装していない。
   【既知の限界・2026-09-06確認】sposoku.comは学生スポーツ(高校総体・中体連・
   国スポ等)がメインのメディアで、BWFワールドツアー等の国際大会は不定期にしか
   扱われない。実際に2026-09-06時点で開催中だった中国マスターズ2026
   (Super750、9/1〜9/6)の記事が掲載されておらず、直近の国際大会速報が
   拾えなくなっていたことを確認した。これが下記2.を追加した理由。
   (b)全国大会結果サマリーについて: TARGET_PLAYERS(代表選手個人名)による
   マッチングでは学生選手が指定リストに含まれず一切拾えなかったため、
   選手名を問わず「■(種目名)」の直後に「優勝：」「準優勝：」「第三位：」
   (団体戦は「第四位：」も)が続く結果サマリー行のブロックを抽出する方式を
   追加した(extract_national_summary_results)。全試合(1大会あたり数十〜
   150件超になることを実データで確認)ではなく種目ごと数行のサマリーのみに
   絞ることで、Discordが荒れない粒度にしている。

2. バド×スピ(BADMINTON SPIRIT、badspi.jp)の新着記事フィードをそのまま速報
   として配信(2026-09-06追加)。日本代表専門メディアで、国際大会の日次速報・
   世界ランキング・国内大会情報を高頻度(実データで日次更新を確認)にカバー
   しており、1.の空白を補う。記事単位でタイトル+リンクをそのままEmbed配信
   する(1.のような選手名マッチング・スコア抽出は行わない、シンプルな
   新着通知)。

環境変数:
  DISCORD_WEBHOOK_SPORTS_CULTURE (必須)
"""
import datetime
import json
import os
import re
import sys

import feedparser
import requests
from bs4 import BeautifulSoup

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "badminton_alerts_seen.json")
STATE_RETENTION_DAYS = 30
REQUEST_TIMEOUT = 15
BADSPI_RSS_URL = "https://www.badspi.jp/feed/"
BADSPI_ITEM_LIMIT = 20
COLOR_BADSPI = 0x2B6CB0
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


def _prune_dict(seen, cutoff):
    pruned = {}
    for key, iso_ts in seen.items():
        try:
            ts = datetime.datetime.fromisoformat(iso_ts)
        except ValueError:
            continue
        if ts >= cutoff:
            pruned[key] = iso_ts
    return pruned


def prune_old_entries(state, now):
    cutoff = now - datetime.timedelta(days=STATE_RETENTION_DAYS)
    state["seen_matches"] = _prune_dict(state.get("seen_matches", {}), cutoff)
    state["seen_badspi_urls"] = _prune_dict(state.get("seen_badspi_urls", {}), cutoff)
    state["seen_national_summaries"] = _prune_dict(state.get("seen_national_summaries", {}), cutoff)
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


LEAGUE_EXCLUDE_KEYWORDS = ["S/Jリーグ", "SJリーグ", "実業団"]


def is_league_excluded(text):
    """S/Jリーグ(実業団リーグ)関連の記事・速報を除外するための判定
    (2026-09-06、細川さんの指定により追加)。"""
    return any(kw in text for kw in LEAGUE_EXCLUDE_KEYWORDS)


def fetch_article_content(url):
    """大会記事を1回だけ取得し、(tournament_title, lines) を返す。
    取得失敗、またはタイトルがS/Jリーグ(実業団)関連の場合は (None, None)。
    選手名マッチング抽出・全国大会結果サマリー抽出の両方がこの結果を
    共有することで、記事ごとのHTTPリクエストを1回に抑える。
    """
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] 大会記事の取得に失敗しました({url}): {err}")
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    tournament_title = title_tag.get_text(strip=True) if title_tag else url

    if is_league_excluded(tournament_title):
        print(f"[INFO] S/Jリーグ(実業団)関連記事のため除外します: {tournament_title}")
        return None, None

    lines = soup.get_text("\n", strip=True).split("\n")
    return tournament_title, lines


def extract_target_player_matches(url, tournament_title, lines):
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


# ============================================================
# 全国規模大会(インターハイ・全中・選抜・国スポ等)の種目別結果サマリー
# ============================================================
# sposoku.comの大会記事は、種目見出し「■(種目名)」の直後に「優勝：」
# 「準優勝：」「第三位：」(団体戦は「第四位：」も)が続く結果サマリー
# ブロックを持つ(記事冒頭に全種目分まとめて出る場合と、各種目のブラケット
# 末尾に「■最終成績」という見出しで個別に出る場合の両方を実データで確認済み)。
# TARGET_PLAYERS(代表選手個人名)によるマッチングでは、学生選手が指定
# リストに含まれないため一切拾えなかった。この関数は選手名を問わず、
# 「優勝/準優勝/第三位」という結果サマリー行のブロックだけを抽出することで、
# 全試合(1回戦から数えると大会あたり数十〜150件超)ではなく、種目ごと数行の
# サマリーのみを配信対象にする(2026-09-06追加)。
#
# 【既知の罠・実データで確認済み】各記事には今年度の結果に続けて「歴代優勝者
# アーカイブ」が同じ「■(見出し)+優勝/準優勝/第三位」形式で掲載されている。
# 区切り方が記事によって2パターンあることを確認した:
#   (a) 明示的な区切り行「過去大会結果」がある(例: interhigh-badminton)。
#       この行以降は無条件で走査を打ち切る。
#   (b) 見出しラベル自体が西暦4桁+「年」になっている(例: zenchu-badmintonの
#       「■2026年」「■2025年」...)。この場合は今年(now.year)以外の年度を
#       除外する。
# これらを無視すると、初回実行時などに何年分もの過去優勝者情報が「新着」
# として大量配信されてしまう。
ARCHIVE_SECTION_MARKER = "過去大会結果"
# 「2026年」「2025年結果」「2020年度結果」等、表記ゆれを許容するため
# 先頭一致(西暦4桁+「年」)のみで判定する(末尾は問わない)。
YEAR_ONLY_LABEL_RE = re.compile(r"^(\d{4})年")
FINAL_RESULT_HEADING_RE = re.compile(r"^■(.+)$")
FINAL_RESULT_LINE_RE = re.compile(r"^(優勝|準優勝|第三位|第四位)：(.+)$")


def extract_national_summary_results(lines, now):
    results = []
    i = 0
    while i < len(lines):
        if lines[i] == ARCHIVE_SECTION_MARKER:
            break  # (a) これ以降は歴代優勝者アーカイブなので走査を打ち切る

        heading_m = FINAL_RESULT_HEADING_RE.match(lines[i])
        if heading_m and i + 1 < len(lines) and FINAL_RESULT_LINE_RE.match(lines[i + 1]):
            label = heading_m.group(1)

            year_m = YEAR_ONLY_LABEL_RE.match(label)
            if year_m and int(year_m.group(1)) != now.year:
                # (b) 見出し自体が過去年度のアーカイブブロックなのでスキップ
                # (ただし本文行は消費して次のブロック探索へ進める)
                j = i + 1
                while j < len(lines) and FINAL_RESULT_LINE_RE.match(lines[j]):
                    j += 1
                i = j
                continue

            summary_lines = []
            j = i + 1
            while j < len(lines) and FINAL_RESULT_LINE_RE.match(lines[j]):
                summary_lines.append(lines[j])
                j += 1
            results.append({"label": label, "summary_lines": summary_lines})
            i = j
            continue
        i += 1
    return results


def build_badminton_embeds(matches, now):
    embeds = []
    for m in matches:
        winner_display = m["winner"] + (f"({m['winner_country']})" if m["winner_country"] else "")
        loser_display = m["loser"] + (f"({m['loser_country']})" if m["loser_country"] else "")

        tournament_short = extract_tournament_short_name(m["tournament"])

        title_parts = [f"🏸 【{tournament_short}】"]
        if m["event"]:
            title_parts.append(m["event"])
        if m["round"]:
            title_parts.append(m["round"])
        title = " ".join(title_parts)
        if m["date_estimate"]:
            title += f"（{m['date_estimate']}・推定）"
        else:
            # 大会日程表からラウンド名が逆引きできなかった場合(見出し表記の
            # ゆらぎ等)のフォールバック。試合日そのものではなく、この結果を
            # 検知・取得した日時であることが分かるように明記する。
            title += f"（取得: {now.strftime('%m/%d %H:%M')}）"

        embeds.append({
            "title": title[:256],  # Discord Embedのtitle上限
            "description": f"**{winner_display}** {m['score']} {loser_display}" +
                            f"\n[詳細を見る](<{m['url']}>)",
            "color": COLOR_BADMINTON,
        })
    return embeds


def extract_tournament_short_name(tournament_title):
    """記事タイトル(例: "【高校総体インターハイバドミントン2026】速報、結果、
    組み合わせ、日程、ライブ配信")から【】内の大会名部分だけを取り出す
    (無ければ記事タイトル全体を使う)。"""
    m = re.match(r"【(.+?)】", tournament_title)
    return m.group(1) if m else tournament_title


def build_national_summary_embeds(summaries, now):
    """全国規模大会(インターハイ・全中・選抜・国スポ等)の種目別結果サマリー
    をEmbed化する。"""
    embeds = []
    for s in summaries:
        tournament_short = extract_tournament_short_name(s["tournament"])
        label = s["label"] if s["label"] != "最終成績" else "最終成績"
        title = f"🏸 【{tournament_short}】{label}"
        embeds.append({
            "title": title[:256],
            "description": "\n".join(s["summary_lines"]) + f"\n[詳細を見る](<{s['url']}>)",
            "color": COLOR_BADMINTON,
            "footer": {"text": f"取得: {now.strftime('%m/%d %H:%M')}"},
        })
    return embeds


# ============================================================
# バド×スピ(badspi.jp)新着記事速報
# ============================================================

def fetch_badspi_articles(limit=BADSPI_ITEM_LIMIT):
    """バド×スピの新着記事フィードを取得する。取得失敗時は空リストを返し、
    sposoku.com側の処理には影響させない。"""
    try:
        resp = requests.get(
            BADSPI_RSS_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] バド×スピRSSの取得に失敗しました: {err}")
        return []

    feed = feedparser.parse(resp.content)
    items = []
    for entry in feed.entries[:limit]:
        if not entry.get("link") or not entry.get("title"):
            continue
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        # タイトル・本文(RSSのsummary)のどちらかにS/Jリーグ(実業団)関連の
        # キーワードが含まれる記事は除外する(2026-09-06、細川さんの指定)。
        if is_league_excluded(title) or is_league_excluded(summary):
            print(f"[INFO] S/Jリーグ(実業団)関連記事のため除外します: {title}")
            continue
        items.append({"title": title, "url": entry.get("link", "")})
    return items


def build_badspi_embeds(articles):
    """記事単位でタイトル+リンクをそのままEmbed化する(選手名マッチングは
    行わず、バド×スピが日本代表・国内大会関連の記事を書いた時点でそのまま
    通知する新着速報)。"""
    embeds = []
    for a in articles:
        embeds.append({
            "title": f"🏸 {a['title']}"[:256],
            "description": f"[記事を読む](<{a['url']}>)",
            "color": COLOR_BADSPI,
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
    seen_badspi = state.setdefault("seen_badspi_urls", {})
    seen_summaries = state.setdefault("seen_national_summaries", {})

    article_urls = fetch_tournament_article_urls()
    print(f"=== 大会記事: {len(article_urls)}件を巡回します ===")

    new_matches = []
    new_summaries = []
    for url in article_urls:
        tournament_title, lines = fetch_article_content(url)
        if tournament_title is None:
            continue  # 取得失敗、またはS/Jリーグ(実業団)関連記事のため除外済み

        for match in extract_target_player_matches(url, tournament_title, lines):
            key = f"{match['url']}::{match['raw_line']}"
            if key in seen:
                continue
            new_matches.append(match)
            seen[key] = now.isoformat()

        for summary in extract_national_summary_results(lines, now):
            # labelを含めないキーにする(「男子学校対抗」の冒頭サマリーと
            # 「最終成績」の詳細ブラケット末尾サマリーが同一内容で重複する
            # ことを実データで確認したため、内容一致で重複排除する)。
            key = f"{url}::{''.join(summary['summary_lines'])}"
            if key in seen_summaries:
                continue
            summary["tournament"] = tournament_title
            summary["url"] = url
            new_summaries.append(summary)
            seen_summaries[key] = now.isoformat()

    print(f"=== 指定選手の新着試合結果: {len(new_matches)}件 ===")
    for m in new_matches:
        print(f"  [{m['event']}/{m['round']}] {m['tournament']}: {m['winner']} {m['score']} {m['loser']}")

    print(f"=== 全国大会の新着結果サマリー: {len(new_summaries)}件 ===")
    for s in new_summaries:
        print(f"  [{s['tournament']}] {s['label']}: {s['summary_lines']}")

    badspi_articles = fetch_badspi_articles()
    new_badspi_articles = []
    for a in badspi_articles:
        if a["url"] in seen_badspi:
            continue
        new_badspi_articles.append(a)
        seen_badspi[a["url"]] = now.isoformat()

    print(f"=== バド×スピ新着記事: {len(new_badspi_articles)}件 ===")
    for a in new_badspi_articles:
        print(f"  {a['title']}")

    had_error = False
    embeds = (
        build_badminton_embeds(new_matches, now)
        + build_national_summary_embeds(new_summaries, now)
        + build_badspi_embeds(new_badspi_articles)
    )
    if embeds:
        if not send_embeds_to_discord(webhook, embeds):
            had_error = True
    else:
        print("[INFO] 配信対象の新着はありませんでした。")

    save_seen_state(state)

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
