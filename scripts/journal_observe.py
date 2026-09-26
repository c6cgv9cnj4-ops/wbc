# -*- coding: utf-8 -*-
"""
モーニングジャーナルの週次「観測」モジュール(2026-09-26 再編)

目的は自分を評価・矯正することではなく、書き続けた記録から「自分では気づいていない
変化・傾向」を発見すること。そのため役割を2つに分ける。

  Python(このモジュールの observe) … 測定・集計・変化検出。同じ入力なら必ず同じ結果。
      - 書いた日数 / 文字数 / 日ごとの文字数 / 期間ごとの推移
      - 語(カタカナ語・漢字語・英単語)が「書いた日のうち何日に出たか」の変化
        (新しく出た / 再び出た / 増えた / 減った / 出てこなかった / 続いている)
      - 観測可能な表現(〜たい・やった・面倒・不安 など)の出現回数の変化
      - 変化した語どうし・語と表現が同じ日に出てくる回数(共起。因果ではない)
      - 各観測の根拠(日付・抜粋・スレッドURL)
  Gemini(interpret) … Python の観測結果と、その根拠の短い抜粋だけを受け取り、
      解釈の「仮説」を複数の可能性として返す。観測IDを参照しない仮説・指示/評価口調の
      文は捨てる。失敗しても観測だけで週次ダッシュボードは成立する。

原則: 「仕事が20回出た」は観測できるが「仕事のストレスが高まった」は観測できない。
出現回数は感情の強さを表さない。出力ではこの境界を必ず明示する。

通信は interpret(Gemini) のみ。それ以外は純粋関数で、tests/test_journal_observe.py で検証する。
"""
from __future__ import annotations

import datetime
import json
import re
from collections import Counter

# ---------------------------------------------------------------------------
# 観測の閾値(変えると過去週との比較結果も変わるので、変更時は PROJECT_STATUS に記録する)
# ---------------------------------------------------------------------------
RATIO_DELTA = 0.3        # 「書いた日のうち出た日の割合」が 30pt 以上動いたら増減とみなす
EXPECTED_GAP = 1.5       # かつ「比較期間の割合から期待される日数」との差が1.5日以上(偶然の揺れを除く)
GONE_EXPECTED = 2.0      # 「出てこなかった」は、比較期間の割合なら今期2日以上出ていたはずの語だけ
MIN_DAYS_WEEK = 2        # 週次: 新規/増加と言うには最低2日に出ていること
MIN_DAYS_MONTH = 3       # 月次(28日単位): 同 3日
GONE_MIN_BASE_PERIODS = 2  # 「出てこなかった」は比較期間のうち2期間以上に出ていた語だけ
STREAK_MIN = 3           # 3期間連続で出ていれば「続いている」
STABLE_DELTA = 0.15      # 続いている語のうち、出る割合の差が15pt未満なら「安定」
MARKER_MIN_TOTAL = 4     # 表現の増減は(今期+比較平均)が4回以上あるものだけ判定
MARKER_RATIO_UP = 1.5    # 1000字あたり出現率が1.5倍以上 → 増
MARKER_RATIO_DOWN = 0.67 # 0.67倍以下 → 減
MARKER_MIN_GAP = 3       # かつ、比較期間の出現率から期待される回数との差が3回以上(少量の揺れを除く)
LIST_LIMIT = 8           # 各リストの表示上限
SNIPPET_HALF = 22        # 抜粋は該当箇所の前後22字

# ---------------------------------------------------------------------------
# 観測可能な表現(感情ではなく「言い回し」を数える)。ラベルは中立語に統一する。
# ---------------------------------------------------------------------------
_WANT_STEMS = "きぎしちにびりいえけげせてねべめれ見出来寝居得"
MARKERS = [
    ("want", "願望表現（〜たい・欲しい）",
     re.compile(rf"(?:[{_WANT_STEMS}]|(?:読|休|楽し|試|飲|住|込|進|頼|悩|踏)み)た[いく]|欲し[いく]|ほし[いく]")),
    ("done", "実行・完了表現（やった・できた等）",
     re.compile(r"(?:やった|やれた|できた|出来た|終わった|終えた|行けた)(?!ら)|済ませ|済んだ|(?<!未)完了|達成|行ってきた|してきた")),
    ("intent", "意図・予定表現（〜しよう・つもり等）",
     re.compile(r"しよう|やろう|行こう|つもり|予定|決めた")),
    ("undone", "未実行表現（できなかった・後回し等）",
     re.compile(r"できなかった|出来なかった|できていない|できてない|やってない|やれてない|行けなかった|先延ばし|後回し|サボ")),
    ("burden", "負担表現（面倒・しんどい等）",
     re.compile(r"面倒|めんどう|めんどくさ|しんどい|億劫|やる気が[出で]な|気が重")),
    ("worry", "不安・気がかり表現",
     re.compile(r"不安(?!定)|心配|焦り|焦る|怖い|こわい|気がかり|気掛かり|モヤモヤ|もやもや")),
    ("positive", "肯定表現（楽しい・良かった等）",
     re.compile(r"楽しい|楽しかった|楽しみ|嬉し|うれし|良かった|よかった|ワクワク|わくわく|面白|おもしろ|最高|幸せ")),
    ("body", "身体・睡眠への言及",
     re.compile(r"眠い|眠れ|寝不足|睡眠|頭痛|頭が[重痛]|体調|だる[いく]|疲れ|腰痛|肩こり|疲労")),
    ("question", "自分への問いかけ（？・〜かな）",
     re.compile(r"[？?]|だろうか|のかな|かなあ|かな[。\n]|かな$")),
]
MARKER_LABEL = {k: lbl for k, lbl, _ in MARKERS}

# 語の区分(固定カテゴリへの分類ではなく「時間軸上のふるまい」の区分。どの語が入るかはデータ次第)
TERM_KINDS = ("new", "returned", "up", "down", "gone", "transient", "stable", "steady")
TERM_KIND_NAME = {"new": "新しく出た", "returned": "再び出た", "up": "増えた", "down": "減った",
                  "gone": "出てこなかった", "transient": "前週だけ出た", "stable": "安定して続いている",
                  "steady": "続いている（割合は変動）"}
