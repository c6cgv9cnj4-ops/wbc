# -*- coding: utf-8 -*-
"""
「行きつけ名店発掘（北本15km）」新着・昇格店舗 検知バッチ

photo/index.html の「行きつけ名店発掘」タブは北本団地(36.0260, 139.5180)から
直線距離15km圏内の個人経営の飲食店・喫茶店を紹介するが、その候補は手動で
リサーチ・追加している。本スクリプトは週1回、Google Places API (New) の
Text Search で圏内をジャンル別に巡回し、
  - 個人経営・地元密着店（チェーン店は完全除外）
  - Google評価 3.8以上
  - クチコミ件数 100件以上
の基準を満たし、かつ index.html にまだ登録されていない新規店舗を検知して
Discordへ通知する。同じ店舗を二重通知しないよう、通知済みplace_idを
state/gourmet_shops_seen.json に蓄積する。

【重要・運用上の注意】
1. GOOGLE_PLACES_API_KEY について:
   photo/index.html に埋め込まれているAPIキーは、ブラウザからのHTTP
   リファラー制限（c6cgv9cnj4-ops.github.io/* のみ許可）が前提のクライアント用
   キーであり、GitHub Actions（サーバー環境・リファラーなし）からのリクエストは
   その制限により拒否される。本スクリプトを実行するには、HTTPリファラー制限を
   かけない（またはGitHub ActionsのIPを許可する）別のAPIキーを新規に発行し、
   リポジトリシークレット GOOGLE_PLACES_API_KEY_SERVER として登録すること。
   このキーをフロントエンド（photo/index.html）に埋め込んではならない。
2. DISCORD_WEBHOOK_GOURMET について:
   既存のDiscord Webhookシークレット（WEBHOOK_LOCAL, WEBHOOK_MARKET等）は
   いずれも別トピック（防災・市況等）用のため流用せず、新規のWebhook URLを
   グルメ通知用チャンネルで発行し、リポジトリシークレット WEBHOOK_GOURMET
   として登録すること。
3. 健康度判定（🥩 低脂質A/バランスB/チートC）は、Places APIからは料理内容が
   分からないため、検索クエリのジャンル（麺類/町中華/コーヒー）から暫定的に
   割り当てた自動判定であり、確定値ではない（Discord通知本文にもその旨を明記）。
   実際の登録（index.html への反映）前に、細川さんが公式サイト・クチコミで
   メニュー内容を確認したうえで health_level を確定させること。

環境変数:
  GOOGLE_PLACES_API_KEY_SERVER (必須・リファラー制限なしのサーバー用キー)
  DISCORD_WEBHOOK_GOURMET      (必須)
"""
import json
import math
import os
import sys
import time

import requests

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "gourmet_shops_seen.json")
REQUEST_TIMEOUT = 15
REQUEST_INTERVAL_SEC = 1.0

BASE_LAT = 36.0260
BASE_LNG = 139.5180
RADIUS_M = 15000

MIN_RATING = 3.8
MIN_REVIEWS = 100

# ============================================================
# 既存登録店舗（photo/index.html の danchiMapData と手動で同期させる）
# 名前の完全一致・部分一致で重複検知に使う。index.html 側にお店を追加/削除
# したら、このリストも合わせて更新すること。
# ============================================================
KNOWN_SHOP_NAMES = [
    "あさひ庵", "支那そば 心麺", "ますや食堂", "カラク",
    "馬力屋", "そば処 いちい", "ユアディアコーヒー", "いしづか", "大木うどん",
    "長木屋", "かねはち", "小山屋食堂",
    "中華料理 宝来", "中華料理 チャイナ", "文楽 東蔵", "KOMIBOU",
    "本手打ちうどん 庄司", "中華そば 四つ葉", "手打ち十割八丁", "四方吉うどん",
    "手打うどん 松屋", "深山うどん", "うどん 有田", "藤倉食堂", "禅味 あら井",
    "ふた葉", "定食むさしや", "百々山", "萬福", "食事処 高半",
    "かねむ食堂", "さか本", "一寸一", "ゆたか",
]
# 「水織」は2024-09-01付で休業確認のため除外済み(細川さんの指摘、2026-09-13)。
# 「いづみや」「長林」「そば処甚五郎(北本市中丸)」「阿良川(北本市荒井)」
# 「やぶ砂(北本市中央)」は複数回ウェブ検索したが実在を確認できなかった名称の
# ため、KNOWN扱いにも新規登録にも含めていない。「阿良川」は名称・エリアが近い
# 実在店「手打ちそば 禅味 あら井」(北本市高尾1-299)と混同された可能性が高い。

