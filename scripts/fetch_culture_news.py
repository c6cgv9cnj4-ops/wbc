# -*- coding: utf-8 -*-
"""
高純度カルチャー・テック配信スクリプト (#webhook_news 向け)

構成(優先順):
  ※長期国債先物(10年国債先物)は2026-08-28、市況データを#webhook_marketに
    一元化する方針により fetch_news.py へ移管した。このファイルには
    実装しない。
  1. 【特枠】林信行(Nobi Hayashi)氏の最新記事
     https://nobi.com/rss2.xml (本人公式サイトのRSS。実データで
     dc:creator="Nobuyuki Hayashi　　　林信行"であることを確認済み)
     ※note.com/nobi は同姓同名の別人(Nobuko Masui)のアカウントであり、
       誤って使用しないよう明記しておく。
  3. Apple/シリコンバレー、珈琲(関東地方限定)
     2026-09-05変更: Yahoo!ニュース転載記事の混入を防ぐため、Google News
     検索経由の取得をやめ、一次配信・専門メディア自身のRSSを直接購読する
     方式に切り替えた(実データで配信元が本当にYahoo!ニュースでないこと・
     直近数日以内に更新されていることを確認済み)。
       - Apple/シリコンバレー: ITmedia速報(rss.itmedia.co.jp) + GIGAZINE
         (gigazine.net)。どちらも一般テック系フィードのため、Gemini API で
         「Apple製品/デザイン、またはシリコンバレー企業の技術戦略」に
         直接関連する記事だけを抽出する。
       - 珈琲(関東): コーヒーステーション(coffee-station.jp) + PostCoffee
         マガジン(postcoffee.co/magazine)。珈琲専門メディア自身のRSSのため
         Yahoo!転載は原理的に混入しない。「器・工芸」を対象外にし、関東地方
         (東京・埼玉・神奈川・千葉・茨城・栃木・群馬)に関連する情報のみに
         絞り込む方針(2026-08-26導入)は継続する。関東限定の判定は記事
         タイトルのテキストのみを根拠にGeminiが行う(RSSは本文を含まない
         ため、タイトルに地域名が出てこない記事は誤って除外される可能性が
         ある)。
  4. 展覧会・美術展・写真展(関東地方限定、Googleカレンダー連携リンク付き)
     Google News RSS検索を情報源とし、Geminiで見出しから「展示名・会場・
     開始日・終了日」を構造化抽出する(会期が読み取れない記事は除外)。
     抽出できた各展示について、Googleカレンダーの公式クイックリンク
     (calendar.google.com/calendar/render、GAS等の追加サーバー不要で
     ワンクリックで登録画面が開く)を2種類生成する:
       a) その展示自体を会期の期間で登録するリンク
       b) 終了日の14日前を1日だけのリマインダーとして登録するリンク
     通常のDiscord Incoming Webhookではインタラクティブな「ボタンUI」
     (Message Components)は送信できないため、クリック可能な
     テキストリンクとして実装している。

  ※個人の「推し」キーワード追跡(バドミントン選手・アニメ/漫画・作家等)は
    専用の #推し チャンネル向けスクリプト(fetch_oshi_news.py)側で扱う。
    このファイルには実装しない(2026-08-26、配信先を分離する方針に変更)。

環境変数:
  DISCORD_WEBHOOK_NEWS (必須。既存のfetch_news.pyと同じWebhookを共用する)
  GEMINI_API_KEY (必須。記事の質フィルタリング用)
"""
import datetime
import json
import os
import re
import sys

import feedparser
import requests
from bs4 import BeautifulSoup

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "culture_news_seen.json")
STATE_RETENTION_DAYS = 14
REQUEST_TIMEOUT = 15
GEMINI_MODEL_NAME = "gemini-3.6-flash"

