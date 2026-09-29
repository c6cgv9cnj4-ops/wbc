# -*- coding: utf-8 -*-
"""毎朝の #モーニングジャーナル 投稿作成と、Bot 投稿の取得除外の検証(2026-09-27)。
通信はすべて差し替え、本文は架空の文章のみ。実行: .venv/bin/python -m unittest discover -s tests -v"""
import contextlib
import datetime
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import create_daily_journal_post as cp  # noqa: E402
import export_discord_logs as ex  # noqa: E402
import journal_observe as jo  # noqa: E402
import journal_review as jr  # noqa: E402
import set_journal_forum_guidelines as g  # noqa: E402

DAY = datetime.date(2026, 9, 27)
BOT = {"id": "900", "username": "StockBot", "bot": True}
ME = {"id": "100", "username": "ricky8999r"}


def _m(mid, author, content, hh="07:00"):
    return {"id": str(mid), "author": author, "content": content,
            "timestamp": f"2026-09-27T{hh}:00+09:00"}


def _run(argv, **patches):
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", ["x"] + argv), contextlib.redirect_stdout(buf), \
            contextlib.ExitStack() as st:
        for name, val in patches.items():
            st.enter_context(mock.patch.object(cp, name, val))
        rc = cp.main()
    return rc, buf.getvalue()


class PostContentTest(unittest.TestCase):
    def test_title_and_template(self):
        self.assertEqual(cp.post_title(DAY), "2026/09/27｜モーニングジャーナル")
        self.assertTrue(cp.JOURNAL_POST_TEMPLATE.startswith("# 今日のモーニングジャーナル\n\n思いつくまま自由に書いてください。"))
        for h in ("### 昨日・今日の出来事", "### やったこと・やること", "### 気づき・発見", "### 今の気分・感覚",
                  "### 最近気になっていること", "### 未解決・気になっていること", "### 今日につなげたいこと",
                  "---\n\n※全部埋めなくてOK", "※思いついたことをそのまま書いてください"):
            self.assertIn(h, cp.JOURNAL_POST_TEMPLATE)
        self.assertLessEqual(len(cp.JOURNAL_POST_TEMPLATE), 2000)
        self.assertEqual(jr.parse_thread_date(cp.post_title(DAY)), DAY)   # 既存の取得処理が日付を読める

    def test_dry_run_without_credentials_sends_nothing(self):
        with mock.patch.dict(os.environ, {"DISCORD_BOT_TOKEN": ""}):
            rc, out = _run(["--date", "2026-09-27"], create_post=mock.Mock(side_effect=AssertionError("post")),
                           _discord_get=mock.Mock(side_effect=AssertionError("get")))
        self.assertEqual(rc, 0)
        self.assertIn("2026/09/27｜モーニングジャーナル", out)
        self.assertIn("### 今日につなげたいこと", out)


