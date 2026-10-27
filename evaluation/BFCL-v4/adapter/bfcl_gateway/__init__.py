import json
import os
import re

_TRUE = {"1", "true", "yes", "on"}


def sanitize_registry_id(name: str) -> str:
    out = re.sub(r"[^0-9A-Za-z.\-]+", "-", name).strip("-")
    out = out.replace("_", "-")
    out = out.replace("FC", "Fc")
    return out or "gateway-model"


def _env_extra_body() -> dict:
    raw = os.getenv("BFCL_GW_EXTRA_BODY", "").strip()
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"BFCL_GW_EXTRA_BODY is not valid JSON: {exc}") from exc
    if not isinstance(val, dict):
        raise RuntimeError("BFCL_GW_EXTRA_BODY must be a JSON object")
    return val


def _validate_transport_env() -> None:
    raw = os.getenv("BFCL_GW_TIMEOUT", "").strip()
    if raw:
        try:
            val = float(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"BFCL_GW_TIMEOUT={raw!r} is not a number (seconds)"
            ) from exc
        if val <= 0:
            raise RuntimeError(f"BFCL_GW_TIMEOUT={raw!r} must be > 0 seconds")

    raw = os.getenv("BFCL_GW_MAX_RETRIES", "").strip()
    if raw:
        try:
            val = int(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"BFCL_GW_MAX_RETRIES={raw!r} is not an integer"
            ) from exc
        if val < 0:
            raise RuntimeError(f"BFCL_GW_MAX_RETRIES={raw!r} must be >= 0")


_ENDPOINT_SUFFIX = re.compile(r"/(?:chat/)?completions/?$")


def normalize_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    while True:
        trimmed = _ENDPOINT_SUFFIX.sub("", url)
        if trimmed == url:
            return url
        url = trimmed


def register() -> str | None:
    base_url = normalize_base_url(os.getenv("BFCL_GW_BASE_URL", ""))
    wire_model = os.getenv("BFCL_GW_MODEL", "").strip()
    api_key = os.getenv("BFCL_GW_API_KEY", "").strip()

    if not (base_url and wire_model):
        return None  # nothing to do; official models still work

    _validate_transport_env()

    registry_id = os.getenv("BFCL_GW_REGISTRY_ID", "").strip() or sanitize_registry_id(
        wire_model
    )
    bad = [c for c in ("_", "/") if c in registry_id]
    if bad:
        raise RuntimeError(
            f"BFCL_GW_REGISTRY_ID={registry_id!r} contains {bad!r}; "
            "these break BFCL's result-dir name round-trip. Use hyphens."
        )

    os.environ["OPENAI_BASE_URL"] = base_url
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    else:
        os.environ.setdefault("OPENAI_API_KEY", "EMPTY")

    from bfcl_eval.constants.model_config import MODEL_CONFIG_MAPPING, ModelConfig

    from .handler import GatewayHandler

    is_fc = os.getenv("BFCL_GW_FC", "1").strip().lower() in _TRUE

    MODEL_CONFIG_MAPPING[registry_id] = ModelConfig(
        model_name=wire_model,  # what goes on the wire
        display_name=registry_id,  # display-only, cannot move a score
        url=base_url,  # display-only
        org="self-hosted",  # display-only
        license="unknown",  # display-only
        model_handler=GatewayHandler,
        input_price=None,  # None => cost column falls to GPU-hours estimate
        output_price=None,
        is_fc_model=is_fc,  # SCORING-load-bearing
        underscore_to_dot=is_fc,
    )
    return registry_id
