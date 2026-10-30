"""State-local records, value pools, and provenance-preserving API argument filling."""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from random import Random
from typing import Any

from tool_graph.sources import Endpoint

# ``make_args_automationbench`` is called once per graph edge/template during
# DFS candidate collection.  A single collection level reuses the same world
# snapshot and observation prefix for many templates; rebuilding the complete
# recursive value pools for every template was the dominant cost of long
# walks.  These bounded identity caches only memoise deterministic extraction,
# never argument choices, so RNG consumption and sampled results are unchanged.
_RECORD_CACHE: OrderedDict[int, tuple[Any, list["Record"]]] = OrderedDict()
_OBS_CACHE: OrderedDict[tuple[int, ...], list["Value"]] = OrderedDict()
_CACHE_LIMIT = 256


def _cached_records(world: dict[str, Any]) -> list["Record"]:
    key = id(world)
    cached = _RECORD_CACHE.get(key)
    if cached is not None and cached[0] is world:
        _RECORD_CACHE.move_to_end(key)
        return cached[1]
    value = records(world)
    _RECORD_CACHE[key] = (world, value)
    _RECORD_CACHE.move_to_end(key)
    while len(_RECORD_CACHE) > _CACHE_LIMIT:
        _RECORD_CACHE.popitem(last=False)
    return value


def _cached_observation_values(observations: list[Any]) -> list["Value"]:
    key = tuple(id(item) for item in observations)
    cached = _OBS_CACHE.get(key)
    if cached is not None:
        _OBS_CACHE.move_to_end(key)
        return cached
    value = observation_values(observations)
    _OBS_CACHE[key] = value
    _OBS_CACHE.move_to_end(key)
    while len(_OBS_CACHE) > _CACHE_LIMIT:
        _OBS_CACHE.popitem(last=False)
    return value


@lru_cache(maxsize=32768)
def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@lru_cache(maxsize=32768)
def words(name: str) -> frozenset[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return frozenset(
        part.lower().rstrip("s") for part in re.split(r"[^A-Za-z0-9]+", expanded) if part
    )


def is_id_name(name: str) -> bool:
    value = norm(name)
    return value == "id" or value.endswith(("id", "uuid", "identifier"))


@dataclass(frozen=True)
class Value:
    value: Any
    key: str
    path: str
    service: str
    collection: str
    source: str
    source_step: int | None = None

    def provenance(self) -> dict[str, Any]:
        result = {"source": self.source, "json_path": self.path, "field": self.key}
        if self.service:
            result["service"] = self.service
        if self.collection:
            result["collection"] = self.collection
        if self.source_step is not None:
            result["source_step"] = self.source_step
        return result


@dataclass
class Record:
    fields: dict[str, Value]
    service: str
    collection: str
    path: str
    depth: int


def records(world: dict[str, Any], cap: int = 20000) -> list[Record]:
    out: list[Record] = []

    def visit(value: Any, service: str, collection: str, path: str, depth: int) -> None:
        if depth > 6 or len(out) >= cap:
            return
        if isinstance(value, dict):
            fields = {
                norm(str(key)): Value(
                    child, str(key), f"{path}.{key}", service, collection, "world_state"
                )
                for key, child in value.items()
                if child is not None and isinstance(child, (str, int, float, bool, list, dict))
            }
            if fields:
                out.append(Record(fields, service, collection, path, depth))
            for key, child in value.items():
                visit(child, service, str(key), f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:500]):
                visit(child, service, collection, f"{path}[{index}]", depth + 1)

    for service, value in world.items():
        if service != "meta":
            visit(value, service, service, f"$.{service}", 0)
    return out


def observation_values(observations: list[Any]) -> list[Value]:
    out: list[Value] = []

    def visit(value: Any, path: str, step: int, depth: int) -> None:
        if depth > 7:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if child is not None and isinstance(child, (str, int, float, bool, list, dict)):
                    out.append(Value(child, str(key), child_path, "", "", "observation", step))
                visit(child, child_path, step, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:100]):
                if isinstance(child, (str, int, float, bool)):
                    out.append(
                        Value(
                            child,
                            path.rsplit(".", 1)[-1],
                            f"{path}[{index}]",
                            "",
                            "",
                            "observation",
                            step,
                        )
                    )
                visit(child, f"{path}[{index}]", step, depth + 1)

    for step, observation in enumerate(observations):
        visit(observation, "$", step, 0)
    return out


