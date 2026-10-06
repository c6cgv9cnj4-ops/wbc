# -*- coding: utf-8 -*-
"""
「埼玉・県央ローカルニュース」(#webhook_local)の記事選別(2026-10-06)。

目的: #webhook_local を「埼玉県全体のニュース」ではなく
「北本を中心に、北本・鴻巣・桶川周辺で、地元の人との会話材料になるニュース」にする。
「地域名が入っている=地域ニュース」にはしない。タイトル(と配信元)から次の順で判定する。

  1. 地域: 北本市 > 鴻巣市・桶川市(中心) > 上尾市・久喜市・伊奈町・蓮田市(生活圏) > その他
     さいたま市・県全体・全国の記事は、北本・鴻巣・桶川が出てこなければ除外する
  2. ノイズ: 中古車・買取、ランキング、天気・地震の一覧、全国チェーンの商品・店舗情報は
     地域名があっても除外する(例: 「北本市のガリバーで中古車入荷」)
  3. 全国向けPR・転載サイト: 北本・鴻巣・桶川が出て、かつ地域の話題の手がかりがある場合だけ残す
  4. 生活圏の周辺市(上尾など): 行政・学校・イベント・消防などの手がかりがある場合だけ残す
  5. 北本市公式note・号外NET(鴻巣・北本/上尾・桶川)の直接購読は地域の一次・専門情報として優先する

防災・緊急情報(公式Mastodon経由)はこのフィルタの対象外(別経路)。
判定は説明可能なルールだけ(LLMは使わない)。除外理由は reason に残し、ログで確認できる。
"""
import re
from urllib.parse import urlsplit

CORE = ["北本", "鴻巣", "桶川"]                      # 中心(北本が最優先)
NEARBY = ["上尾", "久喜", "伊奈町", "蓮田"]            # 北本周辺の生活圏

# 直接購読フィード(地域の一次・専門情報)。タイトルに市名が無い場合の既定の地域を持つ
DIRECT_FEEDS = {
    "kitamoto-city.note.jp": ("北本", True),           # 北本市公式note(自治体の一次情報)
    "kounosu-kitamoto.goguynet.jp": ("鴻巣・北本", True),
    "ageo-okegawa.goguynet.jp": ("上尾・桶川", False),   # 上尾・桶川版。桶川以外は生活圏扱い
}

# 地域の話題の手がかり(生活圏の周辺市・PR/転載サイトの記事に求める)
LOCAL_SIGNALS = [
    "市役所", "市議会", "議会", "市長", "町長", "市民", "町民", "広報", "行政", "条例", "予算", "財政", "選挙", "投票",
    "消防", "消防団", "警察", "防災", "防犯", "自治会", "町内会", "商工会", "社協", "社会福祉協議会", "ボランティア",
    "学校", "小学校", "中学校", "高校", "高等学校", "保育", "幼稚園", "給食", "運動会", "文化祭", "図書館", "公民館",
    "祭", "まつり", "イベント", "フェスティバル", "マルシェ", "開催", "パレード", "音楽祭", "花火",
    "公園", "道路", "交通", "路線バス", "コミュニティバス", "バス停", "駅前", "再開発", "区画整理", "都市計画", "工事", "開通", "渋滞", "通行止",
    "クラウドファンディング", "地域", "地元", "観光", "名産", "特産", "伝統", "文化財", "史跡", "神社", "寺",
    "福祉", "介護", "子育て", "移住", "空き家", "農家", "農業", "トマト", "ふるさと",
    "寄付", "寄贈", "連携協定", "協定",
]

# --- ノイズ ---------------------------------------------------------------
USED_CAR_SOURCES = ("中古車のガリバー", "ガリバー", "rakumachi.jp")
USED_CAR_RE = re.compile(r"中古車|年式|入荷しました|高価買取|無料査定|買取フェア|一棟マンション|物件情報")
RANKING_RE = re.compile(r"ランキング|ランクイン|人気記事|アクセス上位")
WEATHER_QUAKE_RE = re.compile(r"震度|地震詳細|ライブカメラ|最高\d+℃|最低\d+℃|天気予報|予想【朝版】")
CHAINS = ["ガリバー", "しまむら", "ニトリ", "ユニクロ", "ダイソー", "セリア", "ドン・キホーテ", "ドンキ", "マクドナルド",
          "ケンタッキー", "モスバーガー", "吉野家", "すき家", "松屋", "日高屋", "バーミヤン", "ガスト", "サイゼリヤ", "ココス",
          "ココイチ", "CoCo壱", "スターバックス", "コメダ", "ドトール", "セブン-イレブン", "セブンイレブン", "ローソン",
          "ファミリーマート", "ファミマ", "まいばすけっと", "イオン", "ヤマダ", "ケーズデンキ", "ビックカメラ", "マツモトキヨシ",
          "ウエルシア", "業務スーパー", "おたからや", "大黒屋", "TAC", "くら寿司", "スシロー", "ミスタードーナツ"]
