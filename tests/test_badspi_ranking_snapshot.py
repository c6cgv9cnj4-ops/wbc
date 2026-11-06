# -*- coding: utf-8 -*-
"""scripts/badspi_ranking_snapshot.py のテスト。

実HTML（2026-11-06、badspi.jp の世界ランキング5種目）から抽出した行データを
tests/fixtures/badspi_world_2026-11-03.json に保存し、そこから実ページと同じ行構造のHTMLを組み立てて検証する。
（ネットワークは使わない。fixture は抽出値のみで、ページ本文の転載ではない。）
"""
import contextlib
import copy
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import requests  # noqa: E402
import badspi_ranking_snapshot as b  # noqa: E402

NOW = dt.datetime(2026, 11, 6, 21, 5, tzinfo=dt.timezone.utc)
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "badspi_world_2026-11-03.json").read_text(encoding="utf-8"))
CONFIG = json.loads((ROOT / "config" / "badminton_results.json").read_text(encoding="utf-8"))


def page_html(code: str, pages=None) -> str:
    """fixture の行データから、実ページと同じ行順（順位・国・氏名・ポイント・大会数）の HTML を組み立てる。"""
    page = (pages or FIXTURE["pages"])[code]
    rows = "".join(
        "<div>" + "</div><div>".join(
            str(x) for x in (r["rank"], r["country"], r["name"], f"{r['points']:,}", r["tournaments"])
        ) + "</div>"
        for r in page["rows"]
    )
    return f"<html><head><title>{page['page_title']}</title></head><body>{rows}</body></html>"


def build(code, html=None, month=11, year=2026):
    return b.build_snapshot(html or page_html(code), year=year, month=month, discipline_code=code,
                            source_url="https://example.invalid/x", fetched_at=NOW.isoformat())


class RealStructureTest(unittest.TestCase):
    def test_all_five_disciplines_are_complete_with_top15_and_japan_rows(self):
        for code in ("MS", "WS", "MD", "WD", "XD"):
            snap = build(code)
            self.assertTrue(snap["complete"], code)
            self.assertEqual(snap["row_count"], 20, code)
            self.assertEqual(snap["japan_listed_count"], 5, code)
            self.assertEqual(snap["basis_date"], "2026-11-03", "年は指定値、月日はページ由来")
            self.assertFalse(snap["full_ranking_available"], "全件はページに無い")

    def test_japan_listed_rows_keep_their_world_rank_beyond_top15(self):
        snap = build("MS")
        listed = [r["rank"] for r in snap["rows"] if r["section"] == "japan_listed"]
        self.assertEqual(listed, [r["rank"] for r in FIXTURE["pages"]["MS"]["rows"] if r["rank"] > 15])
        self.assertTrue(all(r["country"] == "日本" for r in snap["rows"] if r["section"] == "japan_listed"))

    def test_doubles_pair_is_split_into_two_members(self):
        snap = build("MD")
        pair = next(r for r in snap["rows"] if "／" in r["name"])
        self.assertEqual(len(pair["members"]), 2)
        self.assertEqual(pair["members"], [x.strip() for x in pair["name"].split("／")])

    def test_singles_has_one_member(self):
        snap = build("MS")
        self.assertTrue(all(len(r["members"]) == 1 for r in snap["rows"]))

    def test_configured_urls_are_the_five_world_ranking_pages(self):
        urls = CONFIG["badspi_ranking_urls"]
        self.assertEqual(set(urls), {"MS", "WS", "MD", "WD", "XD"})
        for code, url in urls.items():
            self.assertTrue(url.startswith("https://www.badspi.jp/ranking/%e4%b8%96%e7%95%8c"), code)


class ParseGuardTest(unittest.TestCase):
    def test_wrong_discipline_page_is_rejected(self):
        # MS の URL で WS のページが返ってきた場合は保存しない
        with self.assertRaises(b.ParseError):
            build("MS", html=page_html("WS"))

    def test_missing_basis_in_title_is_rejected(self):
        html = page_html("MS").replace("（11月3日付）", "")
        with self.assertRaises(b.ParseError):
            build("MS", html=html)

    def test_basis_month_mismatch_is_rejected(self):
        with self.assertRaises(b.ParseError):
            build("MS", month=10)

    def test_year_is_taken_from_caller_not_page(self):
        self.assertEqual(build("MS", year=2031)["basis_date"], "2031-11-03")

    def test_missing_top_rank_is_incomplete_with_reason(self):
        pages = copy.deepcopy(FIXTURE["pages"])
        pages["MS"]["rows"] = [r for r in pages["MS"]["rows"] if r["rank"] != 7]
        snap = build("MS", html=page_html("MS", pages))
        self.assertFalse(snap["complete"])
        self.assertIn("7", snap["incomplete_reason"])

    def test_duplicate_rank_is_incomplete(self):
        pages = copy.deepcopy(FIXTURE["pages"])
        pages["MS"]["rows"][1]["rank"] = 1
        snap = build("MS", html=page_html("MS", pages))
        self.assertFalse(snap["complete"])
        self.assertIn("重複", snap["incomplete_reason"])

    def test_truncated_page_without_top15_is_incomplete(self):
        pages = copy.deepcopy(FIXTURE["pages"])
        pages["MS"]["rows"] = pages["MS"]["rows"][:10]
        snap = build("MS", html=page_html("MS", pages))
        self.assertFalse(snap["complete"])


