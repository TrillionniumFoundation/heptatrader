#!/usr/bin/env python3

"""Exercise the local PAPER mutable-input mount shape in a real sandbox."""

from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
UNITS = {
    "broker": ROOT / "systemd/hepta-broker-egress-policy.service",
    "guardian": ROOT / "systemd/hepta-local-paper-authority@.service",
    "finalizer": ROOT / "systemd/hepta-p1-paper-canary-finalizer@.service",
}
IDENTITY = "hepta-agent-trust-domain-paper-identities-v1.json"
DROP_IN_ROOT = "/etc/systemd/system/hepta-broker-egress-policy.service.d"
KILL_SWITCH_ROOTS = frozenset({
    "/run/hepta/ib-paper-control",
    "/run/hepta/ib-paper-control-alpha",
})
COMMON_READ_ONLY = frozenset({
    "/etc/heptatrader/credentials",
    "/etc/heptatrader/paper-campaigns",
    "/etc/heptatrader/p1-safety-soak",
    "/etc/heptatrader/hepta-agent-trust-domain-policy-v1.json",
    "/etc/heptatrader/hepta-agent-trust-domain.json",
    "/etc/heptatrader/hepta-broker-network-policy-v1.json",
    "/etc/heptatrader/hepta-ib-paper-domain-authorizations-v1.json",
    "/etc/heptatrader/hepta-service-identities-v1.json",
    "/etc/heptatrader/local-ai-paper-agent.env",
    "/etc/heptatrader/local-ai-paper-deployment-v1.json",
    "/etc/heptatrader/local-ai-paper-certified-install-closure-v1.json",
    "/etc/heptatrader/hepta-tool-gateway.env",
    "/etc/heptatrader/hepta-execution-simulator.env",
    "/etc/heptatrader/hepta-execution-ib-paper.env",
    "/etc/heptatrader/hepta-supervisor-lease.key",
    "/etc/heptatrader/p1-paper-account-evidence-ed25519.pub",
    "/etc/heptatrader/rootful-systemd-review-ed25519.pub",
    "/etc/heptatrader/paper-account-authority.pub",
    "/etc/heptatrader/release-causal-openssl.cnf",
    "/etc/heptatrader/heptatrader-evidence-receipt-trust-v1.json",
})
GUARDIAN_TRUST_READ_ONLY = frozenset({
    "/etc/heptatrader/trust-domains/alpha.json",
    "/etc/heptatrader/trust-domains/uid-2104.json",
    "/etc/heptatrader/trust-domains/alpha.agent-host.conf",
    "/etc/heptatrader/trust-domains/alpha.execution.env",
    "/etc/heptatrader/trust-domains/alpha.shadow-watch.env",
    "/etc/heptatrader/trust-domains/full-chain.required",
})


