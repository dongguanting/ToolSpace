from __future__ import annotations
import json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from common import arguments, select, output, write_json, summarize, execute

import shutil


def main():
    args = arguments("JobBench official main split, 65 tasks, OpenCode")
    source = Path(
        os.getenv(
            "JOBBENCH_DATA", str(Path(os.environ["RUNTIME"]) / "JobBench/data/dataset")
        )
    )
    tasks = sorted(
        str(p.parent.relative_to(source)) for p in source.glob("*/task*/task_folder")
    )
    if len(tasks) != 65:
        raise SystemExit(
            f"Expected 65 main tasks under {source}; found {len(tasks)}. Run JobBench/setup.sh."
        )
    tasks = select(tasks, args)
    if args.check_only:
        print(f"JobBench: {len(tasks)}/65 task definitions valid")
        return 0
    if not os.getenv("BOYUE_API_KEY"):
        raise SystemExit("Set BOYUE_API_KEY for the Grok-4.3 judge")
    root = output("JobBench", args)
    dataset = root / "dataset"
    # The official harness writes outputs inside the dataset; copy the selected tasks.
    for t in tasks:
        if not (dataset / t).exists():
            shutil.copytree(source / t, dataset / t)
    # Keep mutable runner logs out of the distributed code directory.
    runner = root / "runner"
    if not runner.exists():
        shutil.copytree(HERE / "source", runner)
    env = os.environ.copy()
    model = env["MODEL"]
    label = env["RUN_NAME"]
    short = model.replace("/", "_").replace(".", "-")
    safe_label = label.replace("/", "_").replace(" ", "_").replace(".", "-")
    evaluated = short + "-" + safe_label
    env.update(
        XDG_DATA_HOME=str(root / "state/data"),
        XDG_CONFIG_HOME=str(root / "state/config"),
        XDG_CACHE_HOME=str(root / "state/cache"),
        XDG_STATE_HOME=str(root / "state/state"),
        OPENCODE_DISABLE_AUTOUPDATE="1",
        OPENAI_BASE_URL=env["MODEL_BASE_URL"],
        CUSTOM_API_KEY=env["MODEL_API_KEY"],
        MODEL_NAME=model,
        OPENCODE_DIR=str(Path(env["RUNTIME"]) / "JobBench/opencode"),
        TASKS_BASE_DIR=str(dataset),
        SPLIT="main",
        BENCHMARK_MODELS="toolspace/" + model + "|" + short,
        OPENCODE_PROVIDER_ID="toolspace",
        MODEL_CONTEXT_TOKENS="262144",
        MODEL_OUTPUT_TOKENS="32768",
        OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX="32768",
        TIMEOUT_PER_TASK="7200",
        RETRY_TIMEOUT_PER_TASK="7200",
        MAX_RETRIES="2",
        MAX_CONCURRENT_PER_MODEL=os.getenv("CONCURRENCY", "10"),
        RUN_LABEL=label,
        JOBBENCH_WORKSPACE_ROOT=str(root / "workspaces"),
        JUDGE_API_BASE=env["BOYUE_BASE_URL"],
        JUDGE_API_KEY=env["BOYUE_API_KEY"],
        JUDGE_MODEL=os.getenv("JOBBENCH_JUDGE_MODEL", "grok-4.3"),
        TARGET_DIR=str(dataset),
        EVAL_MODEL=evaluated,
        JUDGE_PYTHON=sys.executable,
        JOBBENCH_JUDGE_PYTHON=sys.executable,
    )
    write_json(
        root / "config.json",
        {
            "model": model,
            "judge": env["JUDGE_MODEL"],
            "task_ids": tasks,
            "task_timeout": 7200,
            "max_attempts": 2,
            "output_name": evaluated,
        },
    )
    execute(["bash", runner / "run_custom_api.sh"], env=env, cwd=runner)
    execute(
        ["bash", runner / "run_custom_judge.sh"],
        env={**env, "MAX_RETRIES": "3"},
        cwd=runner,
    )
    rows = []
    for t in tasks:
        # Upstream stores one total_score/max_score record per judged task.
        records = []
        for p in (dataset / t / "eval_result" / ("eval_" + evaluated)).rglob("*.json"):
            try:
                d = json.loads(p.read_text())
                if isinstance(d, dict) and "total_score" in d and "max_score" in d:
                    records.append(d)
            except (OSError, ValueError):
                pass
        if not records:
            rows.append({"task_id": t, "reward": None, "error": "Missing judge score"})
            continue
        d = records[-1]
        rubrics = d.get("rubrics", [])
        failed_judge = any(
            r.get("result", {}).get("overall_reasoning", "").startswith("Failed after ")
            for r in rubrics
        )
        if not rubrics or failed_judge or len(rubrics) != d.get("total_count"):
            rows.append(
                {
                    "task_id": t,
                    "reward": None,
                    "error": "Incomplete or failed judge response",
                }
            )
            continue
        rows.append(
            {
                "task_id": t,
                "reward": round(d["total_score"] / d["max_score"], 4)
                if d["max_score"]
                else 0,
            }
        )
    return summarize(root, rows, len(tasks), 65, "mean_rounded_task_score")


if __name__ == "__main__":
    raise SystemExit(main())
