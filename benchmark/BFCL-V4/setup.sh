#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
uv venv --python 3.10 "$RUNTIME/BFCL-V4/venv"
env -u UV_INDEX -u UV_INDEX_URL -u UV_EXTRA_INDEX_URL uv --no-config pip install --python "$RUNTIME/BFCL-V4/venv/bin/python" --index-url https://download.pytorch.org/whl/cpu 'torch==2.8.0+cpu'
env -u UV_INDEX -u UV_INDEX_URL -u UV_EXTRA_INDEX_URL uv --no-config pip install --default-index https://pypi.org/simple --python "$RUNTIME/BFCL-V4/venv/bin/python" -r "$BENCH/requirements.txt"
"$RUNTIME/BFCL-V4/venv/bin/python" "$BENCH/prepare.py"
