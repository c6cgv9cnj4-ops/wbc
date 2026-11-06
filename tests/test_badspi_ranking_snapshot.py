# -*- coding: utf-8 -*-
"""scripts/badspi_ranking_snapshot.py の単体テスト（ネットワーク不要）。

注意（重要）：HTMLフィクスチャは【合成データ】であり、badspi.jp の実HTML構造ではない。
ここでの合格は「想定した構造を正しく解析し、不完全データを正常版として扱わない」ことの確認にすぎない。
実ページでの解析・文字コード・総件数の表記は未検証であり、実URLで確認するまで信頼しないこと。
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import requests  # noqa: E402
import badspi_ranking_snapshot as b  # noqa: E402

import datetime as dt  # noqa: E402

NOW = dt.datetime(2026, 11, 6, 21, 5, tzinfo=dt.timezone.utc)

# 合成フィクスチャ（3行・総件数3と記載）
SYNTHETIC_HTML = """
<html><head><title>テスト用ランキング（11月1日付）</title></head>
<body><p>全3件</p><table>
<tr><td>1</td><td>日本</td><td>合成 太郎</td><td>1,200</td><td>5</td></tr>
<tr><td>2</td><td>韓国</td><td>Synthetic B</td><td>1,100</td><td>4</td></tr>
<tr><td>3</td><td>日本</td><td>合成 次郎</td><td>980</td><td>6</td></tr>
</table></body></html>
"""
# 総件数の記載なし（完全性を確認できない）
NO_TOTAL_HTML = SYNTHETIC_HTML.replace("<p>全3件</p>", "")
# 総件数5と記載だが3行しかない（切れた取得を想定）
TRUNCATED_HTML = SYNTHETIC_HTML.replace("全3件", "全5件")
# 順位に欠番（1,2,5）
GAP_HTML = SYNTHETIC_HTML.replace("<td>3</td>", "<td>5</td>")


def _snap(html=SYNTHETIC_HTML, month=11, code="MS"):
    return b.build_snapshot(html, year=2026, month=month, discipline_code=code,
                            source_url="https://example.invalid/ranking", fetched_at=NOW.isoformat())


class ParseTest(unittest.TestCase):
    def test_basis_month_day_is_read_from_title(self):
        self.assertEqual(b.parse_basis_month_day(b.html_to_lines(SYNTHETIC_HTML)), (11, 1))

    def test_rows_are_parsed_as_five_line_groups(self):
        rows = b.parse_rows(b.html_to_lines(SYNTHETIC_HTML))
        self.assertEqual([r["rank"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0]["points"], 1200)
        self.assertEqual(rows[0]["name"], "合成 太郎")
        self.assertEqual(rows[1]["country"], "韓国")

    def test_missing_basis_date_is_parse_error(self):
        with self.assertRaises(b.ParseError):
            b.parse_basis_month_day(["no date here"])

    def test_total_is_read_only_when_stated(self):
        self.assertEqual(b.parse_total(b.html_to_lines(SYNTHETIC_HTML)), 3)
        self.assertIsNone(b.parse_total(b.html_to_lines(NO_TOTAL_HTML)))

    def test_year_is_never_inferred_from_page(self):
        # 年は呼び出し側の指定のみ。ページに年が無くても、指定年で日付を作る（now().year は使わない）
        snap = b.build_snapshot(SYNTHETIC_HTML, year=2031, month=11, discipline_code="MS",
                                source_url="x", fetched_at=NOW.isoformat())
        self.assertEqual(snap["basis_date"], "2031-11-01")


class DecodeTest(unittest.TestCase):
    def test_declared_utf8_is_decoded(self):
        self.assertEqual(b.decode_body("合成".encode("utf-8"), "utf-8"), "合成")

    def test_undeclared_cp932_is_detected_after_utf8_fails(self):
        raw = "合成 太郎".encode("cp932")
        self.assertEqual(b.decode_body(raw, None), "合成 太郎")

    def test_undeclared_utf8_is_decoded_first(self):
        self.assertEqual(b.decode_body("日本語".encode("utf-8"), None), "日本語")

    def test_wrong_declared_charset_falls_back_instead_of_mojibake(self):
        self.assertEqual(b.decode_body("合成".encode("utf-8"), "iso-8859-1"), "合成")


class CompletenessTest(unittest.TestCase):
    def test_complete_only_when_total_matches_rows_and_ranks_contiguous(self):
        snap = _snap()
        self.assertTrue(snap["complete"])
        self.assertIsNone(snap["incomplete_reason"])
        self.assertEqual(snap["total_found"], 3)
        self.assertFalse(snap["verified_against_live_html"], "実HTML未検証であることを記録する")

    def test_contiguous_ranks_alone_are_not_complete_without_total(self):
        snap = _snap(html=NO_TOTAL_HTML)
        self.assertFalse(snap["complete"])
        self.assertIn("総件数が確認できない", snap["incomplete_reason"])

    def test_truncated_page_with_contiguous_ranks_is_incomplete(self):
        snap = _snap(html=TRUNCATED_HTML)
        self.assertTrue(snap["rank_continuous"])
        self.assertFalse(snap["complete"])
        self.assertIn("不一致", snap["incomplete_reason"])

    def test_rank_gap_is_incomplete(self):
        snap = _snap(html=GAP_HTML)
        self.assertFalse(snap["complete"])
        self.assertIn("欠番", snap["incomplete_reason"])

    def test_basis_month_mismatch_is_rejected(self):
        with self.assertRaises(b.ParseError):
            _snap(month=10)

    def test_unknown_discipline_code_is_rejected(self):
        with self.assertRaises(b.ParseError):
            _snap(code="ZZ")


class SaveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "badspi_ranking"

    def tearDown(self):
        self._tmp.cleanup()

    def test_complete_snapshot_is_saved_once_and_not_overwritten(self):
        self.assertEqual(b.save_snapshot(self.dir, _snap()), "saved")
        self.assertEqual(b.save_snapshot(self.dir, _snap()), "exists")
        self.assertFalse((self.dir / "2026-11_MS.incomplete.json").exists())

    def test_incomplete_snapshot_goes_to_separate_file(self):
        self.assertEqual(b.save_snapshot(self.dir, _snap(html=TRUNCATED_HTML)), "incomplete_saved")
        self.assertTrue((self.dir / "2026-11_MS.incomplete.json").exists())
        self.assertFalse((self.dir / "2026-11_MS.json").exists())

    def test_incomplete_never_replaces_existing_complete_snapshot(self):
        b.save_snapshot(self.dir, _snap())
        before = (self.dir / "2026-11_MS.json").read_text(encoding="utf-8")
        b.save_snapshot(self.dir, _snap(html=TRUNCATED_HTML))
        self.assertEqual((self.dir / "2026-11_MS.json").read_text(encoding="utf-8"), before)

    def test_complete_snapshot_does_not_replace_incomplete_file_implicitly(self):
        b.save_snapshot(self.dir, _snap(html=GAP_HTML))
        self.assertEqual(b.save_snapshot(self.dir, _snap()), "saved")
        self.assertTrue((self.dir / "2026-11_MS.incomplete.json").exists(), "不完全版は残す")


class RunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "badspi_ranking"

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_urls_warns_and_fetches_nothing(self):
        calls = []
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = b.run_snapshot(year_month="2026-11", urls={"MS": None, "WS": ""},
                                snapshot_dir=self.dir, fetch=lambda u: calls.append(u) or "")
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("::warning::", buf.getvalue())
        self.assertFalse(self.dir.exists())

    def test_configured_url_is_fetched_and_saved(self):
        rc = b.run_snapshot(year_month="2026-11", urls={"MS": "https://example.invalid/ms"},
                            snapshot_dir=self.dir, fetch=lambda u: SYNTHETIC_HTML, now_utc=NOW)
        self.assertEqual(rc, 0)
        saved = json.loads((self.dir / "2026-11_MS.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["row_count"], 3)

    def test_fetch_failure_returns_1_and_writes_nothing(self):
        def boom(url):
            raise ConnectionError("offline")
        rc = b.run_snapshot(year_month="2026-11", urls={"MS": "https://example.invalid/ms"},
                            snapshot_dir=self.dir, fetch=boom)
        self.assertEqual(rc, 1)
        self.assertFalse(self.dir.exists())

    def test_http_error_is_reported_as_failure(self):
        resp = SimpleNamespace(status_code=500, headers={}, content=b"")

        def raise_status():
            raise requests.HTTPError("500")
        resp.raise_for_status = raise_status
        with mock.patch.object(b.requests, "get", return_value=resp):
            rc = b.run_snapshot(year_month="2026-11", urls={"MS": "https://example.invalid/ms"},
                                snapshot_dir=self.dir)
        self.assertEqual(rc, 1)
        self.assertFalse(self.dir.exists())

    def test_fetch_html_uses_declared_charset_from_header(self):
        resp = SimpleNamespace(content="合成".encode("utf-8"),
                               headers={"Content-Type": "text/html; charset=utf-8"},
                               raise_for_status=lambda: None)
        with mock.patch.object(b.requests, "get", return_value=resp):
            self.assertEqual(b.fetch_html("https://example.invalid/x"), "合成")

    def test_invalid_year_month_is_rejected(self):
        self.assertEqual(b.run_snapshot(year_month="2026-13", urls={"MS": "x"}, snapshot_dir=self.dir), 1)
        self.assertEqual(b.run_snapshot(year_month="bad", urls={"MS": "x"}, snapshot_dir=self.dir), 1)


if __name__ == "__main__":
    unittest.main()
