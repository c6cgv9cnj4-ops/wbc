# -*- coding: utf-8 -*-
"""scripts/badminton_results_free.py の単体テスト（ネットワーク・APIキー・Discord 不要）。

注意：item の構造（start の形式・meta.next_cursor・per_page）は文書からの想定であり、実API（キー取得後）では未検証。
本テストは、想定外の形式を「取得失敗」「欠損」「UNKNOWN」として扱うことと、
通知・保存・日付の状態遷移が仕様どおりであることを確認する。
"""
import contextlib
import copy
import datetime as dt
import io
import json
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
import badminton_results_free as m  # noqa: E402

UTC = dt.timezone.utc
RUN_1 = dt.datetime(2026, 11, 5, 21, 5, tzinfo=UTC)   # 2026-11-06 06:05 JST（api_day 11-04）
RUN_2 = dt.datetime(2026, 11, 6, 21, 5, tzinfo=UTC)   # 2026-11-07 06:05 JST（api_day 11-05）
START_D1 = "2026-11-04T12:00:00+00:00"                 # api_day 11-04 に属する開始時刻
START_D2 = "2026-11-05T12:00:00+00:00"

JP_ROSTER = {
    "pl_jp1": {"name": "日本A", "status": m.JP_CONFIRMED},
    "pl_non": {"name": "外国C", "status": m.NON_JP_CONFIRMED},
}


def _player(pid, name=None):
    return {"id": pid, "name": name or pid}


def _item(mid="ev_1", discipline="MD", s1=("pl_a", "pl_b"), s2=("pl_c", "pl_d"),
          games=((21, 16), (21, 15)), winner=1, start=START_D1):
    item = {
        "id": mid,
        "tournament": {"id": "tr_1", "name": "Test Open", "year": 2026},
        "discipline": discipline,
        "side1": {"players": [_player(p) for p in s1]},
        "side2": {"players": [_player(p) for p in s2]},
        "games": [list(g) for g in games],
        "winner": winner,
        "status": "finished",
    }
    if start is not None:
        item["start"] = start
    return item


def _fake_fetch(pages):
    """pages[i] を i ページ目として返す。cursor は "c-<i>" の形。"""
    calls = []

    def fetch(api_key, cursor):
        calls.append(cursor)
        idx = 0 if cursor is None else int(cursor.split("-")[1])
        return 200, pages[idx]

    fetch.calls = calls
    return fetch


# Discord 送信ロジックの検証を維持するため、このモジュールだけ送信の定数を一時的に有効化する。
# 本番（DISCORD_DELIVERY_ENABLED = False）の封じ込めは tests/test_badminton_handoff.py で別途検証する。
_DISCORD_PATCH = mock.patch.object(m, "DISCORD_DELIVERY_ENABLED", True)


def setUpModule():
    _DISCORD_PATCH.start()


def tearDownModule():
    _DISCORD_PATCH.stop()


class FakePost:
    """Discord 投稿の代わり。raise_for が真の番号の呼び出しで例外を投げ、status で応答を決める。"""

    def __init__(self, status=204, raise_on=(), raise_exc=None, after_call_raise=None):
        self.status = status
        self.payloads = []
        self.raise_on = set(raise_on)
        self.raise_exc = raise_exc or requests.ConnectionError("boom")
        self.after_call_raise = after_call_raise

    def __call__(self, url, json=None, timeout=None):
        n = len(self.payloads)
        self.payloads.append(json)
        if n in self.raise_on:
            raise self.raise_exc
        if self.after_call_raise is not None and n == self.after_call_raise:
            raise SystemExit("process killed after send")
        return SimpleNamespace(status_code=self.status)

    def embed_ids(self):
        out = []
        for p in self.payloads:
            for e in p.get("embeds", []):
                if "footer" in e:
                    out.append(e["footer"]["text"].split(" ")[1])
        return out


def _cfg(**over):
    cfg = dict(m.DEFAULT_CONFIG)
    cfg["discord_output_enabled"] = False
    cfg.update(over)
    return cfg


