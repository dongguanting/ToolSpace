"""Resolve concrete API calls against the bundled endpoint catalog."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

READ_METHODS = {"GET", "HEAD", "OPTIONS"}
READ_POST_RE = re.compile(
    r"(?:/search(?:/|$)|:search(?:stream)?(?:\?|$)|/query(?:\?|$)|"
    r"/find(?:/|$)|:find(?:\?|$)|/lookup(?:/|$)|:lookup(?:\?|$)|"
    r"listaccessiblecustomers)",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")


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
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def endpoint_kind(raw: dict[str, Any]) -> str:
    method = str(raw["method"]).upper()
    if method in READ_METHODS:
        return "query"
    if method != "POST":
        return "action"
    path = urlsplit(raw["url"]).path
    if READ_POST_RE.search(path):
        return "query"
    request = str(raw.get("request", "")).lstrip()
    if "graphql" in path.lower() and re.match(r"^(?:query\b|\{)", request, re.I):
        return "query"
    return "action"


@dataclass(frozen=True)
class Endpoint:
    id: str
    service: str
    method: str
    url: str
    description: str
    request: str
    parameter_names: tuple[str, ...]
    kind: str
    regex: re.Pattern[str]
    specificity: int


class EndpointCatalog:
    """Pinned endpoint definitions plus concrete URL resolution."""

    def __init__(self, path: Path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.path = path
        self.metadata = payload.get("metadata", {})
        self.endpoints: list[Endpoint] = []
        self.by_method: dict[str, list[Endpoint]] = defaultdict(list)
        for service in payload["api_endpoint_catalog"]:
            for raw in service["endpoints"]:
                regex, specificity = compile_template(raw["url"])
                endpoint = Endpoint(
                    id=raw["id"],
                    service=raw.get("api", service["api"]),
                    method=raw["method"].upper(),
                    url=raw["url"],
                    description=raw.get("description") or "",
                    request=str(raw.get("request") or ""),
                    parameter_names=tuple(raw.get("parameter_names") or ()),
                    kind=endpoint_kind(raw),
                    regex=regex,
                    specificity=specificity,
                )
                self.endpoints.append(endpoint)
                self.by_method[endpoint.method].append(endpoint)
        self.by_id = {endpoint.id: endpoint for endpoint in self.endpoints}

    def candidates(self, method: str, url: str) -> list[Endpoint]:
        canonical = canonical_url(url)
        return [
            endpoint
            for endpoint in self.by_method.get(method.upper(), ())
            if endpoint.regex.match(canonical)
        ]

    def resolve(
        self,
        method: str,
        url: str,
        arguments: dict[str, Any],
        revealed: set[str],
    ) -> tuple[Endpoint | None, str]:
        candidates = self.candidates(method, url)
        if not candidates:
            return None, "unmatched"
        if len(candidates) == 1:
            return candidates[0], "unique"

        revealed_candidates = [endpoint for endpoint in candidates if endpoint.id in revealed]
        if len(revealed_candidates) == 1:
            return revealed_candidates[0], "revealed_unique"

        params = parse_jsonish(arguments.get("params"))
        body = parse_jsonish(arguments.get("body"))
        params = params if isinstance(params, dict) else {}
        body_text = (
            json.dumps(body, ensure_ascii=False).lower()
            if not isinstance(body, str)
            else body.lower()
        )
        actual_path = unquote(urlsplit(url).path).lower()
        scores: list[tuple[float, Endpoint]] = []

        for endpoint in candidates:
            score = endpoint.specificity / 1000
            if endpoint.id in revealed:
                score += 20
            tail = endpoint.id.rsplit(".", 1)[-1].lower()
            parts = [p for p in re.split(r"[^a-z0-9]+", endpoint.id.lower()) if len(p) > 2]
            score += sum(0.25 for part in parts if part in body_text)
            if params and set(params).issubset(set(endpoint.parameter_names)):
                score += 1

            if endpoint.service == "wave":
                schema_words = set(re.findall(r"[A-Za-z][A-Za-z0-9]+", endpoint.request))
                operation_words = [
                    word
                    for word in schema_words
                    if _compact(word).endswith(_compact(tail))
                    or _compact(tail).endswith(_compact(word))
                ]
                if any(word.lower() in body_text for word in operation_words):
                    score += 100
                if _compact(endpoint.id.split("wave.", 1)[-1]) in _compact(body_text):
                    score += 100

            if endpoint.service == "quickbooks":
                operation = str(params.get("operation", "")).lower()
                void = str(params.get("void", "")).lower()
                body_dict = body if isinstance(body, dict) else {}
                if operation == "delete" and tail == "delete":
                    score += 100
                elif void in {"true", "1"} and tail == "void":
                    score += 100
                elif {"Id", "SyncToken"}.issubset(body_dict) and tail == "update":
                    score += 25
                elif "Id" not in body_dict and tail == "create":
                    score += 15

            if endpoint.service == "intercom":
                if '"message_type": "note"' in body_text and tail == "note":
                    score += 100
                elif '"message_type": "note"' not in body_text and tail == "reply":
                    score += 10

            if endpoint.service == "salesforce":
                object_hint = endpoint.id.split(".")[-2].lower()
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
                if actual_path.endswith("/search") and tail == "search":
                    score += 100
                elif not actual_path.endswith("/search") and tail == "get":
                    score += 10
            scores.append((score, endpoint))

        scores.sort(key=lambda pair: (pair[0], pair[1].specificity, pair[1].id), reverse=True)
        confidence = (
            "heuristic" if len(scores) < 2 or scores[0][0] > scores[1][0] else "ambiguous_tiebreak"
        )
        return scores[0][1], confidence


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())
