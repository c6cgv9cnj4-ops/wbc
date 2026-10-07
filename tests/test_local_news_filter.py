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
        ("【上尾市】衝撃…ガチャが消えた。 人気店『#C-pla』が異例の全店一斉休業へ… - 号外NET", ""),         # 上尾は通常優先
        ("【上尾市】24時間いつでも『お店の味』が買える！？ホルモン焼屋さんの驚きの自販機", GOGUY_AO),
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
        "【伊奈町】伊奈町の隠れ家『CAFE THE GARDEN』が7周年！お宝マーケットが開催されます！！ - 号外NET",
        "蓮田市の公立保育園が休園へ - 埼玉新聞",
        "北上尾 一棟マンション - rakumachi.jp",
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

    def test_nearby_needs_high_impact_and_ageo_is_core(self):
        self.assertTrue(keep("久喜市長が予算削減の方針を発表 - 読売新聞"))
        self.assertTrue(keep("上尾市にオープンしたカフェに行ってきた - ブログ"))        # 上尾は通常優先
        self.assertFalse(keep("伊奈町で小学校の運動会"))                                # 周辺自治体の通常ニュースは抑える
        self.assertEqual(lf.classify("伊奈町で竜巻 住宅に被害")[2], "伊奈町")

    def test_direct_feed_defaults(self):
        self.assertEqual(lf.classify("ふみだスコーレのご報告", NOTE), (True, "keep:direct_feed", "北本"))
        self.assertTrue(keep("ホルモン焼屋さんの自販機がとっても便利", GOGUY_AO))       # 上尾・桶川版は通常優先
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
            nearby_titles=["上尾市議会が決算を審議 - 埼玉新聞", "蓮田市の人気ラーメン店が閉店 - ブログ"])
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


