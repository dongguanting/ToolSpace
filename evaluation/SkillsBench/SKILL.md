---
name: skillsbench-eval
description: Run the fixed 79-task SkillsBench v1.1 subset with OpenHands and the official container verifiers.
---

Read [the shared configuration](../README.md) and fill in `evaluation/.env`.
From the `evaluation` directory:

```bash
bash SkillsBench/setup.sh
SCOPE=smoke RUN_NAME=smoke bash SkillsBench/run.sh
RUN_NAME=my-model bash SkillsBench/run.sh
```

Requires Docker with access to package repositories.

Score: Mean verifier reward × 100 across 79 tasks and 3 trials. Use each task’s declared agent and verifier time budgets.

Results: `outputs/SkillsBench/<RUN_NAME>/summary.json`. Select tasks with `--limit N` or `--tasks ID,ID`.
