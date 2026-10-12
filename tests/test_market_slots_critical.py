"""#webhook_market の3回配信(朝・昼・夜)・⚪その他のタイトル1行表示・critical_alert のテスト(2026-10-12)。
実際のGemini・Discord・ニュース取得には接続しない。日時は固定。
実行: .venv/bin/python -m unittest tests.test_market_slots_critical
"""
import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402
import market_news_curation as mnc  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))


def at(h, m=0, day=12):
    return dt.datetime(2026, 10, day, h, m, tzinfo=JST)


def item(i, title=None):
    return {"title": title or f"見出し{i} - 日本経済新聞", "url": f"https://example.com/a{i}", "published": "10-12 09:00"}


class FakeClient:
    """Geminiの代わり。渡された応答(JSON)を返し、呼ばれた回数を数える。"""

    def __init__(self, payload):
        self.payload, self.calls = payload, 0
        self.models = self

    def generate_content(self, model, contents):
        self.calls += 1
        self.prompt = contents
        return mock.Mock(text=json.dumps(self.payload, ensure_ascii=False))


def grp(indices, importance="mid", critical=None, category="日本市場"):
    g = {"indices": indices, "representative": indices[0], "category": category, "importance": importance, "reason": "r"}
    if critical is not None:
        g["critical_alert"] = critical
    return g


class SlotTest(unittest.TestCase):
    def test_three_slots_a_day(self):
        self.assertEqual([n for n, _ in mnc.MARKET_SLOTS], ["朝", "昼", "夜"])
        st = {}
        self.assertEqual(mnc.MARKET_SLOTS, (("朝", 6), ("昼", 12), ("夜", 20)))
        self.assertIsNone(mnc.due_slot(at(5, 59), st))           # 朝の便より前
        self.assertEqual(mnc.due_slot(at(6, 0), st), "朝")       # 6:00ちょうどから朝の便
        self.assertEqual(mnc.due_slot(at(7, 3), st), "朝")
        st[mnc.slot_key(at(7, 3), "朝")] = "x"
        self.assertIsNone(mnc.due_slot(at(9, 3), st))            # 便の合間
        self.assertIsNone(mnc.due_slot(at(11, 59), st))
        self.assertEqual(mnc.due_slot(at(13, 3), st), "昼")
        st[mnc.slot_key(at(13, 3), "昼")] = "x"
        self.assertIsNone(mnc.due_slot(at(19, 3), st))
        self.assertEqual(mnc.due_slot(at(23, 3), st), "夜")      # cron遅延で遅れても夜の便は1回送る
        st[mnc.slot_key(at(23, 3), "夜")] = "x"
        self.assertIsNone(mnc.due_slot(at(23, 33), st))
        self.assertEqual(mnc.due_slot(at(7, 3, day=13), st), "朝")  # 翌日はまた朝から

    def test_delayed_run_sends_latest_slot_once(self):
        st = {}
        self.assertEqual(mnc.due_slot(at(12, 30), st), "昼")     # 朝の便が動かないまま昼になった
        st[mnc.slot_key(at(12, 30), "昼")] = "x"
        self.assertIsNone(mnc.due_slot(at(13, 0), st))           # 朝の分をさかのぼって連投しない

    def test_slot_key_is_rolled_back_on_send_failure(self):
        st = {"old": "x"}
        before = set(st)
        st[mnc.slot_key(at(7), "朝")] = "x"
        fn._rollback_new_keys(st, before)
        self.assertEqual(mnc.due_slot(at(7, 30), st), "朝")      # 送信失敗なら次回もう一度送る


class CriticalSchemaTest(unittest.TestCase):
    def test_prompt_and_validate(self):
        items = [item(i) for i in range(4)]
        client = FakeClient([grp([0], "high", True), grp([1], "mid", True), grp([2], "high", "yes"), grp([3], "low")])
        res = mnc.curate(items, client, "m")
        self.assertIn("critical_alert", client.prompt)
        by = {g["items"][0]["url"]: g["critical_alert"] for g in res["groups"]}
        self.assertEqual(by, {"https://example.com/a0": True, "https://example.com/a1": False,
                              "https://example.com/a2": False, "https://example.com/a3": False})
        self.assertTrue(res["critical_alert"])
        self.assertEqual(res["groups"][0]["items"][0]["url"], "https://example.com/a0")  # 急変が先頭
        self.assertTrue(any("high でない" in w for w in res["warnings"]))
        self.assertTrue(any("真偽値でない" in w for w in res["warnings"]))

    def test_missing_flag_defaults_false(self):
        res = mnc.curate([item(0)], FakeClient([grp([0], "high")]), "m")
        self.assertFalse(res["critical_alert"])
        self.assertEqual(res["warnings"], [])