NOBI_RSS_URL = "https://nobi.com/rss2.xml"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 2026-09-05: Google News検索(=結果の多くがYahoo!ニュース転載やPR-TIMES系に
# 偏る)をやめ、専門メディア自身のRSSを直接購読する方式に変更した。
# 各URLは実データで「Yahoo!ニュースでないこと」「直近更新されていること」を
# 個別に確認済み(コーヒーステーション/PostCoffeeマガジンは2026-09-03〜04付、
# ITmedia/GIGAZINEは取得当日付の記事が存在することを確認)。
TOPIC_SOURCES = {
    "🍎 Apple・シリコンバレー": [
        "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml",
        "https://gigazine.net/news/rss_2.0/",
    ],
    # 珈琲のみに特化(器・工芸は対象外)。関東地方(東京・埼玉・神奈川・千葉・茨城・
    # 栃木・群馬)関連かどうかはGeminiによるタイトルベースの判定で絞り込む
    # (build_topic_embeds -> filter_quality_articles の extra_instruction 参照)。
    "☕ 珈琲（関東）": [
        "https://coffee-station.jp/feed/",
        "https://postcoffee.co/magazine/feed/",
    ],
}

KANTO_INSTRUCTION = (
    "さらに、これらの記事は「関東地方(東京都・埼玉県・神奈川県・千葉県・茨城県・"
    "栃木県・群馬県)」に関連するものだけを選んでください。九州・沖縄・関西など"
    "他地域の店舗・イベント・ニュースは、たとえ記名記事で専門性が高くても除外して"
    "ください。タイトルから地域が判断できない場合は含めないでください(安全側に"
    "倒してスキップする)。"
)

APPLE_SV_INSTRUCTION = (
    "さらに、これらの記事は「Appleの製品・デザイン思想」または「シリコンバレー"
    "企業の技術戦略・イノベーション」に直接関連するものだけを選んでください。"
    "配信元(ITmedia/GIGAZINE)はApple専門メディアではなく一般テック速報のため、"
    "セール情報・一般的なガジェットレビュー・ゲーム・アプリ紹介など、Apple/"
    "シリコンバレーと直接関係のない記事は除外してください。"
)

# 展覧会・美術展・写真展(関東地方限定)。検索クエリは既存の珈琲枠と同じ発想で、
# タイトルに「開催」等が含まれる会期情報を持つ記事を拾いやすくしている。
EXHIBITION_QUERIES = [
    "美術展 開催 東京", "写真展 開催 東京", "展覧会 開催 都内",
]
EXHIBITION_REMINDER_DAYS = 14
GOOGLE_CALENDAR_RENDER_URL = "https://calendar.google.com/calendar/render"

COLOR_NOBI = 0xED8936
COLOR_CULTURE = 0x38A169
COLOR_EXHIBITION = 0xB83280


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


def mark_seen(state, url, now):
    state.setdefault("seen_urls", {})[url] = now.isoformat()


def is_seen(state, url):
    return url in state.get("seen_urls", {})


# ============================================================
# 1. 林信行氏の最新記事(特枠)
# ============================================================

def fetch_nobi_articles(state, now, limit=3):
    try:
        feed = feedparser.parse(NOBI_RSS_URL)
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] 林信行氏RSSの取得に失敗しました: {err}")
        return []

    new_items = []
    for entry in feed.entries[:limit]:
        url = entry.get("link")
        if not url or is_seen(state, url):
            continue
        new_items.append({"title": entry.get("title", ""), "url": url})
        mark_seen(state, url, now)
    return new_items


def build_nobi_embed(articles):
    if not articles:
        return None
    lines = [f"- [{a['title']}]({a['url']})" for a in articles]
    return {
        "title": "✍️ 林信行(Nobi Hayashi)氏の最新記事",
        "description": "\n".join(lines),
        "color": COLOR_NOBI,
    }


# ============================================================
# 2(exhibition用). Google News RSS検索ヘルパー(展覧会枠が引き続き使用)
# ============================================================

def fetch_topic_rss(query, retries=2):
    import time
    import urllib.parse
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"
    # 短時間に連続でGoogle News検索を叩くとレート制限(503)されることを
    # 実際のGitHub Actions実行で確認したため、リクエスト間隔を空け、
    # 503時は一度だけリトライする。
    time.sleep(2.0)
    resp = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] Google News RSS取得に失敗(試行{attempt + 1}/{retries})({query}): {err}")
            resp = None
            time.sleep(5.0)
    if resp is None:
        print(f"[ERROR] Google News RSS取得に失敗しました({query})")
        return []
    feed = feedparser.parse(resp.content)
    return [{"title": e.title, "url": e.link} for e in feed.entries]


# ============================================================
# 3. Apple/シリコンバレー/珈琲(専門メディア直接RSS + Gemini質フィルタ)
# ============================================================

