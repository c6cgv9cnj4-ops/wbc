# -*- coding: utf-8 -*-
"""
底値トラッカー(厳選4ジャンル) 自動収集スクリプト

対象店舗:
  - ロヂャース北本店: 公式サイト経由のWebビューアー(cms.mechao.tv、タイル画像結合)
  - マルサン桶川店・ヤオコー・ベルク・ウエルシア: トクバイ(tokubai.co.jp)

処理の流れ:
  1. 各店舗のチラシ画像を取得(price_bot/scripts/scraper.py の
     実証済みスクレイピング処理を再利用する。トクバイの店舗解決・
     ロヂャースのタイル結合ロジックは重複実装しない)
  2. 厳選4ジャンル(調味料・油/生鮮(肉・青果)/主食・米/ペーパー類)の
     品目リストのみを対象に、Gemini APIで価格・規格・特売種別を抽出
  3. 規格テキストを解析して単位単価(100g/100ml/1m/1個/1kg/1ロールあたり)を計算
  4. gas/create_price_tracker.gs の doGet ウェブアプリへGETリクエストで送信し、
     ジャンル別タブの該当セル更新 + 価格ログ蓄積タブへの追記を行う

厳選品目に一致しない商品(酒・飲料・菓子・惣菜・加工肉・麺類・冷凍品・瓶類・
チルド等)はGemini側のプロンプトで明示的に除外を指示し、万一該当外の
product_nameが返っても DEALS_ITEMS に無ければ無視する。

環境変数:
  GEMINI_API_KEY          (必須)
  DEALS_GAS_WEB_APP_URL   (必須)
  DEALS_GAS_SHARED_SECRET (必須)
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

from playwright.sync_api import sync_playwright

# price_bot/scripts/scraper.py の実証済み関数を再利用する
_PRICE_BOT_SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "price_bot", "scripts")
sys.path.insert(0, _PRICE_BOT_SCRIPTS)
from scraper import (  # noqa: E402
    USER_AGENT,
    TILE_RE,
    resolve_tokubai_store_path,
    extract_own_leaflet_paths,
    extract_full_image_url,
    collect_rogers_tile_urls,
    stitch_rogers_page,
    call_gemini,
    parse_ml,
    parse_g,
    parse_count,
    parse_toilet_paper_total_length_m,
)
from google import genai  # noqa: E402

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "deals_downloads")
GEMINI_MODEL_NAME = "gemini-3.6-flash"
REQUEST_INTERVAL_SEC = 2.0

# ============================================================
# 厳選品目マスタ(gas/create_price_tracker.gs の GENRE_ITEMS と
# genre・nameを完全に一致させること)
# ============================================================

DEALS_ITEMS = [
    {"genre": "調味料・油", "name": "生しょうゆ(プラ容器)", "unit": "100ml",
     "measure_type": "volume_ml", "keywords": ["しょうゆ", "醤油", "生しょうゆ"]},
    {"genre": "調味料・油", "name": "純正ごま油", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["ごま油", "純正ごま油", "かどや"]},
    {"genre": "調味料・油", "name": "ノンオイルドレッシング", "unit": "100ml",
     "measure_type": "volume_ml", "keywords": ["ノンオイル", "ドレッシング"]},

    {"genre": "生鮮（肉・青果）", "name": "若鶏モモ肉", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["若鶏", "モモ肉", "鶏もも", "国産鶏"]},
    {"genre": "生鮮（肉・青果）", "name": "豚バラ切落し", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["豚バラ", "切り落とし", "切落し", "国産豚"]},
    {"genre": "生鮮（肉・青果）", "name": "キャベツ", "unit": "1玉",
     "measure_type": "count", "count_units": ["玉", "個"],
     "keywords": ["キャベツ"]},
    {"genre": "生鮮（肉・青果）", "name": "玉ねぎ", "unit": "1kg",
     "measure_type": "weight_kg", "keywords": ["玉ねぎ", "たまねぎ", "タマネギ"]},
    {"genre": "生鮮（肉・青果）", "name": "じゃがいも", "unit": "1kg",
     "measure_type": "weight_kg", "keywords": ["じゃがいも", "ジャガイモ", "馬鈴薯"]},

    {"genre": "主食・米", "name": "白米 5kg", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["白米", "米 5kg", "こめ 5kg"]},
    {"genre": "主食・米", "name": "白米 10kg", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["白米", "米 10kg", "こめ 10kg"]},

    {"genre": "ペーパー類", "name": "トイレットペーパー(12ロール等)", "unit": "1m",
     "measure_type": "toilet_paper", "keywords": ["トイレットペーパー"]},
    {"genre": "ペーパー類", "name": "キッチンペーパー(4ロール等)", "unit": "1ロール",
     "measure_type": "count_roll", "count_units": ["ロール", "本"],
     "keywords": ["キッチンペーパー"]},
]

# 明示的に除外するジャンル(Geminiへのプロンプトで念押しする)
EXCLUDED_GENRES_TEXT = "酒・飲料・菓子・惣菜・加工肉・麺類・冷凍品・瓶詰め・チルド食品"

DEALS_STORES = [
    {"name": "ロヂャース北本店", "source": "rogers", "mechao_store_id": "50098efb7172597ee67c0a957a43a0cb"},
    {"name": "マルサン桶川店", "source": "tokubai", "tokubai_query": "スーパーマルサン桶川店"},
    {"name": "ヤオコー", "source": "tokubai", "tokubai_query": "ヤオコー北本中央店"},
    {"name": "ベルク", "source": "tokubai", "tokubai_query": "ベルク北本東間店"},
    {"name": "ウエルシア", "source": "tokubai", "tokubai_query": "ウエルシア北本中丸店"},
]


def get_item_by_name(name):
    for item in DEALS_ITEMS:
        if item["name"] == name:
            return item
    return None


# ============================================================
# スクレイピング(price_bot側の実証済みロジックを流用)
# ============================================================

def scrape_tokubai_store(context, page, store, image_records):
    try:
        store_path = resolve_tokubai_store_path(page, store["tokubai_query"])
        time.sleep(REQUEST_INTERVAL_SEC)
        if not store_path:
            print(f"[WARN][tokubai] 店舗が見つかりません: {store['name']}")
            return

        leaflet_paths = extract_own_leaflet_paths(page, store_path)
        time.sleep(REQUEST_INTERVAL_SEC)
        if not leaflet_paths:
            print(f"[INFO][tokubai] チラシ掲載なし: {store['name']}")
            return

        for leaflet_path in leaflet_paths:
            image_url = extract_full_image_url(page, leaflet_path)
            time.sleep(REQUEST_INTERVAL_SEC)
            if not image_url:
                continue
            leaflet_id = leaflet_path.rstrip("/").split("/")[-1]
            safe_store = re.sub(r"[^\w]", "_", store["name"])
            dest = os.path.join(DOWNLOAD_DIR, f"{safe_store}_{leaflet_id}.jpg")
            resp = context.request.get(image_url, timeout=30000)
            if resp.status == 200:
                with open(dest, "wb") as f:
                    f.write(resp.body())
                image_records.append({"store": store["name"], "local_path": dest})
        print(f"[OK][tokubai] {store['name']}: 取得完了")
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR][tokubai] {store['name']}: {err}")


def scrape_rogers_store(context, page, store, image_records):
    sid = store["mechao_store_id"]
    url = f"https://cms.mechao.tv/rogers/stores-flyer-r-imglist?s={sid}"
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(1.5)
        urls = collect_rogers_tile_urls(page)
        if not urls:
            print(f"[INFO][rogers] チラシ画像タイルが見つかりません: {store['name']}")
            return

        groups = defaultdict(dict)
        for u in urls:
            m = TILE_RE.search(u)
            if not m:
                continue
            key = (m.group("flyer_id"), int(m.group("page")), int(m.group("layer")))
            groups[key][(int(m.group("row")), int(m.group("col")))] = u

        target_groups = {k: v for k, v in groups.items() if k[2] == 0}
        safe_store = re.sub(r"[^\w]", "_", store["name"])
        count = 0
        for (flyer_id, page_no, _layer), tiles_by_rc in sorted(target_groups.items()):
            dest = os.path.join(DOWNLOAD_DIR, f"{safe_store}_{flyer_id}_p{page_no}.jpg")
            if stitch_rogers_page(context, tiles_by_rc, dest):
                image_records.append({"store": store["name"], "local_path": dest})
                count += 1
        print(f"[OK][rogers] {store['name']}: {count}ページ取得")
    except Exception as err:  # noqa: BLE001
        print(f"[ERROR][rogers] {store['name']}: {err}")


# ============================================================
# Gemini APIによる厳選品目の抽出
# ============================================================

def build_deals_prompt():
    lines = []
    for item in DEALS_ITEMS:
        lines.append(f"- ジャンル:{item['genre']} / 商品名:{item['name']} / 比較単位:{item['unit']} "
                      f"/ 関連キーワード:{', '.join(item['keywords'])}")
    item_list_text = "\n".join(lines)

    return f"""あなたはスーパーのチラシ画像から特売情報を抽出するアシスタントです。
