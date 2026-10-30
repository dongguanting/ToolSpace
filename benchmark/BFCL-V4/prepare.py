from pathlib import Path
import os, sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from prepare import pip

RUNTIME = Path(os.environ["RUNTIME"])
pip("--index-url", "https://download.pytorch.org/whl/cpu", "torch==2.8.0+cpu")
pip("--no-deps", HERE / "source")
if os.environ.get("SKIP_DATA") != "1":
    from sentence_transformers import SentenceTransformer

    SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu")
print("BFCL evaluator ready")
