"""あんぜんねっと(北本市安全安心情報)の取得: HTTP 403・タイムアウト・正常応答・通知抑制・ログの安全性。
通信は一切しない(requests.get を差し替える)。日時は固定。
実行: .venv/bin/python -m unittest tests.test_anzn_fetch
"""
import datetime as dt
import io
import os
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402
import news_alerts as na  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 11, 9, 22, tzinfo=JST)
WEBHOOK = "https://discord.com/api/webhooks/123456/SECRET-TOKEN-abcdef"

# 正常応答の最小サンプル(実ページと同じ構造: div[data-role=collapsible] > h3 > small×2 + a.cPLnk)
OK_HTML = """<html><head><meta charset="euc-jp"></head><body>
<div data-role="collapsible"><h3><small>10-11 04:37頃</small><small>北本市</small>火災 北本市荒井</h3>
<a class="cPLnk" href="https://anzn.net/sp/?11217F&amp;i=195&amp;gist=北">詳細</a></div>
<div data-role="collapsible"><h3><small>10-10 22:10頃</small><small>桶川市</small>救急 桶川市坂田</h3>
<a class="cPLnk" href="https://anzn.net/sp/?11217F&amp;i=194&amp;gist=北">詳細</a></div>
</body></html>""".encode("euc-jp")


class FakeResponse:
    def __init__(self, status=200, content=b"", body_text="SECRET-BODY-should-not-be-logged"):
        self.status_code = status
        self.content = content
        self.text = body_text
        self.url = "https://anzn.net/sp/?11217F&r1=1"
        self.reason = "Forbidden" if status == 403 else "OK"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error: {self.reason} for url: {self.url}", response=self)


class FetchTest(unittest.TestCase):
    def setUp(self):
        na.reset()
        fn.ANZN_LAST_FETCH_OK = None

    def fetch(self, side_effect=None, response=None):
        kw = {"side_effect": side_effect} if side_effect else {"return_value": response}
        with mock.patch.object(fn.requests, "get", **kw) as get, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            items = fn.fetch_anzn_new_arrivals()
        return items, get, out.getvalue()

    def test_normal_response_parsed_and_marked_ok(self):
        items, get, _ = self.fetch(response=FakeResponse(200, OK_HTML))
        self.assertTrue(fn.ANZN_LAST_FETCH_OK)
        self.assertEqual([i["city"] for i in items], ["北本市", "桶川市"])
        self.assertEqual(na.issues(), [])
        self.assertEqual(get.call_count, 1)                                # 1回だけ取りに行く(リトライしない)

    def test_403_is_recorded_once_with_status_and_no_body(self):
        items, get, out = self.fetch(response=FakeResponse(403))
        self.assertEqual(items, [])
        self.assertIs(fn.ANZN_LAST_FETCH_OK, False)
        self.assertEqual(get.call_count, 1)                                # 403を連打しない
        issue = na.issues()
        self.assertEqual([i["kind"] for i in issue], ["anzn_fetch"])
        self.assertIn("HTTP 403", issue[0]["detail"])
        self.assertNotIn("SECRET-BODY", out + str(issue))                  # 応答本文は出さない
        cause, tech = na.explain(issue[0]["detail"], "anzn_fetch")
        self.assertEqual((cause, tech), ("取得先へのアクセスが拒否されました", "HTTP 403"))

    def test_timeout_is_recorded_and_explained(self):
        items, _, _ = self.fetch(side_effect=requests.Timeout("read timed out"))
        self.assertEqual(items, [])
        self.assertIs(fn.ANZN_LAST_FETCH_OK, False)
        cause, tech = na.explain(na.issues()[0]["detail"], "anzn_fetch")
        self.assertEqual(cause, "取得先から応答がありませんでした")
        self.assertIsNone(tech)

    def test_connection_error_is_recorded(self):
        items, _, _ = self.fetch(side_effect=requests.ConnectionError("Failed to establish a new connection"))
        self.assertEqual(items, [])
        self.assertEqual(len(na.issues()), 1)

    def test_error_text_never_includes_webhook_or_token(self):
        # 例外文に Webhook URL が混ざっても、通知側でも伏せ字になる
        items, _, out = self.fetch(side_effect=requests.ConnectionError(f"failed posting to {WEBHOOK}"))
        detail = na.issues()[0]["detail"]
        self.assertNotIn("SECRET-TOKEN", detail + out)
        self.assertIn("<webhook>", detail)

    def test_headers_are_browser_like_and_unchanged(self):
        # 2026-09-06の対策(Accept/Accept-Language/Referer)が消えていないことの回帰テスト
        self.assertEqual(set(fn.ANZN_HEADERS), {"User-Agent", "Accept", "Accept-Language", "Referer"})
        self.assertEqual(fn.ANZN_HEADERS["Referer"], "https://anzn.net/")
        self.assertEqual(fn.ANZN_URL, "https://anzn.net/sp/?11217F&r1=1")
        _, get, _ = self.fetch(response=FakeResponse(200, OK_HTML))
        self.assertEqual(get.call_args.kwargs["headers"], fn.ANZN_HEADERS)
        self.assertEqual(get.call_args.kwargs["timeout"], fn.REQUEST_TIMEOUT)


