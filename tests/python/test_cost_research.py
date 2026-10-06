"""Cost strategy tests: pure simulation or mocked venue; no trade-host mutations."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
def load_tests(_loader, _suite, _pattern):
    tests = ROOT / "research/cost_aware/tests"
    return unittest.TestLoader().discover(str(tests), pattern="test_*.py", top_level_dir=str(tests))