TERM_KIND_ICON = {"new": "🆕", "returned": "↩️", "up": "⬆️", "down": "⬇️", "gone": "💤",
                  "transient": "🌫", "stable": "🟰", "steady": "🔁"}
MARKER_SHORT = {"want": "願望", "done": "実行・完了", "intent": "意図・予定", "undone": "未実行",
                "burden": "負担", "worry": "不安・気がかり", "positive": "肯定", "body": "身体・睡眠",
                "question": "問いかけ"}

# ---------------------------------------------------------------------------
# 語の抽出(形態素解析器を足さず、文字種の連なりで語を取る。精度より再現性を優先)
# ---------------------------------------------------------------------------
# 「飲み会」「書き方」のような 漢字+送り仮名1字+漢字 の名詞も1語として拾う(直後が助詞・句読点のときだけ)
_TERM_RE = re.compile(r"[一-龥][みりきし][一-龥]{1,2}(?=[をはがにでとのもへやだま、。！!？?\s]|$)"
                      r"|[ァ-ヴー]{2,}|[一-龥々]{2,8}|[A-Za-z][A-Za-z0-9]{2,}")
_NOISE_RE = re.compile(r"https?://\S+|<[@#:!&a-z]*[^>]*>")
# 漢字連の直後がこれ以外のひらがな(=送り仮名らしい)なら、末尾の漢字1字は動詞/形容詞の語幹とみなして落とす
# 例: 仕事終わり → 仕事 / 写真撮って → 写真。し・す・さ・せ はサ変(散歩した)なので落とさない。
_PARTICLE_OR_SURU = set("をはがにでとのもへやかよねなしすさせだまばぐ")
# 1字の「朝」「夜」は「朝日新聞」「朝食会場」を壊すので外さない
_TIME_PREFIX = ("今日", "昨日", "明日", "今朝", "毎日", "毎朝", "今夜", "昨夜")
STOP_TERMS = {
    "今日", "昨日", "明日", "今朝", "今夜", "昨夜", "今週", "来週", "先週", "今月", "来月", "先月",
    "今年", "毎日", "毎朝", "自分", "時間", "感じ", "気持", "本当", "最近", "今回", "前回", "一番",
    "必要", "部分", "状態", "場合", "結果", "理由", "意味", "全部", "一日", "午前", "午後", "今後",
    "以上", "以下", "一緒", "普通", "大事", "色々", "結構", "全然", "多分", "一応", "確か", "何回",
    "何度", "一回", "自体", "以外", "以前", "前日", "翌日", "当日", "途中", "最初", "最後", "方法",
    "予定", "感覚", "朝起", "気分", "今度", "久々", "明後日", "一人", "人間", "世界", "仕方",
    "ジャーナル", "モーニング", "モーニングジャーナル", "モーニングページ", "ページ",
    "後回", "先延",   # 「後回し」「先延ばし」の語幹。未実行表現として別に数えるので二重計上しない
}


def extract_terms(text: str) -> list[str]:
    """本文から語を取り出す(重複あり・出現順)。"""
    text = _NOISE_RE.sub(" ", text or "")
    out: list[str] = []
    for m in _TERM_RE.finditer(text):
        w = m.group(0)
        if w[0].isascii():
            w = w.lower()
        elif "一" <= w[0] <= "龥" or w[0] == "々":
            nxt = text[m.end():m.end() + 1]
            if len(w) >= 3 and nxt and "ぁ" <= nxt <= "ん" and nxt not in _PARTICLE_OR_SURU:
                w = w[:-1]
            for p in _TIME_PREFIX:
                if w.startswith(p) and len(w) - len(p) >= 2:
                    w = w[len(p):]
                    break
        if len(w) < 2 or set(w) <= {"ー"} or w in STOP_TERMS or _is_marker_word(w):
            continue
        out.append(w)
    return out


def _is_marker_word(w: str) -> bool:
    """「面倒」「不安」など表現マーカーそのものは語の変化から外す(表現側で数えるため二重計上しない)。"""
    return any(rx.fullmatch(w) or rx.match(w) and len(rx.match(w).group(0)) >= len(w) - 1
               for _, _, rx in MARKERS)


# 願望表現の既知の誤検出(冷たい/贅沢/痛い の仮名書き)を差し引く
_WANT_EXCLUDE = re.compile(r"つめた[いく]|ぜいた[くい]|(?<![てで言])いた[いく]")


def count_markers(text: str) -> Counter:
    text = _NOISE_RE.sub(" ", text or "")   # URL の「?」等を数えない
    c = Counter({k: len(rx.findall(text)) for k, _, rx in MARKERS})
    c["want"] = max(0, c["want"] - len(_WANT_EXCLUDE.findall(text)))
    return c


def _chars(text: str) -> int:
    return len(re.sub(r"\s", "", text or ""))


# ---------------------------------------------------------------------------
# 日単位の記録
# ---------------------------------------------------------------------------
# #モーニングジャーナルの投稿ガイドライン(scripts/set_journal_forum_guidelines.py)の固定見出し。
# 毎日同じ文字列なので観測から外す(「本当はどうしたい？」の願望/問いかけ、「連想」の語など、
# テンプレート導入による機械的な変化を数えないため)。見出しの下・同じ行に書いた本人の文章は残す。
_TEMPLATE_HEADING = re.compile(
    r"^[ \t　]*(?:【モーニングジャーナル】|■[ \t　]*(?:今の頭の中|昨日から残っていること|気になっていること|"
    r"本当はどうしたい[？?]|最近よく考えること|今日思いついたこと|ここから連想したこと|その他))[ \t　]*",
    re.MULTILINE)
_EMPTY_BULLET = re.compile(r"^[ \t　]*[・･][ \t　]*$\n?", re.MULTILINE)


def strip_template(text: str) -> str:
    """テンプレートの固定見出しと、何も書いていない「・」だけの行を取り除く。"""
    return _EMPTY_BULLET.sub("", _TEMPLATE_HEADING.sub("", text or "")).strip()


