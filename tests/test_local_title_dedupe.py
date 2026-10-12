"""埼玉・県央ローカルニュースの重複抑止(2026-10-12): 直近配信済みの出来事と、同一配信内の類似見出しを1件にまとめる。
モックデータ(実際の配信ログに出ていた見出しの形)を使う。通信なし。
実行: .venv/bin/python -m unittest tests.test_local_title_dedupe
"""
import contextlib
import datetime as dt
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=JST)

# 同じ出来事を媒体・URLを変えて配信した例(実ログの形を再現)
SAME_A = "鴻巣市で車が横転し炎上、焼け跡から運転手の遺体発見 衝突したバイク運転の17歳高校生は頭の骨を折る重傷 - 埼玉新聞"
SAME_A2 = "鴻巣市で車が横転し炎上、焼け跡から運転手の遺体発見　衝突したバイク運転の17歳高校生は頭の骨を折る重傷 - 産経ニュース"
SAME_B = "鴻巣市の花火大会で大迫力…世界最大級の「四尺玉」、夜空彩る　2万発の花火を打ち上げ - 東京新聞"
SAME_B2 = "鴻巣市の花火大会で大迫力…世界最大級の「四尺玉」、夜空彩る 2万発の花火を打ち上げ - Infoseek"
# 別の出来事(似た言い回し・市名違い・数字違い)
OTHER_UEDA = "上尾市・桶川市7日ごろまで大雨が続く可能性 あなたの家の浸水リスク、知っていますか"
OTHER_KONOSU = "鴻巣市・北本市7日ごろまで大雨が続く可能性 あなたの家の浸水リスク、知っていますか"
QUAKE_1 = "震度2 茨城県南部 2026年10月02日 0601ごろ 地震詳細"
QUAKE_2 = "震度4 茨城県南部 2026年09月29日 0445ごろ 地震詳細"
UNRELATED = "北本市の小学校で運動会が開かれ、児童が地域の人と交流 - 埼玉新聞"


class SameStoryTest(unittest.TestCase):
    def key(self, t):
        return fn.local_title_key(t)

    def test_same_story_across_media_is_same(self):
        self.assertTrue(fn.is_same_local_story(self.key(SAME_A), self.key(SAME_A2)))
        self.assertTrue(fn.is_same_local_story(self.key(SAME_B), self.key(SAME_B2)))

    def test_different_municipality_is_different_story(self):
        self.assertFalse(fn.is_same_local_story(self.key(OTHER_UEDA), self.key(OTHER_KONOSU)))

    def test_different_numbers_are_different_story(self):
        self.assertFalse(fn.is_same_local_story(self.key(QUAKE_1), self.key(QUAKE_2)))

    def test_unrelated_and_short_titles_are_kept(self):
        self.assertFalse(fn.is_same_local_story(self.key(SAME_A), self.key(UNRELATED)))
        self.assertFalse(fn.is_same_local_story("祭り", "祭り"))           # 短すぎる見出しは比較しない

    def test_media_suffix_and_symbols_ignored(self):
        self.assertEqual(self.key("北本市で祭り - 埼玉新聞"), self.key("北本市で祭り - ＮＥＷＳ"))


class DedupeListTest(unittest.TestCase):
    def test_within_batch_similar_titles_collapse_to_first(self):
        items = [{"title": t, "url": f"https://u/{i}", "published": ""} for i, t in enumerate([SAME_A, SAME_A2, OTHER_UEDA, OTHER_KONOSU, SAME_B, SAME_B2])]
        kept, dropped = fn.dedupe_local_items(items, {}, NOW)
        self.assertEqual(dropped, 2)
        self.assertEqual([i["title"] for i in kept], [SAME_A, OTHER_UEDA, OTHER_KONOSU, SAME_B])

    def test_recent_sent_title_is_excluded_old_one_is_not(self):
        state = {fn.LOCAL_TITLE_KEY_PREFIX + fn.local_title_key(SAME_A)[:120]: (NOW - dt.timedelta(days=1)).isoformat()}
        kept, dropped = fn.dedupe_local_items([{"title": SAME_A2, "url": "u", "published": ""}], state, NOW)
        self.assertEqual((len(kept), dropped), (0, 1))
        state = {fn.LOCAL_TITLE_KEY_PREFIX + fn.local_title_key(SAME_A)[:120]: (NOW - dt.timedelta(days=5)).isoformat()}
        kept, dropped = fn.dedupe_local_items([{"title": SAME_A2, "url": "u", "published": ""}], state, NOW)
        self.assertEqual((len(kept), dropped), (1, 0))


class TwoRunsTest(unittest.TestCase):
    def run_round(self, state, titles, now=NOW, send_ok=True):
        keys_before = set(state)
        items = [{"title": t, "url": f"https://news.google.com/rss/articles/{abs(hash(t)) % 10**8}-{now:%H%M}", "published": "10/12 08:00"} for t in titles]
        with mock.patch.dict(os.environ, {"ANZN_SOURCE": "mac"}), \
             mock.patch.object(fn.mastodon_anzn, "fetch_recent_items", return_value=[]), \
             mock.patch.object(fn, "fetch_rss_items", return_value=items), \
             mock.patch.object(fn, "fetch_direct_rss_items", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()):
            msg, _ = fn.build_local_news_message(state, now)
        if msg is not None and not send_ok:
            fn._rollback_new_keys(state, keys_before)
        return msg

    def test_second_run_does_not_repeat_same_story_from_other_media(self):
        state = {}
        m1 = self.run_round(state, [SAME_A, UNRELATED])
        self.assertIn("鴻巣市で車が横転", m1)
        m2 = self.run_round(state, [SAME_A2, OTHER_UEDA], now=NOW + dt.timedelta(hours=2))
        self.assertIsNotNone(m2)
        self.assertNotIn("鴻巣市で車が横転", m2)                    # 別URL・別媒体でも再配信しない
        self.assertIn("上尾市・桶川市", m2)                        # 新しい出来事は載る

    def test_same_run_duplicates_appear_once(self):
        m = self.run_round({}, [SAME_B, SAME_B2, UNRELATED])
        self.assertEqual(m.count("四尺玉"), 1)

    def test_failed_send_rolls_back_so_next_run_shows_it(self):
        state = {}
        self.run_round(state, [SAME_A], send_ok=False)
        m2 = self.run_round(state, [SAME_A2, UNRELATED], now=NOW + dt.timedelta(hours=1))
        self.assertIn("鴻巣市で車が横転", m2)                       # 送れなかった出来事は次回に載る

    def test_keys_are_not_underscored_so_rollback_applies(self):
        state = {}
        self.run_round(state, [SAME_A])
        keys = [k for k in state if k.startswith(fn.LOCAL_TITLE_KEY_PREFIX)]
        self.assertTrue(keys)
        self.assertFalse(any(k.startswith("_") for k in keys))


if __name__ == "__main__":
    unittest.main()
