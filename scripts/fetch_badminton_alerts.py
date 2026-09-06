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

3. Google Newsの「バドミントン 日本代表」検索RSS(2026-09-06追加)。
   スポーツナビ・TBS NEWS・J SPORTS等、1.2.でカバーしきれない媒体の速報を
   補完する目的。本番テストでPR TIMES(企業提携告知)・楽天(独占販売告知)・
   イーファイト(ゴシップ)・スポーツナビ「究極の2択」(企画動画)が大量混入
   することを確認したため、ドメインブラックリスト(GNEWS_DOMAIN_BLACKLIST)
   とキーワードブラックリスト(GNEWS_KEYWORD_BLACKLIST)の二段構えで除外する
   (is_gnews_noise)。同一記事が複数ポータルに転載され二重配信されることも
   確認したため、タイトル単位の重複除去も行う。

4. 日本バドミントン協会(NBA)公式サイトの大会結果ページ(2026-09-06追加)。
   細川さん指定の一覧URL(/tournament/result/)自体は404で存在しないため、
   大会トップページ(NBA_TOURNAMENT_TOP_URL)に列挙されている個別結果ページ
   へのリンクからID一覧を収集する方式にした。個別結果ページのDOM構造
   (li.v-tournament-info__match-item以下に選手名・所属・勝敗クラス・
   ゲームスコアがクラス名で明確に分かれている)を実データで確認し、日本語
   文字を含む選手名が関与する試合の勝敗概要のみを抽出する(全試合ではなく
   日本選手関連のみに絞る設計は1.(b)と同じ考え方)。

時間帯による配信方針(2026-09-06、細川さんの指定で追加):
  ヨーロッパ開催時等、深夜(23:00〜翌05:59 JST)に試合が集中して終了し、
  バラバラ通知されるのを避けるため、get_time_band()で以下の3帯に分ける。
  - day(09:00〜22:59):     即時配信(15分間隔cronを想定)
  - night(23:00〜05:59):   Discordへは配信せず、digest_poolに積むだけ
  - morning(06:00〜08:59): 即時配信。かつプールに何か残っていれば
    「昨夜のバドミントン結果ダイジェスト」として1通(複数Embed)にまとめて
    先に配信してからプールを空にする(6:00ちょうどの専用cronが遅延しても、
    次にday/morning帯で実行された時点で必ず配信される設計)。

火曜18:00 JST 日本勢ランキングダイジェスト(2026-09-06追加):
  BWF世界ランキングは日曜の大会終了を受け毎週火曜午後に公式更新される
  ため、is_weekly_ranking_check_time()で火曜18:00 JST台を検知する
  (day帯の15分間隔cronが18:00ちょうども含むため専用cronは追加していない。
  state["last_weekly_ranking_check_date"]で週1回だけに制御)。
  日本人選手のランキング(順位+氏名、上位WEEKLY_RANKING_TOP_N人)を前回
  配信時のスナップショットと比較し、変化が無ければ「大会が無かった週」
  とみなして配信をスキップする(細川さん指定のフォールバック仕様)。
  変化があれば、NBA公式のナショナルチームページ(所属)+バドナビの選手
  詳細ページ(年齢、配信対象選手のみオンデマンド取得)を付加した
  ダイジェストを配信する。

環境変数:
  DISCORD_WEBHOOK_SPORTS_CULTURE (必須)
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.parse

import feedparser
import requests
from bs4 import BeautifulSoup

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "badminton_alerts_seen.json")
STATE_RETENTION_DAYS = 30
REQUEST_TIMEOUT = 15
BADSPI_RSS_URL = "https://www.badspi.jp/feed/"
BADSPI_ITEM_LIMIT = 20
COLOR_BADSPI = 0x2B6CB0
GOOGLE_NEWS_BADMINTON_QUERY = "バドミントン 日本代表"
GOOGLE_NEWS_BADMINTON_ITEM_LIMIT = 20
COLOR_GNEWS_BADMINTON = 0x718096

# 2026-09-06、初回実装の本番テストで実際に混入したノイズを見て、細川さんの
# 指定により追加した除外リスト(実データ: PR TIMESの企業提携告知、楽天の
# 独占販売告知、イーファイト発のゴシップ記事等が混入することを確認済み)。
# ドメインはGoogle News RSSのentry.source.href(配信元の実ドメイン)で判定し、
# キーワードはタイトル全体に対して判定する(ゴシップ記事はYahoo!ニュース
# 転載など配信元ドメインだけでは判別できないため、内容キーワードで弾く)。
GNEWS_DOMAIN_BLACKLIST = ["prtimes.jp", "efight.jp"]
GNEWS_KEYWORD_BLACKLIST = [
    "パートナーシップ", "独占販売", "クラウドファンディング", "協賛",
    "グラビア", "水着", "美ボディ", "9頭身", "温泉", "【究極の2択】",
]


def is_gnews_noise(title, source_domain):
    if source_domain and any(d in source_domain for d in GNEWS_DOMAIN_BLACKLIST):
        return True
    return any(kw in title for kw in GNEWS_KEYWORD_BLACKLIST)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TAG_PAGE_URL = "https://www.sposoku.com/tag/badminton/"

# ============================================================
# 【消去厳禁】固定監視選手(推し選手・最優先フィルタ)
# ============================================================
# 2026-09-06、細川さんの指定により恒久固定リストとして独立定義した。
# ここに掲載した選手は、以下のTARGET_PLAYERS(sposoku.com向けマッチング
# リスト)の生成元になっており、TARGET_PLAYERS側を誰かが後から編集・整理
# しても、FAVORITE_PLAYERS自体さえ残っていれば自動的に追跡対象へ含まれる
# (name一覧を動的に合成する設計、下記TARGET_PLAYERS参照)。
# category は BWF種目略称: MS=男子シングルス, WS=女子シングルス,
# MD=男子ダブルス, WD=女子ダブルス, XD=混合ダブルス。
FAVORITE_PLAYERS = [
    {"name": "渡辺勇大", "name_en": "Yuta Watanabe", "category": ["XD", "MD"]},
    {"name": "松友美佐紀", "name_en": "Misaki Matsutomo", "category": ["XD", "WD"]},
    {"name": "奥原希望", "name_en": "Nozomi Okuhara", "category": ["WS"]},
]

