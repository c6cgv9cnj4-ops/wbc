# -*- coding: utf-8 -*-
"""
北本・桶川エリア特売価格チェック - スクレイピング〜価格解析 一括スクリプト(GCP不使用版)

処理の流れ:
  1. トクバイ(tokubai.co.jp)から対象店舗のチラシ画像を取得(Playwright)
  2. ロヂャース公式サイト経由のWebビューアー(cms.mechao.tv)からチラシ画像を取得
     (タイル分割配信のためPillowで1ページに結合してから使う)
  3. 取得した各画像をGemini APIに渡し、追跡19品目に該当する価格・規格・
     特売期限をJSONで抽出
  4. 規格テキストを解析して統一単位単価に変換し、Amazon基準単価と比較して
     判定シグナルを付与
  5. 結果を downloads/results.json に書き出す(次段の push_to_sheets.py が読む)

Googleサービスアカウント・GCPプロジェクトは一切使用しない。
Googleスプレッドシートへの反映は push_to_sheets.py が GASウェブアプリへの
HTTP POSTで行う。

環境変数:
  GEMINI_API_KEY (必須)
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from io import BytesIO

from google import genai
from google.genai import types as genai_types
from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
from master_data import (  # noqa: E402
    get_active_products,
    get_product_by_name,
    get_scrape_target_stores,
    get_rogers_viewer_stores,
    UNIT_BASIS_TO_GRAMS_ML,
)

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "downloads")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_INTERVAL_SEC = 2.0
GEMINI_MODEL_NAME = "gemini-2.0-flash"
GEMINI_MAX_RETRIES = 3
TILE_RE = re.compile(r"/spangle/[^/]+/(?P<flyer_id>\d+)/P(?P<page>\d+)-L(?P<layer>\d+)-R(?P<row>\d+)-C(?P<col>\d+)\.jpg")


# ============================================================
# 1. トクバイ スクレイピング
# ============================================================

def resolve_tokubai_store_path(page, tokubai_query):
    url = (
        "https://tokubai.co.jp/shop_search_results"
        "?shop_fetcher%5Btarget_type%5D=all_region"
        "&shop_fetcher%5Border%5D=recommended"
        "&shop_fetcher%5Bfree_word_query%5D=" + urllib.parse.quote(tokubai_query)
    )
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    links = page.eval_on_selector_all("a[href^='/']", "els => els.map(e => e.getAttribute('href'))")
    for href in links:
        if re.match(r"^/[^/]+/\d+$", href) and not href.startswith("/shop_search"):
            return href
    return None


def extract_own_leaflet_paths(page, store_path):
    page.goto("https://tokubai.co.jp" + store_path, wait_until="domcontentloaded", timeout=30000)
    links = page.eval_on_selector_all("a[href^='/']", "els => els.map(e => e.getAttribute('href'))")
    prefix = store_path + "/leaflets/"
    return sorted(set(h for h in links if h.startswith(prefix)))


def extract_full_image_url(page, leaflet_path):
    page.goto("https://tokubai.co.jp" + leaflet_path, wait_until="domcontentloaded", timeout=30000)
    srcs = page.eval_on_selector_all("img", "els => els.map(e => e.src)")
    for src in srcs:
        if "bargain_office_leaflets/o=true/" in src:
            return src
    candidates = [s for s in srcs if "bargain_office_leaflets" in s]
    return candidates[0] if candidates else None


def scrape_tokubai(context, page, image_records):
    for store in get_scrape_target_stores():
        store_name = store["name"]
        try:
            store_path = resolve_tokubai_store_path(page, store["tokubai_query"])
            time.sleep(REQUEST_INTERVAL_SEC)
            if not store_path:
                print(f"[WARN][tokubai] 店舗が見つかりません: {store_name}")
                continue

            leaflet_paths = extract_own_leaflet_paths(page, store_path)
            time.sleep(REQUEST_INTERVAL_SEC)
            if not leaflet_paths:
                print(f"[INFO][tokubai] チラシ掲載なし: {store_name}")
                continue

            for leaflet_path in leaflet_paths:
                image_url = extract_full_image_url(page, leaflet_path)
                time.sleep(REQUEST_INTERVAL_SEC)
                if not image_url:
                    continue
                leaflet_id = leaflet_path.rstrip("/").split("/")[-1]
                safe_store = re.sub(r"[^\w]", "_", store_name)
                dest = os.path.join(DOWNLOAD_DIR, f"{safe_store}_{leaflet_id}.jpg")
                resp = context.request.get(image_url, timeout=30000)
                if resp.status == 200:
                    with open(dest, "wb") as f:
                        f.write(resp.body())
                    image_records.append({"store": store_name, "local_path": dest})
            print(f"[OK][tokubai] {store_name}: 取得完了")
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR][tokubai] {store_name}: {err}")


# ============================================================
# 2. ロヂャース(mechao.tv) スクレイピング
# ============================================================

def collect_rogers_tile_urls(page):
    all_srcs = set()

    def harvest():
        for s in page.eval_on_selector_all("img", "els => els.map(e => e.src)"):
            if "/spangle/" in s:
                all_srcs.add(s)

    harvest()
    for text in ["おもて", "うら", "1", "2", "3", "4"]:
        try:
            locator = page.get_by_text(text, exact=True)
            if locator.count() > 0:
                locator.first.click(timeout=3000)
                page.wait_for_timeout(800)
                harvest()
        except Exception:
            continue
    return all_srcs


def stitch_rogers_page(context, tiles_by_rc, dest_path):
    max_row = max(rc[0] for rc in tiles_by_rc)
    max_col = max(rc[1] for rc in tiles_by_rc)
    tile_images = {}
    for rc, url in tiles_by_rc.items():
        resp = context.request.get(url, timeout=30000)
        if resp.status != 200:
            continue
        tile_images[rc] = Image.open(BytesIO(resp.body())).convert("RGB")
    if not tile_images:
        return False
    tile_w, tile_h = next(iter(tile_images.values())).size
    canvas = Image.new("RGB", (tile_w * (max_col + 1), tile_h * (max_row + 1)), "white")
    for (row, col), img in tile_images.items():
        canvas.paste(img, (col * tile_w, row * tile_h))
    canvas.save(dest_path, quality=90)
    return True


def scrape_rogers(context, page, image_records):
    for store in get_rogers_viewer_stores():
        store_name = store["name"]
        sid = store["mechao_store_id"]
        url = f"https://cms.mechao.tv/rogers/stores-flyer-r-imglist?s={sid}"
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(1.5)
            urls = collect_rogers_tile_urls(page)
            if not urls:
                print(f"[INFO][rogers] チラシ画像タイルが見つかりません: {store_name}")
                continue

            groups = defaultdict(dict)
            for u in urls:
                m = TILE_RE.search(u)
                if not m:
                    continue
                key = (m.group("flyer_id"), int(m.group("page")), int(m.group("layer")))
                groups[key][(int(m.group("row")), int(m.group("col")))] = u

            target_groups = {k: v for k, v in groups.items() if k[2] == 0}
            safe_store = re.sub(r"[^\w]", "_", store_name)
            count = 0
            for (flyer_id, page_no, _layer), tiles_by_rc in sorted(target_groups.items()):
                dest = os.path.join(DOWNLOAD_DIR, f"{safe_store}_{flyer_id}_p{page_no}.jpg")
                if stitch_rogers_page(context, tiles_by_rc, dest):
                    image_records.append({"store": store_name, "local_path": dest})
                    count += 1
            print(f"[OK][rogers] {store_name}: {count}ページ取得")
        except Exception as err:  # noqa: BLE001
            print(f"[ERROR][rogers] {store_name}: {err}")


# ============================================================
# 3. Gemini APIによる価格抽出
# ============================================================

def build_gemini_prompt():
    products = get_active_products()
    product_lines = "\n".join(
        f"- {p['name']} (換算単位: {p['unit_basis']} / 関連キーワード: {', '.join(p['keywords']) or 'なし'})"
        for p in products
    )
    return f"""あなたはスーパー・ドラッグストアのチラシ画像から特売情報を抽出するアシスタントです。
