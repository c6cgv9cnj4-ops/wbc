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



# ---- 第5回監査対応：フェイルクローズ・重複統合・未知部門・削除検知・main()異常系 ----
import hashlib

SYN_NAOKI_OK = """<table><tr><th>回</th><th>対象期間</th><th>受賞者</th><th>受賞作</th></tr>
<tr><td>第176回</td><td>2026年下半期</td><td>新著者</td><td>「新作テスト」</td></tr></table>"""
# 必須列「受賞作」が欠落した受賞表（構造変更を想定）
SYN_NAOKI_MISSING_COL = """<table><tr><th>回</th><th>対象期間</th><th>受賞者</th></tr>
<tr><td>第176回</td><td>2026年下半期</td><td>新著者</td></tr></table>"""
# 受賞表はあるが今回は受賞なし（公式に受賞作なし）
SYN_NAOKI_NO_WINNER = """<table><tr><th>回</th><th>対象期間</th><th>受賞者</th><th>受賞作</th></tr>
<tr><td>第176回</td><td>2026年下半期</td><td>該当作品なし</td><td>該当作品なし</td></tr></table>"""
SYN_JSA_UNKNOWN = """<div class="mw-parser-output">
<p>第79回（ 2026年 ） 長編部門 - 甲 『新作A』 未知の部門 - 乙 『不明作』 短編部門 - 丙 「短編」</p>
<p>脚注</p></div>"""


class Hardening(unittest.TestCase):
    def test_missing_required_column_is_parse_error(self):
        with self.assertRaises(cma.Anomaly) as ctx:
            cma.parse_naoki(soup(SYN_NAOKI_MISSING_COL))
        self.assertIn("PARSE_ERROR", str(ctx.exception))
        self.assertIn("受賞作", str(ctx.exception))

    def test_no_table_is_parse_error_not_empty(self):
        with self.assertRaises(cma.Anomaly) as ctx:
            cma.parse_naoki(soup(NO_TABLE_PAGE))
        self.assertIn("PARSE_ERROR", str(ctx.exception))

    def test_official_no_winner_is_empty_not_error(self):
        winners = cma.parse_naoki(soup(SYN_NAOKI_NO_WINNER))
        self.assertEqual(len(winners), 1)                      # 表は読めている（該当作品なしの行）
        self.assertTrue(winners[0]["none"])
        self.assertEqual(cma.latest_winners(winners), [])      # 最新回は受賞なし（異常ではない）

    def test_valid_table_extracts_winner(self):
        winners = cma.parse_naoki(soup(SYN_NAOKI_OK))
        self.assertEqual(winners[0]["title"], "新作テスト")
        self.assertEqual(winners[0]["kai"], 176)


class UnknownDepartment(unittest.TestCase):
    def test_unknown_department_is_never_extracted(self):
        winners = cma.parse_jsa(soup(SYN_JSA_UNKNOWN))
        self.assertEqual([w["title"] for w in winners], ["新作A"])
        self.assertNotIn("不明作", [w["title"] for w in winners])


class DuplicateAwardMerge(unittest.TestCase):
    def _item(self, award_key, kai):
        award = next(a for a in cma.AWARDS if a["key"] == award_key)
        return award, {"kai": kai, "year": 2026, "title": "共同受賞作", "author": "共同著者"}

    def test_same_book_two_awards_becomes_one_record_with_both_labels(self):
        groups = cma.merge_new_items([self._item("oyabu", 28), self._item("eikawa", 47)])
        self.assertEqual(len(groups), 1)
        labels = groups[0]["labels"]
        self.assertEqual(len(labels), 2)
        self.assertIn("第28回 大藪春彦賞", labels)
        self.assertIn("第47回 吉川英治文学新人賞", labels)

    def test_same_label_is_not_joined_twice(self):
        item = self._item("oyabu", 28)
        groups = cma.merge_new_items([item, item])
        self.assertEqual(groups[0]["labels"], ["第28回 大藪春彦賞"])

    def test_rerun_after_insert_adds_nothing(self):
        existing = [{"id": "x", "title": "共同受賞作", "author": "共同著者"}]
        self.assertTrue(all(cma.is_registered(w, existing) for _, w in [self._item("oyabu", 28)]))


