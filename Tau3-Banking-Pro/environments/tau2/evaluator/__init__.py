"""Load the unchanged Tau3 evaluators from this package's verifiers directory."""
from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[3] / "verifiers")]
