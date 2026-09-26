# -*- coding: utf-8 -*-
"""
公開リポジトリに日記・個人メモ由来のファイルが入っていないかを検査する(CI用・再発防止)。

対象:
  1. Git 管理下のファイルに、日記/メモの置き場所(logs/・reports/・mindmap/・out/)が無いこと
  2. ワークフローが上記の場所を git add / upload-artifact していないこと

違反があれば一覧を出して exit 1。ファイルの中身は読まない・表示しない(公開ログ対策)。
使い方: python scripts/check_no_personal_data.py
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

FORBIDDEN_PREFIXES = ("logs/", "reports/", "mindmap/", "out/")
# ワークフロー内で、上記の場所をコミット/成果物化する行
_WF_BAD = re.compile(r"(git\s+add[^\n]*\b(logs|reports|mindmap|out)/?)|"
                     r"(path:\s*[|>]?\s*\n?\s*(logs|reports|mindmap|out)/)")


def tracked_violations(files: list[str]) -> list[str]:
    return sorted(f for f in files if f.startswith(FORBIDDEN_PREFIXES))


def workflow_violations(root: str) -> list[str]:
    out = []
    for p in sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.y*ml"))):
        text = open(p, encoding="utf-8").read()
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        if _WF_BAD.search(code):
            out.append(os.path.relpath(p, root))
    return out


def main() -> int:
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                          text=True, check=True).stdout.strip()
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    bad_files = tracked_violations(files)
    bad_wf = workflow_violations(root)
    if bad_files:
        print(f"[NG] 日記・個人メモの置き場所が Git 管理下にあります({len(bad_files)}件):")
        for f in bad_files[:50]:
            print(f"  - {f}")
    if bad_wf:
        print("[NG] 日記・個人メモの置き場所をコミット/成果物化しているワークフロー:")
        for f in bad_wf:
            print(f"  - {f}")
    if bad_files or bad_wf:
        return 1
    print(f"[OK] 日記・個人メモ由来のファイルは Git 管理下にありません(検査 {len(files)} ファイル)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