C_PROBE = textwrap.dedent(r"""
    #define _GNU_SOURCE
    #include <errno.h>
    #include <fcntl.h>
    #include <limits.h>
    #include <stdio.h>
    #include <stdlib.h>
    #include <string.h>
    #include <sys/stat.h>
    #include <sys/types.h>
    #include <unistd.h>

    static int write_all(int fd, const char *value) {
        size_t size = strlen(value), offset = 0;
        while (offset < size) {
            ssize_t result = write(fd, value + offset, size - offset);
            if (result <= 0) return -1;
            offset += (size_t)result;
        }
        return 0;
    }

    static int atomic_replace(const char *path, const char *value) {
        char temporary[PATH_MAX], parent[PATH_MAX];
        if (snprintf(temporary, sizeof(temporary), "%s.sandbox-smoke.tmp",
                     path) >= (int)sizeof(temporary)) {
            errno = ENAMETOOLONG;
            return -1;
        }
        if (strlen(path) >= sizeof(parent)) {
            errno = ENAMETOOLONG;
            return -1;
        }
        strcpy(parent, path);
        char *slash = strrchr(parent, '/');
        if (slash == NULL || slash == parent) {
            errno = EINVAL;
            return -1;
        }
        *slash = '\0';
        int fd = open(temporary,
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (fd < 0) return -1;
        int result = write_all(fd, value);
        if (result == 0) result = fsync(fd);
        int saved = errno;
        if (close(fd) != 0 && result == 0) {
            result = -1;
            saved = errno;
        }
        if (result == 0 && rename(temporary, path) != 0) {
            result = -1;
            saved = errno;
        }
        if (result == 0 && chmod(path, 0600) != 0) {
            result = -1;
            saved = errno;
        }
        if (result == 0) {
            int directory = open(parent, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
            if (directory < 0 || fsync(directory) != 0) {
                result = -1;
                saved = errno;
            }
            if (directory >= 0 && close(directory) != 0 && result == 0) {
                result = -1;
                saved = errno;
            }
        }
        if (result != 0) unlink(temporary);
        errno = saved;
        return result;
    }

    static int permitted_read_only_error(int value) {
        return value == EACCES || value == EPERM || value == EROFS ||
               value == EBUSY;
    }

    static int expect_atomic_read_only(const char *path) {
        if (atomic_replace(path, "forbidden\n") == 0) return -1;
        return permitted_read_only_error(errno) ? 0 : -1;
    }

    static int expect_open_read_only(const char *path) {
        int fd = open(path, O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
        if (fd >= 0) {
            close(fd);
            return -1;
        }
        return permitted_read_only_error(errno) ? 0 : -1;
    }

    static int exact(const char *path, const char *expected) {
        char buffer[128];
        int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) return -1;
        ssize_t count = read(fd, buffer, sizeof(buffer));
        int saved = errno;
        close(fd);
        errno = saved;
        return count == (ssize_t)strlen(expected) &&
               memcmp(buffer, expected, (size_t)count) == 0 ? 0 : -1;
    }

    int main(int argc, char **argv) {
        if (argc != 9) return 90;
        if (atomic_replace(argv[1], "identity-after\n") != 0 ||
            exact(argv[1], "identity-after\n") != 0) return 10;
        if (atomic_replace(argv[6], "drop-in-after\n") != 0 ||
            exact(argv[6], "drop-in-after\n") != 0) return 11;
        for (int index = 7; index <= 8; ++index) {
            if (strcmp(argv[index], "-") != 0 &&
                (atomic_replace(argv[index], "engaged\n") != 0 ||
                 exact(argv[index], "engaged\n") != 0)) return 12 + index;
        }
        if (strcmp(argv[5], "writable") == 0) {
            if (atomic_replace(argv[2], "profile-after\n") != 0 ||
                exact(argv[2], "profile-after\n") != 0) return 20;
        } else if (expect_atomic_read_only(argv[2]) != 0) {
            return 21;
        }
        if (expect_open_read_only(argv[3]) != 0 ||
            expect_atomic_read_only(argv[3]) != 0 ||
            exact(argv[3], "critical-before\n") != 0) return 30;
        if (expect_open_read_only(argv[4]) != 0 ||
            exact(argv[4], "outer-before\n") != 0) return 31;
        return 0;
    }
""")


def directives(path: Path, name: str) -> list[str]:
    prefix = name + "="
    result: list[str] = []
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith(prefix):
            result.extend(line.removeprefix(prefix).split())
    return result


