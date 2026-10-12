"""mastodon_anzn / fetch_news.run_mastodon_anzn / anzn_local の重複防止のテスト(通信・Discordは使わない)。
実行: .venv/bin/python -m unittest tests.test_mastodon_anzn
"""
import base64
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import mastodon_anzn as ma  # noqa: E402
import fetch_news as fn  # noqa: E402
import news_alerts  # noqa: E402
import anzn_local  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 2, 12, 0, tzinfo=ma.JST)


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
    """Mastodon API の since_id / max_id / limit を再現する偽物(新しい順)。"""
    calls = []

    def get(url, params=None, **k):
        calls.append(dict(params or {}))
        if status != 200:
            return FakeResp({}, status)
        ps = sorted(all_posts, key=lambda p: int(p["id"]), reverse=True)
        if params.get("max_id"):
            ps = [p for p in ps if int(p["id"]) < int(params["max_id"])]
        if params.get("since_id"):
            ps = [p for p in ps if int(p["id"]) > int(params["since_id"])]
        return FakeResp(ps[: params.get("limit", 40)])
    get.calls = calls
    return get


class ParseTest(unittest.TestCase):
    def test_kenou_disaster(self):
        it = ma.parse_post(post(1, city="桶川市", kind="救助事案", art="197"))
        self.assertEqual((it["key"], it["city"], it["datetime"]), ("anzn:11217F:197", "桶川市", "10-02 09:15頃"))
        self.assertEqual(it["summary"], "救助事案 桶川市本町1丁目")
        self.assertEqual(it["url"], "https://anzn.net/sp/?11217F&i=197")

    def test_filters(self):
        self.assertIsNone(ma.parse_post(post(1, city="三郷市")))           # 県央3市以外
        self.assertIsNone(ma.parse_post(post(1, disaster=False)))          # 防犯情報など
        self.assertIsNone(ma.parse_post(post(1, art=None))["key"])         # 記事IDが無い場合は None(呼び出し側で代替)

    def test_key_shared_with_mac_url(self):
        mac_url = "https://anzn.net/sp/?11217F&i=197&gist=桶川市大字坂田"
        self.assertEqual(ma.anzn_key_from_url(mac_url), ma.parse_post(post(1, art="197"))["key"])
        self.assertNotEqual(ma.anzn_key_from_url("https://anzn.net/sp/?11S&i=197"), "anzn:11217F:197")  # 地域が違えば別キー


class FetchTest(unittest.TestCase):
    def test_pagination_until_cursor(self):
        posts = [post(i) for i in range(1, 101)]  # 1〜100
        g = fake_get(posts)
        got, trunc = ma.fetch_since("10", get=g)
        self.assertEqual(sorted(int(p["id"]) for p in got), list(range(11, 101)))  # 90件をページ送りで全部
        self.assertFalse(trunc)
        self.assertEqual(len(g.calls), 4)  # 40+40+10 → 空ページで終了

    def test_truncation_and_initial(self):
        posts = [post(i) for i in range(1, 201)]
        got, trunc = ma.fetch_since("1", get=fake_get(posts), max_pages=2)
        self.assertTrue(trunc)
        self.assertEqual(len(got), 80)
        got, trunc = ma.fetch_since(None, get=fake_get(posts))
        self.assertEqual((len(got), got[0]["id"], trunc), (40, "200", False))  # 初回は最新1ページだけ

    def test_errors_raise(self):
        with self.assertRaises(RuntimeError):
            ma.fetch_since("1", get=fake_get([], status=503))


class SelectTest(unittest.TestCase):
    def test_grace_dedupe_and_cursor(self):
        posts = [post(1, art="1"), post(2, city="三郷市", art="2"), post(3, art="3"), post(4, art="3"),
                 post(5, art=None), post(6, art="6", minutes_ago=5), post(7, art="7", minutes_ago=90)]
        state = {"anzn:11217F:1": "x"}
        send, cursor_to, waiting = ma.select_to_send(posts, state, {"anzn:11217F:7"}, NOW)
        self.assertEqual([s["key"] for s in send], ["anzn:11217F:3", "mastodon:5"])  # 1=送信済み,2=対象外,4=同一記事,7=Mac送信済み
        self.assertEqual((cursor_to, waiting), ("5", 1))  # 猶予中の6より先へは進めない(7も次回)


class SendLimitTest(unittest.TestCase):
    def test_30_items_split_25_then_5(self):
        # 30件の未処理の災害情報(間に対象外の投稿も混ぜる)
        posts = []
        for i in range(1, 31):
            posts.append(post(i * 2, art=str(100 + i)))
            posts.append(post(i * 2 + 1, city="三郷市", art=str(900 + i)))
        state = {}
        ma.set_cursor(state, "1", NOW)
        sent = []
        with mock.patch.object(ma, "fetch_since",
                               lambda c: (sorted([p for p in posts if int(p["id"]) > int(c)], key=lambda p: -int(p["id"])), False)), \
                mock.patch.object(fn, "MAC_SENT_PATH", "/nonexistent.json"), \
                mock.patch.object(fn, "send_embed_to_discord", lambda w, e: (sent.append(e), True)[1]):
            news_alerts.reset()
            fn.run_mastodon_anzn(state, NOW, "https://discord.com/api/webhooks/1/T")
            self.assertEqual(len(sent[0]["embeds"][0]["fields"]), 25)  # 1回目は25件
            keys = {k for k in state if k.startswith("anzn:")}
            self.assertEqual(keys, {f"anzn:11217F:{100 + i}" for i in range(1, 26)})
            self.assertFalse(any(f"anzn:11217F:{100 + i}" in state for i in range(26, 31)))  # 26〜30件目は未送信のまま
            self.assertEqual(ma.get_cursor(state), "51")  # 25件目(投稿ID50)の後の対象外投稿51まで。26件目(52)の手前
            sent.clear()
            fn.run_mastodon_anzn(state, NOW, "https://discord.com/api/webhooks/1/T")
            self.assertEqual(len(sent[0]["embeds"][0]["fields"]), 5)  # 2回目は残り5件
        self.assertEqual({k for k in state if k.startswith("anzn:")}, {f"anzn:11217F:{100 + i}" for i in range(1, 31)})
        self.assertEqual(ma.get_cursor(state), "61")

    def test_limit_matches_embed_field_cap(self):
        self.assertEqual(ma.MAX_SEND_PER_RUN, fn.ANZN_EMBED_FIELD_LIMIT)


