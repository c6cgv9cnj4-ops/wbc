"""Discord自動投稿の見た目(共通原則 discord_style.py): スポーツ記事の投稿と、ニュース自動配信の異常通知(news_alerts)のテスト。
通信・Discordは使わない。
実行: .venv/bin/python -m unittest tests.test_news_design
"""
import contextlib
import datetime as dt
import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import discord_style  # noqa: E402
import fetch_news as fn  # noqa: E402
import news_alerts as na  # noqa: E402

NOW = dt.datetime(2026, 10, 6, 11, 15, tzinfo=dt.timezone(dt.timedelta(hours=9)))
RAW_429 = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check "
           "your plan and billing details.', 'status': 'RESOURCE_EXHAUSTED'}}")
RAW_MARKERS = ("RESOURCE_EXHAUSTED", "{'error'", "'code'", "quota")
EMOJI = re.compile("[☀-➿\U0001F300-\U0001FAFF]")


class SportsTest(unittest.TestCase):
    ITEMS = [{"title": "大谷が2打席連続本塁打 - スポーツ報知", "url": "https://example.com/1"},
             {"title": "J1首位攻防は痛み分け - 日刊スポーツ", "url": "https://example.com/2"}]

    def test_structure(self):
        lines = fn.build_sports_leak_message(self.ITEMS, NOW).split("\n")
        self.assertEqual(lines[0], "**スポーツ記事**")               # 見出しは太字1行(#見出しは使わない)
        self.assertTrue(lines[1].startswith("-# "))                   # 補足は小さな文字
        self.assertIn("一般ニュースフィードで検知", lines[1])
        self.assertIn("10/06 11:15 JST", lines[1])
        self.assertEqual(lines[2:], ["- [大谷が2打席連続本塁打 - スポーツ報知](<https://example.com/1>)",
                                     "- [J1首位攻防は痛み分け - 日刊スポーツ](<https://example.com/2>)"])

    def test_no_big_heading_and_no_emoji(self):
        msg = fn.build_sports_leak_message(self.ITEMS, NOW)
        self.assertFalse([ln for ln in msg.split("\n") if ln.startswith(("# ", "## ", "### "))])
        self.assertIsNone(EMOJI.search(msg))

    def test_empty_is_none(self):
        self.assertIsNone(fn.build_sports_leak_message([], NOW))

    def test_other_categories_untouched(self):
        # 全国・ローカル・市場の見出し(今回の対象外)は従来のまま
        with open(fn.__file__, encoding="utf-8") as fh:
            self.assertIn('# 🗾 埼玉・県央ローカルニュース', fh.read())


