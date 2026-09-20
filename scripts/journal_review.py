# -*- coding: utf-8 -*-
"""
モーニングジャーナル「週次レビュー」(見返して自己理解と行動の質が上がる形式)

Discord フォーラム「#モーニングジャーナル」の日付スレッドを読み、次の成果物を作る。

  1. ビジュアル・マインドマップ画像(PNG)   … 4カテゴリの放射状マップ
  2. 1枚完結のインタラクティブHTML(D3.js)  … ノードクリックで該当スレッドを開く
  3. Google スプレッドシート「週次レビュー」タブへ、1週=1行で新しい週を先頭に蓄積
       - 【俯瞰】マインドマップ画像 (=IMAGE)
       - 【資産】💡 Ideas … 各項目に該当スレッドURL
       - 【教訓・脱出】🛑 Friction & Action … ノイズと対処の1行セット
       - 【次週フォーカス】🎯 Next Focus … 残す行動は1つだけ(太字)
       - 【日次ログ直リンク】月〜日のスレッドURL

スレッドURLは https://discord.com/channels/<guild_id>/<thread_id> 形式。

単体実行(HTMLだけ欲しいとき):
  python scripts/journal_review.py --days 30 --html-out mindmap.html --png-out mindmap.png
  python scripts/journal_review.py --use-mock --html-out /tmp/m.html   # 通信なし

環境変数: DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID_MORNING_JOURNAL(or _HEALTH),
          GEMINI_API_KEY(任意。無ければ簡易ルールで構造化), GEMINI_MODEL(任意),
          WEEKLY_SPREADSHEET_ID + GOOGLE_SERVICE_ACCOUNT_JSON(スプレッドシート書き込み時。シートをSAに編集者共有)
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import sys
import time

import requests

JST = datetime.timezone(datetime.timedelta(hours=9))
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000
REQUEST_TIMEOUT = 20
USER_AGENT = "wbc-journal-review/1.0 (+https://github.com/c6cgv9cnj4-ops/wbc)"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

REVIEW_SHEET_NAME = "週次レビュー"
REVIEW_HEADER = [
    "週ID", "週のタイトル / 実行日", "【俯瞰】マインドマップ",
    "【資産】💡 Ideas", "【教訓・脱出】🛑 Friction & Action", "【次週フォーカス】🎯 Next Focus",
    "月", "火", "水", "木", "金", "土", "日",
]
REVIEW_COL_WIDTHS = [90, 180, 420, 340, 380, 260, 120, 120, 120, 120, 120, 120, 120]
REVIEW_IMAGE_ROW_HEIGHT = 330
REVIEW_PAGES_URL = "https://c6cgv9cnj4-ops.github.io/wbc/mindmap/review/"
REVIEW_PNG_DIR = "mindmap/review"

# 表示順・色は固定(🔵思考 / 🟢ToDo / 🟡感情 / 🟣保留)
CATEGORIES = [
    {"key": "think", "title": "思考・アイデア",       "emoji": "🔵", "color": "#2563EB"},
    {"key": "todo",  "title": "ToDo・注力アクション", "emoji": "🟢", "color": "#16A34A"},
    {"key": "feel",  "title": "感情・状態",           "emoji": "🟡", "color": "#D4A017"},
    {"key": "hold",  "title": "保留・整理",           "emoji": "🟣", "color": "#7C3AED"},
]
MAX_NODES_PER_CAT = 6
MAX_IDEAS = 7
MAX_FRICTION = 4
LABEL_MAX = 24
TEXT_PER_THREAD = 1800
TEXT_TOTAL = 14000


# ===========================================================================
# 小ヘルパ
# ===========================================================================
def _clip(text, n: int) -> str:
    text = " ".join(str(text if text is not None else "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def snowflake_to_dt_utc(snowflake_id) -> datetime.datetime:
    ms = (int(snowflake_id) >> 22) + DISCORD_EPOCH_MS
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)


def week_bounds(anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
    y, w, _ = anchor.isocalendar()
    mon = datetime.date.fromisocalendar(y, w, 1)
    return mon, mon + datetime.timedelta(days=6)


def week_title(monday: datetime.date) -> str:
    """'2026年9月第3週 マインドマップ'。月は週の木曜日基準(ISOの考え方)、
    第N週はその月の1日を含む週(月曜始まり)を第1週として数える。"""
    thu = monday + datetime.timedelta(days=3)
    first = datetime.date(thu.year, thu.month, 1)
    first_week_monday = first - datetime.timedelta(days=first.weekday())
    n = (monday - first_week_monday).days // 7 + 1
    return f"{thu.year}年{thu.month}月第{n}週 マインドマップ"


def parse_thread_date(name: str) -> datetime.date | None:
    """スレッド名 'YYYY/MM/DD' 形式(区切りは任意、後ろに文字が付いてもOK)から日付を得る。"""
    def _mk(y, m, d):
        if 2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
            try:
                return datetime.date(y, m, d)
            except ValueError:
                return None
        return None

    groups = "".join(c if c.isdigit() else " " for c in (name or "")).split()
    for i in range(len(groups) - 2):
        if len(groups[i]) == 4:
            got = _mk(int(groups[i]), int(groups[i + 1]), int(groups[i + 2][:2]))
            if got:
                return got
            break
    joined = "".join(ch for ch in (name or "") if ch.isdigit())
    if len(joined) >= 8:
        return _mk(int(joined[:4]), int(joined[4:6]), int(joined[6:8]))
    return None


# ===========================================================================
# 1) Discord からスレッド収集
# ===========================================================================
def _discord_get(path: str, token: str, params: dict | None = None):
    url = f"{DISCORD_API_BASE}{path}"
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
    for attempt in range(3):
        resp = requests.get(url, headers=headers, params=params or {}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429 and attempt < 2:
            time.sleep(min(float(resp.headers.get("Retry-After", "1") or "1"), 8))
            continue
        if resp.status_code >= 500 and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def _thread_messages(thread_id: str, token: str) -> list[dict]:
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


def _list_forum_threads(channel_id: str, token: str) -> tuple[str, list[dict]]:
    meta = _discord_get(f"/channels/{channel_id}", token)
    guild_id = meta.get("guild_id") or ""
    threads: list[dict] = []
    if guild_id:
        data = _discord_get(f"/guilds/{guild_id}/threads/active", token)
        threads.extend(t for t in data.get("threads", []) if t.get("parent_id") == channel_id)
    before = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        data = _discord_get(f"/channels/{channel_id}/threads/archived/public", token, params)
        batch = data.get("threads", [])
        threads.extend(batch)
        last_ts = batch[-1].get("thread_metadata", {}).get("archive_timestamp") if batch else None
        if not data.get("has_more") or not batch or not last_ts:
            break
        before = last_ts
    return guild_id, threads


def thread_url(guild_id: str, thread_id: str) -> str:
    return f"https://discord.com/channels/{guild_id}/{thread_id}"


def collect_threads(token: str, channel_id: str, start: datetime.date, end: datetime.date) -> dict:
    """期間内の日付スレッドを集める。返り値:
    {"guild_id": str, "threads": [{"tid","name","date"(date),"url","text"}]} 日付・作成順。
    スレッドの日付はスレッド名の日付を優先し、無ければ作成日(JST)を使う
    (名前の日付と作成日は一致しないことが多いため)。"""
    guild_id, raw = _list_forum_threads(channel_id, token)
    seen: set[str] = set()
    picked = []
    for t in raw:
        tid = t.get("id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        d = parse_thread_date(t.get("name", "")) or snowflake_to_dt_utc(tid).astimezone(JST).date()
        if start <= d <= end:
            picked.append((d, tid, t.get("name", "(無題)")))
    picked.sort(key=lambda x: (x[0], int(x[1])))
    threads = []
    for d, tid, name in picked:
        try:
            msgs = _thread_messages(tid, token)
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] スレッド {name} の取得に失敗、本文なしで継続: {err}")
            msgs = []
        text = "\n".join((m.get("content") or "").strip() for m in msgs if (m.get("content") or "").strip())
        threads.append({"tid": tid, "name": name, "date": d, "url": thread_url(guild_id, tid), "text": text})
    print(f"[INFO] ジャーナル: {len(threads)}スレッド / 本文あり {sum(1 for t in threads if t['text'])}")
    return {"guild_id": guild_id, "threads": threads}


# ===========================================================================
# 2) 構造化(Gemini / フォールバック)
# ===========================================================================
def _threads_text(threads: list[dict]) -> tuple[str, dict[str, dict]]:
    """Gemini に渡す本文。各スレッドに [T1].. の参照タグを付け、タグ→スレッドの対応を返す。"""
    tag_map: dict[str, dict] = {}
    parts: list[str] = []
    total = 0
    for i, t in enumerate([t for t in threads if t["text"]], 1):
        tag = f"T{i}"
        tag_map[tag] = t
        body = t["text"][:TEXT_PER_THREAD]
        block = f"[{tag}] {t['date'].strftime('%Y-%m-%d')}（スレッド名: {t['name']}）\n{body}"
        if total + len(block) > TEXT_TOTAL:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts), tag_map


def _prompt(period_label: str, body: str) -> str:
    return f"""あなたは、本人が後から見返して「自己理解」と「行動の質」を上げるための
