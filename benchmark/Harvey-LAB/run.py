from __future__ import annotations
import json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from common import arguments, select, output, write_json, summarize

from concurrent.futures import ThreadPoolExecutor


def main():
    args = arguments("Harvey LAB fixed proportional sample, 120 tasks")
    manifest = json.loads((HERE / "tasks.json").read_text())
    assert len(manifest["tasks"]) == 120
    tasks = select(manifest["tasks"], args, key=lambda x: x["task_id"])
    source = HERE / "source"
    task_root = Path(os.environ["RUNTIME"]) / "Harvey-LAB/tasks"
    if args.check_only:
        import hashlib

        for t in tasks:
            p = task_root / t["task_id"] / "task.json"
            if hashlib.sha256(p.read_bytes()).hexdigest() != t["task_json_sha256"]:
                raise ValueError("Task checksum mismatch: " + t["task_id"])
        print(f"Harvey: {len(tasks)}/120 task definitions valid")
        return 0
    if not os.getenv("BOYUE_API_KEY"):
        raise SystemExit("Set BOYUE_API_KEY for the GLM-5.2 judge")
    root = output("Harvey-LAB", args)
    sys.path.insert(0, str(source))
    import harness.run as hrun
    from harness.agent_loop import run_agent
    from harness.tools import ToolExecutor, get_all_tool_definitions
    from sandbox.sandbox import Sandbox
    import evaluation.run_eval as evaluator
    import evaluation.scoring as scoring
    from adapter import (
        candidate_adapter,
        candidate_settings,
        GatewayJudge,
        MatcherClient,
    )

    hrun.BENCH_ROOT = task_root.parent
    # Task definitions and documents live under runtime/Harvey-LAB/tasks.
    evaluator.BENCH_ROOT = task_root.parent
    evaluator.RESULTS_DIR = root
    judge = GatewayJudge()
    settings = candidate_settings()
    scoring.anthropic.Anthropic = lambda *a, **kw: MatcherClient(judge)
    write_json(
        root / "config.json",
        {
            "model": os.environ["MODEL"],
            "judge": judge.model,
            "judge_api": judge.api,
            "judge_reasoning_effort": judge.reasoning_effort,
            "max_turns": 200,
            "candidate": settings,
            "streaming": settings["api"] == "responses",
            "source_commit": manifest["source_commit"],
            "task_ids": [t["task_id"] for t in tasks],
        },
    )

    def one(t):
        task_id = t["task_id"]
        rid = f"{t['index']:03d}"
        dest = root / rid
        dest.mkdir(exist_ok=True)
        saved = dest / "result.json"
        if saved.exists():
            previous = json.loads(saved.read_text())
            if previous.get("reward") is not None:
                return previous
        try:
            print(f"Starting {task_id}", flush=True)
            task = hrun.load_task(task_id)
            workspace = dest / "workspace"
            workspace.mkdir(exist_ok=True)
            out = dest / "output"
            out.mkdir(exist_ok=True)
            if not (dest / "metrics.json").exists():
                hrun.setup_skill_scripts(hrun.DEFAULT_SKILLS, workspace)
                with Sandbox(
                    documents_dir=Path(task["docs_dir"]),
                    output_dir=out,
                    workspace_dir=workspace,
                    image=os.getenv("HARVEY_IMAGE", "toolspace-harvey:8133051"),
                    default_timeout=60,
                ) as sb:
                    result = run_agent(
                        adapter=candidate_adapter(settings),
                        system_prompt=hrun.SYSTEM_PROMPT_PREAMBLE
                        + hrun.load_skills(hrun.DEFAULT_SKILLS),
                        user_prompt=task["instructions"],
                        tool_executor=ToolExecutor(sandbox=sb, shell_timeout=60),
                        tools=get_all_tool_definitions(),
                        max_turns=200,
                        transcript_path=str(dest / "transcript.jsonl"),
                    )
                write_json(
                    dest / "metrics.json",
                    {k: v for k, v in result.items() if k != "messages"},
                )
            else:
                print(f"Using saved generation for {task_id}", flush=True)
            print(f"Scoring {task_id}", flush=True)
            scores = evaluator.evaluate_run(rid, task_id, judge, parallel=4)
            write_json(dest / "scores.json", scores)
            row = {
                "task_id": task_id,
                "reward": scores["score"] / scores["max_score"]
                if scores["max_score"]
                else 0,
                "all_pass": scores.get("all_pass", False),
            }
        except Exception as exc:
            row = {
                "task_id": task_id,
                "reward": None,
                "error": type(exc).__name__ + ": " + str(exc)[-500:],
            }
        write_json(saved, row)
        return row

    with ThreadPoolExecutor(max_workers=int(os.getenv("CONCURRENCY", "4"))) as pool:
        rows = list(pool.map(one, tasks))
    return summarize(root, rows, len(tasks), 120, "all_pass_rate")


if __name__ == "__main__":
    raise SystemExit(main())
