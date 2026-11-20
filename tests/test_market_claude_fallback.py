"""経済ニュース判定の2回路目(Claude)のテスト(2026-11-20)。
回路: Gemini → Claude → (両方失敗なら)未判定の即時配信。実際のGemini・Claude APIには接続しない。
実行: .venv/bin/python -m unittest tests.test_market_claude_fallback
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
GOOD = json.dumps([{"indices": [0], "representative": 0, "category": "米国市場",
                    "importance": "high", "reason": "r", "critical_alert": True}], ensure_ascii=False)


def failing_gemini(err="503 UNAVAILABLE"):
    client = mock.Mock()
    client.models.generate_content.side_effect = RuntimeError(err)
    return client


def ok_gemini():
    client = mock.Mock()
    client.models.generate_content.return_value = mock.Mock(text=GOOD)
    return client


class CircuitTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"MARKET_NEWS_CURATION": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_gemini_ok_does_not_call_claude(self):
        claude = mock.Mock(return_value=GOOD)
        out = mnc.curate(ITEMS, ok_gemini(), "m", fallback=claude)
        self.assertIsNotNone(out)
        claude.assert_not_called()
        self.assertIsNone(mnc.LAST_ERROR)

    def test_gemini_fails_claude_judges(self):
        claude = mock.Mock(return_value=GOOD)
        out = mnc.curate(ITEMS, failing_gemini(), "m", fallback=claude)
        self.assertIsNotNone(out)
        self.assertTrue(out["groups"][0]["critical_alert"])
        claude.assert_called_once()
        # Claude で判定できたので、Gemini障害の通知(LAST_ERROR)は立てない
        self.assertIsNone(mnc.LAST_ERROR)

    def test_gemini_bad_json_falls_back_to_claude(self):
        bad = mock.Mock()
        bad.models.generate_content.return_value = mock.Mock(text="not json")
        claude = mock.Mock(return_value=GOOD)
        out = mnc.curate(ITEMS, bad, "m", fallback=claude)
        self.assertIsNotNone(out)
        claude.assert_called_once()

    def test_both_fail_returns_none_and_keeps_gemini_error(self):
        claude = mock.Mock(side_effect=RuntimeError("Claude 529 overloaded"))
        err_client = failing_gemini("429 RESOURCE_EXHAUSTED")
        out = mnc.curate(ITEMS, err_client, "m", fallback=claude)
        self.assertIsNone(out)                                 # 呼び出し側が未判定の即時配信に進む
        self.assertIn("RESOURCE_EXHAUSTED", str(mnc.LAST_ERROR))  # 通知の分類はGemini側の例外で行う

    def test_no_gemini_client_uses_claude_only(self):
        claude = mock.Mock(return_value=GOOD)
        out = mnc.curate(ITEMS, None, "m", fallback=claude)
        self.assertIsNotNone(out)
        claude.assert_called_once()

    def test_no_clients_returns_none_quietly(self):
        self.assertIsNone(mnc.curate(ITEMS, None, "m", fallback=None))
        self.assertIsNone(mnc.LAST_ERROR)

    def test_make_claude_fallback_requires_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.assertIsNone(mnc.make_claude_fallback())

    def test_make_claude_fallback_uses_env_model_and_text_blocks(self):
        fake_msg = mock.Mock(content=[mock.Mock(type="text", text="{\"groups\": []}")])
        client = mock.Mock()
        client.messages.create.return_value = fake_msg
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "CLAUDE_MODEL": "claude-test-model"}), \
                mock.patch("anthropic.Anthropic", return_value=client) as ctor:
            judge = mnc.make_claude_fallback()
            text = judge("prompt")
        self.assertEqual(text, "{\"groups\": []}")
        self.assertEqual(ctor.call_args.kwargs["api_key"], "test-key")
        self.assertEqual(client.messages.create.call_args.kwargs["model"], "claude-test-model")


if __name__ == "__main__":
    unittest.main()
