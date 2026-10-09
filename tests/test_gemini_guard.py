"""Gemini 429/503 の再試行・待機・通知(gemini_guard.py と fetch_news / news_alerts の連携)のテスト。
実際のGemini・Discordには接続しない。待機は sleep を差し替えて実時間を使わない。日時は固定。
実行: .venv/bin/python -m unittest tests.test_gemini_guard
"""
import contextlib
import datetime as dt
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from google.genai import errors  # noqa: E402

import fetch_news as fn  # noqa: E402
import gemini_guard as gg  # noqa: E402
import market_news_curation as mnc  # noqa: E402
import news_alerts as na  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 9, 7, 46, tzinfo=JST)


def quota_error(delay="4393s", daily=True):
    quota_id = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if daily else "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "message": "You exceeded your current quota. SECRET-LIKE-TEXT-should-not-appear-in-discord",
                      "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
                                  *([{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}] if delay else [])]}}
    return errors.ClientError(429, body)


def busy_error():
    return errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "This model is currently experiencing high demand."}})


class Flaky:
    """指定した順に例外を出し、尽きたら成功を返す呼び出し。"""

    def __init__(self, *errs, result="OK"):
        self.errs, self.result, self.calls = list(errs), result, 0

    def __call__(self):
        self.calls += 1
        if self.errs:
            raise self.errs.pop(0)
        return self.result


def make_guard(state=None, now=NOW):
    sleeps = []
    clock = {"now": now}
    g = gg.Guard({} if state is None else state, clock=lambda: clock["now"], sleep=sleeps.append)
    g.sleeps, g.clock_box = sleeps, clock
    return g


class ClassifyTest(unittest.TestCase):
    def test_daily_quota_with_retry_info(self):
        i = gg.classify(quota_error("4393s"))
        self.assertEqual((i.kind, i.code, i.status, i.retry_after, i.daily), ("quota", 429, "RESOURCE_EXHAUSTED", 4393.0, True))

    def test_short_rate_limit_is_retriable(self):
        i = gg.classify(quota_error("34.5s", daily=False))
        self.assertEqual((i.kind, i.retry_after), ("rate", 34.5))

    def test_long_wait_is_quota_even_if_not_daily(self):
        self.assertEqual(gg.classify(quota_error("600s", daily=False)).kind, "quota")

    def test_retry_in_message_and_header(self):
        e = errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Please retry in 1h13m13.124s."}})
        self.assertAlmostEqual(gg.parse_retry_after(e), 4393.124, places=2)
        resp = mock.Mock(headers={"Retry-After": "12"})
        e2 = errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "x"}}, resp)
        self.assertEqual(gg.parse_retry_after(e2), 12.0)

    def test_other_kinds(self):
        self.assertEqual(gg.classify(busy_error()).kind, "transient")
        self.assertEqual(gg.classify(errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad"}})).kind, "fatal")
        self.assertEqual(gg.classify(errors.ClientError(403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "no"}})).kind, "fatal")
        self.assertEqual(gg.classify(TimeoutError("timed out")).kind, "transient")
        self.assertEqual(gg.classify(ValueError("weird")).kind, "fatal")

    def test_format_duration(self):
        self.assertEqual(gg.format_duration(4393), "約1時間13分")
        self.assertEqual(gg.format_duration(2700), "約45分")
        self.assertEqual(gg.format_duration(34.5), "約34秒")


