"""バドミントン大会マスター(badminton_tournament_master.py)のテスト。通信は使わない(Fetcherを差し替える)。
実データの抜粋(BWFランキング規程のポイント表・M&Qエントリー一覧)を tests/fixtures に置いている。
実行: .venv/bin/python -m unittest tests.test_badminton_tournament_master
"""
import copy
import datetime as dt
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import badminton_tournament_master as bm  # noqa: E402
import fetch_badminton_alerts as fb  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 8, 12, 0, tzinfo=JST)
TITLE = "【アークティックオープン2026】渡邉航貴が台湾のベテラン選手に勝利！田中湧士、沖本優大も2回戦進出！＜2日目結果＞"
ALIASES = bm.load_aliases()
SPONSORS = bm.load_alias_file()["sponsor_words"]


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return f.read()


def cal_html(rows):
    """rows: [(月名, id, 週, 国, '06 -11', 大会名, 大会格, 都市, [pdf])] → BWFカレンダーと同じ構造のHTML。"""
    out, cur = [], None
    for month, cid, wk, cc, days, name, cat, city, docs in rows:
        if month != cur:
            out.append(f"<h2>{month}</h2>")
            cur = month
        links = "".join(f'<a href="{d}">doc</a>' for d in docs)
        out.append(
            f'<table><tr class="bg-lightgray-future"><td>{wk}</td><td><div class="country_code">{cc}</div></td><td> {days} </td>'
            f'<td><a class="show bwf-calendar_expander" data-target="#{cid}">{name}</a></td><td>-</td><td>{cat}</td><td>{city}</td></tr>'
            f'<tr class="tr-tournament-detail" id="{cid}"><td colspan="7">$500,000 PRIZE MONEY {links}</td></tr></table>')
    return "<html><body>" + "".join(out) + "</body></html>"


ROWS_2026 = [
    ("SEPTEMBER", "5500", 36, "CHN", "01 -06", "LI-NING China Masters 2026 powered by X", "HSBC BWF World Tour Super 750", "Shenzhen", []),
    ("SEPTEMBER", "5501", 40, "CHN", "29 -04", "Ruichang China Masters 2026", "BWF Tour Super 100", "Ruichang", []),
    ("OCTOBER", "5594", 41, "FIN", "06 -11", "CLASH OF CLANS Arctic Open 2026 powered by YONEX", "HSBC BWF World Tour Super 500", "Vantaa",
     ["https://example.com/docs/Prospectus.pdf", "https://example.com/docs/M_QVER~1.PDF"]),
    ("OCTOBER", "5595", 42, "MAC", "13 -18", "SANDS CHINA LTD. Macau Open 2026", "HSBC BWF World Tour Super 300", "Macau", []),
    ("OCTOBER", "5596", 42, "GER", "20 -25", "YONEX German Open 2026", "HSBC BWF World Tour Super 300", "Mulheim", []),
    ("OCTOBER", "5597", 43, "GER", "27 -01", "HYLO Open 2026", "HSBC BWF World Tour Super 500", "Saarbrucken", []),
    ("OCTOBER", "5598", 43, "EGY", "12 -17", "VICTOR BWF World Junior Championships 2026", "Grade 1 – Individual Junior Tournaments", "Cairo", []),
    ("NOVEMBER", "5599", 45, "CHN", "10 -15", "Cancelled Test Open 2026 (Cancelled)", "HSBC BWF World Tour Super 300", "X", []),
    ("DECEMBER", "5600", 50, "CHN", "09 -13", "HSBC BWF World Tour Finals 2026", "HSBC BWF World Tour Finals", "Hangzhou", []),
    ("DECEMBER", "5601", 51, "MAS", "02 -06", "PERODUA Malaysia Open 2026", "HSBC BWF World Tour Super 1000", "Kuala Lumpur", []),
]
STATUTES_HTML = '<a href="https://x/docs/Section%205.3.3.1%20-%20World%20Ranking%20System%20-%2026%20April%202026%20V6.0.pdf">x</a>'
PTS_URL = "https://x/docs/Section%205.3.3.1%20-%20World%20Ranking%20System%20-%2026%20April%202026%20V6.0.pdf"