def fit_schema_value(value: Any, spec: dict[str, Any]) -> Any | None:
    if value is None:
        return None
    if "const" in spec and value != spec["const"]:
        return None
    typ = spec.get("type")
    if typ == "array":
        raw = value if isinstance(value, list) else [value]
        fitted = [fit_schema_value(item, spec.get("items") or {}) for item in raw]
        if any(item is None for item in fitted):
            return None
        if len(fitted) < int(spec.get("minItems", 0)):
            return None
        maximum = spec.get("maxItems")
        return fitted if maximum is None else fitted[: int(maximum)]
    if typ == "object" and not isinstance(value, dict):
        return None
    if typ == "string":
        if isinstance(value, (dict, list, bool)):
            return None
        value = str(value)
        if spec.get("pattern"):
            try:
                if re.fullmatch(spec["pattern"], value) is None:
                    return None
            except re.error:
                pass
        if spec.get("format") == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            return None
        if spec.get("format") in {"date", "date-time"}:
            try:
                dt.datetime.fromisoformat(
                    value.replace("Z", "+00:00") + ("T00:00:00" if "T" not in value else "")
                )
            except ValueError:
                return None
    elif typ == "integer":
        if isinstance(value, bool):
            return None
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
            value = int(value)
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
        elif not isinstance(value, int):
            return None
    elif typ == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return None
    elif typ == "boolean" and not isinstance(value, bool):
        return None
    if spec.get("enum") is not None and value not in spec["enum"]:
        return None
    if isinstance(value, (int, float)):
        if spec.get("minimum") is not None and value < spec["minimum"]:
            return None
        if spec.get("maximum") is not None and value > spec["maximum"]:
            return None
    return value


def _score(parameter: str, candidate: Value, endpoint: Endpoint) -> int:
    # Scores depend on names and paths, not mutable candidate values or RNG.
    return _score_names(
        parameter,
        candidate.key,
        candidate.path,
        candidate.collection,
        endpoint.id,
        endpoint.service,
    )


@lru_cache(maxsize=65536)
def _score_names(
    parameter: str, key: str, path: str, collection: str, endpoint_id: str, endpoint_service: str
) -> int:
    wanted, got = norm(parameter), norm(key)
    wanted_words = words(parameter) - {"id", "uuid", "identifier"}
    got_words = words(key) - {"id", "uuid", "identifier"}
    context = words(path) | words(collection)
    endpoint_words = words(endpoint_id) - {
        endpoint_service,
        "get",
        "list",
        "find",
        "search",
        "query",
        "read",
        "fetch",
    }
    if is_id_name(parameter):
        if not is_id_name(key):
            return -1
        if wanted == got and got != "id":
            return 100
        if wanted_words and (wanted_words & (got_words | context | endpoint_words)):
            return 80
        if got == "id" and (wanted_words or endpoint_words) & context:
            return 70
        return -1
    if wanted == got:
        return 100
    if wanted in got or got in wanted:
        return 45
    if wanted_words & got_words:
        return 35
    return -1


def _best(
    parameter: str, spec: dict[str, Any], values: list[Value], endpoint: Endpoint
) -> tuple[Any, Value] | None:
    ranked = sorted(
        (
            (score, -index, candidate)
            for index, candidate in enumerate(values)
            if (score := _score(parameter, candidate, endpoint)) >= 0
        ),
        reverse=True,
        key=lambda item: (item[0], item[1]),
    )
    for _, _, candidate in ranked:
        fitted = fit_schema_value(candidate.value, spec)
        if fitted is not None:
            return fitted, candidate
    return None


