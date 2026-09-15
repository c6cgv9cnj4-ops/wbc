# -*- coding: utf-8 -*-
"""
【週末撮影予報】見頃先回り通知バッチ

photo/index.html（GANREF関東 撮影コーディネーター）に埋め込まれている
撮影スポットデータ（rawSpots）を参照し、実行日（毎週木曜 朝7:00 JST）を
起点に「現在見頃」または「直近1〜2週間以内に見頃を迎える」関東近郊スポットを
抽出してDiscordへ通知する。各スポットにはタップ1回で予定登録できる
Googleカレンダー登録リンクを付与する。

見頃判定は photo/index.html 本体と同じ floraGantt（月ごとの1/0の12要素配列。
月選択フィルタ selectedMonth と同じ判定式 floraGantt[m]===1 を踏襲）を一次情報とする。
被写体名・見頃時期の文言は mainSubject の説明文（例:
「ヒガンバナ（曼珠沙華・9-10月）」）から正規表現で抽出する。
所要時間（車／公共交通）は photo/index.html の calcCarAccess / calcTrainMinutes と
同じ計算式で概算する（アプリ本体の表示値との整合を優先し、独自の推測値は使わない）。

Googleカレンダー登録URLは日本語の被写体名・住所・詳細メモをpercent-encodingで
含むため非常に長くなり、Discord embedの1フィールド上限(1024文字)・embed合計
上限(6000文字)を容易に超過する。対策として、(1) detailsは型番・数値中心の
簡潔な表記にする、(2) それでも長い場合は段階的に被写体名・住所を短縮し、
最終的にはdetails自体を省略する（title/日付/場所のみの予定として作成される。
URLを文字数で単純に切り詰めるとリンクが壊れるため行わない）、(3) カレンダー
リンクをスポット情報と別フィールドに分離する、(4) embed合計が上限に近づいたら
Discordメッセージ自体を複数回に分けて送信する、という4段階で対応している。

環境変数:
  DISCORD_WEBHOOK_LOCAL (必須・「ローカル情報／朝通知用」Discord Webhook)

テスト実行:
  python scripts/check_photo_season.py --simulate-date 2026-09-17 --no-send
    → 実行日を仮定して抽出結果とカレンダーURLを標準出力に表示するのみ（送信しない）
"""
import argparse
import ast
import calendar
import datetime
import json
import math
import os
import re
import sys
from urllib.parse import quote, urlencode

import requests

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
PHOTO_INDEX_PATH = os.path.join(REPO_ROOT, "photo", "index.html")
PHOTO_APP_URL = "https://c6cgv9cnj4-ops.github.io/wbc/photo/index.html"
REQUEST_TIMEOUT = 15

KITAMOTO_LAT = 36.0260
KITAMOTO_LNG = 139.5180

# rawSpots の都道府県インデックス（photo/index.html の P 配列と対応）
PREF_NAMES = ["埼玉県", "東京都", "神奈川県", "千葉県", "茨城県", "栃木県", "群馬県"]

# 「直近1〜2週間以内に見頃を迎える」の判定に使う先行日数
UPCOMING_WINDOW_DAYS = 14

COLOR_PHOTO = 0xF59E0B

CALENDAR_BASE_URL = "https://calendar.google.com/calendar/render?action=TEMPLATE"

