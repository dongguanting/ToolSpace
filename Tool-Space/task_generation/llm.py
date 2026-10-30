"""Configurable JSON generation with explicit credentials and persisted evidence."""

import json
import time
from pathlib import Path

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from automation_environment.io import write_json


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        api: str = "chat",
        reasoning_effort: str | None = None,
        temperature: float = 0.7,
        timeout: int = 600,
        retries: int = 3,
        max_output_tokens: int | None = None,
        audit_dir: Path | None = None,
    ):
        def request_hook(request):
            if not api_key:
                request.headers.pop("authorization", None)

        self.client = OpenAI(
            api_key=api_key or "EMPTY",
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=httpx.Client(event_hooks={"request": [request_hook]}),
        )
        self.base_url = base_url
        self.model, self.api, self.reasoning_effort = model, api, reasoning_effort
        self.temperature, self.retries = temperature, retries
        self.max_output_tokens, self.audit_dir = max_output_tokens, audit_dir
        self.calls = (
            max([int(p.stem) for p in audit_dir.glob("*.json") if p.stem.isdigit()], default=0)
            if audit_dir
            else 0
        )

    def close(self):
        self.client.close()

    def json(self, system: str, payload: dict, max_output_tokens: int | None = None) -> dict:
        self.calls += 1
        prompt = json.dumps(payload, ensure_ascii=False)
        limit = max_output_tokens if max_output_tokens is not None else self.max_output_tokens
        for attempt in range(self.retries):
            try:
                if self.api == "responses":
                    request = {"model": self.model, "instructions": system, "input": prompt}
                    if self.reasoning_effort:
                        request["reasoning"] = {"effort": self.reasoning_effort}
                    if limit:
                        request["max_output_tokens"] = limit
                    response = self.client.responses.create(**request)
                    if response.status != "completed":
                        raise ValueError(f"Incomplete response: {response.status}")
                    content = response.output_text
                else:
                    request = {
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": self.temperature,
                    }
                    if self.reasoning_effort:
                        request["reasoning_effort"] = self.reasoning_effort
                    if limit:
                        request["max_completion_tokens"] = limit
                    response = self.client.chat.completions.create(**request)
                    if not response.choices or response.choices[0].finish_reason == "length":
                        raise ValueError("Empty or truncated response")
                    content = response.choices[0].message.content
                if self.audit_dir:
                    write_json(
                        self.audit_dir / f"{self.calls:05d}.json",
                        {"request": request, "response": response.model_dump(mode="json")},
                    )
                if not content:
                    raise ValueError("Model returned no JSON content")
                text = content.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                value = json.loads(text)
                if not isinstance(value, dict):
                    raise ValueError("Expected a JSON object")
                return value
            except (APIConnectionError, APITimeoutError, APIStatusError) as error:
                status = getattr(error, "status_code", None)
                if (status and status != 429 and status < 500) or attempt + 1 == self.retries:
                    raise
                time.sleep(min(30, 2**attempt))
        raise RuntimeError("Request retry budget exhausted")
