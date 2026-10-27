#!/usr/bin/env python3

import argparse
import json
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize JobBench judge results")
    parser.add_argument("--root", type=Path, required=True, help="Dataset or scoped task root")
    parser.add_argument("--eval-model", default="", help="Evaluated model name filter")
    parser.add_argument("--judge-model", default="", help="Judge model name filter")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args()


def matches_filter(value: str, requested: str) -> bool:
    return not requested or requested in value or value in requested


def load_results(root: Path, eval_model: str, judge_model: str) -> tuple[list[dict], list[dict]]:
    results = []
    errors = []
    pattern = "*/task*/eval_result/eval_*/*_judge.json"

    for result_path in sorted(root.glob(pattern)):
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append({"path": str(result_path), "error": str(error)})
            continue

        evaluated_model = str(payload.get("evaluated_model", ""))
        actual_judge_model = str(payload.get("judge_model", ""))
        if not matches_filter(evaluated_model, eval_model):
            continue
        if not matches_filter(actual_judge_model, judge_model):
            continue

        relative_path = result_path.relative_to(root)
        results.append(
            {
                "profession": relative_path.parts[0],
                "task": relative_path.parts[1],
                "evaluated_model": evaluated_model,
                "judge_model": actual_judge_model,
                "total_score": float(payload.get("total_score", 0) or 0),
                "max_score": float(payload.get("max_score", 0) or 0),
                "passed_count": int(payload.get("passed_count", 0) or 0),
                "total_count": int(payload.get("total_count", len(payload.get("rubrics", []))) or 0),
                "path": str(result_path),
            }
        )

    return results, errors


def aggregate(results: list[dict]) -> list[dict]:
    groups = defaultdict(
        lambda: {
            "tasks": 0,
            "total_score": 0.0,
            "max_score": 0.0,
            "passed_count": 0,
            "total_count": 0,
        }
    )

    for result in results:
        key = (result["evaluated_model"], result["judge_model"])
        group = groups[key]
        group["tasks"] += 1
        group["total_score"] += result["total_score"]
        group["max_score"] += result["max_score"]
        group["passed_count"] += result["passed_count"]
        group["total_count"] += result["total_count"]

    summaries = []
    for (evaluated_model, judge_model), group in sorted(groups.items()):
        weighted_rate = (
            group["total_score"] / group["max_score"] if group["max_score"] else 0.0
        )
        rubric_pass_rate = (
            group["passed_count"] / group["total_count"] if group["total_count"] else 0.0
        )
        summaries.append(
            {
                "evaluated_model": evaluated_model,
                "judge_model": judge_model,
                **group,
                "weighted_score_rate": weighted_rate,
                "rubric_pass_rate": rubric_pass_rate,
            }
        )
    return summaries


def format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Result root does not exist: {root}")

    results, errors = load_results(root, args.eval_model, args.judge_model)
    summaries = aggregate(results)
    report = {
        "root": str(root),
        "matched_result_files": len(results),
        "invalid_result_files": errors,
        "summaries": summaries,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if results else 1

    print(f"Root: {root}")
    print(f"Matched judge results: {len(results)}")
    if errors:
        print(f"Invalid judge results: {len(errors)}")

    for summary in summaries:
        print(
            " | ".join(
                [
                    f"eval={summary['evaluated_model']}",
                    f"judge={summary['judge_model']}",
                    f"tasks={summary['tasks']}",
                    f"score={format_number(summary['total_score'])}/{format_number(summary['max_score'])}",
                    f"weighted={summary['weighted_score_rate']:.2%}",
                    f"rubrics={summary['passed_count']}/{summary['total_count']}",
                    f"pass={summary['rubric_pass_rate']:.2%}",
                ]
            )
        )

    if not results:
        print("No matching judge result files found.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
