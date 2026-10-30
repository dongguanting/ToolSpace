#!/usr/bin/env bash
set -euo pipefail
BENCH_NAME=${1:?benchmark name required}
PYTHON_VERSION=${2:-3.12}
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
command -v uv >/dev/null || { echo 'Install uv: https://docs.astral.sh/uv/getting-started/installation/' >&2; exit 2; }
mkdir -p "$RUNTIME/$BENCH_NAME"
uv venv --python "$PYTHON_VERSION" "$RUNTIME/$BENCH_NAME/venv"
env -u UV_INDEX -u UV_INDEX_URL -u UV_EXTRA_INDEX_URL uv --no-config pip install --default-index https://pypi.org/simple --python "$RUNTIME/$BENCH_NAME/venv/bin/python" -r "$EVAL_ROOT/$BENCH_NAME/requirements.txt"
