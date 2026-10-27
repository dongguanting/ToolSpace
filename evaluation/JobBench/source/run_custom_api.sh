#!/usr/bin/env bash
set -Eeuo pipefail

# Run JobBench through OpenCode using an OpenAI-compatible relay.
# All settings can be overridden with environment variables.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="${SCRIPT_DIR}/eval/run_benchmark_opencode.sh"
OPENCODE_DIR="${OPENCODE_DIR:-${SCRIPT_DIR}/opencode}"

OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.boyuerichdata.opensphereai.com/v1}"
CUSTOM_API_KEY="${CUSTOM_API_KEY:-${CODEX_API_KEY:-${OPENAI_API_KEY:-}}}"
OPENCODE_PROVIDER_ID="${OPENCODE_PROVIDER_ID:-custom_api}"
OPENCODE_PROVIDER_NAME="${OPENCODE_PROVIDER_NAME:-Custom API Relay}"
MODEL_NAME="${MODEL_NAME:-toolspace-model}"
MODEL_CONTEXT_TOKENS="${MODEL_CONTEXT_TOKENS:-1000000}"
MODEL_OUTPUT_TOKENS="${MODEL_OUTPUT_TOKENS:-131072}"
OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX="${OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX:-131072}"

SPLIT="${SPLIT:-main}"
TASKS_BASE_DIR="${TASKS_BASE_DIR:-${SCRIPT_DIR}/dataset/${SPLIT}}"
MAX_CONCURRENT_PER_MODEL="${MAX_CONCURRENT_PER_MODEL:-6}"
TIMEOUT_PER_TASK="${TIMEOUT_PER_TASK:-7200}"
RETRY_TIMEOUT_PER_TASK="${RETRY_TIMEOUT_PER_TASK:-7200}"
MAX_RETRIES="${MAX_RETRIES:-2}"
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-15}"
RUN_LABEL="${RUN_LABEL:-opencode-custom-api}"

die() {
    echo "错误：$*" >&2
    exit 1
}

[[ -x "$RUNNER" ]] || die "找不到 OpenCode runner：$RUNNER"
[[ -d "$OPENCODE_DIR/packages/opencode" ]] \
    || die "尚未安装 OpenCode，请先运行：${SCRIPT_DIR}/setup_opencode.sh"
