"""日経CNBCの空振り遮断・フォールバック・固定スロット起動(2026-10-12)。通信なし(gh・Gemini・RSSはモック)。
実行: .venv/bin/python -m unittest tests.test_nikkei_gating
"""
import datetime as dt
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import dispatch_workflows as dw  # noqa: E402
import fetch_nikkei_cnbc_digest as nk  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
MON = dt.datetime(2026, 10, 12, 10, 0, tzinfo=JST)     # 月曜
SAT = dt.datetime(2026, 10, 10, 10, 0, tzinfo=JST)     # 土曜
ENV = {"DISCORD_WEBHOOK_MARKET": "https://discord.com/api/webhooks/1/x", "GEMINI_API_KEY": "K"}


def utc_iso(t):
    return t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def video(vid, desc="概要欄の本文。" * 6):
    return {"video_id": vid, "title": f"動画{vid}", "url": f"https://youtu.be/{vid}", "published": "", "description": desc}


class ZeroNewGeminiTest(unittest.TestCase):
    def run_main(self, videos, state, client=None, fetch=None):
        client = client or mock.MagicMock()
        sent = []
        fetch_patch = mock.patch.object(nk, "fetch_channel_videos", return_value=videos) if fetch is None else mock.patch.object(nk, "fetch_channel_videos", side_effect=fetch)
        with mock.patch.dict(os.environ, ENV), \
             mock.patch("google.genai.Client", return_value=client), \
             mock.patch.object(nk, "load_state", return_value=state), \
             mock.patch.object(nk, "save_state"), \
             fetch_patch, \
             mock.patch.object(nk, "fetch_transcript", return_value="字幕" * 30) as tr, \
             mock.patch.object(nk, "send_embeds", side_effect=lambda w, e, **k: (sent.extend(e), True)[1]), \
             mock.patch("sys.argv", ["x"]), mock.patch("sys.stdout", new_callable=io.StringIO):
            code = nk.main()
        return code, client, sent, tr

    def test_no_new_videos_means_no_gemini_call(self):
        code, client, sent, tr = self.run_main([video("a")], {"notified": {"a": MON.isoformat()}})
        self.assertEqual(code, 0)
        self.assertEqual(client.models.generate_content.call_count, 0)
        self.assertEqual(sent, [])
        self.assertEqual(tr.call_count, 0)                  # 字幕取得も行わない

    def test_pending_retry_alone_does_not_call_gemini(self):
        state = {"notified": {"p": MON.isoformat()}, "pending_summary": {"p": MON.isoformat()}}
        code, client, sent, _ = self.run_main([video("p")], state)
        self.assertEqual(code, 0)
        self.assertEqual(client.models.generate_content.call_count, 0)

    def test_rss_failure_is_error_not_zero_new(self):
        # RSS取得失敗は「新着0件」と混同せず、エラー終了(戻り値1)・Gemini/Discordは呼ばない
        code, client, sent, _ = self.run_main([], {"notified": {}}, fetch=nk.FeedError("通信失敗"))
        self.assertEqual(code, 1)
        self.assertEqual(client.models.generate_content.call_count, 0)
        self.assertEqual(sent, [])

    def test_one_new_video_calls_gemini_once(self):
        client = mock.MagicMock()
        client.models.generate_content.return_value = mock.Mock(text='[{"index": 0, "lines": ["・要点"]}]')
        code, client, sent, _ = self.run_main([video("n")], {"notified": {}}, client=client)
        self.assertEqual(code, 0)
        self.assertEqual(client.models.generate_content.call_count, 1)

    def test_batch_is_capped_at_five_items_per_gemini_call(self):
        # 新着7件 → 1回のGemini呼び出しで扱うのは最大5件。残り2件は未既読のまま次回へ繰り越す
        videos = [video(str(i)) for i in range(7)]   # 新しい順。古い順に処理されるので先頭5件は 6..2 側
        client = mock.MagicMock()
        client.models.generate_content.return_value = mock.Mock(text="[]")
        code, client, sent, _ = self.run_main(videos, {"notified": {}}, client=client)
        self.assertEqual(client.models.generate_content.call_count, 1)
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual(prompt.count("タイトル: "), 5)
        self.assertEqual(len(sent), 5)

    def test_retry_fills_only_remaining_batch_budget(self):
        # 新着4件 + 要約待ち3件 → 合計5件に収まるよう、再試行は1件だけ
        new = [video(f"n{i}") for i in range(4)]
        pend = [video(f"p{i}") for i in range(3)]
        state = {"notified": {}, "pending_summary": {f"p{i}": MON.isoformat() for i in range(3)}}
        client = mock.MagicMock()
        client.models.generate_content.return_value = mock.Mock(text="[]")
        self.run_main(new + pend, state, client=client)
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual(prompt.count("タイトル: "), 5)

    def test_gemini_429_still_delivers_link_embeds(self):
        # 429(日次上限など)でも配信を完遂し、要約なしのリンクEmbedを送る
        client = mock.MagicMock()
        client.models.generate_content.side_effect = Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
        with mock.patch("gemini_guard.Guard.call", side_effect=Exception("429 RESOURCE_EXHAUSTED")):
            code, client, sent, _ = self.run_main([video("x")], {"notified": {}}, client=client)
        self.assertEqual(code, 0)
        self.assertEqual(len(sent), 1)
        embed = sent[0]
        self.assertIn("要約は生成できませんでした", embed["description"])
        self.assertIn("https://youtu.be/x", embed["description"])
        self.assertTrue(embed["title"].startswith("📺 "))


