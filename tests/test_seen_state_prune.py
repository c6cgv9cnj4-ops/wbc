"""既送信記録の期限掃除(prune_old_entries)の回帰テスト(2026-10-29)。外部通信なし。

背景: 全項目を文字列比較していたため、整数の管理項目 _gemini_budget:YYYY-MM-DD で
      TypeError ('>=' not supported between 'int' and 'str') となり、ニュース配信が停止した。
確認: 管理項目は削除・変換されないこと、日時の解析と期限判定が正しいこと、解析できない値は残すこと。
実行: .venv/bin/python -m unittest tests.test_seen_state_prune
"""
import contextlib
import copy
import datetime as dt
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 29, 9, 0, tzinfo=JST)
RECENT = (NOW - dt.timedelta(days=1)).isoformat()
OLD = (NOW - dt.timedelta(days=20)).isoformat()
REAL_STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "news_seen.json")


def prune_quiet(state, now=NOW):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn.prune_old_entries(state, now)


class ParseSeenAtTest(unittest.TestCase):
    def test_iso_with_offset(self):
        self.assertEqual(fn.parse_seen_at(RECENT), NOW - dt.timedelta(days=1))

    def test_pipe_suffix_is_ignored(self):
        self.assertEqual(fn.parse_seen_at(RECENT + "|12345"), NOW - dt.timedelta(days=1))

    def test_naive_is_treated_as_jst(self):
        parsed = fn.parse_seen_at("2026-10-28T09:00:00")
        self.assertEqual(parsed, dt.datetime(2026, 10, 28, 9, 0, tzinfo=JST))

    def test_utc_offset_is_compared_by_instant(self):
        # 2026-10-14T20:00Z = 2026-10-15T05:00 JST。期限(10-15 09:00 JST)より前なので削除対象。
        utc_value = "2026-10-14T20:00:00+00:00"
        self.assertLess(fn.parse_seen_at(utc_value), NOW - dt.timedelta(days=14))

    def test_invalid_values_return_none(self):
        for value in (None, 5, 3.2, "", "oops", '{"jgb": "126.44"}', "2026-13-99"):
            self.assertIsNone(fn.parse_seen_at(value), repr(value))


class PruneRulesTest(unittest.TestCase):
    def test_old_news_record_is_removed_and_recent_kept(self):
        state = {"https://ex.com/old": OLD, "https://ex.com/new": RECENT}
        self.assertEqual(prune_quiet(state), {"https://ex.com/new": RECENT})

    def test_boundary_exactly_at_cutoff_is_kept(self):
        at_cutoff = (NOW - dt.timedelta(days=fn.STATE_RETENTION_DAYS)).isoformat()
        self.assertIn("k", prune_quiet({"k": at_cutoff}))

    def test_integer_management_key_does_not_raise_and_is_kept(self):
        state = {"_gemini_budget:2026-10-28": 3, "https://ex.com/old": OLD}
        result = prune_quiet(state)
        self.assertEqual(result, {"_gemini_budget:2026-10-28": 3})

    def test_string_management_keys_are_kept_unchanged(self):
        state = {
            "_mastodon_anzn_cursor": OLD + "|117518594402004914",
            "_weekend_market_signature": '{"jgb": "126.44", "nikkei": "63,954.19", "usdjpy": 157.89}',
            "_alert:dispatcher_gap": OLD,
            "_last_run_at": OLD,
        }
        self.assertEqual(prune_quiet(state), state)

    def test_unparseable_news_value_is_kept_not_deleted(self):
        # 解析できない値は「送信済みか不明」。削除すると再配信につながるので残す。
        state = {"https://ex.com/weird": "not-a-date", "https://ex.com/num": 7, "https://ex.com/none": None}
        self.assertEqual(prune_quiet(state), state)

    def test_empty_state(self):
        self.assertEqual(prune_quiet({}), {})

    def test_prune_does_not_mutate_input(self):
        state = {"https://ex.com/old": OLD, "_gemini_budget:2026-10-28": 3}
        snapshot = copy.deepcopy(state)
        prune_quiet(state)
        self.assertEqual(state, snapshot)

    def test_sent_record_still_blocks_resend_after_prune(self):
        url = "https://news.google.com/rss/articles/abc?oc=5"
        state = {fn.normalize_url(url): RECENT, "_gemini_budget:2026-10-28": 3}
        pruned = prune_quiet(state)
        items = [{"title": "同じ記事", "url": url}]
        self.assertEqual(fn.dedupe_new_items(items, "url", pruned, NOW), [])


class RealStateCopyTest(unittest.TestCase):
    """現行の state/news_seen.json をコピーして読み込み・掃除を行う(実ファイルは書き換えない)。"""

    @classmethod
    def setUpClass(cls):
        with open(REAL_STATE, encoding="utf-8") as f:
            cls.raw = json.load(f)

    def test_real_state_with_budget_key_prunes_without_error(self):
        state = copy.deepcopy(self.raw)
        state["_gemini_budget:2026-10-28"] = 3
        result = prune_quiet(state)
        management = [k for k in state if k.startswith("_")]
        for key in management:
            self.assertIn(key, result)
            self.assertEqual(result[key], state[key])
        news_keys = [k for k in state if not k.startswith("_")]
        removed = [k for k in news_keys if k not in result]
        for key in removed:
            self.assertLess(fn.parse_seen_at(state[key]), NOW - dt.timedelta(days=fn.STATE_RETENTION_DAYS))
        for key in news_keys:
            if key in result:
                parsed = fn.parse_seen_at(state[key])
                if parsed is not None:
                    self.assertGreaterEqual(parsed, NOW - dt.timedelta(days=fn.STATE_RETENTION_DAYS))


if __name__ == "__main__":
    unittest.main()
