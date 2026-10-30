---
name: jobbench-eval
description: Run the 65-task JobBench main split with OpenCode and the official rubric judge.
---

Read [the shared configuration](../README.md) and fill in `benchmark/.env`.
From the `benchmark` directory:

```bash
bash JobBench/setup.sh
SCOPE=smoke RUN_NAME=smoke bash JobBench/run.sh
RUN_NAME=my-model bash JobBench/run.sh
```

Set BOYUE_API_KEY for the Grok-4.3 judge.

Score: 100 × mean(round(task.total_score / task.max_score, 4)). Two attempts, 7,200 seconds per task.

Results: `outputs/JobBench/<RUN_NAME>/summary.json`. Select tasks with `--limit N` or `--tasks ID,ID`.
