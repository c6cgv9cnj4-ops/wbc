# -*- coding: utf-8 -*-
"""
「埼玉・県央ローカルニュース」(#webhook_local)の記事選別(2026-10-06)。

目的: #webhook_local を「埼玉県全体のニュース」ではなく
「北本を中心に、北本・鴻巣・桶川周辺で、地元の人との会話材料になるニュース」にする。
「地域名が入っている=地域ニュース」にはしない。タイトル(と配信元)から次の順で判定する。

  0. 最優先: 北本市役所・北本市行政が当事者の問題(不祥事・事務ミス・誤通知・返還・謝罪・訂正・情報漏えい・入札/契約の重大問題など)。
     報道・公式発表に書かれた具体的な事実(語)だけで判定し、市役所のイベント・募集・制度案内は優先しない(下記 ADMIN_*)
  1. 地域: 北本市 > 鴻巣市・桶川市・上尾市(通常優先) > 久喜・蓮田・伊奈・加須・白岡など周辺自治体 > その他
     さいたま市・全国の記事は、北本・鴻巣・桶川・上尾が出てこなければ除外する
     周辺自治体と埼玉県全体の記事は、自治体との距離だけで決めない: 通常のニュースは抑え、北本の人との
     会話材料になる規模・重要性(大規模な行政改革・事業削減・災害・重大事故・制度変更など)の明確なシグナルがあれば採用する
  2. ノイズ: 中古車・買取、ランキング、天気・地震の一覧、全国チェーンの商品・店舗情報は
     地域名があっても除外する(例: 「北本市のガリバーで中古車入荷」)
  3. 全国向けPR・転載サイト: 北本・鴻巣・桶川が出て、かつ地域の話題の手がかりがある場合だけ残す
  4. 周辺自治体・埼玉県全体: 高インパクトのシグナル(IMPACT_PATTERNS)がある場合だけ残す
  5. 北本市公式note・号外NET(鴻巣・北本/上尾・桶川)の直接購読は地域の一次・専門情報として優先する

表示順は priority_level(): 1=北本市行政の問題 2=北本の重大・重要な市政 3=北本の通常 4=鴻巣・桶川・上尾 6=周辺の重大(救済)。
周辺の通常ニュース(5)は従来どおり採用しない。さいたま市など遠方は原則除外(県内広域の災害・重大事件のみ例外)。

防災・緊急情報(公式Mastodon経由)はこのフィルタの対象外(別経路)。
判定は説明可能なルールだけ(LLMは使わない)。除外理由は reason に残し、ログで確認できる。
"""
import re
import unicodedata
from urllib.parse import urlsplit

CORE = ["北本", "鴻巣", "桶川", "上尾"]              # 通常優先(北本が最優先)
NEARBY = ["久喜", "蓮田", "伊奈町", "加須", "白岡", "幸手", "宮代", "杉戸", "行田", "吉見", "川島"]   # 周辺自治体(通常のニュースは抑える)
# 埼玉県全体の記事を「県全体」とみなすとき、特定の他市町村の話題ではないことを確かめるための名前
OTHER_MUNICIPALITIES = ["さいたま", "大宮", "浦和", "与野", "岩槻", "川越", "熊谷", "川口", "秩父", "所沢", "飯能", "本庄", "東松山",
                        "春日部", "狭山", "羽生", "深谷", "入間", "朝霞", "志木", "和光", "新座", "八潮", "富士見", "三郷", "坂戸",
                        "鶴ヶ島", "日高", "吉川", "ふじみ野", "越谷", "草加", "戸田", "蕨", "三芳", "毛呂山", "越生", "滑川", "嵐山",
                        "小川町", "鳩山", "ときがわ", "横瀬", "皆野", "長瀞", "小鹿野", "東秩父", "美里", "神川", "上里", "寄居", "松伏"]

