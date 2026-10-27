#!/usr/bin/env python3
"""Run the generated banking task with OpenAI-compatible agent and user models."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TAU2_ROOT = ROOT / "source"
DEFAULT_TASK = ROOT / "data" / "scenario" / "task_cross_001.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "scenario" / "test_runs" / "glm52_gpt41"
SRT_BIN = Path(os.environ.get("SRT_BIN_DIR", "/usr/local/bin"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--api-base",
        default=os.environ.get(
            "BOYUE_BASE_URL", "https://api.boyuerichdata.opensphereai.com/v1"
        ),
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
    parser.add_argument("--user-model", default="gpt-5.2")
    parser.add_argument(
        "--user-reasoning-effort",
        choices=("minimal", "low", "medium", "high"),
        default="low",
        help="Reasoning effort for the simulated-user model.",
    )
    parser.add_argument("--judge-model", default="gpt-4.1")
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
        "--agent-api-key-env",
        help="Optional environment variable containing the agent API key.",
    )
    parser.add_argument(
        "--user-judge-api-key-env",
        help="Optional environment variable containing the user/judge API key.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument(
        "--simulation-timeout",
        type=float,
        default=1800.0,
        help="Maximum simulation wall time in seconds, excluding review.",
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
    if model.startswith("bailian/"):
        return f"openai/{model}"
    return model if model.startswith("openai/") else f"openai/{model}"


def agent_provider_args(model: str) -> dict:
    if model in {"bailian/deepseek-v4.1-flash", "deepseek-v4-flash"}:
        return {"allowed_openai_params": ["reasoning_effort"]}
    return {}


def effective_user_temperature(
    model: str, reasoning_effort: str | None, temperature: float
) -> float | None:
    normalized_model = model.removeprefix("openai/")
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
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ["PATH"] = f"{SRT_BIN}:{os.environ.get('PATH', '')}"
    sys.path.insert(0, str(TAU2_ROOT / "src"))

    from dotenv import load_dotenv

    load_dotenv(os.environ.get("ENV_FILE", ROOT.parent / ".env"))
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
        "api_base": args.api_base,
        "api_key": user_judge_api_key,
        "timeout": 180,
    }
    if user_temperature is not None:
        llm_args_user["temperature"] = user_temperature

    config = TextRunConfig(
        domain="banking_knowledge",
        agent="llm_agent",
        user="user_simulator",
        llm_agent=litellm_model(args.agent_model),
        llm_args_agent={
            "max_tokens": 32768,
            **(
                {"extra_body": json.loads(os.environ["MODEL_EXTRA_BODY"])}
                if os.getenv("MODEL_EXTRA_BODY")
                else {}
            ),
            "temperature": (
                args.agent_temperature
                if args.agent_temperature is not None
                else args.temperature
            ),
            "api_base": args.agent_api_base or args.api_base,
            "api_key": agent_api_key,
            "timeout": 180,
            **agent_provider_args(args.agent_model),
            **(
                {"reasoning_effort": args.agent_reasoning_effort}
                if args.agent_reasoning_effort is not None
                else {}
            ),
        },
        llm_user=litellm_model(args.user_model),
        llm_args_user=llm_args_user,
        max_steps=args.max_steps,
        max_errors=10,
        timeout=args.simulation_timeout,
        seed=args.seed,
        log_level="INFO",
        verbose_logs=True,
        auto_review=True,
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
        auto_review=True,
        review_mode="full",
        review_model=litellm_model(args.judge_model),
    )
    simulation_path = args.output_dir / "simulation.json"
    simulation_path.write_text(simulation.model_dump_json(indent=2) + "\n")
    summary = {
        "task_id": simulation.task_id,
        "simulation_id": simulation.id,
        "agent_model": args.agent_model,
        "agent_api_base": args.agent_api_base or args.api_base,
        "user_model": args.user_model,
        "user_reasoning_effort": args.user_reasoning_effort,
        "user_temperature": user_temperature,
        "judge_model": args.judge_model,
        "judge_reasoning_effort": args.judge_reasoning_effort,
        "retrieval_config": args.retrieval_config,
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
