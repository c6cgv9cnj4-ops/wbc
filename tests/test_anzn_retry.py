# -*- coding: utf-8 -*-
"""あんぜんねっと本体取得の再試行(5xx・タイムアウトのみ再試行、通信なし)。"""
import os
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import fetch_news  # noqa: E402


class _Resp:
    def __init__(self, code):
        self.status_code = code
        self.reason = "X"


class AnznRetryTest(unittest.TestCase):
    def _run(self, side_effects):
        with mock.patch.object(fetch_news.requests, "get", side_effect=side_effects) as get, \
             mock.patch.object(fetch_news.time, "sleep") as sleep:
            try:
                resp = fetch_news._anzn_get_with_retry(waits=(5, 15))
            except Exception as e:  # noqa: BLE001
                resp = e
        return get, sleep, resp

    def test_500_then_200_recovers(self):
        get, sleep, resp = self._run([_Resp(500), _Resp(200)])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(resp.status_code, 200)
        sleep.assert_called_once_with(5)

    def test_timeout_then_200_recovers(self):
        get, sleep, resp = self._run([requests.Timeout("t"), _Resp(200)])
        self.assertEqual(resp.status_code, 200)

    def test_persistent_500_gives_up_after_all_attempts(self):
        get, sleep, resp = self._run([_Resp(500), _Resp(500), _Resp(500)])
        self.assertEqual(get.call_count, 3)
        self.assertIsInstance(resp, requests.HTTPError)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5, 15])

    def test_403_is_not_retried(self):
        get, sleep, resp = self._run([_Resp(403), _Resp(200)])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(resp.status_code, 403)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
