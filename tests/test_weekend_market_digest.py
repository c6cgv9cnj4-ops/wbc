"""weekend_market_digest の単体テスト（通信・Discord・Geminiは使わない）。
実行: .venv/bin/python -m unittest tests.test_weekend_market_digest
"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import weekend_market_digest as w  # noqa: E402
import news_alerts  # noqa: E402

SUN = dt.datetime(2026, 10, 4, 20, 0, tzinfo=w.JST)  # 日曜20:00 JST
SAT = dt.datetime(2026, 10, 3, 8, 0, tzinfo=w.JST)   # 土曜8:00 JST


def ev(date, country="USD", impact="High", title="CPI m/m"):
    return {"title": title, "country": country, "date": date, "impact": impact, "forecast": "0.3%", "previous": "0.2%"}


class EventsTest(unittest.TestCase):
    def test_filter_country_impact_and_jst(self):
        events = [ev("2026-10-05T08:30:00-04:00"), ev("2026-10-05T08:30:00-04:00", country="EUR"),
                  ev("2026-10-05T08:30:00-04:00", impact="Low"),
                  ev("2026-10-04T19:50:00-04:00", country="JPY", impact="Medium", title="Tankan Manufacturing Index")]
        out = w.filter_events(events, SUN)
        self.assertEqual([e["country"] for e in out], ["JPY", "USD"])  # 時刻順
        self.assertEqual(out[1]["at_jst"], dt.datetime(2026, 10, 5, 21, 30, tzinfo=w.JST))  # ET→JST(+13h)
        self.assertIn("日銀短観", w.render_event(out[0]))
        self.assertIn("10/05(月) 08:50", w.render_event(out[0]))

    def test_title_translation_order(self):
        self.assertTrue(w.ja_title("ADP Non-Farm Employment Change").startswith("ADP雇用統計"))
        self.assertTrue(w.ja_title("Non-Farm Employment Change").startswith("米雇用統計"))
        self.assertTrue(w.ja_title("Tokyo Core CPI y/y").startswith("東京都区部コアCPI"))
        self.assertTrue(w.ja_title("Final GDP Price Index q/q").startswith("GDPデフレーター"))
        self.assertEqual(w.ja_title("Some Unknown Event"), "Some Unknown Event")

    def test_filter_window(self):
        out = w.filter_events([ev("2026-10-03T08:30:00-04:00"), ev("2026-10-20T08:30:00-04:00")], SUN)
        self.assertEqual(out, [])  # 過去と8日より先は出さない

    def test_bad_rows_skipped(self):
        self.assertEqual(w.filter_events([{"country": "USD", "impact": "High", "date": "bad"}, {}], SUN), [])


class NewsTest(unittest.TestCase):
    def test_cutoff_is_friday_close(self):
        self.assertEqual(w.news_cutoff("sat", SAT), dt.datetime(2026, 10, 2, 15, 30, tzinfo=w.JST))
        self.assertEqual(w.news_cutoff("sun", SUN), dt.datetime(2026, 10, 2, 15, 30, tzinfo=w.JST))

    def test_select_news_window_and_state(self):
        items = [{"url": "https://a/1?utm=x", "published_at": "2026-10-02T07:00:00+00:00"},  # 16:00 JST → 期間内
                 {"url": "https://a/2", "published_at": "2026-10-02T05:00:00+00:00"},        # 14:00 JST → 期間外
                 {"url": "https://a/3", "published_at": None},                               # 日時不明 → 残す
                 {"url": "https://a/4"}, {"url": "https://a/1"}]                              # state済み / バッチ内重複
        out = w.select_news(items, {"https://a/4": "2026-10-03T08:00:00+09:00"}, w.news_cutoff("sun", SUN))
        self.assertEqual([i["url"] for i in out], ["https://a/1?utm=x", "https://a/3"])


class MessageTest(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(w, "market_lines", lambda edition: ["## 📈 市場の状況", "- dummy"])
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_sunday_events_failure_is_explicit(self):
        msg = w.build_message("sun", SUN, [], None, None, RuntimeError("HTTP 403"))
        self.assertIn("来週の重要経済イベント：取得できませんでした", msg)
        self.assertIn("月曜に見る材料", msg)

    def test_saturday_has_no_events_section(self):
        msg = w.build_message("sat", SAT, [], None, None, None)
        self.assertNotIn("来週の重要経済イベント", msg)
        self.assertIn("金曜大引け以降の重要ニュース", msg)

    def test_news_fallback_without_gemini(self):
        items = [{"title": "日経平均、続落 - 日本経済新聞", "url": "https://x/1", "published": "10/03 09:00"}]
        msg = w.build_message("sat", SAT, items, None, None, None)
        self.assertIn("- [日経平均、続落 - 日本経済新聞](<https://x/1>)", msg)  # 既存の箇条書きフォールバック


class MainTest(unittest.TestCase):
    def run_main(self, send_ok, ff_fail=False):
        items = [{"title": "T", "url": "https://x/9", "published_at": None, "published": "-"}]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(w, "STATE_PATH", os.path.join(d, "s.json")), \
                mock.patch.object(w, "now_jst", lambda: SUN), \
                mock.patch.object(w, "market_lines", lambda e: ["- dummy"]), \
                mock.patch.object(w.fn, "fetch_economy_news_candidates", lambda: items), \
                mock.patch.object(w, "fetch_ff_events", (lambda: (_ for _ in ()).throw(RuntimeError("HTTP 403"))) if ff_fail
                                  else (lambda: [ev("2026-10-05T08:30:00-04:00")])), \
                mock.patch.object(w.fn, "send_to_discord", lambda url, msg: send_ok) as _, \
                mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_MARKET": "https://discord.com/api/webhooks/1/T"}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            news_alerts.reset()
            rc = w.main(["--edition", "sun"])
            state = w.load_state(os.path.join(d, "s.json"))
            kinds = [i["kind"] for i in news_alerts.issues()]
        return rc, state, kinds

    def test_success_records_state(self):
        rc, state, _ = self.run_main(True)
        self.assertEqual(rc, 0)
        self.assertIn("https://x/9", state)

    def test_send_failure_does_not_record(self):
        rc, state, kinds = self.run_main(False)
        self.assertEqual(rc, 1)
        self.assertNotIn("https://x/9", state)
        self.assertIn("discord_send_weekend", kinds)

    def test_ff_failure_keeps_digest_and_alerts(self):
        rc, state, kinds = self.run_main(True, ff_fail=True)
        self.assertEqual(rc, 0)
        self.assertIn("https://x/9", state)
        self.assertIn("ff_calendar", kinds)


if __name__ == "__main__":
    unittest.main()
