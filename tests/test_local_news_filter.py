"""「埼玉・県央ローカルニュース」の記事選別(local_news_filter.py)のテスト。通信は使わない。
実際に #webhook_local へ送られていた記事のタイトルを、残すもの/除外するものに分けて固定している。
実行: .venv/bin/python -m unittest tests.test_local_news_filter
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402
import local_news_filter as lf  # noqa: E402

NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
NOTE = "https://kitamoto-city.note.jp/n/abc"
GOGUY_KK = "https://kounosu-kitamoto.goguynet.jp/2026/10/01/x/"
GOGUY_AO = "https://ageo-okegawa.goguynet.jp/2026/10/01/x/"


def keep(title, url=""):
    return lf.classify(title, url)[0]


class UserExamplesTest(unittest.TestCase):
    """依頼で挙げられた例。地域名が入っているだけでは地域ニュースにしない。"""

    def test_keep(self):
        self.assertTrue(keep("鴻巣市の小学校で稲刈り体験 児童が地元の田んぼで"))
        self.assertTrue(keep("鴻巣市の企業が地域イベントに参加 - 埼玉新聞"))
        self.assertTrue(keep("北本市の店舗が地域のお祭りで商店街を盛り上げ"))
        self.assertTrue(keep("北本市議会が補正予算を可決 - 埼玉新聞"))
        self.assertTrue(keep("北本市消防団が出初式 - 選挙ドットコム"))

    def test_drop_chain_and_used_car(self):
        self.assertFalse(keep("鴻巣市にも店舗があるマクドナルドが新商品を発売"))
        self.assertEqual(lf.classify("北本市のガリバーで中古車入荷しました - 中古車のガリバー")[1], "drop:used_car_or_buyback")
        self.assertFalse(keep("新着情報“アルファードＧAX Lエディション”2007年式ブラックマイカ入荷しました！ - 中古車のガリバー"))
        self.assertFalse(keep("北本市 新着情報“アクセス”ランキング - ○○"))

    def test_drop_far_and_national(self):
        self.assertEqual(lf.classify("【さいたま市南区】10/6 最高28℃、昼間は暑さを感じる場面がありそうな予想【朝版】 - Yahoo!ニュース")[0], False)
        self.assertEqual(lf.classify("【さいたま市浦和区】待望！北浦和の「ココイチ」がリニューアルオープン - 地域ニュースサイト号外NET")[1],
                         "drop:national_chain_commercial")
        self.assertEqual(lf.classify("教職員を守るカスハラ防止指針、さいたま市教委が策定 - 朝日新聞")[1], "drop:region_not_kitamoto_area")
        self.assertEqual(lf.classify("元巡査を在宅起訴…虚偽の調書を作成 - 埼玉新聞")[1], "drop:region_not_kitamoto_area")


class RealTitlesTest(unittest.TestCase):
    KEEP = [
        ("鴻巣・北本 都市計画変更（原案）説明会を開催します。 - pref.saitama.lg.jp", ""),
        ("【桶川市】リニューアルオープンした「ピッツェリア馬車道 桶川店」に行ってきました！ - saitama-city-marathon.jp", ""),
        ("北本市に想像支援拠点「プラトー」をつくりたい！100年の歴史を未来に繋げる挑戦 - CAMPFIRE (キャンプファイヤー)", ""),
        ("【牧野康成 講演会】 １０月２日（金）、歴史民俗資料館において「桶川市の歴史を語る会」 - 選挙ドットコム", ""),
        ("大型物流施設「プロロジスパーク北本」の内覧会を10月14日・15日に開催 - Storm.mg", ""),
        ("コンタクトレンズのシード、工場を構える埼玉県鴻巣市へカーボンクレジットを寄付 - PR TIMES", ""),
        ("【北本市】関東最大級「GRガレージ 北本BASE」のオープン日が判明しました！ - 埼玉マガジン", ""),
        ("【鴻巣市】エルミ内にあるたこ焼き屋「八福笑店」が閉店していたことが分かりました - 地域ニュースサイト号外NET", ""),
        ("【広報きたもと令和8年5月号特集掲載】覚えていますか「みどりとまつり」", NOTE),
        ("「好きなものを集めたお店」ハレノ日舎を取材しました！ #前編", NOTE),
        ("【鴻巣市】明用にある「天空の里」が開館4周年！", GOGUY_KK),
        ("【北本市】深井にあるヘイワールドで人気のシール販売会が開催されます♪", GOGUY_KK),
        ("【桶川市】桶川で見つけた異国情緒！本格ロシア料理カフェ『サリュート』", GOGUY_AO),
        ("【伊奈町】伊奈町の隠れ家『CAFE THE GARDEN』が7周年！お宝マーケットが開催されます！！", GOGUY_AO),
        ("週末の地元行事パート②◎#上尾市領家工業団地内の企業が主催する【領工会まつり】に出席し... - 選挙ドットコム", ""),
    ]
    DROP = [
        "新着情報“アクセラスポーツ15C”2010年式アルミニウムメタリック入荷しました！ - 中古車のガリバー",
        "しまむらグループのオンラインストア「しまむらパーク」にて、10/ 6（火）より「OPEN1周年記念フェア」を開催！ - PR TIMES",
        "【さいたま市岩槻区】「第14回城下町岩槻鷹狩り行列」のクラウドファンディングは本日まで！ - 地域ニュースサイト号外NET",
        "世界中で開催のスピーチイベントを大宮で実現　主催者は高校1年生 - topics.smt.docomo.ne.jp",
        "茨城、埼玉で震度４ Ｍ４・９、震源は茨城県南部 - 山陽新聞",
        "【速報】関東で地震　Ｍ３．９　東京、神奈川、埼玉、千葉、茨城などで揺れ - ｄメニューニュース",
        "さいたま市中央区のライブカメラ - ウェザーニュース",
        "（埼玉）さいたま市見沼区風渡野で暴行未遂　１０月５日 - ｄメニューニュース",
        "【上尾市】イオンモール上尾の「イオンバイク」が売場を拡大！10月9日にリニューアルオープン♪ - 埼玉マガジン",
        "【上尾市】密着～『業務スーパー上尾店』が待望のリニューアル！ - 号外NET",
        "【上尾市】衝撃…ガチャが消えた。 人気店『#C-pla』が異例の全店一斉休業へ… - 号外NET",
        "北上尾 一棟マンション - rakumachi.jp",
        "埼玉上尾、今季の目標は「優勝」　権田寛奈、人生初体験の主将に／SVリーグ女子 - sanspo.com",
        "【埼玉県】「こどもの居場所フェア埼玉」を開催します―地域でこどもの未来を応援― - ニコニコニュース",
        "給食に日高屋のタンメン…子どもたち夢中で箸を進める 創業者の故郷で特別メニュー",
        "週末にかけて地元行事に参加させていただきました。 - 選挙ドットコム",
    ]

    def test_keep(self):
        for title, url in self.KEEP:
            with self.subTest(title=title):
                self.assertTrue(keep(title, url), lf.classify(title, url))

    def test_drop(self):
        for title in self.DROP:
            with self.subTest(title=title):
                self.assertFalse(keep(title), lf.classify(title))

    def test_aggregated_pr_needs_core_city_and_local_signal(self):
        self.assertFalse(keep("【開催報告】クレーンゲーム×婚活で地域活性化！「くれコン。」に36名が参加 - PR TIMES"))
        self.assertFalse(keep("鴻巣市にもある新サービスを開始 - PR TIMES"))                       # 北本周辺の手がかり無し
        self.assertTrue(keep("鴻巣市内の小中学校でカーボンオフセット 地域貢献 - PR TIMES"))


class RegionPriorityTest(unittest.TestCase):
    def test_kitamoto_first(self):
        self.assertEqual(lf.classify("鴻巣市と北本市が合同で防災訓練")[2], "北本")
        self.assertEqual(lf.classify("鴻巣市の小学校で運動会")[2], "鴻巣")
        self.assertEqual(lf.classify("桶川市の公民館でイベント")[2], "桶川")

    def test_nearby_requires_local_signal(self):
        self.assertTrue(keep("久喜市長が予算削減の方針を発表 - 読売新聞"))
        self.assertFalse(keep("上尾市にオープンしたカフェに行ってきた - ブログ"))
        self.assertEqual(lf.classify("伊奈町で小学校の運動会")[2], "伊奈町")

    def test_direct_feed_defaults(self):
        self.assertEqual(lf.classify("ふみだスコーレのご報告", NOTE), (True, "keep:direct_feed", "北本"))
        self.assertFalse(keep("ホルモン焼屋さんの自販機がとっても便利", GOGUY_AO))      # 上尾・桶川版の市名なしは周辺扱い
        self.assertTrue(keep("【桶川市】桶川の歴史を語る会", GOGUY_AO))

    def test_split_source(self):
        self.assertEqual(lf.split_source("北本市で祭り - 埼玉新聞"), ("北本市で祭り", "埼玉新聞"))
        self.assertEqual(lf.split_source("タイトルのみ"), ("タイトルのみ", ""))


class DigestIntegrationTest(unittest.TestCase):
    """build_local_news_message: 選別が効くこと・スポーツの振り分けと防災欄は従来どおりであること。"""

    def run_digest(self, core_titles, nearby_titles=(), direct=None):
        def fake_rss(url, limit=10):
            titles = nearby_titles if url == fn.GOOGLE_NEWS_NEARBY_RSS else core_titles
            return [{"title": t, "url": f"https://news.google.com/rss/articles/{abs(hash(t))}", "published": "10/06 08:00"}
                    for t in titles]
        with mock.patch.dict(os.environ, {"ANZN_SOURCE": "mac"}), \
             mock.patch.object(fn, "fetch_rss_items", side_effect=fake_rss), \
             mock.patch.object(fn, "fetch_direct_rss_items", side_effect=lambda u, **k: (direct or {}).get(u, [])), \
             mock.patch.object(fn.mastodon_anzn, "fetch_recent_items", return_value=[]):
            return fn.build_local_news_message({}, NOW)

    def test_only_kitamoto_area_news_is_sent(self):
        msg, sports = self.run_digest(
            ["北本市の小学校で運動会 - 埼玉新聞", "【さいたま市浦和区】新店オープン - 号外NET",
             "新着情報“ノアハイブリッドG”入荷しました！ - 中古車のガリバー", "鴻巣市にもあるケンタッキーが新商品を発売"],
            nearby_titles=["上尾市議会が決算を審議 - 埼玉新聞", "上尾市の人気ラーメン店が閉店 - ブログ"])
        self.assertIn("北本市の小学校で運動会", msg)
        self.assertIn("上尾市議会が決算を審議", msg)
        for bad in ("さいたま市浦和区", "ガリバー", "ケンタッキー", "人気ラーメン店"):
            self.assertNotIn(bad, msg)
        self.assertEqual(sports, [])

    def test_all_dropped_means_no_digest(self):
        msg, _ = self.run_digest(["【さいたま市浦和区】新店オープン - 号外NET"])
        self.assertIsNone(msg)

    def test_duplicate_across_queries_once(self):
        msg, _ = self.run_digest(["鴻巣市と上尾市が合同で防災訓練"], nearby_titles=["鴻巣市と上尾市が合同で防災訓練"])
        self.assertEqual(msg.count("合同で防災訓練"), 1)

    def test_sports_still_routed_and_disaster_section_kept(self):
        msg, sports = self.run_digest(["北本市の中学校 野球部が県大会で優勝 - 埼玉新聞", "北本市で秋まつり開催 - 埼玉新聞"])
        self.assertIn("## 🚨 防災・緊急情報", msg)
        self.assertIn("北本市で秋まつり開催", msg)
        self.assertEqual(len(sports), 1)
        self.assertNotIn("野球部", msg)

    def test_direct_feeds_kept(self):
        msg, _ = self.run_digest([], direct={fn.KITAMOTO_NOTE_RSS: [
            {"title": "【広報きたもと10月号】特集", "url": NOTE, "published": "10/01 09:00"}]})
        self.assertIn("広報きたもと10月号", msg)

    def test_queries_target_core_and_nearby_only(self):
        import urllib.parse
        core = urllib.parse.unquote(fn.GOOGLE_NEWS_SAITAMA_RSS)
        near = urllib.parse.unquote(fn.GOOGLE_NEWS_NEARBY_RSS)
        self.assertIn("北本市", core)
        self.assertNotIn("大宮区", core + near)
        for city in ("上尾市", "久喜市", "伊奈町"):
            self.assertIn(city, near)


if __name__ == "__main__":
    unittest.main()
