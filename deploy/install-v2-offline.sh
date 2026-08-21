#!/usr/bin/env bash
# Offline V2 deployment entrypoint. The repository/release bundle contains all
# Python wheels required for Python 3.12 on x86_64 and ARM64.

set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

[[ -f "$REPO_ROOT/shared/tools/verify_wheelhouse.py" ]] || {
  echo "[X] 缺少 wheelhouse 校验工具；请使用完整的 main-offline/Release 包" >&2
  exit 1
}
[[ -f "$REPO_ROOT/v2/wheelhouse/sha256" ]] || {
  echo "[X] 缺少 v2/wheelhouse/sha256；请重新下载完整离线包" >&2
  exit 1
}

exec bash "$SCRIPT_DIR/install-v2-common.sh" offline

