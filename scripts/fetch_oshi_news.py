# -*- coding: utf-8 -*-
"""
「#推し」チャンネル向け キーワード追跡ニュース配信スクリプト

指定したキーワード(推し)ごとにGoogle News RSSで最新ニュースを取得し、
新着のみをDiscordへ配信する。キーワード一覧は OSHI_KEYWORDS (このファイル内、
下記参照)で一元管理し、今後の追加・削除はここを編集するだけで完結する。

各記事リンクの末尾には、RSS側から取得できた公開日時をJSTの "MM/DD HH:MM" 形式で
`[08/25 18:30]` のように付与する(鮮度判断のため)。取得できない場合のみ "-" とする。

Google News RSSは記事本文を含まない(summaryフィールドは実質タイトルの再掲のみ、
2026-08-26実施の実データ確認済み)ため、開催日程の抽出は記事タイトルのみを
対象とする。タイトルから「YYYY年M月D日」(単発/範囲)または「【M/D】」
「【M/D-D】」(全角括弧付きの短縮表記)の明確なパターンが見つかった記事のみ、
Googleカレンダーへのワンクリック登録リンクを、a)会期そのものの登録リンクと
b)終了日(単発イベントは開催日)のEVENT_REMINDER_DAYS日前を1日だけ登録する
リマインダーリンクの2種類付与する。パターンに合致しない一般ニュース記事は
誤検出を避けるため無理に付与せずスキップする。

環境変数:
  DISCORD_WEBHOOK_OSHI (必須)
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

JST = datetime.timezone(datetime.timedelta(hours=9))

# 開催日程抽出パターン(タイトルのみが対象。誤検出を避けるため、
# 明確な年月日表記、または全角括弧【】で囲まれた短縮日付表記のみを対象にする)
EVENT_DATE_RANGE_RE = re.compile(
    r"(?P<y1>\d{4})年(?P<m1>\d{1,2})月(?P<d1>\d{1,2})日\s*[~〜～\-－ー]\s*"
    r"(?:(?P<y2>\d{4})年)?(?P<m2>\d{1,2})月(?P<d2>\d{1,2})日"
)
EVENT_DATE_SINGLE_RE = re.compile(r"(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日")
EVENT_DATE_BRACKET_RANGE_RE = re.compile(
    r"【(?P<m>\d{1,2})/(?P<d1>\d{1,2})\s*[~〜～\-－ー]\s*(?P<d2>\d{1,2})】"
)
EVENT_DATE_BRACKET_SINGLE_RE = re.compile(r"【(?P<m>\d{1,2})/(?P<d>\d{1,2})】")
# 年を含まない短縮表記(【M/D】等)で組み立てた日付が、これより過去だった場合は
# 誤検出(去年のイベント記事等)とみなして安全側でスキップする
BRACKET_DATE_MAX_PAST_DAYS = 60

# ============================================================
# 追跡キーワード設定(ここに追加・削除するだけで対象を変更できる)
# ============================================================
OSHI_KEYWORDS = [
    # 音楽・アーティスト
    "U2",
    "サカナクション",
    "伊藤若冲",
    # バドミントン
    "渡辺勇大",
    "松友美佐紀",
    "田児賢一",
    # アニメ・映画・メカ
    "押井守",
    "パトレイバー",
    "ガンダム",
    "無職転生",
    "幼女戦記",
    "閃光のハサウェイ",
    # 漫画・クリエイター
    "永野護",
    "ファイブスター物語",
    # 作家・カルチャー・ビジネス
    "今野敏",
    "桐野夏生",
    "椎名誠",
    "水曜どうでしょう",
    "大泉洋",
    "田端信太郎",
    "箕輪厚介",
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


def format_published_jst(entry):
    """feedparserのエントリから公開日時を取得し、JSTの "MM/DD HH:MM" 形式で返す。
    取得できない場合(フィード側に日時情報が無い等)は "-" を返す。
    """
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return "-"
    try:
        # feedparserは公開日時をUTCに正規化したtime.struct_timeとして返すため、
        # 各フィールドをそのままUTCとして扱ってからJSTへ変換する。
        dt_utc = datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return "-"
    dt_jst = dt_utc.astimezone(JST)
    return dt_jst.strftime("%m/%d %H:%M")


def fetch_keyword_news(keyword, retries=3):
    """指定キーワードのGoogle News RSSを取得する。
    2026-08-28、OSHI_KEYWORDSが21件に増えたことで1回の実行が21回連続で
    Google News RSSを叩くことになり、実際に大半のキーワードで503(レート
    制限)が発生し配信が空振りする事例を確認した。リクエスト間隔と
    リトライ待機を伸ばし、リトライ回数も増やして緩和する。
    """
    q = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"
    time.sleep(4.0)

    resp = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] Google News RSS取得に失敗(試行{attempt + 1}/{retries})({keyword}): {err}")
            resp = None
            # 試行を重ねるごとに待機を伸ばす(10秒→20秒)。503は数秒の
            # リトライでは解消しないことが実データで確認できたため。
            time.sleep(10.0 * (attempt + 1))

    if resp is None:
        print(f"[ERROR] Google News RSS取得に失敗しました({keyword})")
        return []

    feed = feedparser.parse(resp.content)
    return [
        {"title": e.title, "url": e.link, "published": format_published_jst(e)}
        for e in feed.entries[:ITEMS_PER_KEYWORD]
    ]


def extract_event_dates(title, now):
    """記事タイトルから開催日程(開始日・終了日)を抽出する。
    明確なパターン(年月日表記、または全角括弧【】付きの短縮表記)に一致しない場合は
    Noneを返す(一般ニュース記事を誤って拾わないよう、安全側に倒してスキップする)。
    戻り値: (start_date, end_date) の datetime.date タプル、またはNone。
    """
    m = EVENT_DATE_RANGE_RE.search(title)
    if m:
        y1 = int(m.group("y1"))
        y2 = int(m.group("y2")) if m.group("y2") else y1
        try:
            start = datetime.date(y1, int(m.group("m1")), int(m.group("d1")))
            end = datetime.date(y2, int(m.group("m2")), int(m.group("d2")))
        except ValueError:
            return None
        return (start, end) if end >= start else None

    m = EVENT_DATE_SINGLE_RE.search(title)
    if m:
        try:
            d = datetime.date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError:
            return None
        return d, d

    # ここから先は年を含まない短縮表記のため、当年として組み立てたうえで
    # 過去すぎる日付(去年のイベント記事等の誤検出)は除外する
    m = EVENT_DATE_BRACKET_RANGE_RE.search(title)
    if m:
        year = now.year
        try:
            start = datetime.date(year, int(m.group("m")), int(m.group("d1")))
            end = datetime.date(year, int(m.group("m")), int(m.group("d2")))
        except ValueError:
            return None
        if end < start or (now.date() - end).days > BRACKET_DATE_MAX_PAST_DAYS:
            return None
        return start, end

    m = EVENT_DATE_BRACKET_SINGLE_RE.search(title)
    if m:
        year = now.year
        try:
            d = datetime.date(year, int(m.group("m")), int(m.group("d")))
        except ValueError:
            return None
        if (now.date() - d).days > BRACKET_DATE_MAX_PAST_DAYS:
            return None
        return d, d

    return None


def build_calendar_url(event_title, article_url, start_date, end_date):
    """Googleカレンダーのワンクリック登録用URLを組み立てる(終日イベント)。
    Googleカレンダーの終日イベントは終了日を「排他的(実際の最終日の翌日)」で
    指定する仕様のため、end_dateに1日加算する。
    """
    exclusive_end = end_date + datetime.timedelta(days=1)
    params = {
        "action": "TEMPLATE",
        "text": event_title,
        "dates": f"{start_date.strftime('%Y%m%d')}/{exclusive_end.strftime('%Y%m%d')}",
        "details": article_url,
    }
    return "https://calendar.google.com/calendar/render?" + urllib.parse.urlencode(params)


EVENT_REMINDER_DAYS = 14


def build_reminder_calendar_url(event_title, article_url, end_date):
    """終了日(単発イベントならその開催日)のEVENT_REMINDER_DAYS日前を1日だけの
    終日イベントとして登録するGoogleカレンダーURLを組み立てる。
    """
    reminder_date = end_date - datetime.timedelta(days=EVENT_REMINDER_DAYS)
    return build_calendar_url(
        f"【終了まであと{EVENT_REMINDER_DAYS}日】{event_title}",
        article_url, reminder_date, reminder_date,
    )


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
                lines.append(f"- [{item['title']}](<{item['url']}>) `[{item['published']}]`")
                event_dates = extract_event_dates(item["title"], now)
                if event_dates:
                    start, end = event_dates
                    # カレンダーURLのtextパラメータに記事タイトル全文を入れると
                    # URLが極端に長くなり、カレンダーリンク行が単独で
                    # Discordの2000文字制限を超えてHTTP 400になる事例が
                    # 実際に発生した(2026-08-28)。イベント名は短縮する。
                    event_title = f"{keyword} {item['title']}"[:60]
                    calendar_url = build_calendar_url(event_title, item["url"], start, end)
                    reminder_url = build_reminder_calendar_url(event_title, item["url"], end)
                    # 2つのリンクを1行にまとめず分けることで、どちらか1本が
                    # 長くなっても1行あたりの文字数を抑える(chunk_messageは
                    # 行単位でしか分割できないため)。
                    lines.append(f"  - 📅 [カレンダーに追加](<{calendar_url}>)")
                    lines.append(f"  - ⏰ [終了{EVENT_REMINDER_DAYS}日前リマインダー追加](<{reminder_url}>)")
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

    now = datetime.datetime.now(JST)
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
