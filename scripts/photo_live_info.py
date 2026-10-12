# -*- coding: utf-8 -*-
"""
週末撮影予報の『最新情報』(2026-10-09): 公式発表のイベント開催期間・開花状況・確認日・出典URLの保存と取得。

保存先: data/photo_spot_live_info.json
  {"schema_version": 1, "spots": {"<rawSpotsのid>": {"name": "...", "entries": [{
      "subject_keywords": ["バラ"], "event_name": "秋のバラフェスタ", "event_start": "2026-10-06", "event_end": "2026-11-23",
      "bloom_status": "", "bloom_note": "公式案内の文言そのまま", "source_url": "...", "source_kind": "official",
      "checked_at": "2026-10-08", "retrieved_at": "ISO日時"}]}}}
  - 公式サイト(施設・管理者の公式ページ)から読み取れた事実だけを入れる。読み取れない項目は空にし、推測で補わない。
  - 確認日(checked_at)が古い情報は判定側(photo_season_judge)で『古い』扱いにして確度を下げる。

取得(アダプター): 公式ページの書式は施設ごとに違うため、施設ごとの取得処理を ADAPTERS に登録する(現在は神代植物公園)。
  アダプターが無い施設は『公式情報未確認』のまま扱う(例年の見頃のみ)。取得に失敗した場合は例外を投げ、
  呼び出し側が『公式情報の取得に失敗』として扱う(既存の最新情報は消さず、確認日だけが古いまま残る)。
"""
import datetime
import json
import os
import re
import unicodedata
from urllib.parse import urljoin
import atomic_io

LIVE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "photo_spot_live_info.json")
SCHEMA_VERSION = 1
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


class AdapterError(Exception):
    """公式ページから必要な情報を読み取れなかった。"""


# ---------------------------------------------------------------- 保存 ----
def empty_store():
    return {"schema_version": SCHEMA_VERSION, "spots": {}}


