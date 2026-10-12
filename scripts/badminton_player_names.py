# -*- coding: utf-8 -*-
"""
日本選手の日本語氏名の対応表(2026-10-08)。

BWF公式のエントリー一覧は選手名がローマ字(例: WATANABE Koki)。これを日本語氏名にするための対応表を、
世界ランキングとは無関係の別データ(data/badminton_player_names_ja.json)として持つ。

  情報源(公式): 公益財団法人日本バドミントン協会(badminton.or.jp)の日本代表選手ページ
      /national/player?gender=male|female (+U24 / ジュニアの一覧) と各選手の /national/player/view/{id}
      各選手ページに『ローマ字の名・姓』と『漢字氏名』が並んで載っている(.v-view__hero-ttl-myo / -na / -jp-name)。
  公式ページに両方が載っている選手だけを登録する。読みからの変換や他サイトからの補完はしない。
  誤変換の防止:
    - ローマ字は語の集合(順不同・大文字小文字無視)で一致させる(WATANABE Koki = koki watanabe)。
    - 同じローマ字に別の漢字氏名が登録されている(同姓同名・表記揺れ)場合は『曖昧』として使わない。
    - 性別が分かる登録とエントリー側の性別(M/F)が食い違う場合は使わない。
  対応表に無い選手は、BWF公式のローマ字表記のまま表示する(推測しない)。
  更新: scripts/update_badminton_player_names.py(公式サイトに接続できるとき)。本番の配信処理は通信せずこのファイルだけを読む。
"""
import datetime
import json
import os
import re
import unicodedata
import atomic_io

NAMES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "badminton_player_names_ja.json")
SCHEMA_VERSION = 1
NBA_BASE = "https://www.badminton.or.jp"
# 日本代表の選手一覧(性別はURLの gender パラメータ)。ジュニアは性別の記載が無いため性別照合をしない。
LIST_PAGES = [
    ("/national/player?gender=male", "M"),
    ("/national/player?gender=female", "F"),
    ("/national/player?gender=male&category[]=u24", "M"),
    ("/national/player?gender=female&category[]=u24", "F"),
]


def token_key(latin):
    toks = re.findall(r"[A-Za-z][A-Za-z'\-]*", unicodedata.normalize("NFKC", latin or ""))
    return " ".join(sorted(t.upper() for t in toks))


def clean_ja(name):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or ""))


# ------------------------------------------------------------ 公式ページの解析 ----
def parse_player_list(html):
    """一覧ページ → 選手ページの相対URL(重複なし・出現順)。"""
    seen, out = set(), []
    for m in re.finditer(r"(/national/player/view/\d+)", html):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def parse_player_view(html):
    """選手ページ → {"ja","latin"} 。ローマ字の名・姓と漢字氏名が揃っているときだけ返す。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    given = soup.select_one(".v-view__hero-ttl-myo")
    family = soup.select_one(".v-view__hero-ttl-na")
    ja = soup.select_one(".v-view__hero-ttl-jp-name") or soup.select_one(".v-view__prof-name-jp")
    if not (given and family and ja):
        return None
    latin = f"{given.get_text(strip=True)} {family.get_text(strip=True)}".strip()
    ja_name = clean_ja(ja.get_text(" ", strip=True))
    if not (token_key(latin) and re.search(r"[぀-ヿ一-鿿]", ja_name)):
        return None
    return {"ja": ja_name, "latin": latin}


# ------------------------------------------------------------ 対応表 ----
def empty_names():
    return {"schema_version": SCHEMA_VERSION, "source": "日本バドミントン協会(badminton.or.jp)日本代表選手ページ", "players": {}, "ambiguous": {}}


def load_names(path=None):
    try:
        with open(path or NAMES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("schema_version") == SCHEMA_VERSION:
            return data
    except (OSError, ValueError):
        pass
    return empty_names()


def save_names(data, path=None):
    path = path or NAMES_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_io.write_json_atomic(path, data, ensure_ascii=False, indent=1, sort_keys=True)


def add_player(data, ja, latin, gender, source_url, snapshot_url, now):
    """対応表に1人追加する。同じローマ字に別の漢字氏名が既にある場合は『曖昧』にして両方とも使わない。"""
    key = token_key(latin)
    iso = now.isoformat()
    cur = data["players"].get(key)
    amb = data["ambiguous"].get(key)
    if amb is not None:
        if ja not in amb["ja"]:
            amb["ja"].append(ja)
        return "ambiguous"
    if cur is None:
        data["players"][key] = {"ja": ja, "latin": latin, "gender": gender, "source_url": source_url, "snapshot_url": snapshot_url,
                                "retrieved_at": iso, "verified_at": iso}
        return "added"
    if cur["ja"] == ja:
        cur["verified_at"] = iso
        if gender and not cur.get("gender"):
            cur["gender"] = gender
        return "verified"
    data["ambiguous"][key] = {"ja": [cur["ja"], ja], "latin": latin}
    del data["players"][key]
    return "ambiguous"


class PlayerNames:
    """BWFエントリー(ローマ字・性別)→日本語氏名。曖昧・性別不一致・未登録は None(=ローマ字のまま表示)。"""

    def __init__(self, data=None):
        self.data = data if data is not None else load_names()

    def lookup(self, latin, gender=None):
        key = token_key(latin)
        if not key or key in self.data.get("ambiguous", {}):
            return None
        rec = self.data.get("players", {}).get(key)
        if not rec:
            return None
        if gender and rec.get("gender") and rec["gender"] != gender:
            return None
        return rec["ja"]


def build_from_site(fetch, now, base=NBA_BASE, snapshot_prefix="", data=None, sleep=None):
    """日本バドミントン協会の日本代表選手ページから対応表を作る(更新用)。
    fetch(url)->html。snapshot_prefix はWayback等の保存コピー経由で取る場合のURL接頭辞(出典URLには元のURLを残す)。"""
    data = data if data is not None else empty_names()
    stats = {"pages": 0, "players": 0, "added": 0, "verified": 0, "ambiguous": 0, "skipped": 0, "failed": 0}
    seen_views = {}
    for path, gender in LIST_PAGES:
        html = fetch(snapshot_prefix + base + path)
        stats["pages"] += 1
        for view in parse_player_list(html):
            seen_views.setdefault(view, gender)
    for view, gender in seen_views.items():
        if sleep:
            sleep()
        stats["players"] += 1
        try:
            parsed = parse_player_view(fetch(snapshot_prefix + base + view))
        except Exception:  # noqa: BLE001  1人取れなくても残りを続ける(登録済みの内容は消さない)
            stats["failed"] += 1
            continue
        if not parsed:
            stats["skipped"] += 1
            continue
        r = add_player(data, parsed["ja"], parsed["latin"], gender, base + view, (snapshot_prefix + base + view) if snapshot_prefix else "", now)
        stats[r] += 1
    data["updated_at"] = now.isoformat()
    return data, stats
