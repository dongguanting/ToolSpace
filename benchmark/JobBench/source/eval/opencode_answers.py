#!/usr/bin/env python3
"""Persist final OpenCode chat answers for generated query tasks."""

import argparse
import json
from pathlib import Path


def is_query(task):
    contract = task / "_private/execution_contract.json"
    if not contract.is_file():
        return False
    return json.loads(contract.read_text(encoding="utf-8")).get("task_type") == "query"


def final_answer(trajectory):
    """Accept text from the last normally completed message, never tool/reasoning text."""
    try:
        events = [json.loads(line) for line in trajectory.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if any(not isinstance(event, dict) or event.get("type") == "error" for event in events):
        return None
    progress = [event for event in events if event.get("type") in
                {"step_start", "step_finish", "tool_use"}]
    if not progress or progress[-1].get("type") != "step_finish":
        return None
    finish = progress[-1].get("part", {})
    message_id = finish.get("messageID")
    session_id = progress[-1].get("sessionID")
    if finish.get("reason") != "stop" or not message_id or not session_id:
        return None

    # An emitted text part is a complete snapshot; repeated part IDs replace it.
    parts = {}
    for index, event in enumerate(events):
        part = event.get("part", {})
        if (event.get("type") == "text" and part.get("type") == "text"
                and event.get("sessionID") == session_id and part.get("messageID") == message_id
                and isinstance(part.get("text"), str)):
            parts[part.get("id", index)] = part["text"]
    answer = "\n\n".join(parts.values()).strip()
    if not answer:
        return None
    return {"text": answer, "session_id": session_id, "message_id": message_id}


def save_answer(trajectory, output):
    answer = final_answer(trajectory)
    if answer is None:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    # Preserve files explicitly written by the solver or an earlier recovery.
    with output.open("x", encoding="utf-8") as stream:
        stream.write(answer["text"] + "\n")
    return True


def recover_answer(task, trajectories, output):
    """Recover a saved terminal answer without issuing another model request."""
    if not is_query(task) or (output.exists() and any(output.iterdir())):
        return False
    candidates = sorted(trajectories.glob("*_attempt*.jsonl"),
                        key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    for trajectory in candidates:
        answer = final_answer(trajectory)
        if answer is None:
            continue
        output.mkdir(parents=True, exist_ok=True)
        with (output / "final_answer.md").open("x", encoding="utf-8") as stream:
            stream.write(answer["text"] + "\n")
        record = {"source_trajectory": trajectory.name, "session_id": answer["session_id"],
                  "message_id": answer["message_id"], "reason": "completed-query-answer-recovery"}
        (trajectories / "answer_recovery.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (trajectories / ".jobbench_complete").touch()
        print(f"Recovered final answer from {trajectory.name}")
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    query = commands.add_parser("is-query")
    query.add_argument("task", type=Path)
    save = commands.add_parser("save-answer")
    save.add_argument("trajectory", type=Path)
    save.add_argument("output", type=Path)
    recover = commands.add_parser("recover-answer")
    recover.add_argument("task", type=Path)
    recover.add_argument("trajectories", type=Path)
    recover.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "is-query":
        success = is_query(args.task)
    elif args.command == "save-answer":
        success = save_answer(args.trajectory, args.output)
    else:
        success = recover_answer(args.task, args.trajectories, args.output)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