モーニングジャーナル整理係です。以下は期間「{period_label}」の日記本文です。
各ブロックの先頭 [T数字] は出典タグです。

{body}

次の JSON だけを出力してください(説明文・コードフェンス不要)。本文に無いことは作らない。
{{
  "mindmap": {{
    "think": [{{"label": "…", "detail": "…", "t": "T1"}}],
    "todo":  [...],
    "feel":  [...],
    "hold":  [...]
  }},
  "ideas": [{{"text": "…", "t": "T1"}}],
  "friction": [{{"noise": "…", "action": "…", "t": "T1"}}],
  "next_focus": "…"
}}

各項目の意味:
- think: 新しい着想・やってみたいこと・仮説(🔵)
- todo : 優先して着手したい重要な行動(🟢)
- feel : 心身のコンディション・心地よかった瞬間・モヤモヤ(🟡)
- hold : 今は手放すこと・すぐ結論が出ない悩み(🟣)
- label は{LABEL_MAX}字以内の体言止め、detail は1文(60字以内)、t は必ず上の出典タグから選ぶ。
- 各カテゴリ最大{MAX_NODES_PER_CAT}件。該当が無いカテゴリは空配列。
- ideas: 雑談や愚痴の中から「後で形にできる着想・検証したい仮説・買いたい物・やりたいこと」だけ
  (最大{MAX_IDEAS}件、text は40字以内)。
