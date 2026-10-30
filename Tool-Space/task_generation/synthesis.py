"""Grounded task synthesis with native assertion and semantic coverage gates."""

from __future__ import annotations

import copy
import gzip
import json
import re
from pathlib import Path
from typing import Any

from automationbench.rubric import partial_credit
from automationbench.rubric.registry import AssertionRegistry
from automationbench.schema.world import WorldState

from automation_environment.simulator import canonical_bytes, parse_json_value, sha256_json
from automation_environment.snapshots import restored_simulator
from automation_environment.task_catalog import TaskCatalog
from task_generation.difficulty import plan_errors as difficulty_plan_errors
from task_generation.difficulty import verification_errors as difficulty_verification_errors
from task_generation.llm import LLMClient
from task_generation.prompts import (
    BATCH_PLANNER_SYSTEM,
    FULL_WORKFLOW_MAX_ASSERTIONS,
    FULL_WORKFLOW_MAX_WORDS,
    JOINT_SYSTEM,
    QUESTION_DIFFICULTY_POLICY,
    STATE_DIFFERENCE_EVIDENCE_SYSTEM,
    SUBTASK_PLANNER_SYSTEM,
    TRAJECTORY_EVIDENCE_SYSTEM,
    VERIFY_SYSTEM,
)
from task_generation.state_assertions import derive_assertions
from tool_graph.sources import Sources

HANDLER_FIELDS: dict[str, set[str]] = {
    "salesforce_field_equals": {
        "type",
        "collection",
        "object_type",
        "object",
        "record_id",
        "field",
        "value",
    },
    "salesforce_field_not_changed": {
        "type",
        "collection",
        "object_type",
        "object",
        "record_id",
        "field",
        "expected_value",
        "value",
    },
    "salesforce_task_exists_with_field": {
        "type",
        "field",
        "value",
        "value_contains",
        "additional_checks",
    },
    "salesforce_task_exists_with_fields": {
        "type",
        "subject_contains",
        "contact_id",
        "related_to_id",
        "assigned_to_id",
        "status",
        "priority",
        "due_date",
        "description_contains",
    },
    "salesforce_task_not_exists_with_fields": {
        "type",
        "subject_contains",
        "contact_id",
        "related_to_id",
        "assigned_to_id",
        "status",
        "priority",
        "due_date",
        "description_contains",
    },
    "slack_message_in_channel": {
        "type",
        "channel",
        "channel_id",
        "channel_name",
        "text_contains",
    },
    "slack_message_not_in_channel": {
        "type",
        "channel",
        "channel_id",
        "channel_name",
        "text_contains",
    },
}


def decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


LIST_ITEM = re.compile(r"(?m)^\s*(?:[-*+•●▪‣]|\d+[.)]|\[\d+\]|[A-Za-z][.)])\s+")


QUESTION_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")


PLACEHOLDER = re.compile(r"(?:\{[A-Z][A-Z0-9_]*\}|<[A-Z][A-Z0-9_ -]*>)")


RELATIONAL_CLAUSE = re.compile(
    r"\b(?:announce(?:d|s)?|join(?:ed|s)?|grew|grows?|change(?:d|s)?|"
    r"promot(?:ed|es?)|creat(?:ed|es?)|post(?:ed|s)?|send|sent|was|were|has|have)\b",
    re.IGNORECASE,
)


VALUE_ORIGINS = {"explicit_requirement", "discovered_evidence", "initial_state_guard"}


def contains_cjk(value: Any) -> bool:
    return bool(CJK.search(json.dumps(value, ensure_ascii=False)))


def compact_assertion_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for schema in schemas:
        row = {
            "type": schema["type"],
            "allowed_fields": schema["allowed_fields"],
        }
        examples = schema.get("style_examples") or []
        if examples:
            row["shape_example"] = examples[0]
        compact.append(row)
    return compact


def style_violations(question: str, *, full_workflow: bool = False) -> list[str]:
    violations = []
    limit = FULL_WORKFLOW_MAX_WORDS if full_workflow else 120
    if len(question.split()) > limit:
        violations.append(f"over_{limit}_words")
    if LIST_ITEM.search(question):
        violations.append("list_or_numbered_steps")
    if QUESTION_HEADING.search(question):
        violations.append("question_heading")
    if not full_workflow and "\n" in question.strip():
        violations.append("not_one_paragraph")
    if PLACEHOLDER.search(question):
        violations.append("placeholder")
    return violations


