from pathlib import Path
import os, sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from prepare import github

RUNTIME = Path(os.environ["RUNTIME"])
tasks = RUNTIME / "SkillsBench/tasks"
if not (tasks / "3d-scan-calc/task.md").is_file():
    upstream = RUNTIME / "SkillsBench/upstream"
    github(
        "benchflow-ai/skillsbench", "b63b7b2850226b6aa4fb5929a8c1ac7bc4d9a6af", upstream
    )
    tasks.symlink_to(upstream / "tasks", target_is_directory=True)
for task_id in (HERE / "task_ids.txt").read_text().splitlines():
    if not (tasks / task_id / "verifier/test.sh").is_file():
        raise SystemExit("Missing verifier: " + task_id)
print("SkillsBench v1.1: 79 tasks ready")
