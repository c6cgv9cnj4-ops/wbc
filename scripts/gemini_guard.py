# -*- coding: utf-8 -*-
"""
Gemini API呼び出しの再試行と待機(2026-10-09)。

背景: ニュース配信(news.yml・30分おき)の全国主要ニュース要約と経済ニュース整理がGeminiを呼ぶ。
無料枠は『モデルごとに1日20リクエスト』(quotaId=GenerateRequestsPerDayPerProjectPerModel-FreeTier)で、
超えると HTTP 429 RESOURCE_EXHAUSTED になり、Googleは RetryInfo.retryDelay(例: 4393s≒1時間13分)で待ち時間を返す。
従来は再試行も待機も無く、30分おきの実行のたびに同じ429を受けて通知が積み上がっていた。

方針:
  - 429(日次上限、または待ち時間が長い)  → その場では再試行しない。待ち時間をstateに記録し、その時刻までAPIを呼ばない
  - 429(分あたりの制限など短い待ち)      → Retry-After/RetryInfoがあればその秒数、無ければ上限付き指数バックオフで最大 MAX_RETRIES 回
  - 5xx・タイムアウト・接続エラー(一時的)  → 上限付き指数バックオフで最大 MAX_RETRIES 回
  - 400/401/403/404等(再試行しても直らない) → 再試行しない
  - 1回の実行で待つ合計は WAIT_BUDGET 秒まで(無限・長時間の待機をしない)。並列化・モデル変更はしない。
  - 待機中(ブロック中)は呼び出しを行わず GeminiBlocked を投げる。記事を既読にするかどうかは呼び出し側が決める
    (fetch_news.py は失敗時に既読記録を取り消す)。
APIキー・記事本文はログ・例外メッセージに出さない(詳細は status と code のみ)。
"""
import datetime
import re
import time

BACKOFF_PREFIX = "_gemini_backoff:"       # state キー。値はISO日時(この時刻まで呼び出さない)。サフィックスは理由(quota)
MAX_RETRIES = 2
BASE_DELAY_SECONDS = 4.0
MAX_SINGLE_WAIT_SECONDS = 60.0
WAIT_BUDGET_SECONDS = 120.0
DEFAULT_QUOTA_BACKOFF_MINUTES = 60        # 日次上限なのに待ち時間が返らなかったときの控え時間(Googleの案内ではなく当方の方針)
JST = datetime.timezone(datetime.timedelta(hours=9))

_TRANSIENT_STATUS = {"UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL"}


class ErrorInfo:
    def __init__(self, kind, code=None, status=None, retry_after=None, daily=False):
        self.kind = kind                # quota / rate / transient / fatal
        self.code = code
        self.status = status
        self.retry_after = retry_after  # 秒。分からなければ None
        self.daily = daily


class GeminiBlocked(Exception):
    """待機中のため呼び出しを行わなかった。"""

    def __init__(self, until, reason="quota"):
        super().__init__(f"Gemini待機中: {until.isoformat()}まで({reason})")
        self.until = until
        self.reason = reason
        self.info = ErrorInfo("quota", 429, "RESOURCE_EXHAUSTED", daily=True)
        self.attempts = 0
        self.waited = 0.0


class GeminiCallError(Exception):
    """再試行しても成功しなかった(または再試行しても無駄な)失敗。"""

    def __init__(self, info, attempts, waited, original, backoff_until=None):
        super().__init__(f"Gemini呼び出し失敗 code={info.code} status={info.status} kind={info.kind} attempts={attempts}")
        self.info = info
        self.attempts = attempts
        self.waited = waited
        self.original = original
        self.backoff_until = backoff_until


# ---------------------------------------------------------------- エラーの分類 ----
def parse_retry_after(err):
    """エラーから待ち時間(秒)を取り出す。優先: HTTPヘッダ Retry-After → 応答JSONの RetryInfo.retryDelay → 文面の『retry in …』。"""
    resp = getattr(err, "response", None)
    headers = getattr(resp, "headers", None)
    if headers:
        try:
            val = headers.get("Retry-After") or headers.get("retry-after")
        except Exception:  # noqa: BLE001
            val = None
        if val:
            sec = _seconds_from_header(str(val))
            if sec is not None:
                return sec
    details = getattr(err, "details", None)
    if isinstance(details, dict):
        for d in (details.get("error", details) or {}).get("details", []) or []:
            if isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo"):
                sec = _seconds_from_duration(str(d.get("retryDelay", "")))
                if sec is not None:
                    return sec
    m = re.search(r"retry in ((?:\d+h)?(?:\d+m)?[\d.]+s)", str(err))
    if m:
        return _seconds_from_duration(m.group(1))
    return None


def _seconds_from_header(val):
    if re.fullmatch(r"\d+(?:\.\d+)?", val.strip()):
        return float(val)
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(val)
        return max(0.0, (dt - datetime.datetime.now(dt.tzinfo)).total_seconds())
    except Exception:  # noqa: BLE001
        return None


