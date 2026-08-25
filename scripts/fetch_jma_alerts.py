# -*- coding: utf-8 -*-
"""
JMA(気象庁)防災情報 監視・配信スクリプト (#webhook_local 向け)

対象地域: 北本市・鴻巣市・桶川市・川島町・吉見町・久喜市
  (6市町は気象庁の警報細分区域が3つ(南中部/北東部/北西部)に分かれるため、
   区域単位ではなく市区町村コード単位で個別に監視する)

対応データ:
  1. 気象警報・注意報 (大雨警報・洪水警報・大雨特別警報 等)
     https://www.jma.go.jp/bosai/warning/data/warning/110000.json (埼玉県)
     警報種別コードは気象庁「警報等情報要素コード管理表」
     (https://xml.kishou.go.jp/jmaxml_20220519_code.xls) を実際に取得して確認済み。
  2. 地震情報(震源・震度)
     https://www.jma.go.jp/bosai/quake/data/list.json
     対象6市町が震度情報に含まれる場合のみ通知する。

未対応(実装保留。理由をREADME的にここに明記する):
  - 土砂災害警戒情報: 通常の警報APIのwarnings配列には含まれず、
    「キキクル(危険度分布)」という別系統のタイル画像ベースの情報のため、
    テキストでの構造化取得には別途の実サイト調査が必要。
  - 落雷情報・ゲリラ豪雨アラート: JMAナウキャストは5分間隔のメッシュ画像
    (タイル)配信が中心で、テキスト/JSONでの構造化アラートが存在しないため、
    画像解析(Gemini等)を要する別実装が必要。

環境変数:
  DISCORD_WEBHOOK_LOCAL (必須)
"""
import datetime
import json
import os
import sys

import requests

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "jma_alerts_seen.json")
STATE_RETENTION_DAYS = 14
REQUEST_TIMEOUT = 15
DISCORD_CHUNK_LIMIT = 1900

SAITAMA_OFFICE_CODE = "110000"
QUAKE_LIST_URL = "https://www.jma.go.jp/bosai/quake/data/list.json"

# 対象6市町(気象庁 市区町村コード。 https://www.jma.go.jp/bosai/common/const/area.json で実際に確認済み)
TARGET_MUNICIPALITIES = {
    "1123300": "北本市",
    "1121700": "鴻巣市",
    "1123100": "桶川市",
    "1134600": "川島町",
    "1134700": "吉見町",
    "1123200": "久喜市",
}

# 気象庁「警報等情報要素コード管理表」(xml.kishou.go.jp/jmaxml_20220519_code.xls)
# code.WeatherWarning の「とりうる値」を実際に取得して転記した正式なコード表。
WARNING_CODE_NAMES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "高潮警報",
    "10": "大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "32": "暴風雪特別警報",
    "33": "大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "高潮特別警報",
}

# 緊急度に応じたEmbedの色分け
COLOR_SPECIAL_WARNING = 0x7B1FA2  # 特別警報: 紫(最重要)
COLOR_WARNING = 0xE53E3E          # 警報: 赤
COLOR_ADVISORY = 0xF6AD55         # 注意報: オレンジ
COLOR_CLEARED = 0x718096          # 解除: グレー
COLOR_QUAKE_STRONG = 0xE53E3E     # 震度5弱以上: 赤
COLOR_QUAKE_MODERATE = 0xF6AD55   # 震度3〜4: オレンジ
COLOR_QUAKE_WEAK = 0xECC94B       # 震度1〜2: 黄


def warning_severity(code):
    """コードから(緊急度ラベル, 色, 表示名)を返す。未知のコードは推測で埋めず、
    コード番号をそのまま表示する。"""
    name = WARNING_CODE_NAMES.get(code)
    if code == "00":
        return "解除", COLOR_CLEARED, "解除"
    if name is None:
        return "不明", COLOR_ADVISORY, f"未知のコード({code})"
    if "特別警報" in name:
        return "特別警報", COLOR_SPECIAL_WARNING, name
    if "警報" in name:
        return "警報", COLOR_WARNING, name
    return "注意報", COLOR_ADVISORY, name