- friction: その期間に時間や気力を奪われたこと(モヤモヤ・停滞)と、それにどう対処したか/
  今後どう割り切るか(最大{MAX_FRICTION}件)。noise・action とも各40字以内で必ず1行ずつ。
  本文に対処が書かれていなければ、本文から妥当な「割り切り方」を1つ提案してよい。
- next_focus: 次の1週間で最もインパクトのある行動を「1つだけ」(50字以内・命令形でなく行動名)。
"""


def _loads_loose(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _empty_review() -> dict:
    return {"mindmap": {c["key"]: [] for c in CATEGORIES}, "ideas": [], "friction": [], "next_focus": ""}


def _resolve_ref(tag, tag_map: dict[str, dict]) -> dict | None:
    return tag_map.get(str(tag or "").strip().upper())


def normalize_review(raw: dict, tag_map: dict[str, dict]) -> dict:
    """Gemini 応答を検証・整形。出典タグは実在するスレッドにだけ解決(捏造URLを防ぐ)。"""
    out = _empty_review()
    mm = (raw or {}).get("mindmap") or {}
    for c in CATEGORIES:
        for n in (mm.get(c["key"]) or [])[:MAX_NODES_PER_CAT]:
            if not isinstance(n, dict) or not str(n.get("label", "")).strip():
                continue
            ref = _resolve_ref(n.get("t"), tag_map)
            out["mindmap"][c["key"]].append({
                "label": _clip(n["label"], LABEL_MAX),
                "detail": _clip(n.get("detail", ""), 80),
                "url": ref["url"] if ref else "", "date": ref["date"].strftime("%m/%d") if ref else "",
            })
    for it in (raw.get("ideas") or [])[:MAX_IDEAS]:
        if isinstance(it, dict) and str(it.get("text", "")).strip():
            ref = _resolve_ref(it.get("t"), tag_map)
            out["ideas"].append({"text": _clip(it["text"], 60), "url": ref["url"] if ref else ""})
    for it in (raw.get("friction") or [])[:MAX_FRICTION]:
        if isinstance(it, dict) and str(it.get("noise", "")).strip():
            ref = _resolve_ref(it.get("t"), tag_map)
            out["friction"].append({"noise": _clip(it["noise"], 60),
                                    "action": _clip(it.get("action", ""), 60),
                                    "url": ref["url"] if ref else ""})
    out["next_focus"] = _clip(raw.get("next_focus", ""), 80)
    return out


_KW = {
    "todo": ("やる", "やらなきゃ", "する予定", "決める", "始める", "終わらせ", "提出", "申請", "予約", "連絡", "整理する"),
    "think": ("かも", "試したい", "やりたい", "アイデア", "作りたい", "思いついた", "気づいた", "仮説", "買いたい"),
    "feel": ("疲れ", "眠", "だるい", "楽しかった", "嬉しい", "不安", "モヤモヤ", "しんどい", "体調", "気持ち", "食欲", "酒"),
    "hold": ("迷", "悩", "わからない", "保留", "そのうち", "いつか", "手放", "諦め"),
}


def fallback_review(threads: list[dict]) -> dict:
    """Gemini が使えない時の簡易ルール分類(キーワード一致)。品質は劣るが空にはしない。"""
    out = _empty_review()
    for t in threads:
        for line in [ln.strip("・-* \t") for ln in t["text"].splitlines() if ln.strip()]:
            for key in ("todo", "think", "hold", "feel"):
                if any(k in line for k in _KW[key]) and len(out["mindmap"][key]) < MAX_NODES_PER_CAT:
                    out["mindmap"][key].append({"label": _clip(line, LABEL_MAX), "detail": _clip(line, 80),
                                                "url": t["url"], "date": t["date"].strftime("%m/%d")})
                    if key == "think" and len(out["ideas"]) < MAX_IDEAS:
                        out["ideas"].append({"text": _clip(line, 60), "url": t["url"]})
                    if key == "feel" and any(k in line for k in ("疲れ", "眠", "だるい", "不安", "モヤモヤ", "しんどい")) \
                            and len(out["friction"]) < MAX_FRICTION:
                        out["friction"].append({"noise": _clip(line, 60), "action": "（自動要約なし：本文を見返して対処を決める）",
                                                "url": t["url"]})
                    break
    todos = out["mindmap"]["todo"]
    out["next_focus"] = todos[0]["label"] if todos else "今週の日記を見返して、次にやることを1つだけ決める"
    return out


def analyze(threads: list[dict], period_label: str, api_key: str, model: str) -> tuple[dict, str]:
    """(review, source)。source は 'gemini' / 'fallback' / 'empty'。"""
    body, tag_map = _threads_text(threads)
    if not body:
        r = _empty_review()
        r["next_focus"] = "まずはモーニングジャーナルを1行でも書く"
        return r, "empty"
    if api_key:
        try:
            from google import genai

            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=model, contents=_prompt(period_label, body),
                config={"response_mime_type": "application/json"})
            review = normalize_review(_loads_loose(resp.text or ""), tag_map)
            if any(review["mindmap"][c["key"]] for c in CATEGORIES):
                print(f"[INFO] 週次レビュー構造化: Gemini({model}) 成功")
                return review, "gemini"
            print("[WARN] Gemini 応答が空。フォールバックへ")
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] Gemini 構造化に失敗、フォールバックへ: {err}")
    else:
        print("[INFO] GEMINI_API_KEY 未設定。簡易ルールで構造化")
    return fallback_review([t for t in threads if t["text"]]), "fallback"


# ===========================================================================
# 3) PNG(matplotlib 放射状マインドマップ)
# ===========================================================================
def _resolve_font() -> str | None:
    import glob

    from matplotlib import font_manager

    cands = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
             "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
             "/Library/Fonts/ヒラギノ角ゴシック W3.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc"]
    for pat in ("/usr/share/fonts/**/NotoSansCJK*.*", "/usr/share/fonts/**/ipaex*.ttf"):
        cands.extend(sorted(glob.glob(pat, recursive=True)))
    for p in cands:
        if os.path.exists(p):
            try:
                font_manager.fontManager.addfont(p)
                return font_manager.FontProperties(fname=p).get_name()
            except Exception:  # noqa: BLE001
                continue
    for f in font_manager.fontManager.ttflist:
        if any(k in f.name for k in ("Noto Sans CJK", "IPAex", "Hiragino", "Yu Gothic", "Meiryo")):
            return f.name
    return None


def _wrap(text: str, width: int, max_lines: int = 3) -> str:
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= width:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return "\n".join(lines)


def render_png(review: dict, title: str, period_label: str, out_path: str) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    fam = _resolve_font()
    if fam:
        plt.rcParams["font.family"] = fam
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(14, 11))
    ax.set_aspect("equal")
    ax.axis("off")
    fig.subplots_adjust(left=0.01, right=0.99, top=0.93, bottom=0.01)
    bx: list[float] = []
    by: list[float] = []

    def mark(x, y, pad=0.0):
        bx.extend([x - pad, x + pad])
        by.extend([y - pad, y + pad])

    R_CAT, R_NODE = 4.3, 3.6
    ax.add_patch(Circle((0, 0), 1.55, facecolor="#0F172A", edgecolor="white", linewidth=3, zorder=4))
    ax.text(0, 0.05, _wrap(title, 6, 3), color="white", fontsize=16, weight="bold",
            ha="center", va="center", zorder=6)
    mark(0, 0, 1.6)

    for i, cat in enumerate(CATEGORIES):
        base = math.radians(45 + i * 90 * -1)          # 右上→右下→左下→左上
        cx, cy = R_CAT * math.cos(base), R_CAT * math.sin(base)
        col = cat["color"]
        nodes = review["mindmap"][cat["key"]]
        ax.plot([0, cx], [0, cy], color=col, linewidth=5, alpha=0.55, zorder=1)
        ax.add_patch(Circle((cx, cy), 1.28, facecolor=col, edgecolor="white", linewidth=3, zorder=4))
        ax.text(cx, cy, cat["title"].replace("・", "\n") + f"\n{len(nodes)}件", color="white", fontsize=14,
                weight="bold", ha="center", va="center", zorder=6)
        mark(cx, cy, 1.3)
        n = len(nodes)
        for j, node in enumerate(nodes):
            spread = 74 if n > 1 else 0
            ang = base + math.radians((-spread / 2) + spread * j / max(n - 1, 1))
            nx, ny = cx + R_NODE * math.cos(ang), cy + R_NODE * math.sin(ang)
            ax.plot([cx, nx], [cy, ny], color=col, linewidth=2.2, alpha=0.5, zorder=1)
            ax.add_patch(Circle((nx, ny), 0.3, facecolor=col, edgecolor="white", linewidth=2, zorder=3))
            mark(nx, ny, 0.3)
            right = math.cos(ang) >= 0
            tx = nx + (0.42 if right else -0.42)
            ax.text(tx, ny, _wrap(node["label"], 11, 3), color="#111827", fontsize=12.5, ha="left" if right else "right",
                    va="center", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.12", facecolor="white", edgecolor="none", alpha=0.85))
            mark(tx + (2.9 if right else -2.9), ny, 0.1)

    ax.set_title(f"{title}\n{period_label}", fontsize=19, weight="bold", pad=8)
    pad = 0.6
    ax.set_xlim(min(bx) - pad, max(bx) + pad)
    ax.set_ylim(min(by) - pad, max(by) + pad)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fig.savefig(out_path, dpi=100, facecolor="white")
    plt.close(fig)
    print(f"[OK] PNG: {out_path}")
    return out_path


# ===========================================================================
# 4) HTML(D3.js 放射状ツリー。ノードクリックで該当スレッドを新規タブで開く)
# ===========================================================================
def _html_esc(s) -> str:
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_html(review: dict, title: str, period_label: str, source_label: str,
               threads: list[dict] | None = None) -> str:
    tree = {"name": title, "period": period_label, "children": []}
    for c in CATEGORIES:
        tree["children"].append({
            "name": c["title"], "emoji": c["emoji"], "color": c["color"], "cat": True,
            "children": [{"name": n["label"], "detail": n["detail"], "url": n["url"], "date": n["date"],
                          "color": c["color"]} for n in review["mindmap"][c["key"]]],
        })
    payload = json.dumps(tree, ensure_ascii=False).replace("</", "<\\/")
    counts = "".join(
        f'<span class="chip" style="background:{c["color"]}">{c["emoji"]} {_html_esc(c["title"])} '
        f'{len(review["mindmap"][c["key"]])}件</span>' for c in CATEGORIES)

    def link(text, url):
        return (f'<a href="{_html_esc(url)}" target="_blank" rel="noopener">{_html_esc(text)}</a>'
                if url else _html_esc(text))

    ideas = "".join(f"<li>{link(i['text'], i['url'])}{' 🔗' if i['url'] else ''}</li>" for i in review["ideas"]) or "<li>なし</li>"
    fric = "".join(
        f"<li><b>{_html_esc(f['noise'])}</b><br>→ {link(f['action'] or '（対処未記入）', f['url'])}</li>"
        for f in review["friction"]) or "<li>なし</li>"
    nxt = _html_esc(review["next_focus"]) or "（未設定）"
    thread_links = ""
    if threads:
        thread_links = "".join(
            f'<a class="day" href="{_html_esc(t["url"])}" target="_blank" rel="noopener">'
            f'{t["date"].strftime("%m/%d")}</a>' for t in threads)
    gen = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html_esc(title)}</title>
<style>
:root{{color-scheme:light dark}}
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;background:#f8fafc;color:#0f172a}}
@media (prefers-color-scheme:dark){{body{{background:#0b1220;color:#e5e7eb}} .card{{background:#111a2e!important;border-color:#22304d!important}} .label-halo{{stroke:#0b1220!important}} .day{{background:#1e293b!important;color:#e5e7eb!important}}}}
header{{padding:18px 22px 6px}} h1{{margin:0;font-size:22px}} .sub{{opacity:.7;font-size:13px;margin-top:4px}}
.chips{{display:flex;flex-wrap:wrap;gap:8px;padding:8px 22px}} .chip{{color:#fff;border-radius:999px;padding:4px 12px;font-size:13px;font-weight:600}}
.mapwrap{{overflow-x:auto;-webkit-overflow-scrolling:touch}} #map{{width:100%;min-width:880px;max-width:1300px;margin:0 auto;display:block}}
.node-leaf{{cursor:pointer}} .node-leaf:hover circle{{stroke:#000;stroke-width:3}} .node-leaf:hover text{{text-decoration:underline}}
.label-halo{{stroke:#f8fafc;stroke-width:4px;paint-order:stroke;font-size:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;padding:8px 22px 30px;max-width:1200px;margin:0 auto}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px}} .card h2{{margin:0 0 8px;font-size:16px}}
.card ul{{margin:0;padding-left:20px}} .card li{{margin:6px 0;font-size:14px;line-height:1.5}} .focus{{font-size:18px;font-weight:800;line-height:1.5}}
.days{{display:flex;flex-wrap:wrap;gap:6px}} .day{{background:#e2e8f0;color:#0f172a;border-radius:8px;padding:4px 10px;text-decoration:none;font-size:13px}}
a{{color:#2563eb}} .hint{{padding:0 22px;font-size:12px;opacity:.65}}
</style></head><body>
<header><h1>🧠 {_html_esc(title)}</h1>
<div class="sub">対象期間: {_html_esc(period_label)} ／ 構造化: {_html_esc(source_label)} ／ 生成: {gen}</div></header>
<div class="chips">{counts}</div>
<div class="hint">丸をクリックすると、該当のDiscordスレッドが新しいタブで開きます（🔗のない丸はスレッド特定なし）。ホバーで詳細。</div>
<div class="mapwrap"><svg id="map" viewBox="-680 -520 1360 1040"></svg></div>
<div class="grid">
<div class="card"><h2>💡 Ideas（後で形にできる着想）</h2><ul>{ideas}</ul></div>
<div class="card"><h2>🛑 Friction &amp; Action（ノイズと対処）</h2><ul>{fric}</ul></div>
<div class="card"><h2>🎯 Next Focus（残す行動は1つだけ）</h2><div class="focus">{nxt}</div></div>
<div class="card"><h2>📅 日次スレッド</h2><div class="days">{thread_links or '—'}</div></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script>
const data = {payload};
const svg = d3.select("#map");
const root = d3.hierarchy(data);
const R = 330;
d3.cluster().size([2 * Math.PI, R]).separation((a, b) => (a.parent === b.parent ? 1 : 1.6))(root);
// カテゴリを固定の4方向(右上→右下→左下→左上)へ配置し、その周りに子を扇状に広げる
const cats = root.children || [];
const sector = 2 * Math.PI / Math.max(cats.length, 1);
cats.forEach((c, i) => {{
  const mid = i * sector + sector / 2;
  c.x = mid; c.y = R * 0.52;
  const kids = c.children || [];
  const span = Math.min(sector * 0.86, Math.max(kids.length, 1) * 0.2);
  kids.forEach((k, j) => {{ k.x = kids.length > 1 ? mid - span / 2 + span * j / (kids.length - 1) : mid; k.y = R; }});
}});
root.x = 0; root.y = 0;
const pt = (d) => [d.y * Math.cos(d.x - Math.PI / 2), d.y * Math.sin(d.x - Math.PI / 2)];
const g = svg.append("g");
g.append("g").selectAll("path").data(root.links()).join("path")
  .attr("fill", "none").attr("stroke", d => d.target.data.color || "#94a3b8").attr("stroke-opacity", .55)
  .attr("stroke-width", d => d.source.depth === 0 ? 5 : 2.4)
  .attr("d", d3.linkRadial().angle(d => d.x).radius(d => d.y));
const leaves = g.append("g").selectAll("g").data(root.descendants().filter(d => d.depth === 2)).join("g")
  .attr("class", "node-leaf")
  .attr("transform", d => `translate(${{pt(d)}})`)
  .on("click", (e, d) => {{ if (d.data.url) window.open(d.data.url, "_blank", "noopener"); }});
leaves.append("title").text(d => (d.data.detail || d.data.name) + (d.data.date ? `（${{d.data.date}}）` : "") + (d.data.url ? "\\nクリックでDiscordスレッドを開く" : ""));
leaves.append("circle").attr("r", d => d.data.url ? 10 : 8).attr("fill", d => d.data.color).attr("stroke", "#fff").attr("stroke-width", 2);
leaves.append("text").attr("class", "label-halo")
  .attr("dy", "0.32em")
  .attr("x", d => (Math.sin(d.x) >= 0 ? 16 : -16))
  .attr("text-anchor", d => (Math.sin(d.x) >= 0 ? "start" : "end"))
  .attr("fill", "currentColor").text(d => (d.data.name.length > 20 ? d.data.name.slice(0, 19) + "…" : d.data.name) + (d.data.url ? " 🔗" : ""));
const catg = g.append("g").selectAll("g").data(cats).join("g").attr("transform", d => `translate(${{pt(d)}})`);
catg.append("circle").attr("r", 40).attr("fill", d => d.data.color).attr("stroke", "#fff").attr("stroke-width", 4);
catg.append("text").attr("text-anchor", "middle").attr("fill", "#fff").attr("font-weight", 700).attr("font-size", 13)
  .each(function (d) {{
    const words = (d.data.emoji + " " + d.data.name).match(/.{1,6}/g) || [];
    d3.select(this).selectAll("tspan").data(words.slice(0, 3)).join("tspan").attr("x", 0)
      .attr("dy", (w, i) => (i === 0 ? `${{-0.55 * (Math.min(words.length, 3) - 1)}}em` : "1.15em")).text(w => w);
  }});
const rc = g.append("g");
rc.append("circle").attr("r", 64).attr("fill", "#0f172a").attr("stroke", "#fff").attr("stroke-width", 4);
rc.append("text").attr("text-anchor", "middle").attr("fill", "#fff").attr("font-weight", 800).attr("font-size", 15)
  .each(function () {{
    const lines = (data.name.match(/.{1,8}/g) || []).slice(0, 3);
    d3.select(this).selectAll("tspan").data(lines).join("tspan").attr("x", 0)
      .attr("dy", (w, i) => (i === 0 ? `${{-0.55 * (lines.length - 1)}}em` : "1.2em")).text(w => w);
  }});
</script></body></html>"""