class RunTest(unittest.TestCase):
    def run_once(self, state, posts, send_ok=True, status=200, mac_sent=()):
        sent = []
        with tempfile.TemporaryDirectory() as d:
            ms = os.path.join(d, "mac.json")
            with open(ms, "w", encoding="utf-8") as f:
                json.dump({k: "2026-10-02T00:00:00+09:00" for k in mac_sent}, f)
            with mock.patch.object(ma, "fetch_since", lambda c: ma.__class__ and (
                        (_ for _ in ()).throw(RuntimeError("HTTP 503")) if status != 200 else
                        (sorted([p for p in posts if not c or int(p["id"]) > int(c)], key=lambda p: -int(p["id"])), False))), \
                    mock.patch.object(fn, "MAC_SENT_PATH", ms), \
                    mock.patch.object(fn, "send_embed_to_discord", lambda w, e: (sent.append(e), send_ok)[1]):
                news_alerts.reset()
                err = fn.run_mastodon_anzn(state, NOW, "https://discord.com/api/webhooks/1/T")
        return err, sent, [i["kind"] for i in news_alerts.issues()]

    def test_initial_no_bulk_send(self):
        state = {}
        err, sent, _ = self.run_once(state, [post(i, art=str(i)) for i in range(1, 30)])
        self.assertEqual((err, sent), (False, []))
        self.assertEqual(ma.get_cursor(state), "29")

    def test_send_success_and_no_resend(self):
        state = {}
        ma.set_cursor(state, "10", NOW)
        posts = [post(11, art="11"), post(12, art="12")]
        err, sent, _ = self.run_once(state, posts)
        self.assertEqual(len(sent[0]["embeds"][0]["fields"]), 2)
        self.assertIn("公式Mastodon経由", sent[0]["embeds"][0]["footer"]["text"])
        self.assertIn("anzn:11217F:11", state)
        self.assertEqual(ma.get_cursor(state), "12")
        err, sent, _ = self.run_once(state, posts)
        self.assertEqual(sent, [])  # 同じ記事は二度と送らない

    def test_send_failure_keeps_state(self):
        state = {}
        ma.set_cursor(state, "10", NOW)
        err, sent, kinds = self.run_once(state, [post(11, art="11")], send_ok=False)
        self.assertTrue(err)
        self.assertNotIn("anzn:11217F:11", state)
        self.assertEqual(ma.get_cursor(state), "10")  # カーソルも進めない(次回に再送)
        self.assertIn("discord_send_mastodon_anzn", kinds)

    def test_fetch_failure_does_not_stop(self):
        state = {}
        ma.set_cursor(state, "10", NOW)
        err, sent, kinds = self.run_once(state, [], status=503)
        self.assertFalse(err)
        self.assertIn("mastodon_anzn_fetch", kinds)

    def test_skip_mac_sent(self):
        state = {}
        ma.set_cursor(state, "10", NOW)
        err, sent, _ = self.run_once(state, [post(11, art="11")], mac_sent={"anzn:11217F:11"})
        self.assertEqual(sent, [])
        self.assertEqual(ma.get_cursor(state), "11")

    def test_cursor_survives_prune(self):
        state = {}
        ma.set_cursor(state, "10", NOW)
        self.assertEqual(ma.get_cursor(fn.prune_old_entries(state, NOW)), "10")


class MacSideTest(unittest.TestCase):
    def test_read_actions_keys_and_publish(self):
        store = {"mac": None}

        def gh(*args, input_text=None):
            path = [a for a in args if a.startswith("repos/")][0]
            if path.endswith("news_seen.json"):
                return 0, json.dumps({"anzn:11217F:5": "x", "https://news.yahoo.co.jp/a": "x"}), ""
            if "-X" in args:
                body = json.loads(input_text)
                store["mac"] = json.loads(base64.b64decode(body["content"]).decode())
                return 0, "{}", ""
            if store["mac"] is None:
                return 1, "", "HTTP 404"
            return 0, json.dumps({"sha": "abc", "content": base64.b64encode(json.dumps(store["mac"]).encode()).decode()}), ""
        self.assertEqual(anzn_local.fetch_actions_sent_keys(gh), {"anzn:11217F:5"})
        self.assertTrue(anzn_local.publish_mac_sent({"anzn:11217F:6"}, NOW, gh))
        self.assertTrue(anzn_local.publish_mac_sent({"anzn:11217F:7"}, NOW, gh))
        self.assertEqual(sorted(store["mac"]), ["anzn:11217F:6", "anzn:11217F:7"])

    def test_gh_failure_is_safe(self):
        fail = lambda *a, **k: (1, "", "auth error")  # noqa: E731
        self.assertIsNone(anzn_local.fetch_actions_sent_keys(fail))
        self.assertFalse(anzn_local.publish_mac_sent({"anzn:11217F:6"}, NOW, fail))


if __name__ == "__main__":
    unittest.main()