COMMERCIAL_RE = re.compile(r"新商品|新発売|新メニュー|限定|フェア|セール|キャンペーン|オープン|OPEN|リニューアル|開店|閉店|"
                           r"入荷|特典|優待|買取|周年|SALE|ポイント|割引|％オフ|%オフ")
# 全国向けPR・転載サイト(配信元)
PR_SOURCES = ("PR TIMES", "PRTIMES", "@Press", "ValuePress", "共同通信PRワイヤー", "プレスリリース")
AGGREGATOR_SOURCES = ("ニコニコニュース", "ｄメニューニュース", "Dtimes", "dtimes.jp", "mantan-web.jp", "MANTANWEB",
                      "毎日キレイ", "crea.bunshun.jp", "フーズチャネル", "流通ニュース", "繊研新聞", "news.infoseek.co.jp",
                      "topics.smt.docomo.ne.jp", "47NEWS", "Storm.mg", "公明党", "sannichi.co.jp")


def split_source(title):
    """Google Newsのタイトル末尾「 - 媒体名」を分ける。戻り値: (本文タイトル, 媒体名または空)。"""
    if " - " in title:
        body, src = title.rsplit(" - ", 1)
        if 0 < len(src) <= 40:
            return body.strip(), src.strip()
    return title.strip(), ""


def _has(text, words):
    return any(w in text for w in words)


def classify(title, url=""):
    """記事を採用するか判定する。戻り値: (採用するか, 理由コード, 地域ラベル)。
    理由コードは `keep:` で始まれば採用、`drop:` で始まれば除外(ログ・テスト用)。"""
    body, source = split_source(title)
    host = urlsplit(url or "").netloc.lower()
    direct = next(((d, v) for d, v in DIRECT_FEEDS.items() if host.endswith(d)), None)
    core_hit = [p for p in CORE if p in body]
    near_hit = [p for p in NEARBY if p in body]
    has_signal = _has(body, LOCAL_SIGNALS)

    # --- ノイズ(地域名があっても除外) ---
    if source in USED_CAR_SOURCES or USED_CAR_RE.search(body):
        return False, "drop:used_car_or_buyback", ""
    if RANKING_RE.search(body):
        return False, "drop:ranking", ""
    if WEATHER_QUAKE_RE.search(body):
        return False, "drop:weather_or_quake_list", ""
    if _has(body, CHAINS) and COMMERCIAL_RE.search(body):
        return False, "drop:national_chain_commercial", ""

    # --- 地域(core=北本・鴻巣・桶川 / nearby=上尾・久喜・伊奈・蓮田) ---
    if core_hit:
        tier, region = "core", ("北本" if "北本" in core_hit else core_hit[0])
    elif direct and direct[1][1]:           # 北本市公式note・号外NET(鴻巣・北本)は市名が無くても中心の情報
        tier, region = "core", direct[1][0]
    elif near_hit:
        tier, region = "nearby", near_hit[0]
    elif direct:                            # 号外NET(上尾・桶川)で市名が無い記事は周辺扱い
        tier, region = "nearby", direct[1][0]
    else:
        return False, "drop:region_not_kitamoto_area", ""

    # --- 全国向けPR・転載サイトは、北本・鴻巣・桶川が出て、かつ地域の手がかりがある場合だけ ---
    if source in PR_SOURCES or source in AGGREGATOR_SOURCES or "PRTIMES" in body:
        if core_hit and has_signal:
            return True, "keep:pr_or_aggregator_with_core_and_signal", region
        return False, "drop:pr_or_aggregator_without_local_signal", ""

    # --- 生活圏の周辺市は、行政・学校・イベントなど地域の手がかりがある場合だけ ---
    if tier == "nearby":
        if has_signal:
            return True, "keep:nearby_with_local_signal", region
        return False, "drop:nearby_without_local_signal", ""

    return True, "keep:direct_feed" if direct else "keep:core", region
