"""Environment → graph → task/verifier synthesis → replay → Pro dataset."""

import hashlib
import json
from pathlib import Path

from automation_environment.io import read_json, read_jsonl, write_json, write_jsonl
from automation_environment.simulator import sha256_json
from task_generation.synthesis import prepare, synthesize
from tool_graph.sources import Sources
from verification.replay import validate_reference


def export_task(
    sample: dict, candidate: dict, validation: dict, sources, job: Path
) -> tuple[dict, dict]:
    from automation_environment.task_catalog import TaskCatalog

    task = TaskCatalog(read_json(sources.snapshots / "tasks.json")).get(
        sample["source_task"].split(".")[0], sample["source_task"]
    )
    digest = sha256_json(
        {
            "sample_id": sample["sample_id"],
            "question": candidate["question"],
            "assertions": candidate["assertions"],
        }
    )
    task_name = f"{task.domain}.pro_{digest[:16]}"
    initial = validation["initial_state"]
    # Sparse state retains services subscribed by this exact checkpoint.
    subscribed = set(initial["meta"]["allowed_services"])
    initial = {k: v for k, v in initial.items() if k == "meta" or k in subscribed}
    info = {
        "task_name": task_name,
        "initial_state": initial,
        "assertions": candidate["assertions"],
        "zapier_tools": task.info["zapier_tools"],
    }
    system = [m for m in task.prompt if m["role"] == "system"]
    row = {
        "example_id": int(digest[:12], 16),
        "prompt": [*system, {"role": "user", "content": candidate["question"]}],
        "answer": "",
        "info": json.dumps(info, ensure_ascii=False),
    }
    pro = {
        "task_id": task_name,
        "question": candidate["question"],
        "source_task": task.task_name,
        "sample_id": sample["sample_id"],
        "verifier": {"assertions": candidate["assertions"]},
        "reference_trajectory": sample["steps"],
        "initial_snapshot_id": sample["initial_snapshot_id"],
        "final_state_sha256": sample["final_state_sha256"],
        "initial_state": initial,
        "initial_runtime": sources.load_snapshot(sample["initial_snapshot_id"])["runtime_state"],
        "source_definition": {
            "example_id": task.example_id,
            "prompt": task.prompt,
            "info": task.info,
        },
        "replay": {
            "passed": True,
            "reward": validation["scoring"]["reward"],
        },
        "job": str(job.relative_to(job.parents[1])),
    }
    return row, pro


def synthesize_tasks(
    graph_root: Path,
    walks_path: Path,
    output: Path,
    client,
    generation_attempts: int = 3,
    resume: bool = False,
) -> dict:
    if output.exists() and not resume:
        raise FileExistsError(f"Use a new task directory or --resume: {output}")
    output.mkdir(parents=True, exist_ok=True)
    sources = Sources(
        graph_root / "graph.json", graph_root / "catalog.json", graph_root / "environment"
    )
    import automationbench

    environment_root = Path(automationbench.__file__).parent
    fingerprint = {
        "walks_sha256": hashlib.sha256(walks_path.read_bytes()).hexdigest(),
        "sources": sources.fingerprints(),
        "model": getattr(client, "model", "offline-demo"),
        "api": getattr(client, "api", "fixture"),
        "reasoning_effort": getattr(client, "reasoning_effort", None),
        "temperature": getattr(client, "temperature", None),
        "max_output_tokens": getattr(client, "max_output_tokens", None),
        "generation_attempts": generation_attempts,
        "base_url": getattr(client, "base_url", None),
        "environment_sha256": {
            str(p.relative_to(environment_root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(environment_root.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".jsonc"}
        },
        "code_sha256": {
            f"{package_root.name}/{p.name}": hashlib.sha256(p.read_bytes()).hexdigest()
            for package_root in (
                Path(__file__).parent.parent / "automation_environment",
                Path(__file__).parent.parent / "tool_graph",
                Path(__file__).parent,
                Path(__file__).parent.parent / "verification",
            )
            for p in sorted(package_root.glob("*.py"))
        },
    }
    config_path = output / "config.json"
    if config_path.exists() and read_json(config_path) != fingerprint:
        raise ValueError("Resume inputs or model settings changed")
    write_json(config_path, fingerprint)
    rows, pro_rows, rejected, errors = [], [], [], []
    samples = read_jsonl(walks_path)
    if not samples:
        raise ValueError("No replay-validated walks were provided")
    for sample in samples:
        identifier = sample["sample_id"]
        if not identifier.startswith("rw_") or any(
            c not in "0123456789abcdef" for c in identifier[3:]
        ):
            raise ValueError("Invalid sample identifier")
        job = output / "jobs" / identifier
        status_path = job / "status.json"
        try:
            if status_path.exists() and read_json(status_path)["outcome"] == "accepted":
                rows.append(read_json(job / "task.json"))
                pro_rows.append(read_json(job / "pro_task.json"))
                continue
            if status_path.exists() and read_json(status_path)["outcome"] == "rejected":
                rejected.append(read_json(status_path))
                continue
            job.mkdir(parents=True, exist_ok=True)
            write_json(job / "sample.json", sample)
            prepared = prepare(sample, sources, job, full_workflow=True)
            write_json(job / "prepared.json", prepared)
            candidate = synthesize(
                prepared,
                client,
                job,
                job / "evidence",
                4,
                4,
                9000,
                generation_attempts,
                False,
                True,
            )
            write_json(job / "candidate.json", candidate)
            if not candidate.get("accepted"):
                status = {
                    "sample_id": identifier,
                    "outcome": "rejected",
                    "stage": candidate.get("stage"),
                    "reasons": candidate.get("validation_errors", []),
                }
                write_json(status_path, status)
                rejected.append(status)
                continue
            validation = validate_reference(sample, sources, candidate["assertions"])
            write_json(job / "replay.json", validation)
            row, pro = export_task(sample, candidate, validation, sources, job)
            write_json(job / "task.json", row)
            write_json(job / "pro_task.json", pro)
            write_json(status_path, {"sample_id": identifier, "outcome": "accepted"})
            rows.append(row)
            pro_rows.append(pro)
        except Exception as error:
            detail = str(error)
            credential = getattr(getattr(client, "client", None), "api_key", None)
            if credential:
                detail = detail.replace(credential, "[REDACTED]")
            status = {
                "sample_id": identifier,
                "outcome": "runtime_error",
                "error_type": type(error).__name__,
                "error": detail[:1600],
            }
            write_json(status_path, status)
            errors.append(status)
        finally:
            print(
                json.dumps(
                    {
                        "accepted": len(rows),
                        "rejected": len(rejected),
                        "runtime_errors": len(errors),
                        "current_sample": identifier,
                    }
                ),
                flush=True,
            )
    assert len({r["example_id"] for r in rows}) == len(rows)
    write_jsonl(output / "automationbench_dataset.jsonl", rows)
    write_jsonl(output / "automation_pro.jsonl", pro_rows)
    write_jsonl(output / "rejected.jsonl", rejected)
    write_jsonl(output / "errors.jsonl", errors)
    summary = {
        "accepted": len(rows),
        "rejected": len(rejected),
        "runtime_errors": len(errors),
        "complete": not errors,
    }
    write_json(output / "summary.json", summary)
    write_json(
        output / "manifest.json",
        {
            "schema_version": "automation-pro-release-v1",
            **summary,
            "inputs": fingerprint,
            "artifacts_sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in output.glob("*.jsonl")
            },
        },
    )
    return summary
