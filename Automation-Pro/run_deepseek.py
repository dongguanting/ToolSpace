#!/usr/bin/env python3
"""Run AutomationPro with the Tensoris DeepSeek Chat Completions configuration."""

from __future__ import annotations

import os
import sys

from evaluate import main as evaluate_main


def main() -> int:
    if not os.environ.get("TENSORIS_API_KEY"):
        raise SystemExit("Set TENSORIS_API_KEY in the environment before starting the evaluation")
    defaults = [
        "run",
        "--model", "deepseek-v4.1-flash-1017",
        "--base-url", "https://api.tensoris.ai/v1",
        "--api", "chat",
        "--api-key-env", "TENSORIS_API_KEY",
        "--concurrency", "80",
        "--max-turns", "100",
        "--reasoning-context", "all_turns",
        "--reasoning-effort", "max",
    ]
    return evaluate_main(defaults + sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
