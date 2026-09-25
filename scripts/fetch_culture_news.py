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
  3. Apple/シリコンバレー、珈琲
     2026-09-05変更: Yahoo!ニュース転載記事の混入を防ぐため、Google News
     検索経由の取得をやめ、一次配信・専門メディア自身のRSSを直接購読する
     方式に切り替えた(実データで配信元が本当にYahoo!ニュースでないこと・
     直近数日以内に更新されていることを確認済み)。
       - Apple/シリコンバレー: ITmedia速報(rss.itmedia.co.jp) + GIGAZINE
         (gigazine.net)。どちらも一般テック系フィードのため、Gemini API で
         「Apple製品/デザイン、またはシリコンバレー企業の技術戦略」に
         直接関連する記事だけを抽出する。
       - 珈琲: PostCoffeeマガジン(postcoffee.co/magazine)。珈琲専門メディア
         自身のRSSのためYahoo!転載は原理的に混入しない。
         (coffee-station.jpは一貫して403、afroaster.comは接続タイムアウトを
         本番GitHub Actions実行で確認したため不採用。TOPIC_SOURCES内の
         コメント参照。)
         関東地方限定の絞り込み(2026-08-26導入)は、この情報源が店舗開店の
         地域ニュースでなく全国向けカルチャー記事中心であるため2026-09-05に
         廃止し、「器・工芸」除外の質フィルタのみ継続する。
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
import unicodedata
import urllib.parse

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
    # 珈琲のみに特化(器・工芸は対象外)。
    # 2026-09-05: 情報源をGoogle検索(「新店オープン」等の地域名入りタイトルが
    # 前提)から珈琲専門メディア自身のRSSに変更したことに伴い、関東地方限定の
    # 絞り込みは廃止した。PostCoffeeマガジンは店舗開店の地域ニュースではなく
    # 全国向けの一般的な珈琲カルチャー記事(淹れ方・産地解説等)が中心で、
    # タイトルに地域名が出てくること自体がほぼ無いため、関東限定フィルタを
    # 維持すると実質ほぼ0件配信になってしまうことを実データ(本番GitHub
    # Actions実行)で確認したため。
    # ※他候補の検証結果(本番GitHub Actions実行で確認済み):
    #   - coffee-station.jp: 一貫して403(Cloudflare等のクラウドIPブロック
    #     とみられ、ヘッダー変更でも回避不可) → 不採用
    #   - afroaster.com(AFRO BLOG、個人焙煎士ブログ): 接続タイムアウト
    #     (小規模サイト特有のサーバー不安定とみられる) → 不採用
    #   現時点でPostCoffeeマガジン以外に安定して疎通する珈琲専門メディア/
    #   個人ブログのRSSが見つかっていないため、単独ソースで運用する。
    #   細川さんが把握している珈琲専門メディア・個人ブログがあれば追加候補
    #   として検証する。
    "☕ 珈琲": [
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

# 展覧会の判断材料リンク(2026-09-25追加)。優先順位:
#   p1 展覧会の公式サイト・公式ページ / p2 開催館・ギャラリーの公式ページ /
#   p3 主催者の公式ページ / p4 展覧会内容を確認できる信頼性の高いページ
# 候補URLはGemini(Google検索グラウンディング)に出させるが、AIの出力をそのまま
# 掲載することはせず、必ず実際にGETして「HTTP 200・HTML・本文に展示名と会場名を
# 含む」ことを確認できたものだけを採用する(verify_exhibition_page参照)。
LINK_TIER_LABELS = {
    "p1": "🔗 公式サイト",
    "p2": "🏛️ 会場ページ",
    "p3": "🔗 主催者ページ",
    "p4": "🔗 詳細ページ",
}
# p4(信頼できるページ)として認めるドメイン。これ以外はp4扱いで採用しない。
TRUSTED_MEDIA_DOMAINS = (
    "bijutsutecho.com", "tokyoartbeat.com", "artscape.jp", "museum.or.jp",
    "artagenda.jp", "prtimes.jp", "nikkei.com", "asahi.com", "yomiuri.co.jp",
    "mainichi.jp", "sankei.com", "nhk.or.jp", "jiji.com", "kyodonews.jp",
    "timeout.jp", "fashion-press.net", "walkerplus.com", "enjoytokyo.jp",
    "digicame-info.com", "dc.watch.impress.co.jp", "capa-camera.net",
)
# 公式ページ扱いしないドメイン(ニュース転載・SNS・検索リダイレクト等)
LINK_BLOCKED_DOMAINS = (
    "news.google.com", "vertexaisearch.cloud.google.com", "google.com",
    "yahoo.co.jp", "twitter.com", "x.com", "instagram.com", "facebook.com",
    "youtube.com", "tiktok.com", "wikipedia.org",
)
LINK_VERIFY_TIMEOUT = 8
LINK_MAX_CANDIDATES = 6

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

DIRECT_RSS_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def fetch_direct_rss(feed_url, limit=15, retries=2):
    """専門メディア自身のRSSフィードを直接取得する(Google News検索を
    経由しないため、Yahoo!ニュース転載記事が混入する余地がない)。
    一部サイトはCloudflare等でクラウド系IP(GitHub Actionsランナー含む)を
    ブロックしている場合があり、その場合は403のまま失敗する(ヘッダーでは
    回避不可。実際にGitHub Actions実行でcoffee-station.jpが403になることを
    確認済み)。失敗時はそのフィードだけ0件として扱い、処理は継続する。
    """
    resp = None
    for attempt in range(retries):
        try:
            resp = requests.get(feed_url, headers=DIRECT_RSS_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] 専門メディアRSS取得に失敗(試行{attempt + 1}/{retries})({feed_url}): {err}")
            resp = None
    if resp is None:
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

        if topic_label == "🍎 Apple・シリコンバレー":
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


def _normalize_for_match(text):
    """一致判定用の正規化(全角半角統一・空白/記号除去・小文字化)。"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\s　「」『』【】()（）\[\]<>〈〉《》\"'“”‘’・:：\-‐―—~〜～!！?？、。,.／/|]", "", text)


def _match_keys(name, min_len):
    """名前全体と、区切り記号で分割した各部分(min_len文字以上)を照合キーにする。
    例: 「〇〇展 ―光と影―」→「〇〇展光と影」「〇〇展」「光と影」"""
    keys = {_normalize_for_match(name)}
    for part in re.split(r"[\s　「」『』【】()（）\[\]〈〉《》:：\-‐―—~〜～|／/・]+", name or ""):
        norm = _normalize_for_match(part)
        if len(norm) >= min_len:
            keys.add(norm)
    # 「2026」等の数字だけのキーは無関係なページにも一致するため除外する
    return {k for k in keys if len(k) >= min_len and not k.isdigit()}


def _domain_of(url):
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _domain_in(domain, domains):
    return any(domain == d or domain.endswith("." + d) for d in domains)


def verify_exhibition_page(url, exhibition_name, venue):
    """候補URLを実際に開き、掲載可否を判定する。AIが出したURLを鵜呑みにしないための関門。
    採用条件: HTTP 200 / HTML / ブロック対象ドメインでない / 本文に展示名(の主要部分)を含む /
    会場名が判明している場合は会場名も含む。リダイレクトは追跡し、最終URLを採用する
    (グラウンディングのリダイレクトURLをそのまま掲載しないため)。
    戻り値: {"ok": bool, "final_url": str|None, "page_title": str, "reason": str}
    """
    result = {"ok": False, "final_url": None, "page_title": "", "reason": ""}
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=LINK_VERIFY_TIMEOUT,
                            allow_redirects=True)
    except Exception as err:  # noqa: BLE001
        result["reason"] = f"取得失敗({type(err).__name__})"
        return result
    result["final_url"] = resp.url
    if resp.status_code != 200:
        result["reason"] = f"HTTP {resp.status_code}"
        return result
    if "html" not in resp.headers.get("Content-Type", "").lower():
        result["reason"] = "HTMLでない"
        return result
    if _domain_in(_domain_of(resp.url), LINK_BLOCKED_DOMAINS):
        result["reason"] = f"対象外ドメイン({_domain_of(resp.url)})"
        return result

    resp.encoding = resp.apparent_encoding if resp.encoding in (None, "ISO-8859-1") else resp.encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    result["page_title"] = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    page_text = _normalize_for_match(result["page_title"] + soup.get_text(" "))

    if not any(k in page_text for k in _match_keys(exhibition_name, 4)):
        result["reason"] = "展示名が本文に無い"
        return result
    if venue and not any(k in page_text for k in _match_keys(venue, 3)):
        result["reason"] = "会場名が本文に無い"
        return result
    result["ok"] = True
    result["reason"] = "展示名・会場名一致" if venue else "展示名一致(会場不明)"
    return result


def search_exhibition_link_candidates(client, exhibition_name, venue, start_date, end_date):
    """Gemini(Google検索グラウンディング)で公式ページ等の候補URLを集める。
    ここで得たURLは「候補」に過ぎず、掲載前に必ずverify_exhibition_pageで検証する。
    戻り値: [{"url": str, "tier": "p1".."p4", "source": "model"|"grounding"}]
    """
    from google.genai import types

    prompt = f"""次の展覧会について、Google検索で実在するページを探してください。
展覧会名: {exhibition_name}
会場: {venue or "不明"}
会期: {start_date.isoformat()} 〜 {end_date.isoformat()}

以下の優先順位で、この展覧会の内容を確認できるページのURLを最大{LINK_MAX_CANDIDATES}件挙げてください。
p1: 展覧会の公式サイト・公式ページ(開催館サイト内の当該展覧会ページを含む)
p2: 開催美術館・ギャラリーの公式ページ(当該展覧会の情報が載っているもの)
p3: 主催者の公式ページ
p4: 展覧会内容を確認できる信頼性の高いメディアのページ

検索結果で実際に確認できたURLのみを挙げ、URLを推測で組み立てないでください。
次の形式のJSON配列のみを出力してください(説明文は不要):
[{{"url": "https://...", "tier": "p1"}}]"""

    resp = client.models.generate_content(
        model=GEMINI_MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )

    candidates = []
    text = (resp.text or "").strip()
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if match:
        try:
            for item in json.loads(match.group(0)):
                url, tier = item.get("url"), item.get("tier")
                if isinstance(url, str) and url.startswith("http") and tier in LINK_TIER_LABELS:
                    candidates.append({"url": url, "tier": tier, "source": "model"})
        except (json.JSONDecodeError, AttributeError):
            pass

    # グラウンディングの参照元(実際に検索でヒットしたページ)も候補に加える。
    # tierはAIの申告が無いためp4扱いとし、信頼ドメインでなければ後段で落とす。
    known = {c["url"] for c in candidates}
    try:
        for chunk in resp.candidates[0].grounding_metadata.grounding_chunks or []:
            uri = chunk.web.uri if chunk.web else None
            if uri and uri not in known:
                candidates.append({"url": uri, "tier": "p4", "source": "grounding"})
                known.add(uri)
    except (AttributeError, IndexError, TypeError):
        pass
    return candidates[: LINK_MAX_CANDIDATES * 2]


def find_verified_exhibition_links(client, exhibition_name, venue, start_date, end_date):
    """候補検索→実ページ検証→掲載リンク選定。
    選定ルール: 最優先tierの検証済みページを1本目(公式p1が確認できれば必ず1本目)。
    2本目は1本目と別ドメインかつ別tierで、判断材料が増える場合のみ付ける。
    戻り値: (links, checks)
      links  = [{"label": str, "url": str, "title": str, "tier": str}] (0〜2件)
      checks = 候補ごとの検証ログ(確認モードの表示用)
    失敗時は例外を投げず ([], checks) を返す(投稿自体は止めない)。
    """
    checks = []
    try:
        candidates = search_exhibition_link_candidates(client, exhibition_name, venue, start_date, end_date)
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] 公式ページ候補の検索に失敗しました({exhibition_name}): {err}")
        return [], checks

    verified = []
    seen_final = set()
    for c in candidates:
        v = verify_exhibition_page(c["url"], exhibition_name, venue)
        final_url = v["final_url"] or c["url"]
        tier = c["tier"]
        if v["ok"]:
            domain = _domain_of(final_url)
            if final_url in seen_final:
                v["ok"], v["reason"] = False, "重複"
            elif tier == "p4" and not _domain_in(domain, TRUSTED_MEDIA_DOMAINS):
                v["ok"], v["reason"] = False, f"{v['reason']}だがp4の信頼ドメイン外({domain})"
            elif tier != "p4" and _domain_in(domain, TRUSTED_MEDIA_DOMAINS):
                # メディア記事をAIが「公式」と申告した場合はp4に格下げする
                tier = "p4"
        checks.append({"candidate": c["url"], "source": c["source"], "tier": tier,
                       "final_url": final_url, "ok": v["ok"], "reason": v["reason"]})
        if v["ok"]:
            seen_final.add(final_url)
            verified.append({"url": final_url, "tier": tier, "title": v["page_title"]})

    verified.sort(key=lambda x: x["tier"])
    links = []
    if verified:
        links.append(verified[0])
        for v in verified[1:]:
            if v["tier"] != links[0]["tier"] and _domain_of(v["url"]) != _domain_of(links[0]["url"]):
                links.append(v)
                break
    return [dict(v, label=LINK_TIER_LABELS[v["tier"]]) for v in links], checks


def _link_text(link):
    title = re.sub(r"\s+", " ", link["title"] or "").strip()
    title = title.replace("[", "(").replace("]", ")")
    if not title:
        return _domain_of(link["url"])
    return title if len(title) <= 40 else title[:39] + "…"


def build_exhibition_embeds_from_candidates(client, candidates, state, now, region_instruction=KANTO_INSTRUCTION,
                                            report=None):
    """candidatesのうちGeminiが展覧会/イベントと判定したものをEmbed化する。
    戻り値は (embeds, consumed_urls) のタプル。consumed_urlsはGeminiが
    展覧会/イベント候補として選んだ(=結果的にEmbed化されなかったものも含む)
    URL集合で、呼び出し側が「一般ニュースとしての二重掲載」を避けるために使う。
    reportにlistを渡すと、展覧会ごとのリンク検証ログを追記する(確認モード用)。
    """
    extracted = extract_exhibitions_via_gemini(client, candidates, region_instruction=region_instruction)

    embeds = []
    consumed_urls = set()
    link_cache = {}  # 同一実行内で同じ展覧会を複数記事が報じた場合の再検索を避ける
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

        cache_key = (ex["exhibition_name"], ex["venue"])
        if cache_key not in link_cache:
            link_cache[cache_key] = find_verified_exhibition_links(
                client, ex["exhibition_name"], ex["venue"], start_date, end_date)
        links, checks = link_cache[cache_key]

        # カレンダーの詳細欄には、検証済みの公式等ページがあればそれを、無ければ記事URLを入れる
        details_url = links[0]["url"] if links else ex["url"]
        cal_url = build_google_calendar_url(
            ex["exhibition_name"], start_date, end_date + datetime.timedelta(days=1),
            location=ex["venue"], details=details_url,
        )

        reminder_date = end_date - datetime.timedelta(days=EXHIBITION_REMINDER_DAYS)
        reminder_url = build_google_calendar_url(
            f"【終了まであと{EXHIBITION_REMINDER_DAYS}日】{ex['exhibition_name']}",
            reminder_date, reminder_date + datetime.timedelta(days=1),
            location=ex["venue"], details=details_url,
        )

        lines = []
        if ex["venue"]:
            lines.append(f"📍 {ex['venue']}")
        lines.append(f"🗓️ 会期: {start_date.isoformat()} 〜 {end_date.isoformat()}")
        if links:
            for link in links:
                lines.append(f"{link['label']}: [{_link_text(link)}]({link['url']})")
        else:
            lines.append("🔗 公式ページ：自動確認できず")
        lines.append("")
        lines.append(
            f"[📅 Googleカレンダーに追加]({cal_url}) ｜ "
            f"[⏰ 終了{EXHIBITION_REMINDER_DAYS}日前リマインダー追加]({reminder_url})"
        )
        lines.append(f"[📰 記事を見る]({ex['url']})")

        if report is not None:
            report.append({"exhibition": ex, "start_date": start_date, "end_date": end_date,
                           "checks": checks, "links": links, "description": "\n".join(lines)})

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


def print_exhibition_report(report):
    """確認モード用: 展覧会ごとに抽出結果・候補URL・検証結果・最終リンクを表示する。"""
    print(f"\n===== 展覧会リンク検証レポート({len(report)}件) =====")
    for i, r in enumerate(report, 1):
        ex = r["exhibition"]
        print(f"\n--- [{i}] {ex['exhibition_name']}")
        print(f"  会場: {ex['venue'] or '(不明)'}")
        print(f"  会期: {r['start_date'].isoformat()} 〜 {r['end_date'].isoformat()}")
        print(f"  元記事: {ex['url']}")
        print(f"  検出URL({len(r['checks'])}件):")
        for c in r["checks"]:
            mark = "OK" if c["ok"] else "NG"
            shown = c["final_url"] if c["final_url"] == c["candidate"] else f"{c['candidate']} -> {c['final_url']}"
            print(f"    [{mark}] {c['tier']}/{c['source']} {shown}  ({c['reason']})")
        print("  Discordに出すリンク:")
        if r["links"]:
            for link in r["links"]:
                print(f"    {link['label']}: {link['url']}  「{link['title'][:50]}」")
        else:
            print("    🔗 公式ページ：自動確認できず")
        print("  --- 投稿本文プレビュー ---")
        for line in r["description"].split("\n"):
            print(f"  | {line}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Discordへ送信せず、展覧会枠のみ処理してリンク検証結果を表示する"
                             "(既送信記録は無視・保存しない)")
    args = parser.parse_args()
    dry_run = args.dry_run or os.environ.get("CULTURE_NEWS_DRY_RUN") == "true"

    webhook = os.environ.get("DISCORD_WEBHOOK_NEWS")
    api_key = os.environ.get("GEMINI_API_KEY")
    if dry_run:
        if not api_key:
            print("[ERROR] 環境変数 GEMINI_API_KEY が設定されていません。")
            sys.exit(1)
        from google import genai
        client = genai.Client(api_key=api_key)
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        print("[DRY-RUN] 確認モード: Discord送信・既送信記録の保存は行いません。")
        candidates = fetch_candidates_for_queries(EXHIBITION_QUERIES, {"seen_urls": {}})
        print(f"[DRY-RUN] 展覧会候補記事: {len(candidates)}件")
        report = []
        embeds, _ = build_exhibition_embeds_from_candidates(
            client, candidates, {"seen_urls": {}}, now, region_instruction=KANTO_INSTRUCTION, report=report)
        print_exhibition_report(report)
        with_links = sum(1 for r in report if r["links"])
        print(f"\n[DRY-RUN] Embed {len(embeds)}件 / 公式等リンク確認済み {with_links}件 / "
              f"自動確認できず {len(report) - with_links}件")
        return

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
