from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_canonical_ib_paper_profile as profile  # noqa: E402


class CanonicalIbPaperProfileTests(unittest.TestCase):
    def test_repository_profile_passes(self) -> None:
        self.assertEqual(profile.validate(), [])

    def fixture(self, directory: str) -> tuple[Path, Path]:
        policy_path = Path(directory) / "policy.json"
        environment_path = Path(directory) / "paper.env"
        policy_path.write_text(
            (ROOT / "docs/ib-paper-profile-policy-v1.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        environment_path.write_text(
            (ROOT / "systemd/hepta-execution-ib-paper.env.example").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return policy_path, environment_path

    def replace(self, path: Path, old: str, new: str) -> None:
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_multiple_active_orders_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path, environment_path = self.fixture(directory)
            self.replace(
                environment_path,
                "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS=1",
                "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS=2",
            )
            errors = profile.validate(policy_path, environment_path)
            self.assertTrue(any("single-order" in error for error in errors), errors)

    def test_multiple_or_non_cash_contracts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path, environment_path = self.fixture(directory)
            self.replace(
                environment_path,
                "HEPTA_IB_PAPER_QUOTE_CONTRACTS=EUR.USD|EUR|CASH|IDEALPRO|USD",
                "HEPTA_IB_PAPER_QUOTE_CONTRACTS=EUR.USD|EUR|CASH|IDEALPRO|USD,AAPL|AAPL|STK|SMART|USD",
            )
            errors = profile.validate(policy_path, environment_path)
            self.assertTrue(any("contract" in error for error in errors), errors)

    def test_order_quantity_may_not_exceed_gross_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path, environment_path = self.fixture(directory)
            self.replace(
                environment_path,
                "HEPTA_IB_PAPER_MAX_ORDER_QTY=25000",
                "HEPTA_IB_PAPER_MAX_ORDER_QTY=25001",
            )
            errors = profile.validate(policy_path, environment_path)
            self.assertTrue(any("gross position" in error for error in errors), errors)

    def test_live_or_duplicate_profile_input_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path, environment_path = self.fixture(directory)
            with environment_path.open("a", encoding="utf-8") as stream:
                stream.write("HEPTA_IB_EXECUTION_MODE=LIVE\n")
            errors = profile.validate(policy_path, environment_path)
            self.assertTrue(any("duplicate" in error for error in errors), errors)

    def test_policy_cannot_relax_single_order_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path, environment_path = self.fixture(directory)
            value = json.loads(policy_path.read_text(encoding="utf-8"))
            value["maximum_active_orders"] = 2
            policy_path.write_text(json.dumps(value), encoding="utf-8")
            errors = profile.validate(policy_path, environment_path)
            self.assertTrue(any("single active order" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
