# -*- coding: utf-8 -*-
"""本人確認(○△×)の回答形式・保存の検証(2026-09-29)。架空の文章のみ、通信なし。
実行: .venv/bin/python -m unittest discover -s tests -v"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import journal_confirm as jc  # noqa: E402
import journal_observe as jo  # noqa: E402

T0 = "2026-09-27T20:00:00+09:00"
T1 = "2026-10-04T20:00:00+09:00"
INTERP = {"hypotheses": [{"refs": ["O2"], "text": "架空の仮説Aかもしれない", "alternatives": []},
                         {"refs": ["O3", "O5"], "text": "架空の仮説Bかもしれない", "alternatives": []}],
          "continuity": "", "questions": []}


class ParseTest(unittest.TestCase):
    def test_minimal_formats(self):
        got = jc.parse_replies(["1 ○\n2 ×\n3 △", "どれも違う", "見えた"])
        self.assertEqual(got["1"], ("○", ""))
        self.assertEqual(got["2"], ("×", ""))
        self.assertEqual(got["3"], ("△", ""))
        self.assertEqual(got["ALL"], ("どれも違う", ""))
        self.assertEqual(got["VIEW"], ("見えた", ""))

    def test_variants_comments_and_latest_wins(self):
        got = jc.parse_replies(["1○ 架空のコメント", "2 x", "M1 ◯", "5 別のこと：架空の別件", "1 ×", "見えない"])
        self.assertEqual(got["1"], ("×", ""))                 # 後の回答で上書き
        self.assertEqual(got["2"], ("×", ""))
        self.assertEqual(got["M1"], ("○", ""))
        self.assertEqual(got["ALL"], ("別のこと", "架空の別件"))
        self.assertEqual(got["VIEW"], ("見えない", ""))
        self.assertEqual(jc.parse_replies(["1○ 架空のコメント"])["1"], ("○", "架空のコメント"))

    def test_ignores_unrelated_text(self):
        self.assertEqual(jc.parse_replies(["今日は架空の散歩。10時に出発", "1 onigiri"]), {})


class StorageTest(unittest.TestCase):
    def test_rows_separate_ai_hypothesis_and_owner_answer(self):
        rows = jc.build_rows("2026-W39", "週次", INTERP, T0)
        self.assertEqual([r[0] for r in rows], ["2026-W39-1", "2026-W39-2", "2026-W39-ALL", "2026-W39-VIEW"])
        self.assertEqual(rows[0][4], "架空の仮説Aかもしれない")      # C
        self.assertEqual(rows[0][6], jc.UNANSWERED)                 # D は未回答(=保留)
        self.assertEqual(len(jc.CONFIRM_HEADER), len(rows[0]))

    def test_rows_exist_even_without_hypotheses(self):
        rows = jc.build_rows("2026-W39", "週次", None, T0)
        self.assertEqual([r[3] for r in rows], ["全体", "読後"])
        self.assertEqual(jc.build_rows("2026-09", "月次", None, T0, prefix="M", with_view=False), [])

    def test_apply_answers_only_to_previous_batch_and_keeps_x(self):
        rows = jc.build_rows("2026-W39", "週次", INTERP, T0) + jc.build_rows("2026-09", "月次", INTERP, T0, prefix="M",
                                                                           with_view=False)
        since = jc.latest_batch(rows, exclude_period="2026-W40")
        self.assertEqual(since, T0)
        n = jc.apply_answers(rows, jc.parse_replies(["1 ○", "2 ×", "M2 △", "どれも違う"]), T1, since)
        self.assertEqual(n, 4)
        d = {r[0]: r[6] for r in rows}
        self.assertEqual(d["2026-W39-1"], "○")
        self.assertEqual(d["2026-W39-2"], "×")                       # × は残る
        self.assertEqual(d["2026-09-M2"], "△")
        self.assertEqual(d["2026-09-M1"], jc.UNANSWERED)             # 未回答は未回答のまま
        self.assertEqual(d["2026-W39-VIEW"], jc.UNANSWERED)
        # 次の週の行を足しても、前の回答は消えない
        merged = jc.merge_rows(rows, jc.build_rows("2026-W40", "週次", INTERP, T1))
        self.assertEqual({r[0]: r[6] for r in merged}["2026-W39-2"], "×")

    def test_rerun_same_week_keeps_answered_rows(self):
        rows = jc.build_rows("2026-W39", "週次", INTERP, T0)
        rows[0][6] = "○"
        other = {"hypotheses": [{"refs": ["O9"], "text": "再実行で変わった仮説かもしれない", "alternatives": []}]}
        merged = jc.merge_rows(rows, jc.build_rows("2026-W39", "週次", other, T1))
        self.assertEqual(merged[0][4], "架空の仮説Aかもしれない")    # 回答済みの対応は崩さない
        fresh = jc.merge_rows(jc.build_rows("2026-W39", "週次", INTERP, T0),
                              jc.build_rows("2026-W39", "週次", other, T1))
        self.assertEqual(fresh[0][4], "再実行で変わった仮説かもしれない")   # 未回答なら置き換え


class FetchTest(unittest.TestCase):
    def test_only_owner_posts_after_previous_review(self):
        msgs = [
            {"id": "5", "timestamp": "2026-09-28T08:00:00+09:00", "author": {"bot": False}, "content": "1 ○"},
            {"id": "4", "timestamp": "2026-09-27T20:00:05+09:00", "author": {"bot": False}, "webhook_id": "w",
             "content": "週次レビュー本文"},
            {"id": "3", "timestamp": "2026-09-27T20:10:00+09:00", "author": {"bot": True}, "content": "2 ×"},
            {"id": "2", "timestamp": "2026-09-26T09:00:00+09:00", "author": {"bot": False}, "content": "1 ×"},
        ]
        texts = jc.fetch_owner_texts(lambda path, params: msgs if "before" not in params else [], "ch", T0)
        self.assertEqual(texts, ["1 ○"])


class ComposeTest(unittest.TestCase):
    def _obs(self, weeks_text):
        mon = datetime.date(2026, 9, 21)
        th = []
        for w, texts in weeks_text.items():
            for i, t in enumerate(texts):
                d = mon - datetime.timedelta(days=7 * w) + datetime.timedelta(days=i)
                th.append({"tid": f"{w}{i}", "name": d.strftime("%Y/%m/%d"), "date": d, "url": "u", "text": t})
        return jo.observe(jo.build_days(th), mon)

    def test_numbered_hypotheses_and_guide(self):
        obs = jo.observe(jo.build_days(jo.mock_threads(datetime.date(2026, 9, 21), 8)), datetime.date(2026, 9, 21))
        text = "\n".join(jo.compose_messages(obs, INTERP, "gemini", head=jo.HEAD_MARK,
                                             answer_guide=jc.answer_guide(2)))
        self.assertIn("**1.** 架空の仮説Aかもしれない（根拠 O2）", text)
        self.assertIn("**2.** 架空の仮説Bかもしれない（根拠 O3,O5）", text)
        self.assertIn("`1 ○`", text)
        self.assertIn("答えなくてOK＝保留", text)
        self.assertIn("`見えた` / `見えない`", text)

    def test_low_volume_and_no_change_are_normal_output(self):
        base = {w: ["架空の散歩", "架空の散歩", "架空の散歩"] for w in range(1, 8)}
        low = self._obs({**base, 0: ["架空の散歩", "架空の散歩"]})
        text = "\n".join(jo.compose_messages(low, None, "few", head=jo.HEAD_MARK))
        self.assertIn("十分な記録量がないため、明確な差分は確認できません（書いた日 2日）", text)
        same = self._obs({**base, 0: ["架空の散歩", "架空の散歩", "架空の散歩"]})
        text = "\n".join(jo.compose_messages(same, None, "few", head=jo.HEAD_MARK))
        self.assertIn("今週は大きな変化なし", text)
        self.assertNotIn("十分な記録量がない", text)


if __name__ == "__main__":
    unittest.main()
