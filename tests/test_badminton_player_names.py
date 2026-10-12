"""日本語氏名の対応表(badminton_player_names.py)のテスト。通信は使わない。
実行: .venv/bin/python -m unittest tests.test_badminton_player_names
"""
import datetime as dt
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import badminton_player_names as bpn  # noqa: E402
import badminton_tournament_master as bm  # noqa: E402

NOW = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()


def table(*rows):
    d = bpn.empty_names()
    for ja, latin, gender in rows:
        bpn.add_player(d, ja, latin, gender, "https://www.badminton.or.jp/national/player/view/1", "", NOW)
    return bpn.PlayerNames(d)


class ParseTest(unittest.TestCase):
    def test_official_view_page(self):
        self.assertEqual(bpn.parse_player_view(fixture("nba_player_view_excerpt.html")), {"ja": "奈良岡功大", "latin": "kodai naraoka"})

    def test_incomplete_page_is_not_registered(self):
        self.assertIsNone(bpn.parse_player_view('<span class="v-view__hero-ttl-jp-name">奈良岡 功大</span>'))                 # ローマ字が無い
        self.assertIsNone(bpn.parse_player_view('<span class="v-view__hero-ttl-myo">kodai</span><span class="v-view__hero-ttl-na">naraoka</span>'))   # 漢字が無い
        self.assertIsNone(bpn.parse_player_view("<html></html>"))

    def test_list_page(self):
        self.assertEqual(bpn.parse_player_list(fixture("nba_player_list_excerpt.html")),
                         ["/national/player/view/5", "/national/player/view/54"])


class LookupTest(unittest.TestCase):
    def test_word_order_and_case_are_ignored(self):
        t = table(("奈良岡功大", "kodai naraoka", "M"))
        for latin in ("NARAOKA Kodai", "Kodai NARAOKA", "kodai  naraoka"):
            self.assertEqual(t.lookup(latin, "M"), "奈良岡功大", latin)

    def test_unknown_stays_none(self):
        t = table(("奈良岡功大", "kodai naraoka", "M"))
        self.assertIsNone(t.lookup("KOGA Minoru", "M"))
        self.assertIsNone(t.lookup("NARAOKA", "M"))                    # 姓だけでは一致させない
        self.assertIsNone(t.lookup("NARAOKA Kodai Jr", "M"))           # 語が増えたら別人扱い
        self.assertIsNone(t.lookup("", "M"))

    def test_gender_mismatch_is_not_converted(self):
        t = table(("渡辺祐", "yu watanabe", "F"))
        self.assertIsNone(t.lookup("WATANABE Yu", "M"))
        self.assertEqual(t.lookup("WATANABE Yu", "F"), "渡辺祐")
        self.assertEqual(table(("渡辺祐", "yu watanabe", None)).lookup("WATANABE Yu", "M"), "渡辺祐")   # 性別が不明な登録は照合しない

    def test_same_latin_different_kanji_is_ambiguous(self):
        d = bpn.empty_names()
        self.assertEqual(bpn.add_player(d, "渡辺勇大", "yuta watanabe", "M", "u", "", NOW), "added")
        self.assertEqual(bpn.add_player(d, "渡邊勇太", "yuta watanabe", "M", "u2", "", NOW), "ambiguous")
        self.assertEqual(bpn.add_player(d, "渡邉雄大", "yuta watanabe", "M", "u3", "", NOW), "ambiguous")
        t = bpn.PlayerNames(d)
        self.assertIsNone(t.lookup("WATANABE Yuta", "M"))             # どれにも変換しない
        self.assertEqual(set(d["ambiguous"]["WATANABE YUTA"]["ja"]), {"渡辺勇大", "渡邊勇太", "渡邉雄大"})

    def test_same_person_again_only_updates_verification(self):
        d = bpn.empty_names()
        bpn.add_player(d, "奈良岡功大", "kodai naraoka", "M", "u", "", NOW)
        later = NOW + dt.timedelta(days=1)
        self.assertEqual(bpn.add_player(d, "奈良岡功大", "kodai naraoka", "M", "u", "", later), "verified")
        rec = d["players"]["KODAI NARAOKA"]
        self.assertEqual((rec["retrieved_at"], rec["verified_at"]), (NOW.isoformat(), later.isoformat()))