# 指定注目選手(現役選手に確定。引退選手は含めない)。
# この文字列が記事側の選手名表記に含まれる場合にマッチとする。
# 先頭はFAVORITE_PLAYERSから自動合成(上記の「消去厳禁」を参照)。
TARGET_PLAYERS = [p["name"] for p in FAVORITE_PLAYERS] + [
    "田口真彩",
    "志田千陽",
    "東野有紗", "五十嵐有紗",  # 東野有紗(旧姓:五十嵐有紗)。ダブルスでは旧姓表記の場合があるため両方登録
    "中西貴映",  # 岩永/中西ペア
    "高橋明日香",  # ※高橋沙也加(引退)とは別人。フルネームで区別する
    "福島由紀",  # 松本/福島ペア等
    "松山奈未",
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
    state["seen_gnews_urls"] = _prune_dict(state.get("seen_gnews_urls", {}), cutoff)
    state["seen_nba_result_ids"] = _prune_dict(state.get("seen_nba_result_ids", {}), cutoff)
    state["seen_finals_digest_ids"] = _prune_dict(state.get("seen_finals_digest_ids", {}), cutoff)
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


# ============================================================
# Google News「バドミントン 日本代表」検索RSS
# ============================================================

def fetch_google_news_badminton(limit=GOOGLE_NEWS_BADMINTON_ITEM_LIMIT, retries=2):
    """Google Newsの「バドミントン 日本代表」検索RSSを取得する。短時間の
    連続リクエストでレート制限(503)されることが他スクリプトで確認済みの
    ため、同じ対策(間隔+リトライ)を踏襲する。"""
    q = urllib.parse.quote(GOOGLE_NEWS_BADMINTON_QUERY)
    url = f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"
    time.sleep(2.0)

    resp = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] Google News RSS取得に失敗(試行{attempt + 1}/{retries}): {err}")
            resp = None
            time.sleep(5.0)

    if resp is None:
        print("[ERROR] Google News RSS取得に失敗しました(バドミントン 日本代表)")
        return []

    feed = feedparser.parse(resp.content)
    items = []
    seen_titles_in_batch = set()
    for entry in feed.entries[:limit]:
        title = entry.get("title", "")
        url_ = entry.get("link", "")
        if not title or not url_:
            continue

        source = entry.get("source") or {}
        source_domain = source.get("href", "")
        source_name = source.get("title", "")
        # タイトル末尾の "... - 配信元名" は表示上冗長なので、source_nameが
        # 一致する場合のみ取り除く(一致しない場合はタイトルをそのまま使う)。
        if source_name and title.endswith(f" - {source_name}"):
            title = title[: -len(f" - {source_name}")]

        # S/Jリーグ(実業団)関連記事の除外。
        if is_league_excluded(title):
            print(f"[INFO] S/Jリーグ(実業団)関連記事のため除外します: {title}")
            continue
        # PR TIMES(企業提携告知)・イーファイト(ゴシップ)等のノイズ除外
        # (2026-09-06、本番テストで実際に混入したノイズを見て追加)。
        if is_gnews_noise(title, source_domain):
            print(f"[INFO] ノイズ記事のため除外します({source_domain}): {title}")
            continue
        # 同一記事が複数ポータル(TBS NEWS DIG / Infoseek等)に転載され、
        # タイトルだけ実質同じで二重配信されるケースがあるため、
        # タイトル単位でも重複除去する。
        if title in seen_titles_in_batch:
            continue
        seen_titles_in_batch.add(title)

        items.append({"title": title, "url": url_})
    return items


def build_google_news_badminton_embeds(articles):
    """記事単位でタイトル+リンクをそのままEmbed化する(badspi.jpと同じ
    シンプルな新着通知形式)。"""
    embeds = []
    for a in articles:
        embeds.append({
            "title": f"🏸 {a['title']}"[:256],
            "description": f"[記事を読む](<{a['url']}>)",
            "color": COLOR_GNEWS_BADMINTON,
        })
    return embeds


# ============================================================
# BWF世界ランキング(バドナビ経由、2026-09-06追加)
# ============================================================
# BWF公式サイト(bwfbadminton.com/rankings/)は既知の通り403で直接アクセス
# 不可のため、日本語のバドミントン用具レビューサイト「バドナビ」
# (badminton-navi.net)が掲載している世界ランキング表(男女シングルス・
# ダブルス・混合ダブルスの5種目、各上位100件)を情報源にする。実データで
# table.rankingTable の構造(.rankCell=順位、.nameCell .nameTxt=日本語表記、
# .nameCell の残りテキスト=アルファベット表記)を確認済み。ダブルス種目も
# ペア単位ではなく選手個人ごとに1行でランクインしていることを確認したため、
# ペアの各選手をそれぞれ個別に検索する設計で問題ない。
# 選手名の表記ゆれ(スペース有無・読み仮名括弧)を吸収するため、
# 正規化キー(空白・括弧除去+大文字化)で照合する。
WR_RANKING_URLS = {
    "male_singles": "https://badminton-navi.net/player/ranking_detail/world/men/single",
    "female_singles": "https://badminton-navi.net/player/ranking_detail/world/women/single",
    "male_doubles": "https://badminton-navi.net/player/ranking_detail/world/men/double",
    "female_doubles": "https://badminton-navi.net/player/ranking_detail/world/women/double",
    "mixed_doubles": "https://badminton-navi.net/player/ranking_detail/world/mixed/double",
}
WR_RANKINGS_CACHE_HOURS = 24  # ランキングは頻繁に変わらないためstateにキャッシュする


