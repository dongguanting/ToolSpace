"""Infer endpoint dependencies from observed Automation tool calls.

The graph is inferred from the observed trajectories. Calls in the same assistant
tool-call batch are parallel and never connected. A dependency is added when a
later call reuses values produced by an earlier call, or when a later call touches
the same resource after a state-changing call. Exact failed retries are tracked as
a separate edge type. Merely appearing earlier in the transcript is not enough.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

READ_METHODS = {"GET", "HEAD", "OPTIONS"}


ACTION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


READ_POST_RE = re.compile(
    r"(?:/search(?:/|$)|:search(?:stream)?(?:\?|$)|/query(?:\?|$)|"
    r"/find(?:/|$)|:find(?:\?|$)|/lookup(?:/|$)|:lookup(?:\?|$)|"
    r"listaccessiblecustomers)",
    re.IGNORECASE,
)


EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)


URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+\-Z]+)?\b", re.I)


PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d ().\-]{7,}\d(?!\w)")


TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+\-/]{3,}")


WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{4,}")


PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")


STOP_TOKENS = {
    "true",
    "false",
    "none",
    "null",
    "string",
    "object",
    "array",
    "required",
    "created",
    "updated",
    "success",
    "status",
    "active",
    "pending",
    "approved",
    "rejected",
    "complete",
    "completed",
    "default",
    "company",
    "example",
    "message",
    "messages",
    "contact",
    "contacts",
    "customer",
    "customers",
    "employee",
    "employees",
    "invoice",
    "invoices",
    "account",
    "accounts",
    "record",
    "records",
    "title",
    "description",
    "content",
    "email",
    "phone",
    "value",
    "values",
    "fields",
    "field",
    "query",
    "search",
    "https",
    "api",
    "users",
    "user",
    "admin",
    "system",
    "response",
    "request",
    "result",
    "results",
    "items",
    "item",
    "page",
    "pages",
    "data",
    "type",
    "name",
    "first",
    "last",
}


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def compile_template(template: str) -> tuple[re.Pattern[str], int]:
    canonical = canonical_url(template)
    pieces: list[str] = []
    cursor = 0
    for match in PLACEHOLDER_RE.finditer(canonical):
        pieces.append(re.escape(canonical[cursor : match.start()]))
        pieces.append(r"([^/?#]+)")
        cursor = match.end()
    pieces.append(re.escape(canonical[cursor:]))
    literal = PLACEHOLDER_RE.sub("", canonical)
    return re.compile("^" + "".join(pieces) + "$", re.I), len(literal)


def parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0:1] not in {"{", "[", '"'}:
        return value
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        return value
    return decoded


def parse_arguments(raw_call: dict) -> dict:
    raw = raw_call.get("function", {}).get("arguments", {})
    raw = parse_jsonish(raw)
    return raw if isinstance(raw, dict) else {}


def endpoint_kind(endpoint: dict) -> str:
    method = endpoint["method"].upper()
    if method in READ_METHODS:
        return "query"
    if method != "POST":
        return "action"
    path = urlsplit(endpoint["url"]).path
    if READ_POST_RE.search(path):
        return "query"
    request = str(endpoint.get("request", "")).lstrip()
    if "graphql" in path.lower() and re.match(r"^(?:query\b|\{)", request, re.I):
        return "query"
    return "action"


@dataclass
class Endpoint:
    id: str
    service: str
    method: str
    url: str
    description: str
    request: str
    parameter_names: list[str]
    kind: str
    regex: re.Pattern[str]
    specificity: int


@dataclass
class Call:
    uid: int
    endpoint: Endpoint
    trajectory_id: str
    model: str
    split: str
    task: str
    batch: int
    call_index: int
    arguments: dict
    arguments_text: str
    response_text: str
    response_ok: bool
    request_tokens: dict[str, int]
    resource_tokens: dict[str, int]
    response_tokens: dict[str, int]
    mapping_confidence: str


@dataclass
class Edge:
    source: str
    target: str
    weight: int = 0
    correct: int = 0
    incorrect: int = 0
    type_counts: Counter[str] = field(default_factory=Counter)
    tasks: Counter[str] = field(default_factory=Counter)
    models: Counter[str] = field(default_factory=Counter)
    examples: list[dict] = field(default_factory=list)


def load_catalog(
    path: Path,
) -> tuple[list[Endpoint], dict[str, list[Endpoint]], dict[str, Endpoint]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    endpoints: list[Endpoint] = []
    for service in data["api_endpoint_catalog"]:
        for raw in service["endpoints"]:
            regex, specificity = compile_template(raw["url"])
            endpoints.append(
                Endpoint(
                    id=raw["id"],
                    service=raw.get("api", service["api"]),
                    method=raw["method"].upper(),
                    url=raw["url"],
                    description=raw.get("description") or "",
                    request=str(raw.get("request") or ""),
                    parameter_names=list(raw.get("parameter_names") or []),
                    kind=endpoint_kind(raw),
                    regex=regex,
                    specificity=specificity,
                )
            )
    by_method: dict[str, list[Endpoint]] = defaultdict(list)
    for endpoint in endpoints:
        by_method[endpoint.method].append(endpoint)
    return endpoints, by_method, {endpoint.id: endpoint for endpoint in endpoints}


def endpoint_candidates(
    method: str, url: str, by_method: dict[str, list[Endpoint]]
) -> list[Endpoint]:
    canonical = canonical_url(url)
    return [endpoint for endpoint in by_method.get(method, []) if endpoint.regex.match(canonical)]


def normalize_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def resolve_endpoint(
    candidates: list[Endpoint],
    revealed: set[str],
    arguments: dict,
    url: str,
) -> tuple[Endpoint, str]:
    if len(candidates) == 1:
        return candidates[0], "unique"

    revealed_candidates = [endpoint for endpoint in candidates if endpoint.id in revealed]
    if len(revealed_candidates) == 1:
        return revealed_candidates[0], "revealed_unique"

    params = parse_jsonish(arguments.get("params"))
    body = parse_jsonish(arguments.get("body"))
    params = params if isinstance(params, dict) else {}
    body_text = (
        json.dumps(body, ensure_ascii=False).lower() if not isinstance(body, str) else body.lower()
    )
    actual_path = unquote(urlsplit(url).path).lower()

    scores: list[tuple[float, Endpoint]] = []
    for endpoint in candidates:
        score = endpoint.specificity / 1000
        if endpoint.id in revealed:
            score += 20
        endpoint_tail = endpoint.id.rsplit(".", 1)[-1].lower()
        endpoint_parts = [p for p in re.split(r"[^a-z0-9]+", endpoint.id.lower()) if len(p) > 2]
        score += sum(0.25 for part in endpoint_parts if part in body_text)
        if params and set(params).issubset(set(endpoint.parameter_names)):
            score += 1

        if endpoint.service == "wave":
            schema_words = set(re.findall(r"[A-Za-z][A-Za-z0-9]+", endpoint.request))
            operation_words = [
                word
                for word in schema_words
                if normalize_for_match(word).endswith(normalize_for_match(endpoint_tail))
                or normalize_for_match(endpoint_tail).endswith(normalize_for_match(word))
            ]
            if any(word.lower() in body_text for word in operation_words):
                score += 100
            camel_hint = normalize_for_match(endpoint.id.split("wave.", 1)[-1])
            if camel_hint and camel_hint in normalize_for_match(body_text):
                score += 100

        if endpoint.service == "quickbooks":
            operation = str(params.get("operation", "")).lower()
            void = str(params.get("void", "")).lower()
            body_dict = body if isinstance(body, dict) else {}
            if operation == "delete" and endpoint_tail == "delete":
                score += 100
            elif void in {"true", "1"} and endpoint_tail == "void":
                score += 100
            elif {"Id", "SyncToken"}.issubset(body_dict) and endpoint_tail == "update":
                score += 25
            elif "Id" not in body_dict and endpoint_tail == "create":
                score += 15

        if endpoint.service == "intercom":
            if '"message_type": "note"' in body_text and endpoint_tail == "note":
                score += 100
            elif '"message_type": "note"' not in body_text and endpoint_tail == "reply":
                score += 10

        if endpoint.service == "salesforce":
            object_hint = (
                endpoint.id.split(".")[-2].lower() if len(endpoint.id.split(".")) > 2 else ""
            )
            if object_hint and f"/sobjects/{object_hint}" in actual_path:
                score += 50

        if (
            endpoint.service == "airtable"
            and "/meta/bases" in actual_path
            and ".meta." in endpoint.id
        ):
            score += 100
        if (
            endpoint.service == "bamboohr"
            and actual_path.endswith("/directory")
            and endpoint.id.endswith("directory")
        ):
            score += 100
        if endpoint.service == "zendesk":
            if actual_path.endswith("/search") and endpoint_tail == "search":
                score += 100
            elif not actual_path.endswith("/search") and endpoint_tail == "get":
                score += 10

        scores.append((score, endpoint))

    scores.sort(key=lambda pair: (pair[0], pair[1].specificity, pair[1].id), reverse=True)
    confidence = (
        "heuristic" if len(scores) < 2 or scores[0][0] > scores[1][0] else "ambiguous_tiebreak"
    )
    return scores[0][1], confidence


def reveal_ids(response_text: str) -> set[str]:
    try:
        payload = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    return {
        str(item["id"])
        for item in payload.get("results", [])
        if isinstance(item, dict) and item.get("id")
    }


def response_is_ok(response_text: str) -> bool:
    try:
        payload = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        return bool(response_text)
    if isinstance(payload, dict):
        if payload.get("error"):
            return False
        if payload.get("ok") is False or payload.get("success") is False:
            return False
    return True


def walk_values(value: Any) -> list[str]:
    value = parse_jsonish(value)
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(walk_values(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(walk_values(child))
        return result
    if value is None or isinstance(value, bool):
        return []
    return [str(value)]


def add_token(tokens: dict[str, int], raw: str, strength: int) -> None:
    token = raw.strip(" \t\r\n.,;!?()[]{}<>\"'").lower()
    if len(token) < 4 or len(token) > 180 or token in STOP_TOKENS:
        return
    tokens[token] = max(tokens.get(token, 0), strength)


def extract_tokens(value: Any) -> dict[str, int]:
    tokens: dict[str, int] = {}
    for text in walk_values(value):
        text = html.unescape(unquote(text))
        stripped = " ".join(text.split())
        if 4 <= len(stripped) <= 120:
            add_token(tokens, stripped, 3)
        for match in EMAIL_RE.findall(text):
            add_token(tokens, match, 5)
        for match in URL_RE.findall(text):
            add_token(tokens, match, 5)
        for match in DATE_RE.findall(text):
            add_token(tokens, match, 4)
        for match in PHONE_RE.findall(text):
            add_token(tokens, match, 4)
        for match in TOKEN_RE.findall(text):
            strength = (
                4 if any(ch.isdigit() for ch in match) or any(ch in "_:@/" for ch in match) else 1
            )
            add_token(tokens, match, strength)
        for match in WORD_RE.findall(text):
            add_token(tokens, match, 1)
    return tokens


def resource_values(endpoint: Endpoint, url: str) -> dict[str, int]:
    match = endpoint.regex.match(canonical_url(url))
    return extract_tokens(list(match.groups()) if match else [])


def target_values(arguments: dict, endpoint: Endpoint, url: str) -> dict[str, int]:
    tokens = extract_tokens([arguments.get("params"), arguments.get("body")])
    for token, strength in resource_values(endpoint, url).items():
        tokens[token] = max(tokens.get(token, 0), strength)
    return tokens


def load_trajectories(
    trajectory_dir: Path,
    by_method: dict[str, list[Endpoint]],
) -> tuple[list[list[list[Call]]], Counter[str], Counter[str]]:
    all_trajectories: list[list[list[Call]]] = []
    stats: Counter[str] = Counter()
    response_df: Counter[str] = Counter()
    uid = 0

    for path in sorted(trajectory_dir.glob("*/*.jsonl")):
        model = path.parent.name
        split = path.stem
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                payload = json.loads(line)
                stats["trajectories"] += 1
                trajectory_id = payload.get("id", "")
                task = payload.get("metadata", {}).get("task_label", {}).get("task_name", "")
                responses = {
                    message.get("tool_call_id"): message.get("content", "")
                    for message in payload.get("messages", [])
                    if message.get("role") == "tool"
                }
                revealed: set[str] = set()
                batches: list[list[Call]] = []
                batch_index = 0
                for message in payload.get("messages", []):
                    if message.get("role") != "assistant" or not message.get("tool_calls"):
                        continue
                    batch_calls: list[Call] = []
                    delayed_reveals: set[str] = set()
                    for call_index, raw_call in enumerate(message["tool_calls"]):
                        function = raw_call.get("function", {})
                        tool_name = function.get("name", "")
                        response_text = responses.get(raw_call.get("id"), "")
                        if tool_name == "api_search":
                            stats["api_search_excluded"] += 1
                            delayed_reveals.update(reveal_ids(response_text))
                            continue
                        if tool_name != "api_fetch":
                            stats["auxiliary_excluded"] += 1
                            continue
                        stats["api_fetch"] += 1
                        arguments = parse_arguments(raw_call)
                        method = str(arguments.get("method", "GET")).upper()
                        url = str(arguments.get("url", ""))
                        candidates = endpoint_candidates(method, url, by_method)
                        if not candidates:
                            stats["unmatched_api_fetch"] += 1
                            continue
                        endpoint, confidence = resolve_endpoint(
                            candidates, revealed, arguments, url
                        )
                        stats["matched_api_fetch"] += 1
                        stats[f"mapping_{confidence}"] += 1
                        response_value = parse_jsonish(response_text)
                        response_tokens = extract_tokens(response_value)
                        request_tokens = target_values(arguments, endpoint, url)
                        resource_tokens = resource_values(endpoint, url)
                        for token in response_tokens:
                            response_df[token] += 1
                        uid += 1
                        batch_calls.append(
                            Call(
                                uid=uid,
                                endpoint=endpoint,
                                trajectory_id=trajectory_id,
                                model=model,
                                split=split,
                                task=task,
                                batch=batch_index,
                                call_index=call_index,
                                arguments=arguments,
                                arguments_text=json.dumps(
                                    arguments, ensure_ascii=False, sort_keys=True
                                ),
                                response_text=response_text,
                                response_ok=response_is_ok(response_text),
                                request_tokens=request_tokens,
                                resource_tokens=resource_tokens,
                                response_tokens=response_tokens,
                                mapping_confidence=confidence,
                            )
                        )
                    revealed.update(delayed_reveals)
                    if batch_calls:
                        batches.append(batch_calls)
                    batch_index += 1
                all_trajectories.append(batches)
    return all_trajectories, stats, response_df


def usable_token(token: str, strength: int, response_df: Counter[str]) -> bool:
    frequency = response_df[token]
    if strength >= 4:
        return frequency <= 500
    if strength >= 3:
        return frequency <= 100
    return frequency <= 20


def add_edge(
    edges: dict[tuple[str, str], Edge],
    source: Call,
    target: Call,
    edge_type: str,
    evidence_tokens: list[str],
) -> None:
    key = (source.endpoint.id, target.endpoint.id)
    edge = edges.setdefault(key, Edge(source=key[0], target=key[1]))
    edge.weight += 1
    edge.correct += target.split == "correct"
    edge.incorrect += target.split != "correct"
    edge.type_counts[edge_type] += 1
    edge.tasks[target.task] += 1
    edge.models[target.model] += 1
    if len(edge.examples) < 3:
        edge.examples.append(
            {
                "trajectory": target.trajectory_id,
                "task": target.task,
                "model": target.model,
                "split": target.split,
                "source_batch": source.batch,
                "target_batch": target.batch,
                "type": edge_type,
                "tokens": evidence_tokens[:6],
            }
        )


def infer_edges(
    trajectories: list[list[list[Call]]],
    response_df: Counter[str],
) -> tuple[dict[tuple[str, str], Edge], Counter[str], Counter[str]]:
    edges: dict[tuple[str, str], Edge] = {}
    node_calls: Counter[str] = Counter()
    inference_stats: Counter[str] = Counter()

    for batches in trajectories:
        producer_index: dict[str, tuple[int, list[Call]]] = {}
        action_index: dict[str, tuple[int, list[Call]]] = {}
        last_endpoint_call: dict[str, Call] = {}

        for batch in batches:
            for target in batch:
                node_calls[target.endpoint.id] += 1
                data_candidates: dict[int, tuple[Call, list[tuple[str, int]]]] = {}
                for token, target_strength in target.request_tokens.items():
                    producer = producer_index.get(token)
                    if not producer:
                        continue
                    _, sources = producer
                    for source in sources:
                        source_strength = source.response_tokens.get(token, 0)
                        strength = max(target_strength, source_strength)
                        if not usable_token(token, strength, response_df):
                            continue
                        item = data_candidates.setdefault(source.uid, (source, []))
                        item[1].append((token, strength))

                connected_sources: set[int] = set()
                for source, matched in data_candidates.values():
                    strong = [token for token, strength in matched if strength >= 3]
                    ordinary = [token for token, strength in matched if strength < 3]
                    if not strong and len(set(ordinary)) < 2:
                        continue
                    evidence = sorted(
                        set(strong + ordinary), key=lambda token: (-len(token), token)
                    )
                    add_edge(edges, source, target, "data", evidence)
                    connected_sources.add(source.uid)
                    inference_stats["data_instances"] += 1

                state_candidates: dict[int, tuple[Call, list[str]]] = {}
                for token, strength in target.request_tokens.items():
                    if strength < 3 or not usable_token(token, strength, response_df):
                        continue
                    prior = action_index.get(token)
                    if not prior:
                        continue
                    _, sources = prior
                    for source in sources:
                        if source.uid in connected_sources:
                            continue
                        if source.endpoint.service != target.endpoint.service:
                            continue
                        item = state_candidates.setdefault(source.uid, (source, []))
                        item[1].append(token)
                for source, tokens in state_candidates.values():
                    add_edge(edges, source, target, "state", sorted(set(tokens))[:6])
                    inference_stats["state_instances"] += 1

                previous = last_endpoint_call.get(target.endpoint.id)
                if (
                    previous
                    and previous.batch < target.batch
                    and previous.arguments_text == target.arguments_text
                    and not previous.response_ok
                ):
                    add_edge(edges, previous, target, "retry", ["same arguments after error"])
                    inference_stats["retry_instances"] += 1

            # Calls in a batch become visible only after the entire batch completes.
            batch_response_tokens: defaultdict[str, list[Call]] = defaultdict(list)
            batch_action_tokens: defaultdict[str, list[Call]] = defaultdict(list)
            for source in batch:
                if source.response_ok:
                    for token, strength in source.response_tokens.items():
                        if usable_token(token, strength, response_df):
                            batch_response_tokens[token].append(source)
                if source.endpoint.kind == "action" and source.response_ok:
                    # State dependencies require the concrete resource addressed
                    # by the earlier action. Shared body values such as template
                    # IDs, owners, and status strings do not establish ordering.
                    for token, strength in source.resource_tokens.items():
                        if strength >= 3 and usable_token(token, strength, response_df):
                            batch_action_tokens[token].append(source)
                last_endpoint_call[source.endpoint.id] = source
            for token, calls in batch_response_tokens.items():
                producer_index[token] = (batch[0].batch, calls)
            for token, calls in batch_action_tokens.items():
                action_index[token] = (batch[0].batch, calls)

    return edges, node_calls, inference_stats
