#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
bash "$EVAL_ROOT/shared/setup.sh" Tau3-Banking 3.12
"$RUNTIME/Tau3-Banking/venv/bin/python" "$BENCH/prepare.py"