def _normalize_player_key(name):
    name = re.sub(r"[（(].*?[）)]", "", name)  # 読み仮名括弧(例:「（シー・ユーチ）」)を除去
    name = re.sub(r"\s+", "", name)  # 半角/全角スペースを除去
    return name.strip().upper()


CATEGORY_LABELS_JA = {
    "male_singles": "男子シングルス",
    "female_singles": "女子シングルス",
    "male_doubles": "男子ダブルス",
    "female_doubles": "女子ダブルス",
    "mixed_doubles": "混合ダブルス",
}


def fetch_wr_rankings():
    """バドナビからBWF世界ランキング5種目を取得する。
    戻り値は (rankings, japan_rankings) のタプル。
    - rankings: 選手名の正規化キー→順位の辞書(WR注釈付与に使う、既存仕様)。
    - japan_rankings: {category: [{"rank","name_jp","name_alpha","player_id"}, ...]}
      国旗が"JPN"の行だけを順位順に集めたもの(火曜の日本勢ランキング
      ダイジェスト配信に使う、2026-09-06追加)。
    種目単位で取得失敗してもその種目だけスキップし、処理は継続する。
    """
    rankings = {}
    japan_rankings = {}
    for category, url in WR_RANKING_URLS.items():
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] 世界ランキング取得に失敗しました({category}): {err}")
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        count = 0
        japan_list = []
        for tr in soup.select("table.rankingTable tr"):
            rank_tag = tr.select_one(".rankCell b")
            name_cell = tr.select_one(".nameCell")
            if not (rank_tag and name_cell):
                continue
            try:
                rank = int(rank_tag.get_text(strip=True))
            except ValueError:
                continue
            name_txt_tag = name_cell.select_one(".nameTxt")
            jp_name = name_txt_tag.get_text(strip=True) if name_txt_tag else ""
            # nameCell内には氏名の<a>タグの他に.pointCell(獲得ポイント数)も
            # 同居しているため、<a>タグの範囲だけからアルファベット表記を
            # 取り出す(nameCell全体から取るとポイント数の文字列が混入する
            # バグがあったため2026-09-06修正)。
            name_link = name_cell.select_one("a")
            alpha_name = name_link.get_text(" ", strip=True) if name_link else name_cell.get_text(" ", strip=True)
            if jp_name:
                alpha_name = alpha_name.replace(jp_name, "").strip()
            for raw_name in (jp_name, alpha_name):
                key = _normalize_player_key(raw_name) if raw_name else ""
                if key and key not in rankings:  # 既に(より上位の)登録があれば上書きしない
                    rankings[key] = rank
            count += 1

            country_img = tr.select_one(".contryCell img")
            if country_img and country_img.get("alt") == "JPN":
                player_link = name_cell.select_one('a[href*="/player/detail/"]')
                player_id_m = re.search(r"/player/detail/(\d+)", player_link.get("href", "")) if player_link else None
                japan_list.append({
                    "rank": rank,
                    "name_jp": jp_name,
                    "name_alpha": alpha_name,
                    "player_id": player_id_m.group(1) if player_id_m else None,
                })
        japan_rankings[category] = sorted(japan_list, key=lambda p: p["rank"])
        print(f"[INFO] 世界ランキング取得({category}): {count}件(うち日本人{len(japan_list)}人)")
    return rankings, japan_rankings


def annotate_player_with_wr(name, wr_rankings):
    """選手名に世界ランキング [WR◯] を付与する(見つからなければそのまま)。"""
    rank = wr_rankings.get(_normalize_player_key(name))
    return f"{name}[WR{rank}]" if rank else name


# ============================================================
# 火曜18:00 JST 日本勢ランキングダイジェスト(2026-09-06追加)
# ============================================================
# BWF世界ランキングは日曜の大会終了を受けて毎週火曜午後に公式更新される
# ため、火曜18:00 JSTに合わせてチェックする。既存のday帯15分間隔cron
# (JST 9:00〜22:59)が18:00ちょうども含むため、専用cronは追加せず
# get_time_bandとは別枠でこの時刻を判定する。同じ週に何度もチェックが
# 走らないよう、state["last_weekly_ranking_check_date"]で「その週(火曜)
# 1回だけ」に制御する。
# 前回配信時の日本人ランキング(順位+氏名)のスナップショットと比較し、
# 変化が無ければ「大会が無かった週」とみなして配信をスキップする
# (細川さん指定のフォールバック仕様)。
WEEKLY_RANKING_TOP_N = 5  # 種目ごとに配信・年齢取得の対象にする上位人数
NBA_NATIONAL_TEAM_URLS = [
    "https://www.badminton.or.jp/national/player?gender=male",
    "https://www.badminton.or.jp/national/player?gender=female",
    "https://www.badminton.or.jp/national/player?gender=male&category[]=u24",
    "https://www.badminton.or.jp/national/player?gender=female&category[]=u24",
]


def is_weekly_ranking_check_time(now, state):
    """火曜18:00 JST台で、かつ今週まだチェックしていなければTrue。"""
    if now.weekday() != 1 or now.hour != 18:  # weekday(): 月曜=0, 火曜=1
        return False
    return state.get("last_weekly_ranking_check_date") != now.strftime("%Y-%m-%d")


def fetch_nba_team_affiliations():
    """NBA公式のナショナルチームページ(男女+U24男女)から、選手名の正規化
    キー→所属の辞書を構築する(実データでDOM構造
    .p-player__item > .p-player__name / .p-player__prof を確認済み)。"""
    affiliations = {}
    for url in NBA_NATIONAL_TEAM_URLS:
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] NBA代表選手一覧の取得に失敗しました({url}): {err}")
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".p-player__item"):
            name_tag = item.select_one(".p-player__name")
            prof_tag = item.select_one(".p-player__prof")
            if not (name_tag and prof_tag):
                continue
            name = name_tag.get_text(strip=True)
            prof_lines = prof_tag.get_text("\n", strip=True).split("\n")
            affiliation = prof_lines[0].split("/")[0].strip() if prof_lines else ""
            key = _normalize_player_key(name)
            if key and affiliation:
                affiliations[key] = affiliation
    return affiliations