def _seconds_from_duration(text):
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", text.strip())
    if not m or not any(m.groups()):
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)


def classify(err):
    code = getattr(err, "code", None)
    status = getattr(err, "status", None)
    text = str(err)
    if not isinstance(code, int):
        m = re.search(r"\b([45]\d\d)\b", text)
        code = int(m.group(1)) if m else None
    if not status:
        m = re.search(r"\b(RESOURCE_EXHAUSTED|UNAVAILABLE|DEADLINE_EXCEEDED|INTERNAL|PERMISSION_DENIED|UNAUTHENTICATED|INVALID_ARGUMENT|NOT_FOUND)\b", text)
        status = m.group(1) if m else None
    retry_after = parse_retry_after(err)
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        daily = "PerDay" in text
        long_wait = retry_after is not None and retry_after > MAX_SINGLE_WAIT_SECONDS
        return ErrorInfo("quota" if (daily or long_wait) else "rate", 429, status or "RESOURCE_EXHAUSTED", retry_after, daily)
    low = text.lower()
    name = type(err).__name__.lower()
    if (code in (500, 502, 503, 504) or status in _TRANSIENT_STATUS or "timeout" in name or "timed out" in low
            or "connection" in name or "connection" in low and "error" in low):
        return ErrorInfo("transient", code, status, retry_after)
    return ErrorInfo("fatal", code, status, retry_after)


def format_duration(seconds):
    """秒 → 『約1時間13分』『約45分』『約30秒』。"""
    s = int(round(seconds))
    if s >= 3600:
        h, m = divmod(s // 60, 60)
        return f"約{h}時間{m}分" if m else f"約{h}時間"
    if s >= 60:
        return f"約{(s + 30) // 60}分"
    return f"約{s}秒"


# ---------------------------------------------------------------- ガード ----
class Guard:
    """stateに待機時刻を持つ呼び出しラッパー。state は fetch_news の既送信記録(ISO日時の辞書)を共有する。"""

    def __init__(self, state, clock=None, sleep=time.sleep, max_retries=MAX_RETRIES, wait_budget=WAIT_BUDGET_SECONDS):
        self.state = state
        self.clock = clock or (lambda: datetime.datetime.now(JST))
        self.sleep = sleep
        self.max_retries = max_retries
        self.wait_budget = wait_budget
        self.waited_total = 0.0
        self.calls = 0

    # ----- 待機状態 -----
    def blocked(self):
        """待機中なら (until, reason)。期限切れの記録は消す。"""
        now = self.clock()
        best = None
        for key in [k for k in self.state if k.startswith(BACKOFF_PREFIX)]:
            try:
                until = datetime.datetime.fromisoformat(self.state[key])
            except (TypeError, ValueError):
                self.state.pop(key, None)
                continue
            if until <= now:
                self.state.pop(key, None)
            elif best is None or until > best[0]:
                best = (until, key[len(BACKOFF_PREFIX):])
        return best

    def _set_backoff(self, seconds, reason="quota"):
        until = self.clock() + datetime.timedelta(seconds=seconds)
        self.state[BACKOFF_PREFIX + reason] = until.isoformat()
        return until

    def _clear_backoff(self):
        for key in [k for k in self.state if k.startswith(BACKOFF_PREFIX)]:
            self.state.pop(key, None)

    # ----- 呼び出し -----
    def call(self, fn):
        """fn() を実行する。成功すれば結果を返す。待機中は GeminiBlocked、失敗は GeminiCallError。"""
        blocked = self.blocked()
        if blocked:
            raise GeminiBlocked(blocked[0], blocked[1])
        attempts, waited = 0, 0.0
        while True:
            attempts += 1
            self.calls += 1
            try:
                result = fn()
                self._clear_backoff()
                return result
            except Exception as err:  # noqa: BLE001
                info = classify(err)
                if info.kind == "fatal":
                    raise GeminiCallError(info, attempts, waited, err) from err
                if info.kind == "quota":
                    seconds = info.retry_after if info.retry_after is not None else DEFAULT_QUOTA_BACKOFF_MINUTES * 60
                    until = self._set_backoff(seconds)
                    raise GeminiCallError(info, attempts, waited, err, backoff_until=until) from err
                if attempts > self.max_retries:
                    raise GeminiCallError(info, attempts, waited, err) from err
                wait = info.retry_after if info.retry_after is not None else BASE_DELAY_SECONDS * (2 ** (attempts - 1))
                wait = min(wait, MAX_SINGLE_WAIT_SECONDS)
                if self.waited_total + wait > self.wait_budget:
                    raise GeminiCallError(info, attempts, waited, err) from err
                self.sleep(wait)
                waited += wait
                self.waited_total += wait
