# -*- coding: utf-8 -*-
"""
バドミントン大会マスター(2026-10-08)。

ニュースの大会名から「大会格・レベル・ポイント・日本選手」を、BWF公式の情報だけで補完する。
記事ごとに検索せず、大会マスター(state/badminton_tournament_master.json)を参照する。

  情報源(優先順): BWF公式のみ。報道は大会名の抽出にだけ使い、大会格・ポイントの根拠にしない。
    - 大会カレンダー  https://corporate.bwfbadminton.com/events/calendar/{年}/all/0/all/
        開催日・開催国・都市・大会名・大会格(Super 500 等)・大会ID(カレンダーID)
    - 世界ランキング規程(BWF Statutes 5.3.3.1)のPDF: 大会格(Level)ごとの獲得ポイント表。版(Version)と施行日を保持
    - 大会のエントリー一覧PDF(M&Q Report): 日本(JPN)のエントリー選手
  世界ランキング(順位)と大会獲得ポイントは別物。ここで扱うのは後者だけ。

  推測しない: 特定できない・年度が分からない・複数候補・公式が取得できない・ポイント体系が確認できない場合は
  「確認中」「不明」と表示し、値を補完しない。
  キャッシュは盲信しない: 同じ大会・同じ年は再利用するが、開催が近い/開催中、前回確認から日数が経った、
  ポイント規程が更新された、状態が「確認済み」でない場合だけ公式を再検証する。
"""
import copy
import datetime
import io
import json
import os
import re
import unicodedata

MASTER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "badminton_tournament_master.json")
ALIASES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "badminton_event_aliases.json")

BWF_CALENDAR_URL = "https://corporate.bwfbadminton.com/events/calendar/{year}/all/0/all/"
BWF_STATUTES_URL = "https://corporate.bwfbadminton.com/statutes/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

SCHEMA_VERSION = 1
POINTS_CHECK_HOURS = 24            # ポイント規程の更新確認(HEAD)の間隔
LIVE_RECHECK_HOURS = 24            # 開催直前〜開催中の大会は、この間隔で再検証
UPCOMING_RECHECK_DAYS = 7          # 開催前後の大会は、この間隔で再検証
SHOW_MAX_STALE_DAYS = 30           # 再検証に失敗しても、この日数以内に確認済みなら表示する(開催前〜開催中の大会)
ENTRIES_RECHECK_HOURS = 24

STATUS_OK = "確認済み"
STATUS_RECHECK = "要再確認"
STATUS_CANCELLED = "中止"

# 公式の大会格表記 → (短い表示, World TourのLevel)。Levelは BWF 公式ページ(World Tour: Level 1-5)・
# ランキング規程(Level 6)・カレンダーのカテゴリ絞り込み(grade-2-level-N)で確認した対応。
CATEGORY_MAP = [
    (re.compile(r"World Tour Finals", re.I), "World Tour Finals", 1),
    (re.compile(r"World Tour Super 1000", re.I), "Super 1000", 2),
    (re.compile(r"World Tour Super 750", re.I), "Super 750", 3),
    (re.compile(r"World Tour Super 500", re.I), "Super 500", 4),
    (re.compile(r"World Tour Super 300", re.I), "Super 300", 5),
    (re.compile(r"Tour Super 100", re.I), "Super 100", 6),
]
# ランキング規程の表のうち、Level以外の格(カテゴリ表記 → 表の行キー)
GRADE3_MAP = {"international challenge": "int_challenge", "international series": "int_series", "future series": "future_series"}

ROUND_LABELS = ["優勝", "準優勝", "ベスト4", "ベスト8", "ベスト16", "ベスト32", "ベスト64", "ベスト128", "ベスト256", "ベスト512", "ベスト1024"]
SHOWN_ROUNDS = 4      # 表示は 優勝/準優勝/ベスト4/ベスト8


# ---------------------------------------------------------------- 取得(差し替え可能) ----
class Fetcher:
    """通信の窓口。テストでは差し替える。"""

    def __init__(self, timeout=40):
        self.timeout = timeout

    def _get(self, url, **kw):
        import requests
        r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    def text(self, url):
        return self._get(url).text

    def bytes(self, url):
        r = self._get(url)
        return r.content, {"last_modified": r.headers.get("Last-Modified", ""), "content_length": len(r.content)}

    def head(self, url):
        import requests
        r = requests.head(url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout, allow_redirects=True)
        r.raise_for_status()
        return {"last_modified": r.headers.get("Last-Modified", ""), "content_length": int(r.headers.get("Content-Length") or 0)}