def build_days(threads: list[dict]) -> dict:
    """スレッド一覧 → {date: {"text","chars","terms":Counter,"markers":Counter,"refs":[{name,url}]}}。
    同じ日付のスレッドが複数あれば1日にまとめる。本文が空のスレッドは書いていない日として扱う。"""
    days: dict = {}
    for t in threads:
        body = strip_template(t.get("text") or "")
        if not body:
            continue          # テンプレートだけ(全項目空欄)の投稿は「書いていない日」と同じ扱い
        d = t["date"]
        rec = days.setdefault(d, {"text": "", "refs": []})
        rec["text"] = (rec["text"] + "\n" + body) if rec["text"] else body
        rec["refs"].append({"name": t.get("name", ""), "url": t.get("url", "")})
    for rec in days.values():
        rec["chars"] = _chars(rec["text"])
        rec["terms"] = Counter(extract_terms(rec["text"]))
        rec["markers"] = count_markers(rec["text"])
    return days


def _period_stats(days: dict, start: datetime.date, end: datetime.date) -> dict:
    ds = sorted(d for d in days if start <= d <= end)
    term_days: Counter = Counter()
    term_occ: Counter = Counter()
    markers: Counter = Counter()
    for d in ds:
        term_days.update(set(days[d]["terms"]))
        term_occ.update(days[d]["terms"])
        markers.update(days[d]["markers"])
    return {"start": start, "end": end, "dates": ds, "written": len(ds),
            "chars": sum(days[d]["chars"] for d in ds),
            "term_days": term_days, "term_occ": term_occ, "markers": markers}


def _label(start: datetime.date, end: datetime.date) -> str:
    return f"{start.strftime('%m/%d')}〜{end.strftime('%m/%d')}"


def _pct(cur: float, base: float) -> float | None:
    return None if not base else round((cur - base) / base * 100)


def snippet(text: str, term: str, half: int = SNIPPET_HALF) -> str:
    i = (text or "").find(term)
    if i < 0 and term.isascii():
        i = text.lower().find(term)
    if i < 0:
        return ""
    s, e = max(0, i - half), min(len(text), i + len(term) + half)
    body = " ".join(text[s:e].split())
    return ("…" if s > 0 else "") + body + ("…" if e < len(text) else "")


def _evidence(days: dict, dates: list, term: str, limit: int = 3) -> list[dict]:
    out = []
    for d in dates[:limit]:
        rec = days[d]
        out.append({"date": d.strftime("%m/%d"), "snippet": snippet(rec["text"], term),
                    "url": next((r["url"] for r in rec["refs"] if r.get("url")), "")})
    return out


