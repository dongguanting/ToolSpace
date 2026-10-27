from __future__ import annotations
import json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from common import arguments, select, output, write_json, summarize, execute


def main():
    args = arguments("AutomationBench v1.0.6, 600 public tasks")
    from automationbench.domains import DEFAULT_DOMAINS, get_combined_dataset
    from automationbench.scripts.eval import _task_name

    tasks = list(get_combined_dataset(DEFAULT_DOMAINS))
    assert len(tasks) == 600
    selected = select(tasks, args, key=_task_name)
    if args.check_only:
        print(f"AutomationBench: {len(selected)}/600 task definitions valid")
        return 0
    root = output("AutomationBench", args)
    env = os.environ.copy()
    env["AB_MODEL_KEY"] = env["MODEL_API_KEY"]
    # The benchmark ChatGPT tool remains simulated, independently of model auth.
    env.pop("OPENAI_API_KEY", None)
    model = env["MODEL"]
    api = os.getenv("AB_API", "chat_completions")
    extra = json.loads(os.getenv("MODEL_EXTRA_BODY", "{}"))
    extra.update(temperature=float(os.getenv("AB_TEMPERATURE", "1")), max_tokens=32768)
    cmd = [
        sys.executable,
        "-m",
        "automationbench.scripts.eval",
        "--model",
        model,
        "--base-url",
        env["MODEL_BASE_URL"],
        "--api-key-var",
        "AB_MODEL_KEY",
        "--api",
        api,
        "--toolset",
        "api",
        "--reasoning-context",
        "all_turns",
        "--max-steps",
        os.getenv("AB_MAX_STEPS", "50"),
        "--max-concurrent",
        os.getenv("CONCURRENCY", "8"),
        "--extra-body",
        json.dumps(extra),
        "--export-json",
        str(root / "results.json"),
    ]
    if len(selected) != 600:
        cmd += ["--tasks", ",".join(map(_task_name, selected))]
    if os.getenv("MODEL_REASONING_EFFORT"):
        cmd += ["--reasoning-effort", os.environ["MODEL_REASONING_EFFORT"]]
    write_json(
        root / "config.json",
        {
            "model": model,
            "api": api,
            "toolset": "api",
            "max_steps": int(os.getenv("AB_MAX_STEPS", "50")),
            "task_ids": list(map(_task_name, selected)),
        },
    )
    execute(cmd, env=env)
    data = json.loads((root / "results.json").read_text())
    rows = [
        {
            "task_id": t["name"],
            "reward": float(t["passed"]),
            **(
                {"error": "aborted rollout"}
                if t["name"] in data["summary"].get("aborted_tasks", [])
                else {}
            ),
        }
        for t in data["tasks"]
    ]
    result = summarize(root, rows, len(selected), 600, "strict_pass_rate")
    summary = json.loads((root / "summary.json").read_text())
    summary["average_assertion_score"] = 100 * data["summary"]["avg_score"]
    write_json(root / "summary.json", summary)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
