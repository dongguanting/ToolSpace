from __future__ import annotations
import json
import os
import urllib.error
import urllib.request

SERPER_ENDPOINT = "https://google.serper.dev/search"
_PSEUDO_COUNTRIES = {"wt", "xa", "xl", "ue"}


def _region_params(region: str | None) -> dict:
    if not region or "-" not in region:
        return {}
    (country, _, language) = region.partition("-")
    out = {}
    if len(country) == 2 and country not in _PSEUDO_COUNTRIES:
        out["gl"] = country
    if len(language) == 2 and language != "wt":
        out["hl"] = language
    return out


def _normalize_results(payload: dict, *, max_results: int, show_snippet: bool) -> list:
    organic = payload.get("organic")
    if not isinstance(organic, list):
        return []
    results = []
    for item in organic[:max_results]:
        if not isinstance(item, dict):
            continue
        (title, href) = (item.get("title"), item.get("link"))
        if not isinstance(title, str) or not isinstance(href, str):
            continue
        row = {"title": title, "href": href}
        if show_snippet:
            snippet = item.get("snippet", "")
            row["body"] = snippet if isinstance(snippet, str) else ""
        results.append(row)
    return results


def _write_search_trace(record: dict) -> None:
    path = os.environ.get("BFCL_SEARCH_TRACE", "").strip()
    if not path:
        return
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _serper_search_engine_query(self, keywords, max_results=10, region="wt-wt"):
    def _done(outcome, result, **extra):
        _write_search_trace(
            {
                "provider": "serper",
                "query": keywords,
                "region": region,
                "max_results": max_results,
                "outcome": outcome,
                "n_results": len(result) if isinstance(result, list) else 0,
                "result": result,
                **extra,
            }
        )
        return result

    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        return _done(
            "no_key",
            {"error": "SERPER_API_KEY is not set in the evaluation environment."},
        )
    try:
        limit = 10 if max_results is None else max(1, int(max_results))
    except (TypeError, ValueError):
        limit = 10
    payload = {"q": keywords, "num": limit}
    payload.update(_region_params(region))
    timeout = float(os.environ.get("SERPER_REQUEST_TIMEOUT", "30"))
    req = urllib.request.Request(
        SERPER_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return _done(
            "http_error",
            {"error": f"Serper HTTP {exc.code}: {detail or exc.reason}"},
            http_status=exc.code,
        )
    except Exception as exc:
        return _done(
            "transport_error",
            {"error": f"Serper request failed: {type(exc).__name__}: {exc}"},
        )
    if not isinstance(body, dict):
        return _done(
            "bad_payload", {"error": "Serper returned a non-object JSON payload."}
        )
    organic = body.get("organic")
    if not isinstance(organic, list) or not organic:
        return _done(
            "no_organic",
            {"error": "Serper returned no organic search results for this query."},
        )
    results = _normalize_results(
        body, max_results=limit, show_snippet=bool(getattr(self, "show_snippet", True))
    )
    if not results:
        return _done(
            "unrecognized_shape",
            {"error": "Serper returned results in an unrecognized shape."},
        )
    return _done("ok", results, show_snippet=bool(getattr(self, "show_snippet", True)))


def install() -> str:
    want = os.environ.get("BFCL_SEARCH_PROVIDER", "auto").strip().lower()
    has_serpapi = bool(os.environ.get("SERPAPI_API_KEY", "").strip())
    has_serper = bool(os.environ.get("SERPER_API_KEY", "").strip())
    if want == "serpapi" or (want == "auto" and has_serpapi):
        return "serpapi" if has_serpapi else "none"
    if want not in ("auto", "serper"):
        raise RuntimeError(
            f"BFCL_SEARCH_PROVIDER={want!r} is not one of auto|serpapi|serper"
        )
    if not has_serper:
        return "none"
    from bfcl_eval.eval_checker.multi_turn_eval.func_source_code.web_search import (
        WebSearchAPI,
    )

    if getattr(WebSearchAPI, "_bfcl_serper_installed", False):
        return "serper"
    WebSearchAPI.search_engine_query = _serper_search_engine_query
    WebSearchAPI._bfcl_serper_installed = True
    return "serper"
