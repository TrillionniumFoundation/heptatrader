#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_hepta_agent_trust_domains as trust_domains  # noqa: E402
import hepta_agent_trust_domain as runtime_domain  # noqa: E402
import hepta_agent_mcp_launcher as mcp_launcher  # noqa: E402
import hepta_agent_session_bootstrap as session_bootstrap  # noqa: E402
import hepta_p1_watch_profile_deployer as profile_deployer  # noqa: E402


PASSWD = """\
root:x:0:0:root:/root:/bin/sh
hepta-gw-codex-a:x:2101:2101:gateway:/nonexistent:/usr/sbin/nologin
hepta-agent-codex-a:x:2104:2104:agent:/nonexistent:/usr/sbin/nologin
hepta-exec-codex-a:x:2111:2111:execution:/nonexistent:/usr/sbin/nologin
hepta-gw-openclaw-b:x:2102:2102:gateway:/nonexistent:/usr/sbin/nologin
hepta-agent-openclaw-b:x:2105:2105:agent:/nonexistent:/usr/sbin/nologin
hepta-exec-openclaw-b:x:2112:2112:execution:/nonexistent:/usr/sbin/nologin
"""

GROUP = """\
root:x:0:
hepta-gateway:x:2001:
hepta-gw-codex-a:x:2101:
hepta-agent-codex-a:x:2104:
hepta-exec-codex-a:x:2111:
hepta-gw-openclaw-b:x:2102:
hepta-agent-openclaw-b:x:2105:
hepta-exec-openclaw-b:x:2112:
"""


