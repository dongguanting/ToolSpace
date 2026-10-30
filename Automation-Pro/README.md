# Automation-Pro

Automation-Pro is a reproducible benchmark for agents that complete business
workflows through API-style tools. The release contains 300 frozen tasks across
six domains: marketing, finance, HR, operations, sales, and support (50 tasks
per domain). Tasks run against a local simulator and are scored by stateful
verifiers; replay and validation do not need network access.

## Install and validate

Use Python 3.10 or newer:

```bash
python -m pip install -r requirements.txt
python evaluate.py validate
```

Validation checks the 300 task contracts, domain balance, question alignment,
assertion registrations, initial-state schemas, and release checksums.

## Replay predictions

Create a JSONL file with one record per task. Each record must contain
`example_id` and either native `calls` or API `steps` with `method`, `url`,
`query`, and `body` fields:

```bash
python evaluate.py replay --input predictions.jsonl \
  --output results/replay
```

Use `--domain`, `--example-id`, or `--limit` to select a subset. Replay mutates
an isolated in-memory world and uses the same verifiers as model runs.

## Evaluate a model

The generic runner uses an OpenAI-compatible endpoint. Credentials are read
from the selected environment variable and are never written to result files:

```bash
export OPENAI_API_KEY="your-key"
python evaluate.py run --model my-model \
  --base-url https://api.example.com/v1 \
  --api responses --api-key-env OPENAI_API_KEY \
  --output results/my-model --concurrency 4
```

Use `--api chat` for an OpenAI-compatible Chat Completions endpoint. Replace
`--model` and `--base-url` with the provider-specific model name and endpoint;
`--api-key-env` names the environment variable containing the key.

For the Tensoris DeepSeek Chat Completions configuration:

```bash
export TENSORIS_API_KEY="your-key"
python run_deepseek.py --output results/deepseek-v4.1-flash-1017
```

The wrapper defaults to model `deepseek-v4.1-flash-1017`, base URL
`https://api.tensoris.ai/v1`, Chat Completions, concurrency 80, and a maximum
of 100 model turns per task. It sends
`reasoning: {"context": "all_turns", "effort": "max"}` and preserves the
conversation history needed by the endpoint. Override the defaults with
`--concurrency`, `--max-turns`, `--domain`, `--limit`, or `--example-id`.

Each output directory must be new. A run writes `results.jsonl`,
`summary.json`, `config.json`, and per-task traces. `completed`, `turn_limit`,
and `model_error` describe execution status; task correctness is reported
separately by `task_completed_correctly` and `reward`.

## Data layout

* `data/tasks.jsonl`: canonical 300-task evaluation input.
* `data/questions.jsonl`: question metadata and task IDs.
* `data/index.json`: domain and provenance index.
* `data/manifest.json`: release file checksums.
* `verifiers/reference_trajectories.jsonl`: diagnostic reference API
  trajectories for replay and analysis.
* `environments/`: local schemas, deterministic API routes, and the simulator.

Reference trajectories and verifier targets are evaluator-side artifacts; do not
send them to an agent during a benchmark run.

## Release privacy

All benchmark people, organizations, addresses, records, IDs, and timestamps
are synthetic fixtures. The release contains no API keys, bearer tokens, private
keys, or machine-specific absolute paths. Placeholder domains use reserved
`.example` namespaces where appropriate. Do not commit `.env`, virtual
environments, or generated `results/` directories; the repository ignore rules
exclude them.

## License

See `LICENSE` and `NOTICE.md`. Third-party API schema representations retain
their original attribution and are not claimed as original API intellectual
property.
