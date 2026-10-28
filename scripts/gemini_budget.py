# -*- coding: utf-8 -*-
"""
Gemini の日次予算(状態ファイルに永続化・優先度で縮退)。

目的: 日次上限に早く達して重要な処理(経済の急変判定)が止まるのを防ぐ。
  - 呼び出し1回ごとに「日付 → 累計」を state に記録する(news_seen.json に同梱して Actions でコミット)。
  - 上限に近づくと低い優先度の呼び出しから止める。
      CRITICAL(経済の急変判定)  : 上限まで使える
      HIGH(国内ニュース要約・経済材料整理): 上限から RESERVE_STEP 件を残す
      LOW(市場サマリー等)        : 上限から RESERVE_STEP×2 件を残す
  - 日付は太平洋時間で区切る(クォータのリセット時刻に合わせるため。リセット時刻は【推定】)。

注意:
  - 上限 DEFAULT_DAILY_LIMIT は公式値ではない(コメント上の「無料枠 1日20回/モデル」を既定に置いた)。
    環境変数 GEMINI_DAILY_LIMIT で上書きできる。公式の数値は未確認。
  - 記録されるのは state を共有する呼び出しだけ。別ワークフローの呼び出しは数えない。
"""
from __future__ import annotations

import datetime
import os

try:
    from zoneinfo import ZoneInfo

    _PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # noqa: BLE001  時刻帯データが無い環境では JST の日付で区切る
    _PACIFIC = None
JST = datetime.timezone(datetime.timedelta(hours=9))

PRIORITY_CRITICAL = 0  # 経済の急変判定(最優先・死守)
PRIORITY_HIGH = 1      # 国内ニュース要約・経済材料整理
PRIORITY_LOW = 2       # 市場サマリー・週末・週次など

DEFAULT_DAILY_LIMIT = 20   # 無料枠の目安(公式値ではない・未確認)
RESERVE_STEP = 4           # 優先度ごとに残す余力の単位
KEY_PREFIX = "_gemini_budget:"
KEEP_DAYS = 2              # 古い日付の記録は、この日数より前を削除する


def daily_limit() -> int:
    raw = os.environ.get("GEMINI_DAILY_LIMIT", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_DAILY_LIMIT
    except ValueError:
        return DEFAULT_DAILY_LIMIT
    return value if value > 0 else DEFAULT_DAILY_LIMIT


def window_date(now: datetime.datetime) -> datetime.date:
    """クォータの集計日(太平洋時間の日付)。時刻帯データが無ければ JST の日付。"""
    local = now.astimezone(_PACIFIC) if _PACIFIC is not None else now.astimezone(JST)
    return local.date()


def _key(day: datetime.date) -> str:
    return f"{KEY_PREFIX}{day.isoformat()}"


def used(state: dict, now: datetime.datetime) -> int:
    try:
        return int(state.get(_key(window_date(now)), 0))
    except (TypeError, ValueError):
        return 0


def allowed(state: dict, now: datetime.datetime, priority: int, limit: int | None = None) -> bool:
    """この優先度の呼び出しを今行ってよいか。上限は daily_limit()(環境変数で上書き可)。"""
    limit = daily_limit() if limit is None else limit
    n = used(state, now)
    if priority <= PRIORITY_CRITICAL:
        return n < limit
    if priority == PRIORITY_HIGH:
        return n < limit - RESERVE_STEP
    return n < limit - RESERVE_STEP * 2


def record(state: dict, now: datetime.datetime) -> int:
    """1回の呼び出し(試行)を記録する。戻り値は当日の累計。古い日付の記録は削除する。"""
    today = window_date(now)
    count = used(state, now) + 1
    state[_key(today)] = count
    cutoff = today - datetime.timedelta(days=KEEP_DAYS)
    for key in [k for k in state if k.startswith(KEY_PREFIX)]:
        try:
            day = datetime.date.fromisoformat(key[len(KEY_PREFIX):])
        except ValueError:
            state.pop(key, None)
            continue
        if day < cutoff:
            state.pop(key, None)
    return count
