"""Compatibility and receipt provenance of historical offline audit entries."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENTRIES = (
    "fx_original_health_portable_prefix_verify.py",
    "fx_original_health_portable_160_verify.py",
    "fx_original_health_portable_240_verify.py",
    "fx_original_health_portable_320_verify.py",
    "fx_original_health_fullcycle_audit.py",
    "fx_original_health_sustained_audit.py",
)


class VerifierEntryTests(unittest.TestCase):
    def test_direct_commands_work_without_checkout_pythonpath(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as cwd:
            for name in ENTRIES:
                with self.subTest(entry=name):
                    result = subprocess.run(
                        [sys.executable, "-B", str(ROOT / "research/train_v2" / name),
                         "--help"], cwd=cwd, env=env, text=True,
                        capture_output=True, timeout=15, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("--output", result.stdout)

    def test_portable_cli_binds_entry_and_shared_verifier_bytes(self):
        from test_fx_original_health_portable_160_verify import fixture
        from train_v2 import fx_original_health_portable_160_verify as entry
        from train_v2 import fx_original_health_portable_prefix_verify as core

        with tempfile.TemporaryDirectory() as directory:
            _, report, pins, bundle = fixture(directory)
            root = Path(directory)
            source_report = root / "source-report.json"
            source_report.write_bytes(report[0])
            source_report.chmod(0o600)
            output = root / "new-receipt.json"
            args = ["verify160", "--bundle", str(bundle),
                    "--original-report", str(source_report), "--output", str(output)]
            frozen_pins = dict(entry.PIN)
            with mock.patch.dict(entry.PIN, pins, clear=True), \
                    mock.patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
                entry.main()
            self.assertEqual(entry.PIN, frozen_pins)
            receipt = json.loads(output.read_bytes())
            self.assertEqual(receipt["verified_count"], 160)
            self.assertEqual(receipt["unverified_count"], 320)
            self.assertFalse(receipt["final_480_accepted"])
            self.assertFalse(receipt["trade_authority"])
            self.assertEqual(
                receipt["standalone_verifier_source_sha256"],
                hashlib.sha256(Path(entry.__file__).read_bytes()).hexdigest())
            self.assertEqual(
                receipt["shared_verifier_source_sha256"],
                hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
