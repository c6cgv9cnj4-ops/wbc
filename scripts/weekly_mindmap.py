# -*- coding: utf-8 -*-
"""
週次「心の棚卸し」マインドマップ生成パイプライン

毎週日曜 20:00 JST(GitHub Actions cron `0 11 * * 0`)に無人実行し、直近1週間の
記録を 1) 円形放射状マインドマップ画像(PNG) と 2) Google スプレッドシートの
週次タブ(チェックボックス付き) の 2 系統に出力する。

------------------------------------------------------------------------------
処理フロー
------------------------------------------------------------------------------
  1. データ収集
     - Discord: `# モーニングジャーナル`(フォーラム型チャンネル)配下の、過去7日間に
       作成された日付スレッド(`YYYY/MM/DD`)の全投稿を時系列で結合して取得する。
       ※ export_discord_logs.py のフォーラム取得ロジックと同じ考え方
         (アクティブ+アーカイブ済みスレッド一覧 → スノーフレークから作成日時復元)。
     - Google Calendar: 過去7日間の実績イベント + 翌週7日間の確定予定。
     - Google Tasks: 過去7日間に完了したタスク + 未完了タスク一覧。
     Google 側は 3 API とも「同一の OAuth リフレッシュトークン」で認証する
     (個人 Gmail はサービスアカウントから Tasks / 個人カレンダーを読めないため)。
     カレンダー/タスクはタスク管理の一覧としてではなく、「心のベクトル」を裏付ける
     補助材料(達成実感・体調のリズム・気がかりの残存)として下記の構造化に使う。

  2. Gemini による構造化(gemini-3.6-flash / GEMINI_MODEL で上書き可)
     モーニングジャーナルの記述を主素材に、感情・心理の固定 4 象限のツリー JSON を
     抽出させる。
       1 意欲・ワクワク   (写真・カルチャー・探求・好奇心が向いたこと)   [緑]
       2 モヤモヤ・負荷   (先送りした葛藤・集中を削ぐ執着・気掛かり)     [赤]
       3 体調・バイオリズム (頭の重さ・運動/歩数の達成感・リズムの波)   [青]
       4 納得・心地よさ   (習慣の定着・ホッとした瞬間・整った実感)     [黄]
     各象限 → 中分類ノード → 末端トピック の 3 階層に加え、来週の手帳用アクション
     3行も同時に生成させる。Gemini 失敗時は収集データからキーワードベースで
     決定論的にツリー+アクションを組み立てるフォールバックへ自動で切り替える。

  3. マインドマップ画像(PNG)描画
     matplotlib のみ(graphviz 非依存)。中央ルートから 4 色の大丸を放射状に配置し、
     その先へ中丸・小丸を扇状に広げる。日本語フォントは環境内の Noto Sans CJK /
     IPAexGothic / ヒラギノ等を自動検出。文字サイズは可読性優先で大きめに固定し、
     余白を切り詰めて中央にテキストが凝縮するレイアウトにする。左下に
     「◆ 来週の手帳用3大アクション」を Gemini/フォールバックが生成した文言入りで
     描画する(空欄では出力しない)。テキストは折り返し + 文字数上限で枠外への
     はみ出しを防ぐ。

  4. Google スプレッドシートへの追記
     OAuth 認証で、指定スプレッドシート(WEEKLY_SPREADSHEET_ID。未設定なら新規作成し
     ID をログ出力)に週次タブ `YYYY_Www`(ISO 週)を作成。既存なら中身を作り直す
     (冪等)。列は A:大分類 / B:中分類 / C:具体的な内容・トピック / D:採用
     (D は BOOLEAN データ検証を付けたチェックボックス、初期値 FALSE)。

  5. Discord 通知投稿
     `# 週間まとめ` チャンネルへ mindmap.png を添付投稿。本文にスプレッドシートの
     リンクと案内文を添える。DISCORD_WEBHOOK_WEEKLY があれば Webhook、無ければ
     DISCORD_CHANNEL_ID_WEEKLY_SUMMARY + Bot トークンで送信。

------------------------------------------------------------------------------
冪等性・エラーハンドリング方針(ワークフローを確実に緑で通すため)
------------------------------------------------------------------------------
  * ジャーナル 0 件 / カレンダー空 / タスク空 でも落とさない。空なら「記録なし」
    ノードで雛形マップを描き、スプレッドシートはヘッダのみ、Discord には
    「今週は入力がありませんでした」の短文を投稿する。
  * データソース単位で try/except。1 ソースが 403/404/一過性エラーでも他は続行。
  * Gemini 失敗 → 決定論フォールバック。GEMINI_API_KEY 未設定でも致命にしない。
  * 週次タブが既にあれば作り直し。同じ週を何度再実行しても行は重複しない。
  * 致命終了(exit 1)は「--dry-run でも --use-mock でもないのに Google OAuth
    認証情報が 1 つも無い」場合のみ。それ以外は警告に留めて exit 0。

------------------------------------------------------------------------------
ローカル / モックでのテスト
------------------------------------------------------------------------------
  # 通信なし。合成データで PNG と行プレビュー CSV だけ生成
  python scripts/weekly_mindmap.py --use-mock

  # 実データを収集するが Sheets 書き込み / Discord 投稿はしない(PNG は生成)
  python scripts/weekly_mindmap.py --dry-run

  # 対象週の基準日を指定(バックフィル・検証用)
  python scripts/weekly_mindmap.py --week 2026-09-07 --dry-run

OAuth リフレッシュトークンの発行手順は docs/WEEKLY_MINDMAP_SETUP.md、
生成用ヘルパは scripts/mint_google_oauth_token.py を参照。

------------------------------------------------------------------------------
環境変数
------------------------------------------------------------------------------
  GEMINI_API_KEY                       (任意) 未設定ならフォールバック構造化
  GEMINI_MODEL                         (任意) 既定 gemini-3.6-flash
  DISCORD_BOT_TOKEN                    (任意) ジャーナル取得用
  DISCORD_CHANNEL_ID_MORNING_JOURNAL   (任意) `# モーニングジャーナル` のチャンネルID
  DISCORD_CHANNEL_ID_HEALTH            (任意) 上が未設定のときのフォールバック
  DISCORD_WEBHOOK_WEEKLY               (任意) `# 週間まとめ` の Webhook URL
  DISCORD_CHANNEL_ID_WEEKLY_SUMMARY    (任意) Webhook が無いとき Bot 投稿する先
  GOOGLE_OAUTH_CLIENT_ID               (任意) OAuth クライアントID
  GOOGLE_OAUTH_CLIENT_SECRET           (任意) OAuth クライアントシークレット
  GOOGLE_OAUTH_REFRESH_TOKEN           (任意) OAuth リフレッシュトークン
    ※ OAuth 同意画面は「本番(In production)」で発行すること。「テスト」だと
      リフレッシュトークンが 7 日で失効し、本ジョブ(週1回)は 2 回目以降必ず
      認証エラーになる。認証失敗時は Discord 本文に ⚠️ を出して可視化する。
  WEEKLY_SPREADSHEET_ID                (必須級) 書き込み先スプレッドシート ID。
    未設定なら Sheets 追記だけスキップ(PNG と Discord は継続)。初回は
    `--create-spreadsheet` で 1 度だけ新規作成し、出力された ID を登録する。
  GOOGLE_CALENDAR_IDS                  (任意) カンマ区切り。既定 "primary"
  DRY_RUN                              (任意) 1 で Sheets / Discord をスキップ
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
import textwrap

import requests

# ===========================================================================
# 定数
# ===========================================================================
JST = datetime.timezone(datetime.timedelta(hours=9))
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000  # 2015-01-01T00:00:00Z(スノーフレークの起点)
REQUEST_TIMEOUT = 20
PAST_DAYS = 7
NEXT_DAYS = 7

# 他スクリプト(generate_daily_report.py / fetch_news.py)と同一の現行モデルに揃える。
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

REPORT_WEEKLY_DIR = "reports/weekly"
MINDMAP_DIR = "mindmap"
MINDMAP_ARCHIVE_DIR = "mindmap/archive"
MINDMAP_PAGES_URL = "https://c6cgv9cnj4-ops.github.io/wbc/mindmap/"

DISCORD_USER_AGENT = "wbc-weekly-mindmap/1.0 (+https://github.com/c6cgv9cnj4-ops/wbc)"

# OAuth スコープ(すべて読み取り + Sheets 書き込み)
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# 感情・心理の 4 象限(順序・色・キーは固定。Gemini にもこの順で返させる)
# title … 凡例・スプレッドシート・プロンプト用のフル名
# short … マインドマップの大丸に収める短縮名
CATEGORIES = [
    {"key": "motivation", "title": "意欲・ワクワク",     "short": "意欲\nワクワク",     "color": "#16A34A"},  # 緑
    {"key": "friction",   "title": "モヤモヤ・負荷",     "short": "モヤモヤ\n負荷",     "color": "#DC2626"},  # 赤
    {"key": "biorhythm",  "title": "体調・バイオリズム", "short": "体調\nバイオリズム", "color": "#2563EB"},  # 青
    {"key": "contentment","title": "納得・心地よさ",     "short": "納得\n心地よさ",     "color": "#D4A017"},  # 黄
]

# 構造化ツリーの上限(超過分は「…他N件」に畳む)。スプレッドシート/CSV は
# この文字数まで保持し、画像描画側で必要に応じてさらに短く切り詰める。
MAX_MIDS_PER_CATEGORY = 6
MAX_ENDS_PER_MID = 4
CLIP_MID = 30
CLIP_END = 60

# 来週の手帳用アクション(固定3行)の上限文字数
MAX_ACTIONS = 3
CLIP_ACTION = 42


# ===========================================================================
# 週コンテキスト
# ===========================================================================
def week_context(anchor: datetime.date) -> dict:
    """基準日 anchor が属する ISO 週の情報と収集ウィンドウを返す。"""
    iso_year, iso_week, _ = anchor.isocalendar()
    monday = datetime.date.fromisocalendar(iso_year, iso_week, 1)
    now = datetime.datetime.now(JST)
    return {
        "anchor": anchor,
        "iso_year": iso_year,
        "iso_week": iso_week,
        "week_tag": f"{iso_year}_W{iso_week:02d}",       # スプレッドシートのタブ名
        "label": f"{monday.strftime('%Y/%m/%d')}週",     # ルートノードの表示名
        "root_title": f"心の棚卸しマップ ({monday.strftime('%Y/%m/%d')}週)",
        "past_start": now - datetime.timedelta(days=PAST_DAYS),
        "now": now,
        "next_end": now + datetime.timedelta(days=NEXT_DAYS),
    }


# ===========================================================================
# 小ヘルパ
# ===========================================================================
def _clip(text, n: int) -> str:
    text = " ".join(str(text if text is not None else "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def snowflake_to_dt_utc(snowflake_id) -> datetime.datetime:
    ms = (int(snowflake_id) >> 22) + DISCORD_EPOCH_MS
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)


def _rfc3339(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ===========================================================================
# 1) Discord: モーニングジャーナル(フォーラム型チャンネル)の過去7日分
# ===========================================================================
def _discord_get(path: str, token: str, params: dict | None = None):
    """GET + JSON。429 と 5xx は指数バックオフで最大 3 回まで再試行する。"""
    import time

    url = f"{DISCORD_API_BASE}{path}"
    headers = {"Authorization": f"Bot {token}", "User-Agent": DISCORD_USER_AGENT}
    for attempt in range(3):
        resp = requests.get(url, headers=headers, params=params or {}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429 and attempt < 2:
            wait = float(resp.headers.get("Retry-After", "1") or "1")
            time.sleep(min(wait, 8))
            continue
        if resp.status_code >= 500 and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def _fetch_thread_messages(thread_id: str, token: str) -> list[dict]:
    """スレッド内の全メッセージを古い順に返す(ページング対応)。"""
    out: list[dict] = []
    before = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        batch = _discord_get(f"/channels/{thread_id}/messages", token, params)
        if not batch:
            break
        out.extend(batch)
        before = batch[-1]["id"]
        if len(batch) < 100:
            break
    out.reverse()
    return out


def _list_forum_threads(channel_id: str, token: str) -> list[dict]:
    """フォーラムチャンネル配下のアクティブ + アーカイブ済み公開スレッドを列挙。"""
    threads: list[dict] = []
    meta = _discord_get(f"/channels/{channel_id}", token)
    guild_id = meta.get("guild_id")
    if guild_id:
        data = _discord_get(f"/guilds/{guild_id}/threads/active", token)
        threads.extend(
            t for t in data.get("threads", []) if t.get("parent_id") == channel_id
        )
    before = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        data = _discord_get(
            f"/channels/{channel_id}/threads/archived/public", token, params
        )
        batch = data.get("threads", [])
        threads.extend(batch)
        last_ts = (
            batch[-1].get("thread_metadata", {}).get("archive_timestamp")
            if batch
            else None
        )
        if not data.get("has_more") or not batch or not last_ts:
            break
        before = last_ts
    return threads


def _parse_thread_date(name: str) -> datetime.date | None:
    """スレッド名 'YYYY/MM/DD'(区切りは / . - スペース等なんでも)から日付を取り出す。

    区切りありなら [年,月,日] の 3 グループ、区切り無しの 8 連続数字なら YYYYMMDD として解釈。
    どちらも駄目なら None(呼び出し側でスノーフレークの作成日時にフォールバック)。
    """
    def _mk(y, m, d):
        if 2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
            try:
                return datetime.date(y, m, d)
            except ValueError:
                return None
        return None

    # 「4桁 2桁 2桁」で始まる区切り表記(先頭に年らしい 4 桁が来るものだけ採用)
    groups = "".join(c if c.isdigit() else " " for c in (name or "")).split()
    for i in range(len(groups) - 2):
        if len(groups[i]) == 4:
            got = _mk(int(groups[i]), int(groups[i + 1]), int(groups[i + 2]))
            if got:
                return got
            break
    # 区切り無しの 8 連続数字 YYYYMMDD
    joined = "".join(ch for ch in (name or "") if ch.isdigit())
    if len(joined) >= 8:
        return _mk(int(joined[:4]), int(joined[4:6]), int(joined[6:8]))
    return None


def collect_journal(token: str, channel_id: str, ctx: dict) -> list[dict]:
    """過去7日間の日付スレッドの投稿を時系列で結合して返す。

    返り値: [{"date": "YYYY-MM-DD", "time": "HH:MM", "thread": str, "text": str}, ...]
    どのステップで失敗しても [] を返し、パイプライン全体は止めない。
    """
    if not token or not channel_id:
        print("[WARN] Discord トークン/チャンネルID が無いためジャーナル取得をスキップ")
        return []
    start_date = ctx["past_start"].astimezone(JST).date()
    end_date = ctx["now"].astimezone(JST).date()
    try:
        threads = _list_forum_threads(channel_id, token)
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] スレッド一覧の取得に失敗(権限不足の可能性): {err}")
        return []

    seen: set[str] = set()
    picked: list[tuple[datetime.date, str, str]] = []
    for t in threads:
        tid = t.get("id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        d = _parse_thread_date(t.get("name", "")) or snowflake_to_dt_utc(tid).astimezone(JST).date()
        if start_date <= d <= end_date:
            picked.append((d, tid, t.get("name", "(無題)")))

    picked.sort(key=lambda x: (x[0], x[1]))
    entries: list[dict] = []
    for d, tid, name in picked:
        try:
            msgs = _fetch_thread_messages(tid, token)
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] スレッド {name} の取得に失敗、スキップ: {err}")
            continue
        for m in msgs:
            body = (m.get("content") or "").strip()
            if not body:
                continue
            try:
                ts = datetime.datetime.fromisoformat(m["timestamp"]).astimezone(JST)
                tstr = ts.strftime("%H:%M")
            except Exception:  # noqa: BLE001
                tstr = ""
            entries.append(
                {"date": d.strftime("%Y-%m-%d"), "time": tstr, "thread": name, "text": body}
            )
    print(f"[INFO] ジャーナル: {len(picked)}スレッド / {len(entries)}投稿を取得")
    return entries


# ===========================================================================
# 2) Google OAuth 認証(Calendar / Tasks / Sheets 共通)
# ===========================================================================
def build_google_credentials():
    """環境変数の OAuth 情報から Credentials を作る。無ければ None。"""
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    rt = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()
    if not (cid and csec and rt):
        return None
    from google.oauth2.credentials import Credentials

    return Credentials(
        None,
        refresh_token=rt,
        client_id=cid,
        client_secret=csec,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=GOOGLE_SCOPES,
    )


def build_service(name: str, version: str, creds):
    from googleapiclient.discovery import build

    return build(name, version, credentials=creds, cache_discovery=False)


def verify_google_credentials(creds) -> str:
    """リフレッシュトークンを実際に交換してみる。成功なら ""、失敗なら理由文字列。

    「OAuth 情報は設定済みだがトークンが失効/無効」を早期に 1 回で検知する。
    これをしないと Calendar/Tasks/Sheets が個別に沈黙し、データがあるのに
    「記録なし」を投稿する“ゾンビ実行”になる(同意画面がテスト公開だと 7 日で失効)。
    """
    if creds is None:
        return ""
    try:
        from google.auth.transport.requests import Request

        creds.refresh(Request())
        return ""
    except Exception as err:  # noqa: BLE001
        return f"{type(err).__name__}: {err}"


# ===========================================================================
# 3) Google Calendar: 過去7日の実績 + 翌週7日の予定
# ===========================================================================
def collect_calendar(creds, ctx: dict) -> dict:
    empty = {"past": [], "upcoming": []}
    if creds is None:
        print("[WARN] OAuth 未設定のため Calendar 取得をスキップ")
        return empty
    ids = [c.strip() for c in os.environ.get("GOOGLE_CALENDAR_IDS", "primary").split(",") if c.strip()]
    try:
        svc = build_service("calendar", "v3", creds)
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] Calendar クライアント構築に失敗: {err}")
        return empty

    def _list(cid, tmin, tmax):
        items, page = [], None
        while True:
            resp = svc.events().list(
                calendarId=cid, timeMin=_rfc3339(tmin), timeMax=_rfc3339(tmax),
                singleEvents=True, orderBy="startTime", maxResults=2500, pageToken=page,
            ).execute(num_retries=5)
            for ev in resp.get("items", []):
                if ev.get("status") == "cancelled":
                    continue
                s = ev.get("start", {})
                start = s.get("dateTime") or s.get("date") or ""
                items.append({
                    "calendar": cid,
                    "summary": ev.get("summary", "(無題)"),
                    "start": start,
                    "location": ev.get("location", ""),
                })
            page = resp.get("nextPageToken")
            if not page:
                break
        return items

    out = {"past": [], "upcoming": []}
    for cid in ids:
        try:
            out["past"].extend(_list(cid, ctx["past_start"], ctx["now"]))
            out["upcoming"].extend(_list(cid, ctx["now"], ctx["next_end"]))
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] カレンダー {cid} の取得に失敗、スキップ: {err}")
    out["past"].sort(key=lambda x: x["start"])
    out["upcoming"].sort(key=lambda x: x["start"])
    print(f"[INFO] Calendar: 実績 {len(out['past'])}件 / 予定 {len(out['upcoming'])}件")
    return out


# ===========================================================================
# 4) Google Tasks: 過去7日の完了 + 未完了一覧
# ===========================================================================
def collect_tasks(creds, ctx: dict) -> dict:
    empty = {"completed": [], "open": []}
    if creds is None:
        print("[WARN] OAuth 未設定のため Tasks 取得をスキップ")
        return empty
    try:
        svc = build_service("tasks", "v1", creds)
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] Tasks クライアント構築に失敗: {err}")
        return empty

    try:
        tasklists, page = [], None
        while True:
            resp = svc.tasklists().list(maxResults=100, pageToken=page).execute(num_retries=5)
            tasklists.extend(resp.get("items", []))
            page = resp.get("nextPageToken")
            if not page:
                break
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] タスクリスト一覧の取得に失敗: {err}")
        return empty

    def _list(list_id, **kw):
        items, page = [], None
        while True:
            resp = svc.tasks().list(
                tasklist=list_id, maxResults=100, showHidden=True, pageToken=page, **kw
            ).execute(num_retries=5)
            items.extend(resp.get("items", []))
            page = resp.get("nextPageToken")
            if not page:
                break
        return items

    out = {"completed": [], "open": []}
    for tl in tasklists:
        lid, lname = tl.get("id"), tl.get("title", "(無題リスト)")
        if not lid:
            continue
        try:
            done = _list(lid, showCompleted=True, completedMin=_rfc3339(ctx["past_start"]))
            todo = _list(lid, showCompleted=False)
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] リスト {lname} の取得に失敗、スキップ: {err}")
            continue
        for t in done:
            if t.get("status") == "completed":
                out["completed"].append({"list": lname, "title": t.get("title", ""), "notes": t.get("notes", "")})
        for t in todo:
            if t.get("status") != "completed" and t.get("title"):
                out["open"].append({"list": lname, "title": t.get("title", ""),
                                    "notes": t.get("notes", ""), "due": t.get("due", "")})
    out["completed"] = [t for t in out["completed"] if t["title"]]
    print(f"[INFO] Tasks: 完了 {len(out['completed'])}件 / 未完了 {len(out['open'])}件")
    return out


# ===========================================================================
# 5) 構造化(Gemini → 失敗時フォールバック)
# ===========================================================================
def build_bundle(journal, calendar, tasks) -> dict:
    return {"journal": journal, "calendar": calendar, "tasks": tasks}


def bundle_is_empty(b: dict) -> bool:
    return not (
        b["journal"]
        or b["calendar"]["past"] or b["calendar"]["upcoming"]
        or b["tasks"]["completed"] or b["tasks"]["open"]
    )


def _bundle_to_text(b: dict) -> str:
    parts = []
    if b["journal"]:
        lines = [f"- {e['date']} {e['time']} [{e['thread']}] {e['text']}" for e in b["journal"]]
        parts.append("## モーニングジャーナル(過去7日)\n" + "\n".join(lines))
    else:
        parts.append("## モーニングジャーナル(過去7日)\n(記録なし)")

    cp = b["calendar"]["past"]
    parts.append(
        "## カレンダー実績(過去7日)\n"
        + ("\n".join(f"- {e['start']} {e['summary']}"
                     + (f" @{e['location']}" if e['location'] else "") for e in cp)
           if cp else "(記録なし)")
    )
    cu = b["calendar"]["upcoming"]
    parts.append(
        "## カレンダー予定(翌週7日)\n"
        + ("\n".join(f"- {e['start']} {e['summary']}"
                     + (f" @{e['location']}" if e['location'] else "") for e in cu)
           if cu else "(記録なし)")
    )
    tc = b["tasks"]["completed"]
    parts.append(
        "## 完了タスク(過去7日)\n"
        + ("\n".join(f"- [{t['list']}] {t['title']}" for t in tc) if tc else "(記録なし)")
    )
    to = b["tasks"]["open"]
    parts.append(
        "## 未完了タスク\n"
        + ("\n".join(f"- [{t['list']}] {t['title']}"
                     + (f" (期限 {t['due'][:10]})" if t.get('due') else "") for t in to)
           if to else "(記録なし)")
    )
    return "\n\n".join(parts)


def _gemini_prompt(ctx: dict, bundle_text: str) -> str:
    cats = "\n".join(f"  {i+1}. {c['title']}" for i, c in enumerate(CATEGORIES))
    return f"""あなたは記録者本人の「心のベクトルを映す鏡」です。忖度・迎合・定型挨拶は一切不要。