class DecodeTest(unittest.TestCase):
    def test_declared_utf8_is_decoded(self):
        self.assertEqual(b.decode_body("合成".encode("utf-8"), "utf-8"), "合成")

    def test_undeclared_cp932_is_detected_after_utf8_fails(self):
        self.assertEqual(b.decode_body("合成 太郎".encode("cp932"), None), "合成 太郎")

    def test_wrong_declared_latin1_is_treated_as_undeclared(self):
        self.assertEqual(b.decode_body("合成".encode("utf-8"), "iso-8859-1"), "合成")


class SaveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "badspi_ranking"

    def tearDown(self):
        self._tmp.cleanup()

    def test_complete_saved_once_not_overwritten(self):
        self.assertEqual(b.save_snapshot(self.dir, build("MS")), "saved")
        self.assertEqual(b.save_snapshot(self.dir, build("MS")), "exists")

    def test_incomplete_goes_to_separate_file_and_never_replaces_complete(self):
        b.save_snapshot(self.dir, build("MS"))
        before = (self.dir / "2026-11_MS.json").read_text(encoding="utf-8")
        pages = copy.deepcopy(FIXTURE["pages"])
        pages["MS"]["rows"] = pages["MS"]["rows"][:10]
        bad = build("MS", html=page_html("MS", pages))
        self.assertEqual(b.save_snapshot(self.dir, bad), "incomplete_saved")
        self.assertEqual((self.dir / "2026-11_MS.json").read_text(encoding="utf-8"), before)
        self.assertTrue((self.dir / "2026-11_MS.incomplete.json").exists())


LM_2026 = "Wed, 04 Nov 2026 09:15:00 GMT"   # 2026年11月の更新（基準年の独立根拠）
LM_2025 = "Tue, 04 Nov 2025 09:15:00 GMT"   # 古いページ（前年）