# ===========================================================================
# 5) スプレッドシート(週次レビュー タブ・新しい週を先頭に蓄積)
# ===========================================================================
def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def rich_cell(lines: list[tuple[str, str | None, bool]]) -> dict:
    """[(text, url|None, bold)] → 行ごとにリンク/太字を付けた1セル(textFormatRuns)。
    Sheets の index は UTF-16 コードユニット単位(絵文字は2)。"""
    text = "\n".join(t for t, _, _ in lines)
    runs, pos = [], 0
    for t, url, bold in lines:
        fmt: dict = {}
        if url:
            fmt["link"] = {"uri": url}
            fmt["underline"] = True
            fmt["foregroundColor"] = {"red": 0.07, "green": 0.33, "blue": 0.8}
        if bold:
            fmt["bold"] = True
        runs.append({"startIndex": pos, "format": fmt})
        pos += _u16(t) + 1  # +1 は改行
    return {"userEnteredValue": {"stringValue": text or " "},
            "userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"},
            "textFormatRuns": runs if text else []}


def _plain_cell(text: str, bold: bool = False) -> dict:
    return {"userEnteredValue": {"stringValue": text or " "},
            "userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP",
                                  "textFormat": {"bold": bold}}}


def build_review_row(monday: datetime.date, run_date: datetime.date, review: dict, image_url: str,
                     threads: list[dict]) -> list[dict]:
    y, w, _ = monday.isocalendar()
    week_id = f"{y}-W{w:02d}"
    sunday = monday + datetime.timedelta(days=6)
    a = _plain_cell(week_id, bold=True)
    b = _plain_cell(f"{week_title(monday)}\n期間: {monday.strftime('%m/%d')}〜{sunday.strftime('%m/%d')}\n実行日: {run_date.strftime('%Y-%m-%d')}")
    c = {"userEnteredValue": {"formulaValue": f'=IMAGE("{image_url}")'},
         "userEnteredFormat": {"verticalAlignment": "TOP"}}
    ideas = rich_cell([(f"・{i['text']}" + (" 🔗" if i["url"] else ""), i["url"] or None, False)
                       for i in review["ideas"]] or [("（該当なし）", None, False)])
    fl: list[tuple[str, str | None, bool]] = []
    for f in review["friction"]:
        fl.append((f"■ {f['noise']}", None, True))
        fl.append((f"　→ {f['action'] or '（対処未記入）'}" + (" 🔗" if f["url"] else ""), f["url"] or None, False))
    friction = rich_cell(fl or [("（該当なし）", None, False)])
    focus = rich_cell([(review["next_focus"] or "（未設定）", None, True)])
    days: list[dict] = []
    wd = "月火水木金土日"
    for i in range(7):
        d = monday + datetime.timedelta(days=i)
        ts = [t for t in threads if t["date"] == d]
        lines = [(f"{d.strftime('%m/%d')}({wd[i]})" + (f" #{k + 1}" if len(ts) > 1 else "") + " 🔗", t["url"], False)
                 for k, t in enumerate(ts)] or [(f"{d.strftime('%m/%d')}({wd[i]})\n（投稿なし）", None, False)]
        days.append(rich_cell(lines))
    return [a, b, c, ideas, friction, focus, *days]


