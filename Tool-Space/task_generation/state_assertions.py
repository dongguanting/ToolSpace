"""Derive stable business-state evidence from two WorldStates."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

IDENTITY_KEYS = ("id", "ts", "uuid", "key", "resource_name")
VOLATILE_KEYS = {
    "created_at",
    "updated_at",
    "created_date",
    "last_modified_date",
    "timestamp",
    "sent_at",
    "modified_at",
    "date_created",
    "date_updated",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable(value: Any, *, drop_identity: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable(child, drop_identity=drop_identity)
            for key, child in value.items()
            if key not in VOLATILE_KEYS and not (drop_identity and key in IDENTITY_KEYS)
        }
    if isinstance(value, list):
        return [_stable(child, drop_identity=drop_identity) for child in value]
    return value


def _identity_key(before: list[Any], after: list[Any]) -> str | None:
    rows = [row for row in before + after if isinstance(row, dict)]
    for key in IDENTITY_KEYS:
        if rows and all(row.get(key) is not None for row in rows):
            return key
    return None


def _subset(record: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(record, dict) and all(
            key in record and _subset(record[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return record == expected
    return record == expected


def _collection_assertions(
    service: str, collection: str, before: list[Any], after: list[Any]
) -> list[dict[str, Any]]:
    path = f"$.{service}.{collection}"
    identity = _identity_key(before, after)
    if identity is None:
        return (
            [] if before == after else [{"type": "value_equals", "path": path, "expected": after}]
        )

    old = {row[identity]: row for row in before if isinstance(row, dict)}
    new = {row[identity]: row for row in after if isinstance(row, dict)}
    assertions: list[dict[str, Any]] = []

    added = sorted(
        (_stable(new[key], drop_identity=True) for key in new.keys() - old.keys()),
        key=_canonical,
    )
    for encoded, count in Counter(_canonical(row) for row in added).items():
        match = json.loads(encoded)
        final_count = sum(_subset(_stable(row, drop_identity=True), match) for row in after)
        assertions.append(
            {
                "type": "collection_match_count",
                "path": path,
                "match": match,
                "expected_count": final_count,
                "delta": count,
                "operation": "create",
            }
        )

    for key in sorted(old.keys() - new.keys(), key=str):
        assertions.append(
            {
                "type": "collection_match_count",
                "path": path,
                "match": {identity: key},
                "expected_count": 0,
                "operation": "delete",
            }
        )

    for key in sorted(old.keys() & new.keys(), key=str):
        left, right = _stable(old[key]), _stable(new[key])
        changed = {
            field: value
            for field, value in right.items()
            if field != identity and left.get(field) != value
        }
        if changed:
            assertions.append(
                {
                    "type": "collection_match_count",
                    "path": path,
                    "match": {identity: key, **changed},
                    "expected_count": 1,
                    "operation": "update",
                }
            )
    return assertions


def derive_assertions(
    initial: dict[str, Any], final: dict[str, Any], changed_services: set[str]
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    for service in sorted(changed_services):
        before, after = initial.get(service), final.get(service)
        if before == after:
            continue
        if not isinstance(before, dict) or not isinstance(after, dict):
            assertions.append({"type": "value_equals", "path": f"$.{service}", "expected": after})
            continue
        for field in sorted(before.keys() | after.keys()):
            left, right = before.get(field), after.get(field)
            if left == right:
                continue
            if isinstance(left, list) and isinstance(right, list):
                assertions.extend(_collection_assertions(service, field, left, right))
            else:
                assertions.append(
                    {
                        "type": "value_equals",
                        "path": f"$.{service}.{field}",
                        "expected": _stable(right),
                    }
                )
    return assertions