以下の「追跡対象品目リスト」に該当する商品がこのチラシ画像に写っている場合のみ、
価格・規格(内容量)・特売期間を抽出してJSON配列で返してください。

【追跡対象品目リスト】
{product_lines}

【出力形式】
JSON配列のみを出力してください(説明文・Markdownのコードブロック記号は不要です)。
該当商品が1つもない場合は空配列 [] を返してください。
各要素は以下のキーを持つオブジェクトにしてください:
- "product_name": 上記リストの商品名(完全一致する名前をそのまま使う。曖昧な場合は最も近いものを選ぶ)
- "matched_text": チラシ画像内で実際に読み取った商品名の原文
- "price_yen": 税込価格(数値のみ。読み取れない場合はnull)
- "package_size_text": パッケージ規格の原文(例: "1L","650g","400ml","8枚入","12ロール×3倍巻き"など。読み取れない場合はnull)
- "sale_period_text": 特売期間の原文(例: "8/22〜8/28"。読み取れない場合はnull)
- "confidence": "high" または "low"

数値や規格を推測で埋めないでください。読み取れないものは必ずnullにしてください。
"""


def extract_json_array(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []


def call_gemini(client, image_path, prompt):
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL_NAME,
                contents=[prompt, image_part],
                config=genai_types.GenerateContentConfig(temperature=0.1),
            )
            return extract_json_array(response.text)
        except Exception as err:  # noqa: BLE001
            print(f"[WARN][gemini] 呼び出し失敗(試行{attempt}/{GEMINI_MAX_RETRIES}): {err}")
            time.sleep(3 * attempt)
    return []


# ============================================================
# 4. 単位単価計算・判定(GCP非依存の純Python処理)
# ============================================================

def parse_ml(text):
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[lL](?![a-zA-Z])", text)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"(\d+(?:\.\d+)?)\s*m[lL]", text)
    return float(m.group(1)) if m else None


def parse_g(text):
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[kK]g", text)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"(\d+(?:\.\d+)?)\s*g(?!ラム)", text) or re.search(r"(\d+(?:\.\d+)?)\s*グラム", text)
    return float(m.group(1)) if m else None


def parse_count(text, unit_chars):
    if not text:
        return None
    for ch in unit_chars:
        m = re.search(rf"(\d+)\s*{ch}", text)
        if m:
            return int(m.group(1))
    return None


TOILET_PAPER_BASE_LENGTH_M = 25


def parse_toilet_paper_total_length_m(text):
    if not text:
        return None, "low"
    roll_count = parse_count(text, ["ロール", "個"])
    if roll_count is None:
        return None, "low"
    m_len = re.search(r"(\d+(?:\.\d+)?)\s*m(?!l)", text)
    if m_len:
        return roll_count * float(m_len.group(1)), "high"
    multiplier = re.search(r"(\d+(?:\.\d+)?)\s*倍", text)
    if multiplier:
        return roll_count * TOILET_PAPER_BASE_LENGTH_M * float(multiplier.group(1)), "low"
    return None, "low"


def compute_unit_price(product, price_yen, package_size_text):
    if price_yen is None:
        return None, "low", "価格が読み取れませんでした"

    measure_type = product["measure_type"]
    unit_basis = product["unit_basis"]

    if measure_type == "volume_ml":
        ml = parse_ml(package_size_text)
        if not ml:
            return None, "low", f"内容量(ml/L)を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / ml * UNIT_BASIS_TO_GRAMS_ML[unit_basis], 1), "high", ""

    if measure_type == "weight_g":
        g = parse_g(package_size_text)
        if not g:
            return None, "low", f"内容量(g/kg)を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / g * UNIT_BASIS_TO_GRAMS_ML[unit_basis], 1), "high", ""

    if measure_type == "count_bag":
        n = parse_count(package_size_text, ["袋"])
        if not n:
            return None, "low", f"袋数を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / n, 1), "high", ""

    if measure_type == "count_sheet":
        n = parse_count(package_size_text, ["枚"])
        if not n:
            return None, "low", f"枚数を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / n, 1), "high", ""

    if measure_type == "toilet_paper":
        total_m, confidence = parse_toilet_paper_total_length_m(package_size_text)
        if not total_m:
            return None, "low", f"ロール数・長さを読み取れませんでした: '{package_size_text}'"
        note = "" if confidence == "high" else "倍巻き表記からの推定値のため要確認"
        return round(price_yen / total_m, 2), confidence, note

    return None, "low", f"未対応の測定タイプ: {measure_type}"


def determine_signal(unit_price, amazon_base_price):
    if unit_price is None or amazon_base_price is None:
        return ""
    if unit_price <= amazon_base_price * 0.9:
        return "🟢 店舗買い推奨（底値圏）"
    if unit_price < amazon_base_price:
        return "🟡 通常安値"
    return "⚪ Amazon買い推奨（見送り）"


def parse_sale_end_date(sale_period_text):
    if not sale_period_text:
        return None
    matches = re.findall(r"(\d{1,2})\s*/\s*(\d{1,2})", sale_period_text)
    if not matches:
        return None
    month, day = matches[-1]
    today = datetime.date.today()
    try:
        candidate = datetime.date(today.year, int(month), int(day))
    except ValueError:
        return None
    if candidate < today - datetime.timedelta(days=30):
        candidate = candidate.replace(year=today.year + 1)
    return candidate.isoformat()


def build_result_row(item):
    product = get_product_by_name(item.get("product_name", ""))
    if not product:
        return None

    price_yen = item.get("price_yen")
    unit_price, confidence, note = compute_unit_price(product, price_yen, item.get("package_size_text"))
    amazon_base_price = product.get("amazon_base_price")
    signal = determine_signal(unit_price, amazon_base_price)
    deadline = parse_sale_end_date(item.get("sale_period_text"))
    diff = round(unit_price - amazon_base_price, 1) if (unit_price is not None and amazon_base_price is not None) else None

    ocr_confidence = item.get("confidence", "high")
    if ocr_confidence == "low" or unit_price is None:
        signal = "🔺 要確認(" + (note or "OCRの読み取り精度が低い") + ")"

    return {
        "product_name": product["name"],
        "store": item.get("store", ""),
        "price_yen": price_yen,
        "unit_price": unit_price,
        "amazon_base_price": amazon_base_price,
        "diff": diff,
        "deadline": deadline,  # ISO日付文字列 or None
        "signal": signal,
    }


# ============================================================
# main
# ============================================================

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] 環境変数 GEMINI_API_KEY が設定されていません。")
        sys.exit(1)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    client = genai.Client(api_key=api_key)
    prompt = build_gemini_prompt()

    image_records = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
        page = context.new_page()

        print("=== トクバイ スクレイピング開始 ===")
        scrape_tokubai(context, page, image_records)

        print("=== ロヂャース スクレイピング開始 ===")
        scrape_rogers(context, page, image_records)

        browser.close()

    print(f"=== 画像取得完了: 合計{len(image_records)}枚。Gemini APIで解析します ===")

    results = []
    for record in image_records:
        local_path = record["local_path"]
        if not os.path.exists(local_path):
            continue
        print(f"[OCR] {record['store']}: {os.path.basename(local_path)}")
        items = call_gemini(client, local_path, prompt)
        for item in items:
            item["store"] = record["store"]
            row = build_result_row(item)
            if row:
                results.append(row)
        time.sleep(1.5)

    results_path = os.path.join(DOWNLOAD_DIR, "results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"=== 完了: {len(results)}件を書き出しました: {results_path} ===")


if __name__ == "__main__":
    main()
