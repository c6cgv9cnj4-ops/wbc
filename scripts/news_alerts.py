# -*- coding: utf-8 -*-
"""
ニュース自動配信(fetch_news.py)の「失敗の可視化」(2026-09-26追加)

Actions全体はsuccessのまま、ログのERROR/WARNにしか出ない重要な異常
(取得失敗・Gemini/APIエラー・Discord送信失敗・重要処理のスキップ)を実行中に
record()で記録し、実行の最後にflush()でDiscordへ短い異常通知として送る。

設計上の約束:
  - 通常のニュース投稿の内容・送信順・既存ロジックは一切変えない(記録と最後の通知だけ)
  - 1回の実行で同じ種類の異常は1行にまとめる(件数は「×N」で表示)
  - 同じ種類の異常は ALERT_COOLDOWN_HOURS の間は再通知しない
    (あんぜんねっと403のように毎回起きる異常で通知が連投されるのを防ぐ。
     最終通知時刻は state/news_seen.json に "_alert:<種類>" キーで保存する。
     値はISO日時なので、既存の14日掃除(prune_old_entries)の対象になる)
  - 通知は影響を受けたチャンネルへ送る。そのWebhookが無い/送信に失敗した場合は、
    他の配信用Webhookへ順に送る
  - 通知の失敗で通常配信や終了コードを変えない
  - エラー文中のDiscord Webhook URL(トークンを含む)は伏せ字にする
  - Discordには人間向けの要約だけを出す(2026-10-06): 「対象→原因→配信への影響」を平易な文に整え、
    HTTP 429やJSONなどの生エラーは本文に出さない(技術コードはfooterに短く1つだけ。詳細はログに全文を残す)。
    見た目の共通原則は discord_style.py
"""
import datetime
import re

import discord_style

ALERT_COOLDOWN_HOURS = 12
ALERT_STATE_PREFIX = "_alert:"
CHANNEL_LABELS = {"local": "#webhook_local", "news": "#webhook_news", "market": "#webhook_market"}
FALLBACK_ORDER = ["market", "news", "local"]

_issues = {}


def reset():
    _issues.clear()


def redact(text):
    """Discord Webhook(トークンを含む)をURL全体・パスのみのどちらの形でも伏せ字にする。
    requestsの例外文は「url: /api/webhooks/ID/TOKEN」のようにホスト無しのパスで出るため。"""
    return re.sub(r"(?:https?://(?:[\w-]+\.)?discord(?:app)?\.com)?/api/webhooks/[^\s'\")]+", "<webhook>", str(text))