def _parse_jsonish(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return copy.deepcopy(value), False
    try:
        return json.loads(value), True
    except json.JSONDecodeError:
        return value, False


def _path_values(template: str, concrete: str) -> dict[str, str]:
    names = re.findall(r"\{([^{}]+)\}", template)
    pattern = (
        "^"
        + re.sub(
            r"\{[^{}]+\}", r"([^/?#]+)", re.escape(template).replace(r"\{", "{").replace(r"\}", "}")
        )
        + "$"
    )
    match = re.match(pattern, concrete)
    return dict(zip(names, match.groups())) if match else {}


def _safe_template_id(value: Any, known_scalars: set[Any]) -> bool:
    return value in known_scalars or str(value).lower() in {"me", "self", "current", "default"}


def make_args_automationbench(
    endpoint: Endpoint,
    template: dict[str, Any],
    observations: list[Any],
    world: dict[str, Any],
    rng: Random,
    diagnostics: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Fill one concrete ``api_fetch`` call without inventing required values."""
    if diagnostics is not None:
        diagnostics.clear()

    def fail(code: str, **details: Any) -> None:
        if diagnostics is not None:
            diagnostics.update({"code": code, **details})
        return None

    recs = _cached_records(world)
    obs_values = list(reversed(_cached_observation_values(observations)))
    world_values = [value for record in recs for value in record.fields.values()]
    known_scalars = {
        value.value
        for value in world_values + obs_values
        if isinstance(value.value, (str, int, float, bool))
    }
    required = [name for name, spec in endpoint.parameters.items() if spec.get("required")]
    coherent = max(
        recs,
        key=lambda record: (
            sum(
                _score(name, value, endpoint) >= 0
                for name in required
                for value in record.fields.values()
            ),
            -record.depth,
        ),
        default=None,
    )
    template_url = str(template.get("url") or endpoint.url)
    template_path = _path_values(endpoint.url, template_url)
    params, params_was_json = _parse_jsonish(template.get("params"))
    body, body_was_json = _parse_jsonish(template.get("body"))
    params = params if isinstance(params, dict) else params
    provenance: dict[str, Any] = {
        "method": {"source": "endpoint_catalog"},
        "url": {"source": "endpoint_catalog"},
    }
    chosen: dict[str, Any] = {}

    for name, raw_spec in endpoint.parameters.items():
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        location = spec.get("location", "query")
        candidates = obs_values
        if endpoint.service == "google_sheets" and name == "range":
            # ValueRange.range describes the query just completed. A cell may
            # instead reveal a destination or next range to read.
            candidates = [value for value in obs_values if value.key != "range"]
        found = _best(name, spec, candidates, endpoint)
        # Prefer a concrete template value when a query actually revealed it.
        # This keeps next-page/range/resource locators distinct from the source
        # query's own locator, while retaining observation provenance.
        template_value = template_path.get(name)
        if location == "query" and isinstance(params, dict):
            template_value = params.get(name, template_value)
        for candidate in obs_values:
            if template_value is not None and candidate.value == template_value:
                fitted = fit_schema_value(candidate.value, spec)
                if fitted is not None:
                    found = fitted, candidate
                    break
        if endpoint.service == "google_sheets" and name == "range" and endpoint.kind != "query":
            # A destination tab is a user control, not a query result to substitute.
            tab = str(template_value or "").split("!", 1)[0].strip("'")
            known_tabs = {
                str(ws.get("title", ""))
                for ws in world.get("google_sheets", {}).get("worksheets", [])
            }
            if tab in known_tabs:
                chosen[name] = template_value
                provenance[f"{location}.{name}"] = {"source": "correct_template", "worksheet": tab}
                continue
        if found is None and coherent is not None:
            found = _best(name, spec, list(coherent.fields.values()), endpoint)
        if found is None:
            found = _best(name, spec, world_values, endpoint)
        if found is not None:
            value, source = found
            provenance[f"{location}.{name}"] = source.provenance()
        else:
            if "const" in spec:
                value = spec["const"]
                provenance[f"{location}.{name}"] = {"source": "schema_const"}
            elif "default" in spec:
                value = spec["default"]
                provenance[f"{location}.{name}"] = {"source": "schema_default"}
            elif spec.get("enum"):
                value = rng.choice(spec["enum"])
                provenance[f"{location}.{name}"] = {"source": "schema_enum"}
            else:
                template_value = template_path.get(name)
                if location == "query" and isinstance(params, dict):
                    template_value = params.get(name, template_value)
                value = fit_schema_value(template_value, spec)
                if value is not None and (
                    not is_id_name(name) or _safe_template_id(value, known_scalars)
                ):
                    provenance[f"{location}.{name}"] = {"source": "correct_template"}
                else:
                    value = None
        if value is None:
            if spec.get("required"):
                return fail(
                    "missing_required_parameter",
                    parameter=name,
                    location=location,
                    expected_type=spec.get("type"),
                )
            continue
        chosen[name] = value

    url = endpoint.url
    for name, spec in endpoint.parameters.items():
        if spec.get("location") == "path":
            if name not in chosen:
                return fail(
                    "missing_path_parameter",
                    parameter=name,
                    endpoint_url=endpoint.url,
                )
            url = url.replace("{" + name + "}", urllib.parse.quote(str(chosen[name]), safe=""))
    if "{" in url:
        return fail(
            "unresolved_path_placeholder",
            placeholders=re.findall(r"\{([^{}]+)\}", url),
            endpoint_url=endpoint.url,
        )

    if isinstance(params, dict):
        for name, spec in endpoint.parameters.items():
            if spec.get("location") == "query" and name in chosen:
                params[name] = chosen[name]

    missing = object()

    unresolved_body_ids: list[str] = []

    def rewrite_ids(value: Any, path: str = "body") -> Any:
        if isinstance(value, dict):
            result = {key: rewrite_ids(child, f"{path}.{key}") for key, child in value.items()}
            return missing if any(child is missing for child in result.values()) else result
        if isinstance(value, list):
            result = [rewrite_ids(child, f"{path}[{index}]") for index, child in enumerate(value)]
            return missing if any(child is missing for child in result) else result
        key = path.rsplit(".", 1)[-1]
        if not is_id_name(key):
            return value
        found = _best(key, {"type": "string"}, obs_values, endpoint) or _best(
            key, {"type": "string"}, world_values, endpoint
        )
        if found is None:
            if _safe_template_id(value, known_scalars):
                provenance[path] = {"source": "correct_template"}
                return value
            unresolved_body_ids.append(path)
            return missing
        replacement, source = found
        provenance[path] = source.provenance()
        return replacement

    body = rewrite_ids(body)
    if body is missing:
        return fail(
            "ungrounded_body_identifier",
            fields=unresolved_body_ids[:8],
            additional_fields=max(0, len(unresolved_body_ids) - 8),
        )
    if body not in (None, "") and not any(key.startswith("body") for key in provenance):
        provenance["body"] = {"source": "correct_template"}
    if params not in (None, "") and not any(key.startswith("query.") for key in provenance):
        provenance["params"] = {"source": "correct_template"}

    arguments = {
        "method": endpoint.method,
        "url": url,
        "params": json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        if params_was_json
        else params,
        "body": json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        if body_was_json
        else body,
    }
    if diagnostics is not None:
        diagnostics.update({"code": "filled", "parameters": sorted(chosen)})
    return arguments, provenance
