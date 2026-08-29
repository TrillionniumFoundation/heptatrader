#!/usr/bin/env python3
"""Static contract for the separately published rootful systemd gate base."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "tests" / "rootful_systemd_base" / "Dockerfile"


class RootfulSystemdBaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = DOCKERFILE.read_bytes()
        cls.text = cls.payload.decode("ascii", errors="strict")

    def test_base_must_be_digest_pinned(self) -> None:
        self.assertIn('digest="${BASE_IMAGE##*@sha256:}"', self.text)
        self.assertIn('[ "${#digest}" -eq 64 ]', self.text)
        self.assertIn('case "$digest" in *[!0-9a-f]*)', self.text)
        self.assertNotRegex(self.text, r"(?m)^FROM\s+[^$].*:(?:latest|noble)\s*$")

    def test_only_reviewed_ubuntu_archives_are_accepted(self) -> None:
        self.assertIn(
            "http://archive.ubuntu.com/ubuntu) ;; *) exit 64;;", self.text)
        self.assertIn(
            "http://security.ubuntu.com/ubuntu) ;; *) exit 64;;", self.text)
        self.assertNotRegex(self.text, r"(?i)trusted\s*=\s*yes")
        self.assertNotRegex(self.text, r"(?i)allow-unauthenticated")

    def test_transport_root_is_exact_ubuntu_noble_amd64(self) -> None:
        for contract in (
            '. /etc/os-release',
            '[ "$ID" = ubuntu ]',
            '[ "$VERSION_ID" = 24.04 ]',
            '[ "${VERSION_CODENAME:-}" = noble ]',
            '[ "$(dpkg --print-architecture)" = amd64 ]',
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, self.text)

    def test_runtime_tools_and_python312_are_required(self) -> None:
        required = (
            "/usr/lib/systemd/systemd",
            "/usr/bin/systemctl",
            "/usr/bin/systemd-analyze",
            "/usr/bin/systemd-tmpfiles",
            "/usr/bin/journalctl",
            "/usr/bin/python3.12",
            "/usr/bin/setpriv",
            "/usr/bin/nsenter",
            "/usr/bin/findmnt",
            "/usr/bin/readelf",
            "/usr/sbin/ip",
            "/usr/sbin/nft",
        )
        for path in required:
            with self.subTest(path=path):
                self.assertIn(path, self.text)
        self.assertEqual(
            self.text.count("sys.version_info[:2] == (3, 12)"), 2)

    def test_package_inventory_and_apt_state_are_frozen_in_image(self) -> None:
        self.assertIn("dpkg-query -W", self.text)
        self.assertIn("packages.tsv", self.text)
        self.assertIn("packages.sha256", self.text)
        self.assertIn("rm -rf /var/lib/apt/lists/*", self.text)
        self.assertIn("transport-base.ref", self.text)
        self.assertIn("os-release.sha256", self.text)
        self.assertIn("apt-get clean", self.text)

    def test_exact_gate_labels_and_no_implicit_authority(self) -> None:
        labels = re.findall(
            r"io\.hepta\.rootful-systemd-base\.[a-z-]+=\"[^\"]+\"",
            self.text,
        )
        self.assertEqual(labels, [
            'io.hepta.rootful-systemd-base.offline-ready="true"',
            'io.hepta.rootful-systemd-base.version="1"',
        ])
        for forbidden in (
            "paper-authorized=true", "live-authorized=true",
            "broker-access=true", "ALLOW_TRADE=1",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_final_stage_discards_inherited_image_configuration(self) -> None:
        self.assertRegex(self.text, r"(?m)^FROM \$\{BASE_IMAGE\} AS rootfs$")
        self.assertRegex(self.text, r"(?m)^FROM scratch$")
        self.assertIn("COPY --from=rootfs / /", self.text)
        self.assertIn("rm -f /etc/machine-id", self.text)

    def test_no_fetcher_or_remote_script_execution(self) -> None:
        self.assertNotRegex(self.text, r"(?m)\b(?:curl|wget|git clone)\b")
        self.assertNotRegex(self.text, r"(?m)\|\s*(?:sh|bash)\b")


if __name__ == "__main__":
    unittest.main()
