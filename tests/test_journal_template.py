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

    def _days(self, text):
        d = datetime.date(2026, 9, 21)
        return jo.build_days([{"tid": "1", "name": "2026/09/21", "date": d, "url": "u", "text": text}])

    def test_blank_and_long_posts_are_accepted(self):
        self.assertEqual(self._days(EXPECTED), {})   # 全項目空欄=見出しだけ → 書いていない日と同じ(エラーにならない)
        long_text = EXPECTED.replace("■ ここから連想したこと\n・", "■ ここから連想したこと\n・" + "架空の連想がつづく。" * 800)
        self.assertGreater(self._obs_text(long_text)["chars"], 7000)   # 長文でも問題なく数えられる

    def test_headings_leave_no_footprint(self):
        """固定見出しは観測しない: 「本当はどうしたい？」の願望/問いかけ、「連想」の語が数えられない。"""
        tpl = jo.strip_template(EXPECTED)
        self.assertEqual(tpl, "")
        self.assertGreater(sum(jo.count_markers(EXPECTED).values()), 0)   # 除外前は痕跡がある
        self.assertEqual(jo.extract_terms(tpl), [])

    def test_body_under_headings_is_observed_exactly_as_without_template(self):
        body = {"今の頭の中": "架空の散歩のことを考えていた。本を読みたい。",
                "ここから連想したこと": "架空の連想で写真展に行きたい気がする。どうなるかな？",
                "その他": "架空のカメラ"}
        with_tpl = EXPECTED
        for h, text in body.items():
            with_tpl = with_tpl.replace(f"■ {h}\n・", f"■ {h}\n・{text}")
        without = "\n".join("・" + t for t in body.values())
        a, b = self._obs_text(with_tpl), self._obs_text(without)
        self.assertEqual(a["chars"], b["chars"])
        self.assertEqual(a["terms"], b["terms"])
        self.assertEqual(a["markers"], b["markers"])
        self.assertEqual(a["markers"]["want"], 2)       # 本人の「読みたい」「行きたい」だけ
        self.assertEqual(a["markers"]["question"], 1)   # 本人の「かな？」だけ
        self.assertIn("連想", a["terms"])               # 本文中の「連想」は数える

    def test_heading_with_text_on_same_line_keeps_text(self):
        rec = self._obs_text("■ 今の頭の中 架空の散歩\n■ 本当はどうしたい? 架空の旅に行きたい")
        self.assertIn("散歩", rec["terms"])
        self.assertEqual(rec["markers"]["want"], 1)
        self.assertEqual(rec["markers"]["question"], 0)


if __name__ == "__main__":
    unittest.main()
