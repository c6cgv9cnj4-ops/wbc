# -*- coding: utf-8 -*-
"""
fetch_market_intelligence.py — 市場朝夕サマリー（日経CNBC動画の代替・2026-10-12新設）

一次資料と市場数値をPythonで構造化して集約し、Gemini は「重要材料がある回だけ」1回呼ぶ。

  1. 市場数値: yfinance で日経平均(^N225)・TOPIX(1306.T)・ドル円(JPY=X)・米10年債(^TNX)の終値と前日比。
  2. 一次材料: 日本銀行・JPX の公式RSS(財務省はRSS URLを確認できていないため未接続・設定で追加可能)。
     タイトルに重要キーワード(金融政策・金利・為替・介入・総裁・臨時 等)を含むものを「重要材料」とする。
  3. ゼロ枠切替: 未配信の重要材料が無ければ Gemini を呼ばず、「市場数値＋新着リンク」の軽量Embedで完了。
  4. Gemini分析: 重要材料がある時だけ1回呼ぶ(重要材料最大5件・波及経路(株/為替/金利)・注目イベント)。
     429等の失敗時は集計済みの「市場数値＋重要材料リンク」でDiscord配信を完遂する(フォールバック)。
  5. 既読管理: 配信済みのリンクを state/market_intelligence_state.json に記録し、同じ材料を二度送らない。
     週末(土日)は配信しない(平日朝夕の運用)。

使い方:
  python scripts/fetch_market_intelligence.py             # 通常運用(DISCORD_WEBHOOK_MARKET へ送信)
  python scripts/fetch_market_intelligence.py --dry-run   # 送信せず Embed を標準出力

環境変数:
  DISCORD_WEBHOOK_MARKET (必須・送信時)
  GEMINI_API_KEY         (任意。無ければ重要材料があってもリンク配信にフォールバック)

依存: yfinance, feedparser, requests, google-genai(requirements.txt / 併用)
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import sys
import time

import feedparser
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atomic_io  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))
STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "market_intelligence_state.json")
STATE_RETENTION_DAYS = 30
MAX_IMPORTANT_FOR_GEMINI = 5
MAX_LINKS_IN_EMBED = 5
REQUEST_TIMEOUT = 20
RSS_FETCH_RETRIES = 3
RSS_RETRY_BASE_DELAY = 3.0
GEMINI_MODEL_NAME = "gemini-3.6-flash"
COLOR_MARKET = 0x2E86C1
EMBED_FIELD_LIMIT = 1000  # Discord の field value 上限(1024)に余裕を持たせる

# 市場数値: (Yahoo!Finance シンボル, 表示名)
TICKERS = [
    ("^N225", "日経平均"),
    ("1306.T", "TOPIX ETF (1306)"),   # 1306 は TOPIX連動ETF。指数そのものではないため表記を明示する
    ("JPY=X", "ドル円"),
    ("^TNX", "米10年債利回り"),
]
# ^TNX は現行データでは既にパーセント値(例: 5.244 = 5.244%)。旧来の指数表記(利回り×10。例: 42.5 = 4.25%)が
# 届いた場合に備え、この値を超えるものだけ 10 で割る。通常の利回りは 0〜20% の範囲に収まる前提。
TNX_SCALE_THRESHOLD = 20.0

# 一次資料RSS: (表示名, URL)。URLが None の資料は未接続(=取得対象外として明示する)
FEED_SOURCES = [
    ("日本銀行", "https://www.boj.or.jp/rss/whatsnew.xml"),
    ("JPX", "https://www.jpx.co.jp/rss/jpx-news.xml"),
    ("JPX マーケット", "https://www.jpx.co.jp/rss/markets_news.xml"),
    ("財務省", None),  # 公式RSSのURLを確認できていない(2026-10-12時点)。確認後にここへ追加する
]

# 「重要(Gemini分析対象)」とする真の政策イベント。単語の単純部分一致はしない(「金利」単独は統計にも当たるため採用しない)
IMPORTANT_KEYWORDS = [
    "金融政策", "政策金利", "臨時会合", "決定会合", "総裁", "為替介入", "市場介入",
    "利上げ", "利下げ", "金融緩和", "金融引き締め",
]
# 定例の統計・日程・資料。含まれたら政策キーワードがあっても「重要」から除外し、Gemini を呼ばない(通常配信扱い)
EXCLUDE_KEYWORDS = ["貸出約定", "統計", "残高", "公表日程", "定例"]
# 新着とみなす発表の経過時間上限。初回実行で数日前の資料が一斉に流れるのを防ぐ
NEW_ITEM_MAX_AGE_HOURS = 48

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class FeedError(RuntimeError):
    """RSS取得の失敗(通信エラー・HTTPエラー・中身が空)。「新着0件」とは区別する。"""


# ─────────────────────────────────────────────────────────────
#  状態管理（既読リンク）
# ─────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    atomic_io.write_json_atomic(STATE_PATH, state, ensure_ascii=False, indent=2)


def prune_seen(state, now):
    """既読リンク(state['seen'])を保持期間で間引く。Gemini の待機状態など他のキーは触らない。"""
    cutoff = now - datetime.timedelta(days=STATE_RETENTION_DAYS)
    kept = {}
    for link, iso_ts in state.get("seen", {}).items():
        try:
            if datetime.datetime.fromisoformat(iso_ts) >= cutoff:
                kept[link] = iso_ts
        except (TypeError, ValueError):
            continue
    state["seen"] = kept
    return state


# ─────────────────────────────────────────────────────────────
#  市場数値
# ─────────────────────────────────────────────────────────────
def normalize_yield_pct(value):
    """利回りをパーセント表記に揃える。TNX_SCALE_THRESHOLD を超える値は旧来の指数表記(利回り×10)とみなして 10 で割る。
    例: 42.5 → 4.25、5.244 → 5.244(現行データはそのままパーセント)。"""
    return value / 10.0 if value > TNX_SCALE_THRESHOLD else value


def compute_quote(name, symbol, closes):
    """終値の列(古い→新しい)から最新終値と前日比を作る。2本未満は取得失敗扱い(値は入れない)。"""
    values = [float(c) for c in closes if c is not None and not math.isnan(float(c))]
    if symbol == "^TNX":
        values = [normalize_yield_pct(v) for v in values]
    if len(values) < 2:
        return {"name": name, "symbol": symbol, "close": None, "prev_close": None,
                "change": None, "change_pct": None, "error": "終値が不足"}
    last, prev = values[-1], values[-2]
    change = last - prev
    pct = change / prev * 100 if prev else 0.0
    return {"name": name, "symbol": symbol, "close": last, "prev_close": prev,
            "change": change, "change_pct": pct, "error": None}


def fetch_quotes(history_fn=None):
    """各銘柄の終値・前日比。1銘柄の失敗は他銘柄に影響させず error を入れる。"""
    if history_fn is None:
        import yfinance as yf

        def history_fn(symbol):
            return [float(c) for c in yf.Ticker(symbol).history(period="10d")["Close"].tolist()]
    out = []
    for symbol, name in TICKERS:
        try:
            out.append(compute_quote(name, symbol, history_fn(symbol)))
        except Exception as err:  # noqa: BLE001
            out.append({"name": name, "symbol": symbol, "close": None, "prev_close": None,
                        "change": None, "change_pct": None, "error": str(err)[:80]})
    return out


def fx_direction(change):
    """ドル円(JPY=X)の前日比の向き。値上がり=ドル高・円安、値下がり=円高。"""
    if change > 0:
        return "円安"
    if change < 0:
        return "円高"
    return "横ばい"


def format_quote_line(q):
    """表示行。取得失敗時はダミー値を入れず「取得失敗 / -」と明記する。"""
    if q.get("error") or q.get("close") is None:
        return f"**{q['name']}**: 取得失敗 / -（{q.get('error') or '終値なし'}）"
    sign = "+" if q["change"] >= 0 else ""
    if q["symbol"] == "^TNX":
        # 利回りは%で表示し、前日比は bp(0.01%)で表す
        return f"**{q['name']}**: {q['close']:.2f}%（前日比 {sign}{q['change'] * 100:.1f}bp）"
    if q["symbol"] == "JPY=X":
        return (f"**{q['name']}**: {q['close']:.2f}円（前日比 {sign}{q['change']:.2f}円・{q['change_pct']:+.2f}%、"
                f"{fx_direction(q['change'])}）")
    return f"**{q['name']}**: {q['close']:,.2f}（前日比 {sign}{q['change']:,.2f}、{sign}{q['change_pct']:.2f}%）"


# ─────────────────────────────────────────────────────────────
#  一次資料RSS
# ─────────────────────────────────────────────────────────────
def fetch_feed_bytes(url, retries=RSS_FETCH_RETRIES):
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.content
        except Exception as err:  # noqa: BLE001
            last_err = err
            print(f"[WARN] RSS取得失敗(試行{attempt + 1}/{retries}): {url} {err}")
            time.sleep(RSS_RETRY_BASE_DELAY * (attempt + 1))
    raise FeedError(f"RSS取得に失敗({url}): {last_err}")


def parse_feed(content, source):
    """RSS/RDF/Atom を項目リストにする。項目が無い正常応答は []、壊れた応答は FeedError。"""
    feed = feedparser.parse(content)
    if not feed.version:  # RSS/Atom として認識できなかった(HTMLのエラーページ等)
        raise FeedError(f"{source}: RSSを解釈できません")
    items = []
    seen_links = set()
    for e in feed.entries:
        link = (e.get("link") or "").strip()
        title = (e.get("title") or "").strip()
        if not link or not title or link in seen_links:
            continue
        seen_links.add(link)
        parsed = e.get("published_parsed") or e.get("updated_parsed")
        items.append({"source": source, "title": title, "link": link,
                      "published": format_published_jst(parsed), "published_dt": parsed_to_jst(parsed)})
    return items


def parsed_to_jst(parsed):
    """feedparser の time struct(UTC) → JST の datetime。不明なら None。"""
    if not parsed:
        return None
    try:
        return datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc).astimezone(JST)
    except (TypeError, ValueError):
        return None


def format_published_jst(parsed):
    dt = parsed_to_jst(parsed)
    return dt.strftime("%m/%d %H:%M") if dt else "-"


def fetch_all_feeds(sources=FEED_SOURCES, fetch=fetch_feed_bytes):
    """(items, failed)。failed は取得失敗・未接続の資料名のリスト。0件(正常)とは区別して通知する。"""
    items, failed = [], []
    for name, url in sources:
        if not url:
            failed.append(f"{name}（RSS未接続）")
            continue
        try:
            items.extend(parse_feed(fetch(url), name))
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] {name}: {err}")
            failed.append(name)
    return items, failed


def is_important(title):
    """真の政策イベントを含み、かつ定例統計・日程・資料ではないものだけ重要とする(除外ルールを優先)。"""
    if any(kw in title for kw in EXCLUDE_KEYWORDS):
        return False
    return any(kw in title for kw in IMPORTANT_KEYWORDS)


def split_new_items(items, seen, now=None):
    """未配信の項目を (重要材料, その他) に分ける。既読リンクは除き、now 指定時は直近 NEW_ITEM_MAX_AGE_HOURS 時間の発表だけを残す。
    発表日時が不明な項目は除外する(古い資料を新着として流さないため)。"""
    new = [i for i in items if i["link"] not in seen]
    if now is not None:
        cutoff = now - datetime.timedelta(hours=NEW_ITEM_MAX_AGE_HOURS)
        new = [i for i in new if i.get("published_dt") and i["published_dt"] >= cutoff]
    important = [i for i in new if is_important(i["title"])]
    others = [i for i in new if not is_important(i["title"])]
    return important, others


# ─────────────────────────────────────────────────────────────
#  Gemini（重要材料がある回だけ1回）
# ─────────────────────────────────────────────────────────────
def build_gemini_prompt(important, quotes):
    market = "\n".join(format_quote_line(q).replace("**", "") for q in quotes)
    materials = "\n".join(f"[{n}] {i['source']}: {i['title']}" for n, i in enumerate(important))
    return f"""以下は日本の金融市場の直近の数値と、日本銀行・JPXの公式発表のタイトル一覧です。

