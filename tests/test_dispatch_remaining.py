"""起動係の120分ガードに『次回実行まで約N分』を表示する判定のテスト(2026-10-13)。通信なし。
実行: .venv/bin/python -m unittest tests.test_dispatch_remaining
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import dispatch_workflows as dw  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc)


def run_elapsed(minutes, status="completed"):
    created = (NOW - dt.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
    return {"status": status, "createdAt": created, "event": "schedule"}


class RemainingMinutesTest(unittest.TestCase):
    def test_elapsed_30_shows_about_90_remaining(self):
        go, reason = dw.decide(run_elapsed(30), 120, NOW)
        self.assertFalse(go)
        self.assertIn("次回実行まで約90分", reason)

    def test_elapsed_119_shows_about_1_remaining(self):
        go, reason = dw.decide(run_elapsed(119), 120, NOW)
        self.assertFalse(go)
        self.assertIn("次回実行まで約1分", reason)

    def test_fractional_remaining_is_rounded_up(self):
        go, reason = dw.decide(run_elapsed(119.5), 120, NOW)
        self.assertFalse(go)
        self.assertIn("次回実行まで約1分", reason)

    def test_elapsed_120_is_dispatchable(self):
        go, _ = dw.decide(run_elapsed(120), 120, NOW)
        self.assertTrue(go)

    def test_no_previous_run_is_dispatchable(self):
        go, reason = dw.decide(None, 120, NOW)
        self.assertTrue(go)
        self.assertEqual(reason, "過去のrunなし")

    def test_busy_run_keeps_existing_skip_reason(self):
        go, reason = dw.decide(run_elapsed(30, status="in_progress"), 120, NOW)
        self.assertFalse(go)
        self.assertIn("実行中/待機中", reason)

    def test_timezone_offset_timestamp_is_handled(self):
        created = (NOW - dt.timedelta(minutes=30)).astimezone(JST).isoformat()  # +09:00 付きの時刻
        go, reason = dw.decide({"status": "completed", "createdAt": created, "event": "schedule"}, 120, NOW.astimezone(JST))
        self.assertFalse(go)
        self.assertIn("次回実行まで約90分", reason)

    def test_missing_or_invalid_timestamp_keeps_existing_behaviour(self):
        # 既存どおり例外を送出する(main() の except で ERROR 表示・他ワークフローは継続)。挙動は変えない
        with self.assertRaises(KeyError):
            dw.decide({"status": "completed", "event": "schedule"}, 120, NOW)
        with self.assertRaises(ValueError):
            dw.decide({"status": "completed", "createdAt": "not-a-date", "event": "schedule"}, 120, NOW)


if __name__ == "__main__":
    unittest.main()
