"""市場朝夕サマリー(fetch_market_intelligence)の単体・統合テスト(2026-10-12)。通信なし(市場数値・RSS・Gemini・Discordはモック)。
実行: python -m pytest tests/test_market_intelligence.py
"""
import datetime as dt
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_market_intelligence as mi  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
MON_EVENING = dt.datetime(2026, 10, 12, 17, 0, tzinfo=JST)   # 平日・夕
MON_MORNING = dt.datetime(2026, 10, 12, 8, 0, tzinfo=JST)    # 平日・朝
SAT = dt.datetime(2026, 10, 10, 8, 0, tzinfo=JST)            # 土曜

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title>
<item><title>植田総裁の挨拶</title><link>https://www.boj.or.jp/a</link>
  <pubDate>Mon, 12 Oct 2026 16:00:00 +0900</pubDate></item>
<item><title>国債銘柄別残高</title><link>https://www.boj.or.jp/b</link>
  <pubDate>Mon, 12 Oct 2026 10:00:00 +0900</pubDate></item>
<item><title>植田総裁の挨拶</title><link>https://www.boj.or.jp/a</link>
  <pubDate>Mon, 12 Oct 2026 16:00:00 +0900</pubDate></item>
</channel></rss>"""


def item(title, link, hours_ago=1, now=MON_EVENING, source="日本銀行"):
    return {"source": source, "title": title, "link": link, "published": "",
            "published_dt": now - dt.timedelta(hours=hours_ago)}


def quotes_ok():
    return [mi.compute_quote(name, sym, [100.0, 101.0]) for sym, name in mi.TICKERS]


class QuoteTest(unittest.TestCase):
    def test_compute_quote_change_and_pct(self):
        q = mi.compute_quote("日経平均", "^N225", [69000.0, 69042.109, 69030.922])
        self.assertAlmostEqual(q["close"], 69030.922)
        self.assertAlmostEqual(q["change"], 69030.922 - 69042.109)
        self.assertAlmostEqual(q["change_pct"], (69030.922 - 69042.109) / 69042.109 * 100)
        self.assertIsNone(q["error"])

    def test_nan_is_dropped_and_short_series_is_error(self):
        q = mi.compute_quote("ドル円", "JPY=X", [float("nan"), 158.0, float("nan"), 158.5])
        self.assertAlmostEqual(q["close"], 158.5)
        self.assertEqual(mi.compute_quote("x", "y", [1.0])["error"], "終値が不足")

    def test_format_line_per_symbol(self):
        n225 = mi.compute_quote("日経平均", "^N225", [69042.109, 69030.922])
        self.assertIn("69,030.92", mi.format_quote_line(n225))
        self.assertIn("-11.19", mi.format_quote_line(n225))
        tnx = mi.compute_quote("米10年債利回り", "^TNX", [5.231, 5.244])
        self.assertIn("5.244%", mi.format_quote_line(tnx))
        fx = mi.compute_quote("ドル円", "JPY=X", [158.063, 158.435])
        self.assertIn("158.44円", mi.format_quote_line(fx))

    def test_one_symbol_failure_does_not_stop_others(self):
        def hist(symbol):
            if symbol == "^TNX":
                raise RuntimeError("network down")
            return [1.0, 2.0]
        quotes = mi.fetch_quotes(history_fn=hist)
        self.assertEqual(len(quotes), 4)
        tnx = [q for q in quotes if q["symbol"] == "^TNX"][0]
        self.assertIn("network down", tnx["error"])
        self.assertIn("取得失敗", mi.format_quote_line(tnx))


class FeedTest(unittest.TestCase):
    def test_parse_feed_dedupes_and_converts_jst(self):
        items = mi.parse_feed(RSS_SAMPLE.encode("utf-8"), "日本銀行")
        self.assertEqual([i["link"] for i in items], ["https://www.boj.or.jp/a", "https://www.boj.or.jp/b"])
        self.assertEqual(items[0]["published"], "10/12 16:00")
        self.assertEqual(items[0]["published_dt"].utcoffset(), dt.timedelta(hours=9))

    def test_empty_valid_feed_is_zero_items_not_error(self):
        empty = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'
        self.assertEqual(mi.parse_feed(empty, "x"), [])

    def test_broken_feed_is_error(self):
        with self.assertRaises(mi.FeedError):
            mi.parse_feed(b"<html><body>404</body></html>", "x")

    def test_fetch_all_feeds_separates_failure_and_unconnected(self):
        def fetch(url):
            if "bad" in url:
                raise mi.FeedError("boom")
            return RSS_SAMPLE.encode("utf-8")
        sources = [("日本銀行", "https://ok/x"), ("JPX", "https://bad/x"), ("財務省", None)]
        items, failed = mi.fetch_all_feeds(sources=sources, fetch=fetch)
        self.assertEqual(len(items), 2)
        self.assertIn("JPX", failed)
        self.assertIn("財務省（RSS未接続）", failed)

    def test_important_keyword_filter(self):
        self.assertTrue(mi.is_important("金融政策決定会合の結果"))
        self.assertTrue(mi.is_important("外国為替市場介入について"))
        self.assertFalse(mi.is_important("国債銘柄別残高"))

    def test_split_excludes_seen_and_old_items(self):
        items = [
            item("総裁の挨拶", "https://a", hours_ago=1),
            item("金利の資料", "https://b", hours_ago=1),
            item("総裁の挨拶(古い)", "https://c", hours_ago=100),   # 48時間超は新着扱いしない
            item("為替介入", "https://d", hours_ago=1),
        ]
        important, others = mi.split_new_items(items, seen={"https://d": "x"}, now=MON_EVENING)
        self.assertEqual([i["link"] for i in important], ["https://a", "https://b"])
        self.assertEqual(others, [])


class RunTest(unittest.TestCase):
    def run_once(self, now, items, llm=None, send_ok=True, state=None, quotes=None):
        state = {} if state is None else state
        llm_calls = []

        def llm_fn(prompt):
            llm_calls.append(prompt)
            if llm is None:
                raise AssertionError("LLM should not be called")
            return llm()
        sent = []

        def send_fn(embed):
            sent.append(embed)
            return send_ok
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            mi.run(now, state,
                   quotes_fn=lambda: quotes or quotes_ok(),
                   feeds_fn=lambda: (items, []),
                   llm_fn=llm_fn,
                   send_fn=send_fn)
        return state, llm_calls, sent

    def test_no_important_material_means_no_gemini_call(self):
        items = [item("国債銘柄別残高", "https://b", hours_ago=2)]
        state, calls, sent = self.run_once(MON_EVENING, items, llm=lambda: "{}")
        self.assertEqual(len(calls), 0)                       # Gemini 0回
        self.assertEqual(len(sent), 1)                        # 軽量Embedは送信
        embed = sent[0]
        self.assertIn("重要材料なし", embed["description"])
        self.assertIn("市場数値（終値・前日比）", [f["name"] for f in embed["fields"]])
        self.assertIn("https://b", str(embed))
        self.assertIn("https://b", state["seen"])            # 既読記録

    def test_gemini_success_builds_analysis_embed(self):
        items = [item("金融政策決定会合の結果", "https://a", hours_ago=1)]
        reply = ('{"materials":[{"title":"金融政策決定会合","point":"据え置き"}],'
                 '"transmission":{"stocks":"金融株に追い風","fx":"円安方向","rates":"長期金利は上昇"},'
                 '"events":["10/20 日銀 講演"]}')
        state, calls, sent = self.run_once(MON_EVENING, items, llm=lambda: reply)
        self.assertEqual(len(calls), 1)                       # Gemini 1回
        names = [f["name"] for f in sent[0]["fields"]]
        self.assertIn("重要材料（最大5件）", names)
        self.assertIn("市場への波及経路", names)
        self.assertIn("注目イベント", names)
        self.assertIn("据え置き", str(sent[0]))
        self.assertIn("円安方向", str(sent[0]))

    def test_gemini_429_falls_back_to_link_embed(self):
        items = [item("為替介入の実施", "https://a", hours_ago=1)]

        def quota():
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        state, calls, sent = self.run_once(MON_EVENING, items, llm=quota)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(sent), 1)                        # 配信は完遂する
        embed = sent[0]
        self.assertIn("AI分析は利用できませんでした", embed["description"])
        self.assertIn("429", embed["description"])
        self.assertIn("https://a", str(embed))
        names = [f["name"] for f in embed["fields"]]
        self.assertIn("市場数値（終値・前日比）", names)
        self.assertNotIn("市場への波及経路", names)
        self.assertIn("https://a", state["seen"])

    def test_invalid_gemini_json_falls_back(self):
        items = [item("総裁の挨拶", "https://a", hours_ago=1)]
        state, calls, sent = self.run_once(MON_EVENING, items, llm=lambda: "これはJSONではありません")
        self.assertIn("AI分析は利用できませんでした", sent[0]["description"])

    def test_already_delivered_items_are_not_resent(self):
        items = [item("総裁の挨拶", "https://a", hours_ago=1)]
        state = {"seen": {"https://a": MON_EVENING.isoformat()}}
        _, calls, sent = self.run_once(MON_EVENING, items, llm=lambda: "{}", state=state)
        self.assertEqual(len(calls), 0)
        self.assertNotIn("https://a", str(sent[0]["fields"][0]))

    def test_discord_failure_keeps_seen_unchanged(self):
        items = [item("国債銘柄別残高", "https://b", hours_ago=2)]
        state, calls, sent = self.run_once(MON_EVENING, items, send_ok=False)
        self.assertNotIn("https://b", state.get("seen", {}))  # 送れなかった分は既読にしない

    def test_weekend_does_nothing(self):
        items = [item("総裁の挨拶", "https://a", hours_ago=1, now=SAT)]
        state, calls, sent = self.run_once(SAT, items, llm=lambda: "{}")
        self.assertEqual(calls, [])
        self.assertEqual(sent, [])

    def test_session_label_morning_and_evening(self):
        self.assertEqual(mi.session_label(MON_MORNING), "朝")
        self.assertEqual(mi.session_label(MON_EVENING), "夕")


class GeminiGuardIntegrationTest(unittest.TestCase):
    def test_real_call_gemini_429_becomes_fallback(self):
        # 実際の call_gemini(gemini_guard 経由)で429を起こし、リンク配信に倒れることを確認
        client = mock.MagicMock()
        # 実際の応答と同じく quotaId に GenerateRequestsPerDay を含む(gemini_guard が日次上限と判定する)
        client.models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric 'GenerateRequestsPerDay' limit")
        items = [item("金融政策の見通し", "https://a", hours_ago=1)]
        state = {}
        sent = []
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "K"}), \
             mock.patch("google.genai.Client", return_value=client), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            mi.run(MON_EVENING, state,
                   quotes_fn=quotes_ok,
                   feeds_fn=lambda: ([*items], []),
                   llm_fn=lambda prompt: mi.call_gemini(prompt, state),
                   send_fn=lambda e: (sent.append(e), True)[1])
        self.assertEqual(client.models.generate_content.call_count, 1)  # 再試行しない(日次上限扱い)
        self.assertEqual(len(sent), 1)
        self.assertIn("AI分析は利用できませんでした", sent[0]["description"])
        import gemini_guard
        self.assertTrue(any(k.startswith(gemini_guard.BACKOFF_PREFIX) for k in state))  # 待機状態を記録


if __name__ == "__main__":
    unittest.main()
