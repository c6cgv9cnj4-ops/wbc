"""バドミントン大会結果の表示(fetch_badminton_alerts.py)のテスト。通信は使わない。
実行: .venv/bin/python -m unittest tests.test_badminton_result_display
"""
import os
import sys
import unittest
from unittest import mock

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_badminton_alerts as fb  # noqa: E402

# 実データ(NBA公式 タイオープン2026 結果ページ)のDOMと同じ構造
SAMPLE_HTML = """
<li class="v-tournament-info__match-item">
 <div class="v-tournament-info__match-head">混合ダブルス 1回戦</div>
 <div class="v-tournament-info__match-body">
  <div class="v-tournament-info__match-left">
   <div class="v-tournament-info__match-player v-tournament-info__match-player-win">
    <span class="v-tournament-info__match-name">古賀 輝</span><span class="v-tournament-info__match-team">(ジェイテクト)</span></div>
   <div class="v-tournament-info__match-player v-tournament-info__match-player-win">
    <span class="v-tournament-info__match-name">齋藤 夏</span><span class="v-tournament-info__match-team">(PLENTY GLOBAL LINX)</span></div>
  </div>
  <div class="v-tournament-info__match-center">
   <div class="v-tournament-info__match-score"><span class="v-tournament-info__match-score-num">21</span><span class="v-tournament-info__match-score-game">GAME1</span><span class="v-tournament-info__match-score-num">19</span></div>
   <div class="v-tournament-info__match-score"><span class="v-tournament-info__match-score-num">21</span><span class="v-tournament-info__match-score-game">GAME2</span><span class="v-tournament-info__match-score-num">15</span></div>
  </div>
  <div class="v-tournament-info__match-right">
   <div class="v-tournament-info__match-player"><span class="v-tournament-info__match-name">HE Zhi Wei</span><span class="v-tournament-info__match-team">(チャイニーズ・タイペイ)</span></div>
   <div class="v-tournament-info__match-player"><span class="v-tournament-info__match-name">SUN Liang Ching</span><span class="v-tournament-info__match-team">(チャイニーズ・タイペイ)</span></div>
  </div>
 </div>
</li>"""

BY_CAT = {
    "male_singles": {"西本拳太": 27, "田中湧士": 18, "TOK:JUNIOR POPOV TOMA": 15},
    "male_doubles": {"保木卓朗": 9, "小林優吾": 9, "LIANGWEIKENG": 3, "TOK:KENG LIANG WEI": 3},
    "mixed_doubles": {"古賀輝": 20},
}


def pm(event, left, right, lteam, rteam, games, left_win, lteams=None, rteams=None):
    return {"round_event": event, "left_names": left, "right_names": right, "left_team": lteam, "right_team": rteam,
            "left_teams": lteams or [lteam] * len(left), "right_teams": rteams or [rteam] * len(right),
            "games": games, "left_win": left_win, "right_win": not left_win, "score_text": fb.format_match_score(games)}


def result(matches, by=BY_CAT):
    jm = [{"view": fb.build_match_view(m, by), "round_event": m["round_event"], "left": "x", "right": "y",
           "left_win": m["left_win"], "right_win": m["right_win"], "score_text": m["score_text"]} for m in matches]
    return {"tournament": "マレーシア・オープン 2026", "update_date": "2026年10月8日", "url": "https://example.com/r/1", "japan_matches": jm}


class RankLookupTest(unittest.TestCase):
    def test_exact_and_word_order(self):
        idx = BY_CAT["male_singles"]
        self.assertEqual(fb.lookup_category_rank("西本 拳太", idx), 27)
        self.assertEqual(fb.lookup_category_rank("POPOV Toma Junior", idx), 15)    # 欧文の語順違いも同一人物として照合
        self.assertIsNone(fb.lookup_category_rank("POPOV", idx))                  # 部分一致の推測はしない
        self.assertIsNone(fb.lookup_category_rank("LAI Victor", idx))

    def test_ambiguous_token_key_is_not_used(self):
        idx = {}
        fb._add_category_index(idx, "", "Wei LI", 5)
        fb._add_category_index(idx, "", "LI Wei", 40)        # 同じ語の組で順位が割れる=別人かもしれない
        self.assertIsNone(idx["TOK:LI WEI"])                      # 語順違いの照合には使わない
        self.assertIsNone(fb.lookup_category_rank("Wei  Li", {"TOK:LI WEI": None}))

    def test_pair_rank_only_when_unambiguous(self):
        self.assertEqual(fb.side_world_rank(["保木 卓朗", "小林 優吾"], "男子ダブルス", BY_CAT), 9)
        self.assertEqual(fb.side_world_rank(["LIANG Wei Keng", "WANG Chang"], "男子ダブルス", BY_CAT), 3)   # 片方だけ載っていても採用
        by = {"male_doubles": {"A": 3, "B": 7}}
        self.assertIsNone(fb.side_world_rank(["A", "B"], "男子ダブルス", by))                              # 割れる→「—」
        self.assertIsNone(fb.side_world_rank(["西本 拳太"], "男子ダブルス", BY_CAT))                       # 別種目の順位は使わない
        self.assertIsNone(fb.side_world_rank(["西本 拳太"], "不明な種目", BY_CAT))
        self.assertIsNone(fb.side_world_rank(["西本 拳太"], "男子シングルス", None))                       # ランキング表が無い


