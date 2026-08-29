#!/usr/bin/env python3
"""Offline contract tests for the disposable rootful-gate AppArmor policy."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "systemd" / "hepta-systemd-gate.apparmor"
PROFILE_NAME = "hepta-systemd-gate"


class HeptaSystemdGateAppArmorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = POLICY.read_bytes()
        cls.text = cls.payload.decode("ascii", errors="strict")

    def test_canonical_source_and_stable_digest(self) -> None:
        self.assertTrue(self.payload)
        self.assertTrue(self.payload.endswith(b"\n"))
        self.assertNotIn(b"\r", self.payload)
        self.assertNotIn(b"\0", self.payload)
        self.assertEqual(
            "sha256:" + hashlib.sha256(self.payload).hexdigest(),
            "sha256:" + hashlib.sha256(POLICY.read_bytes()).hexdigest(),
        )

    def test_exact_enforcing_profile_identity(self) -> None:
        declarations = re.findall(
            r"(?m)^profile\s+([A-Za-z0-9_.:@+=-]+)\s+flags=\(([^)]*)\)\s*\{",
            self.text,
        )
        self.assertEqual(
            declarations,
            [(PROFILE_NAME, "attach_disconnected,mediate_deleted")],
        )
        self.assertNotRegex(self.text, r"(?i)flags\s*=\([^)]*complain")
        self.assertNotRegex(self.text, r"(?m)^\s*profile\s+unconfined\b")
        self.assertEqual(self.text.count("abi <abi/3.0>,"), 1)

    def test_moby_baseline_is_preserved_without_local_policy_overlay(self) -> None:
        required = (
            "#include <tunables/global>",
            "#include <abstractions/base>",
            "network,", "capability,", "file,", "umount,",
            "signal (receive) peer=unconfined,",
            "signal (receive) peer=runc,",
            "signal (receive) peer=crun,",
            "deny @{PROC}/* w,",
            "deny @{PROC}/acpi/** rw,",
            "deny @{PROC}/scsi/** rw,",
        )
        for rule in required:
            with self.subTest(rule=rule):
                self.assertIn(rule, self.text)
        self.assertNotRegex(self.text, r"(?m)^\s*#include\s+.*local/")

    def test_required_kernel_and_host_denials_are_explicit(self) -> None:
        required = (
            "deny network alg,",
            "deny mount,",
            "deny @{PROC}/sysrq-trigger rwklx,",
            "deny @{PROC}/kcore rwklx,",
            "deny /sys/firmware/** rwklx,",
            "deny /sys/devices/virtual/powercap/** rwklx,",
            "deny /sys/kernel/security/** rwklx,",
        )
        for rule in required:
            with self.subTest(rule=rule):
                self.assertIn(rule, self.text)

    def test_only_same_profile_ptrace_and_peer_signal(self) -> None:
        self.assertIn(
            "signal (send,receive) peer=hepta-systemd-gate,", self.text)
        self.assertIn(
            "ptrace (trace,tracedby,read,readby) "
            "peer=hepta-systemd-gate,",
            self.text,
        )
        self.assertNotIn("ptrace (trace) peer=unconfined", self.text)
        self.assertNotIn("signal (send) peer=unconfined", self.text)

    def test_parser_accepts_policy_without_loading_it(self) -> None:
        parser = shutil.which("apparmor_parser")
        if parser is None:
            parser = "/usr/sbin/apparmor_parser"
        if not Path(parser).is_file():
            self.skipTest("apparmor_parser is not installed")
        result = subprocess.run(
            [parser, "--skip-kernel-load", "--skip-cache",
             "--skip-read-cache", str(POLICY)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(
            "utf-8", errors="replace"))

    def test_policy_file_contains_no_install_or_load_mechanism(self) -> None:
        for forbidden in (
            "apparmor_parser", "systemctl", "ExecStart", "aa-enforce",
            "/sys/kernel/security/apparmor/.load",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