# 被写体キーワード → 推奨レンズ（メーカー公表値ベースの概算重量。個体差・付属品は含まない目安）
LENS_KIT = {
    "tele": "EF70-200mm F4L（望遠で背景を圧縮・花の密度感を強調／約700g）",
    "wide": "SIGMA 35mm F1.4（広角でスケール感とパースを活かす／約665g）",
    "portrait": "EF85mm F1.8（寄って一輪をボケで際立たせる／約425g）",
}
# Googleカレンダーのdetailsに載せる短縮名（percent-encodingでURLが肥大化しDiscordの
# 文字数上限を超えるのを避けるため、理由書き・重量は省いた型番のみにする）
LENS_KIT_SHORT = {
    "tele": "EF70-200mm F4L",
    "wide": "SIGMA 35mm F1.4",
    "portrait": "EF85mm F1.8",
}
LENS_SHORT_BY_FULL = {v: LENS_KIT_SHORT[k] for k, v in LENS_KIT.items()}
TELE_KEYWORDS = [
    "紅葉", "桜", "サクラ", "曼珠沙華", "ヒガンバナ", "コスモス", "ひまわり", "ヒマワリ",
    "菜の花", "ネモフィラ", "ポピー", "芝桜", "アジサイ", "紫陽花", "イチョウ", "銀杏",
    "新緑", "ススキ", "バラ", "つつじ", "梅", "ハス", "蓮", "牡丹", "しだれ桜", "花畑",
    "並木", "チューリップ", "ダリア", "つばき", "椿",
]
WIDE_KEYWORDS = [
    "タワー", "スカイツリー", "橋", "夜景", "イルミネーション", "展望", "工場", "富士山",
    "湖", "渓谷", "岩畳", "海", "ビーチ", "シルエット", "ライトアップ", "駅舎", "街並み",
    "神社", "寺", "本堂", "山門", "庭園", "城", "灯台", "ダム",
]
PORTRAIT_KEYWORDS = [
    "花", "桜", "サクラ", "アジサイ", "紫陽花", "バラ", "つつじ", "梅", "ハス", "蓮",
    "牡丹", "チューリップ", "コスモス", "ネモフィラ", "ポピー", "ダリア", "つばき", "椿",
]

MONTH_RANGE_RE = re.compile(r"(\d{1,2})(?:[-〜~](\d{1,2}))?月")


def parse_args():
    p = argparse.ArgumentParser(description="週末撮影予報 見頃先回り通知バッチ")
    p.add_argument(
        "--simulate-date", metavar="YYYY-MM-DD",
        help="実行日を指定日として扱う（テスト用。省略時は本日=JST）",
    )
    p.add_argument(
        "--no-send", action="store_true",
        help="Discordへ送信せず、抽出結果とカレンダーURLを標準出力に表示するのみ",
    )
    p.add_argument(
        "--top", type=int, default=10,
        help="通知に含める最大スポット数（Discord embedのフィールド数上限対策。既定10）",
    )
    return p.parse_args()


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def calc_train_minutes(dist_linear, pref_idx):
    base_minutes, factor = 25, 1.35
    if pref_idx == 0:
        factor = 1.2
    elif pref_idx == 1:
        factor, base_minutes = 1.1, 35
    elif pref_idx == 2:
        factor, base_minutes = 1.15, 45
    elif pref_idx == 3:
        factor, base_minutes = 1.4, 40
    elif pref_idx in (5, 6):
        factor, base_minutes = 1.25, 30
    else:
        factor, base_minutes = 1.5, 45
    return max(15, round(base_minutes + dist_linear * factor))


def calc_car_access(dist_linear, dist_road, pref_idx):
    minutes_local = max(10, round((dist_road / 22) * 60))
    if dist_linear < 15:
        return {"has_express": False, "minutes_express": 0, "toll_express": 0, "minutes_local": minutes_local}
    minutes_express = max(20, round(20 + (dist_road / 75) * 60))
    base_toll, per_km = 400, 24
    if pref_idx in (1, 2):
        base_toll, per_km = 800, 28
    elif pref_idx == 3:
        base_toll, per_km = 600, 26
    toll_express = round((base_toll + dist_road * per_km) / 50) * 50
    return {
        "has_express": True, "minutes_express": minutes_express,
        "toll_express": toll_express, "minutes_local": minutes_local,
    }