def official_user_prompt(task_catalog: TaskCatalog, task_name: str) -> str:
    task = task_catalog.get(task_name.split(".", 1)[0], task_name)
    return "\n".join(
        str(message["content"]).strip()
        for message in task.prompt
        if message.get("role") == "user" and message.get("content")
    )


def endpoint_call(endpoint: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    call = {
        "endpoint_id": endpoint.id,
        "service": endpoint.service,
        "operation_type": endpoint.kind,
        "method": arguments.get("method") or endpoint.method,
        "url": arguments.get("url") or endpoint.url,
    }
    if arguments.get("params") not in (None, ""):
        call["query"] = decode(arguments["params"])
    if arguments.get("body") not in (None, ""):
        call["body"] = decode(arguments["body"])
    return call


def endpoint_definition(endpoint: Any) -> dict[str, Any]:
    return {
        "endpoint_id": endpoint.id,
        "service": endpoint.service,
        "operation_type": endpoint.kind,
        "method": endpoint.method,
        "url_template": endpoint.url,
        "description": endpoint.description,
        "parameters": endpoint.parameters,
        "request_schema": endpoint.request,
    }


def write_snapshot(directory: Path, world: dict[str, Any], runtime: dict[str, Any]) -> str:
    complete = {"world_state": world, "runtime_state": runtime}
    digest = sha256_json(complete)
    path = directory / digest[:2] / f"{digest}.json.gz"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "question-synthesis-state-v1",
            "snapshot_id": f"sha256:{digest}",
            "world_state_sha256": f"sha256:{sha256_json(world)}",
            **complete,
        }
        with path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
                stream.write(canonical_bytes(payload))
    return f"sha256:{digest}"


def load_snapshot(state_dir: Path, snapshot_id: str) -> dict[str, Any]:
    digest = snapshot_id.removeprefix("sha256:")
    with gzip.open(state_dir / digest[:2] / f"{digest}.json.gz", "rt", encoding="utf-8") as stream:
        return json.load(stream)


