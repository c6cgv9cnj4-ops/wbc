# -*- coding: utf-8 -*-
"""
試合結果ハンドオフ（ローカル生成JSON → 既存配信へ追加）の監査テスト。
通信はしない（ガードで遮断）。Discord へは送らない。
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
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_guard as G  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import badminton_handoff as H  # noqa: E402
import badminton_results_free as R  # noqa: E402

UTC = dt.timezone.utc
JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 7, 21, 5, tzinfo=UTC)          # JST 2026-11-08 06:05 の実行を想定
API_DAY = "2026-11-06"                                     # expected_api_day_utc(NOW)
KEY_LIKE = "SECRET_KEY_FOR_TEST_ONLY_12345"


def match(**over):
    m = {
        "match_id": "m-1",
        "tournament": "テスト国際大会",
        "discipline": "WS",
        "side_a": [{"name": "奥原 希望"}],
        "side_b": [{"name": "Test Opponent"}],
        "games": [[21, 15], [21, 18]],
        "winner_side": "a",
        "status": "finished",
        "start_utc": "2026-11-06T04:30:00+00:00",
    }
    m.update(over)
    return m


def payload(matches=None, generated_at_utc=None, **over):
    p = H.build_payload(generated_at_utc=generated_at_utc or (NOW - dt.timedelta(hours=1)),
                        api_day_utc=over.pop("api_day_utc", API_DAY),
                        format_verified=over.pop("format_verified", True),
                        status=over.pop("status", "ok"),
                        matches=[match()] if matches is None else matches)
    p.update(over)
    return p


class HandoffTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "handoff.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, obj):
        self.path.write_text(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def collect(self, seen=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return H.collect_embeds(self.path, NOW, {} if seen is None else seen)


class ConsumerFailureIsolationTest(HandoffTestBase):
    """JSONが無い・壊れている・古い・未検証 → 新規0件（既存配信は従来どおり）。"""

    def test_missing_file_is_ignored(self):
        embeds, marks, reason = self.collect()
        self.assertEqual((embeds, marks, reason), ([], [], "missing"))

    def test_corrupt_json_is_ignored(self):
        self.write("{not json")
        self.assertEqual(self.collect()[2], "corrupt")

    def test_empty_file_is_ignored(self):
        self.write("")
        self.assertEqual(self.collect()[2], "corrupt")

    def test_wrong_schema_is_ignored(self):
        self.write({"schema_version": 99, "status": "ok"})
        self.assertEqual(self.collect()[2], "schema_mismatch")

    def test_fetch_failed_is_ignored(self):
        self.write(payload(status="fetch_failed", matches=[match()]))
        self.assertEqual(self.collect()[2], "status_fetch_failed")

    def test_format_unverified_is_ignored_even_if_matches_present(self):
        self.write(payload(format_verified=False, matches=[match()]))
        embeds, _, reason = self.collect()
        self.assertEqual((embeds, reason), ([], "format_unverified"))

    def test_expired_payload_is_ignored(self):
        self.write(payload(generated_at_utc=NOW - dt.timedelta(hours=27)))
        self.assertEqual(self.collect()[2], "expired")

    def test_future_generated_at_is_ignored(self):
        self.write(payload(generated_at_utc=NOW + dt.timedelta(hours=1)))
        self.assertEqual(self.collect()[2], "generated_in_future")

    def test_payload_for_other_api_day_is_ignored(self):
        self.write(payload(api_day_utc="2026-11-01", matches=[match()]))
        self.assertEqual(self.collect()[2], "wrong_api_day")

    def test_zero_matches_is_ok_and_distinct_from_failure(self):
        self.write(payload(matches=[]))
        embeds, marks, reason = self.collect()
        self.assertEqual((embeds, marks, reason), ([], [], "ok"))


class MatchValidationTest(unittest.TestCase):
    def test_valid_match_passes(self):
        self.assertIsNone(H.validate_match(match(), API_DAY))

    def test_winner_contradicting_games_is_rejected(self):
        self.assertEqual(H.validate_match(match(winner_side="b"), API_DAY), "winner_contradicts_games")

    def test_not_finished_is_rejected(self):
        for st in ("postponed", "cancelled", "in_progress", "retired", "", None):
            self.assertEqual(H.validate_match(match(status=st), API_DAY), "not_finished", st)

    def test_start_without_timezone_is_rejected(self):
        self.assertEqual(H.validate_match(match(start_utc="2026-11-06T04:30:00"), API_DAY),
                         "start_without_timezone")

    def test_timezone_offset_is_converted_to_utc_day(self):
        # JST 2026-11-06 09:30 = UTC 2026-11-06 00:30 → 対象日 OK
        self.assertIsNone(H.validate_match(match(start_utc="2026-11-06T09:30:00+09:00"), API_DAY))
        # JST 2026-11-06 08:30 = UTC 2026-11-05 23:30 → 対象日外
        self.assertEqual(H.validate_match(match(start_utc="2026-11-06T08:30:00+09:00"), API_DAY),
                         "outside_window")

    def test_bad_game_scores_are_rejected(self):
        for games in ([[21]], [[21, "15"]], [[-1, 21]], [[51, 0]], [[True, 0]], []):
            self.assertIsNotNone(H.validate_match(match(games=games), API_DAY), games)

    def test_bad_discipline_and_players_are_rejected(self):
        self.assertEqual(H.validate_match(match(discipline="XX"), API_DAY), "bad_discipline")
        self.assertEqual(H.validate_match(match(side_a=[]), API_DAY), "bad_side_a")
        self.assertEqual(H.validate_match(match(side_b=[{"name": ""}]), API_DAY), "bad_side_b_name")

    def test_doubles_with_two_players_is_accepted(self):
        m = match(discipline="MD", side_a=[{"name": "A"}, {"name": "B"}])
        self.assertIsNone(H.validate_match(m, API_DAY))


class ConsumerContentTest(HandoffTestBase):
    def test_valid_match_builds_embed_with_all_required_fields(self):
        self.write(payload())
        embeds, marks, reason = self.collect()
        self.assertEqual(reason, "ok")
        self.assertEqual(len(embeds), 1)
        e = embeds[0]
        self.assertIn("テスト国際大会", e["title"])
        self.assertIn("女子シングルス", e["title"])
        self.assertIn("**奥原 希望** 21-15 21-18 Test Opponent", e["description"])  # 勝者・スコア・敗者
        self.assertIn("badmintonapi.com", e["description"])                       # 出典
        self.assertEqual(marks[0][0], "intl::m-1")

    def test_winner_side_b_is_rendered_first(self):
        self.write(payload([match(winner_side="b", games=[[10, 21], [12, 21]])]))
        embeds, _, _ = self.collect()
        self.assertIn("**Test Opponent** 10-21 12-21 奥原 希望", embeds[0]["description"])

    def test_control_characters_are_removed_from_names(self):
        self.write(payload([match(side_a=[{"name": "奥原\x00\x07希望"}])]))
        embeds, _, _ = self.collect()
        self.assertNotIn("\x00", embeds[0]["description"])
        self.assertIn("奥原希望", embeds[0]["description"])

    def test_invalid_entries_are_skipped_individually(self):
        self.write(payload([match(match_id="ok-1"), match(match_id="bad-1", status="postponed"),
                            "not-a-dict", match(match_id="bad-2", winner_side="b")]))
        embeds, marks, _ = self.collect()
        self.assertEqual([k for k, _ in marks], ["intl::ok-1"])
        self.assertEqual(len(embeds), 1)

    def test_over_limit_is_capped(self):
        many = [match(match_id=f"m-{i}") for i in range(H.MAX_EMBEDS_PER_RUN + 5)]
        self.write(payload(many))
        embeds, _, _ = self.collect()
        self.assertEqual(len(embeds), H.MAX_EMBEDS_PER_RUN)


class DedupeAndCorrectionTest(HandoffTestBase):
    def test_same_match_is_not_sent_twice(self):
        self.write(payload())
        seen = {}
        embeds, marks, _ = self.collect(seen)
        self.assertEqual(len(embeds), 1)
        H.apply_marks(seen, marks, NOW)          # 送信成功後にだけ記録
        embeds2, marks2, _ = self.collect(seen)
        self.assertEqual((embeds2, marks2), ([], []))

    def test_unsent_match_is_retried_when_marks_not_applied(self):
        # 送信失敗時は apply_marks を呼ばない → 次回に再試行される
        self.write(payload())
        seen = {}
        self.collect(seen)
        embeds2, _, _ = self.collect(seen)
        self.assertEqual(len(embeds2), 1)

    def test_changed_result_is_sent_once_as_correction(self):
        self.write(payload())
        seen = {}
        _, marks, _ = self.collect(seen)
        H.apply_marks(seen, marks, NOW)
        self.write(payload([match(games=[[21, 15], [19, 21], [21, 10]], winner_side="a")]))
        embeds, marks2, _ = self.collect(seen)
        self.assertEqual(len(embeds), 1)
        self.assertIn("（訂正）", embeds[0]["title"])
        H.apply_marks(seen, marks2, NOW)
        self.assertEqual(self.collect(seen)[0], [])

    def test_prune_removes_old_marks_only(self):
        seen = {"intl::old": {"hash": "x", "sent_at": (NOW - dt.timedelta(days=40)).isoformat()},
                "intl::new": {"hash": "y", "sent_at": NOW.isoformat()},
                "other::key": {"keep": True}}
        H.prune_seen(seen, NOW)
        self.assertNotIn("intl::old", seen)
        self.assertIn("intl::new", seen)
        self.assertIn("other::key", seen)

    def test_same_content_with_different_id_is_new_match(self):
        self.write(payload([match(match_id="a"), match(match_id="b")]))
        embeds, _, _ = self.collect()
        self.assertEqual(len(embeds), 2)


class ProducerTest(unittest.TestCase):
    """ローカル側：応答形式が未検証の間は試合を書き出さない。取得失敗と0件を区別する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "outbox" / "h.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_discord_delivery_is_disabled_by_constant(self):
        self.assertIs(R.DISCORD_DELIVERY_ENABLED, False)
        self.assertIs(R.DEFAULT_CONFIG["api_format_verified"], False)

    def test_discord_on_config_sends_nothing_from_local_job(self):
        # 設定を true にしても、ローカルジョブからは一切送信しない（封じ込め）
        post = mock.Mock()
        state_path = self.tmp / "state.json"
        cfg = dict(R.DEFAULT_CONFIG, discord_output_enabled=True)
        with contextlib.redirect_stdout(io.StringIO()):
            R.run_once(api_key="K", webhook="https://example.invalid/hook", now_utc=NOW, state_path=state_path,
                       config=cfg, roster={}, fetch=lambda key, cursor: (200, {"data": [], "meta": {}}),
                       post=post, sleep=lambda s: None, event="manual")
        post.assert_not_called()

    def test_unverified_format_writes_no_matches(self):
        window = R.compute_window(NOW)
        state = {"matches": {"x": {"id": "x", "date_status": "confirmed", "winner": 1, "games": [[21, 1], [21, 2]],
                                   "start": "2026-11-06T04:30:00+00:00", "status": "finished",
                                   "side1": [{"id": "1", "name": "奥原"}], "side2": [{"id": "2", "name": "B"}],
                                   "tournament": {"name": "T"}, "discipline": "WS"}}}
        roster = {"1": {"name": "奥原", "status": R.JP_CONFIRMED}}
        with contextlib.redirect_stdout(io.StringIO()):
            R._write_handoff_safely(self.out, NOW, window, {"api_format_verified": False}, "ok", state, roster)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "format_unverified")
        self.assertEqual(data["matches"], [])

    def test_verified_format_writes_only_confirmed_jp_matches(self):
        window = R.compute_window(NOW)
        base = {"date_status": "confirmed", "winner": 1, "games": [[21, 1], [21, 2]],
                "start": "2026-11-06T04:30:00+00:00", "status": "finished",
                "side1": [{"id": "1", "name": "奥原"}], "side2": [{"id": "2", "name": "B"}],
                "tournament": {"name": "T"}, "discipline": "WS"}
        state = {"matches": {
            "jp": dict(base, id="jp"),
            "non_jp": dict(base, id="non", side1=[{"id": "9", "name": "X"}]),
            "unconfirmed_date": dict(base, id="ud", date_status="unknown"),
        }}
        roster = {"1": {"name": "奥原", "status": R.JP_CONFIRMED}, "9": {"name": "X", "status": R.NON_JP_CONFIRMED}}
        with contextlib.redirect_stdout(io.StringIO()):
            R._write_handoff_safely(self.out, NOW, window, {"api_format_verified": True}, "ok", state, roster)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "ok")
        self.assertEqual([m["match_id"] for m in data["matches"]], ["jp"])
        self.assertIsNone(H.validate_match(data["matches"][0], data["api_day_utc"]))

    def test_fetch_failure_overwrites_stale_matches(self):
        self.out.parent.mkdir(parents=True)
        H.write_payload_atomic(self.out, payload())   # 前回の古い結果
        window = R.compute_window(NOW)
        with contextlib.redirect_stdout(io.StringIO()):
            R._write_handoff_safely(self.out, NOW, window, {"api_format_verified": True}, "fetch_failed", {"matches": {}}, {})
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((data["status"], data["matches"]), ("fetch_failed", []))

    def test_write_failure_never_raises(self):
        window = R.compute_window(NOW)
        blocker = self.tmp / "file-not-dir"
        blocker.write_text("x", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            R._write_handoff_safely(blocker / "h.json", NOW, window, {"api_format_verified": True}, "ok", {"matches": {}}, {})
        self.assertIn("既存配信へは影響しません", out.getvalue())

    def test_api_day_matches_results_free_window_formula(self):
        for now in (NOW, dt.datetime(2026, 11, 7, 0, 0, tzinfo=UTC), dt.datetime(2026, 11, 7, 23, 59, tzinfo=UTC)):
            self.assertEqual(H.expected_api_day_utc(now), R.compute_window(now)["api_day_utc"])


class SecretHygieneTest(HandoffTestBase):
    def test_payload_contains_no_key_or_webhook(self):
        p = payload()
        text = json.dumps(p, ensure_ascii=False)
        self.assertNotIn(KEY_LIKE, text)
        self.assertNotIn("discord", text.lower())
        self.assertNotIn("bearer", text.lower())
        self.assertEqual(set(p), {"schema_version", "generated_at", "api_day_utc", "source",
                                  "format_verified", "status", "matches"})

    def test_build_payload_has_no_key_parameter(self):
        import inspect
        self.assertNotIn("api_key", inspect.signature(H.build_payload).parameters)

    def test_embed_text_contains_no_key(self):
        self.write(payload())
        embeds, _, _ = self.collect()
        self.assertNotIn(KEY_LIKE, json.dumps(embeds, ensure_ascii=False))


class ExistingDeliveryNonInterferenceTest(unittest.TestCase):
    """既存 badminton_alerts の配信処理が、ハンドオフ追加の前後で同じ送信・同じ記録を保つこと（ソースで検証）。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "scripts" / "fetch_badminton_alerts.py").read_text(encoding="utf-8")
        cls.wf = (ROOT / ".github" / "workflows" / "badminton_alerts.yml").read_text(encoding="utf-8")

    def test_existing_send_and_state_calls_are_preserved(self):
        for needle in ("build_badminton_embeds(new_matches, now)",
                       "build_national_summary_embeds(new_summaries, now)",
                       "build_badspi_embeds(new_badspi_articles, wr_rankings)",
                       "build_google_news_badminton_embeds(new_gnews_articles, wr_rankings)",
                       "build_nba_result_embeds(new_nba_results)",
                       "save_seen_state(state)",
                       "def send_embeds_to_discord("):
            self.assertGreaterEqual(self.src.count(needle), 1, needle)

    def test_handoff_failure_cannot_stop_existing_delivery(self):
        self.assertIn("except Exception as err:  # noqa: BLE001 - 新機能の障害で既存配信を止めない", self.src)
        self.assertIn("intl_marks = []", self.src)

    def test_marks_applied_only_after_successful_send(self):
        self.assertIn("if send_embeds_to_discord(webhook, embeds):\n                BH.apply_marks(", self.src)

    def test_handoff_only_in_morning_band(self):
        self.assertIn('if band == "morning":', self.src)

    def test_workflow_keeps_original_schedule_and_webhook(self):
        self.assertIn('- cron: "5 21 * * *"', self.wf)
        self.assertIn("DISCORD_WEBHOOK_OSHI: ${{ secrets.WEBHOOK_OSHI }}", self.wf)
        self.assertIn("run: python scripts/fetch_badminton_alerts.py", self.wf)

    def test_handoff_step_is_optional_and_has_no_api_key(self):
        step = self.wf[self.wf.index("試合結果ハンドオフを取得"):self.wf.index("指定選手の試合結果を取得")]
        self.assertIn("continue-on-error: true", step)
        self.assertNotIn("BADMINTON_API_KEY", self.wf)
        self.assertNotIn("secrets.BADMINTON", self.wf)

    def test_concurrency_group_prevents_parallel_runs(self):
        self.assertIn("group: badminton-alerts", self.wf)
        self.assertIn("cancel-in-progress: false", self.wf)


class PublishScriptTest(unittest.TestCase):
    """Mac側の公開スクリプト。ローカルの bare リポジトリに対してのみ動かす（外部通信なし）。"""

    SCRIPT = ROOT / "scripts" / "publish_badminton_handoff.sh"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bare = self.tmp / "remote.git"
        self.src_repo = self.tmp / "seed"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        self.src_repo.mkdir()
        subprocess.run(["git", "-C", str(self.src_repo), "init", "-q"], check=True)
        (self.src_repo / "README.md").write_text("main\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.src_repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.src_repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "-m", "main"], check=True)
        subprocess.run(["git", "-C", str(self.src_repo), "push", "-q", str(self.bare), "HEAD:refs/heads/main"], check=True)
        self.key = self.tmp / "deploy_key"
        self.key.write_text("not a real key\n", encoding="utf-8")
        self.outbox = self.tmp / "h.json"
        H.write_payload_atomic(self.outbox, payload())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, *args, key=True, src=None):
        env = dict(os.environ, BADMINTON_DATA_REMOTE=str(self.bare))
        env.pop("BADMINTON_DATA_DEPLOY_KEY", None)
        if key:
            env["BADMINTON_DATA_DEPLOY_KEY"] = str(self.key)
        return subprocess.run(["bash", str(self.SCRIPT), str(src or self.outbox)],
                              env=env, capture_output=True, text=True)

    def branch_names(self):
        out = subprocess.run(["git", "-C", str(self.bare), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
                             capture_output=True, text=True, check=True)
        return set(out.stdout.split())

    def test_without_key_does_nothing(self):
        r = self.run_script(key=False)
        self.assertEqual(r.returncode, 0)
        self.assertIn("未設定のため公開しません", r.stdout)
        self.assertEqual(self.branch_names(), {"main"})

    def test_publishes_to_data_branch_only_and_leaves_main_untouched(self):
        main_before = subprocess.run(["git", "-C", str(self.bare), "rev-parse", "refs/heads/main"],
                                     capture_output=True, text=True, check=True).stdout
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.branch_names(), {"main", "badminton-results-data"})
        main_after = subprocess.run(["git", "-C", str(self.bare), "rev-parse", "refs/heads/main"],
                                    capture_output=True, text=True, check=True).stdout
        self.assertEqual(main_before, main_after)
        shown = subprocess.run(["git", "-C", str(self.bare), "show", "badminton-results-data:badminton_results_handoff.json"],
                               capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(shown)["schema_version"], 1)

    def test_unchanged_content_is_not_pushed_again(self):
        self.run_script()
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertIn("変化がないため", r.stdout)

    def test_corrupt_json_is_refused(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        r = self.run_script(src=bad)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("badminton-results-data", self.branch_names())

    def test_script_has_no_force_push_and_never_prints_key_path(self):
        text = self.SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("--force", text)
        self.assertNotIn(" -f ", text)
        self.assertIn("refs/heads/${BRANCH}", text)
        r = self.run_script()
        self.assertNotIn(str(self.key), r.stdout + r.stderr)


class WorkflowConcurrencyStaticTest(unittest.TestCase):
    def test_test_workflow_has_no_scheduled_or_secret_use(self):
        wf = (ROOT / ".github" / "workflows" / "badminton_python_tests.yml").read_text(encoding="utf-8")
        self.assertNotIn("schedule:", wf)
        self.assertNotIn("secrets.", wf)
        self.assertIn("tests.test_badminton_handoff", wf)


if __name__ == "__main__":
    unittest.main()
