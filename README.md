# ToolSpace

ToolSpace is a reproducible workspace for generating, evaluating, and replaying
tool-using automation tasks. The repository contains the released Automation-Pro
benchmark, the Tool-Space task-generation pipeline, and the supporting projects
listed below.

## Repository layout

```text
ToolSpace/
├── Automation-Pro/       # 300-task benchmark, simulator, verifiers, evaluator
├── Tool-Space/           # workflow graph and task-generation pipeline
├── Tau3-Banking-Pro/     # related banking benchmark
└── evaluation/           # shared evaluation utilities
```

`Automation-Pro` is the runnable benchmark release. `Tool-Space` is the
pipeline used to observe tool relationships, sample workflows, generate tasks,
and replay native verifiers. The two projects can be used independently.

## Quick validation

Automation-Pro requires Python 3.10 or newer:

```bash
cd Automation-Pro
python -m pip install -r requirements.txt
python evaluate.py validate
```

Tool-Space requires Python 3.13 or newer and `uv`:

```bash
cd Tool-Space
uv sync --locked
uv run python run_pipeline.py demo --output outputs/demo
uv run python run_pipeline.py verify --tasks outputs/demo/tasks
```

The commands above run entirely against local simulators. They do not contact
third-party services and do not require credentials.

## Model evaluation

Automation-Pro supports replaying a JSONL prediction file and evaluating a
model through an OpenAI-compatible Chat Completions or Responses endpoint. Put
credentials in environment variables only; never commit `.env` files or keys:

```bash
cd Automation-Pro
export OPENAI_API_KEY="your-key"
python evaluate.py run --model your-model \
  --base-url https://api.example.com/v1 \
  --api responses --api-key-env OPENAI_API_KEY \
  --output results/local-run --concurrency 4
```

Use `--api chat` for Chat Completions endpoints. Replace `--model` and
`--base-url` for the provider in use; `--api-key-env` selects the environment
variable containing its key.

The optional `run_deepseek.py` wrapper supplies the Tensoris model defaults,
including the configured reasoning settings. It reads `TENSORIS_API_KEY` from
the environment and never writes the value to output files:

```bash
export TENSORIS_API_KEY="your-key"
python run_deepseek.py --limit 1 --concurrency 1 \
  --output results/smoke
```

Generated `results/`, `outputs/`, caches, virtual environments, and local
credentials are release-excluded by `.gitignore`.

## Data handling and licensing

The benchmark identities, organizations, email addresses, records, IDs, and
timestamps are synthetic fixtures created for evaluation. They are not a
customer or production data export. The release contains no API keys, bearer
tokens, private keys, or machine-specific absolute paths. Domains ending in
`.example`, `.example.com`, `.example.org`, or `.example.net` are reserved
placeholders; service hostnames remain only where needed to describe an API
contract.

Automation-Pro includes the applicable license and notices in its directory.
Tool-Space includes its own `NOTICE` and preserves third-party attribution in
the vendored AutomationBench material. Review the notices before redistributing
modified copies.
