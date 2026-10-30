---
name: tau3-banking-eval
description: Run the canonical 97 Banking Knowledge tasks with the tau3 simulator, retrieval tools, and judge.
---

Read [the shared configuration](../README.md) and fill in `benchmark/.env`.
From the `benchmark` directory:

```bash
bash Tau3-Banking/setup.sh
SCOPE=smoke RUN_NAME=smoke bash Tau3-Banking/run.sh
RUN_NAME=my-model bash Tau3-Banking/run.sh
```

Set BOYUE_API_KEY for GPT-5.2 and text-embedding-3-large.

Score: Task success rate × 100; seed 300, 200 steps, alltools retrieval. GPT-5.2 user and judge use reasoning effort low.

Results: `outputs/Tau3-Banking/<RUN_NAME>/summary.json`. Select tasks with `--limit N` or `--tasks ID,ID`.
