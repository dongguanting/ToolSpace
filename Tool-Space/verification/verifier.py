"""Thin adapter for AutomationBench's unchanged native partial-credit rubric."""

from automationbench.rubric import partial_credit
from automationbench.schema.world import WorldState


def score(initial: dict, final: dict, assertions: list[dict]) -> dict:
    state = {
        "info": {"assertions": assertions},
        "initial_state": initial,
        "world": WorldState(**final),
    }
    reward = partial_credit(state)
    return {"reward": reward, "assertion_results": state["_assertion_results"]}
