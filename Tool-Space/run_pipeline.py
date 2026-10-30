"""Public command line for graph construction, generation, and verification."""

import argparse
import json
import os
from pathlib import Path

from automation_environment.io import read_json, read_jsonl, write_json
from examples.demo import DemoClient, create_bundle
from task_generation.llm import LLMClient
from task_generation.pipeline import synthesize_tasks
from tool_graph.graph import build_graph
from tool_graph.walks import generate_walks
from verification.replay import replay_exported

DEMO_WALK = {
    "min_steps": 5,
    "max_steps": 5,
    "min_distinct_endpoints": 3,
    "min_distinct_services": 2,
    "min_mutations": 2,
    "min_distinct_action_endpoints": 2,
    "min_queries_before_action": 3,
    "query_only": False,
    "mutation_budget": 2,
    "max_endpoint_repeats": 3,
    "max_queries_before_action": 3,
    "start_task_allowlist": ["finance.project_reconciliation"],
    "template_scope": "task",
    "search_expansions": 2000,
}


def verify_tasks(task_root: Path) -> dict:
    tasks = read_jsonl(task_root / "automation_pro.jsonl")
    if not tasks:
        raise ValueError("No exported tasks to verify")
    for pro in tasks:
        replay_exported(pro)
    result = {"passed": True, "verified_tasks": len(tasks)}
    write_json(task_root / "independent_validation.json", result)
    return result


def add_model_arguments(parser):
    parser.add_argument("--model", default=os.environ.get("PRO_MODEL"))
    parser.add_argument(
        "--base-url", default=os.environ.get("PRO_BASE_URL", "https://api.openai.com/v1")
    )
    parser.add_argument(
        "--api", choices=["chat", "responses"], default=os.environ.get("PRO_API", "chat")
    )
    parser.add_argument(
        "--reasoning-effort", default=os.environ.get("PRO_REASONING_EFFORT") or None
    )
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--generation-attempts", type=int, default=3)


def main() -> int:
    parser = argparse.ArgumentParser(prog="automation-pro", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run all stages without model calls")
    demo.add_argument("--output", type=Path, required=True)
    graph = commands.add_parser("graph", help="Build graph and snapshots from a workflow bundle")
    graph.add_argument("--input", type=Path, required=True)
    graph.add_argument("--output", type=Path, required=True)
    walk = commands.add_parser("walk", help="Generate executable state-aware graph walks")
    walk.add_argument("--graph", type=Path, required=True)
    walk.add_argument("--config", type=Path, required=True)
    walk.add_argument("--output", type=Path, required=True)
    walk.add_argument("--count", type=int, default=1)
    walk.add_argument("--seed", type=int, default=42)
    walk.add_argument("--max-attempts", type=int, default=100)
    task = commands.add_parser(
        "synthesize", help="Generate grounded questions and native verifiers"
    )
    task.add_argument("--graph", type=Path, required=True)
    task.add_argument("--walks", type=Path, required=True)
    task.add_argument("--output", type=Path, required=True)
    add_model_arguments(task)
    task.add_argument("--resume", action="store_true")
    run = commands.add_parser("run", help="Run the complete pipeline with a real model")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--walk-config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--count", type=int, default=1)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--max-attempts", type=int, default=100)
    add_model_arguments(run)
    verify = commands.add_parser("verify", help="Independently replay exported Pro tasks")
    verify.add_argument("--tasks", type=Path, required=True)
    evaluate = commands.add_parser("score", help="Score a final WorldState using a Pro verifier")
    evaluate.add_argument("--task", type=Path, required=True, help="One pro_task.json file")
    evaluate.add_argument("--final-state", type=Path, required=True)
    args = parser.parse_args()
    if args.command in {"run", "synthesize"} and not args.model:
        parser.error("--model or PRO_MODEL is required")
    # Real model credentials have separate names; simulator tools remain local mocks.
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
    ):
        os.environ.pop(name, None)
    if args.command == "demo":
        root = args.output.resolve()
        root.mkdir(parents=True, exist_ok=False)
        create_bundle(root / "input.json")
        graph_summary = build_graph(root / "input.json", root / "graph")
        walks = generate_walks(root / "graph", root / "walks", 1, 42, DEMO_WALK)
        if walks["generated"] != 1:
            raise RuntimeError("Demo graph walk failed; inspect walks/summary.json")
        tasks = synthesize_tasks(
            root / "graph", root / "walks/walks.jsonl", root / "tasks", DemoClient()
        )
        if tasks["accepted"] != 1 or not tasks["complete"]:
            raise RuntimeError("Demo task synthesis failed; inspect tasks/errors.jsonl")
        result = {
            "passed": True,
            "mode": "offline_fixture",
            "graph": graph_summary,
            "walks": walks,
            "tasks": tasks,
            "verification": verify_tasks(root / "tasks"),
        }
        write_json(root / "validation.json", result)
    elif args.command == "graph":
        result = build_graph(args.input, args.output)
    elif args.command == "walk":
        result = generate_walks(
            args.graph,
            args.output,
            args.count,
            args.seed,
            read_json(args.config),
            args.max_attempts,
        )
        if result["generated"] < args.count:
            print(json.dumps(result))
            return 2
    elif args.command == "run":
        root = args.output.resolve()
        root.mkdir(parents=True, exist_ok=False)
        graph_summary = build_graph(args.input, root / "graph")
        walks = generate_walks(
            root / "graph",
            root / "walks",
            args.count,
            args.seed,
            read_json(args.walk_config),
            args.max_attempts,
        )
        if walks["generated"] < args.count:
            print(json.dumps({"graph": graph_summary, "walks": walks}))
            return 2
        client = LLMClient(
            args.base_url,
            os.environ.get("PRO_API_KEY", ""),
            args.model,
            args.api,
            args.reasoning_effort,
            timeout=args.timeout,
            audit_dir=root / "tasks/model_calls",
        )
        try:
            tasks = synthesize_tasks(
                root / "graph",
                root / "walks/walks.jsonl",
                root / "tasks",
                client,
                args.generation_attempts,
            )
        finally:
            client.close()
        if not tasks["complete"] or not tasks["accepted"]:
            print(json.dumps({"graph": graph_summary, "walks": walks, "tasks": tasks}))
            return 2
        result = {
            "passed": True,
            "graph": graph_summary,
            "walks": walks,
            "tasks": tasks,
            "verification": verify_tasks(root / "tasks"),
        }
        write_json(root / "validation.json", result)
    elif args.command == "synthesize":
        client = LLMClient(
            args.base_url,
            os.environ.get("PRO_API_KEY", ""),
            args.model,
            args.api,
            args.reasoning_effort,
            timeout=args.timeout,
            audit_dir=args.output / "model_calls",
        )
        try:
            result = synthesize_tasks(
                args.graph, args.walks, args.output, client, args.generation_attempts, args.resume
            )
        finally:
            client.close()
        if not result["complete"]:
            print(json.dumps(result))
            return 2
    elif args.command == "score":
        from verification.verifier import score

        pro = read_json(args.task)
        result = score(
            pro["initial_state"],
            read_json(args.final_state),
            pro["verifier"]["assertions"],
        )
    else:
        result = verify_tasks(args.tasks)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