class NearbyImpactTest(unittest.TestCase):
    """周辺自治体・県全体は、自治体名ではなく会話材料になる規模・重要性のシグナルで決める。"""

    def test_kuki_90oku_cut_is_adopted(self):
        for t in ("埼玉 財政再建の久喜市で事業見直し 90億円削減へ - NHKニュース",
                  "埼玉・久喜市長「苦渋の決断」、予算９０億円削減・防災施設の整備は延期 - 読売新聞",
                  "44事業見直しへ、削減効果90億円 財政再建取り組む久喜市 埼玉 [埼玉県] - 朝日新聞"):
            ok, reason, region = lf.classify(t)
            self.assertTrue(ok, t)
            self.assertIn("nearby_high_impact", reason)
            self.assertEqual(region, "久喜")

    def test_hasuda_ordinary_news_is_suppressed(self):
        for t in ("蓮田市の公立保育園が休園へ - 埼玉新聞", "【蓮田市】新しいカフェがオープン！ - 号外NET", "蓮田市の小学校で運動会 - 埼玉新聞"):
            self.assertEqual(lf.classify(t)[1], "drop:nearby_normal_news", t)

    def test_not_a_fixed_rule_by_municipality(self):
        self.assertTrue(keep("蓮田市で大規模停電 約3000戸 - 埼玉新聞"))                  # 蓮田でも重大なら採用
        self.assertFalse(keep("久喜市の新しいカフェがオープン - ブログ"))                 # 久喜でも通常なら抑える
        self.assertFalse(keep("【久喜市】いよいよ明日が営業最終日。「ピザハット 久喜テラレス店」が閉店 - 号外NET"))

    def test_signal_categories(self):
        cases = {"加須市で竜巻 住宅の屋根が飛ぶ": "災害", "白岡市で死亡事故 トラックと衝突": "重大事故・事件",
                 "久喜市が行政改革プラン 職員削減へ": "財政・行政改革", "幸手市で新制度を導入へ 全世帯対象": "制度・施策",
                 "加須市 路線バスが運休 ダイヤ改正で": "交通・施設・住民影響", "行田市で重大な不祥事 緊急会見": "規模・緊急"}
        for t, cat in cases.items():
            self.assertIn(cat, lf.impact_categories(t), t)
            self.assertTrue(keep(t), t)

    def test_amount_threshold_and_fullwidth_digits(self):
        self.assertIn("財政・行政改革", lf.impact_categories("予算９０億円削減"))
        self.assertEqual(lf.impact_categories("事業費5億円を計上"), [])
        self.assertEqual(lf.impact_categories("蓮田市が保育園の運営を見直し"), [])

    def test_prefecture_wide_policy(self):
        ok, reason, region = lf.classify("埼玉県が来年度から新制度を導入へ 全県で - 埼玉新聞")
        self.assertTrue(ok)
        self.assertEqual((region, reason.split("(")[0]), ("埼玉県", "keep:prefecture_high_impact"))
        self.assertEqual(lf.classify("【埼玉県】「こどもの居場所フェア埼玉」を開催します - ニコニコニュース")[1].split(":")[1][:10], "pr_or_aggr")
        self.assertEqual(lf.classify("埼玉県を代表するリサイクルショップ 月間大賞者発表 - 埼玉新聞")[1], "drop:prefecture_normal_news")
        self.assertEqual(lf.classify("さいたま市で大規模火災 埼玉県 - 埼玉新聞")[1], "drop:region_not_kitamoto_area")   # 特定の他市の話題

    def test_existing_noise_rules_still_apply(self):
        self.assertFalse(keep("久喜市で中古車入荷しました 年式2017 - 中古車のガリバー"))
        self.assertFalse(keep("鴻巣市にもあるケンタッキーが新商品を発売"))
        self.assertFalse(keep("震度5 埼玉県久喜市 地震詳細"))
        self.assertFalse(keep("【開催報告】久喜市の地域活性化イベント 大規模に開催 - PR TIMES"))     # PR/転載は中心の自治体+地域の手がかりが必要

    def test_core_unchanged(self):
        self.assertTrue(keep("北本市の小学校で運動会 - 埼玉新聞"))
        self.assertTrue(keep("鴻巣市議会が補正予算を可決 - 埼玉新聞"))
        self.assertTrue(keep("桶川市の公民館でイベント"))
        self.assertTrue(keep("上尾市議会が決算を審議 - 埼玉新聞"))
        self.assertEqual(lf.classify("鴻巣市と上尾市が合同で防災訓練")[2], "鴻巣")


REAL_ADMIN = ("懲戒処分…仕事を4カ月放置した市職員 主査級42歳 さらに決裁のない書類も発行、公印を不正使用、上司にウソを報告し免職に "
              "主幹級の職員は確認不足、“1366万円分”のミス - 埼玉新聞")