class FakeFetcher:
    def __init__(self, year_rows=None, fail=False):
        self.year_rows = year_rows if year_rows is not None else {2026: ROWS_2026}
        self.calls = []
        self.fail = fail
        self.last_modified = "Thu, 07 May 2026 08:28:22 GMT"
        self.statutes = STATUTES_HTML
        self.pdf_text = {PTS_URL: fixture("bwf_world_ranking_system_excerpt.txt"), "https://example.com/docs/M_QVER~1.PDF": fixture("bwf_entry_list_excerpt.txt"),
                         "https://example.com/docs/Prospectus.pdf": "募集要項 Prospectus"}

    def text(self, url):
        self.calls.append(("text", url))
        if self.fail:
            raise RuntimeError("offline")
        if url == bm.BWF_STATUTES_URL:
            return self.statutes
        for y, rows in self.year_rows.items():
            if url == bm.BWF_CALENDAR_URL.format(year=y):
                return cal_html(rows)
        return "<html></html>"

    def bytes(self, url):
        self.calls.append(("bytes", url))
        if self.fail:
            raise RuntimeError("offline")
        return url.encode(), {"last_modified": self.last_modified, "content_length": 100}

    def head(self, url):
        self.calls.append(("head", url))
        if self.fail:
            raise RuntimeError("offline")
        return {"last_modified": self.last_modified, "content_length": 100}

    def n(self, kind=None, contains=""):
        return sum(1 for k, u in self.calls if (kind is None or k == kind) and contains in u)


def make(fetcher=None, now=NOW, master=None):
    f = fetcher or FakeFetcher()
    svc = bm.TournamentMaster(master=master if master is not None else bm.empty_master(), fetcher=f, now=now, aliases=ALIASES, sponsor_words=SPONSORS,
                              ja_names={bm.token_key("WATANABE Koki"): "渡邉　航貴", bm.token_key("TANAKA Yushi"): "田中湧士"})
    return svc, f


def patch_pdf():
    return mock.patch.object(bm, "pdf_to_text", side_effect=lambda data: {PTS_URL.encode(): fixture("bwf_world_ranking_system_excerpt.txt"),
                                                                           b"https://example.com/docs/M_QVER~1.PDF": fixture("bwf_entry_list_excerpt.txt"),
                                                                           b"https://example.com/docs/Prospectus.pdf": "募集要項"}[data])


class ParseTest(unittest.TestCase):
    def test_calendar_rows(self):
        rows = bm.parse_calendar_html(cal_html(ROWS_2026), 2026)
        by = {r["id"]: r for r in rows}
        a = by["5594"]
        self.assertEqual((a["start_date"], a["end_date"], a["country_code"], a["city"]), ("2026-10-06", "2026-10-11", "FIN", "Vantaa"))
        self.assertEqual((a["category_text"], a["prize_money"]), ("HSBC BWF World Tour Super 500", "$500,000"))
        self.assertEqual(len(a["docs"]), 2)
        self.assertEqual((by["5501"]["start_date"], by["5501"]["end_date"]), ("2026-09-29", "2026-10-04"))     # 月またぎ
        self.assertEqual(by["5597"]["end_date"], "2026-11-01")
        self.assertEqual(len(rows), len(ROWS_2026))

    def test_points_table_from_official_text(self):
        p = bm.parse_points_text(fixture("bwf_world_ranking_system_excerpt.txt"))
        self.assertEqual((p["version"], p["in_force"]), ("6.0", "2026-04-26"))
        self.assertEqual(p["rows"]["level4"]["points"][:4], [9200, 7800, 6420, 5040])
        self.assertEqual(p["rows"]["level3"]["points"][:2], [11000, 9350])
        self.assertEqual(p["rows"]["level6"]["points"][:4], [5500, 4680, 3850, 3030])
        self.assertEqual(p["rows"]["level5"]["points"][:2], [7000, 5950])
        self.assertEqual(p["rows"]["int_challenge"]["points"][:2], [4000, 3400])
        self.assertIn("level2_base", p["rows"])
        self.assertNotIn("level2", p["rows"])

    def test_entry_list(self):
        pl = bm.parse_entry_list_text(fixture("bwf_entry_list_excerpt.txt"))
        names = [p["latin"] for p in pl]
        for n in ("WATANABE Koki", "TANAKA Yushi", "OKIMOTO Yudai", "MIYAZAKI Tomoka", "HOKI Takuro", "KOBAYASHI Yugo"):
            self.assertIn(n, names)
        self.assertNotIn("POPOV Christo", names)
        self.assertEqual(next(p for p in pl if p["latin"] == "KOGA Minoru")["draw"], "予選")
        self.assertIsNone(bm.parse_entry_list_text("募集要項 Prospectus"))
        self.assertEqual(bm.parse_entry_list_text("Member ID Country\n1 FOO Bar FRA M"), [])


