---
name: automationbench-eval
description: Run the 600-task AutomationBench v1.0.6 public suite against an OpenAI-compatible model.
---

Read [the shared configuration](../README.md) and fill in `benchmark/.env`.
From the `benchmark` directory:

```bash
bash AutomationBench/setup.sh
SCOPE=smoke RUN_NAME=smoke bash AutomationBench/run.sh
RUN_NAME=my-model bash AutomationBench/run.sh
```

No auxiliary model key is needed.

Score: Official final-state assertions; strict pass rate × 100. API toolset, 50 steps. The benchmark ChatGPT tool remains simulated.

Results: `outputs/AutomationBench/<RUN_NAME>/summary.json`. Select tasks with `--limit N` or `--tasks ID,ID`.
