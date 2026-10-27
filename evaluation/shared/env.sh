#!/usr/bin/env bash
set -euo pipefail
EVAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$EVAL_ROOT/.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi
export RUNTIME="${RUNTIME:-$EVAL_ROOT/.runtime}"
export OUTPUT_ROOT="${OUTPUT_ROOT:-$EVAL_ROOT/outputs}"
export MODEL="${MODEL:-toolspace-model}"
export MODEL_BASE_URL="${MODEL_BASE_URL:-http://localhost:8000/v1}"
export MODEL_API_KEY="${MODEL_API_KEY:-EMPTY}"
export BOYUE_BASE_URL="${BOYUE_BASE_URL:-https://api.boyuerichdata.opensphereai.com/v1}"
export BOYUE_API_KEY="${BOYUE_API_KEY:-}"
export RUN_NAME="${RUN_NAME:-$(date -u +%Y%m%dT%H%M%SZ)}"
export SCOPE="${SCOPE:-full}"
[[ "$RUN_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo 'Invalid RUN_NAME' >&2; exit 2; }
[[ "$SCOPE" == full || "$SCOPE" == smoke ]] || { echo 'SCOPE must be full or smoke' >&2; exit 2; }
export PYTHONDONTWRITEBYTECODE=1
export UV_CACHE_DIR="${UV_CACHE_DIR:-$RUNTIME/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$RUNTIME/python}"
export NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-$RUNTIME/npm-cache}"
export BUN_INSTALL_CACHE_DIR="${BUN_INSTALL_CACHE_DIR:-$RUNTIME/bun-cache}"
export HF_HOME="${HF_HOME:-$RUNTIME/huggingface}"
export PATH="$RUNTIME/tools/node_modules/.bin:$PATH"
export NODE_PATH="$RUNTIME/tools/node_modules${NODE_PATH:+:$NODE_PATH}"
# Keep proxy settings supplied by the user and bypass localhost for local models.
export NO_PROXY="localhost,127.0.0.1,${NO_PROXY:-}"
export no_proxy="$NO_PROXY"
require_judge() { [[ -n "$BOYUE_API_KEY" ]] || { echo 'Set BOYUE_API_KEY in .env' >&2; exit 2; }; }
require_env() { [[ -x "$RUNTIME/$1/venv/bin/python" ]] || { echo "Run $1/setup.sh first" >&2; exit 2; }; }
