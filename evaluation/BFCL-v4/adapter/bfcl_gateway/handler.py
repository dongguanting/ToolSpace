"""OpenAI-compatible transport for the official BFCL handler."""

import json, os
import httpx
from openai import OpenAI
from bfcl_eval.model_handler.api_inference.openai_completion import (
    OpenAICompletionsHandler,
)


class GatewayHandler(OpenAICompletionsHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = OpenAI(
            **{
                **self._build_client_kwargs(),
                "timeout": httpx.Timeout(
                    float(os.getenv("LLM_TIMEOUT", "1800")), connect=30
                ),
                "max_retries": 3,
            }
        )

    def generate_with_backoff(self, **kwargs):
        extra = json.loads(os.getenv("MODEL_EXTRA_BODY", "{}"))
        if extra:
            kwargs["extra_body"] = {**extra, **kwargs.get("extra_body", {})}
        response, latency = super().generate_with_backoff(**kwargs)
        if response.usage is None:
            from openai.types import CompletionUsage

            response.usage = CompletionUsage(
                prompt_tokens=0, completion_tokens=0, total_tokens=0
            )
        return response, latency
