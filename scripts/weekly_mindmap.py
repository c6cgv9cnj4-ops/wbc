# -*- coding: utf-8 -*-
"""
週次「脳内棚卸し」マインドマップ生成パイプライン

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

  2. Gemini による構造化(gemini-3.6-flash / GEMINI_MODEL で上書き可)
     収集データを渡し、固定 5 大分類のツリー JSON を抽出させる。
       1 モーニングジャーナル (内省)   [紫]
       2 インプット (趣味・感性)       [緑]
       3 実績 (完了タスク)             [青]
       4 予定 (Google Calendar)        [水色]
       5 未完了ToDo (Google Tasks)     [黄]
     各大分類 → 中分類ノード → 末端トピック の 3 階層。Gemini 失敗時は収集データから
     決定論的にツリーを組み立てるフォールバックへ自動で切り替える。

  3. マインドマップ画像(PNG)描画
     matplotlib のみ(graphviz 非依存)。中央ルートから 5 色の大丸を放射状に配置し、
     その先へ中丸・小丸を扇状に広げる。日本語フォントは環境内の Noto Sans CJK /
     IPAexGothic / ヒラギノ等を自動検出。左下に「✍️ 来週の手帳用3大アクション」の
     空欄枠を描画。テキストは折り返し + 文字数上限で枠外へのはみ出しを防ぐ。

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

DISCORD_USER_AGENT = "wbc-weekly-mindmap/1.0 (+https://github.com/c6cgv9cnj4-ops/wbc)"

# OAuth スコープ(すべて読み取り + Sheets 書き込み)
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# 5 大分類(順序・色・キーは固定。Gemini にもこの順で返させる)
# title … 凡例・スプレッドシート・プロンプト用のフル名
# short … マインドマップの大丸に収める短縮名
CATEGORIES = [
    {"key": "morning_journal", "title": "モーニングジャーナル (内省)", "short": "モーニング\nジャーナル", "color": "#7C3AED"},  # 紫
    {"key": "input",           "title": "インプット (趣味・感性)",     "short": "インプット",         "color": "#059669"},  # 緑
    {"key": "achievement",     "title": "実績 (完了タスク)",           "short": "実績",               "color": "#2563EB"},  # 青
    {"key": "schedule",        "title": "予定 (Google Calendar)",      "short": "予定",               "color": "#0EA5E9"},  # 水色
    {"key": "todo_open",       "title": "未完了ToDo (Google Tasks)",   "short": "未完了\nToDo",       "color": "#D4A017"},  # 黄
]

# 構造化ツリーの上限(超過分は「…他N件」に畳む)。スプレッドシート/CSV は
# この文字数まで保持し、画像描画側で必要に応じてさらに短く切り詰める。
MAX_MIDS_PER_CATEGORY = 6
MAX_ENDS_PER_MID = 4
CLIP_MID = 30
CLIP_END = 60


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
        "root_title": f"週間棚卸しマップ ({monday.strftime('%Y/%m/%d')}週)",
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
    return f"""あなたは記録者本人の「客観視の鏡」です。忖度・迎合・定型挨拶は一切不要。
以下は {ctx['label']} の1週間の記録(Discordモーニングジャーナル / Googleカレンダー /
Google Tasks の集約)です。ログに無いことは推測・捏造しないでください。

{bundle_text}

このログだけを根拠に「週間棚卸しマインドマップ」の階層データを **JSON のみ** で返して
ください。前後に説明文・コードフェンス・コメントを付けないこと。

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
  ]
}}

固定の大分類(この5つ・この順序・key はこの通り):
  1. key="morning_journal"  … {CATEGORIES[0]['title']}: 日々の気付き・感情の言語化・思考のモヤモヤ
  2. key="input"            … {CATEGORIES[1]['title']}: 見た映画/アニメの感想・美術館訪問・撮影写真への気付き
  3. key="achievement"      … {CATEGORIES[2]['title']}: 完了ToDo・主要成果・改善できた点
  4. key="schedule"         … {CATEGORIES[3]['title']}: 確定アポイント・週の予定・イベント
  5. key="todo_open"        … {CATEGORIES[4]['title']}: 残タスク・再スケジュール理由の分析