以下は {ctx['label']} の1週間の記録(Discordモーニングジャーナル / Googleカレンダー /
Google Tasks の集約)です。ログに無いことは推測・捏造しないでください。

{bundle_text}

主素材は「モーニングジャーナル」の記述です。そこに表れた感情・関心・葛藤・体調の
手触りを中心に読み取り、カレンダー/タスクは「達成できて心が晴れた」「先送りして
気になっている」「体を動かしてリズムが整った」等、感情の裏付けとしてのみ使って
ください(単なるタスク一覧としては扱わない)。

このログだけを根拠に「心の棚卸しマインドマップ」の階層データと、来週の手帳に
書くべきアクション3行を **JSON のみ** で返してください。前後に説明文・コード
フェンス・コメントを付けないこと。

スキーマ:
{{
  "root": "{ctx['root_title']}",
  "categories": [
    {{
      "key": "<下記の固定キー>",
      "children": [
        {{ "title": "中分類ノード(体言止め・20字以内)",
           "children": [ {{ "title": "末端トピック(短文・30字以内)" }} ] }}
      ]
    }}
  ],
  "actions": [ "来週意識すべき心のフォーカス・具体的行動(30字前後)", "...", "..." ]
}}

固定の 4 象限(この4つ・この順序・key はこの通り):
  1. key="motivation"   … {CATEGORIES[0]['title']}: 写真・カルチャー・探求・好奇心が向いたこと。ワクワクした瞬間
  2. key="friction"     … {CATEGORIES[1]['title']}: 先送りした葛藤・集中を削ぐ執着・気掛かり・モヤモヤの正体
  3. key="biorhythm"    … {CATEGORIES[2]['title']}: 頭の重さ・眠気・運動や歩数の達成感・体調のリズムの波
  4. key="contentment"  … {CATEGORIES[3]['title']}: 習慣が定着した実感・ホッとした瞬間・整った・満たされた感覚

