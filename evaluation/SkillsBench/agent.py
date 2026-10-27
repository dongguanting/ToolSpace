#!/usr/bin/env python3
"""Run one SkillsBench task with the published OpenHands SDK.

The task runner owns the Docker lifecycle; this entrypoint only owns the
OpenHands conversation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "dict"):
        value = value.dict()
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(jsonable(value), ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def status_value(conversation: Any) -> str:
    status = getattr(conversation.state, "execution_status", "")
    return str(getattr(status, "value", status) or "")


def conversation_events(conversation: Any) -> list[Any]:
    try:
        return list(getattr(conversation.state, "events", []) or [])
    except (FileNotFoundError, OSError):
        # The SDK event store can evict an event while a callback is serializing it.
        return []


def history(conversation: Any) -> list[Any]:
    return [jsonable(event) for event in conversation_events(conversation)]


def agent_finished(conversation: Any) -> bool:
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.tool.builtins.finish import FinishAction

    for event in reversed(conversation_events(conversation)):
        if isinstance(event, ActionEvent):
            return isinstance(getattr(event, "action", None), FinishAction)
    return False


def agent_sent_message(conversation: Any) -> bool:
    from openhands.sdk.event import ActionEvent, MessageEvent

    for event in reversed(conversation_events(conversation)):
        if (
            isinstance(event, MessageEvent)
            and getattr(event, "source", None) == "agent"
        ):
            return True
        if isinstance(event, ActionEvent):
            return False
    return False


def finish_message(conversation: Any) -> str:
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.tool.builtins.finish import FinishAction

    for event in reversed(conversation_events(conversation)):
        if isinstance(event, ActionEvent) and isinstance(
            getattr(event, "action", None), FinishAction
        ):
            return str(getattr(event.action, "message", "") or "")
    return ""


def normalize_base_url(value: str) -> str:
    value = value.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return value


def build_tools(shell_path: str) -> list[Any]:
    from openhands.sdk.tool import Tool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    return [
        Tool(
            name=TerminalTool.name,
            params={
                "terminal_type": "subprocess",
                "shell_path": shell_path,
                "no_change_timeout_seconds": 1800,
            },
        ),
        Tool(name=TaskTrackerTool.name),
    ]


def run(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "1")
    os.environ.setdefault("NO_COLOR", "1")

    from openhands.sdk import Agent, Conversation, LLM
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
    from openhands.sdk.workspace import LocalWorkspace

    model_config = dict(payload["model_config"])
    model = str(model_config["id"])
    if not model.startswith("openai/"):
        model = "openai/" + model
    extra_body = dict(model_config.get("extra_body") or {})
    llm = LLM(
        model=model,
        base_url=normalize_base_url(str(model_config["base_url"])),
        api_key=str(model_config.get("api_key") or os.environ["OPENAI_API_KEY"]),
        usage_id="agent",
        reasoning_effort=os.getenv("MODEL_REASONING_EFFORT") or "high",
        litellm_extra_body=extra_body,
        timeout=float(os.getenv("LLM_TIMEOUT", "1800")),
        temperature=float(os.getenv("MODEL_TEMPERATURE", "1")),
        max_output_tokens=32768,
        num_retries=5,
        retry_min_wait=8,
        retry_max_wait=64,
        drop_params=True,
    )
    condenser_llm = llm.model_copy(update={"usage_id": "condenser"})
    condenser = LLMSummarizingCondenser(llm=condenser_llm, max_size=240, keep_first=2)
    agent = Agent(
        llm=llm,
        tools=build_tools(str(payload["shell_path"])),
        system_prompt_kwargs={"cli_mode": True},
        condenser=condenser,
    )

    state_path = Path(payload["paths"]["state_path"])
    runtime = {
        "status": "running",
        "task_id": payload.get("task_id", ""),
        "started_at": datetime.now().isoformat(),
    }

    def callback(event: Any) -> None:
        runtime["history"] = history(conversation)
        runtime["last_event"] = jsonable(event)
        write_json(state_path, runtime)

    conversation = Conversation(
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(payload["workspace"])),
        callbacks=[callback],
        max_iteration_per_run=int(payload.get("max_iterations", 500)),
        visualizer=None,
        delete_on_close=False,
    )
    try:
        conversation.send_message(str(payload["prompt"]))
        fake_user_responses = 0
        for _ in range(10):
            conversation.run()
            runtime["history"] = history(conversation)
            write_json(state_path, runtime)
            if status_value(conversation).lower() != "finished":
                break
            if agent_finished(conversation) or not agent_sent_message(conversation):
                break
            conversation.send_message(
                "Please continue working on the task. Do not ask for human help. "
                "When the task is complete, use the finish tool."
            )
            fake_user_responses += 1
        result = {
            "status": status_value(conversation),
            "history": history(conversation),
            "finish_message": finish_message(conversation),
            "fake_user_responses": fake_user_responses,
            "workspace": str(payload["workspace"]),
            "error": "",
        }
        runtime.update(
            {"status": result["status"], "finished_at": datetime.now().isoformat()}
        )
        write_json(state_path, runtime)
        return result
    finally:
        close = getattr(conversation, "close", None)
        if callable(close):
            close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    payload = json.loads(Path(args.config).read_text(encoding="utf-8"))
    result_path = Path(payload["paths"]["result_path"])
    state_path = Path(payload["paths"]["state_path"])
    try:
        result = run(payload)
    except Exception as exc:
        # Keep the callback's saved trajectory when an SDK call fails. Replacing
        # it with [] hid the steps preceding context/transport failures.
        try:
            saved_history = json.loads(state_path.read_text()).get("history", [])
        except (OSError, ValueError):
            saved_history = []
        result = {
            "status": "error",
            "history": saved_history,
            "finish_message": "",
            "fake_user_responses": 0,
            "workspace": str(payload.get("workspace", "")),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        write_json(state_path, result)
    write_json(result_path, result)
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
