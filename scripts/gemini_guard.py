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
import threading
import time

try:
    from zoneinfo import ZoneInfo
    _PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # noqa: BLE001  時刻帯データが無い環境では下の既定値で動く
    _PACIFIC = None

# 日次上限の境界(2026-10-09 B案)。クォータが『太平洋時間0時』でリセットされるかは【未確認】であり、
# 境界は推定値にすぎない。回復を保証する時刻ではない(境界で再試行して失敗すれば、次の境界まで待つだけ)。
# 太平洋時間0時のJST時刻は夏時間(3/9〜11/1頃)は16:00、冬時間は17:00。時刻帯データが無い場合は冬時間側(17:00)に倒す。
# 17:00は夏時間でもリセット後になるため、待ちが最大1時間延びるだけで回復を取り逃がさない。
FALLBACK_BOUNDARY_JST_HOUR = 17
BACKOFF_PREFIX = "_gemini_backoff:"
BASIS_PREFIX = "_gemini_backoff_basis:"       # state キー。値はISO日時(この時刻まで呼び出さない)。サフィックスは理由(quota)
MAX_RETRIES = 2
BASE_DELAY_SECONDS = 4.0
MAX_SINGLE_WAIT_SECONDS = 60.0
WAIT_BUDGET_SECONDS = 120.0
DEFAULT_QUOTA_BACKOFF_MINUTES = 60        # 日次上限なのに待ち時間が返らなかったときの控え時間(Googleの案内ではなく当方の方針)
JST = datetime.timezone(datetime.timedelta(hours=9))
BASIS_RETRY_INFO = "Googleの待ち時間(境界より前のため採用)"
BASIS_BOUNDARY = "日次境界の推定時刻(Googleの待ち時間が境界を越えるため短縮。回復は保証されない)"
BASIS_DEFAULT = "既定の控え時間(Googleの待ち時間なし。境界より前)"
BASIS_MINUTE = "分あたり上限のGoogle待ち時間(日次境界は適用しない)"

_TRANSIENT_STATUS = {"UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL"}


class ErrorInfo:
    def __init__(self, kind, code=None, status=None, retry_after=None, daily=False):
        self.kind = kind                # quota / rate / transient / fatal
        self.code = code
        self.status = status
        self.retry_after = retry_after  # 秒。分からなければ None
        self.daily = daily


class GeminiBlocked(Exception):
    """待機中のため呼び出しを行わなかった。basis は再試行時刻の根拠(推定かどうか)。"""

    def __init__(self, until, reason="quota", basis=""):
        super().__init__(f"Gemini待機中: {until.isoformat()}まで({reason})")
        self.until = until
        self.reason = reason
        self.basis = basis
        self.info = ErrorInfo("quota", 429, "RESOURCE_EXHAUSTED", daily=True)
        self.attempts = 0
        self.waited = 0.0


class GeminiCancelled(Exception):
    """呼び出し元が立てたキャンセル信号(Guard の cancel)により、再試行を打ち切った。"""


class GeminiCallError(Exception):
    """再試行しても成功しなかった(または再試行しても無駄な)失敗。"""

    def __init__(self, info, attempts, waited, original, backoff_until=None, basis=""):
        super().__init__(f"Gemini呼び出し失敗 code={info.code} status={info.status} kind={info.kind} attempts={attempts}")
        self.info = info
        self.attempts = attempts
        self.waited = waited
        self.original = original
        self.backoff_until = backoff_until
        self.basis = basis


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
        daily = "PerDay" in text                       # quotaId の GenerateRequestsPerDay... (日次上限)
        # 日次上限は待機扱い(境界ロジック)。分あたり上限は短ければ再試行、長ければ待機扱い(境界は適用しない)
        long_wait = retry_after is not None and retry_after > MAX_SINGLE_WAIT_SECONDS
        kind = "quota" if (daily or long_wait) else "rate"
        return ErrorInfo(kind, 429, status or "RESOURCE_EXHAUSTED", retry_after, daily)
    low = text.lower()
    name = type(err).__name__.lower()
    if (code in (500, 502, 503, 504) or status in _TRANSIENT_STATUS or "timeout" in name or "timed out" in low
            or "connection" in name or "connection" in low and "error" in low):
        return ErrorInfo("transient", code, status, retry_after)
    return ErrorInfo("fatal", code, status, retry_after)