# ---------------------------------------------------------------------------
# 観測本体
# ---------------------------------------------------------------------------
def observe(days: dict, cur_start: datetime.date, *, unit_days: int = 7,
            n_base: int = 4, n_hist: int = 7) -> dict:
    """cur_start から unit_days 日を「今期」とし、直前 n_base 期間を比較対象、
    直前 n_hist 期間を履歴(新規判定・連続判定)として変化を検出する。
    週次は unit_days=7 / n_base=4 / n_hist=7、月次は unit_days=28 / n_base=1 / n_hist=1。"""
    unit = datetime.timedelta(days=unit_days)
    periods = []
    for i in range(0, max(n_base, n_hist) + 1):
        s = cur_start - unit * i
        periods.append(_period_stats(days, s, s + unit - datetime.timedelta(days=1)))
    cur, prev = periods[0], periods[1]
    base = periods[1:n_base + 1]
    hist = periods[1:n_hist + 1]
    base_active = [p for p in base if p["written"]]
    base_written = sum(p["written"] for p in base_active)
    base_chars = sum(p["chars"] for p in base_active)
    min_days = MIN_DAYS_WEEK if unit_days <= 7 else MIN_DAYS_MONTH
    gone_min = min(GONE_MIN_BASE_PERIODS, n_base)   # 月次(比較1期間)でも減少/不在を判定できるように
    streak_min = STREAK_MIN if n_hist >= STREAK_MIN else None   # 履歴が短い月次では「続いている」を出さない
    baseline_ok = bool(base_active) and cur["written"] > 0

    # --- A. 書く量 ------------------------------------------------------
    nb = len(base_active) or 1
    writing = {
        "written": cur["written"], "span_days": unit_days, "chars": cur["chars"],
        "avg_chars": round(cur["chars"] / cur["written"]) if cur["written"] else 0,
        "prev_written": prev["written"], "prev_chars": prev["chars"],
        "base_written_avg": round(base_written / nb, 1) if base_active else None,
        "base_chars_avg": round(base_chars / nb) if base_active else None,
        "chars_vs_prev_pct": _pct(cur["chars"], prev["chars"]),
        "chars_vs_base_pct": _pct(cur["chars"], base_chars / nb) if base_active else None,
        "daily": [{"date": (cur_start + datetime.timedelta(days=i)).strftime("%m/%d"),
                   "chars": days.get(cur_start + datetime.timedelta(days=i), {}).get("chars", 0)}
                  for i in range(unit_days)] if unit_days <= 7 else [],
        "trend": [{"label": _label(p["start"], p["end"]), "written": p["written"], "chars": p["chars"]}
                  for p in reversed(periods[:n_hist + 1])],
    }

    # --- B. 語の変化 ------------------------------------------------------
    cur_w = max(cur["written"], 1)
    terms: dict[str, list] = {k: [] for k in TERM_KINDS}
    universe = set(cur["term_days"]) | {t for p in base for t in p["term_days"]}
    for t in universe:
        cd = cur["term_days"][t]
        cr = cd / cur_w
        b_days = sum(p["term_days"][t] for p in base_active)
        br = b_days / base_written if base_written else 0.0
        b_present = sum(1 for p in base_active if p["term_days"][t])
        in_hist = any(p["term_days"][t] for p in hist)
        streak = 0
        for p in periods:
            if not p["written"]:
                continue          # 書かなかった期間は連続を途切れさせない(数えもしない)
            if p["term_days"][t]:
                streak += 1
            else:
                break
        pd = prev["term_days"][t]
        item = {"term": t, "days": cd, "written": cur["written"], "ratio": round(cr, 2),
                "base_ratio": round(br, 2), "base_days": b_days, "base_written": base_written,
                "prev_days": pd, "prev_written": prev["written"],
                "prev_ratio": round(pd / prev["written"], 2) if prev["written"] else None,
                "streak": streak, "occ": cur["term_occ"][t],
                # 古い期間→今期の順に「出た日数」(ヒートマップ・時間軸の確認用)
                "series": [p["term_days"][t] for p in reversed(periods[:n_hist + 1])]}
        if cd >= min_days and not in_hist:
            terms["new"].append(item)
        elif cd >= min_days and not b_present:
            terms["returned"].append(item)
        elif not baseline_ok:
            continue
        elif cd >= min_days and cr - br >= RATIO_DELTA and cd - br * cur_w >= EXPECTED_GAP:
            terms["up"].append(item)
        elif (cd >= 1 and b_present >= gone_min and br - cr >= RATIO_DELTA
              and br * cur_w - cd >= EXPECTED_GAP):
            terms["down"].append(item)
        elif (unit_days <= 7 and cd == 0 and pd >= min_days and cur["written"] >= 2
              and not any(p["term_days"][t] for p in periods[2:])):
            terms["transient"].append(item)      # 前週だけ出て、それ以前にも今週にも無い
        elif cd == 0 and b_present >= gone_min and br * cur["written"] >= GONE_EXPECTED:
            last = max(d for p in base_active for d in p["dates"] if days[d]["terms"][t])
            item["last_seen"] = last.strftime("%m/%d")
            terms["gone"].append(item)
        elif streak_min and streak >= streak_min and cd >= 1:
            terms["stable" if abs(cr - br) < STABLE_DELTA else "steady"].append(item)
    terms["new"].sort(key=lambda x: (-x["days"], -x["occ"], x["term"]))
    terms["returned"].sort(key=lambda x: (-x["days"], -x["occ"], x["term"]))
    terms["up"].sort(key=lambda x: (-(x["ratio"] - x["base_ratio"]), x["term"]))
    terms["down"].sort(key=lambda x: (-(x["base_ratio"] - x["ratio"]), x["term"]))
    terms["gone"].sort(key=lambda x: (-x["base_ratio"], x["term"]))
    terms["transient"].sort(key=lambda x: (-x["prev_days"], x["term"]))
    for k in ("stable", "steady"):
        terms[k].sort(key=lambda x: (-x["streak"], -x["days"], x["term"]))
    for k in terms:
        terms[k] = terms[k][:LIST_LIMIT]

    # --- B. 表現の変化 ----------------------------------------------------
    markers = []
    for key, label, _ in MARKERS:
        c = cur["markers"][key]
        b_cnt = sum(p["markers"][key] for p in base_active)
        c_rate = c / cur["chars"] * 1000 if cur["chars"] else 0.0
        b_rate = b_cnt / base_chars * 1000 if base_chars else 0.0
        b_avg = b_cnt / nb if base_active else None
        direction = ""
        expected = b_rate * cur["chars"] / 1000
        if baseline_ok and c + (b_avg or 0) >= MARKER_MIN_TOTAL and abs(c - expected) >= MARKER_MIN_GAP:
            if b_rate == 0 and c >= 3:
                direction = "up"
            elif b_rate and c_rate / b_rate >= MARKER_RATIO_UP:
                direction = "up"
            elif b_rate and c_rate / b_rate <= MARKER_RATIO_DOWN:
                direction = "down"
        p_rate = prev["markers"][key] / prev["chars"] * 1000 if prev["chars"] else None
        markers.append({"key": key, "label": label, "short": MARKER_SHORT[key], "count": c,
                        "prev_rate": round(p_rate, 1) if p_rate is not None else None,
                        "prev": prev["markers"][key], "base_avg": round(b_avg, 1) if b_avg is not None else None,
                        "rate": round(c_rate, 1), "base_rate": round(b_rate, 1), "direction": direction})

    # --- 共起(同じ日に出てくる。因果ではない) ----------------------------
    changed = [x["term"] for k in ("new", "returned", "up") for x in terms[k]]
    term_dates = {t: [d for d in cur["dates"] if days[d]["terms"][t]] for t in changed}
    cooc = []
    for i, a in enumerate(changed):
        for b in changed[i + 1:]:
            shared = sorted(set(term_dates[a]) & set(term_dates[b]))
            if len(shared) >= 2 and a not in b and b not in a:
                cooc.append({"a": a, "b": b, "days": len(shared)})
    cooc.sort(key=lambda x: (-x["days"], x["a"], x["b"]))
    term_markers = []
    for t in changed:
        for key, _, _ in MARKERS:
            both = sum(1 for d in term_dates[t] if days[d]["markers"][key])
            if both >= 2 and both * 2 >= len(term_dates[t]):
                term_markers.append({"term": t, "marker": MARKER_SHORT[key], "days": both,
                                     "term_days": len(term_dates[t])})

    hist_span = f"直前{n_hist}週" if unit_days == 7 else f"直前{unit_days * n_hist}日"
    obs = {"period": _label(cur["start"], cur["end"]), "unit_days": unit_days, "hist_span": hist_span,
           "baseline_ok": baseline_ok, "base_periods": len(base_active),
           "writing": writing, "terms": terms, "markers": markers,
           "cooccur": cooc[:3], "term_markers": sorted(term_markers, key=lambda x: -x["days"])[:3]}
    obs["items"] = _items(obs, days, cur, base_active)
    return obs