def _find_sheet(svc, ssid: str, title: str) -> int | None:
    meta = svc.spreadsheets().get(spreadsheetId=ssid, fields="sheets.properties").execute(num_retries=5)
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == title:
            return s["properties"]["sheetId"]
    return None


def _ensure_review_sheet(svc, ssid: str) -> int:
    gid = _find_sheet(svc, ssid, REVIEW_SHEET_NAME)
    if gid is not None:
        return gid
    resp = svc.spreadsheets().batchUpdate(spreadsheetId=ssid, body={"requests": [{"addSheet": {"properties": {
        "title": REVIEW_SHEET_NAME, "gridProperties": {"columnCount": len(REVIEW_HEADER), "frozenRowCount": 1}}}}]}
    ).execute(num_retries=5)
    gid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    reqs = [{"updateCells": {
        "start": {"sheetId": gid, "rowIndex": 0, "columnIndex": 0},
        "rows": [{"values": [{"userEnteredValue": {"stringValue": h},
                              "userEnteredFormat": {"textFormat": {"bold": True}, "wrapStrategy": "WRAP",
                                                    "backgroundColor": {"red": .93, "green": .93, "blue": .96}}}
                             for h in REVIEW_HEADER]}],
        "fields": "userEnteredValue,userEnteredFormat"}}]
    for i, wpx in enumerate(REVIEW_COL_WIDTHS):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": wpx}, "fields": "pixelSize"}})
    svc.spreadsheets().batchUpdate(spreadsheetId=ssid, body={"requests": reqs}).execute(num_retries=5)
    return gid


