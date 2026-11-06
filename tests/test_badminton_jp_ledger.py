# -*- coding: utf-8 -*-
"""scripts/badminton_jp_ledger.py のテスト（ネットワーク・APIキー不要）。

- JBA カード抽出は、実ページから切り出したカード2件（tests/fixtures/jba_cards_excerpt.html）で検証する。
- 検索（search）は偽のAPI応答で検証する。実APIの応答は未検証。
- 確定（confirm）は、検索結果に無い選手ID・国コード不一致・根拠不足を拒否することを確認する。
"""
import io
import contextlib
import datetime as dt
import json
import sys
import tempfile
import unittest
import os as _os  # noqa: E402
import sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import offline_guard  # noqa: E402,F401  (テスト中の実通信を遮断)
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import badminton_jp_ledger as L  # noqa: E402
import badminton_results_free as R  # noqa: E402

EXCERPT = (ROOT / "tests" / "fixtures" / "jba_cards_excerpt.html").read_text(encoding="utf-8")
SRC = "https://www.badminton.or.jp/national/player?gender=male"


def _doc(**extra):
    base = {"schema_version": 1, "candidates": [
        {"jba_detail_id": "1", "name": "保木卓朗", "team": "トナミ運輸", "prefecture": "富山県",
         "pages": ["JBA男子・全体"], "source_urls": [SRC], "status": "UNMATCHED", "search": None,
         "same_name_count": 1},
        {"jba_detail_id": "2", "name": "別人候補", "team": "", "prefecture": "", "pages": ["JBA男子・全体"],
         "source_urls": [SRC], "status": "UNMATCHED", "search": None, "same_name_count": 1},
    ]}
    base.update(extra)
    return base


def _search_payload(items):
    return {"data": items, "meta": {}}


class CardParseTest(unittest.TestCase):
    def test_real_card_excerpt_is_parsed_with_detail_id_team_and_prefecture(self):
        cards = L.parse_jba_cards(EXCERPT, "JBA男子・全体", SRC)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["name"], "岡村洋輝")
        self.assertEqual(cards[0]["team"], "BIPROGY")
        self.assertEqual(cards[0]["prefecture"], "東京都")
        self.assertTrue(cards[0]["jba_detail_url"].startswith("https://www.badminton.or.jp/national/player/view/"))

    def test_page_without_cards_is_error_not_empty_list(self):
        with self.assertRaises(L.LedgerError):
            L.parse_jba_cards("<html><body>メンテナンス中</body></html>", "x", SRC)

    def test_same_player_on_two_pages_is_merged_by_detail_id(self):
        doc = L.build_candidates([("JBA男子・全体", SRC, EXCERPT),
                                  ("JBA男子・U24", SRC + "&category[]=u24", EXCERPT)])
        self.assertEqual(len(doc["candidates"]), 2)
        self.assertEqual(doc["candidates"][0]["pages"], ["JBA男子・全体", "JBA男子・U24"])
        self.assertEqual(doc["candidates"][0]["status"], "UNMATCHED")
        self.assertIsNone(doc["candidates"][0]["search"])