規則:
  - 4象限すべてを必ず含める。該当ログが無い象限は children を空配列 [] にする。
  - 各象限の中分類は最大 {MAX_MIDS_PER_CATEGORY} 個、各中分類の末端は最大 {MAX_ENDS_PER_MID} 個。
  - 同じ出来事でも「事実」ではなく必ず「そのとき心がどちらへ動いたか」でどの象限に
    入れるか判定する(例:練習に行った=biorhythm、行けたことに満足=contentment)。
  - 末端トピックはログの実内容を短く要約したもの。丸括弧・鉤括弧・コロンは使わない。
  - actions は必ず {MAX_ACTIONS} 行。friction(モヤモヤ)の解消に向けた一手を最低1つ、
    motivation(意欲)を伸ばす一手を最低1つ含め、抽象論ではなくログに基づく具体的な
    行動・心構えにする。各行は句点なしの体言止め or 短い命令形、{CLIP_ACTION}字以内。
参考(このキーだった):
{cats}
"""


def _normalize_actions(raw_actions) -> list[str]:
    """Gemini/フォールバックの actions を最大 MAX_ACTIONS 件・クリップ済みの文字列配列へ。"""
    out: list[str] = []
    for a in (raw_actions if isinstance(raw_actions, list) else []):
        text = a.get("title") if isinstance(a, dict) else a
        text = _clip(text, CLIP_ACTION)
        if text:
            out.append(text)
        if len(out) >= MAX_ACTIONS:
            break
    return out


def _normalize_tree(raw: dict, ctx: dict) -> dict:
    """Gemini / フォールバック出力を共通形へ整える。4象限・上限・クリップを強制。"""
    def _as_list(v):
        return v if isinstance(v, list) else ([v] if v else [])

    by_key = {}
    for c in _as_list(raw.get("categories")):
        if isinstance(c, dict) and c.get("key"):
            by_key[c["key"]] = _as_list(c.get("children"))

    norm_cats = []
    for meta in CATEGORIES:
        mids_in = by_key.get(meta["key"], [])
        mids_out = []
        for mid in mids_in[:MAX_MIDS_PER_CATEGORY]:
            if not isinstance(mid, dict):
                mid = {"title": mid, "children": []}
            ends_in = _as_list(mid.get("children"))
            ends_out = []
            for e in ends_in[:MAX_ENDS_PER_MID]:
                title = e.get("title") if isinstance(e, dict) else e
                title = _clip(title, CLIP_END)
                if title:
                    ends_out.append({"title": title})
            extra = len(ends_in) - MAX_ENDS_PER_MID
            if extra > 0:
                ends_out.append({"title": f"…他{extra}件"})
            mids_out.append({"title": _clip(mid.get("title", ""), CLIP_MID) or "(未分類)",
                             "children": ends_out})
        extra_m = len(mids_in) - MAX_MIDS_PER_CATEGORY
        if extra_m > 0:
            mids_out.append({"title": f"…他{extra_m}項目", "children": []})
        norm_cats.append({**meta, "children": mids_out})

    return {
        "root": raw.get("root") or ctx["root_title"],
        "categories": norm_cats,
        "actions": _normalize_actions(raw.get("actions")),
    }


# キーワードベースの感情象限マッピング(フォールバック専用の簡易分類)。
# 上から順に判定し、最初にマッチした象限へ入れる(モヤモヤ→体調→意欲→納得の優先度)。
_FRICTION_KW = ("モヤモヤ", "先送り", "気になる", "気掛かり", "不安", "焦り", "イライラ",
                "執着", "迷い", "ストレス", "滞り", "後回し", "判断を溜め")
_BIORHYTHM_KW = ("頭が重い", "頭が痛い", "眠い", "疲れ", "だるい", "歩数", "断食", "運動",
                 "体調", "リズム", "筋トレ", "睡眠", "体が重い", "眠れ")
_MOTIVATION_KW = ("ワクワク", "楽しみ", "撮影", "写真", "映画", "アニメ", "美術", "展",
                  "カルチャー", "読書", "音楽", "ライブ", "探求", "好奇心", "面白", "ドラマ")
_CONTENTMENT_KW = ("整った", "ホッと", "良かった", "満たされ", "習慣", "定着", "安心",
                   "落ち着", "達成感", "崩れていない", "続いている")

_JOURNAL_SPORT_KW = ("バドミントン", "ジム", "ランニング", "筋トレ", "ウォーキング", "水泳")


def _classify_journal_entry(text: str) -> str | None:
    """ジャーナル1件をキーワードで象限キーへ振り分ける。マッチ無しは None。"""
    for kw, key in (
        (_FRICTION_KW, "friction"),
        (_BIORHYTHM_KW, "biorhythm"),
        (_MOTIVATION_KW, "motivation"),
        (_CONTENTMENT_KW, "contentment"),
    ):
        if any(k in text for k in kw):
            return key
    return None


def _fallback_actions(cats_children: dict) -> list[str]:
    """4象限のフォールバックツリーから来週アクション3行を機械的に組み立てる。"""
    def _first_leaf(key) -> str | None:
        for mid in cats_children.get(key, []):
            for end in mid.get("children", []):
                return end["title"]
        return None

    actions: list[str] = []
    friction_top = _first_leaf("friction")
    if friction_top:
        actions.append(_clip(f"「{friction_top}」への向き合い方を1つ決めて手放す", CLIP_ACTION))
    motivation_top = _first_leaf("motivation")
    if motivation_top:
        actions.append(_clip(f"「{motivation_top}」の続きを来週も1回は確保する", CLIP_ACTION))
    biorhythm_top = _first_leaf("biorhythm")
    contentment_top = _first_leaf("contentment")
    if biorhythm_top:
        actions.append(_clip(f"「{biorhythm_top}」のリズムを来週も崩さず継続する", CLIP_ACTION))
    elif contentment_top:
        actions.append(_clip(f"「{contentment_top}」の心地よさを来週も再現する", CLIP_ACTION))

    generic = [
        "モヤモヤを1つ紙に書き出して輪郭をはっきりさせる",
        "ワクワクした活動の予定を来週のどこかに1枠入れる",
        "体調のリズムが崩れた日を振り返り原因を1つ特定する",
    ]
    for g in generic:
        if len(actions) >= MAX_ACTIONS:
            break
        if g not in actions:
            actions.append(g)
    return actions[:MAX_ACTIONS]


def deterministic_tree(bundle: dict, ctx: dict) -> dict:
    """Gemini を使わず、収集データから機械的に「心のベクトル」4象限ツリーを組む。"""
    def _mid(title, items, render):
        kids = [{"title": _clip(render(x), CLIP_END)} for x in items]
        return {"title": title, "children": kids}

    cats_children = {c["key"]: [] for c in CATEGORIES}

    # 1) ジャーナル本文をキーワードで4象限へ振り分け(象限ごとに1中分類にまとめる)
    by_quadrant: dict[str, list[str]] = {"friction": [], "biorhythm": [], "motivation": [], "contentment": []}
    for e in bundle["journal"]:
        key = _classify_journal_entry(e["text"])
        if key:
            by_quadrant[key].append(e["text"])
    quadrant_mid_title = {
        "friction": "気掛かり・モヤモヤ",
        "biorhythm": "体調・リズムの記録",
        "motivation": "心が動いた記録",
        "contentment": "納得・満たされた記録",
    }
    for key, texts in by_quadrant.items():
        if texts:
            cats_children[key].append(_mid(quadrant_mid_title[key], texts[:MAX_ENDS_PER_MID], lambda x: x))

    # 2) 体調・バイオリズム: 運動系カレンダー実績を補強
    cp = bundle["calendar"]["past"]
    sport_events = [e for e in cp if any(k in e["summary"] for k in _JOURNAL_SPORT_KW)]
    if sport_events:
        cats_children["biorhythm"].append(_mid("運動の実施記録", sport_events[:MAX_ENDS_PER_MID],
                                                lambda e: f"{e['start'][:10]} {e['summary']}"))

    # 3) 意欲・ワクワク: 趣味系の翌週予定を補強
    cu = bundle["calendar"]["upcoming"]
    hobby_events = [e for e in cu if any(k in e["summary"] for k in _MOTIVATION_KW)]
    if hobby_events:
        cats_children["motivation"].append(_mid("楽しみな予定", hobby_events[:MAX_ENDS_PER_MID],
                                                 lambda e: f"{e['start'][:10]} {e['summary']}"))

    # 4) 納得・心地よさ: 完了タスクを「やり切れた」実感として補強
    tc = bundle["tasks"]["completed"]
    if tc:
        cats_children["contentment"].append(_mid("やり切れたこと", tc[:MAX_ENDS_PER_MID],
                                                  lambda t: t["title"]))

    # 5) モヤモヤ・負荷: 未完了タスクをそのまま気がかりとして補強
    to = bundle["tasks"]["open"]
    if to:
        cats_children["friction"].append(_mid("残っている気がかり", to[:MAX_ENDS_PER_MID],
                                               lambda t: t["title"]
                                               + (f" (期限{t['due'][:10]})" if t.get("due") else "")))

    raw = {"root": ctx["root_title"],
           "categories": [{"key": c["key"], "children": cats_children[c["key"]]} for c in CATEGORIES],
           "actions": _fallback_actions(cats_children)}
    return _normalize_tree(raw, ctx)


def _loads_loose(text: str) -> dict:
    """Gemini 応答から JSON を取り出す。```json フェンスや前後の散文があっても拾う。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s.strip("`")
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j != -1 and j > i:
            return json.loads(s[i:j + 1])
        raise


