# -*- coding: utf-8 -*-
"""
#webhook_market「主要経済ニュース」枠の整理(2026-09-25追加、Phase 1)

fetch_news.py の build_market_message() から呼ばれ、既送信排除後の新着記事
(biz_new)を以下のように整理する。

  1. 同じ材料(同一の出来事・発表・市況)を報じた複数媒体の記事を1グループに集約
  2. 各グループにカテゴリと重要度(🔴🟡⚪)を付与し、重要度順に並べる

設計上の約束(細川さん指定):
  - 記事を捨てない。Geminiがどのグループにも入れなかった記事は「未分類」として
    従来形式のまま必ず出力する。
  - Gemini呼び出しの失敗・JSON不正・APIキー無しの場合は curate() が None を返し、
    呼び出し側は従来の箇条書き(render_flat)に戻す。
  - 重要度は値動きの予想ではなく「影響範囲」で決める。投資判断はさせない。
  - URL・媒体名はすべて元データから組み立てる。Geminiには記事番号だけを返させ、
    リンクや見出しをAIに生成させない(代表見出しも実在記事のタイトルを使う)。
  - 似ていても別の材料は統合しない(プロンプトで明示し、迷ったら分ける)。

環境変数:
  MARKET_NEWS_CURATION=off  … この整理処理を無効化し、従来の箇条書きに戻す
                              (本番で問題が出た場合の即時ロールバック用)
"""
import json
import os
import re
import urllib.parse

CATEGORIES = ["日本市場", "米国市場", "為替・金利", "中央銀行", "個別企業", "海外経済", "国内経済", "その他"]
IMPORTANCE_ORDER = {"high": 0, "mid": 1, "low": 2}
IMPORTANCE_ICON = {"high": "🔴", "mid": "🟡", "low": "⚪"}
# 直近のcurate()でGemini呼び出し・解析が失敗した場合の例外(失敗の可視化用。成功/対象外ならNone)
LAST_ERROR = None
# 媒体リンク行の接頭辞。fetch_news.chunk_message() はこの接頭辞の行を直前の見出し行と
# 同じメッセージに収める。
CONTINUATION_PREFIX = "　└ "
LINKS_PER_LINE = 4

# Google News RSSのタイトル末尾「 - 媒体名」を短い表示名に揃える。
# 未登録の媒体名はそのまま表示する(勝手に変えない)。
SOURCE_SHORT_NAMES = {
    "日本経済新聞": "日経",
    "日経ビジネス電子版": "日経ビジネス",
    "Bloomberg.co.jp": "ブルームバーグ",
    "Bloomberg": "ブルームバーグ",
    "Bloomberg.com": "ブルームバーグ",
    "ブルームバーグ": "ブルームバーグ",
    "Reuters": "ロイター",
    "ロイター": "ロイター",
    "jp.reuters.com": "ロイター",
    "Yahoo!ファイナンス": "Yahoo!ファイナンス",
    "東洋経済オンライン": "東洋経済",
    "時事通信": "時事",
    "ウエルスアドバイザー": "ウエルスアドバイザー",
}
# タイトルに媒体名が付かない直接購読RSSは、ドメインから媒体名を補う。
DOMAIN_SOURCE_NAMES = {
    "toyokeizai.net": "東洋経済",
}
# Yahoo!ファイナンスは配信元をタイトル末尾の丸括弧に書く(例:「NY株3日続落、161ドル安(時事通信)」)。
# 全記事が「Yahoo!ファイナンス」表示になると媒体の区別がつかないため、括弧内の配信元を優先する。
_YAHOO_ORIGIN_RE = re.compile(r"[（(]([^（）()]{2,20})[）)]\s*(?:☆差替)?\s*$")
# 「☆差替」「 | ロイター」「 | ビジネス | 東洋経済オンライン」等、見出し本体でない末尾を除く。
_TRAILING_NOISE_RE = re.compile(r"(\s*☆差替|\s*[|｜][^|｜]{1,20})+$")


def split_title_source(item):
    """記事から (媒体名を除いたタイトル, 表示用の媒体名) を返す。
    表示の整形専用で、URL・重複判定・既送信stateには一切影響しない。"""
    title = item.get("title", "")
    source = ""
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if 0 < len(tail) <= 30:
            title, source = head.strip(), tail.strip()
    if source in ("Yahoo!ファイナンス", ""):
        m = _YAHOO_ORIGIN_RE.search(title)
        if m and source:
            source = m.group(1).strip()
            title = title[:m.start()].strip()
    title = _TRAILING_NOISE_RE.sub("", title).strip() or title
    if not source:
        netloc = urllib.parse.urlsplit(item.get("url", "")).netloc
        for domain, name in DOMAIN_SOURCE_NAMES.items():
            if netloc.endswith(domain):
                source = name
                break
    source = SOURCE_SHORT_NAMES.get(source, source) or "出典"
    return title, source