class DispatchSlotTest(unittest.TestCase):
    def at(self, day, hour, minute=0):
        return day.replace(hour=hour, minute=minute)

    def run_at(self, t, status="completed"):
        return {"status": status, "createdAt": utc_iso(t), "event": "schedule"}

    def test_slots_are_weekday_830_1230_1630_2030(self):
        for h, m in [(8, 30), (12, 30), (16, 30), (20, 30)]:
            go, reason = dw.nikkei_slot_due(None, self.at(MON, h, m))
            self.assertTrue(go, f"{h}:{m:02d}: {reason}")

    def test_off_slot_times_do_not_launch(self):
        # 窓(スロット時刻から3時間)の外だけを検証する。窓内の遅延起動は test_late_wakeup_... で扱う
        for h, m in [(8, 0), (8, 29), (12, 0), (16, 0), (20, 0), (3, 0)]:
            go, _ = dw.nikkei_slot_due(None, self.at(MON, h, m))
            self.assertFalse(go, f"{h}:{m:02d} should not launch")

    def test_weekend_never_launches(self):
        for h, m in [(8, 30), (12, 30), (16, 30), (20, 30)]:
            self.assertFalse(dw.nikkei_slot_due(None, self.at(SAT, h, m))[0])

    def test_slot_not_relaunched_after_run_in_same_slot(self):
        run = self.run_at(self.at(MON, 8, 31))
        self.assertFalse(dw.nikkei_slot_due(run, self.at(MON, 8, 40))[0])
        # 次のスロット(12:30)になれば再び起動する
        self.assertTrue(dw.nikkei_slot_due(run, self.at(MON, 12, 30))[0])

    def test_late_wakeup_still_catches_current_slot_within_window(self):
        # Macがスリープで8:30を逃しても、同じスロットの窓内(3時間)なら拾う
        self.assertTrue(dw.nikkei_slot_due(None, self.at(MON, 10, 15))[0])

    def test_busy_run_blocks_launch(self):
        run = self.run_at(self.at(MON, 8, 0), status="in_progress")
        self.assertFalse(dw.nikkei_slot_due(run, self.at(MON, 8, 30))[0])

    def test_daily_launch_count_is_at_most_four(self):
        now = self.at(MON, 0)
        last = None
        launches = 0
        while now < self.at(MON, 0) + dt.timedelta(days=1):
            go, _ = dw.nikkei_slot_due(self.run_at(last) if last else None, now)
            if go:
                launches += 1
                last = now
            now += dt.timedelta(minutes=5)
        self.assertEqual(launches, 4)

    def test_main_does_not_dispatch_nikkei(self):
        # 2026-10-13 停止: 旧 nikkei_cnbc_digest.yml は起動係(WORKFLOWS)から除外済み。
        # 起動係が nikkei を一切起動しない(スロット時刻でも・gh workflow run も呼ばない)ことを固定する
        with mock.patch.object(dw, "latest_run", return_value=None), \
             mock.patch.object(dw, "check_anzn_liveness", return_value=None), \
             mock.patch.object(dw, "gh", return_value=(0, "", "")) as gh, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dw.main([])
        self.assertNotIn("nikkei_cnbc_digest.yml", out.getvalue())
        for c in gh.call_args_list:
            self.assertNotIn("nikkei_cnbc_digest.yml", c.args)


if __name__ == "__main__":
    unittest.main()
