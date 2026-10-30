"""Validate grounded decision evidence without changing random walks or native scoring."""

from __future__ import annotations

import json
from typing import Any

from task_generation.prompts import QUESTION_DIFFICULTY_POLICY as POLICY

KINDS = {"calculation", "conditional_decision", "cross_source_reconciliation"}


def step_set(value: Any) -> set[int] | None:
    if not isinstance(value, list) or not value or any(type(i) is not int for i in value):
        return None
    return set(value) if len(set(value)) == len(value) else None


def plan_errors(plan: dict, prepared: dict) -> list[str]:
    contract = plan.get("difficulty_contract")
    if not isinstance(contract, dict) or contract.get("version") != POLICY["version"]:
        return ["missing current grounded difficulty_contract"]
    chains = contract.get("chains")
    if not isinstance(chains, list) or len(chains) < POLICY["minimum_evidence_chains"]:
        return ["difficulty_contract needs at least two grounded decision chains"]
    by_step = {row["step"]: row for row in prepared["reference_trajectory"]}
    queries = {i for i, row in by_step.items() if row["operation_type"] == "query"}
    mutations = set(by_step) - queries
    errors, identifiers, signatures = [], set(), set()
    cross_source = 0
    for index, chain in enumerate(chains):
        prefix = f"difficulty chain {index}"
        if not isinstance(chain, dict):
            errors.append(prefix + " is not an object")
            continue
        identifier = chain.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            errors.append(prefix + " needs a unique nonempty id")
        else:
            identifiers.add(identifier)
        if not isinstance(chain.get("kind"), str) or chain["kind"] not in KINDS:
            errors.append(prefix + " has no supported decision kind")
        q, m = step_set(chain.get("query_steps")), step_set(chain.get("mutation_steps"))
        if q is None or m is None or not q <= queries or not m <= mutations:
            errors.append(prefix + " must cite real query and mutation steps")
            continue
        audits = {
            row.get("step"): row
            for row in prepared.get("initial_state_query_evidence", [])
            if isinstance(row, dict)
        }
        for step in q:
            if step >= min(m) and not (
                audits.get(step, {}).get("available") is True
                and audits[step].get("read_only") is True
            ):
                errors.append(
                    prefix
                    + " later query evidence requires a verified read-only initial-state source"
                )
        # Eligibility and arithmetic can be distinct decisions in the same report.
        # Reject a duplicated derivation, not shared query/write step numbers.
        signature = (
            tuple(sorted(q)),
            tuple(sorted(m)),
            json.dumps(chain.get("kind"), sort_keys=True),
            json.dumps(chain.get("rule"), sort_keys=True),
            json.dumps(chain.get("derived_values"), sort_keys=True),
        )
        if signature in signatures:
            errors.append(prefix + " duplicates another evidence chain")
        signatures.add(signature)
        locators = chain.get("source_locators")
        if (
            not isinstance(locators, list)
            or not locators
            or any(not isinstance(s, str) or not s.strip() for s in locators)
        ):
            errors.append(prefix + " needs bounded source locators")
        if not isinstance(chain.get("rule"), str) or not chain["rule"].strip():
            errors.append(prefix + " needs an evidence-grounded rule")
        values = chain.get("derived_values")
        if not isinstance(values, list) or not values:
            errors.append(prefix + " needs hidden derived decision values")
        else:
            for value in values:
                origin = step_set(value.get("source_steps")) if isinstance(value, dict) else None
                if (
                    origin is None
                    or not origin <= q
                    or "value" not in value
                    or not isinstance(value.get("derivation"), str)
                    or not value["derivation"].strip()
                ):
                    errors.append(prefix + " has an unsupported derived-value provenance")
        if chain.get("kind") == "cross_source_reconciliation":
            sources = {
                json.dumps(
                    {key: by_step[i].get(key) for key in ("service", "url", "query", "body")},
                    sort_keys=True,
                )
                for i in q
            }
            if len(sources) < 2:
                errors.append(
                    prefix + " repeats one source instead of reconciling distinct sources"
                )
            else:
                cross_source += 1
    if cross_source < POLICY["minimum_cross_source_chains"]:
        errors.append("difficulty_contract needs at least one real cross-source reconciliation")
    return errors


def verification_errors(
    question: str, plan: dict, verification: dict, rationales: list, scored_indices: set[int]
) -> list[str]:
    audit = verification.get("difficulty_audit")
    if not isinstance(audit, dict) or audit.get("version") != POLICY["version"]:
        return ["semantic verifier omitted current difficulty_audit"]
    errors = []
    for field in (
        "no_answer_leakage",
        "sources_accessible_and_bounded",
        "reference_semantics_preserved",
    ):
        if audit.get(field) is not True:
            errors.append("difficulty audit did not establish " + field)
    for field in ("leaked_answer_spans", "outcome_override_spans", "invented_dependencies"):
        if audit.get(field) != []:
            errors.append("difficulty audit must explicitly rule out " + field)
    chains = {c["id"]: c for c in plan["difficulty_contract"]["chains"]}
    entries = audit.get("validated_chains")
    if not isinstance(entries, list):
        return errors + ["difficulty audit has no validated chains"]
    valid_ids, assertion_union, cross_source = set(), set(), set()
    for index, entry in enumerate(entries):
        prefix = f"difficulty audit chain {index}"
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("chain_id"), str)
            or entry["chain_id"] not in chains
        ):
            errors.append(prefix + " does not identify a planned chain")
            continue
        identifier = entry["chain_id"]
        chain = chains[identifier]
        if identifier in valid_ids:
            errors.append(prefix + " repeats a chain")
        valid_ids.add(identifier)
        if step_set(entry.get("query_steps")) != set(chain["query_steps"]) or step_set(
            entry.get("mutation_steps")
        ) != set(chain["mutation_steps"]):
            errors.append(prefix + " changes the planned evidence or mutation scope")
        quote = entry.get("question_evidence")
        if not isinstance(quote, str) or not quote.strip() or quote not in question:
            errors.append(prefix + " has no exact question evidence")
        if (
            entry.get("question_alone_insufficient") is not True
            or not isinstance(entry.get("counterfactual_test"), str)
            or not entry["counterfactual_test"].strip()
        ):
            errors.append(prefix + " has no source-dependent counterfactual test")
        indices = step_set(entry.get("assertion_indices"))
        if indices is None or not indices <= scored_indices:
            errors.append(prefix + " must map to genuinely scored decision assertions")
            continue
        for i in indices:
            if i >= len(rationales) or rationales[i].get("value_origin") != "discovered_evidence":
                errors.append(
                    prefix + " scores a supplied literal instead of a discovered decision"
                )
        assertion_union.update(indices)
        if chain["kind"] == "cross_source_reconciliation":
            cross_source.add(identifier)
    if len(valid_ids) < POLICY["minimum_evidence_chains"]:
        errors.append("difficulty audit verified fewer than two distinct chains")
    if len(cross_source) < POLICY["minimum_cross_source_chains"]:
        errors.append("difficulty audit did not verify cross-source reconciliation")
    if len(assertion_union) < POLICY["minimum_distinct_scored_decision_assertions"]:
        errors.append("difficulty audit needs two distinct scored decision assertions")
    return errors