class AgentTrustDomainTests(unittest.TestCase):
    def validated(self) -> dict[str, object]:
        return trust_domains.validate(
            trust_domains.DEFAULT_POLICY,
            trust_domains.DEFAULT_FIXTURE,
            trust_domains.IDENTITIES)

    def test_alpha_gateway_profile_reader_is_exact_and_anchored(self) -> None:
        self.assertEqual(
            runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES,
            profile_deployer.NEW_PAYLOAD)
        with tempfile.TemporaryDirectory(
                prefix="hepta-alpha-profile-") as temporary:
            config_root = Path(temporary).resolve() / "profiles"
            config_root.mkdir(mode=0o700)
            profile = config_root / "alpha.env"
            profile.write_bytes(runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES)
            profile.chmod(0o644)
            with mock.patch.object(
                    runtime_domain, "CONFIG_ROOT", config_root):
                result = runtime_domain._read_alpha_gateway_profile(
                    profile,
                    expected_uid=os.getuid(), expected_gid=os.getgid(),
                    safe_ancestor_uid=None, safe_ancestor_gid=None,
                )
            self.assertEqual(
                result.raw, runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES)
            self.assertEqual(
                result.values, runtime_domain.ALPHA_GATEWAY_PROFILE)

            for mutation in ("mode", "hardlink", "content", "symlink"):
                with self.subTest(mutation=mutation):
                    profile.unlink(missing_ok=True)
                    profile.write_bytes(
                        runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES)
                    profile.chmod(0o644)
                    extra = config_root / "extra"
                    extra.unlink(missing_ok=True)
                    if mutation == "mode":
                        profile.chmod(0o664)
                    elif mutation == "hardlink":
                        os.link(profile, extra)
                    elif mutation == "content":
                        profile.write_bytes(
                            runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES[:-1] +
                            b"X")
                    else:
                        target = config_root / "target"
                        target.write_bytes(
                            runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES)
                        target.chmod(0o644)
                        profile.unlink()
                        profile.symlink_to(target.name)
                    with mock.patch.object(
                            runtime_domain, "CONFIG_ROOT", config_root), \
                            self.assertRaises(
                                runtime_domain.TrustDomainRuntimeError):
                        runtime_domain._read_alpha_gateway_profile(
                            profile,
                            expected_uid=os.getuid(),
                            expected_gid=os.getgid(),
                            safe_ancestor_uid=None,
                            safe_ancestor_gid=None,
                        )
                    extra.unlink(missing_ok=True)
                    (config_root / "target").unlink(missing_ok=True)

            real_root = Path(temporary).resolve() / "real"
            real_root.mkdir(mode=0o700)
            (real_root / "alpha.env").write_bytes(
                runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES)
            (real_root / "alpha.env").chmod(0o644)
            linked_root = Path(temporary).resolve() / "linked"
            linked_root.symlink_to(real_root, target_is_directory=True)
            with mock.patch.object(
                    runtime_domain, "CONFIG_ROOT", linked_root), \
                    self.assertRaises(runtime_domain.TrustDomainRuntimeError):
                runtime_domain._read_alpha_gateway_profile(
                    linked_root / "alpha.env",
                    expected_uid=os.getuid(), expected_gid=os.getgid(),
                    safe_ancestor_uid=None, safe_ancestor_gid=None,
                )

    def test_alpha_gateway_profile_reader_rejects_rebind_races(self) -> None:
        for race in ("file", "parent"):
            with self.subTest(race=race), tempfile.TemporaryDirectory(
                    prefix="hepta-alpha-profile-race-") as temporary:
                container = Path(temporary).resolve()
                config_root = container / "profiles"
                config_root.mkdir(mode=0o700)
                profile = config_root / "alpha.env"
                profile.write_bytes(
                    runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES)
                profile.chmod(0o644)
                replacement_root = container / "replacement"
                if race == "parent":
                    replacement_root.mkdir(mode=0o700)
                    replacement = replacement_root / "alpha.env"
                    replacement.write_bytes(
                        runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES)
                    replacement.chmod(0o644)
                real_read = runtime_domain.os.read
                injected = False

                def racing_read(descriptor: int, count: int) -> bytes:
                    nonlocal injected
                    contents = real_read(descriptor, count)
                    if contents and not injected:
                        injected = True
                        if race == "file":
                            replacement = container / "new-profile"
                            replacement.write_bytes(
                                runtime_domain.ALPHA_GATEWAY_PROFILE_BYTES)
                            replacement.chmod(0o644)
                            os.replace(replacement, profile)
                        else:
                            config_root.rename(container / "old-profiles")
                            replacement_root.rename(config_root)
                    return contents

                with mock.patch.object(
                        runtime_domain, "CONFIG_ROOT", config_root), \
                        mock.patch.object(
                            runtime_domain.os, "read",
                            side_effect=racing_read), \
                        self.assertRaisesRegex(
                            runtime_domain.TrustDomainRuntimeError,
                            "TRUST_DOMAIN_GATEWAY_PROFILE_CHANGED"):
                    runtime_domain._read_alpha_gateway_profile(
                        profile,
                        expected_uid=os.getuid(), expected_gid=os.getgid(),
                        safe_ancestor_uid=None, safe_ancestor_gid=None,
                    )
                self.assertTrue(injected)

    def test_gateway_process_projection_is_exact_and_ignores_foreign_bytes(
            self) -> None:
        entries = [
            f"{key}={value}".encode("ascii")
            for key, value in
            runtime_domain.ALPHA_GATEWAY_PROCESS_PROFILE.items()
        ]
        exact = b"\0".join(entries) + b"\0"
        projected = runtime_domain._parse_alpha_gateway_process_environment(
            b"LANG=first\0LANG=second\0FOREIGN=\xff\xfe\0BROKEN\xff\0" + exact)
        self.assertEqual(
            projected, runtime_domain.ALPHA_GATEWAY_PROCESS_PROFILE)

        malformed = {
            "missing": b"\0".join(entries[:-1]) + b"\0",
            "extra": exact + b"HEPTA_UNREVIEWED=1\0",
            "duplicate": exact + entries[0] + b"\0",
            "non-ascii-hepta": exact + b"HEPTA_UNREVIEWED=\xff\0",
            "unterminated": exact[:-1],
        }
        for name, contents in malformed.items():
            with self.subTest(name=name), self.assertRaises(
                    runtime_domain.TrustDomainRuntimeError):
                runtime_domain._parse_alpha_gateway_process_environment(
                    contents)

    def test_gateway_process_readers_accept_real_procfs(self) -> None:
        environment = dict(runtime_domain.ALPHA_GATEWAY_PROCESS_PROFILE)
        environment["FOREIGN_UTF8"] = "ignored-雪"
        child = subprocess.Popen(
            ["/usr/bin/sleep", "30"], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True)
        try:
            profile = runtime_domain._read_alpha_gateway_process_profile(
                child.pid,
                expected_uid=os.getuid(), expected_gid=os.getgid())
            identity = runtime_domain._read_alpha_gateway_process_identity(
                child.pid,
                expected_uid=os.getuid(), expected_gid=os.getgid())
            self.assertEqual(
                profile.canonical_projection,
                runtime_domain.ALPHA_GATEWAY_PROCESS_PROFILE_BYTES)
            self.assertEqual(
                profile.pid_directory_metadata,
                identity.pid_directory_metadata)
            self.assertEqual(profile.starttime_ticks, identity.starttime_ticks)
        finally:
            child.terminate()
            child.wait(timeout=5)

    def test_gateway_process_identity_rejects_pid_and_starttime_races(
            self) -> None:
        child = subprocess.Popen(
            ["/usr/bin/sleep", "30"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True)
        try:
            original_open = runtime_domain._open_gateway_pid_directory
            calls = 0

            def changed_pid(*args, **kwargs):
                nonlocal calls
                descriptor, metadata = original_open(*args, **kwargs)
                calls += 1
                if calls == 2:
                    metadata = (
                        metadata[0], metadata[1] + 1, *metadata[2:])
                return descriptor, metadata

            with mock.patch.object(
                    runtime_domain, "_open_gateway_pid_directory",
                    side_effect=changed_pid), self.assertRaisesRegex(
                        runtime_domain.TrustDomainRuntimeError,
                        "TRUST_DOMAIN_GATEWAY_PROCESS_CHANGED"):
                runtime_domain._read_alpha_gateway_process_identity(
                    child.pid,
                    expected_uid=os.getuid(), expected_gid=os.getgid())

            with mock.patch.object(
                    runtime_domain, "_parse_proc_starttime",
                    side_effect=(100, 101)), self.assertRaisesRegex(
                        runtime_domain.TrustDomainRuntimeError,
                        "TRUST_DOMAIN_GATEWAY_PROCESS_CHANGED"):
                runtime_domain._read_alpha_gateway_process_identity(
                    child.pid,
                    expected_uid=os.getuid(), expected_gid=os.getgid())
        finally:
            child.terminate()
            child.wait(timeout=5)

    def test_alpha_gateway_socket_reader_rejects_metadata_and_rebinds(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-alpha-socket-") as temporary:
            parent = Path(temporary).resolve()
            path = parent / "tools.sock"
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC

            def local_parent(_path: Path, **_kwargs) -> int:
                return os.open(parent, flags)

            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            path.chmod(0o600)
            try:
                with mock.patch.object(
                        runtime_domain, "ALPHA_GATEWAY_SOCKET_PATH", path), \
                        mock.patch.object(
                            runtime_domain, "_open_anchored_directory",
                            side_effect=local_parent):
                    result = runtime_domain._read_alpha_gateway_socket(
                        path, expected_uid=os.getuid(), expected_gid=os.getgid())
                self.assertEqual(result.metadata[1], path.lstat().st_ino)

                path.chmod(0o660)
                with mock.patch.object(
                        runtime_domain, "ALPHA_GATEWAY_SOCKET_PATH", path), \
                        mock.patch.object(
                            runtime_domain, "_open_anchored_directory",
                            side_effect=local_parent), \
                        self.assertRaises(
                            runtime_domain.TrustDomainRuntimeError):
                    runtime_domain._read_alpha_gateway_socket(
                        path, expected_uid=os.getuid(), expected_gid=os.getgid())
                path.chmod(0o600)

                replacement_listener: socket.socket | None = None
                parent_opens = 0

                def replace_before_reopen(_path: Path, **_kwargs) -> int:
                    nonlocal parent_opens, replacement_listener
                    parent_opens += 1
                    if parent_opens == 2:
                        path.unlink()
                        replacement_listener = socket.socket(
                            socket.AF_UNIX, socket.SOCK_STREAM)
                        replacement_listener.bind(str(path))
                        path.chmod(0o600)
                    return os.open(parent, flags)

                with mock.patch.object(
                        runtime_domain, "ALPHA_GATEWAY_SOCKET_PATH", path), \
                        mock.patch.object(
                            runtime_domain, "_open_anchored_directory",
                            side_effect=replace_before_reopen), \
                        self.assertRaisesRegex(
                            runtime_domain.TrustDomainRuntimeError,
                            "TRUST_DOMAIN_GATEWAY_SOCKET_CHANGED"):
                    runtime_domain._read_alpha_gateway_socket(
                        path, expected_uid=os.getuid(), expected_gid=os.getgid())
                self.assertEqual(parent_opens, 2)
                if replacement_listener is not None:
                    replacement_listener.close()
            finally:
                listener.close()

    def test_alpha_gateway_socket_reader_rejects_parent_rebind(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-alpha-socket-parent-") as temporary:
            container = Path(temporary).resolve()
            parent = container / "run"
            replacement = container / "replacement"
            parent.mkdir(mode=0o700)
            replacement.mkdir(mode=0o700)
            path = parent / "tools.sock"
            replacement_path = replacement / "tools.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            replacement_listener = socket.socket(
                socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            replacement_listener.bind(str(replacement_path))
            path.chmod(0o600)
            replacement_path.chmod(0o600)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            opens = 0

            def parent_rebind(_path: Path, **_kwargs) -> int:
                nonlocal opens
                opens += 1
                if opens == 2:
                    parent.rename(container / "old-run")
                    replacement.rename(parent)
                return os.open(parent, flags)

            try:
                with mock.patch.object(
                        runtime_domain, "ALPHA_GATEWAY_SOCKET_PATH", path), \
                        mock.patch.object(
                            runtime_domain, "_open_anchored_directory",
                            side_effect=parent_rebind), \
                        self.assertRaisesRegex(
                            runtime_domain.TrustDomainRuntimeError,
                            "TRUST_DOMAIN_GATEWAY_SOCKET_CHANGED"):
                    runtime_domain._read_alpha_gateway_socket(
                        path, expected_uid=os.getuid(), expected_gid=os.getgid())
                self.assertEqual(opens, 2)
            finally:
                listener.close()
                replacement_listener.close()

    def test_launcher_defaults_to_root_owned_uid_domain_config(self) -> None:
        self.assertEqual(
            mcp_launcher.default_domain_config_path(2104),
            Path("/etc/heptatrader/trust-domains/uid-2104.json"))
        mcp = json.loads((
            ROOT / "plugins/heptatrader-agent-os/.mcp.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(
            mcp["mcpServers"]["heptatrader"]["env"], {})

    def test_runtime_domain_example_binds_unique_identity_and_paths(self) -> None:
        document = runtime_domain.load_runtime_config(
            ROOT / "systemd/hepta-agent-trust-domain.json.example",
            require_root_metadata=False)
        self.assertEqual(document["domain_id"], "alpha")
        self.assertEqual(document["agent_uid"], 2104)
        self.assertEqual(document["gateway_name"], "hepta-gw-alpha")
        self.assertEqual(document["gateway_uid"], 2101)
        self.assertEqual(document["execution_name"], "hepta-exec-alpha")
        self.assertEqual(document["execution_uid"], 2111)
        self.assertEqual(document["execution_gateway_uid"], 2101)
        self.assertEqual(document["execution_gateway_agent_id"], "alpha")
        self.assertEqual(document["connect_group"], "hepta-gw-alpha")
        self.assertEqual(document["connect_group_gid"], 2101)
        self.assertEqual(
            document["socket_path"], "/run/hepta-agent-alpha/tools.sock")
        self.assertEqual(
            document["token_directory"],
            "/run/hepta-agent-alpha/sessions")
        self.assertFalse(document["single_domain_compatibility"])
        self.assertFalse(document["paper_authorized"])
        self.assertFalse(document["live_authorized"])

    def test_runtime_config_profiles_are_path_and_identity_bound(self) -> None:
        contents = (
            ROOT / "systemd/hepta-agent-trust-domain.json.example"
        ).read_bytes()
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-profile-") as temp:
            config_root = Path(temp).resolve()
            domain = config_root / "alpha.json"
            uid = config_root / "uid-2104.json"
            foreign_uid = config_root / "uid-2105.json"
            for path in (domain, uid, foreign_uid):
                path.write_bytes(contents)

            metadata_calls: list[tuple[str, int, int]] = []

            def metadata(path: Path, *, expected_gid: int,
                         expected_mode: int) -> bytes:
                metadata_calls.append(
                    (path.name, expected_gid, expected_mode))
                return path.read_bytes()

            with (
                    mock.patch.object(
                        runtime_domain, "CONFIG_ROOT", config_root),
                    mock.patch.object(
                        runtime_domain, "_validate_config_ancestors"),
                    mock.patch.object(
                        runtime_domain, "_validate_metadata",
                        side_effect=metadata)):
                self.assertEqual(
                    runtime_domain.load_runtime_config(domain)["domain_id"],
                    "alpha")
                self.assertEqual(
                    runtime_domain.load_runtime_config(
                        uid, expected_agent_identity=(2104, 2104))[
                            "domain_id"],
                    "alpha")
                with self.assertRaisesRegex(
                        runtime_domain.TrustDomainRuntimeError,
                        "TRUST_DOMAIN_CONFIG_PROFILE_FORBIDDEN"):
                    runtime_domain.load_runtime_config(uid)
                with self.assertRaisesRegex(
                        runtime_domain.TrustDomainRuntimeError,
                        "TRUST_DOMAIN_CONFIG_PROFILE_FORBIDDEN"):
                    runtime_domain.load_runtime_config(
                        domain, expected_agent_identity=(2104, 2104))
                with self.assertRaisesRegex(
                        runtime_domain.TrustDomainRuntimeError,
                        "TRUST_DOMAIN_CONFIG_AGENT_IDENTITY_MISMATCH"):
                    runtime_domain.load_runtime_config(
                        foreign_uid, expected_agent_identity=(2105, 2104))

            self.assertEqual(metadata_calls, [
                ("alpha.json", 0, 0o600),
                ("uid-2104.json", 2104, 0o640),
                ("uid-2105.json", 2104, 0o640),
            ])

    def test_bootstrap_uses_domain_gateway_identity_and_connect_group(
            self) -> None:
        document = runtime_domain.load_runtime_config(
            ROOT / "systemd/hepta-agent-trust-domain.json.example",
            require_root_metadata=False)
        session_bootstrap._configure_domain(document)
        self.assertEqual(session_bootstrap.DOMAIN_ID, "alpha")
        self.assertEqual(session_bootstrap.AGENT_NAME, "hepta-agent-alpha")
        self.assertEqual(session_bootstrap.AGENT_UID, 2104)
        self.assertEqual(session_bootstrap.GATEWAY_NAME, "hepta-gw-alpha")
        self.assertEqual(session_bootstrap.GATEWAY_UID, 2101)
        self.assertEqual(session_bootstrap.GATEWAY_GID, 2101)
        self.assertEqual(
            session_bootstrap.GATEWAY_SUPPLEMENTARY_GROUPS,
            ())
        self.assertEqual(
            session_bootstrap.SUPERVISOR_SOCKET,
            "/run/hepta-tool-gateway-alpha/session-supervisor.sock")

    def test_bootstrap_requires_primary_group_exclusivity(self) -> None:
        passwd = mock.Mock(
            pw_uid=2101, pw_gid=2101, pw_name="hepta-gw-alpha")
        primary = mock.Mock(
            gr_gid=2101, gr_name="hepta-gw-alpha", gr_mem=[])

        with (
                mock.patch.object(
                    session_bootstrap.pwd, "getpwnam",
                    return_value=passwd),
                mock.patch.object(
                    session_bootstrap.grp, "getgrnam",
                    return_value=primary),
                mock.patch.object(
                    session_bootstrap.os, "getgrouplist",
                    return_value=[2101])):
                session_bootstrap._validate_identity(
                    "hepta-gw-alpha", 2101, 2101)

        primary.gr_mem = ["unexpected-member"]
        with (
                mock.patch.object(
                    session_bootstrap.pwd, "getpwnam",
                    return_value=passwd),
                mock.patch.object(
                    session_bootstrap.grp, "getgrnam",
                    return_value=primary),
                mock.patch.object(
                    session_bootstrap.os, "getgrouplist",
                    return_value=[2101])):
            with self.assertRaisesRegex(
                    session_bootstrap.BootstrapError,
                    "FIXED_IDENTITY_GROUP_MEMBERSHIP_UNSAFE"):
                session_bootstrap._validate_identity(
                    "hepta-gw-alpha", 2101, 2101)

        primary.gr_mem = []
        with (
                mock.patch.object(
                    session_bootstrap.pwd, "getpwnam",
                    return_value=passwd),
                mock.patch.object(
                    session_bootstrap.grp, "getgrnam",
                    return_value=primary),
                mock.patch.object(
                    session_bootstrap.os, "getgrouplist",
                    return_value=[2101, 2001])):
            with self.assertRaisesRegex(
                    session_bootstrap.BootstrapError,
                    "FIXED_IDENTITY_SUPPLEMENTARY_GROUP_UNSAFE"):
                session_bootstrap._validate_identity(
                    "hepta-gw-alpha", 2101, 2101)

    def test_runtime_domain_path_drift_fails_closed(self) -> None:
        document = json.loads((
            ROOT / "systemd/hepta-agent-trust-domain.json.example"
        ).read_text(encoding="utf-8"))
        document["socket_path"] = "/run/hepta-agent-beta/tools.sock"
        with tempfile.TemporaryDirectory(prefix="hepta-runtime-domain-") as temp:
            path = Path(temp) / "domain.json"
            path.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8")
            with self.assertRaisesRegex(
                    runtime_domain.TrustDomainRuntimeError,
                    "TRUST_DOMAIN_RUNTIME_PATH_DRIFT"):
                runtime_domain.load_runtime_config(
                    path, require_root_metadata=False)

    def test_runtime_templates_are_instance_isolated(self) -> None:
        service = (
            ROOT / "systemd/hepta-tool-gateway@.service"
        ).read_text(encoding="utf-8")
        tool_socket = (
            ROOT / "systemd/hepta-tool-gateway@.socket"
        ).read_text(encoding="utf-8")
        supervisor = (
            ROOT / "systemd/hepta-tool-session-supervisor@.socket"
        ).read_text(encoding="utf-8")
        execution_service = (
            ROOT / "systemd/hepta-execution-simulator@.service"
        ).read_text(encoding="utf-8")
        execution_socket = (
            ROOT / "systemd/hepta-execution-simulator@.socket"
        ).read_text(encoding="utf-8")
        event_socket = (
            ROOT / "systemd/hepta-execution-events-simulator@.socket"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "EnvironmentFile=/etc/heptatrader/trust-domains/%i.env",
            service)
        self.assertIn(
            "ListenStream=/run/hepta-agent-%i/tools.sock", tool_socket)
        self.assertIn("SocketUser=hepta-agent-%i", tool_socket)
        self.assertIn(
            "ListenStream=/run/hepta-tool-gateway-%i/"
            "session-supervisor.sock",
            supervisor)
        self.assertIn("User=hepta-gw-%i", service)
        self.assertIn("Group=hepta-gw-%i", service)
        self.assertIn("SupplementaryGroups=\n", service)
        self.assertNotIn("SupplementaryGroups=hepta-gateway", service)
        self.assertIn("SocketUser=hepta-gw-%i", supervisor)
        self.assertIn("SocketGroup=hepta-gw-%i", supervisor)
        self.assertIn("Environment=HEPTA_TOOL_AGENT_ID=%i", service)
        self.assertIn(
            "LoadCredential=hepta-supervisor-lease-key:"
            "/etc/heptatrader/credentials/trust-domains/%i/"
            "hepta-supervisor-lease.key",
            service)
        self.assertNotIn("\nUser=hepta-gateway\n", service)
        self.assertIn("User=hepta-exec-%i", execution_service)
        self.assertIn("Group=hepta-exec-%i", execution_service)
        self.assertIn(
            "EnvironmentFile=/etc/heptatrader/trust-domains/"
            "%i.execution.env", execution_service)
        self.assertIn(
            "LoadCredential=hepta-execution-fence:"
            "/etc/heptatrader/credentials/trust-domains/%i/"
            "hepta-execution-simulator-fence", execution_service)
        for text, leaf in (
                (execution_socket, "execution.sock"),
                (event_socket, "events.sock")):
            self.assertIn(
                f"ListenStream=/run/hepta-execution-%i/{leaf}", text)
            self.assertIn("SocketUser=hepta-gw-%i", text)
            self.assertIn("SocketGroup=hepta-gw-%i", text)
            self.assertIn("SocketMode=0600", text)
            self.assertNotIn("SocketGroup=hepta-gateway", text)
        for text in (
                service, tool_socket, supervisor, execution_service,
                execution_socket, event_socket):
            self.assertNotIn("PAPER-V", text)
            self.assertNotIn("LIVE_", text)

    def test_versioned_multi_domain_fixture_is_isolated(self) -> None:
        result = trust_domains.validate(
            trust_domains.DEFAULT_POLICY,
            trust_domains.DEFAULT_FIXTURE,
            trust_domains.IDENTITIES)
        self.assertTrue(result["passed"])
        self.assertEqual(result["domain_count"], 2)
        self.assertFalse(result["paper_authorized"])
        self.assertFalse(result["live_authorized"])

    def test_provisioned_two_domain_identities_are_exact(self) -> None:
        result = self.validated()
        trust_domains.validate_provisioned_identities(
            result["domains"], PASSWD, GROUP)

    def test_missing_domain_user_fails_closed(self) -> None:
        passwd = PASSWD.replace(
            "hepta-gw-openclaw-b:x:2102:2102:gateway:/nonexistent:"
            "/usr/sbin/nologin\n", "")
        with self.assertRaisesRegex(
                trust_domains.TrustDomainError, "identity is missing"):
            trust_domains.validate_provisioned_identities(
                self.validated()["domains"], passwd, GROUP)

    def test_wrong_domain_uid_or_gid_fails_closed(self) -> None:
        passwd = PASSWD.replace(
            "hepta-gw-openclaw-b:x:2102:2102:",
            "hepta-gw-openclaw-b:x:2199:2199:")
        with self.assertRaisesRegex(
                trust_domains.TrustDomainError, "UID/GID mismatch"):
            trust_domains.validate_provisioned_identities(
                self.validated()["domains"], passwd, GROUP)

    def test_provisioned_shared_uid_fails_closed(self) -> None:
        passwd = PASSWD.replace(
            "hepta-gw-openclaw-b:x:2102:2102:",
            "hepta-gw-openclaw-b:x:2101:2102:")
        with self.assertRaisesRegex(
                trust_domains.TrustDomainError,
                "UID/GID mismatch|UID is shared"):
            trust_domains.validate_provisioned_identities(
                self.validated()["domains"], passwd, GROUP)

    def test_unexpected_gateway_supplementary_group_fails_closed(self) -> None:
        group = GROUP.replace(
            "hepta-gateway:x:2001:",
            "hepta-gateway:x:2001:hepta-gw-codex-a")
        with self.assertRaisesRegex(
                trust_domains.TrustDomainError,
                "supplementary groups mismatch"):
            trust_domains.validate_provisioned_identities(
                self.validated()["domains"], PASSWD, group)

    def test_wrong_credential_path_fails_closed(self) -> None:
        fixture = json.loads(
            trust_domains.DEFAULT_FIXTURE.read_text(encoding="utf-8"))
        fixture["domains"][1]["lease_credential_path"] = (
            fixture["domains"][0]["lease_credential_path"])
        with tempfile.TemporaryDirectory(prefix="hepta-trust-domain-") as temp:
            path = Path(temp) / "fixture.json"
            path.write_text(json.dumps(fixture) + "\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(
                    trust_domains.TrustDomainError,
                    "identity/path does not match|share lease_credential_path"):
                trust_domains.validate(
                    trust_domains.DEFAULT_POLICY, path,
                    trust_domains.IDENTITIES)

    def test_execution_binding_mismatch_fails_closed(self) -> None:
        fixture = json.loads(
            trust_domains.DEFAULT_FIXTURE.read_text(encoding="utf-8"))
        fixture["domains"][0]["execution_gateway_uid"] = 2102
        with tempfile.TemporaryDirectory(prefix="hepta-trust-domain-") as temp:
            path = Path(temp) / "fixture.json"
            path.write_text(json.dumps(fixture) + "\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(
                    trust_domains.TrustDomainError,
                    "Execution Gateway UID/Agent ID binding drifted"):
                trust_domains.validate(
                    trust_domains.DEFAULT_POLICY, path,
                    trust_domains.IDENTITIES)

    def test_declarative_staging_is_explicit_and_round_trips(self) -> None:
        result = self.validated()
        with tempfile.TemporaryDirectory(
                prefix="hepta-trust-domain-stage-") as temp:
            staging = Path(temp).resolve()
            trust_domains.materialize_staging_root(staging, result)
            trust_domains.validate_staging_root(staging, result)
            staged_runtime = runtime_domain.load_runtime_config(
                staging / "etc/heptatrader/trust-domains/codex-a.json",
                require_root_metadata=False)
            staged_uid_runtime = runtime_domain.load_runtime_config(
                staging / "etc/heptatrader/trust-domains/uid-2104.json",
                require_root_metadata=False)
            self.assertEqual(staged_runtime["gateway_uid"], 2101)
            self.assertEqual(staged_runtime["execution_uid"], 2111)
            self.assertEqual(staged_uid_runtime, staged_runtime)
            domain_metadata = os.lstat(
                staging / "etc/heptatrader/trust-domains/codex-a.json")
            uid_metadata = os.lstat(
                staging / "etc/heptatrader/trust-domains/uid-2104.json")
            self.assertEqual(domain_metadata.st_nlink, 1)
            self.assertEqual(uid_metadata.st_nlink, 1)
            self.assertEqual(
                stat.S_IMODE(domain_metadata.st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE(uid_metadata.st_mode), 0o640)
            self.assertNotEqual(
                (domain_metadata.st_dev, domain_metadata.st_ino),
                (uid_metadata.st_dev, uid_metadata.st_ino))
            dropin = (
                staging / "etc/heptatrader/trust-domains/"
                "codex-a.agent-host.conf"
            ).read_text(encoding="utf-8")
            self.assertIn("User=hepta-agent-codex-a\n", dropin)
            self.assertIn("Group=hepta-agent-codex-a\n", dropin)
            self.assertIn("SupplementaryGroups=\n", dropin)
            self.assertIn(
                "[Unit]\n"
                "BindsTo=hepta-broker-egress-policy.service\n"
                "After=hepta-broker-egress-policy.service\n",
                dropin)
            self.assertIn("CapabilityBoundingSet=\n", dropin)
            self.assertIn("AmbientCapabilities=\n", dropin)
            self.assertIn("RestrictNamespaces=yes\n", dropin)
            self.assertIn(
                "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n",
                dropin)
            self.assertIn(
                "Environment=HEPTA_AGENT_DOMAIN_CONFIG="
                "/etc/heptatrader/trust-domains/uid-2104.json\n",
                dropin)
            execution_env = (
                staging / "etc/heptatrader/trust-domains/"
                "codex-a.execution.env"
            ).read_text(encoding="utf-8")
            self.assertIn(
                "HEPTA_EXECUTION_GATEWAY_UID=2101\n", execution_env)
            self.assertIn(
                "HEPTA_EXECUTION_GATEWAY_AGENT_ID=codex-a\n",
                execution_env)
            gateway_env = (
                staging / "etc/heptatrader/trust-domains/codex-a.env"
            ).read_text(encoding="utf-8")
            self.assertIn("HEPTA_TOOL_ALLOW_TRADE=0\n", gateway_env)
            self.assertIn("HEPTA_TOOL_SESSION_TEMPLATES=watch\n", gateway_env)
            self.assertIn(
                "HEPTA_TOOL_CONTRACT_BINDINGS="
                "EUR.USD|EUR|CASH|IDEALPRO|USD\n",
                gateway_env,
            )
            manifest = json.loads((
                staging / trust_domains.STAGING_MANIFEST
            ).read_text(encoding="utf-8"))
            self.assertFalse(manifest["credentials_generated"])
            self.assertFalse(manifest["units_enabled"])
            self.assertFalse(manifest["services_started"])
            self.assertFalse(manifest["paper_authorized"])
            self.assertFalse(manifest["live_authorized"])
            self.assertEqual(
                manifest["shared_connect_group_role"],
                "single-domain-compatibility-only")
            self.assertEqual(
                manifest["domain_runtime_config_metadata"],
                "root:root regular non-symlink single-link mode-0600")
            self.assertEqual(
                manifest["uid_runtime_config_metadata"],
                "root:<domain-agent-group> regular non-symlink single-link "
                "mode-0640")
            codex = manifest["domains"][0]
            self.assertEqual(
                codex["uid_runtime_config"],
                "/etc/heptatrader/trust-domains/uid-2104.json")
            self.assertEqual(
                codex["agent_host_dropin"],
                "/etc/heptatrader/trust-domains/codex-a.agent-host.conf")

    def test_uid_runtime_config_aliases_fail_closed(self) -> None:
        result = self.validated()
        for alias_kind in ("symlink", "hardlink"):
            with self.subTest(alias_kind=alias_kind), \
                    tempfile.TemporaryDirectory(
                        prefix="hepta-trust-domain-stage-") as temp:
                staging = Path(temp).resolve()
                trust_domains.materialize_staging_root(staging, result)
                domain = (
                    staging / "etc/heptatrader/trust-domains/codex-a.json")
                uid = (
                    staging / "etc/heptatrader/trust-domains/uid-2104.json")
                uid.unlink()
                if alias_kind == "symlink":
                    uid.symlink_to(domain.name)
                else:
                    os.link(domain, uid)
                with self.assertRaisesRegex(
                        trust_domains.TrustDomainError,
                        "symlink|artifact mismatch"):
                    trust_domains.validate_staging_root(staging, result)

    def test_staging_rejects_unexpected_directory(self) -> None:
        result = self.validated()
        with tempfile.TemporaryDirectory(
                prefix="hepta-trust-domain-stage-") as temp:
            staging = Path(temp).resolve()
            trust_domains.materialize_staging_root(staging, result)
            (staging / "unexpected").mkdir()
            with self.assertRaisesRegex(
                    trust_domains.TrustDomainError,
                    "artifact allowlist mismatch"):
                trust_domains.validate_staging_root(staging, result)

    def test_staging_rejects_fifo(self) -> None:
        result = self.validated()
        with tempfile.TemporaryDirectory(
                prefix="hepta-trust-domain-stage-") as temp:
            staging = Path(temp).resolve()
            trust_domains.materialize_staging_root(staging, result)
            os.mkfifo(staging / "unexpected.fifo", 0o600)
            with self.assertRaisesRegex(
                    trust_domains.TrustDomainError,
                    "non-regular artifact"):
                trust_domains.validate_staging_root(staging, result)

    def test_staging_rejects_unix_socket(self) -> None:
        result = self.validated()
        with tempfile.TemporaryDirectory(
                prefix="hepta-trust-domain-stage-") as temp:
            staging = Path(temp).resolve()
            trust_domains.materialize_staging_root(staging, result)
            artifact = staging / "unexpected.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(artifact))
                with self.assertRaisesRegex(
                        trust_domains.TrustDomainError,
                        "non-regular artifact"):
                    trust_domains.validate_staging_root(staging, result)
            finally:
                listener.close()

    def test_staging_refuses_nonempty_root(self) -> None:
        result = self.validated()
        with tempfile.TemporaryDirectory(
                prefix="hepta-trust-domain-stage-") as temp:
            staging = Path(temp).resolve()
            (staging / "unrelated").write_text("preserve\n", encoding="utf-8")
            with self.assertRaisesRegex(
                    trust_domains.TrustDomainError,
                    "staging root must be empty"):
                trust_domains.materialize_staging_root(staging, result)

    def test_shared_uid_fails_closed(self) -> None:
        fixture = json.loads(
            trust_domains.DEFAULT_FIXTURE.read_text(encoding="utf-8"))
        fixture["domains"][1]["agent_uid"] = fixture["domains"][0]["agent_uid"]
        with tempfile.TemporaryDirectory(prefix="hepta-trust-domain-") as temp:
            path = Path(temp) / "fixture.json"
            path.write_text(
                json.dumps(fixture, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(
                    trust_domains.TrustDomainError, "share agent_uid"):
                trust_domains.validate(
                    trust_domains.DEFAULT_POLICY, path, trust_domains.IDENTITIES)

    def test_cross_domain_socket_path_fails_closed(self) -> None:
        fixture = json.loads(
            trust_domains.DEFAULT_FIXTURE.read_text(encoding="utf-8"))
        fixture["domains"][1]["socket_path"] = (
            fixture["domains"][0]["socket_path"])
        with tempfile.TemporaryDirectory(prefix="hepta-trust-domain-") as temp:
            path = Path(temp) / "fixture.json"
            path.write_text(
                json.dumps(fixture, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(
                    trust_domains.TrustDomainError,
                    "does not match its domain|share socket_path"):
                trust_domains.validate(
                    trust_domains.DEFAULT_POLICY, path, trust_domains.IDENTITIES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
