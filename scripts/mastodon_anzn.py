# -*- coding: utf-8 -*-
"""
あんぜんねっと公式Mastodon(@SaitamaSafety)から、県央(北本・鴻巣・桶川)の「災害発生情報」を取る
(2026-10-02追加・Macが止まっていても動くクラウド側の主経路)。

あんぜんねっと本体は GitHub Actions のIPで約9割403になるため、本体は自宅Mac(anzn_local.py)が取り、
Actions はこの Mastodon 経由で取る。Mastodon は本体の約9割しか投稿されない(2026-10-02 実測で10件中9件)
ため、Mac経路は補完として残す。

重複防止(両経路共通):
  キー = "anzn:<地域コード>:<記事ID>"(例: anzn:11217F:197)。あんぜんねっとの記事URL ?11217F&i=197 から作る。
  Actions が送ったキーは state/news_seen.json、Mac が送ったキーは state/anzn_mac_sent.json(Macが GitHub API で更新)。
  Actions は投稿から MASTODON_GRACE_MINUTES 経った投稿だけを送り、Macが動いていれば Mac が先に送れるようにする。

取りこぼし防止:
  前回までに処理した最新の投稿ID(カーソル)を state に持ち、それより新しい投稿を max_id でページ送りして全部回収する
  (上限 MAX_PAGES。初回はカーソルを最新に合わせるだけで、過去分は送らない)。
LLM(Gemini)は使わない。
"""
import datetime
import html
import re

import requests

ACCOUNT_ID = "112359439018179544"  # mastodon.social/@SaitamaSafety(あんぜんねっと@埼玉)
STATUSES_URL = f"https://mastodon.social/api/v1/accounts/{ACCOUNT_ID}/statuses"
PAGE_LIMIT = 40
MAX_PAGES = 25                 # 40件×25 ≒ 1000件(実測で約2週間分)。これを超える停止は Mac 側の履歴で補完する
MASTODON_GRACE_MINUTES = 30    # Mac(15分おき)が先に送れるよう、この時間より新しい投稿は次回に回す
# 1回の送信で送る上限。fetch_news.build_anzn_alert_embed は Embed の fields 上限(25件)までしか載せないため、
# これを超える分は今回は送らず、カーソルも手前で止めて次回に回す(載らない記事を既送信にしない・2026-10-02 F1)。
MAX_SEND_PER_RUN = 25
TARGET_CITIES = ("北本市", "鴻巣市", "桶川市")
CURSOR_KEY = "_mastodon_anzn_cursor"  # 値は "ISO日時|投稿ID"(既存の14日掃除で消えないよう日時を先頭に置く)
JST = datetime.timezone(datetime.timedelta(hours=9))

_ANZN_LINK_RE = re.compile(r"anzn\.net/sp/\?([0-9A-Za-z]+)&(?:amp;)?i=(\d+)")


def anzn_key_from_url(url):
    """あんぜんねっとの記事URL(Mac経路のURLも含む)→ 共通キー。取れなければ None。"""
    m = _ANZN_LINK_RE.search(url or "")
    return f"anzn:{m.group(1)}:{m.group(2)}" if m else None


def _text(content):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", content or ""))).strip()


def parse_post(status):
    """県央3市の災害発生情報なら、Mac経路(build_anzn_alert_embed)と同じ形の dict を返す。それ以外は None。"""
    raw = status.get("content", "")
    text = _text(raw)
    if "災害発生情報" not in text:
        return None
    m_city = re.match(r"#\s*(\S+?[市町村])\s+(\S+)", text)
    if not m_city or m_city.group(1) not in TARGET_CITIES:
        return None
    key = anzn_key_from_url(html.unescape(raw)) or anzn_key_from_url(text.replace(" ", ""))
    city, kind = m_city.group(1), m_city.group(2)
    m_dt = re.search(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", text)
    when = f"{int(m_dt.group(1)):02d}-{int(m_dt.group(2)):02d} {int(m_dt.group(3)):02d}:{m_dt.group(4)}頃" if m_dt else ""
    m_place = re.search(r"、(\S+?)地内", text)
    summary = f"{kind} {m_place.group(1)}" if m_place else kind
    url = None
    if key:
        _, area, art = key.split(":")
        url = f"https://anzn.net/sp/?{area}&i={art}"
    return {"key": key, "datetime": when, "city": city, "summary": summary, "url": url,
            "status_id": status["id"], "created_at": status.get("created_at")}


def _id_int(sid):
    try:
        return int(sid)
    except (TypeError, ValueError):
        return 0


def fetch_since(cursor_id, get=requests.get, max_pages=MAX_PAGES, timeout=20):
    """カーソルより新しい投稿を、新しい順にページ送りして全部返す(上限 max_pages)。
    戻り値: (投稿リスト[新しい順], 打ち切りフラグ)。取得失敗は例外を投げる(呼び出し側で異常通知)。"""
    posts, max_id = [], None
    for _ in range(max_pages):
        params = {"limit": PAGE_LIMIT}
        if max_id:
            params["max_id"] = max_id
        if cursor_id:
            params["since_id"] = cursor_id
        resp = get(STATUSES_URL, params=params, headers={"User-Agent": "wbc-anzn-safety/1.0"}, timeout=timeout)
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list):
            raise ValueError("Mastodon API の応答がリストではありません")
        page = [p for p in page if not cursor_id or _id_int(p.get("id")) > _id_int(cursor_id)]
        if not page:
            return posts, False
        posts.extend(page)
        if not cursor_id:  # 初回はカーソル合わせだけなので1ページで十分
            return posts, False
        max_id = page[-1]["id"]
    return posts, True


def get_cursor(state):
    v = state.get(CURSOR_KEY)
    return v.split("|", 1)[1] if v and "|" in v else None


def set_cursor(state, status_id, now):
    state[CURSOR_KEY] = f"{now.isoformat()}|{status_id}"


def select_to_send(posts, state, mac_sent_keys, now, grace_minutes=MASTODON_GRACE_MINUTES,
                   max_items=MAX_SEND_PER_RUN):
    """(送る候補[古い順・最大 max_items 件], 新しいカーソルにしてよい投稿ID, 猶予中の件数)。
    猶予中(投稿から grace_minutes 未満)の投稿や、上限を超えた送信候補より新しいところへはカーソルを進めない。"""
    cutoff = now - datetime.timedelta(minutes=grace_minutes)
    send, seen_keys, cursor_to, waiting = [], set(), None, 0
    for p in sorted(posts, key=lambda s: _id_int(s["id"])):  # 古い順
        try:
            created = datetime.datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            created = now
        if created > cutoff:
            waiting += 1
            break  # これ以降(より新しい投稿)は次回に回す
        item = parse_post(p)
        if item:
            key = item["key"] or f"mastodon:{p['id']}"  # 記事IDが取れない場合は投稿IDで重複防止
            item["key"] = key
            if key not in state and key not in mac_sent_keys and key not in seen_keys:
                if len(send) >= max_items:
                    break  # 上限を超えた送信候補は次回(カーソルはこの投稿の手前で止める)
                seen_keys.add(key)
                send.append(item)
        cursor_to = p["id"]
    return send, cursor_to, waiting
