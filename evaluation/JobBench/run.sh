#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env JobBench
export PATH="$RUNTIME/JobBench/venv/bin:$PATH"
exec "$RUNTIME/JobBench/venv/bin/python" "$BENCH/run.py" "$@"
