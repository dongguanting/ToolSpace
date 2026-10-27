---
name: tau3-banking-eval
description: Run the canonical 97 Banking Knowledge tasks with the tau3 simulator, retrieval tools, and judge.
---

Read [the shared configuration](../README.md) and fill in `evaluation/.env`.
From the `evaluation` directory:

```bash
bash Tau3/setup.sh
SCOPE=smoke RUN_NAME=smoke bash Tau3/run.sh
RUN_NAME=my-model bash Tau3/run.sh
```

Set BOYUE_API_KEY for GPT-5.2 and text-embedding-3-large.

Score: Task success rate × 100; seed 300, 200 steps, alltools retrieval. GPT-5.2 user and judge use reasoning effort low.

Results: `outputs/Tau3/<RUN_NAME>/summary.json`. Select tasks with `--limit N` or `--tasks ID,ID`.