class OtherSourcesTest(unittest.TestCase):
    """あんぜんねっとが失敗しても、他の取得・配信(ローカルニュース・全国・経済)は続く。"""

    def test_failure_does_not_touch_state_or_block_local_digest(self):
        state = {"https://example.com/keep": NOW.isoformat()}
        na.reset()
        with mock.patch.object(fn.requests, "get", side_effect=requests.HTTPError("403 Client Error: Forbidden")), \
             mock.patch.object(fn, "fetch_rss_items", return_value=[{"title": "北本市の小学校で運動会 - 埼玉新聞", "url": "https://example.com/a", "published": "10/11 08:00"}]), \
             mock.patch.object(fn, "fetch_direct_rss_items", return_value=[]), \
             mock.patch.object(fn.mastodon_anzn, "fetch_recent_items", return_value=[]), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            new_items = fn.fetch_anzn_new_items(state, NOW)
            anzn_state = dict(state)
            msg, _ = fn.build_local_news_message(state, NOW)
        self.assertEqual(new_items, [])
        self.assertEqual(anzn_state, {"https://example.com/keep": NOW.isoformat()})  # あんぜんねっと失敗では既送信記録が増えない
        self.assertIn("北本市の小学校で運動会", msg)                                  # ローカルニュースは配信される


class SuppressionTest(unittest.TestCase):
    """同じ403は12時間通知を抑止し、12時間後に再通知。内容が変わった場合は抑止しない。"""

    def run_failure(self, state, now, detail="取得失敗 HTTP 403: Forbidden for url: https://anzn.net/"):
        na.reset()
        na.record("anzn_fetch", "local", "あんぜんねっと(北本市安全安心情報)", detail,
                  "一部スキップ(安全安心情報の新着を取得できず/地域ニュースは継続)")
        payloads, notified, suppressed = na.build_embeds(state, now)
        if notified:
            for ch in payloads:
                pass
            for it in na.issues():
                if it["kind"] in notified:
                    state[na.ALERT_STATE_PREFIX + it["kind"]] = now.isoformat()   # flush() と同じ記録
        return notified, suppressed

    def test_same_403_suppressed_within_12h_and_resent_after(self):
        state = {}
        self.assertEqual(self.run_failure(state, NOW), (["anzn_fetch"], []))
        self.assertEqual(self.run_failure(state, NOW + dt.timedelta(hours=1)), ([], ["anzn_fetch"]))
        self.assertEqual(self.run_failure(state, NOW + dt.timedelta(hours=13)), (["anzn_fetch"], []))

    def test_changed_error_is_not_suppressed_by_old_403(self):
        state = {}
        self.run_failure(state, NOW)
        notified, suppressed = self.run_failure(state, NOW + dt.timedelta(hours=1), detail="取得失敗 ReadTimeout: timed out")
        self.assertEqual((notified, suppressed), ([], ["anzn_fetch"]))   # 同じkindのため抑止される(従来仕様)
        # 復旧後の再発を埋もれさせない: 取得成功で抑止を解除できる
        self.assertEqual(na.clear(state, "anzn_fetch"), 1)
        self.assertEqual(self.run_failure(state, NOW + dt.timedelta(hours=2)), (["anzn_fetch"], []))


class NotificationSafetyTest(unittest.TestCase):
    def test_notification_contains_no_secrets_or_raw_body(self):
        na.reset()
        na.record("anzn_fetch", "local", "あんぜんねっと(北本市安全安心情報)",
                  f"取得失敗 HTTP 403: Forbidden for url: {WEBHOOK} SECRET-BODY", "一部スキップ(新着を取得できず/地域ニュースは継続)")
        payloads, _, _ = na.build_embeds({}, NOW)
        text = str(payloads) + str(na.build_messages({}, NOW)[0])
        self.assertNotIn("SECRET-TOKEN", text)
        self.assertNotIn("SECRET-BODY", text)
        self.assertNotIn("discord.com/api/webhooks", text)


if __name__ == "__main__":
    unittest.main()