class MentionTest(unittest.TestCase):
    def test_variants_of_same_event(self):
        for t in ("【アークティックオープン2026】x", "【アークティック・オープン 2026】x", "アークティックオープン２０２６ 日本勢", "Ｘ【アークティック・オープン2026】"):
            m = bm.extract_event_mention(t, ALIASES)
            self.assertEqual((m["kind"], m["en"], m["year"]), ("resolved", "Arctic Open", 2026), t)

    def test_unknown_multiple_none(self):
        self.assertEqual(bm.extract_event_mention("【全日本社会人2026】男子ダブルス", ALIASES)["kind"], "none")
        self.assertEqual(bm.extract_event_mention("【ナゾカップオープン2026】速報", ALIASES)["kind"], "unknown")
        self.assertEqual(bm.extract_event_mention("ジャパンオープンとコリアオープンの組み合わせ", ALIASES)["kind"], "multiple")
        self.assertEqual(bm.extract_event_mention("バドミントン日本代表が練習公開", ALIASES)["kind"], "none")

    def test_year_detection(self):
        self.assertEqual(bm.extract_event_mention("デンマークオープン", ALIASES)["year"], None)
        self.assertEqual(bm.extract_event_mention("デンマークオープン2025・2026", ALIASES)["year"], "multiple")


