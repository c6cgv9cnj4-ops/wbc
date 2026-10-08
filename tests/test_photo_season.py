"""週末撮影予報の季節判定(photo_season_judge / photo_live_info / check_photo_season)のテスト。
日付は固定。公式サイトへは接続しない(取得処理は偽のfetchで検証する)。
実行: .venv/bin/python -m unittest tests.test_photo_season
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import check_photo_season as cps  # noqa: E402
import photo_live_info as pli  # noqa: E402
import photo_season_judge as psj  # noqa: E402

D = dt.date
JINDAI_ROW = [30, 1, "神代植物公園", "東京都調布市深大寺元町5-15-1", 35.6678, 139.5489,
              "梅（2月）、ばら園（5-6月, 10月）、大温室（通年）、ハナショウブ（6月）", "ばら園テラス・大温室", [0] * 12, "20015",
              "https://www.tokyo-park.or.jp/jindai/"]

LIVE_JINDAI = {
    "subject_keywords": ["バラ", "ばら"], "event_name": "秋のバラフェスタ", "event_start": "2026-10-06", "event_end": "2026-11-23",
    "bloom_status": "", "bloom_note": "公式案内: バラの花期は年2回。春は5月中旬の頃が盛りで、秋は10月中旬からです。",
    "source_url": "https://www.tokyo-park.or.jp/park/jindai/news/2026/2025_2.html", "source_kind": "official", "checked_at": "2026-10-08",
}


def weekend_of(day):
    return psj.target_weekend(day)


class SeasonParseTest(unittest.TestCase):
    def seasons(self, detail):
        return [(s.m_start, s.m_end, s.p_start, s.p_end, s.qualifier) for s in psj.parse_seasons(detail)]

    def test_multiple_seasons_in_one_subject(self):
        self.assertEqual(self.seasons("5-6月, 10月"), [(5, 6, None, None, ""), (10, 10, None, None, "")])

    def test_list_of_months(self):
        self.assertEqual(self.seasons("2,10月"), [(2, 2, None, None, ""), (10, 10, None, None, "")])

    def test_qualifiers_per_season(self):
        self.assertEqual(self.seasons("緑:7-8月, 紅葉:10月"), [(7, 8, None, None, "緑"), (10, 10, None, None, "紅葉")])
        self.assertEqual(self.seasons("曼珠沙華・9-10月"), [(9, 10, None, None, "曼珠沙華")])

    def test_ten_day_periods_and_cross_year(self):
        s = psj.parse_seasons("約40種450〜500本・2月中旬-3月上旬")[0]
        self.assertEqual(s.window(2026), (D(2026, 2, 11), D(2026, 3, 10)))
        w = psj.parse_seasons("12-2月")[0]
        self.assertEqual(w.window(2026), (D(2026, 12, 1), D(2027, 2, 28)))
        self.assertEqual(psj.parse_seasons("3月下旬")[0].window(2026), (D(2026, 3, 21), D(2026, 3, 31)))

    def test_year_round_has_no_season(self):
        self.assertEqual(psj.parse_seasons("通年"), [])
        self.assertEqual(psj.parse_seasons("通年安定"), [])

    def test_segments_label_and_skip(self):
        segs = psj.parse_subject_segments(JINDAI_ROW[6])
        self.assertEqual([s["label"] for s in segs], ["梅", "ばら園", "ハナショウブ"])      # 大温室(通年)は季節判定の対象外
        self.assertEqual(len(segs[1]["seasons"]), 2)                                       # 春と秋の2回


class WeekendTest(unittest.TestCase):
    def test_target_weekend(self):
        self.assertEqual(psj.target_weekend(D(2026, 10, 8)), (D(2026, 10, 10), D(2026, 10, 11)))      # 木曜実行 → 直近の土日
        self.assertEqual(psj.target_weekend(D(2026, 10, 10)), (D(2026, 10, 10), D(2026, 10, 11)))     # 土曜は当日
        self.assertEqual(psj.target_weekend(D(2026, 10, 11)), (D(2026, 10, 17), D(2026, 10, 18)))     # 日曜は次の週末


class SeasonalStatusTest(unittest.TestCase):
    def status(self, row_detail, day):
        segs = psj.parse_subject_segments(row_detail)
        return psj.seasonal_status(segs[0]["seasons"], weekend_of(day))[0]

    def test_rose_garden_spring_and_autumn(self):
        d = "ばら園（5-6月, 10月）"
        self.assertEqual(self.status(d, D(2026, 5, 14)), "in_season")        # 春
        self.assertEqual(self.status(d, D(2026, 10, 8)), "in_season")        # 秋
        self.assertIsNone(self.status(d, D(2026, 8, 6)))                     # 見頃期間外
        self.assertEqual(self.status(d, D(2026, 9, 24)), "upcoming")         # 秋の直前
        self.assertEqual(self.status(d, D(2027, 5, 13)), "in_season")        # 翌年の春も同じ仕組みで扱える

    def test_cross_year_winter(self):
        self.assertEqual(self.status("イルミネーション（11-2月）", D(2027, 1, 7)), "in_season")
        self.assertEqual(self.status("イルミネーション（11-2月）", D(2026, 12, 3)), "in_season")
        self.assertIsNone(self.status("イルミネーション（11-2月）", D(2026, 7, 2)))

    def test_judged_by_target_weekend_not_run_day(self):
        # 実行日(木)は9月でも、対象週末(土日)が10月に入っていれば10月の見頃として扱う。逆も同様
        self.assertEqual(self.status("コスモス（10月）", D(2026, 9, 30)), "in_season")      # 実行日9/30 → 対象週末 10/3-4
        self.assertIsNone(self.status("コスモス（9月）", D(2026, 9, 30)))


class JudgeTest(unittest.TestCase):
    seg = psj.parse_subject_segments(JINDAI_ROW[6])[1]       # ばら園

    def judge(self, today, live=(LIVE_JINDAI,), failed=()):
        return psj.judge_segment(self.seg, list(live), psj.target_weekend(today), today, set(failed))

    def test_target_weekend_inside_official_event_period(self):
        j = self.judge(D(2026, 10, 8))
        self.assertEqual((j["verdict"], j["basis"], j["confidence"]), ("verified_now", "最新情報", "高"))

    def test_before_the_event_starts(self):
        j = self.judge(D(2026, 10, 1))                          # 対象週末 10/3-4 は開催前(10/6開始)
        self.assertEqual(j["verdict"], "verified_upcoming")

    def test_after_the_event_ends(self):
        self.assertEqual(self.judge(D(2026, 11, 26))["verdict"], "verified_ended")      # 11/28-29 は終了後(7日以内)

    def test_festival_covers_weekend_outside_the_usual_month(self):
        j = psj.judge_segment({"label": "ばら園（秋）", "seasons": psj.parse_seasons("10月")}, [LIVE_JINDAI], psj.target_weekend(D(2026, 11, 19)), D(2026, 11, 19))
        self.assertEqual(j["verdict"], "verified_now")                                    # 11/21-22 は月単位では10月の外だが、公式の開催期間内

    def test_spring_weekend_ignores_the_autumn_event_and_stays_typical(self):
        j = self.judge(D(2027, 5, 13))
        self.assertEqual((j["verdict"], j["basis"], j["confidence"]), ("typical_now", "例年の見頃", "低"))

    def test_no_live_info_is_typical_only(self):
        j = self.judge(D(2026, 10, 8), live=())
        self.assertEqual((j["verdict"], j["basis"], j["confidence"]), ("typical_now", "例年の見頃", "低"))
        self.assertNotIn("現在見頃", psj.VERDICT_TEXT["typical_now"])

    def test_out_of_season_without_info_makes_no_claim(self):
        j = self.judge(D(2026, 8, 6), live=())
        self.assertIsNone(j["verdict"])                                                   # 『見頃ではない』と断定もしない(主張しない)

    def test_stale_official_info_lowers_confidence(self):
        old = dict(LIVE_JINDAI, checked_at="2026-09-20")
        j = self.judge(D(2026, 10, 8), live=(old,))
        self.assertEqual((j["verdict"], j["basis"], j["confidence"], j["stale"]), ("verified_now", "最新情報(古い)", "低", True))

    def test_fetch_failure_lowers_confidence(self):
        j = self.judge(D(2026, 10, 8), failed=(LIVE_JINDAI["source_url"],))
        self.assertEqual((j["basis"], j["confidence"], j["fetch_failed"]), ("最新情報(古い)", "低", True))

    def test_bloom_status_over(self):
        j = self.judge(D(2026, 10, 8), live=(dict(LIVE_JINDAI, bloom_status="見頃過ぎ"),))
        self.assertEqual(j["verdict"], "verified_ended")


class OfficialPageTest(unittest.TestCase):
    NEWS = ("リーフレットを片手に10月6日(火)～11月23日(月祝)まで開催される「秋のバラフェスタ」をより一層お楽しみくださいませ。\n"
            "※バラの花期は年２回。春は５月中旬の頃が盛りで、秋は10月中旬からです。秋のバラは花が引き締まり色が濃く鮮明です。")
    TOP = '<a href="/park/jindai/news/2026/2025_2.html">神代植物公園のバラのリーフレット（2026）</a><a href="/event_search/10_2.html">神代植物公園「秋のバラフェスタ」10/６（火）～開催！</a>'

    def fetch(self, url):
        if url.endswith("/jindai/"):
            return self.TOP
        if url.endswith("2025_2.html"):
            return f"<html><body><p>{self.NEWS}</p></body></html>"
        if url.endswith("10_2.html"):
            return "<html><body>秋のバラフェスタ 10/６（火）～開催</body></html>"
        raise RuntimeError("404")

    def test_find_event_period(self):
        start, end, _ = pli.find_event_period(self.NEWS, "バラフェスタ", D(2026, 10, 8))
        self.assertEqual((start, end), (D(2026, 10, 6), D(2026, 11, 23)))
        self.assertIsNone(pli.find_event_period("10月6日～11月23日 別のイベント", "バラフェスタ", D(2026, 10, 8)))

    def test_adapter_reads_official_period_with_source_and_date(self):
        e = pli.jindai_autumn_rose(self.fetch, D(2026, 10, 8))
        self.assertEqual((e["event_start"], e["event_end"], e["checked_at"]), ("2026-10-06", "2026-11-23", "2026-10-08"))
        self.assertTrue(e["source_url"].startswith("https://www.tokyo-park.or.jp/"))
        self.assertIn("秋は10月中旬から", e["bloom_note"])

    def test_adapter_failure_is_reported_and_keeps_old_info(self):
        store = pli.empty_store()
        pli.upsert_entry(store, 30, "神代植物公園", dict(LIVE_JINDAI, checked_at="2026-09-30"))
        ok, failed = pli.refresh_spots(store, [30, 31], D(2026, 10, 8), fetch_text=lambda u: (_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual((ok, list(failed)), ([], ["30"]))                                # アダプターの無い31は対象外
        self.assertEqual(pli.entries_for(store, 30)[0]["checked_at"], "2026-09-30")       # 既存の最新情報は消えず、確認日が古いまま
        _, failed2 = pli.refresh_spots(store, [30], D(2026, 10, 8), fetch_text=lambda u: "<html>バラの記載なし</html>")
        self.assertIn("30", failed2)                                                      # 開催期間が読めない=推測せず失敗扱い

    def test_refresh_updates_same_event_instead_of_duplicating(self):
        store = pli.empty_store()
        pli.refresh_spots(store, [30], D(2026, 10, 8), fetch_text=self.fetch)
        pli.refresh_spots(store, [30], D(2026, 10, 9), fetch_text=self.fetch)
        entries = pli.entries_for(store, 30)
        self.assertEqual((len(entries), entries[0]["checked_at"]), (1, "2026-10-09"))


class SpotEntryTest(unittest.TestCase):
    def test_jindai_verified_entry_shows_basis_period_date_and_source(self):
        e = cps.build_spot_entry(JINDAI_ROW, D(2026, 10, 8), [LIVE_JINDAI])
        self.assertEqual((e["status_key"], e["verdict"]), ("verified", "verified_now"))
        text = "\n".join(f for fld in cps.build_spot_fields(e, D(2026, 10, 8)) for f in fld["value"].split("\n"))
        for expect in ("🌸 **ばら園**（公式で確認：開催中・見頃の対象）", "根拠: 最新情報・公式 ／ 確認日 10/08", "イベント: 秋のバラフェスタ 10/06〜11/23",
                       "秋は10月中旬からです", "出典: [公式ページ](https://www.tokyo-park.or.jp/park/jindai/news/2026/2025_2.html)"):
            self.assertIn(expect, text)

    def test_without_official_info_is_marked_unverified(self):
        e = cps.build_spot_entry(JINDAI_ROW, D(2026, 10, 8), [])
        self.assertEqual(e["status_key"], "typical")
        text = "\n".join(fld["value"] for fld in cps.build_spot_fields(e, D(2026, 10, 8)))
        self.assertIn("例年の見頃（公式情報未確認）", text)
        self.assertIn("公式の開花状況・開催期間は未確認", text)
        self.assertNotIn("現在見頃", text)

    def test_spring_weekend_uses_typical_even_with_autumn_live_info(self):
        e = cps.build_spot_entry(JINDAI_ROW, D(2027, 5, 13), [LIVE_JINDAI])
        self.assertEqual(e["verdict"], "typical_now")

    def test_out_of_season_spot_is_not_listed(self):
        self.assertIsNone(cps.build_spot_entry(JINDAI_ROW, D(2026, 8, 6), []))

    def test_live_only_subject_is_listed(self):
        row = list(JINDAI_ROW)
        row[6] = "大温室（通年）"
        e = cps.build_spot_entry(row, D(2026, 10, 8), [dict(LIVE_JINDAI, subject="秋バラ")])
        self.assertEqual(e["verdict"], "verified_now")
        self.assertEqual(e["judgements"][0]["label"], "秋バラ")

    def test_season_specific_label(self):
        row = [1, 4, "国営ひたち海浜公園", "x", 36.4, 140.6, "コキア（緑:7-8月, 紅葉:10月）", "", [0] * 12, "", ""]
        self.assertEqual(cps.build_spot_entry(row, D(2026, 10, 8), [])["judgements"][0]["label"], "コキア（紅葉）")
        self.assertEqual(cps.build_spot_entry(row, D(2026, 7, 16), [])["judgements"][0]["label"], "コキア（緑）")


class AllSpotsTest(unittest.TestCase):
    """photo/index.html の実データ全スポットで、判定が壊れず、公式未確認を『確認済み』にしないことを確認する。"""

    @classmethod
    def setUpClass(cls):
        cls.rows = cps.load_raw_spots()

    def test_all_spots_every_weekend_of_year(self):
        day = D(2026, 1, 1)
        while day < D(2027, 1, 1):
            for r in self.rows:
                e = cps.build_spot_entry(r, day, [])
                if e:
                    self.assertIn(e["verdict"], ("typical_now", "typical_upcoming"), (r[2], day))     # 公式情報なしで verified にならない
                    for j in e["judgements"]:
                        self.assertNotRegex(j["label"], r"[,，]\)|（\s*）|\(\s*\)|^\s*$", (r[2], j["label"]))
            day += dt.timedelta(days=7)

    def test_labels_are_clean_for_all_spots(self):
        for r in self.rows:
            for seg in psj.parse_subject_segments(r[6]):
                self.assertNotIn(",）", seg["label"])
                self.assertNotIn("（）", seg["label"])
                self.assertTrue(seg["seasons"])

    def test_payload_fits_discord_limits_and_never_says_now_peak_for_unverified(self):
        day = D(2026, 10, 8)
        entries = [e for e in (cps.build_spot_entry(r, day, []) for r in self.rows) if e]
        entries.sort(key=lambda e: (cps.VERDICT_RANK[e["verdict"]], e["dist_km"]))
        payloads = cps.build_discord_payloads(entries[:10], day, max(0, len(entries) - 10))
        for p in payloads:
            emb = p["embeds"][0]
            self.assertLessEqual(len(emb["title"]) + len(emb["description"]) + sum(len(f["name"]) + len(f["value"]) for f in emb["fields"]), 6000)
            for f in emb["fields"]:
                self.assertLessEqual(len(f["value"]), 1024)
        text = str(payloads)
        self.assertNotIn("現在見頃", text)
        self.assertNotIn("公式で確認 1件", text)


if __name__ == "__main__":
    unittest.main()