def build_probe_image(root: Path) -> str:
    probe = root / "probe"
    compiled = subprocess.run([
        "/usr/bin/cc", "-static", "-O2", "-Wall", "-Wextra", "-Werror",
        "-x", "c", "-", "-o", str(probe),
    ], input=C_PROBE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, check=False, timeout=45)
    if compiled.returncode != 0:
        raise AssertionError("static sandbox probe compilation failed: " +
                             compiled.stderr)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as value:
        info = tarfile.TarInfo("probe")
        raw = probe.read_bytes()
        info.size = len(raw)
        info.mode = 0o755
        info.uid = 0
        info.gid = 0
        info.mtime = 0
        value.addfile(info, io.BytesIO(raw))
    imported = subprocess.run(
        ["/usr/bin/docker", "image", "import", "-"],
        input=archive.getvalue(), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=45)
    if imported.returncode != 0:
        raise AssertionError(
            "ordinary build user cannot create private Docker sandbox: " +
            imported.stderr.decode("utf-8", errors="replace"))
    image = imported.stdout.decode("ascii", errors="strict").strip()
    if not image.startswith("sha256:") or len(image) != 71:
        raise AssertionError("Docker import returned an invalid image id")
    return image


def bind(source: Path, target: str, *, read_only: bool = False) -> str:
    option = "type=bind,src=" + str(source) + ",dst=" + target
    return option + (",readonly" if read_only else "")


