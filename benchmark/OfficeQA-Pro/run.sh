#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env OfficeQA-Pro
export PATH="$RUNTIME/OfficeQA-Pro/venv/bin:$PATH"
exec "$RUNTIME/OfficeQA-Pro/venv/bin/python" "$BENCH/run.py" "$@"