def write_review_row(creds, ssid: str, monday: datetime.date, run_date: datetime.date, review: dict,
                     image_url: str, threads: list[dict]) -> str:
    """同じ週(A列の週ID)が既にあればその行を上書き、無ければ2行目(ヘッダー直下)に挿入して
    新しい週を常に先頭へ。古い週はそのまま下に残る(スクロールで見返せる)。"""
    from googleapiclient.discovery import build

    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    gid = _ensure_review_sheet(svc, ssid)
    y, w, _ = monday.isocalendar()
    week_id = f"{y}-W{w:02d}"
    col_a = svc.spreadsheets().values().get(
        spreadsheetId=ssid, range=f"'{REVIEW_SHEET_NAME}'!A2:A").execute(num_retries=5).get("values", [])
    existing = next((i for i, r in enumerate(col_a) if r and r[0].strip() == week_id), None)
    reqs = []
    if existing is None:
        row_index = 1
        reqs.append({"insertDimension": {"range": {"sheetId": gid, "dimension": "ROWS", "startIndex": 1, "endIndex": 2},
                                         "inheritFromBefore": False}})
    else:
        row_index = existing + 1
    reqs.append({"updateCells": {
        "start": {"sheetId": gid, "rowIndex": row_index, "columnIndex": 0},
        "rows": [{"values": build_review_row(monday, run_date, review, image_url, threads)}],
        "fields": "userEnteredValue,userEnteredFormat,textFormatRuns"}})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": gid, "dimension": "ROWS", "startIndex": row_index, "endIndex": row_index + 1},
        "properties": {"pixelSize": REVIEW_IMAGE_ROW_HEIGHT}, "fields": "pixelSize"}})
    svc.spreadsheets().batchUpdate(spreadsheetId=ssid, body={"requests": reqs}).execute(num_retries=5)
    link = f"https://docs.google.com/spreadsheets/d/{ssid}/edit#gid={gid}"
    print(f"[OK] スプレッドシート「{REVIEW_SHEET_NAME}」{'上書き' if existing is not None else '先頭に追加'}: {week_id} / {link}")
    return link


