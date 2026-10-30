"""Infer endpoint relations from observations produced inside Automation worlds."""

import json
from pathlib import Path

from automationbench.tools.api.search import _compute_url, _load_schemas

from automation_environment.catalog import EndpointCatalog
from automation_environment.io import read_json, write_json, write_jsonl
from automation_environment.simulator import (
    LocalAutomationBenchSimulator,
    parse_json_value,
    sha256_json,
)
from automation_environment.snapshots import save_observation, save_snapshot
from automation_environment.task_catalog import TaskCatalog
from tool_graph.inference import infer_edges, load_catalog, load_trajectories


def export_catalog(path: Path) -> dict:
    import automationbench

    root = Path(automationbench.__file__).parent
    digest = sha256_json(
        {str(p.relative_to(root)): sha256_json(p.read_text()) for p in sorted(root.rglob("*.py"))}
    )
    services = []
    for name, schema in sorted(_load_schemas().items()):
        endpoints = []
        for raw in schema["endpoints"]:
            endpoints.append(
                {
                    **raw,
                    "api": name,
                    "url": _compute_url(name, schema["baseUrl"], raw["path"]),
                    "parameter_names": sorted((raw.get("parameters") or {}).keys()),
                }
            )
        services.append({"api": name, "endpoints": endpoints})
    payload = {
        "metadata": {"source_commit": digest, "toolset": "api"},
        "api_endpoint_catalog": services,
    }
    write_json(path, payload)
    return payload


def build_graph(bundle_path: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"Use a new graph directory: {output}")
    bundle = read_json(bundle_path)
    output.mkdir(parents=True)
    catalog_data = export_catalog(output / "catalog.json")
    catalog = EndpointCatalog(output / "catalog.json")
    tasks = bundle["tasks"]
    task_catalog = TaskCatalog(tasks)
    snapshots = output / "environment"
    snapshots.mkdir()
    write_json(snapshots / "tasks.json", tasks)
    occurrences, trajectories, summaries = [], [], []
    for index, workflow in enumerate(bundle["workflows"]):
        task_name = workflow["task_name"]
        task = task_catalog.get(task_name.split(".", 1)[0], task_name)
        trajectory_id = workflow.get("id", f"seed-{index:05d}")
        messages = []
        revealed = set()
        with LocalAutomationBenchSimulator(task, f"workflow:{trajectory_id}") as simulator:
            for step, call in enumerate(workflow["calls"]):
                args = call["arguments"]
                name = call.get("tool", "api_fetch")
                pre = save_snapshot(snapshots, simulator.snapshot(), simulator.runtime_state())
                text = simulator.call(name, args)
                observation = parse_json_value(text)
                if isinstance(observation, dict) and (
                    observation.get("error") or observation.get("ok") is False
                ):
                    raise ValueError(
                        f"Seed workflow failed: {trajectory_id} step {step + 1}: {text}"
                    )
                post = save_snapshot(snapshots, simulator.snapshot(), simulator.runtime_state())
                identifier = f"{trajectory_id}-call-{step:04d}"
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": identifier,
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args)},
                                }
                            ],
                        },
                        {"role": "tool", "tool_call_id": identifier, "content": text},
                    ]
                )
                if name != "api_fetch":
                    continue
                endpoint, confidence = catalog.resolve(
                    str(args.get("method", "GET")), args["url"], args, revealed
                )
                if endpoint is None:
                    raise ValueError(f"Endpoint not in bundled schema: {args['url']}")
                observation_id = save_observation(snapshots, observation)
                occurrences.append(
                    {
                        "occurrence_id": identifier,
                        "endpoint_id": endpoint.id,
                        "trajectory_id": trajectory_id,
                        "task_name": task_name,
                        "domain": task.domain,
                        "arguments": args,
                        "pre_snapshot_id": pre,
                        "post_snapshot_id": post,
                        "recorded_observation_id": observation_id,
                        "local_observation_id": observation_id,
                        "execution_ok": True,
                        "mapping_confidence": confidence,
                    }
                )
        trajectories.append(
            {
                "id": trajectory_id,
                "metadata": {"task_label": {"task_name": task_name}},
                "messages": messages,
            }
        )
        summaries.append(
            {
                "trajectory_id": trajectory_id,
                "semantic_end_state_match": True,
                "errors": [],
                "fatal_error": None,
            }
        )
    write_jsonl(snapshots / "endpoint_occurrences.jsonl", occurrences)
    write_jsonl(snapshots / "trajectory_summaries.jsonl", summaries)
    write_json(
        snapshots / "run_manifest.json",
        {
            "source_commit": catalog_data["metadata"]["source_commit"],
            "workflows": len(trajectories),
            "endpoint_occurrences": len(occurrences),
        },
    )
    normalized = output / "trajectories/seed"
    write_jsonl(normalized / "correct.jsonl", trajectories)
    endpoints, by_method, _ = load_catalog(output / "catalog.json")
    batches, stats, frequencies = load_trajectories(output / "trajectories", by_method)
    edges, counts, inference = infer_edges(batches, frequencies)
    graph = {
        "schema_version": "automation-pro-graph-v1",
        "nodes": [
            {
                "id": e.id,
                "service": e.service,
                "kind": e.kind,
                "calls": counts[e.id],
                "outWeight": sum(edge.weight for edge in edges.values() if edge.source == e.id),
            }
            for e in endpoints
        ],
        "edges": [
            {
                "source": e.source,
                "target": e.target,
                "weight": e.weight,
                "types": dict(e.type_counts),
                "tasks": sorted(e.tasks.items()),
                "evidence": e.examples,
            }
            for _, e in sorted(edges.items())
        ],
        "statistics": {**dict(stats), **dict(inference)},
    }
    write_json(output / "graph.json", graph)
    return {
        "nodes": len(endpoints),
        "edges": len(edges),
        "workflows": len(trajectories),
        "calls": len(occurrences),
    }
