# -*- coding: utf-8 -*-
"""scripts/badminton_results_free.py の単体テスト（ネットワーク・APIキー・Discord 不要）。

注意：item の構造は文書からの想定であり、実API（キー取得後）では未検証。
本テストは、想定外の形式を「取得失敗」「欠損」「UNKNOWN」として扱うことを確認する。
"""
import copy
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import badminton_results_free as m  # noqa: E402

UTC = dt.timezone.utc
RUN_1 = dt.datetime(2026, 11, 5, 21, 5, tzinfo=UTC)   # 2026-11-06 06:05 JST
RUN_2 = dt.datetime(2026, 11, 6, 21, 5, tzinfo=UTC)   # 2026-11-07 06:05 JST

JP_ROSTER = {
    "pl_jp1": {"name": "日本A", "status": m.JP_CONFIRMED},
    "pl_jp2": {"name": "日本B", "status": m.JP_CONFIRMED},
    "pl_non": {"name": "外国C", "status": m.NON_JP_CONFIRMED},
}


def _player(pid, name=None):
    return {"id": pid, "name": name or pid}


def _item(mid="ev_1", discipline="MD", s1=("pl_a", "pl_b"), s2=("pl_c", "pl_d"),
          games=((21, 16), (21, 15)), winner=1):
    return {
        "id": mid,
        "tournament": {"id": "tr_1", "name": "Test Open", "year": 2026},
        "discipline": discipline,
        "side1": {"players": [_player(p) for p in s1]},
        "side2": {"players": [_player(p) for p in s2]},
        "games": [list(g) for g in games],
        "winner": winner,
        "status": "finished",
    }


def _fake_fetch(pages):
    """pages: [payload, ...]。cursor が None なら1ページ目、以降は順に返す。"""
    calls = []

    def fetch(api_key, cursor):
        calls.append(cursor)
        idx = 0 if cursor is None else int(cursor.split("-")[1])
        return 200, pages[idx]

    fetch.calls = calls
    return fetch


class FakePost:
    def __init__(self, status=204):
        self.status = status
        self.payloads = []

    def __call__(self, url, json=None, timeout=None):
        self.payloads.append(json)
        return SimpleNamespace(status_code=self.status)

    def embed_ids(self):
        out = []
        for p in self.payloads:
            for e in p.get("embeds", []):
                if "footer" in e:
                    out.append(e["footer"]["text"].replace("試合ID ", ""))
        return out


def _cfg(**over):
    cfg = dict(m.DEFAULT_CONFIG)
    cfg["discord_output_enabled"] = False
    cfg.update(over)
    return cfg


class WindowTest(unittest.TestCase):
    def test_0600_jst_run_covers_jst_two_days_back_09_to_yesterday_0859(self):
        w = m.compute_window(RUN_1)
        self.assertEqual(w["api_day_utc"], "2026-11-04")
        self.assertEqual(w["jst_range"], ["2026-11-04 09:00", "2026-11-05 08:59"])

    def test_consecutive_runs_are_contiguous(self):
        w1 = m.compute_window(RUN_1)
        w2 = m.compute_window(RUN_2)
        self.assertEqual(w1["jst_range"][1], "2026-11-05 08:59")
        self.assertEqual(w2["jst_range"][0], "2026-11-05 09:00")
        self.assertEqual(w1["api_day_utc"], "2026-11-04")
        self.assertEqual(w2["api_day_utc"], "2026-11-05")


class ClassifyAndPagingTest(unittest.TestCase):
    def test_http_errors_are_error(self):
        self.assertEqual(m.classify_response(500, None)[0], "error")
        status, reason = m.classify_response(401, None)
        self.assertEqual(status, "error")
        self.assertIn("キー不正", reason)

    def test_bad_shape_is_error_not_empty(self):
        self.assertEqual(m.classify_response(200, {"meta": {}})[0], "error")
        self.assertEqual(m.classify_response(200, ["x"])[0], "error")

    def test_empty_list_is_empty_not_error(self):
        self.assertEqual(m.classify_response(200, {"data": []}), ("empty", []))

    def test_follows_next_cursor_across_pages(self):
        pages = [
            {"data": [_item("ev_1")], "meta": {"next_cursor": "c-1"}},
            {"data": [_item("ev_2")], "meta": {}},
        ]
        fetch = _fake_fetch(pages)
        status, items, reason, calls = m.fetch_all_pages("k", fetch, _cfg(), sleep=lambda s: None)
        self.assertEqual(status, "ok")
        self.assertEqual([i["id"] for i in items], ["ev_1", "ev_2"])
        self.assertEqual(calls, 2)
        self.assertEqual(fetch.calls, [None, "c-1"])

    def test_page_limit_reached_is_error_not_partial_ok(self):
        pages = [{"data": [_item("ev_x")], "meta": {"next_cursor": "c-1"}}] * 3
        status, items, reason, _ = m.fetch_all_pages("k", _fake_fetch(pages), _cfg(max_api_pages=2),
                                                     sleep=lambda s: None)
        self.assertEqual(status, "error")
        self.assertIn("不完全", reason)
        self.assertEqual(items, [])

    def test_error_on_second_page_discards_partial_items(self):
        def fetch(api_key, cursor):
            if cursor is None:
                return 200, {"data": [_item("ev_1")], "meta": {"next_cursor": "c-1"}}
            return 500, None
        status, items, reason, _ = m.fetch_all_pages("k", fetch, _cfg(), sleep=lambda s: None)
        self.assertEqual(status, "error")
        self.assertEqual(items, [])

    def test_sleep_between_pages_respects_rate_limit(self):
        waits = []
        pages = [{"data": [_item("ev_1")], "meta": {"next_cursor": "c-1"}},
                 {"data": [_item("ev_2")], "meta": {}}]
        m.fetch_all_pages("k", _fake_fetch(pages), _cfg(api_min_interval_sec=6.0), sleep=waits.append)
        self.assertEqual(waits, [6.0])