def fetch_badnavi_player_age(player_id):
    """バドナビの選手詳細ページから年齢を取得する(取得できなければNone)。
    配信対象(各種目上位WEEKLY_RANKING_TOP_N人)の日本人選手のみに限定して
    呼ぶ設計(全ランキング選手分を取得すると無駄なリクエストになるため)。
    """
    if not player_id:
        return None
    url = f"https://badminton-navi.net/player/detail/{player_id}"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] バドナビ選手詳細の取得に失敗しました({url}): {err}")
        return None
    m = re.search(r"年齢\D*(\d+)歳", resp.text)
    return int(m.group(1)) if m else None


def build_weekly_ranking_snapshot(japan_rankings):
    """種目ごとの上位WEEKLY_RANKING_TOP_N人の(順位, 氏名)をタプル化した
    比較用スナップショットを作る(前回配信時との差分検知に使う)。"""
    snapshot = {}
    for category, players in japan_rankings.items():
        snapshot[category] = [
            [p["rank"], p["name_jp"] or p["name_alpha"]]
            for p in players[:WEEKLY_RANKING_TOP_N]
        ]
    return snapshot


def build_weekly_ranking_digest_embed(japan_rankings, affiliations, now):
    """日本勢上位ランキング(年齢・所属付き)のEmbedを1つ組み立てる。"""
    lines = []
    for category, players in japan_rankings.items():
        top = players[:WEEKLY_RANKING_TOP_N]
        if not top:
            continue
        label = CATEGORY_LABELS_JA.get(category, category)
        lines.append(f"**【{label}】**")
        for p in top:
            name = p["name_jp"] or p["name_alpha"]
            affiliation = affiliations.get(_normalize_player_key(name), "")
            age = fetch_badnavi_player_age(p["player_id"])
            age_text = f"・{age}歳" if age else ""
            affiliation_text = f"・{affiliation}" if affiliation else ""
            lines.append(f"　{p['rank']}位 {name}{age_text}{affiliation_text}")
    if not lines:
        return None
    return {
        "title": "🏆 日本勢 世界ランキング(火曜更新ダイジェスト)",
        "description": "\n".join(lines) + f"\n\n({now.strftime('%Y-%m-%d')} JST時点・バドナビ調べ)",
        "color": 0xC53030,
    }


# ============================================================
# 日本バドミントン協会(NBA)公式サイトの大会結果ページ(2026-09-06追加)
# ============================================================
# 大会トップページ(NBA_TOURNAMENT_TOP_URL)に個別大会結果ページへの
# リンク(/tournament/result/{id})が列挙されているのを実データで確認済み
# (細川さん指定の一覧URL "/tournament/result/" 自体は404で存在しないため、
# 代わりにこちらから収集する)。個別結果ページのDOM構造
# (li.v-tournament-info__match-item 以下に選手名・所属・勝敗クラス・
# ゲームスコアがクラス名で明確に分かれている)を実データで確認し、
# 日本語文字を含む選手名が関与する試合だけを抽出する。
# 「すべて」タブ(#panel-1)には全種目が重複なく含まれ、種目別タブ
# (#panel-2以降)は同じ試合の重複表示だったため、#panel-1のみを対象にする。
NBA_TOURNAMENT_TOP_URL = "https://www.badminton.or.jp/tournament/"
NBA_RESULT_URL_TEMPLATE = "https://www.badminton.or.jp/tournament/result/{id}"
NBA_RESULT_ID_LIMIT = 30
NBA_TITLE_RE = re.compile(r"^(\d{4}年\d{1,2}月\d{1,2}日)\s*\|\s*(.+?)\s*\|\s*大会結果")
JAPANESE_CHAR_RE = re.compile(r"[ぁ-んァ-ヶ一-龠]")
COLOR_NBA = 0x1A365D


def fetch_nba_result_ids(limit=NBA_RESULT_ID_LIMIT):
    """NBA公式サイトの大会トップページから、個別の大会結果ページの
    ID一覧を収集する(トップページに掲載されている範囲のみ。過去の
    全結果を遡るページネーションは追わない)。"""
    try:
        resp = requests.get(NBA_TOURNAMENT_TOP_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] NBA大会トップページの取得に失敗しました: {err}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    ids = []
    seen_in_batch = set()
    for a in soup.select('a[href*="/tournament/result/"]'):
        m = re.search(r"/tournament/result/(\d+)", a.get("href", ""))
        if m and m.group(1) not in seen_in_batch:
            seen_in_batch.add(m.group(1))
            ids.append(m.group(1))
    return ids[:limit]


def is_japanese_name(name):
    return bool(JAPANESE_CHAR_RE.search(name))


def extract_match_games(center_div):
    """.v-tournament-info__match-center から各ゲームの(左スコア, 右スコア)
    のリストを抽出する(2026-09-06追加。実データでDOM構造を確認済み:
    .v-tournament-info__match-score 1つ=1ゲームで、中に
    .v-tournament-info__match-score-num が2つ(左→右の順)入っている)。
    """
    games = []
    if not center_div:
        return games
    for score_div in center_div.select(".v-tournament-info__match-score"):
        nums = score_div.select(".v-tournament-info__match-score-num")
        if len(nums) != 2:
            continue
        try:
            left_score = int(nums[0].get_text(strip=True))
            right_score = int(nums[1].get_text(strip=True))
        except ValueError:
            continue
        games.append((left_score, right_score))
    return games


def format_match_score(games):
    """ゲームリストから「セット数（ゲームごとの得点）」の表示文字列を作る。
    例: "2-0（21-14, 21-18）"。ゲーム情報が無ければ空文字を返す。
    """
    if not games:
        return ""
    left_sets = sum(1 for l, r in games if l > r)
    right_sets = sum(1 for l, r in games if r > l)
    game_scores = ", ".join(f"{l}-{r}" for l, r in games)
    return f"{left_sets}-{right_sets}（{game_scores}）"