class AdminIssueTest(unittest.TestCase):
    """北本市行政の問題・不祥事・ミスは最優先(2026-10-07)。市役所のイベント・募集・制度案内は優先しない。"""

    ADMIN = [
        "北本市職員を懲戒処分 公印を不正使用 - 埼玉新聞",
        "北本市が1366万円を返還 事務処理ミスで - 朝日新聞",
        "北本市、給付金を誤って支給 市が謝罪し再発防止策 - 東京新聞",
        "北本市が国民健康保険税の算定誤り 過大徴収分を返還へ - 埼玉新聞",
        "北本市教育委員会が誤通知 保護者に訂正と謝罪 - 毎日新聞",
        "北本市役所で個人情報が漏えい 市が公表 - 読売新聞",
        "北本市の指定管理者選定に問題 市議会で追及 - 埼玉新聞",
    ]
    ORDINARY = [
        "北本市役所で市民向けイベントを開催 - 埼玉新聞",
        "北本市が子育て支援の新制度を案内 申請受付中 - 広報",
        "北本市職員が募集 来年度採用試験の案内 - 市公式",
        "北本市長が小学校を訪問 - 埼玉新聞",
        "ミス・ユニバース候補が北本市を訪問 - 埼玉新聞",
    ]

    def test_admin_issue_is_top_priority(self):
        for t in self.ADMIN:
            with self.subTest(t=t):
                ok, reason, region = lf.classify(t)
                self.assertTrue(ok, reason)
                self.assertEqual(reason, "keep:kitamoto_admin_issue")
                self.assertEqual(lf.priority_level(t, reason, region), lf.LEVEL_ADMIN_ISSUE)

    def test_ordinary_city_hall_news_is_not_elevated(self):
        for t in self.ORDINARY:
            with self.subTest(t=t):
                ok, reason, region = lf.classify(t)
                self.assertNotEqual(reason, "keep:kitamoto_admin_issue")

    def test_other_municipality_admin_issue_not_elevated(self):
        for t in ("鴻巣市職員を懲戒処分 公印を不正使用 - 埼玉新聞", "桶川市が事務処理ミス 市が謝罪 - 埼玉新聞", "蓮田市が誤通知 謝罪 - 埼玉新聞"):
            with self.subTest(t=t):
                self.assertNotEqual(lf.classify(t)[1], "keep:kitamoto_admin_issue")
                self.assertNotEqual(lf.classify(t, via_admin_query=True)[1], "keep:kitamoto_admin_issue(via_query)")

    def test_title_without_city_name_needs_the_query_origin(self):
        # 実在の報道(2026-05-25 埼玉新聞): タイトルに市名が無い。通常経路では採用せず、『北本市』検索由来の場合だけ採用する
        self.assertFalse(lf.classify(REAL_ADMIN)[0])
        ok, reason, region = lf.classify(REAL_ADMIN, via_admin_query=True)
        self.assertEqual((ok, reason, region), (True, "keep:kitamoto_admin_issue(via_query)", "北本"))
        self.assertFalse(lf.classify("さいたま市職員を懲戒処分 公印を不正使用 - 埼玉新聞", via_admin_query=True)[0])   # 他の市名が出る
        self.assertFalse(lf.classify("市役所で市民向けイベントを開催 - 埼玉新聞", via_admin_query=True)[0])         # 問題語が無い

    def test_official_note_needs_a_strong_issue_word(self):
        self.assertEqual(lf.classify("事務処理ミスのお詫びと再発防止について", NOTE)[1], "keep:kitamoto_admin_issue")
        self.assertEqual(lf.classify("イベント案内のお詫びと訂正", NOTE)[1], "keep:direct_feed")   # 軽微な訂正は通常扱い

    def test_noise_rules_still_win(self):
        self.assertFalse(keep("北本市の中古車ミス 入荷しました 年式2017 - 中古車のガリバー"))

    def test_priority_levels(self):
        pl = lambda t, u="": lf.priority_level(t, *[lf.classify(t, u)[i] for i in (1, 2)])
        self.assertEqual(pl("北本市の小学校で運動会 - 埼玉新聞"), lf.LEVEL_KITAMOTO_NORMAL)
        self.assertEqual(pl("北本市が新庁舎の整備計画を発表 - 埼玉新聞"), lf.LEVEL_KITAMOTO_MAJOR)
        self.assertEqual(pl("北本市で停電 約3000戸 - 埼玉新聞"), lf.LEVEL_KITAMOTO_MAJOR)
        self.assertEqual(pl("鴻巣市の小学校で稲刈り体験"), lf.LEVEL_NEAR_CORE)
        self.assertEqual(pl("久喜市が事業を見直し 90億円削減 - 埼玉新聞"), lf.LEVEL_RESCUED)
        self.assertLess(lf.LEVEL_ADMIN_ISSUE, lf.LEVEL_KITAMOTO_MAJOR)
        self.assertLess(lf.LEVEL_KITAMOTO_MAJOR, lf.LEVEL_KITAMOTO_NORMAL)
        self.assertLess(lf.LEVEL_KITAMOTO_NORMAL, lf.LEVEL_NEAR_CORE)
        self.assertLess(lf.LEVEL_NEAR_CORE, lf.LEVEL_RESCUED)

    def test_far_municipality_excluded_except_wide_area_major(self):
        self.assertEqual(lf.classify("さいたま市で新店オープン - 号外NET")[1], "drop:region_not_kitamoto_area")
        self.assertEqual(lf.classify("さいたま市で大規模火災 埼玉県 - 埼玉新聞")[1], "drop:region_not_kitamoto_area")
        ok, reason, region = lf.classify("大雨で県内広域に避難指示 さいたま市や川口市でも浸水被害 - 埼玉新聞")
        self.assertTrue(ok, reason)
        self.assertTrue(reason.startswith("keep:far_high_impact"))