class RenderTest(unittest.TestCase):
    def test_low_is_title_one_line_under_other(self):
        items = [item(0, "日銀が利上げ決定 - 日本経済新聞"), item(1, "A社が新店舗 - 日本経済新聞"),
                 item(2, "A社が新店を出店 - ロイター"), item(3, "コラム：相場格言 - 東洋経済オンライン"), item(4, "NY株急落で取引停止 - ロイター")]
        res = mnc.curate(items, FakeClient([grp([0], "high"), grp([1, 2], "low"), grp([3], "low"), grp([4], "high", True)]), "m")
        old = mnc.render_groups(res)  # 既定(週末まとめ等)は従来どおり ⚪ も見出し行+リンク行
        self.assertTrue(any(x.startswith("⚪【日本市場】A社が新店舗（2本）") for x in old))
        self.assertFalse(any(x.startswith("⚪ その他") for x in old))
        lines = mnc.render_groups(res, low_one_line=True)
        self.assertTrue(lines[0].startswith("🚨【市場急変】【日本市場】NY株急落で取引停止"))
        self.assertTrue(lines[1].startswith(mnc.CONTINUATION_PREFIX))
        self.assertTrue(lines[2].startswith("🔴【日本市場】日銀が利上げ決定"))
        i = lines.index("⚪ その他（2件・タイトルのみ）")
        self.assertEqual(lines[i + 1:], ["- [A社が新店舗](<https://example.com/a1>)（2本）",
                                         "- [コラム：相場格言](<https://example.com/a3>)"])
        self.assertFalse(any(x.startswith("⚪【") for x in lines))  # ⚪は見出し+リンク行の形では出さない


class BuildMarketMessageTest(unittest.TestCase):
    def run_offslot(self, payload, state=None, items=None):
        state = {} if state is None else state
        client = FakeClient(payload)
        items = items if items is not None else [item(0, "NY株急落で取引停止 - ロイター"), item(1)]
        with mock.patch.object(fn, "fetch_economy_news_candidates", return_value=items), \
                mock.patch.object(fn, "fetch_nikkei225", side_effect=AssertionError("便の合間は株価を取りに行かない")), \
                mock.patch.dict(os.environ, {"MARKET_NEWS_CURATION": ""}):
            msg, skip = fn.build_market_message(state, at(9, 3), client, guard=mock.Mock(call=lambda f: f()))
        return msg, skip, state, client

    def test_offslot_no_critical_sends_nothing_and_keeps_items_unsent(self):
        st = {mnc.slot_key(at(7), "朝"): "x"}
        msg, skip, st, client = self.run_offslot([grp([0], "high"), grp([1], "mid")], st)
        self.assertTrue(skip)
        self.assertNotIn("https://example.com/a0", st)   # 既送信にしない → 次の便で配信される
        self.assertEqual(client.calls, 1)
        msg, skip, st, client2 = self.run_offslot([grp([0], "high"), grp([1], "mid")], st)
        self.assertTrue(skip)
        self.assertEqual(client2.calls, 0)               # 判定済みの記事でGeminiを呼び直さない

    def test_offslot_critical_is_sent_immediately(self):
        st = {mnc.slot_key(at(7), "朝"): "x"}
        msg, skip, st, _ = self.run_offslot([grp([0], "high", True), grp([1], "mid")], st)
        self.assertFalse(skip)
        self.assertTrue(msg.startswith("# 🚨 市場急変アラート (2026-10-12 09:03 JST時点・臨時配信)"))
        self.assertIn("🚨【市場急変】【日本市場】NY株急落で取引停止", msg)
        self.assertNotIn("見出し1", msg)
        self.assertIn("https://example.com/a0", st)      # 臨時配信した記事だけ既送信
        self.assertNotIn("https://example.com/a1", st)

    def test_slot_run_sends_full_message_and_marks_slot(self):
        st = {}
        items = [item(0, "日銀が利上げ決定 - 日本経済新聞"), item(1, "コラム：相場格言 - 東洋経済オンライン")]
        client = FakeClient([grp([0], "high"), grp([1], "low")])
        idx = {"price": "39,000", "change": "+100", "change_rate": "+0.26"}
        with mock.patch.object(fn, "fetch_economy_news_candidates", return_value=items), \
                mock.patch.object(fn, "fetch_index_with_fallback", return_value=(idx, False)), \
                mock.patch.object(fn, "format_yf_line", side_effect=lambda label, t: f"- {label}: x"), \
                mock.patch.object(fn, "fetch_usdjpy", return_value="150.00"), \
                mock.patch.object(fn, "fetch_jgb_futures", return_value=None):
            msg, skip = fn.build_market_message(st, at(7, 3), client, guard=mock.Mock(call=lambda f: f()))
        self.assertFalse(skip)
        self.assertTrue(msg.startswith("# 💹 マーケット情報 (2026-10-12 07:03 JST時点・"))
        self.assertIn("・朝の便)", msg.splitlines()[0])
        self.assertIn("🔴【日本市場】日銀が利上げ決定", msg)
        self.assertIn("⚪ その他（1件・タイトルのみ）\n- [コラム：相場格言](<https://example.com/a1>)", msg)
        self.assertIn(mnc.slot_key(at(7, 3), "朝"), st)
        self.assertIn("https://example.com/a0", st)
        self.assertIsNone(mnc.due_slot(at(9, 3), st))

    def test_offslot_gemini_failure_does_not_send_or_mark(self):
        st = {mnc.slot_key(at(7), "朝"): "x"}
        with mock.patch.object(fn, "fetch_economy_news_candidates", return_value=[item(0)]), \
                mock.patch.object(fn, "record_gemini_failure") as rec:
            bad = mock.Mock()
            bad.models.generate_content.side_effect = RuntimeError("429")
            msg, skip = fn.build_market_message(st, at(9, 3), bad, guard=mock.Mock(call=lambda f: f()))
        self.assertTrue(skip)
        self.assertTrue(rec.called)
        self.assertEqual([k for k in st if k.startswith(mnc.SCREENED_KEY_PREFIX)], [])
        self.assertNotIn("https://example.com/a0", st)


if __name__ == "__main__":
    unittest.main()