# 全国・広域チェーンの明示的除外リスト（個人店発掘の趣旨に反するため）
CHAIN_NAME_KEYWORDS = [
    "マクドナルド", "モスバーガー", "バーガーキング", "ケンタッキー",
    "すき家", "吉野家", "松屋", "なか卯", "ガスト", "サイゼリヤ", "ジョナサン",
    "デニーズ", "ココイチ", "CoCo壱番屋", "丸亀製麺", "はなまるうどん",
    "スターバックス", "ドトール", "タリーズ", "コメダ珈琲店", "サンマルクカフェ",
    "餃子の王将", "日高屋", "幸楽苑", "リンガーハット", "びっくりドンキー",
    "ミスタードーナツ", "セブンイレブン", "ファミリーマート", "ローソン",
]

# ジャンル別クエリと、Places APIからは分からない健康度の暫定割当て
# （health_level は自動判定の暫定値。確定はメニュー確認後に人手で行う）
GENRE_QUERIES = [
    {"query": "町中華 定食", "genre_label": "町中華・定食", "health_level": "C",
     "health_note": "町中華・定食は炒め物や揚げ物中心になりやすいための暫定判定"},
    {"query": "手打ちうどん", "genre_label": "うどん・そば・麺類", "health_level": "B",
     "health_note": "うどん・そばは基本バランス系だが天ぷら等の有無で変動するための暫定判定"},
    {"query": "手打ち蕎麦", "genre_label": "うどん・そば・麺類", "health_level": "B",
     "health_note": "うどん・そばは基本バランス系だが天ぷら等の有無で変動するための暫定判定"},
    {"query": "自家焙煎コーヒー 専門店", "genre_label": "自家焙煎コーヒー専門", "health_level": "A",
     "health_note": "ブラックコーヒー主体の専門店は低脂質という前提の暫定判定"},
]

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.rating,places.userRatingCount,places.googleMapsUri,places.priceLevel,"
    "places.regularOpeningHours"
)

PRICE_LEVEL_JA = {
    "PRICE_LEVEL_FREE": "無料",
    "PRICE_LEVEL_INEXPENSIVE": "～1,000円程度",
    "PRICE_LEVEL_MODERATE": "1,000～3,000円程度",
    "PRICE_LEVEL_EXPENSIVE": "3,000～5,000円程度",
    "PRICE_LEVEL_VERY_EXPENSIVE": "5,000円以上",
}

COLOR_GOURMET = 0x10B981


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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


def is_chain(name):
    return any(kw in name for kw in CHAIN_NAME_KEYWORDS)


def is_known(name):
    return any(known in name or name in known for known in KNOWN_SHOP_NAMES)


