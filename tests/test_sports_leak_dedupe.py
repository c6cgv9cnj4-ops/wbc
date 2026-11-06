"""スポーツ記事の通知で同じ見出しを1件にまとめる(2026-10-27)。通信なし。
実行: .venv/bin/python -m unittest tests.test_sports_leak_dedupe
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 23, 9, 29, tzinfo=JST)
HEAD = "花咲徳栄・黒川と山村学園・亀田が育成指名 プロ野球ドラフト会議"


def item(title, url):
    return {"title": title, "url": url}


class SportsLeakDedupeTest(unittest.TestCase):
    def test_same_headline_from_different_media_is_one_bullet(self):
        items = [item(HEAD + " - 朝日新聞", "https://a.example/1"),
                 item(HEAD + " - 朝日新聞", "https://a.example/2"),
                 item(HEAD + " - バーチャル高校野球", "https://b.example/3")]
        msg = fn.build_sports_leak_message(items, NOW)
        self.assertEqual(msg.count(HEAD), 1)

    def test_different_headlines_are_both_kept(self):
        items = [item(HEAD + " - 朝日新聞", "https://a.example/1"),
                 item("埼玉県加須市の花咲徳栄高 黒川凌大投手 オリックス育成1位指名 - Yahoo!ニュース", "https://c.example/9")]
        msg = fn.build_sports_leak_message(items, NOW)
        self.assertIn("黒川凌大投手", msg)
        self.assertIn(HEAD, msg)

    def test_full_and_half_width_variants_are_same(self):
        items = [item("ＭＬＢ 大谷が２本塁打 - 共同通信", "https://a.example/1"),
                 item("MLB 大谷が2本塁打 - 時事通信", "https://b.example/2")]
        self.assertEqual(fn.build_sports_leak_message(items, NOW).count("大谷"), 1)

    def test_empty_gives_none(self):
        self.assertIsNone(fn.build_sports_leak_message([], NOW))


if __name__ == "__main__":
    unittest.main()
