# -*- coding: utf-8 -*-
"""
週末撮影予報の季節判定(2026-10-09)。check_photo_season.py から使う純粋ロジック(通信なし・日付は引数で固定できる)。

従来の問題: 実行日(木曜)の『月』が、施設紹介に書かれた月(例: ばら園(5-6月, 10月))に入っていれば『現在見頃』と断定していた。
  - 月全体が見頃扱いになり、実際の開花状況・イベント期間を見ていない
  - 括弧内の月が複数あっても最後の1つしか読まず(10月のみ)、ラベルも崩れていた
  - 判定の基準が『対象週末』ではなく『実行日』だった

情報を3つに分ける:
  1. 固定情報(photo/index.html の rawSpots): 施設名・所在地・被写体・一般的な見頃の時期(例年)
  2. 季節情報: 見頃の時期を『日付の範囲』に直したもの(年をまたぐ冬季・春秋の複数回に対応。上旬/中旬/下旬も解釈)
  3. 最新情報(data/photo_spot_live_info.json): 公式発表のイベント開催期間・開花状況・確認日・出典URL

判定は『対象週末(土日)』に対して行い、根拠を区別して表示する:
  最新情報(公式)で確認   … 開催期間内/開花状況が見頃など。確度 高
  最新情報が古い/取得失敗  … 確認日つきで『古い』と明記。確度 低
  例年の見頃のみ(未確認)  … 『例年の見頃・公式情報未確認』。『現在見頃』とは断定しない。確度 低
  情報なし                … 何も主張しない(見頃ではないとも断定しない)
"""
import calendar
import datetime
import re

UPCOMING_WINDOW_DAYS = 14        # 対象週末の終わりから、この日数以内に見頃入りするものを『まもなく』とする
LIVE_RELEVANT_BEFORE_DAYS = 14   # 開催期間の何日前から『開催前』として扱うか
LIVE_RELEVANT_AFTER_DAYS = 7     # 開催期間の何日後まで『終了』として扱うか
LIVE_STALE_DAYS = 14             # 最新情報の確認日がこれより古ければ『古い』

_PERIOD_START = {"上旬": 1, "中旬": 11, "下旬": 21}
_PERIOD_END = {"上旬": 10, "中旬": 20}

_TOKEN = re.compile(
    r"(?<!\d)(?:"
    r"(?P<m1>\d{1,2})月(?P<p1>上旬|中旬|下旬)?\s*[-〜~～ー]\s*(?P<m2>\d{1,2})月(?P<p2>上旬|中旬|下旬)?"      # 2月中旬-3月上旬
    r"|(?P<a>\d{1,2})\s*[-〜~～]\s*(?P<b>\d{1,2})月(?P<pr>上旬|中旬|下旬)?"                                 # 5-6月
    r"|(?P<lst>\d{1,2}(?:\s*[,，]\s*\d{1,2})+)月(?P<pl>上旬|中旬|下旬)?"                                   # 2,10月
    r"|(?P<s>\d{1,2})月(?P<ps>上旬|中旬|下旬)?"                                                           # 10月 / 3月下旬
    r")(?!\d)")


def _last_day(year, month):
    return calendar.monthrange(year, month)[1]


class Season:
    """見頃の時期。月(と旬)で表し、年は対象週末に合わせて日付範囲に直す。年をまたぐ(12-2月など)場合も扱える。"""

    def __init__(self, m_start, m_end, p_start=None, p_end=None, qualifier=""):
        self.m_start, self.m_end, self.p_start, self.p_end, self.qualifier = m_start, m_end, p_start, p_end, qualifier

    def __repr__(self):
        return f"Season({self.m_start}{self.p_start or ''}-{self.m_end}{self.p_end or ''} {self.qualifier})"

    def window(self, start_year):
        """start_year 年に始まる見頃の (開始日, 終了日)。"""
        d1 = datetime.date(start_year, self.m_start, _PERIOD_START.get(self.p_start, 1))
        end_year = start_year + (1 if self.m_end < self.m_start else 0)
        last = _PERIOD_END.get(self.p_end) or _last_day(end_year, self.m_end)
        return d1, datetime.date(end_year, self.m_end, min(last, _last_day(end_year, self.m_end)))

    def windows_around(self, date):
        """date の前後で関係しうる見頃(前年開始=年またぎ・当年・翌年開始)。"""
        return [self.window(date.year + k) for k in (-1, 0, 1)]

    def month_only(self):
        return self.p_start is None and self.p_end is None