# 直接購読フィード(地域の一次・専門情報)。タイトルに市名が無い場合の既定の地域を持つ
DIRECT_FEEDS = {
    "kitamoto-city.note.jp": ("北本", True),           # 北本市公式note(自治体の一次情報)
    "kounosu-kitamoto.goguynet.jp": ("鴻巣・北本", True),
    "ageo-okegawa.goguynet.jp": ("上尾・桶川", True),    # 上尾・桶川版(上尾・桶川は通常優先)
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

# 北本の人との会話材料になる規模・重要性の明確なシグナル(周辺自治体・埼玉県全体の記事を採用する条件)。
# 自治体名では決めず、金額規模・重大・大規模・緊急・行政改革・制度変更・災害・事故などの語で判定する。
_AMOUNT = r"(?:\d{2,}(?:,\d{3})*億円|\d+(?:\.\d+)?兆円)"
IMPACT_PATTERNS = {
    "財政・行政改革": re.compile(
        r"行政改革|財政(?:再建|危機|難|破綻|赤字|悪化)|緊急財政|事業(?:見直し|廃止|削減)|事業を(?:廃止|見直|削減)|"
        r"予算(?:削減|を削減|見直し|カット)|歳出削減|人件費削減|職員(?:削減|を削減)|機構改革|組織再編|市町村合併|"
        + _AMOUNT + r"[^。]{0,8}(?:削減|カット|赤字|不足|減額|枯渇)|(?:削減|カット|赤字|不足|枯渇)[^。]{0,8}" + _AMOUNT),
    "制度・施策": re.compile(
        r"新制度|制度(?:改正|変更|導入|廃止|創設|開始)|条例(?:改正|制定|施行)|義務化|無償化|全面(?:禁止|改正|導入)|"
        r"料金(?:値上げ|改定|引き上げ)|(?:増税|税率引き上げ)|施行へ|導入へ"),
    "災害": re.compile(
        r"豪雨|台風|洪水|氾濫|浸水被害|竜巻|突風|大雪|土砂(?:崩れ|災害)|避難指示|緊急安全確保|大規模(?:停電|火災|災害)|"
        r"断水|停電|被災|震度[5-7]|雹|ひょう被害"),
    "重大事故・事件": re.compile(
        r"死亡|死者|殺人|殺害|爆発|立てこもり|多重事故|脱線|重体|行方不明|心肺停止|強盗|放火|誘拐|通り魔|刺殺|刺され"),
    "交通・施設・住民影響": re.compile(
        r"廃線|運休|ダイヤ改正|新駅|インターチェンジ|バイパス|高速道路|統廃合|学校再編|"
        r"病院[^。]{0,6}(?:閉鎖|廃止|休止|縮小|閉院)|救急[^。]{0,6}(?:休止|受け入れ停止)|ごみ処理[^。]{0,6}(?:広域|統合|停止)|"
        r"広域[^。]{0,4}(?:統合|再編)|組合[^。]{0,4}(?:解散|統合)|新庁舎"),
    "規模・緊急": re.compile(r"大規模|重大|緊急|異例|過去最(?:大|多|高|悪|少)|前例のない"),
}


# --- 北本市行政・市役所が当事者の問題(最優先) -----------------------------------------
# 報道・公式発表のタイトルに書かれた事実の語だけで判定する(システム側で政治的な評価はしない)。
# 「市役所」「市長」などが出るだけのイベント・募集・制度案内は対象外(問題を示す語が必要)。
ADMIN_ACTOR_RE = re.compile(r"北本市(?:役所|職員|教育委員会|教委|議会|長|立|が|は|、)|北本市の(?:職員|担当者|担当課|事務|指定管理|入札|契約|業務委託|補助金|給付|徴収|算定|税|手当|公文書|公印)")
ADMIN_GENERIC_ACTOR_RE = re.compile(r"市職員|市役所|市教委|市議会|市長|市が|市は|市の(?:職員|担当|事務)|主幹級|主査級|課長級|係長級")
ADMIN_STRONG_RE = re.compile(
    r"懲戒(?:処分|免職|解雇)|諭旨|停職|減給|戒告|免職|不祥事|不適切|不正(?:使用|受給|流用|請求|に|な)|公印|公文書|"
    r"(?<!ミ)ミス(?!ター|コン|ティ|テリ|マッチ|ユニバース|ジャパン|北本|・)|"
    r"誤(?:り|っ|通知|説明|支給|送付|請求|算定|入力|交付|記載|案内|発送|廃棄)|過大|過少|過誤|算定(?:誤り|漏れ)|"
    r"未徴収|徴収漏れ|請求漏れ|支給漏れ|過払い|返還(?:へ|を|請求|命令|する|し|金)|再発防止|謝罪|陳謝|"
    r"情報漏えい|情報漏洩|個人情報[^。]{0,6}(?:流出|漏|紛失|誤)|着服|横領|収賄|談合|"
    r"入札[^。]{0,8}(?:不正|問題|やり直し|中止|無効)|指定管理[^。]{0,10}(?:問題|不正|違反|取り消|取消|解除|辞退)|"
    r"(?:契約|工事)[^。]{0,6}(?:違反|不正)|問題視|紛失|百条委員会|第三者委員会|不信任")
ADMIN_SOFT_RE = re.compile(r"訂正|お詫び|不備|違法")
FAR_WIDE_AREA_RE = re.compile(r"県内|県全域|県南|県央|県北|広域|複数の市")


def is_admin_issue(body, source_kind="title"):
    """北本市行政が当事者の問題か。
    source_kind: "title"=タイトルに『北本市(役所/職員/…)』と問題を示す語が並ぶ / "self"=北本市公式の自己発信(強い問題語のみ) /
    "query"=『北本市』×問題語の検索で得た記事でタイトルに市名が無い(他の市町村名が出ない・行政の主体を示す語と強い問題語が必要)。
    """
    text = unicodedata.normalize("NFKC", body)
    if source_kind == "title":
        return bool(ADMIN_ACTOR_RE.search(text) and (ADMIN_STRONG_RE.search(text) or ADMIN_SOFT_RE.search(text)))
    if source_kind == "self":
        return bool(ADMIN_STRONG_RE.search(text))
    if any(m in text for m in [*CORE, *NEARBY, *OTHER_MUNICIPALITIES] if m != "北本"):
        return False
    if "北本" in text:
        return bool(ADMIN_ACTOR_RE.search(text) and ADMIN_STRONG_RE.search(text))
    return bool(ADMIN_GENERIC_ACTOR_RE.search(text) and ADMIN_STRONG_RE.search(text))


# 北本の重要な市政(通常の市役所のお知らせは含めない)
CITY_POLICY_RE = re.compile(r"市長選|市議選|市議会議員選挙|補正予算|当初予算|決算|条例|財政|市政|総合振興計画|新庁舎|庁舎|"
                            r"公共施設[^。]{0,6}(?:再編|統廃合)|議会[^。]{0,4}(?:可決|否決|承認)")


def impact_categories(body):
    """高インパクトのシグナルに当たった分類名の一覧(全角数字は半角にそろえて判定)。"""
    text = unicodedata.normalize("NFKC", body)
    return [name for name, rx in IMPACT_PATTERNS.items() if rx.search(text)]


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


def classify(title, url="", via_admin_query=False):
    """記事を採用するか判定する。戻り値: (採用するか, 理由コード, 地域ラベル)。
    理由コードは `keep:` で始まれば採用、`drop:` で始まれば除外(ログ・テスト用)。
    via_admin_query: 『北本市』×問題語のGoogle News検索で得た記事(タイトルに市名が無い報道を拾うため)。"""
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

    # --- 北本市行政・市役所が当事者の問題は最優先(地域の距離・種別の判定より前) ---
    if is_admin_issue(body, "title"):
        return True, "keep:kitamoto_admin_issue", "北本"
    if direct and direct[0].startswith("kitamoto-city") and is_admin_issue(body, "self"):
        return True, "keep:kitamoto_admin_issue", "北本"
    if via_admin_query and is_admin_issue(body, "query"):
        return True, "keep:kitamoto_admin_issue(via_query)", "北本"

    # --- 地域(core=北本・鴻巣・桶川・上尾 / nearby=久喜・蓮田・伊奈・加須・白岡など / prefecture=埼玉県全体) ---
    if core_hit:
        tier, region = "core", ("北本" if "北本" in core_hit else core_hit[0])
    elif direct and direct[1][1]:           # 北本市公式note・号外NET(鴻巣・北本/上尾・桶川)は市名が無くても中心の情報
        tier, region = "core", direct[1][0]
    elif near_hit:
        tier, region = "nearby", near_hit[0]
    elif "埼玉県" in body and not _has(body, OTHER_MUNICIPALITIES):
        tier, region = "prefecture", "埼玉県"      # 特定の他市町村の話題ではない県全体の記事
    elif (_has(body, OTHER_MUNICIPALITIES) and FAR_WIDE_AREA_RE.search(body)
          and {"災害", "重大事故・事件"} & set(impact_categories(body))):
        tier, region = "far", "埼玉県"            # 遠方(さいたま市等)は原則除外。県内広域の災害・重大事件だけ例外
    else:
        return False, "drop:region_not_kitamoto_area", ""

    # --- 全国向けPR・転載サイトは、北本・鴻巣・桶川が出て、かつ地域の手がかりがある場合だけ ---
    if source in PR_SOURCES or source in AGGREGATOR_SOURCES or "PRTIMES" in body:
        if core_hit and has_signal:
            return True, "keep:pr_or_aggregator_with_core_and_signal", region
        return False, "drop:pr_or_aggregator_without_local_signal", ""

    # --- 周辺自治体・県全体は、自治体の距離ではなく、会話材料になる規模・重要性のシグナルで決める ---
    if tier in ("nearby", "prefecture", "far"):
        impact = impact_categories(body)
        if impact:
            return True, f"keep:{tier}_high_impact({'/'.join(impact)})", region
        return False, f"drop:{tier}_normal_news", ""

    return True, "keep:direct_feed" if direct else "keep:core", region


# --- 表示順(優先順位) --------------------------------------------------------------
LEVEL_ADMIN_ISSUE = 1       # 北本市行政の重大な問題・不祥事・ミス
LEVEL_KITAMOTO_MAJOR = 2    # 北本の重大ニュース・重要な市政
LEVEL_KITAMOTO_NORMAL = 3   # 北本の通常ニュース
LEVEL_NEAR_CORE = 4         # 鴻巣・桶川・上尾
LEVEL_RESCUED = 6           # 周辺・県内広域の重大ニュース(周辺の通常=5 は採用しないので、採用分では最後)


def priority_level(title, reason, region):
    """採用記事の表示順(小さいほど先)。reason/region は classify() の戻り値。"""
    body, _ = split_source(title)
    if reason.startswith("keep:kitamoto_admin_issue"):
        return LEVEL_ADMIN_ISSUE
    if region == "北本" or "北本" in body:
        text = unicodedata.normalize("NFKC", body)
        return LEVEL_KITAMOTO_MAJOR if (impact_categories(body) or CITY_POLICY_RE.search(text)) else LEVEL_KITAMOTO_NORMAL
    if reason.startswith("keep:nearby") or reason.startswith("keep:prefecture") or reason.startswith("keep:far"):
        return LEVEL_RESCUED
    return LEVEL_NEAR_CORE