以下の「厳選対象品目リスト」に完全に合致する商品が、このチラシ画像に写っている
場合のみ抽出してください。リストに無いジャンルの商品(特に{EXCLUDED_GENRES_TEXT}等)は
絶対に抽出しないでください。

【厳選対象品目リスト】
{item_list_text}

【出力形式】
JSON配列のみを出力してください(説明文・Markdownのコードブロック記号は不要です)。
該当商品が1つもない場合は空配列 [] を返してください。
各要素は以下のキーを持つオブジェクトにしてください:
- "genre": 上記リストのジャンル名をそのまま使う
- "product_name": 上記リストの商品名を完全一致するものだけ使う(曖昧なら含めない)
- "price_yen": 税込価格(数値のみ。読み取れない場合はnull)
- "package_size_text": パッケージ規格・内容量の原文(例: "1000ml","400g","5kg","1玉","4ロール"等。
   読み取れない場合はnull)
- "deal_type": 特売の種別が読み取れれば記載(例: "平日市","週末朝市","週末セール","通常"等。
   不明な場合は"通常")
- "confidence": "high" または "low"

数値や規格を推測で埋めないでください。読み取れないものは必ずnullにしてください。
"""


# ============================================================
# 単位単価の計算
# ============================================================

def compute_unit_price(item, price_yen, package_size_text):
    if price_yen is None:
        return None, "価格が読み取れませんでした"

    measure_type = item["measure_type"]

    if measure_type == "volume_ml":
        ml = parse_ml(package_size_text)
        if not ml:
            return None, f"内容量(ml/L)を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / ml * 100, 1), ""

    if measure_type == "weight_g":
        g = parse_g(package_size_text)
        if not g:
            return None, f"内容量(g/kg)を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / g * 100, 1), ""

    if measure_type == "weight_kg":
        g = parse_g(package_size_text)
        if not g:
            return None, f"内容量(g/kg)を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / g * 1000, 1), ""

    if measure_type == "count":
        n = parse_count(package_size_text, item.get("count_units", ["個"]))
        n = n or 1  # 単品(1玉/1個)の特売なら規格テキストに個数が出ないことも多い
        return round(price_yen / n, 1), ""

    if measure_type == "count_roll":
        n = parse_count(package_size_text, item.get("count_units", ["ロール"]))
        if not n:
            return None, f"ロール数を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / n, 1), ""

    if measure_type == "toilet_paper":
        total_m, confidence = parse_toilet_paper_total_length_m(package_size_text)
        if not total_m:
            return None, f"ロール数・長さを読み取れませんでした: '{package_size_text}'"
        note = "" if confidence == "high" else "倍巻き表記からの推定値のため要確認"
        return round(price_yen / total_m, 2), note

    return None, f"未対応の測定タイプ: {measure_type}"


def build_result_item(raw_item, store_name):
    item = get_item_by_name(raw_item.get("product_name", ""))
    if not item:
        return None  # 厳選品目リストに無いものは黙って除外する(ノイズ排除)

    price_yen = raw_item.get("price_yen")
    unit_price, note = compute_unit_price(item, price_yen, raw_item.get("package_size_text"))
    if unit_price is None or raw_item.get("confidence") == "low":
        print(f"[SKIP] {item['name']}({store_name}): {note or 'OCR信頼度が低い'}")
        return None

    return {
        "genre": item["genre"],
        "product_name": item["name"],
        "store": store_name,
        "unit_price": unit_price,
        "raw_price": price_yen,
        "deal_type": raw_item.get("deal_type") or "通常",
    }


# ============================================================
# GASウェブアプリへの送信
# ============================================================

def send_to_gas(web_app_url, secret, items, batch_size=5):
    ok_all = True
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        query = urllib.parse.urlencode({
            "secret": secret,
            "items": json.dumps(batch, ensure_ascii=False),
        })
        full_url = f"{web_app_url}?{query}"
        try:
            with urllib.request.urlopen(full_url, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                print(f"バッチ{i // batch_size + 1}({len(batch)}件) GASからの応答: {body}")
                parsed = json.loads(body)
                if not parsed.get("ok"):
                    ok_all = False
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR] GAS送信に失敗しました: {err}")
            ok_all = False
    return ok_all


# ============================================================
# main
# ============================================================

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    gas_url = os.environ.get("DEALS_GAS_WEB_APP_URL")
    gas_secret = os.environ.get("DEALS_GAS_SHARED_SECRET")

    if not api_key:
        print("[ERROR] 環境変数 GEMINI_API_KEY が設定されていません。")
        sys.exit(1)
    if not gas_url or not gas_secret:
        print("[ERROR] DEALS_GAS_WEB_APP_URL / DEALS_GAS_SHARED_SECRET が設定されていません。")
        sys.exit(1)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    client = genai.Client(api_key=api_key)
    prompt = build_deals_prompt()

    image_records = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
        page = context.new_page()

        for store in DEALS_STORES:
            print(f"=== {store['name']} を取得します ===")
            if store["source"] == "tokubai":
                scrape_tokubai_store(context, page, store, image_records)
            elif store["source"] == "rogers":
                scrape_rogers_store(context, page, store, image_records)

        browser.close()

    print(f"=== 画像取得完了: 合計{len(image_records)}枚。Gemini APIで解析します ===")

    results = []
    for record in image_records:
        local_path = record["local_path"]
        if not os.path.exists(local_path):
            continue
        print(f"[OCR] {record['store']}: {os.path.basename(local_path)}")
        raw_items = call_gemini(client, local_path, prompt)
        for raw_item in raw_items:
            result = build_result_item(raw_item, record["store"])
            if result:
                results.append(result)
        time.sleep(1.5)

    print(f"=== 厳選品目の該当件数: {len(results)}件 ===")
    if not results:
        print("[INFO] 送信対象がありません。")
        return

    if not send_to_gas(gas_url, gas_secret, results):
        sys.exit(1)


if __name__ == "__main__":
    main()