class SearchTest(unittest.TestCase):
    def test_only_player_items_are_kept_sorted_by_score_and_never_confirmed(self):
        doc = _doc()
        items = [
            {"kind": "tournament", "id": "tr_9", "name": "X", "country": None, "score": 99},
            {"kind": "player", "id": "pl_low", "name": "保木卓朗", "country": "JPN", "score": 60},
            {"kind": "player", "id": "pl_hi", "name": "Takuro Hoki", "country": "JPN", "score": 90},
        ]
        fetch = mock.Mock(return_value=(200, _search_payload(items)))
        out = L.search_candidates(doc, "KEY", lambda k, q: fetch(k, q), max_calls=1, sleep=lambda s: None)
        self.assertEqual(out["searched"], 1)
        ids = [r["id"] for r in doc["candidates"][0]["search"]["results"]]
        self.assertEqual(ids, ["pl_hi", "pl_low"])
        self.assertEqual(doc["candidates"][0]["status"], "UNMATCHED", "検索だけでは確定しない")

    def test_max_calls_is_respected_and_resume_skips_searched(self):
        doc = _doc()
        fetch = mock.Mock(return_value=(200, _search_payload([])))
        L.search_candidates(doc, "KEY", lambda k, q: fetch(k, q), max_calls=1, sleep=lambda s: None)
        out = L.search_candidates(doc, "KEY", lambda k, q: fetch(k, q), max_calls=5, sleep=lambda s: None)
        self.assertEqual(out["searched"], 1)
        self.assertEqual(fetch.call_count, 2)

    def test_auth_or_quota_error_stops_the_run(self):
        doc = _doc()
        for code in (401, 403, 429):
            for c in doc["candidates"]:
                c["search"] = None
            out = L.search_candidates(doc, "KEY", lambda k, q, code=code: (code, {"error": {}}),
                                      max_calls=5, sleep=lambda s: None)
            self.assertIsNotNone(out["stopped"])
            self.assertEqual(out["searched"], 1)
            self.assertIsNone(doc["candidates"][0]["search"])

    def test_sleep_between_calls_respects_rate_limit(self):
        doc = _doc()
        waits = []
        L.search_candidates(doc, "KEY", lambda k, q: (200, _search_payload([])), max_calls=5, sleep=waits.append)
        self.assertEqual(waits, [L.SEARCH_INTERVAL_SEC])


class ConfirmTest(unittest.TestCase):
    def setUp(self):
        self.doc = _doc()
        L.search_candidates(self.doc, "KEY", lambda k, q: (200, _search_payload([
            {"kind": "player", "id": "pl_jp", "name": "保木卓朗", "country": "JPN", "score": 95},
            {"kind": "player", "id": "pl_cn", "name": "Hoki Other", "country": "CHN", "score": 70},
        ])), max_calls=2, sleep=lambda s: None)
        self.roster = {"schema_version": 1, "players": []}
        self.ok = dict(detail_id="1", player_id="pl_jp", decision="JP", source_url=SRC,
                       confirmed_by="細川", confirmed_at="2026-11-06")

    def test_confirm_writes_roster_entry_with_evidence(self):
        entry = L.confirm(self.doc, self.roster, **self.ok)
        self.assertEqual(entry["status"], "JP_CONFIRMED")
        self.assertEqual(self.roster["players"][0]["confirmed_by"], "細川")
        self.assertEqual(self.roster["players"][0]["source_url"], SRC)

    def test_roster_written_by_ledger_is_accepted_by_results_loader(self):
        L.confirm(self.doc, self.roster, **self.ok)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text(json.dumps(self.roster, ensure_ascii=False), encoding="utf-8")
            loaded = R.load_roster(p)
        self.assertEqual(loaded["pl_jp"]["status"], "JP_CONFIRMED")

    def test_player_id_not_in_search_results_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.confirm(self.doc, self.roster, **dict(self.ok, player_id="pl_free_text"))

    def test_jp_decision_requires_country_jpn(self):
        with self.assertRaises(L.LedgerError):
            L.confirm(self.doc, self.roster, **dict(self.ok, player_id="pl_cn"))

    def test_non_jp_decision_refused_for_jpn_player(self):
        with self.assertRaises(L.LedgerError):
            L.confirm(self.doc, self.roster, **dict(self.ok, decision="NON_JP"))

    def test_missing_evidence_is_refused(self):
        for bad in (dict(source_url=""), dict(confirmed_by=" "), dict(confirmed_at="2026/11/06"),
                    dict(source_url="ftp://x")):
            with self.assertRaises(L.LedgerError):
                L.confirm(self.doc, self.roster, **dict(self.ok, **bad))
        self.assertEqual(self.roster["players"], [])

    def test_unsearched_candidate_cannot_be_confirmed(self):
        self.doc["candidates"][1]["search"] = None   # 検索していない候補
        with self.assertRaises(L.LedgerError):
            L.confirm(self.doc, self.roster, **dict(self.ok, detail_id="2"))

    def test_same_name_is_not_auto_confirmed_without_confirm_call(self):
        self.assertEqual(self.roster["players"], [])
        self.assertTrue(all(c["status"] == "UNMATCHED" for c in self.doc["candidates"]))


