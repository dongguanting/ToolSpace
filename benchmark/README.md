# ToolSpace Benchmarks

Seven benchmarks against an OpenAI-compatible model endpoint. Each directory contains its launcher, dependencies, evaluator code, and `SKILL.md`.

| Directory | Evaluation set | Score | Default configuration |
| --- | --- | --- | --- |
| `AutomationBench` | v1.0.6, 600 public tasks | strict task pass rate | API toolset, 50 steps, temperature 1.0 |
| `SkillsBench` | v1.1, 79 self-contained tasks × 3 trials | mean official verifier reward | OpenHands SDK 1.23.0; each task's declared time budget |
| `JobBench` | main split, 65 tasks | mean rounded per-task rubric score | OpenCode v1.14.18, 7,200 s/task, 2 attempts, 10 workers; Grok-4.3 judge |
| `Tau3-Banking` | Banking Knowledge, 97 tasks | task success rate | 200 steps, seed 300; GPT-5.2 user and judge, reasoning effort low |
| `BFCL-V4` | official `all_scoring`, 5,217 cases | official weighted overall accuracy | commit `6ea5797`, temperature 0.001 |
| `Harvey-LAB` | fixed proportional sample, 120 tasks | official all-pass rate | native Podman sandbox, 200 turns, temperature 0; GLM-5.2 judge |
| `OfficeQA-Pro` | Pro, 133 questions | official answer accuracy | temperature 0, 200 steps, 8,192 tokens/turn, last 2 interactions, effort max, 2,700 s/question, tolerance 0.05 |

## Install

Use a Linux server with Python, Git, Node.js 18+/npm, Docker (SkillsBench), Podman (Harvey), and [uv](https://docs.astral.sh/uv/getting-started/installation/). On Ubuntu 24.04:

```bash
sudo apt-get update
sudo apt-get install -y git curl nodejs npm docker.io podman jq pandoc libreoffice qpdf unzip fonts-liberation poppler-utils ripgrep bubblewrap socat build-essential
```

Docker and Podman must be able to start containers. Tau3's read-only knowledge shell also requires user namespaces for `bubblewrap`. Evaluation runs on CPU.

```bash
cd benchmark
cp .env.example .env
chmod 600 .env
# Edit .env with your model endpoint and API keys.
bash setup_all.sh
```

Each benchmark gets its own Python environment under `.runtime/`. Setup installs the pinned `requirements.txt`; Python 3.13 is used for AutomationBench, 3.10 for BFCL, and 3.12 for the other benchmarks. Bun and the knowledge-shell runtime are installed under `.runtime/`. Setup installs Harvey's public document sandbox at a fixed image digest. SkillsBench task images are built on demand.

To install only one benchmark, run its `setup.sh`, for example `bash Tau3-Banking/setup.sh`.

## Model APIs and data

Set `MODEL`, `MODEL_BASE_URL` (ending in `/v1`), and `MODEL_API_KEY` in `.env`. A local vLLM server or a hosted OpenAI-compatible endpoint works. Use `EMPTY` for a local endpoint without authentication.

For external models, register at [Boyue](https://api.boyuerichdata.opensphereai.com/) and create an API token ([API documentation](https://boyuerichdata.apifox.cn/)). Set `BOYUE_API_KEY`; the default API base is `https://api.boyuerichdata.opensphereai.com/v1`. These external calls are required:

- Tau3: GPT-5.2 for the user simulator and judge, and `text-embedding-3-large` for dense retrieval. The `alltools` configuration exposes BM25, dense top-10 retrieval, and a read-only knowledge-base shell.
- JobBench: Grok-4.3 rubric judge.
- Harvey: GLM-5.2 rubric judge and deliverable filename matching.
- BFCL: a [SerpAPI](https://serpapi.com/) key for official web search, or a [Serper](https://serper.dev/) key for the included alternative.

OfficeQA Pro requires accepting access at [databricks/officeqa](https://huggingface.co/datasets/databricks/officeqa) and setting `HF_TOKEN` to a Hugging Face read token. Setup downloads the parsed document corpus for the included 133 questions. JobBench's main split and SkillsBench v1.1 task files are downloaded automatically at fixed revisions. The other evaluation inputs are included. The exact OfficeQA question IDs are in `OfficeQA-Pro/task_ids.txt`.

For a model hosted through Boyue, set `MODEL_BASE_URL` to `BOYUE_BASE_URL` and `MODEL_API_KEY` to your Boyue token as well. The candidate and auxiliary models can use different endpoints.

## Run

First check one task:

```bash
SCOPE=smoke RUN_NAME=my-model-smoke bash AutomationBench/run.sh
```

Run one full benchmark or all seven:

```bash
RUN_NAME=my-model bash AutomationBench/run.sh
RUN_NAME=my-model bash run_all.sh
```

Replace the directory name to run another benchmark. All launchers accept `--limit N`, `--tasks ID,ID`, and `--check-only`. `SCOPE=smoke` selects one task; SkillsBench uses one trial for a smoke and three for the full suite.

Results, transcripts, and generated files are saved in `outputs/<benchmark>/<RUN_NAME>/`. Each run writes a `config.json` and a `summary.json`; BFCL also writes the official score tables under `score/`. Reuse a run name to resume supported completed task outputs, or choose a new name for a new configuration.

Optional environment settings:

```bash
CONCURRENCY=4 RUN_NAME=my-model bash JobBench/run.sh
ENV_FILE=/path/to/eval.env RUNTIME=/path/to/runtime OUTPUT_ROOT=/path/to/results bash Tau3-Banking/run.sh
```

The default candidate decoding is temperature 1.0 and at most 32,768 output tokens per turn where the harness exposes the budget. BFCL uses its official decoding defaults. OfficeQA uses temperature 0 and an 8,192-token output limit. For a self-hosted model, configure a 262,144-token context and supply provider-specific sampling fields when supported, for example `MODEL_EXTRA_BODY='{"top_k":20,"top_p":0.95}'`. `MODEL_REASONING_EFFORT` overrides the benchmark’s default where supported; use values accepted by your endpoint.

Harvey follows the [official GPT adapter](https://github.com/harveyai/harvey-labs/blob/813305193c0c61f712252e35db2c9bf8c3fc2a5e/harness/adapters/openai.py): GPT models use streamed Responses API and a 128,000-token output budget; other compatible models use Chat Completions with 32,768 tokens. Its temperature defaults to 0. Set `MODEL_REASONING_EFFORT` for GPT reasoning control. `HARVEY_API=chat` selects Chat Completions for endpoints without Responses support. The task score is 1 only when every rubric criterion passes. Completed generation is reused if scoring needs to be retried.

## Sources

[AutomationBench](https://github.com/zapier/AutomationBench), [SkillsBench](https://github.com/benchflow-ai/skillsbench/tree/v1.1), [JobBench](https://github.com/Job-Bench/job-bench-eval), [tau2-bench](https://github.com/sierra-research/tau2-bench), [BFCL](https://github.com/ShishirPatil/gorilla/tree/6ea57973c7a6097fd7c5915698c54c17c5b1b6c8/berkeley-function-call-leaderboard), [Harvey LAB](https://github.com/harveyai/harvey-labs/tree/813305193c0c61f712252e35db2c9bf8c3fc2a5e), and [OfficeQA](https://github.com/databricks/officeqa).

Tau3 includes the Banking Knowledge task and retrieval modifications used for evaluation on top of tau2 commit `a2c0247`.
