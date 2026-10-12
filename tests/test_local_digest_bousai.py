"""定期まとめ「埼玉・県央ローカルニュース」の防災・緊急情報欄(公式Mastodon経由・2026-10-02)のテスト。
通信・Discordは使わない。
実行: .venv/bin/python -m unittest tests.test_local_digest_bousai
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import mastodon_anzn as ma  # noqa: E402
import fetch_news as fn  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 2, 12, 0, tzinfo=ma.JST)
OLD_WORDING = "このまとめ欄では取得していません"


def post(pid, city="北本市", kind="建物火災", art="200", disaster=True, minutes_ago=60, area="11217F"):
    created = (NOW - dt.timedelta(minutes=minutes_ago)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    src = "(災害発生情報より)" if disaster else "～犯罪情報官NEWSより"
    link = f'<a href="http://anzn.net/sp/?{area}&amp;i={art}">http://anzn.net/sp/?{area}&amp;i={art}</a>' if art else ""
    content = (f"<p>#{city} {kind}<br />10月2日 9:15ごろ、{city}本町1丁目地内で{kind}が発生し、消防隊が出動しています。"
               f" {src} さいたま県央火事ドコ？まっぷ → {link}</p>")
    return {"id": str(pid), "created_at": created, "content": content}


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.data


def fake_get(all_posts, status=200):
    calls = []

    def get(url, params=None, **k):
        calls.append(dict(params or {}))
        if status != 200:
            return FakeResp({}, status)
        ps = sorted(all_posts, key=lambda p: int(p["id"]), reverse=True)
        if params.get("max_id"):
            ps = [p for p in ps if int(p["id"]) < int(params["max_id"])]
        return FakeResp(ps[: params.get("limit", 40)])
    get.calls = calls
    return get


class FetchRecentTest(unittest.TestCase):
    def test_kenou_only_and_no_crime(self):
        posts = [post(1, "北本市", art="1"), post(2, "鴻巣市", kind="救急", art="2"), post(3, "桶川市", art="3"),
                 post(4, "川越市", art="4"),                       # 県央3市以外は入れない
                 post(5, "北本市", art="5", disaster=False)]       # 防犯情報は入れない
        items = ma.fetch_recent_items(NOW, get=fake_get(posts))
        self.assertEqual(sorted(i["city"] for i in items), ["北本市", "桶川市", "鴻巣市"])
        self.assertEqual(len(items), 3)

    def test_newest_first_and_same_article_once(self):
        posts = [post(1, art="7", minutes_ago=90), post(2, art="7", minutes_ago=60), post(3, art="8", minutes_ago=30)]
        items = ma.fetch_recent_items(NOW, get=fake_get(posts))
        self.assertEqual([i["key"] for i in items], ["anzn:11217F:8", "anzn:11217F:7"])  # 新しい順・同一記事は1件

    def test_24h_window_and_stops_paging(self):
        posts = [post(10, art="10", minutes_ago=23 * 60), post(9, art="9", minutes_ago=25 * 60)]
        posts += [post(i, "川越市", art=str(i), minutes_ago=26 * 60 + i) for i in range(1, 9)]
        g = fake_get(posts)
        items = ma.fetch_recent_items(NOW, hours=24, get=g)
        self.assertEqual([i["key"] for i in items], ["anzn:11217F:10"])
        self.assertEqual(len(g.calls), 1)  # 24時間より古い投稿に達したのでそれ以上ページ送りしない

    def test_read_only_no_state_involved(self):
        # 関数は state を受け取らない(カーソルにも既送信キーにも触れない)
        import inspect
        self.assertNotIn("state", inspect.signature(ma.fetch_recent_items).parameters)

    def test_error_raises(self):
        with self.assertRaises(Exception):
            ma.fetch_recent_items(NOW, get=fake_get([], status=500))


class SectionTest(unittest.TestCase):
    def items(self, n):
        return [{"key": f"anzn:11217F:{i}", "datetime": "10-02 09:15頃", "city": "北本市", "summary": f"建物火災 本町{i}丁目"}
                for i in range(n)]

    def test_with_items(self):
        text = fn.build_bousai_section(self.items(2), "mastodon")
        self.assertIn("## 🚨 防災・緊急情報", text)
        self.assertIn("- 10-02 09:15頃 ［北本市］建物火災 本町0丁目", text)
        self.assertIn("公式Mastodon経由", text)
        self.assertNotIn(OLD_WORDING, text)

    def test_none(self):
        text = fn.build_bousai_section([], "mastodon")
        self.assertIn("災害発生情報はありません", text)
        self.assertIn("北本市・鴻巣市・桶川市", text)
        self.assertNotIn(OLD_WORDING, text)

    def test_failed_is_not_none(self):
        text = fn.build_bousai_section([], "mastodon_failed")
        self.assertIn("確認できていません", text)
        self.assertNotIn("ありません", text)  # 取得失敗を「なし」と誤表示しない
        self.assertNotIn(OLD_WORDING, text)

    def test_cap_with_remainder(self):
        text = fn.build_bousai_section(self.items(13), "mastodon")
        self.assertEqual(text.count("［北本市］"), fn.BOUSAI_SUMMARY_MAX_ITEMS)
        self.assertIn("ほか3件", text)

    def test_legacy_statuses_unchanged(self):
        self.assertIn("取得できませんでした", fn.build_bousai_section([], "failed"))
        self.assertIn("警報・火災等の情報はありません", fn.build_bousai_section([], "ok"))

    def test_no_status_generates_old_wording(self):
        for st in ("ok", "failed", "mastodon", "mastodon_failed", "mac"):
            self.assertNotIn(OLD_WORDING, fn.build_bousai_section([], st))
            self.assertNotIn(OLD_WORDING, fn.build_bousai_section(self.items(1), st))


class DigestTest(unittest.TestCase):
    """build_local_news_message(ANZN_SOURCE=mac)。RSS・Mastodonは偽物に差し替える。"""

    def run_digest(self, general, recent=None, recent_exc=None, state=None):
        state = {} if state is None else state
        rss = [{"title": t, "url": f"https://example.com/{i}", "published": "10/02 08:00"}
               for i, t in enumerate(general)]
        fetch_recent = mock.Mock(side_effect=recent_exc) if recent_exc else mock.Mock(return_value=recent or [])
        with mock.patch.dict(os.environ, {"ANZN_SOURCE": "mac"}), \
             mock.patch.object(fn, "fetch_rss_items", return_value=rss), \
             mock.patch.object(fn, "LOCAL_DIRECT_RSS_FEEDS", []), \
             mock.patch.object(ma, "fetch_recent_items", fetch_recent):
            msg, sports = fn.build_local_news_message(state, NOW)
        return msg, state, fetch_recent

    def disaster(self, art="31"):
        return [{"key": f"anzn:11217F:{art}", "datetime": "10-02 09:15頃", "city": "桶川市", "summary": "建物火災 本町"}]

    def test_disaster_shown_with_local_news(self):
        msg, _, _ = self.run_digest(["桶川市で秋の収穫祭が開かれました"], recent=self.disaster())
        self.assertIn("# 🗾 埼玉・県央ローカルニュース", msg)
        self.assertIn("［桶川市］建物火災 本町", msg)
        self.assertIn("## 📰 地域ニュース", msg)               # 既存の地域ニュース部分は従来どおり
        self.assertIn("[桶川市で秋の収穫祭が開かれました](<https://example.com/0>)", msg)
        self.assertLess(msg.index("防災・緊急情報"), msg.index("地域ニュース"))
        self.assertNotIn(OLD_WORDING, msg)

    def test_no_disaster_shows_none_message(self):
        msg, _, _ = self.run_digest(["鴻巣市のひなまつり展が始まります"], recent=[])
        self.assertIn("災害発生情報はありません", msg)
        self.assertIn("## 📰 地域ニュース", msg)
        self.assertNotIn(OLD_WORDING, msg)

    def test_fetch_failure_still_sends_local_news(self):
        msg, _, _ = self.run_digest(["北本市の道路工事のお知らせです"], recent_exc=RuntimeError("HTTP 500"))
        self.assertIn("確認できていません", msg)
        self.assertIn("## 📰 地域ニュース", msg)
        self.assertNotIn(OLD_WORDING, msg)

    def test_disaster_alone_does_not_force_digest(self):
        msg, _, fetch_recent = self.run_digest([], recent=self.disaster())
        self.assertIsNone(msg)                                   # 30分cronで同じ記事を繰り返さない
        fetch_recent.assert_not_called()                         # まとめを出さない回はMastodonも読まない

    def test_does_not_touch_instant_state(self):
        _, state, _ = self.run_digest(["北本市の学校給食の新メニュー発表"], recent=self.disaster(),
                                      state={ma.CURSOR_KEY: "2026-10-02T11:30:00+09:00|555"})
        self.assertEqual(state[ma.CURSOR_KEY], "2026-10-02T11:30:00+09:00|555")   # カーソル不変
        self.assertFalse([k for k in state if k.startswith("anzn:")])              # 記事キーを既送信にしない


if __name__ == "__main__":
    unittest.main()
