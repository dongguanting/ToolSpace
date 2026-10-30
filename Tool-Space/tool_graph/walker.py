"""State-aware weighted walk with real execution, rollback, and fresh replay."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from automation_environment.simulator import parse_json_value, sha256_json
from automation_environment.snapshots import restored_simulator
from automation_environment.task_catalog import TaskCatalog
from tool_graph.arguments import make_args_automationbench
from tool_graph.sources import Edge, Endpoint, Sources

REFUSAL = re.compile(
    r"not found|not exist|unknown|invalid|malformed|unauthori|forbidden|missing|"
    r"unavailable|denied|failed|failure",
    re.I,
)


@dataclass(frozen=True)
class WalkConfig:
    min_steps: int = 3
    max_steps: int | None = 5
    target_steps_mode: int | None = None
    min_distinct_endpoints: int = 2
    max_distinct_endpoints: int | None = None
    min_distinct_services: int = 1
    max_distinct_services: int | None = None
    min_mutations: int = 0
    target_mutations_mode: int | None = None
    min_distinct_action_endpoints: int = 0
    max_distinct_action_endpoints: int | None = None
    min_queries_before_action: int = 0
    max_queries_before_action: int | None = None
    max_endpoint_repeats: int = 2
    mutation_budget: int | None = 0
    query_only: bool = True
    allow_retry: bool = False
    template_draws: int = 12
    successor_draws: int = 12
    search_expansions: int = 1000
    start_state_draws: int = 30
    stable_ratio: float = 0.6
    cross_service_bonus: float = 1.0
    mutation_bonus: float = 1.0
    action_cluster_bonus: float = 1.0
    post_action_query_penalty: float = 1.0
    post_action_query_probability: float = 0.0
    max_post_action_queries: int = 0
    service_revisit_penalty: float = 1.0
    same_endpoint_bonus: float = 1.0
    task_edge_bonus: float = 1.0
    domain_edge_bonus: float = 1.0
    cross_domain_edge_penalty: float = 1.0
    template_scope: str = "all"
    max_parallel_batch_size: int = 1
    start_endpoint_allowlist: list[str] | None = None
    start_task_allowlist: list[str] | None = None


def refused(observation: Any) -> bool:
    if not isinstance(observation, dict):
        return False
    for key, value in observation.items():
        name = re.sub(r"[^a-z0-9]", "", str(key).lower())
        if name in {"error", "errors"} and value:
            return True
        if name in {"ok", "success"} and value is False:
            return True
        if (
            name in {"status", "code", "result", "message", "detail", "reason"}
            and isinstance(value, str)
            and REFUSAL.search(value)
        ):
            return True
    return False


def substance(value: Any, depth: int = 0) -> int:
    if depth > 7:
        return 0
    if isinstance(value, dict):
        return sum(substance(child, depth + 1) for child in value.values())
    if isinstance(value, list):
        return sum(substance(child, depth + 1) for child in value[:100])
    return int(value not in (None, "", False))


def usable(observation: Any) -> bool:
    return observation is not None and not refused(observation) and substance(observation) > 0


DROPPED = object()


def stable_part(left: Any, right: Any) -> Any:
    if isinstance(left, dict) and isinstance(right, dict):
        result = {}
        for key in left.keys() & right.keys():
            value = stable_part(left[key], right[key])
            if value is not DROPPED:
                result[key] = value
        return result
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return DROPPED
        return [
            None if (value := stable_part(a, b)) is DROPPED else value for a, b in zip(left, right)
        ]
    return left if left == right else DROPPED


def changed_services(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


def weighted_choice(rng: random.Random, items: list[Any], weights: list[float]) -> Any:
    return rng.choices(items, weights=[max(weight, 1e-12) for weight in weights], k=1)[0]


class RandomWalker:
    def __init__(
        self,
        sources: Sources,
        config: WalkConfig,
        seed: int,
        excluded_tasks: set[str] | None = None,
        trace_sink: Any | None = None,
        progress_sink: Any | None = None,
    ):
        self.sources = sources
        self.config = config
        self.seed = seed
        self.rng = random.Random(seed)
        self.excluded_tasks = excluded_tasks or set()
        self.task_catalog = TaskCatalog(json.loads((sources.snapshots / "tasks.json").read_text()))
        self.generated_endpoints: Counter[str] = Counter()
        self.generated_tasks: Counter[str] = Counter()
        self.generated_worlds: Counter[str] = Counter()
        self.rejections: Counter[str] = Counter()
        self._reachable_service_cache: dict[tuple[str, tuple[str, ...]], int] = {}
        self._reachable_mutation_cache: dict[tuple[str, tuple[str, ...]], int] = {}
        self._allowed_services_cache: dict[str, set[str]] = {}
        # Optional diagnostics for visualising the DFS search.  The sampler
        # never reads this state, so leaving ``trace_sink`` unset preserves
        # the original execution and memory behaviour.  A sink may be a
        # callable or any object exposing ``append`` (for example a list).
        self.trace_sink = trace_sink
        self.progress_sink = progress_sink
        self.trace_events: list[dict[str, Any]] = []
        self._trace_event_index = 0
        self._trace_generation = 0
        self._trace_sample_index: int | None = None
        self._last_execute_failure_reason: str | None = None
        self._last_execute_failure_detail: dict[str, Any] | None = None

    def _trace(self, event: str, **payload: Any) -> None:
        """Emit one JSON-serialisable DFS diagnostic event when requested.

        Trace emission is deliberately side-effect free with respect to the
        walk: it does not consume RNG values or inspect mutable simulator
        state beyond values supplied by the caller.  A callback is allowed to
        stream events directly to JSONL; ``trace_events`` remains available to
        callers that want to build a single visualisation document.
        """
        if self.progress_sink is not None:
            self.progress_sink(event, payload)
        if self.trace_sink is None:
            return
        record: dict[str, Any] = {
            "event_index": self._trace_event_index,
            "event": event,
            "generation": self._trace_generation,
        }
        if self._trace_sample_index is not None:
            record["sample_index"] = self._trace_sample_index
        record.update(payload)
        self._trace_event_index += 1
        self.trace_events.append(record)
        sink = self.trace_sink
        if callable(sink):
            sink(record)
        elif hasattr(sink, "append"):
            sink.append(record)

    @staticmethod
    def _trace_preview(value: Any, depth: int = 0) -> Any:
        """Bound large observations before putting them in optional traces."""
        if depth >= 3:
            if isinstance(value, (dict, list)):
                return f"<{type(value).__name__}>"
            text = str(value)
            return text if len(text) <= 240 else text[:237] + "..."
        if isinstance(value, dict):
            items = list(value.items())[:32]
            result = {
                str(key): RandomWalker._trace_preview(child, depth + 1) for key, child in items
            }
            if len(value) > len(items):
                result["..."] = f"{len(value) - len(items)} more"
            return result
        if isinstance(value, list):
            result = [RandomWalker._trace_preview(child, depth + 1) for child in value[:16]]
            if len(value) > len(result):
                result.append(f"... {len(value) - len(result)} more")
            return result
        if isinstance(value, tuple):
            return RandomWalker._trace_preview(list(value), depth)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _allowed_services(self, occurrence: dict[str, Any]) -> set[str]:
        task = occurrence["task_name"]
        if task not in self._allowed_services_cache:
            payload = self.sources.load_snapshot(occurrence["pre_snapshot_id"])
            self._allowed_services_cache[task] = set(
                payload["world_state"].get("meta", {}).get("allowed_services", [])
            )
        return self._allowed_services_cache[task]

    def _reachable_service_count(self, start: str, allowed: set[str]) -> int:
        """Upper bound on service diversity reachable within one configured walk."""
        key = (start, tuple(sorted(allowed)))
        if key in self._reachable_service_cache:
            return self._reachable_service_cache[key]
        seen, frontier = {start}, {start}
        services = {self.sources.endpoints[start].service}
        # Any reachable node has a simple path shorter than the finite graph.
        # This traversal bound is not a maximum generated walk length.
        horizon = self.config.max_steps or len(self.sources.endpoints)
        for _ in range(horizon - 1):
            following = set()
            for endpoint_id in frontier:
                for edge in self.sources.successors(
                    endpoint_id,
                    query_only=self.config.query_only,
                    allow_retry=self.config.allow_retry,
                ):
                    endpoint = self.sources.endpoints[edge.target]
                    if endpoint.service in allowed and endpoint.id not in seen:
                        following.add(endpoint.id)
                        services.add(endpoint.service)
            seen.update(following)
            frontier = following
            if not frontier:
                break
        self._reachable_service_cache[key] = len(services)
        return len(services)

    def _reachable_mutation_count(self, start: str, allowed: set[str]) -> int:
        """Graph-level upper bound on mutations in one configured walk."""
        key = (start, tuple(sorted(allowed)))
        if key in self._reachable_mutation_cache:
            return self._reachable_mutation_cache[key]
        scores = {start: int(self.sources.endpoints[start].kind != "query")}
        unbounded = self.config.max_steps is None
        horizon = (
            len(self.sources.endpoints) * (self.config.min_mutations + 1)
            if unbounded
            else self.config.max_steps
        )
        for _ in range(horizon - 1):
            following: dict[str, int] = {}
            for endpoint_id, score in scores.items():
                for edge in self.sources.successors(
                    endpoint_id,
                    query_only=self.config.query_only,
                    allow_retry=self.config.allow_retry,
                ):
                    endpoint = self.sources.endpoints[edge.target]
                    if endpoint.service in allowed:
                        following[endpoint.id] = max(
                            following.get(endpoint.id, -1),
                            score + int(endpoint.kind != "query"),
                        )
            if not following:
                break
            scores = following
            if unbounded and max(scores.values(), default=0) >= self.config.min_mutations:
                break
        result = max(scores.values(), default=0)
        self._reachable_mutation_cache[key] = result
        return result

    def _choose_start_endpoint(self) -> Endpoint:
        endpoints = self.sources.start_endpoints(
            query_only=self.config.query_only, excluded_tasks=self.excluded_tasks
        )
        if self.config.start_endpoint_allowlist:
            allowed_starts = set(self.config.start_endpoint_allowlist)
            endpoints = [endpoint for endpoint in endpoints if endpoint.id in allowed_starts]
        if self.config.min_distinct_services > 1 or self.config.min_mutations:
            endpoints = [
                endpoint
                for endpoint in endpoints
                if any(
                    endpoint.service in (allowed := self._allowed_services(row))
                    and len(allowed) >= self.config.min_distinct_services
                    and self._reachable_service_count(endpoint.id, allowed)
                    >= self.config.min_distinct_services
                    and self._reachable_mutation_count(endpoint.id, allowed)
                    >= self.config.min_mutations
                    for row in self.sources.usable_occurrences(endpoint.id, self.excluded_tasks)
                )
            ]
        if not endpoints:
            raise ValueError("no start endpoint satisfies the configured service diversity")
        uniform = [1 / len(endpoints)] * len(endpoints)
        frequency = [math.log1p(self.sources.nodes[item.id]["outWeight"]) for item in endpoints]
        novelty = [1 / math.sqrt(1 + self.generated_endpoints[item.id]) for item in endpoints]
        frequency_total, novelty_total = sum(frequency), sum(novelty)
        weights = [
            0.5 * uniform[index]
            + 0.3 * frequency[index] / frequency_total
            + 0.2 * novelty[index] / novelty_total
            for index in range(len(endpoints))
        ]
        self._trace(
            "start_pool",
            candidates=[
                {
                    "endpoint_id": endpoint.id,
                    "service": endpoint.service,
                    "kind": endpoint.kind,
                    "weight": weight,
                }
                for endpoint, weight in zip(endpoints, weights)
            ],
        )
        selected = weighted_choice(self.rng, endpoints, weights)
        self._trace(
            "start_selected",
            endpoint_id=selected.id,
            service=selected.service,
            kind=selected.kind,
        )
        return selected

    def _start_occurrences(self, endpoint: Endpoint) -> list[dict[str, Any]]:
        """Deduplicate by WorldState, balance tasks, then prefer unused worlds."""
        by_world: dict[str, dict[str, Any]] = {}
        for row in self.sources.usable_occurrences(endpoint.id, self.excluded_tasks):
            if (
                self.config.start_task_allowlist
                and row["task_name"] not in self.config.start_task_allowlist
            ):
                continue
            allowed = self._allowed_services(row)
            if endpoint.service not in allowed:
                continue
            if len(allowed) < self.config.min_distinct_services:
                continue
            if (
                self._reachable_service_count(endpoint.id, allowed)
                < self.config.min_distinct_services
            ):
                continue
            if self._reachable_mutation_count(endpoint.id, allowed) < self.config.min_mutations:
                continue
            payload = self.sources.load_snapshot(row["pre_snapshot_id"])
            world = payload["world_state"]
            if not world.get("meta", {}).get("schema_version"):
                continue
            world_hash = payload.get("world_state_sha256") or f"sha256:{sha256_json(world)}"
            by_world.setdefault(world_hash, {**row, "world_state_sha256": world_hash})
        by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in by_world.values():
            by_task[row["task_name"]].append(row)
        tasks = sorted(by_task)
        self.rng.shuffle(tasks)
        tasks.sort(key=lambda task: self.generated_tasks[task])
        ordered = []
        for task in tasks:
            rows = by_task[task]
            self.rng.shuffle(rows)
            rows.sort(key=lambda row: self.generated_worlds[row["world_state_sha256"]])
            ordered.extend(rows)
        return ordered[: self.config.start_state_draws]

    def _template_candidates(
        self, endpoint_id: str, task: str, trajectory: str
    ) -> list[dict[str, Any]]:
        rows = self.sources.templates(endpoint_id, task_name=task, trajectory_id=trajectory)
        if self.config.template_scope == "task":
            rows = [row for row in rows if row["task_name"] == task]
        elif self.config.template_scope == "domain":
            domain = task.split(".", 1)[0]
            rows = [row for row in rows if row["task_name"].split(".", 1)[0] == domain]
        elif self.config.template_scope != "all":
            raise ValueError(f"unknown template_scope: {self.config.template_scope}")
        domain = task.split(".", 1)[0]
        tiers = [
            [row for row in rows if row["trajectory_id"] == trajectory],
            [
                row
                for row in rows
                if row["trajectory_id"] != trajectory and row["task_name"] == task
            ],
            [
                row
                for row in rows
                if row["task_name"] != task and row["task_name"].split(".", 1)[0] == domain
            ],
            [row for row in rows if row["task_name"].split(".", 1)[0] != domain],
        ]
        ordered: list[dict[str, Any]] = []
        for tier in tiers:
            self.rng.shuffle(tier)
            ordered.extend(tier)
        return ordered[: self.config.template_draws]

    def _filled_successor(
        self,
        endpoint: Endpoint,
        task: str,
        trajectory: str,
        observations: list[Any],
        world: dict[str, Any],
        calls: set[tuple[str, str]],
        diagnostics: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        rows = self._template_candidates(endpoint.id, task, trajectory)
        attempts: list[dict[str, Any]] = []
        reason_counts: Counter[str] = Counter()
        for template_index, row in enumerate(rows, 1):
            fill_diagnostic: dict[str, Any] = {}
            filled = make_args_automationbench(
                endpoint,
                row["arguments"],
                observations,
                world,
                self.rng,
                diagnostics=fill_diagnostic,
            )
            if filled is None:
                reason_counts[str(fill_diagnostic.get("code") or "unknown")] += 1
                if len(attempts) < 5:
                    attempts.append(
                        {
                            "template_occurrence_id": row.get("occurrence_id"),
                            **fill_diagnostic,
                        }
                    )
                continue
            arguments, provenance = filled
            marker = (endpoint.id, json.dumps(arguments, sort_keys=True, ensure_ascii=False))
            if marker not in calls:
                provenance["_template_occurrence_id"] = row["occurrence_id"]
                if diagnostics is not None:
                    diagnostics.update(
                        {
                            "status": "filled",
                            "templates_considered": template_index,
                            "selected_template_occurrence_id": row.get("occurrence_id"),
                        }
                    )
                return arguments, provenance
            reason_counts["duplicate_concrete_call"] += 1
            if len(attempts) < 5:
                attempts.append(
                    {
                        "template_occurrence_id": row.get("occurrence_id"),
                        "code": "duplicate_concrete_call",
                        "arguments": self._trace_preview(arguments),
                    }
                )
        if diagnostics is not None:
            diagnostics.update(
                {
                    "status": "no_executable_template",
                    "templates_available": len(rows),
                    "templates_examined": len(rows),
                    "reason_counts": dict(sorted(reason_counts.items())),
                    "examples": attempts,
                }
            )
        return None

    def _transition_weight(
        self,
        edge: Edge,
        provenance: dict[str, Any],
        endpoint_counts: Counter[str],
        mutations: int,
        target_mutations: int,
        task: str,
    ) -> float:
        factor = {"data": 3.0, "state": 2.0, "retry": 0.1}.get(edge.dominant, 1.0)
        sources = [value.get("source") for value in provenance.values() if isinstance(value, dict)]
        parameter_bonus = (
            2.0 if "observation" in sources else 1.25 if "world_state" in sources else 1.0
        )
        novelty = 1 / math.sqrt(
            1 + endpoint_counts[edge.target] + self.generated_endpoints[edge.target]
        )
        source_service = self.sources.endpoints[edge.source].service
        target_service = self.sources.endpoints[edge.target].service
        seen_services = {
            self.sources.endpoints[endpoint_id].service for endpoint_id in endpoint_counts
        }
        if source_service == target_service:
            service_bonus = 1.0
        elif target_service not in seen_services:
            service_bonus = self.config.cross_service_bonus
        else:
            service_bonus = self.config.service_revisit_penalty
        endpoint_bonus = self.config.same_endpoint_bonus if edge.source == edge.target else 1.0
        endpoint = self.sources.endpoints[edge.target]
        mutation_bonus = (
            self.config.mutation_bonus
            if endpoint.kind != "query" and mutations < target_mutations
            else 1.0
        )
        phase_bonus = (
            self.config.action_cluster_bonus
            if mutations and endpoint.kind != "query"
            else self.config.post_action_query_penalty
            if mutations and endpoint.kind == "query"
            else 1.0
        )
        domain = task.split(".", 1)[0]
        if task in edge.tasks:
            semantic_bonus = self.config.task_edge_bonus
        elif any(name.split(".", 1)[0] == domain for name in edge.tasks):
            semantic_bonus = self.config.domain_edge_bonus
        else:
            semantic_bonus = self.config.cross_domain_edge_penalty
        return (
            math.log1p(edge.weight)
            * factor
            * parameter_bonus
            * novelty
            * service_bonus
            * endpoint_bonus
            * mutation_bonus
            * phase_bonus
            * semantic_bonus
        )

    def _execute(
        self, simulator: Any, endpoint: Endpoint, arguments: dict[str, Any]
    ) -> tuple[Any, dict[str, Any], dict[str, Any], list[str]] | None:
        self._last_execute_failure_reason = None
        self._last_execute_failure_detail = None
        before = simulator.snapshot()
        try:
            observation = parse_json_value(simulator.call("api_fetch", arguments))
        except Exception as exc:
            self._last_execute_failure_reason = f"exception:{type(exc).__name__}"
            self._last_execute_failure_detail = {
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:240],
            }
            return None
        after = simulator.snapshot()
        changes = changed_services(before, after)
        if endpoint.kind == "query":
            if not usable(observation):
                self._last_execute_failure_reason = "query_unusable_observation"
                self._last_execute_failure_detail = {
                    "observation_type": type(observation).__name__,
                    "observation_is_null": observation is None,
                    "observation_refused": refused(observation),
                    "observation_substance": substance(observation),
                    "observation_preview": self._trace_preview(observation),
                }
                return None
            if changes:
                self._last_execute_failure_reason = "query_changed_state"
                self._last_execute_failure_detail = {
                    "changed_services": changes,
                    "endpoint_service": endpoint.service,
                }
                return None
        else:
            if refused(observation):
                self._last_execute_failure_reason = "mutation_refused"
                self._last_execute_failure_detail = {
                    "observation_preview": self._trace_preview(observation),
                }
                return None
            if changes and any(service not in {endpoint.service, "meta"} for service in changes):
                self._last_execute_failure_reason = "mutation_cross_service_change"
                self._last_execute_failure_detail = {
                    "changed_services": changes,
                    "endpoint_service": endpoint.service,
                    "unexpected_services": [
                        service for service in changes if service not in {endpoint.service, "meta"}
                    ],
                }
                return None
            if not changes and not (
                isinstance(observation, dict)
                and (
                    observation.get("success") is True
                    or observation.get("ok") is True
                    or observation.get("id")
                )
            ):
                self._last_execute_failure_reason = "mutation_missing_success_signal"
                self._last_execute_failure_detail = {
                    "changed_services": changes,
                    "observation_preview": self._trace_preview(observation),
                }
                return None
        return observation, before, after, changes

    def generate(self, sample_index: int) -> dict[str, Any] | None:
        if self.trace_sink is not None:
            # A trace belongs to one generation attempt.  Keeping it bounded
            # per attempt makes it safe for callers that reuse a walker.
            self.trace_events.clear()
            self._trace_event_index = 0
        self._trace_generation += 1
        self._trace_sample_index = sample_index
        unbounded_steps = self.config.max_steps is None
        if unbounded_steps:
            # Stop at the first state meeting every lower bound. If diversity
            # is still insufficient at min_steps, continue searching deeper.
            target_steps = self.config.min_steps
        elif self.config.target_steps_mode is None:
            target_steps = self.rng.randint(self.config.min_steps, self.config.max_steps)
        else:
            mode = min(
                self.config.max_steps,
                max(self.config.min_steps, self.config.target_steps_mode),
            )
            target_steps = round(
                self.rng.triangular(self.config.min_steps, self.config.max_steps, mode)
            )
        target_mutations: int | None = None
        if self.config.target_mutations_mode is not None:
            mutation_ceiling = min(
                target_steps - self.config.min_queries_before_action,
                self.config.mutation_budget
                if self.config.mutation_budget is not None
                else target_steps,
            )
            if mutation_ceiling < self.config.min_mutations:
                self.rejections["impossible_profile"] += 1
                self._trace(
                    "generate_rejected",
                    reason="impossible_profile",
                    target_steps=target_steps,
                )
                return None
            mode = min(
                mutation_ceiling,
                max(self.config.min_mutations, self.config.target_mutations_mode),
            )
            target_mutations = round(
                self.rng.triangular(self.config.min_mutations, mutation_ceiling, mode)
            )
        post_action_queries = 0
        query_prefix_target = self.config.min_queries_before_action
        if target_mutations is not None:
            available_queries = target_steps - target_mutations
            if (
                self.config.max_post_action_queries > 0
                and self.rng.random() < self.config.post_action_query_probability
            ):
                post_action_queries = min(
                    self.rng.randint(1, self.config.max_post_action_queries),
                    max(0, available_queries - self.config.min_queries_before_action),
                )
            query_prefix_target = available_queries - post_action_queries
        self._trace(
            "generate_start",
            target_steps=target_steps,
            target_mutations=target_mutations,
            query_prefix_target=query_prefix_target,
            post_action_queries=post_action_queries,
        )
        start = self._choose_start_endpoint()
        for occurrence in self._start_occurrences(start):
            initial_id = occurrence["pre_snapshot_id"]
            with restored_simulator(
                self.sources.snapshots, initial_id, self.task_catalog
            ) as simulator:
                initial_world_hash = occurrence["world_state_sha256"]
                self._trace(
                    "start_attempt",
                    endpoint_id=start.id,
                    occurrence_id=occurrence.get("occurrence_id"),
                    task_name=occurrence.get("task_name"),
                    trajectory_id=occurrence.get("trajectory_id"),
                    initial_snapshot_id=initial_id,
                    state_hash=initial_world_hash,
                )
                result = self._execute(simulator, start, occurrence["arguments"])
                if result is None:
                    self.rejections["start_execution"] += 1
                    self._trace(
                        "execute_failure",
                        phase="start",
                        depth=0,
                        endpoint_id=start.id,
                        arguments=self._trace_preview(occurrence["arguments"]),
                        reason=self._last_execute_failure_reason or "rejected",
                        failure_context=self._last_execute_failure_detail,
                        state_hash=initial_world_hash,
                    )
                    continue
                observation, before, after, changes = result
                self._trace(
                    "execute_success",
                    phase="start",
                    depth=0,
                    endpoint_id=start.id,
                    arguments=self._trace_preview(occurrence["arguments"]),
                    observation=self._trace_preview(observation),
                    state_before_sha256=f"sha256:{sha256_json(before)}",
                    state_after_sha256=f"sha256:{sha256_json(after)}",
                    changed_services=changes,
                )
                observations = [observation]
                calls = {
                    (
                        start.id,
                        json.dumps(occurrence["arguments"], sort_keys=True, ensure_ascii=False),
                    )
                }
                endpoint_counts = Counter({start.id: 1})
                steps = [
                    self._step(
                        0,
                        start,
                        occurrence["arguments"],
                        {
                            "*": {
                                "source": "recorded_start",
                                "occurrence_id": occurrence["occurrence_id"],
                            }
                        },
                        observation,
                        before,
                        after,
                        changes,
                        None,
                    )
                ]
                expansions = 0

                def extend(current: str, mutations: int) -> bool:
                    nonlocal expansions
                    depth = len(steps)
                    path = [step["endpoint_id"] for step in steps]
                    self._trace(
                        "enter",
                        depth=depth,
                        current=current,
                        path=path,
                        mutations=mutations,
                        target_steps=target_steps,
                        target_mutations=target_mutations,
                        expansions=expansions,
                    )
                    services = {
                        self.sources.endpoints[endpoint_id].service
                        for endpoint_id in endpoint_counts
                    }
                    action_endpoints = {
                        endpoint_id
                        for endpoint_id in endpoint_counts
                        if self.sources.endpoints[endpoint_id].kind != "query"
                    }
                    if len(steps) >= target_steps:
                        mutation_ok = (
                            mutations == target_mutations
                            if target_mutations is not None
                            else mutations >= self.config.min_mutations
                        )
                        goal_checks = [
                            {
                                "code": "target_steps",
                                "actual": len(steps),
                                "operator": ">=" if unbounded_steps else "==",
                                "required": target_steps,
                                "passed": len(steps) >= target_steps
                                if unbounded_steps
                                else len(steps) == target_steps,
                            },
                            {
                                "code": "min_distinct_endpoints",
                                "actual": len(endpoint_counts),
                                "operator": ">=",
                                "required": self.config.min_distinct_endpoints,
                                "passed": len(endpoint_counts)
                                >= self.config.min_distinct_endpoints,
                            },
                            {
                                "code": "min_distinct_services",
                                "actual": len(services),
                                "operator": ">=",
                                "required": self.config.min_distinct_services,
                                "passed": len(services) >= self.config.min_distinct_services,
                            },
                            {
                                "code": "min_distinct_action_endpoints",
                                "actual": len(action_endpoints),
                                "operator": ">=",
                                "required": self.config.min_distinct_action_endpoints,
                                "passed": len(action_endpoints)
                                >= self.config.min_distinct_action_endpoints,
                            },
                            {
                                "code": "target_mutations"
                                if target_mutations is not None
                                else "min_mutations",
                                "actual": mutations,
                                "operator": "==" if target_mutations is not None else ">=",
                                "required": target_mutations
                                if target_mutations is not None
                                else self.config.min_mutations,
                                "passed": mutation_ok,
                            },
                        ]
                        accepted = (
                            len(endpoint_counts) >= self.config.min_distinct_endpoints
                            and len(services) >= self.config.min_distinct_services
                            and len(action_endpoints) >= self.config.min_distinct_action_endpoints
                            and mutation_ok
                        )
                        self._trace(
                            "goal_check",
                            depth=depth,
                            current=current,
                            path=path,
                            passed=accepted,
                            mutations=mutations,
                            distinct_endpoints=len(endpoint_counts),
                            distinct_services=len(services),
                            distinct_action_endpoints=len(action_endpoints),
                            target_steps=target_steps,
                            target_mutations=target_mutations,
                            services=sorted(services),
                            action_endpoints=sorted(action_endpoints),
                            goal_checks=goal_checks,
                        )
                        if accepted or not unbounded_steps:
                            return accepted
                    remaining = None if unbounded_steps else target_steps - len(steps)
                    mutations_needed = (
                        target_mutations - mutations
                        if target_mutations is not None
                        else max(0, self.config.min_mutations - mutations)
                    )
                    prune_checks = [
                        {
                            "code": "mutation_slots",
                            "actual": mutations_needed,
                            "operator": "<=",
                            "required": remaining,
                            "passed": remaining is None or mutations_needed <= remaining,
                        },
                        {
                            "code": "endpoint_diversity_reachable",
                            "actual": len(endpoint_counts) + remaining
                            if remaining is not None
                            else None,
                            "operator": ">=",
                            "required": self.config.min_distinct_endpoints,
                            "passed": remaining is None
                            or len(endpoint_counts) + remaining
                            >= self.config.min_distinct_endpoints,
                        },
                        {
                            "code": "service_diversity_reachable",
                            "actual": len(services) + remaining if remaining is not None else None,
                            "operator": ">=",
                            "required": self.config.min_distinct_services,
                            "passed": remaining is None
                            or len(services) + remaining >= self.config.min_distinct_services,
                        },
                        {
                            "code": "action_endpoint_diversity_reachable",
                            "actual": len(action_endpoints) + remaining
                            if remaining is not None
                            else None,
                            "operator": ">=",
                            "required": self.config.min_distinct_action_endpoints,
                            "passed": remaining is None
                            or len(action_endpoints) + remaining
                            >= self.config.min_distinct_action_endpoints,
                        },
                        {
                            "code": "search_expansions",
                            "actual": expansions,
                            "operator": "<",
                            "required": self.config.search_expansions,
                            "passed": expansions < self.config.search_expansions,
                        },
                    ]
                    if any(not check["passed"] for check in prune_checks):
                        self._trace(
                            "prune",
                            depth=depth,
                            current=current,
                            path=path,
                            remaining=remaining,
                            mutations=mutations,
                            mutations_needed=mutations_needed,
                            expansions=expansions,
                            distinct_endpoints=len(endpoint_counts),
                            distinct_services=len(services),
                            distinct_action_endpoints=len(action_endpoints),
                            services=sorted(services),
                            action_endpoints=sorted(action_endpoints),
                            prune_checks=prune_checks,
                        )
                        return False
                    candidate_by_call = {}
                    filled_cache = {}
                    fill_diagnostics: dict[str, dict[str, Any]] = {}
                    candidate_filter_counts: Counter[str] = Counter()
                    candidate_rejected_examples: dict[str, list[dict[str, str]]] = defaultdict(list)

                    def reject_candidate(code: str, predecessor_id: str, target_id: str) -> None:
                        candidate_filter_counts[code] += 1
                        examples = candidate_rejected_examples[code]
                        if len(examples) < 3:
                            examples.append({"predecessor": predecessor_id, "target": target_id})

                    world = simulator.snapshot()
                    allowed = set(world.get("meta", {}).get("allowed_services", []))
                    for predecessor in list(endpoint_counts):
                        for edge in self.sources.successors(
                            predecessor,
                            query_only=self.config.query_only,
                            allow_retry=self.config.allow_retry,
                        ):
                            candidate_filter_counts["graph_edges_seen"] += 1
                            endpoint = self.sources.endpoints[edge.target]
                            if endpoint.service not in allowed:
                                reject_candidate("service_not_allowed", predecessor, endpoint.id)
                                continue
                            if endpoint_counts[endpoint.id] >= self.config.max_endpoint_repeats:
                                reject_candidate("endpoint_repeat_limit", predecessor, endpoint.id)
                                continue
                            is_new_endpoint = endpoint.id not in endpoint_counts
                            if (
                                self.config.max_distinct_endpoints is not None
                                and len(endpoint_counts) + int(is_new_endpoint)
                                > self.config.max_distinct_endpoints
                            ):
                                reject_candidate("max_distinct_endpoints", predecessor, endpoint.id)
                                continue
                            is_new_service = endpoint.service not in services
                            if (
                                self.config.max_distinct_services is not None
                                and len(services) + int(is_new_service)
                                > self.config.max_distinct_services
                            ):
                                reject_candidate("max_distinct_services", predecessor, endpoint.id)
                                continue
                            is_action = endpoint.kind != "query"
                            is_new_action = is_action and endpoint.id not in action_endpoints
                            if (
                                self.config.max_distinct_action_endpoints is not None
                                and len(action_endpoints) + int(is_new_action)
                                > self.config.max_distinct_action_endpoints
                            ):
                                reject_candidate(
                                    "max_distinct_action_endpoints", predecessor, endpoint.id
                                )
                                continue
                            query_count = len(steps) - mutations
                            if is_action and not mutations and query_count < query_prefix_target:
                                reject_candidate(
                                    "action_before_query_prefix", predecessor, endpoint.id
                                )
                                continue
                            if (
                                not is_action
                                and not mutations
                                and query_count >= query_prefix_target
                            ):
                                reject_candidate(
                                    "query_prefix_complete_requires_action",
                                    predecessor,
                                    endpoint.id,
                                )
                                continue
                            if (
                                not is_action
                                and mutations
                                and target_mutations is not None
                                and mutations < target_mutations
                            ):
                                reject_candidate(
                                    "query_before_target_mutations", predecessor, endpoint.id
                                )
                                continue
                            if (
                                is_action
                                and target_mutations is not None
                                and mutations >= target_mutations
                            ):
                                reject_candidate(
                                    "target_mutations_reached", predecessor, endpoint.id
                                )
                                continue
                            if (
                                is_action
                                and target_mutations is None
                                and self.config.mutation_budget is not None
                                and mutations >= self.config.mutation_budget
                            ):
                                reject_candidate(
                                    "mutation_budget_reached", predecessor, endpoint.id
                                )
                                continue
                            mutations_after = mutations + int(is_action)
                            remaining_after = remaining - 1 if remaining is not None else None
                            required_mutations = (
                                target_mutations
                                if target_mutations is not None
                                else self.config.min_mutations
                            )
                            if (
                                remaining_after is not None
                                and mutations_after + remaining_after < required_mutations
                            ):
                                reject_candidate(
                                    "insufficient_remaining_mutation_slots",
                                    predecessor,
                                    endpoint.id,
                                )
                                continue
                            if len(steps) < 2 and endpoint.method == "DELETE":
                                reject_candidate("early_delete", predecessor, endpoint.id)
                                continue
                            if endpoint.id not in filled_cache:
                                candidate_filter_counts["parameter_fill_attempts"] += 1
                                fill_diagnostic: dict[str, Any] = {}
                                filled_cache[endpoint.id] = self._filled_successor(
                                    endpoint,
                                    occurrence["task_name"],
                                    occurrence["trajectory_id"],
                                    observations,
                                    world,
                                    calls,
                                    diagnostics=fill_diagnostic,
                                )
                                if not filled_cache[endpoint.id]:
                                    candidate_filter_counts["parameter_fill_failed_endpoints"] += 1
                                    fill_diagnostics[endpoint.id] = fill_diagnostic
                            filled = filled_cache[endpoint.id]
                            if not filled:
                                reject_candidate(
                                    "parameter_fill_failed_edge", predecessor, endpoint.id
                                )
                                continue
                            arguments, provenance = filled
                            weight = self._transition_weight(
                                edge,
                                provenance,
                                endpoint_counts,
                                mutations,
                                target_mutations
                                if target_mutations is not None
                                else self.config.min_mutations,
                                occurrence["task_name"],
                            )
                            key = (
                                endpoint.id,
                                json.dumps(arguments, sort_keys=True, ensure_ascii=False),
                            )
                            previous = candidate_by_call.get(key)
                            candidate = (edge, endpoint, arguments, provenance, weight)
                            if previous is None or weight > previous[4]:
                                if previous is not None:
                                    candidate_filter_counts[
                                        "duplicate_replaced_by_higher_weight"
                                    ] += 1
                                candidate_by_call[key] = candidate
                            else:
                                reject_candidate("duplicate_lower_weight", predecessor, endpoint.id)
                    candidates = list(candidate_by_call.values())
                    candidate_filter_counts["unique_executable_candidates"] = len(candidates)
                    candidate_diagnostics = {
                        "counts": dict(sorted(candidate_filter_counts.items())),
                        "rejected_examples": {
                            code: examples
                            for code, examples in sorted(candidate_rejected_examples.items())
                        },
                        "allowed_services": sorted(allowed),
                        "current_services": sorted(services),
                        "query_count": len(steps) - mutations,
                        "mutations": mutations,
                        "remaining_steps": remaining,
                        "parameter_fill_failures": [
                            {"endpoint_id": endpoint_id, **diagnostic}
                            for endpoint_id, diagnostic in sorted(fill_diagnostics.items())
                        ],
                    }
                    total_weight = sum(max(candidate[4], 1e-12) for candidate in candidates)
                    self._trace(
                        "candidate_pool",
                        depth=depth,
                        current=current,
                        path=path,
                        candidates=[
                            {
                                "predecessor": edge.source,
                                "target": endpoint.id,
                                "edge_weight": edge.weight,
                                "edge_types": edge.types,
                                "weight": weight,
                                "probability": max(weight, 1e-12) / total_weight
                                if total_weight
                                else 0.0,
                                "arguments": self._trace_preview(arguments),
                                "provenance": self._trace_preview(provenance),
                            }
                            for edge, endpoint, arguments, provenance, weight in candidates
                        ],
                        candidate_diagnostics=candidate_diagnostics,
                    )
                    if not candidates:
                        self._trace(
                            "dead_end",
                            depth=depth,
                            current=current,
                            path=path,
                            reason="no_filled_candidates",
                            candidate_diagnostics=candidate_diagnostics,
                        )
                    draw_limit = min(self.config.successor_draws, len(candidates))
                    for draw_index in range(draw_limit):
                        total_weight = sum(max(candidate[4], 1e-12) for candidate in candidates)
                        choice = weighted_choice(
                            self.rng, candidates, [candidate[4] for candidate in candidates]
                        )
                        candidates.remove(choice)
                        edge, endpoint, arguments, provenance, _ = choice
                        self._trace(
                            "draw",
                            depth=depth,
                            current=current,
                            path=path,
                            predecessor=edge.source,
                            target=endpoint.id,
                            selected_weight=choice[4],
                            selected_probability=max(choice[4], 1e-12) / total_weight
                            if total_weight
                            else 0.0,
                            remaining_candidates=[
                                {
                                    "predecessor": item[0].source,
                                    "target": item[1].id,
                                    "weight": item[4],
                                    "probability": max(item[4], 1e-12) / total_weight
                                    if total_weight
                                    else 0.0,
                                }
                                for item in [choice, *candidates]
                            ],
                        )
                        checkpoint = {
                            "world_state": simulator.snapshot(),
                            "runtime_state": simulator.runtime_state(),
                        }
                        checkpoint_state_hash = f"sha256:{sha256_json(checkpoint['world_state'])}"
                        self._trace(
                            "checkpoint",
                            depth=depth,
                            current=current,
                            state_hash=checkpoint_state_hash,
                        )
                        expansions += 1
                        self._trace(
                            "execute_start",
                            phase="successor",
                            depth=depth,
                            predecessor=edge.source,
                            endpoint_id=endpoint.id,
                            arguments=self._trace_preview(arguments),
                        )
                        result = self._execute(simulator, endpoint, arguments)
                        if result is None:
                            self._trace(
                                "execute_failure",
                                phase="successor",
                                depth=depth,
                                predecessor=edge.source,
                                endpoint_id=endpoint.id,
                                arguments=self._trace_preview(arguments),
                                reason=self._last_execute_failure_reason or "rejected",
                                failure_context=self._last_execute_failure_detail,
                            )
                            simulator.restore_complete_state(checkpoint)
                            restored_state_hash = (
                                f"sha256:{sha256_json(simulator.snapshot())}"
                                if self.trace_sink is not None
                                else checkpoint_state_hash
                            )
                            self._trace(
                                "restore",
                                depth=depth,
                                reason="execute_failure",
                                endpoint_id=endpoint.id,
                                failure_reason=self._last_execute_failure_reason or "rejected",
                                failure_context=self._last_execute_failure_detail,
                                checkpoint_state_hash=checkpoint_state_hash,
                                state_hash=restored_state_hash,
                                restore_verified=restored_state_hash == checkpoint_state_hash,
                            )
                            self.rejections["successor_execution"] += 1
                            continue
                        observation, before, after, changes = result
                        self._trace(
                            "execute_success",
                            phase="successor",
                            depth=depth,
                            predecessor=edge.source,
                            endpoint_id=endpoint.id,
                            arguments=self._trace_preview(arguments),
                            observation=self._trace_preview(observation),
                            state_before_sha256=f"sha256:{sha256_json(before)}",
                            state_after_sha256=f"sha256:{sha256_json(after)}",
                            changed_services=changes,
                        )
                        marker = (
                            endpoint.id,
                            json.dumps(arguments, sort_keys=True, ensure_ascii=False),
                        )
                        calls.add(marker)
                        endpoint_counts[endpoint.id] += 1
                        observations.append(observation)
                        realized = any(
                            isinstance(value, dict) and value.get("source") == "observation"
                            for value in provenance.values()
                        )
                        steps.append(
                            self._step(
                                len(steps),
                                endpoint,
                                arguments,
                                provenance,
                                observation,
                                before,
                                after,
                                changes,
                                edge,
                                realized,
                            )
                        )
                        self._trace(
                            "push",
                            depth=len(steps),
                            endpoint_id=endpoint.id,
                            predecessor=edge.source,
                            path=[step["endpoint_id"] for step in steps],
                            dependency_realized=realized,
                        )
                        if extend(endpoint.id, mutations_after):
                            return True
                        self._trace(
                            "dead_end",
                            depth=len(steps),
                            current=endpoint.id,
                            path=[step["endpoint_id"] for step in steps],
                            reason="child_search_failed",
                            parent_endpoint=current,
                            parent_draw_index=draw_index + 1,
                            parent_draw_limit=draw_limit,
                            sibling_candidates_remaining=len(candidates),
                        )
                        steps.pop()
                        observations.pop()
                        endpoint_counts[endpoint.id] -= 1
                        if not endpoint_counts[endpoint.id]:
                            del endpoint_counts[endpoint.id]
                        calls.remove(marker)
                        simulator.restore_complete_state(checkpoint)
                        restored_state_hash = (
                            f"sha256:{sha256_json(simulator.snapshot())}"
                            if self.trace_sink is not None
                            else checkpoint_state_hash
                        )
                        self.rejections["backtrack"] += 1
                        self._trace(
                            "backtrack",
                            depth=len(steps),
                            from_endpoint=endpoint.id,
                            to_endpoint=current,
                            path=[step["endpoint_id"] for step in steps],
                            reason="child_search_failed",
                            parent_draw_index=draw_index + 1,
                            parent_draw_limit=draw_limit,
                            sibling_candidates_remaining=len(candidates),
                            checkpoint_state_hash=checkpoint_state_hash,
                            restored_state_hash=restored_state_hash,
                            restore_verified=restored_state_hash == checkpoint_state_hash,
                        )
                    return False

                found = extend(start.id, int(start.kind != "query"))
                mutations = sum(
                    self.sources.endpoints[step["endpoint_id"]].kind != "query" for step in steps
                )
                if not found and expansions >= self.config.search_expansions:
                    self.rejections["search_budget"] += 1

                service_count = len(
                    {self.sources.endpoints[endpoint_id].service for endpoint_id in endpoint_counts}
                )
                action_endpoint_count = len(
                    {
                        endpoint_id
                        for endpoint_id in endpoint_counts
                        if self.sources.endpoints[endpoint_id].kind != "query"
                    }
                )
                mutation_ok = (
                    mutations == target_mutations
                    if target_mutations is not None
                    else mutations >= self.config.min_mutations
                )
                if (
                    len(steps) < self.config.min_steps
                    or len(endpoint_counts) < self.config.min_distinct_endpoints
                    or service_count < self.config.min_distinct_services
                    or action_endpoint_count < self.config.min_distinct_action_endpoints
                    or not mutation_ok
                ):
                    self.rejections["length_or_diversity"] += 1
                    continue
            materialized = self.materialize(initial_id, steps)
            if materialized is None:
                self.rejections["materialize"] += 1
                self._trace(
                    "materialize_failure",
                    initial_snapshot_id=initial_id,
                    path=[step["endpoint_id"] for step in steps],
                )
                continue
            steps, final_state_hash = materialized
            self._assign_batches(steps)
            replay = self.replay(initial_id, steps)
            if not replay["passed"]:
                reason = replay.get("reason", "unknown")
                self.rejections[f"replay_{reason}"] += 1
                self._trace(
                    "replay_failure",
                    reason=reason,
                    path=[step["endpoint_id"] for step in steps],
                )
                continue
            sequence = [(step["endpoint_id"], step["arguments"]) for step in steps]
            digest = hashlib.sha256(
                json.dumps(
                    [self.seed, sample_index, initial_id, sequence],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()[:16]
            sample = {
                "sample_id": f"rw_{digest}",
                "source": "automationbench_correct_snapshots",
                "source_commit": self.sources.source_commit,
                "graph_sha256": self.sources.graph_sha256,
                "initial_snapshot_id": initial_id,
                "initial_world_state_sha256": initial_world_hash,
                "start_endpoint": start.id,
                "source_task": occurrence["task_name"],
                "source_trajectory_id": occurrence["trajectory_id"],
                "services": sorted(
                    {self.sources.endpoints[endpoint_id].service for endpoint_id in endpoint_counts}
                ),
                "random_seed": self.seed,
                "sample_index": sample_index,
                "target_profile": {
                    "steps": len(steps) if unbounded_steps else target_steps,
                    "mutations": target_mutations,
                    "query_prefix": query_prefix_target,
                    "post_action_queries": post_action_queries
                    if target_mutations is not None
                    else None,
                },
                "steps": steps,
                "final_state_sha256": final_state_hash,
                "replay": replay,
            }
            self._trace(
                "accepted",
                depth=len(steps),
                path=[step["endpoint_id"] for step in steps],
                sample_id=sample["sample_id"],
            )
            if self.trace_sink is not None:
                sample["search_trace"] = list(self.trace_events)
            self.generated_tasks[occurrence["task_name"]] += 1
            self.generated_worlds[initial_world_hash] += 1
            self.generated_endpoints.update(endpoint_counts)
            return sample
        self.rejections["no_start_state"] += 1
        return None

    def _assign_batches(self, steps: list[dict[str, Any]]) -> None:
        """Group independent consecutive queries into reproducible logical batches."""
        if not steps:
            return
        max_size = max(1, self.config.max_parallel_batch_size)
        groups: list[list[int]] = [[0]]
        for index in range(1, len(steps)):
            step = steps[index]
            current_group = groups[-1]
            batch_start = current_group[0]
            observation_dependencies = {
                value["source_step"]
                for value in step.get("argument_provenance", {}).values()
                if isinstance(value, dict)
                and value.get("source") == "observation"
                and isinstance(value.get("source_step"), int)
            }
            parallel = (
                len(current_group) < max_size
                and self.sources.endpoints[step["endpoint_id"]].kind == "query"
                and all(
                    self.sources.endpoints[steps[item]["endpoint_id"]].kind == "query"
                    for item in current_group
                )
                and all(source_step < batch_start for source_step in observation_dependencies)
            )
            if parallel:
                current_group.append(index)
            else:
                groups.append([index])
        for batch_index, group in enumerate(groups):
            for call_index, step_index in enumerate(group):
                steps[step_index]["batch_index"] = batch_index
                steps[step_index]["call_index"] = call_index
                steps[step_index]["batch_size"] = len(group)

    @staticmethod
    def _step(
        index: int,
        endpoint: Endpoint,
        arguments: dict[str, Any],
        provenance: dict[str, Any],
        observation: Any,
        before: dict[str, Any],
        after: dict[str, Any],
        changes: list[str],
        edge: Edge | None,
        realized: bool | None = None,
    ) -> dict[str, Any]:
        return {
            "step": index,
            "endpoint_id": endpoint.id,
            "tool": "api_fetch",
            "arguments": arguments,
            "argument_provenance": provenance,
            "observation": observation,
            "state_before_sha256": f"sha256:{sha256_json(before)}",
            "state_after_sha256": f"sha256:{sha256_json(after)}",
            "changed_services": changes,
            "edge_from_previous": None
            if edge is None
            else {
                "source": edge.source,
                "target": edge.target,
                "weight": edge.weight,
                "types": edge.types,
            },
            "dependency_realized": realized,
        }

    def materialize(
        self, initial_snapshot_id: str, planned_steps: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], str] | None:
        """Execute a discovered chain once without search side effects."""
        rebuilt = copy.deepcopy(planned_steps)
        with restored_simulator(
            self.sources.snapshots, initial_snapshot_id, self.task_catalog
        ) as simulator:
            for step in rebuilt:
                endpoint = self.sources.endpoints[step["endpoint_id"]]
                result = self._execute(simulator, endpoint, copy.deepcopy(step["arguments"]))
                if result is None:
                    return None
                observation, before, after, changes = result
                step.update(
                    observation=observation,
                    state_before_sha256=f"sha256:{sha256_json(before)}",
                    state_after_sha256=f"sha256:{sha256_json(after)}",
                    changed_services=changes,
                )
            final_hash = f"sha256:{sha256_json(simulator.snapshot())}"
        return rebuilt, final_hash

    def replay(self, initial_snapshot_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        stable, exact = 0, 0
        with restored_simulator(
            self.sources.snapshots, initial_snapshot_id, self.task_catalog
        ) as simulator:
            for step in steps:

                def failure(reason: str, ratio: float = 0.0) -> dict[str, Any]:
                    return {
                        "passed": False,
                        "stable_ratio": ratio,
                        "reason": reason,
                        "step": step["step"],
                        "endpoint_id": step["endpoint_id"],
                    }

                endpoint = self.sources.endpoints[step["endpoint_id"]]
                result = self._execute(simulator, endpoint, copy.deepcopy(step["arguments"]))
                if result is None:
                    return failure("execution")
                observation, before, after, changes = result
                if f"sha256:{sha256_json(before)}" != step["state_before_sha256"]:
                    return failure("state_before")
                if f"sha256:{sha256_json(after)}" != step["state_after_sha256"]:
                    return failure("state_after")
                kept = stable_part(step["observation"], observation)
                if kept is DROPPED:
                    return failure("shape")
                held, original = substance(kept), substance(step["observation"])
                stable += held
                exact += original
                if original and (held < 1 or held < self.config.stable_ratio * original):
                    return failure("unstable", held / max(original, 1))
                if changes != step["changed_services"]:
                    return failure("state_diff", held / max(original, 1))
            final_hash = f"sha256:{sha256_json(simulator.snapshot())}"
        return {
            "passed": True,
            "stable_ratio": stable / max(exact, 1),
            "final_state_sha256": final_hash,
        }
