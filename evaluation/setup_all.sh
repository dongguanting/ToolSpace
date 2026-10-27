#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$ROOT/shared/env.sh"
for bench in AutomationBench SkillsBench JobBench Tau3 BFCL-v4 Harvey OfficeQA; do
    bash "$ROOT/$bench/setup.sh" "$@"
done
