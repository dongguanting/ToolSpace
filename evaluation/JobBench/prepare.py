from pathlib import Path
import os, sys, subprocess

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from prepare import github, install_node_tools

RUNTIME = Path(os.environ["RUNTIME"])
install_node_tools()
opencode = RUNTIME / "JobBench/opencode"
github("anomalyco/opencode", "23fb5e0516c99ac04a1aa46c193efda2e1b9bb24", opencode)
import shutil

shutil.copy2(HERE / "opencode-bun.lock", opencode / "bun.lock")
subprocess.run(
    [
        str(RUNTIME / "tools/node_modules/.bin/bun"),
        "install",
        "--ignore-scripts",
        "--frozen-lockfile",
        "--registry",
        "https://registry.npmjs.org",
    ],
    cwd=opencode,
    check=True,
)
if os.environ.get("SKIP_DATA") != "1":
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id="JobBench/job-bench",
        repo_type="dataset",
        revision="d43a66575635da0d3f6cb9a20976dc1a60f3ffc9",
        allow_patterns=["dataset/**"],
        local_dir=RUNTIME / "JobBench/data",
    )