def structure(bundle: dict, ctx: dict, api_key: str, model: str) -> tuple[dict, str]:
    """(tree, source) を返す。source は 'gemini' / 'fallback' / 'empty'。"""
    if bundle_is_empty(bundle):
        empty = {"root": ctx["root_title"],
                 "categories": [{"key": c["key"], "children": []} for c in CATEGORIES],
                 "actions": ["今週はまずモーニングジャーナルを1行でも書くことから始める"]}
        return _normalize_tree(empty, ctx), "empty"

    if api_key:
        try:
            from google import genai

            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=model,
                contents=_gemini_prompt(ctx, _bundle_to_text(bundle)),
                config={"response_mime_type": "application/json"},
            )
            raw = _loads_loose(resp.text or "")
            tree = _normalize_tree(raw, ctx)
            if any(c["children"] for c in tree["categories"]):
                if len(tree["actions"]) < MAX_ACTIONS:
                    fallback_cats = {c["key"]: c["children"] for c in tree["categories"]}
                    for a in _fallback_actions(fallback_cats):
                        if len(tree["actions"]) >= MAX_ACTIONS:
                            break
                        if a not in tree["actions"]:
                            tree["actions"].append(a)
                print(f"[INFO] 構造化: Gemini({model}) 成功")
                return tree, "gemini"
            print("[WARN] Gemini 応答が空ツリー。フォールバックへ")
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] Gemini 構造化に失敗、フォールバックへ: {err}")
    else:
        print("[INFO] GEMINI_API_KEY 未設定。決定論フォールバックで構造化")

    return deterministic_tree(bundle, ctx), "fallback"


