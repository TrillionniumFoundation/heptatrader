from __future__ import annotations

import subprocess
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RepositoryIntegrityTests(unittest.TestCase):
    def test_repository_contracts_are_self_consistent(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_repository_integrity.py")],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
