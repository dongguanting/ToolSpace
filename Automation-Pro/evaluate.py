#!/usr/bin/env python3
"""AutomationPro: validate tasks, evaluate a model, or score local tool traces."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_tasks():
    rows = read_jsonl(ROOT / "data/tasks.jsonl")
    index = json.loads((ROOT / "data/index.json").read_text(encoding="utf-8"))
    entries = index["entries"]
    if len(rows) != 300 or len(entries) != 300:
        raise ValueError("AutomationPro requires exactly 300 tasks")
    if len({r["example_id"] for r in rows}) != 300:
        raise ValueError("duplicate example_id")
    for row, entry in zip(rows, entries):
        if row["example_id"] != entry["example_id"]:
            raise ValueError("dataset/index order mismatch")
    return rows, {e["example_id"]: e for e in entries}


def select_tasks(rows, index, args):
    if args.domain:
        rows = [r for r in rows if index[r["example_id"]]["domain"] in args.domain]
    if args.example_id:
        wanted = set(args.example_id)
        missing = wanted - {r["example_id"] for r in rows}
        if missing:
            raise ValueError(f"Unknown or filtered example IDs: {sorted(missing)}")
        rows = [r for r in rows if r["example_id"] in wanted]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        raise ValueError("No tasks selected")
    return rows


def validate(rows, index):
    from environments import Environment
    from automationbench.task_contract import task_contract_sha256
    from verifiers.registry import AssertionRegistry

    manifest = json.loads((ROOT / "data/manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files_sha256"].items():
        path = ROOT / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"File checksum mismatch: {name}")
    questions = read_jsonl(ROOT / "data/questions.jsonl")
    references = read_jsonl(ROOT / "verifiers/reference_trajectories.jsonl")
    if [r["example_id"] for r in rows] != [q["example_id"] for q in questions]:
        raise ValueError("questions/task IDs differ")
    if [r["example_id"] for r in rows] != [r["example_id"] for r in references]:
        raise ValueError("reference/task IDs differ")
    assertion_count = 0
    for row, question in zip(rows, questions):
        info = json.loads(row["info"])
        entry = index[row["example_id"]]
        actual = task_contract_sha256(example_id=row["example_id"], prompt=row["prompt"], info=info)
        if actual != entry["task_contract_sha256"]:
            raise ValueError(f"Task contract changed: {row['example_id']}")
        if question["question"] != row["prompt"][-1]["content"]:
            raise ValueError("Question/prompt mismatch")
        for assertion in info["assertions"]:
            if assertion["type"] not in AssertionRegistry._handlers:
                raise ValueError(f"Unknown assertion: {assertion['type']}")
        # Exercise all schema models and every assertion handler on the initial world.
        Environment(row).score()
        assertion_count += len(info["assertions"])
    domains = dict(Counter(e["domain"] for e in index.values()))
    if domains != {d: 50 for d in ("marketing", "finance", "hr", "operations", "sales", "support")}:
        raise ValueError("Expected six domains of 50 tasks")
    return {"passed": True, "tasks": len(rows), "domains": domains,
            "assertions": assertion_count, "checksummed_files": len(manifest["files_sha256"])}


def request_json(endpoint, payload, api_key, timeout, retries):
    """POST JSON and retry only transient transport/provider failures."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers)
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in (408, 409, 429, 500, 502, 503, 504) or attempt == retries:
                try:
                    detail = error.read().decode("utf-8", errors="replace")[:1000]
                except OSError:
                    detail = ""
                raise RuntimeError(f"Model API HTTP {error.code}: {detail}") from None
        except (URLError, TimeoutError, OSError):
            if attempt == retries:
                raise RuntimeError("Model API connection failed or timed out") from None
        time.sleep(min(2 ** attempt, 8))


def response_input(messages):
    """Convert the local trace representation to Responses API input items."""
    instructions = []
    items = []
    for message in messages:
        if message.get("role") == "system":
            instructions.append(message.get("content", ""))
        elif message.get("role") == "user":
            items.append({"role": "user", "content": message.get("content", "")})
        elif message.get("type") == "assistant_output":
            items.extend(message.get("output", []))
        elif message.get("role") == "tool":
            items.append({"type": "function_call_output",
                          "call_id": message["tool_call_id"],
                          "output": message.get("content", "")})
    return items, "\n\n".join(instructions)


