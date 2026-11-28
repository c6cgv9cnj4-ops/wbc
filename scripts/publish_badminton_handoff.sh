#!/usr/bin/env bash
# 試合結果JSON（ローカルジョブの出力）を、データ専用ブランチ badminton-results-data へ公開する。
#
# 設計上の安全策:
# - 公開先は専用ブランチのみ。main・他のブランチには一切 push しない。
# - force push しない（通常の fast-forward push のみ）。履歴を書き換えられない。
# - 認証は「ディプロイキー（秘密鍵ファイルのパス）」を環境変数で指定する。鍵の中身は表示・記録しない。
#   鍵は書き込み権限を持つため、ファイル権限 600・リポジトリ外に置くこと。
# - 公開前に JSON の形式（schema_version）を検証し、破損ファイルは公開しない。
# - 鍵が未設定、または入力が無い場合は何もせず正常終了する（既存配信は影響を受けない）。
#
# 使い方:
#   BADMINTON_DATA_DEPLOY_KEY=/path/to/key scripts/publish_badminton_handoff.sh <outbox の JSON>
# 環境変数:
#   BADMINTON_DATA_DEPLOY_KEY  ディプロイキーの秘密鍵ファイル（未設定なら公開しない）
#   BADMINTON_DATA_REMOTE      公開先のリモート（既定: 本リポジトリの SSH URL）
set -euo pipefail

SRC="${1:-}"
BRANCH="badminton-results-data"
FILE="badminton_results_handoff.json"
REMOTE="${BADMINTON_DATA_REMOTE:-git@github.com:c6cgv9cnj4-ops/wbc.git}"

if [ -z "${BADMINTON_DATA_DEPLOY_KEY:-}" ]; then
  echo "[SKIP] BADMINTON_DATA_DEPLOY_KEY が未設定のため公開しません（既存配信には影響しません）"
  exit 0
fi
if [ -z "${SRC}" ] || [ ! -s "${SRC}" ]; then
  echo "[SKIP] 公開する試合結果JSONがありません"
  exit 0
fi
if ! python3 -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); sys.exit(0 if d.get("schema_version")==1 else 1)' "${SRC}"; then
  echo "[ERROR] 試合結果JSONの形式が不正なため公開しません"
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
export GIT_SSH_COMMAND="ssh -i ${BADMINTON_DATA_DEPLOY_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"

# 1) リモートへ接続できるかを確認。失敗（認証・接続）は「ブランチ未作成」と区別して中止する
if ! HEADS="$(git ls-remote --heads "${REMOTE}" "${BRANCH}" 2>"${TMP}/ls-remote.err")"; then
  echo "[ERROR] リモートへ接続できません（認証または接続の失敗）。ブランチの有無は確認できないため公開しません"
  # 鍵の場所は表示しない（実際の値を置換する）
  python3 -c 'import sys; k=sys.argv[1]; t=open(sys.argv[2],encoding="utf-8",errors="replace").read(); print(t.replace(k, "<DEPLOY_KEY>")[:500])' "${BADMINTON_DATA_DEPLOY_KEY}" "${TMP}/ls-remote.err"
  exit 2
fi
if [ -n "${HEADS}" ]; then
  # 2) 既存ブランチを取得して、その上に fast-forward で積む
  git clone --quiet --depth 1 --branch "${BRANCH}" "${REMOTE}" "${TMP}/repo"
else
  # 3) 本当に無い場合だけ、孤立ブランチとして作る（main の履歴とは無関係）
  echo "[INFO] 専用ブランチ ${BRANCH} は未作成のため新規作成します"
  git init -q "${TMP}/repo"
  git -C "${TMP}/repo" remote add origin "${REMOTE}"
  git -C "${TMP}/repo" checkout -q --orphan "${BRANCH}"
fi

cp "${SRC}" "${TMP}/repo/${FILE}"
git -C "${TMP}/repo" add "${FILE}"
if git -C "${TMP}/repo" diff --cached --quiet; then
  echo "[INFO] 前回から内容に変化がないため、公開は不要です"
  exit 0
fi
git -C "${TMP}/repo" -c user.name="badminton-local" -c user.email="badminton-local@users.noreply.github.com" \
  commit -q -m "試合結果ハンドオフを更新"
git -C "${TMP}/repo" push -q origin "HEAD:refs/heads/${BRANCH}"
echo "[OK] 試合結果JSONを ${BRANCH} に公開しました（force push なし）"
