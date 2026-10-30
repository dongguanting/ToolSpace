#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env BFCL-V4
export PATH="$RUNTIME/BFCL-V4/venv/bin:$PATH"
exec "$RUNTIME/BFCL-V4/venv/bin/python" "$BENCH/run.py" "$@"
