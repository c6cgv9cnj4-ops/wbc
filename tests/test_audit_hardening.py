"""独立監査の指摘への改修の検証(2026-10-12): 重複排除の安全化・防災のSHA256判定・順位表の名称フォールバック・アトミック書き込み。
通信なし。実行: .venv/bin/python -m unittest tests.test_audit_hardening
"""
import datetime as dt
import difflib
import glob
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import atomic_io  # noqa: E402
import fetch_news as fn  # noqa: E402
import fetch_sports_standings as fs  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=JST)
# 類似度0.7前後だが別の事故(市・数字は同じ、人物と行為が違う)
ACC_1 = "北本市で男性が軽乗用車と衝突し死亡 埼玉県警が捜査を進めている - 埼玉新聞"
ACC_2 = "北本市で女性が自転車と接触しけが 埼玉県警が捜査を進めている - 東京新聞"


class ThresholdTest(unittest.TestCase):
    def test_around_0_7_different_accidents_are_not_merged(self):
        x, y = fn.local_title_key(ACC_1), fn.local_title_key(ACC_2)
        ratio = difflib.SequenceMatcher(None, x, y).ratio()
        self.assertTrue(0.6 <= ratio < 0.85, ratio)              # 旧閾値0.6なら同一扱いされていた範囲
        self.assertFalse(fn.is_same_local_story(x, y))
        kept, dropped = fn.dedupe_local_items([{"title": ACC_1, "url": "a", "published": ""}, {"title": ACC_2, "url": "b", "published": ""}], {}, NOW)
        self.assertEqual((len(kept), dropped), (2, 0))

    def test_same_story_still_merged_at_high_similarity(self):
        a = fn.local_title_key("北本市で車が横転し炎上 運転手が死亡 - 埼玉新聞")
        b = fn.local_title_key("北本市で車が横転し炎上　運転手が死亡 - 産経ニュース")
        self.assertTrue(fn.is_same_local_story(a, b))

    def test_threshold_value(self):
        self.assertEqual(fn.LOCAL_TITLE_SIMILARITY, 0.85)


class UpdateKeywordTest(unittest.TestCase):
    CONT = "北本市で火災 続報 住宅が全焼 - 埼玉新聞"

    def test_followup_is_not_removed_when_same_as_recent(self):
        state = {fn.LOCAL_TITLE_KEY_PREFIX + fn.local_title_key(self.CONT)[:120]: (NOW - dt.timedelta(hours=1)).isoformat()}
        kept, dropped = fn.dedupe_local_items([{"title": self.CONT, "url": "u", "published": ""}], state, NOW)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_followup_pair_in_same_batch_both_kept(self):
        items = [{"title": self.CONT, "url": "u1", "published": ""}, {"title": self.CONT + " ", "url": "u2", "published": ""}]
        kept, dropped = fn.dedupe_local_items(items, {}, NOW)
        self.assertEqual((len(kept), dropped), (2, 0))

    def test_each_keyword_bypasses(self):
        for w in fn.LOCAL_UPDATE_KEYWORDS:
            self.assertTrue(fn.has_local_update_keyword(f"北本市で{w}の知らせ - 埼玉新聞"), w)
        self.assertFalse(fn.has_local_update_keyword("北本市の小学校で運動会 - 埼玉新聞"))


class BousaiHashTest(unittest.TestCase):
    FIRE = {"key": "anzn:11217F:111", "datetime": "10-11 04:37頃", "city": "北本市", "summary": "火災 北本市荒井", "url": "u"}

    def test_fingerprint_is_sha256_of_body(self):
        keys = fn._bousai_fingerprints(self.FIRE)
        hashed = [k for k in keys if k.startswith(fn.BOUSAI_BODY_PREFIX)]
        self.assertEqual(len(hashed), 1)
        self.assertEqual(len(hashed[0][len(fn.BOUSAI_BODY_PREFIX):]), 64)          # SHA256 の16進64桁

    def test_identical_body_within_24h_skipped_and_outside_shown(self):
        state = {}
        fn.mark_bousai_shown([self.FIRE], state, NOW - dt.timedelta(hours=2))
        other_id = dict(self.FIRE, key="anzn:X:9")
        self.assertEqual(fn.filter_bousai_already_shown([other_id], state, NOW)[0], [])
        self.assertEqual(len(fn.filter_bousai_already_shown([other_id], state, NOW + dt.timedelta(hours=30))[0]), 1)

    def test_similar_but_not_identical_body_is_not_suppressed(self):
        state = {}
        fn.mark_bousai_shown([self.FIRE], state, NOW)
        near = dict(self.FIRE, key="anzn:X:8", summary="火災 北本市荒井3丁目")       # 本文が違えば別事案(類似度では判定しない)
        self.assertEqual(len(fn.filter_bousai_already_shown([near], state, NOW)[0]), 1)


class SportsFallbackTest(unittest.TestCase):
    def cell(self, html):
        return BeautifulSoup(html, "html.parser").td

    def test_pc_name_preferred(self):
        self.assertEqual(fs.club_name_from_cell(self.cell('<td class="o-table__cell--club"><a><span data-media="pc">柏レイソル</span><span data-media="sp">柏</span></a></td>')), "柏レイソル")

    def test_no_pc_attribute_falls_back_without_empty_or_doubling(self):
        self.assertEqual(fs.club_name_from_cell(self.cell('<td class="o-table__cell--club"><a><span>サンフレッチェ広島</span><span>広島</span></a></td>')), "サンフレッチェ広島")
        self.assertEqual(fs.club_name_from_cell(self.cell('<td class="o-table__cell--club"><a><span data-media="sp">広島</span></a></td>')), "広島")

    def test_doubled_text_is_collapsed(self):
        self.assertEqual(fs.club_name_from_cell(self.cell('<td class="o-table__cell--club">サンフレッチェ広島サンフレッチェ広島</td>')), "サンフレッチェ広島")
        self.assertEqual(fs._collapse_doubled("柏レイソル"), "柏レイソル")
        self.assertEqual(fs.club_name_from_cell(self.cell('<td class="o-table__cell--club"></td>')), "")   # 文字が無い場合のみ空


class AtomicWriteTest(unittest.TestCase):
    def test_success_writes_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            atomic_io.write_json_atomic(p, {"a": 1}, ensure_ascii=False, indent=2)
            self.assertEqual(json.load(open(p, encoding="utf-8")), {"a": 1})
            self.assertEqual(glob.glob(os.path.join(d, ".tmp-*")), [])

    def test_failure_mid_write_keeps_original_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            atomic_io.write_json_atomic(p, {"keep": True})
            before = open(p, encoding="utf-8").read()
            with self.assertRaises(TypeError):
                atomic_io.write_json_atomic(p, {"bad": object()})          # JSON化できない値で途中失敗
            self.assertEqual(open(p, encoding="utf-8").read(), before)     # 元の内容は壊れていない
            self.assertEqual(glob.glob(os.path.join(d, ".tmp-*")), [])     # 一時ファイルも残らない

    def test_state_savers_use_atomic_write(self):
        with mock.patch.object(atomic_io, "write_json_atomic") as w, \
             mock.patch.object(fn, "STATE_PATH", "/tmp/nonexistent-state.json"):
            fn.save_seen_state({"x": 1})
        self.assertTrue(w.called)


if __name__ == "__main__":
    unittest.main()
