"""あんぜんねっと(自宅Mac)配信の既送信記録が壊れているときの安全な停止のテスト(2026-10-13)。

通信なし・送信なし。取得・送信・保存はすべて差し替えて、本物を呼ばない。
実行: .venv/bin/python -m unittest tests.test_anzn_local_state
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import anzn_local as al  # noqa: E402
import fetch_news  # noqa: E402


class SeenStateFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.state = os.path.join(self.dir, "anzn_seen.json")
        self.env = os.path.join(self.dir, "missing.env")  # 存在しない .env(Webhook は環境変数で与えない)

        def patch(target, name, *args, **kw):
            p = mock.patch.object(target, name, *args, **kw)
            self.addCleanup(p.stop)
            return p.start()

        self.fetch = patch(fetch_news, "fetch_anzn_new_items", return_value=[])
        self.send_embed = patch(fetch_news, "send_embed_to_discord", return_value=True)
        self.send_text = patch(fetch_news, "send_to_discord", return_value=True)
        self.save = patch(fetch_news, "save_seen_state")
        patch(fetch_news, "STATE_PATH", self.state)  # run() がこの値を書き換えるため、テスト後に元へ戻す
        patch(al.news_alerts, "flush")
        patch(al.news_alerts, "issues", return_value=[])

    def run_main(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = al.main(["--state", self.state, "--env-file", self.env, *extra])
        return code, buf.getvalue()

    def assert_nothing_sent_or_saved(self):
        self.fetch.assert_not_called()
        self.send_embed.assert_not_called()
        self.send_text.assert_not_called()
        self.save.assert_not_called()

    def write_bytes(self, data):
        with open(self.state, "wb") as fh:
            fh.write(data)
        with open(self.state, "rb") as fh:
            return fh.read()

    def test_broken_json_stops_with_code_1_and_keeps_file(self):
        before = self.write_bytes(b'{"https://anzn.net/sp/?1": "2026-10-1')
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("[ERROR]", out)
        self.assert_nothing_sent_or_saved()
        with open(self.state, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(os.listdir(self.dir), ["anzn_seen.json"])  # バックアップ等のファイルを作らない

    def test_non_dict_json_stops_with_code_1_and_keeps_file(self):
        before = self.write_bytes(b'["https://anzn.net/sp/?1", "https://anzn.net/sp/?2"]')
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("[ERROR]", out)
        self.assert_nothing_sent_or_saved()
        with open(self.state, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_empty_file_stops_with_code_1(self):
        before = self.write_bytes(b"")
        code, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assert_nothing_sent_or_saved()
        with open(self.state, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_missing_state_file_continues_normally(self):
        self.assertFalse(os.path.exists(self.state))
        code, _ = self.run_main("--dry-run")  # dry-run は送信も保存もしない
        self.assertEqual(code, 0)
        self.fetch.assert_called_once()  # 空の状態として取得処理まで進む
        self.send_embed.assert_not_called()
        self.save.assert_not_called()
        self.assertFalse(os.path.exists(self.state))


if __name__ == "__main__":
    unittest.main()
