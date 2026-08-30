from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


class ModuleDisciplineTests(unittest.TestCase):
    def test_active_modules_have_owners_and_budgets(self) -> None:
        root = Path(__file__).resolve().parents[2]
        subprocess.run(
  [sys.executable, str(root / "scripts/check_module_discipline.py")],
  cwd=root,
  check=True,
        )


if __name__ == "__main__":
    unittest.main()