def _parse_nba_match_item(item, wr_rankings):
    """1試合分のDOMを解析する共通ヘルパー(日本語フィルタなし)。
    japan_matches(既存仕様)と決勝結果ダイジェスト(2026-09-06追加)の
    両方がこれを共有する。解析できなければNoneを返す。"""
    head = item.select_one(".v-tournament-info__match-head")
    left = item.select_one(".v-tournament-info__match-left")
    right = item.select_one(".v-tournament-info__match-right")
    if not (head and left and right):
        return None

    # ダブルスは左右それぞれの側に選手が2人分(.v-tournament-info__match-name
    # が2つ)入っていることを実データで確認したため、select_oneではなく
    # select()で両者とも取得して結合する(select_oneだと1人目しか
    # 拾えず、2人目の名前が欠落するバグがあった)。
    left_names = [n.get_text(strip=True) for n in left.select(".v-tournament-info__match-name")]
    right_names = [n.get_text(strip=True) for n in right.select(".v-tournament-info__match-name")]
    if not (left_names and right_names):
        return None

    left_player_div = left.select_one(".v-tournament-info__match-player")
    right_player_div = right.select_one(".v-tournament-info__match-player")
    left_win = bool(left_player_div and any("win" in c for c in left_player_div.get("class", [])))
    right_win = bool(right_player_div and any("win" in c for c in right_player_div.get("class", [])))

    left_teams = [t.get_text(strip=True) for t in left.select(".v-tournament-info__match-team")]
    right_teams = [t.get_text(strip=True) for t in right.select(".v-tournament-info__match-team")]
    left_team = left_teams[0] if left_teams else ""
    right_team = right_teams[0] if right_teams else ""

    wr = wr_rankings or {}
    left_display = "・".join(annotate_player_with_wr(n, wr) for n in left_names) + left_team
    right_display = "・".join(annotate_player_with_wr(n, wr) for n in right_names) + right_team

    center = item.select_one(".v-tournament-info__match-center")
    score_text = format_match_score(extract_match_games(center))

    return {
        "round_event": head.get_text(strip=True),
        "left_names": left_names,
        "right_names": right_names,
        "left_team": left_team,
        "right_team": right_team,
        "left_win": left_win,
        "right_win": right_win,
        "score_text": score_text,
        "left": left_display,
        "right": right_display,
    }


def fetch_nba_result_detail(result_id, wr_rankings=None):
    """個別の大会結果ページを取得し、大会名・更新日・日本選手が関与する
    試合の概要(japan_matches)と、全試合の生データ(all_matches、決勝結果
    ダイジェスト用、2026-09-06追加)の両方を返す。取得失敗・S/Jリーグ
    (実業団)関連・結果がまだ1件も掲載されていない場合はNoneを返す。"""
    url = NBA_RESULT_URL_TEMPLATE.format(id=result_id)
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] NBA大会結果ページの取得に失敗しました({url}): {err}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    m = NBA_TITLE_RE.match(title_text)
    if not m:
        print(f"[WARN] NBA大会結果ページのタイトル形式が想定外のためスキップします({url}): {title_text}")
        return None
    update_date, tournament_name = m.groups()

    if is_league_excluded(tournament_name) or is_league_excluded(title_text):
        print(f"[INFO] S/Jリーグ(実業団)関連大会のため除外します: {tournament_name}")
        return None

    panel1 = soup.select_one("#panel-1")
    raw_items = panel1.select("li.v-tournament-info__match-item") if panel1 else []
    if not raw_items:
        return None  # まだ結果が1件も掲載されていない(大会前の告知ページ等)

    all_matches = [m for m in (_parse_nba_match_item(item, wr_rankings) for item in raw_items) if m]

    japan_matches = []
    for pm in all_matches:
        if not (any(is_japanese_name(n) for n in pm["left_names"]) or any(is_japanese_name(n) for n in pm["right_names"])):
            continue  # 日本選手が関与しない試合は対象外
        japan_matches.append({
            "round_event": pm["round_event"],
            "left": pm["left"],
            "right": pm["right"],
            "left_win": pm["left_win"],
            "right_win": pm["right_win"],
            "score_text": pm["score_text"],
        })

    return {
        "id": result_id,
        "tournament": tournament_name,
        "update_date": update_date,
        "url": url,
        "japan_matches": japan_matches,
        "all_matches": all_matches,
    }


def build_nba_result_embeds(results):
    embeds = []
    for r in results:
        lines = []
        for m in r["japan_matches"][:15]:  # Discord Embed description上限対策
            score = f" {m['score_text']}" if m["score_text"] else ""
            if m["left_win"]:
                lines.append(f"◯ **{m['left']}**{score} {m['right']} ({m['round_event']})")
            elif m["right_win"]:
                lines.append(f"{m['left']}{score} **{m['right']}** ◯ ({m['round_event']})")
            else:
                lines.append(f"{m['left']}{score} {m['right']} ({m['round_event']})")
        if not lines:
            lines = ["(日本選手が関与する試合結果はまだ掲載されていません)"]
        embeds.append({
            "title": f"🏸 【{r['tournament']}】大会結果(NBA公式・{r['update_date']}更新)"[:256],
            "description": "\n".join(lines) + f"\n[詳細を見る](<{r['url']}>)",
            "color": COLOR_NBA,
        })
    return embeds


