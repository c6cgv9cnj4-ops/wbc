# -*- coding: utf-8 -*-
"""モーニングジャーナル週次観測(scripts/journal_observe.py / weekly_mindmap.py)の単体テスト。
実行: .venv/bin/python -m unittest discover -s tests -v"""
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import journal_observe as jo  # noqa: E402

MON = datetime.date(2026, 9, 21)


def _thread(d, text, tid="1"):
    return {"tid": tid, "name": d.strftime("%Y/%m/%d"), "date": d,
            "url": f"https://discord.com/channels/g/{tid}", "text": text}


class ExtractTermsTest(unittest.TestCase):
    def test_okurigana_and_mixed_nouns(self):
        got = jo.extract_terms("仕事終わりに写真撮って、昨日の飲み会で話し方を考えた。体調管理を続ける")
        self.assertIn("仕事", got)
        self.assertIn("写真", got)
        self.assertIn("飲み会", got)
        self.assertIn("話し方", got)
        self.assertIn("体調管理", got)   # 助詞「を」の前なので末尾を削らない
        self.assertNotIn("仕事終", got)

    def test_stopwords_urls_and_marker_words_removed(self):
        got = jo.extract_terms("今日は自分の時間。https://example.com/abc 面倒で不安。カメラ")
        for w in ("今日", "自分", "時間", "example", "面倒", "不安"):
            self.assertNotIn(w, got)
        self.assertIn("カメラ", got)


class ReviewRegressionTest(unittest.TestCase):
    """2026-09-26 レビュー指摘の再発防止。"""

    def test_terms_not_truncated_by_common_suffixes(self):
        got = jo.extract_terms("誕生日だった。新宿駅まで歩いた。朝日新聞を読む。朝食会場で。飲み会だった。運動し家で休む")
        for w in ("誕生日", "新宿駅", "朝日新聞", "朝食会場", "飲み会"):
            self.assertIn(w, got)
        self.assertNotIn("運動し家", got)

    def test_marker_false_positives(self):
        c = jo.count_markers("未完了のまま。できたら行く。不安定な天気。つめたい風。ぜいたくな昼。https://x.com/?a=1")
        self.assertEqual(c["done"], 0)
        self.assertEqual(c["worry"], 0)
        self.assertEqual(c["want"], 0)
        self.assertEqual(c["question"], 0)

    def test_monthly_detects_decrease_and_absence(self):
        start = MON - datetime.timedelta(days=27)
        th = [_thread(start - datetime.timedelta(days=28 - i), "仕事の話。散歩した", tid=f"b{i}") for i in range(28)]
        th += [_thread(start + datetime.timedelta(days=i), "散歩した" + ("。仕事" if i % 7 == 0 else ""), tid=f"c{i}")
               for i in range(28)]
        th += [_thread(start - datetime.timedelta(days=28 - i), "カメラ", tid=f"k{i}") for i in range(0, 28, 2)]
        obs = jo.observe(jo.build_days(th), start, unit_days=28, n_base=1, n_hist=1)
        self.assertIn("仕事", [x["term"] for x in obs["terms"]["down"]])
        self.assertIn("カメラ", [x["term"] for x in obs["terms"]["gone"]])
        self.assertEqual(obs["terms"]["steady"], [])

    def test_streak_skips_unwritten_weeks(self):
        th = []
        for w in (0, 1, 3, 4):          # 2週前は1日も書いていない
            th += [_thread(MON - datetime.timedelta(days=7 * w - i), "写真を撮る", tid=f"{w}{i}") for i in range(3)]
        obs = jo.observe(jo.build_days(th), MON)
        steady = {x["term"]: x["streak"] for x in obs["terms"]["steady"]}
        self.assertEqual(steady.get("写真"), 4)

    def test_extra_lines_are_inside_chunks(self):
        obs = jo.observe(jo.build_days(jo.mock_threads(MON, 8)), MON)
        msgs = jo.compose_messages(obs, None, "no_key", head=jo.HEAD_MARK, extra_lines=["参考（ジャーナル外）: x" * 20])
        self.assertTrue(all(len(m) <= 1900 for m in msgs))
        self.assertIn("参考（ジャーナル外）", msgs[0])


