"""地域ニュース(#webhook_local)の重複配信修正(2026-10-13)のテスト。通信なし・Gemini不使用。
実行: .venv/bin/python -m unittest tests.test_local_dedupe_fixes
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 13, 13, 30, tzinfo=JST)
ACCIDENT_AGO = "【事故】バイクと衝突 車の“運転手”か1人死亡 軽自動車が横転･炎上 埼玉･上尾市 - YouTube"
ACCIDENT_IMG = "【画像】バイクと衝突し車炎上 車の運転手が死亡、バイクの男子高校生は重傷 埼玉・上尾市 - ライブドアニュース"
ACCIDENT_DATED = "バイクと衝突軽自動車が横転炎上車の運転手か1人死亡埼玉上尾市2026年10月11日掲載"


def item(title, url):
    return {"title": title, "url": url, "published": ""}


def recent_state(*titles):
    return {fn.LOCAL_TITLE_KEY_PREFIX + fn.local_title_key(t): NOW.isoformat() for t in titles}


class TitleVariantTest(unittest.TestCase):
    def test_lead_tags_and_date_suffix_ignored(self):
        self.assertEqual(fn.local_title_key(ACCIDENT_IMG), fn.local_title_key("バイクと衝突し車炎上 車の運転手が死亡、バイクの男子高校生は重傷 埼玉・上尾市"))
        self.assertNotIn("掲載", fn.local_title_key(ACCIDENT_DATED))
        self.assertFalse(fn.local_title_key(ACCIDENT_AGO).startswith("事故"))

    def test_media_suffix_ignored(self):
        self.assertEqual(fn.local_title_key("北本市の運動会 - 埼玉新聞"), fn.local_title_key("北本市の運動会 - 産経ニュース"))

    def test_full_width_space_and_dash_variant(self):
        a = fn.local_title_key("鴻巣市で火災　けが人なし")
        b = fn.local_title_key("鴻巣市で火災 けが人なし")
        self.assertEqual(a, b)


class RepeatDeliveryTest(unittest.TestCase):
    def test_same_accident_repeat_with_death_word_is_dropped(self):
        # 修正前は「死亡」を含むだけで通過し、同じ事故が繰り返し配信されていた。
        # 既出が複数表記で記録されている実運用の状態(10/12〜10/13の送信分)で検証する
        state = recent_state(ACCIDENT_DATED, ACCIDENT_AGO, ACCIDENT_IMG)
        kept, dropped = fn.dedupe_local_items([item(ACCIDENT_AGO, "u1"), item(ACCIDENT_IMG, "u2")], state, NOW)
        self.assertEqual((len(kept), dropped), (0, 2))

    def test_single_prior_variant_is_known_limit(self):
        # 既出が1表記だけのとき、語の並びが大きく違う別表記は類似度判定で拾えず通す(誤除外を避けるための既知の限界)
        state = recent_state(ACCIDENT_DATED)
        kept, _ = fn.dedupe_local_items([item(ACCIDENT_IMG, "u9")], state, NOW)
        self.assertEqual(len(kept), 1)

    def test_followup_with_continuation_word_is_kept(self):
        state = recent_state(ACCIDENT_DATED)
        kept, _ = fn.dedupe_local_items([item("続報 " + ACCIDENT_AGO, "u3")], state, NOW)
        self.assertEqual(len(kept), 1)

    def test_new_info_word_on_same_story_is_kept(self):
        # 既出の出来事に「容疑者」という新しい情報が加われば、更新として配信する
        state = recent_state("上尾市でバイクと車が衝突 運転手が死亡")
        kept, _ = fn.dedupe_local_items([item("上尾市でバイクと車が衝突 運転手が死亡 容疑者を逮捕", "u4")], state, NOW)
        self.assertEqual(len(kept), 1)

    def test_already_known_word_does_not_bypass(self):
        state = recent_state("上尾市でバイクと車が衝突 運転手が死亡")
        kept, dropped = fn.dedupe_local_items([item("上尾市でバイクと車が衝突 運転手が死亡", "u5")], state, NOW)
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_different_accidents_not_merged(self):
        # 新しい重要ニュースを誤って除外しない(別の事故は両方残す)
        a = "北本市で男性が軽乗用車と衝突し死亡 埼玉県警が捜査を進めている - 埼玉新聞"
        b = "北本市で女性が自転車と接触しけが 埼玉県警が捜査を進めている - 東京新聞"
        kept, dropped = fn.dedupe_local_items([item(a, "a"), item(b, "b")], {}, NOW)
        self.assertEqual((len(kept), dropped), (2, 0))

    def test_recent_window_expired_is_not_suppressed(self):
        old = (NOW - dt.timedelta(days=4)).isoformat()
        state = {fn.LOCAL_TITLE_KEY_PREFIX + fn.local_title_key(ACCIDENT_DATED): old}
        kept, _ = fn.dedupe_local_items([item(ACCIDENT_AGO, "u6")], state, NOW)
        self.assertEqual(len(kept), 1)


class FollowUpTest(unittest.TestCase):
    BASE = "北本市で車が横転し炎上 運転手が死亡"

    def _state(self):
        return recent_state(self.BASE)

    def test_near_duplicate_without_new_info_is_dropped(self):
        kept, dropped = fn.dedupe_local_items([item(self.BASE + " - 産経ニュース", "d1")], self._state(), NOW)
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_followup_with_new_info_word_is_kept(self):
        kept, _ = fn.dedupe_local_items([item(self.BASE + " 容疑者を逮捕", "f1")], self._state(), NOW)
        self.assertEqual(len(kept), 1)

    def test_followup_without_keyword_but_added_info_is_kept(self):
        # 語彙に無い追加情報(「原因は居眠り」)も、既出より4文字以上増えれば通す(誤除外の回帰を防ぐ)
        kept, _ = fn.dedupe_local_items([item(self.BASE + " 原因は居眠り", "f2")], self._state(), NOW)
        self.assertEqual(len(kept), 1)

    def test_added_chars_measure(self):
        self.assertEqual(fn._added_chars("abcd", "abcd"), 0)
        self.assertGreaterEqual(fn._added_chars("abcd原因は居眠り", "abcd"), fn.LOCAL_NEW_INFO_CHARS)


class SendFailureTest(unittest.TestCase):
    def test_rollback_removes_local_title_keys_so_next_run_resends(self):
        before = {"https://old.example/a": NOW.isoformat()}
        state = dict(before)
        state[fn.LOCAL_TITLE_KEY_PREFIX + "x"] = NOW.isoformat()
        state["https://new.example/b"] = NOW.isoformat()
        state["_alert:x"] = NOW.isoformat()              # 管理キーは残す
        removed = fn._rollback_new_keys(state, set(before) | {"_alert:x"})
        self.assertEqual(removed, 2)
        self.assertNotIn(fn.LOCAL_TITLE_KEY_PREFIX + "x", state)
        self.assertNotIn("https://new.example/b", state)
        self.assertIn("_alert:x", state)


if __name__ == "__main__":
    unittest.main()
