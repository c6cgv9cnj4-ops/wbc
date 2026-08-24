# -*- coding: utf-8 -*-
"""
底値トラッカー(コア品目マスター約35品目) 自動収集スクリプト

対象店舗(現時点で実装済みなのは以下2店舗。他店舗は個別調査のうえ順次追加する):
  - ロヂャース北本店: 公式サイト経由のWebビューアー(cms.mechao.tv、タイル画像結合)
  - マルサン桶川店・ヤオコー・ベルク・ウエルシア: トクバイ(tokubai.co.jp)
    (ヤオコー/ベルク/ウエルシアはtokubai上に該当店舗ページ自体はあるが、
     時期によっては「チラシ掲載なし」になることがある。トクバイ以外の
     代替取得先(公式サイト/Shufoo!等)は、実際のサイト構造を調査してから
     個別に追加実装する)

処理の流れ:
  1. 各店舗のチラシ画像を取得(price_bot/scripts/scraper.py の
     実証済みスクレイピング処理を再利用する。トクバイの店舗解決・
     ロヂャースのタイル結合ロジックは重複実装しない)
  2. コア品目マスター(DEALS_ITEMS)の正規化マッチングにより、チラシ内の
     表記ゆれ(銘柄名・入り数・容量表記等)を吸収しつつGemini APIで
     メーカー名・価格・規格・特売種別を抽出
  3. 規格テキストを解析して単位単価(100g/100ml/1m/1個/1kg/1ロール/1回あたり等)を計算
  4. gas/create_price_tracker.gs の doGet ウェブアプリへGETリクエストで送信し、
     ダッシュボードの該当商品行を動的に追加/更新 + 価格ログ蓄積タブへの追記を行う

コア品目マスターに一致しない商品(酒・飲料・菓子・惣菜・加工肉・麺類・冷凍品・
瓶類・チルド等)はGemini側のプロンプトで明示的に除外を指示し、万一該当外の
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
# ジャンル定義
# ============================================================

GENRE_VEGETABLE = "生鮮（野菜・果物）"
GENRE_MEAT = "生鮮（精肉）"
GENRE_STAPLE = "主食・米"
GENRE_SEASONING = "調味料・油・日配"
GENRE_PAPER_ETC = "日用品・紙類・飲料"
GENRE_HAIRCARE = "日用品・ヘアケア"

# ドラッグストア枠(ウエルシア等)で対象にするジャンル。
# 「生鮮野菜や肉は除外し、ペーパー類・洗剤・調味料・飲料に絞って比較」という
# 運用方針(GENRE_VEGETABLE / GENRE_MEAT / GENRE_STAPLE は含めない)。
DRUGSTORE_ALLOWED_GENRES = {GENRE_SEASONING, GENRE_PAPER_ETC, GENRE_HAIRCARE}

# ============================================================
# コア品目マスター(約35品目)
# gas/create_price_tracker.gs はジャンル/品目を持たず商品名の完全一致で
# 動的に行を作成/更新する設計のため、このリストが唯一の正規化ルールになる。
# ============================================================

DEALS_ITEMS = [
    # ---- 生鮮(野菜・果物) ----
    {"genre": GENRE_VEGETABLE, "name": "キャベツ", "unit": "1玉",
     "measure_type": "count", "count_units": ["玉", "個"], "keywords": ["キャベツ"]},
    {"genre": GENRE_VEGETABLE, "name": "レタス", "unit": "1玉",
     "measure_type": "count", "count_units": ["玉", "個"], "keywords": ["レタス"]},
    {"genre": GENRE_VEGETABLE, "name": "白菜", "unit": "1玉",
     "measure_type": "count", "count_units": ["玉", "個", "カット"], "keywords": ["白菜", "ハクサイ"]},
    {"genre": GENRE_VEGETABLE, "name": "玉ねぎ", "unit": "1kg",
     "measure_type": "weight_kg", "keywords": ["玉ねぎ", "たまねぎ", "タマネギ"]},
    {"genre": GENRE_VEGETABLE, "name": "じゃがいも", "unit": "1kg",
     "measure_type": "weight_kg", "keywords": ["じゃがいも", "ジャガイモ", "馬鈴薯"]},
    {"genre": GENRE_VEGETABLE, "name": "人参", "unit": "1袋",
     "measure_type": "count", "count_units": ["袋", "個"], "keywords": ["人参", "にんじん", "ニンジン"]},
    {"genre": GENRE_VEGETABLE, "name": "大根", "unit": "1本",
     "measure_type": "count", "count_units": ["本"], "keywords": ["大根", "だいこん", "ダイコン"]},
    {"genre": GENRE_VEGETABLE, "name": "長ねぎ", "unit": "1束",
     "measure_type": "count", "count_units": ["束", "本"], "keywords": ["長ねぎ", "長ネギ", "ねぎ"]},
    {"genre": GENRE_VEGETABLE, "name": "きゅうり", "unit": "1袋",
     "measure_type": "count", "count_units": ["袋", "本"], "keywords": ["きゅうり", "キュウリ", "胡瓜"]},
    {"genre": GENRE_VEGETABLE, "name": "トマト", "unit": "1袋",
     "measure_type": "count", "count_units": ["袋", "個"], "keywords": ["トマト"]},
    {"genre": GENRE_VEGETABLE, "name": "ほうれん草", "unit": "1束",
     "measure_type": "count", "count_units": ["束", "袋"], "keywords": ["ほうれん草", "ホウレンソウ"]},
    {"genre": GENRE_VEGETABLE, "name": "小松菜", "unit": "1束",
     "measure_type": "count", "count_units": ["束", "袋"], "keywords": ["小松菜", "コマツナ"]},
    {"genre": GENRE_VEGETABLE, "name": "ブロッコリー", "unit": "1株",
     "measure_type": "count", "count_units": ["株", "個"], "keywords": ["ブロッコリー"]},
    {"genre": GENRE_VEGETABLE, "name": "もやし", "unit": "1袋",
     "measure_type": "count", "count_units": ["袋"], "keywords": ["もやし", "モヤシ"]},
    {"genre": GENRE_VEGETABLE, "name": "えのき/しめじ", "unit": "1パック",
     "measure_type": "count", "count_units": ["パック", "個"], "keywords": ["えのき", "エノキ", "しめじ", "シメジ"]},

    # ---- 生鮮(精肉) ----
    {"genre": GENRE_MEAT, "name": "若鶏モモ肉", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["若鶏", "モモ肉", "鶏もも", "国産鶏"]},
    {"genre": GENRE_MEAT, "name": "豚バラ切落し", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["豚バラ", "切り落とし", "切落し", "国産豚"]},
    {"genre": GENRE_MEAT, "name": "豚こま切れ", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["豚こま", "こま切れ", "コマ切れ"]},

    # ---- 主食・米 ----
    # 銘柄名(コシヒカリ/ひとめぼれ等)や無洗米等の表記ゆれは、build_deals_prompt()の
    # 指示とkeywordsの両方で「白米 5kg/10kg」に正規化して吸収する。
    {"genre": GENRE_STAPLE, "name": "白米 5kg", "unit": "100g",
     "measure_type": "weight_g",
     "keywords": ["白米", "米 5kg", "こめ 5kg", "コシヒカリ", "ひとめぼれ", "あきたこまち",
                  "ななつぼし", "ブレンド米", "複数原料米", "無洗米"]},
    {"genre": GENRE_STAPLE, "name": "白米 10kg", "unit": "100g",
     "measure_type": "weight_g",
     "keywords": ["白米", "米 10kg", "こめ 10kg", "コシヒカリ", "ひとめぼれ", "あきたこまち",
                  "ななつぼし", "ブレンド米", "複数原料米", "無洗米"]},

    # ---- 調味料・油・日配 ----
    {"genre": GENRE_SEASONING, "name": "生しょうゆ", "unit": "100ml",
     "measure_type": "volume_ml", "keywords": ["しょうゆ", "醤油", "生しょうゆ"]},
    {"genre": GENRE_SEASONING, "name": "キャノーラ油/サラダ油", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["キャノーラ油", "サラダ油", "食用油"]},
    {"genre": GENRE_SEASONING, "name": "純正ごま油", "unit": "100g",
     "measure_type": "weight_g", "keywords": ["ごま油", "純正ごま油", "かどや"]},
    {"genre": GENRE_SEASONING, "name": "ノンオイルドレッシング", "unit": "100ml",
     "measure_type": "volume_ml", "keywords": ["ノンオイル", "ドレッシング"]},
    {"genre": GENRE_SEASONING, "name": "料理酒/みりん", "unit": "100ml",
     "measure_type": "volume_ml", "keywords": ["料理酒", "みりん", "本みりん"]},
    {"genre": GENRE_SEASONING, "name": "納豆", "unit": "3パック",
     "measure_type": "count_basis_n", "count_basis": 3, "count_units": ["パック", "個"],
     "keywords": ["納豆"]},
    {"genre": GENRE_SEASONING, "name": "食パン", "unit": "1斤",
     "measure_type": "count", "count_units": ["斤"], "keywords": ["食パン"]},

    # ---- 日用品・紙類・飲料 ----
    {"genre": GENRE_PAPER_ETC, "name": "トイレットペーパー(12ロール等)", "unit": "1m",
     "measure_type": "toilet_paper", "keywords": ["トイレットペーパー"]},
    {"genre": GENRE_PAPER_ETC, "name": "キッチンペーパー(4ロール等)", "unit": "1ロール",
     "measure_type": "count_roll", "count_units": ["ロール", "本"], "keywords": ["キッチンペーパー"]},
    {"genre": GENRE_PAPER_ETC, "name": "洗濯用洗剤", "unit": "1回",
     "measure_type": "detergent", "count_units": ["回"], "keywords": ["洗濯用洗剤", "洗濯洗剤", "ジェルボール"]},
    {"genre": GENRE_PAPER_ETC, "name": "柔軟剤", "unit": "100ml",
     "measure_type": "volume_ml", "keywords": ["柔軟剤"]},
    {"genre": GENRE_PAPER_ETC, "name": "炭酸水", "unit": "500ml",
     "measure_type": "volume_ml_basis", "volume_basis_ml": 500, "keywords": ["炭酸水", "スパークリングウォーター"]},

    # ---- 日用品・ヘアケア ----
    {"genre": GENRE_HAIRCARE, "name": "シャンプー", "unit": "100ml",
     "measure_type": "volume_ml", "keywords": ["シャンプー"]},
    {"genre": GENRE_HAIRCARE, "name": "コンディショナー/トリートメント", "unit": "100ml",
     "measure_type": "volume_ml", "keywords": ["コンディショナー", "トリートメント", "リンス"]},
    {"genre": GENRE_HAIRCARE, "name": "ボディーソープ", "unit": "100ml",
     "measure_type": "volume_ml", "keywords": ["ボディーソープ", "ボディソープ"]},
]

# 明示的に除外するジャンル(Geminiへのプロンプトで念押しする)
EXCLUDED_GENRES_TEXT = "酒・飲料(炭酸水以外)・菓子・惣菜・加工肉・麺類・冷凍品・瓶詰め・チルド食品"

DEALS_STORES = [
    {"name": "ロヂャース北本店", "source": "rogers",
     "mechao_store_id": "50098efb7172597ee67c0a957a43a0cb", "category_scope": None},
    {"name": "マルサン桶川店", "source": "tokubai",
     "tokubai_query": "スーパーマルサン桶川店", "category_scope": None},
    {"name": "ヤオコー", "source": "tokubai",
     "tokubai_query": "ヤオコー北本中央店", "category_scope": None},
    {"name": "ベルク", "source": "tokubai",
     "tokubai_query": "ベルク北本東間店", "category_scope": None},
    {"name": "ウエルシア", "source": "tokubai",
     "tokubai_query": "ウエルシア北本中丸店", "category_scope": DRUGSTORE_ALLOWED_GENRES},
    # とりせん北本店: 公式サイト(torisen.co.jp/shop/)がtokubai.co.jpへの店舗ウィジェット
    # (offices/119)を掲載していたことから発見。実際にresolve_tokubai_store_path()で
    # 店舗解決・チラシ画像URL取得まで動作確認済み(2026-08-25実施)。
    {"name": "とりせん", "source": "tokubai",
     "tokubai_query": "とりせん 北本店", "category_scope": None},
    # 業務スーパー・ヤオコー公式・ベルク・ウエルシア公式・ヨークマートは、
    # 実サイト調査のうえ個別のスクレイパーを実装してからここに追加する
    # (未調査のまま追加すると動かないコードになるため保留)。
    # 調査結果メモ:
    #   - ヤオコー公式(yaoko-net.com): この実行環境からTCP接続タイムアウトで到達不可
    #   - Shufoo!(shufoo.net): 店舗名検索で「ヤオコー」自体は0件(テナント店のみヒット)。
    #     ヤオコー本体はShufoo!に出店していないため経由不可と判断。
    #   - とりせん(torisen.jp): 公式(torisen.co.jp)とは無関係の別サイトだったため使用禁止。
    #   - 業務スーパー(gyomuu.com)・ヨークマート(yorkmart.co.jp): TCP接続タイムアウトで到達不可
]

# category_scope=None は「全ジャンルが対象(絞り込みなし)」を意味する。


def get_items_for_store(store):
    scope = store.get("category_scope")
    if scope is None:
        return DEALS_ITEMS
    return [item for item in DEALS_ITEMS if item["genre"] in scope]


def get_item_by_name(name, allowed_items=None):
    pool = allowed_items if allowed_items is not None else DEALS_ITEMS
    for item in pool:
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
# Gemini APIによるコア品目マスターの正規化抽出
# ============================================================

def build_deals_prompt(store):
    items_for_store = get_items_for_store(store)
    lines = []
    for item in items_for_store:
        lines.append(f"- ジャンル:{item['genre']} / 商品名:{item['name']} / 比較単位:{item['unit']} "
                      f"/ 関連キーワード:{', '.join(item['keywords'])}")
    item_list_text = "\n".join(lines)

    return f"""あなたはスーパー・ドラッグストアのチラシ画像から特売情報を抽出するアシスタントです。