class WindowAndDriftTest(unittest.TestCase):
    def test_0600_jst_run_covers_jst_two_days_back_09_to_yesterday_0859(self):
        w = m.compute_window(RUN_1)
        self.assertEqual(w["api_day_utc"], "2026-11-04")
        self.assertEqual(w["jst_range"], ["2026-11-04 09:00", "2026-11-05 08:59"])

    def test_consecutive_runs_are_contiguous(self):
        w1, w2 = m.compute_window(RUN_1), m.compute_window(RUN_2)
        self.assertEqual(w1["jst_range"][1], "2026-11-05 08:59")
        self.assertEqual(w2["jst_range"][0], "2026-11-05 09:00")
        self.assertEqual((w1["api_day_utc"], w2["api_day_utc"]), ("2026-11-04", "2026-11-05"))

    def test_scheduled_run_delayed_across_utc_midnight_is_shifted(self):
        late = dt.datetime(2026, 11, 6, 0, 20, tzinfo=UTC)   # 予定 11-05 21:05 UTC の実行が遅延
        d = m.schedule_drift(late, "schedule")
        self.assertTrue(d["shifted"])
        self.assertEqual(d["intended_api_day_utc"], "2026-11-04")

    def test_manual_run_at_same_time_is_not_treated_as_shifted(self):
        late = dt.datetime(2026, 11, 6, 0, 20, tzinfo=UTC)
        self.assertFalse(m.schedule_drift(late, "workflow_dispatch")["shifted"])

    def test_early_scheduled_run_is_not_treated_as_shifted(self):
        early = dt.datetime(2026, 11, 5, 21, 3, tzinfo=UTC)
        self.assertFalse(m.schedule_drift(early, "schedule")["shifted"])

    def test_on_time_scheduled_run_is_not_shifted(self):
        self.assertFalse(m.schedule_drift(RUN_1, "schedule")["shifted"])


class ClassifyAndRetryTest(unittest.TestCase):
    def test_http_401_is_error_with_hint(self):
        status, reason = m.classify_response(401, None)
        self.assertEqual(status, "error")
        self.assertIn("キー不正", reason)

    def test_bad_shape_is_error_not_empty(self):
        self.assertEqual(m.classify_response(200, {"meta": {}})[0], "error")
        self.assertEqual(m.classify_response(200, ["x"])[0], "error")

    def test_empty_list_is_empty_not_error(self):
        self.assertEqual(m.classify_response(200, {"data": []}), ("empty", []))

    def test_follows_next_cursor_across_pages(self):
        pages = [{"data": [_item("ev_1")], "meta": {"next_cursor": "c-1"}},
                 {"data": [_item("ev_2")], "meta": {}}]
        fetch = _fake_fetch(pages)
        status, items, _, calls = m.fetch_all_pages("k", fetch, _cfg(), sleep=lambda s: None)
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
            return 401, None
        status, items, _, _ = m.fetch_all_pages("k", fetch, _cfg(), sleep=lambda s: None)
        self.assertEqual(status, "error")
        self.assertEqual(items, [])

    def test_5xx_is_retried_up_to_three_times_then_succeeds(self):
        responses = [(500, None), (503, None), (200, {"data": [_item("ev_1")], "meta": {}})]
        waits = []

        def fetch(api_key, cursor):
            return responses.pop(0)
        status, items, _, calls = m.fetch_all_pages("k", fetch, _cfg(), sleep=waits.append)
        self.assertEqual(status, "ok")
        self.assertEqual(calls, 3)
        self.assertEqual(waits, [2, 4])

    def test_5xx_exhausts_retries_and_is_error(self):
        waits = []

        def fetch(api_key, cursor):
            return 500, None
        status, items, reason, calls = m.fetch_all_pages("k", fetch, _cfg(), sleep=waits.append)
        self.assertEqual(status, "error")
        self.assertEqual(calls, 4)          # 初回 + 再試行3回
        self.assertEqual(waits, [2, 4, 8])

    def test_401_and_403_are_not_retried(self):
        for code in (401, 403):
            calls = []

            def fetch(api_key, cursor, code=code):
                calls.append(code)
                return code, None
            status, _, _, n = m.fetch_all_pages("k", fetch, _cfg(), sleep=lambda s: None)
            self.assertEqual(status, "error")
            self.assertEqual(n, 1, f"{code} はリトライしない")

    def test_timeout_is_retried_then_succeeds(self):
        seq = [requests.Timeout("t1"), requests.Timeout("t2"), (200, {"data": [_item("ev_1")], "meta": {}})]

        def fetch(api_key, cursor):
            nxt = seq.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        status, items, _, calls = m.fetch_all_pages("k", fetch, _cfg(), sleep=lambda s: None)
        self.assertEqual(status, "ok")
        self.assertEqual(calls, 3)

    def test_persistent_timeout_is_error_after_retries(self):
        def fetch(api_key, cursor):
            raise requests.Timeout("t")
        status, _, reason, calls = m.fetch_all_pages("k", fetch, _cfg(), sleep=lambda s: None)
        self.assertEqual(status, "error")
        self.assertIn("再試行", reason)
        self.assertEqual(calls, 4)

    def test_fetch_page_builds_expected_request(self):
        resp = SimpleNamespace(status_code=200, json=lambda: {"data": []})
        with mock.patch.object(m.requests, "get", return_value=resp) as get:
            status, payload = m.fetch_page("KEY123", "c-9")
        self.assertEqual(status, 200)
        args, kwargs = get.call_args
        resp2 = SimpleNamespace(status_code=200, json=lambda: {"data": []})
        with mock.patch.object(m.requests, "get", return_value=resp2) as get_first:
            m.fetch_page("KEY123", None)
        self.assertEqual(get_first.call_args.kwargs["params"], {}, "初回は無パラメータ（422 unknown_parameter を避ける）")
        self.assertEqual(args[0], "https://api.badmintonapi.com/v1/results/yesterday")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer KEY123"})
        self.assertEqual(kwargs["params"], {"cursor": "c-9"})
        self.assertEqual(kwargs["timeout"], m.REQUEST_TIMEOUT)
        self.assertEqual(m.REQUEST_TIMEOUT, 20)


