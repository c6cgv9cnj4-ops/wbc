"""scripts/check_monthly_awards.py の単体テスト（ネットワーク非依存）。"""
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cma", ROOT / "scripts" / "check_monthly_awards.py")
cma = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cma)
HTML_PATH = ROOT / "honozassi.html"
HTML = HTML_PATH.read_text(encoding="utf-8")


def soup(s):
    return BeautifulSoup(s, "html.parser")


JSA_PAGE = """<div class="mw-parser-output">
<p>第79回（ 2026年 ） 長編および連作短編集部門 - 甲 『 新作A 』 短編部門 - 乙 「短編」 評論・研究部門 - 丙 『評論書』 翻訳部門（試行） - 丁 （著）『翻訳書』 長編部門 - 戊 『新作B』</p>
<p>第78回（ 2025年 ） 長編部門 - 己 『旧作』</p>
<p>脚注 注釈</p></div>"""

NO_TABLE_PAGE = "<div><p>受賞の表はありません</p></div>"


class Normalize(unittest.TestCase):
    def test_title_variants_match(self):
        self.assertEqual(cma.norm("『けんぐゎい』"), cma.norm("けんぐゎい"))

    def test_clean_author_strips_notes(self):
        self.assertEqual(cma.clean_author("メリッサ・ダ・コスタ （ 英語版 ）"), "メリッサ・ダ・コスタ")


class Allowlist(unittest.TestCase):
    def test_only_exact_allowed_departments_are_kept(self):
        winners = cma.parse_jsa(soup(JSA_PAGE))
        depts = {w["dept"] for w in winners}
        self.assertEqual(depts, {"長編および連作短編集部門", "長編部門"})

    def test_short_and_translation_excluded_from_latest(self):
        top = cma.latest_winners(cma.parse_jsa(soup(JSA_PAGE)))
        self.assertEqual([w["title"] for w in top], ["新作A", "新作B"])
        self.assertTrue(all(w["kai"] == 79 for w in top))

    def test_no_marker_is_anomaly(self):
        with self.assertRaises(cma.Anomaly):
            cma.parse_jsa(soup(NO_TABLE_PAGE))


class AnomalyVsNoAward(unittest.TestCase):
    def test_missing_table_is_anomaly(self):
        with self.assertRaises(cma.Anomaly):
            cma.parse_naoki(soup(NO_TABLE_PAGE))

    def test_empty_latest_is_no_award_not_anomaly(self):
        self.assertEqual(cma.latest_winners([]), [])

    def test_fetch_error_gives_anomaly_exit(self):
        with mock.patch.object(cma, "fetch", side_effect=ConnectionError("down")):
            self.assertEqual(cma.main(["--html", str(HTML_PATH), "--dry-run"]), cma.EXIT_ANOMALY)


class Registration(unittest.TestCase):
    def test_existing_work_is_registered(self):
        self.assertTrue(cma.is_registered({"title": "けんぐゎい", "author": "朝倉かすみ"}, cma.parse_existing(HTML)))

    def test_same_title_other_author_is_new(self):
        self.assertFalse(cma.is_registered({"title": "けんぐゎい", "author": "別人"}, cma.parse_existing(HTML)))


class ExactRecordValidation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = os.path.join(self.dir, "old.html")
        shutil.copy(HTML_PATH, self.old)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, text):
        path = os.path.join(self.dir, "new.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _add_one(self):
        award = cma.AWARDS[1]
        bid = cma.next_ids(HTML, 1)[0]
        w = {"kai": 999, "year": 2099, "title": "テスト作品", "author": "テスト著者"}
        return cma.insert_entries(HTML, [(award, cma.make_entry(bid, award, w))]), bid

    def test_addition_only_passes_and_returns_new_id(self):
        new_html, bid = self._add_one()
        added = cma.validate_new(self.old, self._write(new_html), 1)
        self.assertEqual(added, [bid])

    def test_changed_existing_title_fails(self):
        new_html, _ = self._add_one()
        bad = new_html.replace('title:"けんぐゎい"', 'title:"改変"', 1)
        with self.assertRaises(cma.ValidationError):
            cma.validate_new(self.old, self._write(bad), 1)

    def test_removed_record_fails(self):
        new_html, _ = self._add_one()
        with self.assertRaises(cma.ValidationError):
            cma.validate_new(self.old, self._write(new_html.replace('{ id:"b144", genre:"naoki"', '{ id:"zz", genre:"naoki"', 1)), 1)

    def test_duplicate_id_fails(self):
        new_html, bid = self._add_one()
        bad = new_html.replace(f'{{ id:"{bid}"', '{ id:"b00"', 1)
        with self.assertRaises(cma.ValidationError):
            cma.validate_new(self.old, self._write(bad), 1)

    def test_broken_js_fails_syntax_check(self):
        new_html, _ = self._add_one()
        self.assertFalse(cma.js_syntax_ok(new_html.replace("    function toggleSelect(id) {", "    function toggleSelect(id) { (", 1)))
        self.assertTrue(cma.js_syntax_ok(HTML))


class Ids(unittest.TestCase):
    def test_next_ids_follow_max(self):
        self.assertEqual(cma.next_ids(HTML, 2), ["b145", "b146"])  # b144 は直木賞の既存


class AtomicWrite(unittest.TestCase):
    def test_replace_writes_and_leaves_no_temp(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "honozassi.html")
            with open(p, "w", encoding="utf-8") as f:
                f.write("old")
            cma.atomic_replace(p, "new")
            self.assertEqual(open(p, encoding="utf-8").read(), "new")
            self.assertEqual(os.listdir(d), ["honozassi.html"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_failure_keeps_original_and_removes_temp(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "honozassi.html")
            with open(p, "w", encoding="utf-8") as f:
                f.write("old")
            with mock.patch.object(cma.os, "replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    cma.atomic_replace(p, "new")
            self.assertEqual(open(p, encoding="utf-8").read(), "old")
            self.assertEqual(os.listdir(d), ["honozassi.html"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class StepSummary(unittest.TestCase):
    def test_no_env_no_output(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
            cma.write_step_summary(["x"])  # 例外・出力なし

    def test_env_writes_markdown(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "summary.md")
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": path}):
                cma.write_step_summary(["### 参考情報: 芥川賞", "- 第1回"])
            self.assertIn("参考情報: 芥川賞", open(path, encoding="utf-8").read())
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