class MarkerTest(unittest.TestCase):
    def test_want_marker_avoids_common_false_positives(self):
        c = jo.count_markers("冷たい水。重たい荷物。雨みたい。いったい何。")
        self.assertEqual(c["want"], 0)
        c = jo.count_markers("写真展に行きたい。本を読みたい。レンズが欲しい。")
        self.assertEqual(c["want"], 3)

    def test_other_markers(self):
        c = jo.count_markers("面倒でしんどい。片付けを後回し。不安。楽しかった。眠い。どうするかな。")
        self.assertEqual(c["burden"], 2)
        self.assertEqual(c["undone"], 1)
        self.assertEqual(c["worry"], 1)
        self.assertEqual(c["positive"], 1)
        self.assertEqual(c["body"], 1)
        self.assertEqual(c["question"], 1)


class ObserveTest(unittest.TestCase):
    def setUp(self):
        self.days = jo.build_days(jo.mock_threads(MON, 8))
        self.obs = jo.observe(self.days, MON)

    def _terms(self, kind):
        return [x["term"] for x in self.obs["terms"][kind]]

    def test_detects_planted_changes(self):
        self.assertTrue(self.obs["baseline_ok"])
        self.assertIn("面接", self._terms("new"))
        self.assertIn("部屋", self._terms("new"))
        self.assertIn("散歩", self._terms("down"))
        self.assertIn("飲み会", self._terms("gone"))
        self.assertEqual(self.obs["writing"]["written"], 5)
        self.assertEqual(self.obs["writing"]["base_written_avg"], 4.0)

    def test_deterministic(self):
        again = jo.observe(jo.build_days(jo.mock_threads(MON, 8)), MON)
        self.assertEqual(jo.redacted_summary(again), jo.redacted_summary(self.obs))
        self.assertEqual([i["text"] for i in again["items"]], [i["text"] for i in self.obs["items"]])

    def test_items_have_ids_and_evidence_urls(self):
        ids = [i["id"] for i in self.obs["items"]]
        self.assertEqual(ids, [f"O{n}" for n in range(1, len(ids) + 1)])
        new_item = next(i for i in self.obs["items"] if "「面接」" in i["text"])
        self.assertTrue(new_item["evidence"][0]["url"].startswith("https://discord.com/"))
        self.assertIn("面接", new_item["evidence"][0]["snippet"])

    def test_no_baseline_only_reports_new(self):
        only_now = [t for t in jo.mock_threads(MON, 8) if t["date"] >= MON]
        obs = jo.observe(jo.build_days(only_now), MON)
        self.assertFalse(obs["baseline_ok"])
        for k in ("up", "down", "gone"):
            self.assertEqual(obs["terms"][k], [])
        self.assertTrue(obs["terms"]["new"])
        self.assertTrue(all(not m["direction"] for m in obs["markers"]))

    def test_empty_week(self):
        past = [t for t in jo.mock_threads(MON, 8) if t["date"] < MON]
        obs = jo.observe(jo.build_days(past), MON)
        self.assertEqual(obs["writing"]["written"], 0)
        self.assertEqual(obs["terms"]["gone"], [])   # 書いていない週に「消えた」とは言わない
        self.assertEqual(len(obs["items"]), 1)

    def test_single_occurrence_is_not_a_change(self):
        # 比較期間で1日に1回だけ出た語が今週出ないのは「出てこなかった」にしない
        th = [_thread(MON - datetime.timedelta(days=7 * w + i), "散歩した。カメラ" if (w, i) == (1, 0) else "散歩した",
                      tid=f"{w}{i}") for w in range(1, 5) for i in range(4)]
        th += [_thread(MON + datetime.timedelta(days=i), "散歩した", tid=f"c{i}") for i in range(4)]
        obs = jo.observe(jo.build_days(th), MON)
        self.assertEqual(obs["terms"]["gone"], [])
        self.assertIn("散歩", [x["term"] for x in obs["terms"]["steady"]])

    def test_monthly_unit(self):
        obs = jo.observe(self.days, MON + datetime.timedelta(days=6) - datetime.timedelta(days=27),
                         unit_days=28, n_base=1, n_hist=1)
        self.assertEqual(obs["unit_days"], 28)
        self.assertEqual(obs["writing"]["daily"], [])
        self.assertEqual(obs["writing"]["written"], 17)   # 直近28日 = 過去3週×4日 + 今週5日


