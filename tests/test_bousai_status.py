"""防災情報: 報告種別(発生・鎮火・更新)が違えば別の情報として配信し、同一内容の再取得だけを除外する(2026-10-12)。
通信なし。実行: .venv/bin/python -m unittest tests.test_bousai_status
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402
import mastodon_anzn as ma  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 11, 12, 0, tzinfo=JST)


def post(pid, text):
    return {"id": pid, "content": f"<p>{text}</p>", "created_at": "2026-10-11T05:00:00+09:00"}


OCCUR = "#北本市 火災 災害発生情報 10月11日 04:37頃、北本市荒井地内で火災が発生しました。"
EXTING = "#北本市 火災 災害発生情報（鎮火） 10月11日 04:37頃、北本市荒井地内の火災は鎮火しました。"
UPDATE = "#北本市 火災 災害発生情報（更新） 10月11日 04:37頃、北本市荒井地内の火災について続報があります。"


class ParseStatusTest(unittest.TestCase):
    def test_status_detected_per_report(self):
        self.assertEqual(ma.parse_post(post("1", OCCUR))["status"], "発生")
        self.assertEqual(ma.parse_post(post("2", EXTING))["status"], "鎮火")
        self.assertEqual(ma.parse_post(post("3", UPDATE))["status"], "更新")

    def test_same_time_and_place_but_different_status(self):
        a, b = ma.parse_post(post("1", OCCUR)), ma.parse_post(post("2", EXTING))
        self.assertEqual((a["datetime"], a["city"], a["summary"]), (b["datetime"], b["city"], b["summary"]))
        self.assertNotEqual(a["status"], b["status"])


class FilterStatusTest(unittest.TestCase):
    def test_extinguish_report_after_occurrence_is_delivered(self):
        state = {}
        occur = ma.parse_post(post("1", OCCUR))
        fn.mark_bousai_shown([occur], state, NOW)                       # 発生報を送信済み
        extinguish = ma.parse_post(post("2", EXTING))
        new, hidden = fn.filter_bousai_already_shown([extinguish], state, NOW)
        self.assertEqual((len(new), hidden), (1, 0))                    # 鎮火報は除外されず配信対象

    def test_same_content_refetch_is_excluded(self):
        state = {}
        first = ma.parse_post(post("1", OCCUR))
        fn.mark_bousai_shown([first], state, NOW)
        refetch = ma.parse_post(post("9", OCCUR))                       # 同一内容・別の投稿ID
        new, hidden = fn.filter_bousai_already_shown([refetch], state, NOW)
        self.assertEqual((len(new), hidden), (0, 1))

    def test_update_report_is_delivered_after_occurrence(self):
        state = {}
        fn.mark_bousai_shown([ma.parse_post(post("1", OCCUR))], state, NOW)
        new, _ = fn.filter_bousai_already_shown([ma.parse_post(post("3", UPDATE))], state, NOW)
        self.assertEqual(len(new), 1)

    def test_direct_path_items_without_status_field(self):
        state = {}
        occur = {"datetime": "10-11 04:37頃", "city": "北本市", "summary": "火災 北本市荒井", "url": "u1"}
        extinguish = {"datetime": "10-11 04:37頃", "city": "北本市", "summary": "火災 北本市荒井 鎮火", "url": "u2"}
        fn.mark_bousai_shown([occur], state, NOW)
        new, hidden = fn.filter_bousai_already_shown([extinguish], state, NOW)
        self.assertEqual((len(new), hidden), (1, 0))

    def test_identical_direct_item_is_excluded(self):
        state = {}
        occur = {"datetime": "10-11 04:37頃", "city": "北本市", "summary": "火災 北本市荒井", "url": "u1"}
        fn.mark_bousai_shown([occur], state, NOW)
        new, hidden = fn.filter_bousai_already_shown([dict(occur, url="u9")], state, NOW)
        self.assertEqual((len(new), hidden), (0, 1))


if __name__ == "__main__":
    unittest.main()
