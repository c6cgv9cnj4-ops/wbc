# -*- coding: utf-8 -*-
"""
商品マスタ・店舗マスタ（Pythonパイプライン用）
Googleスプレッドシート「商品マスタ」「店舗マスタ」シートと内容を一致させておくこと。
"""

# 商品名: (単位, Amazon基準単価, 単位のグラム/ml/個数換算に使う正規表現ヒント)
# unit_basis: 換算先の単位("100ml","100g","10g","1袋","1枚","1m")
PRODUCTS = [
    {
        "name": "キッコーマン 濃いだし本つゆ (1L基準)",
        "unit_basis": "100ml",
        "amazon_base_price": 39.8,
        "keywords": ["濃いだし", "本つゆ", "キッコーマン"],
        "measure_type": "volume_ml",
    },
    {
        "name": "ミツカン カンタン酢 (1L基準)",
        "unit_basis": "100ml",
        "amazon_base_price": 39.8,
        "keywords": ["カンタン酢", "ミツカン"],
        "measure_type": "volume_ml",
    },
    {
        "name": "減塩味噌4種(タニタ/ひかり/フンドーキン/マルサンアイ 650g基準)",
        "unit_basis": "100g",
        "amazon_base_price": 48.9,
        "keywords": ["減塩みそ", "減塩味噌", "タニタ食堂", "信州こうじみそ", "あわせみそ", "純正こうじみそ"],
        "measure_type": "weight_g",
    },
    {
        "name": "かどや 純正ごま油 (400g/200g基準)",
        "unit_basis": "100g",
        "amazon_base_price": 135.0,
        "keywords": ["かどや", "純正ごま油", "ごま油"],
        "measure_type": "weight_g",
    },
    {
        "name": "エキストラバージンオリーブオイル (400g〜基準)",
        "unit_basis": "100g",
        "amazon_base_price": 195.0,
        "keywords": ["エキストラバージンオリーブオイル", "オリーブオイル"],
        "measure_type": "weight_g",
    },
    {
        "name": "ヒガシマル 牡蠣だし醤油 (400ml基準)",
        "unit_basis": "100ml",
        "amazon_base_price": 92.0,
        "keywords": ["牡蠣だし醤油", "ヒガシマル"],
        "measure_type": "volume_ml",
    },
    {
        "name": "ヒガシマル うどんスープ (袋基準)",
        "unit_basis": "1袋",
        "amazon_base_price": 19.0,
        "keywords": ["うどんスープ", "ヒガシマル"],
        "measure_type": "count_bag",
    },
    {
        "name": "創味シャンタン/味覇 (缶・チューブ基準)",
        "unit_basis": "100g",
        "amazon_base_price": 159.6,
        "keywords": ["創味シャンタン", "味覇", "ウェイパー"],
        "measure_type": "weight_g",
    },
    {
        "name": "S&B 李錦記 オイスターソース (255g基準)",
        "unit_basis": "100g",
        "amazon_base_price": 156.0,
        "keywords": ["オイスターソース", "李錦記"],
        "measure_type": "weight_g",
    },
    {
        "name": "S&B カレー粉 (赤缶 84g基準)",
        "unit_basis": "10g",
        "amazon_base_price": 48.8,
        "keywords": ["カレー粉", "赤缶", "S&B"],
        "measure_type": "weight_g",
    },
    {
        "name": "かつお粉・だし粉 (100g袋基準)",
        "unit_basis": "10g",
        "amazon_base_price": 38.0,
        "keywords": ["かつお粉", "だし粉", "だしの素"],
        "measure_type": "weight_g",
    },
    {
        "name": "お徳用 おろし生しょうが (160g〜基準)",
        "unit_basis": "100g",
        "amazon_base_price": 186.2,
        "keywords": ["おろし生しょうが", "おろししょうが", "お徳用しょうが"],
        "measure_type": "weight_g",
    },
    {
        "name": "お徳用 おろしにんにく (160g〜基準)",
        "unit_basis": "100g",
        "amazon_base_price": 186.2,
        "keywords": ["おろしにんにく", "お徳用にんにく"],
        "measure_type": "weight_g",
    },
    {
        "name": "トイレットペーパー (2〜3倍長巻き特化)",
        "unit_basis": "1m",
        "amazon_base_price": 0.83,
        "keywords": ["トイレットペーパー", "3倍巻き", "2.5倍巻き", "長巻き"],
        "measure_type": "toilet_paper",
    },
    {
        "name": "ジップロック フリーザーバッグ Sサイズ",
        "unit_basis": "1枚",
        "amazon_base_price": 11.0,
        "keywords": ["ジップロック", "フリーザーバッグ", "Sサイズ"],
        "measure_type": "count_sheet",
    },
    {
        "name": "ジップロック フリーザーバッグ Mサイズ",
        "unit_basis": "1枚",
        "amazon_base_price": 10.8,
        "keywords": ["ジップロック", "フリーザーバッグ", "Mサイズ"],
        "measure_type": "count_sheet",
    },
    {
        "name": "ジップロック フリーザーバッグ Lサイズ",
        "unit_basis": "1枚",
        "amazon_base_price": 20.4,
        "keywords": ["ジップロック", "フリーザーバッグ", "Lサイズ"],
        "measure_type": "count_sheet",
    },
    {
        "name": "食器用洗剤 大容量詰替 (キュキュット/Magica/JOY)",
        "unit_basis": "100ml",
        "amazon_base_price": 39.8,
        "keywords": ["キュキュット", "Magica", "マジカ", "JOY", "ジョイ", "食器用洗剤", "詰め替え"],
        "measure_type": "volume_ml",
    },
    {
        "name": "液体洗濯洗剤 超特大詰替 (アタック/アリエール/NANOX ※ジェルボール除外)",
        "unit_basis": "100g",
        "amazon_base_price": 65.7,
        "keywords": ["アタック", "アリエール", "NANOX", "ナノックス", "液体洗濯洗剤"],
        "measure_type": "weight_g",
        "exclude_keywords": ["ジェルボール", "ジェルボール4D"],
    },
    {
        "name": "（予約枠）柔軟剤 ※銘柄未確定",
        "unit_basis": "100ml",
        "amazon_base_price": None,
        "keywords": [],
        "measure_type": "volume_ml",
        "active": False,
    },
]

