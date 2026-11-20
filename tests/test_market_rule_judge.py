"""経済ニュース判定の2回路(Gemini → Python規則判定)のテスト(2026-11-20)。
実際のGeminiには接続しない。実行: .venv/bin/python -m unittest tests.test_market_rule_judge
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import market_news_curation as mnc  # noqa: E402

ITEMS = [
    {"title": "NY株急落で取引停止 - ロイター", "url": "https://example.com/a0", "published": "11-20 07:00"},
    {"title": "日銀が利上げ決定 - 日本経済新聞", "url": "https://example.com/a1", "published": "11-20 07:10"},
]
GOOD = json.dumps([{"indices": [0], "representative": 0, "category": "米国市場", "importance": "high",
                    "reason": "Gemini判定の理由", "critical_alert": True}], ensure_ascii=False)


def failing(err="503 UNAVAILABLE"):
    c = mock.Mock()
    c.models.generate_content.side_effect = RuntimeError(err)
    return c


def ok():
    c = mock.Mock()
    c.models.generate_content.return_value = mock.Mock(text=GOOD)
    return c


class CircuitTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"MARKET_NEWS_CURATION": ""})
        p.start()
        self.addCleanup(p.stop)

    def test_gemini_ok_is_used_and_rule_not_run(self):
        with mock.patch.object(mnc, "rule_judge") as rj:
            out = mnc.curate(ITEMS, ok(), "m")
        rj.assert_not_called()
        self.assertTrue(out["groups"][0]["critical_alert"])
        self.assertIsNone(mnc.LAST_ERROR)

    def test_gemini_fails_rule_judges_with_reason(self):
        out = mnc.curate(ITEMS, failing(), "m", rule_fallback=True)
        self.assertIsNotNone(out)
        crit = [g for g in out["groups"] if g["critical_alert"]]
        self.assertEqual(len(crit), 1)
        self.assertIn("急落", crit[0]["reason"])            # 理由に一致した語を入れる
        self.assertIn("取引停止", crit[0]["reason"])
        self.assertEqual(mnc.LAST_ERROR.__class__, RuntimeError)   # Gemini失敗は記録する

    def test_no_keyword_no_critical_but_still_a_judgment(self):
        out = mnc.curate([ITEMS[1]], failing(), "m", rule_fallback=True)
        self.assertIsNotNone(out)                             # 0件の判定は「成功」(即時配信に落ちない)
        self.assertFalse(any(g["critical_alert"] for g in out["groups"]))
        self.assertIn("急変を示す語なし", out["groups"][0]["reason"])

    def test_no_gemini_client_uses_rule_only(self):
        out = mnc.curate(ITEMS, None, "m", rule_fallback=True)
        self.assertTrue(out["groups"][0]["critical_alert"])
        self.assertIsNone(mnc.LAST_ERROR)

    def test_regular_slot_without_rule_fallback_keeps_old_behavior(self):
        self.assertIsNone(mnc.curate(ITEMS, failing(), "m"))   # 材料整理は従来どおり箇条書きに戻す

    def test_off_switch_returns_none(self):
        with mock.patch.dict(os.environ, {"MARKET_NEWS_CURATION": "off"}):
            self.assertIsNone(mnc.curate(ITEMS, ok(), "m"))

    def test_added_keywords_are_detected(self):
        # 2026-11-20 追加語: 実データ検証で見逃したもの
        titles = ["日経平均、大幅下落 終値1000円超安", "東証、乱高下の展開", "NYダウ急反落 利上げ警戒", "米国株、暴騰で最高値更新"]
        groups, _, _ = mnc.rule_judge([{"title": t} for t in titles])
        self.assertTrue(all(g["critical_alert"] for g in groups))

    def test_render_shows_reason_for_critical(self):
        out = mnc.curate(ITEMS, failing(), "m", rule_fallback=True)
        text = "\n".join(mnc.render_groups(out))
        self.assertIn("判定理由: キーワード判定", text)


if __name__ == "__main__":
    unittest.main()
