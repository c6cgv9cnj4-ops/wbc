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
        self.assertEqual(lines[0], "- [アジア大会の金メダル第1号は北條巧（北本・北本中） - 朝日新聞](<https://news.google.com/rss/articles/o1>) `[10/05 20:00]`")
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


class HeadlineKeyTest(unittest.TestCase):
    BASE = "【トライアスロン】今大会日本勢１号金のトライアスロン男子・北條巧「優勝を誇りに思う」"

    def test_same_headline_different_outlets_same_key(self):
        a = self.BASE + " - 日刊スポーツ"
        b = self.BASE + " (日刊スポーツ) - news.yahoo.co.jp"
        c = self.BASE + "（日刊スポーツ） - news.yahoo.co.jp"
        d = self.BASE
        self.assertEqual(len({ko.headline_key(t) for t in (a, b, c, d)}), 1)

    def test_width_and_spaces_are_normalized(self):
        self.assertEqual(ko.headline_key("北條巧 が 金メダル １号 - 朝日新聞"), ko.headline_key("北條巧が金メダル1号 - jiji.com"))

    def test_different_headlines_stay_different(self):
        self.assertNotEqual(ko.headline_key("北條巧「優勝を誇りに思う」 - 日刊スポーツ"), ko.headline_key("北條巧が帰国会見 - 日刊スポーツ"))

    def test_only_obvious_media_in_parentheses_is_removed(self):
        self.assertNotEqual(ko.headline_key("樋口正修が打撃好調（中日）"), ko.headline_key("樋口正修が打撃好調"))   # チーム名は媒体名ではない
        self.assertNotEqual(ko.headline_key("北條巧が金（初）"), ko.headline_key("北條巧が金"))
        self.assertEqual(ko.headline_key("樋口正修が初打点 (週刊ベースボールONLINE)"), ko.headline_key("樋口正修が初打点"))


class OriginDedupeIntegrationTest(unittest.TestCase):
    def feed(self, titles):
        return [{"title": t, "url": f"https://news.google.com/rss/articles/d{i}", "published": "10/06 08:00"} for i, t in enumerate(titles)]

    def run_digest(self, titles, core=()):
        base = DigestIntegrationTest()
        return base.run_digest(core=list(core), origin=self.feed(titles))

    H = "今大会日本勢１号金のトライアスロン男子・北條巧「優勝を誇りに思う」"

    def test_same_story_three_outlets_becomes_one_line(self):
        msg, sports = self.run_digest([self.H + " - 日刊スポーツ", self.H + " (日刊スポーツ) - news.yahoo.co.jp",
                                       self.H + "（日刊スポーツ） - news.yahoo.co.jp"])
        self.assertEqual(msg.count("北條巧"), 1)
        self.assertIn("日刊スポーツ", msg)                  # 代表は先着(最初の媒体)
        self.assertEqual(sports, [])

    def test_different_headlines_are_separate_articles(self):
        msg, _ = self.run_digest([self.H + " - 日刊スポーツ", "北條巧が帰国会見「次はオリンピック」 - 朝日新聞"])
        self.assertEqual(msg.count("北條巧"), 2)

    def test_cap_still_applies_after_dedupe(self):
        titles = []
        for i in range(5):                                   # 5つの別記事、それぞれ転載2件
            titles += [f"北條巧が金メダル 別記事{i} - 媒体A", f"北條巧が金メダル 別記事{i} (媒体B新聞) - news.yahoo.co.jp"]
        msg, _ = self.run_digest(titles)
        self.assertEqual(msg.count("北條巧"), ko.MAX_PER_PERSON)

    def test_dedupe_does_not_consume_cap(self):
        titles = [self.H + " - 日刊スポーツ", self.H + " (日刊スポーツ) - news.yahoo.co.jp", self.H + "（日刊スポーツ） - news.yahoo.co.jp",
                  "北條巧が帰国会見 - 朝日新聞", "北條巧、次戦はW杯 - 共同通信"]
        msg, _ = self.run_digest(titles)
        self.assertEqual(msg.count("北條巧"), 3)             # 転載が枠を使い切らず、別記事が3件まで入る
        self.assertIn("帰国会見", msg)
        self.assertIn("次戦はW杯", msg)

    def test_per_person_independent(self):
        h = "金メダルに「ありがとう」"
        msg, _ = self.run_digest([f"北條巧が{h} - 媒体A", f"中日・樋口正修が{h} - 媒体B"])
        self.assertIn("北條巧", msg)
        self.assertIn("樋口正修", msg)

    def test_non_ledger_person_not_included(self):
        msg, _ = self.run_digest(["大島敦氏が金メダルを祝福 - 朝日新聞", "北條巧が金メダル - 朝日新聞"])
        self.assertNotIn("大島敦", msg)
        self.assertIn("北條巧", msg)

    def test_sports_double_delivery_still_zero(self):
        base = DigestIntegrationTest()
        msg, sports = base.run_digest(core=["中日 3-2 阪神 延長10回サヨナラ - 日刊スポーツ"],
                                      origin=self.feed(["中日、1番・樋口正修のプロ初適時打 - 中日新聞Web",
                                                        "中日、1番・樋口正修のプロ初適時打 (中日新聞Web) - news.yahoo.co.jp"]))
        self.assertEqual(msg.count("樋口正修"), 1)
        self.assertFalse([x for x in sports if "樋口正修" in x["title"]])