def _items(obs: dict, days: dict, cur: dict, base_active: list) -> list[dict]:
    """人が読める観測文(ID付き)と根拠。Gemini にはこのIDで参照させる。"""
    items: list[dict] = []

    def add(kind, text, evidence=None):
        items.append({"id": f"O{len(items) + 1}", "kind": kind, "text": text, "evidence": evidence or []})

    w = obs["writing"]
    unit = "週" if obs["unit_days"] <= 7 else "期間"
    if w["base_written_avg"] is not None:
        add("writing", f"書いた日数 {w['written']}/{w['span_days']}日（{_base_label(obs)} "
                       f"{w['base_written_avg']}日）、総文字数 {w['chars']:,}字（平均比 "
                       f"{_signed_pct(w['chars_vs_base_pct'])}）")
    else:
        add("writing", f"書いた日数 {w['written']}/{w['span_days']}日、総文字数 {w['chars']:,}字（比較できる過去データなし）")

    cur_dates = cur["dates"]
    T = obs["terms"]
    for x in T["new"]:
        dts = [d for d in cur_dates if days[d]["terms"][x["term"]]]
        add("new", f"「{x['term']}」が書いた{x['written']}日中{x['days']}日に出た（{obs['hist_span']}の記録には無い）",
            _evidence(days, dts, x["term"]))
    for x in T["returned"]:
        dts = [d for d in cur_dates if days[d]["terms"][x["term"]]]
        add("returned", f"「{x['term']}」が{x['days']}日に出た（直近{obs['base_periods']}{unit}には無く、{obs['hist_span']}のうちそれより前に出ていた）",
            _evidence(days, dts, x["term"]))
    for x in T["up"]:
        dts = [d for d in cur_dates if days[d]["terms"][x["term"]]]
        add("up", f"「{x['term']}」への言及が増加: 出た日の割合 {_timeline(x, obs)}"
                  f"（今期 {x['days']}/{x['written']}日）", _evidence(days, dts, x["term"]))
    for x in T["down"]:
        dts = [d for d in cur_dates if days[d]["terms"][x["term"]]]
        add("down", f"「{x['term']}」への言及が減少: 出た日の割合 {_timeline(x, obs)}"
                    f"（今期 {x['days']}/{x['written']}日）", _evidence(days, dts, x["term"]))
    for x in T["gone"]:
        dts = sorted((d for p in base_active for d in p["dates"] if days[d]["terms"][x["term"]]), reverse=True)
        add("gone", f"「{x['term']}」が今期は出なかった（比較期間では書いた日の{_pp(x['base_ratio'])}、"
                    f"最後は{x['last_seen']}）", _evidence(days, dts, x["term"], limit=1))
    for x in T["transient"]:
        pdts = [d for p in base_active[:1] for d in p["dates"] if days[d]["terms"][x["term"]]]
        add("transient", f"「{x['term']}」は前週だけ{x['prev_days']}日出て、今期と{obs['hist_span']}のそれ以前には無い",
            _evidence(days, pdts, x["term"], limit=1))
    for m in obs["markers"]:
        if m["direction"]:
            arrow = "増加" if m["direction"] == "up" else "減少"
            prev = f" → 前週 {m['prev_rate']}" if m.get("prev_rate") is not None and obs["unit_days"] <= 7 else ""
            add("marker", f"{m['label']}の出現率が{arrow}: 1000字あたり {_base_label(obs)} {m['base_rate']}"
                          f"{prev} → 今期 {m['rate']}（今期 {m['count']}回）")
    for c in obs["cooccur"]:
        add("cooccur", f"「{c['a']}」と「{c['b']}」が同じ日に{c['days']}日出てきた")
    for tm in obs["term_markers"]:
        add("cooccur", f"「{tm['term']}」が出た{tm['term_days']}日のうち{tm['days']}日に{tm['marker']}の表現もあった")
    return items


def _base_label(obs: dict) -> str:
    if obs["unit_days"] > 7:
        return f"前の{obs['unit_days']}日"
    return f"直前{obs['base_periods']}週の平均"


def _timeline(x: dict, obs: dict) -> str:
    """比較期間 → 前週 → 今週 の順で割合を並べる(月次は 前の28日 → 直近28日)。"""
    if obs["unit_days"] > 7 or x.get("prev_ratio") is None:
        return f"{_base_label(obs)} {_pp(x['base_ratio'])} → 今期 {_pp(x['ratio'])}"
    return f"{_base_label(obs)} {_pp(x['base_ratio'])} → 前週 {_pp(x['prev_ratio'])} → 今週 {_pp(x['ratio'])}"


def _pp(r: float) -> str:
    return f"{round(r * 100)}%"


def _signed_pct(p) -> str:
    return "—" if p is None else f"{p:+d}%"


# ---------------------------------------------------------------------------
# Gemini: 観測 → 解釈の仮説(事実として扱わない)
# ---------------------------------------------------------------------------
PROMPT_EVIDENCE_CHARS = 1200   # Gemini に渡す抜粋の総量上限(本文全体は渡さない)
PROMPT_EVIDENCE_KINDS = ("new", "returned", "up", "down", "transient")   # 今期の変化の根拠だけ渡す
_BANNED = re.compile(r"すべき|しましょう|べきです|必要があります|してください|明らかに|間違いなく|確実に"
                     r"|\d+(?:\.\d+)?\s*(?:点|/10|／10)|スコア")
# 仮説の形で書かれているか(断定文は捨てる)
_HEDGE = re.compile(r"かもしれ|可能性|考えられ|ように見え|とも読め|かも|のでは")


def build_prompt(obs: dict, context_note: str = "") -> str:
    lines, used = [], 0
    for it in obs["items"]:
        lines.append(f"[{it['id']}] {it['text']}")
        if it["kind"] not in PROMPT_EVIDENCE_KINDS:
            continue
        for ev in it["evidence"][:1]:
            if ev["snippet"] and used + len(ev["snippet"]) <= PROMPT_EVIDENCE_CHARS:
                lines.append(f"    - {ev['date']}: {ev['snippet']}")
                used += len(ev["snippet"])
    steady = "、".join(f"{x['term']}({x['streak']}期連続)"
                      for k in ("stable", "steady") for x in obs["terms"][k]) or "なし"
    return f"""あなたは、本人がモーニングジャーナル(朝に頭の中を自由に書き出す日記)から
自分の変化に気づくのを手伝う観測パートナーです。評価・指導・目標設定はしません。

以下は Python が本文を数えて検出した「観測」です(事実)。抜粋は根拠の一部です。
対象期間: {obs['period']}
{context_note}
{chr(10).join(lines)}
続いている語: {steady}

守ること:
- 観測に書かれていないことを事実として述べない。語の回数は感情の強さを表さない。
- 解釈は必ず「〜かもしれない」「〜の可能性がある」の形で書き、1つに断定しない(別の可能性も必ず1つ以上添える)。
- 気持ち・ストレス・幸福度などを点数や数値にしない。語の増減をそのまま感情の増減と言い換えない。
- 「〜すべき」「〜しましょう」などの指示・目標・ToDo・良し悪しの評価は書かない。
- 各仮説は根拠にした観測ID(O1など)を refs に必ず入れる。関連の薄い観測同士を無理につなげない。
- 変化が乏しい場合は、無理に意味を作らず hypotheses を少なくしてよい。

JSON のみで返す:
{{"hypotheses": [{{"refs": ["O2","O5"], "text": "〜かもしれない", "alternatives": ["別の可能性"]}}],
  "continuity": "以前から続く傾向か/新しい変化か、観測から言える範囲で1〜2文",
  "questions": ["本人が考えてみたくなる問い(命令形にしない)"]}}
hypotheses は最大4件、questions は最大2件。日本語。"""