def _source_labels(items):
    """グループ内の媒体名。同じ媒体が複数あれば「時事②」のように番号を付けて区別する。"""
    labels, seen = [], {}
    for it in items:
        name = split_title_source(it)[1]
        seen[name] = seen.get(name, 0) + 1
        labels.append(name if seen[name] == 1 else f"{name}{'②③④⑤⑥⑦⑧⑨'[min(seen[name], 9) - 2]}")
    return labels


def render_flat(items):
    """従来どおりの箇条書き(2026-09-25以前と同一の表示)。フォールバック用。"""
    return [f"- [{item['title']}](<{item['url']}>) `[{item['published']}]`" for item in items]


def _build_prompt(items):
    listing = "\n".join(
        f"{i}. [{split_title_source(it)[1]}] {split_title_source(it)[0]}（{it.get('published', '')}）"
        for i, it in enumerate(items)
    )
    return f"""以下は日本の投資家向けに収集した経済・市況ニュースの見出し一覧です。
番号. [媒体名] 見出し（配信日時JST）の形式です。

{listing}

次の作業をしてください。

【1. 同じ材料のグループ化】
- 「同じ材料」とは、同一の出来事・発表・経済指標、または同じ市場の同じ時点の市況
  (寄り付き・前場・後場・大引け・終値などの同じ区切り)を報じた記事のことです
  (例: 同じ日のNY株式市場の終値を報じた複数媒体の記事)。
- 次は別材料として扱い、統合しないでください:
  - テーマやカテゴリが同じなだけの別の出来事(A社の決算とB社の決算など)
  - 異なる市場・異なる日・同じ日でも異なる時点の市況(寄り付きの記事と大引けの記事など)
  - 市況記事と、見通し・展望・注目ポイント・注目銘柄紹介・個別銘柄戦略などの解説記事
  - 市況記事と、VI(変動性指数)・寄与度ランキングなどの別指標の記事
  - 同じ企業でも内容が異なる発表(上場初値と決算開示など)
- 同じ材料か迷う場合は統合せず、別グループにしてください。
- すべての番号をちょうど1回ずつ、いずれかのグループに入れてください(1記事だけのグループも可)。

【2. カテゴリ】次から1つだけ選んでください:
{" / ".join(CATEGORIES)}

【3. 重要度】株価が上がるか下がるかの予想ではなく、「影響が及ぶ範囲」で判定します。
投資判断や売買推奨は一切しないでください。
- high: 確定した重要事実・正式発表に限る。
  日銀・FRB・ECB等の政策決定、政府の正式な経済政策の決定、主要経済指標(GDP・CPI・
  雇用統計・日銀短観等)の正式発表、大型企業の正式発表(決算・大型買収等)、
  市場全体を揺るがす重大な市場イベント(歴史的な急落・急騰、為替介入、取引停止等)
- mid: 株価・為替・金利の値動きや途中経過、市況概況(終値の報道を含む)、市況解説、
  相場見通し、投資戦略記事、特定の業種・テーマに関わる記事
- low: 個別性の高い小規模な企業ニュース、一般的な解説・用語説明・コラム・読み物、
  影響範囲が限定的な話題
- 次は原則として high にしないでください:
  - 「明日の戦略」「見通し」「予想」など将来を予測する記事
  - ドル円や日経平均などの途中経過・値動き速報(市場全体を変える重大な材料でない場合)
  - コラム・解説・論評
  - 通常の市況概況(「3日続落」「まちまち」「続伸」など、重大なイベントを伴わない日々の値動き)

【4. 代表見出し】各グループについて、グループ内の記事のうち材料の内容が最もよく
分かる見出しの番号を representative として選んでください(見出しを新しく作らないこと)。

出力は次のJSON配列のみ(説明文・コードフェンス不要):
[{{"indices": [0, 3], "representative": 0, "category": "米国市場", "importance": "high",
   "reason": "判定理由を20字程度で"}}]"""


def _parse_json(text):
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