class InterpretationTest(unittest.TestCase):
    def setUp(self):
        self.obs = jo.observe(jo.build_days(jo.mock_threads(MON, 8)), MON)

    def test_normalize_drops_unreferenced_and_directive(self):
        raw = {"hypotheses": [
            {"refs": ["O2"], "text": "部屋のことが気にかかっている時期かもしれない", "alternatives": ["単に引っ越しの話題かも"]},
            {"refs": ["O99"], "text": "根拠のない仮説"},
            {"refs": ["O3"], "text": "片付けをすべきです"},
            {"refs": [], "text": "参照なし"},
        ], "continuity": "新しい変化に見える", "questions": ["部屋の話が増えたのはいつからだろう", "毎朝片付けしましょう"]}
        got = jo.normalize_interpretation(raw, self.obs)
        self.assertEqual(len(got["hypotheses"]), 1)
        self.assertEqual(got["hypotheses"][0]["refs"], ["O2"])
        self.assertEqual(got["questions"], ["部屋の話が増えたのはいつからだろう"])

    def test_interpret_statuses(self):
        self.assertEqual(jo.interpret(self.obs, "", "m")[1], "no_key")
        with mock.patch("google.genai.Client", side_effect=RuntimeError("402 credits depleted")):
            self.assertEqual(jo.interpret(self.obs, "k", "m"), (None, "failed"))
        fake = mock.Mock()
        fake.models.generate_content.return_value.text = (
            '```json\n{"hypotheses":[{"refs":["O4"],"text":"面接が近い週なのかもしれない","alternatives":["別の用事"]}],'
            '"continuity":"","questions":[]}\n```')
        with mock.patch("google.genai.Client", return_value=fake):
            interp, st = jo.interpret(self.obs, "k", "m")
        self.assertEqual(st, "gemini")
        self.assertEqual(interp["hypotheses"][0]["refs"], ["O4"])

    def test_prompt_contains_observations_not_whole_journal(self):
        p = jo.build_prompt(self.obs)
        self.assertIn("[O2]", p)
        self.assertIn("指示・目標・ToDo", p)
        self.assertLess(len(p), 8000)


class OutputTest(unittest.TestCase):
    def setUp(self):
        self.obs = jo.observe(jo.build_days(jo.mock_threads(MON, 8)), MON)

    def test_messages_fit_discord_and_label_hypotheses(self):
        interp = {"hypotheses": [{"refs": ["O2"], "text": "x" * 150, "alternatives": ["y"]}] * 4,
                  "continuity": "", "questions": ["問い"]}
        msgs = jo.compose_messages(self.obs, interp, "gemini", head=jo.HEAD_MARK)
        self.assertGreaterEqual(len(msgs), 2)
        self.assertTrue(all(len(m) <= 1900 for m in msgs))
        self.assertTrue(msgs[0].startswith(jo.HEAD_MARK))
        self.assertIn("仮説。事実ではありません", "\n".join(msgs))
        self.assertNotIn("3大アクション", "\n".join(msgs))

    def test_sheet_row_matches_header(self):
        row = jo.sheet_row("2026-W39", self.obs, None, "no_key")
        self.assertEqual(len(row), len(jo.SHEET_HEADER))
        self.assertIsInstance(row[1], int)

    def test_redacted_summary_has_no_words(self):
        s = jo.redacted_summary(self.obs)
        for w in ("面接", "部屋", "飲み会", "写真"):
            self.assertNotIn(w, s)

    def test_render_png(self):
        import tempfile
        import warnings
        with tempfile.TemporaryDirectory() as d, warnings.catch_warnings():
            warnings.simplefilter("ignore")  # CI外で日本語フォント未指定時のグリフ警告を抑止
            path = jo.render_png(self.obs, os.path.join(d, "x.png"), title="t")
            self.assertGreater(os.path.getsize(path), 10_000)


class WeeklyPipelineTest(unittest.TestCase):
    def test_default_anchor_targets_finished_week(self):
        import weekly_mindmap as wm
        sun = datetime.date(2026, 9, 27)
        self.assertEqual(wm.default_anchor(sun), sun)
        self.assertEqual(wm.default_anchor(datetime.date(2026, 9, 28)), sun)   # 月曜に遅延起動
        self.assertEqual(wm.default_anchor(datetime.date(2026, 10, 1)), sun)
        ctx = wm.week_context(datetime.date(2026, 9, 28))
        self.assertEqual(ctx["monday"], datetime.date(2026, 9, 28))

    def test_month_closing(self):
        import weekly_mindmap as wm
        self.assertTrue(wm.is_month_closing_week(wm.week_context(datetime.date(2026, 9, 27))))
        self.assertFalse(wm.is_month_closing_week(wm.week_context(datetime.date(2026, 9, 20))))


if __name__ == "__main__":
    unittest.main()