def responses_tools():
    """Return the flat function tool format required by Responses API."""
    from environments import tool_definitions

    result = []
    for item in tool_definitions():
        function = item["function"]
        result.append({"type": "function", "name": function["name"],
                       "description": function.get("description", ""),
                       "parameters": function["parameters"]})
    return result


def run_task(row, args, api_key):
    from environments import Environment, tool_definitions

    env = Environment(row)
    messages = json.loads(json.dumps(row["prompt"]))
    usage, model_calls, attempted_tools = Counter(), 0, 0
    status, error_message = "turn_limit", None
    for _ in range(args.max_turns):
        try:
            if args.api == "responses":
                input_items, instructions = response_input(messages)
                payload = {"model": args.model, "input": input_items,
                           "tools": responses_tools()}
                if instructions:
                    payload["instructions"] = instructions
                if args.reasoning_effort or args.reasoning_context:
                    payload["reasoning"] = {}
                    if args.reasoning_effort:
                        payload["reasoning"]["effort"] = args.reasoning_effort
                    if args.reasoning_context:
                        payload["reasoning"]["context"] = args.reasoning_context
                if args.temperature is not None:
                    payload["temperature"] = args.temperature
                if args.max_tokens:
                    payload["max_output_tokens"] = args.max_tokens
                response = request_json(args.base_url.rstrip("/") + "/responses", payload,
                                        api_key, args.timeout, args.retries)
                model_calls += 1
                output = response.get("output") or []
                messages.append({"type": "assistant_output", "output": output})
                for key, value in (response.get("usage") or {}).items():
                    if isinstance(value, (int, float)):
                        usage[key] += value
                calls = [item for item in output if item.get("type") == "function_call"]
                if not calls:
                    status = "completed" if response.get("status", "completed") == "completed" else "provider_stop"
                    break
                for call in calls:
                    attempted_tools += 1
                    try:
                        observation = env.execute(call["name"], call.get("arguments", "{}"))
                    except (ValueError, TypeError, KeyError, AttributeError) as error:
                        observation = json.dumps({"error": str(error)})
                    messages.append({"role": "tool", "tool_call_id": call["call_id"],
                                     "content": observation})
                continue

            payload = {"model": args.model, "messages": messages, "tools": tool_definitions()}
            if args.temperature is not None:
                payload["temperature"] = args.temperature
            if args.reasoning_effort or args.reasoning_context:
                payload["reasoning"] = {}
                if args.reasoning_effort:
                    payload["reasoning"]["effort"] = args.reasoning_effort
                if args.reasoning_context:
                    payload["reasoning"]["context"] = args.reasoning_context
            if args.max_tokens:
                payload["max_completion_tokens"] = args.max_tokens
            response = request_json(args.base_url.rstrip("/") + "/chat/completions", payload,
                                    api_key, args.timeout, args.retries)
            model_calls += 1
            choice = response["choices"][0]
            message = choice["message"]
            if message.get("role") != "assistant":
                raise ValueError("Model returned a non-assistant message")
            messages.append({k: message[k] for k in ("role", "content", "tool_calls", "reasoning_content")
                             if k in message})
            for key, value in (response.get("usage") or {}).items():
                if isinstance(value, (int, float)):
                    usage[key] += value
            calls = message.get("tool_calls") or []
            if not calls:
                status = "completed" if choice.get("finish_reason") == "stop" else "provider_stop"
                break
            for call in calls:
                attempted_tools += 1
                try:
                    observation = env.execute(call["function"]["name"], call["function"]["arguments"])
                except (ValueError, TypeError, KeyError, AttributeError) as error:
                    observation = json.dumps({"error": str(error)})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": observation})
        except (RuntimeError, ValueError, KeyError, IndexError, TypeError) as error:
            status, error_message = "model_error", str(error)
            break
    score = env.score()
    if error_message:
        score = {**score, "reward": 0.0, "task_completed_correctly": 0}
    result = {"example_id": row["example_id"], "status": status, **score,
              "model_calls": model_calls, "tool_calls": attempted_tools, "usage": dict(usage)}
    if error_message:
        result["error"] = error_message
    trace = {"example_id": row["example_id"], "messages": messages, "calls": env.calls}
    return result, trace


