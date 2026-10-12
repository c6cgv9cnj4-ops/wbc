# -*- coding: utf-8 -*-
"""日本語氏名の対応表(data/badminton_player_names_ja.json)を日本バドミントン協会の公式ページから更新する。
使い方: python scripts/update_badminton_player_names.py [--snapshot 20261005202856]
  公式サイトに接続できないとき(メンテナンス等)は --snapshot にWaybackの保存日時を指定すると、保存コピー経由で取得する
  (出典URLには公式の元URLを残し、snapshot_url に保存コピーのURLを記録する)。既存の登録は消さず、追加・確認日の更新だけ行う。"""
import argparse
import datetime
import sys
import time

import requests

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import badminton_player_names as bpn  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="")
    args = ap.parse_args()
    prefix = f"https://web.archive.org/web/{args.snapshot}/" if args.snapshot else ""

    def fetch(url, tries=4):
        for i in range(tries):
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
                r.raise_for_status()
                return r.text
            except Exception:  # noqa: BLE001  保存コピーは連続アクセスで接続を拒否されることがあるため待って再試行する
                if i == tries - 1:
                    raise
                time.sleep(8 * (i + 1))

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    data, stats = bpn.build_from_site(fetch, now, snapshot_prefix=prefix, data=bpn.load_names(), sleep=lambda: time.sleep(2.5))
    bpn.save_names(data)
    print(f"[OK] 登録 {len(data['players'])}人 / 曖昧 {len(data['ambiguous'])}件 / {stats}")


if __name__ == "__main__":
    main()