class ExplainTest(unittest.TestCase):
    def test_gemini_429(self):
        cause, tech = na.explain(f"Gemini APIエラー {na.short_error(RAW_429)}", "gemini_national")
        self.assertEqual(cause, "Geminiの利用上限に達しました")
        self.assertEqual(tech, "HTTP 429")
        for m in RAW_MARKERS:
            self.assertNotIn(m, cause)

    def test_status_codes(self):
        self.assertEqual(na.explain("取得失敗 HTTP 403: Forbidden for url: https://example.com", "anzn_fetch"),
                         ("取得先へのアクセスが拒否されました", "HTTP 403"))
        self.assertEqual(na.explain("Gemini APIエラー HTTP 503: UNAVAILABLE model overloaded", "gemini_market")[0],
                         "Geminiが一時的に混み合っています")
        self.assertEqual(na.explain("HTTP 502: bad gateway", "x")[0], "取得先側で一時的なエラーが発生しました")
        self.assertEqual(na.explain("取得失敗 ReadTimeout: timed out", "x")[0], "取得先から応答がありませんでした")
        self.assertEqual(na.explain("HTTP 429: too many", "discord_send_x")[0], "Discordの利用上限に達しました")

    def test_plain_details_kept(self):
        d = "前回実行から450分空きました(Macの停止・スリープ・gh認証切れの可能性)"
        cause, tech = na.explain(d, "dispatcher_gap")
        self.assertEqual(cause, d)
        self.assertIsNone(tech)                                       # 「450」を HTTP 450 と誤認しない
        self.assertEqual(na.explain("要約が0件", "gemini_national")[0], "要約が0件")
        self.assertEqual(na.explain("送信失敗", "discord_send_x")[0], "送信に失敗しました")
        self.assertEqual(na.explain("送信失敗(ログにHTTPステータスあり)", "discord_send_x")[0], "送信に失敗しました")

    def test_unknown_json_blob_is_dropped(self):
        cause, _ = na.explain("取得失敗 SomeError: {'detail': 'x' * 500 …", "x")
        self.assertNotIn("{", cause)
        self.assertNotIn("detail", cause)

    def test_webhook_redacted(self):
        cause, _ = na.explain("取得失敗 https://discord.com/api/webhooks/123/SECRET failed", "x")
        self.assertNotIn("SECRET", cause)

    def test_delivery(self):
        self.assertEqual(na.humanize_delivery("スキップ(記事は既送信扱いにせず次回以降に再試行)"),
                         "今回はスキップ。記事は既送信扱いにせず次回以降に再試行。")
        self.assertEqual(na.humanize_delivery("継続(このクエリ分の記事のみ欠落)"), "配信は継続。このクエリ分の記事のみ欠落。")
        self.assertEqual(na.humanize_delivery("停止中(復旧までは新着が届きません)"), "停止中。復旧までは新着が届きません。")
        self.assertEqual(na.humanize_delivery("継続"), "配信は継続。")
        self.assertEqual(na.humanize_delivery("想定外の文"), "想定外の文")


class AlertTest(unittest.TestCase):
    def setUp(self):
        na.reset()

    def gemini(self):
        na.record("gemini_national", "news", "全国主要ニュース(Gemini要約)", f"Gemini APIエラー {na.short_error(RAW_429)}",
                  "スキップ(記事は既送信扱いにせず次回以降に再試行)")

    def test_embed_single(self):
        self.gemini()
        payloads, notified, suppressed = na.build_embeds({}, NOW)
        e = payloads["news"]["embeds"][0]
        self.assertEqual(e["title"], "⚠️ ニュース自動配信の異常")
        self.assertEqual(e["color"], discord_style.ALERT_COLOR)
        self.assertEqual(e["description"].split("\n"),
                         ["**全国主要ニュース(Gemini要約)**", "Geminiの利用上限に達しました",
                          "影響: 今回はスキップ。記事は既送信扱いにせず次回以降に再試行。"])
        self.assertEqual(e["footer"]["text"], "11:15 JST ・ HTTP 429 ・ 同じ異常は12時間通知しません")
        for m in RAW_MARKERS:
            self.assertNotIn(m, e["description"] + e["footer"]["text"] + e["title"])
        self.assertEqual((notified, suppressed), (["gemini_national"], []))

    def test_one_emoji_only(self):
        self.gemini()
        na.record("google_news_fetch", "news", "経済ニュース(Google News検索)", "取得失敗(リトライ後も失敗): 日経平均", "継続(このクエリ分の記事のみ欠落)")
        e = na.build_embeds({}, NOW)[0]["news"]["embeds"][0]
        self.assertEqual(len(EMOJI.findall(e["title"] + e["description"] + e["footer"]["text"])), 1)

    def test_multi_issue_hides_ambiguous_tech_code(self):
        self.gemini()
        na.record("discord_send_national_message", "news", "Discord送信(全国主要ニュース)", "送信失敗(ログにHTTPステータスあり)", "スキップ(既送信にせず次回再送)")
        e = na.build_embeds({}, NOW)[0]["news"]["embeds"][0]
        self.assertEqual(e["description"].count("影響:"), 2)
        self.assertNotIn("HTTP", e["footer"]["text"])

    def test_repeat_count_and_separate_channels(self):
        na.record("anzn_fetch", "local", "あんぜんねっと", "取得失敗 HTTP 403: Forbidden", "一部スキップ(新着を取得できず)")
        na.record("anzn_fetch", "local", "あんぜんねっと", "取得失敗 HTTP 403: Forbidden", "一部スキップ(新着を取得できず)")
        self.gemini()
        payloads = na.build_embeds({}, NOW)[0]
        self.assertEqual(sorted(payloads), ["local", "news"])          # 配信先ごとに分ける(分類を混ぜない)
        self.assertIn("**あんぜんねっと** ×2", payloads["local"]["embeds"][0]["description"])

    def test_fallback_text_has_no_big_heading(self):
        self.gemini()
        msg = na.build_messages({}, NOW)[0]["news"]
        self.assertFalse([ln for ln in msg.split("\n") if ln.startswith(("# ", "## ", "### "))])
        self.assertIn("-# 11:15 JST", msg)
        for m in RAW_MARKERS:
            self.assertNotIn(m, msg)

    def test_cooldown(self):
        self.gemini()
        state = {na.ALERT_STATE_PREFIX + "gemini_national": (NOW - dt.timedelta(hours=1)).isoformat()}
        self.assertEqual(na.build_embeds(state, NOW)[0], {})
        state[na.ALERT_STATE_PREFIX + "gemini_national"] = (NOW - dt.timedelta(hours=13)).isoformat()
        self.assertIn("news", na.build_embeds(state, NOW)[0])