class NormalizeTest(unittest.TestCase):
    def test_complete_item_is_normalized(self):
        n = m.normalize_match(_item())
        self.assertEqual(n["discipline"], "男子ダブルス")
        self.assertEqual(n["games"], [[21, 16], [21, 15]])
        self.assertEqual(n["winner"], 1)

    def test_missing_side_players_returns_none(self):
        self.assertIsNone(m.normalize_match({**_item(), "side2": {"players": []}}))

    def test_unknown_discipline_returns_none(self):
        self.assertIsNone(m.normalize_match(_item(discipline="XX")))

    def test_bad_games_become_none_not_guessed(self):
        n = m.normalize_match({**_item(), "games": [[21, "16"]]})
        self.assertIsNone(n["games"])

    def test_invalid_winner_stays_none(self):
        self.assertIsNone(m.normalize_match(_item(winner=3))["winner"])


class ThreeValueJudgementTest(unittest.TestCase):
    def _judge(self, s1, s2, roster=JP_ROSTER):
        entry = m.build_record(m.normalize_match(_item(s1=s1, s2=s2)), "2026-11-04")
        return m.classify_match(entry, roster)

    def test_registered_japanese_is_jp(self):
        self.assertEqual(self._judge(("pl_jp1", "pl_x"), ("pl_y", "pl_z")), "jp")

    def test_all_registered_non_japanese_is_non_jp(self):
        self.assertEqual(self._judge(("pl_non", "pl_non"), ("pl_non", "pl_non")), "non_jp")

    def test_unregistered_is_unknown_not_non_japanese(self):
        self.assertEqual(self._judge(("pl_x", "pl_y"), ("pl_z", "pl_w")), "unknown")

    def test_doubles_one_confirmed_one_unknown_is_jp_with_factual_label(self):
        entry = m.build_record(m.normalize_match(_item(s1=("pl_jp1", "pl_x"), s2=("pl_y", "pl_z"))),
                               "2026-11-04")
        self.assertEqual(m.classify_match(entry, JP_ROSTER), "jp")
        embed = m.build_embed(entry, JP_ROSTER, m.compute_window(RUN_1))
        self.assertIn("日本人確認1名", embed["description"])
        self.assertIn("判定不能3名", embed["description"])

    def test_empty_roster_makes_everything_unknown(self):
        self.assertEqual(self._judge(("pl_jp1", "pl_x"), ("pl_y", "pl_z"), roster={}), "unknown")

    def test_roster_loader_ignores_invalid_status_and_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "roster.json"
            p.write_text(json.dumps({"players": [
                {"player_id": "pl_ok", "name": "A", "status": "JP_CONFIRMED"},
                {"player_id": "pl_bad", "name": "B", "status": "JP?"},
                {"name": "no id", "status": "JP_CONFIRMED"},
            ]}), encoding="utf-8")
            roster = m.load_roster(p)
            self.assertEqual(set(roster), {"pl_ok"})
            self.assertEqual(m.load_roster(Path(d) / "missing.json"), {})


class RecordTest(unittest.TestCase):
    def test_hash_changes_when_result_changes(self):
        a = m.build_record(m.normalize_match(_item()), "2026-11-04")
        b = m.build_record(m.normalize_match(_item(games=((21, 10), (21, 10)))), "2026-11-04")
        self.assertNotEqual(a["content_hash"], b["content_hash"])

    def test_hash_ignores_display_name_change(self):
        a = m.build_record(m.normalize_match(_item()), "2026-11-04")
        renamed = copy.deepcopy(_item())
        renamed["side1"]["players"][0]["name"] = "表記変更"
        b = m.build_record(m.normalize_match(renamed), "2026-11-04")
        self.assertEqual(a["content_hash"], b["content_hash"])


class RunTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self._tmp.name) / "state" / "results.json"

    def tearDown(self):
        self._tmp.cleanup()

    def run_once(self, pages, *, now=RUN_1, cfg=None, roster=JP_ROSTER, post=None, key="k", webhook="hook"):
        post = post or FakePost()
        rc = m.run_once(api_key=key, webhook=webhook, now_utc=now, state_path=self.state_path,
                        config=cfg or _cfg(), roster=roster, fetch=_fake_fetch(pages), post=post,
                        sleep=lambda s: None)
        return rc, post

    def load(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))


class DiscordSwitchTest(RunTestBase):
    PAGE = {"data": [_item("ev_jp", s1=("pl_jp1", "pl_x"), s2=("pl_y", "pl_z"))], "meta": {}}

    def test_without_api_key_nothing_is_done(self):
        rc, post = self.run_once([self.PAGE], key="")
        self.assertEqual(rc, 0)
        self.assertEqual(post.payloads, [])
        self.assertFalse(self.state_path.exists())

    def test_discord_off_stores_but_never_marks_notified(self):
        rc, post = self.run_once([self.PAGE], cfg=_cfg(discord_output_enabled=False))
        self.assertEqual(rc, 0)
        self.assertEqual(post.payloads, [], "OFF中は一切送信しない")
        entry = self.load()["matches"]["ev_jp"]
        self.assertIsNotNone(entry["persisted_at"])
        self.assertIsNone(entry["notified_at"], "OFF中は通知済みにしない")

    def test_switching_on_notifies_unsent_backlog_once(self):
        self.run_once([self.PAGE], cfg=_cfg(discord_output_enabled=False))
        rc, post = self.run_once([{"data": [], "meta": {}}], now=RUN_2,
                                 cfg=_cfg(discord_output_enabled=True))
        self.assertEqual(rc, 0)
        self.assertEqual(post.embed_ids(), ["ev_jp"], "OFF中に保存した試合をONへ切替後に通知")
        self.assertIsNotNone(self.load()["matches"]["ev_jp"]["notified_at"])

    def test_notified_match_is_not_resent_on_next_run(self):
        cfg = _cfg(discord_output_enabled=True)
        self.run_once([self.PAGE], cfg=cfg)
        rc, post = self.run_once([self.PAGE], now=RUN_2, cfg=cfg)
        self.assertEqual(post.embed_ids(), [])

    def test_unknown_only_match_is_stored_but_not_notified(self):
        page = {"data": [_item("ev_unk", s1=("pl_x", "pl_y"), s2=("pl_z", "pl_w"))], "meta": {}}
        _, post = self.run_once([page], cfg=_cfg(discord_output_enabled=True))
        self.assertNotIn("ev_unk", post.embed_ids())
        self.assertIn("ev_unk", self.load()["matches"])

    def test_on_without_webhook_is_error_and_sends_nothing(self):
        rc, post = self.run_once([self.PAGE], cfg=_cfg(discord_output_enabled=True), webhook="")
        self.assertEqual(rc, 1)
        self.assertEqual(post.payloads, [])

    def test_on_with_empty_roster_posts_header_about_unjudged_state(self):
        _, post = self.run_once([self.PAGE], cfg=_cfg(discord_output_enabled=True), roster={})
        text = json.dumps(post.payloads, ensure_ascii=False)
        self.assertIn("日本人の判定は行っていません", text)
        self.assertEqual(post.embed_ids(), [])


