# -*- coding: utf-8 -*-
"""
fetch_nikkei_cnbc_digest.py — 日経CNBC公式YouTubeチャンネルの新着動画を全自動で監視・要約・配信

2026-09-22、細川さんの指定により「完全自動化（手放し運用）に一本化」。従来は
以下の2箇所に日経CNBC関連の処理が分散していたが、本スクリプトに統合し両方から削除した。
  - my-project/scripts/fetch_news.py の「📺 日経CNBC 要約」枠
    → 実体は最新1本のタイトル+リンクのみで、要約は一切していなかった(名ばかりの要約)。
  - 一般社団法人りっきー興行/04_Stocks/morning_stock_report.py の「📺 日経CNBC / マーケット要約」欄
    → 実際に字幕取得・Gemini要約をしていたが、朝レポートの1フィールドとして1日1回・
      その時点の最新1本だけを拾う設計で、複数本投稿された日は後発の動画を取りこぼしていた。

本スクリプトの設計:
  1. チャンネル監視: 公式YouTubeチャンネル(@NikkeiCNBC)のRSSフィード(無料・APIキー不要)を
     都度チェックする。ショート動画(/shorts/)はノイズのため除外し、通常動画はすべて対象にする。
  2. 重複排除: 処理済みの動画IDを state/notified_cnbc_videos.json に記録する
     (プロジェクト内の他スクリプトと同じ「state/配下・Gitコミットして永続化」方式に合わせた。
     細川さんの要件文中の data/notified_cnbc_videos.json とはディレクトリ名のみ異なる)。
     直近RETENTION_DAYS日 かつ 最大MAX_ENTRIES件を超えた古い記録はパージする。
  3. 文字起こし・要約: youtube-transcript-apiで日本語字幕を取得し、Gemini APIで
     「日経平均の動き・注目セクター・為替金利動向」を軸に3〜5行の箇条書きに圧縮する。
     字幕が無い動画(ライブアーカイブ化直後等)はタイトル+概要欄からの要約にフォールバックする。
     要約に失敗した場合も動画自体は「新着」として送信し、その旨を明記する(黙って握りつぶさない)。
  4. Discord通知: タイトル・公開日時・要約・YouTubeリンクをEmbedにまとめ、
     #webhook_market (WEBHOOK_MARKET) へ配信する。新着が0件の日は何も送らない。

環境変数:
  DISCORD_WEBHOOK_MARKET (必須)
  GEMINI_API_KEY          (必須。無い場合は要約なしでタイトル+リンクのみ配信する)

使い方:
  python scripts/fetch_nikkei_cnbc_digest.py            # 通常運用
  python scripts/fetch_nikkei_cnbc_digest.py --dry-run   # 送信せず内容を標準出力

依存: feedparser, requests, youtube-transcript-api, google-genai（requirements.txtに導入済み）
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

import feedparser
import requests

JST = datetime.timezone(datetime.timedelta(hours=9))
STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "notified_cnbc_videos.json")
STATE_RETENTION_DAYS = 60
STATE_MAX_ENTRIES = 500
# 2026-09-26(N6): 要約できずリンクのみ配信した動画は state["pending_summary"] に残し、
# 次回以降(RSSにまだ載っている間)に再要約して要約を追送する。字幕は投稿数時間後に付くことも多い。
PENDING_SUMMARY_MAX_HOURS = 72  # これを過ぎたら再要約をあきらめる
MAX_RETRIES_PER_RUN = 4

NIKKEI_CNBC_CHANNEL_ID = "UClVsQnfs-jKkjKmUKUHnT2g"
NIKKEI_CNBC_RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={NIKKEI_CNBC_CHANNEL_ID}"
RSS_FETCH_RETRIES = 3
RSS_RETRY_BASE_DELAY = 3.0
REQUEST_TIMEOUT = 20
TRANSCRIPT_MAX_CHARS = 6000
GEMINI_MODEL_NAME = "gemini-3.6-flash"
MAX_VIDEOS_PER_RUN = 8  # 1回の実行で処理する新着動画の上限(初回運用時の大量消化・API負荷対策)
COLOR_CNBC = 0xC0392B

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ─────────────────────────────────────────────────────────────
#  状態管理（重複排除）
# ─────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data.get("notified"), dict) else {"notified": {}}
    except (OSError, ValueError, AttributeError):
        return {"notified": {}}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def prune_state(state, now):
    cutoff = now - datetime.timedelta(days=STATE_RETENTION_DAYS)
    kept = {}
    for vid, iso_ts in state.get("notified", {}).items():
        try:
            ts = datetime.datetime.fromisoformat(iso_ts)
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            kept[vid] = iso_ts
    if len(kept) > STATE_MAX_ENTRIES:
        kept = dict(sorted(kept.items(), key=lambda kv: kv[1], reverse=True)[:STATE_MAX_ENTRIES])
    state["notified"] = kept
    pending = {}
    for vid, iso_ts in state.get("pending_summary", {}).items():
        try:
            age = now - datetime.datetime.fromisoformat(iso_ts)
        except (ValueError, TypeError):
            continue
        if age <= datetime.timedelta(hours=PENDING_SUMMARY_MAX_HOURS):
            pending[vid] = iso_ts
        else:
            print(f"[INFO] 再要約の期限({PENDING_SUMMARY_MAX_HOURS}時間)を過ぎたため打ち切り: {vid}")
    if pending:
        state["pending_summary"] = pending
    else:
        state.pop("pending_summary", None)  # 通常時のstateの形を変えない
    return state


# ─────────────────────────────────────────────────────────────
#  チャンネル監視
# ─────────────────────────────────────────────────────────────
def fetch_channel_videos(limit=15, retries=RSS_FETCH_RETRIES):
    """RSSから直近の通常動画(ショート除く)を新しい順で返す。取得失敗時は空リスト。"""
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(NIKKEI_CNBC_RSS_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if not feed.entries:
                print("[WARN] 日経CNBC RSSの新着が0件でした(構造変化の可能性)。")
                return []
            out = []
            for e in feed.entries[:limit]:
                link = e.get("link", "")
                vid = e.get("yt_videoid", "")
                if not link or not vid or "/shorts/" in link:
                    continue
                out.append({
                    "video_id": vid, "title": e.get("title", "(タイトル不明)"), "url": link,
                    "description": e.get("summary", ""),
                    "published": format_published_jst(e.get("published_parsed")),
                    "published_parsed": e.get("published_parsed"),
                })
            return out
        except Exception as err:  # noqa: BLE001
            last_err = err
            print(f"[WARN] 日経CNBC RSS取得に失敗(試行{attempt + 1}/{retries}): {err}")
            time.sleep(RSS_RETRY_BASE_DELAY * (attempt + 1))
    print(f"[ERROR] 日経CNBC RSS取得に失敗しました(全{retries}回試行): {last_err}")
    return []


def format_published_jst(parsed):
    if not parsed:
        return "-"
    try:
        dt_utc = datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return "-"
    return dt_utc.astimezone(JST).strftime("%m/%d %H:%M")


# ─────────────────────────────────────────────────────────────
#  文字起こし・要約
# ─────────────────────────────────────────────────────────────
def fetch_transcript(video_id):
    """日本語字幕テキスト。取得できない場合はNone(概要欄フォールバックのシグナル)。"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id, languages=["ja"])
        text = " ".join(s.text for s in transcript)
        return text[:TRANSCRIPT_MAX_CHARS] if text else None
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] 字幕取得に失敗しました({video_id}): {err}")
        return None