def parse_seasons(detail):
    """括弧内の文言(例: '5-6月, 10月' / '曼珠沙華・9-10月' / '緑:7-8月, 紅葉:10月' / '2,10月')から Season のリストを作る。
    月が1つも読めなければ空(通年・常設の被写体は季節判定の対象にしない)。"""
    seasons, pos = [], 0
    for m in _TOKEN.finditer(detail):
        if m.group("m1"):
            ms, me, ps, pe = int(m.group("m1")), int(m.group("m2")), m.group("p1"), m.group("p2")
            spans = [(ms, me, ps, pe)]
        elif m.group("a"):
            spans = [(int(m.group("a")), int(m.group("b")), None, m.group("pr"))]
        elif m.group("lst"):
            spans = [(int(x), int(x), None, m.group("pl")) for x in re.split(r"[,，]", m.group("lst"))]
        else:
            ms = int(m.group("s"))
            spans = [(ms, ms, m.group("ps"), m.group("ps"))]
        qual = re.sub(r"[\s,，、・:：]+$", "", detail[pos:m.start()]).strip(" ・:：,，、")
        suffix = ""
        pos = m.end()
        for s0, s1, p0, p1 in spans:
            if not (1 <= s0 <= 12 and 1 <= s1 <= 12):
                continue
            q = qual if (qual and len(qual) <= 12 and not re.search(r"\d", qual)) else ""
            seasons.append(Season(s0, s1, p0, p1, q))
    # 月の直後に付く説明(例: '9月彼岸時期早朝')は読み取らない(日付の範囲に影響させない)
    return seasons


def parse_subject_segments(main_subject_text):
    """mainSubject('菜の花（4月）、ばら園（5-6月, 10月）、大温室（通年）')から、季節情報を持つ被写体だけを
    [{"subject","label","seasons":[Season...]}] で返す。季節が読めない断片(建築物・通年など)は含めない。"""
    segments = []
    for part in main_subject_text.split("、"):
        part = part.strip()
        m = re.match(r"^(?P<subject>[^（]+)（(?P<detail>[^）]*)）$", part)
        if not m:
            continue
        seasons = parse_seasons(m.group("detail").strip())
        if not seasons:
            continue
        subject = m.group("subject").strip()
        quals = []
        for s in seasons:
            if s.qualifier and s.qualifier not in quals:
                quals.append(s.qualifier)
        label = f"{subject}（{'・'.join(quals)}）" if quals else subject
        segments.append({"subject": subject, "label": label, "seasons": seasons})
    return segments


# ---------------------------------------------------------------- 対象週末 ----
def target_weekend(today):
    """配信実行日から見た『対象週末』(直近の土曜〜日曜)。土曜に実行すれば当日、日曜に実行すれば次の週末。"""
    sat = today + datetime.timedelta(days=(5 - today.weekday()) % 7)
    return sat, sat + datetime.timedelta(days=1)


def _overlaps(a1, a2, b1, b2):
    return a1 <= b2 and b1 <= a2


# ---------------------------------------------------------------- 季節情報による判定(例年) ----
def seasonal_status(seasons, weekend):
    """対象週末に対する例年の見頃。戻り値: ("in_season"|"upcoming"|None, 該当した Season)。"""
    sat, sun = weekend
    best = None
    for s in seasons:
        for w1, w2 in s.windows_around(sat):
            if _overlaps(w1, w2, sat, sun):
                return "in_season", s
            if sun < w1 <= sun + datetime.timedelta(days=UPCOMING_WINDOW_DAYS) and best is None:
                best = ("upcoming", s)
    return best if best else (None, None)


