# -*- coding: utf-8 -*-
"""#モーニングジャーナルの投稿ガイドライン(テンプレート)設定の検証。通信はすべて差し替え、
本文は架空の文章のみを使う。実行: .venv/bin/python -m unittest discover -s tests -v"""
import contextlib
import datetime
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import journal_observe as jo  # noqa: E402
import set_journal_forum_guidelines as g  # noqa: E402

EXPECTED = ("【モーニングジャーナル】\n\n■ 今の頭の中\n・\n\n■ 昨日から残っていること\n・\n\n"
            "■ 気になっていること\n・\n\n■ 本当はどうしたい？\n・\n\n■ 最近よく考えること\n・\n\n"
            "■ 今日思いついたこと\n・\n\n■ ここから連想したこと\n・\n\n■ その他\n・")


def _resp(code, body):
    r = mock.Mock()
    r.status_code, r.json.return_value = code, body
    return r


class TemplateTest(unittest.TestCase):
    def test_exact_text(self):
        self.assertEqual(g.JOURNAL_TEMPLATE, EXPECTED)
        self.assertLessEqual(len(g.JOURNAL_TEMPLATE), g.TOPIC_MAX)

    def test_dry_run_sends_nothing(self):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["x"]), \
                mock.patch.object(g.requests, "get", side_effect=AssertionError("network")), \
                mock.patch.object(g.requests, "patch", side_effect=AssertionError("network")), \
                contextlib.redirect_stdout(buf):
            self.assertEqual(g.main(), 0)
        self.assertIn("■ ここから連想したこと", buf.getvalue())

    def test_apply_changes_only_topic_of_forum(self):
        with mock.patch.object(g.requests, "get", return_value=_resp(200, {"type": 15, "name": "モーニングジャーナル", "topic": ""})), \
                mock.patch.object(g.requests, "patch", return_value=_resp(200, {"topic": EXPECTED})) as p, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(g.apply("tok", "123"), 0)
        self.assertEqual(p.call_args.kwargs["json"], {"topic": EXPECTED})   # topic 以外は送らない

    def test_apply_refuses_non_forum_and_is_idempotent(self):
        with mock.patch.object(g.requests, "get", return_value=_resp(200, {"type": 0, "topic": ""})), \
                mock.patch.object(g.requests, "patch") as p, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(g.apply("tok", "1"), 1)
            p.assert_not_called()
        with mock.patch.object(g.requests, "get", return_value=_resp(200, {"type": 15, "topic": EXPECTED})), \
                mock.patch.object(g.requests, "patch") as p, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(g.apply("tok", "1"), 0)
            p.assert_not_called()


class TemplateUsageTest(unittest.TestCase):
    """テンプレートを使った投稿(架空の文章)が既存の取得・観測でそのまま扱えること。"""

    def _obs_text(self, text):
        d = datetime.date(2026, 9, 21)
        days = jo.build_days([{"tid": "1", "name": "2026/09/21", "date": d, "url": "u", "text": text}])
        return days[d]

    def test_blank_and_long_posts_are_accepted(self):
        blank = self._obs_text(EXPECTED)                          # 全項目空欄のまま
        self.assertGreater(blank["chars"], 0)
        long_text = EXPECTED.replace("■ ここから連想したこと\n・", "■ ここから連想したこと\n・" + "架空の連想がつづく。" * 800)
        self.assertGreater(self._obs_text(long_text)["chars"], 7000)   # 長文でも問題なく数えられる

    def test_heading_footprint_is_known(self):
        """見出しだけの投稿が観測に残す痕跡(報告用に固定)。見出しは毎日同じなので、
        テンプレート導入週には「願望」「問いかけ」の出現率と語「連想」がその分だけ上がる。"""
        rec = self._obs_text(EXPECTED)
        self.assertEqual(sorted(rec["terms"]), ["連想"])
        self.assertEqual(rec["markers"]["want"], 1)       # 「本当はどうしたい？」
        self.assertEqual(rec["markers"]["question"], 1)   # 同上の「？」
        self.assertEqual(sum(v for k, v in rec["markers"].items() if k not in ("want", "question")), 0)


if __name__ == "__main__":
    unittest.main()
