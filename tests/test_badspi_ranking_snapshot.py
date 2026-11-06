# -*- coding: utf-8 -*-
"""scripts/badspi_ranking_snapshot.py のテスト（ネットワーク不要）。

- 実HTML: tests/fixtures/real/ の原本（2026-11-06取得の5種目）で解析・保存・月次の入口を検証する。
- 行データ由来のHTML（page_html）は、実HTMLの行データを再構成した補助データとして、異常系の検証に使う。
"""
import contextlib
import copy
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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import requests  # noqa: E402
import badspi_ranking_snapshot as b  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "badspi_world_2026-11-03.json").read_text(encoding="utf-8"))
CONFIG = json.loads((ROOT / "config" / "badminton_results.json").read_text(encoding="utf-8"))
REAL = ROOT / "tests" / "fixtures" / "real"
NOW = "2026-11-06T09:00:00+00:00"


def real_html(code: str) -> str:
    return b.decode_body((REAL / f"badspi_world_{code}_20261103.html").read_bytes(), "utf-8")


def page_html(code: str, pages=None) -> str:
    """実HTMLから抽出した行データを、同じ行順の HTML に再構成する（異常系の補助用）。"""
    page = (pages or FIXTURE["pages"])[code]
    rows = "".join(
        "<div>" + "</div><div>".join(
            str(x) for x in (r["rank"], r["country"], r["name"], f"{r['points']:,}", r["tournaments"])
        ) + "</div>"
        for r in page["rows"]
    )
    return f"<html><head><title>{page['page_title']}</title></head><body>{rows}</body></html>"


def build(code, html=None):
    return b.build_snapshot(html or real_html(code), discipline_code=code,
                            source_url="https://example.invalid/x", fetched_at=NOW)


class RealHtmlTest(unittest.TestCase):
    def test_five_real_pages_are_complete_and_match_recorded_rows(self):
        for code in ("MS", "WS", "MD", "WD", "XD"):
            snap = build(code)
            self.assertTrue(snap["complete"], code)
            self.assertEqual(snap["row_count"], 20, code)
            self.assertEqual(snap["basis_month_day"], "11-03", code)
            fixture_rows = [(r["rank"], r["country"], r["name"], r["points"], r["tournaments"])
                            for r in FIXTURE["pages"][code]["rows"]]
            real_rows = [(r["rank"], r["country"], r["name"], r["points"], r["tournaments"]) for r in snap["rows"]]
            self.assertEqual(real_rows, fixture_rows, code)

    def test_real_title_has_no_year_and_year_is_not_invented(self):
        snap = build("MS")
        self.assertNotRegex(snap["page_title"], r"\d{4}年")
        self.assertIsNone(snap["year"])
        self.assertEqual(snap["year_status"], "unconfirmed")
        self.assertEqual(snap["basis_label"], "11月3日付（年未確認）")
        self.assertFalse(snap["full_ranking_available"])

    def test_japan_listed_rows_keep_world_rank_beyond_top15(self):
        snap = build("MS")
        listed = [r["rank"] for r in snap["rows"] if r["section"] == "japan_listed"]
        self.assertEqual(listed, [r["rank"] for r in FIXTURE["pages"]["MS"]["rows"] if r["rank"] > 15])

    def test_doubles_pair_is_split_into_two_members(self):
        snap = build("MD")
        pair = next(r for r in snap["rows"] if "／" in r["name"])
        self.assertEqual(pair["members"], [x.strip() for x in pair["name"].split("／")])
        self.assertEqual(len(pair["members"]), 2)

    def test_real_page_for_other_discipline_is_rejected(self):
        with self.assertRaises(b.ParseError):
            build("MS", html=real_html("WS"))

    def test_configured_urls_are_the_five_world_ranking_pages(self):
        urls = CONFIG["badspi_ranking_urls"]
        self.assertEqual(set(urls), {"MS", "WS", "MD", "WD", "XD"})
        for code, url in urls.items():
            self.assertTrue(url.startswith("https://www.badspi.jp/ranking/%e4%b8%96%e7%95%8c"), code)


class ParseGuardTest(unittest.TestCase):
    def test_missing_basis_in_title_is_rejected(self):
        with self.assertRaises(b.ParseError):
            build("MS", html=real_html("MS").replace("（11月3日付）", ""))

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

    def test_truncated_page_is_incomplete(self):
        pages = copy.deepcopy(FIXTURE["pages"])
        pages["MS"]["rows"] = pages["MS"]["rows"][:10]
        self.assertFalse(build("MS", html=page_html("MS", pages))["complete"])