def replay_task(row, prediction):
    from environments import Environment

    env = Environment(row)
    errors = []
    calls = prediction.get("calls")
    if calls is None:
        steps = prediction.get("steps")
        if not isinstance(steps, list):
            raise ValueError("Each replay record needs a calls or steps list")
        calls = [{"name": "api_fetch", "arguments": {
            "method": step["method"], "url": step["url"],
            "params": json.dumps(step.get("query") or {}),
            "body": json.dumps(step.get("body") or {}),
        }} for step in steps]
    if not isinstance(calls, list):
        raise ValueError("calls must be a list")
    for number, call in enumerate(calls, 1):
        try:
            env.execute(call["name"], call.get("arguments", {}))
        except (ValueError, TypeError, KeyError) as error:
            errors.append({"call": number, "error": str(error)})
    result = {"example_id": row["example_id"], "status": "replayed", **env.score(),
              "tool_calls": len(calls), "tool_errors": errors}
    return result, {"example_id": row["example_id"], "calls": env.calls}


def summarize(results):
    def metrics(items):
        return {"tasks": len(items),
                "passed": sum(r["task_completed_correctly"] for r in items),
                "pass_rate": sum(r["task_completed_correctly"] for r in items) / len(items),
                "mean_reward": sum(r["reward"] for r in items) / len(items),
                "model_errors": sum(r["status"] == "model_error" for r in items)}
    domains = sorted({r["domain"] for r in results})
    return {**metrics(results), "domains": {
        domain: metrics([r for r in results if r["domain"] == domain]) for domain in domains}}


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Verify all 300 task contracts, files, schemas and assertions")
    for command in ("run", "replay"):
        p = sub.add_parser(command)
        p.add_argument("--domain", nargs="+", choices=["marketing", "finance", "hr", "operations", "sales", "support"])
        p.add_argument("--example-id", nargs="+", type=int)
        p.add_argument("--limit", type=positive_int)
        p.add_argument("--output", type=Path, required=True, help="New output directory")
        if command == "run":
            p.add_argument("--model", required=True)
            p.add_argument("--base-url", required=True, help="OpenAI-compatible API base, including /v1 if required")
            p.add_argument("--api", choices=["chat", "responses"], default="chat")
            p.add_argument("--api-key-env", default="OPENAI_API_KEY")
            p.add_argument("--max-turns", type=positive_int, default=50)
            p.add_argument("--concurrency", type=positive_int, default=1)
            p.add_argument("--timeout", type=positive_int, default=1200)
            p.add_argument("--retries", type=int, choices=range(0, 11), default=2)
            p.add_argument("--temperature", type=float)
            p.add_argument("--reasoning-effort")
            p.add_argument("--reasoning-context", default=None)
            p.add_argument("--max-tokens", type=positive_int)
        else:
            p.add_argument("--input", type=Path, required=True, help="JSONL of example_id and calls (or steps)")
    args = parser.parse_args(argv)
    rows, index = load_tasks()
    if args.command == "validate":
        print(json.dumps(validate(rows, index), ensure_ascii=False, indent=2))
        return 0
    rows = select_tasks(rows, index, args)
    predictions = {}
    if args.command == "replay":
        for prediction in read_jsonl(args.input):
            identifier = prediction["example_id"]
            if identifier not in index:
                raise ValueError(f"Unknown replay example_id: {identifier}")
            if identifier in predictions:
                raise ValueError(f"Duplicate replay example_id: {identifier}")
            predictions[identifier] = prediction
        missing = {r["example_id"] for r in rows} - set(predictions)
        if missing:
            raise ValueError(f"Missing traces for {len(missing)} selected tasks")
    # Initialize the simulator and outcome adapters before creating worker threads.
    from environments import Environment  # noqa: F401

    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "traces").mkdir()
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    config["toolset"] = "api"
    config["dataset_sha256"] = hashlib.sha256((ROOT / "data/tasks.jsonl").read_bytes()).hexdigest()
    write_json(args.output / "config.json", config)
    api_key = os.environ.get(args.api_key_env, "") if args.command == "run" else ""
    results = []
    workers = args.concurrency if args.command == "run" else 1
    with (args.output / "results.jsonl").open("w", encoding="utf-8") as stream:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_task, row, args, api_key) if args.command == "run"
                       else pool.submit(replay_task, row, predictions[row["example_id"]]): row for row in rows}
            for future in as_completed(futures):
                result, trace = future.result()
                entry = index[result["example_id"]]
                result.update({k: entry[k] for k in ("domain", "question_id", "task_name", "task_contract_sha256")})
                results.append(result)
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                stream.flush()
                write_json(args.output / "traces" / f"{result['example_id']}.json", trace)
                print(f"[{len(results)}/{len(rows)}] {result['example_id']} reward={result['reward']:.3f} {result['status']}", flush=True)
    summary = summarize(results)
    summary["mode"] = args.command
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["model_errors"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
