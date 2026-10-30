#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
require_env SkillsBench
export PATH="$RUNTIME/SkillsBench/venv/bin:$PATH"
exec "$RUNTIME/SkillsBench/venv/bin/python" "$BENCH/run.py" "$@"
