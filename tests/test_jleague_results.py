"""Jリーグ: 順位表のクラブ名の重複修正と、直近(前日〜当日)の試合結果の取得・整形・重複配信防止(2026-10-12)。
通信なし(requests.get をモック。フィクスチャは実ページの抜粋)。
実行: .venv/bin/python -m unittest tests.test_jleague_results
"""
import datetime as dt
import io
import os
import sys
import unittest
from unittest import mock

from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import fetch_sports_standings as fs  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW_SUN = dt.datetime(2026, 10, 12, 7, 20, tzinfo=JST)     # 日曜の朝 → 前日(10/11)と当日(10/12)が対象
DAY_11 = dt.date(2026, 10, 11)


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()


class StandingsNameTest(unittest.TestCase):
    def test_club_name_not_duplicated(self):
        resp = mock.Mock(text=fixture("jleague_standings_excerpt.html"))
        resp.raise_for_status = lambda: None
        with mock.patch.object(fs.requests, "get", return_value=resp):
            rows = fs.fetch_jleague_standings()
        self.assertEqual(rows[0][1], "柏レイソル")                # 旧: 「柏レイソル柏」
        self.assertEqual(rows[0][2], "21")                        # 勝点の位置は従来どおり
        self.assertEqual(len(rows[0]), 11)

    def test_fallback_to_first_span_and_plain_text(self):
        td = BeautifulSoup('<td class="o-table__cell--club"><a><span>サンフレッチェ広島</span><span>広島</span></a></td>', "html.parser").td
        self.assertEqual(fs.club_name_from_cell(td), "サンフレッチェ広島")
        td2 = BeautifulSoup('<td class="o-table__cell--club">FC東京</td>', "html.parser").td
        self.assertEqual(fs.club_name_from_cell(td2), "FC東京")


class ResultParsingTest(unittest.TestCase):
    def test_targets_for_matching_and_other_dates(self):
        html = "<html><body>" + fixture("jleague_schedule_group_excerpt.html") + "</body></html>"
        t = fs.jleague_result_targets(html, {DAY_11})
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["round"], "第9節")
        self.assertTrue(t[0]["href"].endswith("/match/j1/2026/101101/"))
        self.assertEqual(fs.jleague_result_targets(html, {dt.date(2026, 10, 13)}), [])    # 試合の無い日

    def test_unfinished_matches_are_excluded(self):
        html = fixture("jleague_schedule_group_excerpt.html").replace("m-schedule--game-over", "m-schedule--scheduled")
        self.assertEqual(fs.jleague_result_targets("<html>" + html + "</html>", {DAY_11}), [])

    def test_detail_parsed_with_home_away_order(self):
        d = fs.parse_jleague_match_detail(fixture("jleague_match_detail_excerpt.html"))
        self.assertEqual((d["home"], d["away"], d["home_score"], d["away_score"]), ("ジェフユナイテッド千葉", "Ｖ・ファーレン長崎", 1, 1))

    def test_detail_without_numeric_score_is_skipped(self):
        html = fixture("jleague_match_detail_excerpt.html").replace('>1</p>', '>-</p>', 1)
        self.assertIsNone(fs.parse_jleague_match_detail(html))
        self.assertIsNone(fs.parse_jleague_match_detail("<html><body>no block</body></html>"))


class FetchFlowTest(unittest.TestCase):
    def fake_get(self, schedule_html, detail_html, calls):
        def get(url, **kw):
            calls.append(url)
            r = mock.Mock()
            r.raise_for_status = lambda: None
            r.text = schedule_html if url.endswith("/match/j1/2026/") else detail_html
            return r
        return get

    def test_game_day_fetches_details_once_and_embeds(self):
        calls = []
        with mock.patch.object(fs.requests, "get", side_effect=self.fake_get(
                "<html>" + fixture("jleague_schedule_group_excerpt.html") + "</html>", fixture("jleague_match_detail_excerpt.html"), calls)):
            results = fs.fetch_jleague_results(NOW_SUN, {})
        self.assertEqual(len(results), 1)
        self.assertEqual(len(calls), 2)                          # 一覧1回 + 試合詳細1回
        emb = fs.build_jleague_results_embed(results, NOW_SUN)
        self.assertEqual(emb["title"], "⚽ J1 試合結果（前日〜当日）")
        self.assertIn("ジェフユナイテッド千葉 **1** - **1** Ｖ・ファーレン長崎（第9節）", emb["description"])
        self.assertIn("**10/11**", emb["description"])

    def test_already_sent_matches_are_not_refetched(self):
        calls = []
        seen = {"https://www.jleague.jp/match/j1/2026/101101/": "2026-10-11T22:00:00+09:00"}
        with mock.patch.object(fs.requests, "get", side_effect=self.fake_get(
                "<html>" + fixture("jleague_schedule_group_excerpt.html") + "</html>", fixture("jleague_match_detail_excerpt.html"), calls)):
            results = fs.fetch_jleague_results(NOW_SUN, seen)
        self.assertEqual(results, [])
        self.assertEqual(len(calls), 1)                          # 一覧のみ

    def test_no_match_day_gives_no_embed(self):
        empty = "<html><body><p>nothing</p></body></html>"
        with mock.patch.object(fs.requests, "get", side_effect=self.fake_get(empty, "", [])):
            results = fs.fetch_jleague_results(NOW_SUN, {})
        self.assertEqual(results, [])
        self.assertIsNone(fs.build_jleague_results_embed(results, NOW_SUN))

    def test_list_failure_returns_none(self):
        with mock.patch.object(fs.requests, "get", side_effect=RuntimeError("offline")):
            self.assertIsNone(fs.fetch_jleague_results(NOW_SUN, {}))


class MainDeliveryTest(unittest.TestCase):
    def run_main(self, results, send_ok, state):
        sent = []
        with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_SPORTS_CULTURE": "https://discord.com/api/webhooks/1/x"}), \
             mock.patch.object(fs, "load_state", return_value=state), \
             mock.patch.object(fs, "save_state"), \
             mock.patch.object(fs, "fetch_npb_standings", return_value=None), \
             mock.patch.object(fs, "fetch_jleague_standings", return_value=None), \
             mock.patch.object(fs, "fetch_f1_top3", return_value=None), \
             mock.patch.object(fs, "fetch_motogp_top3", return_value=None), \
             mock.patch.object(fs, "fetch_jleague_results", return_value=results), \
             mock.patch.object(fs, "send_embeds_to_discord", side_effect=lambda w, e: (sent.extend(e), send_ok)[1]), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            try:
                fs.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, sent

    def results(self):
        return [{"href": "https://www.jleague.jp/match/j1/2026/101101/", "date": DAY_11, "round": "第9節",
                 "home": "ジェフユナイテッド千葉", "away": "Ｖ・ファーレン長崎", "home_score": 1, "away_score": 1}]

    def test_send_marks_results_as_seen(self):
        state = {}
        code, sent = self.run_main(self.results(), True, state)
        self.assertEqual(code, 0)
        self.assertEqual(len(sent), 1)
        self.assertIn("https://www.jleague.jp/match/j1/2026/101101/", state.get(fs.JLEAGUE_RESULTS_SEEN_KEY, {}))

    def test_failed_send_is_not_marked_so_it_is_retried(self):
        state = {}
        code, sent = self.run_main(self.results(), False, state)
        self.assertEqual(code, 1)
        self.assertNotIn("https://www.jleague.jp/match/j1/2026/101101/", state.get(fs.JLEAGUE_RESULTS_SEEN_KEY, {}))


if __name__ == "__main__":
    unittest.main()
