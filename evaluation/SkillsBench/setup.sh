#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
bash "$EVAL_ROOT/shared/setup.sh" SkillsBench 3.12
"$RUNTIME/SkillsBench/venv/bin/python" "$BENCH/prepare.py"
