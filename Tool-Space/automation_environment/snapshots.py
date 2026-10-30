"""Content-addressed complete simulator checkpoints."""

import gzip
import json
from contextlib import contextmanager
from pathlib import Path

from automation_environment.io import read_json
from automation_environment.simulator import (
    LocalAutomationBenchSimulator,
    canonical_bytes,
    sha256_json,
)
from automation_environment.task_catalog import TaskCatalog


def save_snapshot(root: Path, world: dict, runtime: dict) -> str:
    payload = {"world_state": world, "runtime_state": runtime}
    digest = sha256_json(payload)
    path = root / "snapshots" / digest[:2] / f"{digest}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
                stream.write(canonical_bytes(payload))
    return "sha256:" + digest


def load_snapshot(root: Path, snapshot_id: str) -> dict:
    digest = snapshot_id.removeprefix("sha256:")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid checkpoint digest")
    path = root / "snapshots" / digest[:2] / f"{digest}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if sha256_json(payload) != digest:
        raise ValueError("Checkpoint content hash mismatch")
    return payload


def save_observation(root: Path, value: object) -> str:
    digest = sha256_json(value)
    path = root / "observations" / digest[:2] / f"{digest}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            stream.write(canonical_bytes({"content": value}))
    return "sha256:" + digest


@contextmanager
def restored_simulator(root: Path, snapshot_id: str, task_catalog: TaskCatalog | None = None):
    payload = load_snapshot(root, snapshot_id)
    identity = payload["runtime_state"]["task"]
    tasks_path = root / "tasks.json"
    catalog = task_catalog or TaskCatalog(read_json(tasks_path) if tasks_path.exists() else None)
    task = catalog.get(identity["domain"], identity["task_name"])
    runtime = payload["runtime_state"]["deterministic_runtime"]
    with LocalAutomationBenchSimulator(task, runtime["seed_text"]) as simulator:
        simulator.restore_complete_state(payload)
        yield simulator
