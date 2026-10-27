#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env OfficeQA
export PATH="$RUNTIME/OfficeQA/venv/bin:$PATH"
exec "$RUNTIME/OfficeQA/venv/bin/python" "$BENCH/run.py" "$@"