# ============================================================
# 決勝結果ダイジェスト(WR入り、注目選手結果付き)(2026-09-06追加)
# ============================================================
# NBA公式の結果ページに「決勝(戦)」ラウンドのデータが載った時点で、その
# 大会は終了したとみなし、種目別の優勝/準優勝(WRランキング付き)+注目選手
# (FAVORITE_PLAYERS)の直近結果をまとめた専用フォーマットで配信する。
# トリガーは「決勝データを新規検出した実行タイミング」そのもの(BWFの
# 大会日程を別途取得する手段が無い=既知の制約のため、日程判定ではなく
# 結果データの有無で間接的に大会終了を検知する設計。多くの大会が日曜に
# 決勝を迎えるため、実質的に月曜前後の巡回で検知されることが多い)。
FINAL_ROUND_RE = re.compile(r"^決勝(戦)?")
CATEGORY_EVENT_LABELS = ["男子シングルス", "女子シングルス", "男子ダブルス", "女子ダブルス", "混合ダブルス"]


def extract_final_matches(all_matches):
    """全試合データ(_parse_nba_match_item由来)から、種目ごとの決勝戦
    だけを抽出する({種目名: match_dict})。"""
    finals = {}
    for pm in all_matches:
        if not FINAL_ROUND_RE.match(pm["round_event"]):
            continue
        for event_label in CATEGORY_EVENT_LABELS:
            if event_label in pm["round_event"]:
                finals[event_label] = pm
                break
    return finals


# NBA公式結果ページのteam欄は、海外選手の場合は国名(漢字表記を含む)で
# 入っている実データを確認済み。中国・台湾・香港等の選手名は漢字表記のため
# is_japanese_name(選手名の文字種判定)だけでは「中国人選手なのに国籍=日本」
# と誤判定してしまうバグがあった(2026-09-06、決勝ダイジェスト機能の
# テスト中に発見)。team欄の値がバドミントン強豪国としてよく登場する
# 国名リストに一致する場合はteam欄をそのまま国籍として優先採用し、
# 一致しない場合のみ選手名の文字種で「日本」判定する(消去法)。
KNOWN_NON_JAPAN_TEAM_NAMES = [
    "中国", "韓国", "インドネシア", "マレーシア", "タイ", "インド", "台湾", "チャイニーズ・タイペイ",
    "デンマーク", "フランス", "スペイン", "ドイツ", "オランダ", "イングランド", "スコットランド",
    "アメリカ", "カナダ", "シンガポール", "香港", "ホンコン・チャイナ", "ベトナム", "フィリピン",
    "オーストラリア", "ニュージーランド", "ブラジル", "スイス", "スウェーデン", "ノルウェー",
    "フィンランド", "ロシア", "ウクライナ", "ポーランド", "イタリア", "ベルギー", "エジプト",
    "南アフリカ", "ブルガリア", "チェコ", "ハンガリー", "ラトビア", "アイルランド", "トルコ",
]


def player_nationality_display(names, team):
    """選手側の国籍表記を決める。team欄が既知の海外国名と一致すれば
    それを優先し(漢字圏の選手名を誤って「日本」と判定するのを防ぐ)、
    一致しなければ選手名の文字種から「日本」と判定する。"""
    team_clean = (team or "").strip("()（）")
    if team_clean in KNOWN_NON_JAPAN_TEAM_NAMES:
        return team_clean
    if any(is_japanese_name(n) for n in names):
        return "日本"
    return team_clean or "国籍不明"


def format_final_side(names, team, wr_rankings):
    """決勝の片側(優勝/準優勝いずれか)を「氏名（国籍 / WR ○位）」形式で
    整形する(ダブルスは「・」区切りで両選手分)。ランキング未掲載の選手は
    「WR未掲載」と明記する(ペア再編等で暫定的にランク付けが無い場合の
    注記も兼ねる)。"""
    nationality = player_nationality_display(names, team)
    parts = []
    for n in names:
        rank = wr_rankings.get(_normalize_player_key(n))
        rank_text = f"WR {rank}位" if rank else "WR未掲載"
        parts.append(f"{n}（{nationality} / {rank_text}）")
    return "・".join(parts)


def find_favorite_player_matches(all_matches, favorite_players):
    """FAVORITE_PLAYERSの各選手について、この大会での最終戦(出場していれば
    最も進んだラウンド=敗退or優勝したカード)を返す({氏名: match_dict})。
    NBA結果ページはラウンド順(1回戦→…→決勝)に並んでいる実データを確認
    済みのため、最後にマッチしたものを採用すれば最終戦になる。"""
    results = {}
    for fav in favorite_players:
        name = fav["name"]
        matched = None
        for pm in all_matches:
            if name in pm["left_names"] or name in pm["right_names"]:
                matched = pm
        results[name] = matched
    return results


def format_favorite_player_line(name, match, wr_rankings):
    """注目選手1名分の直近結果を1行に整形する。大会に出場していなければ
    Noneを返す(その選手の行自体を出力しない)。"""
    if match is None:
        return None
    if name in match["left_names"]:
        opp_names, opp_team, won = match["right_names"], match["right_team"], match["left_win"]
    else:
        opp_names, opp_team, won = match["left_names"], match["left_team"], match["right_win"]

    opp_nationality = player_nationality_display(opp_names, opp_team)
    opp_ranked = []
    for n in opp_names:
        rank = wr_rankings.get(_normalize_player_key(n))
        opp_ranked.append(f"{n}(WR{rank})" if rank else n)
    opp_display = "・".join(opp_ranked)

    result_text = "勝利" if won else "敗退"
    score = match["score_text"] or "スコア不明"
    return f"・{name}：{match['round_event']} {result_text} {score} ({opp_display} / {opp_nationality})"


