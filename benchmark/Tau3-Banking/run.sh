#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env Tau3-Banking
export PATH="$RUNTIME/Tau3-Banking/venv/bin:$PATH"
exec "$RUNTIME/Tau3-Banking/venv/bin/python" "$BENCH/run.py" "$@"