class GuardTest(unittest.TestCase):
    def test_success_no_wait(self):
        g = make_guard()
        f = Flaky()
        self.assertEqual(g.call(f), "OK")
        self.assertEqual((f.calls, g.sleeps), (1, []))

    def test_transient_exponential_backoff_then_success(self):
        g = make_guard()
        f = Flaky(busy_error(), busy_error())
        self.assertEqual(g.call(f), "OK")
        self.assertEqual((f.calls, g.sleeps), (3, [4.0, 8.0]))

    def test_transient_gives_up_after_max_retries(self):
        g = make_guard()
        f = Flaky(busy_error(), busy_error(), busy_error(), busy_error())
        with self.assertRaises(gg.GeminiCallError) as cm:
            g.call(f)
        self.assertEqual((f.calls, cm.exception.attempts, g.sleeps), (3, 3, [4.0, 8.0]))       # 無限リトライしない
        self.assertEqual(cm.exception.info.code, 503)
        self.assertEqual([k for k in g.state if k.startswith(gg.BACKOFF_PREFIX)], [])           # 一時的なエラーでは待機状態を作らない

    def test_short_rate_limit_respects_retry_after(self):
        g = make_guard()
        f = Flaky(quota_error("20s", daily=False))
        self.assertEqual(g.call(f), "OK")
        self.assertEqual(g.sleeps, [20.0])

    def test_rate_limit_without_delay_uses_backoff(self):
        g = make_guard()
        f = Flaky(quota_error(None, daily=False))
        self.assertEqual(g.call(f), "OK")
        self.assertEqual(g.sleeps, [4.0])

    def test_wait_is_capped_and_budgeted(self):
        g = make_guard()
        g.wait_budget = 70
        f = Flaky(quota_error("55s", daily=False), quota_error("55s", daily=False))
        with self.assertRaises(gg.GeminiCallError):
            g.call(f)
        self.assertEqual(g.sleeps, [55.0])                        # 2回目は合計予算(70秒)を超えるので待たない
        self.assertLessEqual(sum(g.sleeps), g.wait_budget)

    def test_daily_quota_no_retry_and_blocks_following_calls(self):
        g = make_guard()
        f = Flaky(quota_error("4393s"))
        with self.assertRaises(gg.GeminiCallError) as cm:
            g.call(f)
        self.assertEqual((f.calls, g.sleeps), (1, []))             # 即時リトライしない
        self.assertEqual(cm.exception.backoff_until, NOW + dt.timedelta(seconds=4393))
        f2 = Flaky()
        with self.assertRaises(gg.GeminiBlocked) as cb:
            g.call(f2)
        self.assertEqual(f2.calls, 0)                              # 待機中はAPIを呼ばない
        self.assertEqual(cb.exception.until, NOW + dt.timedelta(seconds=4393))

    def test_backoff_survives_in_state_between_runs(self):
        g = make_guard()
        with self.assertRaises(gg.GeminiCallError):
            g.call(Flaky(quota_error("4393s")))
        g2 = make_guard(state=g.state, now=NOW + dt.timedelta(minutes=30))        # 30分後の次回実行
        with self.assertRaises(gg.GeminiBlocked):
            g2.call(Flaky())
        g3 = make_guard(state=g.state, now=NOW + dt.timedelta(seconds=4394))      # 待ち時間が過ぎた
        f = Flaky()
        self.assertEqual(g3.call(f), "OK")
        self.assertEqual([k for k in g.state if k.startswith(gg.BACKOFF_PREFIX)], [])    # 成功したら待機状態を消す

    def test_daily_quota_without_delay_uses_default_backoff(self):
        g = make_guard()
        with self.assertRaises(gg.GeminiCallError) as cm:
            g.call(Flaky(quota_error(None, daily=True)))
        self.assertEqual(cm.exception.backoff_until, NOW + dt.timedelta(minutes=gg.DEFAULT_QUOTA_BACKOFF_MINUTES))

    def test_fatal_is_not_retried(self):
        g = make_guard()
        f = Flaky(errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad request"}}))
        with self.assertRaises(gg.GeminiCallError) as cm:
            g.call(f)
        self.assertEqual((f.calls, g.sleeps, cm.exception.info.kind), (1, [], "fatal"))


class DailyBoundaryTest(unittest.TestCase):
    """B案(2026-10-09): 日次上限の再試行時刻 = Googleの待ち時間と『次の太平洋時間0時(推定)』の早い方。
    太平洋時間0時のJST時刻は夏時間16:00・冬時間17:00。クォータのリセット時刻は未確認のため、回復は保証しない。"""

    def J(self, y, mo, d, h=0, mi=0, sec=0):
        return dt.datetime(y, mo, d, h, mi, sec, tzinfo=JST)

    def test_boundary_summer_and_winter(self):
        self.assertEqual(gg.next_daily_boundary(self.J(2026, 10, 9, 7, 46))[0], self.J(2026, 10, 9, 16))   # 夏時間 PDT
        self.assertEqual(gg.next_daily_boundary(self.J(2026, 12, 1, 10))[0], self.J(2026, 12, 1, 17))      # 冬時間 PST

    def test_strictly_after_now(self):
        self.assertEqual(gg.next_daily_boundary(self.J(2026, 10, 9, 16))[0], self.J(2026, 10, 10, 16))     # ちょうど境界なら次の境界

    def test_dst_switch_2026(self):
        # 2026-03-08 が夏時間開始。切替後の境界は 16:00 に変わる
        self.assertEqual(gg.next_daily_boundary(self.J(2026, 3, 7, 18))[0], self.J(2026, 3, 8, 17))     # 切替前(冬時間)
        self.assertEqual(gg.next_daily_boundary(self.J(2026, 3, 8, 18))[0], self.J(2026, 3, 9, 16))

    def test_year_end(self):
        self.assertEqual(gg.next_daily_boundary(self.J(2026, 12, 31, 20))[0], self.J(2027, 1, 1, 17))

    def test_fallback_without_timezone_data(self):
        with mock.patch.object(gg, "_PACIFIC", None):
            self.assertEqual(gg.next_daily_boundary(self.J(2026, 10, 9, 10))[0], self.J(2026, 10, 9, 17))
            self.assertEqual(gg.next_daily_boundary(self.J(2026, 10, 9, 17, 30))[0], self.J(2026, 10, 10, 17))
            self.assertIn("推定", gg.next_daily_boundary(self.J(2026, 10, 9, 10))[1])

    def test_retry_info_before_boundary_is_kept(self):
        until, basis = gg.daily_retry_until(self.J(2026, 10, 9, 7, 46), 4393)
        self.assertEqual(until, self.J(2026, 10, 9, 8, 59, 13))      # 4393秒後
        self.assertEqual(basis, gg.BASIS_RETRY_INFO)

    def test_retry_info_past_boundary_is_shortened(self):
        until, basis = gg.daily_retry_until(self.J(2026, 10, 9, 10), 86400)
        self.assertEqual(until, self.J(2026, 10, 9, 16))
        self.assertIn("回復は保証されない", basis)

    def test_missing_retry_after_uses_default_but_not_past_boundary(self):
        self.assertEqual(gg.daily_retry_until(self.J(2026, 10, 9, 7, 46), None), (self.J(2026, 10, 9, 8, 46), gg.BASIS_DEFAULT))
        until, basis = gg.daily_retry_until(self.J(2026, 10, 9, 15, 30), None)
        self.assertEqual(until, self.J(2026, 10, 9, 16))
        self.assertIn("回復は保証されない", basis)

    def test_daily_429_blocks_until_boundary_then_allows_retry(self):
        clock = {"now": self.J(2026, 10, 9, 10)}
        state = {}
        g = gg.Guard(state, clock=lambda: clock["now"], sleep=lambda s: None)
        with self.assertRaises(gg.GeminiCallError) as cm:
            g.call(Flaky(quota_error("86400s")))
        self.assertEqual(cm.exception.backoff_until, self.J(2026, 10, 9, 16))
        clock["now"] = self.J(2026, 10, 9, 15, 59)
        with self.assertRaises(gg.GeminiBlocked):
            g.call(Flaky())
        clock["now"] = self.J(2026, 10, 9, 16)                       # 期限ちょうど: 再試行してよい
        f = Flaky()
        self.assertEqual(g.call(f), "OK")
        self.assertEqual(f.calls, 1)
        self.assertEqual([k for k in state if k.startswith(gg.BACKOFF_PREFIX) or k.startswith(gg.BASIS_PREFIX)], [])   # 成功で解除

    def test_consecutive_daily_429_recomputes_each_time(self):
        clock = {"now": self.J(2026, 10, 9, 10)}
        g = gg.Guard({}, clock=lambda: clock["now"], sleep=lambda s: None)
        with self.assertRaises(gg.GeminiCallError) as c1:
            g.call(Flaky(quota_error("86400s")))
        self.assertEqual(c1.exception.backoff_until, self.J(2026, 10, 9, 16))
        clock["now"] = self.J(2026, 10, 9, 16, 0, 30)                # 境界直後にまた429
        with self.assertRaises(gg.GeminiCallError) as c2:
            g.call(Flaky(quota_error("4393s")))
        self.assertEqual(c2.exception.backoff_until, self.J(2026, 10, 9, 16, 0, 30) + dt.timedelta(seconds=4393))   # 境界(次の10/10 16:00)より前なのでGoogle値
        self.assertEqual(c2.exception.basis, gg.BASIS_RETRY_INFO)
        self.assertEqual(g.calls, 2)

    def test_per_minute_long_wait_ignores_daily_boundary(self):
        g = gg.Guard({}, clock=lambda: self.J(2026, 10, 9, 10), sleep=lambda s: None)
        with self.assertRaises(gg.GeminiCallError) as cm:
            g.call(Flaky(quota_error("600s", daily=False)))
        self.assertEqual(cm.exception.backoff_until, self.J(2026, 10, 9, 10, 10))   # 10:10 = 境界(16:00)を使わない
        self.assertEqual(cm.exception.basis, gg.BASIS_MINUTE)

    def test_per_minute_short_retries_without_backoff(self):
        g = gg.Guard({}, clock=lambda: self.J(2026, 10, 9, 10), sleep=lambda s: None)
        f = Flaky(quota_error("20s", daily=False))
        self.assertEqual(g.call(f), "OK")
        self.assertEqual(g.sleeps if hasattr(g, "sleeps") else None, None)
        self.assertEqual([k for k in g.state if k.startswith(gg.BACKOFF_PREFIX)], [])

    def test_expired_basis_is_removed_with_backoff(self):
        state = {gg.BACKOFF_PREFIX + "quota": self.J(2026, 10, 9, 9).isoformat(), gg.BASIS_PREFIX + "quota": "x"}
        g = gg.Guard(state, clock=lambda: self.J(2026, 10, 9, 10), sleep=lambda s: None)
        self.assertIsNone(g.blocked())
        self.assertEqual(state, {})

    def test_blocked_returns_basis(self):
        state = {}
        g = gg.Guard(state, clock=lambda: self.J(2026, 10, 9, 10), sleep=lambda s: None)
        with self.assertRaises(gg.GeminiCallError):
            g.call(Flaky(quota_error("86400s")))
        until, reason, basis = g.blocked()
        self.assertEqual((until, reason), (self.J(2026, 10, 9, 16), "quota"))
        self.assertIn("回復は保証されない", basis)

    def test_notice_text_says_boundary_is_estimated(self):
        import fetch_news as fnews
        txt = fnews._next_retry_text(self.J(2026, 10, 9, 16), gg.BASIS_BOUNDARY)
        self.assertIn("16:00 JST以降の実行で再試行", txt)
        self.assertIn("推定", txt)
        self.assertIn("回復は保証されません", txt)
        self.assertNotIn("回復は保証されません", fnews._next_retry_text(self.J(2026, 10, 9, 8, 59), gg.BASIS_RETRY_INFO))


# ------------------------------------------------------------------ 全国主要ニュース ----
class Resp:
    def __init__(self, text):
        self.text = text


GOOD = '[{"index": 0, "category": "政治", "headline": "見出しA", "summary_lines": ["a", "b", "c"]}]'


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.models = self

    def generate_content(self, **kw):
        self.calls += 1
        o = self.outcomes.pop(0) if self.outcomes else Resp(GOOD)
        if isinstance(o, Exception):
            raise o
        return o


ITEMS = [{"title": f"国内ニュース{i}", "url": f"https://news.yahoo.co.jp/pickup/{i}", "published": "10/09 07:00"} for i in range(5)]


def run_national(client, state, now=NOW, guard=None, items=ITEMS):
    bodies = []

    def body(url):
        bodies.append(url)
        return "リード文" + url[-1]
    na.reset()
    with mock.patch.object(fn, "fetch_rss_items", return_value=[dict(i) for i in items]), \
         mock.patch.object(fn, "fetch_yahoo_pickup_body", side_effect=body), \
         mock.patch.object(fn, "YAHOO_CATEGORY_RSS", []), \
         contextlib.redirect_stdout(io.StringIO()):
        g = guard or gg.Guard(state, clock=lambda: now, sleep=lambda s: None)
        msg, sports = fn.build_national_news_message(client, state, now, g)
    return msg, bodies


def keys(state):
    return [k for k in state if k.startswith("https://")]


class NationalFlowTest(unittest.TestCase):
    def test_quota_holds_articles_and_notifies_once_with_details(self):
        state = {}
        client = FakeClient(quota_error("4393s"))
        msg, bodies = run_national(client, state)
        self.assertIsNone(msg)
        self.assertEqual(keys(state), [])                          # 失敗記事は既読にしない
        self.assertEqual(client.calls, 1)                          # 即時リトライしない
        issues = na.issues()
        self.assertEqual([(i["kind"], i["count"]) for i in issues], [("gemini_national:quota", 1)])     # ×2 にならない
        payload = na.build_embeds({}, NOW)[0]["news"]["embeds"][0]
        d = payload["description"]
        for expect in ("**全国主要ニュース(Gemini要約)**", "Geminiの利用上限に達しました", "・失敗: 要約処理1回（API呼び出し1回）", "・HTTP 429 RESOURCE_EXHAUSTED",
                       "・保留: 記事5件（既読にせず、次回以降の実行で再処理します）", "・再試行方針: 利用上限のため再試行せず、待ち時間（約1時間13分）が終わるまでAPIを呼びません",
                       "・次回の再試行: 08:59 JST以降の実行で再試行（Googleの待ち時間(境界より前のため採用)）", "影響: 今回はスキップ。この回の全国ニュースは配信されません。記事は保留。"):
            self.assertIn(expect, d)
        self.assertIn("同じ異常は12時間通知しません", payload["footer"]["text"])
        for secret in ("SECRET-LIKE", "violations", "{'error'", "quotaId"):
            self.assertNotIn(secret, d + payload["footer"]["text"])

    def test_next_run_while_waiting_makes_no_api_call_and_keeps_holding(self):
        state = {}
        run_national(FakeClient(quota_error("4393s")), state)
        client2 = FakeClient()
        later = NOW + dt.timedelta(minutes=30)
        msg, bodies = run_national(client2, state, now=later)
        self.assertIsNone(msg)
        self.assertEqual((client2.calls, bodies), (0, []))        # APIも本文取得も行わない
        self.assertEqual(keys(state), [])                          # 引き続き保留(既読にしない)
        d = na.build_embeds({}, later)[0]["news"]["embeds"][0]["description"]
        self.assertIn("API呼び出しなし（利用上限の待機中のため送信していません）", d)
        self.assertIn("・次回の再試行: 08:59 JST以降の実行で再試行", d)

    def test_notice_is_suppressed_for_same_error_then_recovers(self):
        state = {}
        run_national(FakeClient(quota_error("4393s")), state)
        state[na.ALERT_STATE_PREFIX + "gemini_national:quota"] = NOW.isoformat()     # 通知済み
        later = NOW + dt.timedelta(minutes=30)
        run_national(FakeClient(), state, now=later)
        _, notified, suppressed = na.build_embeds(state, later)
        self.assertEqual((notified, suppressed), ([], ["gemini_national:quota"]))     # 同じ異常は抑止
        # 待ち時間が過ぎて復旧 → 記事は処理され、抑止が解除される
        recovered = NOW + dt.timedelta(seconds=4394)
        client = FakeClient()
        msg, _ = run_national(client, state, now=recovered)
        self.assertIsNotNone(msg)
        self.assertIn("見出しA", msg)
        self.assertEqual(client.calls, 1)
        self.assertGreater(len(keys(state)), 0)                    # 成功した記事は既読になる
        self.assertNotIn(na.ALERT_STATE_PREFIX + "gemini_national:quota", state)
        # 復旧後の再発は12時間以内でもすぐ通知される
        again = recovered + dt.timedelta(hours=1)
        run_national(FakeClient(quota_error("100s", daily=True)), state, now=again, items=[{"title": "新しい", "url": "https://news.yahoo.co.jp/pickup/9", "published": ""}])
        self.assertEqual(na.build_embeds(state, again)[1], ["gemini_national:quota"])

    def test_changed_error_is_not_buried_by_cooldown(self):
        state = {na.ALERT_STATE_PREFIX + "gemini_national:quota": NOW.isoformat()}
        later = NOW + dt.timedelta(hours=2)
        run_national(FakeClient(busy_error(), busy_error(), busy_error()), state, now=later)
        _, notified, suppressed = na.build_embeds(state, later)
        self.assertEqual((notified, suppressed), (["gemini_national:http503"], []))
        d = na.build_embeds(state, later)[0]["news"]["embeds"][0]["description"]
        self.assertIn("Geminiが一時的に混み合っています", d)
        self.assertIn("・失敗: 要約処理1回（API呼び出し3回、待機12秒）", d)
        self.assertIn("・再試行方針: 待ち時間を置いて最大2回まで再試行しましたが回復しませんでした", d)
        self.assertIn("・次回の再試行: 次回の定期実行（通常30分ごと）。時刻は確定できません", d)
        self.assertNotIn("JST以降", d)                              # 確定できない時刻は出さない

    def test_transient_error_recovers_within_run(self):
        state = {}
        client = FakeClient(busy_error())
        msg, _ = run_national(client, state)
        self.assertIsNotNone(msg)
        self.assertEqual(client.calls, 2)
        self.assertEqual(na.issues(), [])

    def test_unparseable_response_is_held_not_marked_read(self):
        state = {}
        msg, _ = run_national(FakeClient(Resp("これはJSONではない")), state)
        self.assertIsNone(msg)
        self.assertEqual(keys(state), [])
        self.assertEqual(na.issues()[0]["kind"], "gemini_national:other")

    def test_empty_summary_keeps_old_behaviour(self):
        state = {}
        msg, _ = run_national(FakeClient(Resp("[]")), state)
        self.assertIsNone(msg)
        self.assertEqual(len(keys(state)), len(ITEMS))             # 正常に0件 → 従来どおり既読(無限再試行しない)
        self.assertEqual([i["kind"] for i in na.issues()], ["gemini_national:empty"])

    def test_other_deliveries_continue_when_gemini_blocked(self):
        # Gemini待機中でも、ローカルニュース・スポーツ振り分けなど他の処理は呼び出せる(全国ニュースの関数だけがNoneを返す)
        state = {}
        run_national(FakeClient(quota_error("4393s")), state)
        sports = [{"title": "プロ野球 巨人が勝利", "url": "https://news.yahoo.co.jp/pickup/s1", "published": ""}]
        na.reset()
        with mock.patch.object(fn, "fetch_rss_items", return_value=sports + [dict(i) for i in ITEMS]), \
             mock.patch.object(fn, "fetch_yahoo_pickup_body", return_value="x"), mock.patch.object(fn, "YAHOO_CATEGORY_RSS", []), \
             contextlib.redirect_stdout(io.StringIO()):
            msg, sp = fn.build_national_news_message(FakeClient(), state, NOW + dt.timedelta(minutes=30), gg.Guard(state, clock=lambda: NOW + dt.timedelta(minutes=30)))
        self.assertIsNone(msg)
        self.assertEqual([i["title"] for i in sp], ["プロ野球 巨人が勝利"])          # スポーツ振り分けは従来どおり


# ------------------------------------------------------------------ 経済ニュース整理 ----
MARKET_ITEMS = [{"title": f"日経平均が上昇{i} - 日本経済新聞", "url": f"https://example.com/m{i}", "published": "10/09 07:00"} for i in range(2)]


class MarketCurationTest(unittest.TestCase):
    def test_curate_uses_caller_and_falls_back_when_blocked(self):
        g = make_guard()
        with self.assertRaises(gg.GeminiCallError):
            g.call(Flaky(quota_error("4393s")))
        client = FakeClient()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(mnc.curate(MARKET_ITEMS, client, "m", caller=g.call))
        self.assertEqual(client.calls, 0)                           # 待機中は呼ばない
        self.assertIsInstance(mnc.LAST_ERROR, gg.GeminiBlocked)
        na.reset()
        fn.record_gemini_failure("gemini_market", "market", "経済ニュースの材料整理(Gemini)", mnc.LAST_ERROR, "継続(従来の箇条書き表示で配信)")
        d = na.build_embeds({}, NOW)[0]["market"]["embeds"][0]["description"]
        self.assertIn("影響: 配信は継続。従来の箇条書き表示で配信。", d)
        self.assertNotIn("保留", d)

    def test_curate_without_caller_unchanged(self):
        client = FakeClient(Resp("not json"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(mnc.curate(MARKET_ITEMS, client, "m"))
        self.assertEqual(client.calls, 1)


class AlertApiTest(unittest.TestCase):
    def test_clear_removes_only_matching_kinds(self):
        st = {"_alert:gemini_national": "x", "_alert:gemini_national:quota": "x", "_alert:gemini_nationalx": "x", "_alert:gemini_market:quota": "x"}
        self.assertEqual(na.clear(st, "gemini_national"), 2)
        self.assertEqual(sorted(st), ["_alert:gemini_market:quota", "_alert:gemini_nationalx"])

    def test_old_calls_without_extra_render_as_before(self):
        na.reset()
        na.record("anzn_fetch", "local", "あんぜんねっと", "取得失敗 HTTP 403: Forbidden", "一部スキップ(新着を取得できず)")
        d = na.build_embeds({}, NOW)[0]["local"]["embeds"][0]["description"].split("\n")
        self.assertEqual(d, ["**あんぜんねっと**", "取得先へのアクセスが拒否されました", "影響: 一部をスキップ。新着を取得できず。"])


if __name__ == "__main__":
    unittest.main()