def short_error(err, limit=120):
    """例外やエラー文を通知用に短くする(HTTPステータスがあれば先頭に出す)。"""
    text = redact(err)
    m = re.search(r"\b(4\d\d|5\d\d)\b", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return f"HTTP {m.group(1)}: {text}" if m and f"HTTP {m.group(1)}" not in text else text


def record(kind, channel, target, detail, delivery):
    """異常を記録する。同じkindは1件にまとめ、回数だけ数える。
    channel: 影響を受けた配信先("local"/"news"/"market")
    target: 対象(例: 「あんぜんねっと(北本市安全安心情報)」)
    detail: 内容(short_error()済みの文字列を推奨)
    delivery: 通常配信への影響(例: 「継続」「一部スキップ」)
    """
    if kind in _issues:
        _issues[kind]["count"] += 1
        return
    _issues[kind] = {
        "kind": kind, "channel": channel, "target": target,
        "detail": detail, "delivery": delivery, "count": 1,
    }


def issues():
    return list(_issues.values())


def _in_cooldown(state, kind, now):
    last = state.get(ALERT_STATE_PREFIX + kind)
    if not last:
        return False
    try:
        return now - datetime.datetime.fromisoformat(last) < datetime.timedelta(hours=ALERT_COOLDOWN_HOURS)
    except (TypeError, ValueError):
        return False


_TECH_STATUS_RE = re.compile(r"\b(RESOURCE_EXHAUSTED|UNAVAILABLE|DEADLINE_EXCEEDED|PERMISSION_DENIED|UNAUTHENTICATED)\b")
# 原因の言い回し。{s} は対象(Gemini など)。
_REASONS = {
    "quota": "{s}の利用上限に達しました",
    "busy": "{s}が一時的に混み合っています",
    "server": "{s}側で一時的なエラーが発生しました",
    "unauth": "{s}の認証に失敗しました",
    "denied": "{s}へのアクセスが拒否されました",
    "notfound": "{s}で対象が見つかりませんでした",
    "timeout": "{s}から応答がありませんでした",
}
_DELIVERY_HEAD = {"継続": "配信は継続", "スキップ": "今回はスキップ", "一部スキップ": "一部をスキップ", "停止中": "停止中"}


def explain(detail, kind=""):
    """記録された内容(detail)を、人間向けの原因文と技術コード(最大1つ)に分ける。
    戻り値: (原因文, 技術コードまたはNone)。生のJSON・応答全文は返さない(ログ側に残す)。"""
    t = re.sub(r"\s+", " ", redact(detail)).strip()
    low = t.lower()
    m_http = re.search(r"HTTP\s*(\d{3})", t)
    code = m_http.group(1) if m_http else None
    m_status = _TECH_STATUS_RE.search(t)
    status = m_status.group(1) if m_status else None
    if t.startswith("Gemini") or kind.startswith("gemini"):
        subject = "Gemini"
    elif kind.startswith("discord"):
        subject = "Discord"
    else:
        subject = "取得先"

    reason = None
    if code == "429" or status == "RESOURCE_EXHAUSTED" or "quota" in low:
        reason = "quota"
    elif code == "503" or status == "UNAVAILABLE" or "overloaded" in low:
        reason = "busy"
    elif code and code.startswith("5"):
        reason = "server"
    elif code == "401" or status == "UNAUTHENTICATED":
        reason = "unauth"
    elif code == "403" or status == "PERMISSION_DENIED":
        reason = "denied"
    elif code == "404":
        reason = "notfound"
    elif "timeout" in low or "timed out" in low or "タイムアウト" in t or status == "DEADLINE_EXCEEDED":
        reason = "timeout"
    tech = f"HTTP {code}" if code else status
    if reason:
        return _REASONS[reason].format(s=subject), tech

    # 辞書に無いものは、JSON断片やHTTP接頭辞を落として短い平文にする
    text = re.split(r"[{]", t, maxsplit=1)[0]
    text = re.sub(r"^HTTP\s*\d{3}:?\s*", "", text).strip(" :")
    text = text.replace("取得失敗", "取得に失敗しました", 1).replace("送信失敗", "送信に失敗しました", 1)
    text = re.sub(r"\((ログにHTTPステータスあり)\)", "", text).strip()
    if not text:
        text = "エラーが発生しました"
    return (text[:80] + "…" if len(text) > 80 else text), tech


def humanize_delivery(delivery):
    """「スキップ(理由)」形式を、読みやすい文に整える。形式が違えばそのまま返す。"""
    m = re.match(r"^(継続|スキップ|一部スキップ|停止中)(?:[（(](.*)[）)])?$", str(delivery).strip())
    if not m:
        return str(delivery)
    head = _DELIVERY_HEAD[m.group(1)]
    return f"{head}。{m.group(2)}。" if m.group(2) else f"{head}。"


def _compose(state, now):
    """クールダウン中でない異常を、配信先チャンネルごとに「見出し・ブロック・補足」へ整理する。
    戻り値: ({channel: {"blocks": [...], "footer": str}}, [通知対象のkind], [抑止したkind])"""
    by_channel, notified, suppressed = {}, [], []
    for issue in _issues.values():
        if _in_cooldown(state, issue["kind"], now):
            suppressed.append(issue["kind"])
            continue
        by_channel.setdefault(issue["channel"], []).append(issue)
        notified.append(issue["kind"])

    composed = {}
    for channel, items in by_channel.items():
        blocks, techs = [], []
        for it in items:
            cause, tech = explain(it["detail"], it["kind"])
            if tech and tech not in techs:
                techs.append(tech)
            count = f" ×{it['count']}" if it["count"] > 1 else ""
            blocks.append("\n".join([
                f"{discord_style.heading(it['target'])}{count}",
                cause,
                f"影響: {humanize_delivery(it['delivery'])}",
            ]))
        # 技術コードは異常が1種類のときだけ短く添える(複数だとどの異常のものか曖昧になるため。全文はログ側)
        shown_techs = techs if len(items) == 1 else []
        footer = " ・ ".join([f"{now.strftime('%H:%M')} JST"] + shown_techs
                            + [f"同じ異常は{ALERT_COOLDOWN_HOURS}時間通知しません"])
        composed[channel] = {"blocks": blocks, "footer": footer}
    return composed, notified, suppressed


TITLE = "⚠️ ニュース自動配信の異常"


def build_messages(state, now):
    """通知本文(Embedが使えないときの通常メッセージ版)。
    戻り値: ({channel: message}, [通知対象のkind], [クールダウンで抑止したkind])"""
    composed, notified, suppressed = _compose(state, now)
    messages = {}
    for channel, c in composed.items():
        messages[channel] = "\n\n".join([discord_style.heading(TITLE), *c["blocks"],
                                          discord_style.subtext(c["footer"])])
    return messages, notified, suppressed


def build_embeds(state, now):
    """通知のEmbed版(赤い縦線つき。本当に重要な障害だけが色で目立つ)。
    戻り値: ({channel: payload}, [通知対象のkind], [クールダウンで抑止したkind])"""
    composed, notified, suppressed = _compose(state, now)
    payloads = {}
    for channel, c in composed.items():
        payloads[channel] = {"embeds": [{
            "title": TITLE,
            "description": "\n\n".join(c["blocks"])[:4000],
            "color": discord_style.ALERT_COLOR,
            "footer": {"text": c["footer"]},
        }]}
    return payloads, notified, suppressed


def flush(webhooks, send_fn, state, now, embed_fn=None):
    """異常通知を送り、送れた種類の最終通知時刻をstateに記録する。
    webhooks: {"local": url, "news": url, "market": url}(未設定はNone可)
    send_fn: fetch_news.send_to_discord (url, message) -> bool
    embed_fn: fetch_news.send_embed_to_discord (url, payload) -> bool。あればEmbedで送り、
              失敗したときだけ通常メッセージで再送する(無ければ通常メッセージのみ)
    戻り値: 送信できた通知数
    """
    messages, notified, suppressed = build_messages(state, now)
    payloads, _, _ = build_embeds(state, now)
    if suppressed:
        print(f"[INFO] クールダウン中のため異常通知を抑止: {', '.join(suppressed)}")
    sent = 0
    for channel, message in messages.items():
        print(f"=== 異常通知({CHANNEL_LABELS.get(channel, channel)}向け) ===")
        print(message)
        # 技術的な詳細はDiscordに出さない代わりに、ログには従来どおり全文を残す
        for it in _issues.values():
            if it["channel"] == channel and it["kind"] in notified:
                print(f"[ISSUE] kind={it['kind']} x{it['count']} target={it['target']} "
                      f"detail={redact(it['detail'])} delivery={it['delivery']}")
        order = [channel] + [c for c in FALLBACK_ORDER if c != channel]
        delivered = False
        for c in order:
            url = webhooks.get(c)
            if not url:
                continue
            try:
                if embed_fn and embed_fn(url, payloads[channel]):
                    delivered = True
                    break
                if send_fn(url, message):
                    delivered = True
                    break
            except Exception as err:  # noqa: BLE001
                print(f"[WARN] 異常通知の送信で例外: {short_error(err)}")
        if delivered:
            sent += 1
            # 抑止中の種類は時刻を更新しない(更新すると永久に再通知されなくなる)
            for it in _issues.values():
                if it["channel"] == channel and it["kind"] in notified:
                    state[ALERT_STATE_PREFIX + it["kind"]] = now.isoformat()
        else:
            print("[WARN] 異常通知をどのWebhookにも送れませんでした(通常配信・終了コードには影響させません)。")
    return sent
