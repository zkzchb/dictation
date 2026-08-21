#!/usr/bin/env bash
# Standard V2 deployment entrypoint. Source is obtained from Git and Python
# dependencies are installed from the configured PyPI index.

set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$SCRIPT_DIR/install-v2-common.sh" online