def fetch_direct_rss(feed_url, limit=15):
    """専門メディア自身のRSSフィードを直接取得する(Google News検索を
    経由しないため、Yahoo!ニュース転載記事が混入する余地がない)。"""
    try:
        resp = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] 専門メディアRSS取得に失敗しました({feed_url}): {err}")
        return []
    feed = feedparser.parse(resp.content)
    return [
        {"title": e.title, "url": e.link}
        for e in feed.entries[:limit]
        if e.get("link") and e.get("title")
    ]


def filter_quality_articles(client, candidates, max_items=3, extra_instruction=None):
    """Geminiで「記名記事・専門性が高い・煽りでない」もののみ残す。
    extra_instruction を指定すると、トピックごとの追加条件(地域限定等)を
    プロンプトに追加できる。
    API呼び出し自体が失敗した場合は、安全側(何も表示しない)に倒す
    (低品質な記事を誤って通すより、今回は0件の方が実害が小さいため)。
    """
    if not candidates:
        return []

    titles_text = "\n".join(f"{i}. {c['title']}" for i, c in enumerate(candidates))
    extra_text = f"\n{extra_instruction}\n" if extra_instruction else ""
    prompt = f"""以下は複数のニュース記事タイトルのリストです。それぞれについて、
「執筆者の顔が見える記名記事・専門性の高い読み物・単なる商品告知やコピペではない
本質的な内容」と判断できるものだけを選んでください。煽り見出しや、内容の薄い
プレスリリース系の記事は除外してください。
{extra_text}
{titles_text}

選んだ記事の番号(0始まり)だけをJSON配列で出力してください(例: [0, 3])。
自信が持てない場合は含めないでください。説明文は不要です。"""

    try:
        from google import genai
        resp = client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt)
        text = resp.text.strip()
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        indices = json.loads(text)
        selected = [candidates[i] for i in indices if isinstance(i, int) and 0 <= i < len(candidates)]
        return selected[:max_items]
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] Gemini質フィルタに失敗したため、このトピックは0件扱いにします: {err}")
        return []


def build_topic_embeds(client, state, now):
    embeds = []
    for topic_label, feed_urls in TOPIC_SOURCES.items():
        candidates = []
        for feed_url in feed_urls:
            for item in fetch_direct_rss(feed_url):
                if not is_seen(state, item["url"]):
                    candidates.append(item)
        # 重複URL除去
        seen_in_batch = set()
        uniq_candidates = []
        for c in candidates:
            if c["url"] not in seen_in_batch:
                seen_in_batch.add(c["url"])
                uniq_candidates.append(c)

        if topic_label == "☕ 珈琲（関東）":
            extra_instruction = KANTO_INSTRUCTION
        elif topic_label == "🍎 Apple・シリコンバレー":
            extra_instruction = APPLE_SV_INSTRUCTION
        else:
            extra_instruction = None
        selected = filter_quality_articles(client, uniq_candidates, extra_instruction=extra_instruction)
        if not selected:
            continue
        lines = [f"- [{a['title']}]({a['url']})" for a in selected]
        embeds.append({
            "title": topic_label,
            "description": "\n".join(lines),
            "color": COLOR_CULTURE,
        })
        for a in selected:
            mark_seen(state, a["url"], now)
    return embeds


# ============================================================
# 4. 展覧会・美術展・写真展(Gemini構造化抽出 + Googleカレンダー連携)
# ============================================================

def parse_iso_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return None


def build_google_calendar_url(title, start_date, end_date_exclusive, location=None, details=None):
    """Googleカレンダーの「予定を追加」クイックリンク(公式URLスキーム)を
    組み立てる。GASや追加サーバーは不要で、リンクを開くだけで登録画面が
    表示される。終日予定のため、end_date_exclusiveは最終日の翌日を渡すこと
    (Googleカレンダーの終日予定は終了日が排他的表記のため)。
    """
    import urllib.parse
    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": f"{start_date.strftime('%Y%m%d')}/{end_date_exclusive.strftime('%Y%m%d')}",
    }
    if location:
        params["location"] = location
    if details:
        params["details"] = details
    return f"{GOOGLE_CALENDAR_RENDER_URL}?{urllib.parse.urlencode(params)}"


