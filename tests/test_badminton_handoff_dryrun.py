# -*- coding: utf-8 -*-
"""
既存配信（fetch_badminton_alerts.main）を通した統合ドライラン。
- 外部取得はすべて空に差し替え、通信は遮断（offline_guard）。
- Discord 送信は関数を差し替えて「何が送られたか」だけを記録する（実送信なし）。
- 既存の送信内容が維持され、ハンドオフの追加・失敗・重複が既存配信を壊さないことを確認する。
"""
import contextlib
import datetime as dt
import io
import json
import os
import shutil
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

try:
    import fetch_badminton_alerts as fb  # noqa: E402
except ImportError as err:  # 既存配信の依存（feedparser・bs4・google-genai）が無い環境では実行しない
    fb = None
    _IMPORT_ERROR = str(err)

UTC = dt.timezone.utc
JST = dt.timezone(dt.timedelta(hours=9))


def _match(mid="m-1", api_day=None):
    day = api_day or H.expected_api_day_utc(dt.datetime.now(UTC))   # 取得窓（UTC前日）の中の試合にする
    return {"match_id": mid, "tournament": "BWF Dry Run Open", "discipline": "MS",
            "side_a": [{"name": "Dry Run Player"}], "side_b": [{"name": "Dry Run Opponent"}],
            "games": [[21, 12], [21, 19]], "winner_side": "a", "status": "finished",
            "start_utc": f"{day}T03:00:00+00:00"}