def pdf_to_text(data):
    """PDFバイト列 → テキスト。pypdf が無い/読めない場合は例外(呼び出し側で『確認中』にする)。"""
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


# ---------------------------------------------------------------- 名前の正規化 ----
_STOP = {"of", "the", "by", "and", "a", "in", "at", "for"}


def _nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def name_tokens(name):
    """大会名 → 比較用の語集合。年・括弧書き(Cancelled等)・"powered by"/"presented by" 以降を除く。"""
    s = _nfkc(name).lower()
    s = re.sub(r"\((?:cancel+ed|postponed|present)[^)]*\)", " ", s)
    s = re.sub(r"\b(?:powered|presented) ?by\b.*$", " ", s)
    s = re.sub(r"(?:powered|presented)by.*$", " ", s)
    s = re.sub(r"\b(?:19|20)\d\d\b", " ", s)
    return {t for t in re.findall(r"[a-z0-9]+", s) if t not in _STOP}


def display_key_ja(text):
    """日本語表記の比較キー(全角半角・空白・中黒・記号を除く)。"""
    return re.sub(r"[\s・･\-‐ー－=＝　]+", "", _nfkc(text)).lower()


def load_alias_file(path=None):
    try:
        with open(path or ALIASES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_aliases(path=None):
    return load_alias_file(path).get("events", [])


# ---------------------------------------------------------------- ニュース見出しからの大会名抽出 ----
EVENT_WORDS = re.compile(r"オープン|マスターズ|選手権|インターナショナル|チャレンジ|ジュニア|アジア大会|アジア競技大会|ツアー|ファイナルズ|カップ|国際|ワールド")
BRACKET_RE = re.compile(r"【([^】]+)】")


def extract_event_mention(title, aliases):
    """見出しから大会の言及を1つ取り出す。戻り値: dict(kind=resolved|unknown|multiple|none, ...)。
    aliasに載っている大会名(日本語表記の正規化一致)を優先し、無ければ【】内の大会らしい語だけを『未特定』として返す。"""
    key = display_key_ja(re.sub(r" - [^-]{1,40}$", "", title))
    hits = []                                   # (一致した日本語キー, 大会定義, 一致した表記)
    for ev in aliases:
        for ja in ev.get("ja", []):
            k = display_key_ja(ja)
            if k and k in key:
                hits.append((k, ev, ja))
    # 別の大会名の一部でしかない短い一致(例: 「オープン」だけ)は、より長い一致があれば無視する
    hits = [h for h in hits if not any(h[0] != o[0] and h[0] in o[0] and h[1]["en"] != o[1]["en"] for o in hits)]
    if hits:
        distinct = {h[1]["en"] for h in hits}
        if len(distinct) > 1:
            return {"kind": "multiple", "names": sorted(distinct)}
        k, ev, ja = max(hits, key=lambda h: len(h[0]))
        return {"kind": "resolved", "en": ev["en"], "display": ev.get("display") or ja, "year": _title_year(title), "event": ev}
    for m in BRACKET_RE.finditer(title):
        if EVENT_WORDS.search(m.group(1)):
            return {"kind": "unknown", "name": m.group(1), "year": _title_year(title)}
    return {"kind": "none"}


def _title_year(title):
    years = set(re.findall(r"(?<!\d)(20\d\d)(?!\d)", _nfkc(title)))
    if len(years) == 1:
        return int(years.pop())
    return None if not years else "multiple"


# ---------------------------------------------------------------- BWFカレンダーの解析 ----
_MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]