class AdminDigestTest(unittest.TestCase):
    """build_local_news_message: 行政問題が先頭・スポーツ側や通常記事より優先。既存の経路(ゆかり型・防災・スポーツ)は従来どおり。"""

    def run_digest(self, core=(), admin=(), nearby=(), origin=()):
        def fake_rss(url, limit=10):
            titles = {fn.GOOGLE_NEWS_ADMIN_RSS: admin, fn.GOOGLE_NEWS_NEARBY_RSS: nearby}.get(url)
            if url == fn.GOOGLE_NEWS_ORIGIN_RSS:
                return list(origin)
            titles = core if titles is None else titles
            return [{"title": t, "url": f"https://news.google.com/rss/articles/{abs(hash(t))}", "published": "10/06 08:00"} for t in titles]
        with mock.patch.dict(os.environ, {"ANZN_SOURCE": "mac"}), \
             mock.patch.object(fn, "fetch_rss_items", side_effect=fake_rss), \
             mock.patch.object(fn, "fetch_direct_rss_items", return_value=[]), \
             mock.patch.object(fn.mastodon_anzn, "fetch_recent_items", return_value=[]):
            return fn.build_local_news_message({}, NOW)

    def lines(self, msg):
        return [ln for ln in msg.split("\n") if ln.startswith("- [")]

    def test_order(self):
        origin = [{"title": "アジア大会の金メダル第1号は北條巧 - 朝日新聞", "url": "https://news.google.com/rss/articles/o1", "published": ""}]
        msg, _ = self.run_digest(
            core=["鴻巣市の小学校で稲刈り体験 - 埼玉新聞", "北本市の小学校で運動会 - 埼玉新聞", "北本市が新庁舎の整備計画を発表 - 埼玉新聞"],
            admin=[REAL_ADMIN, "北本市職員を懲戒処分 公印を不正使用 - 埼玉新聞"],
            nearby=["久喜市が事業を見直し 90億円削減 - 埼玉新聞", "蓮田市の人気ラーメン店が閉店 - ブログ"], origin=origin)
        ls = self.lines(msg)
        order = [next(i for i, ln in enumerate(ls) if k in ln) for k in
                 ("1366万円", "懲戒処分 公印", "北條巧", "新庁舎", "運動会", "稲刈り", "90億円")]
        self.assertEqual(order, sorted(order), ls)
        self.assertNotIn("ラーメン", msg)                      # 周辺の通常ニュースは従来どおり採用しない

    def test_admin_issue_not_diverted_to_sports_or_noise(self):
        msg, sports = self.run_digest(core=["北本市教育委員会が部活動の補助金を過大支給 市が謝罪 - 埼玉新聞"])
        self.assertIn("過大支給", msg)
        self.assertEqual(sports, [])

    def test_admin_same_headline_deduped(self):
        msg, _ = self.run_digest(admin=[REAL_ADMIN, REAL_ADMIN.replace(" - 埼玉新聞", " - 別媒体")])
        self.assertEqual(msg.count("1366万円"), 1)

    def test_unrelated_query_hits_not_adopted(self):
        msg, _ = self.run_digest(core=[], admin=["さいたま市職員を懲戒処分 公印を不正使用 - 埼玉新聞", "全国の自治体で不適切会計 - 共同通信"])
        self.assertIsNone(msg)


if __name__ == "__main__":
    unittest.main()
