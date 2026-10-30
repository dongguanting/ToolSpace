"""Local business application simulators for AutomationPro."""

from __future__ import annotations

import copy
import inspect
import json
import sys
from pathlib import Path

# Keep the upstream import paths inside the vendored simulator intact.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from automationbench.schema.world import WorldState
from automationbench.tools.api import API_TOOLS
from environments.outcome_runtime import install

install()


def strip_none_values(value):
    """Match the normalization performed by the archived evaluation runner."""
    if isinstance(value, dict):
        return {k: strip_none_values(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [strip_none_values(v) for v in value if v is not None]
    return value


def allowed_services(info):
    fields = sorted((f for f in WorldState.model_fields if f != "meta"), key=len, reverse=True)
    allowed = set(info["initial_state"]) & set(fields)
    names = [a["type"] for a in info["assertions"]] + info["zapier_tools"]
    for name in names:
        for field in fields:
            if name == field or name.startswith(field + "_"):
                allowed.add(field)
                break
    return sorted(allowed)


class Environment:
    """An isolated mutable world; only the three API tools are agent accessible."""

    def __init__(self, row):
        info = row["info"]
        if isinstance(info, str):
            info = json.loads(info)
        self.info = strip_none_values(copy.deepcopy(info))
        self.initial_state = copy.deepcopy(self.info["initial_state"])
        self.world = WorldState(**self.initial_state)
        self.world.meta.allowed_services = allowed_services(self.info)
        self.calls = []
        self.tools = {tool.__name__: tool for tool in API_TOOLS}

    def execute(self, name, arguments):
        """Dispatch model arguments without permitting caller-supplied world state."""
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        if name not in self.tools:
            raise ValueError(f"Unknown tool: {name}")
        if "world" in arguments:
            raise ValueError("world is managed by the environment")
        arguments = {k: v for k, v in arguments.items() if v != {}}
        tool = self.tools[name]
        injected = {"world": self.world} if "world" in inspect.signature(tool).parameters else {}
        result = tool(**arguments, **injected)
        result = result if isinstance(result, str) else json.dumps(result)
        self.calls.append({"name": name, "arguments": arguments, "observation": result})
        return result

    def score(self):
        from verifiers import partial_credit

        state = {"info": self.info, "initial_state": self.initial_state, "world": self.world}
        reward = partial_credit(state)
        return {
            "reward": reward,
            "task_completed_correctly": int(reward == 1.0),
            "assertion_results": state["_assertion_results"],
        }


def tool_definitions():
    """Expose the archived API tools with their original parameter types."""
    result = []
    for tool in API_TOOLS:
        properties, required = {}, []
        for name, parameter in inspect.signature(tool).parameters.items():
            if name == "world":
                continue
            kind = "integer" if name == "top_k" else "string"
            spec = {"type": kind}
            if parameter.default is inspect.Parameter.empty:
                required.append(name)
            elif parameter.default is None:
                spec["type"] = [kind, "null"]
            else:
                spec["default"] = parameter.default
            properties[name] = spec
        result.append({"type": "function", "function": {
            "name": tool.__name__, "description": inspect.getdoc(tool),
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False},
        }})
    return result