def build_finals_digest_embed(result, wr_rankings):
    """1大会分の決勝結果ダイジェストEmbedを組み立てる。決勝データが
    1種目も無ければNoneを返す。"""
    finals = extract_final_matches(result["all_matches"])
    if not finals:
        return None

    lines = []

    favorite_matches = find_favorite_player_matches(result["all_matches"], FAVORITE_PLAYERS)
    favorite_lines = [
        line for fav in FAVORITE_PLAYERS
        if (line := format_favorite_player_line(fav["name"], favorite_matches.get(fav["name"]), wr_rankings))
    ]
    if favorite_lines:
        lines.append("**【注目選手結果】**")
        lines.extend(favorite_lines)
        lines.append("－" * 20)

    for event_label in CATEGORY_EVENT_LABELS:
        match = finals.get(event_label)
        if not match:
            continue
        if match["left_win"]:
            winner_names, winner_team = match["left_names"], match["left_team"]
            loser_names, loser_team = match["right_names"], match["right_team"]
        else:
            winner_names, winner_team = match["right_names"], match["right_team"]
            loser_names, loser_team = match["left_names"], match["left_team"]

        lines.append(f"**【{event_label}】**")
        lines.append(f"・優勝：{format_final_side(winner_names, winner_team, wr_rankings)}")
        lines.append(f"・スコア：{match['score_text'] or '不明'}")
        lines.append(f"・準優勝：{format_final_side(loser_names, loser_team, wr_rankings)}")
        if event_label in ("男子シングルス", "女子シングルス") and any(is_japanese_name(n) for n in winner_names):
            lines.append(f"・日本勢結果：{winner_names[0]}（優勝）")
        lines.append("")

    description = "\n".join(lines).strip()
    description += f"\n\n出典: NBA公式（{result['update_date']}更新） / [詳細を見る](<{result['url']}>)"

    return {
        "title": f"🏸 【{result['tournament']}】決勝結果"[:256],
        "description": description[:4096],  # Discord Embed description上限
        "color": 0x744210,
    }


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


EMPTY_DIGEST_POOL = {"matches": [], "summaries": [], "badspi": [], "gnews": [], "nba": []}


def get_time_band(now):
    """JST時刻から配信帯を判定する(2026-09-06、細川さんの指定により追加)。
    - day:     09:00〜22:59 (試合が行われやすい時間帯、15分間隔cronに対応) → 即時配信
    - night:   23:00〜05:59 (深夜〜早朝、ヨーロッパ開催時の試合終了が集中) → 配信せずプール
    - morning: 06:00〜08:59 → 即時配信(かつ、この帯の最初の実行でプールを
               「昨夜のダイジェスト」としてまとめて配信する)
    dayとmorningのどちらも「即時配信モード」だが、プールの有無をチェックする
    処理は両方に共通で入れている(6:00ちょうどの専用cronが遅延・失敗しても、
    次の通常実行で必ずダイジェストが送られるようにするため)。
    """
    hour = now.hour
    if 9 <= hour <= 22:
        return "day"
    if hour == 23 or hour <= 5:
        return "night"
    return "morning"


