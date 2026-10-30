#!/usr/bin/env python3
"""Validate or evaluate the frozen 98-task Tau3 Banking Pro collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASK_FILE = ROOT / "data/tasks.json"


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def load_tasks() -> list[dict]:
    manifest = json.loads((ROOT / "data/manifest.json").read_text())
    catalog_path = ROOT / manifest["public_task_catalog"]
    catalog_raw = catalog_path.read_bytes()
    if hashlib.sha256(catalog_raw).hexdigest() != manifest["public_task_catalog_sha256"]:
        raise ValueError("The public task catalog checksum changed")
    catalog = json.loads(catalog_raw)
    catalog_ids = [entry["public_id"] for entry in catalog["tasks"]]
    if catalog_ids != manifest["public_task_ids"] or len(set(catalog_ids)) != 98:
        raise ValueError("The public task catalog has invalid IDs or order")
    raw = TASK_FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["tasks_sha256"]:
        raise ValueError("The frozen Pro98 tasks.json checksum changed")
    tasks = json.loads(raw)
    ids = [task["id"] for task in tasks]
    if len(tasks) != 98 or len(set(ids)) != 98 or ids != manifest["task_ids"]:
        raise ValueError("Expected the exact 98 unique Pro98 task IDs in source order")
    if [item["public_id"] for item in catalog["tasks"]] != ids:
        raise ValueError("The public catalog does not match the task IDs and order")
    for entry, task in zip(manifest["entries"], tasks, strict=True):
        path = ROOT / "data" / entry["path"]
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"Individual task checksum changed: {task['id']}")
        if json.loads(content) != task:
            raise ValueError(f"Individual task differs from tasks.json: {task['id']}")
    return tasks


def select_tasks(
    tasks: list[dict], requested: str | None, limit: int | None
) -> list[dict]:
    if requested:
        by_id = {task["id"]: task for task in tasks}
        catalog = json.loads((ROOT / "data/public_task_catalog.json").read_text())
        titles = {item["public_id"]: item["title"] for item in catalog["tasks"]}
        ids = requested.split(",")
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate IDs in --tasks")
        missing = set(ids) - by_id.keys()
        if missing:
            raise ValueError("Unknown task IDs: " + ", ".join(sorted(missing)))
        tasks = []
        for task_id in ids:
            task = dict(by_id[task_id])
            task["_public_id"] = task_id
            task["_title"] = titles[task_id]
            tasks.append(task)
    else:
        catalog = json.loads((ROOT / "data/public_task_catalog.json").read_text())
        title_by_public = {item["public_id"]: item["title"] for item in catalog["tasks"]}
        tasks = [
            dict(
                task,
                _public_id=task["id"],
                _title=title_by_public[task["id"]],
            )
            for task in tasks
        ]
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be positive")
        tasks = tasks[:limit]
    return tasks


def check_package(tasks: list[dict]) -> None:
    manifest = json.loads((ROOT / "data/manifest.json").read_text())
    for relative, expected in manifest["imported_file_sha256"].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Imported source/data checksum changed: {relative}")
    os.environ["TAU2_DATA_DIR"] = str(ROOT / "data")
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    sys.path.insert(0, str(ROOT / "environments"))
    from tau2.data_model.tasks import Task
    from tau2.domains.banking_knowledge.environment import get_tasks
    from tau2.evaluator import evaluator

    for task in tasks:
        Task.model_validate(task)
    if {task.id for task in get_tasks()} != set(manifest["task_ids"]):
        raise ValueError("Native domain loader did not select exactly Pro98")
    if Path(evaluator.__file__).resolve().parent != ROOT / "verifiers":
        raise ValueError("Evaluator resolved outside this package")
    print(
        json.dumps(
            {
                "benchmark": "Tau3-Banking-Pro",
                "validated_tasks": len(tasks),
                "full_task_count": 98,
                "imported_files_verified": len(manifest["imported_file_sha256"]),
                "evaluator": "verifiers/evaluator.py",
                "api_calls": 0,
            },
            indent=2,
        )
    )


def summarize(rows: list[dict], requested: int) -> dict:
    scored = [
        row
        for row in rows
        if isinstance(row.get("reward"), (int, float)) and math.isfinite(row["reward"])
    ]
    complete = len(scored) == requested and not any(row.get("error") for row in rows)
    return {
        "benchmark": "Tau3-Banking-Pro",
        "metric": "task_success_rate",
        "score": sum(row["reward"] for row in scored) / requested if complete else None,
        "requested": requested,
        "scored": len(scored),
        "unscored": requested - len(scored),
        "successes": sum(row["reward"] == 1 for row in scored),
        "complete": complete,
        "full_suite": requested == 98,
        "tasks": rows,
    }


def result_identity(task: dict) -> dict:
    """Return the public task ID and its workflow title."""
    return {
        "task_id": task.get("_public_id", task["id"]),
        "title": task.get("_title"),
    }


def run_task(
    task: dict,
    output: Path,
    args: argparse.Namespace,
    env: dict,
    cancelled: threading.Event | None = None,
) -> dict:
    identity = result_identity(task)
    public_id = identity["task_id"]
    dest = output / public_id
    dest.mkdir()
    write_json(dest / "task.json", {k: v for k, v in task.items() if not k.startswith("_")})
    command = [
        sys.executable,
        str(ROOT / "environments/run_task.py"),
        "--task",
        str(dest / "task.json"),
        "--output-dir",
        str(dest),
        "--api-base",
        args.user_api_base,
        "--agent-api-base",
        args.api_base,
        "--user-api-base",
        args.user_api_base,
        "--judge-api-base",
        args.user_api_base,
        "--agent-model",
        args.model,
        "--agent-api-key-env",
        "MODEL_API_KEY",
        "--user-judge-api-key-env",
        "USER_API_KEY",
        "--user-model",
        args.user_model,
        "--user-reasoning-effort",
        args.user_reasoning_effort,
        "--judge-model",
        args.judge_model,
        "--judge-reasoning-effort",
        "low",
        "--seed",
        str(args.seed),
        "--max-steps",
        str(args.max_steps),
        "--simulation-timeout",
        str(args.simulation_timeout),
        "--retrieval-config",
        args.retrieval_config,
        "--temperature",
        "1.0",
        "--agent-temperature",
        str(args.temperature),
        "--review" if args.review else "--no-review",
    ]
    if args.reasoning_effort:
        command += ["--agent-reasoning-effort", args.reasoning_effort]
    if args.max_tokens:
        command += ["--agent-max-tokens", str(args.max_tokens)]
    try:
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("Run interrupted before worker started")
        with (dest / "run.log").open("w") as log:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + args.task_timeout
                while True:
                    if cancelled is not None and cancelled.is_set():
                        raise InterruptedError("Run interrupted")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, args.task_timeout)
                    try:
                        code = process.wait(timeout=min(1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                # Clean up the worker's complete process group, including shell descendants.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        if code:
            raise RuntimeError(f"Worker exited {code}; see run.log")
        result = json.loads((dest / "summary.json").read_text())
        if result.get("task_id") != task["id"]:
            raise ValueError("Worker returned a different task ID")
        result["title"] = identity["title"]
        write_json(dest / "summary.json", result)
        reward = result.get("reward")
        if not isinstance(reward, (int, float)) or not math.isfinite(reward):
            raise ValueError("No finite native evaluator verdict")
        row = {
            **identity,
            "reward": reward,
            "termination_reason": result.get("termination_reason"),
        }
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        row = {
            **identity,
            "reward": None,
            "error": f"{type(error).__name__}: {error}",
        }
    write_json(dest / "status.json", row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only", action="store_true", help="Offline integrity and schema checks"
    )
    parser.add_argument(
        "--tasks",
        help="Comma-separated public Pro98 IDs (task_generated_pro_001..098)",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        help="Fresh output directory; existing paths are rejected",
    )
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--model", default=os.getenv("MODEL"))
    parser.add_argument("--api-base", default=os.getenv("MODEL_BASE_URL"))
    parser.add_argument(
        "--user-api-base",
        default=os.getenv("USER_BASE_URL", os.getenv("BOYUE_BASE_URL")),
    )
    parser.add_argument("--user-model", default="gpt-5.2")
    parser.add_argument(
        "--user-reasoning-effort",
        choices=("minimal", "low", "medium", "high"),
        default="low",
    )
    parser.add_argument("--judge-model", default="gpt-5.2")
    parser.add_argument(
        "--review", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument("--retrieval-config", default="alltools")
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument(
        "--reasoning-effort",
        default=os.getenv("MODEL_REASONING_EFFORT"),
        choices=("minimal", "low", "medium", "high", "xhigh", "max"),
    )
    parser.add_argument("--simulation-timeout", type=float, default=1800)
    parser.add_argument("--task-timeout", type=float, default=2400)
    args = parser.parse_args()
    try:
        tasks = select_tasks(load_tasks(), args.tasks, args.limit)
        if args.check_only:
            check_package(tasks)
            return 0
        if (
            not args.output
            or not args.model
            or not args.api_base
            or not args.user_api_base
        ):
            parser.error(
                "Live runs require --output, MODEL/--model, MODEL_BASE_URL/--api-base, and USER_BASE_URL/--user-api-base"
            )
        if (
            args.concurrency < 1
            or args.max_steps < 1
            or args.simulation_timeout <= 0
            or args.task_timeout <= 0
            or (args.max_tokens is not None and args.max_tokens < 1)
        ):
            parser.error("Concurrency, step/token limits and timeouts must be positive")
        check_package(tasks)
        env = os.environ.copy()
        user_key = env.get("USER_API_KEY") or env.get("BOYUE_API_KEY")
        if not env.get("MODEL_API_KEY") or not user_key:
            parser.error("Set MODEL_API_KEY and USER_API_KEY (or BOYUE_API_KEY)")
        env.update(
            USER_API_KEY=user_key,
            TAU2_DATA_DIR=str(ROOT / "data"),
            embeddingKey=env.get("EMBEDDING_API_KEY") or user_key,
        )
        env.setdefault("TAU2_EMBEDDINGS_CACHE_DIR", str(ROOT / ".embeddings_cache"))
        output = args.output.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=False)
        config = {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        }
        config.update(
            task_ids=[task["_public_id"] for task in tasks],
            task_titles={task["_public_id"]: task["_title"] for task in tasks},
            tasks_sha256=hashlib.sha256(TASK_FILE.read_bytes()).hexdigest(),
            credential_variables=["MODEL_API_KEY", "USER_API_KEY", "EMBEDDING_API_KEY"],
            model_extra_body=env.get("MODEL_EXTRA_BODY"),
        )
        write_json(output / "config.json", config)
        cancelled = threading.Event()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [
                pool.submit(run_task, task, output, args, env, cancelled)
                for task in tasks
            ]
            try:
                rows = [future.result() for future in futures]
            except KeyboardInterrupt:
                cancelled.set()
                rows = [future.result() for future in futures]
        summary = summarize(rows, len(tasks))
        write_json(output / "summary.json", summary)
        print(
            json.dumps(
                {key: value for key, value in summary.items() if key != "tasks"},
                indent=2,
            )
        )
        return 0 if summary["complete"] else 1
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
