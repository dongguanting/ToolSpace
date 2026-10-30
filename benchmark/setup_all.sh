#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$ROOT/shared/env.sh"
for bench in AutomationBench SkillsBench JobBench Tau3-Banking BFCL-V4 Harvey-LAB OfficeQA-Pro; do
    bash "$ROOT/$bench/setup.sh" "$@"
done
