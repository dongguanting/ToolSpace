import copy
import json
import socket

import pytest

from automation_environment.io import read_json, read_jsonl
from examples.demo import DemoClient, create_bundle
from run_pipeline import DEMO_WALK
from task_generation.pipeline import synthesize_tasks
from tool_graph.graph import build_graph
from tool_graph.walks import generate_walks
from verification.replay import replay_exported
from verification.verifier import score


@pytest.fixture(scope="module")
def completed_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("pipeline")
    create_bundle(root / "input.json")
    assert build_graph(root / "input.json", root / "graph")["nodes"] == 758
    assert generate_walks(root / "graph", root / "walks", 1, 42, DEMO_WALK)["generated"] == 1
    result = synthesize_tasks(
        root / "graph", root / "walks/walks.jsonl", root / "tasks", DemoClient()
    )
    assert result == {"accepted": 1, "rejected": 0, "runtime_errors": 0, "complete": True}
    return root


def test_reference_is_self_contained(completed_run):
    pro = read_jsonl(completed_run / "tasks/automation_pro.jsonl")[0]
    assert replay_exported(json.loads(json.dumps(pro)))["reward"] == 1
    assert len(pro["reference_trajectory"]) == 5
    changed = copy.deepcopy(pro)
    changed["final_state_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="Exported final state mismatch"):
        replay_exported(changed)
    assert set(pro["verifier"]) == {"assertions"}


def test_verifier_keeps_native_scoring(completed_run):
    pro = read_jsonl(completed_run / "tasks/automation_pro.jsonl")[0]
    replay = read_json(completed_run / "tasks/jobs" / pro["sample_id"] / "replay.json")
    specs = pro["verifier"]["assertions"]
    assert score(pro["initial_state"], pro["initial_state"], specs)["reward"] == 0
    wrong = copy.deepcopy(replay["final_state"])
    row = next(r for r in wrong["google_sheets"]["rows"] if r["worksheet_id"] == "summary")
    row["cells"]["Actual"] = 999
    assert score(pro["initial_state"], wrong, specs)["reward"] < 1
    duplicate = copy.deepcopy(replay["final_state"])
    row = next(r for r in duplicate["google_sheets"]["rows"] if r["worksheet_id"] == "summary")
    duplicate["google_sheets"]["rows"].append(
        {**copy.deepcopy(row), "id": "duplicate", "row_id": 99}
    )
    result = score(pro["initial_state"], duplicate, specs)
    assert result["reward"] == 1  # The native existential assertions permit duplicates.


def test_verifier_allows_equivalent_prose(completed_run):
    pro = read_jsonl(completed_run / "tasks/automation_pro.jsonl")[0]
    replay = read_json(completed_run / "tasks/jobs" / pro["sample_id"] / "replay.json")
    final = copy.deepcopy(replay["final_state"])
    final["slack"]["messages"][-1]["text"] = (
        "For Project Finance, actual spending is 1500 and the remaining variance is 500."
    )
    assert score(pro["initial_state"], final, pro["verifier"]["assertions"])["reward"] == 1


def test_resume_does_not_call_model(completed_run):
    class NoCalls(DemoClient):
        def json(self, *args, **kwargs):
            raise AssertionError("Completed tasks must not resample")

    result = synthesize_tasks(
        completed_run / "graph",
        completed_run / "walks/walks.jsonl",
        completed_run / "tasks",
        NoCalls(),
        resume=True,
    )
    assert result["accepted"] == 1


def test_offline_mode_needs_no_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline pipeline tried to connect to the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    create_bundle(tmp_path / "input.json")
    build_graph(tmp_path / "input.json", tmp_path / "graph")
    generate_walks(tmp_path / "graph", tmp_path / "walks", 1, 42, DEMO_WALK)
    assert (
        synthesize_tasks(
            tmp_path / "graph", tmp_path / "walks/walks.jsonl", tmp_path / "tasks", DemoClient()
        )["accepted"]
        == 1
    )


@pytest.mark.parametrize("accepted", [True, False])
def test_pipeline_entry_runs_all_stages(tmp_path, monkeypatch, accepted):
    import sys

    import run_pipeline

    closed = []

    class FixtureModel(DemoClient):
        def __init__(self, *args, **kwargs):
            pass

        def json(self, system, payload, **kwargs):
            response = super().json(system, payload, **kwargs)
            if "acceptable" in response and not accepted:
                response.update(acceptable=False, rejection_reason="Fixture rejection")
            return response

        def close(self):
            closed.append(True)

    def forbidden(*args, **kwargs):
        raise AssertionError("Fixture pipeline tried to connect to the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(run_pipeline, "LLMClient", FixtureModel)
    create_bundle(tmp_path / "input.json")
    (tmp_path / "walk.json").write_text(json.dumps(DEMO_WALK))
    output = tmp_path / "output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_pipeline.py",
            "run",
            "--input",
            str(tmp_path / "input.json"),
            "--walk-config",
            str(tmp_path / "walk.json"),
            "--output",
            str(output),
            "--model",
            "fixture",
        ],
    )
    assert run_pipeline.main() == (0 if accepted else 2)
    assert closed == [True]
    summary = read_json(output / "tasks/summary.json")
    assert summary["accepted"] == int(accepted)
    if accepted:
        assert read_json(output / "tasks/independent_validation.json")["verified_tasks"] == 1
        assert read_json(output / "validation.json")["passed"] is True
    else:
        assert summary["rejected"] == 1
        assert not (output / "validation.json").exists()