# ===========================================================================
# 6) マインドマップ画像(PNG)描画 — matplotlib 放射状
# ===========================================================================
def _resolve_jp_font():
    """環境内の日本語フォントを探して matplotlib に登録し、family 名を返す。"""
    import glob

    from matplotlib import font_manager

    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
        "/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "C:\\Windows\\Fonts\\meiryo.ttc",
        "C:\\Windows\\Fonts\\YuGothM.ttc",
    ]
    # 新しめの fonts-noto-cjk は VF 版のみのことがあるので glob でも拾う
    for pat in ("/usr/share/fonts/**/NotoSansCJK*.*", "/usr/share/fonts/**/NotoSerifCJK*.*",
                "/usr/share/fonts/**/ipaex*.ttf", "/usr/share/fonts/**/*ipag*.ttf"):
        candidates.extend(sorted(glob.glob(pat, recursive=True)))

    for path in candidates:
        if os.path.exists(path):
            try:
                font_manager.fontManager.addfont(path)
                name = font_manager.FontProperties(fname=path).get_name()
                print(f"[INFO] 日本語フォント: {name} ({path})")
                return name
            except Exception:  # noqa: BLE001
                continue
    # 最後の手段: 既に登録済みの CJK っぽいフォントを探す
    for f in font_manager.fontManager.ttflist:
        if any(k in f.name for k in ("Noto Sans CJK", "Noto Serif CJK", "IPAex", "IPAGothic",
                                     "Hiragino", "Yu Gothic", "Meiryo", "TakaoGothic", "VL Gothic")):
            print(f"[INFO] 日本語フォント(登録済み): {f.name}")
            return f.name
    print("[WARN] 日本語フォントが見つからず。文字化けの可能性あり(CJK フォント未導入)")
    return None


def _wrap_n(text: str, width: int, max_lines: int) -> str:
    """折り返し + 行数上限。あふれたら最終行を「…」で締める。"""
    text = " ".join((text or "").split())
    if not text:
        return ""
    ws = textwrap.wrap(text, width=width, break_long_words=True, break_on_hyphens=False)
    if len(ws) > max_lines:
        ws = ws[:max_lines]
        ws[-1] = ws[-1][: max(width - 1, 1)].rstrip() + "…"
    return "\n".join(ws) or text


# 画像として描くときだけの上限(データ側の行はフル。画像の可読性を優先)
RENDER_MAX_MIDS = 4
RENDER_MAX_ENDS = 2


def render_mindmap(tree: dict, ctx: dict, source: str, out_path: str) -> str:
    import math

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, FancyBboxPatch

    fam = _resolve_jp_font()
    if fam:
        plt.rcParams["font.family"] = fam
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(15, 13.6))
    ax.set_aspect("equal")
    ax.axis("off")
    fig.subplots_adjust(left=0.01, right=0.99, top=0.90, bottom=0.01)

    # 放射マップの中心。4象限は右上/右下/左下/左上へ振り分け(真上・真下を避けて
    # タイトル・下段アクション帯とノードの衝突を防ぐ)。半径を詰め密度を上げる。
    CX0, CY0 = 0.2, 0.6
    R_CAT, R_MID, R_END = 4.2, 2.95, 1.5
    START_ANGLE_DEG = 45
    ACTION_BAND_H = 3.4

    HALO = dict(boxstyle="round,pad=0.08", facecolor="white", edgecolor="none", alpha=0.8)

    # 実際に描いたノード・ラベルの座標を集めておき、全描画後にその外接矩形+
    # 最小限のマージンで xlim/ylim を確定する(固定の理論値だと、ノードを扇状に
    # 広げた際のオフセットでラベルが枠外にはみ出すため)。
    bounds_x: list[float] = []
    bounds_y: list[float] = []

    def _mark(x: float, y: float, pad: float = 0.0) -> None:
        bounds_x.extend([x - pad, x + pad])
        bounds_y.extend([y - pad, y + pad])

    def circle(xy, r, fc, ec="white", lw=2.2, z=3):
        ax.add_patch(Circle(xy, r, facecolor=fc, edgecolor=ec, linewidth=lw, zorder=z))
        _mark(xy[0], xy[1], r)

    def line(a, b, color, lw=2.2, z=1):
        ax.plot([a[0], b[0]], [a[1], b[1]], color=color, linewidth=lw, zorder=z, alpha=0.5)

    def label(xy, text, size, color="white", weight="bold", z=5, ha="center", va="center",
              halo=False, pad=0.0):
        ax.text(xy[0], xy[1], text, fontsize=size, color=color, ha=ha, va=va, weight=weight,
                zorder=z, clip_on=False, bbox=HALO if halo else None)
        _mark(xy[0], xy[1], pad)

    n = len(CATEGORIES)
    for i, cat in enumerate(tree["categories"]):
        base = math.radians(START_ANGLE_DEG - i * (360 / n))   # このカテゴリのセクター中心角
        cx, cy = CX0 + R_CAT * math.cos(base), CY0 + R_CAT * math.sin(base)
        color = cat["color"]

        line((CX0, CY0), (cx, cy), color, lw=4.2)
        circle((cx, cy), 2.15, color, lw=3.4)
        label((cx, cy), cat["short"], 19, pad=0.6)

        mids = cat["children"][:RENDER_MAX_MIDS]
        if not mids:
            ox, oy = 2.8 * math.cos(base), 2.8 * math.sin(base)
            label((cx + ox, cy + oy), "記録なし", 16, color="#8a8a8a", weight="normal",
                  halo=True, pad=1.0)
            continue

        m_span = math.radians(min(80, 24 * len(mids)))
        m0 = base - m_span / 2
        m_step = m_span / max(len(mids) - 1, 1)
        for j, mid in enumerate(mids):
            ma = m0 + j * m_step if len(mids) > 1 else base
            rm = R_MID + (0.75 if j % 2 else 0.0)          # 1つおきに外へずらし中丸の重なりを防ぐ
            mx, my = cx + rm * math.cos(ma), cy + rm * math.sin(ma)
            line((cx, cy), (mx, my), color, lw=2.2)
            circle((mx, my), 0.44, color, ec="white", lw=1.6)
            # 中分類ラベルはマーカーの「内側」(カテゴリ寄り)に少しだけ置き、末端ラベルと分離。
            # オフセットは大分類の丸(半径2.15)と重ならない範囲に留める(rm=R_MID時が最小距離)。
            lmx, lmy = mx - 0.4 * math.cos(ma), my - 0.4 * math.sin(ma)
            label((lmx, lmy), _wrap_n(mid["title"], 10, 3), 14.5, color="#1f2937", halo=True,
                  pad=1.4)

            ends = mid["children"][:RENDER_MAX_ENDS]
            if not ends:
                continue
            e_span = math.radians(min(52, 30 * len(ends)))
            e0 = ma - e_span / 2
            e_step = e_span / max(len(ends) - 1, 1)
            for k, end in enumerate(ends):
                ea = e0 + k * e_step if len(ends) > 1 else ma
                rr = R_END + (0.65 if k % 2 else 0.0)
                ex, ey = mx + rr * math.cos(ea), my + rr * math.sin(ea)
                line((mx, my), (ex, ey), color, lw=1.3)
                circle((ex, ey), 0.16, "white", ec=color, lw=1.6, z=4)
                ha = "left" if ex >= mx else "right"
                ox = 0.2 if ha == "left" else -0.2
                lx = ex + ox + (2.2 if ha == "left" else -2.2)
                label((ex + ox, ey), _wrap_n(_clip(end["title"], 22), 10, 2), 13,
                      color="#242424", weight="normal", ha=ha, halo=True)
                _mark(lx, ey, 0.6)

    # 中央ルート
    circle((CX0, CY0), 2.7, "#111827", ec="white", lw=3.4, z=6)
    label((CX0, CY0), f"心の棚卸し\n{ctx['label']}", 17, color="white", z=7)

    # タイトル / メタ / 凡例(figure座標。ax の放射マップとは独立して上部に配置)
    ts = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    src_label = {"gemini": "Gemini構造化", "fallback": "簡易構造化(Gemini未使用)",
                 "empty": "記録なし"}[source]
    fig.suptitle(f"心の棚卸しマップ　{ctx['label']}", fontsize=30, weight="bold", y=0.985)
    fig.text(0.5, 0.945, f"生成 {ts} ／ {src_label}", fontsize=14, color="#666666", ha="center")

    legend_x0 = 0.5 - (len(CATEGORIES) * 0.24) / 2
    for idx, cat in enumerate(CATEGORIES):
        lx = legend_x0 + idx * 0.24
        fig.patches.append(plt.Circle((lx, 0.912), 0.008, facecolor=cat["color"],
                                      edgecolor="none", transform=fig.transFigure,
                                      clip_on=False))
        fig.text(lx + 0.016, 0.912, cat["title"], fontsize=13, color="#333333",
                 ha="left", va="center")

    # ここまでのノード・ラベルの実座標から外接矩形を求め、最小限の余白で
    # xlim/ylim を確定する(理論上の半径だけで見積もると、扇状オフセットで
    # ラベルが枠外にはみ出すため、実測ベースで詰める)。
    content_half_w = max(abs(min(bounds_x)), abs(max(bounds_x))) if bounds_x else R_CAT
    content_top = max(bounds_y) if bounds_y else R_CAT
    content_bottom = min(bounds_y) if bounds_y else -R_CAT
    LX = content_half_w + 0.35
    LY_TOP = content_top + 0.35

    # 下端の帯: 来週の手帳用 3大アクション(Gemini/フォールバックが生成した文言を描画)
    bx0, bw = -LX + 0.3, 2 * LX - 0.6
    bh = ACTION_BAND_H
    by0 = content_bottom - 0.35 - bh
    ax.add_patch(FancyBboxPatch(
        (bx0, by0), bw, bh, boxstyle="round,pad=0.15,rounding_size=0.3",
        facecolor="#FFFDF3", edgecolor="#D8C89A", linewidth=1.8, zorder=8,
    ))
    ax.text(bx0 + 0.4, by0 + bh - 0.4, "◆ 来週の手帳用 3大アクション",
            fontsize=19, weight="bold", color="#7A5C00", ha="left", va="top", zorder=9)
    actions = (tree.get("actions") or [])[:MAX_ACTIONS]
    row_h = (bh - 0.9) / MAX_ACTIONS
    for m, num in enumerate(("①", "②", "③")):
        yy = by0 + bh - 0.95 - m * row_h
        ax.text(bx0 + 0.35, yy, num, fontsize=16, color="#7A5C00", ha="left", va="center", zorder=9)
        text = actions[m] if m < len(actions) else ""
        if text:
            ax.text(bx0 + 0.85, yy, _wrap_n(text, 34, 1), fontsize=14.5, color="#3f2f00",
                    weight="bold", ha="left", va="center", zorder=9)
        else:
            ax.plot([bx0 + 0.85, bx0 + bw - 0.4], [yy - 0.02, yy - 0.02],
                    color="#C9B98A", linewidth=1.1, zorder=9)

    ax.set_xlim(-LX, LX)
    ax.set_ylim(by0 - 0.3, LY_TOP)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", pad_inches=0.15, facecolor="white")
    plt.close(fig)
    print(f"[OK] 画像を保存: {out_path}")
    return out_path


