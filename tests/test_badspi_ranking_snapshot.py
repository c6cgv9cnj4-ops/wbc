# -*- coding: utf-8 -*-
"""scripts/badspi_ranking_snapshot.py の単体テスト（ネットワーク不要）。

注意：HTMLフィクスチャは【合成データ】で、badspi.jp の実HTML構造ではない。
実ページの構造はキー取得とは別に、人が確認して差分を調整する必要がある。
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import badspi_ranking_snapshot as b  # noqa: E402

NOW = dt.datetime(2026, 11, 6, 21, 5, tzinfo=dt.timezone.utc)

SYNTHETIC_HTML = """
<html><head><title>テスト用ランキング（11月1日付）</title></head>
<body><table>
<tr><td>1</td><td>日本</td><td>合成 太郎</td><td>1,200</td><td>5</td></tr>
<tr><td>2</td><td>韓国</td><td>Synthetic B</td><td>1,100</td><td>4</td></tr>
<tr><td>3</td><td>日本</td><td>合成 次郎</td><td>980</td><td>6</td></tr>
</table></body></html>
"""

SYNTHETIC_HTML_GAP = SYNTHETIC_HTML.replace("<td>3</td>", "<td>5</td>")


class ParseTest(unittest.TestCase):
    def test_basis_month_day_is_read_from_title_without_year_guess(self):
        lines = b.html_to_lines(SYNTHETIC_HTML)
        self.assertEqual(b.parse_basis_month_day(lines), (11, 1))

    def test_rows_are_parsed_as_five_line_groups(self):
        rows = b.parse_rows(b.html_to_lines(SYNTHETIC_HTML))
        self.assertEqual([r["rank"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0]["points"], 1200)
        self.assertEqual(rows[0]["name"], "合成 太郎")
        self.assertEqual(rows[1]["country"], "韓国")

    def test_missing_basis_date_is_parse_error(self):
        with self.assertRaises(b.ParseError):
            b.parse_basis_month_day(["no date here"])


class SnapshotTest(unittest.TestCase):
    def _build(self, html=SYNTHETIC_HTML, month=11):
        return b.build_snapshot(html, year=2026, month=month, discipline_code="MS",
                                source_url="https://example.invalid/ranking", fetched_at=NOW.isoformat())

    def test_complete_snapshot_when_ranks_are_contiguous(self):
        snap = self._build()
        self.assertTrue(snap["complete"])
        self.assertEqual(snap["row_count"], 3)
        self.assertEqual(snap["basis_date"], "2026-11-01")
        self.assertEqual(snap["year_month"], "2026-11")

    def test_rank_gap_marks_incomplete_not_guessed(self):
        snap = self._build(html=SYNTHETIC_HTML_GAP)
        self.assertFalse(snap["rank_continuous"])
        self.assertFalse(snap["complete"])

    def test_basis_month_mismatch_is_rejected(self):
        with self.assertRaises(b.ParseError):
            self._build(month=10)

    def test_unknown_discipline_code_is_rejected(self):
        with self.assertRaises(b.ParseError):
            b.build_snapshot(SYNTHETIC_HTML, year=2026, month=11, discipline_code="ZZ",
                             source_url="x", fetched_at=NOW.isoformat())


class SaveAndRunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "badspi_ranking"

    def tearDown(self):
        self._tmp.cleanup()

    def test_monthly_snapshot_is_saved_once_and_not_overwritten(self):
        snap = b.build_snapshot(SYNTHETIC_HTML, year=2026, month=11, discipline_code="MS",
                                source_url="x", fetched_at=NOW.isoformat())
        self.assertEqual(b.save_snapshot(self.dir, snap), "saved")
        self.assertEqual(b.save_snapshot(self.dir, snap), "exists")
        saved = json.loads((self.dir / "2026-11_MS.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["row_count"], 3)

    def test_incomplete_snapshot_is_replaced_only_by_complete_one(self):
        gap = b.build_snapshot(SYNTHETIC_HTML_GAP, year=2026, month=11, discipline_code="MS",
                               source_url="x", fetched_at=NOW.isoformat())
        full = b.build_snapshot(SYNTHETIC_HTML, year=2026, month=11, discipline_code="MS",
                                source_url="x", fetched_at=NOW.isoformat())
        self.assertEqual(b.save_snapshot(self.dir, gap), "saved")
        self.assertEqual(b.save_snapshot(self.dir, full), "replaced")
        self.assertEqual(b.save_snapshot(self.dir, gap), "exists", "完全版を不完全版で上書きしない")

    def test_no_urls_configured_means_no_fetch(self):
        calls = []
        rc = b.run_snapshot(year_month="2026-11", urls={"MS": None, "WS": ""},
                            snapshot_dir=self.dir, fetch=lambda u: calls.append(u) or "")
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertFalse(self.dir.exists())

    def test_configured_url_is_fetched_and_saved(self):
        rc = b.run_snapshot(year_month="2026-11", urls={"MS": "https://example.invalid/ms"},
                            snapshot_dir=self.dir, fetch=lambda u: SYNTHETIC_HTML, now_utc=NOW)
        self.assertEqual(rc, 0)
        self.assertTrue((self.dir / "2026-11_MS.json").exists())

    def test_fetch_failure_returns_1_and_saves_nothing(self):
        def boom(url):
            raise ConnectionError("offline")
        rc = b.run_snapshot(year_month="2026-11", urls={"MS": "https://example.invalid/ms"},
                            snapshot_dir=self.dir, fetch=boom)
        self.assertEqual(rc, 1)
        self.assertFalse(self.dir.exists())

    def test_invalid_year_month_is_rejected(self):
        self.assertEqual(b.run_snapshot(year_month="2026-13", urls={"MS": "x"}, snapshot_dir=self.dir), 1)
        self.assertEqual(b.run_snapshot(year_month="bad", urls={"MS": "x"}, snapshot_dir=self.dir), 1)


if __name__ == "__main__":
    unittest.main()
