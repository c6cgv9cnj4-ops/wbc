# -*- coding: utf-8 -*-
"""
週次観測の「本人確認(○△×)」の回答形式・保存(2026-09-29, Acceptance Test 用の最小実装)。

データの4分類(混ぜない):
  A 生データ          … Discord #モーニングジャーナル(GitHub には置かない)
  B Python の事実     … スプレッドシート「週次観測」タブ
  C AI の仮説         … 「仮説確認」タブの「AI仮説(C)」列
  D 本人の確認結果    … 「仮説確認」タブの「本人回答(D)」「本人コメント(D)」「回答日時(D)」列
  × も「どれも違う」も消さずに残す(AI 仮説が外れた履歴)。未回答は「未回答」(=保留)のまま。

回答の場所: #週刊まとめ に届いた週次レビューへの本人の返信(同じチャンネルへの普通の投稿でも可)。
  1 ○ / 2 △ / 3 × / どれも違う / 別のこと：〇〇 / 見えた / 見えない
  月次の仮説は M1 ○ のように M を付ける。コメントは記号の後ろに任意で書ける。
  次の週次レビューを作る直前に、前回のレビュー以降の本人の投稿を読んで反映する。
  未回答を催促・再送はしない。未回答を否定として扱わない。
"""
from __future__ import annotations

import datetime
import re

CONFIRM_SHEET_NAME = "仮説確認"
CONFIRM_HEADER = ["ID", "対象期間", "種別", "区分", "AI仮説(C)", "根拠(観測ID)",
                  "本人回答(D)", "本人コメント(D)", "回答日時(D)", "生成日時"]
UNANSWERED = "未回答"
ANSWER_VALUES = ("○", "△", "×", "どれも違う", "別のこと", "見えた", "見えない", UNANSWERED)

_SYMBOL = {"○": "○", "◯": "○", "〇": "○", "o": "○", "O": "○",
           "△": "△", "▲": "△",
           "×": "×", "x": "×", "X": "×", "✕": "×", "✖": "×"}
_LINE_HYP = re.compile(r"^\s*(M?\d{1,2})\s*[.．、:：)）]?\s*([○◯〇oO△▲×xX✕✖])(?![A-Za-z])\s*[:：、,，]?\s*(.*)$")
_LINE_ALL = re.compile(r"^\s*(?:\d{1,2}\s*)?(どれも違う|別のこと)\s*[:：、,，]?\s*(.*)$")
_LINE_VIEW = re.compile(r"^\s*(見えた|見えない)\s*[:：、,，]?\s*(.*)$")


def answer_guide(n_hyp: int, prefix: str = "", with_view: bool = True) -> list[str]:
    """Discord の週次レビューに付ける回答方法(短く・任意であることを明記)。"""
    if not n_hyp and not with_view:
        return []
    lines = ["", "**▼ 返信で答える**（任意。答えなくてOK＝保留。催促はしません）"]
    if n_hyp:
        ex = " / ".join(f"`{prefix}{i} {s}`" for i, s in zip(range(1, n_hyp + 1), ("○", "△", "×", "○")))
        lines.append(f"・仮説: {ex}（○合っている △一部 ×違う。記号の後ろにコメント任意）")
        lines.append("・全体: `どれも違う` / `別のこと：〇〇`")
    if with_view:
        lines.append("・読んで自分の変化が見えたか: `見えた` / `見えない`")
    return lines


def parse_replies(texts: list[str]) -> dict[str, tuple[str, str]]:
    """本人の返信(古い順)から回答を取り出す。同じ項目は後の回答で上書き。
    返り値: {"1": ("○", "コメント"), "M2": ("×", ""), "ALL": ("別のこと", "〇〇"), "VIEW": ("見えた", "")}"""
    out: dict[str, tuple[str, str]] = {}
    for text in texts:
        for ln in (text or "").splitlines():
            m = _LINE_VIEW.match(ln)
            if m:
                out["VIEW"] = (m.group(1), m.group(2).strip())
                continue
            m = _LINE_ALL.match(ln)
            if m:
                out["ALL"] = (m.group(1), m.group(2).strip())
                continue
            m = _LINE_HYP.match(ln)
            if m:
                out[m.group(1).upper()] = (_SYMBOL[m.group(2)], m.group(3).strip())
    return out


def build_rows(period: str, kind: str, interp: dict | None, generated_at: str,
               prefix: str = "", with_view: bool = True) -> list[list]:
    """1回分(週次 or 月次)の行。仮説が無くても「全体」「読後」の行は作る(回答の受け皿)。"""
    rows = []
    for i, h in enumerate((interp or {}).get("hypotheses") or [], 1):
        rows.append([f"{period}-{prefix}{i}", period, kind, "仮説", h["text"], ",".join(h["refs"]),
                     UNANSWERED, "", "", generated_at])
    if kind == "週次":
        rows.append([f"{period}-ALL", period, kind, "全体", "（仮説全体への回答: どれも違う／別のこと）", "",
                     UNANSWERED, "", "", generated_at])
        if with_view:
            rows.append([f"{period}-VIEW", period, kind, "読後", "（この週次レビューで自分の変化が見えたか）", "",
                         UNANSWERED, "", "", generated_at])
    return rows


