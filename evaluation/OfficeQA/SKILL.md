---
name: officeqa-pro-eval
description: Run OfficeQA Pro’s fixed 133 questions with the document tools and official answer reward.
---

Read [the shared configuration](../README.md) and fill in `evaluation/.env`.
From the `evaluation` directory:

```bash
bash OfficeQA/setup.sh
SCOPE=smoke RUN_NAME=smoke bash OfficeQA/run.sh
RUN_NAME=my-model bash OfficeQA/run.sh
```

Accept Hugging Face dataset access and set HF_TOKEN before setup. The exact 133 question IDs are listed in task_ids.txt.

Score: Official accuracy × 100, tolerance 0.05; temperature 0, 200 steps, 2,700 seconds per question.

Results: `outputs/OfficeQA/<RUN_NAME>/summary.json`. Select tasks with `--limit N` or `--tasks ID,ID`.