def load_seen_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_seen_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def prune_old_quake_entries(state, now):
    cutoff = now - datetime.timedelta(days=STATE_RETENTION_DAYS)
    seen_quakes = state.get("quakes", {})
    pruned = {}
    for eid, iso_ts in seen_quakes.items():
        try:
            ts = datetime.datetime.fromisoformat(iso_ts)
        except ValueError:
            continue
        if ts >= cutoff:
            pruned[eid] = iso_ts
    state["quakes"] = pruned
    return state


# ============================================================
# 気象警報・注意報
# ============================================================

def fetch_current_warnings():
    """埼玉県全体の警報・注意報を取得し、対象6市町ぶんだけ抽出して返す。
    戻り値: { municipality_code: [ {code, name, severity} ... ] }
    """
    try:
        resp = requests.get(
            f"https://www.jma.go.jp/bosai/warning/data/warning/{SAITAMA_OFFICE_CODE}.json",
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] JMA警報APIの取得に失敗しました: {err}")
        return {}

    result = {}
    for area_type in data.get("areaTypes", []):
        for area in area_type.get("areas", []):
            code = area.get("code")
            if code not in TARGET_MUNICIPALITIES:
                continue
            active = []
            for w in area.get("warnings", []):
                wcode = w.get("code")
                status = w.get("status", "")
                if wcode is None:
                    continue
                if status in ("発表警報・注意報はなし",):
                    continue
                severity, color, name = warning_severity(wcode)
                active.append({
                    "code": wcode,
                    "name": name,
                    "severity": severity,
                    "color": color,
                    "status": status,
                })
            result[code] = active
    return result


def diff_new_warnings(current_warnings, state):
    """前回状態と比較し、新規に発表/解除された警報だけを返す。
    (毎回同じ内容を通知し続けないよう、状態が変化した時だけ通知する)
    """
    prev = state.get("warnings", {})
    new_events = []

    for muni_code, active_list in current_warnings.items():
        muni_name = TARGET_MUNICIPALITIES[muni_code]
        prev_codes = set(prev.get(muni_code, []))
        current_codes = {w["code"] for w in active_list}

        # 新規発表(前回無かったコードが今回ある)
        for w in active_list:
            if w["code"] not in prev_codes:
                new_events.append({"municipality": muni_name, **w})

        # 解除(前回あったコードが今回消えた)
        cleared_codes = prev_codes - current_codes
        for code in cleared_codes:
            severity, color, name = warning_severity(code)
            new_events.append({
                "municipality": muni_name, "code": code, "name": name,
                "severity": "解除", "color": COLOR_CLEARED, "status": "解除",
            })

    state["warnings"] = {
        muni_code: [w["code"] for w in active_list]
        for muni_code, active_list in current_warnings.items()
    }
    return new_events, state


def build_warning_embeds(new_events, now):
    if not new_events:
        return []
    now_jst = now.strftime("%Y-%m-%d %H:%M")
    embeds = []
    for ev in new_events:
        title = f"{'🟣' if ev['severity']=='特別警報' else '🔴' if ev['severity']=='警報' else '🟢' if ev['severity']=='解除' else '🟡'} {ev['municipality']}: {ev['name']}"
        if ev["severity"] != "解除":
            title += "（発表）"
        else:
            title += "（解除）"
        embeds.append({
            "title": title,
            "color": ev["color"],
            "footer": {"text": f"気象庁 埼玉県 / {now_jst} JST時点"},
        })
    return embeds


# ============================================================
# 地震情報
# ============================================================

QUAKE_NOTIFY_WINDOW_HOURS = 3  # この時間より古い地震は(初回実行時の大量バックフィル通知を防ぐため)通知しない