class ResolveTest(unittest.TestCase):
    def resolve(self, title, **kw):
        svc, f = make(**kw)
        with patch_pdf():
            return svc, f, svc.resolve(title)

    def test_real_article_to_display(self):
        svc, f, info = self.resolve(TITLE)
        self.assertEqual(info["kind"], "ok")
        title, md = bm.render_meta(info, svc)
        self.assertEqual(title, "アークティック・オープン 2026")
        for expect in ("**アークティック・オープン 2026**", "Super 500 ｜ Level 4", "優勝　9,200ポイント", "準優勝　7,800ポイント", "ベスト4　6,420ポイント",
                       "ベスト8　5,040ポイント", "🇯🇵 日本選手", "渡邉航貴・田中湧士", "沖本優大" if False else "OKIMOTO Yudai", "KOGA Minoru（予選）", "BWFランキング規程 v6.0（2026-04-26施行）"):
            self.assertIn(expect, md)
        self.assertNotIn("世界", md)          # 世界ランキング(順位)とは分離

    def test_same_event_many_articles_same_info_and_no_refetch(self):
        svc, f = make()
        outs = []
        with patch_pdf():
            for i in range(10):
                info = svc.resolve(TITLE.replace("2日目", f"{i}日目"))
                outs.append(bm.render_meta(info, svc))
        self.assertEqual(len({o for o in outs}), 1)                       # 10本とも同じ大会情報
        self.assertEqual(f.n("text", "/events/calendar/2026/"), 1)        # カレンダーは1回だけ
        self.assertEqual(f.n("head"), 1)
        self.assertEqual(f.n("bytes", "M_QVER"), 1)                       # エントリー一覧も1回だけ
        self.assertEqual(f.n("bytes", "Prospectus"), 0)                   # 募集要項より先にM&Qを見る

    def test_second_process_uses_saved_master_without_network(self):
        svc, f = make()
        with patch_pdf():
            first = bm.render_meta(svc.resolve(TITLE), svc)
        saved = copy.deepcopy(svc.master)
        svc2, f2 = make(master=saved, now=NOW + dt.timedelta(minutes=30))
        with patch_pdf():
            second = bm.render_meta(svc2.resolve(TITLE), svc2)
        self.assertEqual(first[1].split("確認")[0], second[1].split("確認")[0])
        self.assertEqual(f2.calls, [])

    def test_wrong_event_is_never_picked(self):
        # 『中国オープン』は中国開催の大会名に限る。マカオオープン(公式名に CHINA を含む)・ジュニア選手権・チームは別の大会
        _, _, info = self.resolve("【中国オープン2026】結果")
        self.assertEqual(info["kind"], "pending")
        _, _, info = self.resolve("【世界選手権2026】結果")                  # 世界ジュニア選手権は別大会
        self.assertEqual(info["kind"], "pending")
        _, _, info = self.resolve("【マカオオープン2026】結果")             # 許容語を明示した大会は一致する
        self.assertEqual(info["kind"], "ok")

    def test_multiple_candidates_are_pending(self):
        _, _, info = self.resolve("【中国マスターズ2026】結果")             # LI-NING(S750)とRuichang(S100)
        self.assertEqual((info["kind"], info["reason"]), ("pending", "同名の大会が複数あり特定できません"))

    def test_german_open_and_hylo_open_are_different(self):
        svc, _, info = self.resolve("ドイツオープン2026")
        self.assertEqual(info["record"]["name_official"], "YONEX German Open 2026")
        svc, _, info = self.resolve("ハイロオープン2026")
        self.assertEqual(info["record"]["name_official"], "HYLO Open 2026")

    def test_no_year_uses_only_the_running_event_window(self):
        _, _, info = self.resolve("【アークティックオープン】日本勢が初戦突破")            # 今日(10/8)はAOの期間内 → 2026
        self.assertEqual((info["kind"], info["record"]["year"]), ("ok", 2026))
        _, _, info = self.resolve("【アークティックオープン】日本勢が初戦突破", now=NOW + dt.timedelta(days=60))
        self.assertEqual((info["kind"], info["reason"]), ("pending", "年度を特定できません"))
        _, _, info = self.resolve("デンマークオープン 日本勢")
        self.assertEqual(info["kind"], "pending")

    def test_unknown_and_none(self):
        _, _, info = self.resolve("【全日本社会人2026】速報")
        self.assertEqual(info["kind"], "none")
        _, _, info = self.resolve("【ナゾカップオープン2026】速報")
        self.assertEqual((info["kind"], info["reason"]), ("pending", "公式の大会マスターで特定できません"))
        svc, f = make()
        with patch_pdf():
            md = bm.render_meta(svc.resolve("【ナゾカップオープン2026】速報"), svc)[1]
        self.assertTrue(md.startswith("-# 大会情報：確認中"))

    def test_cancelled_event(self):
        svc, f = make()
        svc.aliases = ALIASES + [{"en": "Cancelled Test Open", "display": "テスト", "ja": ["テストオープン"]}]
        with patch_pdf():
            info = svc.resolve("テストオープン2026")
        self.assertIn("中止", bm.render_meta(info, svc)[1])