class DeletionDetection(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = os.path.join(self.dir, "old.html")
        shutil.copy(HTML_PATH, self.old)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _remove_record(self, rid):
        import re as _re
        new, n = _re.subn(r'\n        \{ id:"%s",[^{}]*?read:false \},' % _re.escape(rid), "", HTML, count=1)
        if n != 1:
            new, n = _re.subn(r'\n        \{ id:"%s",.*?\n        \{ id:' % _re.escape(rid), "\n        { id:", HTML, count=1, flags=_re.S)
        return new

    def test_pure_deletion_detected_by_count(self):
        new = self._remove_record("b00")
        with self.assertRaises(cma.ValidationError):
            cma.validate_new(self.old, self._write("del.html", new), 0)

    def test_deletion_compensated_by_addition_detected_by_id(self):
        # 1件削除し2件追加（件数は old+1 と一致させる）→ 件数検査は通り、ID単位の比較で b00 の消失を検出する
        award = cma.AWARDS[1]
        ids = cma.next_ids(HTML, 2)
        base = self._remove_record("b00")
        new = cma.insert_entries(base, [
            (award, cma.make_entry(ids[0], award, {"kai": 1, "year": 2099, "title": "補填A", "author": "補填著者"})),
            (award, cma.make_entry(ids[1], award, {"kai": 2, "year": 2099, "title": "補填B", "author": "補填著者"})),
        ])
        with self.assertRaises(cma.ValidationError) as ctx:
            cma.validate_new(self.old, self._write("del2.html", new), 1)
        self.assertIn("既存レコードが変化: b00", str(ctx.exception))


class MainFailClosed(unittest.TestCase):
    """main() の異常系統合テスト。本番ファイルには触れず、一時コピーのみを対象とする。"""

    PAGES = {
        "本屋大賞": '<h2>2026年（第23回）</h2><table><tr><th>順位</th><th>受賞作</th><th>著者</th></tr>'
                    '<tr><td>1</td><td>イン・ザ・メガチャーチ</td><td>朝井リョウ</td></tr></table>',
        "直木三十五賞": SYN_NAOKI_OK,
        "山本周五郎賞": '<table><tr><th>回（年）</th><th>賞</th><th>受賞・候補者</th><th>受賞・候補作</th><th>刊行</th></tr>'
                        '<tr><td>第1回（2026年）</td><td>受賞</td><td>朝井リョウ</td><td>『イン・ザ・メガチャーチ』</td><td>x</td></tr></table>',
        "大藪春彦賞": '<table><tr><th>回（年）</th><th></th><th>受賞・候補作</th><th>著者</th><th>初刊</th></tr>'
                      '<tr><td>第1回（2026年）</td><td>受賞</td><td>『イン・ザ・メガチャーチ』</td><td>朝井リョウ</td><td>x</td></tr></table>',
        "吉川英治文学新人賞": '<table><tr><th>回（年）</th><th>賞</th><th>受賞・候補者</th><th>受賞・候補作</th><th>刊行</th></tr>'
                          '<tr><td>第1回（2026年）</td><td>受賞</td><td>朝井リョウ</td><td>『イン・ザ・メガチャーチ』</td><td>x</td></tr></table>',
        "日本推理作家協会賞": '<div class="mw-parser-output"><p>第79回（ 2026年 ） 長編部門 - 朝井リョウ 『イン・ザ・メガチャーチ』</p></div>',
    }

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "honozassi.html")
        shutil.copy(HTML_PATH, self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _sha(self):
        return hashlib.sha256(open(self.path, "rb").read()).hexdigest()

    def _fake_fetch(self, title):
        if title in self.PAGES:
            return self.PAGES[title]
        raise ConnectionError("not used in test")  # 芥川賞（参考）は省略

    def test_control_run_adds_one_record(self):
        before = self._sha()
        with mock.patch.object(cma, "fetch", side_effect=self._fake_fetch):
            rc = cma.main(["--html", self.path])
        self.assertEqual(rc, 0)
        self.assertNotEqual(self._sha(), before)
        self.assertEqual(len(cma.load_records(self.path)), len(cma.load_records(HTML_PATH)) + 1)

    def test_tampered_existing_record_leaves_file_unchanged(self):
        before = self._sha()
        original_insert = cma.insert_entries
        def tampered(html, plan):
            return original_insert(html, plan).replace('title:"イン・ザ・メガチャーチ"', 'title:"改変"', 1)
        with mock.patch.object(cma, "fetch", side_effect=self._fake_fetch), \
             mock.patch.object(cma, "insert_entries", side_effect=tampered):
            rc = cma.main(["--html", self.path])
        self.assertEqual(rc, 0)
        self.assertEqual(self._sha(), before)
        self.assertEqual(os.listdir(self.dir), ["honozassi.html"])

    def test_syntax_failure_leaves_file_unchanged(self):
        before = self._sha()
        with mock.patch.object(cma, "fetch", side_effect=self._fake_fetch), \
             mock.patch.object(cma, "js_syntax_ok", return_value=False):
            rc = cma.main(["--html", self.path])
        self.assertEqual(rc, 0)
        self.assertEqual(self._sha(), before)
        self.assertEqual(os.listdir(self.dir), ["honozassi.html"])

    def test_parse_error_exit_code_and_file_unchanged(self):
        before = self._sha()
        pages = dict(self.PAGES, **{"直木三十五賞": SYN_NAOKI_MISSING_COL})
        with mock.patch.object(cma, "fetch", side_effect=lambda t: pages[t]):
            rc = cma.main(["--html", self.path])
        self.assertEqual(rc, cma.EXIT_PARSE_ERROR)
        self.assertEqual(self._sha(), before)


class AllowlistExactMatch(unittest.TestCase):
    """部門Allowlistは丸括弧注記・類似名称・未知部門を含めて完全一致のみ採用する（恒久テスト）。"""

    @staticmethod
    def _run(segment):
        page = f'<div class="mw-parser-output"><p>第79回（ 2026年 ） {segment}</p></div>'
        return [(w["dept"], w["title"]) for w in cma.parse_jsa(soup(page))]

    def test_parenthesized_annotation_is_rejected(self):
        self.assertEqual(self._run("長編部門（試行） - 甲 『作品』"), [])

    def test_similar_names_are_rejected(self):
        self.assertEqual(self._run("長編部門A - 甲 『作品』"), [])
        self.assertEqual(self._run("長編部門の特別 - 甲 『作品』"), [])

    def test_unknown_department_is_rejected(self):
        self.assertEqual(self._run("未知の部門 - 甲 『作品』"), [])

    def test_rejected_entry_does_not_leak_into_allowed_section(self):
        # 許可部門の直後の未知部門の作品が、許可部門の作品として混入しないこと
        self.assertEqual(self._run("長編部門 - 甲 『A』 長編部門A - 乙 『B』"), [("長編部門", "A")])

    def test_both_allowed_names_pass_exactly(self):
        self.assertEqual(self._run("長編部門 - 甲 『A』"), [("長編部門", "A")])
        self.assertEqual(self._run("長編および連作短編集部門 - 甲 『A』、 乙 『B』"),
                         [("長編および連作短編集部門", "A"), ("長編および連作短編集部門", "B")])

    def test_allowlist_constant_is_exactly_two_names(self):
        self.assertEqual(cma.JSA_ALLOW, {"長編部門", "長編および連作短編集部門"})


if __name__ == "__main__":
    unittest.main()