以下の「厳選対象品目リスト」に完全に合致する商品が、このチラシ画像に写っている
場合のみ抽出してください。リストに無いジャンルの商品(特に{EXCLUDED_GENRES_TEXT}等)は
絶対に抽出しないでください。

【厳選対象品目リスト】
{item_list_text}

【表記ゆれの正規化について(特に主食・米)】
チラシ上の表記が「コシヒカリ」「ひとめぼれ」「あきたこまち」「ななつぼし」
「ブレンド米」「複数原料米」「無洗米」等の具体的な銘柄名であっても、精米された
食用の米(白米)であれば対象に含めてください。銘柄名に関わらず、内容量が5kgの
ものは product_name を「白米 5kg」、10kgのものは「白米 10kg」としてください
(銘柄名は maker に記載し、product_name には含めないでください)。

【出力形式】
JSON配列のみを出力してください(説明文・Markdownのコードブロック記号は不要です)。
該当商品が1つもない場合は空配列 [] を返してください。
各要素は以下のキーを持つオブジェクトにしてください:
- "genre": 上記リストのジャンル名をそのまま使う
- "product_name": 上記リストの商品名を完全一致するものだけ使う(曖昧なら含めない)
- "maker": メーカー名・銘柄名(読み取れない場合はnull)
- "price_yen": 税込価格(数値のみ。読み取れない場合はnull)
- "package_size_text": パッケージ規格・内容量の原文(例: "1000ml","400g","5kg","1玉","4ロール",
   "3パック","12回分"等。読み取れない場合はnull)