class DuplicateTest(unittest.TestCase):
    def _env(self):
        return mock.patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "tok", "DISCORD_CHANNEL_ID_MORNING_JOURNAL": "555"})

    def _patches(self, names, create, empty=False, recover=None):
        # 既存スレッドは message_count=0(フォーラムでは最初の投稿を数えないので通常の状態)
        return dict(_discord_get=mock.Mock(return_value={"type": 15, "name": "モーニングジャーナル"}),
                    _list_forum_threads=mock.Mock(return_value=("g", [
                        {"id": str(i), "name": n, "message_count": 0, "total_message_sent": 0}
                        for i, n in enumerate(names)])),
                    create_post=create,
                    thread_is_empty=mock.Mock(return_value=empty),
                    post_template_message=recover or mock.Mock(side_effect=AssertionError("recover")))

    def test_skips_when_same_date_exists(self):
        for existing in (["2026/09/27｜モーニングジャーナル"], ["2026/09/27"], ["2026/09/26", "2026/09/27 追記"]):
            create = mock.Mock()
            with self._env():
                rc, out = _run(["--apply", "--date", "2026-09-27"], **self._patches(existing, create))
            self.assertEqual(rc, 0)
            create.assert_not_called()
            self.assertIn("[SKIP]", out)

    def test_normal_or_manual_post_is_never_refilled(self):
        # 2026-09-29 に実際にあった形: Bot の投稿と本人の手動投稿が同じ日にある → 何も投稿しない
        create = mock.Mock(side_effect=AssertionError("post"))
        with self._env():
            rc, out = _run(["--apply", "--date", "2026-09-29"],
                           **self._patches(["2026/09/29", "2026/09/29｜モーニングジャーナル"], create))
        self.assertEqual(rc, 0)
        self.assertIn("[SKIP]", out)

    def test_recovers_only_when_starter_is_missing(self):
        recover = mock.Mock(return_value={})
        create = mock.Mock(side_effect=AssertionError("post"))
        with self._env():
            rc, out = _run(["--apply", "--date", "2026-09-27"],
                           **self._patches(["2026/09/27｜モーニングジャーナル"], create, empty=True, recover=recover))
        self.assertEqual(rc, 0)
        recover.assert_called_once()

    def test_thread_is_empty_checks_starter_message(self):
        for code, expected in ((200, False), (404, True)):
            with mock.patch.object(cp.requests, "get", return_value=mock.Mock(status_code=code)) as get:
                self.assertEqual(cp.thread_is_empty({"id": "123", "message_count": 0}, "tok"), expected)
            self.assertTrue(get.call_args.args[0].endswith("/channels/123/messages/123"))

    def test_creates_once_when_missing(self):
        create = mock.Mock(return_value={"name": "2026/09/27｜モーニングジャーナル"})
        with self._env():
            rc, out = _run(["--apply", "--date", "2026-09-27"], **self._patches(["2026/09/25", "2026/09/24"], create))
        self.assertEqual(rc, 0)
        create.assert_called_once()
        self.assertIn("[OK] 作成しました", out)

    def test_dry_run_with_credentials_checks_but_does_not_post(self):
        create = mock.Mock(side_effect=AssertionError("post"))
        with self._env():
            rc, out = _run(["--date", "2026-09-27"], **self._patches(["2026/09/25"], create))
        self.assertEqual(rc, 0)
        self.assertIn("--apply で作成されます", out)

    def test_refuses_non_forum(self):
        create = mock.Mock(side_effect=AssertionError("post"))
        p = self._patches([], create)
        p["_discord_get"] = mock.Mock(return_value={"type": 0, "name": "general"})
        with self._env():
            rc, _ = _run(["--apply", "--date", "2026-09-27"], **p)
        self.assertEqual(rc, 1)

    def test_create_payload(self):
        with mock.patch.object(cp.requests, "post") as post:
            post.return_value.status_code, post.return_value.json.return_value = 201, {"name": "x"}
            cp.create_post("tok", "555", DAY)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["name"], "2026/09/27｜モーニングジャーナル")
        self.assertEqual(body["message"]["content"], cp.JOURNAL_POST_TEMPLATE)
        self.assertEqual(body["message"]["allowed_mentions"], {"parse": []})
        self.assertTrue(post.call_args.args[0].endswith("/channels/555/threads"))

    def test_env_file_does_not_override_or_print(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as fh:
            fh.write("# c\nDISCORD_BOT_TOKEN='from-file-secret'\nOTHER=1\n")
        try:
            with mock.patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "already"}, clear=False):
                cp.load_env_file(fh.name)
                self.assertEqual(os.environ["DISCORD_BOT_TOKEN"], "already")
            with mock.patch.dict(os.environ, {"DISCORD_BOT_TOKEN": ""}, clear=False):
                os.environ.pop("DISCORD_BOT_TOKEN")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    cp.load_env_file(fh.name)
                self.assertEqual(os.environ["DISCORD_BOT_TOKEN"], "from-file-secret")
                self.assertNotIn("from-file-secret", buf.getvalue())
        finally:
            os.unlink(fh.name)


