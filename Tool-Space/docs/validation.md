# Validation

Run from `Tool-Space/`:

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python run_pipeline.py demo --output outputs/check
uv run python run_pipeline.py verify --tasks outputs/check/tasks
uv build
```

The tests cover independent replay, state-hash corruption, native scoring, equivalent prose, cached resume, offline execution, parallel-batch graph dependencies, checkpoint integrity, Chat/Responses serialization, and the full `run` entry point for accepted and rejected tasks.

The offline example has 758 catalog nodes, four observed edges and a five-call Sheets/Slack workflow. It generates one task with two native assertions, then independently replays the exported row. Model responses are fixtures; simulator execution is real.

A prior live Chat Completions run with `glm-5.2` and reasoning effort `high` accepted one task with no runtime errors. Its two native assertions and independent reference replay received reward 1.0. The same exported task is replayed with the reorganized runtime during local release verification. One synthetic workflow establishes integration, not broad generation quality or acceptance yield.

Release checks include import resolution for retained environment modules, all 47 schema files and the vendor license in the wheel, source-distribution contents, and execution from an independently installed wheel. Native scoring remains unchanged, including its existence/keyword matching limitations.

Original default prompt constants, difficulty functions and retained state-evidence helpers match the archive by Python AST. The 528 files fingerprinted in the original archive before extraction remain unchanged. Local scratch outputs and model-call records are excluded from the repository; only synthetic inputs, fixtures and reproducible tests are included.
