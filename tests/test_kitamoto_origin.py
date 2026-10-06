"""北本ゆかり型(kitamoto_origin.py / data/kitamoto_origin_people.json)のテスト。通信は使わない。
実行: .venv/bin/python -m unittest tests.test_kitamoto_origin
"""
import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402
import kitamoto_origin as ko  # noqa: E402

NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))


class LedgerTest(unittest.TestCase):
    def test_initial_ledger_is_the_five_confirmed_strong_people(self):
        led = ko.load_ledger()
        self.assertEqual([p["name"] for p in led["people"]], ["北條巧", "樋口正修", "佐藤悠介", "新井馨", "パーマ大佐"])
        self.assertTrue(all(p["class"] == "strong" for p in led["people"]))

    def test_every_person_has_evidence(self):
        with open(ko.LEDGER_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        names = [p["name"] for p in raw["people"]]
        self.assertEqual(len(names), len(set(names)))                       # 重複なし
        for p in raw["people"]:
            for key in ("name", "aliases", "class", "role", "relation", "evidence_quote", "evidence_url", "require_any", "added"):
                self.assertIn(key, p, f"{p.get('name')}: {key}")
            self.assertTrue(p["relation"] and p["evidence_quote"], p["name"])      # 推測で追加しない: 学校・関係と根拠が必須
            self.assertTrue(p["evidence_url"].startswith("https://"), p["name"])
            self.assertIn("北本", p["relation"] + p["evidence_quote"], p["name"])

    def test_weak_people_are_never_used(self):
        data = {"people": [{"name": "大島敦", "class": "weak", "aliases": [], "require_any": []},
                           {"name": "北條巧", "class": "strong", "aliases": [], "require_any": []}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        try:
            led = ko.load_ledger(fh.name)
            self.assertEqual([p["name"] for p in led["people"]], ["北條巧"])
            self.assertIsNone(ko.match("大島敦氏が講演", led))
        finally:
            os.unlink(fh.name)

    def test_missing_ledger_is_empty(self):
        led = ko.load_ledger("/nonexistent/ledger.json")
        self.assertEqual(led["people"], [])
        self.assertIsNone(ko.search_url(led))
        self.assertIsNone(ko.match("北條巧が金メダル", led))


class MatchTest(unittest.TestCase):
    def name(self, title):
        p = ko.match(title)
        return p["name"] if p else None

    def test_important_articles_match(self):
        self.assertEqual(self.name("アジア大会の金メダル第1号は北條巧 「人生一番の勝負だと思った」 - 朝日新聞"), "北條巧")
        self.assertEqual(self.name("中日、1番・樋口正修のプロ初適時打初打点で先制 - 中日新聞Web"), "樋口正修")
        self.assertEqual(self.name("パーマ大佐、結婚を発表 - ORICON NEWS"), "パーマ大佐")

    def test_ambiguous_names_need_aux(self):
        self.assertEqual(self.name("元北本市長の新井馨氏が死去"), "新井馨")
        self.assertEqual(self.name("新井馨市長が定例会見"), "新井馨")
        self.assertIsNone(self.name("新井馨さん(会社員)が優勝 地域のゴルフ大会"))                 # 北本・市長の併記なし
        self.assertEqual(self.name("サッカー・佐藤悠介が現役引退を発表"), "佐藤悠介")
        self.assertEqual(self.name("J2 佐藤悠介が決勝ゴール - スポーツ報知"), "佐藤悠介")
        self.assertIsNone(self.name("佐藤悠介さんが県議選に出馬表明"))                           # 同姓同名(競技語なし)

    def test_listing_type_titles_are_not_important(self):
        self.assertIsNone(self.name("樋口正修 先発予想 中日 vs 広島"))
        self.assertIsNone(self.name("パーマ大佐 出演決定！お笑いライブのチケット発売"))
        self.assertIsNone(self.name("北條巧 日程・放送予定 トライアスロン"))
        self.assertIsNone(self.name("いわき vs 横浜FC : 第6節 【明治安田J2リーグ】 - DAZN"))

    def test_name_must_be_in_title_not_media(self):
        self.assertIsNone(self.name("トライアスロン男子で日本勢が金 - 北條巧公式"))   # 媒体名だけに氏名
        self.assertIsNone(self.name("中日が勝利 先発は高橋宏"))
        self.assertIsNone(self.name("樋口正人が優勝"))                                    # 近い別人

    def test_general_sports_not_matched(self):
        for t in ("中日 3-2 阪神 延長10回サヨナラ - 日刊スポーツ", "広島 先発の床田が7回1失点 - スポニチ",
                  "トライアスロン日本代表、アジア大会へ出発"):
            self.assertIsNone(self.name(t), t)

    def test_search_query(self):
        q = ko.search_query(days=1)
        self.assertEqual(q, '("北條巧" OR "樋口正修" OR "佐藤悠介" OR "新井馨" OR "パーマ大佐") when:1d')
        self.assertIn("when%3A90d", ko.search_url(days=90))


class DigestIntegrationTest(unittest.TestCase):
    ORIGIN = [
        {"title": "アジア大会の金メダル第1号は北條巧 - 朝日新聞", "url": "https://news.google.com/rss/articles/o1", "published": "10/05 20:00"},
        {"title": "中日、1番・樋口正修のプロ初適時打初打点で先制 - 中日新聞Web", "url": "https://news.google.com/rss/articles/o2", "published": "10/05 21:00"},
    ]

    def run_digest(self, core=(), origin=None, state=None):
        def fake_rss(url, limit=10):
            if url == fn.GOOGLE_NEWS_ORIGIN_RSS:
                return list(self.ORIGIN if origin is None else origin)
            if url == fn.GOOGLE_NEWS_NEARBY_RSS:
                return []
            return [{"title": t, "url": f"https://news.google.com/rss/articles/{abs(hash(t))}", "published": "10/06 08:00"} for t in core]
        with mock.patch.dict(os.environ, {"ANZN_SOURCE": "mac"}), \
             mock.patch.object(fn, "fetch_rss_items", side_effect=fake_rss), \
             mock.patch.object(fn, "fetch_direct_rss_items", return_value=[]), \
             mock.patch.object(fn.mastodon_anzn, "fetch_recent_items", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()):
            return fn.build_local_news_message({} if state is None else state, NOW)

    def test_origin_sports_goes_to_local_not_sports(self):
        msg, sports = self.run_digest()
        self.assertIn("北條巧", msg)
        self.assertIn("樋口正修", msg)                      # 野球(スポーツ判定)でも地域ニュース優先
        self.assertEqual(sports, [])                        # スポーツ側へは回さない=二重配信なし
        self.assertEqual(fn.is_sports_related(self.ORIGIN[1]["title"]), True)   # スポーツ判定自体は従来どおり

    def test_origin_first_and_layout_unchanged(self):
        msg, _ = self.run_digest(core=["北本市で秋まつり開催 - 埼玉新聞"])
        lines = [ln for ln in msg.split("\n") if ln.startswith("- [")]
        self.assertIn("北條巧", lines[0])                   # 北本ゆかり型を先頭に置く
        self.assertIn("北本市で秋まつり開催", lines[-1])
        self.assertEqual(lines[0], "- [アジア大会の金メダル第1号は北條巧 - 朝日新聞](<https://news.google.com/rss/articles/o1>) `[10/05 20:00]`")
        self.assertIn("## 🚨 防災・緊急情報", msg)

    def test_general_sports_still_goes_to_sports(self):
        msg, sports = self.run_digest(core=["北本市の中学校 野球部が県大会で優勝 - 埼玉新聞", "北本市で秋まつり開催 - 埼玉新聞"], origin=[])
        self.assertEqual(len(sports), 1)
        self.assertNotIn("野球部", msg)

    def test_existing_local_rules_untouched(self):
        msg, _ = self.run_digest(core=["【さいたま市浦和区】新店オープン - 号外NET", "北本市の小学校で運動会 - 埼玉新聞"])
        self.assertIn("北本市の小学校で運動会", msg)
        self.assertNotIn("さいたま市浦和区", msg)

    def test_per_person_cap(self):
        many = [{"title": f"北條巧が金メダル 速報{i} - 媒体{i}", "url": f"https://news.google.com/rss/articles/c{i}", "published": ""} for i in range(7)]
        msg, _ = self.run_digest(origin=many)
        self.assertEqual(msg.count("北條巧"), ko.MAX_PER_PERSON)

    def test_no_origin_means_no_digest(self):
        msg, sports = self.run_digest(origin=[])
        self.assertIsNone(msg)

    def test_not_resent_next_run(self):
        state = {}
        first, _ = self.run_digest(state=state)
        second, _ = self.run_digest(state=state)
        self.assertIsNotNone(first)
        self.assertIsNone(second)                           # 既送信(state)で再配信しない


class SportsRouteTest(unittest.TestCase):
    def run_leak(self, items):
        sent = []
        with mock.patch.object(fn, "send_to_discord", side_effect=lambda u, m: (sent.append(m) or True)), \
             contextlib.redirect_stdout(io.StringIO()):
            err = fn._handle_sports_leak(items, "https://hook/sports", NOW, "一般ニュースフィード")
        return err, sent

    def test_origin_removed_general_kept(self):
        items = [{"title": "北條巧が金メダル - 朝日新聞", "url": "https://ex.com/a"},
                 {"title": "中日 3-2 阪神 延長10回サヨナラ - 日刊スポーツ", "url": "https://ex.com/b"}]
        err, sent = self.run_leak(items)
        self.assertFalse(err)
        self.assertEqual(len(sent), 1)
        self.assertIn("https://ex.com/b", sent[0])
        self.assertNotIn("https://ex.com/a", sent[0])

    def test_only_origin_sends_nothing(self):
        err, sent = self.run_leak([{"title": "樋口正修がプロ初本塁打 - 中日新聞", "url": "https://ex.com/a"}])
        self.assertFalse(err)
        self.assertEqual(sent, [])

    def test_unimportant_listing_stays_sports(self):
        err, sent = self.run_leak([{"title": "樋口正修 先発予想 中日 vs 広島 - 日刊スポーツ", "url": "https://ex.com/a"}])
        self.assertEqual(len(sent), 1)                      # 一覧型は台帳対象外=従来どおりスポーツ側


if __name__ == "__main__":
    unittest.main()
