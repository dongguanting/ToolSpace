from pathlib import Path
import os, sys, subprocess

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from prepare import extract

RUNTIME = Path(os.environ["RUNTIME"])
tasks = RUNTIME / "Harvey-LAB/tasks"
if not (
    tasks / "antitrust-competition/analyze-antitrust-hsr-strategy/task.json"
).is_file():
    extract(HERE / "tasks.tar.gz", tasks)
if os.environ.get("SKIP_IMAGES") != "1":
    subprocess.run(["bash", str(HERE / "build_sandbox.sh")], check=True)
