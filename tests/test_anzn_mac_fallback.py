"""自宅Mac(あんぜんねっと本体)が取れないときの公式Mastodon代替配信のテスト(2026-11-06)。

通信なし・送信なし。Mastodon取得・Discord送信・GitHub書き込みはすべて差し替える。
実行: .venv/bin/python -m unittest tests.test_anzn_mac_fallback
"""
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import anzn_local as al  # noqa: E402
import fetch_news  # noqa: E402
import mastodon_anzn as ma  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime(2026, 11, 6, 10, 0, tzinfo=JST)


def _post(pid, art, created="2026-11-06T00:55:00.000Z"):
    return {
        "id": pid,
        "created_at": created,
        "content": f"<p># 北本市 火災 災害発生情報 北本市○○地内 https://anzn.net/sp/?11217F&amp;i={art}</p>",
    }


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.webhook = "https://example.invalid/webhook"  # 実通信しない
        p = mock.patch.object(fetch_news, "send_embed_to_discord", return_value=True)
        self.send = p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(al, "publish_mac_sent", return_value=True)
        self.publish = p2.start()
        self.addCleanup(p2.stop)

    def _run(self, state, posts, anzn_ok, dry_run=False, remote=None):
        al.run_mastodon_fallback(
            state, NOW, self.webhook, anzn_ok=anzn_ok, dry_run=dry_run,
            posts_fetch=lambda cursor: (posts, False),
            remote_sent=lambda: remote,
        )

    def test_anzn_ok_only_advances_cursor(self):
        state = {ma.CURSOR_KEY: f"{NOW.isoformat()}|100"}
        self._run(state, [_post("300", 197), _post("200", 196)], anzn_ok=True)
        self.send.assert_not_called()
        self.assertEqual(ma.get_cursor(state), "300")

    def test_anzn_fail_sends_new_post_and_records(self):
        state = {ma.CURSOR_KEY: f"{NOW.isoformat()}|100"}
        self._run(state, [_post("300", 197)], anzn_ok=False)
        self.send.assert_called_once()
        self.assertIn("anzn:11217F:197", state)
        self.publish.assert_called_once()
        self.assertEqual(self.publish.call_args[0][0], {"anzn:11217F:197"})
        self.assertEqual(ma.get_cursor(state), "300")

    def test_skips_article_already_sent_by_mac_url_key(self):
        state = {
            ma.CURSOR_KEY: f"{NOW.isoformat()}|100",
            "https://anzn.net/sp/?11217F&i=197": NOW.isoformat(),  # Mac本体で送信済み(URL形式)
        }
        self._run(state, [_post("300", 197)], anzn_ok=False)
        self.send.assert_not_called()
        self.assertEqual(ma.get_cursor(state), "300")  # 送らなくてもカーソルは進める

    def test_skips_article_already_sent_by_actions(self):
        state = {ma.CURSOR_KEY: f"{NOW.isoformat()}|100"}
        self._run(state, [_post("300", 197)], anzn_ok=False, remote={"anzn:11217F:197"})
        self.send.assert_not_called()

    def test_send_failure_keeps_cursor_and_state(self):
        self.send.return_value = False
        state = {ma.CURSOR_KEY: f"{NOW.isoformat()}|100"}
        self._run(state, [_post("300", 197)], anzn_ok=False)
        self.assertNotIn("anzn:11217F:197", state)
        self.assertEqual(ma.get_cursor(state), "100")  # 次回に再送できるようカーソルは据え置き
        self.publish.assert_not_called()

    def test_dry_run_does_not_send_or_record(self):
        state = {ma.CURSOR_KEY: f"{NOW.isoformat()}|100"}
        self._run(state, [_post("300", 197)], anzn_ok=False, dry_run=True)
        self.send.assert_not_called()
        self.assertNotIn("anzn:11217F:197", state)
        self.assertEqual(ma.get_cursor(state), "100")

    def test_first_run_sets_cursor_without_sending(self):
        state = {}
        self._run(state, [_post("300", 197)], anzn_ok=False)
        self.send.assert_not_called()
        self.assertEqual(ma.get_cursor(state), "300")

    def test_mastodon_fetch_error_is_contained(self):
        state = {ma.CURSOR_KEY: f"{NOW.isoformat()}|100"}

        def boom(cursor):
            raise RuntimeError("network down")

        al.run_mastodon_fallback(state, NOW, self.webhook, anzn_ok=False, posts_fetch=boom,
                                 remote_sent=lambda: None)
        self.send.assert_not_called()
        self.assertEqual(ma.get_cursor(state), "100")


class ArticleKeyHelpersTest(unittest.TestCase):
    def test_url_keys_are_converted_to_common_key(self):
        state = {
            "https://anzn.net/sp/?11217F&i=195&gist=北": "2026-11-01T00:00:00+09:00",
            "https://example.com/news/1": "2026-11-01T00:00:00+09:00",
            "_mastodon_anzn_cursor": "2026-11-01T00:00:00+09:00|1",
        }
        self.assertEqual(al._anzn_keys_from_state_urls(state), {"anzn:11217F:195"})


if __name__ == "__main__":
    unittest.main()