class LocalPaperAtomicReplaceSandboxTests(unittest.TestCase):
    def test_unit_mount_contracts_are_parent_writable_and_inputs_read_only(self):
        for role, path in UNITS.items():
            with self.subTest(role=role):
                writable = set(directives(path, "ReadWritePaths"))
                read_only = {
                    item.removeprefix("-")
                    for item in directives(path, "BindReadOnlyPaths")
                }
                self.assertIn("/etc/heptatrader", writable)
                self.assertIn(DROP_IN_ROOT, writable)
                self.assertNotIn("-" + DROP_IN_ROOT, writable)
                self.assertNotIn(
                    "/etc/heptatrader/" + IDENTITY, writable)
                self.assertNotIn(
                    "/etc/heptatrader/" + IDENTITY, read_only)
                self.assertNotIn("/etc/heptatrader", read_only)
                self.assertTrue(COMMON_READ_ONLY.issubset(read_only))
                if role == "guardian":
                    self.assertNotIn(
                        "/etc/heptatrader/trust-domains", read_only)
                    self.assertTrue(
                        GUARDIAN_TRUST_READ_ONLY.issubset(read_only))
                else:
                    self.assertIn(
                        "/etc/heptatrader/trust-domains", read_only)
                if role == "finalizer":
                    self.assertTrue(KILL_SWITCH_ROOTS.issubset(writable))
                    self.assertTrue(all(
                        root + "/kill-switch" not in writable
                        for root in KILL_SWITCH_ROOTS))

    def test_real_private_mount_namespace_allows_atomic_parent_replace(self):
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-mount-sandbox-") as temporary:
            suite_root = Path(temporary)
            image = build_probe_image(suite_root)
            try:
                for role in UNITS:
                    with self.subTest(role=role):
                        fixture = suite_root / role
                        config_root = fixture / "etc/heptatrader"
                        trust_root = config_root / "trust-domains"
                        trust_root.mkdir(parents=True, mode=0o700)
                        identity = config_root / IDENTITY
                        profile = trust_root / "alpha.env"
                        critical = trust_root / "alpha.json"
                        outside = fixture / "outer-sentinel"
                        drop_in_root = (
                            fixture / "etc/systemd/system/"
                            "hepta-broker-egress-policy.service.d")
                        drop_in_root.mkdir(parents=True, mode=0o700)
                        drop_in = drop_in_root / "20-local-paper.conf"
                        identity.write_bytes(b"identity-before\n")
                        profile.write_bytes(b"profile-before\n")
                        critical.write_bytes(b"critical-before\n")
                        outside.write_bytes(b"outer-before\n")
                        drop_in.write_bytes(b"drop-in-before\n")

                        kill_switches: list[Path] = []
                        if role == "finalizer":
                            for source in sorted(KILL_SWITCH_ROOTS):
                                parent = fixture / source.lstrip("/")
                                parent.mkdir(parents=True, mode=0o700)
                                kill_switch = parent / "kill-switch"
                                kill_switch.write_bytes(b"disengaged\n")
                                kill_switches.append(kill_switch)

                        command = [
                            "/usr/bin/docker", "run", "--rm", "--network=none",
                            "--read-only", "--cap-drop=ALL",
                            "--security-opt=no-new-privileges",
                            "--user=" + str(os.getuid()) + ":" + str(os.getgid()),
                            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=1m",
                            "--mount=" + bind(fixture, "/sandbox", read_only=True),
                            "--mount=" + bind(
                                config_root, "/sandbox/etc/heptatrader"),
                            "--mount=" + bind(
                                drop_in_root,
                                "/sandbox/etc/systemd/system/"
                                "hepta-broker-egress-policy.service.d"),
                        ]
                        if role == "guardian":
                            command.append("--mount=" + bind(
                                critical,
                                "/sandbox/etc/heptatrader/"
                                "trust-domains/alpha.json", read_only=True))
                        else:
                            command.append("--mount=" + bind(
                                trust_root,
                                "/sandbox/etc/heptatrader/trust-domains",
                                read_only=True))
                        if role == "finalizer":
                            for source, target in zip(
                                    kill_switches, sorted(KILL_SWITCH_ROOTS),
                                    strict=True):
                                command.append("--mount=" + bind(
                                    source.parent, "/sandbox" + target))
                        container_kills = [
                            "/sandbox" + value + "/kill-switch"
                            for value in sorted(KILL_SWITCH_ROOTS)
                        ] if role == "finalizer" else ["-", "-"]
                        command.extend([
                            image, "/probe",
                            "/sandbox/etc/heptatrader/" + IDENTITY,
                            "/sandbox/etc/heptatrader/trust-domains/alpha.env",
                            "/sandbox/etc/heptatrader/trust-domains/alpha.json",
                            "/sandbox/outer-sentinel",
                            "writable" if role == "guardian" else "read-only",
                            "/sandbox/etc/systemd/system/"
                            "hepta-broker-egress-policy.service.d/"
                            "20-local-paper.conf",
                            *container_kills,
                        ])
                        completed = subprocess.run(
                            command, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, check=False,
                            timeout=45)
                        self.assertEqual(
                            completed.returncode, 0,
                            "private mount sandbox failed for " + role +
                            ": stdout=" + completed.stdout +
                            " stderr=" + completed.stderr)
                        self.assertEqual(
                            identity.read_bytes(), b"identity-after\n")
                        self.assertEqual(
                            critical.read_bytes(), b"critical-before\n")
                        self.assertEqual(
                            outside.read_bytes(), b"outer-before\n")
                        self.assertEqual(
                            drop_in.read_bytes(), b"drop-in-after\n")
                        for kill_switch in kill_switches:
                            self.assertEqual(
                                kill_switch.read_bytes(), b"engaged\n")
            finally:
                subprocess.run(
                    ["/usr/bin/docker", "image", "rm", "--force", image],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False, timeout=45)

    def test_private_sandbox_rejects_absent_required_write_parent(self):
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-mount-missing-") as temporary:
            fixture = Path(temporary)
            image = build_probe_image(fixture)
            missing = fixture / "absent-drop-in-parent"
            try:
                completed = subprocess.run([
                    "/usr/bin/docker", "run", "--rm", "--network=none",
                    "--read-only", "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--user=" + str(os.getuid()) + ":" + str(os.getgid()),
                    "--mount=" + bind(
                        missing, "/sandbox/etc/systemd/system/"
                        "hepta-broker-egress-policy.service.d"),
                    image, "/probe",
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, check=False, timeout=45)
                self.assertNotEqual(
                    completed.returncode, 0,
                    "an absent non-optional write parent was accepted")
                self.assertFalse(missing.exists())
            finally:
                subprocess.run(
                    ["/usr/bin/docker", "image", "rm", "--force", image],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False, timeout=45)


if __name__ == "__main__":
    unittest.main()
