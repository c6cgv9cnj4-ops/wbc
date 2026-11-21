# -*- coding: utf-8 -*-
"""
試合結果JSONの公開経路（ローカル実行 → 判定 → 専用ブランチ公開）の検証。
外部には一切接続しない。公開先はローカルの bare リポジトリ（擬似リモート）。
"""
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_guard as G  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import badminton_handoff as H  # noqa: E402
import local_badminton_run as LR  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 7, 21, 5, tzinfo=UTC)
KEY_PATH = "/secret/location/deploy_key_for_test"


def ok_payload(generated=NOW - dt.timedelta(minutes=2), **over):
    p = H.build_payload(generated_at_utc=generated, api_day_utc=H.expected_api_day_utc(NOW),
                        format_verified=True, status="ok",
                        matches=[{"match_id": "pub-1", "tournament": "Japan Open テスト",
                                  "discipline": "WS", "side_a": [{"name": "奥原希望"}],
                                  "side_b": [{"name": "B"}], "games": [[21, 1], [21, 2]],
                                  "winner_side": "a", "status": "finished",
                                  "start_utc": f"{H.expected_api_day_utc(NOW)}T03:00:00+00:00"}])
    p.update(over)
    return p


class DecisionTest(unittest.TestCase):
    def decide(self, payload, **kw):
        base = dict(enabled=True, key_present=True, last_digest=None, digest="d1")
        base.update(kw)
        return H.publish_decision(payload, NOW, **base)

    def test_disabled_never_publishes(self):
        self.assertEqual(self.decide(ok_payload(), enabled=False), "disabled")

    def test_fresh_verified_payload_publishes(self):
        self.assertEqual(self.decide(ok_payload()), "publish")

    def test_old_payload_is_never_republished(self):
        old = ok_payload(generated=NOW - dt.timedelta(hours=3))
        self.assertEqual(self.decide(old), "stale")

    def test_future_payload_is_refused(self):
        self.assertEqual(self.decide(ok_payload(generated=NOW + dt.timedelta(hours=1))), "stale")

    def test_no_key_refuses(self):
        self.assertEqual(self.decide(ok_payload(), key_present=False), "no_key")

    def test_fetch_failed_or_unverified_refuses(self):
        self.assertEqual(self.decide(ok_payload(status="fetch_failed")), "not_ok")
        self.assertEqual(self.decide(ok_payload(format_verified=False)), "not_ok")

    def test_missing_or_bad_payload_refuses(self):
        self.assertEqual(self.decide(None), "no_payload")
        self.assertEqual(self.decide(ok_payload(generated_at="bad")), "bad_generated_at")

    def test_identical_content_is_not_published_twice(self):
        self.assertEqual(self.decide(ok_payload(), last_digest="d1"), "unchanged")