class ReviewTest(unittest.TestCase):
    def test_review_lists_unsearched_as_unmatched_and_same_name_flag(self):
        doc = _doc()
        doc["candidates"][1]["same_name_count"] = 2
        md = L.review_markdown(doc)
        self.assertIn("未照合（検索未実施）", md)
        self.assertIn("（同名あり）", md)
        self.assertIn("自動確定はしていません", md)


class CliTest(unittest.TestCase):
    def test_search_without_key_warns_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            cpath = Path(d) / "c.json"
            cpath.write_text(json.dumps(_doc()), encoding="utf-8")
            before = cpath.read_text(encoding="utf-8")
            buf = io.StringIO()
            with mock.patch.object(L, "CANDIDATES_PATH", cpath), \
                    mock.patch.object(L, "REVIEW_PATH", Path(d) / "r.md"), \
                    mock.patch.object(L, "_load_api_key", return_value=""), \
                    contextlib.redirect_stdout(buf):
                rc = L.main(["search"])
            self.assertEqual(rc, 0)
            self.assertIn("::warning::", buf.getvalue())
            self.assertEqual(cpath.read_text(encoding="utf-8"), before)

    def test_confirm_cli_refuses_and_leaves_roster_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            cpath, rpath = Path(d) / "c.json", Path(d) / "p.json"
            cpath.write_text(json.dumps(_doc()), encoding="utf-8")
            rpath.write_text(json.dumps({"schema_version": 1, "players": []}), encoding="utf-8")
            with mock.patch.object(L, "CANDIDATES_PATH", cpath), mock.patch.object(L, "ROSTER_PATH", rpath), \
                    mock.patch.object(L, "REVIEW_PATH", Path(d) / "r.md"), \
                    contextlib.redirect_stdout(io.StringIO()):
                rc = L.main(["confirm", "--detail-id", "1", "--player-id", "pl_x", "--decision", "JP",
                             "--source-url", SRC, "--confirmed-by", "細川", "--confirmed-at", "2026-11-06"])
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(rpath.read_text(encoding="utf-8"))["players"], [])


class QuotaAndRobustnessTest(unittest.TestCase):
    def _many(self, n):
        return {"candidates": [{"jba_detail_id": str(i), "name": f"候補{i}", "team": "", "prefecture": "",
                                "pages": ["x"], "source_urls": [SRC], "status": "UNMATCHED",
                                "search": None, "same_name_count": 1} for i in range(n)]}

    def test_default_and_over_limit_are_capped_at_20(self):
        self.assertEqual(L.SEARCH_MAX_PER_RUN, 20)
        doc = self._many(49)
        fetch = mock.Mock(return_value=(200, _search_payload([])))
        out = L.search_candidates(doc, "KEY", lambda k, q: fetch(k, q), max_calls=500, sleep=lambda s: None)
        self.assertEqual(out["searched"], 20)
        self.assertEqual(out["remaining"], 29)

    def test_resume_next_day_continues_from_unsearched_only(self):
        doc = self._many(30)
        L.search_candidates(doc, "KEY", lambda k, q: (200, _search_payload([])), sleep=lambda s: None)
        calls = []
        out = L.search_candidates(doc, "KEY", lambda k, q: (calls.append(q) or (200, _search_payload([]))),
                                  sleep=lambda s: None)
        self.assertEqual(out["searched"], 10)
        self.assertEqual(calls[0], "候補20", "検索済みの候補は再検索しない")

    def test_429_stops_the_day_with_explicit_reason_and_no_retry(self):
        doc = self._many(5)
        fetch = mock.Mock(return_value=(429, {"error": {"code": "rate_limited"}}))
        out = L.search_candidates(doc, "KEY", lambda k, q: fetch(k, q), sleep=lambda s: None)
        self.assertEqual(fetch.call_count, 1)
        self.assertIn("429", out["stopped"])
        self.assertEqual(out["remaining"], 5)

    def test_malformed_search_items_do_not_crash_and_are_safe(self):
        doc = _doc()
        items = [
            {"kind": "player", "id": "pl_a", "name": None, "country": 123, "score": "high"},
            {"kind": "player", "id": "pl_b", "name": "B", "country": "JPN", "score": True},
            "not-a-dict",
            {"kind": "player"},
        ]
        L.search_candidates(doc, "KEY", lambda k, q: (200, _search_payload(items)), max_calls=1, sleep=lambda s: None)
        res = doc["candidates"][0]["search"]["results"]
        self.assertEqual([r["id"] for r in res], ["pl_a", "pl_b"])
        self.assertIsNone(res[0]["country"], "数値の国コードは採用しない")
        self.assertIsNone(res[0]["score"])
        self.assertEqual(res[0]["name"], "")

    def test_jp_confirm_refused_when_country_is_not_string_jpn(self):
        doc = _doc()
        L.search_candidates(doc, "KEY", lambda k, q: (200, _search_payload([
            {"kind": "player", "id": "pl_x", "name": "保木", "country": ["JPN"], "score": 90}])),
            max_calls=1, sleep=lambda s: None)
        with self.assertRaises(L.LedgerError):
            L.confirm(doc, {"players": []}, detail_id="1", player_id="pl_x", decision="JP", source_url=SRC,
                      confirmed_by="細川", confirmed_at="2026-11-06")