@unittest.skipIf(fb is None, "既存配信の依存が無い環境では統合ドライランを省略（CIでは依存を導入する）")
class IntegratedDryRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.handoff = self.tmp / "handoff.json"
        self.now = dt.datetime.now(UTC).replace(microsecond=0)
        self.sent = []          # 送信されたメッセージ（embeds のリスト）
        self.saved_state = None
        self.send_ok = True

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_payload(self, matches=None, **over):
        p = H.build_payload(generated_at_utc=self.now - dt.timedelta(hours=1),
                            api_day_utc=H.expected_api_day_utc(self.now), format_verified=True,
                            status="ok", matches=[_match()] if matches is None else matches)
        p.update(over)
        self.handoff.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")

    def run_main(self, band="morning", state=None):
        self.sent.clear()
        state = {} if state is None else state
        patches = [
            mock.patch.object(fb, "fetch_tournament_article_urls", return_value=[]),
            mock.patch.object(fb, "fetch_badspi_articles", return_value=[]),
            mock.patch.object(fb, "fetch_google_news_badminton", return_value=[]),
            mock.patch.object(fb, "fetch_nba_result_ids", return_value=[]),
            mock.patch.object(fb, "fetch_wr_rankings_detailed", return_value=({}, {}, {})),
            mock.patch.object(fb, "is_weekly_ranking_check_time", return_value=False),
            mock.patch.object(fb, "get_time_band", return_value=band),
            mock.patch.object(fb, "load_seen_state", return_value=state),
            mock.patch.object(fb, "save_seen_state", side_effect=self._save),
            mock.patch.object(fb, "build_badspi_embeds", return_value=[{"title": "既存バド×スピ", "description": "d", "color": 1}]),
            mock.patch.object(fb.tournament_master, "TournamentMaster",
                              return_value=mock.Mock(dirty=False, errors=[])),
            mock.patch.object(fb, "send_embeds_to_discord", side_effect=self._send),
        ]
        env = {"DISCORD_WEBHOOK_OSHI": "https://example.invalid/not-used",
               "BADMINTON_HANDOFF_PATH": str(self.handoff)}
        exit_code = 0
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()) as out:
            for p in patches:
                p.start()
            try:
                fb.main()
            except SystemExit as e:
                exit_code = e.code
            finally:
                for p in patches:
                    p.stop()
        self.stdout = out.getvalue()
        return exit_code

    def _send(self, webhook, embeds, batch_size=10):
        self.sent.append(list(embeds))
        return self.send_ok

    def _save(self, state):
        self.saved_state = json.loads(json.dumps(state, default=str))

    def all_titles(self):
        return [e["title"] for batch in self.sent for e in batch]

    def test_valid_handoff_is_added_to_existing_morning_message_once(self):
        self.write_payload()
        state = {}
        self.assertEqual(self.run_main(state=state), 0)
        self.assertEqual(len(self.sent), 1, "1回の配信（1メッセージ）に既存と新規をまとめる")
        titles = self.all_titles()
        self.assertIn("既存バド×スピ", titles)                      # 既存機能は維持
        self.assertTrue(any("BWF Dry Run Open" in t for t in titles))  # 新規結果が追加
        self.assertIn("intl::m-1", self.saved_state["intl_results_seen"])

        # 同じデータで再実行しても、同一試合は二重投稿されない
        self.sent.clear()
        self.run_main(state=self.saved_state)
        self.assertFalse(any("BWF Dry Run Open" in t for t in self.all_titles()))

    def test_players_covered_by_existing_articles_are_not_sent_twice(self):
        # 指定選手（既存の大会記事が担当）を含む試合は除外し、それ以外の試合だけを追加する
        covered = dict(_match("covered"), side_a=[{"name": "渡辺勇大"}])
        self.write_payload([covered, _match("other")])
        self.run_main()
        titles = self.all_titles()
        self.assertEqual(sum("BWF Dry Run Open" in t for t in titles), 1)
        self.assertIn("intl::other", self.saved_state["intl_results_seen"])
        self.assertNotIn("intl::covered", self.saved_state["intl_results_seen"])

    def test_missing_handoff_keeps_existing_delivery_unchanged(self):
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.all_titles(), ["既存バド×スピ"])
        self.assertIn("ハンドオフは使用しません（missing）", self.stdout)

    def test_corrupt_handoff_never_breaks_existing_delivery(self):
        self.handoff.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.all_titles(), ["既存バド×スピ"])

    def test_stale_or_unverified_handoff_is_ignored(self):
        p = H.build_payload(generated_at_utc=self.now - dt.timedelta(days=3),
                            api_day_utc=H.expected_api_day_utc(self.now), format_verified=True,
                            status="ok", matches=[_match()])
        self.handoff.write_text(json.dumps(p), encoding="utf-8")
        self.run_main()
        self.assertFalse(any("BWF Dry Run Open" in t for t in self.all_titles()))
        self.write_payload(format_verified=False)
        self.run_main()
        self.assertFalse(any("BWF Dry Run Open" in t for t in self.all_titles()))

    def test_night_band_never_sends_handoff(self):
        self.write_payload()
        self.run_main(band="night")
        self.assertEqual(self.sent, [])

    def test_failed_send_does_not_mark_handoff_as_sent_and_retries(self):
        self.write_payload()
        self.send_ok = False
        state = {}
        self.assertEqual(self.run_main(state=state), 1)   # 既存の失敗時と同じ終了コード
        self.assertNotIn("intl::m-1", self.saved_state.get("intl_results_seen", {}))
        self.send_ok = True
        self.sent.clear()
        self.run_main(state=self.saved_state)
        self.assertTrue(any("BWF Dry Run Open" in t for t in self.all_titles()))

    def test_exception_in_handoff_path_is_isolated(self):
        self.write_payload()
        with mock.patch.object(fb.BH, "collect_embeds", side_effect=RuntimeError("boom")):
            self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.all_titles(), ["既存バド×スピ"])
        self.assertIn("既存配信は継続", self.stdout)

    def test_no_discord_request_is_made_by_handoff_module_itself(self):
        # 送信は既存の send_embeds_to_discord のみ（ハンドオフ側には HTTP 呼び出しが無い）
        src = (ROOT / "scripts" / "badminton_handoff.py").read_text(encoding="utf-8")
        self.assertNotIn("requests", src)
        self.assertNotIn("urllib", src)
        self.assertNotIn("webhook", src.lower())


class HandoffModuleHasNoNetworkTest(unittest.TestCase):
    def test_module_is_stdlib_only(self):
        src = (ROOT / "scripts" / "badminton_handoff.py").read_text(encoding="utf-8")
        for banned in ("import requests", "import feedparser", "from bs4", "import socket"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
