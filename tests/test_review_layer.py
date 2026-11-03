# -*- coding: utf-8 -*-
"""レビュー表示・集約層(2026-09-30 再設計)と、Gemini への送信境界の検証。
すべて架空の文章。通信はすべて差し替える。実行: .venv/bin/python -m unittest discover -s tests -v"""
import contextlib
import datetime
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import journal_observe as jo  # noqa: E402

MON = datetime.date(2026, 9, 21)


def synthetic_threads():
    """比較期間: 求人サイトを見る記録 / 今期: 応募・面接の記録。文には架空の固有の言い回しを入れる。"""
    th, tid = [], 5000
    for w in range(1, 5):
        for i in range(4):
            d = MON - datetime.timedelta(days=7 * w) + datetime.timedelta(days=i)
            tid += 1
            th.append({"tid": str(tid), "name": d.strftime("%Y/%m/%d"), "date": d,
                       "url": f"https://discord.com/channels/1/{tid}",
                       "text": f"インディードで求人を眺めていた。架空の前期の独り言その{w}{i}だった。散歩をした。"})
    for i in range(5):
        d = MON + datetime.timedelta(days=i)
        tid += 1
        th.append({"tid": str(tid), "name": d.strftime("%Y/%m/%d"), "date": d,
                   "url": f"https://discord.com/channels/1/{tid}",
                   "text": f"トーハンに応募して面接の日程が決まった。架空の今期の独り言その{i}だった。散歩をした。"})
    return th


def sentence_pieces(threads, min_len=8):
    """日記の「文」の断片(語より長いもの)。これが Gemini への送信内容に出てはいけない。"""
    out = set()
    for t in threads:
        for piece in re.split(r"[。\n]", t["text"]):
            piece = piece.strip()
            if len(piece) >= min_len:
                out.add(piece)
    return out


def fake_genai(capture: list, reply: str):
    class Client:
        def __init__(self, api_key):
            self.models = self

        def generate_content(self, model, contents, config=None):
            capture.append(contents)
            r = mock.Mock()
            r.text = reply
            return r
    return Client


GOOD_REPLY = json.dumps({"hypotheses": [
    {"text": "求人サイトを見る段階から、応募・面接の段階へ記録の比重が移った可能性があります", "words": ["インディード", "応募", "面接"]},
    {"text": "特定の企業への応募が具体化したように見えます", "words": ["トーハン", "応募"]},
]}, ensure_ascii=False)


class Base(unittest.TestCase):
    def setUp(self):
        self.threads = synthetic_threads()
        self.days = jo.build_days(self.threads)
        self.obs = jo.observe(self.days, MON)
        self.diff = jo.diff_for_ai(self.obs)


class SendBoundaryTest(Base):
    def test_diff_contains_only_words_and_counts(self):
        blob = json.dumps(self.diff, ensure_ascii=False)
        for piece in sentence_pieces(self.threads):
            self.assertNotIn(piece, blob)
        self.assertNotIn("http", blob)
        dec = [x["語"] for x in self.diff["減少・消失した語"]]
        inc = [x["語"] for x in self.diff["新規・再登場・増加した語"]]
        self.assertIn("インディード", dec)
        self.assertTrue({"応募", "面接", "トーハン"} & set(inc))
        for x in self.diff["減少・消失した語"] + self.diff["新規・再登場・増加した語"]:
            self.assertEqual(set(x), {"語", "区分", "比較期間の出現日数", "今期の出現日数"})

    def test_prompt_sent_to_gemini_has_no_journal_text(self):
        sent = []
        with mock.patch("google.genai.Client", fake_genai(sent, GOOD_REPLY)):
            hyps, status, prompt = jo.interpret_diff(self.diff, "key", "m")
        self.assertEqual(status, "gemini")
        self.assertEqual(len(sent), 1)
        for piece in sentence_pieces(self.threads):
            self.assertNotIn(piece, sent[0])
        self.assertNotIn("http", sent[0])
        self.assertNotIn("架空の", sent[0])   # 文にだけ含まれる言い回し
        self.assertEqual(sent[0], prompt)

    def test_transient_stable_markers_cooccur_are_not_sent(self):
        blob = json.dumps(self.diff, ensure_ascii=False)
        for word in ("散歩",):                      # 毎日出る(安定)語は渡さない
            self.assertNotIn(word, blob)
        for key in ("千字", "共起", "表現"):
            self.assertNotIn(key, blob)


