#!/usr/bin/env bash
set -Eeuo pipefail

# Judge JobBench outputs through an OpenAI-compatible relay.
# The relay must implement POST /v1/chat/completions (and multimodal input when
# a rubric requires judging images).

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="${SCRIPT_DIR}/eval/run_judge.sh"

JUDGE_API_BASE="${JUDGE_API_BASE:-${OPENAI_BASE_URL:-https://api.boyuerichdata.opensphereai.com/v1}}"
JUDGE_API_KEY="${JUDGE_API_KEY:-${OPENAI_API_KEY:-}}"
JUDGE_MODEL="${JUDGE_MODEL:-grok-4.3}"
SPLIT="${SPLIT:-main}"
TARGET_DIR="${TARGET_DIR:-${SCRIPT_DIR}/dataset/${SPLIT}}"
EVAL_MODEL="${EVAL_MODEL:-toolspace-eval}"
MAX_JUDGE_WORKERS="${MAX_JUDGE_WORKERS:-10}"
MAX_RETRIES="${MAX_RETRIES:-3}"
TIMEOUT_PER_RUBRIC="${TIMEOUT_PER_RUBRIC:-300}"
JUDGE_MAX_OUTPUT_TOKENS="${JUDGE_MAX_OUTPUT_TOKENS:-8192}"
JUDGE_TOKEN_PARAM="${JUDGE_TOKEN_PARAM:-max_completion_tokens}"

die() {
    echo "错误：$*" >&2
    exit 1
}

[[ -x "$RUNNER" ]] || die "找不到 judge runner：$RUNNER"
[[ -d "$TARGET_DIR" ]] || die "找不到目标目录：$TARGET_DIR"
[[ "$JUDGE_API_BASE" == http://* || "$JUDGE_API_BASE" == https://* ]] \
    || die "JUDGE_API_BASE 必须以 http:// 或 https:// 开头"
[[ -n "$JUDGE_MODEL" ]] || die "JUDGE_MODEL 不能为空"

if [[ -z "$JUDGE_API_KEY" ]]; then
    [[ -t 0 ]] || die "非交互式运行时请设置 JUDGE_API_KEY（或 OPENAI_API_KEY）"
    read -r -s -p "请输入 judge API key：" JUDGE_API_KEY
    echo
fi

export JUDGE_API_BASE JUDGE_API_KEY JUDGE_MODEL SPLIT TARGET_DIR EVAL_MODEL
export MAX_JUDGE_WORKERS MAX_RETRIES TIMEOUT_PER_RUBRIC
export JUDGE_MAX_OUTPUT_TOKENS JUDGE_TOKEN_PARAM

echo "JobBench 自定义 Judge 配置"
echo "API Base：$JUDGE_API_BASE"
echo "Judge 模型：$JUDGE_MODEL"
echo "目标目录：$TARGET_DIR"
echo "被评模型过滤：${EVAL_MODEL:-全部}"
echo "并发：$MAX_JUDGE_WORKERS"
echo "注意：中转站需支持 POST /v1/chat/completions"

exec "$RUNNER"