class FailureAndDuplicateTest(RunTestBase):
    def test_fetch_error_changes_no_match_and_returns_1(self):
        ok_page = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}
        self.run_once([ok_page], cfg=_cfg(discord_output_enabled=False))
        before = self.load()["matches"]

        def failing(api_key, cursor):
            return 500, None
        post = FakePost()
        rc = m.run_once(api_key="k", webhook="hook", now_utc=RUN_2, state_path=self.state_path,
                        config=_cfg(discord_output_enabled=False), roster=JP_ROSTER, fetch=failing,
                        post=post, sleep=lambda s: None)
        self.assertEqual(rc, 1)
        self.assertEqual(self.load()["matches"], before)
        self.assertEqual(post.payloads, [], "Discord OFFなら失敗通知も送らない")
        self.assertEqual(self.load()["runs"][-1]["status"], "error")

    def test_fetch_error_with_discord_on_sends_failure_header_not_empty_header(self):
        def failing(api_key, cursor):
            return 500, None
        post = FakePost()
        m.run_once(api_key="k", webhook="hook", now_utc=RUN_1, state_path=self.state_path,
                   config=_cfg(discord_output_enabled=True), roster=JP_ROSTER, fetch=failing,
                   post=post, sleep=lambda s: None)
        text = json.dumps(post.payloads, ensure_ascii=False)
        self.assertIn("取得失敗", text)
        self.assertNotIn("対象試合なし", text)

    def test_zero_results_is_empty_not_error(self):
        _, post = self.run_once([{"data": [], "meta": {}}], cfg=_cfg(discord_output_enabled=True))
        text = json.dumps(post.payloads, ensure_ascii=False)
        self.assertIn("対象試合なし", text)
        self.assertNotIn("取得失敗", text)

    def test_duplicate_match_in_next_run_is_stored_once(self):
        page = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}
        self.run_once([page], cfg=_cfg())
        self.run_once([page], now=RUN_2, cfg=_cfg())
        self.assertEqual(list(self.load()["matches"]), ["ev_1"])
        self.assertEqual(self.load()["matches"]["ev_1"]["versions"], [])

    def test_correction_is_kept_as_new_version_and_not_resent(self):
        cfg = _cfg(discord_output_enabled=True)
        first = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}
        self.run_once([first], cfg=cfg)
        corrected = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"), games=((21, 19), (21, 19)))], "meta": {}}
        _, post = self.run_once([corrected], now=RUN_2, cfg=cfg)
        entry = self.load()["matches"]["ev_1"]
        self.assertTrue(entry["correction_pending"])
        self.assertEqual(len(entry["versions"]), 1)
        self.assertEqual(entry["versions"][0]["games"], [[21, 16], [21, 15]])
        self.assertEqual(entry["games"], [[21, 19], [21, 19]])
        self.assertNotIn("ev_1", post.embed_ids(), "訂正は自動再送しない")

    def test_send_failure_keeps_match_unnotified_then_retries(self):
        cfg = _cfg(discord_output_enabled=True)
        page = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}
        self.run_once([page], cfg=cfg, post=FakePost(status=500))
        entry = self.load()["matches"]["ev_1"]
        self.assertIsNone(entry["notified_at"])
        self.assertIsNotNone(entry["notify_error"])
        _, post = self.run_once([{"data": [], "meta": {}}], now=RUN_2, cfg=cfg, post=FakePost(204))
        self.assertEqual(post.embed_ids(), ["ev_1"])
        self.assertIsNotNone(self.load()["matches"]["ev_1"]["notified_at"])

    def test_batches_are_split_by_ten_embeds(self):
        items = [_item(f"ev_{i}", s1=("pl_jp1", "pl_x")) for i in range(12)]
        _, post = self.run_once([{"data": items, "meta": {}}], cfg=_cfg(discord_output_enabled=True))
        sizes = [len(p["embeds"]) for p in post.payloads]
        self.assertEqual(sizes, [1, 10, 2], "ヘッダー1件、本体は10件ずつ")

    def test_corrupt_state_is_not_overwritten(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{broken", encoding="utf-8")
        rc, post = self.run_once([{"data": [], "meta": {}}])
        self.assertEqual(rc, 1)
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(post.payloads, [])


class RetentionTest(unittest.TestCase):
    def test_old_matches_and_runs_are_pruned(self):
        now = dt.datetime(2026, 11, 6, tzinfo=UTC)
        state = m.empty_state()
        state["matches"] = {
            "old": {"persisted_at": (now - dt.timedelta(days=31)).isoformat()},
            "new": {"persisted_at": (now - dt.timedelta(days=2)).isoformat()},
        }
        state["runs"] = [{"i": i} for i in range(5)]
        removed = m.prune(state, now, retention_days=30, run_keep=3)
        self.assertEqual(removed, 1)
        self.assertEqual(list(state["matches"]), ["new"])
        self.assertEqual(len(state["runs"]), 3)

    def test_backlog_older_than_window_is_not_notified(self):
        now = dt.datetime(2026, 11, 6, tzinfo=UTC)
        entry = m.build_record(m.normalize_match(_item("ev_old", s1=("pl_jp1", "pl_x"))), "2026-11-04")
        entry.update({"persisted_at": (now - dt.timedelta(days=5)).isoformat(), "notified_at": None})
        state = {"matches": {"ev_old": entry}}
        cands, extra = m.pending_notifications(state, JP_ROSTER, now, backlog_days=3, cap=30)
        self.assertEqual(cands, [])


class SaveStateTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            m.save_state(p, {"schema_version": 1, "matches": {"a": {}}, "runs": []})
            self.assertEqual(m.load_state(p)["matches"], {"a": {}})
            self.assertFalse((Path(d) / "s.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