def _loads_loose(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def normalize_interpretation(raw: dict, obs: dict) -> dict:
    """参照IDの無い仮説・指示/断定口調の文を捨てる。"""
    valid = {it["id"] for it in obs["items"]}
    hyps = []
    for h in (raw or {}).get("hypotheses") or []:
        if not isinstance(h, dict):
            continue
        refs = [r for r in (h.get("refs") or []) if isinstance(r, str) and r in valid]
        text = " ".join(str(h.get("text") or "").split())[:160]
        alts = [" ".join(str(a).split())[:120] for a in (h.get("alternatives") or []) if str(a).strip()]
        alts = [a for a in alts if not _BANNED.search(a)][:2]
        if not refs or not text or _BANNED.search(text) or not _HEDGE.search(text):
            continue
        hyps.append({"refs": refs, "text": text, "alternatives": alts})
    qs = [" ".join(str(q).split())[:120] for q in ((raw or {}).get("questions") or [])]
    qs = [q for q in qs if q and not _BANNED.search(q)][:2]
    cont = " ".join(str((raw or {}).get("continuity") or "").split())[:200]
    if _BANNED.search(cont):
        cont = ""
    return {"hypotheses": hyps[:4], "continuity": cont, "questions": qs}


def interpret(obs: dict, api_key: str, model: str, context_note: str = "") -> tuple[dict | None, str]:
    """(解釈, 状態) を返す。状態: 'gemini' / 'few'(変化の観測が無い) / 'no_key' / 'failed'。"""
    if len(obs["items"]) <= 1:
        return None, "few"
    if not api_key:
        print("[INFO] GEMINI_API_KEY 未設定。解釈(仮説)はスキップし観測のみ出力")
        return None, "no_key"
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model, contents=build_prompt(obs, context_note),
            config={"response_mime_type": "application/json"},
        )
        result = normalize_interpretation(_loads_loose(resp.text or ""), obs)
        print(f"[INFO] Gemini({model}) 解釈: 仮説{len(result['hypotheses'])}件 / 問い{len(result['questions'])}件")
        return result, "gemini"
    except Exception as err:  # noqa: BLE001
        print(f"[WARN] Gemini 解釈に失敗（観測のみで継続）: {str(err)[:200]}")
        return None, "failed"


# ---------------------------------------------------------------------------
# 出力: Discord 本文 / シート行 / CIログ用の伏せ字サマリー
# ---------------------------------------------------------------------------
HEAD_MARK = "📊 週次観測"
def _term_line(kind: str, x: dict) -> str:
    """一覧用の短い表記。増減・継続は 比較期間→前週→今週 の割合を並べる。"""
    if kind in ("new", "returned"):
        return f"{x['term']}({x['days']}日)"
    if kind in ("up", "down", "stable", "steady"):
        mid = f"→前週{_pp(x['prev_ratio'])}" if x.get("prev_ratio") is not None and len(x.get("series", [])) > 2 else ""
        tail = f"・{x['streak']}期連続" if kind in ("stable", "steady") else ""
        return f"{x['term']}({_pp(x['base_ratio'])}{mid}→{_pp(x['ratio'])}{tail})"
    if kind == "gone":
        return f"{x['term']}(最後 {x['last_seen']})"
    if kind == "transient":
        return f"{x['term']}(前週{x['prev_days']}日)"
    return x["term"]