class FallbackTest(Base):
    def _compose(self, hyps, status):
        return "\n".join(jo.compose_review(self.obs, self.diff, hyps, status, [], head=jo.HEAD_MARK))

    def assert_no_raw_metrics(self, text):
        self.assertIsNone(re.search(r"\bO\d+\b", text))
        for bad in ("千字", "共起", "同じ日に", "/千字", "出た日の割合", "表現の出現率"):
            self.assertNotIn(bad, text)
        self.assertIsNone(re.search(r"\d+/\d+日", text))

    def test_no_key_failed_invalid_all_fall_back_to_static_contrast(self):
        cases = [("", None, None), ("key", RuntimeError("429 RESOURCE_EXHAUSTED"), None),
                 ("key", None, "これはJSONではない"), ("key", None, json.dumps({"hypotheses": [
                     {"text": "仕事のストレスが高まった", "words": ["面接"]},          # 断定
                     {"text": "新しい関心かもしれない", "words": ["一覧に無い語"]}]}, ensure_ascii=False))]
        expected = ["no_key", "failed", "failed", "invalid"]
        for (key, exc, reply), exp in zip(cases, expected):
            sent = []
            client = fake_genai(sent, reply or "")
            if exc:
                client = mock.Mock(side_effect=exc)
            with mock.patch("google.genai.Client", client):
                hyps, status, _ = jo.interpret_diff(self.diff, key, "m")
            self.assertIsNone(hyps)
            self.assertEqual(status, exp)
            text = self._compose(hyps, status)
            self.assertIn("【前期からの主な変化（機械集計）】", text)
            self.assertIn("減少・消失：", text)
            self.assertIn("新規・増加：", text)
            self.assertIn("インディード", text)
            self.assertIn("AIによる仮説生成は利用できませんでした", text)
            self.assert_no_raw_metrics(text)

    def test_no_change_is_normal(self):
        empty = {**self.diff, "減少・消失した語": [], "新規・再登場・増加した語": []}
        hyps, status, prompt = jo.interpret_diff(empty, "key", "m")
        self.assertEqual((hyps, status, prompt), (None, "few", ""))
        text = "\n".join(jo.compose_review(self.obs, empty, None, "few", [], head=jo.HEAD_MARK))
        self.assertIn("変化候補なし", text)
        self.assert_no_raw_metrics(text)


class MainReviewTest(Base):
    def test_threads_with_python_excerpts_and_confirm_marks(self):
        with mock.patch("google.genai.Client", fake_genai([], GOOD_REPLY)):
            hyps, status, _ = jo.interpret_diff(self.diff, "key", "m")
        ex = [jo.excerpts_for(h["words"], self.days, self.obs, MON, 4) for h in hyps]
        text = "\n".join(jo.compose_review(self.obs, self.diff, hyps, status, ex, head=jo.HEAD_MARK))
        self.assertIn("【変化の筋 1】", text)
        self.assertIn("【変化の筋 2】", text)
        self.assertNotIn("【変化の筋 3】", text)
        self.assertIn("以前：", text)
        self.assertIn("今期：", text)
        self.assertIn("`1 ○` / `1 △` / `1 ×`", text)
        FallbackTest.assert_no_raw_metrics(self, text)
        # 抜粋は元の記録の本文から取っている(Gemini が作った文ではない)
        before, now = ex[0]
        self.assertIn("インディード", before)
        self.assertTrue(any(before.split(" ", 1)[1].strip("…") in t["text"] for t in self.threads))
        self.assertIn("応募", now)
        self.assertTrue(any(now.split(" ", 1)[1].strip("…") in t["text"] for t in self.threads))

    def test_before_is_no_record_when_only_new_words(self):
        before, now = jo.excerpts_for(["トーハン"], self.days, self.obs, MON, 4)
        self.assertEqual(before, jo.NO_RECORD)
        self.assertIn("トーハン", now)

    def test_at_most_three_threads(self):
        words = [x["語"] for x in self.diff["新規・再登場・増加した語"]] or ["応募"]
        raw = {"hypotheses": [{"text": f"変化その{i}かもしれない", "words": words[:1]} for i in range(6)]}
        self.assertEqual(len(jo.normalize_diff_hypotheses(raw, self.diff)), 3)

    def test_quote_like_or_unknown_words_rejected(self):
        raw = {"hypotheses": [
            {"text": "「本人は求人サイトを毎朝ながめていたらしい」という記録から、そうかもしれない", "words": ["応募"]},
            {"text": "新しい関心かもしれない", "words": ["存在しない語"]}]}
        self.assertEqual(jo.normalize_diff_hypotheses(raw, self.diff), [])

    def test_detail_rows_keep_all_raw_metrics(self):
        rows = jo.detail_rows("prod-x", "2026-W39", "週次", self.obs, self.diff, None, "no_key", [], "t")
        sections = {r[3] for r in rows}
        self.assertTrue({"観測項目", "表現率", "語の区分", "AIに渡した機械データ", "AIの状態"} <= sections)
        self.assertEqual(len(rows[0]), len(jo.DETAIL_HEADER))
        self.assertEqual(sum(1 for r in rows if r[3] == "観測項目"),
                         sum(max(1, len(it["evidence"])) for it in self.obs["items"]))


