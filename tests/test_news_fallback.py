"""全国主要ニュース: Gemini失敗時も配信を継続する(見出し＋リンクのフォールバック)と、終了コード0の検証。
通信なし。Gemini はモック(402/429/503/想定外の例外)。実行: .venv/bin/python -m unittest tests.test_news_fallback
"""
import contextlib
import datetime as dt
import io
import os
import sys
import unittest
from unittest import mock

from google.genai import errors

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402
import gemini_guard as gg  # noqa: E402
import news_alerts as na  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 11, 12, 0, tzinfo=JST)
ITEMS = [{"title": f"国内ニュース見出し{i}", "url": f"https://news.yahoo.co.jp/pickup/{i}", "published": "10/11 11:00"} for i in range(4)]
SECRET = "SECRET-KEY-ABC123"


def api_error(kind):
    if kind == "402":
        return errors.ClientError(402, {"error": {"code": 402, "status": "RESOURCE_EXHAUSTED", "message": "Your prepayment credits are depleted."}})
    if kind == "429":
        return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                                  "details": [{"@type": "x.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
    if kind == "503":
        return errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})
    if kind == "timeout":
        return TimeoutError("timed out")
    return RuntimeError(f"unexpected {SECRET}")


def client_raising(exc):
    client = mock.MagicMock()
    client.models.generate_content.side_effect = exc
    return client


class BuildFallbackTest(unittest.TestCase):
    def setUp(self):
        na.reset()
        self.patches = [mock.patch.object(gg, "BASE_DELAY_SECONDS", 0.0), mock.patch.object(gg, "MAX_SINGLE_WAIT_SECONDS", 0.0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def build(self, client, state=None):
        state = {} if state is None else state
        with mock.patch.object(fn, "fetch_rss_items", return_value=[dict(i) for i in ITEMS]), \
             mock.patch.object(fn, "YAHOO_CATEGORY_RSS", []), \
             mock.patch.object(fn, "fetch_yahoo_pickup_body", return_value="本文"), \
             contextlib.redirect_stdout(io.StringIO()):
            return fn.build_national_news_message(client, state, NOW, gg.Guard(state, clock=lambda: NOW, sleep=lambda s: None)), state

    def test_api_errors_produce_link_payload_without_exception(self):
        for kind in ("402", "429", "503", "timeout", "unexpected"):
            with self.subTest(kind=kind):
                na.reset()
                (msg, sports), state = self.build(client_raising(api_error(kind)))
                self.assertIsNotNone(msg)
                self.assertIn("要約なし", msg)
                self.assertEqual(msg.count("- ["), len(ITEMS))
                self.assertIn("<https://news.yahoo.co.jp/pickup/3>", msg)
                self.assertNotIn(SECRET, msg)                               # 想定外の例外文も配信・通知に出さない
                self.assertEqual(len([k for k in state if not k.startswith("_")]), len(ITEMS))   # 配信済みとして記録(管理キーは除く)

    def test_payload_is_a_valid_discord_text(self):
        (msg, _), _ = self.build(client_raising(api_error("402")))
        self.assertFalse([ln for ln in msg.split("\n") if ln.startswith(("# ", "## "))][1:])   # 見出しは1つだけ
        self.assertLess(len(msg), 2000)

    def test_blocked_by_quota_wait_also_falls_back(self):
        state = {gg.BACKOFF_PREFIX + "quota": (NOW + dt.timedelta(hours=1)).isoformat(), gg.BASIS_PREFIX + "quota": "x"}
        client = mock.MagicMock()
        (msg, _), _ = self.build(client, state)
        self.assertIn("要約なし", msg)
        self.assertEqual(client.models.generate_content.call_count, 0)     # 待機中はAPIを呼ばない

    def test_successful_summary_is_unchanged(self):
        client = mock.MagicMock()
        client.models.generate_content.return_value = mock.Mock(text='[{"index": 0, "category": "政治", "headline": "見出しA", "summary_lines": ["a"]}]')
        (msg, _), _ = self.build(client)
        self.assertIn("📌 【政治】見出しA", msg)
        self.assertNotIn("要約なし", msg)


class MainExitCodeTest(unittest.TestCase):
    """main() 全体: Gemini失敗でも国内ニュースが配信され、終了コード0(sys.exit されない)。"""

    def run_main(self, exc):
        import fetch_news
        sent = []
        env = {"DISCORD_WEBHOOK_NEWS": "https://discord.com/api/webhooks/1/x", "GEMINI_API_KEY": SECRET, "ANZN_SOURCE": "mac"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(gg, "BASE_DELAY_SECONDS", 0.0), mock.patch.object(gg, "MAX_SINGLE_WAIT_SECONDS", 0.0), \
             mock.patch("google.genai.Client", return_value=client_raising(exc)), \
             mock.patch.object(fetch_news, "load_seen_state", return_value={}), \
             mock.patch.object(fetch_news, "save_seen_state"), \
             mock.patch.object(fetch_news, "check_dispatcher_gap", return_value=None), \
             mock.patch.object(fetch_news, "fetch_rss_items", return_value=[dict(i) for i in ITEMS]), \
             mock.patch.object(fetch_news, "YAHOO_CATEGORY_RSS", []), \
             mock.patch.object(fetch_news, "fetch_yahoo_pickup_body", return_value="本文"), \
             mock.patch.object(fetch_news, "build_market_message", return_value=("market", False)), \
             mock.patch.object(fetch_news, "send_to_discord", side_effect=lambda url, msg: (sent.append(msg), True)[1]), \
             mock.patch.object(fetch_news, "send_embed_to_discord", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            na.reset()
            exit_code = 0
            try:
                fetch_news.main()
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else 1
        return exit_code, sent

    def test_exit_code_zero_and_national_links_sent_for_each_failure(self):
        for kind in ("402", "429", "503", "timeout", "unexpected"):
            with self.subTest(kind=kind):
                code, sent = self.run_main(api_error(kind))
                self.assertEqual(code, 0)
                national = [m for m in sent if "国内外の主要トップニュース" in m]
                self.assertEqual(len(national), 1)
                self.assertIn("<https://news.yahoo.co.jp/pickup/0>", national[0])
                self.assertNotIn(SECRET, "".join(sent))

    def test_gemini_failure_is_reported_in_alert_not_as_error(self):
        code, sent = self.run_main(api_error("402"))
        self.assertEqual(code, 0)
        self.assertTrue(any("要約なし" in m for m in sent))


if __name__ == "__main__":
    unittest.main()
