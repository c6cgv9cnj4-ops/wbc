"""scripts/check_monthly_awards.py の単体テスト（ネットワーク非依存）。"""
import importlib.util
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cma", ROOT / "scripts" / "check_monthly_awards.py")
cma = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cma)
HTML = (ROOT / "honozassi.html").read_text(encoding="utf-8")


class Normalize(unittest.TestCase):
    def test_title_variants_match(self):
        self.assertEqual(cma.norm("『けんぐゎい』"), cma.norm("けんぐゎい"))
        self.assertEqual(cma.norm("汝、星のごとく"), cma.norm("汝 星のごとく"))

    def test_clean_author_strips_notes(self):
        self.assertEqual(cma.clean_author("メリッサ・ダ・コスタ （ 英語版 ）"), "メリッサ・ダ・コスタ")


class Registration(unittest.TestCase):
    def test_existing_work_is_registered(self):
        existing = cma.parse_existing(HTML)
        self.assertTrue(cma.is_registered({"title": "けんぐゎい", "author": "朝倉かすみ"}, existing))

    def test_same_title_other_author_is_new(self):
        existing = cma.parse_existing(HTML)
        self.assertFalse(cma.is_registered({"title": "けんぐゎい", "author": "別人"}, existing))


class Insertion(unittest.TestCase):
    def test_insert_adds_only_and_validates(self):
        award = cma.AWARDS[1]  # 直木賞
        ids = cma.next_ids(HTML, 1)
        w = {"kai": 999, "year": 2099, "title": "テスト作品", "author": "テスト著者"}
        plan = [(award, cma.make_entry(ids[0], award, w))]
        new = cma.insert_entries(HTML, plan)
        self.assertIsNone(cma.validate(HTML, new, 1))

    def test_validate_rejects_removal(self):
        new = HTML.replace('title:"けんぐゎい"', 'title:"改変"', 1)
        self.assertIsNotNone(cma.validate(HTML, new, 0))

    def test_validate_rejects_duplicate_id(self):
        new = HTML.replace('{ id:"b144"', '{ id:"b001"', 1)
        self.assertIsNotNone(cma.validate(HTML, new, 0))

    def test_validate_rejects_broken_js(self):
        new = HTML.replace("</body>", "<script>function (</script></body>", 1)
        self.assertIsNotNone(cma.validate(HTML, new, 0))


class Anomaly(unittest.TestCase):
    def test_empty_parse_is_anomaly(self):
        with self.assertRaises(cma.Anomaly):
            cma.latest_winners([])

    def test_fetch_error_exit_code(self):
        with mock.patch.object(cma, "fetch", side_effect=ConnectionError("down")):
            with mock.patch.object(sys, "argv", ["x", "--html", str(ROOT / "honozassi.html"), "--dry-run"]):
                self.assertEqual(cma.main(), cma.EXIT_ANOMALY)


if __name__ == "__main__":
    unittest.main()