class DailyBudgetAndQueueTest(unittest.TestCase):
    def _doc(self, n):
        return {"candidates": [{"jba_detail_id": str(i), "name": f"候補{i}", "team": "T", "prefecture": "P",
                                "pages": ["x"], "source_urls": [SRC], "status": "UNMATCHED",
                                "search": None, "same_name_count": 1, "jba_detail_url": f"u{i}"}
                               for i in range(n)]}

    def test_daily_budget_is_30_and_keeps_20_for_results(self):
        self.assertEqual(L.SEARCH_DAILY_BUDGET, 30)
        doc = self._doc(60)
        ok = lambda k, q: (200, _search_payload([]))
        a = L.search_candidates(doc, "K", ok, max_calls=20, sleep=lambda s: None, today_utc="2026-11-06")
        b = L.search_candidates(doc, "K", ok, max_calls=20, sleep=lambda s: None, today_utc="2026-11-06")
        self.assertEqual(a["searched"], 20)
        self.assertEqual(b["searched"], 10, "同じUTC日の合計は30まで")
        self.assertIn("検索枠", b["stopped"])
        self.assertEqual(doc["search_usage"]["2026-11-06"], 30)

    def test_budget_resets_on_next_utc_day(self):
        doc = self._doc(60)
        ok = lambda k, q: (200, _search_payload([]))
        L.search_candidates(doc, "K", ok, max_calls=20, sleep=lambda s: None, today_utc="2026-11-06")
        L.search_candidates(doc, "K", ok, max_calls=20, sleep=lambda s: None, today_utc="2026-11-06")
        nxt = L.search_candidates(doc, "K", ok, max_calls=20, sleep=lambda s: None, today_utc="2026-11-07")
        self.assertEqual(nxt["searched"], 20)
        self.assertIsNone(nxt["stopped"])

    def test_queue_lists_only_jpn_hits_in_batches_of_ten_and_never_confirms(self):
        doc = self._doc(12)
        hits = [{"kind": "player", "id": f"pl_{i}", "name": f"候補{i}", "country": "JPN" if i % 2 == 0 else "CHN",
                 "score": 90} for i in range(12)]
        it = iter(hits)
        L.search_candidates(doc, "K", lambda k, q: (200, _search_payload([next(it)])), max_calls=20,
                            sleep=lambda s: None, today_utc="2026-11-06")
        md = L.confirm_queue_markdown(doc)
        self.assertIn("対象: 6 名", md)
        self.assertIn("塊 1", md)
        self.assertNotIn("候補1 |", md, "JPN以外は対象外")
        self.assertIn("自動確定はしていません", md)
        self.assertTrue(all(c["status"] == "UNMATCHED" for c in doc["candidates"]))

    def test_queue_is_empty_before_search(self):
        self.assertIn("JPN候補が出た人はいません", L.confirm_queue_markdown(self._doc(3)))


