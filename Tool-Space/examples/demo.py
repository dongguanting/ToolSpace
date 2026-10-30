"""A synthetic reconciliation workflow with two evidence-dependent decisions."""

import json
from pathlib import Path

from automation_environment.io import write_json

QUESTION = (
    "Please reconcile the current project budget using the Budget and Actuals tabs in the "
    "Project Finance workbook. Calculate the total actual spend and the budget variance, "
    "then add a Project Finance summary row to the Reconciliation tab with Project, "
    "Actual, and Variance values. Post a concise summary with the actual spend and variance "
    "to #finance-review, using the channel listed in the Routing tab. Use the figures in "
    "the workbook rather than assumptions."
)


def assertions() -> list[dict]:
    outcomes = [
        {
            "type": "google_sheets_row_exists",
            "spreadsheet_id": "project-finance",
            "worksheet_id": "summary",
            "cells": {"Project": "Project Finance", "Actual": 1500, "Variance": 500},
        },
        {
            "type": "slack_message_in_channel",
            "channel": "CFINANCE01",
            "text_contains": ["Project Finance", "1500", "500"],
        },
    ]
    return outcomes


def create_bundle(path: Path) -> None:
    tabs = [
        (
            "budget",
            "Budget",
            ["Project", "Budget", "NextRange"],
            [{"Project": "Project Finance", "Budget": 2000, "NextRange": "Actuals!A1:C4"}],
        ),
        (
            "actuals",
            "Actuals",
            ["Project", "Actual", "NextRange"],
            [
                {"Project": "Project Finance", "Actual": 600, "NextRange": "Routing!A1:B4"},
                {"Project": "Project Finance", "Actual": 900, "NextRange": "Routing!A1:B4"},
            ],
        ),
        (
            "routing",
            "Routing",
            ["Project", "Channel"],
            [{"Project": "Project Finance", "Channel": "CFINANCE01"}],
        ),
        ("summary", "Reconciliation", ["Project", "Actual", "Variance"], []),
    ]
    rows = [
        {
            "id": f"{name}-{index}",
            "spreadsheet_id": "project-finance",
            "worksheet_id": name,
            "row_id": index + 2,
            "cells": cells,
        }
        for name, _, _, records in tabs
        for index, cells in enumerate(records)
    ]
    world = {
        "meta": {"current_time": "2026-01-01T00:00:00+00:00"},
        "google_sheets": {
            "spreadsheets": [{"id": "project-finance", "title": "Project Finance"}],
            "worksheets": [
                {
                    "id": name,
                    "spreadsheet_id": "project-finance",
                    "title": title,
                    "headers": headers,
                }
                for name, title, headers, _ in tabs
            ],
            "rows": rows,
        },
        "slack": {"channels": [{"id": "CFINANCE01", "name": "finance-review"}]},
    }
    task = {
        "example_id": 1,
        "prompt": [
            {
                "role": "system",
                "content": "Complete business tasks using the local API tools. Discover schemas with api_search and execute operations with api_fetch.",
            },
            {"role": "user", "content": QUESTION},
        ],
        "answer": "",
        "info": {
            "task_name": "finance.project_reconciliation",
            "initial_state": world,
            "assertions": assertions(),
            "zapier_tools": ["google_sheets", "slack"],
        },
    }
    root = "https://sheets.googleapis.com/v4/spreadsheets/project-finance/values/"
    calls = [
        {
            "arguments": {
                "method": "GET",
                "url": root + tab + ("!A1:B4" if tab == "Routing" else "!A1:C4"),
            }
        }
        for tab in ("Budget", "Actuals", "Routing")
    ]
    calls += [
        {
            "arguments": {
                "method": "POST",
                "url": root + "Reconciliation!A1:C2:append",
                "params": {"valueInputOption": "RAW"},
                "body": {"values": [["Project Finance", 1500, 500]]},
            }
        },
        {
            "arguments": {
                "method": "POST",
                "url": "https://slack.com/api/chat.postMessage",
                "body": {
                    "channel": "CFINANCE01",
                    "text": "Project Finance actual spend 1500; budget variance 500.",
                },
            }
        },
    ]
    calls[3:] = list(reversed(calls[3:]))
    write_json(
        path,
        {
            "schema_version": "automation-pro-input-v1",
            "tasks": [task],
            "workflows": [
                {
                    "id": "project-reconciliation",
                    "task_name": "finance.project_reconciliation",
                    "calls": calls,
                }
            ],
        },
    )


