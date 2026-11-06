"""防災まとめ(直近24時間)の同一内容多重配信の防止(2026-10-11)。通信なし(モックデータ)。
検証: 2回連続実行しても2回目に既掲載の防災行が0件になること、新着だけが載ること、送信失敗時に次回再掲されること、
即時通知の既送信キー(anzn:)に触れないこと。
実行: .venv/bin/python -m unittest tests.test_bousai_digest_dedupe
"""
import contextlib
import datetime as dt
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch_news as fn  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 11, 10, 0, tzinfo=JST)
FIRE_A = {"key": "anzn:11217F:111", "datetime": "10-11 04:37頃", "city": "北本市", "summary": "火災 北本市荒井",
          "url": "https://anzn.net/sp/?11217F&i=111", "status_id": "1", "created_at": "2026-10-11T04:40:00+09:00"}
FIRE_B = {"key": "anzn:11217F:112", "datetime": "10-11 06:10頃", "city": "鴻巣市", "summary": "救急 鴻巣市本町",
          "url": "https://anzn.net/sp/?11217F&i=112", "status_id": "2", "created_at": "2026-10-11T06:15:00+09:00"}
FIRE_C = {"key": "anzn:11217F:113", "datetime": "10-11 09:20頃", "city": "桶川市", "summary": "火災 桶川市坂田",
          "url": "https://anzn.net/sp/?11217F&i=113", "status_id": "3", "created_at": "2026-10-11T09:25:00+09:00"}
BOUSAI_LINE_RE = "］"          # 防災行は「- 日時［市］概要」の形式


def local_article(n):
    return [{"title": f"北本市の小学校で運動会{n} - 埼玉新聞", "url": f"https://example.com/local/{n}", "published": "10/11 08:00"}]


def run(state, mastodon_items, local_n, send_ok=True, env="mac"):
    """1回のローカルダイジェスト生成(Mastodon経由・ANZN_SOURCE=mac)。送信失敗時は fn と同じ要領で既送信を取り消す。"""
    keys_before = set(state)
    with mock.patch.dict(os.environ, {"ANZN_SOURCE": env}), \
         mock.patch.object(fn.mastodon_anzn, "fetch_recent_items", return_value=[dict(i) for i in mastodon_items]), \
         mock.patch.object(fn, "fetch_rss_items", return_value=local_article(local_n) if local_n else []), \
         mock.patch.object(fn, "fetch_direct_rss_items", return_value=[]), \
         contextlib.redirect_stdout(io.StringIO()):
        msg, _ = fn.build_local_news_message(state, NOW)
    if msg is not None and not send_ok:
        fn._rollback_new_keys(state, keys_before)      # main() と同じ: Discord送信失敗なら既掲載を取り消す
    return msg


def bousai_lines(msg):
    if not msg:
        return []
    return [ln for ln in msg.split("\n") if ln.startswith("- ") and BOUSAI_LINE_RE in ln]


class TwoRunsTest(unittest.TestCase):
    def test_second_run_has_zero_duplicate_bousai_lines(self):
        state = {}
        m1 = run(state, [FIRE_A, FIRE_B], local_n=1)
        self.assertEqual(len(bousai_lines(m1)), 2)
        m2 = run(state, [FIRE_A, FIRE_B], local_n=2)          # 新しい地域ニュースが出て、まとめは再び作られる
        self.assertIsNotNone(m2)
        self.assertIn("北本市の小学校で運動会2", m2)
        self.assertEqual(bousai_lines(m2), [])                  # 重複0件
        self.assertNotIn("## 🚨 防災・緊急情報", m2)            # 同じ晩の見出しは1回だけ(新着も無いので載せない)

    def test_same_night_new_item_is_shown_without_repeating_heading(self):
        state = {}
        m1 = run(state, [FIRE_A], local_n=1)
        self.assertEqual(m1.count("## 🚨 防災・緊急情報"), 1)
        m2 = run(state, [FIRE_A, FIRE_C], local_n=2)           # 同じ晩の新着は載る
        self.assertEqual(bousai_lines(m2), ["- 10-11 09:20頃 ［桶川市］火災 桶川市坂田"])
        self.assertEqual(m2.count("## 🚨 防災・緊急情報"), 1)  # 見出しは増えない

    def test_next_night_shows_heading_again(self):
        state = {}
        run(state, [FIRE_A], local_n=1)
        later = dt.datetime(2026, 10, 11, 19, 0, tzinfo=JST)   # 同日でも18時を過ぎれば次の晩
        with mock.patch.dict(os.environ, {"ANZN_SOURCE": "mac"}), \
             mock.patch.object(fn.mastodon_anzn, "fetch_recent_items", return_value=[dict(FIRE_A)]), \
             mock.patch.object(fn, "fetch_rss_items", return_value=local_article(2)), \
             mock.patch.object(fn, "fetch_direct_rss_items", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()):
            msg, _ = fn.build_local_news_message(state, later)
        self.assertIn("## 🚨 防災・緊急情報", msg)

    def test_only_new_item_is_shown_on_next_run(self):
        state = {}
        run(state, [FIRE_A], local_n=1)
        m2 = run(state, [FIRE_A, FIRE_C], local_n=2)
        self.assertEqual([ln for ln in bousai_lines(m2)], [f"- 10-11 09:20頃 ［桶川市］火災 桶川市坂田"])

    def test_third_run_still_no_duplicates(self):
        state = {}
        run(state, [FIRE_A, FIRE_B], local_n=1)
        run(state, [FIRE_A, FIRE_B], local_n=2)
        m3 = run(state, [FIRE_A, FIRE_B, FIRE_C], local_n=3)
        self.assertEqual(len(bousai_lines(m3)), 1)

    def test_no_local_news_and_all_shown_sends_nothing(self):
        state = {}
        run(state, [FIRE_A], local_n=1)
        self.assertIsNone(run(state, [FIRE_A], local_n=None))   # 防災情報だけでは定期まとめを出さない(従来どおり)

    def test_failed_send_rolls_back_so_next_run_shows_again(self):
        state = {}
        run(state, [FIRE_A], local_n=1, send_ok=False)
        m2 = run(state, [FIRE_A], local_n=2)
        self.assertEqual(len(bousai_lines(m2)), 1)              # 送れなかったまとめの分は次回に載る

    def test_empty_bousai_keeps_original_text(self):
        state = {}
        m = run(state, [], local_n=1)
        self.assertIn("- 直近24時間に確認された北本市・鴻巣市・桶川市の災害発生情報はありません", m)


