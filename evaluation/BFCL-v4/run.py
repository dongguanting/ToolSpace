from __future__ import annotations
import json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from common import arguments, select, output, write_json, execute


def main():
    args = arguments("BFCL V4 official all_scoring, 5,217 cases")
    data = HERE / "source/bfcl_eval/data"
    root = output("BFCL-v4", args)
    os.environ["BFCL_PROJECT_ROOT"] = str(root)
    from bfcl_eval.constants.category_mapping import TEST_COLLECTION_MAPPING
    from bfcl_eval.utils import load_dataset_entry

    if args.check_only:
        count = sum(
            len(load_dataset_entry(category))
            for category in TEST_COLLECTION_MAPPING["all_scoring"]
        )
        print(f"BFCL: {count}/5217 official all_scoring entries valid")
        return 0
    partial = args.limit is not None or args.tasks is not None
    if not partial and not (
        os.getenv("SERPAPI_API_KEY") or os.getenv("SERPER_API_KEY")
    ):
        raise SystemExit("Set SERPAPI_API_KEY (official search) or SERPER_API_KEY")
    env = os.environ.copy()
    env.update(
        BFCL_PROJECT_ROOT=str(root),
        BFCL_GW_BASE_URL=env["MODEL_BASE_URL"],
        BFCL_GW_MODEL=env["MODEL"],
        BFCL_GW_API_KEY=env["MODEL_API_KEY"],
        BFCL_GW_REGISTRY_ID="toolspace-model",
        PYTHONPATH=str(HERE / "adapter")
        + os.pathsep
        + str(HERE / "source")
        + os.pathsep
        + env.get("PYTHONPATH", ""),
    )
    cmd = [
        sys.executable,
        "-m",
        "bfcl_eval",
        "generate",
        "--model",
        "toolspace-model",
        "--temperature",
        "0.001",
        "--num-threads",
        os.getenv("CONCURRENCY", "8"),
        "--skip-server-setup",
        "--include-input-log",
    ]
    if partial:
        # A smoke is a few simple-Python entries; it never claims an overall score.
        cases = [
            json.loads(l)
            for l in (data / "BFCL_v4_simple_python.json").read_text().splitlines()
            if l.strip()
        ]
        selected = select(cases, args, key=lambda x: x["id"])
        write_json(
            root / "test_case_ids_to_generate.json",
            {"simple_python": [x["id"] for x in selected]},
        )
        cmd += ["--run-ids"]
        category = "simple_python"
        expected_ids = {x["id"] for x in selected}
    else:
        cmd += ["--test-category", "all_scoring"]
        category = "all_scoring"
        expected_ids = {
            entry["id"]
            for name in TEST_COLLECTION_MAPPING["all_scoring"]
            for entry in load_dataset_entry(name)
        }
    write_json(
        root / "config.json",
        {
            "model": env["MODEL"],
            "temperature": 0.001,
            "category": category,
            "partial": partial,
            "search_provider": "serpapi"
            if os.getenv("SERPAPI_API_KEY")
            else "serper"
            if os.getenv("SERPER_API_KEY")
            else None,
        },
    )
    execute(cmd, env=env)
    evaluation = [
        sys.executable,
        "-m",
        "bfcl_eval",
        "evaluate",
        "--model",
        "toolspace-model",
        "--test-category",
        category,
    ]
    if partial:
        evaluation += ["--partial-eval"]
    execute(evaluation, env=env)
    # Preserve every official score file and mark inference placeholders explicitly.
    rows = []
    for p in (root / "result/toolspace-model").rglob("BFCL_v4_*_result.json"):
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rows.append(row)
    placeholders = sum(
        "Error during inference" in str(r.get("result", "")) for r in rows
    )
    import csv

    score = None
    metric = "official_weighted_overall_accuracy"
    if partial:
        metric = "simple_python_accuracy"
        files = list((root / "score").rglob("BFCL_v4_simple_python_score.json"))
        if files:
            with files[0].open() as handle:
                score = 100 * json.loads(handle.readline())["accuracy"]
    else:
        with (root / "score/data_overall.csv").open() as handle:
            for row in csv.DictReader(handle):
                if row.get("Model") == "toolspace-model":
                    score = float(row["Overall Acc"].rstrip("%"))
    generated_ids = {row["id"] for row in rows}
    missing_ids = expected_ids - generated_ids
    duplicate_rows = len(rows) - len(generated_ids)
    complete = (
        not missing_ids
        and not duplicate_rows
        and placeholders == 0
        and score is not None
    )
    write_json(
        root / "summary.json",
        {
            "metric": metric,
            "score": score if complete else None,
            "full_suite": not partial,
            "requested": len(expected_ids),
            "generated": len(rows),
            "missing": len(missing_ids),
            "duplicate_rows": duplicate_rows,
            "inference_placeholders": placeholders,
            "complete": complete,
            "scores": "score/data_overall.csv",
            "partial": partial,
        },
    )
    print("Official score:", root / "score/data_overall.csv")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
