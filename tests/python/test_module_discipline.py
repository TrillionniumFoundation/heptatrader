from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import unittest


class ModuleDisciplineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]

    def test_active_modules_have_owners_and_budgets(self) -> None:
        subprocess.run(
            [sys.executable, str(self.root / "scripts/check_module_discipline.py")],
            cwd=self.root,
            check=True,
        )

    def test_source_size_exceptions_are_no_growth_debt_not_closed_gaps(self) -> None:
        document = json.loads(
            (
                self.root / "docs/modules/source-size-budget-v1.json"
            ).read_text(encoding="utf-8")
        )
        exceptions = document["exceptions"]
        self.assertTrue(exceptions)
        debt_ids: set[str] = set()
        for path, item in exceptions.items():
            self.assertNotIn("gap", item, path)
            self.assertEqual("accepted-no-growth", item["status"], path)
            self.assertRegex(item["debt_id"], r"^TD-SIZE-[A-Z0-9-]+$")
            self.assertNotIn(item["debt_id"], debt_ids)
            debt_ids.add(item["debt_id"])
            self.assertRegex(
                item["owner"], r"^@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
            )
            self.assertGreater(item["baseline_lines"], 0)
            self.assertTrue(item["rationale"].strip())
            self.assertTrue(item["exit"].strip())
            self.assertRegex(item["review_by"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


if __name__ == "__main__":
    unittest.main()