規則:
  - 5大分類すべてを必ず含める。該当ログが無い分類は children を空配列 [] にする。
  - 各大分類の中分類は最大 {MAX_MIDS_PER_CATEGORY} 個、各中分類の末端は最大 {MAX_ENDS_PER_MID} 個。
  - 「事実」と「感情」を同じノードに混在させない。感情・内省は morning_journal 側へ。
  - 末端トピックはログの実内容を短く要約したもの。丸括弧・鉤括弧・コロンは使わない。
参考(このキーだった):
{cats}
"""


def _normalize_tree(raw: dict, ctx: dict) -> dict:
    """Gemini / フォールバック出力を共通形へ整える。5分類・上限・クリップを強制。"""
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

    return {"root": raw.get("root") or ctx["root_title"], "categories": norm_cats}


def deterministic_tree(bundle: dict, ctx: dict) -> dict:
    """Gemini を使わず、収集データから機械的に 3 階層ツリーを組む。"""
    def _mid(title, items, render):
        kids = [{"title": _clip(render(x), CLIP_END)} for x in items]
        return {"title": title, "children": kids}

    cats_children = {c["key"]: [] for c in CATEGORIES}

    # 1) モーニングジャーナル: 日付ごとに 1 中分類、投稿を末端に
    by_date: dict[str, list[str]] = {}
    for e in bundle["journal"]:
        by_date.setdefault(e["date"], []).append(e["text"])
    for d, texts in sorted(by_date.items()):
        cats_children["morning_journal"].append(
            {"title": d, "children": [{"title": _clip(t, CLIP_END)} for t in texts[:MAX_ENDS_PER_MID]]}
        )

    # 2) インプット: ジャーナル本文からキーワードで拾う(映画・アニメ・美術館・写真・本)
    KW = ("映画", "アニメ", "美術", "写真", "撮影", "本", "読書", "展", "ライブ", "音楽", "ドラマ")
    hits = [e["text"] for e in bundle["journal"] if any(k in e["text"] for k in KW)]
    if hits:
        cats_children["input"].append(_mid("感性の記録", hits[:MAX_ENDS_PER_MID], lambda x: x))

    # 3) 実績: 完了タスク + 過去カレンダー
    tc = bundle["tasks"]["completed"]
    if tc:
        cats_children["achievement"].append(_mid("完了ToDo", tc[:MAX_ENDS_PER_MID],
                                                 lambda t: f"{t['title']}"))
    cp = bundle["calendar"]["past"]
    if cp:
        cats_children["achievement"].append(_mid("実施した予定", cp[:MAX_ENDS_PER_MID],
                                                 lambda e: f"{e['start'][:10]} {e['summary']}"))

    # 4) 予定: 翌週カレンダー
    cu = bundle["calendar"]["upcoming"]
    if cu:
        cats_children["schedule"].append(_mid("翌週の確定予定", cu[:MAX_ENDS_PER_MID],
                                              lambda e: f"{e['start'][:10]} {e['summary']}"))

    # 5) 未完了ToDo
    to = bundle["tasks"]["open"]
    if to:
        cats_children["todo_open"].append(_mid("残タスク", to[:MAX_ENDS_PER_MID],
                                               lambda t: t["title"]
                                               + (f" (期限{t['due'][:10]})" if t.get("due") else "")))

    raw = {"root": ctx["root_title"],
           "categories": [{"key": c["key"], "children": cats_children[c["key"]]} for c in CATEGORIES]}
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
                 "categories": [{"key": c["key"], "children": []} for c in CATEGORIES]}
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

    fig, ax = plt.subplots(figsize=(22, 16.5))
    ax.set_aspect("equal")
    ax.axis("off")
    LX, LY = 15.5, 13.8
    ax.set_xlim(-LX, LX)
    ax.set_ylim(-LY, LY)

    # 放射マップの中心。上=タイトル、左上=凡例、下端の帯=3大アクション枠に空ける
    CX0, CY0 = 0.6, 1.3
    R_CAT, R_MID, R_END = 5.3, 3.5, 1.7
    HALO = dict(boxstyle="round,pad=0.1", facecolor="white", edgecolor="none", alpha=0.75)

    def circle(xy, r, fc, ec="white", lw=2, z=3):
        ax.add_patch(Circle(xy, r, facecolor=fc, edgecolor=ec, linewidth=lw, zorder=z))

    def line(a, b, color, lw=2.0, z=1):
        ax.plot([a[0], b[0]], [a[1], b[1]], color=color, linewidth=lw, zorder=z, alpha=0.45)

    def label(xy, text, size, color="white", weight="bold", z=5, ha="center", va="center",
              halo=False):
        ax.text(xy[0], xy[1], text, fontsize=size, color=color, ha=ha, va=va, weight=weight,
                zorder=z, clip_on=False, bbox=HALO if halo else None)

    n = len(CATEGORIES)
    for i, cat in enumerate(tree["categories"]):
        base = math.radians(90 - i * (360 / n))          # このカテゴリのセクター中心角
        cx, cy = CX0 + R_CAT * math.cos(base), CY0 + R_CAT * math.sin(base)
        color = cat["color"]

        line((CX0, CY0), (cx, cy), color, lw=3.4)
        circle((cx, cy), 1.55, color, lw=3)
        label((cx, cy), cat["short"], 10.5)

        mids = cat["children"][:RENDER_MAX_MIDS]
        if not mids:
            ox, oy = 2.15 * math.cos(base), 2.15 * math.sin(base)
            label((cx + ox, cy + oy), "記録なし", 9, color="#8a8a8a", weight="normal", halo=True)
            continue

        m_span = math.radians(min(78, 22 * len(mids)))
        m0 = base - m_span / 2
        m_step = m_span / max(len(mids) - 1, 1)
        for j, mid in enumerate(mids):
            ma = m0 + j * m_step if len(mids) > 1 else base
            rm = R_MID + (0.9 if j % 2 else 0.0)          # 1つおきに外へずらし中丸の重なりを防ぐ
            mx, my = cx + rm * math.cos(ma), cy + rm * math.sin(ma)
            line((cx, cy), (mx, my), color, lw=1.9)
            circle((mx, my), 0.32, color, ec="white", lw=1.4)
            # 中分類ラベルはマーカーの「内側」(カテゴリ寄り)に置き、末端ラベルと分離
            lmx, lmy = mx - 1.0 * math.cos(ma), my - 1.0 * math.sin(ma)
            label((lmx, lmy), _wrap_n(mid["title"], 12, 3), 7.6, color="#1f2937", halo=True)

            ends = mid["children"][:RENDER_MAX_ENDS]
            if not ends:
                continue
            e_span = math.radians(min(50, 28 * len(ends)))
            e0 = ma - e_span / 2
            e_step = e_span / max(len(ends) - 1, 1)
            for k, end in enumerate(ends):
                ea = e0 + k * e_step if len(ends) > 1 else ma
                rr = R_END + (0.75 if k % 2 else 0.0)
                ex, ey = mx + rr * math.cos(ea), my + rr * math.sin(ea)
                line((mx, my), (ex, ey), color, lw=1.0)
                circle((ex, ey), 0.11, "white", ec=color, lw=1.3, z=4)
                ha = "left" if ex >= mx else "right"
                ox = 0.22 if ha == "left" else -0.22
                label((ex + ox, ey), _wrap_n(_clip(end["title"], 28), 15, 2), 6.8,
                      color="#242424", weight="normal", ha=ha, halo=True)

    # 中央ルート
    circle((CX0, CY0), 1.95, "#111827", ec="white", lw=3, z=6)
    label((CX0, CY0), f"週間棚卸し\n{ctx['label']}", 10.5, color="white", z=7)

    # タイトル / メタ
    ts = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    src_label = {"gemini": "Gemini構造化", "fallback": "簡易構造化(Gemini未使用)",
                 "empty": "記録なし"}[source]
    fig.suptitle(f"週間棚卸しマップ  {ctx['label']}", fontsize=22, weight="bold", y=0.975)
    fig.text(0.5, 0.935, f"生成 {ts} ／ {src_label}", fontsize=11, color="#666666", ha="center")

    # 凡例(左上に縦並び。マップ本体と重ならない空きゾーン)
    ly = LY - 1.6
    for cat in CATEGORIES:
        ax.add_patch(Circle((-LX + 0.6, ly), 0.24, facecolor=cat["color"], edgecolor="none"))
        ax.text(-LX + 1.05, ly, cat["title"], fontsize=9.5, color="#333333",
                ha="left", va="center")
        ly -= 0.8

    # 下端の帯: 来週の手帳用 3大アクション(空欄枠)
    bx0, bw = -LX + 0.7, 2 * LX - 1.4
    by0, bh = -LY + 0.6, 3.0
    ax.add_patch(FancyBboxPatch(
        (bx0, by0), bw, bh, boxstyle="round,pad=0.15,rounding_size=0.3",
        facecolor="#FFFDF3", edgecolor="#D8C89A", linewidth=1.6, zorder=8,
    ))
    ax.text(bx0 + 0.55, by0 + bh - 0.45, "◆ 来週の手帳用 3大アクション",
            fontsize=14, weight="bold", color="#7A5C00", ha="left", va="top", zorder=9)
    for m, num in enumerate(("①", "②", "③")):
        yy = by0 + bh - 1.3 - m * 0.75
        ax.text(bx0 + 0.8, yy, num, fontsize=13, color="#7A5C00", ha="left", va="center", zorder=9)
        ax.plot([bx0 + 1.45, bx0 + bw - 0.6], [yy - 0.28, yy - 0.28],
                color="#C9B98A", linewidth=1.1, zorder=9)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[OK] 画像を保存: {out_path}")
    return out_path


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
        body={"properties": {"title": "週間棚卸しマップ"}}, fields="spreadsheetId",
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
def post_to_discord(message: str, image_path: str | None) -> bool:
    webhook = os.environ.get("DISCORD_WEBHOOK_WEEKLY", "").strip()
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID_WEEKLY_SUMMARY", "").strip()

    payload = {"content": message[:1900]}
    has_image = bool(image_path and os.path.exists(image_path))
    fh = None
    try:
        if webhook:
            url, hdrs, pl = webhook, {"User-Agent": DISCORD_USER_AGENT}, {**payload, "username": "週次棚卸しBot"}
        elif bot_token and channel_id:
            url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
            hdrs, pl = {"Authorization": f"Bot {bot_token}", "User-Agent": DISCORD_USER_AGENT}, payload
        else:
            print("[WARN] DISCORD_WEBHOOK_WEEKLY も Bot+チャンネルID も無いため投稿をスキップ")
            return False

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
        return ok
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] Discord 投稿に失敗: {err}")
        return False
    finally:
        if fh:
            fh.close()


def compose_message(ctx: dict, sheet_link: str | None, source: str, empty: bool,
                    warnings: list[str] | None = None) -> str:
    head = f"🧠 週間棚卸しマップ  {ctx['label']}"
    warn_block = ("\n".join(f"⚠️ {w}" for w in warnings) + "\n\n") if warnings else ""
    if empty:
        body = (warn_block + "今週は取得できた記録がありませんでした"
                + ("（上の警告が原因の可能性があります）。" if warnings
                   else "。来週は小さくてもログを残していきましょう。")
                + (f"\n\nスプレッドシート: {sheet_link}" if sheet_link else ""))
        return head + "\n\n" + body
    note = "（簡易構造化：Gemini 未使用）" if source == "fallback" else ""
    lines = [head + (f"  {note}" if note else ""), ""]
    if warn_block:
        lines.append(warn_block.rstrip())
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

    # --- 5. Discord -------------------------------------------------
    message = compose_message(ctx, sheet_link, source, empty, warnings)
    if dry_run:
        print("[INFO] dry-run: Discord 投稿をスキップ。本文プレビュー:\n" + message)
    else:
        post_to_discord(message, png_path)

    print("=== 完了 ===" + ("（警告あり）" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
