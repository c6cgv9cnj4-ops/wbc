"""スポーツ記事の配信対象外(2026-10-29)。外部通信なし(モック)。
確認: 少年野球・浦和ビッグウェイブなどが検知されること、通常の記事は検知されないこと、
      スポーツ記事は Webhook が設定されていても送信されないこと。
実行: .venv/bin/python -m unittest tests.test_sports_exclusion
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
NOW = dt.datetime(2026, 10, 29, 9, 0, tzinfo=JST)


class SportsDetectionTest(unittest.TestCase):
    def test_reported_titles_are_detected(self):
        for title in ("少年野球大会で優勝 - 埼玉新聞",
                      "浦和ビッグウェイブが勝利 - 地域紙",
                      "浦和レッズ 開幕戦 - スポーツ紙",
                      "高校サッカー 県予選 - 埼玉新聞",
                      "Jリーグ 第30節 結果 - 日刊スポーツ"):
            self.assertTrue(fn.is_sports_related(title), title)

    def test_general_news_is_not_detected(self):
        for title in ("北本市で防災訓練 - 埼玉新聞", "日銀が利上げ決定 - 日本経済新聞", "円相場 1ドル150円台 - ロイター"):
            self.assertFalse(fn.is_sports_related(title), title)


class SportsNeverSentTest(unittest.TestCase):
    def test_sports_items_not_sent_even_with_webhook(self):
        items = [{"title": "少年野球大会で優勝 - 埼玉新聞", "url": "https://ex.com/a"}]
        sent = []
        with mock.patch.object(fn, "send_to_discord", side_effect=lambda u, m: (sent.append(m) or True)), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            err = fn._handle_sports_leak(items, "https://hook/sports", NOW, "埼玉ローカルニュース")
        self.assertFalse(err)
        self.assertEqual(sent, [])
        self.assertIn("配信対象外", out.getvalue())

    def test_empty_list_is_noop(self):
        with mock.patch.object(fn, "send_to_discord") as send:
            self.assertFalse(fn._handle_sports_leak([], "https://hook/sports", NOW, "x"))
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
