<div align="center">

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="assets/readme/hero.svg">
  <img src="assets/readme/hero.gif" alt="ToolSpace — real tools, executable worlds, long-horizon agents" width="100%">
</picture>

<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/readme/title-dark.svg">
    <img src="assets/readme/title-light.svg" width="360" alt="ToolSpace">
  </picture><br>
  Scaling Tool-Centric Environment Synthesis<br>for Long-Horizon Agents
</h1>

##### If you like our project, please give us a star ⭐ on GitHub for the latest update.

[![Paper](https://img.shields.io/badge/Paper-PDF-b5212f.svg)](https://toolspace-agent.github.io/main.pdf)
[![Model: ToolSpace-8B](https://img.shields.io/badge/🤗%20Model-ToolSpace--8B-ffc107.svg)](https://huggingface.co/dongguanting/ToolSpace-8B)
[![Model: ToolSpace-35B-A3B](https://img.shields.io/badge/🤗%20Model-ToolSpace--35B--A3B-ffc107.svg)](https://huggingface.co/dongguanting/ToolSpace-35B-A3B)
[![Model: ToolSpace-744B-A40B](https://img.shields.io/badge/🤗%20Model-ToolSpace--744B--A40B-ffc107.svg)](https://huggingface.co/dongguanting/ToolSpace-744B-A40B)
![arXiv: coming soon](https://img.shields.io/badge/arXiv-coming_soon-8b949e.svg?logo=arxiv)
<!-- TODO: Link the arXiv badge when released. -->

*From real tool traces to executable environments and long-horizon tasks through **typed dependencies, state-aware walks, and replay verification**.*

[Demo](#demo) · [Method](#method) · [Results](#results) · [Installation](#installation) · [Repository](#repository) · [Quick Start](#quick-start) · [Citation](#citation)

</div>

<table align="center">
<tr>
<td align="center" width="25%"><b>2,042</b><br><sub>canonical tools</sub></td>
<td align="center" width="25%"><b>804</b><br><sub>environments</sub></td>
<td align="center" width="25%"><b>34,376</b><br><sub>SFT samples</sub></td>
<td align="center" width="25%"><b>7</b><br><sub>evaluation benchmarks</sub></td>
</tr>
</table>

<a id="demo"></a>
## 🎬 ToolSpace in One Minute

https://github.com/user-attachments/assets/05d377d9-8247-4a8a-92b0-70aa658579af

<p align="center">
  <a href="https://github.com/user-attachments/assets/05d377d9-8247-4a8a-92b0-70aa658579af"><b>▶ Watch / download the 1080p video</b></a> &nbsp;·&nbsp;
  <a href="https://toolspace-agent.github.io/#demo"><b>Explore the interactive demo ↗</b></a><br>
  <sub>Illustrative graph simulation · executable replay available in the offline demo.</sub>
</p>

<a id="method"></a>
## 🛰️ From Tool Traces to Verified Tasks

<p align="center"><img src="assets/readme/framework.png" alt="ToolSpace framework: mine typed dependencies from real execution traces, ground the tool graph, sample state-aware workflows, reverse-synthesize tasks, and verify by replay" width="100%"><br><sub>Framework overview · Figure 2 in the paper.</sub></p>

### 1 · Explore and ground the tool space

Mine adjacent calls in real MCP and open-agent traces with **deterministic rules**. Nodes encode tools and normalized arguments; edges capture observed dependencies. Cluster the graph into environments, synthesizing persistent data for operation tools, restoring state for query tools, and reusing recorded outputs for stateless tools.

| Dependency | Execution evidence | Share of edges |
| :--- | :--- | ---: |
| 🟣 **Data** | An output value supplies a type-compatible input to the next call. | 69% |
| 🟢 **State** | One call changes an object that a later call reads or updates. | 25% |
| 🟠 **Retry** | A failed call is followed by a successful alternative. | 6% |

### 2 · Walk, reverse-synthesize, and replay

Sample **state-compatible successors** by edge weight, grounding arguments in the task, initial state, or earlier observations. Checkpoint before execution; **roll back and resample** on failure. Reverse-synthesize the successful workflow into a goal and state/trace/output assertions, then **replay from the initial snapshot** before acceptance.

<table>
<tr>
<td width="50%"><b>Cross-environment composition</b><br>66.2% of the analyzed trajectories span at least two environments; the mean is 18.86 tool calls.</td>
<td width="50%"><b>Ground only what needs state</b><br>55.3% fewer synthesized files in a 500-environment study: 2,724 → 1,218.</td>
</tr>
</table>

<sub>Trajectory statistics use the 21,385 trajectories with at least five calls. File count is the paper's synthesis-cost proxy, not a measured reduction in dollars or runtime. See §5 and §6.4.</sub>

<a id="results"></a>
## 📊 Main Results

**Backbone → ToolSpace** on all seven benchmarks. Scores use each benchmark's native metric.

<p align="center"><img src="assets/readme/results.svg" alt="Backbone and ToolSpace scores on AutomationBench, SkillsBench, JobBench, Tau3-Banking, BFCL-V4, Harvey-LAB, and OfficeQA-Pro, at 8B, 35B, and 744B scales" width="100%"></p>

<details>
<summary><b>Per-benchmark scores · Table 1</b></summary>

| Model | AutomationBench | SkillsBench | JobBench | Tau3-Banking | BFCL-V4 | Harvey-LAB | OfficeQA-Pro | Avg. |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen3-8B | 0.0 | 0.1 | 0.1 | 4.1 | 42.8 | 0.0 | 3.0 | 7.2 |
| ToolSpace-8B | 32.3 | 3.9 | 13.1 | 10.3 | 65.3 | 0.0 | 12.0 | 19.6 |
| ToolSpace-8B + RL | 34.3 | 4.1 | 15.5 | 11.3 | 67.5 | 0.0 | 12.0 | **20.7** |
| Qwen3.6-35B-A3B | 8.2 | 30.7 | 6.4 | 9.3 | 60.0 | 0.8 | 45.9 | 23.0 |
| ToolSpace-35B-A3B | 45.8 | 42.4 | 40.0 | 34.0 | 74.3 | 1.7 | 48.9 | 41.0 |
| ToolSpace-35B-A3B + RL | 45.8 | 45.3 | 40.0 | 42.3 | 74.8 | 2.5 | 51.9 | **43.2** |
| GLM 5.2 | 29.3 | 56.7 | 43.4 | 37.1 | 75.6 | 13.3 | 56.9 | 44.6 |
| ToolSpace-744B-A40B | 54.7 | 67.8 | 57.5 | 43.3 | 78.4 | 27.5 | 67.1 | **56.6** |
| Claude Opus 5 | 45.6 | 63.7 | 68.0 | 48.7 | 80.6 | 22.7 | 67.2 | **56.6** |

</details>

### Harder tasks with the Pro benchmarks

Six frontier models lose **up to 32.9 points** on ToolSpace-generated Pro tasks.

| Model | Automation → Pro | Banking → Pro |
| :--- | :---: | :---: |
| Claude Opus 5.5 | 59.2 → **26.3** (−32.9) | 51.5 → **35.7** (−15.8) |
| GPT 6.1 Sol | 50.8 → **24.7** (−26.1) | 46.4 → **29.6** (−16.8) |
| DeepSeek-V4.1-Flash | 50.0 → **20.0** (−30.0) | 47.4 → **35.7** (−11.7) |
| GLM 5.3 | 51.5 → **22.7** (−28.8) | 40.2 → **20.4** (−19.8) |
| Qwen 3.8 Max | 49.7 → **24.0** (−25.7) | 55.2 → **29.6** (−25.6) |
| GLM 5.2 | 29.3 → **18.0** (−11.3) | 37.1 → **12.2** (−24.9) |

<sub>Source: Table 2. Parentheses show Pro − standard in percentage points. The paper reports 600 Automation-Pro tasks; the current public [Automation-Pro](Automation-Pro/) package contains 300 frozen tasks. [Tau3-Banking-Pro](Tau3-Banking-Pro/) contains 98 tasks, separate from the original 97-task banking set.</sub>

<a id="installation"></a>
## 🔧 Installation

### Environment Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then clone the repository. Keep `TOOLSPACE_ROOT` set in the same shell for the commands below.

```bash
git clone https://github.com/dongguanting/ToolSpace.git
cd ToolSpace
export TOOLSPACE_ROOT="$PWD"
```

Each module uses its own environment:

| Module | Python | Runtime requirements |
| :--- | :---: | :--- |
| Task synthesis | 3.13+ | `uv`; local Automation simulator |
| Seven benchmarks | 3.10 / 3.12 / 3.13 | Linux; Node.js 18+; Docker for SkillsBench; Podman for Harvey-LAB |
| Automation-Pro | 3.10+ | Local simulator and native verifiers |
| Tau3-Banking-Pro | 3.12 | Linux; Node.js; `ripgrep`, `bubblewrap`, `socat`; working user namespaces |

Benchmark setup creates separate environments under `benchmark/.runtime/` and installs pinned dependencies. It needs access to GitHub, PyPI, and Hugging Face. See the [system package installation commands](benchmark/README.md#install).

<a id="api-configuration"></a>
### API Configuration

**The default online evaluation setup requires a Boyue API key** for user simulation, dense retrieval, and LLM judges. Register at [Boyue API](https://api.boyuerichdata.opensphereai.com/), open **API令牌 (API tokens)**, and create a token in the **`auto` group** ([documentation](https://boyuerichdata.apifox.cn/)).

Create the evaluation configuration:

```bash
cp benchmark/.env.example benchmark/.env
```

Edit `benchmark/.env` with your endpoint and credentials:

```dotenv
# Model under evaluation: local serving or an OpenAI-compatible hosted API.
MODEL=toolspace-model
MODEL_BASE_URL=http://localhost:8000/v1
MODEL_API_KEY=EMPTY

# Boyue: auxiliary models used by the evaluation harness.
BOYUE_API_KEY=sk-your-boyue-token
BOYUE_BASE_URL=https://api.boyuerichdata.opensphereai.com/v1

# BFCL-V4 web search: set either SERPER_API_KEY or SERPAPI_API_KEY.
SERPER_API_KEY=your-serper-key

# OfficeQA-Pro: a Hugging Face read token with dataset access.
HF_TOKEN=hf_your-read-token
```

`MODEL` must match the model ID exposed by your serving endpoint. To evaluate a Boyue-hosted model, use its model ID, set `MODEL_BASE_URL` to the Boyue base URL, and set `MODEL_API_KEY` to your Boyue token.

| Used by | Required service |
| :--- | :--- |
| Tau3-Banking / Tau3-Banking-Pro | Boyue: GPT-5.2 user simulator and judge/reviewer where enabled; `text-embedding-3-large` for dense retrieval |
| JobBench | Boyue: Grok-4.3 rubric judge |
| Harvey-LAB | Boyue: GLM-5.2 rubric judge and deliverable matching |
| BFCL-V4 | [SerpAPI](https://serpapi.com/) or [Serper](https://serper.dev/) for web search |
| OfficeQA-Pro | Accept access to [databricks/officeqa](https://huggingface.co/datasets/databricks/officeqa), then set `HF_TOKEN` |

Boyue is a third-party OpenAI-compatible service. Alternative providers can be configured through the corresponding base URL, key, and model settings; see the [benchmark API guide](benchmark/README.md#model-apis-and-data). **Offline demos, replay, and validation need no API keys.**

Benchmark launchers load `benchmark/.env` automatically. For the synthesis and Pro commands below, export the same configuration into your shell:

```bash
set -a
source "$TOOLSPACE_ROOT/benchmark/.env"
set +a
```

<a id="repository"></a>
## 📂 Repository

```text
ToolSpace/
├── Tool-Space/                  # Task synthesis: graph → walk → task → replay
│   ├── automation_environment/  # Local simulator, tool catalog, and checkpoints
│   ├── tool_graph/              # Typed dependencies and executable workflow sampling
│   ├── task_generation/         # Model prompts, task/verifier generation, and export
│   ├── verification/            # Independent replay and native scoring
│   ├── examples/                # Input bundles, walk configs, and offline fixtures
│   └── run_pipeline.py          # Full pipeline and individual-stage CLI
├── benchmark/                   # Seven standard evaluation suites
│   ├── AutomationBench/
│   ├── SkillsBench/
│   ├── JobBench/
│   ├── Tau3-Banking/
│   ├── BFCL-V4/
│   ├── Harvey-LAB/
│   └── OfficeQA-Pro/
├── Automation-Pro/              # Frozen automation challenge set
├── Tau3-Banking-Pro/             # Frozen banking challenge set
└── assets/                      # README figures and demo media
```

| Folder | What you get | Start here |
| :--- | :--- | :--- |
| `Tool-Space/` | Tool graph construction, workflow sampling, task/verifier generation, and replay | [Pipeline guide](Tool-Space/README.md) · [Input format](Tool-Space/docs/input-format.md) |
| `benchmark/` | Seven evaluation suites, pinned dependencies, and unified outputs | [Setup and evaluation](benchmark/README.md) |
| `Automation-Pro/` | 300 frozen automation tasks across six domains | [Validation, replay, and scoring](Automation-Pro/README.md) |
| `Tau3-Banking-Pro/` | 98 banking tasks, 698 knowledge documents, and native outcome scoring | [Setup and evaluation](Tau3-Banking-Pro/README.md) |

**Release scope:** `Tool-Space/` implements Automation-based synthesis with **47 services and 758 endpoints**. The broader tool-space and training statistics above describe the paper's experiments.

<a id="quick-start"></a>
## 🚀 Quick Start

### 1. Task Synthesis

**Offline demo — no API key required:**

```bash
cd "$TOOLSPACE_ROOT/Tool-Space"
uv sync --locked
uv run python run_pipeline.py demo --output outputs/demo
uv run python run_pipeline.py verify --tasks outputs/demo/tasks
```

Environment execution and replay are real; model responses use fixtures. The dataset, summary, and replay report are saved under `outputs/demo/tasks/`. Use a fresh output directory for each run.

**Generate new tasks through Boyue** after completing [API Configuration](#api-configuration):

```bash
cd "$TOOLSPACE_ROOT/Tool-Space"
export PRO_MODEL="your-boyue-model-id"
export PRO_BASE_URL="$BOYUE_BASE_URL"
export PRO_API_KEY="$BOYUE_API_KEY"
export PRO_API="chat"  # Use "responses" if supported by your endpoint.

uv run python run_pipeline.py run \
  --input examples/project_reconciliation.json \
  --walk-config examples/walk.json \
  --count 1 --seed 42 --output outputs/pro
```

The synthesis CLI reads `PRO_*` from the shell; it does not load `.env` automatically. See the [pipeline guide](Tool-Space/README.md) for individual stages, custom inputs, and resume support.

### 2. Seven-Benchmark Evaluation

Configure `benchmark/.env` first, then install and run a smoke test:

```bash
cd "$TOOLSPACE_ROOT/benchmark"
bash setup_all.sh
SCOPE=smoke RUN_NAME=my-model-smoke bash AutomationBench/run.sh
```

Run one full suite or all seven:

```bash
RUN_NAME=my-model bash AutomationBench/run.sh
RUN_NAME=my-model bash run_all.sh
```

Replace `AutomationBench` with another directory to run a different suite. All launchers support `--limit N`, `--tasks ID,ID`, and `--check-only`. Results are written to `benchmark/outputs/<benchmark>/<RUN_NAME>/`.

| Directory | Evaluation set | Metric |
| :--- | :--- | :--- |
| [AutomationBench](benchmark/AutomationBench/) | 600 public tasks | Strict task pass rate |
| [SkillsBench](benchmark/SkillsBench/) | 79 tasks × 3 trials | Mean official verifier reward |
| [JobBench](benchmark/JobBench/) | 65 tasks | Mean rubric score |
| [Tau3-Banking](benchmark/Tau3-Banking/) | 97 Banking Knowledge tasks | Native task success rate |
| [BFCL-V4](benchmark/BFCL-V4/) | 5,217 `all_scoring` cases | Official weighted overall accuracy |
| [Harvey-LAB](benchmark/Harvey-LAB/) | Fixed 120-task sample | Official all-pass rate |
| [OfficeQA-Pro](benchmark/OfficeQA-Pro/) | 133 questions | Official answer accuracy |

### 3. Automation-Pro

Install an isolated environment and validate the 300 task contracts:

```bash
cd "$TOOLSPACE_ROOT/Automation-Pro"
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python evaluate.py validate
```

Evaluate the model configured in [API Configuration](#api-configuration):

```bash
.venv/bin/python evaluate.py run \
  --model "$MODEL" --base-url "$MODEL_BASE_URL" \
  --api chat --api-key-env MODEL_API_KEY \
  --output results/my-model --concurrency 4
```

Or replay an existing predictions file without a model API:

```bash
.venv/bin/python evaluate.py replay \
  --input predictions.jsonl --output results/replay
```

Use a fresh result directory. See the [prediction format and scoring guide](Automation-Pro/README.md).

### 4. Tau3-Banking-Pro

On Linux, install the Python environment and validate the 98-task package:

```bash
cd "$TOOLSPACE_ROOT/Tau3-Banking-Pro"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python evaluate.py --check-only
```

For live evaluation, install the knowledge-shell runtime and use Boyue for the customer simulator and dense retrieval. The Linux packages listed in [Environment Setup](#installation) must also be available.

```bash
npm install --prefix .runtime/shell --no-save @anthropic-ai/sandbox-runtime@0.0.23
export USER_BASE_URL="$BOYUE_BASE_URL"
export USER_API_KEY="$BOYUE_API_KEY"

.venv/bin/python evaluate.py \
  --limit 1 --concurrency 1 --output outputs/pro98-smoke \
  --reasoning-effort max --no-review
```

Use a reasoning effort supported by your model endpoint, a fresh output directory, and remove `--limit 1` for all 98 tasks. Report **Pro98** with the model and retrieval configuration; see the [banking evaluation guide](Tau3-Banking-Pro/README.md). Keep reference trajectories and verifier targets on the evaluator side.

## 🙏 Acknowledgments

We thank the creators of [AutomationBench](https://github.com/zapier/AutomationBench), [SkillsBench](https://github.com/benchflow-ai/skillsbench), [JobBench](https://github.com/Job-Bench/job-bench-eval), [tau2-bench](https://github.com/sierra-research/tau2-bench), [BFCL](https://github.com/ShishirPatil/gorilla), [Harvey LAB](https://github.com/harveyai/harvey-labs), and [OfficeQA](https://github.com/databricks/officeqa). Third-party components retain their respective licenses and attribution; see the license and notice files in each module.

<a id="citation"></a>
## 📄 Citation

```bibtex
@article{dong2026toolspace,
  title   = {ToolSpace: Scaling Tool-Centric Environment Synthesis for Long-Horizon Agents},
  author  = {Dong, Guanting and Sun, Zhichao and Yang, Xinyu and others},
  year    = {2026},
  url     = {https://toolspace-agent.github.io/}
}
```

<p align="center"><a href="mailto:dongguanting@ruc.edu.cn">Contact</a></p>
