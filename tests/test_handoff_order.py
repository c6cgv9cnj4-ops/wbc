# -*- coding: utf-8 -*-
"""
実行順序の統合テスト：
  Mac（launchd / 手動）でJSON生成 → 専用ブランチへ公開 → GitHub Actions（06:05）が取得 → 既存Discord配信へ追加。
外部へは接続しない。公開先・取得元は擬似リモート（ローカルの bare リポジトリ）。
Discord 送信は差し替えて記録のみ。
"""
import contextlib
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_guard as G  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import badminton_handoff as H  # noqa: E402
import local_badminton_run as LR  # noqa: E402

try:
    import fetch_badminton_alerts as fb  # noqa: E402
except ImportError as err:  # 既存配信の依存が無い環境では省略（CI は依存を導入する）
    fb = None
    _IMPORT_ERROR = str(err)

UTC = dt.timezone.utc
BRANCH = "badminton-results-data"


def _match(mid="ord-1"):
    day = H.expected_api_day_utc(dt.datetime.now(UTC))
    return {"match_id": mid, "tournament": "BWF Order Test Open", "discipline": "WS",
            "side_a": [{"name": "Order Player"}], "side_b": [{"name": "Order Opponent"}],
            "games": [[21, 10], [21, 12]], "winner_side": "a", "status": "finished",
            "start_utc": f"{day}T03:00:00+00:00"}


def _workflow_fetch_step() -> str:
    """実際のワークフローから「試合結果ハンドオフを取得」ステップのシェル部分を取り出す（手書きコピーにしない）。"""
    lines = (ROOT / ".github" / "workflows" / "badminton_alerts.yml").read_text(encoding="utf-8").splitlines()
    i = next(n for n, l in enumerate(lines) if "試合結果ハンドオフを取得" in l)
    body = []
    for l in lines[i + 1:]:
        if l.startswith("      - name:"):
            break
        body.append(l)
    start = next(n for n, l in enumerate(body) if l.strip() == "run: |")
    return "\n".join(l[10:] for l in body[start + 1:] if l.strip()) + "\n"


