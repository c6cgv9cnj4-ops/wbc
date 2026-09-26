# -*- coding: utf-8 -*-
"""日記・個人メモを公開リポジトリ / 公開ログ / Issue に出さないことの検証(2026-09-26)。
実データは使わない。Discord / GAS / GitHub API はすべて差し替える。
実行: .venv/bin/python -m unittest discover -s tests -v"""
import contextlib
import datetime
import glob
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import requests

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import check_no_personal_data as guard  # noqa: E402
import export_discord_logs as ex  # noqa: E402
import generate_monthly_mindmap as gm  # noqa: E402
import sync_weekly_sheet as sw  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))
SECRET_BODY = "ひみつの日記ほんぶん"      # 合成の本文。ログやIssueに出てはいけない
SECRET_MEMO = "こじんてきなメモ"
GAS_SECRET = "gas-shared-secret-xyz"


def _msg(mid, content, when):
    return {"id": str(mid), "content": content, "timestamp": when.isoformat(),
            "author": {"username": "me", "global_name": "me"}}


class ExportAndSyncPipelineTest(unittest.TestCase):
    """discord_logs.yml と同じ順(export → sync)で実行し、出力を検査する。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        now = datetime.datetime.now(JST)
        self.today = now.date()
        forum = {self.today: [("2026/09/26", [_msg(1, "TODO " + SECRET_BODY, now)], "https://discord.com/x/1")]}
        self.texts = [_msg(2, SECRET_MEMO, now)]
        self.patches = [
            mock.patch.object(ex, "fetch_forum_threads_by_date", return_value=forum),
            mock.patch.object(ex, "fetch_messages_since", return_value=self.texts),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def _run_export(self, env):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(buf):
            ex.main()
        return buf.getvalue()

    def test_forum_diary_never_becomes_issue_and_body_not_logged(self):
        created = []
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID_HEALTH": "h", "DISCORD_CHANNEL_ID_INPUT": "",
               "GITHUB_TOKEN": "g", "GITHUB_REPOSITORY": "o/r", "LOG_LOOKBACK_DAYS": ""}
        with mock.patch.object(ex, "create_github_issue", side_effect=lambda *a: created.append(a) or {"number": 1}), \
                mock.patch.object(ex, "find_existing_issue_by_message_id", return_value=None):
            log = self._run_export(env)
        self.assertEqual(created, [])                 # 「TODO」で始まる日記でも Issue にしない
        self.assertNotIn(SECRET_BODY, log)
        self.assertTrue(glob.glob("logs/health/*.md"))  # 作業ファイルはランナー内に作られる

    def test_memo_todo_buy_never_becomes_issue(self):
        # #インプットの「TODO」「BUY」で始まる架空メモ(大文字小文字も)から Issue を作らない
        for content in ("TODO " + SECRET_MEMO, "BUY " + SECRET_MEMO, "todo " + SECRET_MEMO, "Buy " + SECRET_MEMO):
            self.texts[0]["content"] = content
            env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID_HEALTH": "", "DISCORD_CHANNEL_ID_INPUT": "i",
                   "GITHUB_TOKEN": "g", "GITHUB_REPOSITORY": "o/r", "LOG_LOOKBACK_DAYS": ""}
            with mock.patch.object(ex.requests, "post", side_effect=AssertionError("Issue API called")) as post, \
                    mock.patch.object(ex, "create_github_issue", side_effect=AssertionError("issue")), \
                    mock.patch.object(ex, "find_existing_issue_by_message_id", side_effect=AssertionError("search")):
                log = self._run_export(env)
            post.assert_not_called()
            self.assertNotIn(SECRET_MEMO, log)
            self.assertNotIn("Issue", log)

    def test_export_runs_without_github_token(self):
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID_HEALTH": "h", "DISCORD_CHANNEL_ID_INPUT": "i",
               "GITHUB_TOKEN": "", "GITHUB_REPOSITORY": "", "LOG_LOOKBACK_DAYS": ""}
        self.assertIn("完了", self._run_export(env))

    def test_workflow_has_no_issue_permission(self):
        wf = open(os.path.join(ROOT, ".github", "workflows", "discord_logs.yml"), encoding="utf-8").read()
        self.assertNotIn("issues: write", wf)
        self.assertNotIn("GITHUB_TOKEN", wf)

    def test_backfill_mode_needs_no_github_token(self):
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID_HEALTH": "h", "DISCORD_CHANNEL_ID_INPUT": "i",
               "GITHUB_TOKEN": "", "GITHUB_REPOSITORY": "", "LOG_LOOKBACK_DAYS": "7"}
        log = self._run_export(env)   # weekly-sheet-sync.yml / daily_summary.yml の使い方
        self.assertIn("バックフィル", log)
        self.assertEqual(len(glob.glob("logs/health/*.md")), 7)

    def test_sync_sends_runner_files_to_gas_without_logging_secret_or_body(self):
        self._run_export({"DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID_HEALTH": "h",
                          "DISCORD_CHANNEL_ID_INPUT": "i", "LOG_LOOKBACK_DAYS": "7"})
        sent = []

        def ok_get(url, timeout):
            sent.append(url)
            r = mock.Mock()
            r.raise_for_status.return_value = None
            r.json.return_value = {"ok": True, "written": 1}
            return r

        buf = io.StringIO()
        env = {"JOURNAL_GAS_WEB_APP_URL": "https://script.google.test/exec", "JOURNAL_GAS_SHARED_SECRET": GAS_SECRET}
        with mock.patch.dict(os.environ, env), mock.patch.object(sw.requests, "get", side_effect=ok_get), \
                contextlib.redirect_stdout(buf):
            sw.main()
        self.assertTrue(any("health" in u for u in sent))          # 日記はシートへ届く
        self.assertTrue(any(requests.utils.quote(SECRET_BODY) in u or SECRET_BODY in requests.utils.unquote(u)
                            for u in sent))
        log = buf.getvalue()
        self.assertNotIn(SECRET_BODY, log)
        self.assertNotIn(GAS_SECRET, log)

    def test_sync_errors_are_redacted(self):
        self._run_export({"DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID_HEALTH": "h",
                          "DISCORD_CHANNEL_ID_INPUT": "", "LOG_LOOKBACK_DAYS": "1"})

        def bad_get(url, timeout):
            resp = requests.Response()
            resp.status_code, resp.url = 404, url
            raise requests.HTTPError(f"404 Client Error: Not Found for url: {url}", response=resp)

        buf = io.StringIO()
        env = {"JOURNAL_GAS_WEB_APP_URL": "https://script.google.test/exec", "JOURNAL_GAS_SHARED_SECRET": GAS_SECRET}
        with mock.patch.dict(os.environ, env), mock.patch.object(sw.requests, "get", side_effect=bad_get), \
                mock.patch.object(sw.time, "sleep"), contextlib.redirect_stdout(buf), \
                self.assertRaises(SystemExit):
            sw.main()
        log = buf.getvalue()
        self.assertIn("HTTPError(HTTP 404)", log)
        self.assertNotIn(GAS_SECRET, log)
        self.assertNotIn(SECRET_BODY, log)
        self.assertNotIn("script.google.test", log)

    def test_monthly_fetch_errors_are_redacted(self):
        def bad_get(url, params=None, timeout=None):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}?secret={GAS_SECRET}")

        buf = io.StringIO()
        with mock.patch.object(gm.requests, "get", side_effect=bad_get), mock.patch.object(gm.time, "sleep"), \
                contextlib.redirect_stdout(buf):
            self.assertIsNone(gm.fetch_month_items("https://script.google.test/exec", GAS_SECRET, "2026-09"))
        self.assertNotIn(GAS_SECRET, buf.getvalue())


class GuardTest(unittest.TestCase):
    def test_tracked_paths(self):
        self.assertEqual(guard.tracked_violations(["logs/health/a.md", "reports/x.csv", "mindmap/a.png",
                                                   "scripts/a.py", "state/news_seen.json", "docs/logs.md"]),
                         ["logs/health/a.md", "mindmap/a.png", "reports/x.csv"])

    def test_workflow_patterns(self):
        with tempfile.TemporaryDirectory() as d:
            wf = os.path.join(d, ".github", "workflows")
            os.makedirs(wf)
            open(os.path.join(wf, "bad.yml"), "w").write("steps:\n  - run: git add logs/health\n")
            open(os.path.join(wf, "bad2.yml"), "w").write("  with:\n    path: |\n      reports/weekly/\n")
            open(os.path.join(wf, "ok.yml"), "w").write("  - run: git add state/news_seen.json\n"
                                                         "# git add logs/health (コメントは対象外)\n")
            self.assertEqual(guard.workflow_violations(d), [os.path.join(".github", "workflows", "bad.yml"),
                                                            os.path.join(".github", "workflows", "bad2.yml")])

    def test_repository_is_clean_now(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(guard.main(), 0, buf.getvalue())

    def test_pages_config_excludes_data_dirs(self):
        cfg = open(os.path.join(ROOT, "_config.yml"), encoding="utf-8").read()
        for d in ("logs/", "reports/", "mindmap/", "state/", "scripts/"):
            self.assertIn(f"  - {d}", cfg)

    def test_gitignore_blocks_diary_dirs(self):
        import subprocess
        for p in ("logs/health/2026-09-26.md", "logs/daily/x.md", "reports/monthly/x.md", "mindmap/a.png"):
            r = subprocess.run(["git", "check-ignore", "-q", p], cwd=ROOT)
            self.assertEqual(r.returncode, 0, p)


if __name__ == "__main__":
    unittest.main()