def merge_rows(existing: list[list], new_rows: list[list]) -> list[list]:
    """同じ対象期間を再実行したとき: その期間にまだ1件も回答が無ければ新しい行に置き換える。
    回答済みの行がある期間は、仮説(C)と回答(D)の対応が崩れないよう既存を残す。"""
    periods = {(r[1], r[2]) for r in new_rows}
    keep, replace = [], True
    for r in existing:
        if (r[1], r[2]) in periods and len(r) > 6 and r[6] not in ("", UNANSWERED):
            replace = False
    for r in existing:
        if replace and (r[1], r[2]) in periods:
            continue
        keep.append(r)
    return keep + (new_rows if replace else [])


def apply_answers(rows: list[list], answers: dict[str, tuple[str, str]], answered_at: str,
                  batch_generated_at: str) -> int:
    """直前の回(生成日時が batch_generated_at の行)に回答を書く。D 列だけ更新。更新件数を返す。"""
    n = 0
    for r in rows:
        if len(r) < 10 or r[9] != batch_generated_at:
            continue
        key = r[0].rsplit("-", 1)[-1].upper()
        if key in answers:
            val, comment = answers[key]
            r[6], r[7], r[8] = val, comment, answered_at
            n += 1
    return n


def latest_batch(rows: list[list], exclude_period: str) -> str | None:
    """今回の対象期間を除いた、最新の生成日時(=直前のレビュー)。"""
    times = [r[9] for r in rows if len(r) >= 10 and r[1] != exclude_period and r[9]]
    return max(times) if times else None


# ---------------------------------------------------------------------------
# 外部 I/O(Discord / Sheets)。呼び出し側で例外を握り、週次の投稿は止めない
# ---------------------------------------------------------------------------
def fetch_owner_texts(get_json, channel_id: str, since_iso: str) -> list[str]:
    """#週刊まとめ の since_iso 以降の本人(Bot・Webhook 以外)の投稿本文を古い順に返す。
    get_json(path, params) は Discord GET の関数(認証は呼び出し側)。本文はログに出さない。"""
    since = datetime.datetime.fromisoformat(since_iso)
    msgs, before = [], None
    for _ in range(10):
        params = {"limit": 100}
        if before:
            params["before"] = before
        batch = get_json(f"/channels/{channel_id}/messages", params) or []
        if not batch:
            break
        msgs.extend(batch)
        before = batch[-1]["id"]
        if datetime.datetime.fromisoformat(batch[-1]["timestamp"]) < since or len(batch) < 100:
            break
    own = [m for m in msgs if not (m.get("author") or {}).get("bot") and not m.get("webhook_id")
           and datetime.datetime.fromisoformat(m["timestamp"]) > since]
    own.sort(key=lambda m: int(m["id"]))
    return [m.get("content") or "" for m in own]


def read_rows(svc, ssid: str) -> list[list]:
    vals = svc.spreadsheets().values().get(
        spreadsheetId=ssid, range=f"'{CONFIRM_SHEET_NAME}'!A2:J").execute(num_retries=5).get("values", [])
    return [r + [""] * (10 - len(r)) for r in vals]


def ensure_sheet(svc, ssid: str) -> None:
    meta = svc.spreadsheets().get(spreadsheetId=ssid, fields="sheets(properties(title))").execute(num_retries=5)
    if any(s["properties"]["title"] == CONFIRM_SHEET_NAME for s in meta.get("sheets", [])):
        return
    svc.spreadsheets().batchUpdate(spreadsheetId=ssid, body={"requests": [{"addSheet": {"properties": {
        "title": CONFIRM_SHEET_NAME, "gridProperties": {"frozenRowCount": 1}}}}]}).execute(num_retries=5)
    svc.spreadsheets().values().update(
        spreadsheetId=ssid, range=f"'{CONFIRM_SHEET_NAME}'!A1", valueInputOption="RAW",
        body={"values": [CONFIRM_HEADER]}).execute(num_retries=5)


def write_rows(svc, ssid: str, rows: list[list]) -> None:
    svc.spreadsheets().values().clear(
        spreadsheetId=ssid, range=f"'{CONFIRM_SHEET_NAME}'!A2:J").execute(num_retries=5)
    if rows:
        svc.spreadsheets().values().update(
            spreadsheetId=ssid, range=f"'{CONFIRM_SHEET_NAME}'!A2", valueInputOption="RAW",
            body={"values": rows}).execute(num_retries=5)


def summarize(rows: list[list]) -> str:
    """公開ログ向け: 件数だけ(仮説本文・コメントは出さない)。"""
    from collections import Counter
    c = Counter(r[6] or UNANSWERED for r in rows)
    return " ".join(f"{k}={c.get(k, 0)}" for k in ANSWER_VALUES)
