#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env Harvey-LAB
export PATH="$RUNTIME/Harvey-LAB/venv/bin:$PATH"
exec "$RUNTIME/Harvey-LAB/venv/bin/python" "$BENCH/run.py" "$@"
