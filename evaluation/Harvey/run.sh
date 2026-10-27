#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env Harvey
export PATH="$RUNTIME/Harvey/venv/bin:$PATH"
exec "$RUNTIME/Harvey/venv/bin/python" "$BENCH/run.py" "$@"
