# -*- coding: utf-8 -*-
"""
週末マーケットニュース(#webhook_market・2026-09-29追加)

土日だけ、値動きの数値に加えて「なぜ動いているか」のニュースと、来週の重要経済イベントをまとめて送る。
平日の fetch_news.py(#webhook_market の随時配信)とは別処理・別 state で、04_Stocks(売買スコア・Tier・
B観測・非公開化スクリーナー)には一切接続しない。

版:
  sat(土曜8:00 JST) : 金曜の市場の値動き(日本株・米国株・為替・金利・原油・金・CME日経先物)＋金曜大引け以降の重要ニュース
  sun(日曜20:00 JST): 週末の重要ニュース＋市場状況(CMEは月曜朝まで休場のため金曜終値ベース)＋
                      来週の重要経済イベント(Forex Factory 週次JSON・USD/JPY・High/Medium・JST)＋月曜に見る材料

再利用: 値動き・ニュース取得・Discord送信は fetch_news.py、材料の集約・重要度は market_news_curation.py
(Gemini が使えない場合は既存どおり箇条書きに戻る)、異常通知は news_alerts.py。いずれも変更しない。

state: state/weekend_digest_seen.json(平日の state/news_seen.json とは分離)。送信に成功した記事だけ既送信にする。

使い方:
  python scripts/weekend_market_digest.py --edition sat|sun [--dry-run]
"""
import argparse
import datetime
import json
import os
import sys

import requests

import fetch_news as fn
import market_news_curation as mnc
import news_alerts

JST = datetime.timezone(datetime.timedelta(hours=9))
STATE_PATH = "state/weekend_digest_seen.json"
STATE_RETENTION_DAYS = 14
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_COUNTRIES = {"USD": "🇺🇸", "JPY": "🇯🇵"}
FF_IMPACTS = {"High": "🔴", "Medium": "🟡"}
WEEKDAYS_JA = "月火水木金土日"
# 英語の指標名のうち、よく出るものだけ日本語を添える(辞書に無いものは英語のまま)
# 部分一致なので、長く具体的な名前を先に並べる(例: ADP / Tokyo Core CPI / GDP Price Index)
FF_TITLE_JA = [
    ("ADP Non-Farm Employment Change", "ADP雇用統計"),
    ("Tokyo Core CPI", "東京都区部コアCPI"),
    ("GDP Price Index", "GDPデフレーター"),
    ("Non-Farm Employment Change", "米雇用統計(非農業部門雇用者数)"),
    ("Unemployment Rate", "失業率"),
    ("Average Hourly Earnings", "平均時給"),
    ("Core PCE Price Index", "コアPCE物価指数"),
    ("Core CPI", "コアCPI"),
    ("CPI", "消費者物価指数"),
    ("PPI", "生産者物価指数"),
    ("GDP", "GDP"),
    ("Retail Sales", "小売売上高"),
    ("ISM Manufacturing PMI", "ISM製造業景況指数"),
    ("ISM Services PMI", "ISM非製造業景況指数"),
    ("Federal Funds Rate", "FOMC政策金利"),
    ("FOMC Statement", "FOMC声明"),
    ("FOMC Meeting Minutes", "FOMC議事要旨"),
    ("FOMC Press Conference", "FOMC議長会見"),
    ("BOJ Policy Rate", "日銀政策金利"),
    ("Monetary Policy Statement", "日銀 金融政策決定"),
    ("Monetary Policy Meeting Minutes", "日銀 金融政策決定会合 議事要旨"),
    ("Tankan", "日銀短観"),
    ("Unemployment Claims", "新規失業保険申請件数"),
]


def now_jst():
    return datetime.datetime.now(JST)


# ── state(週末版専用)─────────────────────────────
def load_state(path=None):
    path = path or STATE_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state, path=None):
    path = path or STATE_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def prune_state(state, now):
    cutoff = (now - datetime.timedelta(days=STATE_RETENTION_DAYS)).isoformat()
    return {k: v for k, v in state.items() if k.startswith("_") or v >= cutoff}


# ── ニュース(期間で絞り、週末版の state で重複を除く)─────────────
def news_cutoff(edition, now):
    """sat: 金曜15:30 JST(大引け)以降 / sun: 金曜15:30 JST 以降(土曜版で送った分は state で除外)。"""
    friday = now.date() - datetime.timedelta(days=(now.weekday() - 4) % 7)
    return datetime.datetime.combine(friday, datetime.time(15, 30), JST)