# ===========================================================================
# 6) 週次バッチから呼ぶ入口
# ===========================================================================
def run_weekly(*, monday: datetime.date, token: str, channel_id: str, creds, api_key: str, model: str,
               dry_run: bool, out_dir_png: str = REVIEW_PNG_DIR, out_dir_html: str = "reports/weekly") -> dict:
    """1週間分のレビューを生成し(PNG/HTML)、dry_run でなければスプレッドシートに書く。
    例外は呼び出し側で握りつぶす前提だが、ここでも段階ごとに警告して継続する。"""
    sunday = monday + datetime.timedelta(days=6)
    y, w, _ = monday.isocalendar()
    tag = f"{y}_W{w:02d}"
    period = f"{monday.strftime('%Y/%m/%d')}〜{sunday.strftime('%Y/%m/%d')}"
    result: dict = {"tag": tag, "warnings": []}
    data = collect_threads(token, channel_id, monday, sunday)
    threads = data["threads"]
    review, source = analyze(threads, period, api_key, model)
    title = "モーニングジャーナル思考マップ"

    png_path = os.path.join(out_dir_png, f"{tag}.png")
    render_png(review, title, period, png_path)
    html_path = os.path.join(out_dir_html, f"{tag}_journal_mindmap.html")
    os.makedirs(out_dir_html, exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(build_html(review, f"{title}（{period}）", period, source, threads))
    print(f"[OK] HTML: {html_path}")
    result.update(png=png_path, html=html_path, review=review, source=source)

    ssid = os.environ.get("WEEKLY_SPREADSHEET_ID", "").strip()
    if dry_run:
        print("[INFO] dry-run: 週次レビューのスプレッドシート書き込みをスキップ")
    elif not ssid or creds is None:
        msg = "週次レビューのシート書き込みをスキップ（WEEKLY_SPREADSHEET_ID または Sheets 認証(サービスアカウント)が無効）"
        print(f"[WARN] {msg}")
        result["warnings"].append(msg)
    else:
        try:
            result["sheet_link"] = write_review_row(
                creds, ssid, monday, datetime.datetime.now(JST).date(), review,
                f"{REVIEW_PAGES_URL}{tag}.png", threads)
        except Exception as err:  # noqa: BLE001
            print(f"[WARN] 週次レビューのシート書き込みに失敗: {err}")
            result["warnings"].append("週次レビューのシート書き込みに失敗")
    return result


# ===========================================================================
# 7) CLI(単体実行・モック)
# ===========================================================================
def mock_data() -> tuple[dict, list[dict]]:
    g = "1540221086598299678"
    mon = datetime.date(2026, 9, 14)
    threads = [{"tid": str(1549066485345951774 + i), "name": (mon + datetime.timedelta(days=i)).strftime("%Y/%m/%d"),
                "date": mon + datetime.timedelta(days=i), "url": thread_url(g, str(1549066485345951774 + i)),
                "text": "モック"} for i in range(0, 7, 2)]
    u = [t["url"] for t in threads]
    review = {
        "mindmap": {
            "think": [{"label": "Botのボタン操作を日常の入口にする", "detail": "コピペをやめ1タップで完結", "url": u[0], "date": "09/14"},
                      {"label": "週次振り返りを資産化", "detail": "見返せる形式に", "url": u[1], "date": "09/16"},
                      {"label": "RSIをスマホで即確認", "detail": "", "url": u[2], "date": "09/18"}],
            "todo": [{"label": "スイング候補の確認を朝のルーティンに", "detail": "", "url": u[0], "date": "09/14"},
                     {"label": "ジャーナルURLの自動記録", "detail": "", "url": u[3], "date": "09/20"}],
            "feel": [{"label": "夜更かしで眠気が残る", "detail": "", "url": u[1], "date": "09/16"},
                     {"label": "動作確認できて達成感", "detail": "", "url": u[2], "date": "09/18"},
                     {"label": "食欲が戻ってきた", "detail": "", "url": "", "date": ""}],
            "hold": [{"label": "コピー方式の再挑戦は今は保留", "detail": "", "url": u[2], "date": "09/18"}],
        },
        "ideas": [{"text": "朝レポートにボタン付きパネルを常設する", "url": u[0]},
                  {"text": "週次レビューをスプレッドシートに蓄積する", "url": u[1]}],
        "friction": [{"noise": "スマホでコピーできず手入力に時間を取られた", "action": "ボタン方式へ切替えて解消", "url": u[2]},
                     {"noise": "夜遅くまで作業して睡眠不足", "action": "23時以降は新規作業をしないと割り切る", "url": u[1]}],
        "next_focus": "朝の10分でスイング候補とRSIを確認する習慣を作る",
    }
    return review, threads


def main() -> int:
    ap = argparse.ArgumentParser(description="モーニングジャーナル週次レビュー(PNG/HTML/Sheets)")
    ap.add_argument("--days", type=int, default=30, help="遡る日数(単体実行時。既定30日)")
    ap.add_argument("--html-out", default="", help="HTML出力先(1枚完結)")
    ap.add_argument("--png-out", default="", help="PNG出力先")
    ap.add_argument("--use-mock", action="store_true", help="通信せず合成データで生成")
    args = ap.parse_args()

    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    today = datetime.datetime.now(JST).date()
    start = today - datetime.timedelta(days=args.days - 1)
    period = f"{start.strftime('%Y/%m/%d')}〜{today.strftime('%Y/%m/%d')}"
    title = "モーニングジャーナル思考マップ"

    if args.use_mock:
        review, threads = mock_data()
        source, period = "gemini", "2026/09/14〜2026/09/20（モック）"
    else:
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        ch = (os.environ.get("DISCORD_CHANNEL_ID_MORNING_JOURNAL", "").strip()
              or os.environ.get("DISCORD_CHANNEL_ID_HEALTH", "").strip())
        if not token or not ch:
            print("[ERROR] DISCORD_BOT_TOKEN / DISCORD_CHANNEL_ID_MORNING_JOURNAL(or HEALTH) が必要です")
            return 1
        threads = collect_threads(token, ch, start, today)["threads"]
        review, source = analyze(threads, period, api_key, model)

    src_label = {"gemini": "Gemini構造化", "fallback": "簡易構造化(Gemini未使用)", "empty": "記録なし"}[source]
    if args.png_out:
        render_png(review, title, period, args.png_out)
    if args.html_out:
        os.makedirs(os.path.dirname(args.html_out) or ".", exist_ok=True)
        with open(args.html_out, "w", encoding="utf-8") as fh:
            fh.write(build_html(review, f"{title}（{period}）", period, src_label, threads))
        print(f"[OK] HTML: {args.html_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