def fetch_target_earthquakes(state, now):
    try:
        resp = requests.get(QUAKE_LIST_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        quakes = resp.json()
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] JMA地震情報APIの取得に失敗しました: {err}")
        return []

    seen_quakes = state.setdefault("quakes", {})
    new_items = []
    cutoff = now - datetime.timedelta(hours=QUAKE_NOTIFY_WINDOW_HOURS)

    for q in quakes:
        eid = q.get("eid")
        if not eid or eid in seen_quakes:
            continue

        # 直近の地震以外は、通知はせず「既読」扱いにするだけにとどめる
        # (スクリプト初回実行時に過去920件分の通知が一気に飛ぶのを防ぐため)
        try:
            quake_at = datetime.datetime.fromisoformat(q.get("at", ""))
        except ValueError:
            quake_at = None
        if quake_at is None or quake_at < cutoff:
            seen_quakes[eid] = now.isoformat()
            continue

        hit_municipalities = []
        for pref in q.get("int", []):
            for city in pref.get("city", []):
                code = city.get("code")
                if code in TARGET_MUNICIPALITIES:
                    hit_municipalities.append((TARGET_MUNICIPALITIES[code], city.get("maxi")))

        if hit_municipalities:
            new_items.append({
                "eid": eid,
                "at": q.get("at"),
                "anm": q.get("anm"),
                "mag": q.get("mag"),
                "maxi": q.get("maxi"),
                "municipalities": hit_municipalities,
            })
        seen_quakes[eid] = now.isoformat()

    return new_items


def build_quake_embeds(new_quakes):
    embeds = []
    for q in new_quakes:
        try:
            maxi = int(q["maxi"])
        except (TypeError, ValueError):
            maxi = 0
        if maxi >= 5:
            color = COLOR_QUAKE_STRONG
        elif maxi >= 3:
            color = COLOR_QUAKE_MODERATE
        else:
            color = COLOR_QUAKE_WEAK

        muni_lines = "\n".join(f"- {name}: 震度{maxi_local}" for name, maxi_local in q["municipalities"])
        embeds.append({
            "title": f"🌏 地震情報: {q['anm']} (M{q['mag']} 最大震度{q['maxi']})",
            "description": f"発生時刻: {q['at']}\n\n**対象エリアの震度**\n{muni_lines}",
            "color": color,
        })
    return embeds


# ============================================================
# Discord送信
# ============================================================

def send_embeds_to_discord(webhook_url, embeds, batch_size=10):
    if not webhook_url:
        print("[ERROR] DISCORD_WEBHOOK_LOCAL が設定されていないため送信をスキップします。")
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


def send_test_alert(webhook):
    """実際にWebhookへ疎通するかを確認するためのダミー送信(--testオプション)。
    本番のstate(既送信記録)には一切触れない。"""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    dummy_warning_embed = build_warning_embeds([{
        "municipality": "北本市(テスト)", "code": "03", "name": "大雨警報",
        "severity": "警報", "color": COLOR_WARNING, "status": "テスト送信",
    }], now)
    dummy_quake_embed = build_quake_embeds([{
        "anm": "テスト震源(実在しません)", "mag": "0.0", "maxi": "1",
        "at": now.isoformat(),
        "municipalities": [("北本市(テスト)", "1")],
    }])
    ok = send_embeds_to_discord(webhook, dummy_warning_embed + dummy_quake_embed)
    print("[TEST] テスト送信結果:", "成功" if ok else "失敗")
    return ok


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_LOCAL")
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_LOCAL が設定されていません。")
        sys.exit(1)

    if "--test" in sys.argv:
        sys.exit(0 if send_test_alert(webhook) else 1)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    state = load_seen_state()
    state = prune_old_quake_entries(state, now)

    had_error = False
    all_embeds = []

    current_warnings = fetch_current_warnings()
    new_warning_events, state = diff_new_warnings(current_warnings, state)
    if new_warning_events:
        print(f"=== 警報・注意報 状態変化: {len(new_warning_events)}件 ===")
        for ev in new_warning_events:
            print(f"  {ev['municipality']}: {ev['name']} ({ev['status']})")
        all_embeds.extend(build_warning_embeds(new_warning_events, now))
    else:
        print("[INFO] 警報・注意報の状態変化はありませんでした。")

    new_quakes = fetch_target_earthquakes(state, now)
    if new_quakes:
        print(f"=== 対象エリアの新規地震情報: {len(new_quakes)}件 ===")
        for q in new_quakes:
            print(f"  {q['anm']} M{q['mag']} 最大震度{q['maxi']}")
        all_embeds.extend(build_quake_embeds(new_quakes))
    else:
        print("[INFO] 対象エリアに関する新規地震情報はありませんでした。")

    if all_embeds:
        if not send_embeds_to_discord(webhook, all_embeds):
            had_error = True
    else:
        print("[INFO] 配信対象の新着はありませんでした。")

    save_seen_state(state)

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
