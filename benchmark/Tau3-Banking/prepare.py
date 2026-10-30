from pathlib import Path
import os, sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from prepare import pip, install_node_tools

RUNTIME = Path(os.environ["RUNTIME"])
pip("--no-deps", HERE / "source")
install_node_tools()
for name in ["rg", "bwrap", "socat"]:
    import shutil

    if not shutil.which(name):
        raise SystemExit(
            f"Missing {name}; install ripgrep bubblewrap socat with apt-get"
        )