def prepare(
    sample: dict[str, Any], sources: Sources, state_dir: Path, *, full_workflow: bool = False
) -> dict[str, Any]:
    initial_payload = sources.load_snapshot(sample["initial_snapshot_id"])
    initial_world = initial_payload["world_state"]
    initial_runtime = initial_payload.get("runtime_state") or {}
    task_catalog = TaskCatalog(json.loads((sources.snapshots / "tasks.json").read_text()))
    source_task = task_catalog.get(sample["source_task"].split(".", 1)[0], sample["source_task"])
    source_assertions = copy.deepcopy(source_task.info.get("assertions", []))
    trajectory, changed_services = [], set()

    with restored_simulator(
        sources.snapshots, sample["initial_snapshot_id"], task_catalog
    ) as simulator:
        for index, saved in enumerate(sample["steps"], 1):
            endpoint = sources.endpoints[saved["endpoint_id"]]
            before = simulator.snapshot()
            observation = parse_json_value(simulator.call("api_fetch", saved["arguments"]))
            after = simulator.snapshot()
            changed = sorted(
                service
                for service in before.keys() | after.keys()
                if before.get(service) != after.get(service)
            )
            if endpoint.kind == "query" and changed:
                raise ValueError(f"query changed state at step {index}: {endpoint.id}")
            changed_services.update(service for service in changed if service != "meta")
            trajectory.append(
                {
                    "step": index,
                    **endpoint_call(endpoint, saved["arguments"]),
                    "argument_provenance": saved.get("argument_provenance") or {},
                    "observation": observation,
                    "changed_services": changed,
                }
            )
        final_world, final_runtime = simulator.snapshot(), simulator.runtime_state()

    initial_query_evidence = []
    if full_workflow:
        observed = {}
        with restored_simulator(
            sources.snapshots, sample["initial_snapshot_id"], task_catalog
        ) as simulator:
            for index, saved in enumerate(sample["steps"], 1):
                endpoint = sources.endpoints[saved["endpoint_id"]]
                if endpoint.kind != "query":
                    continue
                simulator.restore_complete_state(initial_payload)
                audit = {
                    "step": index,
                    "endpoint_id": endpoint.id,
                    "service": endpoint.service,
                    "exact_saved_query_arguments": saved["arguments"],
                    "read_only": False,
                    "available": False,
                }
                try:
                    observation = parse_json_value(simulator.call("api_fetch", saved["arguments"]))
                    audit["read_only"] = simulator.snapshot() == initial_world
                    error = isinstance(observation, dict) and bool(observation.get("error"))
                    audit["available"] = bool(audit["read_only"] and not error)
                    encoded = json.dumps(observation, sort_keys=True)
                    if encoded in observed:
                        audit["same_initial_observation_as_step"] = observed[encoded]
                    else:
                        audit["initial_observation"] = observation
                        observed[encoded] = index
                except Exception as error:
                    audit["error"] = type(error).__name__ + ": " + str(error)
                initial_query_evidence.append(audit)

    final_hash = f"sha256:{sha256_json(final_world)}"
    if final_hash != sample["final_state_sha256"]:
        raise ValueError(f"fresh final state differs for {sample['sample_id']}")
    initial_state_id = write_snapshot(state_dir, initial_world, initial_runtime)
    final_state_id = write_snapshot(state_dir, final_world, final_runtime)
    state_differences = [
        {"id": f"D{index}", **assertion}
        for index, assertion in enumerate(
            derive_assertions(initial_world, final_world, changed_services), 1
        )
    ]
    if (
        any(sources.endpoints[step["endpoint_id"]].kind != "query" for step in sample["steps"])
        and not state_differences
    ):
        raise ValueError("mutation trajectory produced no state differences")

    endpoint_ids = list(dict.fromkeys(step["endpoint_id"] for step in sample["steps"]))
    endpoint_definitions = [endpoint_definition(sources.endpoints[item]) for item in endpoint_ids]
    source_user_prompt = official_user_prompt(task_catalog, sample["source_task"])
    source_reference = {
        "user_prompt": source_user_prompt,
        "assertions": source_assertions,
    }
    planner_payload = {
        "sample_id": sample["sample_id"],
        "source_task": sample["source_task"],
        "source_task_reference": source_reference,
        "endpoint_definitions": endpoint_definitions,
        "trajectory": trajectory,
        "deterministic_state_differences": state_differences,
    }
    examples_by_type = {}
    for assertion in source_assertions:
        examples_by_type.setdefault(assertion["type"], []).append(assertion)
    if full_workflow:
        # A multi-service task needs the official handlers for its whole scope,
        # rather than only the original task's narrow assertion vocabulary.
        services = {row["service"] for row in trajectory}
        for domain in ("sales", "marketing", "operations", "support", "finance", "hr"):
            task_catalog._load_domain(domain)
            for definition in task_catalog._domains.get(domain, {}).values():
                for assertion in definition.info.get("assertions", []):
                    kind = assertion["type"]
                    if any(kind.startswith(service + "_") for service in services):
                        examples_by_type.setdefault(kind, []).append(assertion)
    source_types = sorted(examples_by_type)
    negative_types = sorted(
        assertion_type
        for assertion_type in source_types
        if AssertionRegistry.is_negative(assertion_type)
    )
    assertion_schemas = []
    for assertion_type in source_types:
        examples = examples_by_type[assertion_type]
        fields = HANDLER_FIELDS.get(
            assertion_type,
            {key for row in examples for key in row},
        )
        assertion_schemas.append(
            {
                "type": assertion_type,
                "allowed_fields": sorted(fields),
                "style_examples": examples[:2],
            }
        )
    return {
        "sample_id": sample["sample_id"],
        "source_task": sample["source_task"],
        "source_trajectory_id": sample["source_trajectory_id"],
        "initial_state_id": initial_state_id,
        "expected_final_state_id": final_state_id,
        "expected_final_world_state_sha256": final_hash,
        "state_differences": state_differences,
        "endpoint_definitions": endpoint_definitions,
        "reference_trajectory": trajectory,
        "initial_state_query_evidence": initial_query_evidence,
        "planner_payload": planner_payload,
        "automationbench_style_reference": source_reference,
        "allowed_assertion_types": source_types,
        "negative_assertion_types": negative_types,
        "allowed_assertion_schemas": assertion_schemas,
        "full_trajectory_characters": len(json.dumps(trajectory, ensure_ascii=False)),
    }