class NonMacPathTest(unittest.TestCase):
    """ANZN_SOURCE が mac 以外(Actions が直接取得する従来経路)でも同じ重複抑止。"""

    def run_direct(self, state, items, local_n):
        with mock.patch.dict(os.environ, {"ANZN_SOURCE": ""}), \
             mock.patch.object(fn, "fetch_bousai_summary_items", return_value=[dict(i) for i in items]), \
             mock.patch.object(fn, "ANZN_LAST_FETCH_OK", True), \
             mock.patch.object(fn, "fetch_rss_items", return_value=local_article(local_n)), \
             mock.patch.object(fn, "fetch_direct_rss_items", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()):
            msg, _ = fn.build_local_news_message(state, NOW)
        return msg

    def test_direct_path_no_duplicates(self):
        plain = [{"datetime": "10-11 04:37頃", "city": "北本市", "summary": "火災 北本市荒井", "url": "https://anzn.net/sp/?11217F&i=111"}]
        state = {}
        self.assertEqual(len(bousai_lines(self.run_direct(state, plain, 1))), 1)
        m2 = self.run_direct(state, plain, 2)
        self.assertEqual(bousai_lines(m2), [])


class IsolationTest(unittest.TestCase):
    def test_immediate_alert_keys_untouched_and_no_collision(self):
        state = {"anzn:11217F:111": "2026-10-11T04:41:00+09:00"}
        run(state, [FIRE_A], local_n=1)
        self.assertEqual(state["anzn:11217F:111"], "2026-10-11T04:41:00+09:00")       # 即時通知の既送信キーは変わらない
        shown = [k for k in state if k.startswith(fn.BOUSAI_SHOWN_PREFIX)]
        self.assertEqual(shown, [fn.BOUSAI_SHOWN_PREFIX + "anzn:11217F:111"])
        self.assertFalse(any(k.startswith("anzn:") and k != "anzn:11217F:111" for k in state))

    def test_item_identity_fallbacks(self):
        self.assertEqual(fn._bousai_item_id({"key": "k1", "url": "u"}), "k1")
        self.assertEqual(fn._bousai_item_id({"url": "u"}), "u")
        self.assertEqual(fn._bousai_item_id({"datetime": "d", "city": "c", "summary": "s"}), "d|c|s|")
        self.assertEqual(fn._bousai_item_id({"status_id": "7"}), "status:7")


class WindowAndIdentityTest(unittest.TestCase):
    """2026-10-12: 直近24時間以内に送信済みの同一事案(本文一致 / 発生日時・場所一致)を再送しない。"""

    def filt(self, state, items, now=NOW):
        return fn.filter_bousai_already_shown(items, state, now)

    def test_same_body_different_id_within_window_is_skipped(self):
        state = {}
        fn.mark_bousai_shown([FIRE_A], state, NOW)
        copy = dict(FIRE_A, key="anzn:OTHER:999", url="https://other/x")       # 別の記事IDでも本文が同じ
        self.assertEqual(self.filt(state, [copy])[0], [])

    def test_same_datetime_and_city_different_body_is_not_skipped(self):
        # 2026-10-12: 判定は本文のSHA256のみ。発生日時・場所だけの一致では除外しない
        state = {}
        fn.mark_bousai_shown([FIRE_A], state, NOW)
        same_event = dict(FIRE_A, key="anzn:X:1", summary="火災 北本市 別表現")
        self.assertEqual(len(self.filt(state, [same_event])[0]), 1)

    def test_sent_more_than_24h_ago_is_sent_again(self):
        state = {}
        fn.mark_bousai_shown([FIRE_A], state, NOW - dt.timedelta(hours=25))
        self.assertEqual(len(self.filt(state, [FIRE_A])[0]), 1)

    def test_within_24h_is_still_skipped(self):
        state = {}
        fn.mark_bousai_shown([FIRE_A], state, NOW - dt.timedelta(hours=23))
        self.assertEqual(self.filt(state, [FIRE_A])[0], [])

    def test_different_event_is_not_skipped(self):
        state = {}
        fn.mark_bousai_shown([FIRE_A], state, NOW)
        self.assertEqual(len(self.filt(state, [FIRE_B, FIRE_C])[0]), 2)

    def test_full_width_and_spaces_ignored_for_body_match(self):
        state = {}
        fn.mark_bousai_shown([FIRE_A], state, NOW)
        spaced = dict(FIRE_A, key="anzn:Z:2", summary="火災　北本市荒井")          # 全角空白の違いだけ
        self.assertEqual(self.filt(state, [spaced])[0], [])


if __name__ == "__main__":
    unittest.main()