class NormalizeTest(unittest.TestCase):
    def test_complete_item_is_normalized_with_start(self):
        n = m.normalize_match(_item())
        self.assertEqual(n["discipline"], "男子ダブルス")
        self.assertEqual(n["games"], [[21, 16], [21, 15]])
        self.assertEqual(n["start"], START_D1)

    def test_missing_side_players_returns_none(self):
        self.assertIsNone(m.normalize_match({**_item(), "side2": {"players": []}}))

    def test_unknown_discipline_returns_none(self):
        self.assertIsNone(m.normalize_match(_item(discipline="XX")))

    def test_bad_games_become_none_not_guessed(self):
        self.assertIsNone(m.normalize_match({**_item(), "games": [[21, "16"]]})["games"])

    def test_invalid_winner_stays_none(self):
        self.assertIsNone(m.normalize_match(_item(winner=3))["winner"])


class RosterAndJudgementTest(unittest.TestCase):
    def _judge(self, s1, s2, roster=JP_ROSTER):
        rec = m.build_record(m.normalize_match(_item(s1=s1, s2=s2)), "2026-11-04")
        return m.classify_match(rec, roster)

    def test_registered_japanese_is_jp(self):
        self.assertEqual(self._judge(("pl_jp1", "pl_x"), ("pl_y", "pl_z")), "jp")

    def test_unregistered_is_unknown_not_non_japanese(self):
        self.assertEqual(self._judge(("pl_x", "pl_y"), ("pl_z", "pl_w")), "unknown")

    def test_all_registered_non_japanese_is_non_jp(self):
        self.assertEqual(self._judge(("pl_non", "pl_non"), ("pl_non", "pl_non")), "non_jp")

    def test_doubles_one_confirmed_one_unknown_is_jp_with_factual_count(self):
        rec = m.build_record(m.normalize_match(_item(s1=("pl_jp1", "pl_x"), s2=("pl_y", "pl_z"))), "2026-11-04")
        self.assertEqual(m.classify_match(rec, JP_ROSTER), "jp")
        emb = m.build_embed(rec, JP_ROSTER, m.compute_window(RUN_1))
        self.assertIn("日本人確認1名", emb["description"])
        self.assertIn("判定不能3名", emb["description"])

    def test_empty_roster_makes_everything_unknown(self):
        self.assertEqual(self._judge(("pl_jp1", "pl_x"), ("pl_y", "pl_z"), roster={}), "unknown")

    def test_name_match_alone_does_not_confirm_japanese(self):
        item = _item(s1=("pl_x", "pl_y"))
        item["side1"]["players"][0]["name"] = "日本A"   # 名前が一致しても ID で判定する
        rec = m.build_record(m.normalize_match(item), "2026-11-04")
        self.assertEqual(m.classify_match(rec, JP_ROSTER), "unknown")

    def _write_roster(self, d, players):
        p = Path(d) / "roster.json"
        p.write_text(json.dumps({"players": players}), encoding="utf-8")
        return p

    def test_roster_entry_without_evidence_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write_roster(d, [
                {"player_id": "pl_no_src", "status": "JP_CONFIRMED", "confirmed_by": "A", "confirmed_at": "2026-11-01"},
                {"player_id": "pl_non_no_src", "status": "NON_JP_CONFIRMED"},
            ])
            self.assertEqual(m.load_roster(p), {})

    def test_non_jp_with_full_evidence_is_loaded_and_keeps_basis(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write_roster(d, [{
                "player_id": "pl_non", "name": "外国C", "status": "NON_JP_CONFIRMED",
                "source_url": "https://example.invalid/roster", "confirmed_by": "細川",
                "confirmed_at": "2026-11-06"}])
            entry = m.load_roster(p)["pl_non"]
            self.assertEqual(entry["status"], "NON_JP_CONFIRMED")
            self.assertEqual(entry["source_url"], "https://example.invalid/roster")
            self.assertEqual(entry["confirmed_by"], "細川")

    def test_roster_loader_ignores_invalid_status_and_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write_roster(d, [{"player_id": "pl_bad", "status": "JP?"}])
            self.assertEqual(m.load_roster(p), {})
            self.assertEqual(m.load_roster(Path(d) / "missing.json"), {})

    def test_load_config_requires_boolean_true_for_discord(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({"discord_output_enabled": "true"}), encoding="utf-8")
            self.assertFalse(m.load_config(p)["discord_output_enabled"])
            p.write_text(json.dumps({"discord_output_enabled": True}), encoding="utf-8")
            self.assertTrue(m.load_config(p)["discord_output_enabled"])
            self.assertFalse(m.load_config(Path(d) / "none.json")["discord_output_enabled"])


class DateStatusTest(unittest.TestCase):
    def test_start_on_window_day_is_confirmed(self):
        self.assertEqual(m.date_status_for(START_D1, "2026-11-04"), "confirmed")

    def test_start_on_other_day_is_outside(self):
        self.assertEqual(m.date_status_for(START_D2, "2026-11-04"), "outside")

    def test_missing_or_unreadable_start_is_unknown(self):
        self.assertEqual(m.date_status_for(None, "2026-11-04"), "unknown")
        self.assertEqual(m.date_status_for("not-a-date", "2026-11-04"), "unknown")

    def test_unconfirmed_window_day_stays_unconfirmed(self):
        self.assertEqual(m.date_status_for(START_D1, None), "unconfirmed")


class RunTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self._tmp.name) / "state" / "results.json"

    def tearDown(self):
        self._tmp.cleanup()

    def run_once(self, pages, *, now=RUN_1, cfg=None, roster=JP_ROSTER, post=None, key="k",
                 webhook="hook", event="schedule", fetch=None):
        post = post or FakePost()
        rc = m.run_once(api_key=key, webhook=webhook, now_utc=now, state_path=self.state_path,
                        config=cfg or _cfg(), roster=roster, fetch=fetch or _fake_fetch(pages), post=post,
                        sleep=lambda s: None, event=event)
        return rc, post

    def load(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))


