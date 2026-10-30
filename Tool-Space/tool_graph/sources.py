"""Read the dependency graph, endpoint catalog, and complete checkpoints."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from automation_environment.simulator import sha256_json
from automation_environment.snapshots import load_snapshot


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Endpoint:
    id: str
    service: str
    kind: str
    method: str
    url: str
    description: str
    parameters: dict[str, dict[str, Any]]
    request: Any = None


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    weight: int
    types: dict[str, int]
    tasks: dict[str, int]

    @property
    def dominant(self) -> str:
        return max(self.types, key=self.types.get) if self.types else "data"


class Sources:
    def __init__(self, graph_path: Path, catalog_path: Path, snapshots: Path):
        self.graph_path = graph_path.resolve()
        self.catalog_path = catalog_path.resolve()
        self.snapshots = snapshots.resolve()
        graph = json.loads(self.graph_path.read_text(encoding="utf-8"))
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.manifest = json.loads(
            (self.snapshots / "run_manifest.json").read_text(encoding="utf-8")
        )
        summary_path = self.snapshots / "trajectory_summaries.jsonl"
        self.verified_trajectory_ids: set[str] | None = None
        self.excluded_trajectory_ids: set[str] = set()
        if summary_path.is_file():
            summaries = [
                json.loads(line) for line in summary_path.open(encoding="utf-8") if line.strip()
            ]
            self.verified_trajectory_ids = {
                row["trajectory_id"]
                for row in summaries
                if row.get("semantic_end_state_match") is True
                and not row.get("fatal_error")
                and not row.get("errors")
            }
            self.excluded_trajectory_ids = {
                row["trajectory_id"]
                for row in summaries
                if row["trajectory_id"] not in self.verified_trajectory_ids
            }
        catalog_ids = {e["id"] for s in catalog["api_endpoint_catalog"] for e in s["endpoints"]}
        if {n["id"] for n in graph["nodes"]} != catalog_ids:
            raise ValueError("graph nodes and endpoint catalog differ")
        if catalog["metadata"]["source_commit"] != self.manifest["source_commit"]:
            raise ValueError("endpoint catalog and snapshots use different source commits")

        self.nodes = {node["id"]: node for node in graph["nodes"]}
        self.edges: dict[str, list[Edge]] = defaultdict(list)
        for raw in graph["edges"]:
            self.edges[raw["source"]].append(
                Edge(
                    raw["source"],
                    raw["target"],
                    int(raw["weight"]),
                    raw.get("types") or {},
                    dict(raw.get("tasks") or []),
                )
            )
        for source in self.edges:
            self.edges[source].sort(key=lambda edge: edge.target)

        by_id = {
            endpoint["id"]: (service, endpoint)
            for service in catalog["api_endpoint_catalog"]
            for endpoint in service["endpoints"]
        }
        self.endpoints = {
            endpoint_id: Endpoint(
                id=endpoint_id,
                service=raw.get("api") or service["api"],
                kind=self.nodes[endpoint_id]["kind"],
                method=raw["method"].upper(),
                url=raw["url"],
                description=raw.get("description") or "",
                parameters=raw.get("parameters") or {},
                request=raw.get("request"),
            )
            for endpoint_id, (service, raw) in by_id.items()
        }
        self.occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
        with (self.snapshots / "endpoint_occurrences.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if (
                    self.verified_trajectory_ids is not None
                    and row.get("trajectory_id") not in self.verified_trajectory_ids
                ):
                    continue
                if row.get("endpoint_id"):
                    self.occurrences[row["endpoint_id"]].append(row)

    @property
    def source_commit(self) -> str:
        return self.manifest["source_commit"]

    @property
    def graph_sha256(self) -> str:
        return file_sha256(self.graph_path)

    def successors(self, endpoint_id: str, *, query_only: bool, allow_retry: bool) -> list[Edge]:
        return [
            edge
            for edge in self.edges.get(endpoint_id, ())
            if (not query_only or self.endpoints[edge.target].kind == "query")
            and (allow_retry or "retry" not in edge.types)
        ]

    def usable_occurrences(
        self, endpoint_id: str, excluded_tasks: set[str] | None = None
    ) -> list[dict[str, Any]]:
        excluded_tasks = excluded_tasks or set()
        return [
            row
            for row in self.occurrences.get(endpoint_id, ())
            if row.get("execution_ok")
            and row.get("recorded_observation_id")
            and row.get("task_name") not in excluded_tasks
        ]

    def start_endpoints(self, *, query_only: bool, excluded_tasks: set[str]) -> list[Endpoint]:
        return [
            endpoint
            for endpoint in self.endpoints.values()
            if endpoint.kind == "query"
            and self.usable_occurrences(endpoint.id, excluded_tasks)
            and self.successors(endpoint.id, query_only=query_only, allow_retry=False)
        ]

    def templates(
        self, endpoint_id: str, *, task_name: str, trajectory_id: str
    ) -> list[dict[str, Any]]:
        """Return successful real calls, preferring the current trajectory/task."""
        rows = self.usable_occurrences(endpoint_id)
        return sorted(
            rows,
            key=lambda row: (
                row.get("trajectory_id") != trajectory_id,
                row.get("task_name") != task_name,
                row["occurrence_id"],
            ),
        )

    @lru_cache(maxsize=256)
    def load_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return load_snapshot(self.snapshots, snapshot_id)

    @lru_cache(maxsize=512)
    def load_observation(self, observation_id: str) -> Any:
        digest = observation_id.removeprefix("sha256:")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid observation digest")
        path = self.snapshots / "observations" / digest[:2] / f"{digest}.json.gz"
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            content = json.load(stream)["content"]
        if sha256_json(content) != digest:
            raise ValueError("Observation content hash mismatch")
        return content

    def fingerprints(self) -> dict[str, str]:
        return {
            "graph": file_sha256(self.graph_path),
            "endpoint_catalog": file_sha256(self.catalog_path),
            "snapshot_manifest": file_sha256(self.snapshots / "run_manifest.json"),
            "endpoint_occurrences": file_sha256(self.snapshots / "endpoint_occurrences.jsonl"),
            "source_tasks": file_sha256(self.snapshots / "tasks.json"),
            "trajectory_summaries": file_sha256(self.snapshots / "trajectory_summaries.jsonl"),
        }