# ===========================================================================
# 6.5) GitHub Pages 常設ページ(mindmap/index.html + アーカイブ)
# ===========================================================================
def _html_escape(s) -> str:
    import html as _html
    return _html.escape(str(s if s is not None else ""))


def _mindmap_page_html(*, title: str, week_label: str, generated_ts: str, source_label: str,
                        sheet_link: str | None, png_rel: str, warnings: list[str] | None,
                        back_link: str | None = None, archive_index_link: str | None = None) -> str:
    warn_html = ""
    if warnings:
        items = "".join(f"<li>{_html_escape(w)}</li>" for w in warnings)
        warn_html = f'<div class="warn"><strong>⚠️ 警告</strong><ul>{items}</ul></div>'
    sheet_html = (f'<a class="btn" href="{_html_escape(sheet_link)}" target="_blank" '
                  f'rel="noopener">📄 スプレッドシートを開く</a>') if sheet_link else ""
    nav_parts = []
    if back_link:
        nav_parts.append(f'<a href="{_html_escape(back_link)}">← 最新の週へ</a>')
    if archive_index_link:
        nav_parts.append(f'<a href="{_html_escape(archive_index_link)}">📚 過去の週一覧</a>')
    nav_html = "".join(nav_parts)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html_escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; padding:24px 16px 48px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Hiragino Sans",sans-serif; background:#f8fafc; color:#1e293b; }}
  @media (prefers-color-scheme: dark) {{ body {{ background:#0f172a; color:#e2e8f0; }} }}
  .wrap {{ max-width: 980px; margin: 0 auto; }}
  h1 {{ font-size:1.4rem; margin:0 0 4px; }}
  .meta {{ font-size:0.85rem; color:#64748b; margin-bottom:18px; }}
  .nav {{ display:flex; gap:14px; margin-bottom:18px; flex-wrap:wrap; }}
  .nav a {{ font-size:0.85rem; color:#2563eb; text-decoration:none; font-weight:600; }}
  .nav a:hover {{ text-decoration:underline; }}
  img {{ max-width:100%; height:auto; border-radius:10px; border:1px solid #e2e8f0; background:#fff; display:block; }}
  .actions {{ margin-top:16px; }}
  .btn {{ display:inline-block; padding:9px 16px; border-radius:8px; background:#2563eb; color:#fff; text-decoration:none; font-size:0.85rem; font-weight:700; }}
  .warn {{ background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:10px 14px; margin-bottom:16px; font-size:0.85rem; color:#1e293b; }}
  .warn ul {{ margin:6px 0 0; padding-left:18px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>🧠 心の棚卸しマップ　{_html_escape(week_label)}</h1>
  <div class="meta">生成: {_html_escape(generated_ts)} ／ {_html_escape(source_label)}</div>
  <div class="nav">{nav_html}</div>
  {warn_html}
  <img src="{_html_escape(png_rel)}" alt="心の棚卸しマインドマップ {_html_escape(week_label)}">
  <div class="actions">{sheet_html}</div>
</div>
</body>
</html>
"""


def _write_archive_index() -> None:
    import glob

    files = sorted(
        (os.path.basename(p) for p in glob.glob(os.path.join(MINDMAP_ARCHIVE_DIR, "*.html"))
         if os.path.basename(p) != "index.html"),
        reverse=True,
    )
    items = "".join(
        f'<li><a href="{_html_escape(f)}">{_html_escape(f[:-5])}</a></li>' for f in files
    ) or "<li>まだアーカイブがありません</li>"
    html_doc = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>心の棚卸しマップ - 過去の週一覧</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; padding:24px 16px 48px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Hiragino Sans",sans-serif; background:#f8fafc; color:#1e293b; }}
  @media (prefers-color-scheme: dark) {{ body {{ background:#0f172a; color:#e2e8f0; }} }}
  .wrap {{ max-width:640px; margin:0 auto; }}
  h1 {{ font-size:1.3rem; }}
  a.back {{ font-size:0.85rem; color:#2563eb; text-decoration:none; font-weight:600; }}
  ul {{ list-style:none; padding:0; margin-top:16px; }}
  li {{ margin-bottom:8px; }}
  li a {{ display:block; padding:10px 14px; background:#fff; border:1px solid #e2e8f0; border-radius:8px;
         text-decoration:none; color:#1e293b; font-weight:600; }}
  @media (prefers-color-scheme: dark) {{ li a {{ background:#1e293b; border-color:#334155; color:#e2e8f0; }} }}
  li a:hover {{ border-color:#2563eb; }}
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="../index.html">← 最新の週へ</a>
  <h1>📚 心の棚卸しマップ - 過去の週一覧</h1>
  <ul>{items}</ul>
</div>
</body>
</html>
"""
    with open(os.path.join(MINDMAP_ARCHIVE_DIR, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html_doc)


def publish_mindmap_pages(png_path: str | None, ctx: dict, source: str,
                          sheet_link: str | None, warnings: list[str] | None) -> str | None:
    """mindmap/index.html(固定URL・最新版に上書き)と mindmap/archive/{week_tag}.html
    (永久保存)を書き出し、mindmap/archive/index.html(過去週一覧)も更新する。
    GitHub Pages で https://c6cgv9cnj4-ops.github.io/wbc/mindmap/ が常に最新を指すようにする
    (Actions アーティファクトへの一時出力だけだと数十日で消え 404 になるための恒久対応)。"""
    if not png_path or not os.path.exists(png_path):
        print("[WARN] PNG が無いため mindmap ページの生成をスキップ")
        return None
    import shutil

    os.makedirs(MINDMAP_ARCHIVE_DIR, exist_ok=True)

    shutil.copyfile(png_path, os.path.join(MINDMAP_DIR, "latest.png"))
    shutil.copyfile(png_path, os.path.join(MINDMAP_ARCHIVE_DIR, f"{ctx['week_tag']}.png"))

    generated_ts = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    src_label = {"gemini": "Gemini構造化", "fallback": "簡易構造化(Gemini未使用)",
                 "empty": "記録なし"}[source]

    index_html = _mindmap_page_html(
        title=f"心の棚卸しマップ {ctx['label']}", week_label=ctx["label"],
        generated_ts=generated_ts, source_label=src_label, sheet_link=sheet_link,
        png_rel="latest.png", warnings=warnings, archive_index_link="archive/index.html",
    )
    with open(os.path.join(MINDMAP_DIR, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(index_html)

    archive_html = _mindmap_page_html(
        title=f"心の棚卸しマップ {ctx['label']}（アーカイブ）", week_label=ctx["label"],
        generated_ts=generated_ts, source_label=src_label, sheet_link=sheet_link,
        png_rel=f"{ctx['week_tag']}.png", warnings=warnings,
        back_link="../index.html", archive_index_link="index.html",
    )
    with open(os.path.join(MINDMAP_ARCHIVE_DIR, f"{ctx['week_tag']}.html"), "w", encoding="utf-8") as fh:
        fh.write(archive_html)

    _write_archive_index()
    print(f"[OK] mindmap ページを更新: {MINDMAP_PAGES_URL} "
          f"(アーカイブ: mindmap/archive/{ctx['week_tag']}.html)")
    return MINDMAP_PAGES_URL


# ===========================================================================
# 7) スプレッドシート書き込み
# ===========================================================================
def tree_to_rows(tree: dict) -> list[list]:
    """A:大分類 / B:中分類 / C:トピック / D:採用 の行に平坦化。

    D は文字列 "FALSE"。Sheets の USER_ENTERED + BOOLEAN データ検証で
    未チェックのチェックボックスとして表示される。
    """
    rows: list[list] = []
    for cat in tree["categories"]:
        if not cat["children"]:
            rows.append([cat["title"], "(記録なし)", "", "FALSE"])
            continue
        for mid in cat["children"]:
            if not mid["children"]:
                rows.append([cat["title"], mid["title"], "", "FALSE"])
                continue
            for end in mid["children"]:
                rows.append([cat["title"], mid["title"], end["title"], "FALSE"])
    return rows


def create_spreadsheet(creds) -> str:
    """空の週次スプレッドシートを 1 つ作成し ID を返す(初回セットアップ専用)。"""
    svc = build_service("sheets", "v4", creds)
    created = svc.spreadsheets().create(
        body={"properties": {"title": "心の棚卸しマップ"}}, fields="spreadsheetId",
    ).execute(num_retries=5)
    return created["spreadsheetId"]


def write_week_sheet(creds, ctx: dict, rows: list[list]) -> tuple[str, str] | tuple[None, None]:
    """週次タブを作り直して行を書き込み、(spreadsheet_id, deep_link) を返す。

    WEEKLY_SPREADSHEET_ID 未設定なら (None, None) を返して呼び出し側でスキップさせる
    (毎回スプレッドシートを新規作成してしまう事故を防ぐ)。
    """
    ssid = os.environ.get("WEEKLY_SPREADSHEET_ID", "").strip()
    if not ssid:
        print("[ERROR] WEEKLY_SPREADSHEET_ID が未設定です。Sheets 追記をスキップします。"
              " 初回は `python scripts/weekly_mindmap.py --create-spreadsheet` で作成し、"
              "出力された ID を Secret / .env に登録してください。")
        return None, None

    svc = build_service("sheets", "v4", creds)
    tab = ctx["week_tag"]
    meta = svc.spreadsheets().get(
        spreadsheetId=ssid, fields="sheets(properties(sheetId,title))"
    ).execute(num_retries=5)
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

    reqs = []
    if tab in existing:
        reqs.append({"updateCells": {
            "range": {"sheetId": existing[tab]},
            "fields": "userEnteredValue,dataValidation,userEnteredFormat",
        }})
    else:
        reqs.append({"addSheet": {"properties": {"title": tab, "gridProperties": {"columnCount": 4}}}})
    resp = svc.spreadsheets().batchUpdate(
        spreadsheetId=ssid, body={"requests": reqs}
    ).execute(num_retries=5)

    if tab in existing:
        gid = existing[tab]
    else:
        gid = resp["replies"][-1]["addSheet"]["properties"]["sheetId"]

    header = ["大分類", "中分類", "具体的な内容・トピック", "採用"]
    values = [header] + (rows or [["", "(今週は入力がありませんでした)", "", "FALSE"]])
    svc.spreadsheets().values().update(
        spreadsheetId=ssid, range=f"'{tab}'!A1",
        valueInputOption="USER_ENTERED", body={"values": values},
    ).execute(num_retries=5)

    nrows = len(values)
    fmt_reqs = [
        {"repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.96},
            }},
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }},
        {"updateSheetProperties": {
            "properties": {"sheetId": gid, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }},
        {"repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": 1, "endRowIndex": nrows,
                      "startColumnIndex": 3, "endColumnIndex": 4},
            "cell": {"dataValidation": {"condition": {"type": "BOOLEAN"}}},
            "fields": "dataValidation",
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 190}, "fields": "pixelSize",
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 200}, "fields": "pixelSize",
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
            "properties": {"pixelSize": 420}, "fields": "pixelSize",
        }},
    ]
    svc.spreadsheets().batchUpdate(
        spreadsheetId=ssid, body={"requests": fmt_reqs}
    ).execute(num_retries=5)

    link = f"https://docs.google.com/spreadsheets/d/{ssid}/edit#gid={gid}"
    print(f"[OK] スプレッドシート更新: タブ {tab} / {nrows - 1}行 / {link}")
    return ssid, link


# ===========================================================================
# 8) Discord 投稿
# ===========================================================================
def post_to_discord(message: str, image_path: str | None) -> tuple[bool, str | None]:
    """(成功可否, 投稿したメッセージID) を返す。メッセージIDはピン留め用
    (Webhook 投稿時は ?wait=true を付けて本文を取り戻し、そこから抽出する)。"""
    webhook = os.environ.get("DISCORD_WEBHOOK_WEEKLY", "").strip()
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID_WEEKLY_SUMMARY", "").strip()

    payload = {"content": message[:1900]}
    has_image = bool(image_path and os.path.exists(image_path))
    fh = None
    try:
        if webhook:
            url = webhook + ("&wait=true" if "?" in webhook else "?wait=true")
            hdrs, pl = {"User-Agent": DISCORD_USER_AGENT}, {**payload, "username": "週次棚卸しBot"}
        elif bot_token and channel_id:
            url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
            hdrs, pl = {"Authorization": f"Bot {bot_token}", "User-Agent": DISCORD_USER_AGENT}, payload
        else:
            print("[WARN] DISCORD_WEBHOOK_WEEKLY も Bot+チャンネルID も無いため投稿をスキップ")
            return False, None

        if has_image:
            # 画像あり: multipart（payload_json + files[0]）
            fh = open(image_path, "rb")
            resp = requests.post(
                url, headers=hdrs, timeout=60,
                data={"payload_json": json.dumps(pl)},
                files={"files[0]": (os.path.basename(image_path), fh, "image/png")},
            )
        else:
            # 画像なし: 生 JSON ボディ（form-urlencoded の payload_json は Discord が読まない）
            resp = requests.post(url, headers=hdrs, json=pl, timeout=60)

        ok = resp.status_code < 300
        kind = "Webhook" if webhook else "Bot"
        print(f"[{'OK' if ok else 'WARN'}] Discord({kind}) HTTP {resp.status_code}"
              + ("" if ok else f" {resp.text[:200]}"))
        message_id = None
        if ok:
            try:
                message_id = resp.json().get("id")
            except Exception:  # noqa: BLE001
                message_id = None
        return ok, message_id
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] Discord 投稿に失敗: {err}")
        return False, None
    finally:
        if fh:
            fh.close()


def _discord_bot_request(method: str, path: str, bot_token: str, json_body: dict | None = None):
    url = f"{DISCORD_API_BASE}{path}"
    headers = {"Authorization": f"Bot {bot_token}", "User-Agent": DISCORD_USER_AGENT}
    return requests.request(method, url, headers=headers, json=json_body, timeout=REQUEST_TIMEOUT)


def update_weekly_channel_topic(bot_token: str, channel_id: str, page_url: str) -> None:
    """チャンネルトピックの先頭を「🧠 最新マインドマップ: <URL>」に更新する(ベストエフォート、
    Bot に MANAGE_CHANNELS 権限が無い等の失敗は警告に留めて継続する)。"""
    if not bot_token or not channel_id or not page_url:
        return
    try:
        resp = _discord_bot_request("GET", f"/channels/{channel_id}", bot_token)
        resp.raise_for_status()
        current = resp.json().get("topic") or ""
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] チャンネル情報の取得に失敗、トピック更新をスキップ: {err}")
        return

    marker_line = f"🧠 最新マインドマップ: {page_url}"
    kept = [ln for ln in current.split("\n") if ln.strip() and not ln.strip().startswith("🧠 最新マインドマップ:")]
    new_topic = "\n".join(kept + [marker_line])[:1024]  # Discord のチャンネルトピック上限
    if new_topic == current:
        print("[INFO] チャンネルトピックは既に最新")
        return
    try:
        resp = _discord_bot_request("PATCH", f"/channels/{channel_id}", bot_token,
                                    json_body={"topic": new_topic})
        resp.raise_for_status()
        print("[OK] チャンネルトピックを更新")
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] チャンネルトピックの更新に失敗(権限不足の可能性): {err}")


def pin_latest_and_unpin_old(bot_token: str, channel_id: str, message_id: str | None) -> None:
    """今回の週次サマリーをピン留めし、以前ピン留めしていた週次サマリーは解除する
    (このBotが投稿した「🧠 心の棚卸しマップ」始まりのメッセージだけを対象にし、
    ユーザーが手動で別途ピンしたメッセージには触れない。ベストエフォート)。"""
    if not bot_token or not channel_id:
        return
    try:
        resp = _discord_bot_request("GET", f"/channels/{channel_id}/pins", bot_token)
        resp.raise_for_status()
        pins = resp.json()
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] ピン留め一覧の取得に失敗、ピン更新をスキップ: {err}")
        return

    for p in pins:
        if (p.get("content") or "").startswith("🧠 心の棚卸しマップ") and p.get("id") != message_id:
            try:
                r = _discord_bot_request("DELETE", f"/channels/{channel_id}/pins/{p['id']}", bot_token)
                r.raise_for_status()
                print(f"[OK] 旧ピンを解除: {p['id']}")
            except Exception as err:  # noqa: BLE001
                print(f"[WARN] 旧ピンの解除に失敗: {err}")

    if not message_id:
        return
    try:
        r = _discord_bot_request("PUT", f"/channels/{channel_id}/pins/{message_id}", bot_token)
        r.raise_for_status()
        print("[OK] 最新の週次サマリーをピン留め")
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] ピン留めに失敗(権限不足の可能性): {err}")


def compose_message(ctx: dict, sheet_link: str | None, source: str, empty: bool,
                    warnings: list[str] | None = None, page_url: str | None = None) -> str:
    head = f"🧠 心の棚卸しマップ  {ctx['label']}"
    warn_block = ("\n".join(f"⚠️ {w}" for w in warnings) + "\n\n") if warnings else ""
    if empty:
        body = (warn_block + "今週は取得できた記録がありませんでした"
                + ("（上の警告が原因の可能性があります）。" if warnings
                   else "。来週は小さくてもログを残していきましょう。")
                + (f"\n\nスプレッドシート: {sheet_link}" if sheet_link else "")
                + (f"\n🔗 常設ページ: {page_url}" if page_url else ""))
        return head + "\n\n" + body
    note = "（簡易構造化：Gemini 未使用）" if source == "fallback" else ""
    lines = [head + (f"  {note}" if note else ""), ""]
    if warn_block:
        lines.append(warn_block.rstrip())
    if page_url:
        lines.append(f"🔗 常設ページ: {page_url}")
    if sheet_link:
        lines.append(f"📄 スプレッドシート: {sheet_link}")
    lines.append("")
    lines.append("今週の脳内棚卸しが完了しました。画像またはスプレッドシートから、"
                 "来週手帳に書く 3 つのアクションを選んでください。")
    return "\n".join(lines)


# ===========================================================================
# モック(--use-mock)
# ===========================================================================
def mock_bundle() -> dict:
    return {
        "journal": [
            {"date": "2026-09-08", "time": "07:12", "thread": "2026/09/08",
             "text": "朝から頭が重い。先週やり残したタスクが気になって集中できない感覚。"},
            {"date": "2026-09-08", "time": "22:40", "thread": "2026/09/08",
             "text": "『DUNE 砂の惑星PART2』を見た。映像の圧が凄い。撮影の光の作り方をメモ。"},
            {"date": "2026-09-10", "time": "06:55", "thread": "2026/09/10",
             "text": "1万歩達成できた日が続いている。16時間断食も崩れていない。"},
            {"date": "2026-09-11", "time": "23:30", "thread": "2026/09/11",
             "text": "また『あとで考える』で先送りした。判断を溜める癖が今週も出た。"},
        ],
        "calendar": {
            "past": [
                {"calendar": "primary", "start": "2026-09-09T10:00:00+09:00",
                 "summary": "税理士 打ち合わせ", "location": "オンライン"},
                {"calendar": "primary", "start": "2026-09-10T19:00:00+09:00",
                 "summary": "バドミントン 練習", "location": "北本市体育センター"},
            ],
            "upcoming": [
                {"calendar": "primary", "start": "2026-09-15T14:00:00+09:00",
                 "summary": "写真展 搬入", "location": "さいたま"},
                {"calendar": "primary", "start": "2026-09-17T10:00:00+09:00",
                 "summary": "歯科 定期健診", "location": ""},
            ],
        },
        "tasks": {
            "completed": [
                {"list": "仕事", "title": "請求書を送付", "notes": ""},
                {"list": "生活", "title": "車のオイル交換予約", "notes": ""},
            ],
            "open": [
                {"list": "仕事", "title": "決算書ドラフトを作成", "notes": "", "due": "2026-09-16T00:00:00.000Z"},
                {"list": "個人", "title": "写真の現像バックログを消化", "notes": "", "due": ""},
                {"list": "生活", "title": "ふるさと納税の枠を確認", "notes": "", "due": ""},
            ],
        },
    }


# ===========================================================================
# main
# ===========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="週次棚卸しマインドマップ生成")
    parser.add_argument("--week", default="", help="対象週の基準日 YYYY-MM-DD(空なら今日)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Sheets 書き込み / Discord 投稿を行わない(PNG は生成)")
    parser.add_argument("--use-mock", action="store_true",
                        help="通信せず合成データで PNG と行プレビュー CSV を生成")
    parser.add_argument("--create-spreadsheet", action="store_true",
                        help="初回セットアップ用: 空の週次スプレッドシートを1つ作成しIDを表示して終了")
    args = parser.parse_args()

    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")

    if args.create_spreadsheet:
        creds = build_google_credentials()
        if creds is None:
            print("[ERROR] GOOGLE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN を設定してから実行してください。")
            return 1
        err = verify_google_credentials(creds)
        if err:
            print(f"[ERROR] OAuth トークンが無効です（再発行が必要）: {err}")
            return 1
        ssid = create_spreadsheet(creds)
        print("=" * 60)
        print(f"WEEKLY_SPREADSHEET_ID={ssid}")
        print(f"https://docs.google.com/spreadsheets/d/{ssid}/edit")
        print("=" * 60)
        print("↑ この ID を GitHub Secret / .env の WEEKLY_SPREADSHEET_ID に登録してください。")
        return 0

    try:
        anchor = (datetime.date.fromisoformat(args.week) if args.week
                  else datetime.datetime.now(JST).date())
    except ValueError:
        print(f"[WARN] --week の日付形式が不正（{args.week}）。今日を基準にします。")
        anchor = datetime.datetime.now(JST).date()
    ctx = week_context(anchor)
    print(f"=== 週次棚卸し {ctx['week_tag']} ({ctx['label']}) / dry_run={dry_run} mock={args.use_mock} ===")

    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    # --- 1. 収集 ---------------------------------------------------------
    warnings: list[str] = []
    if args.use_mock:
        bundle = mock_bundle()
        creds = None
    else:
        ch = (os.environ.get("DISCORD_CHANNEL_ID_MORNING_JOURNAL", "").strip()
              or os.environ.get("DISCORD_CHANNEL_ID_HEALTH", "").strip())
        journal = collect_journal(os.environ.get("DISCORD_BOT_TOKEN", "").strip(), ch, ctx)
        creds = build_google_credentials()
        if creds is None and not dry_run:
            print("[ERROR] Google OAuth 認証情報(GOOGLE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN)が未設定です。"
                  " --dry-run か --use-mock で実行するか、Secrets を設定してください。")
            return 1
        # OAuth 情報はあるがトークンが失効/無効なケースを 1 回で検知して可視化する
        auth_err = verify_google_credentials(creds)
        if auth_err:
            print(f"[ERROR] Google OAuth トークンの更新に失敗しました（再発行が必要）: {auth_err}")
            warnings.append("Google 認証エラー：カレンダー/ToDo/シートを取得できませんでした。"
                            "リフレッシュトークンの再発行が必要です（同意画面は本番公開に）。")
            creds = None  # 以降の Calendar/Tasks/Sheets は綺麗にスキップさせる
        calendar = collect_calendar(creds, ctx)
        tasks = collect_tasks(creds, ctx)
        bundle = build_bundle(journal, calendar, tasks)

    empty = bundle_is_empty(bundle)
    if empty:
        print("[INFO] 収集データが全ソース空。雛形マップを生成します。")

    # --- 2. 構造化 -----------------------------------------------------
    tree, source = structure(bundle, ctx, api_key, model)

    # --- 3. 画像 ------------------------------------------------------
    png_path = os.path.join(REPORT_WEEKLY_DIR, f"{ctx['week_tag']}_mindmap.png")
    try:
        render_mindmap(tree, ctx, source, png_path)
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] 画像描画に失敗しました(投稿はスキップ): {err}")
        png_path = None

    rows = tree_to_rows(tree)

    # 行プレビュー CSV(常に出す。ローカル確認・Actions アーティファクト用)
    csv_path = os.path.join(REPORT_WEEKLY_DIR, f"{ctx['week_tag']}_rows.csv")
    os.makedirs(REPORT_WEEKLY_DIR, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["大分類", "中分類", "具体的な内容・トピック", "採用"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], "FALSE"])
    print(f"[OK] 行プレビュー CSV: {csv_path} ({len(rows)}行)")

    if args.use_mock:
        publish_mindmap_pages(png_path, ctx, source, None, warnings)
        print("=== モック実行のため Sheets / Discord は行いません。完了。 ===")
        return 0

    # --- 4. スプレッドシート ----------------------------------------
    sheet_link = None
    if dry_run:
        print("[INFO] dry-run: スプレッドシート書き込みをスキップ")
    elif creds is None:
        print("[INFO] OAuth 認証情報が無い/無効のためスプレッドシート書き込みをスキップ")
    else:
        try:
            _, sheet_link = write_week_sheet(creds, ctx, rows)
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] スプレッドシート書き込みに失敗: {err}")
            warnings.append("スプレッドシートの更新に失敗しました。")

    # --- 4.5. GitHub Pages 常設ページ(常に書き出す。PNG/CSVと同様 dry-run でも生成) ---
    page_url = publish_mindmap_pages(png_path, ctx, source, sheet_link, warnings)

    # --- 5. Discord -------------------------------------------------
    message = compose_message(ctx, sheet_link, source, empty, warnings, page_url)
    if dry_run:
        print("[INFO] dry-run: Discord 投稿をスキップ。本文プレビュー:\n" + message)
    else:
        ok, message_id = post_to_discord(message, png_path)
        bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        channel_id = os.environ.get("DISCORD_CHANNEL_ID_WEEKLY_SUMMARY", "").strip()
        if ok and page_url and bot_token and channel_id:
            update_weekly_channel_topic(bot_token, channel_id, page_url)
            pin_latest_and_unpin_old(bot_token, channel_id, message_id)

    print("=== 完了 ===" + ("（警告あり）" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
