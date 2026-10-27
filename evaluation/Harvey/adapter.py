"""Endpoint configuration for Harvey's native GPT adapter and compatible APIs."""

import json, os, time
from types import SimpleNamespace
from openai import OpenAI, BadRequestError, InternalServerError
from harness.adapters.base import ModelAdapter, ModelResponse, ToolCall
from harness.adapters.openai import OpenAIAdapter


class ResponsesAdapter(OpenAIAdapter):
    """Use the official GPT context and tool protocol with a configured endpoint."""

    def __init__(
        self,
        model,
        base_url,
        key,
        temperature=0.0,
        max_tokens=128000,
        reasoning_effort=None,
    ):
        ModelAdapter.__init__(self, model, temperature, reasoning_effort)
        self.max_tokens = max_tokens
        self._endpoint = OpenAI(
            base_url=base_url, api_key=key, timeout=1800, max_retries=3
        )
        self.client = SimpleNamespace(
            responses=SimpleNamespace(create=self._create_response)
        )
        self._context = []
        self._system_instructions = None

    def _create_response(self, **kwargs):
        started = last_log = time.monotonic()
        count = characters = 0
        with self._endpoint.responses.stream(**kwargs) as stream:
            for event in stream:
                count += 1
                delta = getattr(event, "delta", None)
                if isinstance(delta, str):
                    characters += len(delta)
                now = time.monotonic()
                if count == 1 or now - last_log >= 30:
                    print(
                        f"Responses stream: {count} events, {characters} characters, "
                        f"{now - started:.1f}s",
                        flush=True,
                    )
                    last_log = now
            return stream.get_final_response()

    def chat(self, messages, tools):
        started = time.monotonic()
        print(f"Responses request: {len(self._context)} context items", flush=True)
        result = super().chat(messages, tools)
        print(
            f"Responses reply: {result.input_tokens} input tokens, "
            f"{result.output_tokens} output tokens, "
            f"{time.monotonic() - started:.1f}s",
            flush=True,
        )
        return result


def candidate_settings():
    model = os.environ["MODEL"].split("/")[-1]
    api = os.getenv(
        "HARVEY_API",
        "responses" if model.startswith(("gpt", "o1", "o3", "o4")) else "chat",
    )
    if api not in {"responses", "chat"}:
        raise ValueError("HARVEY_API must be responses or chat")
    if api == "responses" and json.loads(os.getenv("MODEL_EXTRA_BODY", "{}")):
        raise ValueError(
            "The official Responses adapter uses MODEL_REASONING_EFFORT; "
            "unset MODEL_EXTRA_BODY or select HARVEY_API=chat"
        )
    return {
        "api": api,
        "temperature": float(os.getenv("MODEL_TEMPERATURE", "0")),
        "max_tokens": int(
            os.getenv(
                "HARVEY_MAX_OUTPUT_TOKENS", "128000" if api == "responses" else "32768"
            )
        ),
        "reasoning_effort": os.getenv("MODEL_REASONING_EFFORT"),
    }


def candidate_adapter(settings):
    kwargs = {k: v for k, v in settings.items() if k != "api"}
    adapter = ResponsesAdapter if settings["api"] == "responses" else ChatAdapter
    return adapter(
        os.environ["MODEL"],
        os.environ["MODEL_BASE_URL"],
        os.environ["MODEL_API_KEY"],
        **kwargs,
    )


class ChatAdapter(ModelAdapter):
    def __init__(
        self,
        model,
        base_url,
        key,
        temperature=0,
        max_tokens=32768,
        reasoning_effort=None,
    ):
        super().__init__(model, temperature, reasoning_effort)
        self.max_tokens = max_tokens
        self.client = OpenAI(
            base_url=base_url, api_key=key, timeout=1800, max_retries=3
        )

    def chat(self, messages, tools):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "tools": [{"type": "function", "function": t} for t in tools],
        }
        extra = json.loads(os.getenv("MODEL_EXTRA_BODY", "{}"))
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if extra:
            kwargs["extra_body"] = extra
        response = self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        body = message.model_dump(exclude_none=True)
        if getattr(message, "reasoning_content", None):
            body["reasoning_content"] = message.reasoning_content
        return ModelResponse(
            message=body,
            text=message.content or "",
            tool_calls=[
                ToolCall(id=t.id, name=t.function.name, arguments=t.function.arguments)
                for t in message.tool_calls or []
            ],
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            finish_reason=response.choices[0].finish_reason,
        )

    def make_system_message(self, content):
        return {"role": "system", "content": content}

    def make_user_message(self, content):
        return {"role": "user", "content": content}

    def make_tool_result_messages(self, results):
        return [{"role": "tool", "tool_call_id": i, "content": r} for i, r in results]


