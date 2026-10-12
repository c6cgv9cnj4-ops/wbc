"""Gemini 429対策の検証(2026-10-12): 日経CNBCの要約をまとめて1回に、再試行はgemini_guard、
週末マーケットは同じガードで呼ぶ。429発生時の挙動とバッチ集約をモックで確認。通信なし。
実行: .venv/bin/python -m unittest tests.test_gemini_batch_429
"""
import datetime as dt
import io
import json
import os
import sys
import unittest
from unittest import mock

from google.genai import errors

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_nikkei_cnbc_digest as nk  # noqa: E402
import gemini_guard as gg  # noqa: E402
import market_news_curation as mnc  # noqa: E402
import news_alerts as na  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 12, 10, 0, tzinfo=JST)
LONG = "日経平均は続伸。半導体株が買われ、為替は1ドル158円台で推移した。" * 2


def daily_429():
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                              "details": [{"@type": "x.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})


def busy_503():
    return errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.models = self

    def generate_content(self, **kw):
        self.calls += 1
        o = self.outcomes.pop(0) if self.outcomes else mock.Mock(text=self.ok_text)
        if isinstance(o, Exception):
            raise o
        return o

    ok_text = "[]"


def items(n, transcript=True):
    return [{"title": f"動画{i}", "text": LONG, "is_transcript": transcript} for i in range(n)]


class NikkeiBatchTest(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch.object(gg, "BASE_DELAY_SECONDS", 0.0), mock.patch.object(gg, "MAX_SINGLE_WAIT_SECONDS", 0.0),
                  mock.patch.dict(os.environ, {"GEMINI_API_KEY": "KEY-NOT-PRINTED"})]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def run_batch(self, client, its, state=None):
        state = {} if state is None else state
        with mock.patch("google.genai.Client", return_value=client), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            res = nk.summarize_batch(its, state)
        return res, state, out.getvalue()

    def test_many_videos_one_call(self):
        client = FakeClient(mock.Mock(text=json.dumps([{"index": i, "lines": [f"要点{i}"]} for i in range(5)])))
        res, _, _ = self.run_batch(client, items(5))
        self.assertEqual(client.calls, 1)                              # 5本で1回だけ呼ぶ(従来は最大10回)
        self.assertEqual(res[2], ["・要点2"])                           # 番号どおりに対応づけ

    def test_daily_429_is_not_retried_and_no_per_video_calls(self):
        client = FakeClient(daily_429())
        res, state, out = self.run_batch(client, items(5))
        self.assertEqual(client.calls, 1)
        self.assertEqual(res, [None] * 5)                              # 全件『要約なし(リンクのみ)』へフォールバック
        self.assertIn("全5件を要約なしで配信", out)
        self.assertTrue(any(k.startswith(gg.BACKOFF_PREFIX) for k in state))   # 待機状態をstateに保存

    def test_transient_503_retried_with_backoff_then_succeeds(self):
        client = FakeClient(busy_503(), busy_503(), mock.Mock(text=json.dumps([{"index": 0, "lines": ["・ok"]}])))
        res, _, _ = self.run_batch(client, items(1))
        self.assertEqual(client.calls, 3)
        self.assertEqual(res, [["・ok"]])

    def test_transient_gives_up_after_limited_retries(self):
        client = FakeClient(busy_503(), busy_503(), busy_503(), busy_503())
        res, _, _ = self.run_batch(client, items(2))
        self.assertEqual(client.calls, 3)                              # 上限(2回再試行)で打ち切り
        self.assertEqual(res, [None, None])

    def test_waiting_state_prevents_calls_next_run(self):
        state = {}
        self.run_batch(FakeClient(daily_429()), items(2), state)
        client2 = FakeClient()
        res, _, _ = self.run_batch(client2, items(2), state)
        self.assertEqual(client2.calls, 0)
        self.assertEqual(res, [None, None])

    def test_invalid_json_and_bad_index_fall_back(self):
        res, _, _ = self.run_batch(FakeClient(mock.Mock(text="これはJSONではない")), items(2))
        self.assertEqual(res, [None, None])
        res, _, _ = self.run_batch(FakeClient(mock.Mock(text=json.dumps([{"index": 9, "lines": ["x"]}, {"index": 1, "lines": ["・y"]}]))), items(2))
        self.assertEqual(res, [None, ["・y"]])

    def test_no_key_or_short_text_makes_no_call(self):
        client = FakeClient()
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            res, _, _ = self.run_batch(client, items(2))
        self.assertEqual((client.calls, res), (0, [None, None]))
        client = FakeClient()
        res, _, _ = self.run_batch(client, [{"title": "短い", "text": "x", "is_transcript": False}])
        self.assertEqual((client.calls, res), (0, [None]))

    def test_main_sends_link_only_embeds_on_429(self):
        videos = [{"video_id": f"v{i}", "title": f"動画{i}", "url": f"https://www.youtube.com/watch?v=v{i}",
                   "published": "10/12 09:00", "description": "概要欄の本文。" * 10} for i in range(3)]
        sent = []
        client = FakeClient(daily_429())
        env = {"DISCORD_WEBHOOK_MARKET": "https://discord.com/api/webhooks/1/x", "GEMINI_API_KEY": "KEY-NOT-PRINTED"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("google.genai.Client", return_value=client), \
             mock.patch.object(nk, "load_state", return_value={"notified": {}}), \
             mock.patch.object(nk, "save_state"), \
             mock.patch.object(nk, "fetch_channel_videos", return_value=list(reversed(videos))), \
             mock.patch.object(nk, "fetch_transcript", return_value=None), \
             mock.patch.object(nk, "send_embeds", side_effect=lambda w, e, **k: (sent.extend(e), True)[1]), \
             mock.patch("sys.argv", ["fetch_nikkei_cnbc_digest.py"]), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            code = nk.main()
        self.assertEqual(code, 0)
        self.assertEqual(client.calls, 1)                              # 3本まとめて1回
        self.assertEqual(len(sent), 3)
        self.assertNotIn("KEY-NOT-PRINTED", json.dumps(sent, ensure_ascii=False))


class WeekendGuardTest(unittest.TestCase):
    def setUp(self):
        na.reset()
        self.p = [mock.patch.object(gg, "BASE_DELAY_SECONDS", 0.0), mock.patch.object(gg, "MAX_SINGLE_WAIT_SECONDS", 0.0)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def news(self):
        return [{"title": f"日経平均が上昇{i} - 日本経済新聞", "url": f"https://example.com/m{i}", "published": "10/12 07:00"} for i in range(2)]

    def test_curate_with_guard_daily_429_falls_back_and_waits(self):
        state = {}
        guard = gg.Guard(state, clock=lambda: NOW, sleep=lambda s: None)
        client = FakeClient(daily_429())
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertIsNone(mnc.curate(self.news(), client, "m", caller=guard.call))
            self.assertIsNone(mnc.curate(self.news(), client, "m", caller=guard.call))
        self.assertEqual(client.calls, 1)                              # 2回目は待機中のため呼ばない
        self.assertIsInstance(mnc.LAST_ERROR, (gg.GeminiCallError, gg.GeminiBlocked))

    def test_curate_without_guard_unchanged(self):
        client = FakeClient(daily_429())
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertIsNone(mnc.curate(self.news(), client, "m"))
        self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
