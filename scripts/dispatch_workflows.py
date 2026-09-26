# -*- coding: utf-8 -*-
"""
GitHub Actions のcron遅延対策: 自宅Macの launchd から5分ごとに実行し、
速報系ワークフローを `gh workflow run` で起動する(2026-09-26追加・N3)。

cron は設定10〜30分に対し実測3〜4時間おきにしか起動されないため、Mac を「起動係」にする。
処理そのもの(取得・state・Discord送信)は従来どおり Actions 側で行い、Mac には秘密情報も
stateも置かない(gh の認証だけを使う)。既存の cron は残すので、Mac が止まっても従来の
頻度には戻るだけで、配信が止まることはない。

起動の条件(ワークフローごと):
  - 実行中・待機中の run が無い(同時実行による古いstateでの二重配信を避ける)
  - 直近の run(cron・手動を問わず)の作成から、最小間隔(分)が経過している

使い方:
  .venv/bin/python scripts/dispatch_workflows.py            # 条件を満たすものを起動
  .venv/bin/python scripts/dispatch_workflows.py --dry-run  # 判定だけ表示(起動しない)

異常(gh の認証切れ・API失敗)は exit 1 とログ(~/Library/Logs/actions-dispatcher.log)に出す。
Mac の停止そのものは Actions 側(fetch_news.py の MAC_DISPATCHER=on)で「前回実行からの空き」として検知する。
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = "c6cgv9cnj4-ops/wbc"
REF = "main"
# (ワークフロー, 最小間隔[分])。cron の設定値に合わせる。
WORKFLOWS = [
    ("jma_alerts.yml", 10),
    ("news.yml", 30),
    ("nikkei_cnbc_digest.yml", 60),
]
BUSY_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}

# 2026-09-27(N7): あんぜんねっと自宅Mac配信(com.rickykogyo.anzn-local)だけが止まった場合の検知。
# anzn_local.py は15分ごとの実行のたびに state を保存するため、その更新時刻で生存を判定する。
# Mac全体の停止は Actions 側(news.yml の MAC_DISPATCHER=on)で検知済みなので、ここでは扱わない。
SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/anzn-local")
ANZN_STATE_PATH = os.path.join(SUPPORT_DIR, "anzn_seen.json")
DISPATCHER_STATE_PATH = os.path.join(SUPPORT_DIR, "dispatcher_state.json")
ANZN_STALE_MINUTES = 60       # 15分周期の3回分以上が止まったら異常とみなす
DISPATCHER_AWAKE_MINUTES = 15  # 起動係自身が直前まで動いていた(スリープ復帰直後ではない)とみなす間隔


def gh(*args):
    """gh を実行して (returncode, stdout, stderr) を返す。"""
    exe = shutil.which("gh") or "/opt/homebrew/bin/gh"
    proc = subprocess.run([exe, *args], capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def latest_run(workflow):
    code, out, err = gh("run", "list", "-R", REPO, "--workflow", workflow, "-L", "1",
                        "--json", "status,createdAt,event")
    if code != 0:
        raise RuntimeError(f"gh run list 失敗({workflow}): {err.strip()[:200]}")
    runs = json.loads(out or "[]")
    return runs[0] if runs else None


def decide(run, min_interval, now):
    """(起動するか, 理由) を返す。"""
    if run is None:
        return True, "過去のrunなし"
    if run.get("status") in BUSY_STATUSES:
        return False, f"実行中/待機中({run['status']})"
    created = datetime.datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
    elapsed = (now - created).total_seconds() / 60
    if elapsed < min_interval:
        return False, f"前回から{elapsed:.0f}分(<{min_interval}分)"
    return True, f"前回から{elapsed:.0f}分({run.get('event')})"


def check_anzn_liveness(now, dry_run=False):
    """ANZNの state が ANZN_STALE_MINUTES より古ければ、既存の異常通知(12時間抑止)で #webhook_local へ知らせる。
    起動係自身がスリープ等で止まっていた直後の回は、ANZNも同様に止まっていただけなので判定しない。"""
    try:
        with open(DISPATCHER_STATE_PATH, encoding="utf-8") as f:
            dstate = json.load(f)
    except (OSError, ValueError):
        dstate = {}
    prev = dstate.get("last_run")
    awake = False
    if prev:
        try:
            awake = (now - datetime.datetime.fromisoformat(prev)).total_seconds() / 60 <= DISPATCHER_AWAKE_MINUTES
        except (TypeError, ValueError):
            awake = False
    dstate["last_run"] = now.isoformat()

    stale_minutes = None
    if os.path.exists(ANZN_STATE_PATH):
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(ANZN_STATE_PATH), datetime.timezone.utc)
        stale_minutes = (now - mtime).total_seconds() / 60
    is_stale = awake and stale_minutes is not None and stale_minutes > ANZN_STALE_MINUTES
    label = "未作成" if stale_minutes is None else f"{stale_minutes:.0f}分前"
    print(f"  anzn-local 生存確認: 最終実行 {label}" + (" → 異常" if is_stale else "") + ("" if awake else "(起動係の復帰直後のため判定せず)"))

    if is_stale and not dry_run:
        # 通知のときだけ読み込む(通常時の起動係の動作・依存に影響させない)
        import anzn_local  # noqa: E402
        import fetch_news  # noqa: E402
        import news_alerts  # noqa: E402
        webhook = anzn_local.load_webhook(anzn_local.DEFAULT_ENV_PATH)
        if webhook:
            news_alerts.record("anzn_local_stale", "local", "あんぜんねっと自宅Mac配信(launchd)",
                               f"最終実行から{stale_minutes:.0f}分経過(anzn-local ジョブの停止・異常終了の可能性)",
                               "停止中(復旧までは安全安心情報の新着が届きません)")
            news_alerts.flush({"local": webhook}, fetch_news.send_to_discord, dstate, now)
        else:
            print("  [ERROR] 通知用の DISCORD_WEBHOOK_LOCAL が見つかりません。")
    if not dry_run:
        os.makedirs(SUPPORT_DIR, exist_ok=True)
        with open(DISPATCHER_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(dstate, f, ensure_ascii=False, indent=1)
    return is_stale


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="判定だけ表示し、起動しない")
    args = ap.parse_args(argv)
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.astimezone(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")

    exit_code = 0
    for workflow, min_interval in WORKFLOWS:
        try:
            go, reason = decide(latest_run(workflow), min_interval, now)
            if go and not args.dry_run:
                code, _out, err = gh("workflow", "run", workflow, "-R", REPO, "--ref", REF)
                if code != 0:
                    raise RuntimeError(f"gh workflow run 失敗: {err.strip()[:200]}")
            action = ("起動" if go else "見送り") + ("(dry-run)" if args.dry_run and go else "")
            print(f"[{stamp}] {workflow}: {action} - {reason}")
        except Exception as err:  # noqa: BLE001
            # gh の認証切れ・ネットワーク断など。他のワークフローの判定は続ける
            print(f"[{stamp}] [ERROR] {workflow}: {err}")
            exit_code = 1
    try:
        check_anzn_liveness(now, dry_run=args.dry_run)
    except Exception as err:  # noqa: BLE001
        print(f"[{stamp}] [ERROR] anzn-local 生存確認に失敗: {err}")
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
