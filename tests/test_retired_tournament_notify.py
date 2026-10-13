"""地方バドミントン大会の自動通知の廃止(2026-10-13)を検証する。通信なし・Gemini/Discord 非接続。
実行: python3 -m unittest tests.test_retired_tournament_notify
確認内容:
  - 大会通知の自動起動(ワークフロー・スケジュール・スクリプト)が無いこと
  - 専用の状態ファイルは作業中の state/ から外れ、記録として archive/ に残っていること
  - 共有機能(Gemini ガード・アラートの 503/429 方針・120分ガード・WEBHOOK_LOCAL を使う他の通知)が残っていること
"""
import ast
import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
TOURNAMENT_NAMES = ("local_badminton_tournaments", "fetch_local_badminton_tournaments", "local_badminton_seen",
                    "local_badminton_last_run", "local_badminton_koho_cache")


class TournamentNotifyRemovedTest(unittest.TestCase):
    def test_no_workflow_runs_the_tournament_script(self):
        for wf in WORKFLOWS:
            text = wf.read_text(encoding="utf-8")
            self.assertNotIn("fetch_local_badminton_tournaments", text, wf.name)

    def test_no_tournament_workflow_or_schedule_file(self):
        self.assertFalse((ROOT / ".github" / "workflows" / "local_badminton_tournaments.yml").exists())
        for wf in WORKFLOWS:
            self.assertNotIn("local_badminton", wf.name, wf.name)

    def test_tournament_script_is_removed(self):
        self.assertFalse((ROOT / "scripts" / "fetch_local_badminton_tournaments.py").exists())

    def test_no_active_state_or_cache_for_tournaments(self):
        active = [p.name for p in (ROOT / "state").glob("*") if any(n in p.name for n in TOURNAMENT_NAMES)]
        self.assertEqual(active, [])

    def test_dispatcher_does_not_register_tournament_workflow(self):
        import dispatch_workflows as dw
        names = [name for name, _rule in dw.WORKFLOWS]
        self.assertFalse([n for n in names if "local_badminton" in n])


class ArchivePreservedTest(unittest.TestCase):
    ARCHIVE = ROOT / "archive" / "retired_local_badminton_tournaments"

    def test_records_are_kept_in_archive(self):
        for name in ("local_badminton_seen.json", "local_badminton_last_run.json", "local_badminton_koho_cache.json"):
            self.assertTrue((self.ARCHIVE / name).exists(), name)

    def test_seen_record_is_valid_and_has_history(self):
        data = json.loads((self.ARCHIVE / "local_badminton_seen.json").read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("notified"), dict)
        self.assertTrue(data["notified"])

    def test_readme_explains_retirement(self):
        text = (self.ARCHIVE / "README.md").read_text(encoding="utf-8")
        self.assertIn("廃止", text)
        self.assertIn("2026-10-13", text)


class SharedFeaturesKeptTest(unittest.TestCase):
    def test_shared_gemini_guard_api_is_present(self):
        import gemini_guard as gg
        for name in ("Guard", "classify", "GeminiBlocked", "GeminiCallError", "GeminiCancelled", "BASE_DELAY_SECONDS"):
            self.assertTrue(hasattr(gg, name), name)
        self.assertEqual(gg.Guard({}, base_delay=1.5).base_delay, 1.5)

    def test_other_scripts_still_use_the_shared_guard(self):
        users = [p.name for p in (ROOT / "scripts").glob("*.py") if re.search(r"^\s*(import|from)\s+gemini_guard", p.read_text(encoding="utf-8"), re.M)]
        # 実際の利用箇所(import 行あり)。大会通知の廃止で変わらないことを確認する
        for name in ("fetch_news.py", "fetch_culture_news.py",
                     "fetch_market_intelligence.py", "weekend_market_digest.py"):
            self.assertIn(name, users)

    def test_alert_script_and_workflow_are_kept(self):
        self.assertTrue((ROOT / "scripts" / "fetch_badminton_alerts.py").exists())
        self.assertIn("fetch_badminton_alerts.py", (ROOT / ".github" / "workflows" / "badminton_alerts.yml").read_text(encoding="utf-8"))

    def test_dispatcher_120_minute_guard_and_display_are_kept(self):
        import dispatch_workflows as dw
        import datetime as dt
        now = dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc)
        created = (now - dt.timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        go, reason = dw.decide({"status": "completed", "createdAt": created, "event": "schedule"}, 120, now)
        self.assertFalse(go)
        self.assertIn("次回実行まで約90分", reason)
        self.assertEqual(dict(dw.WORKFLOWS)["news.yml"], 120)

    def test_shared_webhook_secret_still_used_by_other_notifications(self):
        users = [wf.name for wf in WORKFLOWS if "WEBHOOK_LOCAL" in wf.read_text(encoding="utf-8")]
        self.assertTrue(users)
        self.assertIn("news.yml", users)
        self.assertIn("jma_alerts.yml", users)


class SyntaxSanityTest(unittest.TestCase):
    def test_remaining_scripts_parse_for_py311(self):
        for p in (ROOT / "scripts").glob("*.py"):
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p), feature_version=(3, 11))


if __name__ == "__main__":
    unittest.main()