class ParseAndViewTest(unittest.TestCase):
    def test_real_dom_sample(self):
        item = BeautifulSoup(SAMPLE_HTML, "html.parser").select_one("li")
        parsed = fb._parse_nba_match_item(item, {})
        self.assertEqual(parsed["right_teams"], ["(チャイニーズ・タイペイ)"] * 2)
        v = fb.build_match_view(parsed, BY_CAT)
        self.assertEqual((v["event"], v["round"]), ("混合ダブルス", "1回戦"))        # 実データは「種目 ラウンド」の並び
        self.assertEqual((v["result"], v["own_sets"], v["opp_sets"]), ("win", 2, 0))
        self.assertEqual(v["own"]["rank"], 20)
        self.assertIsNone(v["opp"]["rank"])
        self.assertEqual(v["own"]["affil"], ["ジェイテクト", "PLENTY GLOBAL LINX"])
        self.assertEqual(v["opp"]["country"], "チャイニーズ・タイペイ")

    def test_split_round_event_both_orders(self):
        self.assertEqual(fb.split_round_event("1回戦 男子シングルス"), ("男子シングルス", "1回戦"))
        self.assertEqual(fb.split_round_event("男子シングルス 1回戦"), ("男子シングルス", "1回戦"))

    def test_japanese_on_right_side_is_own(self):
        v = fb.build_match_view(pm("1回戦 男子シングルス", ["LAI Victor"], ["田中 湧士"], "(カナダ)", "(筑波大学)",
                                   [(21, 15), (14, 21), (21, 19)], True), BY_CAT)
        self.assertEqual(v["own"]["names"], ["田中 湧士"])
        self.assertEqual((v["result"], v["own_sets"], v["opp_sets"], v["games"]), ("loss", 1, 2, ["15-21", "21-14", "19-21"]))
        self.assertEqual(v["opp"]["country"], "カナダ")

    def test_walkover_has_no_score(self):
        v = fb.build_match_view(pm("1回戦 男子シングルス", ["西本 拳太"], ["POPOV Toma Junior"], "(NTT)", "(フランス)", [], True), BY_CAT)
        self.assertIsNone(v["own_sets"])
        self.assertIn("スコアなし", fb.render_match_view(v))


