"""地域ニュース(#webhook_local)の重複配信修正(2026-10-13)のテスト。通信なし・Gemini不使用。
実行: .venv/bin/python -m unittest tests.test_local_dedupe_fixes
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 13, 13, 30, tzinfo=JST)
ACCIDENT_AGO = "【事故】バイクと衝突 車の“運転手”か1人死亡 軽自動車が横転･炎上 埼玉･上尾市 - YouTube"
ACCIDENT_IMG = "【画像】バイクと衝突し車炎上 車の運転手が死亡、バイクの男子高校生は重傷 埼玉・上尾市 - ライブドアニュース"
ACCIDENT_DATED = "バイクと衝突軽自動車が横転炎上車の運転手か1人死亡埼玉上尾市2026年10月11日掲載"


def item(title, url):
    return {"title": title, "url": url, "published": ""}


def recent_state(*titles):
    return {fn.LOCAL_TITLE_KEY_PREFIX + fn.local_title_key(t): NOW.isoformat() for t in titles}


class UrlKeyTest(unittest.TestCase):
    def test_same_url_same_key(self):
        self.assertEqual(fn.normalize_url("https://a.example/n/1"), fn.normalize_url("https://a.example/n/1"))

    def test_http_and_https_are_same_key(self):
        self.assertEqual(fn.normalize_url("http://a.example/n/1"), fn.normalize_url("https://a.example/n/1"))

    def test_tracking_params_ignored(self):
        self.assertEqual(fn.normalize_url("https://a.example/n/1?utm_source=x&utm_medium=y#top"),
                         fn.normalize_url("https://a.example/n/1"))

    def test_host_case_and_trailing_slash(self):
        self.assertEqual(fn.normalize_url("https://A.Example/n/1/"), fn.normalize_url("https://a.example/n/1"))

    def test_root_path_kept(self):
        self.assertEqual(fn.normalize_url("https://a.example/"), "https://a.example/")

    def test_legacy_http_keys_migrated_without_resend(self):
        state = {"http://a.example/n/1": NOW.isoformat(), "https://b.example/x": NOW.isoformat()}
        out = fn.migrate_url_keys(state)
        self.assertNotIn("http://a.example/n/1", out)
        self.assertIn("https://a.example/n/1", out)
        self.assertIn("https://b.example/x", out)

    def test_migration_keeps_existing_https_record(self):
        state = {"http://a.example/n/1": "old", "https://a.example/n/1": "new"}
        self.assertEqual(fn.migrate_url_keys(state)["https://a.example/n/1"], "new")


class StateCorruptTest(unittest.TestCase):
    def _load_from(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "news_seen.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            with mock.patch.object(fn, "STATE_PATH", path):
                return fn.load_seen_state()

    def test_broken_json_raises_instead_of_empty(self):
        with self.assertRaises(fn.StateCorruptError):
            self._load_from("{ broken")

    def test_non_dict_raises(self):
        with self.assertRaises(fn.StateCorruptError):
            self._load_from("[1, 2]")

    def test_valid_state_loads(self):
        self.assertEqual(self._load_from(json.dumps({"https://a.example/x": "t"})), {"https://a.example/x": "t"})

    def test_main_stops_without_sending_on_corrupt_state(self):
        env = {"DISCORD_WEBHOOK_LOCAL": "https://discord.invalid/x"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(fn, "load_seen_state", side_effect=fn.StateCorruptError("broken")), \
             mock.patch.object(fn, "send_to_discord") as send, \
             mock.patch.object(fn, "save_seen_state") as save:
            with self.assertRaises(SystemExit) as cm:
                fn.main()
        self.assertEqual(cm.exception.code, 1)
        send.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
