# -*- coding: utf-8 -*-
"""
通信遮断ガード（offline_guard）の自己テスト。
実通信が試みられた場合に必ず NetworkBlocked になり、試行が記録されることを確認する。
ここでの「接続試行」は全て遮断され、外部へ届くことはない。
"""
import os
import socket
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_guard as G  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import badminton_results_free as R  # noqa: E402
import badminton_jp_ledger as L  # noqa: E402
import badspi_ranking_snapshot as BS  # noqa: E402

import requests  # noqa: E402

GUARD_TEST_KEY = "GUARD_TEST_KEY_NOT_REAL"


class OfflineGuardTest(unittest.TestCase):
    def setUp(self):
        G.reset_attempts()

    def tearDown(self):
        G.reset_attempts()

    def test_raw_socket_connect_is_blocked(self):
        s = socket.socket()
        try:
            with self.assertRaises(G.NetworkBlocked):
                s.connect(("127.0.0.1", 9))
        finally:
            s.close()
        self.assertEqual(len(G.ATTEMPTS), 1)

    def test_create_connection_is_blocked(self):
        with self.assertRaises(G.NetworkBlocked):
            socket.create_connection(("example.invalid", 443), timeout=1)
        self.assertEqual(len(G.ATTEMPTS), 1)

    def test_urlopen_is_blocked(self):
        with self.assertRaises(G.NetworkBlocked):
            urllib.request.urlopen("https://example.invalid/", timeout=1)
        self.assertEqual(len(G.ATTEMPTS), 1)

    def test_requests_get_and_post_are_blocked(self):
        with self.assertRaises(G.NetworkBlocked):
            requests.get("https://api.badmintonapi.com/v1/results/yesterday", timeout=1)
        with self.assertRaises(G.NetworkBlocked):
            requests.post("https://discord.com/api/webhooks/x/y", json={}, timeout=1)
        self.assertEqual(len(G.ATTEMPTS), 2)

    def test_real_api_helpers_cannot_reach_network_without_mock(self):
        # 実在の関数を、モックせずに呼んでも通信は届かず NetworkBlocked になる（偽キーでも同様）
        with self.assertRaises(G.NetworkBlocked):
            R.fetch_page(GUARD_TEST_KEY, None)
        with self.assertRaises(G.NetworkBlocked):
            L.fetch_search(GUARD_TEST_KEY, "test")
        with self.assertRaises(G.NetworkBlocked):
            BS.fetch_html("https://www.badspi.jp/ranking/")
        self.assertGreaterEqual(len(G.ATTEMPTS), 3)

    def test_every_badminton_test_module_loads_the_guard(self):
        here = os.path.dirname(os.path.abspath(__file__))
        names = ["test_badminton_results_free.py", "test_badspi_ranking_snapshot.py",
                 "test_badminton_jp_ledger.py", "test_state_branch.py", "test_local_badminton_run.py"]
        for name in names:
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                self.assertIn("import offline_guard", fh.read(), name)


if __name__ == "__main__":
    unittest.main()