# ---------------------------------------------------------------- 最新情報(公式) ----
def _d(s):
    return datetime.date.fromisoformat(s) if s else None


def live_relevance(entry, weekend):
    """最新情報(開催期間)が対象週末の判定に関係するか。戻り値: "during"|"before"|"after"|None
    開催期間の前 LIVE_RELEVANT_BEFORE_DAYS 日〜後 LIVE_RELEVANT_AFTER_DAYS 日の外にある週末は、別の時期の話なので関係なし(None)。"""
    start, end = _d(entry.get("event_start")), _d(entry.get("event_end"))
    sat, sun = weekend
    if start and end:
        if _overlaps(start, end, sat, sun):
            return "during"
        if sun < start <= sun + datetime.timedelta(days=LIVE_RELEVANT_BEFORE_DAYS):
            return "before"
        if end < sat <= end + datetime.timedelta(days=LIVE_RELEVANT_AFTER_DAYS):
            return "after"
        return None
    return None


def live_is_stale(entry, today):
    checked = _d(entry.get("checked_at"))
    return checked is None or (today - checked).days > LIVE_STALE_DAYS


def keyword_match(entry, label):
    kws = entry.get("subject_keywords") or []
    return any(k in label for k in kws)


def judge_segment(segment, live_entries, weekend, today, fetch_failed_sources=()):
    """1つの被写体を対象週末で判定する。戻り値は dict:
      verdict:   "verified_now"(公式で開催中/見頃) | "verified_upcoming"(公式で開催前) | "verified_ended"(公式で終了) |
                 "typical_now"(例年の見頃のみ) | "typical_upcoming"(例年もうすぐ・未確認) | None(主張しない)
      basis:     "最新情報" | "最新情報(古い)" | "例年の見頃"
      confidence:"高" | "中" | "低"
      live:      根拠にした最新情報 entry(あれば)  /  fetch_failed: 公式取得に失敗した出典か
    """
    typ, season = seasonal_status(segment["seasons"], weekend)
    for e in live_entries:
        if not keyword_match(e, segment["label"]):
            continue
        rel = live_relevance(e, weekend)
        if rel is None:
            continue
        stale = live_is_stale(e, today)
        failed = e.get("source_url") in fetch_failed_sources
        basis = "最新情報(古い)" if (stale or failed) else "最新情報"
        conf = "低" if (stale or failed) else "高"
        status = (e.get("bloom_status") or "").strip()
        if rel == "during":
            if status in ("見頃過ぎ", "終了"):
                verdict = "verified_ended"
            else:
                verdict = "verified_now"
        elif rel == "before":
            verdict = "verified_upcoming"
        else:
            verdict = "verified_ended"
        return {"verdict": verdict, "basis": basis, "confidence": conf, "live": e, "fetch_failed": failed, "stale": stale,
                "season": season}
    if typ == "in_season":
        return {"verdict": "typical_now", "basis": "例年の見頃", "confidence": "低", "live": None, "fetch_failed": False, "stale": False, "season": season}
    if typ == "upcoming":
        return {"verdict": "typical_upcoming", "basis": "例年の見頃", "confidence": "低", "live": None, "fetch_failed": False, "stale": False, "season": season}
    return {"verdict": None, "basis": "", "confidence": "", "live": None, "fetch_failed": False, "stale": False, "season": None}


VERDICT_TEXT = {
    "verified_now": "公式で確認：開催中・見頃の対象",
    "verified_upcoming": "公式で確認：まもなく開始",
    "verified_ended": "公式で確認：終了",
    "typical_now": "例年の見頃（公式情報未確認）",
    "typical_upcoming": "例年はまもなく見頃（公式情報未確認）",
}