def _validate(parsed, n):
    """Geminiの出力を検証し、(groups, ungrouped_indices, warnings) を返す。
    番号の範囲外・重複所属・不正なカテゴリ/重要度はここで補正し、
    どのグループにも入らなかった記事は ungrouped として残す(捨てない)。
    """
    if not isinstance(parsed, list):
        raise ValueError("JSONが配列ではありません")
    warnings = []
    assigned = set()
    groups = []
    for entry in parsed:
        if not isinstance(entry, dict):
            warnings.append(f"不正なグループ要素を無視: {entry!r}")
            continue
        indices = []
        for idx in entry.get("indices") or []:
            if not isinstance(idx, int) or not (0 <= idx < n):
                warnings.append(f"範囲外の記事番号を無視: {idx!r}")
                continue
            if idx in assigned:
                warnings.append(f"重複所属の記事番号を無視(先のグループを優先): {idx}")
                continue
            assigned.add(idx)
            indices.append(idx)
        if not indices:
            continue
        rep = entry.get("representative")
        if rep not in indices:
            rep = indices[0]
        category = entry.get("category")
        if category not in CATEGORIES:
            warnings.append(f"未定義カテゴリ {category!r} を「その他」に補正")
            category = "その他"
        importance = entry.get("importance")
        if importance not in IMPORTANCE_ORDER:
            warnings.append(f"未定義の重要度 {importance!r} を low に補正")
            importance = "low"
        groups.append({
            "indices": indices,
            "representative": rep,
            "category": category,
            "importance": importance,
            "reason": str(entry.get("reason") or ""),
        })
    ungrouped = [i for i in range(n) if i not in assigned]
    return groups, ungrouped, warnings


def curate(items, client, model_name):
    """新着記事をグループ化・分類する。失敗時は None(呼び出し側で従来表示に戻す)。

    戻り値: {"groups": [...], "ungrouped": [item, ...], "warnings": [...], "raw": str}
      groups の各要素: {"items": [item, ...], "headline", "category", "importance", "reason"}
    """
    global LAST_ERROR
    LAST_ERROR = None
    if os.environ.get("MARKET_NEWS_CURATION", "").lower() == "off":
        print("[INFO] MARKET_NEWS_CURATION=off のため、経済ニュースの整理をスキップします。")
        return None
    if not items:
        return None
    if client is None:
        print("[INFO] Geminiクライアントが無いため、経済ニュースは従来の箇条書きで出力します。")
        return None

    try:
        resp = client.models.generate_content(model=model_name, contents=_build_prompt(items))
        raw = resp.text or ""
        groups_idx, ungrouped_idx, warnings = _validate(_parse_json(raw), len(items))
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] 経済ニュースのグループ化に失敗したため、従来の箇条書きに戻します: {err}")
        LAST_ERROR = err
        return None
    if not groups_idx:
        print("[WARN] 経済ニュースのグループが0件だったため、従来の箇条書きに戻します。")
        return None

    groups = []
    for g in groups_idx:
        headline, _ = split_title_source(items[g["representative"]])
        # 代表記事を先頭に、残りは取得順
        ordered = [g["representative"]] + [i for i in g["indices"] if i != g["representative"]]
        groups.append({
            "items": [items[i] for i in ordered],
            "headline": headline,
            "category": g["category"],
            "importance": g["importance"],
            "reason": g["reason"],
        })
    # 重要度 → 記事数の多い順(多くの媒体が報じた材料を上に) → 元の取得順
    groups.sort(key=lambda g: (IMPORTANCE_ORDER[g["importance"]], -len(g["items"]), items.index(g["items"][0])))
    return {
        "groups": groups,
        "ungrouped": [items[i] for i in ungrouped_idx],
        "warnings": warnings,
        "raw": raw,
    }


def render_groups(result):
    """整理結果をDiscord向けの行リストにする。1グループ = 見出し行 + 媒体リンク行。"""
    lines = []
    for g in result["groups"]:
        icon = IMPORTANCE_ICON[g["importance"]]
        count = f"（{len(g['items'])}本）" if len(g["items"]) > 1 else ""
        latest = max((it.get("published") or "" for it in g["items"]), default="")
        time_part = f" `{latest}`" if latest else ""
        lines.append(f"{icon}【{g['category']}】{g['headline']}{count}{time_part}")
        links = [f"[{label}](<{it['url']}>)" for label, it in zip(_source_labels(g["items"]), g["items"])]
        # Google NewsのURLは1本250字前後あるため、1行に並べるリンク数を制限して
        # 1行がDiscordの2000字上限を超えないようにする(超えると投稿自体が拒否される)。
        for i in range(0, len(links), LINKS_PER_LINE):
            lines.append(CONTINUATION_PREFIX + " / ".join(links[i:i + LINKS_PER_LINE]))
    if result["ungrouped"]:
        lines.append("▼ 未分類（AI判定の対象外・従来表示）")
        lines.extend(render_flat(result["ungrouped"]))
    return lines
