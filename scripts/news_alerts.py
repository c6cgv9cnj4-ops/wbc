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
"""
import datetime
import re

ALERT_COOLDOWN_HOURS = 12
ALERT_STATE_PREFIX = "_alert:"
CHANNEL_LABELS = {"local": "#webhook_local", "news": "#webhook_news", "market": "#webhook_market"}
FALLBACK_ORDER = ["market", "news", "local"]

_issues = {}


def reset():
    _issues.clear()


def short_error(err, limit=120):
    """例外やエラー文を通知用に短くする(HTTPステータスがあれば先頭に出す)。"""
    text = str(err)
    text = re.sub(r"https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\S+", "<webhook>", text)
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


def build_messages(state, now):
    """クールダウン中でない異常を、配信先チャンネルごとの通知本文にまとめる。
    戻り値: ({channel: message}, [通知対象のkind], [クールダウンで抑止したkind])
    """
    by_channel, notified, suppressed = {}, [], []
    for issue in _issues.values():
        if _in_cooldown(state, issue["kind"], now):
            suppressed.append(issue["kind"])
            continue
        by_channel.setdefault(issue["channel"], []).append(issue)
        notified.append(issue["kind"])

    messages = {}
    stamp = now.strftime("%Y-%m-%d %H:%M")
    for channel, items in by_channel.items():
        lines = [f"⚠️ **ニュース自動配信で異常を検知** ({stamp} JST)"]
        for it in items:
            count = f" ×{it['count']}" if it["count"] > 1 else ""
            lines.append(f"・対象: {it['target']}{count}")
            lines.append(f"　内容: {it['detail']}")
            lines.append(f"　通常配信: {it['delivery']}")
        lines.append(f"※同じ異常は{ALERT_COOLDOWN_HOURS}時間は再通知しません")
        messages[channel] = "\n".join(lines)
    return messages, notified, suppressed


def flush(webhooks, send_fn, state, now):
    """異常通知を送り、送れた種類の最終通知時刻をstateに記録する。
    webhooks: {"local": url, "news": url, "market": url}(未設定はNone可)
    send_fn: fetch_news.send_to_discord (url, message) -> bool
    戻り値: 送信できた通知数
    """
    messages, notified, suppressed = build_messages(state, now)
    if suppressed:
        print(f"[INFO] クールダウン中のため異常通知を抑止: {', '.join(suppressed)}")
    sent = 0
    for channel, message in messages.items():
        print(f"=== 異常通知({CHANNEL_LABELS.get(channel, channel)}向け) ===")
        print(message)
        order = [channel] + [c for c in FALLBACK_ORDER if c != channel]
        delivered = False
        for c in order:
            url = webhooks.get(c)
            if not url:
                continue
            try:
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