class DisplayLabelTest(unittest.TestCase):
    """台帳の「北本との関係」から読者向けの短い表示を作る(人物ごとの関係をコードに持たない)。"""
    EXPECTED = {"北條巧": "北本・北本中", "樋口正修": "北本・東小／東中", "佐藤悠介": "北本・西中",
                "新井馨": "北本・石戸小", "パーマ大佐": "北本・北本中"}

    def test_all_five_labels_come_from_the_ledger(self):
        people = {p["name"]: p for p in ko.load_ledger()["people"]}
        self.assertEqual(set(people), set(self.EXPECTED))
        for name, label in self.EXPECTED.items():
            self.assertEqual(ko.display_label(people[name]), label, name)

    def test_higuchi_is_exactly_the_required_form(self):
        p = next(x for x in ko.load_ledger()["people"] if x["name"] == "樋口正修")
        self.assertEqual(f"{p['name']}（{ko.display_label(p)}）", "樋口正修（北本・東小／東中）")
        t = "樋口正修「ファンから頂いたバスボム、キティーちゃんでした！」"
        self.assertEqual(ko.label_title(t, p), "樋口正修（北本・東小／東中）「ファンから頂いたバスボム、キティーちゃんでした！」")

    def test_generic_conversion_not_hardcoded(self):
        self.assertEqual(ko.display_label({"relation": "北本市立中丸小学校・北本市立北本中学校"}), "北本・中丸小／北本中")
        self.assertEqual(ko.display_label({"relation": "石戸国民学校(現北本市立石戸小学校)卒業"}), "北本・石戸小")
        self.assertEqual(ko.display_label({"relation": "北本で育った"}), "北本育ち")
        self.assertEqual(ko.display_label({"relation": ""}), "北本")

    def test_ledger_change_changes_label(self):
        p = {"name": "テスト太郎", "aliases": [], "class": "strong", "require_any": [], "relation": "北本市立南小学校・北本市立宮内中学校"}
        self.assertEqual(ko.label_title("テスト太郎が優勝", p), "テスト太郎（北本・南小／宮内中）が優勝")

    def test_label_title_rules(self):
        p = next(x for x in ko.load_ledger()["people"] if x["name"] == "北條巧")
        self.assertEqual(ko.label_title("中日・北條巧が金 - 朝日新聞", p), "中日・北條巧（北本・北本中）が金 - 朝日新聞")
        once = ko.label_title("北條巧が金", p)
        self.assertEqual(ko.label_title(once, p), once)                        # 二重に付けない
        self.assertEqual(ko.label_title("別の記事", p), "別の記事")               # 氏名が無ければそのまま

    def test_no_evidence_in_label(self):
        for p in ko.load_ledger()["people"]:
            label = ko.display_label(p)
            self.assertNotIn("http", label)
            self.assertNotIn(p["evidence_quote"][:6], label)
            self.assertNotIn("出身", label)                                       # 「北本市出身」と断定しない


class LabelIntegrationTest(unittest.TestCase):
    def test_digest_line_has_label_and_no_evidence(self):
        base = DigestIntegrationTest()
        origin = [{"title": "樋口正修「ファンから頂いたバスボム、キティーちゃんでした！」 - 週刊ベースボールONLINE",
                   "url": "https://news.google.com/rss/articles/l1", "published": "10/06 09:00"}]
        msg, sports = base.run_digest(origin=origin)
        self.assertIn("[樋口正修（北本・東小／東中）「ファンから頂いたバスボム、キティーちゃんでした！」 - 週刊ベースボールONLINE]", msg)
        self.assertNotIn("wikipedia", msg)
        self.assertNotIn("北本市立", msg)
        self.assertEqual(sports, [])

    def test_all_five_get_label_in_digest(self):
        base = DigestIntegrationTest()
        origin = [{"title": t, "url": f"https://news.google.com/rss/articles/m{i}", "published": ""} for i, t in enumerate(
            ["北條巧が金メダル - 朝日新聞", "中日・樋口正修が初打点 - 中日新聞Web", "元北本市長の新井馨氏が死去 - 埼玉新聞",
             "サッカー・佐藤悠介が現役引退を発表 - スポーツ報知", "パーマ大佐、結婚を発表 - ORICON NEWS"])]
        msg, _ = base.run_digest(origin=origin)
        for name, label in DisplayLabelTest.EXPECTED.items():
            self.assertIn(f"{name}（{label}）", msg, name)

    def test_headline_dedupe_still_works_with_label(self):
        base = DigestIntegrationTest()
        h = "北條巧が金メダル「ありがとう」"
        origin = [{"title": h + " - 日刊スポーツ", "url": "https://news.google.com/rss/articles/n1", "published": ""},
                  {"title": h + " (日刊スポーツ) - news.yahoo.co.jp", "url": "https://news.google.com/rss/articles/n2", "published": ""}]
        msg, _ = base.run_digest(origin=origin)
        self.assertEqual(msg.count("北條巧"), 1)


if __name__ == "__main__":
    unittest.main()
