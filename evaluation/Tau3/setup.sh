#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
bash "$EVAL_ROOT/shared/setup.sh" Tau3 3.12
"$RUNTIME/Tau3/venv/bin/python" "$BENCH/prepare.py"
