"""日経CNBCの空振り遮断と起動間隔(2026-10-12)。通信なし(gh・Gemini・RSSはモック)。
実行: .venv/bin/python -m unittest tests.test_nikkei_gating
"""
import datetime as dt
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import dispatch_workflows as dw  # noqa: E402
import fetch_nikkei_cnbc_digest as nk  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
MON = dt.datetime(2026, 10, 12, 10, 0, tzinfo=JST)     # 月曜
SAT = dt.datetime(2026, 10, 10, 10, 0, tzinfo=JST)     # 土曜


class ZeroNewGeminiTest(unittest.TestCase):
    def run_main(self, videos, state):
        client = mock.MagicMock()
        sent = []
        with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_MARKET": "https://discord.com/api/webhooks/1/x", "GEMINI_API_KEY": "K"}), \
             mock.patch("google.genai.Client", return_value=client), \
             mock.patch.object(nk, "load_state", return_value=state), \
             mock.patch.object(nk, "save_state"), \
             mock.patch.object(nk, "fetch_channel_videos", return_value=videos), \
             mock.patch.object(nk, "fetch_transcript", return_value="字幕" * 30) as tr, \
             mock.patch.object(nk, "send_embeds", side_effect=lambda w, e, **k: (sent.extend(e), True)[1]), \
             mock.patch("sys.argv", ["x"]), mock.patch("sys.stdout", new_callable=io.StringIO):
            code = nk.main()
        return code, client, sent, tr

    def test_no_new_videos_means_no_gemini_call(self):
        videos = [{"video_id": "a", "title": "t", "url": "u", "published": "", "description": "d"}]
        code, client, sent, tr = self.run_main(videos, {"notified": {"a": MON.isoformat()}})
        self.assertEqual(code, 0)
        self.assertEqual(client.models.generate_content.call_count, 0)
        self.assertEqual(sent, [])
        self.assertEqual(tr.call_count, 0)                  # 字幕取得も行わない

    def test_pending_retry_alone_does_not_call_gemini(self):
        videos = [{"video_id": "p", "title": "要約待ち", "url": "u", "published": "", "description": "d"}]
        state = {"notified": {"p": MON.isoformat()}, "pending_summary": {"p": MON.isoformat()}}
        code, client, sent, _ = self.run_main(videos, state)
        self.assertEqual(code, 0)
        self.assertEqual(client.models.generate_content.call_count, 0)

    def test_one_new_video_calls_gemini_once(self):
        videos = [{"video_id": "n", "title": "新着", "url": "u", "published": "", "description": "概要欄の本文。" * 6}]
        client = mock.MagicMock()
        client.models.generate_content.return_value = mock.Mock(text='[{"index": 0, "lines": ["・要点"]}]')
        with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_MARKET": "https://discord.com/api/webhooks/1/x", "GEMINI_API_KEY": "K"}), \
             mock.patch("google.genai.Client", return_value=client), \
             mock.patch.object(nk, "load_state", return_value={"notified": {}}), \
             mock.patch.object(nk, "save_state"), \
             mock.patch.object(nk, "fetch_channel_videos", return_value=videos), \
             mock.patch.object(nk, "fetch_transcript", return_value=None), \
             mock.patch.object(nk, "send_embeds", return_value=True), \
             mock.patch("sys.argv", ["x"]), mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(nk.main(), 0)
        self.assertEqual(client.models.generate_content.call_count, 1)


class DispatchIntervalTest(unittest.TestCase):
    def test_weekday_market_hours_are_60_minutes(self):
        self.assertEqual(dw.nikkei_min_interval(MON.replace(hour=8)), 60)
        self.assertEqual(dw.nikkei_min_interval(MON.replace(hour=16, minute=59)), 60)

    def test_outside_market_hours_are_240_minutes(self):
        self.assertEqual(dw.nikkei_min_interval(MON.replace(hour=7, minute=59)), 240)
        self.assertEqual(dw.nikkei_min_interval(MON.replace(hour=17)), 240)
        self.assertEqual(dw.nikkei_min_interval(MON.replace(hour=23)), 240)

    def test_weekend_is_240_minutes(self):
        self.assertEqual(dw.nikkei_min_interval(SAT.replace(hour=10)), 240)

    def test_jst_conversion(self):
        # UTC 01:30 = JST 10:30(平日) → 60分
        self.assertEqual(dw.nikkei_min_interval(dt.datetime(2026, 10, 12, 1, 30, tzinfo=dt.timezone.utc)), 60)

    def simulate_day(self, day_start):
        """5分刻みの起動係を1日回し、日経CNBCの起動回数を数える(直前の起動時刻を最新runとする)。"""
        now = day_start
        last = None
        launches = 0
        while now < day_start + dt.timedelta(days=1):
            run = {"status": "completed", "createdAt": last.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"), "event": "workflow_dispatch"} if last else None
            go, _ = dw.decide(run, dw.nikkei_min_interval(now), now)
            if go:
                launches += 1
                last = now
            now += dt.timedelta(minutes=5)
        return launches

    def test_daily_launch_count_is_bounded(self):
        weekday = self.simulate_day(MON.replace(hour=0, minute=0))
        weekend = self.simulate_day(SAT.replace(hour=0, minute=0))
        self.assertLessEqual(weekday, 13)
        self.assertLessEqual(weekend, 6)
        self.assertGreater(weekday, 0)

    def test_decide_respects_computed_interval(self):
        created = (MON - dt.timedelta(minutes=50)).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        run = {"status": "completed", "createdAt": created, "event": "schedule"}
        self.assertFalse(dw.decide(run, dw.nikkei_min_interval(MON), MON)[0])                       # 60分未満
        run70 = {"status": "completed", "createdAt": (MON - dt.timedelta(minutes=70)).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"), "event": "schedule"}
        self.assertTrue(dw.decide(run70, dw.nikkei_min_interval(MON), MON)[0])                      # 60分超で起動

    def test_main_dry_run_uses_time_based_interval(self):
        created = (MON - dt.timedelta(minutes=70)).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        with mock.patch.object(dw, "latest_run", return_value={"status": "completed", "createdAt": created, "event": "schedule"}), \
             mock.patch.object(dw, "check_anzn_liveness", return_value=None), \
             mock.patch.object(dw, "decide", wraps=dw.decide) as dec, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dw.main(["--dry-run"])
        intervals = {c.args[1] for c in dec.call_args_list}
        self.assertTrue(intervals <= {10, 120, 60, 240})                 # 判定に使われる間隔は設定値のいずれか(時刻に依存しない)
        self.assertEqual(len(dec.call_args_list), 3)


if __name__ == "__main__":
    unittest.main()
