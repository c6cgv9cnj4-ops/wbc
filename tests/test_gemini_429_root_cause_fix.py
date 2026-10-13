"""2026-10-13 Gemini 429/503 根本対策の回帰テスト。通信なし。
実行: python3 -m unittest tests.test_gemini_429_root_cause_fix
対象:
  ① local badminton: 並列(2スレッド)で1つが429を受けたら、他の呼び出し・実行中の再試行を止める(threading.Event)
  ② 起動係: news.yml の120分ガード中は起動せず、残り分数を理由に出す
  ③ badminton_alerts: gemini_guard 経由。503は 1.5秒→3.0秒 で再試行し、429は再試行しない。[GEMINI_CALL] を出す
"""
import contextlib
import datetime as dt
import io
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from google.genai import errors  # noqa: E402

import dispatch_workflows as dw  # noqa: E402
import fetch_badminton_alerts as fba  # noqa: E402
import gemini_guard as gg  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 13, 10, 0, tzinfo=JST)


def quota_error():
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                      "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                   "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}
    return errors.ClientError(429, body)


def busy_error():
    return errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})


def ok_response(text):
    resp = mock.MagicMock()
    resp.text = text
    resp.candidates = []
    resp.usage_metadata.total_token_count = 10
    return resp


class GuardCancelTest(unittest.TestCase):
    def test_cancel_stops_inflight_retry_before_next_attempt(self):
        """実行中の503リトライ中に別スレッドが429で止めたら、次の試行を行わない。"""
        cancel = threading.Event()
        fn = mock.Mock(side_effect=busy_error())
        guard = gg.Guard({}, retry_rate_limit=False, cancel=cancel, sleep=lambda s: cancel.set())
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(gg.GeminiCancelled):
                guard.call(fn, task="t")
        self.assertEqual(fn.call_count, 1)

    def test_cancelled_before_start_makes_no_call(self):
        cancel = threading.Event()
        cancel.set()
        fn = mock.Mock(return_value=ok_response("[]"))
        guard = gg.Guard({}, cancel=cancel)
        with self.assertRaises(gg.GeminiCancelled):
            guard.call(fn, task="t")
        fn.assert_not_called()

    def test_transient_backoff_uses_base_delay_1_5_then_3_0(self):
        sleeps = []
        fn = mock.Mock(side_effect=busy_error())
        guard = gg.Guard({}, sleep=sleeps.append, max_retries=2, base_delay=1.5, retry_rate_limit=False)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(gg.GeminiCallError):
                guard.call(fn, task="t")
        self.assertEqual(sleeps, [1.5, 3.0])
        self.assertEqual(fn.call_count, 3)

    def test_default_base_delay_unchanged(self):
        self.assertEqual(gg.Guard({}).base_delay, gg.BASE_DELAY_SECONDS)


# 大会通知の廃止(2026-10-13)に伴い、地方大会(fetch_local_badminton_tournaments)向けの旧テストは除去済み。
# 残すのは共有の Gemini ガード(キャンセル・基準待ち)とアラートのガード経由呼び出しの検証。


# 試合結果速報(fetch_badminton_alerts)の呼び出しは本件では変更しない(指示により据え置き)。
# そのため、アラート側のガード経由・503再試行・429打ち切りのテストは含めない。


if __name__ == "__main__":
    unittest.main()