# ---------------------------------------------------------------- 呼び出しの計測(2026-10-13) ----
# 1回のHTTP試行ごとに1行出す(再試行も1回と数える)。APIキー・応答本文・エラー文は出さず、status と code のみ。
#   [GEMINI_CALL] count=12 task=local_search status=200 tokens=812
#   [GEMINI_429]  count=13 task=local_search error=RESOURCE_EXHAUSTED kind=quota
#   [GEMINI_ERR]  count=14 task=culture_filter status=503 kind=transient
# count はプロセス内の累計。Guard を通らない呼び出しは数えられない(通すこと)。
_CALL_LOCK = threading.Lock()
_CALL_TOTAL = [0]


def total_calls():
    return _CALL_TOTAL[0]


def _next_count():
    with _CALL_LOCK:
        _CALL_TOTAL[0] += 1
        return _CALL_TOTAL[0]


def _tokens_of(result):
    try:
        usage = getattr(result, "usage_metadata", None)
        total = getattr(usage, "total_token_count", None)
        return total if isinstance(total, int) else None
    except Exception:  # noqa: BLE001  計測のための処理で本体を落とさない
        return None


def log_success(task, result):
    tokens = _tokens_of(result)
    print(f"[GEMINI_CALL] count={_next_count()} task={task} status=200 tokens={tokens if tokens is not None else '-'}")


def log_error(task, info):
    n = _next_count()
    if info.kind in ("quota", "rate") and info.code == 429:
        print(f"[GEMINI_429] count={n} task={task} error={info.status or 'RESOURCE_EXHAUSTED'} kind={info.kind}")
    else:
        print(f"[GEMINI_ERR] count={n} task={task} status={info.code or info.status or '-'} kind={info.kind}")