def fetch_candidates_for_queries(queries, state):
    """複数クエリでGoogle News RSSを検索し、URL重複を除いた未送信候補を返す。"""
    candidates = []
    for q in queries:
        for item in fetch_topic_rss(q):
            if not is_seen(state, item["url"]):
                candidates.append(item)

    seen_in_batch = set()
    uniq_candidates = []
    for c in candidates:
        if c["url"] not in seen_in_batch:
            seen_in_batch.add(c["url"])
            uniq_candidates.append(c)
    return uniq_candidates


def extract_exhibitions_via_gemini(client, candidates, region_instruction=KANTO_INSTRUCTION):
    """候補記事タイトルから、具体的な1つの展覧会/美術展/写真展/イベント/公演の
    開催情報(催事名・会場・開始日・終了日)を構造化抽出する。会期・開催日が
    読み取れない記事(感想記事、過去の回顧記事、チケット販売告知のみ等)は除外する。
    region_instructionにNoneを渡すと地域限定を適用しない(全国対象)。
    API呼び出し自体が失敗した場合は安全側(0件)に倒す。
    """
    if not candidates:
        return []

    titles_text = "\n".join(f"{i}. {c['title']}" for i, c in enumerate(candidates))
    region_text = f"\n{region_instruction}\n" if region_instruction else ""
    prompt = f"""以下は展覧会・美術展・写真展・イベント・公演に関連する可能性がある
ニュース見出しのリストです。それぞれについて、具体的な1つの展覧会/美術展/
写真展/イベント/公演の開催情報(会期・開催日が分かるもの)を報じているかを
判定してください。単なる感想記事、過去の回顧記事、チケット販売開始のみを
報じ会期・開催日に触れていない記事は除外してください。
{region_text}
該当するものだけ、以下の形式のJSON配列で出力してください:
[{{"index": 0, "exhibition_name": "催事名", "venue": "会場名(不明ならnull)",
   "start_date": "YYYY-MM-DD(不明ならnull)", "end_date": "YYYY-MM-DD(不明ならnull)"}}]

見出しに明記されていない情報は絶対に推測せず、必ずnullにしてください。
年が明記されていない日付は記事の文脈(発行日等)から妥当な年を判断し、
それでも判断できない場合はnullにしてください。トークイベント・公演等、
終了日という概念が無い単発の催事は、開催日をstart_dateとend_dateの両方に
入れてください。該当する見出しが無ければ空配列[]を返してください。
説明文は不要です。

見出し一覧:
{titles_text}"""

    try:
        resp = client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt)
        text = resp.text.strip()
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(text)
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] Gemini展覧会/イベント情報抽出に失敗したため、このバッチは0件扱いにします: {err}")
        return []

    results = []
    for item in parsed:
        idx = item.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
            continue
        results.append({
            "url": candidates[idx]["url"],
            "exhibition_name": item.get("exhibition_name"),
            "venue": item.get("venue"),
            "start_date": item.get("start_date"),
            "end_date": item.get("end_date"),
        })
    return results


def build_exhibition_embeds_from_candidates(client, candidates, state, now, region_instruction=KANTO_INSTRUCTION):
    """candidatesのうちGeminiが展覧会/イベントと判定したものをEmbed化する。
    戻り値は (embeds, consumed_urls) のタプル。consumed_urlsはGeminiが
    展覧会/イベント候補として選んだ(=結果的にEmbed化されなかったものも含む)
    URL集合で、呼び出し側が「一般ニュースとしての二重掲載」を避けるために使う。
    """
    extracted = extract_exhibitions_via_gemini(client, candidates, region_instruction=region_instruction)

    embeds = []
    consumed_urls = set()
    for ex in extracted:
        mark_seen(state, ex["url"], now)
        consumed_urls.add(ex["url"])

        if not ex["exhibition_name"]:
            continue
        end_date = parse_iso_date(ex["end_date"])
        if not end_date:
            # 終了日(単発イベントなら開催日)が取れない場合は、カレンダー登録・
            # リマインダー計算ができないためスキップする(推測で埋めない)。
            continue
        start_date = parse_iso_date(ex["start_date"]) or end_date

        cal_url = build_google_calendar_url(
            ex["exhibition_name"], start_date, end_date + datetime.timedelta(days=1),
            location=ex["venue"], details=ex["url"],
        )

        reminder_date = end_date - datetime.timedelta(days=EXHIBITION_REMINDER_DAYS)
        reminder_url = build_google_calendar_url(
            f"【終了まであと{EXHIBITION_REMINDER_DAYS}日】{ex['exhibition_name']}",
            reminder_date, reminder_date + datetime.timedelta(days=1),
            location=ex["venue"], details=ex["url"],
        )

        lines = []
        if ex["venue"]:
            lines.append(f"📍 {ex['venue']}")
        lines.append(f"🗓️ 会期: {start_date.isoformat()} 〜 {end_date.isoformat()}")
        lines.append("")
        lines.append(
            f"[📅 Googleカレンダーに追加]({cal_url}) ｜ "
            f"[⏰ 終了{EXHIBITION_REMINDER_DAYS}日前リマインダー追加]({reminder_url})"
        )
        lines.append(f"[記事を見る]({ex['url']})")

        embeds.append({
            "title": f"🖼️ {ex['exhibition_name']}"[:256],
            "description": "\n".join(lines),
            "color": COLOR_EXHIBITION,
        })
    return embeds, consumed_urls