- "deal_type": 特売の種別が読み取れれば記載(例: "平日市","週末朝市","週末セール","通常"等。
   不明な場合は"通常")
- "confidence": "high" または "low"

数値や規格を推測で埋めないでください。読み取れないものは必ずnullにしてください。
"""


# ============================================================
# 単位単価の計算
# ============================================================

_WEIGHT_MULTIPLY_KG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[kK]g\s*[×xX]\s*(\d+)")
_WEIGHT_MULTIPLY_G_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:g|グラム)\s*[×xX]\s*(\d+)")


def parse_g_extended(text):
    """price_bot側のparse_g()を拡張し、'100g×3パック'のような複数パック表記に対応する。
    重量そのものがどこにも記載されていない('5枚入'のみ等)場合は、推測で埋めずNoneのまま返す。
    """
    if not text:
        return None
    m = _WEIGHT_MULTIPLY_KG_RE.search(text)
    if m:
        return float(m.group(1)) * 1000 * int(m.group(2))
    m = _WEIGHT_MULTIPLY_G_RE.search(text)
    if m:
        return float(m.group(1)) * int(m.group(2))
    return parse_g(text)


def _piece_count_only_hint(text):
    """'5枚入'のように枚数/パック数のみで重量記載が無いテキストかどうかの補助判定。
    (SKIP理由をより具体的にするためだけに使う。パース成功可否には影響しない)
    """
    if not text:
        return ""
    has_piece_word = re.search(r"(枚|パック|本)\s*入", text)
    has_weight = re.search(r"\d+\s*(g|kg|グラム)", text)
    if has_piece_word and not has_weight:
        return "(重量の記載がなく枚数/パック数のみのため、推測せずスキップ)"
    return ""


def compute_unit_price(item, price_yen, package_size_text):
    if price_yen is None:
        return None, "価格が読み取れませんでした"

    measure_type = item["measure_type"]

    if measure_type == "volume_ml":
        ml = parse_ml(package_size_text)
        if not ml:
            return None, f"内容量(ml/L)を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / ml * 100, 1), ""

    if measure_type == "volume_ml_basis":
        ml = parse_ml(package_size_text)
        if not ml:
            return None, f"内容量(ml/L)を読み取れませんでした: '{package_size_text}'"
        basis = item.get("volume_basis_ml", 100)
        return round(price_yen / ml * basis, 1), ""

    if measure_type == "weight_g":
        g = parse_g_extended(package_size_text)
        if not g:
            hint = _piece_count_only_hint(package_size_text)
            return None, f"内容量(g/kg)を読み取れませんでした: '{package_size_text}'{hint}"
        return round(price_yen / g * 100, 1), ""

    if measure_type == "weight_kg":
        g = parse_g_extended(package_size_text)
        if not g:
            hint = _piece_count_only_hint(package_size_text)
            return None, f"内容量(g/kg)を読み取れませんでした: '{package_size_text}'{hint}"
        return round(price_yen / g * 1000, 1), ""

    if measure_type == "count":
        n = parse_count(package_size_text, item.get("count_units", ["個"]))
        n = n or 1  # 単品(1玉/1個)の特売なら規格テキストに個数が出ないことも多い
        return round(price_yen / n, 1), ""

    if measure_type == "count_basis_n":
        basis = item.get("count_basis", 1)
        n = parse_count(package_size_text, item.get("count_units", ["個"]))
        if n:
            return round(price_yen / n * basis, 1), ""
        # 個数の明記が無い場合、標準梱包数(count_basis)そのままの特売と仮定する
        # (トイレットペーパーの倍巻き推定と同様の「低信頼度・要確認」フォールバック)
        return round(price_yen, 1), f"個数の記載がないため標準梱包数({basis}個入り)と仮定した推定値のため要確認"

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

    if measure_type == "detergent":
        # 洗剤は「1回あたり」を基準にする(粉末はg、液体はml等、商品によって
        # 表記単位がバラバラで、g/mlベースに揃えると店舗間比較の基準が崩れるため、
        # チラシに「○回分」の記載がある場合のみ扱う。無い場合は推測せずスキップする)
        n = parse_count(package_size_text, item.get("count_units", ["回"]))
        if not n:
            return None, f"回数(○回分)の記載を読み取れませんでした: '{package_size_text}'"
        return round(price_yen / n, 1), ""

    return None, f"未対応の測定タイプ: {measure_type}"


def build_result_item(raw_item, store):
    allowed_items = get_items_for_store(store)
    item = get_item_by_name(raw_item.get("product_name", ""), allowed_items)
    if not item:
        return None  # コア品目マスター(または当該店舗のジャンル対象外)は黙って除外する(ノイズ排除)

    price_yen = raw_item.get("price_yen")
    unit_price, note = compute_unit_price(item, price_yen, raw_item.get("package_size_text"))
    if unit_price is None or raw_item.get("confidence") == "low":
        print(f"[SKIP] {item['name']}({store['name']}): {note or 'OCR信頼度が低い'}")
        return None

    return {
        "genre": item["genre"],
        "maker": raw_item.get("maker") or "-",
        "product_name": item["name"],
        "spec": raw_item.get("package_size_text") or "-",
        "unit": item["unit"],
        "store": store["name"],
        "unit_price": unit_price,
        "raw_price": price_yen,
        "deal_type": raw_item.get("deal_type") or "通常",
        "memo": note or "",
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

    store_by_name = {s["name"]: s for s in DEALS_STORES}
    prompt_cache = {}

    results = []
    for record in image_records:
        local_path = record["local_path"]
        if not os.path.exists(local_path):
            continue
        store = store_by_name[record["store"]]
        if store["name"] not in prompt_cache:
            prompt_cache[store["name"]] = build_deals_prompt(store)
        prompt = prompt_cache[store["name"]]

        print(f"[OCR] {record['store']}: {os.path.basename(local_path)}")
        raw_items = call_gemini(client, local_path, prompt)
        for raw_item in raw_items:
            result = build_result_item(raw_item, store)
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