def build_digest_embeds(pool, now):
    """深夜帯(23:00〜05:59)にプールされた新着を、朝にまとめて1回で配信する
    ためのEmbed群を組み立てる。先頭に見出しEmbedを付け、以降は通常の各
    ビルダーを流用する(配信フォーマットの一貫性を保つため)。"""
    body_embeds = (
        build_badminton_embeds(pool.get("matches", []), now)
        + build_national_summary_embeds(pool.get("summaries", []), now)
        + build_badspi_embeds(pool.get("badspi", []))
        + build_google_news_badminton_embeds(pool.get("gnews", []))
        + build_nba_result_embeds(pool.get("nba", []))
    )
    if not body_embeds:
        return []
    header = {
        "title": "🌙 昨夜のバドミントン結果ダイジェスト",
        "description": f"23:00〜{now.strftime('%m/%d')} 05:59 JSTの間に検知した新着 {len(body_embeds)}件をまとめてお届けします。",
        "color": 0x2D3748,
    }
    return [header] + body_embeds


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_SPORTS_CULTURE")
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_SPORTS_CULTURE が設定されていません。")
        sys.exit(1)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    band = get_time_band(now)
    print(f"=== 時間帯判定: {band} ({now.strftime('%Y-%m-%d %H:%M')} JST) ===")

    state = load_seen_state()
    state = prune_old_entries(state, now)
    seen = state.setdefault("seen_matches", {})
    seen_badspi = state.setdefault("seen_badspi_urls", {})
    seen_summaries = state.setdefault("seen_national_summaries", {})
    seen_gnews = state.setdefault("seen_gnews_urls", {})
    seen_nba = state.setdefault("seen_nba_result_ids", {})
    pool = state.setdefault("digest_pool", dict(EMPTY_DIGEST_POOL))
    for key, default in EMPTY_DIGEST_POOL.items():
        pool.setdefault(key, list(default))

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

    gnews_articles = fetch_google_news_badminton()
    new_gnews_articles = []
    for a in gnews_articles:
        if a["url"] in seen_gnews:
            continue
        new_gnews_articles.append(a)
        seen_gnews[a["url"]] = now.isoformat()

    print(f"=== Google News新着記事: {len(new_gnews_articles)}件 ===")
    for a in new_gnews_articles:
        print(f"  {a['title']}")

    nba_ids = fetch_nba_result_ids()

    # 世界ランキングはstateにキャッシュし、WR_RANKINGS_CACHE_HOURS以内なら
    # 再取得しない(頻繁に変わらない情報のため、実行のたびに5種目分の
    # リクエストを送るのは無駄かつ相手サイトへの負荷になる)。ただし
    # 火曜18:00のチェック時は、BWF公式が火曜午後に更新したばかりの最新
    # ランキングを反映したいため、キャッシュの新旧に関わらず必ず再取得する。
    weekly_check_time = is_weekly_ranking_check_time(now, state)

    wr_cache = state.get("wr_rankings") or {}
    wr_updated_at = wr_cache.get("updated_at")
    wr_stale = True
    if wr_updated_at:
        try:
            wr_stale = (now - datetime.datetime.fromisoformat(wr_updated_at)) > datetime.timedelta(hours=WR_RANKINGS_CACHE_HOURS)
        except ValueError:
            wr_stale = True
    japan_rankings = {}
    if wr_stale or weekly_check_time or not wr_cache.get("data"):
        reason = "火曜定期更新チェック" if weekly_check_time else "キャッシュが無い/古い"
        print(f"[INFO] 世界ランキングを再取得します({reason})。")
        wr_rankings, japan_rankings = fetch_wr_rankings()
        if wr_rankings:
            state["wr_rankings"] = {"updated_at": now.isoformat(), "data": wr_rankings}
        else:
            wr_rankings = wr_cache.get("data", {})  # 取得失敗時は古いキャッシュをそのまま使う
    else:
        wr_rankings = wr_cache.get("data", {})
        print(f"[INFO] 世界ランキングのキャッシュを使用します(更新: {wr_updated_at})。")

    if weekly_check_time:
        state["last_weekly_ranking_check_date"] = now.strftime("%Y-%m-%d")  # 同じ週に2回走らないよう先に記録
        if not japan_rankings:
            print("[WARN] 火曜定期チェックだがランキング取得に失敗したため、今週はスキップします。")
        else:
            new_snapshot = build_weekly_ranking_snapshot(japan_rankings)
            if new_snapshot == state.get("last_weekly_ranking_snapshot"):
                print("[INFO] 日本人ランキングに変化がないため(大会が無かった週)、ダイジェスト配信をスキップします。")
            else:
                print("[INFO] 日本人ランキングに変化を検知したため、ダイジェストを配信します。")
                affiliations = fetch_nba_team_affiliations()
                digest_embed = build_weekly_ranking_digest_embed(japan_rankings, affiliations, now)
                if digest_embed and send_embeds_to_discord(webhook, [digest_embed]):
                    state["last_weekly_ranking_snapshot"] = new_snapshot

    # 決勝結果ダイジェスト(2026-09-06追加)は、japan_matchesとは別の観点
    # (「決勝データの有無」)で大会を再チェックする必要があるため、
    # 一度seen_nbaに登録済みの大会でも、seen_finals未登録なら
    # (=まだ決勝ダイジェストを送っていなければ)結果ページを再取得する。
    # 「決勝データが無い(大会継続中)」大会はseen_finalsに登録されないため
    # 毎回リトライされる。もしチェック"試行"件数の方に上限を掛けると、
    # 常に同じ先頭の未完了大会だけを消費し続けて後方の大会に到達できなく
    # なるバグがあった(2026-09-06、本番テストで発見)ため、判定チェック
    # 自体は毎回nba_ids全件に対して行い、実際に「配信する」件数だけを
    # 制限する設計にした(導入時に既存の完了済み大会が一斉に「決勝あり」
    # 判定されてDiscordが荒れるのを避けるため。残りは次回以降の実行で
    # 少しずつ処理される)。
    seen_finals = state.setdefault("seen_finals_digest_ids", {})
    FINALS_DIGEST_PER_RUN_LIMIT = 3

    new_nba_results = []
    finals_digest_sent = []
    for result_id in nba_ids:
        need_japan_check = result_id not in seen_nba
        need_finals_check = result_id not in seen_finals
        if not (need_japan_check or need_finals_check):
            continue

        detail = fetch_nba_result_detail(result_id, wr_rankings)
        if need_japan_check:
            seen_nba[result_id] = now.isoformat()  # 除外・結果無しでも再取得しないよう既読化
        if detail is None:
            continue

        if need_japan_check:
            new_nba_results.append(detail)

        if need_finals_check and len(finals_digest_sent) < FINALS_DIGEST_PER_RUN_LIMIT:
            finals_embed = build_finals_digest_embed(detail, wr_rankings)
            if finals_embed:
                if send_embeds_to_discord(webhook, [finals_embed]):
                    seen_finals[result_id] = now.isoformat()
                    finals_digest_sent.append(detail["tournament"])
                    print(f"[OK] 決勝結果ダイジェストを配信しました: {detail['tournament']}")
            # 決勝データがまだ無い(大会継続中)場合はseen_finalsに登録せず、
            # 次回以降の実行で再度チェックする。

    print(f"=== NBA公式の新着大会結果: {len(new_nba_results)}件 ===")
    for r in new_nba_results:
        print(f"  [{r['tournament']}] 日本選手関連{len(r['japan_matches'])}試合")

    print(f"=== 決勝結果ダイジェスト配信: {len(finals_digest_sent)}件 ===")
    for t in finals_digest_sent:
        print(f"  {t}")

    had_error = False

    if band == "night":
        # 深夜帯: Discordへは配信せず、プールに積むだけ。
        pool["matches"].extend(new_matches)
        pool["summaries"].extend(new_summaries)
        pool["badspi"].extend(new_badspi_articles)
        pool["gnews"].extend(new_gnews_articles)
        pool["nba"].extend(new_nba_results)
        pooled_total = sum(len(v) for v in pool.values())
        print(f"[INFO] 深夜帯(23:00〜05:59)のためプールに追加しました(今回追加"
              f"{len(new_matches) + len(new_summaries) + len(new_badspi_articles) + len(new_gnews_articles) + len(new_nba_results)}"
              f"件、プール合計{pooled_total}件)。")
    else:
        # day/morning: 通常の即時配信。プールに何か残っていれば
        # (=夜間帯からの繰り越し、または前回ダイジェスト送信の失敗分)
        # 「昨夜のダイジェスト」として先にまとめて送り、プールを空にする。
        pooled_total = sum(len(v) for v in pool.values())
        if pooled_total:
            digest_embeds = build_digest_embeds(pool, now)
            print(f"=== 昨夜のダイジェストを配信します({pooled_total}件) ===")
            if send_embeds_to_discord(webhook, digest_embeds):
                for key in EMPTY_DIGEST_POOL:
                    pool[key] = []
            else:
                had_error = True
                print("[WARN] ダイジェスト送信に失敗したため、プールは保持し次回リトライします。")

        embeds = (
            build_badminton_embeds(new_matches, now)
            + build_national_summary_embeds(new_summaries, now)
            + build_badspi_embeds(new_badspi_articles)
            + build_google_news_badminton_embeds(new_gnews_articles)
            + build_nba_result_embeds(new_nba_results)
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