## 市場数値
{market}

## 重要材料（公式発表のタイトル）
{materials}

上記の内容だけを根拠に、次のJSONオブジェクトだけを出力してください（説明文・コードフェンス不要）。
{{"materials": [{{"title": "材料名", "point": "要点を1行で"}}],
 "transmission": {{"stocks": "株式への波及", "fx": "為替への波及", "rates": "金利への波及"}},
 "events": ["注目イベント(日付・内容)"]}}
・materials は重要なものから最大{MAX_IMPORTANT_FOR_GEMINI}件。
・本文に無い数値・銘柄名・日付は書かない(推測で埋めない)。推測は「【推測】」と明記する。
・events は発表タイトルから確実に読み取れるものだけを挙げ、無ければ空配列にする。"""


def parse_gemini_json(text):
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("JSONオブジェクトではありません")
    return data


def call_gemini(prompt, state):
    """Gemini を1回呼ぶ。待機・再試行は gemini_guard に任せる。失敗は例外のまま送出する。"""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が未設定です")
    from google import genai
    import gemini_guard
    client = genai.Client(api_key=api_key)
    guard = gemini_guard.Guard(state)
    resp = guard.call(lambda: client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt))
    return resp.text


# ─────────────────────────────────────────────────────────────
#  Embed 生成（Gemini の成否によらず形を揃える）
# ─────────────────────────────────────────────────────────────
def _clip(text, limit=EMBED_FIELD_LIMIT):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _link_lines(items):
    return "\n".join(f"・[{i['title'][:90]}](<{i['link']}>)（{i['source']} {i['published']}）" for i in items)


def build_market_embed(session, now, quotes, items, failed_sources, note=None, analysis=None):
    """市場サマリーのEmbed。analysis(dict)があればAI分析欄を付け、無ければリンク欄で代替する。
    items: 重要材料(分析なし時はリンクとして表示)。"""
    fields = [{"name": "市場数値（終値・前日比）", "value": _clip("\n".join(format_quote_line(q) for q in quotes)), "inline": False}]
    if analysis:
        mats = analysis.get("materials") or []
        mat_text = "\n".join(f"・**{m.get('title', '')}**: {m.get('point', '')}" for m in mats[:MAX_IMPORTANT_FOR_GEMINI]) or "（該当なし）"
        fields.append({"name": "重要材料（最大5件）", "value": _clip(mat_text), "inline": False})
        tr = analysis.get("transmission") or {}
        fields.append({"name": "市場への波及経路", "value": _clip(
            f"・株: {tr.get('stocks', '-')}\n・為替: {tr.get('fx', '-')}\n・金利: {tr.get('rates', '-')}"), "inline": False})
        events = analysis.get("events") or []
        fields.append({"name": "注目イベント", "value": _clip("\n".join(f"・{e}" for e in events) or "（確認できた予定なし）"), "inline": False})
    if items:
        fields.append({"name": "公式発表（リンク）" if analysis else "重要材料・新着（リンク）",
                       "value": _clip(_link_lines(items[:MAX_LINKS_IN_EMBED])), "inline": False})
    if failed_sources:
        fields.append({"name": "取得できなかった資料", "value": "・" + "・".join(failed_sources), "inline": False})
    desc = note or ("AI分析つき" if analysis else "市場数値と新着リンク")
    return {
        "title": f"📊 市場{session}のサマリー",
        "description": desc,
        "color": COLOR_MARKET,
        "fields": fields,
        "footer": {"text": f"{now.strftime('%m/%d %H:%M')} JST ｜ 出典: 日本銀行・JPX公式RSS / Yahoo!Finance（市場数値）"},
    }


def build_light_embed(session, now, quotes, others, failed_sources):
    """重要材料が無い回の軽量Embed(Gemini未使用)。"""
    return build_market_embed(session, now, quotes, others, failed_sources,
                              note="重要材料なし（市場数値と新着リンクのみ・AI分析は省略）")


def build_fallback_embed(session, now, quotes, important, failed_sources, reason):
    """Gemini失敗時のフォールバック。集計済みの数値と重要材料のリンクで完遂する。"""
    return build_market_embed(session, now, quotes, important, failed_sources,
                              note=f"AI分析は利用できませんでした（{reason}）。集計済みの数値と公式発表のリンクを配信します。")


def session_label(now):
    return "朝" if now.hour < 12 else "夕"


# ─────────────────────────────────────────────────────────────
#  実行本体
# ─────────────────────────────────────────────────────────────
def run(now, state, *, quotes_fn, feeds_fn, llm_fn, send_fn=None, dry_run=False):
    """1回分の配信処理。依存(市場数値・RSS・Gemini・Discord送信)は注入できるようにしてある。
    戻り値: (embed or None, 状態) — 送信できたら既読を記録する。"""
    if now.weekday() >= 5:
        print("[INFO] 週末のため配信しません(平日朝夕の運用)。")
        return None
    session = session_label(now)
    prune_seen(state, now)
    seen = state["seen"]

    quotes = quotes_fn()
    items, failed = feeds_fn()
    important, others = split_new_items(items, seen, now)
    print(f"[INFO] {session}: 市場数値{len(quotes)}件 / 新着資料{len(important) + len(others)}件"
          f"（重要{len(important)}件）/ 取得失敗資料: {failed or 'なし'}")

    if not important:
        # ゼロ枠: 重要材料が無ければ Gemini を呼ばず、数値＋新着リンクで完了
        embed = build_light_embed(session, now, quotes, others, failed)
        print("[INFO] 重要材料なし: Geminiは呼ばずに軽量Embedを作成します。")
    else:
        target = important[:MAX_IMPORTANT_FOR_GEMINI]
        try:
            analysis = parse_gemini_json(llm_fn(build_gemini_prompt(target, quotes)))
            embed = build_market_embed(session, now, quotes, target, failed, analysis=analysis)
            print("[INFO] Gemini分析: OK")
        except Exception as err:  # noqa: BLE001
            import news_alerts
            reason = news_alerts.short_error(err)
            print(f"[WARN] Gemini分析に失敗。リンク配信へフォールバック: {reason}")
            embed = build_fallback_embed(session, now, quotes, target, failed, reason)

    if dry_run:
        print(json.dumps(embed, ensure_ascii=False, indent=2))
        return embed

    ok = send_fn(embed) if send_fn else False
    if not ok:
        print("[WARN] Discord送信に失敗したため、既読記録を保存しません(次回再試行)。")
        return None
    stamp = now.isoformat()
    for i in important + others:
        seen[i["link"]] = stamp
    print("[INFO] Discord配信完了。")
    return embed


def send_embed(webhook_url, embed):
    try:
        resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 300:
            print(f"[ERROR] Discord送信失敗 HTTP{resp.status_code}: {resp.text[:300]}")
            return False
        return True
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Discord送信失敗: {err}")
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="送信せず Embed を標準出力")
    args = ap.parse_args(argv)

    now = datetime.datetime.now(JST)
    state = load_state()
    webhook = os.environ.get("DISCORD_WEBHOOK_MARKET", "").strip()
    if not args.dry_run and not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_MARKET が設定されていません。")
        return 1

    def send_fn(embed):
        return send_embed(webhook, embed)

    def feeds_fn():
        return fetch_all_feeds()

    result = run(now, state,
                 quotes_fn=fetch_quotes,
                 feeds_fn=feeds_fn,
                 llm_fn=lambda prompt: call_gemini(prompt, state),
                 send_fn=None if args.dry_run else send_fn,
                 dry_run=args.dry_run)
    if result is not None and not args.dry_run:
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
