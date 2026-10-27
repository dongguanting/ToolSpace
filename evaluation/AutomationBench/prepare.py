from pathlib import Path
import os, sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from prepare import pip

RUNTIME = Path(os.environ["RUNTIME"])
pip("--no-deps", HERE / "source")