# 店舗マスタ: name はダッシュボードのC列(店舗名)と完全一致させる
# tokubai_query は https://tokubai.co.jp の shop_fetcher[free_word_query] にそのまま渡す文字列
#   scrape_target=False の店舗は自動スクレイピングの対象外(トクバイにチラシが
#   出ないディスカウント業態など)。ダッシュボードのC列ドロップダウンには引き続き
#   表示されるので、手動入力の予備枠として使う(固定の「平常安値」を把握している
#   場合はfixed_reference_priceに1品目分の目安を仮置きしてもよい)。
STORES = [
    {"name": "ロヂャース北本店", "area": "北本",
     "scrape_target": "rogers_viewer",
     "mechao_store_id": "50098efb7172597ee67c0a957a43a0cb",
     "active": True,
     "note": "トクバイには掲載がないが、公式サイト(rogers.co.jp/chirashi/)経由の"
             "Webビューアー(cms.mechao.tv)でチラシが確認できたため、専用スクレイパー"
             "(scrape_rogers.py)で自動巡回する。store_idはタイル画像URLではなく"
             "rogers.co.jp/chirashi/掲載の店舗一覧リンクから特定した固定値。"},
    {"name": "スーパーマルサン桶川店", "area": "桶川", "tokubai_query": "スーパーマルサン桶川店",
     "active": True, "scrape_target": True},
    {"name": "ベルク北本東間店", "area": "北本", "tokubai_query": "ベルク北本東間店",
     "active": True, "scrape_target": True},
    {"name": "ヤオコー北本中央店", "area": "北本", "tokubai_query": "ヤオコー北本中央店",
     "active": True, "scrape_target": True},
    {"name": "コープ北本店", "area": "北本", "tokubai_query": "コープみらい北本店",
     "active": True, "scrape_target": True},
    {"name": "業務スーパー北本店", "area": "北本", "tokubai_query": "業務スーパー北本店",
     "active": True, "scrape_target": True},
    {"name": "ドラッグストアコスモス北本本宿店", "area": "北本", "tokubai_query": "ドラッグストアコスモス北本本宿店",
     "active": True, "scrape_target": True},
    {"name": "ドラッグストアコスモス北本店", "area": "北本", "tokubai_query": "ドラッグストアコスモス北本店",
     "active": True, "scrape_target": True},
    {"name": "ドラッグストアセキ", "area": "北本", "tokubai_query": "ドラッグストアセキ北本本町店",
     "active": True, "scrape_target": True},
    {"name": "スギ薬局", "area": "北本", "tokubai_query": "スギ薬局北本南店",
     "active": True, "scrape_target": True},
    {"name": "ウエルシア", "area": "北本", "tokubai_query": "ウエルシア北本中丸店",
     "active": True, "scrape_target": True},
]

UNIT_BASIS_TO_GRAMS_ML = {
    "100ml": 100,
    "100g": 100,
    "10g": 10,
    # 1袋・1枚・1mは個数系のためグラム換算しない(measure_typeで別処理)
}


def get_product_by_name(name):
    for p in PRODUCTS:
        if p["name"] == name:
            return p
    return None


def get_active_products():
    return [p for p in PRODUCTS if p.get("active", True)]


def get_active_stores():
    """店舗マスタ全体(手動入力の予備枠を含む)。ダッシュボードのドロップダウン等に使う。"""
    return [s for s in STORES if s.get("active", True)]


def get_scrape_target_stores():
    """トクバイ経由で自動スクレイピングする店舗(scrape_target=Trueのもの)。"""
    return [s for s in get_active_stores() if s.get("scrape_target", True) is True]


def get_rogers_viewer_stores():
    """ロヂャース専用Webビューアー(mechao.tv)経由で自動スクレイピングする店舗。"""
    return [s for s in get_active_stores() if s.get("scrape_target") == "rogers_viewer"]
