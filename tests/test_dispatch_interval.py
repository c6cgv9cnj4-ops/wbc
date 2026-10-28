"""news.yml の起動間隔(2時間)と、起動係の停止検知しきい値の整合を確認する。通信なし。
実行: .venv/bin/python -m unittest tests.test_dispatch_interval
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import dispatch_workflows as dw  # noqa: E402
import fetch_news as fn  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 11, 12, 0, tzinfo=JST)


def run_at(minutes_ago, status="completed"):
    created = (NOW - dt.timedelta(minutes=minutes_ago)).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return {"status": status, "createdAt": created, "event": "schedule"}


class DispatcherIntervalTest(unittest.TestCase):
    def test_news_interval_is_240_minutes(self):
        self.assertEqual(dict(dw.WORKFLOWS)["news.yml"], 240)

    def test_news_not_dispatched_before_240_minutes(self):
        go, _ = dw.decide(run_at(180), 240, NOW)
        self.assertFalse(go)

    def test_news_dispatched_after_240_minutes(self):
        go, _ = dw.decide(run_at(245), 240, NOW)
        self.assertTrue(go)

    def test_no_run_yet_dispatches(self):
        self.assertTrue(dw.decide(None, 240, NOW)[0])


class DispatcherGapAlertTest(unittest.TestCase):
    """起動間隔(2時間)どおりの実行で停止検知が誤発報しないこと。"""

    def gap_alert(self, minutes_since_last):
        from unittest import mock as m
        import news_alerts as na
        na.reset()
        last = (NOW - dt.timedelta(minutes=minutes_since_last)).isoformat()
        state = {fn.LAST_RUN_KEY: last}
        with m.patch.dict(os.environ, {"MAC_DISPATCHER": "on"}):
            fn.check_dispatcher_gap(state, NOW)
        return [i["kind"] for i in na.issues()]

    def test_normal_four_hour_cadence_does_not_alert(self):
        # 2026-10-27: news.yml を4時間間隔に変更。通常の間隔(約240分)では通知しない
        self.assertEqual(self.gap_alert(245), [])
        self.assertEqual(self.gap_alert(265), [])

    def test_real_stop_still_alerts(self):
        self.assertEqual(self.gap_alert(330), ["dispatcher_gap"])

    def test_threshold_value(self):
        self.assertEqual(fn.DISPATCHER_GAP_ALERT_MINUTES, 270)


if __name__ == "__main__":
    unittest.main()