def select_news(items, state, cutoff):
    """期間内(公開日時が取れないものは期間判定せず)かつ週末版で未送信の記事。"""
    out, seen_batch = [], set()
    for it in items:
        key = fn.normalize_url(it.get("url", ""))
        if not key or key in state or key in seen_batch:
            continue
        pub = it.get("published_at")
        if pub:
            try:
                if datetime.datetime.fromisoformat(pub) < cutoff:
                    continue
            except ValueError:
                pass
        seen_batch.add(key)
        out.append(it)
    return out


# ── 来週の経済イベント(Forex Factory)──────────────────
def fetch_ff_events():
    resp = requests.get(FF_CALENDAR_URL, headers={"User-Agent": fn.USER_AGENT}, timeout=fn.REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Forex Factory の応答がリストではありません")
    return data


def ja_title(title):
    for en, ja in FF_TITLE_JA:
        if en.lower() in title.lower():
            return f"{ja}（{title}）" if ja != title else title
    return title


def filter_events(events, now, days=8):
    """USD/JPY・High/Medium・[now, now+days) を JST に変換して時刻順に返す。"""
    out = []
    for ev in events:
        if ev.get("country") not in FF_COUNTRIES or ev.get("impact") not in FF_IMPACTS:
            continue
        try:
            at = datetime.datetime.fromisoformat(ev["date"]).astimezone(JST)
        except (KeyError, TypeError, ValueError):
            continue
        if now <= at < now + datetime.timedelta(days=days):
            out.append({**ev, "at_jst": at})
    return sorted(out, key=lambda e: e["at_jst"])


def render_event(ev):
    at = ev["at_jst"]
    extra = " ".join(x for x in (f"予想{ev['forecast']}" if ev.get("forecast") else "",
                                  f"前回{ev['previous']}" if ev.get("previous") else "") if x)
    return (f"- {at:%m/%d}({WEEKDAYS_JA[at.weekday()]}) {at:%H:%M} {FF_COUNTRIES[ev['country']]}"
            f"{FF_IMPACTS[ev['impact']]} {ja_title(ev['title'])}" + (f"　{extra}" if extra else ""))


# ── 値動き(fetch_news の関数をそのまま使う)──────────────────
def fmt_change(data, unit=""):
    arrow = "🔺" if not str(data["change"]).startswith("-") else "🔻"
    return f"**{data['price']}{unit}** {arrow} {data['change']} ({data['change_rate']}%)"


def market_lines(edition):
    note = "（金曜終値・CME日経先物は月曜朝まで休場）" if edition == "sun" else "（金曜の終値ベース）"
    lines = [f"## 📈 市場の状況{note}"]
    nikkei, _ = fn.fetch_index_with_fallback("日経平均株価", fn.fetch_nikkei225)
    lines.append(f"- 日経平均株価: {fmt_change(nikkei, '円')}" if nikkei else "- 日経平均株価: 取得できませんでした")
    lines.append(fn.format_yf_line("日経平均先物", fn.FUTURES_TICKERS["日経平均先物"]).replace("日経平均先物", "CME日経平均先物", 1))
    for conf in fn.KABUTAN_US_INDICES:
        data, _ = fn.fetch_index_with_fallback(conf["label"], lambda c=conf: fn.fetch_kabutan_us_index(c["url"], c["label"]))
        lines.append(f"- {conf['label']}: {fmt_change(data)}" if data else f"- {conf['label']}: 取得できませんでした")
    usdjpy = fn.fetch_usdjpy()
    lines.append(f"- ドル円: **{usdjpy}円**" if usdjpy is not None else "- ドル円: 取得できませんでした")
    jgb = fn.fetch_jgb_futures()
    if jgb:
        chg = f"{jgb['change']}円" if jgb.get("change") else "前日比不明"
        lines.append(f"- 長期国債先物: **{jgb['price']}円** {chg}")
    else:
        lines.append("- 長期国債先物: 取得できませんでした")
    for label in ("WTI原油先物", "金先物"):
        lines.append(fn.format_yf_line(label, fn.FUTURES_TICKERS[label]))
    return lines


# ── 本文の組み立て ────────────────────────────────
def build_message(edition, now, news_items, gemini_client, events, events_error):
    title = "土曜版：金曜の市場と週末の材料" if edition == "sat" else "日曜版：週末の材料と来週の予定"
    lines = [f"# 🗓️ 週末マーケットニュース（{title}・{now:%Y-%m-%d %H:%M} JST）", ""]
    lines += market_lines(edition)
    lines.append("\n## 📰 " + ("金曜大引け以降の重要ニュース" if edition == "sat" else "週末の重要ニュース"))
    curated = mnc.curate(news_items, gemini_client, fn.GEMINI_MODEL_NAME) if news_items else None
    if news_items:
        if curated is None and mnc.LAST_ERROR:
            news_alerts.record("gemini_weekend", "market", "週末マーケットニュースの材料整理(Gemini)",
                               f"Gemini APIエラー {news_alerts.short_error(mnc.LAST_ERROR)}", "継続(箇条書きで配信)")
        lines += mnc.render_groups(curated) if curated else mnc.render_flat(news_items)
    else:
        lines.append("- 該当なし")
    if edition == "sun":
        lines.append("\n## 📅 来週の重要経済イベント（米国・日本／重要度🔴High・🟡Medium／JST）")
        if events_error:
            lines.append("- 来週の重要経済イベント：取得できませんでした")
        elif events:
            lines += [render_event(e) for e in events]
        else:
            lines.append("- 該当なし")
        lines.append("\n## 👀 月曜に見る材料")
        monday = (now + datetime.timedelta(days=1)).date()
        mon_events = [e for e in (events or []) if e["at_jst"].date() in (monday, monday + datetime.timedelta(days=1))
                      and e["at_jst"] < datetime.datetime.combine(monday + datetime.timedelta(days=1), datetime.time(9), JST)]
        top = [g for g in (curated or {}).get("groups", []) if g["importance"] == "high"][:3]
        lines += [f"- 経済指標: {render_event(e)[2:]}" for e in mon_events]
        lines += [f"- 🔴【{g['category']}】{g['headline']}" for g in top]
        lines.append("- CME日経平均先物: 月曜朝の取引再開後の水準（上の金曜終値と比較）")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", choices=("sat", "sun"), required=True)
    ap.add_argument("--dry-run", action="store_true", help="送信せず本文を表示し、state も保存しない")
    args = ap.parse_args(argv)

    webhook = os.environ.get("DISCORD_WEBHOOK_MARKET")
    if not webhook and not args.dry_run:
        print("[ERROR] DISCORD_WEBHOOK_MARKET が未設定です。")
        return 1
    gemini_client = None
    if os.environ.get("GEMINI_API_KEY"):
        from google import genai
        gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    now = now_jst()
    state = prune_state(load_state(), now)
    news_items = select_news(fn.fetch_economy_news_candidates(), state, news_cutoff(args.edition, now))

    events, events_error = None, None
    if args.edition == "sun":
        try:
            events = filter_events(fetch_ff_events(), now)
        except Exception as err:  # noqa: BLE001
            events_error = err
            print(f"[ERROR] Forex Factory の経済イベント取得に失敗しました: {news_alerts.short_error(err)}")
            news_alerts.record("ff_calendar", "market", "来週の重要経済イベント(Forex Factory)",
                               f"取得失敗 {news_alerts.short_error(err)}", "継続(イベント欄は「取得できませんでした」と表示)")

    message = build_message(args.edition, now, news_items, gemini_client, events, events_error)
    print(message)
    if args.dry_run:
        print(f"[DRY-RUN] 送信・state保存はしません（記事{len(news_items)}件・イベント{len(events or [])}件）")
        for it in news_alerts.issues():
            print(f"[DRY-RUN] 異常通知予定: {it['kind']} {it['detail']}")
        return 0

    ok = fn.send_to_discord(webhook, message)
    if ok:
        for it in news_items:  # 送信に成功した記事だけ既送信にする
            state[fn.normalize_url(it["url"])] = now.isoformat()
    else:
        news_alerts.record("discord_send_weekend", "market", "Discord送信(週末マーケットニュース)",
                           "送信失敗", "スキップ(既送信にせず次回に回す)")
    news_alerts.flush({"market": webhook, "news": os.environ.get("DISCORD_WEBHOOK_NEWS")},
                      fn.send_to_discord, state, now)
    save_state(state)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
