# -*- coding: utf-8 -*-
"""
北本ゆかり型の判定(2026-10-06)。

「北本で起きたニュース(地域発生型)」とは別に、「北本で育った人物の全国・プロスポーツ等のニュース
(北本ゆかり型)」を、地域ニュース(#webhook_local)へ優先して採用する。

  - 対象は data/kitamoto_origin_people.json の台帳に登録した人物だけ(推測で追加しない)。
    台帳には、北本市立の小中学校・北本で育ったことを信頼できる根拠で確認できた人物(strong)だけを入れる。
    「北本市出身」の記載だけ・高校だけ・勤務先だけ等の人物(weak)は採用しない。
  - 記事タイトルに氏名(または別表記)が完全一致で含まれること。同姓同名が多い人物は、台帳の
    require_any(競技・肩書きなどの語)のいずれかもタイトルに必要。
  - 結果一覧・日程・中継・出演告知など一覧型のタイトルは「重要な記事」ではないため対象外
    (通常のスポーツ記事は従来どおりスポーツ側へ回る)。
  - 北本ゆかり型と判定した記事は地域ニュース側を優先し、スポーツ側へは二重に送らない。
  - 同じ人物の記事が1回の配信に並びすぎないよう、人物ごとに MAX_PER_PERSON 件までにする。
    件数を数える前に、同一見出しの媒体違い(転載)は1件にまとめる(headline_key。媒体名だけを正規化する)。
"""
import json
import os
import re
import unicodedata
import urllib.parse

LEDGER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
                           "kitamoto_origin_people.json")
MAX_PER_PERSON = 3

# 一覧型・告知型のタイトル(人物の「重要な記事」ではない)
LISTING_RE = re.compile(r"ライブ中継|ライブ配信|DAZN|スコア|星取|試合結果|結果一覧|日程|番組表|放送予定|テレビ放送|"
                        r"出演者|出演情報|出演決定|チケット|公演|ラインナップ|予告先発|\bvs\b|\bVS\b|第\d+節|ライブ\s*中継")

# 見出し末尾の「（媒体名）」とみなす語(明らかな媒体名だけ。「（中日）」のようにチーム名と紛らわしい語は含めない)
MEDIA_WORDS = ("新聞", "スポーツ", "ニュース", "NEWS", "News", "ONLINE", "Online", "オンライン", "Web", "WEB", "通信",
               "放送", "報知", "スポニチ", "サンスポ", "ORICON", "共同", "時事", "TBS", "NHK", "Yahoo", "週刊", "デイリー")
_PAREN_TAIL = re.compile(r"\s*[（(]([^（）()]{1,25})[）)]\s*$")

_cache = {}


def load_ledger(path=None):
    path = path or LEDGER_PATH
    if path in _cache:
        return _cache[path]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {"people": []}
    # strong だけを使う(weak等が将来混ざっても採用しない)
    data["people"] = [p for p in data.get("people", []) if p.get("class") == "strong" and p.get("name")]
    _cache[path] = data
    return data


def _title_body(title):
    """「 - 媒体名」を除いたタイトル本文。"""
    if " - " in title:
        body, src = title.rsplit(" - ", 1)
        if 0 < len(src) <= 40:
            return body
    return title


def headline_key(title):
    """同一見出し(媒体違いの転載)を同じ値にする比較用キー。
    末尾の「 - 媒体名」と、明らかな媒体名の「（媒体名）」だけを外し、全角半角・空白をそろえる。
    同一出来事かどうかの意味判定はしない(見出しが違えば別記事)。"""
    body = _title_body(title)
    while True:
        m = _PAREN_TAIL.search(body)
        if m and any(w in m.group(1) for w in MEDIA_WORDS):
            body = body[:m.start()]
            continue
        break
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", body))


def match(title, ledger=None):
    """台帳の人物に当たる『重要な記事』なら、その人物(dict)を返す。当たらなければ None。"""
    people = (ledger or load_ledger())["people"]
    body = _title_body(title)
    if LISTING_RE.search(body):
        return None
    for p in people:
        names = [p["name"], *p.get("aliases", [])]
        if not any(n in body for n in names):
            continue
        aux = p.get("require_any") or []
        if aux and not any(a in body for a in aux):
            continue
        return p
    return None


def search_query(ledger=None, days=1):
    """Google News検索クエリ: ("氏名" OR "氏名" ...) when:Nd"""
    people = (ledger or load_ledger())["people"]
    names = []
    for p in people:
        names.append(p["name"])
        names.extend(p.get("aliases", []))
    if not names:
        return None
    return "(" + " OR ".join(f'"{n}"' for n in names) + f") when:{days}d"


def search_url(ledger=None, days=1):
    q = search_query(ledger, days)
    if not q:
        return None
    return f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=ja&gl=JP&ceid=JP:ja"
