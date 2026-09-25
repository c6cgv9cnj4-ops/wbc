# -*- coding: utf-8 -*-
"""
#webhook_market 経済ニュース整理(market_news_curation.py)のドライラン。

Discordへは一切送信せず、state/news_seen.json も書き換えない。実データ
(fetch_news.fetch_economy_news_candidates)を取得し、グループ化結果・
フォールバック結果・Discordに出す予定の本文・効果の数値をレポートする。

使い方(my-project直下で):
  GEMINI_API_KEY=... .venv/bin/python scripts/dry_run_market_curation.py [オプション]

オプション:
  --use-state PATH     そのstateと照合し「次の本番実行で新着になる記事」だけを対象にする
                       (stateは読むだけで保存しない)。省略時は取得できた全候補を対象にする
  --input PATH         取得済み候補(JSON)を使う(同じ入力で再検証したいとき)
  --save-input PATH    今回取得した候補をJSONで保存する
  --out PATH           レポートの保存先(省略時は標準出力のみ)
"""
import argparse
import copy
import datetime
import io
import json
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_news  # noqa: E402
import market_news_curation as mnc  # noqa: E402

# 本番メッセージで経済ニュース枠の前に付く株価ブロックの長さ(目安)。
# 分割数の見積もりに使う(2026-09-25の本番ログ実測で約950字)。
DEFAULT_PRICE_BLOCK_CHARS = 950


def bigram_jaccard(a, b):
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def chunk_stats(news_lines, price_block_chars):
    """株価ブロック相当のダミー行を先頭に置いて本番と同じ分割関数にかけ、
    分割数と「見出し行と媒体リンク行が別メッセージに泣き別れたグループ数」を返す。"""
    dummy = "x" * max(price_block_chars - 1, 0)
    text = "\n".join([dummy, "## 📰 主要経済ニュース(日経・東洋経済)"] + news_lines)
    chunks = fetch_news.chunk_message(text)
    split_groups = 0
    for c in chunks[1:]:
        if c.startswith("　└ "):
            split_groups += 1
    return len(chunks), split_groups, len(text), max(len(c) for c in chunks)


class _FailingClient:
    class models:  # noqa: N801
        @staticmethod
        def generate_content(**_kwargs):
            raise RuntimeError("シミュレーション: Gemini APIエラー")


class _FixedClient:
    def __init__(self, text):
        self._text = text
        outer = self

        class _Models:
            @staticmethod
            def generate_content(**_kwargs):
                class _Resp:
                    text = outer._text
                return _Resp()
        self.models = _Models()


