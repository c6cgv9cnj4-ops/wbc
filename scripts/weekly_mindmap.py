# -*- coding: utf-8 -*-
"""
週次「観測」ダッシュボード(モーニングジャーナル) ― 2026-09-26 再編

目的: 自分を評価・管理・矯正することではなく、書き続けたモーニングジャーナルから
「自分では気づいていない気持ち・関心・行動・思考の変化や傾向」を発見すること。
書く時点では何も要求せず、後から読むときも「評価」ではなく「観測」として扱う。

毎週日曜 20:00 JST(GitHub Actions cron `0 11 * * 0`)に無人実行する。
ファイル名・CLI・ワークフロー名は互換のため weekly_mindmap のまま据え置き。

------------------------------------------------------------------------------
処理フロー
------------------------------------------------------------------------------
  1. 収集: `# モーニングジャーナル`(フォーラム)の日付スレッドを、対象週を含む直近8週分
     取得する(journal_review.collect_threads を流用。スレッドURL付き)。
     Google カレンダー/ToDo は件数だけを「参考(ジャーナル外)」として数える。
  2. 観測(Python / scripts/journal_observe.py): 書いた日数・文字数・語が「書いた日のうち
     何日に出たか」の変化(新規/再登場/増/減/不在/継続)・表現(〜たい/やった/面倒/不安 等)の
     出現率・同じ日に出た組み合わせ・根拠(日付/抜粋/URL)。再現可能な数値のみ。
  3. 解釈(Gemini): 2 の観測と根拠抜粋だけを渡し、観測IDを参照した「仮説」を複数の可能性
     として返させる。指示・評価・断定口調は除去。失敗しても観測だけで出力は成立する。
  4. 出力(すべて非公開の置き場所のみ):
       - PNG ダッシュボード(reports/weekly/YYYY_Www_observe.png。コミットしない)
       - Discord `# 週間まとめ`: 1通目 A.数値+B.変化(PNG添付) / 2通目 C.仮説+D.根拠
       - Google スプレッドシート「週次観測」タブに1週1行(対象週で上書き・冪等)
  5. 月末週(その月最後の日曜)は、直近28日 vs その前28日の同じ観測を別メッセージで投稿。

廃止(2026-09-26): 感情4象限(意欲/モヤモヤ/体調/納得)への分類、放射状マインドマップ、
「来週の手帳用3大アクション」、週次レビュー(思考/ToDo/感情/保留・Friction&Action・
Next Focus)の週次実行、GitHub Pages(公開リポジトリ)へのマップ公開、行CSVのコミット。
既存シートの「週次ログ」「週次レビュー」タブは履歴として残し、以後は更新しない。

------------------------------------------------------------------------------
ローカル / モックでのテスト
------------------------------------------------------------------------------
  python scripts/weekly_mindmap.py --use-mock            # 通信なし。合成8週分で PNG と本文
  python scripts/weekly_mindmap.py --dry-run             # 実データ取得・書込/投稿なし
  python scripts/weekly_mindmap.py --week 2026-09-27 --use-mock   # 月末週(月次観測も)
  python -m unittest discover -s tests                   # 観測ロジックの単体テスト

GitHub Actions 上(公開リポジトリのためログも公開)では、本文・語を含むプレビューを
ログに出さず、件数だけを出す(GITHUB_ACTIONS=true で自動判定)。

------------------------------------------------------------------------------
環境変数
------------------------------------------------------------------------------
  GEMINI_API_KEY / GEMINI_MODEL(既定 gemini-3.6-flash)  未設定・失敗なら仮説なしで継続
  DISCORD_BOT_TOKEN / DISCORD_CHANNEL_ID_MORNING_JOURNAL(無ければ DISCORD_CHANNEL_ID_HEALTH)
  DISCORD_WEBHOOK_WEEKLY(無ければ DISCORD_CHANNEL_ID_WEEKLY_SUMMARY + Bot で投稿)
  GOOGLE_SERVICE_ACCOUNT_JSON(Sheets) / GOOGLE_OAUTH_CLIENT_ID・SECRET・REFRESH_TOKEN
  (Calendar/Tasks。約7日で失効しても観測は継続) / WEEKLY_SPREADSHEET_ID / GOOGLE_CALENDAR_IDS
  DRY_RUN=1 で Sheets / Discord をスキップ
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import requests

import journal_observe as jo

# ===========================================================================
# 定数
# ===========================================================================
JST = datetime.timezone(datetime.timedelta(hours=9))
DISCORD_API_BASE = "https://discord.com/api/v10"
REQUEST_TIMEOUT = 20
NEXT_DAYS = 7
LOOKBACK_WEEKS = 8           # 対象週 + 過去7週(新規判定・連続判定・月次の前28日比較に使う)

# 他スクリプト(generate_daily_report.py / fetch_news.py)と同一の現行モデルに揃える。
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

REPORT_WEEKLY_DIR = "reports/weekly"
OBSERVE_SHEET_NAME = "週次観測"
PIN_MARKERS = (jo.HEAD_MARK, "🧠 心の棚卸しマップ")   # 旧形式のピンも解除対象にする

DISCORD_USER_AGENT = "wbc-weekly-mindmap/2.0 (+https://github.com/c6cgv9cnj4-ops/wbc)"

# OAuth スコープ(すべて読み取り + Sheets 書き込み)
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


# ===========================================================================
# 週コンテキスト
# ===========================================================================
def default_anchor(today: datetime.date) -> datetime.date:
    """引数なし実行の対象週。日曜ならその週、それ以外は直前の日曜で終わる週
    (cron が数時間遅れて月曜に起動しても、書き終えた週を対象にするため)。"""
    return today - datetime.timedelta(days=(today.weekday() + 1) % 7)


def week_context(anchor: datetime.date) -> dict:
    """基準日 anchor が属する ISO 週(月〜日)の情報と収集ウィンドウを返す。"""
    iso_year, iso_week, _ = anchor.isocalendar()
    monday = datetime.date.fromisocalendar(iso_year, iso_week, 1)
    sunday = monday + datetime.timedelta(days=6)
    now = datetime.datetime.now(JST)
    week_start = datetime.datetime.combine(monday, datetime.time(0, 0), JST)
    week_end = datetime.datetime.combine(sunday, datetime.time(23, 59, 59), JST)
    end = min(now, week_end)
    return {
        "anchor": anchor,
        "monday": monday,
        "sunday": sunday,
        "week_tag": f"{iso_year}_W{iso_week:02d}",       # ファイル名用
        "week_tag_dash": f"{iso_year}-W{iso_week:02d}",  # スプレッドシート表示用(例: 2026-W39)
        "label": f"{monday.strftime('%Y/%m/%d')}週",
        "past_start": week_start,                         # Calendar/Tasks の集計窓(対象週)
        "now": end,
        "next_end": end + datetime.timedelta(days=NEXT_DAYS),
    }


def is_month_closing_week(ctx: dict) -> bool:
    """この週の日曜日が、その月における最後の日曜日かどうか(=月次観測の実行週か)。"""
    sunday = ctx["sunday"]
    return (sunday + datetime.timedelta(days=7)).month != sunday.month


def _rfc3339(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_public_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"



# ===========================================================================
# Google 認証(Sheets=サービスアカウント / Calendar・Tasks=ユーザーOAuth)
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


SHEETS_ONLY_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def build_service_account_credentials():
    """スプレッドシート操作用のサービスアカウント認証(2026-09-21追加)。

    ユーザーOAuthは同意画面が「テスト」だと約7日で失効するため、Sheets の読み書きは
    サービスアカウントで行う(失効しない)。環境変数:
      GOOGLE_SERVICE_ACCOUNT_JSON  … キーJSONの全文(GitHub Secret 用)
      GOOGLE_SERVICE_ACCOUNT_FILE  … キーJSONのファイルパス(ローカル用)
    対象スプレッドシートを、サービスアカウントのメールアドレスに「編集者」で共有しておくこと。
    無ければ None(従来のユーザーOAuthにフォールバック)。
    """
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    try:
        if raw:
            info = json.loads(raw)
        elif path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                info = json.load(fh)
        else:
            return None
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(info, scopes=SHEETS_ONLY_SCOPES)
        print(f"[INFO] Sheets 認証: サービスアカウント({info.get('client_email', '?')})")
        return creds
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] サービスアカウント認証情報を読み込めません: {type(err).__name__}: {err}")
        return None


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




# ===========================================================================
# スプレッドシート
# ===========================================================================
def create_spreadsheet(creds) -> str:
    """空の週次スプレッドシートを 1 つ作成し ID を返す(初回セットアップ専用)。"""
    svc = build_service("sheets", "v4", creds)
    created = svc.spreadsheets().create(
        body={"properties": {"title": "心の棚卸しマップ"}}, fields="spreadsheetId",
    ).execute(num_retries=5)
    return created["spreadsheetId"]




def _find_sheet_id_by_title(svc, ssid: str, title: str) -> tuple[int | None, bool]:
    meta = svc.spreadsheets().get(
        spreadsheetId=ssid, fields="sheets(properties(sheetId,title))"
    ).execute(num_retries=5)
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == title:
            return s["properties"]["sheetId"], True
    return None, False




# ===========================================================================
# Discord
# ===========================================================================
def post_to_discord(message: str, image_path: str | None, *,
                    components: list[dict] | None = None,
                    username: str = "週次棚卸しBot") -> tuple[bool, str | None]:
    """(成功可否, 投稿したメッセージID) を返す。メッセージIDはピン留め用
    (Webhook 投稿時は ?wait=true を付けて本文を取り戻し、そこから抽出する)。"""
    webhook = os.environ.get("DISCORD_WEBHOOK_WEEKLY", "").strip()
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID_WEEKLY_SUMMARY", "").strip()

    payload = {"content": message[:1900]}
    if components:
        payload["components"] = components
    has_image = bool(image_path and os.path.exists(image_path))
    fh = None
    try:
        if webhook:
            url = webhook + ("&wait=true" if "?" in webhook else "?wait=true")
            hdrs, pl = {"User-Agent": DISCORD_USER_AGENT}, {**payload, "username": username}
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



def write_observe_row(creds, ctx: dict, row: list) -> str | None:
    """「週次観測」タブに1週1行を書く。A列(対象週)が同じ行があれば上書き(冪等)。
    数値列は数値のまま書くので、シート上で推移グラフを作れる。"""
    ssid = os.environ.get("WEEKLY_SPREADSHEET_ID", "").strip()
    if not ssid:
        print("[ERROR] WEEKLY_SPREADSHEET_ID が未設定です。Sheets 追記をスキップします。")
        return None
    svc = build_service("sheets", "v4", creds)
    gid, exists = _find_sheet_id_by_title(svc, ssid, OBSERVE_SHEET_NAME)
    if not exists:
        resp = svc.spreadsheets().batchUpdate(spreadsheetId=ssid, body={"requests": [{"addSheet": {
            "properties": {"title": OBSERVE_SHEET_NAME,
                           "gridProperties": {"columnCount": len(jo.SHEET_HEADER), "frozenRowCount": 1}}}}]},
        ).execute(num_retries=5)
        gid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        svc.spreadsheets().values().update(
            spreadsheetId=ssid, range=f"'{OBSERVE_SHEET_NAME}'!A1",
            valueInputOption="RAW", body={"values": [jo.SHEET_HEADER]},
        ).execute(num_retries=5)
        svc.spreadsheets().batchUpdate(spreadsheetId=ssid, body={"requests": [{"repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "wrapStrategy": "WRAP",
                                           "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.96}}},
            "fields": "userEnteredFormat(textFormat,wrapStrategy,backgroundColor)"}}]},
        ).execute(num_retries=5)

    col_a = svc.spreadsheets().values().get(
        spreadsheetId=ssid, range=f"'{OBSERVE_SHEET_NAME}'!A2:A",
    ).execute(num_retries=5).get("values", [])
    idx = next((i for i, r in enumerate(col_a) if r and r[0] == ctx["week_tag_dash"]), len(col_a))
    # RAW: 仮説や語に「=」「+」で始まる文字列が来ても数式として解釈させない
    svc.spreadsheets().values().update(
        spreadsheetId=ssid, range=f"'{OBSERVE_SHEET_NAME}'!A{idx + 2}",
        valueInputOption="RAW", body={"values": [row]},
    ).execute(num_retries=5)
    link = f"https://docs.google.com/spreadsheets/d/{ssid}/edit#gid={gid}"
    print(f"[OK] 週次観測シート更新: 行{idx + 2}")
    return link


# ===========================================================================
# Discord 投稿(ピン留め)
# ===========================================================================
def pin_latest_and_unpin_old(bot_token: str, channel_id: str, message_id: str | None) -> None:
    """今回の週次観測をピン留めし、以前このBotが投稿した週次まとめのピンは解除する
    (PIN_MARKERS を含むものだけが対象。手動ピンには触れない。ベストエフォート)。"""
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
        content = p.get("content") or ""
        if any(m in content for m in PIN_MARKERS) and p.get("id") != message_id:
            try:
                _discord_bot_request("DELETE", f"/channels/{channel_id}/pins/{p['id']}", bot_token).raise_for_status()
            except Exception as err:  # noqa: BLE001
                print(f"[WARN] 旧ピンの解除に失敗: {err}")
    if message_id:
        try:
            _discord_bot_request("PUT", f"/channels/{channel_id}/pins/{message_id}", bot_token).raise_for_status()
            print("[OK] 最新の週次観測をピン留め")
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] ピン留めに失敗(権限不足の可能性): {err}")


def post_messages(messages: list[str], image_path: str | None, username: str) -> str | None:
    """複数メッセージを順に投稿(画像は1通目のみ)。1通目のメッセージIDを返す。"""
    first_id = None
    for i, msg in enumerate(messages):
        ok, mid = post_to_discord(msg, image_path if i == 0 else None, username=username)
        if i == 0:
            first_id = mid if ok else None
        if not ok:
            break
    return first_id


# ===========================================================================
# 収集
# ===========================================================================
def collect_journal_threads(ctx: dict) -> tuple[list[dict], list[str]]:
    """対象週を含む直近 LOOKBACK_WEEKS 週のスレッド(URL付き)を返す。"""
    import journal_review as jr

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    ch = (os.environ.get("DISCORD_CHANNEL_ID_MORNING_JOURNAL", "").strip()
          or os.environ.get("DISCORD_CHANNEL_ID_HEALTH", "").strip())
    if not token or not ch:
        return [], ["Discord トークン/チャンネルIDが無いためジャーナルを取得できませんでした。"]
    start = ctx["monday"] - datetime.timedelta(days=7 * (LOOKBACK_WEEKS - 1))
    try:
        threads = jr.collect_threads(token, ch, start, ctx["sunday"])["threads"]
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] ジャーナル取得に失敗: {err}")
        return [], ["ジャーナルの取得に失敗しました（Bot権限・チャンネルIDを確認）。"]
    if threads and not any(t["text"] for t in threads):
        return threads, ["スレッドはあるが本文が空でした（Bot の MESSAGE CONTENT 権限を確認）。"]
    failed = [t for t in threads if t.get("fetch_error")]
    if failed:
        return threads, [f"{len(failed)}件のスレッドを取得できず、その日は「書かなかった日」として数えています。"]
    return threads, []


def _context_counts(calendar: dict, tasks: dict, available: bool) -> str:
    if not available:
        return ""
    return (f"参考（ジャーナル外）: カレンダー予定 {len(calendar.get('past', []))}件 / "
            f"完了したToDo {len(tasks.get('completed', []))}件")


# ===========================================================================
# main
# ===========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="モーニングジャーナル週次観測")
    parser.add_argument("--week", default="", help="対象週の基準日 YYYY-MM-DD(空なら直近の日曜で終わる週)")
    parser.add_argument("--dry-run", action="store_true", help="Sheets 書き込み / Discord 投稿を行わない")
    parser.add_argument("--use-mock", action="store_true", help="通信せず合成データで PNG と本文を生成")
    parser.add_argument("--create-spreadsheet", action="store_true",
                        help="初回セットアップ用: 空のスプレッドシートを1つ作成しIDを表示して終了")
    args = parser.parse_args()
    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")

    if args.create_spreadsheet:
        creds = build_service_account_credentials() or build_google_credentials()
        if creds is None or verify_google_credentials(creds):
            print("[ERROR] 有効な Google 認証情報がありません。")
            return 1
        ssid = create_spreadsheet(creds)
        print(f"WEEKLY_SPREADSHEET_ID={ssid}\nhttps://docs.google.com/spreadsheets/d/{ssid}/edit")
        return 0

    today = datetime.datetime.now(JST).date()
    try:
        anchor = datetime.date.fromisoformat(args.week) if args.week else default_anchor(today)
    except ValueError:
        print(f"[WARN] --week の日付形式が不正（{args.week}）。直近の週を対象にします。")
        anchor = default_anchor(today)
    ctx = week_context(anchor)
    print(f"=== 週次観測 {ctx['week_tag']} ({ctx['label']}) / dry_run={dry_run} mock={args.use_mock} ===")
    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    quiet_log = _in_public_ci()

    # --- 1. 収集 -----------------------------------------------------------
    warnings: list[str] = []
    sheets_creds = None
    context_line = ""
    if args.use_mock:
        threads = jo.mock_threads(ctx["monday"], LOOKBACK_WEEKS)
        api_key = ""  # モックでは Gemini を呼ばない
    else:
        threads, w = collect_journal_threads(ctx)
        warnings += w
        creds = build_google_credentials()                   # Calendar/Tasks(ユーザーOAuth)
        sheets_creds = build_service_account_credentials()   # Sheets(サービスアカウント)
        if sheets_creds is not None and verify_google_credentials(sheets_creds):
            warnings.append("サービスアカウント認証エラー：スプレッドシートを更新できませんでした。")
            sheets_creds = None
        if creds is not None and verify_google_credentials(creds):
            print("[WARN] Google OAuth の期限切れ。カレンダー/ToDo の件数はスキップ")
            creds = None
        if sheets_creds is None:
            sheets_creds = creds
        context_line = _context_counts(collect_calendar(creds, ctx), collect_tasks(creds, ctx), creds is not None)

    # --- 2. 観測(Python) ----------------------------------------------------
    days = jo.build_days(threads)
    obs = jo.observe(days, ctx["monday"], unit_days=7, n_base=4, n_hist=LOOKBACK_WEEKS - 1)
    print(f"[INFO] 観測: {jo.redacted_summary(obs)}")
    empty = obs["writing"]["written"] == 0

    # --- 3. 解釈(Gemini・仮説) ---------------------------------------------
    interp, istatus = (None, "few") if empty else jo.interpret(obs, api_key, model)

    # --- 4. 出力 -----------------------------------------------------------
    png_path = os.path.join(REPORT_WEEKLY_DIR, f"{ctx['week_tag']}_observe.png")
    try:
        jo.render_png(obs, png_path, title=f"週次観測ダッシュボード  {obs['period']}",
                      font_family=_resolve_jp_font())
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] PNG 描画に失敗（本文のみ投稿）: {err}")
        png_path = None

    sheet_link = None
    row = jo.sheet_row(ctx["week_tag_dash"], obs, interp, istatus)
    if args.use_mock or dry_run:
        print("[INFO] モック/dry-run のためスプレッドシート書き込みをスキップ")
    elif sheets_creds is None:
        print("[INFO] Sheets 認証情報が無い/無効のため書き込みをスキップ")
    else:
        try:
            sheet_link = write_observe_row(sheets_creds, ctx, row)
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] スプレッドシート書き込みに失敗: {err}")
            warnings.append("スプレッドシートの更新に失敗しました。")

    if empty:
        messages = [f"{jo.HEAD_MARK}  {obs['period']}\n\n"
                    + "".join(f"⚠️ {x}\n" for x in warnings)
                    + "今週は取得できたジャーナルがありませんでした。書かなかった週も、そのまま記録として残ります。"]
    else:
        messages = jo.compose_messages(obs, interp, istatus, head=jo.HEAD_MARK, warnings=warnings,
                                       sheet_link=sheet_link, extra_lines=[context_line] if context_line else None)

    if dry_run or args.use_mock:
        if quiet_log:
            print(f"[INFO] dry-run: 投稿スキップ（公開ログのため本文は非表示。{len(messages)}通・"
                  f"{sum(len(m) for m in messages)}字）")
        else:
            print("[INFO] dry-run/モック: 投稿スキップ。本文プレビュー:\n" + "\n\n---\n\n".join(messages))
    else:
        mid = post_messages(messages, png_path, username="週次観測")
        bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        channel_id = os.environ.get("DISCORD_CHANNEL_ID_WEEKLY_SUMMARY", "").strip()
        if mid and bot_token and channel_id:
            pin_latest_and_unpin_old(bot_token, channel_id, mid)

    # --- 5. 月末週: 直近28日 vs その前28日 -------------------------------
    m_start = ctx["sunday"] - datetime.timedelta(days=27)
    mobs = (jo.observe(days, m_start, unit_days=28, n_base=1, n_hist=1)
            if is_month_closing_week(ctx) else None)
    if mobs and mobs["writing"]["written"]:   # 最終週に書かなくても、28日内に記録があれば出す
        print(f"[INFO] 月次観測: {jo.redacted_summary(mobs)}")
        minterp, mstatus = jo.interpret(mobs, api_key, model,
                                        context_note="(28日間とその前の28日間の比較)")
        mhead = f"🌕 月次観測（{ctx['sunday'].month}月）"
        mmsgs = jo.compose_messages(mobs, minterp, mstatus, head=mhead)
        if dry_run or args.use_mock:
            if not quiet_log:
                print("[INFO] 月次プレビュー:\n" + "\n\n---\n\n".join(mmsgs))
        else:
            post_messages(mmsgs, None, username="月次観測")

    print("=== 完了 ===" + ("（警告あり）" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