class DisplayRulesTest(unittest.TestCase):
    def meta(self, title, **kw):
        svc, f = make(**kw)
        with patch_pdf():
            return bm.render_meta(svc.resolve(title), svc)[1], svc

    def test_super_1000_points_are_not_guessed(self):
        md, _ = self.meta("マレーシアオープン2026")
        self.assertIn("Super 1000 ｜ Level 2", md)
        self.assertIn("優勝ポイント　Super 1000は賞金条件で区分が分かれるため確認中", md)
        self.assertNotIn("12,000", md)

    def test_non_tour_event_has_no_points(self):
        md, _ = self.meta("【世界ジュニア2026】結果")
        self.assertIn("Grade 1 – Individual Junior Tournaments", md)
        self.assertIn("優勝ポイント　確認中", md)

    def test_world_tour_finals_shows_only_winner_and_runner_up(self):
        md, _ = self.meta("ワールドツアーファイナルズ2026")
        self.assertIn("優勝　14,000ポイント", md)
        self.assertNotIn("ベスト4", md)

    def test_entries_unavailable_means_confirming(self):
        svc, f = make()
        f.pdf_text = {}
        with mock.patch.object(bm, "pdf_to_text", side_effect=RuntimeError("no pypdf")):
            info = svc.resolve(TITLE)
        md = bm.render_meta(info, svc)[1]
        self.assertIn("🇯🇵 日本選手\n確認中", md)
        self.assertIn("エントリー一覧の取得に失敗", " ".join(svc.errors))

    def test_points_unknown_when_regulation_not_verified(self):
        svc, f = make()
        with mock.patch.object(bm, "pdf_to_text", side_effect=RuntimeError("x")):
            md = bm.render_meta(svc.resolve(TITLE), svc)[1]
        self.assertIn("優勝ポイント　確認中", md)
        self.assertNotIn("9,200", md)


class RevalidationTest(unittest.TestCase):
    def warm(self):
        svc, f = make()
        with patch_pdf():
            svc.resolve(TITLE)
        return copy.deepcopy(svc.master)

    def test_live_event_is_rechecked_after_a_day_but_finished_event_is_not(self):
        saved = self.warm()
        svc, f = make(master=copy.deepcopy(saved), now=NOW + dt.timedelta(hours=26))
        with patch_pdf():
            svc.resolve(TITLE)
        self.assertEqual(f.n("text", "/events/calendar/2026/"), 1)        # 開催中は24時間超で再検証
        svc, f = make(master=copy.deepcopy(saved), now=NOW + dt.timedelta(days=40))
        with patch_pdf():
            svc.resolve(TITLE)
        self.assertEqual(f.n("text", "/events/calendar/2026/"), 0)        # 終了した大会は再取得しない

    def test_official_change_is_recorded(self):
        saved = self.warm()
        rows = [list(r) for r in ROWS_2026]
        rows[2][4] = "07 -12"
        f = FakeFetcher({2026: [tuple(r) for r in rows]})
        svc, f = make(f, master=copy.deepcopy(saved), now=NOW + dt.timedelta(hours=30))
        with patch_pdf():
            info = svc.resolve(TITLE)
        rec = info["record"]
        self.assertEqual(rec["start_date"], "2026-10-07")
        self.assertGreater(rec["last_changed_at"], saved["tournaments"]["2026-5594"]["last_changed_at"])
        self.assertEqual(rec["retrieved_at"], saved["tournaments"]["2026-5594"]["retrieved_at"])

    def test_event_dropped_from_calendar_becomes_recheck(self):
        saved = self.warm()
        f = FakeFetcher({2026: [r for r in ROWS_2026 if r[1] != "5594"]})
        svc, f = make(f, master=copy.deepcopy(saved), now=NOW + dt.timedelta(hours=30))
        with patch_pdf():
            info = svc.resolve(TITLE)
        self.assertEqual(info["record"]["status"], bm.STATUS_RECHECK)
        md = bm.render_meta(info, svc)[1]
        self.assertIn("確認中", md.split("\n")[1])
        self.assertNotIn("9,200", md)

    def test_stale_when_reverification_fails(self):
        saved = self.warm()
        svc, f = make(FakeFetcher(fail=True), master=copy.deepcopy(saved), now=NOW + dt.timedelta(hours=30))
        with patch_pdf():
            info = svc.resolve(TITLE)
        self.assertTrue(info["stale"])
        md = bm.render_meta(info, svc)[1]
        self.assertIn("再検証できず", md)
        self.assertNotIn("9,200", md)         # 再検証できない間は大会格・ポイントを断定しない

    def test_points_regulation_update_refreshes_table(self):
        saved = self.warm()
        later = NOW + dt.timedelta(hours=30)
        new_text = fixture("bwf_world_ranking_system_excerpt.txt").replace("Version 6.0", "Version 7.0").replace("9200 7800 6420 5040", "9300 7900 6500 5100")
        f = FakeFetcher()
        f.last_modified = "Fri, 08 May 2026 08:28:22 GMT"
        svc, f = make(f, master=copy.deepcopy(saved), now=later)
        with mock.patch.object(bm, "pdf_to_text", side_effect=lambda data: new_text if data == PTS_URL.encode() else fixture("bwf_entry_list_excerpt.txt")):
            md = bm.render_meta(svc.resolve(TITLE), svc)[1]
        self.assertIn("優勝　9,300ポイント", md)
        self.assertIn("v7.0", md)
        self.assertEqual(f.n("bytes", "World%20Ranking"), 1)

    def test_points_regulation_unchanged_is_only_a_head_check(self):
        saved = self.warm()
        svc, f = make(master=copy.deepcopy(saved), now=NOW + dt.timedelta(hours=30))
        with patch_pdf():
            svc.resolve(TITLE)
        self.assertEqual(f.n("head"), 1)
        self.assertEqual(f.n("bytes", "World%20Ranking"), 0)

    def test_points_regulation_check_failure_hides_points(self):
        saved = self.warm()
        svc, f = make(FakeFetcher(fail=True), master=copy.deepcopy(saved), now=NOW + dt.timedelta(hours=30))
        with patch_pdf():
            svc.ensure_points_table()
        self.assertEqual(svc.master["points_table"]["status"], bm.STATUS_RECHECK)
        self.assertIsNone(bm.points_for(svc.master["points_table"], "level4"))


