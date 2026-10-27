#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
bash "$EVAL_ROOT/shared/setup.sh" OfficeQA 3.12
"$RUNTIME/OfficeQA/venv/bin/python" "$BENCH/prepare.py"
