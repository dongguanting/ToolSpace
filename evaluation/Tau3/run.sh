#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env Tau3
export PATH="$RUNTIME/Tau3/venv/bin:$PATH"
exec "$RUNTIME/Tau3/venv/bin/python" "$BENCH/run.py" "$@"
