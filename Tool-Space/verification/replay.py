"""Replay generated tasks from their exact checkpoint before export."""

from automation_environment.simulator import (
    LocalAutomationBenchSimulator,
    TaskDefinition,
    parse_json_value,
    sha256_json,
)
from automation_environment.snapshots import restored_simulator
from verification.verifier import score


def validate_reference(sample: dict, sources, assertions: list[dict]) -> dict:
    initial = sources.load_snapshot(sample["initial_snapshot_id"])["world_state"]
    with restored_simulator(sources.snapshots, sample["initial_snapshot_id"]) as simulator:
        for index, step in enumerate(sample["steps"], 1):
            if "sha256:" + sha256_json(simulator.snapshot()) != step["state_before_sha256"]:
                raise ValueError(f"Replay input state mismatch at step {index}")
            observation = parse_json_value(simulator.call("api_fetch", step["arguments"]))
            if observation != step["observation"]:
                raise ValueError(f"Replay observation mismatch at step {index}")
        final = simulator.snapshot()
        runtime = simulator.runtime_state()
    if "sha256:" + sha256_json(final) != sample["final_state_sha256"]:
        raise ValueError("Replay final state mismatch")
    result = score(initial, final, assertions)
    if result["reward"] != 1:
        raise ValueError("Reference assertions must receive full native reward")
    return {
        "passed": True,
        "initial_state": initial,
        "final_state": final,
        "final_runtime": runtime,
        "scoring": result,
    }


def replay_exported(pro: dict) -> dict:
    """Replay a self-contained Pro row without the producer's graph or job folders."""
    definition = pro["source_definition"]
    info = definition["info"]
    task = TaskDefinition(
        info["task_name"].split(".")[0],
        info["task_name"],
        definition["example_id"],
        definition["prompt"],
        info,
    )
    payload = {"world_state": pro["initial_state"], "runtime_state": pro["initial_runtime"]}
    with LocalAutomationBenchSimulator(
        task, pro["initial_runtime"]["deterministic_runtime"]["seed_text"]
    ) as simulator:
        simulator.restore_complete_state(payload)
        for index, step in enumerate(pro["reference_trajectory"], 1):
            if "sha256:" + sha256_json(simulator.snapshot()) != step["state_before_sha256"]:
                raise ValueError(f"Exported checkpoint mismatch at step {index}")
            observation = parse_json_value(simulator.call("api_fetch", step["arguments"]))
            if observation != step["observation"]:
                raise ValueError(f"Exported observation mismatch at step {index}")
        final = simulator.snapshot()
    if "sha256:" + sha256_json(final) != pro["final_state_sha256"]:
        raise ValueError("Exported final state mismatch")
    result = score(pro["initial_state"], final, pro["verifier"]["assertions"])
    if result["reward"] != 1:
        raise ValueError("Exported reference did not receive full score")
    return {"passed": True, "reward": result["reward"], "task_id": pro["task_id"]}
