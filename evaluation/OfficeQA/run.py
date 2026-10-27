from __future__ import annotations
import os, sys, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from common import arguments, select, output, write_json, summarize

import csv
from concurrent.futures import ThreadPoolExecutor


def main():
    args = arguments(
        "OfficeQA Pro, fixed 133 questions, official reward tolerance 0.05"
    )
    data = Path(
        os.getenv("OFFICEQA_DATA", str(Path(os.environ["RUNTIME"]) / "OfficeQA/data"))
    )
    dataset = HERE / "data/officeqa_pro.csv"
    corpus = data / "corpus/jsons"
    if not dataset.exists():
        raise SystemExit(
            "Run OfficeQA/setup.sh with an authorized HF_TOKEN for databricks/officeqa"
        )
    with dataset.open() as f:
        questions = list(csv.DictReader(f))
    if len(questions) != 133:
        raise ValueError("OfficeQA Pro must contain 133 questions")
    canonical = (HERE / "task_ids.txt").read_text().splitlines()
    if [q["uid"] for q in questions] != canonical:
        raise ValueError("OfficeQA task IDs changed")
    questions = select(questions, args, key=lambda x: x["uid"])
    if args.check_only:
        if not corpus.is_dir():
            raise FileNotFoundError(corpus)
        print(f"OfficeQA: {len(questions)}/133 questions valid")
        return 0
    root = output("OfficeQA", args)
    subset = root / "questions.csv"
    with subset.open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(questions[0]))
        writer.writeheader()
        writer.writerows(questions)
    env = os.environ.copy()
    env.update(
        MODEL_NAME=env["MODEL"],
        MODEL_TEMPERATURE="0",
        MODEL_MAX_TOKENS="8192",
        OFFICEQA_MAX_STEPS="200",
        OFFICEQA_CONTEXT_WINDOW="2",
        MODEL_REASONING_EFFORT=env.get("MODEL_REASONING_EFFORT", "max"),
        OFFICEQA_TASK_TIMEOUT="2700",
        OFFICEQA_TOLERANCE="0.05",
        OFFICEQA_INDEX=str(Path(env["RUNTIME"]) / "OfficeQA/index.sqlite3"),
    )
    write_json(
        root / "config.json",
        {
            "model": env["MODEL"],
            "task_ids": [q["uid"] for q in questions],
            "temperature": 0,
            "max_steps": 200,
            "task_timeout": 2700,
            "max_tokens": 8192,
            "context_window": 2,
            "reasoning_effort": env["MODEL_REASONING_EFFORT"],
            "tolerance": 0.05,
        },
    )
    # Build the shared read-only index once before worker processes start.
    from evaluate import CorpusStore

    store = CorpusStore(corpus, Path(env["OFFICEQA_INDEX"]), 18000)
    store.close()
    workers = min(int(os.getenv("CONCURRENCY", "8")), len(questions))

    def worker(i):
        with (root / f"worker-{i}.log").open("w") as log:
            return subprocess.run(
                [
                    sys.executable,
                    str(HERE / "evaluate.py"),
                    "--dataset",
                    str(subset),
                    "--corpus",
                    str(corpus),
                    "--results",
                    str(root),
                    "--model",
                    env["MODEL"],
                    "--tolerance",
                    "0.05",
                    "--workers",
                    str(workers),
                    "--worker-index",
                    str(i),
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            ).returncode

    with ThreadPoolExecutor(max_workers=workers) as pool:
        codes = list(pool.map(worker, range(workers)))
    if any(codes):
        raise RuntimeError("OfficeQA worker failed; see worker logs")
    from evaluate import successful_records

    records = successful_records(root, env["MODEL"], {str(q["uid"]) for q in questions})
    rows = []
    for q in questions:
        r = records.get(str(q["uid"]))
        rows.append(
            {"task_id": str(q["uid"]), "reward": float(r["score"])}
            if r
            else {
                "task_id": str(q["uid"]),
                "reward": None,
                "error": "No valid submission",
            }
        )
    return summarize(root, rows, len(questions), 133, "accuracy")


if __name__ == "__main__":
    raise SystemExit(main())
