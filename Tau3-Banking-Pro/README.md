# Tau3 Banking Pro — 98 tasks

Tau3 Banking Pro (Pro98) is a frozen collection of 98 generated banking customer-service tasks. An agent converses with a simulated customer, retrieves banking policies, and uses stateful banking tools. The native Tau3 evaluator scores the resulting database state against each task's reference outcome.

This challenge collection is separate from the canonical 97-task Banking Knowledge benchmark. Report results as **Pro98**, with the model and retrieval configuration used.

This directory includes the task inputs, transactional database, all 698 knowledge documents, policy prompts, user-simulator guidelines, modified Tau3 runtime, and native evaluators. It can be copied independently of the surrounding workspace. No model credentials, virtual environments, embeddings cache, or historical trajectories are included.

## Why Pro?

Pro98 emphasizes composing multiple discoverable banking capabilities. Its reference workflows invoke a median of four distinct discoverable tools, compared with two in the original benchmark. References combining multiple business WRITE calls are also more common.

| Structural trait | Original (97 tasks) | Pro (98 tasks) |
| --- | ---: | ---: |
| Distinct discoverable tools invoked per reference, median | 2 | 4 |
| References invoking ≥4 distinct discoverable tools | 35.1% (34/97) | 65.3% (64/98) |
| References containing ≥2 business WRITE calls | 69.1% (67/97) | 93.9% (92/98) |
| Required documents listed per task, mean | 9.78 | 10.63 |
| Golden/reference actions per task, mean | 9.85 | 13.87 |

These statistics describe the supplied reference solutions and include agent and customer calls. Discoverable-tool diversity counts distinct underlying tool names after resolving the invocation wrappers. Business WRITE calls use the actual direct or underlying tool's WRITE classification, excluding verification and tool-access setup; they count calls rather than guaranteed distinct state changes.

Reference actions are not minimum required agent steps. Pro references include more explicit identity lookups and clock checks, which contribute to the higher action count. Required-document lists identify relevant documents without requiring every file to be read. Average business WRITE calls are 3.65 for the original set and 3.40 for Pro; some original tasks also have longer references. The main structural difference is broader typical tool composition and more consistent multi-operation workflows.

## Baseline results

Reported success rates on the 98-task Pro98 collection range from 12.2% to 35.7%, leaving substantial room for improvement.

