# -*- coding: utf-8 -*-
"""
チラシ取得〜Gemini価格解析の共用モジュール(scripts/fetch_deals.py から import)

提供する機能:
  - トクバイ / ロヂャース(cms.mechao.tv)のチラシ画像取得用ヘルパー
  - Gemini APIへの画像送信・JSON応答の解析
  - 規格テキスト(ml / g / 枚数 / トイレットペーパー長さ)の数値化

単独実行はしない。Googleサービスアカウント・GCPプロジェクトは使用しない。

環境変数:
  GEMINI_API_KEY (必須, 呼び出し元が Gemini を実行する場合)
"""
import json
import os
import re
import sys
import urllib.parse
from io import BytesIO

from google.genai import types as genai_types
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import gemini_guard  # noqa: E402

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
GEMINI_MODEL_NAME = "gemini-2.0-flash"
# Geminiへ送るチラシ画像の長辺上限(px)。結合画像は長辺2000〜4000px規模になる
# ことがあり、そのまま送ると画像入力トークン(SKU: ... gemini image)が膨らむ。
# 価格・規格のOCR用途なら1024pxで読み取り精度はほぼ落ちない。
GEMINI_IMAGE_MAX_EDGE_PX = 1024
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




# ============================================================
# 3. Gemini APIによる価格抽出
# ============================================================



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


def downscale_for_gemini(image_bytes, max_edge=GEMINI_IMAGE_MAX_EDGE_PX):
    """Geminiに渡す前にチラシ画像を長辺 max_edge px 以下へ縮小してJPEGで返す。

    画像入力トークン数は解像度に比例して増えるため、送信直前に縮小して課金を
    抑える。元から max_edge 以下なら何もせずそのまま返す。デコードできない
    バイト列が来た場合も、握りつぶさず元データをそのまま返す(従来動作を維持)。
    """
    try:
        img = Image.open(BytesIO(image_bytes))
        img.load()
    except Exception:  # noqa: BLE001  画像として開けないものは従来どおり素通し
        return image_bytes

    long_edge = max(img.size)
    if long_edge <= max_edge:
        return image_bytes

    scale = max_edge / long_edge
    new_size = (max(1, round(img.size[0] * scale)), max(1, round(img.size[1] * scale)))
    resized = img.convert("RGB").resize(new_size, Image.Resampling.LANCZOS)
    out = BytesIO()
    resized.save(out, format="JPEG", quality=85)
    return out.getvalue()


def call_gemini(client, image_path, prompt, guard=None):
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    original_len = len(image_bytes)
    image_bytes = downscale_for_gemini(image_bytes)
    if len(image_bytes) != original_len:
        print(f"[gemini] 画像を縮小: {original_len // 1024}KB -> {len(image_bytes) // 1024}KB "
              f"(長辺{GEMINI_IMAGE_MAX_EDGE_PX}px上限)")
    image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    guard = guard or gemini_guard.Guard({})
    try:
        response = guard.call(lambda: client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=[prompt, image_part],
            config=genai_types.GenerateContentConfig(temperature=0.1),
        ), task="price_bot_ocr")
        return extract_json_array(response.text)
    except gemini_guard.GeminiBlocked as err:
        print(f"[WARN][gemini] 待機中のため呼び出しを省略: {err}")
    except gemini_guard.GeminiCallError as err:
        print(f"[WARN][gemini] 呼び出し失敗(再試行後): {err}")
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










# ============================================================
# main
# ============================================================