class EmbedTest(unittest.TestCase):
    def tearDown(self):
        fb.set_tournament_meta(None)

    def test_no_service_means_unchanged(self):
        e = {"title": "🏸 x", "description": "本文", "color": 1}
        self.assertEqual(fb.decorate_embed_with_tournament_meta(e, [TITLE]), e)

    def test_meta_is_put_before_article(self):
        svc, f = make()
        fb.set_tournament_meta(svc)
        e = {"title": f"🏸 {TITLE}", "description": "本文……", "color": 1}
        with patch_pdf():
            out = fb.decorate_embed_with_tournament_meta(e, [TITLE])
        self.assertEqual(out["title"], "🏸 アークティック・オープン 2026")
        self.assertTrue(out["description"].startswith("**アークティック・オープン 2026**\nSuper 500 ｜ Level 4"))
        self.assertIn(f"────────\n**{TITLE}**\n本文……", out["description"])
        self.assertLessEqual(len(out["description"]), 4000)

    def test_articles_without_event_are_untouched_and_failure_does_not_break(self):
        svc, f = make()
        fb.set_tournament_meta(svc)
        e = {"title": "🏸 日本代表が練習公開", "description": "本文", "color": 1}
        self.assertEqual(fb.decorate_embed_with_tournament_meta(e, ["日本代表が練習公開"]), e)
        with mock.patch.object(svc, "resolve", side_effect=RuntimeError("boom")):
            self.assertEqual(fb.decorate_embed_with_tournament_meta(e, [TITLE]), e)

    def test_pending_adds_one_line_only(self):
        svc, f = make()
        fb.set_tournament_meta(svc)
        e = {"title": "🏸 【ナゾカップオープン2026】速報", "description": "本文", "color": 1}
        out = fb.decorate_embed_with_tournament_meta(e, ["【ナゾカップオープン2026】速報"])
        self.assertEqual(out["title"], e["title"])
        self.assertEqual(out["description"].count("\n"), 1)
        self.assertTrue(out["description"].startswith("-# 大会情報：確認中"))


if __name__ == "__main__":
    unittest.main()