class PipelineTest(unittest.TestCase):
    """weekly_mindmap.main() を通しで動かし、Gemini への送信内容と Discord 本文を検査する。"""

    def _run(self, env, argv, gemini_reply=GOOD_REPLY):
        import weekly_mindmap as wm
        threads = synthetic_threads()
        sent, posts = [], []
        base_env = {"GEMINI_API_KEY": "key", "DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID_MORNING_JOURNAL": "c",
                    "GITHUB_ACTIONS": "", "JOURNAL_RUN_MODE": "", "WEEKLY_SPREADSHEET_ID": "", "DRY_RUN": ""}
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {**base_env, **env}), \
                mock.patch.object(sys, "argv", ["weekly_mindmap.py"] + argv), \
                mock.patch("journal_review.collect_threads", return_value={"threads": threads}), \
                mock.patch.object(wm, "REPORT_WEEKLY_DIR", d), \
                mock.patch.object(wm, "build_google_credentials", return_value=None), \
                mock.patch.object(wm, "build_service_account_credentials", return_value=None), \
                mock.patch.object(wm, "post_messages", side_effect=lambda m, img, username: posts.append((m, img))), \
                mock.patch.object(wm.jo, "render_png", return_value=None), \
                mock.patch("google.genai.Client", fake_genai(sent, gemini_reply)), \
                contextlib.redirect_stdout(buf):
            rc = wm.main()
        return rc, threads, sent, posts, buf.getvalue()

    def test_month_closing_week_sends_no_journal_text_and_no_png(self):
        rc, threads, sent, posts, _ = self._run({}, ["--week", "2026-09-27"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(sent), 2)                       # 週次と月次で1回ずつ
        for prompt in sent:
            for piece in sentence_pieces(threads):
                self.assertNotIn(piece, prompt)
            self.assertNotIn("http", prompt)
        self.assertEqual(len(posts), 2)                      # 週次・月次の投稿
        for msgs, img in posts:
            self.assertIsNone(img)                           # PNG は添付しない
            text = "\n".join(msgs)
            self.assertIsNone(re.search(r"\bO\d+\b", text))
            self.assertNotIn("千字", text)

    def test_gemini_down_still_no_raw_metrics(self):
        rc, _, sent, posts, _ = self._run({"GEMINI_API_KEY": ""}, ["--week", "2026-09-27"])
        self.assertEqual(sent, [])
        text = "\n".join("\n".join(m) for m, _ in posts)
        self.assertIn("【前期からの主な変化（機械集計）】", text)
        self.assertIsNone(re.search(r"\bO\d+\b", text))

    def test_acceptance_mode_never_touches_production_tabs(self):
        import weekly_mindmap as wm
        written, details, confirms = [], [], []
        with mock.patch.object(wm, "write_observe_row", side_effect=lambda c, ctx, row, sheet_name: written.append(sheet_name)), \
                mock.patch.object(wm, "write_detail_rows", side_effect=lambda c, name, rows, replace: details.append((name, replace))), \
                mock.patch.object(wm, "sync_confirmations",
                                  side_effect=lambda c, ctx, rows, g, sheet, read_replies: confirms.append((sheet, read_replies))), \
                mock.patch.object(wm, "verify_google_credentials", return_value=""):
            creds = object()
            with mock.patch.object(wm, "build_service_account_credentials", return_value=creds):
                rc, _, _, posts, _ = self._run_with_creds({"JOURNAL_RUN_MODE": "acceptance"}, creds)
        self.assertEqual(rc, 0)
        self.assertEqual(written, ["AT_週次観測"])
        self.assertEqual(details, [("AT_観測詳細", False)])
        self.assertEqual(confirms, [("AT_仮説確認", False)])
        self.assertTrue(all(m[0].startswith("【Acceptance Test】") for m, _ in posts))

    def _run_with_creds(self, env, creds):
        import weekly_mindmap as wm
        threads = synthetic_threads()
        sent, posts = [], []
        base_env = {"GEMINI_API_KEY": "key", "DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID_MORNING_JOURNAL": "c",
                    "GITHUB_ACTIONS": "", "WEEKLY_SPREADSHEET_ID": "sheet", "DRY_RUN": ""}
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {**base_env, **env}), \
                mock.patch.object(sys, "argv", ["weekly_mindmap.py", "--week", "2026-09-27"]), \
                mock.patch("journal_review.collect_threads", return_value={"threads": threads}), \
                mock.patch.object(wm, "REPORT_WEEKLY_DIR", d), \
                mock.patch.object(wm, "build_google_credentials", return_value=None), \
                mock.patch.object(wm, "post_messages", side_effect=lambda m, img, username: posts.append((m, img))), \
                mock.patch.object(wm, "pin_latest_and_unpin_old", side_effect=AssertionError("pin")), \
                mock.patch.object(wm.jo, "render_png", return_value=None), \
                mock.patch("google.genai.Client", fake_genai(sent, GOOD_REPLY)), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = wm.main()
        return rc, threads, sent, posts, ""