def fallback_selftests(items):
    """Gemini異常系で「記事を捨てず従来表示に戻る」ことを確認する。"""
    n = len(items)
    cases = [
        ("API例外", _FailingClient()),
        ("JSON不正", _FixedClient("これはJSONではありません")),
        ("空配列", _FixedClient("[]")),
        ("範囲外番号のみ", _FixedClient(json.dumps([{"indices": [n + 5], "category": "その他", "importance": "low"}]))),
        ("一部だけ分類(残りは未分類に残るべき)",
         _FixedClient(json.dumps([{"indices": [0], "representative": 0, "category": "謎カテゴリ", "importance": "???"}]))),
    ]
    out = []
    for label, client in cases:
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = mnc.curate(items, client, "dummy")
        if res is None:
            lines = mnc.render_flat(items)
            mode = "従来表示にフォールバック"
            kept = len(lines)
        else:
            lines = mnc.render_groups(res)
            mode = f"部分整理(グループ{len(res['groups'])}・未分類{len(res['ungrouped'])})"
            kept = sum(len(g["items"]) for g in res["groups"]) + len(res["ungrouped"])
        ok = "OK" if kept == n else "NG(記事欠落)"
        out.append(f"  - {label}: {mode} / 出力記事 {kept}/{n} → {ok}")
        if res and res["warnings"]:
            for w in res["warnings"]:
                out.append(f"      warn: {w}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-state")
    ap.add_argument("--input")
    ap.add_argument("--save-input")
    ap.add_argument("--out")
    ap.add_argument("--price-block-chars", type=int, default=DEFAULT_PRICE_BLOCK_CHARS)
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            candidates = json.load(f)
    else:
        candidates = fetch_news.fetch_economy_news_candidates()
    if args.save_input:
        with open(args.save_input, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=1)

    scope = "取得できた全候補(既送信を無視)"
    items = candidates
    if args.use_state:
        with open(args.use_state, encoding="utf-8") as f:
            state = copy.deepcopy(json.load(f))
        items = fetch_news.dedupe_new_items(candidates, "url", state, now)  # stateはコピーなので保存されない
        scope = f"stateと照合した新着のみ({args.use_state}、保存しない)"

    R = []
    R.append(f"#webhook_market 経済ニュース整理 ドライラン  {now:%Y-%m-%d %H:%M} JST")
    R.append("=" * 70)
    R.append(f"対象: {scope}")
    R.append(f"取得候補数: {len(candidates)}  / 入力新着記事数: {len(items)}")
    R.append("")

    api_key = os.environ.get("GEMINI_API_KEY")
    result = None
    gemini_log = ""
    if not items:
        R.append("新着0件のため整理処理は走りません(本番は「- 新着なし」を出力)。")
    elif not api_key:
        R.append("GEMINI_API_KEY 未設定: Gemini判定はスキップ(本番でキーが無い場合と同じく従来表示)。")
    else:
        from google import genai
        client = genai.Client(api_key=api_key)
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = mnc.curate(items, client, fetch_news.GEMINI_MODEL_NAME)
        gemini_log = buf.getvalue().strip()

    # ---- グループ詳細 ----
    if result:
        groups = result["groups"]
        multi = [g for g in groups if len(g["items"]) > 1]
        R.append("【グループ一覧】(重要度順)")
        for gi, g in enumerate(groups, 1):
            titles = [mnc.split_title_source(it)[0] for it in g["items"]]
            sims = [bigram_jaccard(titles[i], titles[j]) for i in range(len(titles)) for j in range(i + 1, len(titles))]
            sim_note = f" 見出し類似度(最小) {min(sims):.2f}" if sims else ""
            R.append(f"G{gi:02d} {mnc.IMPORTANCE_ICON[g['importance']]} {g['importance']:<4} 【{g['category']}】"
                     f" {len(g['items'])}本{sim_note}  理由: {g['reason']}")
            for it in g["items"]:
                t, s = mnc.split_title_source(it)
                R.append(f"      - [{s}] {t}  ({it.get('published', '')})")
        R.append("")
        R.append("【グループ化されなかった記事(未分類)】")
        if result["ungrouped"]:
            for it in result["ungrouped"]:
                R.append(f"  - {it['title']}")
        else:
            R.append("  なし(全記事がいずれかのグループに所属)")
        if result["warnings"]:
            R.append("【検証で補正した点】")
            R.extend(f"  - {w}" for w in result["warnings"])
        R.append("")

        # 統合漏れの疑い: 別グループ同士で見出しがよく似ているペア
        heads = [(gi, mnc.split_title_source(it)[0]) for gi, g in enumerate(groups, 1) for it in g["items"]]
        suspects = []
        for i in range(len(heads)):
            for j in range(i + 1, len(heads)):
                if heads[i][0] != heads[j][0]:
                    s = bigram_jaccard(heads[i][1], heads[j][1])
                    if s >= 0.30:
                        suspects.append((s, heads[i], heads[j]))
        R.append("【要目視: 別グループだが見出しが似ているペア(類似度0.30以上)】")
        if suspects:
            for s, a, b in sorted(suspects, reverse=True)[:10]:
                R.append(f"  {s:.2f}  G{a[0]:02d}「{a[1]}」 / G{b[0]:02d}「{b[1]}」")
        else:
            R.append("  なし")
        R.append("")

        # 分布
        from collections import Counter
        cat = Counter(g["category"] for g in groups)
        imp = Counter(g["importance"] for g in groups)
        R.append("【カテゴリ分布(グループ数)】 " + " / ".join(f"{k}:{v}" for k, v in cat.most_common()))
        R.append("【重要度分布(グループ数)】 " + " / ".join(
            f"{mnc.IMPORTANCE_ICON[k]}{v}" for k, v in sorted(imp.items(), key=lambda x: mnc.IMPORTANCE_ORDER[x[0]])))
        R.append("")

    # ---- 効果の数値 ----
    if items:
        flat_lines = mnc.render_flat(items)
        new_lines = mnc.render_groups(result) if result else flat_lines
        f_chunks, _, f_chars, _ = chunk_stats(flat_lines, args.price_block_chars)
        n_chunks, n_split, n_chars, n_max = chunk_stats(new_lines, args.price_block_chars)
        n_units = (len(result["groups"]) + len(result["ungrouped"])) if result else len(items)
        R.append("【効果(従来 → 新方式)】")
        R.append(f"  並ぶ項目数(記事/材料) : {len(items):>4} → {n_units:>4}"
                 f"  ({(1 - n_units / len(items)) * 100:.0f}%減)")
        if result:
            merged = sum(len(g['items']) for g in result['groups'] if len(g['items']) > 1)
            R.append(f"  複数記事を束ねた材料数 : {len(multi)}  (束ねられた記事 {merged}本)")
        R.append(f"  Discord表示行数        : {len(flat_lines):>4} → {len(new_lines):>4}")
        R.append(f"  本文文字数(株価込み目安): {f_chars:>5} → {n_chars:>5}")
        R.append(f"  Discord分割メッセージ数: {f_chunks:>4} → {n_chunks:>4}"
                 f"  (株価ブロック{args.price_block_chars}字を含む見積もり)")
        R.append(f"  見出しとリンクが別メッセージに分かれたグループ: {n_split}")
        R.append(f"  1メッセージの最大文字数: {n_max} (Discord上限2000)")
        R.append("")

        R.append("【Geminiフォールバック自己テスト(実データの記事で異常系を再現)】")
        R.extend(fallback_selftests(items))
        R.append("")

        R.append("【フォールバック時の本文(=現行と同一)】先頭10行")
        R.extend("  " + ln for ln in flat_lines[:10])
        R.append("")
        R.append("【Discordへ出す予定の本文(経済ニュース枠)】")
        R.append("## 📰 主要経済ニュース(日経・東洋経済)")
        R.extend(new_lines)
    if gemini_log:
        R.append("")
        R.append("【curate()ログ】")
        R.append(gemini_log)
    if result:
        R.append("")
        R.append("【Gemini生出力】")
        R.append(result["raw"])

    report = "\n".join(R)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)


if __name__ == "__main__":
    main()
