#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
bash "$EVAL_ROOT/shared/setup.sh" AutomationBench 3.13
"$RUNTIME/AutomationBench/venv/bin/python" "$BENCH/prepare.py"
