# -*- coding: utf-8 -*-
"""
精密スポーツ順位表・成績配信スクリプト (#webhook_sports_culture 向け)
「順位表・成績」「モータースポーツ」のうち、構造化データとして安定して
取得できる項目を実装する(バドミントンの世界ランキング付き試合結果や
日本人選手の怪我アラートは、信頼できる構造化ソースが見つかっていない
ため、別途調査してから実装する)。

対応データ:
  1. NPB順位表: https://npb.jp/bis/2026/stats/ (公式サイト、静的HTML)
  2. Jリーグ順位表(J1): https://www.jleague.jp/standings/j1/ (公式サイト、静的HTML)
  3. F1ドライバーズ・スタンディング上位3名:
     https://www.formula1.com/en/results/{year}/drivers (公式サイト、静的HTML)
  4. MotoGPライダーズ・スタンディング上位3名:
     https://www.motogp.com/en/world-standing/{year}/motogp/championship-standings
     (公式サイト。JS動的レンダリングのためPlaywrightが必要なことを確認済み)

MLBの順位表は、信頼できる公式無料ソースを別途調査してから追加する。

環境変数:
  DISCORD_WEBHOOK_SPORTS_CULTURE (必須)
"""
import datetime
import os
import sys

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

REQUEST_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

COLOR_NPB = 0x1A365D
COLOR_JLEAGUE = 0x2C7A7B
COLOR_F1 = 0xE10600
COLOR_MOTOGP = 0xCC0000


def current_year(now):
    return now.year


# ============================================================
# 1. NPB順位表
# ============================================================

def fetch_npb_standings(now):
    url = f"https://npb.jp/bis/{current_year(now)}/stats/"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] NPB順位表の取得に失敗しました: {err}")
        return None

    # npb.jpはContent-Typeヘッダーで文字コードを明記していないため、requestsが
    # ISO-8859-1に誤判定することがある(実データで確認済み)。apparent_encodingで補正する。
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 2:
        print("[WARN] NPB順位表のテーブル構造が想定と異なります。")
        return None

    def parse_table(table):
        rows = []
        for tr in table.find_all("tr")[1:]:  # 先頭行はヘッダー
            th = tr.find("th")
            if not th:
                continue
            team_span = th.select_one("span.hide_sp") or th
            team_name = team_span.get_text(strip=True)
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) >= 6:
                rows.append([team_name] + cells)
        return rows

    return {"central": parse_table(tables[0]), "pacific": parse_table(tables[1])}


def build_npb_embed(standings, now):
    if not standings:
        return None

    def format_league(rows):
        lines = []
        for i, row in enumerate(rows, start=1):
            # 想定カラム: [チーム名, 試合, 勝, 敗, 分, 勝率, 差]
            team, games, wins, losses, draws, pct, gb = row[:7]
            lines.append(f"{i}. {team} {wins}勝{losses}敗{draws}分 ({pct}) 差{gb}")
        return "\n".join(lines)

    now_jst = now.strftime("%Y-%m-%d")
    return {
        "title": "⚾ NPB順位表",
        "description": (
            f"**セ・リーグ**\n{format_league(standings['central'])}\n\n"
            f"**パ・リーグ**\n{format_league(standings['pacific'])}"
        ),
        "color": COLOR_NPB,
        "footer": {"text": f"NPB.jp 日本野球機構 / {now_jst} 時点"},
    }


# ============================================================
# 2. Jリーグ(J1)順位表
# ============================================================

def fetch_jleague_standings():
    url = "https://www.jleague.jp/standings/j1/"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Jリーグ順位表の取得に失敗しました: {err}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        print("[WARN] Jリーグ順位表のテーブルが見つかりませんでした。")
        return None

    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) >= 7:
            rows.append(cells)
    return rows


def build_jleague_embed(rows, now):
    if not rows:
        return None
    lines = []
    for row in rows[:18]:
        # 想定カラム: [順位, クラブ, 勝点, 試合数, 勝, 分, 負, 得点, 失点, 得失点, ...]
        pos, club, points = row[0], row[1], row[2]
        lines.append(f"{pos}. {club} 勝点{points}")
    now_jst = now.strftime("%Y-%m-%d")
    return {
        "title": "⚽ J1リーグ順位表",
        "description": "\n".join(lines),
        "color": COLOR_JLEAGUE,
        "footer": {"text": f"Jリーグ公式 / {now_jst} 時点"},
    }