[[ -d "$TASKS_BASE_DIR" ]] || die "找不到任务目录：$TASKS_BASE_DIR"
[[ "$OPENAI_BASE_URL" == http://* || "$OPENAI_BASE_URL" == https://* ]] \
    || die "OPENAI_BASE_URL 必须以 http:// 或 https:// 开头"
[[ "$OPENCODE_PROVIDER_ID" =~ ^[A-Za-z0-9_-]+$ ]] \
    || die "OPENCODE_PROVIDER_ID 只能包含字母、数字、下划线和连字符"
[[ -n "$MODEL_NAME" ]] || die "MODEL_NAME 不能为空"
[[ "$SPLIT" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] \
    || die "SPLIT 必须是数据集名称，仅包含字母、数字、下划线和连字符"
[[ "$MAX_CONCURRENT_PER_MODEL" =~ ^[1-9][0-9]*$ ]] \
    || die "MAX_CONCURRENT_PER_MODEL 必须是正整数"
[[ "$TIMEOUT_PER_TASK" =~ ^[1-9][0-9]*$ ]] \
    || die "TIMEOUT_PER_TASK 必须是正整数"
[[ "$RETRY_TIMEOUT_PER_TASK" =~ ^[1-9][0-9]*$ ]] \
    || die "RETRY_TIMEOUT_PER_TASK 必须是正整数"
[[ "$MAX_RETRIES" =~ ^[1-9][0-9]*$ ]] || die "MAX_RETRIES 必须是正整数"
[[ "$RETRY_DELAY_SECONDS" =~ ^[0-9]+$ ]] || die "RETRY_DELAY_SECONDS 必须是非负整数"
[[ "$MODEL_CONTEXT_TOKENS" =~ ^[1-9][0-9]*$ ]] \
    || die "MODEL_CONTEXT_TOKENS 必须是正整数"
[[ "$MODEL_OUTPUT_TOKENS" =~ ^[1-9][0-9]*$ ]] \
    || die "MODEL_OUTPUT_TOKENS 必须是正整数"

if [[ -z "$CUSTOM_API_KEY" ]]; then
    [[ -t 0 ]] || die "非交互式运行时请设置 CUSTOM_API_KEY（也兼容 CODEX_API_KEY/OPENAI_API_KEY）"
    read -r -s -p "请输入自定义 API key：" CUSTOM_API_KEY
    echo
fi
[[ -n "$CUSTOM_API_KEY" ]] || die "API key 不能为空"

# Accept either the API root or the complete chat-completions endpoint.
OPENAI_BASE_URL="${OPENAI_BASE_URL%/}"
OPENAI_BASE_URL="${OPENAI_BASE_URL%/chat/completions}"

# Merge this provider into any caller-supplied OpenCode configuration.
OPENCODE_CONFIG_CONTENT="$(
    BASE_CONFIG="${OPENCODE_CONFIG_CONTENT:-}" \
    PROVIDER_ID="$OPENCODE_PROVIDER_ID" \
    PROVIDER_NAME="$OPENCODE_PROVIDER_NAME" \
    PROVIDER_BASE_URL="$OPENAI_BASE_URL" \
    PROVIDER_API_KEY="$CUSTOM_API_KEY" \
    MODEL_ID="$MODEL_NAME" \
    MODEL_CONTEXT_TOKENS="$MODEL_CONTEXT_TOKENS" \
    MODEL_OUTPUT_TOKENS="$MODEL_OUTPUT_TOKENS" \
    python3 - <<'PY'
import json
import os

raw = os.environ.get("BASE_CONFIG", "").strip()
config = json.loads(raw) if raw else {}
provider_id = os.environ["PROVIDER_ID"]
model_id = os.environ["MODEL_ID"]

config.setdefault("provider", {})[provider_id] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": os.environ["PROVIDER_NAME"],
    "options": {
        "baseURL": os.environ["PROVIDER_BASE_URL"],
        "apiKey": os.environ["PROVIDER_API_KEY"],
    },
    "models": {
        model_id: {
            "name": model_id,
            "limit": {
                "context": int(os.environ["MODEL_CONTEXT_TOKENS"]),
                "output": int(os.environ["MODEL_OUTPUT_TOKENS"]),
            },
        }
    },
}
print(json.dumps(config, separators=(",", ":")))
PY
)"

BENCHMARK_MODELS="${BENCHMARK_MODELS:-${OPENCODE_PROVIDER_ID}/${MODEL_NAME}|${MODEL_NAME}_run3}"

export OPENCODE_CONFIG_CONTENT OPENCODE_DIR BENCHMARK_MODELS
export OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX
export SPLIT TASKS_BASE_DIR MAX_CONCURRENT_PER_MODEL
export TIMEOUT_PER_TASK RETRY_TIMEOUT_PER_TASK MAX_RETRIES RETRY_DELAY_SECONDS RUN_LABEL

# Match the runner: original <profession>/taskN and generated task_NNNNNN layouts.
task_count=$(find "$TASKS_BASE_DIR" -regextype posix-extended \
    -mindepth 2 -maxdepth 3 -type d \
    -regex '.*/task_?[0-9]+/task_folder' 2>/dev/null | wc -l)
(( task_count > 0 )) || die "任务目录中没有找到 task_folder：$TASKS_BASE_DIR"

echo "JobBench OpenCode 自定义 API 配置"
echo "API Base：$OPENAI_BASE_URL"
echo "Chat endpoint：${OPENAI_BASE_URL}/chat/completions"
echo "模型：$MODEL_NAME"
echo "模型上下文/输出上限：${MODEL_CONTEXT_TOKENS}/${MODEL_OUTPUT_TOKENS} tokens"
echo "OpenCode Provider：$OPENCODE_PROVIDER_ID ($OPENCODE_PROVIDER_NAME)"
echo "Split：$SPLIT"
echo "任务目录：$TASKS_BASE_DIR"
echo "任务数量：$task_count"
echo "单模型并发：$MAX_CONCURRENT_PER_MODEL"
echo "单次/重试超时：${TIMEOUT_PER_TASK}s/${RETRY_TIMEOUT_PER_TASK}s"
echo "最大尝试次数：$MAX_RETRIES"
echo "失败重试等待：${RETRY_DELAY_SECONDS}s"
echo "运行标签：$RUN_LABEL"
echo "提示：中转站必须支持 OpenAI Chat Completions 的 tools/tool_calls。"

exec "$RUNNER"