class OtherPathsTest(unittest.TestCase):
    """週次観測以外で日記を Gemini に送りうる経路が、Gemini を呼ばないこと。"""

    def test_journal_review_analyze_never_calls_gemini(self):
        import journal_review as jr
        threads = [{"tid": "1", "name": "2026/09/21", "date": MON, "url": "u", "text": "架空の本文。散歩した。"}]
        with mock.patch("google.genai.Client", side_effect=AssertionError("gemini called")), \
                contextlib.redirect_stdout(io.StringIO()):
            review, source = jr.analyze(threads, "期間", "key", "m")
        self.assertEqual(source, "fallback")

    def test_monthly_mindmap_never_calls_gemini(self):
        import generate_monthly_mindmap as gm
        items = [{"date": "2026-09-21", "type": "health", "text": "架空の日記本文"}]
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "key", "GITHUB_EVENT_NAME": ""}), \
                        mock.patch.object(gm, "fetch_month_items", return_value=items), \
                        mock.patch.object(gm, "call_gemini", side_effect=AssertionError("gemini called")), \
                        mock.patch("google.genai.Client", side_effect=AssertionError("gemini called")), \
                        contextlib.redirect_stdout(io.StringIO()):
                    gm.main()
                out = open(os.listdir(os.path.join(d, "reports", "monthly"))[0] and
                           os.path.join(d, "reports", "monthly", os.listdir(os.path.join(d, "reports", "monthly"))[0]),
                           encoding="utf-8").read()
            finally:
                os.chdir(cwd)
        self.assertNotIn("架空の日記本文", out)

    def test_daily_report_stops_before_reading_logs(self):
        import generate_daily_report as gd
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "key", "DISCORD_WEBHOOK_DAILY": "https://x"}), \
                mock.patch.object(gd, "read_log", side_effect=AssertionError("read")), \
                mock.patch.object(gd, "call_gemini", side_effect=AssertionError("gemini called")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(gd.main())


if __name__ == "__main__":
    unittest.main()