class DemoClient:
    """Deterministic fixture responses; never presented as fresh LLM generation."""

    max_output_tokens = None

    def json(self, system: str, payload: dict, **kwargs) -> dict:
        if "complete_untruncated_steps_in_this_chunk" in payload:
            return {
                "steps": [
                    {**row, "facts": [json.dumps(row["observation"])]}
                    for row in payload["complete_untruncated_steps_in_this_chunk"]
                ]
            }
        if "complete_untruncated_differences_in_this_chunk" in payload:
            return {"differences": payload["complete_untruncated_differences_in_this_chunk"]}
        if "approved_plan" not in payload:
            chains = [
                {
                    "id": "actual",
                    "kind": "calculation",
                    "query_steps": [2],
                    "mutation_steps": [5],
                    "source_locators": ["Project Finance/Actuals"],
                    "rule": "Sum the actual amounts in Actuals",
                    "derived_values": [
                        {"value": 1500, "source_steps": [2], "derivation": "600 + 900"}
                    ],
                },
                {
                    "id": "variance",
                    "kind": "cross_source_reconciliation",
                    "query_steps": [1, 2],
                    "mutation_steps": [4],
                    "source_locators": ["Project Finance/Budget", "Project Finance/Actuals"],
                    "rule": "Subtract actual spend from budget",
                    "derived_values": [
                        {"value": 500, "source_steps": [1, 2], "derivation": "2000 - (600 + 900)"}
                    ],
                },
            ]
            return {
                "acceptable": True,
                "target_scope": ["Project Finance"],
                "required_actions": [{"step": 4}, {"step": 5}],
                "write_contracts": [{"step": 4}, {"step": 5}],
                "information_goals": [{"steps": [1, 2, 3]}],
                "step_coverage": [{"step": n} for n in range(1, 6)],
                "difficulty_contract": {"version": "grounded_decisions_v2", "chains": chains},
            }
        if "question" not in payload:
            return {
                "question": QUESTION,
                "assertions": assertions(),
                "assertion_rationales": [
                    {
                        "assertion_index": 0,
                        "question_evidence": "Calculate the total actual spend and the budget variance",
                        "value_origin": "discovered_evidence",
                    },
                    {
                        "assertion_index": 1,
                        "question_evidence": "Post a concise summary with the actual spend and variance",
                        "value_origin": "discovered_evidence",
                    },
                ],
            }
        return {
            "accepted": True,
            "reasons": [],
            "leaked_result_values": [],
            "covered_mutation_steps": [4, 5],
            "covered_assertion_indices": list(range(len(assertions()))),
            "difficulty_audit": {
                "version": "grounded_decisions_v2",
                "no_answer_leakage": True,
                "sources_accessible_and_bounded": True,
                "reference_semantics_preserved": True,
                "leaked_answer_spans": [],
                "outcome_override_spans": [],
                "invented_dependencies": [],
                "validated_chains": [
                    {
                        "chain_id": "actual",
                        "query_steps": [2],
                        "mutation_steps": [5],
                        "assertion_indices": [0],
                        "question_evidence": "Calculate the total actual spend",
                        "question_alone_insufficient": True,
                        "counterfactual_test": "Changing an Actuals row changes the summary actual",
                    },
                    {
                        "chain_id": "variance",
                        "query_steps": [1, 2],
                        "mutation_steps": [4],
                        "assertion_indices": [1],
                        "question_evidence": "budget variance",
                        "question_alone_insufficient": True,
                        "counterfactual_test": "Changing the Budget tab changes the posted variance",
                    },
                ],
            },
        }

    def close(self):
        pass
