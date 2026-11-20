"""Gemini の一時的な混雑(503)の異常通知を、連続失敗で初めて出すことのテスト(2026-11-20)。
実際のDiscord・Geminiには接続しない。
実行: .venv/bin/python -m unittest tests.test_news_alerts_transient
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import news_alerts as na  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
KIND_503 = "gemini_market:http503"
KIND_429 = "gemini_market:quota"


def run(state, kind, detail="Gemini APIエラー HTTP 503: UNAVAILABLE", now=None):
    """1回の実行を模擬する: 異常を記録 → streak更新 → 通知本文を組み立てる。"""
    na.reset()
    na.record(kind, "market", "経済ニュースの急変判定(Gemini)", detail, "継続(次の便で通常配信)")
    na.update_streaks(state)
    messages, notified, suppressed = na.build_messages(state, now or dt.datetime(2026, 11, 20, 8, 27, tzinfo=JST))
    return messages, notified, suppressed


class TransientStreakTest(unittest.TestCase):
    def tearDown(self):
        na.reset()

    def test_first_503_is_held_back(self):
        state = {}
        messages, notified, suppressed = run(state, KIND_503)
        self.assertEqual(messages, {})
        self.assertEqual(notified, [])
        self.assertEqual(suppressed, [KIND_503])
        self.assertEqual(state[na.STREAK_PREFIX + KIND_503], 1)
        self.assertNotIn(na.ALERT_STATE_PREFIX + KIND_503, state)  # 通知していないので12時間抑止の時刻も付けない

    def test_second_consecutive_503_notifies(self):
        state = {}
        run(state, KIND_503)
        messages, notified, suppressed = run(state, KIND_503)
        self.assertEqual(notified, [KIND_503])
        self.assertEqual(suppressed, [])
        self.assertIn("混み合っています", messages["market"])
        self.assertEqual(state[na.STREAK_PREFIX + KIND_503], 2)

    def test_success_run_resets_streak(self):
        state = {}
        run(state, KIND_503)                       # 1回目の失敗
        na.reset()
        na.update_streaks(state)                   # 次の実行は失敗が起きなかった(連続が途切れた)
        self.assertNotIn(na.STREAK_PREFIX + KIND_503, state)
        messages, notified, _ = run(state, KIND_503)   # 再び失敗しても1回目扱い
        self.assertEqual(notified, [])
        self.assertEqual(state[na.STREAK_PREFIX + KIND_503], 1)

    def test_clear_on_recovery_resets_streak(self):
        state = {}
        run(state, KIND_503)
        self.assertEqual(na.clear(state, "gemini_market"), 1)
        self.assertNotIn(na.STREAK_PREFIX + KIND_503, state)

    def test_quota_error_still_notifies_immediately(self):
        state = {}
        messages, notified, suppressed = run(state, KIND_429, detail="Gemini APIエラー HTTP 429: RESOURCE_EXHAUSTED")
        self.assertEqual(notified, [KIND_429])
        self.assertEqual(suppressed, [])
        self.assertIn("利用上限", messages["market"])

    def test_cooldown_still_applies_after_notification(self):
        state = {}
        run(state, KIND_503)                       # 1回目: 保留
        run(state, KIND_503)                       # 2回目: 通知(flush() が通知時刻を記録する)
        state[na.ALERT_STATE_PREFIX + KIND_503] = dt.datetime(2026, 11, 20, 8, 27, tzinfo=JST).isoformat()
        messages, notified, suppressed = run(state, KIND_503, now=dt.datetime(2026, 11, 20, 12, 27, tzinfo=JST))
        self.assertEqual(notified, [])
        self.assertEqual(suppressed, [KIND_503])   # 12時間以内は再通知しない

    def test_streak_keys_are_not_pruned_as_seen_records(self):
        # "_" 始まりの管理キーは既送信記録の掃除で消えず、整数値でも比較エラーにならない
        state = {na.STREAK_PREFIX + KIND_503: 1}
        self.assertTrue(state[na.STREAK_PREFIX + KIND_503] == 1)
        self.assertTrue(na.STREAK_PREFIX.startswith("_"))


if __name__ == "__main__":
    unittest.main()