def summarize(title, source_text, is_transcript):
    """3〜5行の箇条書き要約。GEMINI_API_KEY未設定・本文が薄い・失敗時はNone。"""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or not source_text or len(source_text) < 40:
        return None
    try:
        from google import genai
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] google-genaiの読み込みに失敗しました: {err}")
        return None

    source_label = "動画の字幕テキスト" if is_transcript else "動画タイトルと概要欄"
    prompt = f"""以下は日経CNBC（日本の経済専門テレビ局）の動画の{source_label}です。

タイトル: {title}

本文:
{source_text}

この内容から、日経平均の動き・注目セクター・為替金利動向を軸に、今日の相場の要点を
3〜5行の箇条書きで日本語でまとめてください。各行は「・」で始め、簡潔に(1行40字程度)。
本文に無い数値・銘柄名は書かないこと(推測で埋めない)。
{"字幕が実際の発言内容のため、具体的な数値・銘柄名・材料を優先してください。" if is_transcript else "概要欄からは詳細が読み取れない場合があるため、分かる範囲で簡潔にまとめてください。"}
説明文やコードフェンスは不要で、箇条書きの本文のみ出力してください。"""
    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt)
        lines = [ln.strip() for ln in resp.text.strip().split("\n") if ln.strip()]
        lines = [ln if ln.startswith("・") else f"・{ln}" for ln in lines]
        return lines[:5] if lines else None
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] 要約生成に失敗しました: {err}")
        return None


