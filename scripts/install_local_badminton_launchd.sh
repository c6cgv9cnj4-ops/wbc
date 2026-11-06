#!/usr/bin/env bash
# 朝06:05 のローカル実行（launchd）を、テンプレートから plist に展開する。
# 既定では plist を書き出すだけで、launchctl は実行しない（登録は --load を付けた時だけ）。
# 使い方:
#   scripts/install_local_badminton_launchd.sh                 … ~/Library/LaunchAgents に展開のみ
#   scripts/install_local_badminton_launchd.sh --load          … 展開して launchctl で登録（社長の承認後のみ）
#   scripts/install_local_badminton_launchd.sh --target PATH   … 指定パスに展開のみ（検証用）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${HERE}/launchd/com.rickykogyo.badminton-results.plist.template"
LABEL="com.rickykogyo.badminton-results"
LOCAL_DIR="${BADMINTON_LOCAL_DIR:-$HOME/Library/Application Support/ricky-kogyo/badminton}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
TARGET="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOAD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --load) LOAD=1 ;;
    --target) shift; TARGET="$1" ;;
    *) echo "不明な引数: $1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "${LOCAL_DIR}/logs" "$(dirname "${TARGET}")"
sed -e "s|__PYTHON__|${PYTHON_BIN}|g" \
    -e "s|__RUNNER__|${HERE}/scripts/local_badminton_run.py|g" \
    -e "s|__WORKDIR__|${HERE}|g" \
    -e "s|__LOCALDIR__|${LOCAL_DIR}|g" \
    -e "s|__LOGDIR__|${LOCAL_DIR}/logs|g" \
    "${TEMPLATE}" > "${TARGET}"
echo "展開しました: ${TARGET}"

if command -v plutil >/dev/null 2>&1; then
  plutil -lint "${TARGET}"
fi

if [ "${LOAD}" -eq 1 ]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "${TARGET}"
  echo "登録しました（次回 06:05 に実行）: ${LABEL}"
else
  echo "まだ登録していません。登録は次のコマンドで行う（社長の承認後）:"
  echo "  ${HERE}/scripts/install_local_badminton_launchd.sh --load"
fi