def validate_plan(
    plan: dict[str, Any], prepared: dict[str, Any], extract_coherent_subtask: bool
) -> None:
    if plan.get("acceptable") is not True:
        return
    expected = set(range(1, len(prepared["reference_trajectory"]) + 1))
    coverage = {row.get("step") for row in plan.get("step_coverage", []) if isinstance(row, dict)}
    if coverage != expected:
        raise ValueError(
            f"plan step coverage differs: missing={sorted(expected - coverage)} "
            f"extra={sorted(coverage - expected)}"
        )
    mutation_steps = {
        step["step"]
        for step in prepared["reference_trajectory"]
        if step["operation_type"] != "query"
    }
    planned_actions = {
        row.get("step") for row in plan.get("required_actions", []) if isinstance(row, dict)
    }
    if extract_coherent_subtask:
        if not planned_actions:
            raise ValueError("subtask plan selects no mutation steps")
        if not planned_actions <= mutation_steps:
            raise ValueError(
                f"subtask plan contains non-mutation steps: {sorted(planned_actions - mutation_steps)}"
            )
    elif mutation_steps != planned_actions:
        raise ValueError(f"plan omits mutation steps: {sorted(mutation_steps - planned_actions)}")
    if not extract_coherent_subtask:
        by_step = {row["step"]: row for row in prepared["reference_trajectory"]}
        included = set(planned_actions)
        for goal in plan.get("information_goals", []):
            included.update(goal.get("steps") or [])
        if not included <= set(by_step):
            raise ValueError("plan refers to nonexistent discovery steps")
        required_services = {row["service"] for row in by_step.values()}
        covered_services = {by_step[step]["service"] for step in included}
        if not required_services <= covered_services:
            raise ValueError(f"plan drops services: {sorted(required_services - covered_services)}")
    contract_steps = {
        row.get("step") for row in plan.get("write_contracts", []) if isinstance(row, dict)
    }
    required_contract_steps = planned_actions if extract_coherent_subtask else mutation_steps
    if not required_contract_steps <= contract_steps:
        raise ValueError(
            "plan omits mutation write contracts: "
            f"{sorted(required_contract_steps - contract_steps)}"
        )
    if not isinstance(plan.get("target_scope"), list) or not plan["target_scope"]:
        raise ValueError("plan has no explicit target_scope")
    if not extract_coherent_subtask:
        errors = difficulty_plan_errors(plan, prepared)
        if errors:
            raise ValueError("; ".join(errors))


def _contains_values(assertion: dict[str, Any]) -> list[str]:
    values = []
    for key, value in assertion.items():
        if "contains" not in key.lower():
            continue
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return values