class DecodeTest(unittest.TestCase):
    def test_declared_utf8_is_decoded(self):
        self.assertEqual(b.decode_body("合成".encode("utf-8"), "utf-8"), "合成")

    def test_undeclared_cp932_is_detected_after_utf8_fails(self):
        self.assertEqual(b.decode_body("合成 太郎".encode("cp932"), None), "合成 太郎")

    def test_wrong_declared_latin1_is_treated_as_undeclared(self):
        self.assertEqual(b.decode_body("合成".encode("utf-8"), "iso-8859-1"), "合成")


class SaveAndLoadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "badspi_ranking"

    def tearDown(self):
        self._tmp.cleanup()

    def test_complete_saved_by_basis_month_day_without_year(self):
        self.assertEqual(b.save_snapshot(self.dir, build("MS")), "saved")
        self.assertTrue((self.dir / "11-03_MS.json").exists())

    def test_same_basis_complete_is_not_overwritten(self):
        b.save_snapshot(self.dir, build("MS"))
        before = (self.dir / "11-03_MS.json").read_text(encoding="utf-8")
        self.assertEqual(b.save_snapshot(self.dir, build("MS")), "exists")
        self.assertEqual((self.dir / "11-03_MS.json").read_text(encoding="utf-8"), before)

    def test_incomplete_never_replaces_complete_of_same_basis(self):
        b.save_snapshot(self.dir, build("MS"))
        pages = copy.deepcopy(FIXTURE["pages"])
        pages["MS"]["rows"] = pages["MS"]["rows"][:10]
        self.assertEqual(b.save_snapshot(self.dir, build("MS", html=page_html("MS", pages))), "incomplete_saved")
        self.assertTrue((self.dir / "11-03_MS.incomplete.json").exists())
        self.assertTrue(json.loads((self.dir / "11-03_MS.json").read_text(encoding="utf-8"))["complete"])

    def test_latest_loader_picks_newest_complete_and_ignores_incomplete_and_broken(self):
        old = dict(build("MS"), fetched_at="2026-11-01T00:00:00+00:00", basis_month_day="10-27")
        new = dict(build("MS"), fetched_at="2026-11-06T00:00:00+00:00")
        self.dir.mkdir(parents=True)
        (self.dir / "10-27_MS.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        (self.dir / "11-03_MS.json").write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
        (self.dir / "11-03_WS.incomplete.json").write_text(json.dumps(dict(build("WS"), complete=False)),
                                                           encoding="utf-8")
        (self.dir / "broken.json").write_text("{x", encoding="utf-8")
        latest = b.load_latest_rankings(self.dir)
        self.assertEqual(set(latest), {"MS"})
        self.assertEqual(latest["MS"]["fetched_at"], "2026-11-06T00:00:00+00:00")

    def test_loader_returns_empty_when_directory_missing(self):
        self.assertEqual(b.load_latest_rankings(self.dir / "none"), {})


class FindRankTest(unittest.TestCase):
    def setUp(self):
        self.ms = build("MS")
        self.md = build("MD")

    def test_singles_exact_name_gives_rank(self):
        self.assertEqual(b.find_rank(self.ms, ["クリスト・ポポフ"]), 1)

    def test_japanese_player_in_top15_gives_rank(self):
        self.assertEqual(b.find_rank(self.ms, ["渡邉航貴"]), 15)

    def test_japanese_player_listed_below_15_gives_world_rank(self):
        self.assertEqual(b.find_rank(self.ms, ["田中湧士"]), 17)

    def test_unknown_or_partial_name_is_none_not_guessed(self):
        self.assertIsNone(b.find_rank(self.ms, ["ポポフ"]))
        self.assertIsNone(b.find_rank(self.ms, ["存在しない選手"]))
        self.assertIsNone(b.find_rank(self.ms, [""]))
        self.assertIsNone(b.find_rank(self.ms, []))

    def test_doubles_pair_matches_regardless_of_member_order(self):
        pair = next(r for r in self.md["rows"] if "／" in r["name"])
        self.assertEqual(b.find_rank(self.md, list(reversed(pair["members"]))), pair["rank"])

    def test_doubles_with_one_member_is_none(self):
        pair = next(r for r in self.md["rows"] if "／" in r["name"])
        self.assertIsNone(b.find_rank(self.md, [pair["members"][0]]))

    def test_ambiguous_duplicate_names_are_none(self):
        snap = copy.deepcopy(self.ms)
        snap["rows"].append(dict(snap["rows"][0], rank=99))
        self.assertIsNone(b.find_rank(snap, ["クリスト・ポポフ"]))


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "badspi_ranking"
        self.urls = {c: f"https://example.invalid/{c}" for c in ("MS", "WS", "MD", "WD", "XD")}

    def tearDown(self):
        self._tmp.cleanup()

    def fetch_real(self, url):
        return real_html(url.rsplit("/", 1)[-1]), url + "?final"

    def test_all_five_saved_with_final_url(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(urls=self.urls, snapshot_dir=self.dir, fetch=self.fetch_real)
        self.assertEqual(rc, 0)
        saved = json.loads((self.dir / "11-03_MS.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["source_url"], "https://example.invalid/MS?final")
        self.assertEqual(buf.getvalue().count("[SAVED]"), 5)

    def test_one_failing_discipline_does_not_stop_others(self):
        def fetch(url):
            if url.endswith("/MS"):
                raise requests.ConnectionError("offline")
            return self.fetch_real(url)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(urls=self.urls, snapshot_dir=self.dir, fetch=fetch)
        self.assertEqual(rc, 1)
        self.assertFalse((self.dir / "11-03_MS.json").exists())
        self.assertTrue((self.dir / "11-03_WS.json").exists())
        self.assertIn("[ERROR] MS", buf.getvalue())

    def test_unset_url_is_skipped_individually(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(urls=dict(self.urls, WD=None), snapshot_dir=self.dir, fetch=self.fetch_real)
        self.assertEqual(rc, 0)
        self.assertIn("[SKIP] WD", buf.getvalue())
        self.assertFalse((self.dir / "11-03_WD.json").exists())

    def test_all_unset_warns_and_fetches_nothing(self):
        calls = []
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(urls={c: None for c in self.urls}, snapshot_dir=self.dir,
                                fetch=lambda u: calls.append(u))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("::warning::", buf.getvalue())

    def test_http_error_is_failure_and_writes_nothing(self):
        resp = mock.Mock(content=b"", headers={}, url="x")
        resp.raise_for_status.side_effect = requests.HTTPError("500")
        with mock.patch.object(b.requests, "get", return_value=resp):
            rc = b.run_snapshot(urls={"MS": "https://example.invalid/MS"}, snapshot_dir=self.dir)
        self.assertEqual(rc, 1)
        self.assertFalse(self.dir.exists())


class MonthlyEntrypointTest(unittest.TestCase):
    """月次workflow と同じ入口（main）を実行し、保存データが実際に生成されることを確認する。"""

    def test_main_entrypoint_writes_snapshots_for_all_five_configured_urls(self):
        url_to_code = {url: code for code, url in CONFIG["badspi_ranking_urls"].items()}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "badspi_ranking"
            with mock.patch.object(b, "fetch_html", side_effect=lambda url: (real_html(url_to_code[url]), url)), \
                    contextlib.redirect_stdout(io.StringIO()):
                rc = b.main(["--snapshot-dir", str(out)])
            self.assertEqual(rc, 0)
            produced = sorted(p.name for p in out.glob("*.json"))
            self.assertEqual(produced, ["11-03_MD.json", "11-03_MS.json", "11-03_WD.json",
                                        "11-03_WS.json", "11-03_XD.json"])
            for p in out.glob("*.json"):
                snap = json.loads(p.read_text(encoding="utf-8"))
                self.assertTrue(snap["complete"])
                self.assertIsNone(snap["year"])

    def test_github_actions_has_no_scheduled_collector_and_is_test_only(self):
        wf = ROOT / ".github" / "workflows"
        self.assertFalse((wf / "badspi_ranking_monthly.yml").exists())
        self.assertFalse((wf / "badminton_results_morning.yml").exists())
        text = (wf / "badminton_python_tests.yml").read_text(encoding="utf-8")
        self.assertNotIn("schedule:", text, "GitHub Actions は定時取得を行わない（テスト専用）")
        self.assertNotIn("secrets.", text, "GitHub Secrets を参照しない")
        self.assertIn("unittest", text)


if __name__ == "__main__":
    unittest.main()