class GatewayJudge:
    def __init__(self):
        self.model = os.getenv("HARVEY_JUDGE_MODEL", "glm-5.2")
        self.api = (
            "responses"
            if self.model.split("/")[-1].startswith(("gpt", "o1", "o3", "o4"))
            else "chat"
        )
        self.reasoning_effort = os.getenv("HARVEY_JUDGE_REASONING_EFFORT")
        self.client = OpenAI(
            base_url=os.environ["BOYUE_BASE_URL"],
            api_key=os.environ["BOYUE_API_KEY"],
            timeout=1800,
            max_retries=3,
        )

    def json_text(self, messages, max_tokens, temperature=0.0, schema=None):
        if self.api == "responses":
            format_config = (
                {
                    "type": "json_schema",
                    "name": "evaluation",
                    "schema": schema,
                    "strict": True,
                }
                if schema
                else {"type": "json_object"}
            )
            kwargs = {
                "model": self.model,
                "input": messages,
                "max_output_tokens": max_tokens,
                "text": {"format": format_config},
            }
            if self.reasoning_effort:
                kwargs["reasoning"] = {"effort": self.reasoning_effort}
            else:
                kwargs["temperature"] = temperature
            with self.client.responses.stream(**kwargs) as stream:
                response = stream.get_final_response()
            if response.status == "incomplete":
                raise ValueError("Truncated judge response")
            messages = [item for item in response.output if item.type == "message"]
            final = [
                item
                for item in messages
                if getattr(item, "phase", None) == "final_answer"
            ]
            if not messages:
                raise ValueError("Judge returned no final text")
            message = (final or messages)[-1]
            return "\n".join(
                part.text for part in message.content if hasattr(part, "text")
            )
        format_config = (
            {
                "type": "json_schema",
                "json_schema": {"name": "evaluation", "schema": schema, "strict": True},
            }
            if schema
            else {"type": "json_object"}
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=format_config,
        )
        if response.choices[0].finish_reason == "length":
            raise ValueError("Truncated judge response")
        return response.choices[0].message.content or ""

    def evaluate(self, prompt_template, variables, temperature=0.0, **kwargs):
        from evaluation.judge import Judge, _VERDICT_SCHEMA

        prompt = prompt_template.format(**variables)
        error = None
        for attempt in range(2):
            try:
                text = self.json_text(
                    [{"role": "user", "content": prompt}],
                    max_tokens=16384,
                    temperature=temperature,
                    schema=_VERDICT_SCHEMA if attempt == 0 else None,
                )
                return Judge._parse_json(text)
            except (
                ValueError,
                json.JSONDecodeError,
                BadRequestError,
                InternalServerError,
            ) as exc:
                error = exc
        raise ValueError("Invalid judge JSON") from error

    def evaluate_from_file(self, prompt_name, variables):
        from evaluation.judge import PROMPTS_DIR

        return self.evaluate(
            (PROMPTS_DIR / (prompt_name + ".txt")).read_text(), variables
        )


class MatcherClient:
    def __init__(self, judge):
        self.messages = self
        self.judge = judge

    def create(self, **kwargs):
        # Preserve the official filename-matching prompt and return structure.
        from evaluation.judge import Judge

        schema = kwargs.get("output_config", {}).get("format", {}).get("schema")
        text = self.judge.json_text(
            kwargs["messages"],
            temperature=kwargs.get("temperature", 0),
            max_tokens=kwargs.get("max_tokens", 1024),
            schema=schema,
        )
        parsed = Judge._parse_json(text)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(parsed))])