class DiscordSwitchTest(RunTestBase):
    PAGE = {"data": [_item("ev_jp", s1=("pl_jp1", "pl_x"), s2=("pl_y", "pl_z"))], "meta": {}}

    def test_without_api_key_warns_and_changes_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc, post = self.run_once([self.PAGE], key="")
        self.assertEqual(rc, 0)
        self.assertIn("::warning::BADMINTON_API_KEY", buf.getvalue())
        self.assertEqual(post.payloads, [])
        self.assertFalse(self.state_path.exists())

    def test_discord_off_stores_but_never_marks_notified(self):
        rc, post = self.run_once([self.PAGE], cfg=_cfg(discord_output_enabled=False))
        self.assertEqual(rc, 0)
        self.assertEqual(post.payloads, [], "OFF中は一切送信しない")
        entry = self.load()["matches"]["ev_jp"]
        self.assertIsNone(entry["notified_at"])
        self.assertEqual(entry["notify_attempts"], [])

    def test_switching_on_notifies_unsent_backlog_once(self):
        self.run_once([self.PAGE], cfg=_cfg(discord_output_enabled=False))
        rc, post = self.run_once([{"data": [], "meta": {}}], now=RUN_2, cfg=_cfg(discord_output_enabled=True))
        self.assertEqual(rc, 0)
        self.assertEqual(post.embed_ids(), ["ev_jp"])
        entry = self.load()["matches"]["ev_jp"]
        self.assertIsNotNone(entry["notified_at"])
        self.assertEqual([a["result"] for a in entry["notify_attempts"]], ["ok"])

    def test_notified_match_is_not_resent(self):
        cfg = _cfg(discord_output_enabled=True)
        self.run_once([self.PAGE], cfg=cfg)
        _, post = self.run_once([self.PAGE], now=RUN_2, cfg=cfg)
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

    def test_on_with_empty_roster_states_unjudged_in_header(self):
        _, post = self.run_once([self.PAGE], cfg=_cfg(discord_output_enabled=True), roster={})
        text = json.dumps(post.payloads, ensure_ascii=False)
        self.assertIn("日本人の判定は行っていません", text)
        self.assertEqual(post.embed_ids(), [])


class DateBoundaryNotifyTest(RunTestBase):
    def test_shifted_scheduled_run_is_stored_unconfirmed_not_notified_and_reports_gap(self):
        late = dt.datetime(2026, 11, 6, 0, 20, tzinfo=UTC)
        page = {"data": [_item("ev_late", s1=("pl_jp1", "pl_x"), start=START_D1)], "meta": {}}
        _, post = self.run_once([page], now=late, cfg=_cfg(discord_output_enabled=True), event="schedule")
        entry = self.load()["matches"]["ev_late"]
        self.assertEqual(entry["date_status"], "unconfirmed")
        self.assertIsNone(entry["api_day_utc"])
        self.assertEqual(post.embed_ids(), [])
        text = json.dumps(post.payloads, ensure_ascii=False)
        self.assertIn("欠落", text)
        self.assertIn("2026-11-04", text)

    def test_start_outside_window_is_not_notified(self):
        page = {"data": [_item("ev_out", s1=("pl_jp1", "pl_x"), start=START_D2)], "meta": {}}
        _, post = self.run_once([page], cfg=_cfg(discord_output_enabled=True))
        self.assertEqual(self.load()["matches"]["ev_out"]["date_status"], "outside")
        self.assertNotIn("ev_out", post.embed_ids())

    def test_missing_start_is_unknown_not_notified(self):
        page = {"data": [_item("ev_nostart", s1=("pl_jp1", "pl_x"), start=None)], "meta": {}}
        _, post = self.run_once([page], cfg=_cfg(discord_output_enabled=True))
        self.assertEqual(self.load()["matches"]["ev_nostart"]["date_status"], "unknown")
        self.assertNotIn("ev_nostart", post.embed_ids())