class BotExclusionTest(unittest.TestCase):
    """Bot のテンプレート投稿を除外し、本人の返信だけを1日分として扱う。"""

    def _threads(self, msgs_by_tid, names):
        with mock.patch.object(jr, "_list_forum_threads",
                               return_value=("g", [{"id": t, "name": n} for t, n in names.items()])), \
                mock.patch.object(jr, "_discord_get",
                                  side_effect=lambda path, tok, params=None: list(reversed(msgs_by_tid[path.split("/")[2]]))):
            return jr.collect_threads("tok", "forum", DAY - datetime.timedelta(days=3), DAY)["threads"]

    def test_bot_template_excluded_and_replies_joined(self):
        msgs = {"1": [_m(1, BOT, cp.JOURNAL_POST_TEMPLATE, "05:00"),
                      _m(2, ME, "架空の返信その一。散歩に行きたい。", "07:10"),
                      _m(3, ME, "架空の返信その二。写真を撮った。", "21:30")]}
        th = self._threads(msgs, {"1": "2026/09/27｜モーニングジャーナル"})
        self.assertEqual(len(th), 1)
        self.assertNotIn("今日のモーニングジャーナル", th[0]["text"])
        self.assertIn("架空の返信その一", th[0]["text"])
        self.assertIn("架空の返信その二", th[0]["text"])
        days = jo.build_days(th)
        self.assertEqual(list(days), [DAY])                         # 複数返信が1日分
        rec = days[DAY]
        self.assertEqual(rec["markers"]["positive"], 0)            # テンプレの「嬉しい、楽しい」は数えない
        self.assertEqual(rec["markers"]["body"], 0)                # 「疲れた」も数えない
        self.assertEqual(rec["markers"]["want"], 1)                # 本人の「行きたい」だけ
        self.assertIn("散歩", rec["terms"])

    def test_template_only_day_is_not_written(self):
        th = self._threads({"1": [_m(1, BOT, cp.JOURNAL_POST_TEMPLATE)]}, {"1": "2026/09/27｜モーニングジャーナル"})
        self.assertEqual(th[0]["text"], "")
        self.assertEqual(jo.build_days(th), {})

    def test_past_manual_posts_unchanged(self):
        msgs = {"7": [_m(7, ME, "架空の手動投稿の本文。"), _m(8, ME, "架空の追記。")]}
        th = self._threads(msgs, {"7": "2026/09/25"})
        self.assertEqual(th[0]["text"], "架空の手動投稿の本文。\n架空の追記。")
        self.assertEqual(th[0]["date"], datetime.date(2026, 9, 25))

    def test_sheet_export_also_excludes_bot(self):
        msgs = {"1": [_m(1, BOT, cp.JOURNAL_POST_TEMPLATE), _m(2, ME, "架空の返信。")]}
        with mock.patch.object(jr, "_list_forum_threads",
                               return_value=("g", [{"id": "1", "name": "2026/09/27｜モーニングジャーナル"}])), \
                mock.patch.object(jr, "_discord_get",
                                  side_effect=lambda path, tok, params=None: list(reversed(msgs["1"]))):
            by_date = ex.fetch_forum_threads_by_date("forum", "tok", DAY, DAY)
        md = ex.build_forum_markdown(by_date[DAY], "2026-09-27", "モーニングジャーナル")
        self.assertIn("架空の返信。", md)
        self.assertNotIn("今日のモーニングジャーナル", md)
        self.assertNotIn("StockBot", md)


class GuidelinesClearTest(unittest.TestCase):
    def test_clear_sends_empty_topic(self):
        get = mock.Mock(return_value=mock.Mock(status_code=200, json=mock.Mock(
            return_value={"type": 15, "name": "モーニングジャーナル", "topic": g.JOURNAL_TEMPLATE})))
        patch = mock.Mock(return_value=mock.Mock(status_code=200, json=mock.Mock(return_value={"topic": None})))
        with mock.patch.object(g.requests, "get", get), mock.patch.object(g.requests, "patch", patch), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(g.apply("tok", "1", ""), 0)
        self.assertEqual(patch.call_args.kwargs["json"], {"topic": ""})


if __name__ == "__main__":
    unittest.main()