def compose_messages(obs: dict, interp: dict | None, interp_status: str, *, head: str,
                     warnings: list[str] | None = None, sheet_link: str | None = None,
                     extra_lines: list[str] | None = None) -> list[str]:
    """Discord 用。[1通目: A 数値 + B 変化] [2通目: C 仮説 + D 根拠] に分け、各1900字以内。"""
    w = obs["writing"]
    unit = "週" if obs["unit_days"] <= 7 else "期間"
    a = [f"{head}  {obs['period']}", ""]
    for x in warnings or []:
        a.append(f"⚠️ {x}")
    if warnings:
        a.append("")
    a.append("**A. 書いた記録**（Pythonで計数）")
    base = (f" ／ {_base_label(obs)} {w['base_written_avg']}日・{w['base_chars_avg']:,}字"
            if w["base_written_avg"] is not None else " ／ 比較できる過去データなし")
    a.append(f"・{w['written']}/{w['span_days']}日 書いた・計{w['chars']:,}字（1日平均{w['avg_chars']:,}字）{base}")
    if w["chars_vs_prev_pct"] is not None:
        a.append(f"・文字数 前{unit}比 {_signed_pct(w['chars_vs_prev_pct'])}")
    a.append("")
    a.append("**B. 変化**（語が書いた日のうち何日に出たか＝言及の多さ。気持ちの強さではありません）")
    T = obs["terms"]
    any_change = False
    for k in ("new", "returned", "up", "down", "transient", "gone"):
        if T[k]:
            any_change = True
            a.append(f"{TERM_KIND_ICON[k]} {TERM_KIND_NAME[k]}: " + "、".join(_term_line(k, x) for x in T[k]))
    if not any_change:
        a.append("・目立った語の増減は検出されませんでした" + ("" if obs["baseline_ok"] else "（比較データ不足）"))
    mk = [m for m in obs["markers"] if m["direction"]]
    if mk:
        a.append("🗣 表現の出現率（千字あたり）: " + "、".join(
            f"{m['short']} {m['base_rate']}"
            + (f"→前週{m['prev_rate']}" if m.get("prev_rate") is not None and obs["unit_days"] <= 7 else "")
            + f"→{m['rate']}" for m in mk))
    if obs["cooccur"] or obs["term_markers"]:
        pairs = [f"{c['a']}×{c['b']}({c['days']}日)" for c in obs["cooccur"][:3]]
        pairs += [f"{t['term']}×{t['marker']}表現({t['days']}/{t['term_days']}日)" for t in obs["term_markers"][:3]]
        a.append("🔗 同じ日に出た: " + "、".join(pairs))
    if T["stable"] or T["steady"]:
        a += ["", "**変わらなかったこと**" + ("（直前4週→前週→今週）" if obs["unit_days"] <= 7 else "")]
        for k in ("stable", "steady"):
            if T[k]:
                a.append(f"{TERM_KIND_ICON[k]} {TERM_KIND_NAME[k]}: " + "、".join(_term_line(k, x) for x in T[k]))
    a += list(extra_lines or [])
    if sheet_link:
        a += ["", f"📄 推移（シート）: {sheet_link}"]

    c = ["**C. 気づき（Geminiによる仮説。事実ではありません）**"]
    if interp and (interp["hypotheses"] or interp["continuity"]):
        for h in interp["hypotheses"]:
            c.append(f"・[{','.join(h['refs'])}] {h['text']}")
            for alt in h["alternatives"]:
                c.append(f"　└ 別の可能性: {alt}")
        if interp["continuity"]:
            c.append(f"・継続/新規: {interp['continuity']}")
        for q in interp["questions"]:
            c.append(f"❓ {q}")
    else:
        reason = {"failed": "Gemini の呼び出しに失敗", "few": "解釈するほどの変化が観測されなかった",
                  "no_key": "Gemini 未設定"}.get(interp_status, interp_status)
        c.append(f"・今回は仮説なし（{reason}）。A・B の観測だけで完結しています。")
    c += ["", "**D. 根拠**（観測ID → 日付・元スレッド）"]
    for it in obs["items"]:
        c.append(f"{it['id']} {it['text']}")
        for ev in it["evidence"][:1]:
            c.append(f"　{ev['date']} {ev['snippet']} {('<' + ev['url'] + '>') if ev['url'] else ''}".rstrip())
    return _chunk("\n".join(a)) + _chunk("\n".join(c))


def _chunk(text: str, limit: int = 1900) -> list[str]:
    out, buf = [], ""
    for line in text.split("\n"):
        line = line[:limit]
        if len(buf) + len(line) + 1 > limit:
            out.append(buf.rstrip("\n"))
            buf = ""
        buf += line + "\n"
    if buf.strip():
        out.append(buf.rstrip("\n"))
    return out


SHEET_HEADER = (["対象週", "書いた日数", "総文字数", "1日平均文字数", "文字数 前週比%"]
                + [MARKER_SHORT[k] for k, _, _ in MARKERS]
                + [TERM_KIND_NAME[k] + "語" for k in TERM_KINDS]
                + ["気づき（Gemini仮説）", "根拠スレッド", "解釈の状態"])


def sheet_row(week_tag: str, obs: dict, interp: dict | None, interp_status: str) -> list:
    w, T = obs["writing"], obs["terms"]
    by_key = {m["key"]: m["count"] for m in obs["markers"]}
    hyp = "\n".join(f"[{','.join(h['refs'])}] {h['text']}" for h in (interp or {}).get("hypotheses", []))
    urls = []
    for it in obs["items"]:
        for ev in it["evidence"]:
            if ev["url"] and ev["url"] not in urls:
                urls.append(ev["url"])
    return ([week_tag, w["written"], w["chars"], w["avg_chars"],
             "" if w["chars_vs_prev_pct"] is None else w["chars_vs_prev_pct"]]
            + [by_key.get(k, 0) for k, _, _ in MARKERS]
            + ["、".join(_term_line(k, x) for x in T[k]) for k in TERM_KINDS]
            + [hyp, "\n".join(urls[:10]), interp_status])


def redacted_summary(obs: dict) -> str:
    """公開ログ(GitHub Actions)向け: 語・本文を出さず件数だけ。"""
    w, T = obs["writing"], obs["terms"]
    return (f"書いた日数={w['written']} 文字数={w['chars']} 観測={len(obs['items'])}件 "
            + " ".join(f"{k}={len(v)}" for k, v in T.items())
            + f" 表現変化={sum(1 for m in obs['markers'] if m['direction'])}")


# ---------------------------------------------------------------------------
# PNG ダッシュボード(A. 数値・グラフ)
# ---------------------------------------------------------------------------
INK, MUTED, GRID, ACCENT, REF = "#1F2937", "#6B7280", "#E5E7EB", "#2563EB", "#9CA3AF"