def search_places(api_key, query):
    body = {
        "textQuery": f"{query} 埼玉県 北本 鴻巣 桶川 上尾 川島 吉見 行田 さいたま市北区",
        "languageCode": "ja",
        "maxResultCount": 20,
        "locationBias": {
            "circle": {
                "center": {"latitude": BASE_LAT, "longitude": BASE_LNG},
                "radius": float(RADIUS_M),
            }
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    try:
        resp = requests.post(PLACES_SEARCH_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("places", [])
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR] Places検索に失敗しました（query={query}）: {err}")
        return []


def build_candidate(place, genre_def):
    loc = place.get("location") or {}
    lat, lng = loc.get("latitude"), loc.get("longitude")
    if lat is None or lng is None:
        return None
    dist_km = haversine_km(BASE_LAT, BASE_LNG, lat, lng)
    if dist_km > RADIUS_M / 1000.0:
        return None  # locationBiasは目安のため、圏外ヒットを厳密に除外する

    name = (place.get("displayName") or {}).get("text", "")
    if not name or is_chain(name) or is_known(name):
        return None

    rating = place.get("rating")
    reviews = place.get("userRatingCount")
    if rating is None or reviews is None or rating < MIN_RATING or reviews < MIN_REVIEWS:
        return None

    return {
        "place_id": place.get("id"),
        "name": name,
        "addr": place.get("formattedAddress", "住所不明"),
        "dist_km": round(dist_km, 1),
        "rating": rating,
        "reviews": reviews,
        "maps_uri": place.get("googleMapsUri", ""),
        "price_level": PRICE_LEVEL_JA.get(place.get("priceLevel", ""), "情報なし"),
        "genre_label": genre_def["genre_label"],
        "health_level": genre_def["health_level"],
        "health_note": genre_def["health_note"],
    }


HEALTH_EMOJI = {"A": "🟢", "B": "🟡", "C": "🔴"}
HEALTH_LABEL = {"A": "低脂質クリーンA", "B": "バランスB", "C": "チートデイC"}


def build_embed(shop):
    h = shop["health_level"]
    lines = [
        f"📍 **{shop['addr']}**（北本団地から約{shop['dist_km']}km）",
        f"⭐ Google評価 ★{shop['rating']} / クチコミ {shop['reviews']:,}件",
        f"🥩 健康度判定（暫定・要確認）: {HEALTH_EMOJI.get(h, '⚪')} {HEALTH_LABEL.get(h, '不明')}",
        f"　└ {shop['health_note']}",
        f"💰 予算帯目安: {shop['price_level']}",
    ]
    if shop["maps_uri"]:
        lines.append(f"🗺️ [Googleマップで見る](<{shop['maps_uri']}>)")
    lines.append(
        "\n⚠️ この店舗は自動検知の候補です。個人経営であること・メニュー内容・"
        "健康度を細川さんが確認のうえ、photo/index.html への正式登録をお願いします。"
    )
    return {
        "title": f"🍜 新着候補: {shop['name']}（{shop['genre_label']}）",
        "description": "\n".join(lines),
        "color": COLOR_GOURMET,
    }


def send_embeds_to_discord(webhook_url, embeds, batch_size=10):
    if not webhook_url:
        print("[ERROR] DISCORD_WEBHOOK_GOURMET が設定されていないため送信をスキップします。")
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
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY_SERVER")
    webhook = os.environ.get("DISCORD_WEBHOOK_GOURMET")

    if not api_key:
        print("[ERROR] 環境変数 GOOGLE_PLACES_API_KEY_SERVER が設定されていません。"
              "photo/index.html 埋め込みのブラウザ用キーはHTTPリファラー制限のため"
              "サーバー実行では使えません。別途サーバー用キーを発行してください。")
        sys.exit(1)
    if not webhook:
        print("[ERROR] 環境変数 DISCORD_WEBHOOK_GOURMET が設定されていません。")
        sys.exit(1)

    state = load_seen_state()
    seen_place_ids = state.setdefault("seen_place_ids", [])
    seen_set = set(seen_place_ids)

    new_shops = []
    for genre_def in GENRE_QUERIES:
        print(f"=== 検索中: {genre_def['query']} ===")
        places = search_places(api_key, genre_def["query"])
        time.sleep(REQUEST_INTERVAL_SEC)
        for place in places:
            candidate = build_candidate(place, genre_def)
            if not candidate:
                continue
            if candidate["place_id"] in seen_set:
                continue
            new_shops.append(candidate)
            seen_set.add(candidate["place_id"])

    print(f"=== 新規候補: {len(new_shops)}件 ===")
    if not new_shops:
        print("[INFO] 新規に基準を満たす店舗はありませんでした。")
        return

    embeds = [build_embed(s) for s in new_shops]
    if not send_embeds_to_discord(webhook, embeds):
        sys.exit(1)

    state["seen_place_ids"] = list(seen_set)
    save_seen_state(state)


if __name__ == "__main__":
    main()