def format_duration(seconds):
    """秒 → 『約1時間13分』『約45分』『約30秒』。"""
    s = int(round(seconds))
    if s >= 3600:
        h, m = divmod(s // 60, 60)
        return f"約{h}時間{m}分" if m else f"約{h}時間"
    if s >= 60:
        return f"約{(s + 30) // 60}分"
    return f"約{s}秒"


# ---------------------------------------------------------------- 日次境界(推定) ----
def next_daily_boundary(now):
    """now より後の最初の『太平洋時間0時』をJSTで返す(zoneinfo で夏時間・冬時間を自動で扱う)。
    戻り値: (JST の aware datetime, 根拠の文言)。時刻帯データが無い環境では JST 17:00 を使う(冬時間側・安全側)。
    注意: クォータのリセットが太平洋時間0時であることは未確認。ここで得る時刻は推定。"""
    if _PACIFIC is None:
        boundary = now.astimezone(JST).replace(hour=FALLBACK_BOUNDARY_JST_HOUR, minute=0, second=0, microsecond=0)
        if boundary <= now.astimezone(JST):
            boundary += datetime.timedelta(days=1)
        return boundary, "日次境界(時刻帯データなしのため JST17時を使用・推定)"
    local = now.astimezone(_PACIFIC)
    next_day = local.date() + datetime.timedelta(days=1)            # 今日の0時は now 以前なので、翌日の0時が『次』
    midnight = datetime.datetime(next_day.year, next_day.month, next_day.day, tzinfo=_PACIFIC)
    return midnight.astimezone(JST), "日次境界(太平洋時間0時・推定)"


def daily_retry_until(now, retry_after):
    """日次上限の再試行時刻。Googleの待ち時間(retry_after秒)と日次境界を比べ、早い方を採る。
    Google側の時刻が境界を越える場合は境界に短縮する。待ち時間が無い場合は既定の控え時間と境界の早い方。
    戻り値: (再試行時刻 JST, 根拠の文言)。回復を保証する時刻ではない。"""
    boundary, boundary_basis = next_daily_boundary(now)
    if retry_after is None:
        candidate = now.astimezone(JST) + datetime.timedelta(minutes=DEFAULT_QUOTA_BACKOFF_MINUTES)
        basis = BASIS_DEFAULT
    else:
        candidate = now.astimezone(JST) + datetime.timedelta(seconds=retry_after)
        basis = BASIS_RETRY_INFO
    if candidate > boundary:
        return boundary, BASIS_BOUNDARY + "／" + boundary_basis
    return candidate, basis


# ---------------------------------------------------------------- ガード ----
class Guard:
    """stateに待機時刻を持つ呼び出しラッパー。state は fetch_news の既送信記録(ISO日時の辞書)を共有する。"""

    def __init__(self, state, clock=None, sleep=time.sleep, max_retries=MAX_RETRIES, wait_budget=WAIT_BUDGET_SECONDS,
                 retry_rate_limit=True, cancel=None, base_delay=BASE_DELAY_SECONDS):
        self.state = state
        self.clock = clock or (lambda: datetime.datetime.now(JST))
        self.sleep = sleep
        self.max_retries = max_retries
        self.wait_budget = wait_budget
        # False の場合、分あたり上限(短い待ち時間の429)も再試行せず即座に失敗にする(Fast-Fail用途)
        self.retry_rate_limit = retry_rate_limit
        # cancel は is_set() を持つ信号(threading.Event など)。立つと以降の試行・待機を行わず GeminiCancelled
        # 並列実行で1スレッドが429を受けた時に、他スレッドの『実行中の再試行』も止めるために使う(2026-10-13)
        self.cancel = cancel
        # 一時的な失敗(503等)の指数バックオフの基準秒。base_delay × 2^(試行-1)
        self.base_delay = base_delay
        self.waited_total = 0.0
        self.calls = 0

    def _cancelled(self):
        return self.cancel is not None and self.cancel.is_set()

    # ----- 待機状態 -----
    def blocked(self):
        """待機中なら (until, reason, basis)。期限切れの記録は消す。"""
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
                self.state.pop(BASIS_PREFIX + key[len(BACKOFF_PREFIX):], None)
            elif best is None or until > best[0]:
                reason = key[len(BACKOFF_PREFIX):]
                best = (until, reason, self.state.get(BASIS_PREFIX + reason, ""))
        return best

    def _set_backoff(self, until, reason="quota", basis=""):
        self.state[BACKOFF_PREFIX + reason] = until.isoformat()
        self.state[BASIS_PREFIX + reason] = basis
        return until

    def _clear_backoff(self):
        for key in [k for k in self.state if k.startswith(BACKOFF_PREFIX) or k.startswith(BASIS_PREFIX)]:
            self.state.pop(key, None)

    # ----- 呼び出し -----
    def call(self, fn, task="gemini"):
        """fn() を実行する。成功すれば結果を返す。待機中は GeminiBlocked、失敗は GeminiCallError。
        task は計測ログ(GEMINI_CALL/GEMINI_429)に出す呼び出し元の名前。"""
        blocked = self.blocked()
        if blocked:
            raise GeminiBlocked(blocked[0], blocked[1], blocked[2])
        attempts, waited = 0, 0.0
        while True:
            if self._cancelled():
                raise GeminiCancelled(f"キャンセル済みのため呼び出しを打ち切り(task={task})")
            attempts += 1
            self.calls += 1
            try:
                result = fn()
            except Exception as err:  # noqa: BLE001
                info = classify(err)
                log_error(task, info)
                if info.kind == "fatal":
                    raise GeminiCallError(info, attempts, waited, err) from err
                if info.kind == "rate" and not self.retry_rate_limit:
                    raise GeminiCallError(info, attempts, waited, err) from err
                if info.kind == "quota":
                    now = self.clock()
                    if info.daily:
                        until, basis = daily_retry_until(now, info.retry_after)
                    elif info.retry_after is not None:
                        until, basis = now.astimezone(JST) + datetime.timedelta(seconds=info.retry_after), BASIS_MINUTE
                    else:
                        until, basis = now.astimezone(JST) + datetime.timedelta(minutes=DEFAULT_QUOTA_BACKOFF_MINUTES), BASIS_DEFAULT
                    self._set_backoff(until, "quota", basis)
                    raise GeminiCallError(info, attempts, waited, err, backoff_until=until, basis=basis) from err
                if attempts > self.max_retries:
                    raise GeminiCallError(info, attempts, waited, err) from err
                wait = info.retry_after if info.retry_after is not None else self.base_delay * (2 ** (attempts - 1))
                wait = min(wait, MAX_SINGLE_WAIT_SECONDS)
                if self.waited_total + wait > self.wait_budget:
                    raise GeminiCallError(info, attempts, waited, err) from err
                if self._cancelled():  # 待機に入る直前にも確認する(他スレッドの429で打ち切られた場合)
                    raise GeminiCancelled(f"キャンセル済みのため再試行を打ち切り(task={task})")
                self.sleep(wait)
                waited += wait
                self.waited_total += wait
            else:
                log_success(task, result)
                self._clear_backoff()
                return result
