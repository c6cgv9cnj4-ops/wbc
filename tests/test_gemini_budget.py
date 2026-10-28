"""Gemini 日次予算(gemini_budget)と、Guard の予算ゲートの検証。外部通信なし(合成データ・モック)。
実行: .venv/bin/python -m unittest tests.test_gemini_budget
"""
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import gemini_budget as gb  # noqa: E402
import gemini_guard as gg  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime(2026, 10, 28, 9, 0, tzinfo=JST)


def state_with(n, now=NOW):
    st = {}
    for _ in range(n):
        gb.record(st, now)
    return st


class ThresholdTest(unittest.TestCase):
    """上限20・余力4の場合: 急変は20まで、国内要約は16まで、低優先は12まで。"""

    def test_critical_uses_full_limit(self):
        self.assertTrue(gb.allowed(state_with(19), NOW, gb.PRIORITY_CRITICAL, limit=20))
        self.assertFalse(gb.allowed(state_with(20), NOW, gb.PRIORITY_CRITICAL, limit=20))

    def test_high_keeps_reserve_for_critical(self):
        self.assertTrue(gb.allowed(state_with(15), NOW, gb.PRIORITY_HIGH, limit=20))
        self.assertFalse(gb.allowed(state_with(16), NOW, gb.PRIORITY_HIGH, limit=20))

    def test_low_is_cut_first(self):
        self.assertTrue(gb.allowed(state_with(11), NOW, gb.PRIORITY_LOW, limit=20))
        self.assertFalse(gb.allowed(state_with(12), NOW, gb.PRIORITY_LOW, limit=20))


class RecordTest(unittest.TestCase):
    def test_record_counts_and_returns_total(self):
        st = {}
        self.assertEqual(gb.record(st, NOW), 1)
        self.assertEqual(gb.record(st, NOW), 2)
        self.assertEqual(gb.used(st, NOW), 2)

    def test_counter_resets_on_pacific_date(self):
        # 2026-10-28 15:00 JST = 太平洋時間 10/27 23:00 / 17:00 JST = 太平洋時間 10/28 01:00
        before_reset = datetime.datetime(2026, 10, 28, 15, 0, tzinfo=JST)
        after_reset = datetime.datetime(2026, 10, 28, 17, 0, tzinfo=JST)
        st = state_with(5, before_reset)
        self.assertEqual(gb.used(st, after_reset), 0)
        self.assertEqual(gb.used(st, before_reset), 5)

    def test_old_days_are_pruned(self):
        st = {}
        gb.record(st, NOW - datetime.timedelta(days=5))
        gb.record(st, NOW)
        keys = [k for k in st if k.startswith(gb.KEY_PREFIX)]
        self.assertEqual(len(keys), 1)

    def test_broken_value_counts_as_zero(self):
        st = {gb._key(gb.window_date(NOW)): "oops"}
        self.assertEqual(gb.used(st, NOW), 0)


class LimitEnvTest(unittest.TestCase):
    def test_env_overrides_limit(self):
        with mock.patch.dict(os.environ, {"GEMINI_DAILY_LIMIT": "5"}):
            self.assertEqual(gb.daily_limit(), 5)

    def test_invalid_or_nonpositive_env_falls_back(self):
        for raw in ("abc", "0", "-3"):
            with mock.patch.dict(os.environ, {"GEMINI_DAILY_LIMIT": raw}):
                self.assertEqual(gb.daily_limit(), gb.DEFAULT_DAILY_LIMIT)


class GuardBudgetGateTest(unittest.TestCase):
    def guard(self, state):
        return gg.Guard(state, clock=lambda: NOW, sleep=lambda s: None)

    def test_over_budget_blocks_without_calling(self):
        st = state_with(12)
        fn = mock.Mock(return_value="never")
        with mock.patch.dict(os.environ, {"GEMINI_DAILY_LIMIT": "20"}):
            with self.assertRaises(gg.GeminiBlocked) as cm:
                self.guard(st).call(fn, priority=gb.PRIORITY_LOW)
        fn.assert_not_called()
        self.assertEqual(cm.exception.reason, "budget")

    def test_critical_still_runs_when_low_is_blocked(self):
        st = state_with(12)
        fn = mock.Mock(return_value="ok")
        with mock.patch.dict(os.environ, {"GEMINI_DAILY_LIMIT": "20"}):
            self.assertEqual(self.guard(st).call(fn, priority=gb.PRIORITY_CRITICAL), "ok")
        fn.assert_called_once()
        self.assertEqual(gb.used(st, NOW), 13)

    def test_successful_call_is_recorded(self):
        st = {}
        self.guard(st).call(lambda: "ok", priority=gb.PRIORITY_HIGH)
        self.assertEqual(gb.used(st, NOW), 1)

    def test_no_priority_keeps_old_behavior(self):
        st = state_with(50)
        self.assertEqual(self.guard(st).call(lambda: "ok"), "ok")
        self.assertEqual(gb.used(st, NOW), 50)   # 予算の記録に触れない


if __name__ == "__main__":
    unittest.main()