# ============================================================
# 3. F1ドライバーズ・スタンディング(上位3名)
# ============================================================

def fetch_f1_top3(now):
    url = f"https://www.formula1.com/en/results/{current_year(now)}/drivers"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] F1ランキングの取得に失敗しました: {err}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("table tbody tr")
    results = []
    for tr in rows[:3]:
        name_spans = tr.select("span.max-lg\\:hidden, span.max-md\\:hidden")
        full_name = " ".join(s.get_text(strip=True) for s in name_spans if s.get_text(strip=True))
        team_link = tr.select_one("a[href*='/team/']")
        team = team_link.get_text(strip=True) if team_link else "-"
        if full_name:
            results.append({"name": full_name, "team": team})
    return results


def build_f1_embed(top3):
    if not top3:
        return None
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i]} {d['name']} ({d['team']})" for i, d in enumerate(top3)]
    return {
        "title": "🏎️ F1ドライバーズ・ランキング TOP3",
        "description": "\n".join(lines),
        "color": COLOR_F1,
    }


# ============================================================
# 4. MotoGPライダーズ・スタンディング(上位3名) — JS動的レンダリングのためPlaywright使用
# ============================================================

def fetch_motogp_top3(now):
    # このページはtable要素ではなく、div.standings-table__body-row を1行とする
    # JS動的レンダリングのグリッド構造であることを実際に検証して確認した。
    url = f"https://www.motogp.com/en/world-standing/{current_year(now)}/motogp/championship-standings"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=30000)
            rows = page.eval_on_selector_all(
                "div.standings-table__body-row",
                """els => els.slice(0, 3).map(row => {
                    const name = row.querySelector('.standings-table__body-cell--full-name');
                    const team = row.querySelector('[class*="team"]');
                    return {
                        name: name ? name.innerText.trim() : null,
                        team: team ? team.innerText.trim() : null,
                    };
                })"""
            )
            browser.close()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] MotoGPランキングの取得に失敗しました: {err}")
        return []

    return [r for r in rows if r.get("name") and r.get("team")]


def build_motogp_embed(top3):
    if not top3:
        return None
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i]} {d['name']} ({d['team']})" for i, d in enumerate(top3)]
    return {
        "title": "🏍️ MotoGPライダーズ・ランキング TOP3",
        "description": "\n".join(lines),
        "color": COLOR_MOTOGP,
    }


# ============================================================
# Discord送信
# ============================================================

def send_embeds_to_discord(webhook_url, embeds, batch_size=10):
    if not webhook_url:
        print("[ERROR] DISCORD_WEBHOOK_SPORTS_CULTURE が設定されていないため送信をスキップします。")
        return False
    ok = True
    for i in range(0, len(embeds), batch_size):
        batch = embeds[i:i + batch_size]
        try:
            resp = requests.post(webhook_url, json={"embeds": batch}, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 300:
                print(f"[ERROR] Discord送信に失敗しました(HTTP {resp.status_code}): {resp.text[:300]}")
                ok = False
            else:
                print(f"[OK] Discord送信成功(HTTP {resp.status_code}, {len(batch)}件)")
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] Discord送信中に例外が発生しました: {err}")
            ok = False
    return ok


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_SPORTS_CULTURE")
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_SPORTS_CULTURE が設定されていません。")
        sys.exit(1)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    embeds = []

    npb = fetch_npb_standings(now)
    npb_embed = build_npb_embed(npb, now)
    if npb_embed:
        embeds.append(npb_embed)

    jleague = fetch_jleague_standings()
    jleague_embed = build_jleague_embed(jleague, now)
    if jleague_embed:
        embeds.append(jleague_embed)

    f1_top3 = fetch_f1_top3(now)
    f1_embed = build_f1_embed(f1_top3)
    if f1_embed:
        embeds.append(f1_embed)

    motogp_top3 = fetch_motogp_top3(now)
    motogp_embed = build_motogp_embed(motogp_top3)
    if motogp_embed:
        embeds.append(motogp_embed)

    had_error = False
    if embeds:
        print(f"=== 配信内容: {len(embeds)}件のEmbed ===")
        if not send_embeds_to_discord(webhook, embeds):
            had_error = True
    else:
        print("[INFO] 配信対象がありませんでした(全ソースの取得に失敗した可能性があります)。")
        had_error = True

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
