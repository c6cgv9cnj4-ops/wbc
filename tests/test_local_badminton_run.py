# -*- coding: utf-8 -*-
"""ローカル実行（Mac mini・launchd）の検証。ネットワーク・Keychain・Discord・launchctl は使わない（すべて差し替え）。

確認すること:
- APIキーが無い場合は取得を試みず停止（終了コード78）。キーを表示しない。
- 二重起動はロックで防ぐ（終了コード75）。
- 当日分が取得済みなら再取得しない。badspi は週1回だけ取得する。
- 台帳の検索は日次上限内。Discord は送信しない（既定OFF・webhookは空）。
- launchd の plist は 06:05 固定・キーを含まない。登録（launchctl）は既定では実行されない。
"""
import contextlib
import datetime as dt
import fcntl
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import os as _os  # noqa: E402
import sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import offline_guard  # noqa: E402,F401  (テスト中の実通信を遮断)
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import requests  # noqa: E402
import local_badminton_run as LR  # noqa: E402
import badminton_results_free as R  # noqa: E402
import badminton_jp_ledger as L  # noqa: E402
import badspi_ranking_snapshot as BS  # noqa: E402

FAKE_KEY = "FAKE_KEY_DO_NOT_PRINT_123"
RUN_1 = dt.datetime(2026, 11, 5, 21, 5, tzinfo=dt.timezone.utc)   # 2026-11-06 06:05 JST
REAL = ROOT / "tests" / "fixtures" / "real"


def _item(mid="ev_loc", s1=("pl_jp1", "渡邉航貴")):
    return {"id": mid, "tournament": {"name": "Local Open", "year": 2026}, "discipline": "MS",
            "side1": {"players": [{"id": s1[0], "name": s1[1]}]},
            "side2": {"players": [{"id": "pl_x", "name": "相手"}]},
            "games": [[21, 10], [21, 12]], "winner": 1, "status": "finished", "start": "2026-11-04T12:00:00Z"}


def _fetch_items(items):
    calls = []

    def fetch(api_key, cursor):
        calls.append(api_key)
        return 200, {"data": items, "meta": {}}
    fetch.calls = calls
    return fetch


def _real_html(code):
    return BS.decode_body((REAL / f"badspi_world_{code}_20261103.html").read_bytes(), "utf-8")


_URL_TO_CODE = {url: code for code, url in json.loads(
    (ROOT / "config" / "badminton_results.json").read_text(encoding="utf-8"))["badspi_ranking_urls"].items()}


def _badspi_fake(calls=None):
    """設定のURLごとに、対応する実HTML（原本）を返す。"""
    def fetch(url):
        if calls is not None:
            calls.append(url)
        return _real_html(_URL_TO_CODE[url]), url
    return fetch


class KeychainTest(unittest.TestCase):
    def test_reads_stdout_only_and_strips_newline(self):
        runner = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout=FAKE_KEY + "\n", stderr=""))
        self.assertEqual(LR.read_keychain_key(runner=runner), FAKE_KEY)
        args = runner.call_args.args[0]
        self.assertEqual(args[:2], ["security", "find-generic-password"])
        self.assertIn("-w", args)

    def test_missing_item_returns_none_without_leaking_stderr(self):
        runner = mock.Mock(return_value=SimpleNamespace(returncode=44, stdout="", stderr="secret detail"))
        self.assertIsNone(LR.read_keychain_key(runner=runner))

    def test_tool_unavailable_returns_none(self):
        runner = mock.Mock(side_effect=OSError("no security"))
        self.assertIsNone(LR.read_keychain_key(runner=runner))


class LockTest(unittest.TestCase):
    def test_second_holder_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.lock"
            with LR.RunLock(path) as first:
                self.assertIsNotNone(first)
                with LR.RunLock(path) as second:
                    self.assertIsNone(second)
            with LR.RunLock(path) as again:
                self.assertIsNotNone(again)


class RunLocalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.local = Path(self._tmp.name) / "badminton"
        self.roster_patch = mock.patch.object(R, "load_roster", wraps=lambda p=None: {
            "pl_jp1": {"name": "渡邉航貴", "status": R.JP_CONFIRMED}})
        self.roster_patch.start()
        self.addCleanup(self.roster_patch.stop)
        self.post = mock.Mock(return_value=SimpleNamespace(status_code=204))
        self.no_search = mock.Mock(return_value=(200, {"data": [], "meta": {}}))

    def tearDown(self):
        self._tmp.cleanup()

    def run_local(self, *, key, fetch, now=RUN_1, badspi=None, search=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = LR.run_local(local_dir=self.local, env={}, now_utc=now,
                              keychain=lambda: key, fetch=fetch, post=self.post,
                              fetch_search=search or self.no_search,
                              badspi_fetch=badspi or _badspi_fake(),
                              sleep=lambda s: None)
        return rc, buf.getvalue()

    def test_no_key_stops_without_any_api_call_and_never_prints_key(self):
        fetch = _fetch_items([_item()])
        rc, out = self.run_local(key=None, fetch=fetch)
        self.assertEqual(rc, LR.EX_NO_KEY)
        self.assertEqual(fetch.calls, [], "キーが無ければAPIを呼ばない")
        self.assertIn("キーの取得や登録は行っていません", out)
        self.assertNotIn(FAKE_KEY, out)
        self.assertFalse((self.local / "badminton_results_state.json").exists())

    def test_no_key_still_runs_keyless_badspi_step(self):
        rc, out = self.run_local(key=None, fetch=_fetch_items([]))
        self.assertIn("badspi 世界ランキング", out)
        self.assertEqual(len(list((self.local / "badspi_ranking").glob("*.json"))), 5)

    def test_key_present_runs_results_and_writes_only_local_state(self):
        fetch = _fetch_items([_item()])
        rc, out = self.run_local(key=FAKE_KEY, fetch=fetch)
        self.assertEqual(rc, LR.EX_OK, out)
        self.assertEqual(fetch.calls, [FAKE_KEY])
        state = json.loads((self.local / "badminton_results_state.json").read_text(encoding="utf-8"))
        self.assertIn("ev_loc", state["matches"])
        self.assertNotIn(FAKE_KEY, out)
        self.assertNotIn(FAKE_KEY, (self.local / "badminton_results_state.json").read_text(encoding="utf-8"))

    def test_discord_is_never_sent_by_local_runner(self):
        rc, _ = self.run_local(key=FAKE_KEY, fetch=_fetch_items([_item()]))
        self.assertEqual(self.post.call_count, 0)

    def test_second_run_same_utc_day_does_not_refetch(self):
        self.run_local(key=FAKE_KEY, fetch=_fetch_items([_item()]))
        fetch2 = _fetch_items([_item()])
        rc, out = self.run_local(key=FAKE_KEY, fetch=fetch2, now=RUN_1 + dt.timedelta(hours=1))
        self.assertEqual(fetch2.calls, [])
        self.assertIn("取得済みのため、再取得しません", out)

    def test_locked_run_exits_75_and_does_nothing(self):
        self.local.mkdir(parents=True)
        fh = open(self.local / "run.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fetch = _fetch_items([_item()])
            rc, out = self.run_local(key=FAKE_KEY, fetch=fetch)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
        self.assertEqual(rc, LR.EX_LOCKED)
        self.assertEqual(fetch.calls, [])

    def test_api_failure_is_exit_1_and_keeps_no_partial_state(self):
        def boom(k, c):
            raise requests.ConnectionError("network down")
        rc, out = self.run_local(key=FAKE_KEY, fetch=boom)
        self.assertEqual(rc, LR.EX_FAIL)
        self.assertNotIn(FAKE_KEY, out)

    def test_search_step_is_capped_and_writes_queue_locally(self):
        cand = {"schema_version": 1, "candidates": [
            {"jba_detail_id": str(i), "name": f"候補{i}", "team": "", "prefecture": "", "pages": ["p"],
             "source_urls": ["https://www.badminton.or.jp/x"], "status": "UNMATCHED", "search": None,
             "same_name_count": 1, "jba_detail_url": f"u{i}"} for i in range(49)]}
        repo_copy = ROOT / "config" / "badminton_jp_candidates.json"
        self.local.mkdir(parents=True)
        (self.local / "badminton_jp_candidates.json").write_text(json.dumps(cand), encoding="utf-8")
        search = mock.Mock(return_value=(200, {"data": [
            {"kind": "player", "id": "pl_1", "name": "候補0", "country": "JPN", "score": 90}], "meta": {}}))
        rc, out = self.run_local(key=FAKE_KEY, fetch=_fetch_items([]), search=search)
        self.assertLessEqual(search.call_count, L.SEARCH_MAX_PER_RUN)
        saved = json.loads((self.local / "badminton_jp_candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(1 for c in saved["candidates"] if c["search"] is not None), search.call_count)
        self.assertTrue((self.local / "badminton_jp_confirm_queue.md").exists())
        self.assertIn("候補0", (self.local / "badminton_jp_confirm_queue.md").read_text(encoding="utf-8"))
        self.assertNotIn(FAKE_KEY, out)
        self.assertNotEqual(repo_copy.read_text(encoding="utf-8"), "", "repo config is untouched")

    def test_badspi_weekly_gate(self):
        snap_dir = self.local / "badspi_ranking"
        self.assertTrue(LR.badspi_due(snap_dir, RUN_1))
        snap_dir.mkdir(parents=True)
        for code in BS.DISCIPLINE_NAMES:
            snap = BS.build_snapshot(_real_html(code), discipline_code=code, source_url="x",
                                     fetched_at=RUN_1.isoformat())
            BS.save_snapshot(snap_dir, snap)
        self.assertFalse(LR.badspi_due(snap_dir, RUN_1 + dt.timedelta(days=3)))
        self.assertTrue(LR.badspi_due(snap_dir, RUN_1 + dt.timedelta(days=8)))

    def test_badspi_runs_only_when_due(self):
        calls = []
        self.run_local(key=None, fetch=_fetch_items([]), badspi=_badspi_fake(calls))
        n_first = len(calls)
        self.assertEqual(n_first, 5)
        self.run_local(key=None, fetch=_fetch_items([]), now=RUN_1 + dt.timedelta(days=1),
                       badspi=_badspi_fake(calls))
        self.assertEqual(len(calls), n_first, "週1回：1日後は取得しない")


class MainEntrypointNoKeyTest(unittest.TestCase):
    """launchd が実行する入口（main）。キー無しでは試合結果のAPIを一切呼ばず、badspi（キー不要）だけ取得する。"""

    def test_main_without_key_never_calls_results_api_or_discord_and_fetches_badspi_only(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "badminton"
            calls = {"results": 0, "post": 0, "badspi": []}

            def results_api(*a, **k):
                calls["results"] += 1
                raise AssertionError("results API must not be called without key")

            def post(*a, **k):
                calls["post"] += 1
                raise AssertionError("Discord must not be called")

            def badspi(url):
                calls["badspi"].append(url)
                code = {u: c for c, u in json.loads((ROOT / "config" / "badminton_results.json").read_text(
                    encoding="utf-8"))["badspi_ranking_urls"].items()}[url]
                return _real_html(code), url

            with mock.patch.dict(os.environ, {"BADMINTON_LOCAL_DIR": str(local)}), \
                    mock.patch.object(LR, "read_keychain_key", return_value=None), \
                    mock.patch.object(R, "fetch_page", side_effect=results_api), \
                    mock.patch.object(requests, "post", side_effect=post), \
                    mock.patch.object(BS, "fetch_html", side_effect=badspi), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                rc = LR.main([])
            self.assertEqual(rc, LR.EX_NO_KEY)
            self.assertEqual(calls["results"], 0)
            self.assertEqual(calls["post"], 0)
            self.assertEqual(len(calls["badspi"]), 5)
            self.assertEqual(len(list((local / "badspi_ranking").glob("*.json"))), 5)
            self.assertNotIn("BADMINTON_API_KEY=", out.getvalue())
            self.assertFalse((local / "badminton_results_state.json").exists())


class LaunchdTemplateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # 検証用の専用venv（実際のPythonへのリンク。本番の venv は作らない）
        (self.tmp / "venv" / "bin").mkdir(parents=True)
        (self.tmp / "venv" / "bin" / "python").symlink_to(sys.executable)

    def tearDown(self):
        self._tmp.cleanup()

    def render(self, extra_env=None):
        target = self.tmp / "out.plist"
        env = dict(os.environ, BADMINTON_LOCAL_DIR=str(self.tmp / "local"),
                   BADMINTON_VENV_DIR=str(self.tmp / "venv"), **(extra_env or {}))
        r = subprocess.run(["bash", str(ROOT / "scripts" / "install_local_badminton_launchd.sh"),
                            "--target", str(target)], env=env, capture_output=True, text=True)
        return r, target

    def test_plist_is_0605_runs_runner_and_contains_no_secrets(self):
        r, target = self.render()
        self.assertEqual(r.returncode, 0, r.stderr)
        text = target.read_text(encoding="utf-8")
        self.assertIn("<integer>6</integer>", text)
        self.assertIn("<integer>5</integer>", text)
        self.assertIn("local_badminton_run.py", text)
        for forbidden in ("KEY", "WEBHOOK", "DISCORD", "secret", "token"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("__", text.replace("__init__", ""), "placeholders are all replaced")

    def test_plist_passes_plutil_lint_on_macos(self):
        if shutil.which("plutil") is None:
            self.skipTest("plutil は macOS のみ")
        r, target = self.render()
        lint = subprocess.run(["plutil", "-lint", str(target)], capture_output=True, text=True)
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_default_install_does_not_call_launchctl(self):
        fake_bin = self.tmp / "bin"
        fake_bin.mkdir()
        calls = self.tmp / "launchctl_calls.txt"
        (fake_bin / "launchctl").write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n', encoding="utf-8")
        (fake_bin / "launchctl").chmod(0o755)
        env_path = f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"
        r, _ = self.render({"PATH": env_path})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(calls.exists(), "既定では launchctl を実行しない")

    def test_plist_uses_venv_python_not_system_python(self):
        r, target = self.render()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(self.tmp / "venv" / "bin" / "python"), target.read_text(encoding="utf-8"))

    def test_missing_venv_stops_with_instruction_and_writes_no_plist(self):
        target = self.tmp / "none.plist"
        env = dict(os.environ, BADMINTON_LOCAL_DIR=str(self.tmp / "local"),
                   BADMINTON_VENV_DIR=str(self.tmp / "no-venv"))
        r = subprocess.run(["bash", str(ROOT / "scripts" / "install_local_badminton_launchd.sh"),
                            "--target", str(target)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 3)
        self.assertFalse(target.exists())

    def test_unknown_argument_is_rejected(self):
        r, _ = self.render()
        r = subprocess.run(["bash", str(ROOT / "scripts" / "install_local_badminton_launchd.sh"), "--nope"],
                           capture_output=True, text=True,
                           env=dict(os.environ, BADMINTON_VENV_DIR=str(self.tmp / "venv")))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
