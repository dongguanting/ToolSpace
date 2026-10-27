---
name: bfcl-v4-eval
description: Run BFCL V4 all_scoring with an OpenAI-compatible candidate and the official weighted evaluator.
---

Read [the shared configuration](../README.md) and fill in `evaluation/.env`.
From the `evaluation` directory:

```bash
bash BFCL-v4/setup.sh
SCOPE=smoke RUN_NAME=smoke bash BFCL-v4/run.sh
RUN_NAME=my-model bash BFCL-v4/run.sh
```

Set SERPAPI_API_KEY for official web search, or SERPER_API_KEY for the alternative provider. Smoke runs one simple_python case.

Score: Official weighted overall accuracy over 5,217 cases: 10% Non-Live, 10% Live, 10% Irrelevance, 30% Multi-Turn, 40% Agentic.

Results: `outputs/BFCL-v4/<RUN_NAME>/summary.json`. Select tasks with `--limit N` or `--tasks ID,ID`.
