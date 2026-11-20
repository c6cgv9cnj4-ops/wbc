# -*- coding: utf-8 -*-
"""あんぜんねっと経路の耐障害性(通信なし)。
GitHub同期・HTML解析・想定外の例外のどれかで代替経路(公式Mastodon)が止まらないことを確認する。"""
import argparse
import datetime
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import anzn_local  # noqa: E402
import fetch_news  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))


class GhNeverRaisesTest(unittest.TestCase):
    def test_timeout_returns_failure_code(self):
        with mock.patch.object(anzn_local.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 60)):
            code, out, err = anzn_local._gh("api", "x")
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")

    def test_missing_gh_returns_failure_code(self):
        with mock.patch.object(anzn_local.subprocess, "run", side_effect=FileNotFoundError("gh")):
            code, _out, _err = anzn_local._gh("api", "x")
        self.assertNotEqual(code, 0)


class SyncFailureDoesNotRaiseTest(unittest.TestCase):
    def test_actions_keys_unreadable_returns_none(self):
        self.assertEqual(anzn_local.fetch_actions_sent_keys(gh=lambda *a, **k: (0, "[1, 2]", "")), set())  # 想定外の型でも例外にしない
        self.assertIsNone(anzn_local.fetch_actions_sent_keys(gh=lambda *a, **k: (1, "", "boom")))

    def test_publish_exception_returns_false(self):
        def boom(*a, **k):
            raise RuntimeError("api down")
        now = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=JST)
        self.assertFalse(anzn_local.publish_mac_sent({"anzn:11217F:1"}, now, gh=boom))


class ParseFailureIsFetchFailureTest(unittest.TestCase):
    def test_parse_exception_marks_fetch_failed_and_returns_empty(self):
        resp = mock.Mock(status_code=200, content=b"<html></html>")
        resp.raise_for_status.return_value = None
        with mock.patch.object(fetch_news.requests, "get", return_value=resp), \
             mock.patch.object(fetch_news, "_parse_anzn_blocks", side_effect=ValueError("html changed")), \
             mock.patch.object(fetch_news.news_alerts, "record"):
            items = fetch_news.fetch_anzn_new_arrivals()
        self.assertEqual(items, [])
        self.assertFalse(fetch_news.ANZN_LAST_FETCH_OK)


class FallbackAlwaysReachedTest(unittest.TestCase):
    def test_unexpected_exception_still_runs_mastodon_fallback(self):
        calls = {}

        def fake_fallback(state, now, webhook, anzn_ok, dry_run=False):
            calls["anzn_ok"] = anzn_ok

        with tempfile.TemporaryDirectory() as d:
            args = argparse.Namespace(dry_run=True, state=os.path.join(d, "s.json"),
                                      env_file=os.path.join(d, "none.env"), import_state=None)
            with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_LOCAL": "https://example.invalid/x"}), \
                 mock.patch.object(anzn_local.fetch_news, "fetch_anzn_new_items", side_effect=KeyError("x")), \
                 mock.patch.object(anzn_local, "run_mastodon_fallback", side_effect=fake_fallback), \
                 mock.patch.object(anzn_local.news_alerts, "issues", return_value=[]):
                rc = anzn_local.run(args)
        self.assertEqual(rc, 0)
        self.assertIn("anzn_ok", calls)
        self.assertFalse(calls["anzn_ok"])


if __name__ == "__main__":
    unittest.main()
