# -*- coding: utf-8 -*-
"""
NPB(プロ野球) 当日の試合結果配信 (#webhook_sports_culture 向け)

情報源: NPB.jp 日本野球機構オフィシャルサイト(静的HTML。2026-08時点の実データで
構造を確認済み)。Playwright不要・requests + BeautifulSoup のみで完結する。

  1. 日程・結果一覧:
     https://npb.jp/games/{year}/schedule_{MM}_detail.html
       - <tr id="date{MMDD}"> が1試合。 .team1/.score1/.state/.score2/.team2、
         試合ページへのリンク(/scores/{year}/{MMDD}/{slug}/)、.pit(勝敗投手)を含む。
       - 中止は行内に <div class="cancel">中止</div> (スコアのdivが出ない)。
       - 未消化試合は .score が &nbsp; でスコア無し。

  2. 各試合トップ: https://npb.jp/scores/{year}/{MMDD}/{slug}/
       - <th>【勝投手】</th><td>名前（今季成績）</td> の形で 勝投手/敗投手/セーブ。
       - 「本塁打」テーブル(チーム別の本塁打リスト)。

  3. 各試合ボックススコア: https://npb.jp/scores/{year}/{MMDD}/{slug}/box.html
       - #tablefix_ls: 回別得点表。 tr.top=先攻(ビジター) / tr.bottom=後攻(ホーム)、
         td.total-1=計(得点) / td.total-2=H,E。
       - 投手成績テーブル: 各行の先頭セルの記号 ○=勝 ●=敗 H=ホールド S=セーブ。

取得・通知項目(要件):
  - 対戦カードと最終スコア        … box.html #tablefix_ls (無ければ一覧のスコア)
  - 勝ち投手 / 負け投手 / セーブ  … 試合トップの【勝投手】【敗投手】【セーブ】
                                   (無ければ box.html の記号 ○/●/S から補完)
  - ホールド                      … box.html 投手成績テーブルの記号 "H"

  ※「勝利打点」はNPBが1988年限りで廃止した記録で、公式・主要サイトいずれも
    構造化データを持たず常に取得不可だったため、出力項目自体を削除した
    (2026-08-30、細川さんの指示)。

配信仕様:
  - 試合があった日のみ結果を配信する。
  - 全試合中止/カード無しの日は「本日の試合はありません(または中止)」を1本配信。
  - まだ結果が出そろっていない時間帯(夜22時より前)に結果ゼロなら、静かに終了して
    何も送らない(手動テスト実行でノイズを出さないため)。
  - 同一日を二重配信しないよう state/npb_results_seen.json に
    {"YYYY-MM-DD": {"hash": ..., "finals": 確定試合数}} を記録する。
    未記録の日、または前回より確定試合数が増えた場合のみ(再)配信する。

環境変数:
  DISCORD_WEBHOOK_SPORTS_CULTURE (必須)

使い方:
  python scripts/fetch_npb_results.py                # 本番(当日JST)
  python scripts/fetch_npb_results.py --date 2026-08-29
  python scripts/fetch_npb_results.py --date 2026-08-29 --dry-run   # 送信も記録もしない
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

JST = datetime.timezone(datetime.timedelta(hours=9))
REQUEST_TIMEOUT = 20
MAX_RETRY = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "npb_results_seen.json")
STATE_RETENTION = 14  # 直近何日分の配信記録を残すか

COLOR_NPB = 0x1A365D
COLOR_INFO = 0x8E8E93

# 一覧・ボックスで表記ゆれのあるチーム短縮名 -> 表示名(統一)
TEAM_CANON = {
    "巨人": "巨人", "ＤｅＮＡ": "DeNA", "DeNA": "DeNA", "阪神": "阪神",
    "広島": "広島", "中日": "中日", "ヤクルト": "ヤクルト",
    "ソフトバンク": "ソフトバンク", "日本ハム": "日本ハム", "ロッテ": "ロッテ",
    "楽天": "楽天", "西武": "西武", "オリックス": "オリックス",
}


# ============================================================
# HTTP
# ============================================================

def fetch(url, allow_404=False):
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404 and allow_404:
                return None
            resp.raise_for_status()
            # npb.jp はContent-Typeに文字コードを書かないことがあり、requestsが
            # ISO-8859-1へ誤判定する。apparent_encodingで補正する。
            if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "ascii"):
                resp.encoding = resp.apparent_encoding
            return resp.text
        except Exception as err:  # noqa: BLE001
            last_err = err
            if attempt < MAX_RETRY:
                time.sleep(2 * attempt)
    print(f"[WARN] 取得失敗({url}): {last_err}")
    return None


# ============================================================
# 1. 日程・結果一覧
# ============================================================

def _num_or_none(text):
    text = (text or "").strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return int(text) if text.isdigit() else None


def canon_team(name):
    name = (name or "").strip()
    return TEAM_CANON.get(name, name)


def parse_schedule_day(year, month, mmdd):
    """指定日の全試合を dict のリストで返す。カードが無ければ空リスト。"""
    url = f"https://npb.jp/games/{year}/schedule_{month:02d}_detail.html"
    html = fetch(url)
    if not html:
        return None  # 取得自体に失敗(呼び出し側でエラー扱い)

    soup = BeautifulSoup(html, "html.parser")
    games = []
    for tr in soup.find_all("tr", id=f"date{mmdd}"):
        t1 = tr.select_one(".team1")
        t2 = tr.select_one(".team2")
        if not t1 or not t2:
            continue
        # NPB.jpの日程表は team1=ホーム / team2=ビジター の順(球場名で検証済み)。
        home = canon_team(t1.get_text(strip=True))
        away = canon_team(t2.get_text(strip=True))

        cancelled = bool(tr.select_one(".cancel")) or "中止" in tr.get_text()
        s1 = _num_or_none(tr.select_one(".score1").get_text() if tr.select_one(".score1") else None)
        s2 = _num_or_none(tr.select_one(".score2").get_text() if tr.select_one(".score2") else None)
        home_score, away_score = s1, s2

        link = tr.select_one("a[href*='/scores/']")
        slug = None
        if link and link.get("href"):
            m = re.search(r"/scores/\d{4}/\d{4}/([a-z0-9\-]+)/", link["href"])
            slug = m.group(1) if m else None

        place = tr.select_one(".place")
        gtime = tr.select_one(".time")
        pit = [re.sub(r"\s+", "", d.get_text()) for d in tr.select(".pit") if d.get_text(strip=True)]
        win_hint = lose_hint = None
        for p in pit:
            if p.startswith("勝："):
                win_hint = p[2:]
            elif p.startswith("敗："):
                lose_hint = p[2:]

        # NPB.jpの日程表は「試合終了後」に勝敗投手(.pit)を表示する。得点が
        # 入っていても .pit が無い間は試合中とみなし、確定扱いしない。
        if cancelled:
            status = "cancelled"
        elif s1 is not None and s2 is not None and (win_hint or lose_hint):
            status = "final"
        else:
            status = "scheduled"

        games.append({
            "away": away, "home": home,
            "away_score": away_score, "home_score": home_score,
            "status": status, "slug": slug,
            "place": place.get_text(strip=True).replace("　", "") if place else "",
            "time": gtime.get_text(strip=True) if gtime else "",
            "win_hint": win_hint, "lose_hint": lose_hint,
        })
    return games


# ============================================================
# 2 & 3. 各試合の詳細(勝敗S・ホールド・本塁打・回別得点)
# ============================================================

def _split_name_record(text):
    """'平良（5勝8敗）' -> ('平良', '5勝8敗') / '髙島（5勝4敗）' 等。"""
    text = re.sub(r"\s+", "", text or "")
    m = re.match(r"^(.+?)[（(](.+?)[）)]$", text)
    if m:
        return m.group(1), m.group(2)
    return (text or None), None


def parse_game_index(html):
    """試合トップページから 勝投手/敗投手/セーブ と 本塁打リストを取り出す。"""
    out = {"win": None, "win_rec": None, "lose": None, "lose_rec": None,
           "save": None, "save_rec": None, "hr_lines": []}
    if not html:
        return out

    for label, value in re.findall(r"<th>【([^】]+)】</th>\s*<td>(.*?)</td>", html, re.S):
        plain = re.sub(r"<[^>]+>", "", value)
        plain = re.sub(r"[ \t\r\n　]+", "", plain).strip()
        if not plain:
            continue
        name, rec = _split_name_record(plain)
        if label == "勝投手":
            out["win"], out["win_rec"] = name, rec
        elif label == "敗投手":
            out["lose"], out["lose_rec"] = name, rec
        elif label == "セーブ":
            out["save"], out["save_rec"] = name, rec

    # 本塁打テーブル: <h4>本塁打</h4> の直後の <table>
    m = re.search(r"<h4>本塁打</h4>\s*<table>(.*?)</table>", html, re.S)
    if m:
        for tr in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S):
            th = re.search(r"<th>【?([^】<]+)】?</th>", tr)
            td = re.search(r"<td>(.*?)</td>", tr, re.S)
            if not th or not td:
                continue
            team = th.group(1).strip()
            body = re.sub(r"<[^>]+>", "", td.group(1))
            body = re.sub(r"[ \t\r\n]+", " ", body).strip()
            if body:
                out["hr_lines"].append(f"[{canon_team(team)}] {body}")
    return out


def parse_box(html):
    """box.html から回別得点(最終スコア)と、投手記号(○●HS)を取り出す。"""
    out = {"away": None, "away_score": None, "home": None, "home_score": None,
           "win": None, "lose": None, "saves": [], "holds": []}
    if not html:
        return out
    soup = BeautifulSoup(html, "html.parser")

    ls = soup.select_one("#tablefix_ls")
    if ls:
        top = ls.select_one("tbody tr.top")
        bottom = ls.select_one("tbody tr.bottom")

        def _read(row):
            if not row:
                return None, None
            th = row.find("th")
            short = None
            if th:
                sp = th.select_one(".hide_pc") or th.select_one(".hide_sp") or th
                short = sp.get_text(strip=True)
            total = row.select_one("td.total-1")
            return canon_team(short), _num_or_none(total.get_text() if total else None)

        out["away"], out["away_score"] = _read(top)
        out["home"], out["home_score"] = _read(bottom)

    # 投手成績テーブル(先攻・後攻の2つ)。ヘッダに「投手」「投球回」を含むtable。
    for tbl in soup.find_all("table"):
        head = tbl.find("tr")
        if not head:
            continue
        ths = [x.get_text(strip=True) for x in head.find_all("th")]
        if "投手" not in ths or "投球回" not in ths:
            continue
        body = tbl.find("tbody") or tbl
        for tr in body.find_all("tr", recursive=False):
            cells = tr.find_all("td", recursive=False)
            player = tr.find("td", class_="player")
            if not player or not cells:
                continue
            mark = cells[0].get_text(strip=True)
            name = player.get_text(strip=True)
            if "○" in mark:
                out["win"] = name
            if "●" in mark:
                out["lose"] = name
            if "S" in mark or "Ｓ" in mark:
                out["saves"].append(name)
            if "H" in mark or "Ｈ" in mark:
                out["holds"].append(name)
    return out


def fetch_game_detail(year, mmdd, slug):
    base = f"https://npb.jp/scores/{year}/{mmdd}/{slug}/"
    idx = parse_game_index(fetch(base, allow_404=True))
    box = parse_box(fetch(base + "box.html", allow_404=True))

    win = idx["win"] or box["win"]
    lose = idx["lose"] or box["lose"]
    save = idx["save"] or (box["saves"][0] if box["saves"] else None)
    return {
        "win": win, "win_rec": idx["win_rec"],
        "lose": lose, "lose_rec": idx["lose_rec"],
        "save": save, "save_rec": idx["save_rec"],
        "holds": box["holds"],
        "hr_lines": idx["hr_lines"],
        "box_away": box["away"], "box_away_score": box["away_score"],
        "box_home": box["home"], "box_home_score": box["home_score"],
    }


# ============================================================
# Embed 構築
# ============================================================

def _pit(name, rec):
    if not name:
        return "—"
    return f"{name}（{rec}）" if rec else name


def build_game_embed(game, detail, date_label):
    # ホーム/アウェイの別は日程表(球場で検証済み)を正とし、スコアはbox優先。
    home = game["home"] or detail.get("box_home")
    away = game["away"] or detail.get("box_away")
    hsc = detail.get("box_home_score")
    asc = detail.get("box_away_score")
    if hsc is None:
        hsc = game["home_score"]
    if asc is None:
        asc = game["away_score"]

    def _side(score, other, label):
        if score is not None and other is not None and score > other:
            return f"**{label}**"
        return label

    # 表示はNPB.jpに合わせてホーム先頭
    score_line = f"{_side(hsc, asc, f'{home} {hsc}')}　-　{_side(asc, hsc, f'{asc} {away}')}"

    lines = [
        score_line,
        f"🏟 {game['place'] or '球場未確認'}",
        f"✅ 勝投手: {_pit(detail['win'], detail['win_rec'])}",
        f"❌ 敗投手: {_pit(detail['lose'], detail['lose_rec'])}",
        f"🔒 セーブ: {_pit(detail['save'], detail['save_rec'])}",
        f"🤝 ホールド: {('・'.join(detail['holds'])) if detail['holds'] else '—'}",
    ]
    if detail["hr_lines"]:
        lines.append("💣 本塁打:")
        lines.extend(f"　{h}" for h in detail["hr_lines"])

    return {
        "title": f"⚾ {home} {hsc} - {asc} {away}",
        "description": "\n".join(lines),
        "color": COLOR_NPB,
        "footer": {"text": f"NPB.jp 日本野球機構 / {date_label} 結果"},
    }


def build_notice_embed(title, body, date_label):
    return {
        "title": title,
        "description": body,
        "color": COLOR_INFO,
        "footer": {"text": f"NPB.jp 日本野球機構 / {date_label}"},
    }


# ============================================================
# 配信要否の判定(state)
# ============================================================

def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state):
    # 古い日付の記録を間引く
    for key in sorted(state.keys())[:-STATE_RETENTION]:
        del state[key]
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def payload_hash(games, details):
    basis = []
    for g, d in zip(games, details):
        basis.append({
            "c": f"{g['away']}-{g['home']}",
            "s": f"{g['away_score']}-{g['home_score']}",
            "w": d.get("win"), "l": d.get("lose"),
            "sv": d.get("save"), "h": sorted(d.get("holds") or []),
        })
    return hashlib.sha256(
        json.dumps(basis, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# ============================================================
# Discord
# ============================================================

def send_to_discord(webhook_url, content, embeds):
    ok = True
    for i in range(0, max(1, len(embeds)), 10):
        batch = embeds[i:i + 10]
        payload = {"embeds": batch, "allowed_mentions": {"parse": []}}
        if i == 0 and content:
            payload["content"] = content
        try:
            resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 300:
                print(f"[ERROR] Discord送信失敗(HTTP {resp.status_code}): {resp.text[:300]}")
                ok = False
            else:
                print(f"[OK] Discord送信(HTTP {resp.status_code}, embed {len(batch)}件)")
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] Discord送信中に例外: {err}")
            ok = False
        time.sleep(1)
    return ok


# ============================================================
# main
# ============================================================

def resolve_target_date(now, arg):
    if arg:
        return datetime.datetime.strptime(arg, "%Y-%m-%d").date()
    # 深夜〜早朝の手動実行は前日を対象にする(夜間cronでは常に当日)
    if now.hour < 6:
        return (now - datetime.timedelta(days=1)).date()
    return now.date()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="対象日 YYYY-MM-DD (省略時は当日JST)")
    parser.add_argument("--dry-run", action="store_true", help="送信もstate更新もしない")
    args = parser.parse_args()

    webhook = os.environ.get("DISCORD_WEBHOOK_SPORTS_CULTURE")
    if not webhook and not args.dry_run:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_SPORTS_CULTURE が未設定です。")
        sys.exit(1)

    now = datetime.datetime.now(JST)
    target = resolve_target_date(now, args.date)
    date_str = target.strftime("%Y-%m-%d")
    mmdd = target.strftime("%m%d")
    jp_date = target.strftime("%-m/%-d") + f"（{'月火水木金土日'[target.weekday()]}）"
    print(f"=== NPB試合結果: 対象日 {date_str} ({jp_date}) ===")

    games = parse_schedule_day(target.year, target.month, mmdd)
    if games is None:
        print("[ERROR] 日程ページの取得に失敗しました。")
        sys.exit(1)

    finals = [g for g in games if g["status"] == "final"]
    cancelled = [g for g in games if g["status"] == "cancelled"]
    scheduled = [g for g in games if g["status"] == "scheduled"]
    print(f"  カード数={len(games)} 確定={len(finals)} 中止={len(cancelled)} 未消化={len(scheduled)}")

    state = load_state()
    prev = state.get(date_str) or {}

    # ---- 送信内容の決定 ----
    content = None
    embeds = []
    new_record = None

    if finals:
        details = [fetch_game_detail(target.year, mmdd, g["slug"]) if g["slug"] else {}
                   for g in finals]
        h = payload_hash(finals, details)
        if not args.dry_run and prev.get("hash") == h:
            print("[INFO] 前回配信から結果に変化なし。スキップします。")
            return
        if not args.dry_run and prev.get("finals", 0) >= len(finals) and prev.get("hash") != h:
            # 試合数は同じで中身だけ変化 = 軽微な訂正。二重配信を避けてstateだけ更新。
            print("[INFO] 確定試合数は変わらず内容のみ差分。配信は控えてstateを更新します。")
            state[date_str] = {"hash": h, "finals": len(finals)}
            save_state(state)
            return

        content = f"⚾ **NPB 本日の試合結果 {jp_date}** — {len(finals)}試合"
        for g, d in zip(finals, details):
            embeds.append(build_game_embed(g, d, jp_date))
        if cancelled:
            cx = "、".join(f"{g['away']}-{g['home']}" for g in cancelled)
            embeds.append(build_notice_embed("☔ 中止", f"{cx} は中止でした。", jp_date))
        new_record = {"hash": h, "finals": len(finals)}

    elif games and cancelled and not scheduled:
        if not args.dry_run and prev.get("kind") == "all_cancelled":
            print("[INFO] 全試合中止は配信済み。スキップします。")
            return
        cx = "、".join(f"{g['away']}-{g['home']}" for g in cancelled)
        content = f"⚾ **NPB {jp_date}**"
        embeds.append(build_notice_embed(
            "☔ 本日の試合はすべて中止",
            f"本日組まれていた{len(cancelled)}カード（{cx}）はすべて中止でした。",
            jp_date))
        new_record = {"kind": "all_cancelled"}

    elif not games:
        if not args.dry_run and prev.get("kind") == "no_games":
            print("[INFO] 『試合なし』は配信済み。スキップします。")
            return
        content = f"⚾ **NPB {jp_date}**"
        embeds.append(build_notice_embed(
            "🗓 本日はNPBの試合はありません",
            "本日は公式戦のカードが組まれていません（移動日・オフなど）。", jp_date))
        new_record = {"kind": "no_games"}

    else:
        # 未消化のみ(結果待ち)。夜遅い時間帯以外はノイズ回避のため沈黙する。
        if now.hour >= 22 or args.date:
            content = f"⚾ **NPB {jp_date}**"
            embeds.append(build_notice_embed(
                "⏳ 試合結果がまだ確定していません",
                f"{len(scheduled)}試合が進行中/開始前です。夜間の再実行で結果を配信します。",
                jp_date))
        else:
            print("[INFO] まだ結果が出そろっていない時間帯のため、今回は何も送りません。")
            return

    # ---- 送信 ----
    if args.dry_run:
        print("---- DRY RUN: 送信内容 ----")
        print("content:", content)
        print(json.dumps(embeds, ensure_ascii=False, indent=2))
        return

    if not send_to_discord(webhook, content, embeds):
        sys.exit(1)

    if new_record is not None:
        state[date_str] = new_record
        save_state(state)


if __name__ == "__main__":
    main()