def parse_calendar_html(html, year):
    """BWF公式カレンダー(1年分)のHTML → 大会レコードのリスト。月見出し(h2)と行の並びから日付を作る。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out, month = [], None
    for el in soup.find_all(["h2", "tr"]):
        if el.name == "h2":
            t = el.get_text(strip=True).upper()
            if t in _MONTHS:
                month = _MONTHS.index(t) + 1
            continue
        if "tr-tournament-detail" in (el.get("class") or []) or not any("bg-lightgray" in c for c in (el.get("class") or [])):
            continue
        tds = el.find_all("td")
        if len(tds) < 7 or month is None:
            continue
        link = tds[3].select_one("a.bwf-calendar_expander")
        cal_id = (link.get("data-target") or "").lstrip("#") if link else ""
        m = re.match(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$", tds[2].get_text(" ", strip=True))
        if not (cal_id and m):
            continue
        d1, d2 = int(m.group(1)), int(m.group(2))
        end_month = month if d2 >= d1 else month % 12 + 1
        end_year = year + (1 if end_month < month else 0)
        detail = el.find_next_sibling("tr", id=cal_id)
        detail_text = detail.get_text(" ", strip=True) if detail else ""
        prize = re.search(r"(\$[\d,]+)\s*PRIZE MONEY", detail_text)
        docs = [a.get("href") for a in (detail.find_all("a") if detail else []) if re.search(r"\.pdf$", a.get("href") or "", re.I)]
        out.append({
            "id": cal_id, "year": year,
            "name_official": tds[3].get_text(" ", strip=True),
            "country_code": tds[1].get_text(" ", strip=True),
            "start_date": datetime.date(year, month, d1).isoformat(),
            "end_date": datetime.date(end_year, end_month, d2).isoformat(),
            "category_text": tds[5].get_text(" ", strip=True),
            "city": tds[6].get_text(" ", strip=True),
            "prize_money": prize.group(1) if prize else "",
            "docs": docs,
        })
    return out


def category_info(category_text):
    """公式の大会格表記 → {"label","level","points_key"}。対応が定義されていない格は level=None(ポイント不明)。"""
    for rx, label, level in CATEGORY_MAP:
        if rx.search(category_text):
            return {"label": label, "level": level, "points_key": f"level{level}"}
    low = category_text.strip().lower()
    if low in GRADE3_MAP:
        return {"label": category_text.strip(), "level": None, "points_key": GRADE3_MAP[low]}
    return {"label": category_text.strip(), "level": None, "points_key": None}


# ---------------------------------------------------------------- ポイント表(BWFランキング規程)の解析 ----
_NUM_ROW = re.compile(r"((?:\d{1,5}\s+){10}\d{1,5})\s*$")


def parse_points_text(text):
    """ランキング規程PDFのテキスト → {"version","in_force","rows":{key:{"label","points":[11個]}}}。
    表は「Grade 2 – BWF World Tour, Level 4  9200 7800 6420 ... (11列)」の形。見つからなければ rows が空。"""
    t = _nfkc(text)
    version = re.search(r"Version\s*([\d.]+)", t)
    in_force = re.search(r"In Force:\s*(\d{1,2})/(\d{1,2})/(\d{4})", t)
    body = t[t.find("6.3 The table"):] if "6.3 The table" in t else t
    end = body.find("6.4 ")
    body = body[:end] if end > 0 else body
    flat = re.sub(r"\s+", " ", body)
    rows = {}
    for m in re.finditer(r"(Grade \d[^0-9]*?(?:Level \d)?[^0-9]*?(?:\([^)]*\)[^0-9]*?)?)\s((?:\d{2,5} ){10}\d{2,5})(?= |$)", flat):
        label = re.sub(r"\s+", " ", m.group(1)).strip()
        points = [int(x) for x in m.group(2).split()]
        key = _points_key(label)
        if key and key not in rows:
            rows[key] = {"label": label, "points": points}
    return {
        "version": version.group(1) if version else "",
        "in_force": f"{in_force.group(3)}-{int(in_force.group(2)):02d}-{int(in_force.group(1)):02d}" if in_force else "",
        "rows": rows,
    }


def _points_key(label):
    low = label.lower()
    m = re.search(r"world tour,? level (\d)", low)
    if m:
        lvl = int(m.group(1))
        return f"level{lvl}" if lvl != 2 else ("level2_" + ("addl500" if "500k" in low else "addl250" if "250" in low else "base"))
    if "level 1" in low and "finals" in low:
        return "level1"
    if "international challenge" in low:
        return "int_challenge"
    if "international series" in low:
        return "int_series"
    if "future series" in low:
        return "future_series"
    return None


def points_for(points_table, points_key):
    """大会格のポイント行を返す。Level 2(Super 1000)は賞金条件で複数区分があり公式情報だけでは区分を確定できないため None。"""
    if not points_table or points_table.get("status") != STATUS_OK or not points_key:
        return None
    if points_key == "level2":
        return None
    row = (points_table.get("rows") or {}).get(points_key)
    return row["points"] if row else None


# ---------------------------------------------------------------- エントリー一覧(M&Q Report)の解析 ----
_PERSON = re.compile(
    r"(?P<last>[A-Z][A-Z'\-]+(?: [A-Z][A-Z'\-]+)*) (?P<first>[A-Z][a-z][A-Za-z'\-]*(?: [A-Z][a-z][A-Za-z'\-]*)*) "
    r"(?:JPN [MF]|[MF] JPN)\b")


def parse_entry_list_text(text):
    """M&Q Report のテキスト → 日本(JPN)エントリー選手 [{"latin","gender","draw"}](重複なし・出現順)。
    シングルスは『姓 名 JPN M』、ダブルスは『姓 名 M JPN』の並びで載る。エントリー表でない文書は None、
    エントリー表だが日本選手がいなければ []。"""
    if "Member ID" not in text or "Country" not in text:
        return None
    out, seen = [], set()
    for line in text.split("\n"):
        draw = "予選" if "Qualifying" in line else "本戦"
        for m in _PERSON.finditer(line):
            latin = f"{m.group('last')} {m.group('first')}"
            if latin not in seen:
                seen.add(latin)
                out.append({"latin": latin, "gender": "M" if re.search(r"\bM\b", m.group(0)) else "F", "draw": draw})
    return out


def token_key(latin):
    toks = re.findall(r"[A-Za-z][A-Za-z'\-]*", latin or "")
    return " ".join(sorted(t.upper() for t in toks))


# ---------------------------------------------------------------- マスター本体 ----
def _iso(dt):
    return dt.isoformat()


def _parse_dt(s):
    try:
        return datetime.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def empty_master():
    return {"schema_version": SCHEMA_VERSION, "points_table": {}, "calendars": {}, "tournaments": {}}


def load_master(path=None):
    try:
        with open(path or MASTER_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("schema_version") == SCHEMA_VERSION:
            return data
    except (OSError, ValueError):
        pass
    return empty_master()


def save_master(master, path=None):
    path = path or MASTER_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=1, sort_keys=True)


class TournamentMaster:
    """大会マスターの参照・検証・更新。通信は fetcher 経由(1回の実行で同じ年/ファイルは1度しか取りに行かない)。"""

    def __init__(self, master=None, fetcher=None, now=None, aliases=None, ja_names=None, sponsor_words=None):
        self.master = master if master is not None else load_master()
        self.fetcher = fetcher or Fetcher()
        self.now = now or datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        self.aliases = aliases if aliases is not None else load_aliases()
        self.sponsor_words = set(sponsor_words if sponsor_words is not None else load_alias_file().get("sponsor_words", []))
        self.ja_names = ja_names or {}          # {欧文の語集合キー(token_key): 日本語氏名}
        self.dirty = False
        self._run_fetched = set()               # この実行で取得済みのキー(同一年・同一ファイルの再取得を防ぐ)
        self.errors = []

    # ----- ポイント規程 -----
    def ensure_points_table(self):
        pt = self.master.setdefault("points_table", {})
        if "points" in self._run_fetched:
            return pt
        self._run_fetched.add("points")
        checked = _parse_dt(pt.get("checked_at"))
        if pt.get("status") == STATUS_OK and checked and (self.now - checked) < datetime.timedelta(hours=POINTS_CHECK_HOURS):
            return pt
        try:
            html = self.fetcher.text(BWF_STATUTES_URL)
            urls = [u for u in re.findall(r'href="([^"]+\.pdf)"', html) if re.search(r"World(?:%20| )Ranking(?:%20| )System", u) and "5.3.3.1" in u.replace("%20", " ")]
            if not urls:
                raise RuntimeError("ランキング規程PDFのリンクが見つかりません")
            url = urls[0].replace(" ", "%20")
            meta = self.fetcher.head(url)
            same = (pt.get("source_url") == url and pt.get("status") == STATUS_OK and pt.get("rows")
                    and pt.get("last_modified") == meta.get("last_modified") and pt.get("content_length") == meta.get("content_length"))
            if same:
                pt["checked_at"] = _iso(self.now)
                self.dirty = True
                return pt
            data, meta = self.fetcher.bytes(url)
            parsed = parse_points_text(pdf_to_text(data))
            if not parsed["rows"] or "level4" not in parsed["rows"] or not parsed["version"]:
                raise RuntimeError("ポイント表を読み取れません")
            self.master["points_table"] = pt = {
                "source_url": url, "version": parsed["version"], "in_force": parsed["in_force"], "rows": parsed["rows"],
                "last_modified": meta.get("last_modified", ""), "content_length": meta.get("content_length", 0),
                "retrieved_at": _iso(self.now), "checked_at": _iso(self.now), "status": STATUS_OK,
            }
            self.dirty = True
        except Exception as err:  # noqa: BLE001
            self.errors.append(f"ポイント規程の確認に失敗: {err}")
            if pt:
                pt["status"] = STATUS_RECHECK      # 規程が更新された可能性を否定できないので、ポイントは表示しない
                self.dirty = True
        return pt

    # ----- 年別カレンダー -----
    def ensure_year(self, year, force=False):
        key = f"cal{year}"
        if key in self._run_fetched:
            return
        cal = self.master["calendars"].get(str(year))
        if cal and not force:
            return
        self._run_fetched.add(key)
        url = BWF_CALENDAR_URL.format(year=year)
        try:
            rows = parse_calendar_html(self.fetcher.text(url), year)
            if not rows:
                raise RuntimeError("カレンダーに大会がありません")
        except Exception as err:  # noqa: BLE001
            self.errors.append(f"{year}年カレンダーの取得に失敗: {err}")
            return
        known = set()
        for r in rows:
            tid = f"{year}-{r['id']}"
            known.add(tid)
            old = self.master["tournaments"].get(tid)
            rec = self._record_from_row(r, url, old)
            self.master["tournaments"][tid] = rec
        for tid, rec in self.master["tournaments"].items():          # カレンダーから消えた大会は『要再確認』にする(削除しない)
            if rec["year"] == year and tid not in known and rec["status"] == STATUS_OK:
                rec["status"] = STATUS_RECHECK
        self.master["calendars"][str(year)] = {"source_url": url, "retrieved_at": _iso(self.now), "verified_at": _iso(self.now), "row_count": len(rows)}
        self.dirty = True

    def _record_from_row(self, r, url, old):
        cancelled = bool(re.search(r"cancel", r["name_official"], re.I))
        core = {k: r[k] for k in ("name_official", "country_code", "start_date", "end_date", "category_text", "city", "prize_money")}
        rec = {
            "id": r["id"], "year": r["year"], **core, "docs": r["docs"],
            "status": STATUS_CANCELLED if cancelled else STATUS_OK,
            "source_url": url, "retrieved_at": (old or {}).get("retrieved_at", _iso(self.now)),
            "verified_at": _iso(self.now), "last_changed_at": (old or {}).get("last_changed_at", _iso(self.now)),
            "entries": (old or {}).get("entries"),
        }
        if old and any(old.get(k) != core[k] for k in core):
            rec["last_changed_at"] = _iso(self.now)             # 公式側の変更を検知した
        return rec

    def needs_verify(self, rec):
        if rec["status"] != STATUS_OK:
            return rec["status"] != STATUS_CANCELLED
        verified = _parse_dt(rec.get("verified_at"))
        if not verified:
            return True
        start = datetime.date.fromisoformat(rec["start_date"])
        end = datetime.date.fromisoformat(rec["end_date"])
        today = self.now.date()
        age = self.now - verified
        if start - datetime.timedelta(days=3) <= today <= end + datetime.timedelta(days=1):
            return age > datetime.timedelta(hours=LIVE_RECHECK_HOURS)
        if end + datetime.timedelta(days=1) < today:
            return False                                         # 終了済みの大会は変わらない
        return age > datetime.timedelta(days=UPCOMING_RECHECK_DAYS)

    # ----- 照合 -----
    def loose_candidates(self, event, year):
        """大会名の語を含み、国コードが合う公式カレンダーの大会(別開催地の同名大会・ジュニア大会なども含む)。曖昧さの検出用。"""
        q = name_tokens(event["en"])
        countries = event.get("countries") or []
        return [r for r in self.master["tournaments"].values()
                if r["year"] == year and q and q <= name_tokens(r["name_official"]) and (not countries or r["country_code"] in countries)]

    def candidates(self, event, year):
        """確定できる大会。loose_candidates が1つだけで、かつ公式名にスポンサー名など許容した語以外(Junior・Team・Mixed等)が
        含まれない場合だけ返す。2つ以上ある場合は同名の別大会の可能性があるため返さない(呼び出し側で『複数候補』にする)。"""
        loose = self.loose_candidates(event, year)
        if len(loose) != 1:
            return []
        q = name_tokens(event["en"])
        allowed = self.sponsor_words | set(event.get("extra_ok", []))
        return loose if (name_tokens(loose[0]["name_official"]) - q) <= allowed else []

    def resolve_year(self, year_hint, event):
        """年度を決める。見出しに年があればそれ。無ければ、今日が開催期間(前後7日)に入る大会が1つだけの年度。"""
        if isinstance(year_hint, int):
            return year_hint, ""
        if year_hint == "multiple":
            return None, "見出しに複数の年度があり年度を特定できません"
        years = []
        for y in (self.now.year - 1, self.now.year, self.now.year + 1):
            self.ensure_year(y)
            for r in self.loose_candidates(event, y):
                s0 = datetime.date.fromisoformat(r["start_date"]) - datetime.timedelta(days=7)
                e0 = datetime.date.fromisoformat(r["end_date"]) + datetime.timedelta(days=7)
                if s0 <= self.now.date() <= e0:
                    years.append(y)
        return (years[0], "") if len(set(years)) == 1 and len(years) == 1 else (None, "年度を特定できません")

    def resolve(self, title):
        """見出し → 表示用の情報。戻り値 dict(kind: ok|pending|none, ...)。通信が必要なら必要最小限だけ行う。"""
        men = extract_event_mention(title, self.aliases)
        if men["kind"] == "none":
            return {"kind": "none"}
        if men["kind"] == "multiple":
            return {"kind": "pending", "name": " / ".join(men["names"]), "reason": "複数の大会候補があります"}
        if men["kind"] == "unknown":
            return {"kind": "pending", "name": men["name"], "reason": "公式の大会マスターで特定できません"}
        year, why = self.resolve_year(men["year"], men["event"])
        if year is None:
            return {"kind": "pending", "name": men["display"], "reason": why}
        self.ensure_year(year)
        if len(self.loose_candidates(men["event"], year)) > 1:
            return {"kind": "pending", "name": f"{men['display']} {year}", "reason": "同名の大会が複数あり特定できません"}
        cands = self.candidates(men["event"], year)
        if not cands:
            return {"kind": "pending", "name": f"{men['display']} {year}", "reason": f"{year}年の公式カレンダーに該当する大会がありません"}
        rec = cands[0]
        if self.needs_verify(rec):
            self.ensure_year(year, force=True)
            rec = self.master["tournaments"].get(rec_key(rec), rec)
            if self.needs_verify(rec) and self.errors:
                rec = dict(rec, _stale=True)
        self.ensure_points_table()
        self.ensure_entries(rec)
        return {"kind": "ok", "display": men["display"], "record": self.master["tournaments"].get(rec_key(rec), rec),
                "stale": rec.get("_stale", False)}

    # ----- 日本選手 -----
    def ensure_entries(self, rec):
        live = self.master["tournaments"].get(rec_key(rec))
        if not live or live["status"] != STATUS_OK:
            return
        ent = live.get("entries") or {}
        fetched = _parse_dt(ent.get("verified_at"))
        end = datetime.date.fromisoformat(live["end_date"])
        done = ent.get("players") is not None and (end + datetime.timedelta(days=1) < self.now.date())
        if done or (fetched and self.now - fetched < datetime.timedelta(hours=ENTRIES_RECHECK_HOURS)):
            return
        if f"entries{rec_key(rec)}" in self._run_fetched:
            return
        self._run_fetched.add(f"entries{rec_key(rec)}")
        docs = sorted(live.get("docs") or [], key=lambda u: (0 if re.search(r"M\s*[_&]?\s*Q|entr|seed", u, re.I) else 1))
        for url in docs[:4]:
            try:
                data, _meta = self.fetcher.bytes(url)
                players = parse_entry_list_text(pdf_to_text(data))
            except Exception as err:  # noqa: BLE001
                self.errors.append(f"エントリー一覧の取得に失敗({live['name_official']}): {err}")
                continue
            if players is None:
                continue                      # エントリー表ではない文書(募集要項など)
            live["entries"] = {"source_url": url, "players": players, "retrieved_at": ent.get("retrieved_at", _iso(self.now)),
                               "verified_at": _iso(self.now)}
            self.dirty = True
            return
        # エントリー表を読めた文書が無い場合は players=None(=確認中)のまま

    def japanese_players(self, rec):
        """日本(JPN)のエントリー選手 [{"name","gender","draw"}]。日本語氏名が分かる選手は日本語、分からない選手は公式のローマ字表記のまま。
        エントリー一覧を取得できていなければ None(=確認中)。"""
        players = (rec.get("entries") or {}).get("players")
        if players is None:
            return None
        out = []
        for p in players:
            ja = self.ja_names.get(token_key(p["latin"]))
            out.append({"name": re.sub(r"\s+", "", ja) if ja else p["latin"], "gender": p["gender"], "draw": p["draw"]})
        return out


def rec_key(rec):
    return f"{rec['year']}-{rec['id']}"


# ---------------------------------------------------------------- 表示 ----
SEP = "────────"


def _fmt_points(n):
    return f"{n:,}ポイント"


def render_meta(info, master_obj):
    """resolve() の結果 → (記事タイトル用の大会名, 説明文の先頭に置く大会メタ情報のMarkdown)。noneなら (None, "")。"""
    if info["kind"] == "none":
        return None, ""
    if info["kind"] == "pending":
        return None, f"-# 大会情報：確認中（{info['name']}：{info['reason']}）\n"
    rec = info["record"]
    stale = info.get("stale")
    cat = category_info(rec["category_text"])
    pt = master_obj.master.get("points_table") or {}
    title = f"{info['display']} {rec['year']}"
    if rec["status"] == STATUS_CANCELLED:
        return title, f"**{title}**\n大会は中止（公式カレンダー表記）\n"
    ok = rec["status"] == STATUS_OK and not stale
    lines = [f"**{title}**"]
    head = cat["label"] if ok and cat["label"] else "確認中"
    if ok and cat["level"]:
        head += f" ｜ Level {cat['level']}"
    lines.append(head)
    pts = points_for(pt, cat["points_key"]) if ok else None
    if pts and cat["level"] == 1:                         # World Tour Finals はグループ戦で3位以下の扱いが異なるため上位2つだけ
        rows = list(zip(ROUND_LABELS[:2], pts[:2]))
    elif pts:
        rows = list(zip(ROUND_LABELS[:SHOWN_ROUNDS], pts[:SHOWN_ROUNDS]))
    else:
        rows = []
    lines.append("")
    if rows:
        lines += [f"{label}　{_fmt_points(p)}" for label, p in rows]
    else:
        why = "Super 1000は賞金条件で区分が分かれるため確認中" if ok and cat["level"] == 2 else "確認中"
        lines.append(f"優勝ポイント　{why}")
    players = master_obj.japanese_players(rec) if ok else None
    lines += ["", "🇯🇵 日本選手" + (f"（{len(players)}名）" if players else "")]
    if players:
        for gender, label in (("M", "男子"), ("F", "女子")):
            group = [p["name"] + ("（予選）" if p["draw"] == "予選" else "") for p in players if p["gender"] == gender]
            if group:
                lines.append(f"{label}　" + "・".join(group))
    else:
        lines.append("該当なし（公式エントリー一覧）" if players == [] else "確認中")
    src = ["BWF公式カレンダー"]
    if pts:
        src.append(f"ポイントはBWFランキング規程 v{pt.get('version', '?')}（{pt.get('in_force', '')}施行）")
    if players is not None:
        src.append("日本選手はBWF公式エントリー一覧")
    lines += ["", f"-# 出典：{'・'.join(src)}｜確認 {str(rec.get('verified_at', ''))[:10]}" + ("（再検証できず）" if stale else "")]
    return title, "\n".join(lines) + "\n"
