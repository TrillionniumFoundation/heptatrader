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

class InstalledUnitInventoryTests(unittest.TestCase):
    def fixture(self, root):
        unit = root / 'lib/systemd/system/demo.service'
        unit.parent.mkdir(parents=True)
        unit = unit.with_name('hepta-demo.service')
        unit.write_text('[Service]\nExecStart=/usr/bin/hepta-demo\n')
        binary = root / 'bin/hepta-demo'
        binary.parent.mkdir(); binary.write_text('#!/bin/sh\nexit 0\n'); binary.chmod(0o755)
        return unit, binary

    def test_correct_install_matches_and_old_libexec_path_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit, binary = self.fixture(root)
            self.assertEqual(units.validate_installed(root), [])
            unit.write_text('[Service]\nExecStart=/usr/libexec/hepta-demo\n')
            self.assertTrue(any('missing/unsafe installed' in e for e in units.validate_installed(root)))

    def test_non_executable_symlink_and_missing_service_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); unit, binary = self.fixture(root)
            binary.chmod(0o644)
            self.assertTrue(units.validate_installed(root))
            binary.unlink(); binary.symlink_to('/bin/true')
            self.assertTrue(units.validate_installed(root))
            binary.unlink(); binary.write_text('x'); binary.chmod(0o755)
            (unit.parent/'hepta-demo.socket').write_text('[Socket]\nService=hepta-missing.service\n')
            self.assertTrue(any('missing required installed unit' in e for e in units.validate_installed(root)))

    def test_credential_code_must_be_packaged_not_assumed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); unit, _ = self.fixture(root)
            unit.write_text('[Service]\nExecStart=/usr/bin/python3 -I -S ${CREDENTIALS_DIRECTORY}/code\n'
                            'LoadCredential=code:/usr/libexec/heptatrader/policy.py\n')
            self.assertTrue(units.validate_installed(root, 'ib-paper'))
            helper=root/'libexec/heptatrader/policy.py'; helper.parent.mkdir(parents=True); helper.write_text('pass\n')
            self.assertEqual(units.validate_installed(root, 'ib-paper'), [])

    def test_broker_unit_cannot_be_published_by_core_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); unit, _ = self.fixture(root)
            unit.rename(unit.with_name('hepta-execution-ib-paper.service'))
            self.assertTrue(any('Broker authority unit' in e for e in units.validate_installed(root, 'core')))


if __name__ == "__main__":
    unittest.main()
