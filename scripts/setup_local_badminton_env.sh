#!/usr/bin/env bash
# Mac mini ローカル実行の環境を作る（専用venv・固定依存・plist展開・plutil検証）。
# - 既存の python3 や他プロジェクトの依存関係は変更しない（専用venvのみを作る）。
# - 既定では launchctl を実行しない（登録は scripts/install_local_badminton_launchd.sh --load を社長が承認後に実行）。
# - キーは扱わない（キーチェーンへの登録は社長が行う）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${HOME}/Library/Application Support/ricky-kogyo/badminton"
LOCAL_DIR="${BADMINTON_LOCAL_DIR:-${APP_DIR}}"
VENV_DIR="${BADMINTON_VENV_DIR:-${APP_DIR}/venv}"
BASE_PYTHON="${BASE_PYTHON:-$(command -v python3)}"
REQ="${HERE}/requirements-badminton-local.txt"

mkdir -p "${LOCAL_DIR}/logs"
if [ ! -x "${VENV_DIR}/bin/python" ]; then
  "${BASE_PYTHON}" -m venv "${VENV_DIR}"
  echo "専用venvを作成しました: ${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -m pip install --quiet --disable-pip-version-check -r "${REQ}"
echo "依存を固定版で導入しました（${REQ}）"

# 導入した版の確認（固定版と一致しなければ失敗）
"${VENV_DIR}/bin/python" - "${REQ}" <<'PY'
import sys
from importlib.metadata import version
pins = {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "==" in line:
        name, ver = line.split("==", 1)
        pins[name] = ver
bad = [(n, v, version(n)) for n, v in pins.items() if version(n) != v]
if bad:
    raise SystemExit(f"版が固定値と一致しません: {bad}")
print("固定版の確認: OK（" + ", ".join(f"{n}=={v}" for n, v in pins.items()) + "）")
PY

# 実行系の自己確認（ネットワーク・キーチェーン・Discord には触れない）
"${VENV_DIR}/bin/python" "${HERE}/scripts/local_badminton_run.py" --check

# plist の展開（venvのPythonに固定）と検証。登録は行わない
PYTHON_BIN="${VENV_DIR}/bin/python" BADMINTON_LOCAL_DIR="${LOCAL_DIR}" BADMINTON_VENV_DIR="${VENV_DIR}" \
  "${HERE}/scripts/install_local_badminton_launchd.sh" ${PLIST_TARGET:+--target "${PLIST_TARGET}"} ${1:+"$1"}
echo "セットアップ完了（登録はしていません）"
