from __future__ import annotations
import json, os, sys, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from common import arguments, select, output, write_json, summarize

from concurrent.futures import ThreadPoolExecutor


def main():
    args = arguments("Tau3 Banking Knowledge, canonical 97 tasks")
    source = HERE / "source"
    tasks = json.loads(
        (source / "data/tau2/domains/banking_knowledge/tasks.json").read_text()
    )
    assert len(tasks) == 97 and len({t["id"] for t in tasks}) == 97
    tasks = select(tasks, args, key=lambda x: x["id"])
    if args.check_only:
        os.environ["TAU2_DATA_DIR"] = str(source / "data")
        sys.path.insert(0, str(source / "src"))
        from tau2.data_model.tasks import Task

        for t in tasks:
            Task.model_validate(t)
        print(f"Tau3: {len(tasks)}/97 task definitions valid")
        return 0
    if not os.getenv("BOYUE_API_KEY"):
        raise SystemExit("Set BOYUE_API_KEY for the user, judge and embeddings")
    root = output("Tau3-Banking", args)
    env = os.environ.copy()
    env.update(
        TAU2_DATA_DIR=str(source / "data"),
        TAU2_EMBEDDINGS_CACHE_DIR=str(Path(env["RUNTIME"]) / "Tau3-Banking/embeddings"),
        NEW_API_KEY=env["MODEL_API_KEY"],
        embeddingKey=env["BOYUE_API_KEY"],
        gptKey=env["BOYUE_API_KEY"],
    )
    write_json(
        root / "config.json",
        {
            "model": env["MODEL"],
            "user": "gpt-5.2",
            "judge": "gpt-5.2",
            "reasoning_effort": "low",
            "seed": 300,
            "max_steps": int(os.getenv("TAU3_MAX_STEPS", "200")),
            "retrieval": "alltools",
            "embedding_model": "text-embedding-3-large",
            "task_ids": [t["id"] for t in tasks],
        },
    )

    def one(t):
        dest = root / t["id"]
        dest.mkdir(exist_ok=True)
        existing = dest / "summary.json"
        if not existing.exists():
            write_json(dest / "task.json", t)
            cmd = [
                sys.executable,
                HERE / "run_task.py",
                "--task",
                dest / "task.json",
                "--output-dir",
                dest,
                "--api-base",
                env["BOYUE_BASE_URL"],
                "--agent-api-base",
                env["MODEL_BASE_URL"],
                "--agent-model",
                env["MODEL"],
                "--agent-api-key-env",
                "MODEL_API_KEY",
                "--user-judge-api-key-env",
                "BOYUE_API_KEY",
                "--user-model",
                "gpt-5.2",
                "--user-reasoning-effort",
                "low",
                "--judge-model",
                "gpt-5.2",
                "--judge-reasoning-effort",
                "low",
                "--seed",
                "300",
                "--max-steps",
                os.getenv("TAU3_MAX_STEPS", "200"),
                "--temperature",
                "1.0",
                "--agent-temperature",
                os.getenv("MODEL_TEMPERATURE", "1.0"),
                "--retrieval-config",
                "alltools",
            ]
            if os.getenv("MODEL_REASONING_EFFORT"):
                cmd += [
                    "--agent-reasoning-effort",
                    os.environ["MODEL_REASONING_EFFORT"],
                ]
            with (dest / "run.log").open("w") as log:
                completed = subprocess.run(
                    list(map(str, cmd)), env=env, stdout=log, stderr=subprocess.STDOUT
                )
            if completed.returncode or not existing.exists():
                return {
                    "task_id": t["id"],
                    "reward": None,
                    "error": "Task failed; see run.log",
                }
        result = json.loads(existing.read_text())
        return {
            "task_id": t["id"],
            "reward": result.get("reward"),
            **(
                {"error": "No evaluator verdict"}
                if result.get("reward") is None
                else {}
            ),
        }

    with ThreadPoolExecutor(max_workers=int(os.getenv("CONCURRENCY", "33"))) as pool:
        rows = list(pool.map(one, tasks))
    return summarize(root, rows, len(tasks), 97, "task_success_rate")


if __name__ == "__main__":
    raise SystemExit(main())
