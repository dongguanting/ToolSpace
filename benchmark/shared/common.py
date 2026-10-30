"""Small helpers shared by the seven launchers."""

from __future__ import annotations
import argparse, json, math, os, subprocess
from pathlib import Path


def arguments(description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument(
        "--limit", type=int, default=1 if os.getenv("SCOPE") == "smoke" else None
    )
    p.add_argument("--output", type=Path)
    p.add_argument("--tasks", help="Comma-separated task IDs")
    p.add_argument(
        "--check-only",
        action="store_true",
        help="Validate input files without calling models",
    )
    a = p.parse_args()
    if a.limit is not None and a.limit < 1:
        p.error("--limit must be positive")
    return a


def select(items, args, key=lambda x: x):
    by_id = {str(key(x)): x for x in items}
    if len(by_id) != len(items):
        raise ValueError("Duplicate task IDs")
    if args.tasks:
        requested = args.tasks.split(",")
        missing = set(requested) - by_id.keys()
        if missing:
            raise ValueError("Unknown task IDs: " + ",".join(sorted(missing)))
        items = [by_id[t] for t in requested]
    if args.limit is not None:
        items = items[: args.limit]
    if not items:
        raise ValueError("No tasks selected")
    return items


def output(bench, args):
    root = (
        args.output or Path(os.environ["OUTPUT_ROOT"]) / bench / os.environ["RUN_NAME"]
    )
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == "config.json" and "model" in data:
        data = {
            **data,
            "base_url": os.getenv("MODEL_BASE_URL"),
            "extra_body": os.getenv("MODEL_EXTRA_BODY", "{}"),
        }
        if path.exists() and json.loads(path.read_text()) != data:
            raise ValueError("Run configuration changed; use a new RUN_NAME")
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def summarize(root, rows, expected, full_count, metric="mean_reward"):
    done = [
        r
        for r in rows
        if isinstance(r.get("reward"), (int, float)) and math.isfinite(r["reward"])
    ]
    errors = [r for r in rows if r.get("error")]
    complete = len(done) == expected and not errors
    result = {
        "metric": metric,
        "score": 100 * sum(r["reward"] for r in done) / expected if complete else None,
        "requested": expected,
        "scored": len(done),
        "errors": len(errors),
        "complete": complete,
        "full_suite": expected == full_count,
        "tasks": rows,
    }
    write_json(root / "summary.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "tasks"}, indent=2))
    return 0 if complete else 1


def execute(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, **kwargs)