class YearGuardTest(unittest.TestCase):
    """ページに年が無い。実行年を無条件に採用して古いページを誤登録しないことを検証する。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "d"
        self.url = "https://example.invalid/MS"

    def tearDown(self):
        self._tmp.cleanup()

    def run_with(self, *, last_modified, human=None, year_month="2026-11", html=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(year_month=year_month, urls={"MS": self.url}, snapshot_dir=self.dir,
                                fetch=lambda u: (html or page_html("MS"), u, last_modified),
                                human_year=human)
        return rc, buf.getvalue()

    def test_no_year_evidence_holds_and_saves_nothing(self):
        rc, out = self.run_with(last_modified=None)
        self.assertEqual(rc, 0)
        self.assertIn("[HOLD] MS", out)
        self.assertFalse(self.dir.exists())

    def test_old_page_with_previous_year_last_modified_is_held_not_saved_as_current_year(self):
        # 古いページ（2025年11月の更新）を、実行年2026で誤登録しないこと
        rc, out = self.run_with(last_modified=LM_2025)
        self.assertEqual(rc, 0)
        self.assertIn("[HOLD] MS", out)
        self.assertFalse((self.dir / "2026-11_MS.json").exists())

    def test_matching_last_modified_year_month_saves_with_evidence(self):
        rc, out = self.run_with(last_modified=LM_2026)
        self.assertEqual(rc, 0)
        saved = json.loads((self.dir / "2026-11_MS.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["year_evidence"]["kind"], "http_last_modified")

    def test_human_confirmation_with_matching_year_saves(self):
        rc, out = self.run_with(last_modified=None, human={"source": "人が確認（2026-11-06）", "year": 2026})
        saved = json.loads((self.dir / "2026-11_MS.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["year_evidence"]["kind"], "human_confirmed")

    def test_human_confirmation_with_other_year_is_held(self):
        rc, out = self.run_with(last_modified=None, human={"source": "x", "year": 2025})
        self.assertIn("[HOLD]", out)
        self.assertFalse(self.dir.exists())

    def test_existing_complete_snapshot_is_never_touched_by_held_run(self):
        self.run_with(last_modified=LM_2026)
        path = self.dir / "2026-11_MS.json"
        before = path.read_text(encoding="utf-8")
        self.run_with(last_modified=None, html=page_html("MS", copy.deepcopy(FIXTURE["pages"])))
        self.run_with(last_modified=LM_2025)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_held_year_is_not_marked_as_failure_in_exit_code(self):
        rc, _ = self.run_with(last_modified=None)
        self.assertEqual(rc, 0)

    def test_page_text_year_is_not_used_as_evidence(self):
        self.assertIsNone(b.year_evidence(year=2026, month=11, last_modified=None, human=None))
        self.assertIsNone(b.year_from_last_modified("not a date"))


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "badspi_ranking"
        self.urls = {c: f"https://example.invalid/{c}" for c in ("MS", "WS", "MD", "WD", "XD")}

    def tearDown(self):
        self._tmp.cleanup()

    def fetch_ok(self, url):
        code = url.rsplit("/", 1)[-1]
        return page_html(code), url + "?final", LM_2026

    def test_all_five_saved_and_final_url_recorded(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(year_month="2026-11", urls=self.urls, snapshot_dir=self.dir,
                                fetch=self.fetch_ok, now_utc=NOW)
        self.assertEqual(rc, 0)
        for code in ("MS", "WS", "MD", "WD", "XD"):
            saved = json.loads((self.dir / f"2026-11_{code}.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["source_url"], f"https://example.invalid/{code}?final")
        self.assertEqual(buf.getvalue().count("SAVED"), 5)

    def test_one_failing_discipline_does_not_stop_others(self):
        def fetch(url):
            if url.endswith("/MS"):
                raise requests.ConnectionError("offline")
            return self.fetch_ok(url)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(year_month="2026-11", urls=self.urls, snapshot_dir=self.dir, fetch=fetch)
        self.assertEqual(rc, 1)
        self.assertFalse((self.dir / "2026-11_MS.json").exists())
        self.assertTrue((self.dir / "2026-11_WS.json").exists())
        self.assertIn("[ERROR] MS", buf.getvalue())

    def test_unset_url_is_skipped_individually_with_reason(self):
        urls = dict(self.urls, WD=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(year_month="2026-11", urls=urls, snapshot_dir=self.dir, fetch=self.fetch_ok)
        self.assertEqual(rc, 0)
        self.assertIn("[SKIP] WD", buf.getvalue())
        self.assertFalse((self.dir / "2026-11_WD.json").exists())
        self.assertTrue((self.dir / "2026-11_XD.json").exists())

    def test_all_unset_warns_and_fetches_nothing(self):
        calls = []
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(year_month="2026-11", urls={c: None for c in self.urls},
                                snapshot_dir=self.dir, fetch=lambda u: calls.append(u))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("::warning::", buf.getvalue())

    def test_http_error_is_failure_and_writes_nothing(self):
        resp = mock.Mock(content=b"", headers={}, url="x")
        resp.raise_for_status.side_effect = requests.HTTPError("500")
        with mock.patch.object(b.requests, "get", return_value=resp):
            rc = b.run_snapshot(year_month="2026-11", urls={"MS": "https://example.invalid/MS"},
                                snapshot_dir=self.dir)
        self.assertEqual(rc, 1)
        self.assertFalse(self.dir.exists())

    def test_invalid_year_month_is_rejected(self):
        self.assertEqual(b.run_snapshot(year_month="2026-13", urls=self.urls, snapshot_dir=self.dir), 1)


class RealHtmlTest(unittest.TestCase):
    """実HTML（tests/fixtures/real の原本）で解析する。合成HTMLではない。"""
    REAL = ROOT / "tests" / "fixtures" / "real"

    def _real(self, code):
        raw = (self.REAL / f"badspi_world_{code}_20261103.html").read_bytes()
        return b.decode_body(raw, "utf-8")

    def test_five_real_pages_are_complete_and_match_recorded_rows(self):
        for code in ("MS", "WS", "MD", "WD", "XD"):
            snap = b.build_snapshot(self._real(code), year=2026, month=11, discipline_code=code,
                                    source_url="x", fetched_at=NOW.isoformat())
            self.assertTrue(snap["complete"], code)
            self.assertEqual(snap["row_count"], 20, code)
            self.assertEqual(snap["basis_date"], "2026-11-03", code)
            fixture_rows = [(r["rank"], r["country"], r["name"], r["points"], r["tournaments"])
                            for r in FIXTURE["pages"][code]["rows"]]
            real_rows = [(r["rank"], r["country"], r["name"], r["points"], r["tournaments"])
                         for r in snap["rows"]]
            self.assertEqual(real_rows, fixture_rows, code)

    def test_real_page_for_other_discipline_is_rejected(self):
        with self.assertRaises(b.ParseError):
            b.build_snapshot(self._real("WS"), year=2026, month=11, discipline_code="MS",
                             source_url="x", fetched_at=NOW.isoformat())

    def test_real_page_month_mismatch_is_waiting_not_error(self):
        with self.assertRaises(b.BasisMismatch):
            b.build_snapshot(self._real("MS"), year=2026, month=12, discipline_code="MS",
                             source_url="x", fetched_at=NOW.isoformat())

    def test_run_with_mismatch_is_wait_and_returns_zero(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(year_month="2026-12", urls={"MS": "https://example.invalid/MS"},
                                snapshot_dir=Path(tmp.name) / "d",
                                fetch=lambda u: (self._real("MS"), u, None))
        self.assertEqual(rc, 0)
        self.assertIn("[WAIT] MS", buf.getvalue())
        self.assertFalse((Path(tmp.name) / "d").exists())

    def test_year_is_never_read_from_page_and_real_title_has_no_year(self):
        title = b.parse_title(self._real("MS"))
        self.assertNotRegex(title, r"\d{4}年")


if __name__ == "__main__":
    unittest.main()