def assertion_shape_errors(
    question: str,
    assertions: Any,
    rationales: Any,
    allowed_schemas: dict[str, set[str]],
    required_negative_types: set[str] | None = None,
    max_assertions: int | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(assertions, list) or not assertions:
        return ["assertions must be a non-empty list"]
    if max_assertions is not None and len(assertions) > max_assertions:
        errors.append(f"over_{max_assertions}_assertions: got {len(assertions)}")
    required_negative_types = required_negative_types or set()
    seen = set()
    for index, assertion in enumerate(assertions):
        if not isinstance(assertion, dict) or not isinstance(assertion.get("type"), str):
            errors.append(f"assertion {index} is not an assertion object")
            continue
        assertion_type = assertion["type"]
        if assertion_type not in allowed_schemas:
            errors.append(f"assertion {index} uses disallowed type {assertion['type']}")
        else:
            unknown = set(assertion) - allowed_schemas[assertion_type]
            if unknown:
                errors.append(f"assertion {index} has unsupported fields {sorted(unknown)}")
        canonical = json.dumps(assertion, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if canonical in seen:
            errors.append(f"assertion {index} duplicates an earlier assertion")
        seen.add(canonical)
        if assertion_type == "slack_message_in_channel":
            text_contains = assertion.get("text_contains")
            if not isinstance(text_contains, list) or len(text_contains) < 2:
                errors.append(
                    f"assertion {index} slack text_contains must group at least two atomic anchors"
                )
        literal_titles = {
            value
            for key, value in assertion.items()
            if key in {"subject_contains", "title_contains", "summary_contains", "name_contains"}
            and isinstance(value, str)
            and " ".join(value.lower().split()) in " ".join(question.lower().split())
        }
        for value in _contains_values(assertion):
            if len(value) > 43 or len(value.split()) > 7 or "\n" in value:
                errors.append(f"assertion {index} contains value is too specific: {value!r}")
            elif (
                len(value.split()) >= 3
                and RELATIONAL_CLAUSE.search(value)
                and value not in literal_titles
            ):
                errors.append(
                    f"assertion {index} contains value resembles generated prose; "
                    f"use atomic anchors instead: {value!r}"
                )
            elif re.search(r"\bstrength\s*:", value, re.IGNORECASE):
                errors.append(
                    f"assertion {index} contains value depends on formatting; "
                    f"use the atomic strength value instead: {value!r}"
                )
    if required_negative_types and not any(
        isinstance(assertion, dict) and assertion.get("type") in required_negative_types
        for assertion in assertions
    ):
        errors.append(
            "at least one official negative assertion is required; allowed negative types: "
            + ", ".join(sorted(required_negative_types))
        )
    if not isinstance(rationales, list) or len(rationales) != len(assertions):
        errors.append("assertion_rationales must align one-to-one with assertions")
    else:
        indexes = {row.get("assertion_index") for row in rationales if isinstance(row, dict)}
        if indexes != set(range(len(assertions))):
            errors.append("assertion_rationales indexes do not cover all assertions exactly")
        normalized_question = " ".join(question.lower().split())
        for index, rationale in enumerate(rationales):
            if not isinstance(rationale, dict):
                errors.append(f"assertion rationale {index} is not an object")
                continue
            evidence = rationale.get("question_evidence")
            if (
                not isinstance(evidence, str)
                or " ".join(evidence.lower().split()) not in normalized_question
            ):
                errors.append(
                    f"assertion rationale {index} question_evidence is not an exact question quote"
                )
            if rationale.get("value_origin") not in VALUE_ORIGINS:
                errors.append(f"assertion rationale {index} has an invalid value_origin")
    if contains_cjk({"question": question, "assertion_rationales": rationales}):
        errors.append("generated natural language contains Chinese text")
    return errors


def state_validation_errors(
    assertions: list[dict[str, Any]],
    initial_world: dict[str, Any],
    final_world: dict[str, Any],
    scored_indices: set[int] | None = None,
) -> list[str]:
    errors = []
    initial_model, final_model = WorldState(**initial_world), WorldState(**final_world)
    for index, assertion in enumerate(assertions):
        try:
            if not AssertionRegistry.check(final_model, assertion):
                errors.append(f"assertion {index} is false in the expected final WorldState")
        except Exception as error:
            errors.append(f"assertion {index} cannot execute: {type(error).__name__}: {error}")
    if errors:
        return errors
    scoring_state = {
        "info": {"assertions": assertions},
        "initial_state": initial_model.model_dump(mode="json"),
        "world": final_model,
    }
    score = partial_credit(scoring_state)
    if scored_indices is not None:
        scored_indices.update(
            index
            for index, row in enumerate(scoring_state.get("_assertion_results", []))
            if not row.get("excluded")
        )
    if score != 1.0:
        errors.append(f"official partial_credit is {score}, expected 1.0")
    if not any(not row.get("excluded") for row in scoring_state.get("_assertion_results", [])):
        errors.append("all assertions are free in the initial state")
    return errors


def evidence_chunks(
    rows: list[dict[str, Any]], max_items: int, max_characters: int
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    characters = 0
    for row in rows:
        row_characters = len(json.dumps(row, ensure_ascii=False))
        if current and (len(current) >= max_items or characters + row_characters > max_characters):
            chunks.append(current)
            current = []
            characters = 0
        current.append(row)
        characters += row_characters
    if current:
        chunks.append(current)
    return chunks


def summarize_evidence(
    prepared: dict[str, Any],
    client: LLMClient,
    cache_dir: Path,
    trajectory_chunk_size: int,
    state_difference_chunk_size: int,
    chunk_characters: int,
) -> dict[str, Any]:
    sample_cache_dir = cache_dir / prepared["sample_id"]
    cache_path = sample_cache_dir / "evidence.json"
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    source_reference = prepared["planner_payload"]["source_task_reference"]
    compact_schemas = compact_assertion_schemas(prepared["allowed_assertion_schemas"])
    trajectory = prepared["reference_trajectory"]
    differences = prepared["state_differences"]
    trajectory_chunks = evidence_chunks(trajectory, trajectory_chunk_size, chunk_characters)
    difference_chunks = evidence_chunks(differences, state_difference_chunk_size, chunk_characters)
    summary_budget = client.max_output_tokens

    sample_cache_dir.mkdir(parents=True, exist_ok=True)
    trajectory_summaries = []
    for index, chunk in enumerate(trajectory_chunks, 1):
        chunk_path = sample_cache_dir / f"trajectory-{index:04d}.json"
        if chunk_path.is_file():
            summary = json.loads(chunk_path.read_text(encoding="utf-8"))
        else:
            summary = client.json(
                TRAJECTORY_EVIDENCE_SYSTEM,
                {
                    "source_user_prompt": source_reference["user_prompt"],
                    "chunk_index": index,
                    "total_chunks": len(trajectory_chunks),
                    "complete_untruncated_steps_in_this_chunk": chunk,
                },
                max_output_tokens=summary_budget,
            )
            temporary = chunk_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(chunk_path)
        rows = summary.get("steps")
        expected = {row["step"] for row in chunk}
        actual = {row.get("step") for row in rows or [] if isinstance(row, dict)}
        if actual != expected:
            raise ValueError(
                f"trajectory evidence chunk {index} must preserve steps "
                f"{sorted(expected)}, got {sorted(item for item in actual if item is not None)}"
            )
        trajectory_summaries.append(summary)

    difference_summaries = []
    for index, chunk in enumerate(difference_chunks, 1):
        chunk_path = sample_cache_dir / f"difference-{index:04d}.json"
        if chunk_path.is_file():
            summary = json.loads(chunk_path.read_text(encoding="utf-8"))
        else:
            summary = client.json(
                STATE_DIFFERENCE_EVIDENCE_SYSTEM,
                {
                    "allowed_assertion_schemas": compact_schemas,
                    "chunk_index": index,
                    "total_chunks": len(difference_chunks),
                    "complete_untruncated_differences_in_this_chunk": chunk,
                },
                max_output_tokens=summary_budget,
            )
            temporary = chunk_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(chunk_path)
        rows = summary.get("differences")
        expected = {row["id"] for row in chunk}
        actual = {row.get("id") for row in rows or [] if isinstance(row, dict)}
        if actual != expected:
            raise ValueError(
                f"state evidence chunk {index} must preserve differences "
                f"{sorted(expected)}, got {sorted(item for item in actual if item is not None)}"
            )
        difference_summaries.append(summary)

    evidence = {
        "schema_version": "1.0",
        "trajectory_steps": len(trajectory),
        "state_differences": len(differences),
        "trajectory_chunks": trajectory_summaries,
        "state_difference_chunks": difference_summaries,
    }
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(cache_path)
    return evidence


def cached_llm_json(
    path: Path, client: LLMClient, system: str, payload: dict[str, Any]
) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    value = client.json(system, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return value


def generation_system_prompts(extract_coherent_subtask: bool) -> tuple[str, str, str]:
    """Share the prose-only presentation rule across planner, writer, and verifier."""
    planner_system = SUBTASK_PLANNER_SYSTEM if extract_coherent_subtask else BATCH_PLANNER_SYSTEM
    joint_system, verify_system = JOINT_SYSTEM, VERIFY_SYSTEM
    if not extract_coherent_subtask:
        from task_generation.prompts import FULL_WORKFLOW_RULES, GROUNDED_DIFFICULTY_RULES

        planner_system += FULL_WORKFLOW_RULES
        joint_system = joint_system.replace(
            "one direct, business-oriented paragraph targeting 60–100 English words, with a hard\n   maximum of 120 English words",
            "a direct business request in natural continuous English prose, with a hard maximum of 250 English words",
        )
        verify_system = verify_system.replace(
            "one natural business paragraph targeting 60–100 English words, with a hard\n   maximum of 120 English words, and has no headings,\n   lists, placeholders",
            "natural continuous English prose of at most 250 English words, with no headings, lists, placeholders",
        )
        joint_system += FULL_WORKFLOW_RULES
        verify_system += FULL_WORKFLOW_RULES
        planner_system += GROUNDED_DIFFICULTY_RULES
        joint_system += GROUNDED_DIFFICULTY_RULES
        verify_system += GROUNDED_DIFFICULTY_RULES
    return planner_system, joint_system, verify_system


def synthesize(
    prepared: dict[str, Any],
    client: LLMClient,
    state_dir: Path,
    evidence_dir: Path,
    trajectory_chunk_size: int,
    state_difference_chunk_size: int,
    chunk_characters: int,
    generation_attempts: int,
    extract_coherent_subtask: bool,
    require_negative_assertion: bool,
) -> dict[str, Any]:
    negative_assertion_required = bool(
        require_negative_assertion and prepared["negative_assertion_types"]
    )
    planner_system, joint_system, verify_system = generation_system_prompts(
        extract_coherent_subtask
    )
    source_reference = prepared["planner_payload"]["source_task_reference"]
    compact_schemas = compact_assertion_schemas(prepared["allowed_assertion_schemas"])
    evidence = summarize_evidence(
        prepared,
        client,
        evidence_dir,
        trajectory_chunk_size,
        state_difference_chunk_size,
        chunk_characters,
    )
    stage_name = (
        "stages" if extract_coherent_subtask else "stages-" + QUESTION_DIFFICULTY_POLICY["version"]
    )
    stage_dir = evidence_dir / prepared["sample_id"] / stage_name
    planner_payload = {
        "sample_id": prepared["sample_id"],
        "source_task": prepared["source_task"],
        "source_user_prompt": source_reference["user_prompt"],
        "trajectory_evidence": evidence["trajectory_chunks"],
        "state_difference_evidence": evidence["state_difference_chunks"],
        "allowed_assertion_schemas": compact_schemas,
    }
    if not extract_coherent_subtask:
        planner_payload["difficulty_policy"] = QUESTION_DIFFICULTY_POLICY
        planner_payload["initial_state_query_evidence"] = prepared.get(
            "initial_state_query_evidence", []
        )
    plan = cached_llm_json(stage_dir / "plan.json", client, planner_system, planner_payload)
    if contains_cjk(plan):
        return {"accepted": False, "stage": "planner_language", "plan": plan}
    try:
        validate_plan(plan, prepared, extract_coherent_subtask)
    except ValueError as error:
        return {
            "accepted": False,
            "stage": "planner_validation",
            "plan": plan,
            "validation_errors": [str(error)],
        }
    if plan.get("acceptable") is not True:
        return {"accepted": False, "stage": "planner", "plan": plan}

    initial_world = load_snapshot(state_dir, prepared["initial_state_id"])["world_state"]
    final_world = load_snapshot(state_dir, prepared["expected_final_state_id"])["world_state"]
    if extract_coherent_subtask:
        mutation_steps = {
            row["step"]
            for row in plan.get("required_actions", [])
            if isinstance(row, dict) and isinstance(row.get("step"), int)
        }
    else:
        mutation_steps = {
            step["step"]
            for step in prepared["reference_trajectory"]
            if step["operation_type"] != "query"
        }
    joint_payload = {
        "extraction_mode": ("coherent_subtask" if extract_coherent_subtask else "all_mutations"),
        "selected_mutation_steps": sorted(mutation_steps),
        "style_reference": {"user_prompt": source_reference["user_prompt"]},
        "allowed_assertion_schemas": compact_schemas,
        "approved_plan": plan,
        "complete_trajectory_evidence": evidence["trajectory_chunks"],
        "initial_state_query_evidence": prepared.get("initial_state_query_evidence", []),
        "state_difference_evidence": evidence["state_difference_chunks"],
        "negative_assertion_requirement": {
            "required": negative_assertion_required,
            "allowed_types": prepared["negative_assertion_types"],
        },
    }
    feedback: list[str] = []
    generated: dict[str, Any] = {}
    verification: dict[str, Any] = {}
    for attempt in range(generation_attempts):
        request = {**joint_payload, "previous_attempt_validation_errors": feedback}
        if generated:
            request["previous_attempt_candidate"] = generated
            if isinstance(generated.get("question"), str):
                request["previous_attempt_question_word_count"] = len(generated["question"].split())
            request["revision_instruction"] = (
                "Revise this candidate using the validation errors. Preserve all required "
                "operations and substantive discoveries; keep every native and semantic gate. "
                "Error indexes refer to this candidate's zero-based assertion indexes."
            )
        generated = cached_llm_json(
            stage_dir / f"generation-{attempt + 1:02d}.json",
            client,
            joint_system,
            request,
        )
        if generated.get("unanswerable") is True:
            return {
                "accepted": False,
                "stage": "joint_generator",
                "plan": plan,
                "generated": generated,
            }
        question = generated.get("question")
        assertions = generated.get("assertions")
        rationales = generated.get("assertion_rationales")
        feedback = []
        if not isinstance(question, str) or len(question.strip()) < 40:
            feedback.append("question must be a non-empty natural request")
        else:
            feedback.extend(style_violations(question, full_workflow=not extract_coherent_subtask))
            forbidden = [
                definition["endpoint_id"] for definition in prepared["endpoint_definitions"]
            ]
            if "api_fetch" in question or any(name in question for name in forbidden):
                feedback.append("question exposes a tool or endpoint name")
        if isinstance(question, str):
            allowed_schemas = {
                row["type"]: set(row["allowed_fields"])
                for row in prepared["allowed_assertion_schemas"]
            }
            feedback.extend(
                assertion_shape_errors(
                    question,
                    assertions,
                    rationales,
                    allowed_schemas,
                    set(prepared["negative_assertion_types"])
                    if negative_assertion_required
                    else None,
                    max_assertions=FULL_WORKFLOW_MAX_ASSERTIONS
                    if not extract_coherent_subtask
                    else None,
                )
            )
        scored_indices: set[int] = set()
        if isinstance(assertions, list) and all(isinstance(row, dict) for row in assertions):
            feedback.extend(
                state_validation_errors(assertions, initial_world, final_world, scored_indices)
            )
        if feedback:
            continue

        question = generated["question"].strip()
        assertions = generated["assertions"]
        verification = cached_llm_json(
            stage_dir / f"verification-{attempt + 1:02d}.json",
            client,
            verify_system,
            {
                "extraction_mode": (
                    "coherent_subtask" if extract_coherent_subtask else "all_mutations"
                ),
                "selected_mutation_steps": sorted(mutation_steps),
                "question": question,
                "assertions": assertions,
                "assertion_rationales": rationales,
                "allowed_assertion_schemas": compact_schemas,
                "approved_plan": plan,
                "complete_trajectory_evidence": evidence["trajectory_chunks"],
                "initial_state_query_evidence": prepared.get("initial_state_query_evidence", []),
                "state_difference_evidence": evidence["state_difference_chunks"],
                "negative_assertion_requirement": {
                    "required": negative_assertion_required,
                    "allowed_types": prepared["negative_assertion_types"],
                },
            },
        )
        covered_steps = set(verification.get("covered_mutation_steps", []))
        covered_assertions = set(verification.get("covered_assertion_indices", []))
        if contains_cjk(verification):
            feedback = ["semantic verifier returned non-English natural language"]
        elif verification.get("accepted") is not True:
            reasons = verification.get("reasons") or ["semantic verifier rejected the contract"]
            feedback = [f"semantic verifier: {reason}" for reason in reasons]
        elif verification.get("leaked_result_values"):
            feedback = ["semantic verifier identified leaked discovery results"]
        elif covered_steps != mutation_steps:
            feedback = [
                f"semantic verifier mutation coverage must be {sorted(mutation_steps)}, "
                f"got {sorted(covered_steps)}"
            ]
        elif covered_assertions != set(range(len(assertions))):
            feedback = [
                "semantic verifier assertion coverage must include every zero-based assertion index"
            ]
        else:
            if not extract_coherent_subtask:
                feedback = difficulty_verification_errors(
                    question, plan, verification, rationales, scored_indices
                )
                if feedback:
                    continue
            return {
                "accepted": True,
                "question": question,
                "assertions": assertions,
                "assertion_rationales": generated["assertion_rationales"],
                "plan": plan,
                "joint_verification": verification,
            }

    return {
        "accepted": False,
        "stage": "joint_validation",
        "plan": plan,
        "generated": generated,
        "verification": verification,
        "validation_errors": feedback,
    }