def render_png(obs: dict, out_path: str, *, title: str, font_family: str | None = None) -> str:
    import os

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if font_family:
        plt.rcParams["font.family"] = font_family
    plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED,
                         "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False})
    w, T = obs["writing"], obs["terms"]
    fig = plt.figure(figsize=(12, 12), dpi=110, facecolor="white")
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 1.9], hspace=0.55, wspace=0.28)
    fig.suptitle(title, fontsize=17, color=INK, x=0.03, ha="left", y=0.985)
    fig.text(0.03, 0.935, "数値はPythonによる計数です。出現回数は気持ちの強さや良し悪しを表しません。",
             fontsize=9.5, color=MUTED)

    labels = [t["label"].split("〜")[0] for t in w["trend"]]
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.bar(labels, [t["written"] for t in w["trend"]],
            color=[REF] * (len(labels) - 1) + [ACCENT], width=0.6)
    ax1.set_title("書いた日数（週ごと・横軸は週の初日）" if w["span_days"] <= 7 else "書いた日数", fontsize=12, color=INK, loc="left")
    ax1.set_ylim(0, w["span_days"] if w["span_days"] <= 7 else None)
    ax1.tick_params(labelsize=8.5)
    ax1.grid(axis="y", color=GRID, lw=0.8)
    ax1.set_axisbelow(True)

    ax2 = fig.add_subplot(gs[0, 1])
    ax2.bar(labels, [t["chars"] for t in w["trend"]],
            color=[REF] * (len(labels) - 1) + [ACCENT], width=0.6)
    ax2.set_title("総文字数（週ごと）" if w["span_days"] <= 7 else "総文字数", fontsize=12, color=INK, loc="left")
    ax2.tick_params(labelsize=8.5)
    ax2.grid(axis="y", color=GRID, lw=0.8)
    ax2.set_axisbelow(True)

    ax3 = fig.add_subplot(gs[1, 0])
    if w["daily"]:
        ax3.bar([d["date"] for d in w["daily"]], [d["chars"] for d in w["daily"]], color=ACCENT, width=0.6)
        ax3.set_title("今週の日ごとの文字数", fontsize=12, color=INK, loc="left")
    ax3.tick_params(labelsize=8.5)
    ax3.grid(axis="y", color=GRID, lw=0.8)
    ax3.set_axisbelow(True)

    ax4 = fig.add_subplot(gs[1, 1])
    ms = obs["markers"]
    ys = list(range(len(ms)))[::-1]
    weekly = w["span_days"] <= 7
    series = [("base_rate", "直前4週の平均" if weekly else "前の期間", "#D1D5DB")]
    if weekly:
        series.append(("prev_rate", "前週", REF))
    series.append(("rate", "今週" if weekly else "今期", ACCENT))
    hgt = 0.8 / len(series)
    for i, (key, lbl, col) in enumerate(series):
        off = (len(series) - 1) / 2 * hgt - i * hgt
        ax4.barh([y + off for y in ys], [m.get(key) or 0 for m in ms], height=hgt * 0.9, color=col, label=lbl)
    ax4.set_yticks(ys)
    ax4.set_yticklabels([m["short"] + (" ▲" if m["direction"] == "up" else " ▼" if m["direction"] == "down" else "")
                         for m in ms], fontsize=9)
    ax4.set_title("表現の出現率（1000字あたり）", fontsize=12, color=INK, loc="left")
    ax4.legend(fontsize=8.5, frameon=False, loc="lower right")
    ax4.tick_params(axis="x", labelsize=8.5)
    ax4.grid(axis="x", color=GRID, lw=0.8)
    ax4.set_axisbelow(True)

    # 語 × 期間 のヒートマップ(セル=その期間に出た日数、色=書いた日のうちの割合)
    ax5 = fig.add_subplot(gs[2, :])
    rows = []
    for k in ("new", "returned", "up", "down", "transient", "gone", "stable", "steady"):
        for x in T[k][:3]:
            rows.append((f"{x['term']}［{TERM_KIND_NAME[k].split('（')[0]}］", x["series"]))
    written = [t["written"] for t in w["trend"]]
    if rows and all(len(r[1]) == len(written) for r in rows):
        import numpy as np
        grid = np.array([[d / wr if wr else np.nan for d, wr in zip(r[1], written)] for r in rows])
        ax5.imshow(np.ma.masked_invalid(grid), aspect="auto", cmap="Blues", vmin=0, vmax=1)
        for yi, r in enumerate(rows):
            for xi, d in enumerate(r[1]):
                val = grid[yi, xi]
                txt = "–" if np.isnan(val) else str(d)
                ax5.text(xi, yi, txt, ha="center", va="center", fontsize=8.5,
                         color="white" if (not np.isnan(val) and val > 0.55) else INK)
        ax5.set_yticks(range(len(rows)))
        ax5.set_yticklabels([r[0] for r in rows], fontsize=9)
        ax5.set_xticks(range(len(written)))
        ax5.set_xticklabels([t["label"].split("〜")[0] + ("\n今週" if i == len(written) - 1 else "")
                             for i, t in enumerate(w["trend"])], fontsize=8.5)
        ax5.tick_params(length=0)
        for sp in ax5.spines.values():
            sp.set_visible(False)
        ax5.set_title("語の推移（数字＝その週に出た日数、濃さ＝書いた日のうちの割合、–＝書かなかった週）",
                      fontsize=12, color=INK, loc="left")
    else:
        ax5.axis("off")
        ax5.set_title("語の推移", fontsize=12, color=INK, loc="left")
        ax5.text(0.0, 0.8, "今期は比較できる語の変化がありませんでした。", fontsize=10.5, color=MUTED,
                 transform=ax5.transAxes)
    if not obs["baseline_ok"]:
        fig.text(0.03, 0.005, "※ 比較できる過去データが無いため、増減・消失は判定していません。",
                 fontsize=9.5, color=MUTED)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fig.savefig(out_path, facecolor="white", bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    print(f"[OK] 観測ダッシュボード PNG: {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# モック(複数週の合成ジャーナル。テスト・--use-mock 用)
# ---------------------------------------------------------------------------
def mock_threads(cur_monday: datetime.date, weeks: int = 8) -> list[dict]:
    """変化を仕込んだ合成データ: 写真=ずっと / 飲み会=過去だけ / 面接=今週から /
    部屋の片付け=今週増 / 今週は負担表現が増える。"""
    base_lines = [
        "朝はまだ眠い。写真を撮りに行きたいなと思った。散歩して1万歩いけた。",
        "昨日の飲み会が楽しかった。写真の整理をしたい。",
        "体調はふつう。写真の現像をやった。カメラのレンズが欲しい。",
        "飲み会で話したことを思い出す。散歩は短めにした。",
    ]
    cur_lines = [
        "面接の準備が面倒。部屋の片付けもできなかった。写真は少しだけ撮った。",
        "面接でどう話すか気になる。部屋が散らかっていてしんどい。",
        "部屋の片付けを後回しにした。面接の服を決めた。写真展に行きたい。",
        "眠い。面接が終わったら部屋を片付けよう。散歩はできた。",
        "部屋の片付けが面倒。でも写真を見返すと楽しい。",
    ]
    out = []
    gid, tid = "100", 2000
    for wk in range(weeks - 1, -1, -1):
        mon = cur_monday - datetime.timedelta(days=7 * wk)
        lines = cur_lines if wk == 0 else base_lines
        for i, line in enumerate(lines):
            d = mon + datetime.timedelta(days=i)
            tid += 1
            out.append({"tid": str(tid), "name": d.strftime("%Y/%m/%d"), "date": d,
                        "url": f"https://discord.com/channels/{gid}/{tid}", "text": line})
    return out