def load_live(path=None):
    try:
        with open(path or LIVE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("schema_version") == SCHEMA_VERSION:
            return data
    except (OSError, ValueError):
        pass
    return empty_store()


def save_live(store, path=None):
    path = path or LIVE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_io.write_json_atomic(path, store, ensure_ascii=False, indent=1, sort_keys=True)


def entries_for(store, spot_id):
    return list(store.get("spots", {}).get(str(spot_id), {}).get("entries", []))


def upsert_entry(store, spot_id, spot_name, entry):
    """同じ催し(event_name と開始年)の既存エントリーは置き換え、無ければ追加する。"""
    spot = store.setdefault("spots", {}).setdefault(str(spot_id), {"name": spot_name, "entries": []})
    year = (entry.get("event_start") or "")[:4]
    for i, old in enumerate(spot["entries"]):
        if old.get("event_name") == entry.get("event_name") and (old.get("event_start") or "")[:4] == year:
            spot["entries"][i] = entry
            return
    spot["entries"].append(entry)


# ---------------------------------------------------------------- 公式ページの取得 ----
def http_text(url, timeout=15):
    import requests
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja"}, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text


def page_text(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for x in soup.select("script,style,nav,header,footer"):
        x.decompose()
    return soup.get_text("\n", strip=True)


def _nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def pick_year(month, day, today):
    """月日だけの表記から年を決める。今日の前後(約4か月前〜8か月後)に収まる年を選ぶ。決められなければ None。"""
    for y in (today.year, today.year + 1, today.year - 1):
        try:
            d = datetime.date(y, month, day)
        except ValueError:
            continue
        if today - datetime.timedelta(days=120) <= d <= today + datetime.timedelta(days=240):
            return y
    return None


_RANGE = re.compile(r"(\d{1,2})月(\d{1,2})日(?:\([^)]*\))?\s*[～~〜]\s*(?:(\d{1,2})月)?(\d{1,2})日(?:\([^)]*\))?")


def find_event_period(text, event_keyword, today):
    """本文から『M月D日(曜)～M月D日(曜)…「…event_keyword…」』の形で書かれた開催期間を探す。
    戻り値: (開始日, 終了日, 原文の抜粋)。見つからなければ None。"""
    t = _nfkc(text)
    for m in _RANGE.finditer(t):
        around = t[m.end():m.end() + 30] + t[max(0, m.start() - 30):m.start()]
        if event_keyword not in around:
            continue
        m1, d1 = int(m.group(1)), int(m.group(2))
        m2 = int(m.group(3)) if m.group(3) else m1
        d2 = int(m.group(4))
        y1 = pick_year(m1, d1, today)
        if y1 is None:
            continue
        y2 = y1 + (1 if m2 < m1 else 0)
        try:
            return datetime.date(y1, m1, d1), datetime.date(y2, m2, d2), t[max(0, m.start() - 10):m.end() + 30].replace("\n", " ")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- アダプター(施設ごと) ----
JINDAI_TOP = "https://www.tokyo-park.or.jp/jindai/"


def jindai_autumn_rose(fetch_text, today):
    """神代植物公園『秋のバラフェスタ』の開催期間(東京都公園協会の公式ページ)。
    トップページの『バラフェスタ』リンク(イベントページ)と、同ページから辿れる『バラ』のお知らせ(開催期間を本文に記載)を読む。
    開始日・終了日の両方が公式本文から読めたときだけエントリーを返す。"""
    top_html = fetch_text(JINDAI_TOP)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(top_html, "html.parser")
    links = []
    for a in soup.select("a[href]"):
        label = a.get_text(" ", strip=True)
        if "バラフェスタ" in label or ("バラ" in label and "リーフレット" in label):
            links.append(urljoin(JINDAI_TOP, a["href"]))
    if not links:
        raise AdapterError("神代植物公園のトップページにバラフェスタの案内が見つかりません")
    period, source, note = None, None, ""
    for url in dict.fromkeys(links):
        try:
            text = page_text(fetch_text(url))
        except Exception:  # noqa: BLE001
            continue
        found = find_event_period(text, "バラフェスタ", today)
        if found and (not period):
            period, source = found, url
        m = re.search(r"バラの花期は年[２2]回[^。]*。[^。]*。", _nfkc(text))
        if m and not note:
            note = "公式案内: " + m.group(0)
    if not period:
        raise AdapterError("バラフェスタの開催期間(開始日〜終了日)を公式本文から読み取れません")
    start, end, _excerpt = period
    return {
        "subject_keywords": ["バラ", "ばら"],
        "event_name": "秋のバラフェスタ",
        "event_start": start.isoformat(), "event_end": end.isoformat(),
        "bloom_status": "", "bloom_note": note,
        "source_url": source, "source_kind": "official",
        "checked_at": today.isoformat(),
        "retrieved_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(),
    }


# rawSpots の id → 取得処理(複数可)。公式の出典URL(取得の失敗時に『取得失敗』と表示するため source_urls を併記)
ADAPTERS = {
    "30": {"name": "神代植物公園", "fetchers": [jindai_autumn_rose], "source_urls": [JINDAI_TOP]},
}


def refresh_spots(store, spot_ids, today, fetch_text=http_text, adapters=None):
    """アダプターがある施設の最新情報を公式ページから取得して store を更新する。
    戻り値: (更新できた施設id, 失敗した施設id → 理由)。失敗しても既存の最新情報は消さない(確認日が古いまま残る)。"""
    adapters = ADAPTERS if adapters is None else adapters
    ok, failed = [], {}
    for sid in spot_ids:
        ad = adapters.get(str(sid))
        if not ad:
            continue
        try:
            for fetcher in ad["fetchers"]:
                entry = fetcher(fetch_text, today)
                upsert_entry(store, sid, ad["name"], entry)
            ok.append(str(sid))
        except Exception as err:  # noqa: BLE001
            failed[str(sid)] = f"{type(err).__name__}: {err}"[:120]
    return ok, failed
