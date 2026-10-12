"""地域ニュースの重複抑止の回帰テスト(2026-10-13)。
同一事故・別媒体(死亡表現の有無・言い回し)・続報・別事件・別地域・人数違い・配信済み履歴の期限を検証する。
見出しは実際の配信ログの形をモックにしたもの。通信なし。実行: .venv/bin/python -m unittest tests.test_local_dedupe_regression
"""
import datetime as dt
import difflib
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=JST)

# 同一事故・別媒体(死亡あり)
DEATH_NNN = "バイクと衝突…軽自動車が横転・炎上 車の“運転手”か1人死亡 埼玉・上尾市（2026年10月11日掲載） - 日テレNEWS NNN"
DEATH_LIVE = "バイクと衝突…軽自動車が横転・炎上 車の“運転手”か1人死亡 埼玉・上尾市 - ライブドアニュース"
DEATH_INFOSEEK = "バイクと衝突…軽自動車が横転・炎上 車の“運転手”か1人死亡 埼玉・上尾市｜Infoseekニュース - Infoseek"
# 同一事故・別媒体(死亡なし・言い回しが近い)
NODEATH_A = "埼玉・上尾市でバイクと衝突した軽乗用車が炎上 車内から運転手が遺体で見つかる バイクの男子高校生も重傷 - TBS"
NODEATH_B = "埼玉・上尾市でバイクと衝突した軽乗用車が炎上 車内から運転手が遺体で見つかる バイクの男子高校生（17）も重傷 - テレ朝"
# 言い回しが大きく違う同一事故(閾値0.85未満。現状は別見出し扱い)
REWORD_A = "車が横転し炎上、焼け跡から運転手の遺体発見 衝突したバイク運転の17歳高校生は頭の骨を折る重傷 - 埼玉新聞"
REWORD_B = "「車が横転して出火」軽乗用車が男子高校生の運転するバイクと衝突し炎上 車内から運転手の遺体 - 産経ニュース"
# 続報・新情報
FOLLOW_BASE = "北本市で軽乗用車とバイクが衝突し1人死亡 埼玉県警が捜査 - 埼玉新聞"
FOLLOW_ARREST = "続報 北本市で軽乗用車とバイクが衝突し1人死亡 運転していた男を逮捕 - 埼玉新聞"
FOLLOW_CONTAINER = "北本市で火災 住宅が全焼 - 埼玉新聞"
FOLLOW_FIRE_OUT = "北本市で火災 住宅が全焼 鎮火 - 埼玉新聞"
# 別事件
OTHER_TOWN = "上尾市で軽乗用車とバイクが衝突し1人死亡 埼玉県警が捜査 - 埼玉新聞"
OTHER_COUNT = "北本市で軽乗用車とバイクが衝突し2人死亡 埼玉県警が捜査 - 埼玉新聞"
OTHER_KIND = "北本市で乗用車と自転車が衝突し1人けが 埼玉県警が捜査 - 東京新聞"
# 別地域の同文
RAIN_UEDA = "上尾市・桶川市7日ごろまで大雨が続く可能性 あなたの家の浸水リスク、知っていますか - 埼玉新聞"
RAIN_KONOSU = "鴻巣市・北本市7日ごろまで大雨が続く可能性 あなたの家の浸水リスク、知っていますか - 埼玉新聞"
# 媒体名の表記だけ違う地震
QUAKE_A = "震度2 茨城県南部 2026年10月02日0601ごろ 地震詳細|ibcnニュース"
QUAKE_B = "震度2 茨城県南部 2026年10月02日0601ごろ 地震詳細|宮城のニュース"


def item(title, n=0):
    return {"title": title, "url": f"https://news.google.com/rss/articles/T{n}", "published": "10/12 08:00"}


def sent_state(title, hours_ago=1):
    return {fn.LOCAL_TITLE_KEY_PREFIX + fn.local_title_key(title): (NOW - dt.timedelta(hours=hours_ago)).isoformat()}


class SameAccidentTest(unittest.TestCase):
    def test_death_word_different_media_is_suppressed(self):
        kept, dropped = fn.dedupe_local_items([item(DEATH_LIVE)], sent_state(DEATH_NNN), NOW)
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_death_word_across_three_media_in_one_batch_keeps_one(self):
        kept, dropped = fn.dedupe_local_items([item(DEATH_NNN, 1), item(DEATH_LIVE, 2), item(DEATH_INFOSEEK, 3)], {}, NOW)
        self.assertEqual((len(kept), dropped), (1, 2))

    def test_no_death_word_close_wording_is_suppressed(self):
        ratio = difflib.SequenceMatcher(None, fn.local_title_key(NODEATH_A), fn.local_title_key(NODEATH_B)).ratio()
        self.assertGreaterEqual(ratio, fn.LOCAL_TITLE_SIMILARITY, ratio)
        kept, dropped = fn.dedupe_local_items([item(NODEATH_B)], sent_state(NODEATH_A), NOW)
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_far_wording_is_not_suppressed_by_design(self):
        # 閾値0.85未満の言い回しは別見出し扱い(誤統合を避ける既定。閾値は実ログの根拠なしに変えない)
        kept, dropped = fn.dedupe_local_items([item(REWORD_B)], sent_state(REWORD_A), NOW)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_media_site_name_after_bar_is_ignored(self):
        self.assertEqual(fn.local_title_key(QUAKE_A), fn.local_title_key(QUAKE_B))
        kept, dropped = fn.dedupe_local_items([item(QUAKE_B)], sent_state(QUAKE_A), NOW)
        self.assertEqual((len(kept), dropped), (0, 1))