class BuildTest(unittest.TestCase):
    def test_build_from_official_pages(self):
        pages = {
            "https://www.badminton.or.jp/national/player?gender=male": fixture("nba_player_list_excerpt.html"),
            "https://www.badminton.or.jp/national/player?gender=female": "<html></html>",
            "https://www.badminton.or.jp/national/player?gender=male&category[]=u24": "<html></html>",
            "https://www.badminton.or.jp/national/player?gender=female&category[]=u24": "<html></html>",
            "https://www.badminton.or.jp/national/player/view/5": fixture("nba_player_view_excerpt.html"),
        }

        def fetch(url):
            if url not in pages:
                raise RuntimeError("404")
            return pages[url]
        data, stats = bpn.build_from_site(fetch, NOW)
        self.assertEqual((stats["added"], stats["failed"]), (1, 1))                    # 54番は取得失敗 → 残りは続行
        rec = data["players"]["KODAI NARAOKA"]
        self.assertEqual((rec["ja"], rec["gender"], rec["source_url"]), ("奈良岡功大", "M", "https://www.badminton.or.jp/national/player/view/5"))

    def test_snapshot_keeps_official_url_as_source(self):
        pages = {}
        seen = []

        def fetch(url):
            seen.append(url)
            return fixture("nba_player_view_excerpt.html") if "/view/" in url else fixture("nba_player_list_excerpt.html")
        data, _ = bpn.build_from_site(fetch, NOW, snapshot_prefix="https://web.archive.org/web/2026/")
        rec = data["players"]["KODAI NARAOKA"]
        self.assertEqual(rec["source_url"], "https://www.badminton.or.jp/national/player/view/5")
        self.assertTrue(rec["snapshot_url"].startswith("https://web.archive.org/web/2026/https://www.badminton.or.jp/"))


class EntryDisplayTest(unittest.TestCase):
    ENTRIES = {"players": [{"latin": "WATANABE Koki", "gender": "M", "draw": "本戦"}, {"latin": "KOGA Minoru", "gender": "M", "draw": "予選"},
                           {"latin": "TAGUCHI Maya", "gender": "F", "draw": "本戦"}]}

    def japanese(self, names):
        svc = bm.TournamentMaster(master=bm.empty_master(), names=names, aliases=[], sponsor_words=[])
        return {p["name"] for p in svc.japanese_players({"entries": self.ENTRIES})}

    def test_registered_players_are_japanese_and_others_keep_bwf_spelling(self):
        names = self.japanese(table(("渡邉航貴", "koki watanabe", "M"), ("田口真彩", "maya taguchi", "F")))
        self.assertEqual(names, {"渡邉航貴", "KOGA Minoru", "田口真彩"})

    def test_ranking_data_is_not_a_name_source(self):
        # 世界ランキング取得(バドナビ)の結果に氏名辞書は含めない。氏名の変換は対応表(別データ)だけを使う
        from unittest import mock
        import fetch_badminton_alerts as fb
        html = ('<table class="rankingTable"><tr><td class="rankCell"><b>27</b></td><td class="nameCell"><a href="/player/detail/1">'
                '<span class="nameTxt">西本拳太</span> Kenta NISHIMOTO</a></td><td class="contryCell"><img alt="JPN"></td></tr></table>')
        resp = mock.Mock(status_code=200, text=html)
        resp.raise_for_status = lambda: None
        with mock.patch.object(fb.requests, "get", return_value=resp):
            _rankings, _japan, by_category = fb.fetch_wr_rankings_detailed()
        self.assertNotIn("_names_ja", by_category)
        self.assertEqual(set(by_category), {"male_singles", "female_singles", "male_doubles", "female_doubles", "mixed_doubles"})

    def test_gender_mismatch_in_entry_keeps_bwf_spelling(self):
        names = self.japanese(table(("古賀実", "minoru koga", "F")))                       # 女子登録 vs エントリーは男子
        self.assertIn("KOGA Minoru", names)

    def test_ambiguous_entry_keeps_bwf_spelling(self):
        d = bpn.empty_names()
        bpn.add_player(d, "渡邉航貴", "koki watanabe", "M", "u", "", NOW)
        bpn.add_player(d, "渡邊幸喜", "koki watanabe", "M", "u", "", NOW)
        self.assertIn("WATANABE Koki", self.japanese(bpn.PlayerNames(d)))


class DataFileTest(unittest.TestCase):
    def test_committed_data_is_consistent(self):
        d = bpn.load_names()
        self.assertEqual(d["schema_version"], bpn.SCHEMA_VERSION)
        for key, rec in d["players"].items():
            self.assertEqual(key, bpn.token_key(rec["latin"]))
            self.assertTrue(rec["source_url"].startswith("https://www.badminton.or.jp/national/player/view/"))
            self.assertNotIn(key, d["ambiguous"])


if __name__ == "__main__":
    unittest.main()
