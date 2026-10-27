#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env AutomationBench
export PATH="$RUNTIME/AutomationBench/venv/bin:$PATH"
exec "$RUNTIME/AutomationBench/venv/bin/python" "$BENCH/run.py" "$@"
