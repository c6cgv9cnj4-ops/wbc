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

REPO = "c6cgv9cnj4-ops/wbc"
REF = "main"
# (ワークフロー, 最小間隔[分])。cron の設定値に合わせる。
WORKFLOWS = [
    ("jma_alerts.yml", 10),
    ("news.yml", 30),
    ("nikkei_cnbc_digest.yml", 60),
]
BUSY_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}


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
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