class FailureAndDuplicateTest(RunTestBase):
    def test_fetch_error_changes_no_match_and_does_not_prune(self):
        ok_page = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}
        self.run_once([ok_page], cfg=_cfg())
        state = self.load()
        state["matches"]["ev_old"] = {"persisted_at": "2000-01-01T00:00:00+00:00", "id": "ev_old"}
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        before = self.load()["matches"]

        post = FakePost()
        rc = m.run_once(api_key="k", webhook="hook", now_utc=RUN_2, state_path=self.state_path,
                        config=_cfg(), roster=JP_ROSTER, fetch=lambda k, c: (500, None), post=post,
                        sleep=lambda s: None, event="schedule")
        self.assertEqual(rc, 1)
        self.assertEqual(self.load()["matches"], before, "失敗時は既存の試合を一切変更しない（prune も無し）")
        self.assertEqual(post.payloads, [])
        self.assertEqual(self.load()["runs"][-1]["status"], "error")

    def test_fetch_error_with_discord_on_sends_failure_header_not_empty_header(self):
        post = FakePost()
        m.run_once(api_key="k", webhook="hook", now_utc=RUN_1, state_path=self.state_path,
                   config=_cfg(discord_output_enabled=True), roster=JP_ROSTER,
                   fetch=lambda k, c: (500, None), post=post, sleep=lambda s: None, event="schedule")
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

    def test_correction_is_kept_as_version_not_resent_and_flag_can_be_acked(self):
        cfg = _cfg(discord_output_enabled=True)
        first = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}
        self.run_once([first], cfg=cfg)
        corrected = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"), games=((21, 19), (21, 19)))], "meta": {}}
        _, post = self.run_once([corrected], now=RUN_2, cfg=cfg)
        entry = self.load()["matches"]["ev_1"]
        self.assertTrue(entry["correction_pending"])
        self.assertEqual(entry["versions"][0]["games"], [[21, 16], [21, 15]])
        self.assertEqual(entry["games"], [[21, 19], [21, 19]])
        self.assertNotIn("ev_1", post.embed_ids(), "訂正は自動再送しない")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.main(["--ack-correction", "ev_1", "--by", "細川", "--state", str(self.state_path)])
        self.assertEqual(rc, 0)
        acked = self.load()["matches"]["ev_1"]
        self.assertFalse(acked["correction_pending"])
        self.assertEqual(acked["correction_acknowledged"]["by"], "細川")

    def test_ack_requires_by_and_existing_correction(self):
        self.assertEqual(m.main(["--ack-correction", "ev_1", "--state", str(self.state_path)]), 1)
        self.run_once([{"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}], cfg=_cfg())
        self.assertEqual(m.main(["--ack-correction", "ev_1", "--by", "x", "--state", str(self.state_path)]), 1)

    def test_send_exception_is_unknown_and_resent_with_retry_note_and_history(self):
        cfg = _cfg(discord_output_enabled=True)
        page = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}
        # 1回目: ヘッダーは成功、試合の送信で接続例外（結果不明）
        _, post1 = self.run_once([page], cfg=cfg, post=FakePost(raise_on={1}))
        entry = self.load()["matches"]["ev_1"]
        self.assertIsNone(entry["notified_at"])
        self.assertEqual([a["result"] for a in entry["notify_attempts"]], ["unknown"])
        # 2回目: 再送され、前回の結果不明を理由に重複の可能性を明記、試行回数は2
        _, post2 = self.run_once([{"data": [], "meta": {}}], now=RUN_2, cfg=cfg)
        self.assertEqual(post2.embed_ids(), ["ev_1"])
        text = json.dumps(post2.payloads, ensure_ascii=False)
        self.assertIn("重複して届く可能性", text)
        self.assertIn("送信試行 2回目", text)
        entry = self.load()["matches"]["ev_1"]
        self.assertEqual([a["result"] for a in entry["notify_attempts"]], ["unknown", "ok"])
        self.assertIsNotNone(entry["notified_at"])

    def test_crash_after_send_before_final_save_keeps_attempt_and_resends_later(self):
        cfg = _cfg(discord_output_enabled=True)
        page = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}
        with self.assertRaises(SystemExit):
            self.run_once([page], cfg=cfg, post=FakePost(after_call_raise=1))
        # 送信前の attempt 保存により、中断後も試行の事実が残る（result は未確定）
        entry = self.load()["matches"]["ev_1"]
        self.assertIsNone(entry["notified_at"])
        self.assertEqual(entry["notify_attempts"][-1]["result"], None)
        # 次回: 未通知データを自動破棄せず再送し、重複の可能性を記録
        _, post = self.run_once([{"data": [], "meta": {}}], now=RUN_2, cfg=cfg)
        self.assertEqual(post.embed_ids(), ["ev_1"])
        entry = self.load()["matches"]["ev_1"]
        self.assertEqual(entry["notify_attempts"][0]["result"], None)
        self.assertEqual(entry["notify_attempts"][-1]["result"], "ok")

    def test_send_failure_http_keeps_unnotified_then_retries(self):
        cfg = _cfg(discord_output_enabled=True)
        page = {"data": [_item("ev_1", s1=("pl_jp1", "pl_x"))], "meta": {}}
        self.run_once([page], cfg=cfg, post=FakePost(status=500))
        entry = self.load()["matches"]["ev_1"]
        self.assertIsNone(entry["notified_at"])
        self.assertEqual(entry["notify_attempts"][-1]["result"], "failed")
        _, post = self.run_once([{"data": [], "meta": {}}], now=RUN_2, cfg=cfg, post=FakePost(204))
        self.assertEqual(post.embed_ids(), ["ev_1"])

    def test_batches_are_split_by_ten_embeds(self):
        items = [_item(f"ev_{i}", s1=("pl_jp1", "pl_x")) for i in range(12)]
        _, post = self.run_once([{"data": items, "meta": {}}], cfg=_cfg(discord_output_enabled=True))
        self.assertEqual([len(p["embeds"]) for p in post.payloads], [1, 10, 2])

    def test_corrupt_state_is_not_overwritten(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text("{broken", encoding="utf-8")
        rc, post = self.run_once([{"data": [], "meta": {}}])
        self.assertEqual(rc, 1)
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(post.payloads, [])


class RetentionAndQuarantineTest(unittest.TestCase):
    def test_old_matches_and_runs_are_pruned(self):
        now = dt.datetime(2026, 11, 6, tzinfo=UTC)
        state = m.empty_state()
        state["matches"] = {
            "old": {"persisted_at": (now - dt.timedelta(days=31)).isoformat()},
            "edge": {"persisted_at": (now - dt.timedelta(days=30)).isoformat()},
            "new": {"persisted_at": (now - dt.timedelta(days=2)).isoformat()},
        }
        state["runs"] = [{"i": i} for i in range(5)]
        self.assertEqual(m.prune(state, now, retention_days=30, run_keep=3), 1)
        self.assertEqual(sorted(state["matches"]), ["edge", "new"], "ちょうど30日は保持")
        self.assertEqual(len(state["runs"]), 3)

    def test_unreadable_persisted_at_is_quarantined_not_deleted(self):
        now = dt.datetime(2026, 11, 6, tzinfo=UTC)
        state = m.empty_state()
        state["matches"] = {"broken": {"persisted_at": "not-a-date", "id": "broken"}}
        m.prune(state, now, retention_days=30, run_keep=90)
        self.assertNotIn("broken", state["matches"])
        self.assertIn("broken", state["quarantine"])
        self.assertEqual(state["quarantine"]["broken"]["persisted_at"], "not-a-date")

    def test_backlog_older_than_window_is_not_notified(self):
        now = dt.datetime(2026, 11, 6, tzinfo=UTC)
        rec = m.build_record(m.normalize_match(_item("ev_old", s1=("pl_jp1", "pl_x"))), "2026-11-04")
        rec.update({"persisted_at": (now - dt.timedelta(days=5)).isoformat(), "notified_at": None})
        cands, extra = m.pending_notifications({"matches": {"ev_old": rec}}, JP_ROSTER, now,
                                               backlog_days=3, cap=30)
        self.assertEqual(cands, [])


class SaveStateTest(unittest.TestCase):
    def test_save_and_load_roundtrip_leaves_no_tmp_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            m.save_state(p, {"schema_version": 2, "matches": {"a": {}}, "quarantine": {}, "runs": []})
            self.assertEqual(m.load_state(p)["matches"], {"a": {}})
            self.assertFalse((Path(d) / "s.json.tmp").exists())


class StartParsingTest(unittest.TestCase):
    """start は文書上の形式・タイムゾーンが未記載。タイムゾーンが明示されたものだけ日時として扱う。"""

    def test_utc_z_suffix_is_parsed_as_utc(self):
        self.assertEqual(m.parse_start("2026-11-04T12:00:00Z"), dt.datetime(2026, 11, 4, 12, tzinfo=UTC))

    def test_offset_is_converted_to_utc(self):
        # JST 21:00 = UTC 12:00
        self.assertEqual(m.parse_start("2026-11-04T21:00:00+09:00"), dt.datetime(2026, 11, 4, 12, tzinfo=UTC))

    def test_naive_datetime_is_none_not_assumed_utc(self):
        self.assertIsNone(m.parse_start("2026-11-04T12:00:00"))
        self.assertIsNone(m.parse_start("2026-11-04"))

    def test_invalid_or_non_string_is_none(self):
        for bad in ("not-a-date", "", None, 12345, ["2026-11-04T12:00:00Z"]):
            self.assertIsNone(m.parse_start(bad), repr(bad))

    def test_naive_start_makes_match_date_unknown_and_not_notifiable(self):
        norm = m.normalize_match(_item("ev_naive", s1=("pl_jp1", "pl_x"), start="2026-11-04T12:00:00"))
        rec = m.build_record(norm, "2026-11-04")
        self.assertEqual(rec["date_status"], "unknown")
        self.assertFalse(m.is_notifiable(rec, JP_ROSTER))


class AnomalyWarningTest(RunTestBase):
    def test_items_present_but_none_normalizable_warns_and_is_not_empty_wording(self):
        bad = {"data": [{"id": "ev_bad", "discipline": "MD"}], "meta": {}}  # side1/side2 欠損
        buf = io.StringIO()
        post = FakePost()
        with contextlib.redirect_stdout(buf):
            self.run_once([bad], cfg=_cfg(discord_output_enabled=True), post=post)
        self.assertIn("::warning::取得件数はあるが正規化できた試合が0件", buf.getvalue())
        text = json.dumps(post.payloads, ensure_ascii=False)
        self.assertIn("正規化 0件", text)
        self.assertIn("対象試合なしとは扱いません", text)
        self.assertNotIn("対象試合なし（期間内の結果は0件）", text)

    def test_unknown_discipline_is_counted_and_warned(self):
        page = {"data": [_item("ev_x", discipline="ZZ", s1=("pl_jp1", "pl_x"))], "meta": {}}
        buf = io.StringIO()
        post = FakePost()
        with contextlib.redirect_stdout(buf):
            self.run_once([page], cfg=_cfg(discord_output_enabled=True), post=post)
        self.assertIn("未知の種目表記 1件", buf.getvalue())
        text = json.dumps(post.payloads, ensure_ascii=False)
        self.assertIn("未知種目 1件", text)

    def test_counts_line_present_on_normal_run(self):
        page = {"data": [_item("ev_ok", s1=("pl_jp1", "pl_x"))], "meta": {}}
        post = FakePost()
        self.run_once([page], cfg=_cfg(discord_output_enabled=True), post=post)
        text = json.dumps(post.payloads, ensure_ascii=False)
        self.assertIn("取得 1件 ／ 正規化 1件 ／ 対象 1件 ／ 未知種目 0件", text)

    def test_zero_items_is_still_empty_not_anomaly(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_once([{"data": [], "meta": {}}], cfg=_cfg(discord_output_enabled=True))
        self.assertNotIn("::warning::", buf.getvalue())


class UnverifiedApiTest(RunTestBase):
    def test_side_without_players_key_is_not_normalized_and_not_notified(self):
        item = _item("ev_shape", s1=("pl_jp1", "pl_x"))
        item["side1"] = {"team": [{"id": "pl_jp1", "name": "日本A"}]}  # キー名が想定と違う
        post = FakePost()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_once([{"data": [item], "meta": {}}], cfg=_cfg(discord_output_enabled=True), post=post)
        self.assertEqual(post.embed_ids(), [])
        self.assertIn("正規化できた試合が0件", buf.getvalue())

    def test_string_winner_is_not_guessed(self):
        norm = m.normalize_match(_item("ev_w", s1=("pl_jp1", "pl_x"), winner="1"))
        self.assertIsNone(norm["winner"])

    def test_unverified_items_are_recorded_in_run_log(self):
        self.run_once([{"data": [], "meta": {}}], cfg=_cfg())
        log = self.load()["runs"][-1]
        self.assertIn("api_unverified", log)
        self.assertTrue(any(x.startswith("start:") for x in log["api_unverified"]))
        self.assertTrue(any(x.startswith("side1/side2:") for x in log["api_unverified"]))

    def test_api_key_unset_and_discord_off_stay_safe(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc, post = self.run_once([{"data": [_item("ev_s", s1=("pl_jp1", "pl_x"))], "meta": {}}],
                                     key="", cfg=_cfg(discord_output_enabled=True))
        self.assertEqual(rc, 0)
        self.assertEqual(post.payloads, [])
        rc2, post2 = self.run_once([{"data": [_item("ev_s2", s1=("pl_jp1", "pl_x"))], "meta": {}}],
                                   cfg=_cfg(discord_output_enabled=False))
        self.assertEqual(post2.payloads, [])
        self.assertIsNone(self.load()["matches"]["ev_s2"]["notified_at"])


class RankingAttachTest(RunTestBase):
    """保存済みbadspiランキングを、結果通知の選手・対戦相手の順位として付ける（完全一致のみ）。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts"))
        import badspi_ranking_snapshot as bs
        cls.bs = bs
        raw = (ROOT / "tests" / "fixtures" / "real" / "badspi_world_MS_20261103.html").read_bytes()
        cls.ms = bs.build_snapshot(bs.decode_body(raw, "utf-8"), discipline_code="MS",
                                   source_url="https://example.invalid/MS",
                                   fetched_at="2026-11-06T00:00:00+00:00")
        raw_md = (ROOT / "tests" / "fixtures" / "real" / "badspi_world_MD_20261103.html").read_bytes()
        cls.md = bs.build_snapshot(bs.decode_body(raw_md, "utf-8"), discipline_code="MD",
                                   source_url="https://example.invalid/MD",
                                   fetched_at="2026-11-06T00:00:00+00:00")

    def _named_item(self, mid, discipline, side1_names, side2_names):
        def side(names, base):
            return {"players": [{"id": f"pl_{base}{i}", "name": n} for i, n in enumerate(names)]}
        return {"id": mid, "tournament": {"name": "Test Open", "year": 2026}, "discipline": discipline,
                "side1": side(side1_names, "a"), "side2": side(side2_names, "b"),
                "games": [[21, 10], [21, 12]], "winner": 1, "status": "finished", "start": START_D1}

    def test_singles_rank_attached_for_exact_name_and_unknown_marked(self):
        item = self._named_item("ev_rank", "MS", ["クリスト・ポポフ"], ["存在しない選手"])
        roster = {"pl_a0": {"name": "クリスト・ポポフ", "status": m.JP_CONFIRMED}}
        rec = m.build_record(m.normalize_match(item), "2026-11-04")
        emb = m.build_embed(rec, roster, m.compute_window(RUN_1), rankings={"MS": self.ms})
        self.assertIn("1位 ／ 順位不明", emb["description"])
        self.assertIn("上位15位＋掲載日本選手のみ。全順位ではありません", emb["description"])
        self.assertIn("11月3日付（年未確認）", emb["description"])

    def test_japanese_player_rank_15_attached(self):
        item = self._named_item("ev_jp15", "MS", ["渡邉航貴"], ["クリスト・ポポフ"])
        rec = m.build_record(m.normalize_match(item), "2026-11-04")
        emb = m.build_embed(rec, {}, m.compute_window(RUN_1), rankings={"MS": self.ms})
        self.assertIn("15位 ／ 1位", emb["description"])

    def test_doubles_pair_rank_is_order_insensitive_and_exact(self):
        pair = next(r for r in self.md["rows"] if "／" in r["name"])
        a, b2 = pair["members"]
        item = self._named_item("ev_md", "MD", [b2, a], ["存在しない", "組"])
        rec = m.build_record(m.normalize_match(item), "2026-11-04")
        emb = m.build_embed(rec, {}, m.compute_window(RUN_1), rankings={"MD": self.md})
        self.assertIn(f"{pair['rank']}位 ／ 順位不明", emb["description"])

    def test_no_snapshot_means_unavailable_not_guessed(self):
        item = self._named_item("ev_none", "MS", ["クリスト・ポポフ"], ["x"])
        rec = m.build_record(m.normalize_match(item), "2026-11-04")
        emb = m.build_embed(rec, {}, m.compute_window(RUN_1), rankings={})
        self.assertIn("保存データなし（順位不明）", emb["description"])

    def test_end_to_end_run_attaches_saved_ranking_to_notification(self):
        with tempfile.TemporaryDirectory() as d:
            snapdir = Path(d) / "badspi_ranking"
            self.bs.save_snapshot(snapdir, self.ms)
            rankings = self.bs.load_latest_rankings(snapdir)
            item = self._named_item("ev_e2e", "MS", ["クリスト・ポポフ"], ["存在しない"])
            item["side1"]["players"][0]["id"] = "pl_jp1"
            post = FakePost()
            with contextlib.redirect_stdout(io.StringIO()):
                rc = m.run_once(api_key="k", webhook="hook", now_utc=RUN_1, state_path=self.state_path,
                                config=_cfg(discord_output_enabled=True),
                                roster={"pl_jp1": {"name": "クリスト・ポポフ", "status": m.JP_CONFIRMED}},
                                fetch=_fake_fetch([{"data": [item], "meta": {}}]), post=post,
                                sleep=lambda s: None, event="schedule", rankings=rankings)
            self.assertEqual(rc, 0)
            text = json.dumps(post.payloads, ensure_ascii=False)
            self.assertIn("1位 ／ 順位不明", text)
            self.assertIn("ev_e2e", text)


if __name__ == "__main__":
    unittest.main()