class PublishEndToEndTest(unittest.TestCase):
    """擬似リモート（bare）に対して、公開スクリプトを実際に実行する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bare = self.tmp / "remote.git"
        seed = self.tmp / "seed"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        seed.mkdir()
        subprocess.run(["git", "-C", str(seed), "init", "-q"], check=True)
        (seed / "README.md").write_text("main\n", encoding="utf-8")
        g = ["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(g + ["add", "."], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "main"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", str(self.bare), "HEAD:refs/heads/main"], check=True)
        self.keyfile = self.tmp / "deploy_key_for_test"
        self.keyfile.write_text("dummy\n", encoding="utf-8")
        self.local = self.tmp / "local"
        (self.local / "outbox").mkdir(parents=True)
        self.outbox = self.local / "outbox" / H.DATA_FILENAME
        H.write_payload_atomic(self.outbox, ok_payload())
        self.env = dict(os.environ, BADMINTON_DATA_REMOTE=str(self.bare), BADMINTON_DATA_DEPLOY_KEY=str(self.keyfile))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def refs(self):
        out = subprocess.run(["git", "-C", str(self.bare), "for-each-ref", "--format=%(refname) %(objectname)"],
                             capture_output=True, text=True, check=True).stdout
        return dict(tuple(line.split(" ", 1)) for line in out.splitlines())

    def publish(self, config=None, env=None, now=NOW):
        cfg = {"data_publish_enabled": True} if config is None else config
        with contextlib.redirect_stdout(io.StringIO()) as out:
            res = LR.publish_handoff(outbox_path=self.outbox, local_dir=self.local, config=cfg,
                                     now_utc=now, env=self.env if env is None else env)
        return res, out.getvalue()

    def test_disabled_by_default_and_touches_nothing(self):
        res, _ = self.publish(config={"data_publish_enabled": False})
        self.assertEqual(res, "disabled")
        self.assertNotIn("refs/heads/badminton-results-data", self.refs())

    def test_publishes_only_to_data_branch_and_main_is_unchanged(self):
        main_before = self.refs()["refs/heads/main"]
        res, _ = self.publish()
        self.assertEqual(res, "published")
        refs = self.refs()
        self.assertIn("refs/heads/badminton-results-data", refs)
        self.assertEqual(refs["refs/heads/main"], main_before)
        shown = subprocess.run(["git", "-C", str(self.bare), "show",
                                "badminton-results-data:badminton_results_handoff.json"],
                               capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(shown)["status"], "ok")

    def test_same_content_is_not_published_again(self):
        self.publish()
        res, _ = self.publish()
        self.assertEqual(res, "unchanged")

    def test_old_json_is_not_republished_after_time_passes(self):
        self.publish()
        H.write_payload_atomic(self.outbox, ok_payload(generated=NOW - dt.timedelta(hours=5)))
        res, _ = self.publish(now=NOW + dt.timedelta(hours=4))
        self.assertEqual(res, "stale")

    def test_no_key_never_publishes(self):
        env = dict(self.env)
        env.pop("BADMINTON_DATA_DEPLOY_KEY")
        res, _ = self.publish(env=env)
        self.assertEqual(res, "no_key")
        self.assertNotIn("refs/heads/badminton-results-data", self.refs())

    def test_failure_is_logged_not_hidden_and_key_path_is_redacted(self):
        env = dict(self.env, BADMINTON_DATA_REMOTE=str(self.tmp / "missing.git"))
        res, out = self.publish(env=env)
        self.assertEqual(res, "failed")
        self.assertIn("公開に失敗しました", out)
        log = (self.local / "logs" / LR.PUBLISH_LOG).read_text(encoding="utf-8")
        self.assertIn("rc=", log)
        self.assertNotIn(str(self.keyfile), log + out)
        self.assertFalse((self.local / "publish_last.json").exists())   # 失敗時は記録しない（次回再試行）

    def test_corrupt_outbox_is_never_published(self):
        self.outbox.write_text("{broken", encoding="utf-8")
        res, _ = self.publish()
        self.assertEqual(res, "no_payload")
        self.assertNotIn("refs/heads/badminton-results-data", self.refs())

    def test_script_refuses_force_and_other_branches(self):
        text = LR.PUBLISH_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("--force", text)
        self.assertNotIn("+refs", text)
        self.assertIn("BRANCH=\"badminton-results-data\"", text)


class RunLocalPublishPathTest(unittest.TestCase):
    """launchd と手動実行の共通入口 run_local で、公開が呼ばれる条件を検証する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_key_stops_before_fetch_and_never_publishes(self):
        with mock.patch.object(LR, "publish_handoff") as pub, contextlib.redirect_stdout(io.StringIO()):
            rc = LR.run_local(local_dir=self.tmp, env={}, now_utc=NOW, keychain=lambda: None,
                              badspi_fetch=mock.Mock(side_effect=OSError("offline (test)")))
        self.assertEqual(rc, LR.EX_NO_KEY)
        pub.assert_not_called()

    def test_fetch_failure_writes_fetch_failed_and_does_not_publish(self):
        import requests

        def boom(api_key, cursor):
            raise requests.ConnectionError("offline (test)")

        with mock.patch.object(LR, "search_step", return_value={"status": "skipped"}), \
                mock.patch.object(LR.BS, "run_snapshot", return_value=0), \
                mock.patch.object(LR, "badspi_due", return_value=False), \
                mock.patch.object(LR, "publish_handoff", wraps=LR.publish_handoff) as pub, \
                contextlib.redirect_stdout(io.StringIO()):
            LR.run_local(local_dir=self.tmp, env={"BADMINTON_DATA_DEPLOY_KEY": KEY_PATH}, now_utc=NOW,
                         keychain=lambda: "TEST_KEY_NOT_REAL", fetch=boom, post=mock.Mock(), sleep=lambda s: None)
        data = json.loads((self.tmp / "outbox" / H.DATA_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "fetch_failed")
        self.assertEqual(data["matches"], [])
        self.assertEqual(pub.call_count, 1)
        self.assertEqual(pub.call_args.kwargs["config"]["data_publish_enabled"], False)  # 既定は無効

    def test_discord_remains_disabled_in_runner(self):
        import badminton_results_free as R
        self.assertIs(R.DISCORD_DELIVERY_ENABLED, False)
        self.assertIs(R.load_config()["data_publish_enabled"], False)


if __name__ == "__main__":
    unittest.main()


class RetryAfterPublishFailureTest(unittest.TestCase):
    """公開失敗後の次回実行で、API を再取得せずに公開だけ再試行されることを実行で証明する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.local = self.tmp / "local"
        self.local.mkdir()
        self.bare = self.tmp / "remote.git"
        seed = self.tmp / "seed"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        seed.mkdir()
        subprocess.run(["git", "-C", str(seed), "init", "-q"], check=True)
        (seed / "README.md").write_text("main\n", encoding="utf-8")
        g = ["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(g + ["add", "."], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "main"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", str(self.bare), "HEAD:refs/heads/main"], check=True)
        self.keyfile = self.tmp / "deploy_key_for_test"
        self.keyfile.write_text("dummy\n", encoding="utf-8")
        self.now = dt.datetime.now(UTC).replace(microsecond=0)
        self.api_day = LR.R.compute_window(self.now)["api_day_utc"]
        # 当日の取得は成功済み（state に ok の実行がある）。JSON は 05:30 相当に生成済み（5分前）
        state = LR.R.empty_state()
        state["runs"].append({"status": "ok", "api_day_utc": self.api_day, "run_at": self.now.isoformat()})
        LR.R.save_state(self.local / "badminton_results_state.json", state)
        H.write_payload_atomic(self.local / "outbox" / H.DATA_FILENAME,
                               ok_payload(generated=self.now - dt.timedelta(minutes=5)))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_once_local(self, remote):
        fetch = mock.Mock(side_effect=AssertionError("API must not be re-fetched"))
        env = {"BADMINTON_DATA_REMOTE": str(remote), "BADMINTON_DATA_DEPLOY_KEY": str(self.keyfile)}
        cfg = dict(LR.R.load_config(), data_publish_enabled=True)
        with mock.patch.object(LR, "search_step", return_value={"status": "skipped"}), \
                mock.patch.object(LR.BS, "run_snapshot", return_value=0), \
                mock.patch.object(LR, "badspi_due", return_value=False), \
                mock.patch.object(LR.R, "load_config", return_value=cfg), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            rc = LR.run_local(local_dir=self.local, env=env, now_utc=self.now,
                              keychain=lambda: "TEST_KEY_NOT_REAL", fetch=fetch, post=mock.Mock(),
                              sleep=lambda s: None)
        fetch.assert_not_called()           # 当日取得済みのため API を呼ばない
        return rc, out.getvalue()

    def test_failed_publish_is_retried_next_run_without_refetch(self):
        broken = self.tmp / "no-such-remote.git"
        rc1, out1 = self.run_once_local(broken)
        self.assertEqual(rc1, LR.EX_FAIL)                                  # 失敗は隠さない
        self.assertIn("公開に失敗しました", out1)
        self.assertFalse((self.local / "publish_last.json").exists())      # 成功していないので記録しない
        rc2, out2 = self.run_once_local(self.bare)                          # 次回実行（同日）
        self.assertEqual(rc2, LR.EX_OK, out2)
        self.assertIn("公開しました", out2)
        self.assertTrue((self.local / "publish_last.json").exists())
        rc3, out3 = self.run_once_local(self.bare)                          # 同じ内容は再公開しない
        self.assertIn("公開しません（unchanged）", out3)

    def test_stale_json_is_not_published_on_retry(self):
        H.write_payload_atomic(self.local / "outbox" / H.DATA_FILENAME,
                               ok_payload(generated=self.now - dt.timedelta(hours=3)))
        rc, out = self.run_once_local(self.bare)
        self.assertIn("公開しません（stale）", out)
        self.assertNotIn("refs/heads/badminton-results-data", self._refs())

    def test_not_ok_json_is_never_published_on_retry(self):
        H.write_payload_atomic(self.local / "outbox" / H.DATA_FILENAME,
                               ok_payload(generated=self.now - dt.timedelta(minutes=1), status="fetch_failed"))
        rc, out = self.run_once_local(self.bare)
        self.assertIn("公開しません（not_ok）", out)
        self.assertNotIn("refs/heads/badminton-results-data", self._refs())

    def _refs(self):
        out = subprocess.run(["git", "-C", str(self.bare), "for-each-ref", "--format=%(refname)"],
                             capture_output=True, text=True, check=True).stdout
        return set(out.split())


class PublishAuthFailureTest(unittest.TestCase):
    """認証・接続の失敗を「ブランチ未作成」と誤認しないこと。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.key = self.tmp / "k"
        self.key.write_text("x", encoding="utf-8")
        self.src = self.tmp / "p.json"
        H.write_payload_atomic(self.src, ok_payload())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unreachable_remote_is_reported_as_connection_failure_not_missing_branch(self):
        env = dict(os.environ, BADMINTON_DATA_REMOTE=str(self.tmp / "nope.git"),
                   BADMINTON_DATA_DEPLOY_KEY=str(self.key))
        r = subprocess.run(["bash", str(LR.PUBLISH_SCRIPT), str(self.src)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("接続の失敗", r.stdout)
        self.assertNotIn("未作成", r.stdout)
        self.assertNotIn(str(self.key), r.stdout + r.stderr)   # 鍵の場所は表示しない

    def test_missing_branch_on_working_remote_is_created(self):
        bare = self.tmp / "ok.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        env = dict(os.environ, BADMINTON_DATA_REMOTE=str(bare), BADMINTON_DATA_DEPLOY_KEY=str(self.key))
        r = subprocess.run(["bash", str(LR.PUBLISH_SCRIPT), str(self.src)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("未作成のため新規作成", r.stdout)

    def test_existing_branch_is_updated_not_recreated(self):
        bare = self.tmp / "ok2.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        env = dict(os.environ, BADMINTON_DATA_REMOTE=str(bare), BADMINTON_DATA_DEPLOY_KEY=str(self.key))
        subprocess.run(["bash", str(LR.PUBLISH_SCRIPT), str(self.src)], env=env, capture_output=True, text=True, check=True)
        H.write_payload_atomic(self.src, ok_payload(matches=[]))
        r = subprocess.run(["bash", str(LR.PUBLISH_SCRIPT), str(self.src)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("未作成のため新規作成", r.stdout)
        count = subprocess.run(["git", "-C", str(bare), "rev-list", "--count", "badminton-results-data"],
                               capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(count, "2")   # 既存の履歴に積まれている（作り直していない）
