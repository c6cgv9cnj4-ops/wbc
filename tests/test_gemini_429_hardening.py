"""Gemini 429 恒久対策(2026-10-13)のテスト。
  - gemini_guard: 呼び出しの計測ログ([GEMINI_CALL]/[GEMINI_429])、分あたり上限の即時失敗オプション
  - fetch_culture_news: guard経由・失敗時の箇条書きフォールバック・日次待機のstate持ち越し
実際のGemini・Discord・公式サイトには接続しない。待機は sleep を差し替え、時刻は固定する。
実行: .venv/bin/python -m unittest tests.test_gemini_429_hardening
"""
import contextlib
import datetime as dt
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from google.genai import errors  # noqa: E402

import fetch_culture_news as fc  # noqa: E402
import gemini_guard as gg  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 13, 10, 0, tzinfo=JST)
SECRET = "SECRET-LIKE-TEXT-must-not-appear-in-logs"


def quota_error(delay="4393s", daily=True):
    quota_id = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if daily else "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "message": f"You exceeded your current quota. {SECRET}",
                      "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
                                  *([{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}] if delay else [])]}}
    return errors.ClientError(429, body)


def busy_error():
    return errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "This model is currently experiencing high demand."}})


def ok_response(text="[]", tokens=None):
    resp = mock.MagicMock()
    resp.text = text
    resp.candidates = []
    resp.usage_metadata.total_token_count = tokens
    return resp


def client_raising(err):
    client = mock.MagicMock()
    client.models.generate_content.side_effect = err
    return client


class GuardLogTest(unittest.TestCase):
    def test_success_line_has_count_task_status_and_tokens(self):
        buf = io.StringIO()
        resp = ok_response(tokens=123)
        with contextlib.redirect_stdout(buf):
            gg.Guard({}).call(lambda: resp, task="unit_task")
        out = buf.getvalue()
        self.assertRegex(out, r"\[GEMINI_CALL\] count=\d+ task=unit_task status=200 tokens=123")

    def test_429_line_is_logged_without_secret_text(self):
        buf = io.StringIO()

        def boom():
            raise quota_error()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(gg.GeminiCallError):
                gg.Guard({}, sleep=lambda s: None).call(boom, task="unit_task")
        out = buf.getvalue()
        self.assertIn("[GEMINI_429] count=", out)
        self.assertIn("task=unit_task", out)
        self.assertIn("error=RESOURCE_EXHAUSTED", out)
        self.assertNotIn(SECRET, out)

    def test_count_is_cumulative_per_attempt(self):
        before = gg.total_calls()
        buf = io.StringIO()
        flaky = [busy_error(), ok_response()]

        def fn():
            item = flaky.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        with contextlib.redirect_stdout(buf):
            gg.Guard({}, sleep=lambda s: None).call(fn, task="unit_task")
        self.assertEqual(gg.total_calls() - before, 2)  # 503(1回目) + 成功(2回目)
        self.assertIn("[GEMINI_ERR] count=", buf.getvalue())
        self.assertIn("[GEMINI_CALL] count=", buf.getvalue())

    def test_rate_limit_is_not_retried_when_disabled(self):
        sleeps = []
        calls = []

        def boom():
            calls.append(1)
            raise quota_error(delay="5s", daily=False)
        guard = gg.Guard({}, sleep=sleeps.append, retry_rate_limit=False)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(gg.GeminiCallError):
                guard.call(boom, task="unit_task")
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_rate_limit_is_still_retried_by_default(self):
        sleeps = []
        calls = []

        def boom():
            calls.append(1)
            raise quota_error(delay="5s", daily=False)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(gg.GeminiCallError):
                gg.Guard({}, sleep=sleeps.append).call(boom, task="unit_task")
        self.assertEqual(len(calls), gg.MAX_RETRIES + 1)


# 2026-10-13 大会通知の廃止に伴い、地方大会の Fast-Fail テスト(LocalBadmintonFastFailTest)を除去。
# 共有の Gemini ガード(GuardLogTest)と文化ニュースのフォールバック(CultureFallbackTest)は残す。