class FlushTest(unittest.TestCase):
    def setUp(self):
        na.reset()
        na.record("gemini_national", "news", "全国主要ニュース(Gemini要約)", f"Gemini APIエラー {na.short_error(RAW_429)}",
                  "スキップ(記事は既送信扱いにせず次回以降に再試行)")

    def run_flush(self, embed_ok, text_ok, webhooks=None, with_embed=True):
        sent_embeds, sent_texts, state = [], [], {}
        embed_fn = (lambda u, p: (sent_embeds.append((u, p)) or embed_ok)) if with_embed else None
        send_fn = lambda u, m: (sent_texts.append((u, m)) or text_ok)  # noqa: E731
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            n = na.flush(webhooks or {"news": "https://hook/news"}, send_fn, state, NOW, embed_fn=embed_fn)
        return n, sent_embeds, sent_texts, state, buf.getvalue()

    def test_embed_success_does_not_send_text(self):
        n, embeds, texts, state, _ = self.run_flush(True, True)
        self.assertEqual((n, len(embeds), len(texts)), (1, 1, 0))
        self.assertIn(na.ALERT_STATE_PREFIX + "gemini_national", state)

    def test_embed_failure_falls_back_to_text(self):
        n, embeds, texts, state, _ = self.run_flush(False, True)
        self.assertEqual((n, len(embeds), len(texts)), (1, 1, 1))
        self.assertIn(na.ALERT_STATE_PREFIX + "gemini_national", state)

    def test_without_embed_fn_text_only(self):
        n, embeds, texts, _, _ = self.run_flush(True, True, with_embed=False)
        self.assertEqual((n, len(embeds), len(texts)), (1, 0, 1))

    def test_total_failure_keeps_state_unmarked(self):
        n, _, _, state, out = self.run_flush(False, False)
        self.assertEqual(n, 0)
        self.assertEqual(state, {})                                   # 送れなければ次回に再通知できるよう時刻を残さない
        self.assertIn("どのWebhookにも送れません", out)

    def test_falls_back_to_other_webhook(self):
        n, embeds, _, _, _ = self.run_flush(True, True, webhooks={"news": None, "market": "https://hook/market"})
        self.assertEqual((n, embeds[0][0]), (1, "https://hook/market"))

    def test_log_keeps_raw_detail_but_discord_does_not(self):
        _, embeds, _, _, out = self.run_flush(True, True)
        self.assertIn("RESOURCE_EXHAUSTED", out)                      # ログには従来どおり詳細を残す
        self.assertNotIn("RESOURCE_EXHAUSTED", repr(embeds))          # Discordへは出さない

    def test_exception_in_sender_is_contained(self):
        def boom(u, p):
            raise RuntimeError("boom")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            n = na.flush({"news": "https://hook/news"}, lambda u, m: False, {}, NOW, embed_fn=boom)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
