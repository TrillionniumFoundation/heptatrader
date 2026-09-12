from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_systemd_units as units  # noqa: E402


class SystemdUnitTests(unittest.TestCase):
    def test_repository_units_satisfy_readiness_contract(self) -> None:
        self.assertEqual(units.validate(ROOT), [])

    def test_simulator_network_isolation_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "systemd").mkdir()
            (root / "systemd/hepta-execution-simulator.service").write_text(
                "[Service]\nType=simple\nUser=u\nGroup=u\nExecStart=/bin/x\n"
                "NoNewPrivileges=yes\nProtectSystem=strict\nUMask=0077\n"
                "Environment=HEPTA_EXECUTION_SERVICE_MODE=SIMULATOR\n",
                encoding="utf-8",
            )
            (root / "systemd/hepta-tool-gateway.socket").write_text(
                "[Socket]\nListenStream=/run/x\nSocketMode=0600\nService=x.service\n",
                encoding="utf-8",
            )
            errors = units.validate(root)
            self.assertTrue(any("PrivateNetwork=yes" in item for item in errors), errors)

    def test_policy_stop_path_must_deny_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "systemd").mkdir()
            (root / "systemd/hepta-broker-egress-policy.service").write_text(
                "[Service]\nType=oneshot\nUser=root\nGroup=root\nExecStart=/bin/x\n"
                "ExecStop=/bin/x\nNoNewPrivileges=yes\nProtectSystem=strict\nUMask=0077\n"
                "CapabilityBoundingSet=CAP_NET_ADMIN\n",
                encoding="utf-8",
            )
            (root / "systemd/x.socket").write_text(
                "[Socket]\nListenStream=/run/x\nSocketMode=0600\nService=hepta-broker-egress-policy.service\n",
                encoding="utf-8",
            )
            errors = units.validate(root)
            self.assertTrue(any("deny-all" in item for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
