#!/usr/bin/env python3
"""Run the generated banking task with OpenAI-compatible agent and user models."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAU2_ROOT = ROOT / "environments"
DEFAULT_TASK = ROOT / "data" / "tasks.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "single-task"
SRT_BIN = Path(os.environ.get("SRT_BIN_DIR", str(ROOT / ".runtime/shell/node_modules/.bin")))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--api-base", default=os.environ.get("USER_BASE_URL", "https://api.openai.com/v1")
    )
    parser.add_argument(
        "--agent-api-base",
        default=None,
        help=(
            "Optional API base used only by the agent model. The simulated user, "
            "judge, and dense-retrieval embedder continue to use --api-base."
        ),
    )
    parser.add_argument("--agent-model", default="glm-5.2")
    parser.add_argument("--user-api-base", default=None)
    parser.add_argument("--judge-api-base", default=None)
    parser.add_argument("--user-model", default="gpt-5.2")
    parser.add_argument(
        "--user-reasoning-effort",
        choices=("minimal", "low", "medium", "high"),
        default="low",
        help="Reasoning effort for the simulated-user model.",
    )
    parser.add_argument("--judge-model", default="gpt-4.1")
    parser.add_argument(
        "--review", action=argparse.BooleanOptionalAction, default=True,
        help="Run post-simulation conversation review and authentication classification (default: enabled). Use --no-review to keep native scoring only.",
    )
    parser.add_argument(
        "--judge-reasoning-effort",
        choices=("minimal", "low", "medium", "high"),
        default=None,
        help="Reasoning effort for review and authentication judge calls.",
    )
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument(
        "--agent-temperature",
        type=float,
        default=None,
        help="Override temperature for the agent model only.",
    )
    parser.add_argument(
        "--agent-reasoning-effort",
        choices=("minimal", "low", "medium", "high", "xhigh", "max"),
        default=None,
        help="Reasoning effort for the agent model.",
    )
    parser.add_argument(
        "--agent-max-tokens",
        type=int,
        default=None,
        help="Maximum response tokens for providers that require an explicit limit.",
    )
    parser.add_argument(
        "--agent-api-key-env",
        help="Optional environment variable containing the agent API key.",
    )
    parser.add_argument(
        "--user-judge-api-key-env",
        help="Optional environment variable containing the user/judge API key.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--simulation-timeout", type=float, default=1800.0,
                        help="Maximum simulation wall time in seconds, excluding review.")
    parser.add_argument(
        "--shell-timeout", type=int, default=30,
        help="Maximum execution time in seconds for each banking KB shell command.",
    )
    parser.add_argument(
        "--retrieval-config",
        default="alltools",
        help=(
            "Banking knowledge retrieval variant. Defaults to alltools, which "
            "withholds required documents and provides BM25 search, dense "
            "search, and a read-only knowledge-base shell."
        ),
    )
    parser.add_argument(
        "--enforce-communication-protocol",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    return parser.parse_args()


def litellm_model(model: str) -> str:
    if model == "gpt-6.1-sol":
        return "openai/responses/gpt-6.1-sol"
    if model == "claude-opus-5-5":
        return f"anthropic/{model}"
    if model.startswith("anthropic/"):
        return model
    if model.startswith("bailian/"):
        return f"openai/{model}"
    return model if "/" in model else f"openai/{model}"


def effective_agent_api_base(model: str, api_base: str) -> str:
    if model == "claude-opus-5-5" or model.startswith("anthropic/"):
        return api_base.rstrip("/").removesuffix("/v1")
    return api_base


def agent_provider_args(model: str) -> dict:
    if model == "claude-opus-5-5" or model.startswith("anthropic/"):
        return {"allowed_openai_params": ["output_config"]}
    if model == "glm53-flash-lmdeploy-mooncake-512k-80gpu-mtp3-coding-agent-1020":
        return {"allowed_openai_params": ["reasoning_effort"]}
    if model in {"glm-5.2", "glm-5.3", "glm-5.3-flash", "gpt-6.1-sol", "bailian/deepseek-v4.1-flash", "deepseek-v4-flash", "dsv41-flash-1017-code_task", "deepseek-v4.1-flash-1017", "Qwen3.6-35B-A3B-base", "Qwen3.6-35B-A3B-rl79"}:
        return {"allowed_openai_params": ["reasoning_effort"]}
    return {}


def effective_user_temperature(
    model: str, reasoning_effort: str | None, temperature: float
) -> float | None:
    normalized_model = model.removeprefix("openai/")
    if normalized_model == "glm5.3-flash-hf1137-fp8-256k-32gpu-mtp3-mooncake-agent-1012" and reasoning_effort == "low":
        return None
    if normalized_model.startswith("gpt-5.2") and reasoning_effort not in {
        None,
        "none",
    }:
        return None
    return temperature


def review_summary(review: object | None) -> dict | None:
    if review is None:
        return None
    return review.model_dump(mode="json")


def main() -> None:
    args = parse_args()
    if args.shell_timeout < 1:
        raise SystemExit("--shell-timeout must be positive")
    if args.agent_max_tokens is not None and args.agent_max_tokens < 1:
        raise SystemExit("--agent-max-tokens must be positive")
    os.environ["TAU2_KB_SHELL_TIMEOUT_SECONDS"] = str(args.shell_timeout)
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ["PATH"] = f"{SRT_BIN}:{os.environ.get('PATH', '')}"
    sys.path.insert(0, str(TAU2_ROOT))

    from dotenv import load_dotenv

    load_dotenv(os.environ.get("ENV_FILE", ROOT / ".env"))
    os.environ["TAU2_DATA_DIR"] = str(ROOT / "data")
    agent_api_key = (
        os.getenv(args.agent_api_key_env)
        if args.agent_api_key_env
        else (os.getenv("NEW_API_KEY") or os.getenv("boyueKey"))
    )
    if not agent_api_key:
        source = args.agent_api_key_env or "NEW_API_KEY/boyueKey"
        raise RuntimeError(f"Agent API key is missing from {source}")
    # Route embedding and GPT calls through embeddingKey while retaining the
    # process-wide OpenAI credential used by the agent.
    embedding_api_key = os.getenv("embeddingKey") or agent_api_key
    qwen_embedding_api_key = os.getenv("qwenEmbKey") or embedding_api_key
    user_judge_api_key = (
        os.getenv(args.user_judge_api_key_env)
        if args.user_judge_api_key_env
        else (os.getenv("embeddingKey") or os.getenv("gptKey") or agent_api_key)
    )
    if not user_judge_api_key:
        source = args.user_judge_api_key_env or "embeddingKey/gptKey/agent key"
        raise RuntimeError(f"User/judge API key is missing from {source}")
    os.environ["OPENAI_API_KEY"] = agent_api_key
    os.environ["TAU2_EMBEDDING_API_KEY"] = embedding_api_key
    os.environ["OPENROUTER_API_KEY"] = qwen_embedding_api_key
    os.environ["TAU2_JUDGE_API_KEY"] = user_judge_api_key
    os.environ["TAU2_JUDGE_API_BASE"] = args.judge_api_base or args.api_base
    os.environ["OPENAI_API_BASE"] = args.api_base
    # The OpenAI Python client used by the dense knowledge embedder reads
    # OPENAI_BASE_URL, while LiteLLM accepts OPENAI_API_BASE/api_base.
    os.environ["OPENAI_BASE_URL"] = args.api_base
    # Keep dense retrieval on the configured local/precomputed cache and allow
    # Qwen queries to use the same OpenAI-compatible endpoint explicitly.
    os.environ.setdefault("TAU2_EMBEDDINGS_CACHE_DIR", str(ROOT / ".embeddings_cache"))
    os.environ.setdefault("TAU2_QWEN_EMBEDDING_BASE_URL", args.api_base)
    if args.judge_reasoning_effort is not None:
        os.environ["TAU2_JUDGE_REASONING_EFFORT"] = args.judge_reasoning_effort
    else:
        os.environ.pop("TAU2_JUDGE_REASONING_EFFORT", None)

    from tau2.data_model.simulation import TextRunConfig
    from tau2.data_model.tasks import Task
    from tau2.evaluator.evaluator import EvaluationType
    from tau2.runner.batch import run_single_task

    task = Task.model_validate_json(args.task.read_text())
    user_temperature = effective_user_temperature(
        args.user_model, args.user_reasoning_effort, args.temperature
    )
    llm_args_user = {
        "reasoning_effort": args.user_reasoning_effort,
        "api_base": args.user_api_base or args.api_base,
        **agent_provider_args(args.user_model),
        "api_key": user_judge_api_key,
        "timeout": 180,
    }
    if user_temperature is not None:
        llm_args_user["temperature"] = user_temperature

    llm_args_agent = {
        "temperature": (
            args.agent_temperature
            if args.agent_temperature is not None
            else args.temperature
        ),
        "api_base": effective_agent_api_base(
            args.agent_model, args.agent_api_base or args.api_base
        ),
        "api_key": agent_api_key,
        "timeout": 180,
        **agent_provider_args(args.agent_model),
    }
    if args.agent_reasoning_effort is not None:
        if args.agent_model == "claude-opus-5-5" or args.agent_model.startswith("anthropic/"):
            llm_args_agent["output_config"] = {
                "effort": args.agent_reasoning_effort
            }
        else:
            llm_args_agent["reasoning_effort"] = args.agent_reasoning_effort
    if args.agent_max_tokens is not None:
        llm_args_agent["max_tokens"] = args.agent_max_tokens
    if os.getenv("MODEL_EXTRA_BODY"):
        llm_args_agent["extra_body"] = json.loads(os.environ["MODEL_EXTRA_BODY"])

    config = TextRunConfig(
        domain="banking_knowledge",
        agent="llm_agent",
        user="user_simulator",
        llm_agent=litellm_model(args.agent_model),
        llm_args_agent=llm_args_agent,
        llm_user=litellm_model(args.user_model),
        llm_args_user=llm_args_user,
        max_steps=args.max_steps,
        max_errors=10,
        timeout=args.simulation_timeout,
        seed=args.seed,
        log_level="INFO",
        verbose_logs=True,
        auto_review=args.review,
        review_mode="full",
        review_model=litellm_model(args.judge_model),
        hallucination_retries=0,
        retrieval_config=args.retrieval_config,
        enforce_communication_protocol=args.enforce_communication_protocol,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    simulation = run_single_task(
        config,
        task,
        seed=args.seed,
        evaluation_type=EvaluationType.ALL,
        save_dir=args.output_dir,
        verbose_logs=True,
        auto_review=args.review,
        review_mode="full",
        review_model=litellm_model(args.judge_model),
    )
    simulation_path = args.output_dir / "simulation.json"
    simulation_data = simulation.model_dump(mode="json")
    # Run configs retain routing metadata, but serialized artifacts must not
    # retain API credentials passed through the Tau2/LiteLLM configuration.
    info = simulation_data.get("info") or {}
    for info_key in ("agent_info", "user_info"):
        provider_info = info.get(info_key) or {}
        llm_args = provider_info.get("llm_args") or {}
        llm_args.pop("api_key", None)
    simulation_path.write_text(
        json.dumps(simulation_data, ensure_ascii=False, indent=2) + "\n"
    )
    returned_model_ids = []
    for message in simulation.get_messages():
        if getattr(message, "role", None) != "assistant":
            continue
        raw_data = getattr(message, "raw_data", None)
        returned_model = raw_data.get("model") if isinstance(raw_data, dict) else None
        if isinstance(returned_model, str) and returned_model not in returned_model_ids:
            returned_model_ids.append(returned_model)
    summary = {
        "task_id": simulation.task_id,
        "simulation_id": simulation.id,
        "agent_model": args.agent_model,
        "requested_agent_model": args.agent_model,
        "returned_agent_model_ids": returned_model_ids,
        "agent_max_tokens": args.agent_max_tokens,
        "agent_reasoning_effort": args.agent_reasoning_effort,
        "agent_output_config": (
            {"effort": args.agent_reasoning_effort}
            if args.agent_reasoning_effort is not None
            and (args.agent_model == "claude-opus-5-5" or args.agent_model.startswith("anthropic/"))
            else None
        ),
        "agent_api_base": effective_agent_api_base(
            args.agent_model, args.agent_api_base or args.api_base
        ),
        "user_api_base": args.user_api_base or args.api_base,
        "judge_api_base": args.judge_api_base or args.api_base,
        "user_model": args.user_model,
        "user_reasoning_effort": args.user_reasoning_effort,
        "user_temperature": user_temperature,
        "judge_model": args.judge_model,
        "judge_reasoning_effort": args.judge_reasoning_effort,
        "review_enabled": args.review,
        "review_status": "completed" if args.review else "skipped",
        "retrieval_config": args.retrieval_config,
        "shell_timeout_seconds": args.shell_timeout,
        "termination_reason": simulation.termination_reason.value,
        "duration_seconds": simulation.duration,
        "message_count": len(simulation.get_messages()),
        "reward": simulation.reward_info.reward if simulation.reward_info else None,
        "reward_info": (
            simulation.reward_info.model_dump(mode="json")
            if simulation.reward_info
            else None
        ),
        "review": review_summary(simulation.review),
        "user_only_review": review_summary(simulation.user_only_review),
        "simulation_file": str(simulation_path),
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