class BatchDecisionTest(unittest.TestCase):
    """社長の一括確認：CSVに decision を書いて1回 apply する。根拠が揃う行だけ確定し、他は未確定のまま。"""

    def _doc(self):
        doc = _doc()
        L.search_candidates(doc, "K", lambda k, q: (200, _search_payload([
            {"kind": "player", "id": "pl_jp", "name": "保木卓朗", "country": "JPN", "score": 95}])),
            max_calls=1, sleep=lambda s: None, today_utc="2026-11-06")
        return doc

    def _csv(self, doc, decision="", who="", when="", source=None):
        import csv
        import io
        text = L.export_decisions_csv(doc)
        rows = list(csv.reader(io.StringIO(text)))
        header = rows[0]
        idx = {h: i for i, h in enumerate(header)}
        for row in rows[1:]:
            if row[idx["jba_detail_id"]] == "1":
                row[idx["decision(JP/NON_JP/空欄)"]] = decision
                row[idx["confirmed_by"]] = who
                row[idx["confirmed_at(YYYY-MM-DD)"]] = when
                if source is not None:
                    row[idx["JBA選手ページ(根拠URL)"]] = source
        out = io.StringIO()
        csv.writer(out).writerows([header] + rows[1:])
        return out.getvalue()

    def test_export_has_one_row_per_candidate_with_evidence_columns_and_no_status_change(self):
        doc = self._doc()
        text = L.export_decisions_csv(doc)
        self.assertEqual(text.count("\n"), 1 + len(doc["candidates"]))
        self.assertIn("候補あり（人が確認）", text)
        self.assertIn("未照合（検索未実施）", text)
        self.assertTrue(all(c["status"] == "UNMATCHED" for c in doc["candidates"]), "出力だけでは確定しない")

    def test_blank_decision_rows_stay_unconfirmed(self):
        doc = self._doc()
        roster = {"players": []}
        res = L.apply_decisions(doc, roster, self._csv(doc))
        self.assertEqual(res["applied"], [])
        self.assertEqual(roster["players"], [])

    def test_complete_evidence_row_is_confirmed_in_one_apply(self):
        doc = self._doc()
        roster = {"players": []}
        csv_text = self._csv(doc, "JP", "細川", "2026-11-06", SRC)
        res = L.apply_decisions(doc, roster, csv_text)
        self.assertEqual(len(res["applied"]), 1)
        self.assertEqual(roster["players"][0]["player_id"], "pl_jp")
        self.assertEqual(roster["players"][0]["confirmed_by"], "細川")

    def test_missing_evidence_stays_unconfirmed_with_reason(self):
        doc = self._doc()
        roster = {"players": []}
        res = L.apply_decisions(doc, roster, self._csv(doc, "JP", "", "", SRC))
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(res["rejected"]), 1)
        self.assertIn("確認者", res["rejected"][0]["reason"])
        self.assertEqual(roster["players"], [])

    def test_missing_jba_url_stays_unconfirmed(self):
        doc = self._doc()
        res = L.apply_decisions(doc, {"players": []}, self._csv(doc, "JP", "細川", "2026-11-06", ""))
        self.assertEqual(res["applied"], [])
        self.assertIn("出典", res["rejected"][0]["reason"])

    def test_candidate_without_jpn_hit_cannot_be_decided(self):
        doc = self._doc()
        doc["candidates"][1]["search"] = {"searched_at": "x", "results": [
            {"id": "pl_cn", "name": "別人候補", "country": "CHN", "score": 80}]}
        import csv, io
        text = L.export_decisions_csv(doc)
        rows = list(csv.reader(io.StringIO(text)))
        idx = {h: i for i, h in enumerate(rows[0])}
        for r in rows[1:]:
            if r[0] == "2":
                r[idx["decision(JP/NON_JP/空欄)"]] = "JP"
                r[idx["confirmed_by"]] = "細川"
                r[idx["confirmed_at(YYYY-MM-DD)"]] = "2026-11-06"
                r[idx["JBA選手ページ(根拠URL)"]] = SRC
        out = io.StringIO()
        csv.writer(out).writerows(rows)
        res = L.apply_decisions(doc, {"players": []}, out.getvalue())
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(res["rejected"]), 1)


if __name__ == "__main__":
    unittest.main()