Results from the [ToolSpace Pro benchmarks](https://toolspace-agent.github.io/#pro).

| Model | Original success rate | Pro success rate | Δ (Pro − original, pp) |
| --- | ---: | ---: | ---: |
| Claude Opus 5.5 | 51.5% | 35.7% | −15.8 |
| GPT 6.1 Sol | 46.4% | 29.6% | −16.8 |
| deepseek-V4.1-Flash | 47.4% | 35.7% | −11.7 |
| GLM 5.3 | 40.2% | 20.4% | −19.8 |
| Qwen 3.8 Max | 55.2% | 29.6% | −25.6 |
| GLM 5.2 | 37.1% | 12.2% | −24.9 |

## Structure

```text
Tau3-Banking-Pro/
├── data/
│   ├── tasks.json                   # Frozen ordered list of 98 task objects
│   ├── manifest.json                # Local paths, task IDs and SHA-256 hashes
│   ├── public_task_catalog.json     # Stable public IDs and workflow titles
│   └── tau2/
│       ├── domains/banking_knowledge/
│       │   ├── db.json
│       │   ├── documents/           # 698 knowledge documents
│       │   ├── prompts/             # Banking policy and retrieval prompts
│       │   ├── tasks.json           # Same frozen task list
│       │   └── tasks/<task_id>.json  # 98 native-loader task files
│       └── user_simulator/          # Official customer-simulation guidelines
├── environments/
│   ├── run_task.py                  # One-task simulation worker
│   └── tau2/                       # Copied modified framework and banking domain
│   │   ├── domains/banking_knowledge/
│   │   └── evaluator/__init__.py    # Routes tau2.evaluator imports to verifiers/
├── verifiers/                      # Unchanged native evaluator implementation
├── evaluate.py                     # Offline validation and suite launcher
├── requirements.txt                # Recorded Python dependency versions
├── .env.example
├── LICENSE                         # Upstream Tau2 MIT license
└── README.md
```

The full framework is retained because the banking domain imports shared task schemas, tool execution, orchestrators, user simulation, retrieval, and registry code. Evaluation uses `verifiers/evaluator.py` through its original `tau2.evaluator` import name. The scoring code is copied without rewriting its reward logic.

## Setup and offline validation

Use Linux and Python 3.12 (the source supports Python >=3.12,<3.14). From this directory:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python evaluate.py --check-only
```

The check verifies all imported source/data hashes, exact task membership, per-task equality, task schemas, the native domain loader, and the relocated evaluator import. It makes no model or embedding calls. Select tasks with public IDs such as `task_generated_pro_001`, or use `--limit 1`; `--tasks` accepts IDs listed in the public task catalog and rejects unknown IDs. Results and output directories use public IDs and concise workflow titles. The task IDs retain Tau3's generated-task prefix because the runtime uses that prefix for outcome-only read logging.

The default `alltools` retrieval configuration exposes BM25, dense retrieval, and the read-only knowledge shell. Live use additionally needs Node.js, `rg`, `bwrap`, `socat`, and `@anthropic-ai/sandbox-runtime@0.0.23`, with working Linux user namespaces:

```bash
npm install --prefix .runtime/shell --no-save @anthropic-ai/sandbox-runtime@0.0.23
```

Install `ripgrep`, `bubblewrap`, and `socat` using your operating system's package manager when absent. `SRT_BIN_DIR` can select an existing sandbox-runtime binary directory.

## Model configuration and evaluation

Copy `.env.example` to a private `.env`, fill in the endpoint and credentials, then export it:

```bash
cp .env.example .env
chmod 600 .env
# Edit .env before sourcing it.
set -a
source .env
set +a
```

`MODEL` / `MODEL_BASE_URL` / `MODEL_API_KEY` select the banking agent. `USER_BASE_URL` and `USER_API_KEY` select the auxiliary endpoint for the customer simulator, optional reviewer, and dense retrieval. `BOYUE_BASE_URL` / `BOYUE_API_KEY` are supported auxiliary fallbacks. An optional `EMBEDDING_API_KEY` overrides the dense-retrieval key. The default dense model is `text-embedding-3-large`; preserve embedding model, endpoint and retrieval variant when comparing runs. Use `EMPTY` for a local agent endpoint without authentication.

After validating the desired routes and retrieval sandbox, a bounded one-task invocation is:

```bash
.venv/bin/python evaluate.py \
  --limit 1 --concurrency 1 --output outputs/pro98-smoke-001 \
  --reasoning-effort max --no-review
```

Select a reasoning effort supported by your model endpoint. For all 98 tasks:

```bash
.venv/bin/python evaluate.py \
  --concurrency 8 --output outputs/pro98-pass-001 \
  --reasoning-effort max --no-review
```

Defaults are seed 300, 200 steps, agent temperature 1.0, GPT-5.2 customer simulation with low reasoning, `alltools` retrieval, 1,800 seconds per simulation, 2,400 seconds per worker, and no post-simulation review. `--review` enables GPT-5.2 low-reasoning conversation/authentication review. Native scoring remains enabled in either mode. Provider-specific options can be set through `MODEL_EXTRA_BODY`; `--max-tokens` sets an explicit per-response output limit. The launcher supports `--tasks`, `--limit`, explicit model/API flags, and `--help`.

Each invocation requires a fresh output directory. Outputs contain a credential-free `config.json`, aggregate `summary.json`, and one directory per selected public task ID with task input, worker log, native artifacts, `simulation.json`, `summary.json`, and `status.json`. Result and status summaries include the public ID and workflow title. Failed workers preserve partial evidence; their aggregate reward is `null` rather than 0. A native task failure may legitimately score 0. The aggregate score is a fraction from 0 to 1 and remains `null` until every selected task has a finite evaluator verdict without a worker error.

## Scoring and integration

All 98 tasks use `reward_basis: ["DB"]`. The native environment evaluator reconstructs the initialized environment, replays reference actions to produce the target state, replays the agent trajectory, and compares database state using the copied banking-specific rules and supported reference alternatives. A reference action list describes a route to the target state; it does not require an agent to reproduce that exact call sequence. Native abnormal-termination rules are retained.

Task descriptions provide concise scenario purposes; customer instructions and scoring data are stored in their respective task fields. Task JSON contains hidden initialization and scoring information. An external agent harness should receive only the official customer-facing messages, banking policy, current tool schemas, and tool results. Keep task JSON, database internals, verifier code, and credentials outside the agent's accessible workspace. Run tools through the official environment and retain the official simulation/result format.

Offline validation checks package integrity and task schemas. Run a one-task smoke evaluation to verify your model endpoints and retrieval sandbox before evaluating the full collection.
