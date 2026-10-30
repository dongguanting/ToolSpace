"""Sample and persist executable workflows from an observed tool graph."""

from pathlib import Path

from automation_environment.io import write_json, write_jsonl
from automation_environment.simulator import sha256_json
from tool_graph.sources import Sources
from tool_graph.walker import RandomWalker, WalkConfig


def generate_walks(
    graph_root: Path, output: Path, count: int, seed: int, config: dict, max_attempts: int = 100
) -> dict:
    if count < 1 or max_attempts < 1:
        raise ValueError("count and max_attempts must be positive")
    if output.exists():
        raise FileExistsError(f"Use a new walk directory: {output}")
    output.mkdir(parents=True)
    sources = Sources(
        graph_root / "graph.json", graph_root / "catalog.json", graph_root / "environment"
    )
    walker = RandomWalker(sources, WalkConfig(**config), seed)
    samples, signatures = [], set()
    for attempt in range(max_attempts):
        sample = walker.generate(len(samples))
        if sample is None:
            continue
        signature = sha256_json([(s["endpoint_id"], s["arguments"]) for s in sample["steps"]])
        if signature in signatures:
            continue
        signatures.add(signature)
        samples.append(sample)
        if len(samples) == count:
            break
    write_jsonl(output / "walks.jsonl", samples)
    report = {
        "requested": count,
        "generated": len(samples),
        "attempts": attempt + 1,
        "seed": seed,
        "config": config,
        "rejections": dict(walker.rejections),
    }
    write_json(output / "summary.json", report)
    return report