class OrderTestBase(unittest.TestCase):
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
        self.checkout = self.tmp / "checkout"       # GitHub Actions の checkout に相当
        subprocess.run(["git", "clone", "-q", str(self.bare), str(self.checkout)], check=True)
        self.keyfile = self.tmp / "deploy_key_for_test"
        self.keyfile.write_text("dummy\n", encoding="utf-8")
        self.local = self.tmp / "mac-local"
        (self.local / "outbox").mkdir(parents=True)
        self.outbox = self.local / "outbox" / H.DATA_FILENAME
        self.now = dt.datetime.now(UTC).replace(microsecond=0)
        self.seen = {}
        self.sent = []
        self.events = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- Mac 側 ---
    def mac_generate(self, minutes_ago=5, status="ok", format_verified=True, matches=None):
        p = H.build_payload(generated_at_utc=self.now - dt.timedelta(minutes=minutes_ago),
                            api_day_utc=H.expected_api_day_utc(self.now), format_verified=format_verified,
                            status=status, matches=[_match()] if matches is None else matches)
        H.write_payload_atomic(self.outbox, p)

    def mac_publish(self, remote=None):
        env = {"BADMINTON_DATA_REMOTE": str(remote or self.bare), "BADMINTON_DATA_DEPLOY_KEY": str(self.keyfile)}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            res = LR.publish_handoff(outbox_path=self.outbox, local_dir=self.local,
                                     config={"data_publish_enabled": True}, now_utc=self.now, env=env)
        self.events.append(("mac_publish", res))
        self.mac_out = out.getvalue()
        return res

    # --- GitHub Actions 側 ---
    def github_fetch(self, origin=None):
        """ワークフローの取得ステップを、checkout の中で実行する。"""
        runner_temp = self.tmp / f"runner_temp_{len(self.events)}"
        runner_temp.mkdir(exist_ok=True)
        env_file = self.tmp / f"github_env_{len(self.events)}"
        env_file.write_text("", encoding="utf-8")
        if origin is not None:
            subprocess.run(["git", "-C", str(self.checkout), "remote", "set-url", "origin", str(origin)], check=True)
        step = self.tmp / "step.sh"
        step.write_text(_workflow_fetch_step(), encoding="utf-8")
        r = subprocess.run(["bash", str(step)], cwd=str(self.checkout), capture_output=True, text=True,
                           env=dict(os.environ, RUNNER_TEMP=str(runner_temp), GITHUB_ENV=str(env_file)))
        path = None
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("BADMINTON_HANDOFF_PATH="):
                path = line.split("=", 1)[1]
        self.events.append(("github_fetch", r.returncode))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # 取得ステップは失敗しても成功扱い（continue-on-error）
        return Path(path) if path and Path(path).exists() else None

    def github_main(self, handoff_path, band="morning", state=None):
        if fb is None:
            self.skipTest("既存配信の依存が無い")
        self.sent.clear()
        state = self.seen if state is None else state
        env = {"DISCORD_WEBHOOK_OSHI": "https://example.invalid/not-used",
               "BADMINTON_HANDOFF_PATH": str(handoff_path) if handoff_path else ""}
        patches = [
            mock.patch.object(fb, "fetch_tournament_article_urls", return_value=[]),
            mock.patch.object(fb, "fetch_badspi_articles", return_value=[]),
            mock.patch.object(fb, "fetch_google_news_badminton", return_value=[]),
            mock.patch.object(fb, "fetch_nba_result_ids", return_value=[]),
            mock.patch.object(fb, "fetch_wr_rankings_detailed", return_value=({}, {}, {})),
            mock.patch.object(fb, "is_weekly_ranking_check_time", return_value=False),
            mock.patch.object(fb, "get_time_band", return_value=band),
            mock.patch.object(fb, "load_seen_state", return_value=state),
            mock.patch.object(fb, "save_seen_state", side_effect=lambda s: None),
            mock.patch.object(fb, "build_badspi_embeds", return_value=[{"title": "既存バド×スピ", "description": "d", "color": 1}]),
            mock.patch.object(fb.tournament_master, "TournamentMaster", return_value=mock.Mock(dirty=False, errors=[])),
            mock.patch.object(fb, "send_embeds_to_discord", side_effect=self._send),
        ]
        code = 0
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
            for p in patches:
                p.start()
            try:
                fb.main()
            except SystemExit as e:
                code = e.code
            finally:
                for p in patches:
                    p.stop()
        self.events.append(("github_main", code))
        return code

    def _send(self, webhook, embeds, batch_size=10):
        self.sent.append(list(embeds))
        return True

    def titles(self):
        return [e["title"] for batch in self.sent for e in batch]

    def intl_sent(self):
        return any("BWF Order Test Open" in t for t in self.titles())

    def existing_sent(self):
        return "既存バド×スピ" in self.titles()

    def write_branch_file(self, payload_text):
        """ブランチに直接ファイルを置く（公開を経ずに、期限切れ・破損などの状態を作る）。"""
        w = self.tmp / "branch_writer"
        if w.exists():
            shutil.rmtree(w)
        subprocess.run(["git", "clone", "-q", str(self.bare), str(w)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(w), "checkout", "-q", "--orphan", BRANCH], check=True)
        subprocess.run(["git", "-C", str(w), "rm", "-rq", "--cached", "."], capture_output=True)
        (w / "badminton_results_handoff.json").write_text(payload_text, encoding="utf-8")
        g = ["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(g + ["add", "."], check=True)
        subprocess.run(g + ["commit", "-q", "-m", "test"], check=True)
        subprocess.run(["git", "-C", str(w), "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}"], check=True,
                       capture_output=True)


@unittest.skipIf(fb is None, "既存配信の依存（feedparser 等）が無い環境では統合順序テストを省略")
class OrderHappyPathTest(OrderTestBase):
    def test_mac_publish_then_github_fetch_then_single_existing_message(self):
        self.mac_generate(minutes_ago=5)
        self.assertEqual(self.mac_publish(), "published")       # 1) Mac の生成・公開が先
        handoff = self.github_fetch()                            # 2) GitHub 06:05 の取得
        self.assertIsNotNone(handoff)
        self.assertEqual(self.github_main(handoff), 0)           # 3) 既存配信へ追加
        self.assertEqual(len(self.sent), 1, "既存と新規は1メッセージ")
        self.assertTrue(self.existing_sent())
        self.assertTrue(self.intl_sent())
        self.assertEqual([e[0] for e in self.events], ["mac_publish", "github_fetch", "github_main"])

    def test_second_github_run_does_not_duplicate(self):
        self.mac_generate(minutes_ago=5)
        self.mac_publish()
        seen = {}
        self.github_main(self.github_fetch(), state=seen)
        self.assertTrue(self.intl_sent())
        self.github_main(self.github_fetch(), state=seen)        # 同日の再実行（既送信記録を引き継ぐ）
        self.assertFalse(self.intl_sent())
        self.assertTrue(self.existing_sent())

    def test_correction_is_sent_once_after_republish(self):
        self.mac_generate(minutes_ago=5)
        self.mac_publish()
        seen = {}
        self.github_main(self.github_fetch(), state=seen)
        changed = _match()
        changed["games"] = [[21, 10], [19, 21], [21, 12]]
        self.mac_generate(minutes_ago=1, matches=[changed])
        self.assertEqual(self.mac_publish(), "published")        # 内容が変わったので再公開
        self.github_main(self.github_fetch(), state=seen)
        self.assertTrue(any("（訂正）" in t for t in self.titles()))


@unittest.skipIf(fb is None, "既存配信の依存が無い環境では省略")
class OrderFailureIsolationTest(OrderTestBase):
    """どの段階で失敗しても、既存配信は従来どおり（既存の内容だけを送る・終了コードは正常）。"""

    def assert_existing_only(self, handoff):
        self.assertEqual(self.github_main(handoff), 0)
        self.assertTrue(self.existing_sent())
        self.assertFalse(self.intl_sent())

    def test_mac_publish_failure(self):
        self.mac_generate(minutes_ago=5)
        self.assertEqual(self.mac_publish(remote=self.tmp / "missing.git"), "failed")
        self.assert_existing_only(self.github_fetch())

    def test_json_missing_on_mac(self):
        self.assertEqual(self.mac_publish(), "no_payload")
        self.assert_existing_only(self.github_fetch())

    def test_json_expired_on_branch_is_ignored(self):
        old = H.build_payload(generated_at_utc=self.now - dt.timedelta(hours=27),
                              api_day_utc=H.expected_api_day_utc(self.now), format_verified=True,
                              status="ok", matches=[_match()])
        self.write_branch_file(json.dumps(old))
        self.assert_existing_only(self.github_fetch())

    def test_unverified_or_failed_json_is_never_published(self):
        self.mac_generate(minutes_ago=5, format_verified=False)
        self.assertEqual(self.mac_publish(), "not_ok")
        self.mac_generate(minutes_ago=5, status="fetch_failed")
        self.assertEqual(self.mac_publish(), "not_ok")
        self.assert_existing_only(self.github_fetch())

    def test_corrupt_json_on_branch_is_ignored(self):
        self.write_branch_file("{broken")
        self.assert_existing_only(self.github_fetch())

    def test_github_side_fetch_failure_continues(self):
        self.mac_generate(minutes_ago=5)
        self.mac_publish()
        self.assertIsNone(self.github_fetch(origin=self.tmp / "unreachable.git"))
        self.assert_existing_only(None)

    def test_github_fetches_before_mac_publish_delay(self):
        # Mac の公開が遅れ、GitHub の取得が先に走った場合：既存配信のみ（次回以降に持ち越し、誤配信しない）
        self.assertIsNone(self.github_fetch())
        self.mac_generate(minutes_ago=5)
        self.mac_publish()
        self.assert_existing_only(None)


@unittest.skipIf(fb is None, "既存配信の依存が無い環境では省略")
class ConcurrencyAndDelayTest(OrderTestBase):
    def test_overlapping_mac_runs_are_serialized_by_lock(self):
        # 05:30 の実行が進行中に 05:40 の再試行が来ても、二重に動かない（EX_LOCKED で終了）
        with LR.RunLock(self.local / "run.lock") as held:
            self.assertIsNotNone(held)
            with mock.patch.object(LR, "publish_handoff") as pub, contextlib.redirect_stdout(io.StringIO()) as out:
                rc = LR.run_local(local_dir=self.local, env={}, now_utc=self.now, keychain=lambda: "K")
        self.assertEqual(rc, LR.EX_LOCKED)
        pub.assert_not_called()
        self.assertIn("二重起動", out.getvalue())

    def test_github_run_sees_either_old_or_new_branch_state_atomically(self):
        # 取得は ref 単位。公開の途中（ファイル未完成）を読むことはない（コミット単位で見える）
        self.mac_generate(minutes_ago=5)
        self.mac_publish()
        before = self.github_fetch()
        self.mac_generate(minutes_ago=1, matches=[_match("ord-2")])
        self.mac_publish()
        after = self.github_fetch()
        for p in (before, after):
            data, reason = H.read_payload(p)
            self.assertEqual(reason, "ok")
            json.dumps(data)

    def test_publish_is_not_retried_when_nothing_changed(self):
        self.mac_generate(minutes_ago=5)
        self.assertEqual(self.mac_publish(), "published")
        self.assertEqual(self.mac_publish(), "unchanged")


class WorkflowSafetyStaticTest(unittest.TestCase):
    def test_schedule_and_flags_are_as_designed(self):
        import badminton_results_free as R
        wf = (ROOT / ".github" / "workflows" / "badminton_alerts.yml").read_text(encoding="utf-8")
        self.assertIn('- cron: "5 21 * * *"', wf)
        self.assertIn("continue-on-error: true", wf)
        self.assertIs(R.DISCORD_DELIVERY_ENABLED, False)
        cfg = json.loads((ROOT / "config" / "badminton_results.json").read_text(encoding="utf-8"))
        self.assertIs(cfg["data_publish_enabled"], False)
        self.assertIs(cfg["api_format_verified"], False)

    def test_workflow_does_not_reference_secrets_for_handoff(self):
        wf = (ROOT / ".github" / "workflows" / "badminton_alerts.yml").read_text(encoding="utf-8")
        step = wf[wf.index("試合結果ハンドオフを取得"):wf.index("指定選手の試合結果を取得")]
        self.assertNotIn("secrets.", step)


if __name__ == "__main__":
    unittest.main()