class CultureFallbackTest(unittest.TestCase):
    def no_sleep_guard(self, state):
        return gg.Guard(state, sleep=lambda s: None)

    def test_topic_filter_failure_falls_back_to_bullets(self):
        state = {}
        client = client_raising(quota_error())
        items = [{"title": "記事A", "url": "https://a.test/1"}, {"title": "記事B", "url": "https://a.test/2"}]
        with mock.patch.object(fc, "TOPIC_SOURCES", {"🍎 テスト": ["https://feed.test/rss"]}), \
                mock.patch.object(fc, "fetch_direct_rss", return_value=items), \
                contextlib.redirect_stdout(io.StringIO()):
            embeds = fc.build_topic_embeds(client, state, NOW, guard=self.no_sleep_guard(state))
        self.assertEqual(len(embeds), 1)
        self.assertIn("AI選別なし・未確認", embeds[0]["title"])
        self.assertIn("[記事A](https://a.test/1)", embeds[0]["description"])
        self.assertEqual(client.models.generate_content.call_count, 1)

    def test_daily_block_is_kept_in_state_and_skips_next_run(self):
        state = {}
        client = client_raising(quota_error(daily=True))
        items = [{"title": "記事A", "url": "https://a.test/1"}]
        with mock.patch.object(fc, "TOPIC_SOURCES", {"🍎 テスト": ["https://feed.test/rss"]}), \
                mock.patch.object(fc, "fetch_direct_rss", return_value=items), \
                contextlib.redirect_stdout(io.StringIO()):
            fc.build_topic_embeds(client, state, NOW, guard=self.no_sleep_guard(state))
            self.assertTrue(any(k.startswith(gg.BACKOFF_PREFIX) for k in state))
            # 次回の実行(同じstateを読み直した想定): 待機中なので呼び出さない
            state2 = dict(state)
            state2["seen_urls"] = {}
            embeds = fc.build_topic_embeds(client, state2, NOW, guard=self.no_sleep_guard(state2))
        self.assertEqual(client.models.generate_content.call_count, 1)  # 2回目の実行では呼ばれない
        self.assertEqual(len(embeds), 1)  # 見出しのみで継続

    def test_503_is_retried_then_falls_back(self):
        state = {}
        client = client_raising(busy_error())
        sleeps = []
        items = [{"title": "記事A", "url": "https://a.test/1"}]
        with mock.patch.object(fc, "TOPIC_SOURCES", {"🍎 テスト": ["https://feed.test/rss"]}), \
                mock.patch.object(fc, "fetch_direct_rss", return_value=items), \
                contextlib.redirect_stdout(io.StringIO()):
            embeds = fc.build_topic_embeds(client, state, NOW, guard=gg.Guard(state, sleep=sleeps.append))
        self.assertEqual(client.models.generate_content.call_count, gg.MAX_RETRIES + 1)
        self.assertEqual(len(sleeps), gg.MAX_RETRIES)
        self.assertEqual(len(embeds), 1)

    def test_exhibition_extraction_failure_falls_back_to_unverified_list(self):
        state = {"seen_urls": {}}
        client = client_raising(quota_error())
        candidates = [{"title": "展覧会X 開催中", "url": "https://ex.test/1"}]
        with contextlib.redirect_stdout(io.StringIO()):
            embeds, consumed = fc.build_exhibition_embeds_from_candidates(
                client, candidates, state, NOW, guard=self.no_sleep_guard(state))
        self.assertEqual(len(embeds), 1)
        self.assertIn("未確認", embeds[0]["title"])
        self.assertIn("[展覧会X 開催中](https://ex.test/1)", embeds[0]["description"])
        self.assertEqual(consumed, set())
        self.assertIn("https://ex.test/1", state["seen_urls"])  # 次回も同じ見出しを出し続けない


if __name__ == "__main__":
    unittest.main()
