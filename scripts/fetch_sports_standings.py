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

  ※MLB順位表は配信不要となったため削除した(旧: MLB公式Stats API連携)。

配信タイミング: 外部の試合日程/レースカレンダーには依存せず、各ソースの
取得結果を前回配信分のハッシュ値(state/sports_standings_snapshot.json)と
比較し、「前回から内容が変化した場合のみ」そのソースのEmbedを配信する。
NPB/Jリーグは順位・勝敗数が試合消化ごとに変わるため、プロ野球の月曜休み
やオフシーズンなど試合が無い日は自然にハッシュ不一致が発生せず配信され
ない。F1/MotoGPはTOP3の顔ぶれ・順番が変わらない限り配信されない
(ポイント数は取得対象外のため、TOP3の構成に変化が無いレースは検知対象外
という制約がある)。

環境変数:
  DISCORD_WEBHOOK_SPORTS_CULTURE (必須)
"""
import datetime
import hashlib
import json
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

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "sports_standings_snapshot.json")

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


def build_f1_embed(top3, now):
    if not top3:
        return None
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i]} {d['name']} ({d['team']})" for i, d in enumerate(top3)]
    now_jst = now.strftime("%Y-%m-%d")
    return {
        "title": "🏎️ F1ドライバーズ・ランキング TOP3",
        "description": "\n".join(lines),
        "color": COLOR_F1,
        "footer": {"text": f"Formula1.com / {now_jst} 時点"},
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


def build_motogp_embed(top3, now):
    if not top3:
        return None
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i]} {d['name']} ({d['team']})" for i, d in enumerate(top3)]
    now_jst = now.strftime("%Y-%m-%d")
    return {
        "title": "🏍️ MotoGPライダーズ・ランキング TOP3",
        "description": "\n".join(lines),
        "color": COLOR_MOTOGP,
        "footer": {"text": f"MotoGP.com / {now_jst} 時点"},
    }


# ============================================================
# Discord送信
# ============================================================

# ============================================================
# 配信要否の判定(前回配信時からの差分検知)
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
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def content_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_embed_if_changed(source_key, data, state, build_fn, now):
    """dataが前回配信時のスナップショット(stateのハッシュ)から変化していなければ
    Noneを返して配信をスキップする。変化していれば(または初回なら)Embedを構築し、
    stateのハッシュを今回分に更新する(実際の保存はmain側でsave_stateする)。
    """
    new_hash = content_hash(data)
    if state.get(source_key) == new_hash:
        print(f"[INFO] {source_key}: 前回配信時から変化なし(試合/レースの動きなし)。スキップします。")
        return None
    state[source_key] = new_hash
    return build_fn(data, now)


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
    state = load_state()
    embeds = []
    any_fetch_ok = False

    npb = fetch_npb_standings(now)
    if npb:
        any_fetch_ok = True
        embed = build_embed_if_changed("npb", npb, state, build_npb_embed, now)
        if embed:
            embeds.append(embed)

    jleague = fetch_jleague_standings()
    if jleague:
        any_fetch_ok = True
        embed = build_embed_if_changed("jleague", jleague, state, build_jleague_embed, now)
        if embed:
            embeds.append(embed)

    f1_top3 = fetch_f1_top3(now)
    if f1_top3:
        any_fetch_ok = True
        embed = build_embed_if_changed("f1", f1_top3, state, build_f1_embed, now)
        if embed:
            embeds.append(embed)

    motogp_top3 = fetch_motogp_top3(now)
    if motogp_top3:
        any_fetch_ok = True
        embed = build_embed_if_changed("motogp", motogp_top3, state, build_motogp_embed, now)
        if embed:
            embeds.append(embed)

    save_state(state)

    had_error = False
    if embeds:
        print(f"=== 配信内容: {len(embeds)}件のEmbed(前回から更新があったソースのみ) ===")
        if not send_embeds_to_discord(webhook, embeds):
            had_error = True
    elif any_fetch_ok:
        print("[INFO] 全ソースとも前回配信時から変化なし(試合/レースが無かったとみられるため)、今回は配信をスキップします。")
    else:
        print("[ERROR] 全ソースの取得に失敗しました。")
        had_error = True

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