class FollowUpTest(unittest.TestCase):
    def test_true_followup_with_new_word_is_kept(self):
        kept, dropped = fn.dedupe_local_items([item(FOLLOW_ARREST)], sent_state(FOLLOW_BASE), NOW)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_fire_out_after_occurrence_is_kept(self):
        kept, dropped = fn.dedupe_local_items([item(FOLLOW_FIRE_OUT)], sent_state(FOLLOW_CONTAINER), NOW)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_repeat_of_sent_followup_is_suppressed(self):
        kept, dropped = fn.dedupe_local_items([item(FOLLOW_ARREST)], sent_state(FOLLOW_ARREST), NOW)
        self.assertEqual((len(kept), dropped), (0, 1))


class DifferentEventTest(unittest.TestCase):
    def test_different_municipality_kept(self):
        kept, dropped = fn.dedupe_local_items([item(OTHER_TOWN)], sent_state(FOLLOW_BASE), NOW)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_different_victim_count_kept(self):
        kept, dropped = fn.dedupe_local_items([item(OTHER_COUNT)], sent_state(FOLLOW_BASE), NOW)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_different_accident_kind_kept(self):
        kept, dropped = fn.dedupe_local_items([item(OTHER_KIND)], sent_state(FOLLOW_BASE), NOW)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_same_wording_different_region_kept(self):
        kept, dropped = fn.dedupe_local_items([item(RAIN_KONOSU)], sent_state(RAIN_UEDA), NOW)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_different_quake_magnitude_kept(self):
        q4 = "震度4 茨城県南部 2026年09月29日0445ごろ 地震詳細|宮城のニュース"
        kept, dropped = fn.dedupe_local_items([item(q4)], sent_state(QUAKE_A), NOW)
        self.assertEqual((len(kept), dropped), (1, 0))


class HistoryWindowTest(unittest.TestCase):
    def test_sent_within_three_days_suppressed_older_kept(self):
        kept, dropped = fn.dedupe_local_items([item(DEATH_LIVE)], sent_state(DEATH_NNN, hours_ago=70), NOW)
        self.assertEqual((len(kept), dropped), (0, 1))
        kept, dropped = fn.dedupe_local_items([item(DEATH_LIVE)], sent_state(DEATH_NNN, hours_ago=24 * 5), NOW)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_history_survives_restart(self):
        # 状態はJSONに保存され、再読込後も同じ判定になる(プロセス再起動の模擬)
        import json
        import tempfile
        state = {}
        fn.mark_local_titles_sent([item(DEATH_NNN)], state, NOW)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            reloaded = json.load(open(p, encoding="utf-8"))
        kept, dropped = fn.dedupe_local_items([item(DEATH_LIVE)], reloaded, NOW + dt.timedelta(hours=2))
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_keys_are_not_truncated_so_keywords_at_the_end_still_count(self):
        long_title = "北本市で火災 " + "住宅の状況を詳しく伝える記事です" * 6 + " 続報 - 埼玉新聞"
        fn.mark_local_titles_sent([item(long_title)], (state := {}), NOW)
        self.assertIn(fn.LOCAL_TITLE_KEY_PREFIX + fn.local_title_key(long_title), state)


class MediaSuffixTest(unittest.TestCase):
    def test_media_added_numeric_tail_is_same_article(self):
        base = "空間を最大活用したCBシリーズを体感 埼玉・上尾市CB原市エクラ モデルルーム内覧会を開催 - 埼玉新聞"
        tailed = "空間を最大活用したCBシリーズを体感 埼玉・上尾市CB原市エクラ モデルルーム内覧会を開催写真画像1/1 - PR TIMES"
        kept, dropped = fn.dedupe_local_items([item(tailed)], sent_state(base), NOW)
        self.assertEqual((len(kept), dropped), (0, 1))


class BatchTest(unittest.TestCase):
    def test_unrelated_items_all_kept(self):
        titles = [OTHER_TOWN, OTHER_KIND, RAIN_UEDA, RAIN_KONOSU, QUAKE_A]
        kept, dropped = fn.dedupe_local_items([item(t, i) for i, t in enumerate(titles)], {}, NOW)
        self.assertEqual((len(kept), dropped), (len(titles), 0))


if __name__ == "__main__":
    unittest.main()