# ============================================================
# Discord送信
# ============================================================

DISCORD_EMBED_TOTAL_CHAR_LIMIT = 6000  # Discord公式の1メッセージあたりEmbed合計文字数上限
DISCORD_EMBED_CHAR_SAFETY_MARGIN = 200  # 概算誤差に対する安全マージン


def _embed_char_count(embed):
    count = len(embed.get("title", "")) + len(embed.get("description", ""))
    footer = embed.get("footer")
    if footer:
        count += len(footer.get("text", ""))
    for field in embed.get("fields", []):
        count += len(field.get("name", "")) + len(field.get("value", ""))
    return count


def _batch_embeds(embeds, batch_size=10):
    """Discordの制限(1メッセージあたりEmbed最大10件・合計文字数6000)の
    両方を満たすようEmbedをバッチに分割する。展覧会/推しウォッチセクション
    追加により合計文字数超過(HTTP 400)が実際に発生したための対応。
    """
    batches = []
    current, current_chars = [], 0
    for embed in embeds:
        chars = _embed_char_count(embed)
        if current and (
            len(current) >= batch_size
            or current_chars + chars > DISCORD_EMBED_TOTAL_CHAR_LIMIT - DISCORD_EMBED_CHAR_SAFETY_MARGIN
        ):
            batches.append(current)
            current, current_chars = [], 0
        current.append(embed)
        current_chars += chars
    if current:
        batches.append(current)
    return batches


def send_embeds_to_discord(webhook_url, embeds, batch_size=10):
    if not webhook_url:
        print("[ERROR] DISCORD_WEBHOOK_NEWS が設定されていないため送信をスキップします。")
        return False
    ok = True
    for batch in _batch_embeds(embeds, batch_size=batch_size):
        try:
            resp = requests.post(webhook_url, json={"embeds": batch}, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 300:
                print(f"[ERROR] Discord送信に失敗しました(HTTP {resp.status_code}): {resp.text}")
                print(f"[DEBUG] 失敗したバッチの内容: {json.dumps(batch, ensure_ascii=False)}")
                ok = False
            else:
                print(f"[OK] Discord送信成功(HTTP {resp.status_code}, {len(batch)}件)")
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] Discord送信中に例外が発生しました: {err}")
            ok = False
    return ok


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_NEWS")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_NEWS が設定されていません。")
        sys.exit(1)
    if not api_key:
        print("[ERROR] 環境変数 GEMINI_API_KEY が設定されていません。")
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=api_key)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    state = load_seen_state()
    state = prune_old_entries(state, now)

    embeds = []

    nobi_articles = fetch_nobi_articles(state, now)
    nobi_embed = build_nobi_embed(nobi_articles)
    if nobi_embed:
        embeds.append(nobi_embed)
    else:
        print("[INFO] 林信行氏の新着記事はありませんでした。")

    topic_embeds = build_topic_embeds(client, state, now)
    embeds.extend(topic_embeds)

    exhibition_candidates = fetch_candidates_for_queries(EXHIBITION_QUERIES, state)
    exhibition_embeds, _ = build_exhibition_embeds_from_candidates(
        client, exhibition_candidates, state, now, region_instruction=KANTO_INSTRUCTION
    )
    embeds.extend(exhibition_embeds)

    had_error = False
    if embeds:
        print(f"=== 配信内容: {len(embeds)}件のEmbed ===")
        if not send_embeds_to_discord(webhook, embeds):
            had_error = True
    else:
        print("[INFO] 配信対象がありませんでした。")

    save_seen_state(state)

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
