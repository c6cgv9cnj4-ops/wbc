#!/usr/bin/env bash
# 状態ブランチ（既定: badminton-results-state）が無ければ、空の初期コミットだけを持つブランチを作る。
# - main・既存ブランチ・既存データには触れない。
# - 既に存在すれば何もしない（冪等）。
# - force push は使わない。別ジョブが同時に作成した場合は、存在を再確認して成功扱いにする。
set -euo pipefail

BRANCH="${STATE_BRANCH:-badminton-results-state}"
REMOTE="${STATE_REMOTE:-origin}"

exists() { git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; }

if exists; then
  echo "状態ブランチ ${BRANCH} は既に存在します（変更なし）"
  exit 0
fi

TREE=$(git mktree </dev/null)
COMMIT=$(git -c user.name="github-actions[bot]" \
             -c user.email="github-actions[bot]@users.noreply.github.com" \
             commit-tree "$TREE" -m "状態ブランチを初期化")

if git push "$REMOTE" "${COMMIT}:refs/heads/${BRANCH}"; then
  echo "状態ブランチ ${BRANCH} を作成しました（${COMMIT}）"
  exit 0
fi

if exists; then
  echo "別の実行が先に作成しました（変更なし）"
  exit 0
fi
echo "状態ブランチの作成に失敗しました" >&2
exit 1