def build_digest(video):
    """(要約行のリスト or None, 出典ラベル) を返す。"""
    transcript = fetch_transcript(video["video_id"])
    if transcript:
        lines = summarize(video["title"], transcript, is_transcript=True)
        if lines:
            return lines, "字幕からの要約"
    # フォールバック: 概要欄
    desc = (video.get("description") or "").strip()
    if desc:
        lines = summarize(video["title"], desc, is_transcript=False)
        if lines:
            return lines, "概要欄からの要約(字幕なし)"
    return None, None


# ─────────────────────────────────────────────────────────────
#  Discord配信
# ─────────────────────────────────────────────────────────────
def build_embed(video, digest_lines, source_label):
    if digest_lines:
        desc = "\n".join(digest_lines) + f"\n\n[動画を見る](<{video['url']}>)"
    else:
        desc = f"（要約は生成できませんでした。動画本編をご確認ください）\n\n[動画を見る](<{video['url']}>)"
    return {
        "title": f"📺 {video['title']}"[:256],
        "url": video["url"],
        "description": desc[:4000],
        "color": COLOR_CNBC,
        "footer": {"text": f"日経CNBC公式チャンネル ｜ 公開: {video['published']}"
                           + (f" ｜ {source_label}" if source_label else "")},
    }


def send_embeds(webhook_url, embeds, batch_size=10):
    ok = True
    for i in range(0, len(embeds), batch_size):
        try:
            resp = requests.post(webhook_url, json={"embeds": embeds[i:i + batch_size]}, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 300:
                print(f"[ERROR] Discord送信失敗 HTTP{resp.status_code}: {resp.text[:300]}")
                ok = False
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] Discord送信失敗: {err}")
            ok = False
        time.sleep(0.5)
    return ok


# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="送信せず内容を標準出力")
    args = ap.parse_args()

    now = datetime.datetime.now(JST)
    state = prune_state(load_state(), now)
    notified = state["notified"]

    videos = fetch_channel_videos()
    new_videos = [v for v in videos if v["video_id"] not in notified]
    # 古い順に処理する(Discord上での投稿順を時系列に揃えるため)
    new_videos = list(reversed(new_videos))[:MAX_VIDEOS_PER_RUN]
    pending = state.get("pending_summary", {})
    retry_videos = [v for v in reversed(videos) if v["video_id"] in pending][:MAX_RETRIES_PER_RUN]

    print(f"[INFO] RSS取得 {len(videos)}件 / 新着 {len(new_videos)}件"
          + (f"（{len(new_videos)}件に制限。残りは次回実行で処理）" if len(new_videos) == MAX_VIDEOS_PER_RUN else ""))

    if not new_videos and not retry_videos:
        print("[INFO] 新着動画はありませんでした。")
        return 0

    embeds = []
    failed_ids = []
    for v in new_videos:
        lines, source_label = build_digest(v)
        embeds.append(build_embed(v, lines, source_label))
        if not lines:
            failed_ids.append(v["video_id"])
        print(f"  - {v['published']} {v['title'][:50]} … " + ("要約OK" if lines else "要約なし(リンクのみ)"))

    recovered_ids = []
    for v in retry_videos:
        lines, source_label = build_digest(v)
        if lines:
            embeds.append(build_embed(v, lines, f"{source_label}・再要約"))
            recovered_ids.append(v["video_id"])
        print(f"  - [再要約] {v['title'][:50]} … " + ("要約OK" if lines else "要約なし(次回再試行)"))
    if not embeds:
        print("[INFO] 再要約できた動画がありませんでした(次回再試行)。")
        return 0

    if args.dry_run:
        print("\n[dry-run] 送信予定Embed:")
        print(json.dumps(embeds, ensure_ascii=False, indent=2))
        return 0

    webhook = os.environ.get("DISCORD_WEBHOOK_MARKET", "").strip()
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_MARKET が設定されていません。")
        return 1

    ok = send_embeds(webhook, embeds)
    # 送信できた分だけ既読にする(全滅時は次回再試行、部分成功はここでは全件既読とする
    # 簡易実装。Discord送信はバッチ単位のため、個別成否の切り分けはしない)。
    if ok:
        for v in new_videos:
            notified[v["video_id"]] = now.isoformat()
        # 要約できなかった動画は「リンク配信済み・要約待ち」、再要約できた動画は要約待ちから外す
        for vid in failed_ids:
            state.setdefault("pending_summary", {})[vid] = now.isoformat()
        for vid in recovered_ids:
            state.get("pending_summary", {}).pop(vid, None)
        if not state.get("pending_summary"):
            state.pop("pending_summary", None)
        save_state(state)
        print(f"[INFO] Discord配信完了: {len(embeds)}件")
        return 0
    print("[WARN] Discord送信に失敗したため、既読記録の保存をスキップします(次回再試行します)。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
