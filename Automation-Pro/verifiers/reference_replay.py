"""Resolve references to actual API responses in diagnostic replay steps."""

import base64
import binascii
import json


def resolve(value, replacements, field=None):
    if isinstance(value, dict):
        return {k: resolve(v, replacements, k) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, replacements) for v in value]
    if not isinstance(value, str):
        return value
    if field in {"raw", "data"}:
        try:
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        except (ValueError, UnicodeDecodeError, binascii.Error):
            pass
        else:
            updated = resolve(decoded, replacements)
            if updated != decoded:
                return base64.urlsafe_b64encode(updated.encode()).decode()
    # A single pass prevents one replacement from being rewritten by another.
    import re

    if not replacements:
        return value
    pattern = "|".join(re.escape(v) for v in sorted(replacements, key=len, reverse=True))
    return re.sub(pattern, lambda match: replacements[match.group()], value)


def execute_steps(env, steps, errors):
    replacements = {}
    for number, step in enumerate(steps, 1):
        try:
            resolved = resolve(step, replacements)
            observation = env.execute("api_fetch", {
                "method": resolved["method"], "url": resolved["url"],
                "params": json.dumps(resolved.get("query") or {}),
                "body": json.dumps(resolved.get("body") or {}),
            })
            response = json.loads(observation)
            if isinstance(response, dict) and ("error" in response or response.get("ok") is False):
                errors.append({"call": number, "error": response})
                continue
            for binding in step.get("response_bindings", []):
                actual = response
                for key in binding["path"]:
                    actual = actual[key]
                if not isinstance(actual, str) or not actual:
                    raise ValueError("Reference response binding must resolve to a nonempty string")
                replacements[binding["reference"]] = actual
        except (ValueError, TypeError, KeyError, IndexError) as error:
            errors.append({"call": number, "error": str(error)})