def load_raw_spots(html_path=PHOTO_INDEX_PATH):
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    marker = "const rawSpots = ["
    start = html.index(marker)
    array_start = start + len(marker) - 1  # '[' の位置
    depth = 0
    end = None
    for i in range(array_start, len(html)):
        ch = html[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError("rawSpots 配列の終端が見つかりませんでした（photo/index.html の構造が変わった可能性）")
    literal = html[array_start:end]
    return ast.literal_eval(literal)


def parse_subject_segments(main_subject_text):
    """
    mainSubject 文字列（例:'菜の花（4月）、アジサイ（6月）、ヒガンバナ（曼珠沙華・9-10月）、コスモス（10月）'）
    から、括弧内に月情報を含むセグメントだけを (label, month_start, month_end) のリストとして抽出する。
    月情報を含まないセグメント（建築物の常設要素など）はスキップする。
    """
    segments = []
    for part in main_subject_text.split("、"):
        part = part.strip()
        m = re.match(r"^(?P<subject>[^（]+)（(?P<detail>[^）]*)）$", part)
        if not m:
            continue
        subject = m.group("subject").strip()
        detail = m.group("detail").strip()
        month_matches = list(MONTH_RANGE_RE.finditer(detail))
        if not month_matches:
            continue
        mm = month_matches[-1]  # 月情報は括弧内の末尾に置かれる書式のため最後の一致を採用
        month_start = int(mm.group(1))
        month_end = int(mm.group(2)) if mm.group(2) else month_start
        qualifier = (detail[:mm.start()] + detail[mm.end():]).strip(" ・")
        label = f"{subject}（{qualifier}）" if qualifier else subject
        segments.append({"label": label, "month_start": month_start, "month_end": month_end})
    return segments


def month_in_range(month, start, end):
    if start <= end:
        return start <= month <= end
    return month >= start or month <= end  # 12月→1月のような年またぎ


def classify_segment(seg, today):
    """今月が見頃なら 'now'、直近UPCOMING_WINDOW_DAYS日以内に見頃入りするなら 'upcoming'、それ以外は None。"""
    if month_in_range(today.month, seg["month_start"], seg["month_end"]):
        return "now"
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_left_in_month = days_in_month - today.day
    if days_left_in_month <= UPCOMING_WINDOW_DAYS:
        next_month = today.month % 12 + 1
        if month_in_range(next_month, seg["month_start"], seg["month_end"]):
            return "upcoming"
    return None


def recommend_lenses(subject_labels_text):
    text = subject_labels_text
    lenses = []
    if any(k in text for k in TELE_KEYWORDS):
        lenses.append(LENS_KIT["tele"])
    if any(k in text for k in WIDE_KEYWORDS):
        lenses.append(LENS_KIT["wide"])
    if any(k in text for k in PORTRAIT_KEYWORDS):
        lenses.append(LENS_KIT["portrait"])
    if not lenses:
        lenses = [LENS_KIT["tele"], LENS_KIT["wide"]]
    # 3本すべてを毎回持ち出すと嵩張るため、最大2本の組み合わせに絞る（望遠を優先）
    if len(lenses) > 2:
        lenses = lenses[:2]
    return lenses


def next_weekend(today):
    days_until_sat = (5 - today.weekday()) % 7
    saturday = today + datetime.timedelta(days=days_until_sat)
    sunday = saturday + datetime.timedelta(days=1)
    end_exclusive = sunday + datetime.timedelta(days=1)
    return saturday, sunday, end_exclusive


# DiscordのカレンダーリンクフィールドがDISCORD_FIELD_LIMIT(1024)に収まるよう、
# calendar_url自体はこの長さを上限とする。フィールド側のラッパー文言
# "[タップしてGoogleカレンダーに追加](...)" が23文字のため、1024-23に安全マージンを取る。
CALENDAR_URL_SAFE_LENGTH = 995


def _clip_text(text, max_len):
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def build_calendar_url(spot, subject_text, status_text, lenses, car_minutes_text, train_minutes, today):
    """
    Googleカレンダーの予定作成画面を開くURLを生成する。
    日本語テキストはpercent-encodingで1文字あたり最大9文字に膨らみ、Discord
    embedの1フィールド上限(1024文字)を容易に超えるため、detailsは型番・数値
    中心の簡潔な表記にとどめる（理由書きや重量は #build_discord_payload 側の
    Discord本文にのみ記載する）。それでも長くなる被写体名・住所の場合は段階的に
    情報量を落とす（URLを文字数で単純に切り詰めるとリンクが壊れるため行わない）。
    """
    saturday, sunday, end_exclusive = next_weekend(today)
    dates_param = f"{saturday:%Y%m%d}/{end_exclusive:%Y%m%d}"
    lenses_short = [LENS_SHORT_BY_FULL.get(l, l) for l in lenses]

    def render(subject, address, with_details):
        text_param = f"【撮影】{spot['name']}（{subject}）"
        params = {"text": text_param, "dates": dates_param, "location": address}
        if with_details:
            details_lines = [
                f"被写体:{subject}({status_text})",
                f"レンズ:{'/'.join(lenses_short)}",
                f"速報:{spot['url'] or 'なし'}",
                f"所要目安:車{car_minutes_text}/電車約{train_minutes}分",
                f"アプリ:{PHOTO_APP_URL}",
            ]
            params["details"] = "\n".join(details_lines)
        return f"{CALENDAR_BASE_URL}&{urlencode(params, quote_via=quote)}"

    # 1段階目: フル情報。2段階目: 被写体名・住所を短縮。3段階目: detailsを省略（title/日付/場所のみ）。
    candidates = [
        (subject_text, spot["address"], True),
        (_clip_text(subject_text, 20), _clip_text(spot["address"], 40), True),
        (_clip_text(subject_text, 20), _clip_text(spot["address"], 40), False),
    ]
    for subject, address, with_details in candidates:
        url = render(subject, address, with_details)
        if len(url) <= CALENDAR_URL_SAFE_LENGTH:
            break
    return url


def build_spot_entry(row, today):
    spot = {
        "id": row[0],
        "pref_idx": row[1],
        "pref": PREF_NAMES[row[1]] if row[1] < len(PREF_NAMES) else "関東",
        "name": row[2],
        "address": row[3],
        "lat": row[4],
        "lng": row[5],
        "main_subject": row[6],
        "url": row[10] if len(row) > 10 else "",
    }

    segments = parse_subject_segments(spot["main_subject"])
    now_labels, upcoming_labels = [], []
    for seg in segments:
        status = classify_segment(seg, today)
        if status == "now":
            now_labels.append(seg["label"])
        elif status == "upcoming":
            upcoming_labels.append(seg["label"])

    if now_labels:
        status_key, status_text, subject_labels = "now", "現在見頃", now_labels
    elif upcoming_labels:
        status_key, status_text, subject_labels = "upcoming", "まもなく見頃", upcoming_labels
    else:
        return None

    dist_lin = haversine_km(KITAMOTO_LAT, KITAMOTO_LNG, spot["lat"], spot["lng"])
    dist_road = dist_lin * 1.3
    train_min = calc_train_minutes(dist_lin, spot["pref_idx"])
    car = calc_car_access(dist_lin, dist_road, spot["pref_idx"])

    if car["has_express"]:
        car_text = f"高速利用で約{car['minutes_express']}分（通行料目安{car['toll_express']:,}円）/一般道で約{car['minutes_local']}分"
        car_minutes_short = f"約{car['minutes_express']}分（高速）"
    else:
        car_text = f"一般道で約{car['minutes_local']}分（{round(dist_lin)}km圏内のため高速利用なし）"
        car_minutes_short = f"約{car['minutes_local']}分"
    train_text = f"約{train_min}分〜（乗換目安・概算）"

    subject_text = "・".join(subject_labels)
    lenses = recommend_lenses(subject_text)
    calendar_url = build_calendar_url(spot, subject_text, status_text, lenses, car_minutes_short, train_min, today)

    return {
        "spot": spot,
        "status_key": status_key,
        "status_text": status_text,
        "subject_text": subject_text,
        "dist_km": round(dist_lin, 1),
        "car_text": car_text,
        "train_text": train_text,
        "lenses": lenses,
        "calendar_url": calendar_url,
    }


DISCORD_FIELD_LIMIT = 1024
# embed合計(title+description+全フィールド)の実上限は6000文字。日本語混じりの
# GoogleカレンダーURLは長くなりがちなため、安全マージンを取って分割する。
DISCORD_EMBED_TOTAL_BUDGET = 5500


def _clip_field(name, value):
    if len(value) > DISCORD_FIELD_LIMIT:
        print(f"[WARN] フィールド'{name}'が{len(value)}文字でDiscord上限(1024)を超過したため切り詰めます。")
        value = value[: DISCORD_FIELD_LIMIT - 1] + "…"
    return {"name": name[:256], "value": value, "inline": False}


def build_spot_fields(e):
    """1スポットにつき「情報」フィールドと「カレンダー登録リンク」フィールドの2つを返す。"""
    s = e["spot"]
    info_lines = [
        f"📍 **{s['address']}**",
        f"🌸 **{e['subject_text']}**（{e['status_text']}）",
        f"🔭 {' / '.join(e['lenses'])}",
        f"🚗 {e['car_text']}",
        f"🚃 {e['train_text']}",
    ]
    if s["url"]:
        info_lines.append(f"🔗 [現地リアルタイム開花・公式速報]({s['url']})")
    info_field = _clip_field(
        f"{s['name']}（{s['pref']}・北本団地から約{e['dist_km']}km）",
        "\n".join(info_lines),
    )
    cal_field = _clip_field(
        "📅 予定を登録",
        f"[タップしてGoogleカレンダーに追加]({e['calendar_url']})",
    )
    return [info_field, cal_field]


def build_discord_payloads(entries, today, truncated_count):
    """
    Discordのembed合計文字数上限(6000文字)を超えないよう、必要に応じて
    複数のWebhookメッセージ(payload)に分割して返す。
    """
    saturday, sunday, _ = next_weekend(today)
    total_count = len(entries) + truncated_count
    base_intro = (
        f"実行日: {today:%Y-%m-%d}(木) ｜ 対象週末: {saturday:%m/%d}(土)-{sunday:%m/%d}(日)\n"
        f"該当スポット {total_count}件（現在見頃 / 2週間以内に見頃入り）\n"
        "※所要時間は直線距離ベースの概算です（渋滞・乗換時間は含みません）。\n"
        "※推奨レンズは被写体傾向からの暫定提案です。現地の咲き具合に応じて調整してください。"
    )

    payloads = []
    current_fields = []
    current_total = 0
    title = "📸 【週末撮影予報】いま見頃の関東カメラスポット"

    def flush(intro_text):
        if current_fields:
            payloads.append({"embeds": [{
                "title": title,
                "description": intro_text,
                "color": COLOR_PHOTO,
                "fields": current_fields,
            }]})

    for e in entries:
        fields = build_spot_fields(e)
        added_len = sum(len(f["name"]) + len(f["value"]) for f in fields)
        intro_len = len(title) + len(base_intro)
        if current_fields and current_total + added_len + intro_len > DISCORD_EMBED_TOTAL_BUDGET:
            flush(base_intro if not payloads else "（続き）")
            current_fields, current_total = [], 0
        current_fields.extend(fields)
        current_total += added_len

    flush(base_intro if not payloads else "（続き）")
    return payloads


def send_to_discord(webhook_url, payload):
    resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300:
        print(f"[ERROR] Discord送信に失敗しました(HTTP {resp.status_code}): {resp.text[:300]}")
        return False
    print(f"[OK] Discord送信成功(HTTP {resp.status_code})")
    return True


def main():
    args = parse_args()

    if args.simulate_date:
        today = datetime.datetime.strptime(args.simulate_date, "%Y-%m-%d").date()
    else:
        try:
            from zoneinfo import ZoneInfo
            today = datetime.datetime.now(ZoneInfo("Asia/Tokyo")).date()
        except Exception:
            today = datetime.date.today()

    print(f"=== 週末撮影予報バッチ 実行日: {today:%Y-%m-%d}（{['月','火','水','木','金','土','日'][today.weekday()]}） ===")

    raw_spots = load_raw_spots()
    print(f"[INFO] rawSpots読み込み: {len(raw_spots)}件")

    hits = []
    for row in raw_spots:
        entry = build_spot_entry(row, today)
        if entry:
            hits.append(entry)

    print(f"[INFO] 見頃/直近見頃ヒット: {len(hits)}件")
    if not hits:
        print("[INFO] 該当スポットがないため送信をスキップします。")
        return

    priority = {"now": 0, "upcoming": 1}
    hits.sort(key=lambda e: (priority[e["status_key"]], e["dist_km"]))

    shown = hits[: args.top]
    truncated_count = max(0, len(hits) - len(shown))

    for e in shown:
        s = e["spot"]
        print(f"  - [{e['status_text']}] {s['name']}（{e['subject_text']}） dist={e['dist_km']}km")
        print(f"      calendar_url: {e['calendar_url']}")

    payloads = build_discord_payloads(shown, today, truncated_count)
    print(f"[INFO] Discordメッセージ数: {len(payloads)}件（1メッセージ6000文字上限のため分割）")

    if args.no_send:
        print("--- [DRY RUN] Discord送信ペイロード ---")
        print(json.dumps(payloads, ensure_ascii=False, indent=2))
        return

    webhook = os.environ.get("DISCORD_WEBHOOK_LOCAL")
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_LOCAL が設定されていません。")
        sys.exit(1)

    ok = True
    for payload in payloads:
        if not send_to_discord(webhook, payload):
            ok = False
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
