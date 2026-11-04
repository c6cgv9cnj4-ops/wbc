"""1日1回の定時ワークフロー(バド速報・カルチャー)の実行漏れ救済ルールを確認する。通信なし。
実行: cd tests && python -m unittest test_daily_rescue
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import dispatch_workflows as dw  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))


def jst(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=JST)


def run_created(now_jst, hours_ago_jst=None, at=None):
    created = at or now_jst
    return {"status": "completed", "createdAt": created.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": "schedule"}


class DailyRescueTest(unittest.TestCase):
    def test_before_0630_waits(self):
        go, reason = dw.daily_rescue_rule(None, jst(2026, 11, 5, 6, 20))
        self.assertFalse(go)
        self.assertIn("待機", reason)

    def test_after_noon_never_rescues(self):
        go, reason = dw.daily_rescue_rule(None, jst(2026, 11, 5, 12, 0))
        self.assertFalse(go)
        self.assertIn("救済時間外", reason)

    def test_missed_morning_run_is_rescued_at_0630(self):
        yesterday = jst(2026, 11, 4, 6, 5)  # 前日の定時実行(今日の06:00より前)
        go, _ = dw.daily_rescue_rule(run_created(yesterday), jst(2026, 11, 5, 6, 35))
        self.assertTrue(go)

    def test_no_run_at_all_is_rescued(self):
        go, _ = dw.daily_rescue_rule(None, jst(2026, 11, 5, 7, 0))
        self.assertTrue(go)

    def test_scheduled_run_today_suppresses_rescue(self):
        today_run = run_created(jst(2026, 11, 5, 6, 5))
        go, reason = dw.daily_rescue_rule(today_run, jst(2026, 11, 5, 6, 40))
        self.assertFalse(go)
        self.assertIn("本日の実行あり", reason)

    def test_failed_run_today_is_not_retried(self):
        failed = dict(run_created(jst(2026, 11, 5, 6, 5)), status="completed", conclusion="failure")
        go, _ = dw.daily_rescue_rule(failed, jst(2026, 11, 5, 7, 0))
        self.assertFalse(go)  # 失敗の再試行はしない(無限ループ防止)

    def test_run_at_exactly_0600_counts_as_today(self):
        go, _ = dw.daily_rescue_rule(run_created(jst(2026, 11, 5, 6, 0)), jst(2026, 11, 5, 7, 0))
        self.assertFalse(go)

    def test_both_daily_workflows_use_rescue_rule(self):
        names = {name: rule for name, rule in dw.WORKFLOWS}
        self.assertIs(names["badminton_alerts.yml"], dw.daily_rescue_rule)
        self.assertIs(names["culture_news.yml"], dw.daily_rescue_rule)


if __name__ == "__main__":
    unittest.main()
