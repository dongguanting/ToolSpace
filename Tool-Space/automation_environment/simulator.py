"""Small, model-free adapter around AutomationBench's in-process API simulator.

This intentionally does not instantiate the benchmark runner or ``verifiers``.
Only the task definitions, WorldState schema, and the three API tools are used.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import sys
import time as stdlib_time
import types
import uuid as stdlib_uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime as RealDateTime
from datetime import timedelta, timezone
from typing import Any


def strip_none_values(obj: Any) -> Any:
    """Undo HuggingFace Dataset's cross-row ``None`` schema padding."""
    if isinstance(obj, dict):
        return {k: strip_none_values(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [strip_none_values(v) for v in obj if v is not None]
    return obj


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _tuple_tree_to_lists(value: Any) -> Any:
    """Convert ``random.getstate()`` into canonical-JSON-compatible data."""
    if isinstance(value, tuple):
        return [_tuple_tree_to_lists(child) for child in value]
    if isinstance(value, list):
        return [_tuple_tree_to_lists(child) for child in value]
    return value


def _list_tree_to_tuples(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_list_tree_to_tuples(child) for child in value)
    return value


def parse_json_value(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


@dataclass(frozen=True)
class TaskDefinition:
    domain: str
    task_name: str
    example_id: Any
    prompt: list[dict[str, Any]]
    info: dict[str, Any]

    @property
    def initial_state(self) -> dict[str, Any]:
        return strip_none_values(copy.deepcopy(self.info.get("initial_state", {})))

    @property
    def contract_sha256(self) -> str:
        return sha256_json(
            {
                "example_id": self.example_id,
                "prompt": self.prompt,
                "info": self.info,
            }
        )


class _UUIDProxy:
    def __init__(self, generator):
        self._generator = generator

    def uuid4(self):
        return self._generator()

    def __getattr__(self, name: str) -> Any:
        return getattr(stdlib_uuid, name)


class _TimeProxy:
    def __init__(self, clock: "DeterministicRuntime"):
        self._clock = clock

    def time(self) -> float:
        return self._clock.now().timestamp()

    def __getattr__(self, name: str) -> Any:
        return getattr(stdlib_time, name)


class DeterministicRuntime(AbstractContextManager["DeterministicRuntime"]):
    """Freeze tool time and UUID generation without editing upstream source."""

    def __init__(self, base_time: RealDateTime, seed_text: str):
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)
        self.base_time = base_time.astimezone(timezone.utc)
        self.seed_text = seed_text
        self.call_ordinal = 0
        self.uuid_ordinal = 0
        self._patches: list[tuple[types.ModuleType, str, Any]] = []
        self._random_state = None
        clock = self

        class FrozenDateTime(RealDateTime):
            @classmethod
            def now(cls, tz=None):
                value = clock.now()
                if tz is None:
                    return value.replace(tzinfo=None)
                return value.astimezone(tz)

            @classmethod
            def utcnow(cls):
                return clock.now().replace(tzinfo=None)

        self.frozen_datetime = FrozenDateTime

    def now(self) -> RealDateTime:
        return self.base_time + timedelta(milliseconds=self.call_ordinal)

    def tick(self) -> None:
        self.call_ordinal += 1

    def uuid4(self) -> stdlib_uuid.UUID:
        self.uuid_ordinal += 1
        digest = hashlib.sha256(
            f"{self.seed_text}:uuid:{self.uuid_ordinal}".encode("utf-8")
        ).digest()[:16]
        return stdlib_uuid.UUID(bytes=digest, version=4)

    def __enter__(self) -> "DeterministicRuntime":
        self._random_state = random.getstate()
        random.seed(int(hashlib.sha256(self.seed_text.encode()).hexdigest()[:16], 16))
        uuid_proxy = _UUIDProxy(self.uuid4)
        time_proxy = _TimeProxy(self)
        # Some implementations import ``time`` inside the endpoint function
        # or ``uuid`` after module globals have already been patched. Cover
        # those late imports too (Slack and Salesforce are examples).
        for module_name, module in sorted(sys.modules.items()):
            if not module_name.startswith("automationbench.") or not isinstance(
                module, types.ModuleType
            ):
                continue
            namespace = vars(module)
            if namespace.get("datetime") is RealDateTime:
                self._set(module, "datetime", self.frozen_datetime)
            if namespace.get("uuid") is stdlib_uuid:
                self._set(module, "uuid", uuid_proxy)
            if namespace.get("uuid4") is stdlib_uuid.uuid4:
                self._set(module, "uuid4", self.uuid4)
            if namespace.get("time") is stdlib_time:
                self._set(module, "time", time_proxy)
        self._set(stdlib_time, "time", time_proxy.time)
        self._set(stdlib_uuid, "uuid4", self.uuid4)
        return self

    def _set(self, module: types.ModuleType, name: str, value: Any) -> None:
        self._patches.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    def __exit__(self, exc_type, exc, tb) -> None:
        for module, name, original in reversed(self._patches):
            setattr(module, name, original)
        self._patches.clear()
        if self._random_state is not None:
            random.setstate(self._random_state)


def _parse_iso(value: str) -> RealDateTime | None:
    if "T" not in value:
        return None
    try:
        parsed = RealDateTime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class LocalAutomationBenchSimulator:
    """Replay API tool calls against one task's WorldState."""

    def __init__(
        self,
        task: TaskDefinition,
        seed_text: str,
    ):
        from automationbench.schema.world import WorldState
        from automationbench.tools.api import api_fetch, api_search, base64_encode

        self.WorldState = WorldState
        self.api_fetch = api_fetch
        self.api_search = api_search
        self.base64_encode = base64_encode
        self.task = task
        initial_state = task.initial_state
        initial_state.setdefault("meta", {})
        base_time = _parse_iso(initial_state["meta"].get("current_time", ""))
        if base_time is None:
            base_time = RealDateTime(2026, 1, 1, tzinfo=timezone.utc)
        self.runtime = DeterministicRuntime(base_time, seed_text)
        self.raw_initial_state = initial_state

    def __enter__(self) -> "LocalAutomationBenchSimulator":
        # Simulator credentials must never enable a live ChatGPT integration.
        for key in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
            "OPENAI_ORG_ID",
            "OPENAI_PROJECT_ID",
        ):
            os.environ.pop(key, None)
        self.runtime.__enter__()
        allowed = compute_allowed_services(
            self.raw_initial_state,
            self.task.info.get("assertions", []),
            self.task.info.get("zapier_tools", []),
            self.WorldState,
        )
        self.world = self.WorldState(**self.raw_initial_state)
        self.world.meta.allowed_services = allowed
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.runtime.__exit__(exc_type, exc, tb)

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "api_fetch":
            self.runtime.tick()
            result = self.api_fetch(world=self.world, **arguments)
            return result
        if name == "api_search":
            return self.api_search(**arguments)
        if name == "base64_encode":
            return self.base64_encode(**arguments)
        raise KeyError(f"unsupported tool in API trajectory: {name}")

    def snapshot(self) -> dict[str, Any]:
        return self.world.model_dump(mode="json")

    def runtime_state(self) -> dict[str, Any]:
        """Serialize every state component needed to continue from a snapshot.

        ``WorldState.model_dump`` contains all declared state for the 47 services.
        Two upstream implementations additionally mutate private attributes, and
        the deterministic replay adapter has clock/UUID/PRNG cursors of its own.
        They are deliberately stored beside, rather than inside, WorldState.
        """
        private_service_state: dict[str, Any] = {}
        offline_jobs = getattr(self.world.google_ads, "_offline_jobs", None)
        if offline_jobs:
            private_service_state["google_ads._offline_jobs"] = copy.deepcopy(offline_jobs)
        updated_rows = getattr(self.world.google_sheets, "_updated_row_keys", None)
        if updated_rows:
            private_service_state["google_sheets._updated_row_keys"] = sorted(updated_rows)
        return {
            "schema_version": "1.0",
            "task": {
                "domain": self.task.domain,
                "task_name": self.task.task_name,
                "contract_sha256": self.task.contract_sha256,
            },
            "deterministic_runtime": {
                "base_time": self.runtime.base_time.isoformat(),
                "seed_text": self.runtime.seed_text,
                "call_ordinal": self.runtime.call_ordinal,
                "uuid_ordinal": self.runtime.uuid_ordinal,
                "python_random_state": _tuple_tree_to_lists(random.getstate()),
            },
            "private_service_state": private_service_state,
        }

    def restore_complete_state(self, payload: dict[str, Any]) -> None:
        """Replace the live environment with one exported complete snapshot.

        The simulator must be active (inside its context manager), because the
        global deterministic runtime patches must remain installed while calls
        continue.
        """
        world_state = payload["world_state"]
        runtime_state = payload.get("runtime_state") or {}
        snapshot_task = runtime_state.get("task") or {}
        if snapshot_task and (
            snapshot_task.get("domain") != self.task.domain
            or snapshot_task.get("task_name") != self.task.task_name
            or snapshot_task.get("contract_sha256") != self.task.contract_sha256
        ):
            raise ValueError(
                "snapshot task contract does not match the simulator task: "
                f"{snapshot_task.get('domain')}/{snapshot_task.get('task_name')}"
            )
        self.world = self.WorldState(**copy.deepcopy(world_state))
        restore_runtime_state(self.world, runtime_state)

        deterministic = runtime_state.get("deterministic_runtime") or {}
        base_time = _parse_iso(str(deterministic.get("base_time") or ""))
        if base_time is not None:
            self.runtime.base_time = base_time
        if "seed_text" in deterministic:
            self.runtime.seed_text = str(deterministic["seed_text"])
        self.runtime.call_ordinal = int(deterministic.get("call_ordinal", 0))
        self.runtime.uuid_ordinal = int(deterministic.get("uuid_ordinal", 0))
        if "python_random_state" in deterministic:
            random.setstate(_list_tree_to_tuples(deterministic["python_random_state"]))


def restore_runtime_state(world: Any, runtime_state: dict[str, Any]) -> None:
    """Restore the private sidecar emitted by ``runtime_state()``."""
    private = runtime_state.get("private_service_state", {})
    if "google_ads._offline_jobs" in private:
        object.__setattr__(
            world.google_ads,
            "_offline_jobs",
            copy.deepcopy(private["google_ads._offline_jobs"]),
        )
    if "google_sheets._updated_row_keys" in private:
        object.__setattr__(
            world.google_sheets,
            "_updated_row_keys",
            set(private["google_sheets._updated_row_keys"]),
        )


def compute_allowed_services(
    initial_state: dict[str, Any],
    assertions: list[dict[str, Any]],
    zapier_tools: list[str],
    world_state_class,
) -> list[str]:
    service_fields = sorted(
        (str(field) for field in world_state_class.model_fields if field != "meta"),
        key=len,
        reverse=True,
    )

    def service_for_name(name: str) -> str | None:
        for field in service_fields:
            if name == field or name.startswith(field + "_"):
                return field
        return None

    allowed: set[str] = {
        key for key in initial_state if key != "meta" and key in world_state_class.model_fields
    }
    for assertion in assertions or ():
        service = service_for_name(str(assertion.get("type", "")))
        if service:
            allowed.add(service)
    for tool_name in zapier_tools or ():
        service = service_for_name(tool_name)
        if service:
            allowed.add(service)
    return sorted(allowed)
