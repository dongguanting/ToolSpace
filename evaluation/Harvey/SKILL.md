---
name: harvey-eval
description: Run the fixed 120-task Harvey LAB sample with the official tools, document sandbox, and rubric scorer.
---

Read [the shared configuration](../README.md) and fill in `evaluation/.env`.
From the `evaluation` directory:

```bash
bash Harvey/setup.sh
SCOPE=smoke RUN_NAME=smoke bash Harvey/run.sh
RUN_NAME=my-model bash Harvey/run.sh
```

Set BOYUE_API_KEY. Setup pulls the pinned official Podman sandbox. GPT candidates use streamed Responses; other compatible models use Chat Completions. Temperature defaults to 0. MODEL_REASONING_EFFORT controls GPT reasoning.

Score: 100 × the fraction of tasks where every rubric criterion passes. Use the task list in tasks.json and a GLM-5.2 judge.

Results: `outputs/Harvey/<RUN_NAME>/summary.json`. Select tasks with `--limit N` or `--tasks ID,ID`.