class RenderTest(unittest.TestCase):
    MATCHES = [
        pm("1回戦 男子シングルス", ["西本 拳太"], ["POPOV Toma Junior"], "(NTT東日本)", "(フランス)", [(21, 18), (19, 21), (21, 15)], True),
        pm("1回戦 男子シングルス", ["LAI Victor"], ["田中 湧士"], "(カナダ)", "(筑波大学)", [(21, 15), (14, 21), (21, 19)], True),
        pm("男子ダブルス 1回戦", ["保木 卓朗", "小林 優吾"], ["LIANG Wei Keng", "WANG Chang"], "(トナミ運輸)", "(中国)", [(21, 19), (18, 21), (21, 17)], True),
    ]

    def text(self):
        embeds = fb.build_nba_result_embeds([result(self.MATCHES)])
        return embeds, "\n".join(e["description"] for e in embeds)

    def test_japanese_ui_and_no_wr(self):
        embeds, t = self.text()
        self.assertEqual(len(embeds), 1)
        self.assertEqual(embeds[0]["title"], "🏸 マレーシア・オープン 2026　大会結果")
        self.assertNotIn("WR", t)
        for expect in ("**男子シングルス**", "**男子ダブルス**", "**西本 拳太**　世界27位　**勝利**", "**2 – 1**", "21-18 / 19-21 / 21-15",
                       "POPOV Toma Junior", "-# 世界15位・フランス", "**田中 湧士**　世界18位　敗戦", "**1 – 2**", "LAI Victor",
                       "-# 世界—位・カナダ", "**保木 卓朗・小林 優吾**　世界9位　**勝利**", "LIANG Wei Keng・WANG Chang", "-# 世界3位・中国",
                       "NTT東日本", "[詳細を見る](<https://example.com/r/1>)"):
            self.assertIn(expect, t)

    def test_sections_in_event_order_and_one_match_per_block(self):
        _, t = self.text()
        self.assertLess(t.index("**男子シングルス**"), t.index("**男子ダブルス**"))
        self.assertEqual(t.count(fb.RESULT_SEP), 3)            # 試合ごとに区切り
        self.assertEqual(t.count("**男子シングルス**"), 1)       # 同じ種目は1つの見出しにまとめる

    def test_missing_rank_is_dash_not_guess(self):
        t = fb.render_match_view(fb.build_match_view(self.MATCHES[0], {}))
        self.assertIn("世界—位", t)
        self.assertNotIn("世界27位", t)

    def test_country_codes_are_japanese(self):
        v = fb.build_match_view(pm("1回戦 男子シングルス", ["西本 拳太"], ["Foo BAR"], "(NTT)", "(FRA)", [(21, 1), (21, 1)], True), BY_CAT)
        self.assertIn("フランス", fb.render_match_view(v))

    def test_legacy_entries_are_kept(self):
        legacy = {"round_event": "1回戦 男子シングルス", "left": "西本 拳太[WR27]", "right": "X", "left_win": True,
                  "right_win": False, "score_text": "2-0（21-1, 21-1）"}
        r = {"tournament": "T", "update_date": "d", "url": "u", "japan_matches": [legacy]}
        t = fb.build_nba_result_embeds([r])[0]["description"]
        self.assertIn("**その他**", t)
        self.assertIn("西本 拳太[WR27]", t)

    def test_no_matches_message_kept(self):
        r = {"tournament": "T", "update_date": "d", "url": "u", "japan_matches": []}
        self.assertIn("まだ掲載されていません", fb.build_nba_result_embeds([r])[0]["description"])

    def test_all_matches_kept_and_split_within_limits(self):
        many = [pm("1回戦 男子シングルス", [f"選手{i} 太郎"], [f"OPP{i} Name"], "(長い所属名チーム)", "(フランス)", [(21, 18), (19, 21), (21, 15)], True)
                for i in range(60)]
        embeds = fb.build_nba_result_embeds([result(many)])
        self.assertGreater(len(embeds), 1)
        t = "\n".join(e["description"] for e in embeds)
        for i in range(60):
            self.assertIn(f"選手{i} 太郎", t)                    # 件数の上限で試合を落とさない
        for e in embeds:
            self.assertLessEqual(len(e["description"]), 4096)
        self.assertIn("男子シングルス（続き）", embeds[1]["description"])
        self.assertTrue(embeds[1]["title"].endswith("（続き）"))
        for batch in fb.split_embed_batches(embeds):
            self.assertLessEqual(sum(fb._embed_chars(e) for e in batch), 5500 + 4096)
            self.assertLessEqual(len(batch), 10)

    def test_batches_respect_six_thousand_chars(self):
        e = [{"title": "t", "description": "あ" * 3000}] * 5
        batches = fb.split_embed_batches(e)
        self.assertTrue(all(sum(fb._embed_chars(x) for x in b) <= 6000 for b in batches))
        self.assertEqual(sum(len(b) for b in batches), 5)


class OtherLabelsTest(unittest.TestCase):
    def test_wr_labels_are_japanese(self):
        wr = {fb._normalize_player_key("西本拳太"): 27}
        self.assertEqual(fb.annotate_player_with_wr("西本 拳太", wr), "西本 拳太（世界27位）")
        self.assertEqual(fb.annotate_side_with_wr("西本 拳太", wr), "西本 拳太（世界27位）")
        self.assertEqual(fb.annotate_player_with_wr("知らない 選手", wr), "知らない 選手")
        self.assertEqual(fb.format_final_side(["西本 拳太"], "(NTT)", wr), "西本 拳太（日本 / 世界27位）")
        self.assertIn("世界—位", fb.format_final_side(["未掲載 選手"], "(NTT)", wr))

    def test_favorite_block_uses_japanese_rank_label(self):
        m = pm("男子ダブルス 準々決勝", ["渡辺 勇大"], ["X Y"], "(NTT東日本)", "(中国)", [(21, 10), (21, 10)], True)
        block = fb.format_favorite_match_block(m, {}, {"渡辺 勇大"})
        self.assertIn("世界—位", block)
        self.assertNotIn("WR", block)


class WrFetchTest(unittest.TestCase):
    TABLE = """<table class="rankingTable"><tr><td class="rankCell"><b>27</b></td>
      <td class="nameCell"><a href="/player/detail/1"><span class="nameTxt">西本拳太</span> Kenta NISHIMOTO</a><span class="pointCell">50000</span></td>
      <td class="contryCell"><img alt="JPN"></td></tr></table>"""

    def test_by_category_built_without_changing_legacy_return(self):
        resp = mock.Mock(status_code=200, text=self.TABLE)
        resp.raise_for_status = lambda: None
        with mock.patch.object(fb.requests, "get", return_value=resp):
            rankings, japan, by = fb.fetch_wr_rankings_detailed()
            legacy = fb.fetch_wr_rankings()
        self.assertEqual(len(legacy), 2)
        self.assertEqual(by["male_singles"]["西本拳太"], 27)
        self.assertEqual(fb.lookup_category_rank("NISHIMOTO Kenta", by["male_singles"]), 27)
        self.assertEqual(rankings[fb._normalize_player_key("西本拳太")], 27)


if __name__ == "__main__":
    unittest.main()
