#!/usr/bin/env python3

"""Rootless lifecycle tests for the explicit Agent session bootstrap."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import threading
from types import SimpleNamespace


def load_module(root: Path):
    path = root / "scripts/hepta_agent_session_bootstrap.py"
    spec = importlib.util.spec_from_file_location(
        "hepta_agent_session_bootstrap_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_boundary_audit_tests(module, root: Path) -> None:
    def expect_error(code: str, callback) -> None:
        try:
            callback()
        except module.BootstrapError as error:
            assert str(error) == code, (str(error), code)
        else:
            raise AssertionError(f"{code} was not rejected")

    with tempfile.TemporaryDirectory(
            prefix="hepta-boundary-audit-", dir=root) as directory:
        fixture = Path(directory)
        fixture.chmod(0o700)
        control_root = fixture / "paper-control"
        control_root.mkdir(mode=0o755)
        control_root.chmod(0o755)
        assert stat.S_IMODE(control_root.stat().st_mode) == 0o755
        control = control_root / "ib-paper-control-alpha"
        control.mkdir(mode=0o750)
        control.chmod(0o750)
        assert stat.S_IMODE(control.stat().st_mode) == 0o750
        marker = control / "kill-switch"
        marker.write_bytes(b"engaged")
        marker.chmod(0o440)
        campaign_root = fixture / "paper-campaigns"
        systemctl = fixture / "systemctl"
        egress = fixture / "hepta-broker-egress-policy"
        systemctl.write_bytes(b"boundary-test-systemctl\n")
        egress.write_bytes(b"boundary-test-egress\n")
        systemctl.chmod(0o755)
        egress.chmod(0o755)

        saved = {
            "ROOT_UID": module.ROOT_UID,
            "DOMAIN_ID": module.DOMAIN_ID,
            "AGENT_UID": module.AGENT_UID,
            "AGENT_GID": module.AGENT_GID,
            "RUNTIME_PARENT": module.RUNTIME_PARENT,
            "PAPER_CAMPAIGN_ROOT": module.PAPER_CAMPAIGN_ROOT,
            "PAPER_CONTROL_ROOT": module.PAPER_CONTROL_ROOT,
            "SYSTEMCTL": module.SYSTEMCTL,
            "BROKER_EGRESS_POLICY": module.BROKER_EGRESS_POLICY,
            "_audit_read_only_command": module._audit_read_only_command,
            "_audit_paper_execution_identity":
                module._audit_paper_execution_identity,
        }
        assert os.geteuid() == os.getegid(), (
            "rootless audit fixture requires matching effective uid/gid")
        module.ROOT_UID = os.geteuid()
        module.DOMAIN_ID = "alpha"
        module.AGENT_UID = os.geteuid()
        module.AGENT_GID = os.getegid()
        module.PAPER_CAMPAIGN_ROOT = campaign_root
        module.PAPER_CONTROL_ROOT = control_root
        module.SYSTEMCTL = str(systemctl)
        module.BROKER_EGRESS_POLICY = str(egress)
        non_root_final_gid = module.ROOT_UID + 123
        non_root_final = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o750,
            st_uid=module.ROOT_UID,
            st_gid=non_root_final_gid,
            st_nlink=2,
        )
        module._audit_require_directory_final_safe(
            non_root_final,
            expected_uid=module.ROOT_UID,
            expected_gid=non_root_final_gid,
            allowed_modes=frozenset({0o750}),
            expected_nlink=2,
        )
        expect_error(
            "BOUNDARY_AUDIT_DIRECTORY_ANCESTOR_UNSAFE",
            lambda: module._audit_require_directory_ancestor_safe(
                non_root_final))
        original_getpwnam = module.pwd.getpwnam
        original_getgrnam = module.grp.getgrnam
        original_getgrouplist = module.os.getgrouplist
        module.pwd.getpwnam = lambda name: SimpleNamespace(
            pw_name=name, pw_uid=2121, pw_gid=2121)
        module.grp.getgrnam = lambda name: SimpleNamespace(
            gr_name=name, gr_gid=2121, gr_mem=[])
        module.os.getgrouplist = lambda _name, _gid: [2121]
        try:
            assert module._audit_paper_execution_identity() == (2121, 2121)
            module.os.getgrouplist = lambda _name, _gid: [2121, 9999]
            expect_error(
                "BOUNDARY_AUDIT_PAPER_IDENTITY_UNSAFE",
                module._audit_paper_execution_identity)
        finally:
            module.pwd.getpwnam = original_getpwnam
            module.grp.getgrnam = original_getgrnam
            module.os.getgrouplist = original_getgrouplist
        module._audit_paper_execution_identity = lambda: (
            os.geteuid(), os.getegid())
        command_calls: list[list[str]] = []
        command_fault = {"unit": False, "egress": False}

        def read_only_command(arguments: list[str]):
            command_calls.append(list(arguments))
            if arguments[0] == module.SYSTEMCTL:
                if command_fault["unit"]:
                    return SimpleNamespace(
                        returncode=0, stdout=b"active\n", stderr=b"")
                return SimpleNamespace(
                    returncode=3, stdout=b"inactive\n", stderr=b"")
            assert arguments == [
                module.BROKER_EGRESS_POLICY, "--check-deny-all"]
            return SimpleNamespace(
                returncode=1 if command_fault["egress"] else 0,
                stdout=b"", stderr=b"denied\n" if command_fault["egress"]
                else b"")

        module._audit_read_only_command = read_only_command

        def make_runtime(name: str) -> Path:
            runtime = fixture / name
            runtime.mkdir(mode=0o711)
            runtime.chmod(0o711)
            module.RUNTIME_PARENT = runtime
            return runtime

        def write_active(
                runtime: Path, generation: int = 7, *,
                token: bytes = b"boundary-secret-" + b"x" * 32,
                fence: bytes | None = None,
                expired: bool = False) -> bytes:
            token_path = runtime / module.TOKEN_NAME
            fence_path = runtime / module.FENCE_TOKEN_NAME
            token_path.write_bytes(token)
            fence_path.write_bytes(token if fence is None else fence)
            token_path.chmod(0o600)
            fence_path.chmod(0o600)
            now_ms = module._epoch_ms()
            accepted_at_ms = now_ms - (120_000 if expired else 1_000)
            body = {
                "schema": "hepta.shadow-watch-lease-receipt.v1",
                "version": 1,
                "domain_id": "alpha",
                "agent_id": "alpha",
                "agent_uid": module.AGENT_UID,
                "boundary": "WATCH",
                "operation": "PROVISION",
                "lease_generation": generation,
                "previous_lease_generation": None,
                "previous_receipt_body_sha256": None,
                "accepted": True,
                "reason_code": "OK",
                "accepted_at_ms": accepted_at_ms,
                "ttl_seconds": 60,
                "expires_at_ms": accepted_at_ms + 60_000,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
            }
            receipt = {
                **body, "body_sha256": module._document_digest(body)}
            receipt_path = runtime / module.WATCH_LEASE_RECEIPT_NAME
            receipt_path.write_bytes(module._canonical_bytes(receipt))
            receipt_path.chmod(0o440)
            return token

        try:
            # A revoked boundary may have no token directory and the audit
            # must not call the mutating runtime-directory constructor.
            absent_runtime = fixture / "absent-runtime"
            module.RUNTIME_PARENT = absent_runtime
            result = module.audit_boundary("revoked", 19)
            assert not absent_runtime.exists()
            assert result["boundary_intact"] is True
            assert result["observed_state"] == "revoked"
            assert result["expected_generation"] == 19
            assert result["watch"] == {
                "token_directory_present": False,
                "lock_file_count": 0,
                "fixed_token_count": 0,
                "fixed_fence_count": 0,
                "authority_receipt_count": 0,
                "managed_temporary_count": 0,
                "unknown_entry_count": 0,
                "credential_pair_state": "ABSENT",
                "lease_generation": None,
                "expires_at_ms": None,
            }
            assert result["paper"]["unit_count"] == 9
            assert result["paper"]["inactive_unit_count"] == 9
            assert set(result["paper"]["unit_states"].values()) == {
                "inactive"}
            assert result["paper"]["campaign_policy_file_count"] == 0
            assert result["paper"]["kill_switch_engaged"] is True
            assert result["paper"]["broker_egress_deny_all"] is True
            assert command_calls[-1] == [
                module.BROKER_EGRESS_POLICY, "--check-deny-all"]
            assert sum(
                call[0] == module.SYSTEMCTL for call in command_calls) == 9
            canonical = module._canonical_bytes(result)
            assert canonical == (
                json.dumps(
                    result, ensure_ascii=True, allow_nan=False,
                    sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("ascii")
            body = dict(result)
            claimed = body.pop("body_sha256")
            assert claimed == module._document_digest(body)
            assert set(result) == {
                "schema", "version", "audited_at_ms", "domain_id",
                "agent_uid", "expected_state", "expected_generation",
                "observed_state", "watch", "paper", "helpers",
                "boundary_intact", "paper_authorized", "live_authorized",
                "mutation_authorized", "direct_broker_access",
                "body_sha256",
            }
            assert result["schema"] == (
                "hepta.agent-session-boundary-audit.v1")
            assert type(result["version"]) is int
            assert type(result["audited_at_ms"]) is int
            for field in (
                    "boundary_intact", "paper_authorized", "live_authorized",
                    "mutation_authorized", "direct_broker_access"):
                assert type(result[field]) is bool
            assert result["paper_authorized"] is False
            assert result["live_authorized"] is False
            assert result["mutation_authorized"] is False
            assert result["direct_broker_access"] is False

            empty = make_runtime("empty")
            empty_result = module.audit_boundary("revoked", None)
            assert empty_result["watch"]["token_directory_present"] is True
            lock = empty / module.LOCK_NAME
            lock.write_bytes(b"")
            lock.chmod(0o600)
            lock_result = module.audit_boundary("revoked", None)
            assert lock_result["watch"]["lock_file_count"] == 1

            active = make_runtime("active")
            secret = write_active(active)
            active_snapshot = {
                path.name: (
                    path.lstat().st_dev, path.lstat().st_ino,
                    path.lstat().st_mode, path.lstat().st_nlink,
                    path.lstat().st_uid, path.lstat().st_gid,
                    path.lstat().st_size, path.lstat().st_mtime_ns,
                    path.lstat().st_ctime_ns,
                )
                for path in active.iterdir()
            }
            active_result = module.audit_boundary("active", 7)
            assert active_result["observed_state"] == "active"
            assert active_result["watch"]["credential_pair_state"] == (
                "MATCHING_DISTINCT")
            assert active_result["watch"]["lease_generation"] == 7
            assert active_result["watch"]["fixed_token_count"] == 1
            assert active_result["watch"]["fixed_fence_count"] == 1
            assert active_result["watch"]["authority_receipt_count"] == 1
            serialized = module._canonical_bytes(active_result)
            assert secret not in serialized
            assert secret.hex().encode("ascii") not in serialized
            assert active_snapshot == {
                path.name: (
                    path.lstat().st_dev, path.lstat().st_ino,
                    path.lstat().st_mode, path.lstat().st_nlink,
                    path.lstat().st_uid, path.lstat().st_gid,
                    path.lstat().st_size, path.lstat().st_mtime_ns,
                    path.lstat().st_ctime_ns,
                )
                for path in active.iterdir()
            }
            expect_error(
                "WATCH_LEASE_RECEIPT_BINDING_INVALID",
                lambda: module.audit_boundary("active", 8))

            expect_error(
                "BOUNDARY_AUDIT_ACTIVE_GENERATION_REQUIRED",
                lambda: module.audit_boundary("active", None))
            expired = make_runtime("expired")
            write_active(expired, expired=True)
            expect_error(
                "BOUNDARY_AUDIT_ACTIVE_LEASE_EXPIRED",
                lambda: module.audit_boundary("active", 7))
            mismatch = make_runtime("mismatch")
            write_active(
                mismatch, fence=b"different-secret-" + b"y" * 32)
            expect_error(
                "BOUNDARY_AUDIT_CREDENTIAL_MISMATCH",
                lambda: module.audit_boundary("active", 7))
            fixed_symlink = make_runtime("fixed-symlink")
            write_active(fixed_symlink)
            (fixed_symlink / module.TOKEN_NAME).unlink()
            (fixed_symlink / module.TOKEN_NAME).symlink_to("/dev/null")
            expect_error(
                "BOUNDARY_AUDIT_FILE_METADATA_UNSAFE",
                lambda: module.audit_boundary("active", 7))

            for index, fixed_name in enumerate((
                    module.TOKEN_NAME, module.FENCE_TOKEN_NAME,
                    module.WATCH_LEASE_RECEIPT_NAME)):
                runtime = make_runtime(f"fixed-{index}")
                (runtime / fixed_name).write_bytes(b"residue")
                expect_error(
                    "BOUNDARY_AUDIT_REVOKED_AUTHORITY_PRESENT",
                    lambda: module.audit_boundary("revoked", 7))
            for index, prefix in enumerate(
                    module.BOUNDARY_AUDIT_MANAGED_PREFIXES):
                runtime = make_runtime(f"temporary-{index}")
                (runtime / (prefix + "1234-0123456789abcdef")).write_bytes(
                    b"residue")
                expect_error(
                    "BOUNDARY_AUDIT_MANAGED_TEMPORARY_PRESENT",
                    lambda: module.audit_boundary("revoked", 7))
            unknown = make_runtime("unknown")
            (unknown / "unexpected").write_bytes(b"unknown")
            expect_error(
                "BOUNDARY_AUDIT_UNKNOWN_ENTRY_PRESENT",
                lambda: module.audit_boundary("revoked", None))
            symlink_entry = make_runtime("symlink-entry")
            (symlink_entry / "unexpected").symlink_to("/dev/null")
            expect_error(
                "BOUNDARY_AUDIT_UNKNOWN_ENTRY_PRESENT",
                lambda: module.audit_boundary("revoked", None))
            unsafe_directory = make_runtime("unsafe-directory")
            unsafe_directory.chmod(0o700)
            expect_error(
                "BOUNDARY_AUDIT_DIRECTORY_METADATA_UNSAFE",
                lambda: module.audit_boundary("revoked", None))
            unsafe_lock = make_runtime("unsafe-lock")
            bad_lock = unsafe_lock / module.LOCK_NAME
            bad_lock.write_bytes(b"")
            bad_lock.chmod(0o644)
            expect_error(
                "BOUNDARY_AUDIT_FILE_METADATA_UNSAFE",
                lambda: module.audit_boundary("revoked", None))
            aliased_target = make_runtime("aliased-target")
            alias = fixture / "runtime-alias"
            alias.symlink_to(aliased_target, target_is_directory=True)
            module.RUNTIME_PARENT = alias
            expect_error(
                "BOUNDARY_AUDIT_DIRECTORY_UNSAFE",
                lambda: module.audit_boundary("revoked", None))

            list_failure = make_runtime("list-failure")
            original_listdir = module.os.listdir
            module.os.listdir = lambda _fd: (_ for _ in ()).throw(
                OSError("injected list failure"))
            try:
                expect_error(
                    "BOUNDARY_AUDIT_INVENTORY_SCAN_FAILED",
                    lambda: module.audit_boundary("revoked", None))
            finally:
                module.os.listdir = original_listdir

            drift = make_runtime("inventory-drift")
            list_calls = [0]

            def drifting_listdir(descriptor: int):
                names = original_listdir(descriptor)
                list_calls[0] += 1
                if list_calls[0] == 1:
                    (drift / "appeared").write_bytes(b"drift")
                return names

            module.os.listdir = drifting_listdir
            try:
                expect_error(
                    "BOUNDARY_AUDIT_INVENTORY_CHANGED",
                    lambda: module.audit_boundary("revoked", None))
            finally:
                module.os.listdir = original_listdir

            fstat_failure = make_runtime("fstat-failure")
            original_fstat = module.os.fstat
            module.os.fstat = lambda _fd: (_ for _ in ()).throw(
                OSError("injected fstat failure"))
            try:
                try:
                    module.audit_boundary("revoked", None)
                except OSError:
                    pass
                else:
                    raise AssertionError("fstat failure was accepted")
            finally:
                module.os.fstat = original_fstat

            campaign_root.mkdir(mode=0o755)
            module.RUNTIME_PARENT = empty
            campaign_result = module.audit_boundary("revoked", None)
            assert campaign_result["paper"][
                "campaign_policy_directory_present"] is True
            policy = campaign_root / "alpha.json"
            policy.write_bytes(b"{}\n")
            policy.chmod(0o600)
            expect_error(
                "BOUNDARY_AUDIT_CAMPAIGN_POLICY_PRESENT",
                lambda: module.audit_boundary("revoked", None))
            policy.unlink()

            marker.chmod(0o600)
            marker.write_bytes(b"disable")
            marker.chmod(0o440)
            expect_error(
                "BOUNDARY_AUDIT_KILL_SWITCH_NOT_ENGAGED",
                lambda: module.audit_boundary("revoked", None))
            marker.chmod(0o600)
            marker.write_bytes(b"engaged")
            marker.chmod(0o400)
            expect_error(
                "BOUNDARY_AUDIT_FILE_METADATA_UNSAFE",
                lambda: module.audit_boundary("revoked", None))
            marker.chmod(0o440)

            command_fault["unit"] = True
            expect_error(
                "BOUNDARY_AUDIT_PAPER_UNIT_NOT_INACTIVE",
                lambda: module.audit_boundary("revoked", None))
            command_fault["unit"] = False
            command_fault["egress"] = True
            expect_error(
                "BOUNDARY_AUDIT_BROKER_EGRESS_NOT_DENY_ALL",
                lambda: module.audit_boundary("revoked", None))
            command_fault["egress"] = False

            systemctl.chmod(0o775)
            expect_error(
                "BOUNDARY_AUDIT_HELPER_METADATA_UNSAFE",
                lambda: module.audit_boundary("revoked", None))
            systemctl.chmod(0o755)

            original_root_uid = module.ROOT_UID
            module.ROOT_UID = original_root_uid + 1
            try:
                expect_error(
                    "ROOT_REQUIRED",
                    lambda: module.audit_boundary("revoked", None))
            finally:
                module.ROOT_UID = original_root_uid
        finally:
            for name, value in saved.items():
                setattr(module, name, value)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    module = load_module(root)
    assert module.ROOT_UID == 0
    assert module.WATCH_LEASE_RECEIPT_NAME == (
        "shadow-watch-lease-receipt.json")
    assert module.FENCE_TOKEN_NAME == ".session-fence.token"
    assert module.IDENTIFIER.fullmatch("codex.agent-1")
    assert not module.IDENTIFIER.fullmatch("")
    assert not module.IDENTIFIER.fullmatch("bad session")
    assert set(module._parser()._subparsers._group_actions[0].choices) == {
        "provision-watch", "rotate", "revoke", "audit-boundary"}
    parsed_audit = module._parser().parse_args([
        "--domain-config", "/etc/heptatrader/trust-domains/alpha.json",
        "audit-boundary", "--expected-state", "active", "--generation", "7",
    ])
    assert parsed_audit.operation == "audit-boundary"
    assert parsed_audit.expected_state == "active"
    assert parsed_audit.generation == 7
    run_boundary_audit_tests(module, root)

    original_run = module.subprocess.run
    module.subprocess.run = lambda *args, **kwargs: SimpleNamespace(
        returncode=0,
        stdout=(
            '{"accepted":true,"reason_code":"OK",'
            '"lease_generation":7}\n'),
        stderr="",
    )
    try:
        result = module._sessionctl(["revoke", "--generation", "6"])
        assert result == {
            "accepted": True,
            "reason_code": "OK",
            "lease_generation": 7,
        }
        module.subprocess.run = lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                '{"accepted":true,"reason_code":"",'
                '"lease_generation":7}\n'),
            stderr="",
        )
        try:
            module._sessionctl(["revoke", "--generation", "6"])
        except module.BootstrapError as error:
            assert str(error) == "SESSIONCTL_RESULT_INVALID"
        else:
            raise AssertionError("non-canonical accepted reason was allowed")
        module.subprocess.run = lambda *args, **kwargs: SimpleNamespace(
            returncode=4,
            stdout=(
                '{"accepted":false,"reason_code":"SESSION_LEASE_NOT_FOUND",'
                '"lease_generation":0}\n'),
            stderr="",
        )
        try:
            module._sessionctl(["revoke", "--generation", "6"])
        except module.SessionNotFoundError as error:
            assert str(error) == "SESSION_LEASE_NOT_FOUND"
        else:
            raise AssertionError("missing durable lease was not distinguished")
    finally:
        module.subprocess.run = original_run

    sessionctl_binary = os.environ.get("HEPTA_SESSIONCTL_TEST_BIN", "")
    if sessionctl_binary:
        assert Path(sessionctl_binary).is_file(), (
            "HEPTA_SESSIONCTL_TEST_BIN must name a built executable")
        with tempfile.TemporaryDirectory(
                prefix="hepta-sessionctl-wire-test-") as directory:
            fixture = Path(directory)
            socket_path = fixture / "supervisor.sock"
            token_path = fixture / "token"
            token_path.write_text("T" * 32 + "\n", encoding="ascii")
            token_path.chmod(0o600)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(socket_path))
            listener.listen(1)
            server_errors: list[BaseException] = []

            def exact(connection: socket.socket, size: int) -> bytes:
                chunks = bytearray()
                while len(chunks) < size:
                    chunk = connection.recv(size - len(chunks))
                    if not chunk:
                        raise RuntimeError("sessionctl request truncated")
                    chunks.extend(chunk)
                return bytes(chunks)

            def field(identifier: int, value: str) -> bytes:
                encoded = value.encode("ascii")
                return struct.pack("!HI", identifier, len(encoded)) + encoded

            def serve() -> None:
                try:
                    connection, _ = listener.accept()
                    with connection:
                        request_size = struct.unpack(
                            "!I", exact(connection, 4))[0]
                        request = exact(connection, request_size)
                        assert request.startswith(b"HSS1")
                        result = (
                            b"HSS1" + field(8, "1") +
                            field(9, "OK") + field(12, "11"))
                        connection.sendall(
                            struct.pack("!I", len(result)) + result)
                except BaseException as error:
                    server_errors.append(error)

            server = threading.Thread(target=serve)
            server.start()
            original_binary = module.SESSIONCTL
            original_socket = module.SUPERVISOR_SOCKET
            module.SESSIONCTL = sessionctl_binary
            module.SUPERVISOR_SOCKET = str(socket_path)
            try:
                result = module._sessionctl([
                    "revoke", "--token-file", str(token_path),
                    "--generation", "11",
                ])
                assert result == {
                    "accepted": True,
                    "reason_code": "OK",
                    "lease_generation": 11,
                }
            finally:
                module.SESSIONCTL = original_binary
                module.SUPERVISOR_SOCKET = original_socket
                server.join(timeout=5)
                listener.close()
            assert not server.is_alive()
            assert not server_errors

    with tempfile.TemporaryDirectory(
            prefix="hepta-agent-bootstrap-test-") as directory:
        runtime = Path(directory)
        runtime.chmod(0o700)
        descriptor = os.open(
            runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        original_uid = module.AGENT_UID
        original_gid = module.AGENT_GID
        original_domain_id = module.DOMAIN_ID
        original_root_uid = module.ROOT_UID
        original_runtime = module.RUNTIME_PARENT
        original_sessionctl = module._sessionctl
        calls: list[list[str]] = []
        generation = [0]

        def sessionctl(arguments: list[str]) -> dict[str, object]:
            calls.append(list(arguments))
            if arguments[0] == "provision":
                generation[0] = 1
            elif arguments[0] == "rotate":
                generation[0] = int(
                    arguments[arguments.index("--generation") + 1]) + 1
            elif arguments[0] == "revoke":
                generation[0] = int(
                    arguments[arguments.index("--generation") + 1])
            return {
                "accepted": True,
                "reason_code": "OK",
                "lease_generation": generation[0],
            }

        module.AGENT_UID = os.geteuid()
        module.AGENT_GID = os.getegid()
        module.RUNTIME_PARENT = runtime
        module._sessionctl = sessionctl
        try:
            first = module.provision_watch(
                descriptor, "codex", "session-001", 3600)
            assert first == 1
            assert calls[-1][0:3] == ["provision", "--template", "watch"]
            assert "--peer-uid" in calls[-1]
            token = runtime / module.TOKEN_NAME
            fence = runtime / module.FENCE_TOKEN_NAME
            before = token.lstat()
            fence_before = fence.lstat()
            assert stat.S_ISREG(before.st_mode)
            assert stat.S_IMODE(before.st_mode) == 0o600
            assert before.st_uid == os.geteuid()
            first_contents = token.read_bytes()
            assert fence.read_bytes() == first_contents
            assert fence_before.st_ino != before.st_ino
            assert fence_before.st_uid == os.geteuid()
            assert fence_before.st_gid == os.getegid()
            assert fence_before.st_nlink == 1
            assert stat.S_IMODE(fence_before.st_mode) == 0o600
            assert 24 <= len(first_contents.rstrip(b"\n")) <= 512

            try:
                module.provision_watch(
                    descriptor, "codex", "session-duplicate", 3600)
            except module.BootstrapError as error:
                assert str(error) == "SESSION_TOKEN_ALREADY_EXISTS"
            else:
                raise AssertionError("duplicate provision did not fail closed")

            second = module.rotate(descriptor, first, 3600)
            assert second == 2
            assert calls[-1][0] == "rotate"
            assert calls[-1][
                calls[-1].index("--token-owner-uid") + 1] == str(module.AGENT_UID)
            after = token.lstat()
            fence_after = fence.lstat()
            assert after.st_ino != before.st_ino
            assert token.read_bytes() != first_contents
            assert fence_after.st_ino != fence_before.st_ino
            assert fence_after.st_ino != after.st_ino
            assert fence.read_bytes() == token.read_bytes()
            assert stat.S_IMODE(after.st_mode) == 0o600

            linked = runtime / "linked-token"
            linked.symlink_to(token.name)
            try:
                module._token_metadata(
                    descriptor, linked.name, owner_uid=os.geteuid())
            except module.BootstrapError:
                pass
            else:
                raise AssertionError("symlink token was accepted")

            third = module.revoke(descriptor, second)
            assert third == second
            assert calls[-1][0] == "revoke"
            assert calls[-1][
                calls[-1].index("--token-owner-uid") + 1] == str(os.geteuid())
            assert not token.exists()
            assert not fence.exists()

            # The two private directory entries must be durable before any
            # supervisor mutation can commit. Verify that ordering for both
            # provision and replacement-pair rotation.
            real_fsync = module.os.fsync
            directory_fsynced = [False]

            def observe_directory_fsync(file_descriptor: int) -> None:
                real_fsync(file_descriptor)
                if file_descriptor == descriptor:
                    directory_fsynced[0] = True

            def require_durable_pair(
                    arguments: list[str]) -> dict[str, object]:
                if arguments[0] in {"provision", "rotate"}:
                    assert directory_fsynced[0], (
                        "supervisor mutation preceded token-pair dir fsync")
                    directory_fsynced[0] = False
                return sessionctl(arguments)

            module.os.fsync = observe_directory_fsync
            module._sessionctl = require_durable_pair
            try:
                durable_generation = module.provision_watch(
                    descriptor, "codex",
                    "session-crash-durable-provision", 3600)
                directory_fsynced[0] = False
                durable_generation = module.rotate(
                    descriptor, durable_generation, 3600)
                module.revoke(descriptor, durable_generation)
            finally:
                module._sessionctl = sessionctl
                module.os.fsync = real_fsync
            assert not token.exists()
            assert not fence.exists()

            # A crash during an uncertain initial provision can preserve one
            # root-only temporary pair.  Generation-bound revoke must use the
            # private fence to reconcile that exact pair instead of leaving
            # the host permanently unable to provision.
            residue_token = (
                runtime /
                ".session-token-provision-4242-0123456789abcdef")
            residue_fence = (
                runtime /
                ".session-fence-provision-4242-fedcba9876543210")
            residue_token.write_bytes(b"P" * 48 + b"\n")
            residue_fence.write_bytes(b"P" * 48 + b"\n")
            residue_token.chmod(0o000)
            residue_fence.chmod(0o600)
            assert module.revoke(descriptor, 1) == 1
            assert calls[-1][0] == "revoke"
            assert calls[-1][
                calls[-1].index("--token-file") + 1] == str(residue_fence)
            assert not residue_token.exists()
            assert not residue_fence.exists()

            # SESSION_NOT_FOUND is an authoritative no-live-generation result
            # for this never-published private provision pair.
            residue_token = (
                runtime /
                ".session-token-provision-4243-0011223344556677")
            residue_fence = (
                runtime /
                ".session-fence-provision-4243-8899aabbccddeeff")
            residue_token.write_bytes(b"Q" * 48 + b"\n")
            residue_fence.write_bytes(b"Q" * 48 + b"\n")
            residue_token.chmod(0o600)
            residue_fence.chmod(0o600)
            normal_sessionctl = module._sessionctl

            def session_not_found(
                    arguments: list[str]) -> dict[str, object]:
                calls.append(list(arguments))
                raise module.SessionNotFoundError("SESSION_NOT_FOUND")

            module._sessionctl = session_not_found
            try:
                assert module.revoke(descriptor, 1) == 1
            finally:
                module._sessionctl = normal_sessionctl
            assert not residue_token.exists()
            assert not residue_fence.exists()

            # Mixed-process and non-initial-generation residue stays closed.
            residue_token = (
                runtime /
                ".session-token-provision-4244-0123456789abcdef")
            residue_fence = (
                runtime /
                ".session-fence-provision-4245-fedcba9876543210")
            residue_token.write_bytes(b"R" * 48 + b"\n")
            residue_fence.write_bytes(b"R" * 48 + b"\n")
            residue_token.chmod(0o600)
            residue_fence.chmod(0o600)
            call_offset = len(calls)
            try:
                module.revoke(descriptor, 1)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_PROVISION_RESIDUE_LAYOUT_INVALID")
            else:
                raise AssertionError("mixed-process residue was reconciled")
            assert len(calls) == call_offset
            residue_fence.unlink()
            residue_fence = (
                runtime /
                ".session-fence-provision-4244-fedcba9876543210")
            residue_fence.write_bytes(b"R" * 48 + b"\n")
            residue_fence.chmod(0o600)
            try:
                module.revoke(descriptor, 2)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_PROVISION_RESIDUE_GENERATION_INVALID")
            else:
                raise AssertionError("non-initial residue was reconciled")
            assert len(calls) == call_offset
            residue_token.unlink()
            residue_fence.unlink()

            # A private token-pair residue can mean that a supervisor commit
            # outlived the bootstrap process. Neither provision nor rotation
            # may create another authority generation until root reconciles it.
            for residue_name in (
                    ".session-token-crash-residue",
                    ".session-fence-crash-residue"):
                residue = runtime / residue_name
                residue.write_bytes(b"crash-residue-bearer\n")
                residue.chmod(0o600)
                call_offset = len(calls)
                try:
                    module.provision_watch(
                        descriptor, "codex",
                        "session-crash-residue-provision", 3600)
                except module.BootstrapError as error:
                    assert str(error) == "SESSION_TOKEN_RESIDUE_PRESENT"
                else:
                    raise AssertionError(
                        "provision ignored unreconciled private residue")
                assert len(calls) == call_offset
                residue.unlink()

            residue_generation = module.provision_watch(
                descriptor, "codex", "session-crash-residue-rotate", 3600)
            residue = runtime / ".session-fence-crash-residue"
            residue.write_bytes(b"crash-residue-bearer\n")
            residue.chmod(0o600)
            call_offset = len(calls)
            try:
                module.rotate(descriptor, residue_generation, 3600)
            except module.BootstrapError as error:
                assert str(error) == "SESSION_TOKEN_RESIDUE_PRESENT"
            else:
                raise AssertionError(
                    "rotate ignored unreconciled private residue")
            assert len(calls) == call_offset
            fixed_token_before = token.lstat()
            fixed_fence_before = fence.lstat()
            try:
                module.revoke(descriptor, residue_generation)
            except module.BootstrapError as error:
                assert str(error) == "SESSION_TOKEN_RESIDUE_PRESENT"
            else:
                raise AssertionError(
                    "revoke ignored a possibly newer crash generation")
            assert len(calls) == call_offset
            assert token.lstat().st_ino == fixed_token_before.st_ino
            assert fence.lstat().st_ino == fixed_fence_before.st_ino
            residue.unlink()
            module.revoke(descriptor, residue_generation)

            # Domain-mode WATCH lifecycle evidence is emitted only from the
            # root bootstrap and is chained to the exact accepted generation.
            # Rootless CI substitutes only the expected bootstrap UID; the
            # production constant above remains fixed to UID 0.
            module.DOMAIN_ID = "alpha"
            module.ROOT_UID = os.geteuid()
            receipt_path = runtime / module.WATCH_LEASE_RECEIPT_NAME
            previous = module._watch_lease_receipt_preflight(
                descriptor, "PROVISION")
            assert previous is None
            receipt_generation = module.provision_watch(
                descriptor, "alpha", "receipt-provision", 3600)
            first_receipt = module._publish_watch_lease_receipt_or_compensate(
                descriptor,
                operation="PROVISION",
                generation=receipt_generation,
                agent_id="alpha",
                ttl_seconds=3600,
                accepted_at_ms=1_000_000,
                previous=previous,
            )
            assert receipt_path.read_bytes() == module._canonical_bytes(
                first_receipt)
            receipt_metadata = receipt_path.lstat()
            assert receipt_metadata.st_uid == os.geteuid()
            assert receipt_metadata.st_gid == os.getegid()
            assert stat.S_IMODE(receipt_metadata.st_mode) == 0o440
            module._validate_watch_lease_receipt(
                first_receipt, expected_generation=receipt_generation)
            previous = module._watch_lease_receipt_preflight(
                descriptor, "ROTATE", receipt_generation)
            rotated_generation = module.rotate(
                descriptor, receipt_generation, 3600)
            rotated_receipt = (
                module._publish_watch_lease_receipt_or_compensate(
                    descriptor,
                    operation="ROTATE",
                    generation=rotated_generation,
                    agent_id="alpha",
                    ttl_seconds=3600,
                    accepted_at_ms=2_000_000,
                    previous=previous,
                )
            )
            assert rotated_receipt["previous_lease_generation"] == (
                receipt_generation)
            assert rotated_receipt["previous_receipt_body_sha256"] == (
                first_receipt["body_sha256"])
            assert receipt_path.read_bytes() == module._canonical_bytes(
                rotated_receipt)
            module.revoke(descriptor, rotated_generation)
            assert not token.exists()
            assert not receipt_path.exists()

            # If the supervisor has already reaped an expired WATCH lease,
            # exact expired receipt evidence permits removal of only the
            # matching local bearer and receipt. Any earlier or mismatched
            # state remains fail-closed.
            expired_generation = module.provision_watch(
                descriptor, "alpha", "receipt-expired", 60)
            expired_receipt = (
                module._publish_watch_lease_receipt_or_compensate(
                    descriptor,
                    operation="PROVISION",
                    generation=expired_generation,
                    agent_id="alpha",
                    ttl_seconds=60,
                    accepted_at_ms=1,
                    previous=None,
                )
            )
            assert expired_receipt["expires_at_ms"] < module._epoch_ms()
            normal_sessionctl = module._sessionctl
            module._sessionctl = lambda _arguments: (_ for _ in ()).throw(
                module.SessionNotFoundError("SESSION_NOT_FOUND"))
            try:
                assert module.revoke(
                    descriptor, expired_generation) == expired_generation
            finally:
                module._sessionctl = normal_sessionctl
            assert not token.exists()
            assert not receipt_path.exists()

            # A digest-valid but domain-mismatched prior receipt cannot be
            # used as the rotation chain root.
            receipt_generation = module.provision_watch(
                descriptor, "alpha", "receipt-tamper", 3600)
            first_receipt = module._publish_watch_lease_receipt_or_compensate(
                descriptor,
                operation="PROVISION",
                generation=receipt_generation,
                agent_id="alpha",
                ttl_seconds=3600,
                accepted_at_ms=3_000_000,
                previous=None,
            )
            tampered = dict(first_receipt)
            tampered["domain_id"] = "beta"
            tampered_body = dict(tampered)
            tampered_body.pop("body_sha256")
            tampered["body_sha256"] = module._document_digest(tampered_body)
            receipt_path.chmod(0o640)
            receipt_path.write_bytes(module._canonical_bytes(tampered))
            receipt_path.chmod(0o440)
            try:
                module._watch_lease_receipt_preflight(
                    descriptor, "ROTATE", receipt_generation)
            except module.BootstrapError as error:
                assert str(error) == "WATCH_LEASE_RECEIPT_BINDING_INVALID"
            else:
                raise AssertionError("domain-mismatched receipt was accepted")
            receipt_path.chmod(0o640)
            receipt_path.write_bytes(module._canonical_bytes(first_receipt))
            receipt_path.chmod(0o440)
            module.revoke(descriptor, receipt_generation)

            # If atomic receipt publication fails after the supervisor has
            # accepted and the token has committed, the exact generation is
            # revoked and both bearer and partial evidence disappear.
            original_fault = module._fault
            receipt_generation = module.provision_watch(
                descriptor, "alpha", "receipt-compensation", 3600)

            def fail_receipt_publish(stage: str) -> None:
                if stage == "receipt.after_publish":
                    raise OSError(5, stage)

            module._fault = fail_receipt_publish
            call_offset = len(calls)
            try:
                module._publish_watch_lease_receipt_or_compensate(
                    descriptor,
                    operation="PROVISION",
                    generation=receipt_generation,
                    agent_id="alpha",
                    ttl_seconds=3600,
                    accepted_at_ms=4_000_000,
                    previous=None,
                )
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_PROVISION_RECEIPT_"
                    "LOCAL_COMMIT_FAILED_REVOKED")
            else:
                raise AssertionError(
                    "failed receipt publication left an accepted lease")
            finally:
                module._fault = original_fault
            assert [call[0] for call in calls[call_offset:]] == ["revoke"]
            assert not token.exists()
            assert not receipt_path.exists()

            original_fault = module._fault

            # The fixed path may exist before commit, but it remains owned by
            # the bootstrap identity until the final fchown. The fixed Agent
            # identity therefore cannot copy a bearer that might later need
            # compensation.
            precommit_observations: list[tuple[int, int, int]] = []

            def observe_private_publish(stage: str) -> None:
                if stage != "provision.before_agent_commit":
                    return
                metadata = token.lstat()
                precommit_observations.append((
                    metadata.st_uid,
                    metadata.st_gid,
                    stat.S_IMODE(metadata.st_mode),
                ))
                assert stat.S_IMODE(metadata.st_mode) == 0o600

            module._fault = observe_private_publish
            private_generation = module.provision_watch(
                descriptor, "codex", "session-private-linearization", 3600)
            module._fault = original_fault
            assert precommit_observations == [
                (os.geteuid(), os.getegid(), 0o600)]
            committed = token.lstat()
            assert committed.st_uid == os.geteuid()
            assert committed.st_gid == os.getegid()
            assert stat.S_IMODE(committed.st_mode) == 0o600
            module.revoke(descriptor, private_generation)
            assert not token.exists()

            def fail_at(target: str):
                def inject(stage: str) -> None:
                    if stage == target:
                        raise OSError(5, target)
                return inject

            def assert_compensated(
                    operation: str, stage: str, invoke) -> None:
                call_offset = len(calls)
                module._fault = fail_at(stage)
                try:
                    invoke()
                except module.BootstrapError as error:
                    assert str(error) == (
                        f"SESSION_{operation}_LOCAL_COMMIT_FAILED_REVOKED")
                else:
                    raise AssertionError(
                        f"{operation} fault {stage} did not fail")
                finally:
                    module._fault = original_fault
                transaction_calls = calls[call_offset:]
                assert transaction_calls[0][0] == operation.lower()
                assert transaction_calls[-1][0] == "revoke"
                assert "--token-owner-uid" in transaction_calls[-1]
                accepted_generation = int(
                    transaction_calls[-1][
                        transaction_calls[-1].index("--generation") + 1])
                assert accepted_generation == generation[0]
                assert not token.exists()
                assert not [
                    entry for entry in runtime.iterdir()
                    if (
                        entry.name.startswith(".session-token-")
                        or entry.name.startswith(".session-fence-")
                        or entry.name == module.FENCE_TOKEN_NAME
                    )
                ]

            # Every fallible provision stage precedes Agent readability. A
            # fault must revoke the exact lease generation and leave no token.
            for stage in (
                    "provision.before_private_stage",
                    "provision.after_private_stage",
                    "provision.after_publish",
                    "provision.after_publish_fsync",
                    "provision.after_temporary_unlink",
                    "provision.before_agent_commit",
                    "agent_commit.before_chown"):
                assert_compensated(
                    "PROVISION", stage,
                    lambda: module.provision_watch(
                        descriptor, "codex", "session-provision-fault", 3600))

            # Rotate has both the old on-disk token and a newly accepted
            # supervisor owner. Its recovery link keeps the exact replacement
            # token available for compensation across atomic publication.
            for stage in (
                    "rotate.before_private_stage",
                    "rotate.after_private_stage",
                    "rotate.after_recovery_link",
                    "rotate.before_publish",
                    "rotate.after_publish",
                    "rotate.after_publish_fsync",
                    "rotate.after_recovery_unlink",
                    "rotate.before_agent_commit",
                    "agent_commit.before_chown"):
                current = module.provision_watch(
                    descriptor, "codex", "session-rotate-fault", 3600)
                assert_compensated(
                    "ROTATE", stage,
                    lambda current=current: module.rotate(
                        descriptor, current, 3600))

            # A concurrently replaced path is never unlinked as compensation.
            current = module.provision_watch(
                descriptor, "codex", "session-path-change", 3600)
            preserved = b"operator-owned-file\n"

            def replace_path_then_fail(stage: str) -> None:
                if stage != "rotate.before_publish":
                    return
                token.unlink()
                token.write_bytes(preserved)
                token.chmod(0o600)
                raise OSError(5, stage)

            module._fault = replace_path_then_fail
            try:
                module.rotate(descriptor, current, 3600)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_ROTATE_LOCAL_COMMIT_FAILED_REVOKED")
            else:
                raise AssertionError("changed token path was not rejected")
            finally:
                module._fault = original_fault
            assert token.read_bytes() == preserved
            assert calls[-1][0] == "revoke"
            try:
                module.provision_watch(
                    descriptor, "codex", "session-existing-file", 3600)
            except module.BootstrapError as error:
                assert str(error) == "SESSION_TOKEN_ALREADY_EXISTS"
            else:
                raise AssertionError("existing operator file was overwritten")
            assert token.read_bytes() == preserved
            token.unlink()

            # If the supervisor cannot confirm compensation, the exact new
            # token is quarantined mode 000 rather than exposed to the Agent.
            normal_sessionctl = module._sessionctl

            def reject_compensation(
                    arguments: list[str]) -> dict[str, object]:
                if arguments[0] == "revoke":
                    calls.append(list(arguments))
                    raise module.BootstrapError(
                        "SESSION_SUPERVISOR_REJECTED")
                return sessionctl(arguments)

            module._sessionctl = reject_compensation
            module._fault = fail_at("provision.after_publish")
            try:
                module.provision_watch(
                    descriptor, "codex", "session-compensation-fault", 3600)
            except module.BootstrapError as error:
                assert str(error) == "SESSION_PROVISION_COMPENSATION_FAILED"
            else:
                raise AssertionError(
                    "failed provision compensation was accepted")
            finally:
                module._fault = original_fault
                module._sessionctl = normal_sessionctl
            assert token.exists()
            assert stat.S_IMODE(token.lstat().st_mode) == 0
            recovery_fence = module._fence_token_metadata(descriptor)
            assert stat.S_IMODE(recovery_fence.st_mode) == 0o600
            assert module._revoke_exact_fence(
                descriptor,
                generation[0],
                [module.FENCE_TOKEN_NAME],
                recovery_fence,
            )
            quarantined_token = token.lstat()
            module._cleanup_exact(
                descriptor, [module.TOKEN_NAME], quarantined_token)
            module._cleanup_exact(
                descriptor, [module.FENCE_TOKEN_NAME], recovery_fence)
            os.fsync(descriptor)
            assert not token.exists()
            assert not fence.exists()
            assert calls[-1][0] == "revoke"

            # If delivery quarantine itself fails while authoritative revoke
            # is unavailable, exact-unlink may remove only the Agent bearer.
            # The distinct root fence must survive every fallback stage.
            for quarantine_stage in (
                    "quarantine.before_open",
                    "quarantine.before_chown",
                    "quarantine.before_chmod",
                    "quarantine.before_fsync"):
                def fail_commit_and_quarantine(
                        stage: str,
                        target: str = quarantine_stage) -> None:
                    if stage in {"provision.after_publish", target}:
                        raise OSError(5, stage)

                module._sessionctl = reject_compensation
                module._fault = fail_commit_and_quarantine
                try:
                    module.provision_watch(
                        descriptor, "codex",
                        "session-fence-preservation", 3600)
                except module.BootstrapError as error:
                    assert str(error) == (
                        "SESSION_PROVISION_COMPENSATION_FAILED")
                else:
                    raise AssertionError(
                        "failed revoke lost its recovery boundary")
                finally:
                    module._fault = original_fault
                    module._sessionctl = normal_sessionctl
                assert not token.exists()
                recovery_fence = module._fence_token_metadata(descriptor)
                assert stat.S_IMODE(recovery_fence.st_mode) == 0o600
                assert module._revoke_exact_fence(
                    descriptor,
                    generation[0],
                    [module.FENCE_TOKEN_NAME],
                    recovery_fence,
                )
                module._cleanup_exact(
                    descriptor,
                    [module.FENCE_TOKEN_NAME],
                    recovery_fence,
                )
                os.fsync(descriptor)
                assert not fence.exists()

            # If any individual quarantine operation fails on a readable
            # residue, the fallback exact-unlinks that inode.
            for stage in (
                    "quarantine.before_open",
                    "quarantine.before_chown",
                    "quarantine.before_chmod",
                    "quarantine.before_fsync"):
                token.write_bytes(b"quarantine-fallback-token\n")
                token.chmod(0o600)
                residue = token.lstat()
                module._fault = fail_at(stage)
                try:
                    module._secure_unrevoked_token(
                        descriptor, [module.TOKEN_NAME], residue)
                finally:
                    module._fault = original_fault
                assert not token.exists()

            # A missing private-state proof before fchown is still
            # pre-commit. Both provision and rotate must revoke the exact
            # accepted generation rather than leak an active inaccessible
            # lease.
            real_private = module._agent_commit_private
            for operation in ("PROVISION", "ROTATE"):
                if operation == "ROTATE":
                    current = module.provision_watch(
                        descriptor, "codex",
                        "session-private-check-rotate", 3600)
                    invoke = lambda current=current: module.rotate(
                        descriptor, current, 3600)
                else:
                    invoke = lambda: module.provision_watch(
                        descriptor, "codex",
                        "session-private-check-provision", 3600)
                call_offset = len(calls)
                module._agent_commit_private = (
                    lambda *_args, **_kwargs: False)
                try:
                    try:
                        invoke()
                    except module.BootstrapError as error:
                        assert str(error) == (
                            f"SESSION_{operation}_"
                            "LOCAL_COMMIT_FAILED_REVOKED")
                    else:
                        raise AssertionError(
                            f"{operation} private check failure was accepted")
                finally:
                    module._agent_commit_private = real_private
                assert [call[0] for call in calls[call_offset:]] == [
                    operation.lower(), "revoke"]
                assert not token.exists()

            # An ownership syscall failure before applying the transition is
            # positively private and must also compensate. Force visibility
            # false because this rootless fixture intentionally maps Agent and
            # bootstrap to the same host uid.
            real_fchown = module.os.fchown
            real_visible = module._agent_commit_visible

            def fail_before_apply(
                    _file_descriptor: int, _uid: int, _gid: int) -> None:
                raise OSError(5, "commit not applied")

            for operation in ("PROVISION", "ROTATE"):
                if operation == "ROTATE":
                    current = module.provision_watch(
                        descriptor, "codex",
                        "session-fchown-fail-rotate", 3600)
                    invoke = lambda current=current: module.rotate(
                        descriptor, current, 3600)
                else:
                    invoke = lambda: module.provision_watch(
                        descriptor, "codex",
                        "session-fchown-fail-provision", 3600)
                call_offset = len(calls)
                module.os.fchown = fail_before_apply
                module._agent_commit_visible = (
                    lambda *_args, **_kwargs: False)
                try:
                    try:
                        invoke()
                    except module.BootstrapError as error:
                        assert str(error) == (
                            f"SESSION_{operation}_"
                            "LOCAL_COMMIT_FAILED_REVOKED")
                    else:
                        raise AssertionError(
                            f"{operation} unapplied fchown was accepted")
                finally:
                    module.os.fchown = real_fchown
                    module._agent_commit_visible = real_visible
                assert [call[0] for call in calls[call_offset:]] == [
                    operation.lower(), "revoke"]
                assert not token.exists()

            # If chown became visible but Python observed an asynchronous local
            # error, the exact Agent-readable inode is committed. It must not
            # enter compensation after a concurrent reader could copy it.
            injected_commit_error = [False]

            def applied_then_error(
                    file_descriptor: int, uid: int, gid: int) -> None:
                real_fchown(file_descriptor, uid, gid)
                if not injected_commit_error[0]:
                    injected_commit_error[0] = True
                    raise OSError(5, "commit result interrupted")

            call_offset = len(calls)
            module.os.fchown = applied_then_error
            try:
                committed_generation = module.provision_watch(
                    descriptor, "codex", "session-commit-uncertain", 3600)
            finally:
                module.os.fchown = real_fchown
            assert injected_commit_error == [True]
            assert [call[0] for call in calls[call_offset:]] == ["provision"]
            assert stat.S_IMODE(token.lstat().st_mode) == 0o600
            module.revoke(descriptor, committed_generation)
            assert not token.exists()

            # If fchown may have committed but both positive visibility and
            # private-state verification are unavailable, root must revoke
            # the exact accepted generation before returning failure.
            private_checks = [0]

            def first_private_check_only(*args, **kwargs) -> bool:
                private_checks[0] += 1
                if private_checks[0] == 1:
                    return real_private(*args, **kwargs)
                return False

            call_offset = len(calls)
            module.os.fchown = applied_then_error
            module._agent_commit_visible = lambda *_args, **_kwargs: False
            module._agent_commit_private = first_private_check_only
            injected_commit_error[0] = False
            try:
                try:
                    module.provision_watch(
                        descriptor, "codex",
                        "session-commit-verification-unavailable", 3600)
                except module.BootstrapError as error:
                    assert str(error) == (
                        "SESSION_PROVISION_AGENT_COMMIT_UNCERTAIN_"
                        "LOCAL_COMMIT_FAILED_REVOKED")
                else:
                    raise AssertionError(
                        "unverifiable ownership commit was accepted")
            finally:
                module.os.fchown = real_fchown
                module._agent_commit_visible = real_visible
                module._agent_commit_private = real_private
            assert [call[0] for call in calls[call_offset:]] == [
                "provision", "revoke"]
            assert not token.exists()

            # Exercise the real post-fchown fstat failure path rather than
            # replacing the visibility helpers. An unreadable ownership
            # result is uncertain and must revoke through the separate fence.
            real_fstat = module.os.fstat
            fchown_attempted = [False]

            def fchown_then_fail(
                    _file_descriptor: int, _uid: int, _gid: int) -> None:
                fchown_attempted[0] = True
                raise OSError(5, "ownership result interrupted")

            def post_fchown_fstat(file_descriptor: int):
                if fchown_attempted[0]:
                    raise OSError(5, "ownership metadata unavailable")
                return real_fstat(file_descriptor)

            call_offset = len(calls)
            module.AGENT_UID = os.geteuid() + 10_000
            module.os.fchown = fchown_then_fail
            module.os.fstat = post_fchown_fstat
            try:
                try:
                    module.provision_watch(
                        descriptor, "codex",
                        "session-fstat-unavailable", 3600)
                except module.BootstrapError as error:
                    assert str(error) == (
                        "SESSION_PROVISION_AGENT_COMMIT_UNCERTAIN_"
                        "LOCAL_COMMIT_FAILED_REVOKED")
                else:
                    raise AssertionError(
                        "post-fchown fstat failure was accepted")
            finally:
                module.os.fstat = real_fstat
                module.os.fchown = real_fchown
                module.AGENT_UID = os.geteuid()
            assert [call[0] for call in calls[call_offset:]] == [
                "provision", "revoke"]
            assert not token.exists()
            assert not fence.exists()

            current = module.provision_watch(
                descriptor, "codex", "session-rotate-commit-uncertain", 3600)
            call_offset = len(calls)
            private_checks[0] = 0
            injected_commit_error[0] = False
            module.os.fchown = applied_then_error
            module._agent_commit_visible = lambda *_args, **_kwargs: False
            module._agent_commit_private = first_private_check_only
            try:
                try:
                    module.rotate(descriptor, current, 3600)
                except module.BootstrapError as error:
                    assert str(error) == (
                        "SESSION_ROTATE_AGENT_COMMIT_UNCERTAIN_"
                        "LOCAL_COMMIT_FAILED_REVOKED")
                else:
                    raise AssertionError(
                        "unverifiable rotate ownership commit was accepted")
            finally:
                module.os.fchown = real_fchown
                module._agent_commit_visible = real_visible
                module._agent_commit_private = real_private
            assert [call[0] for call in calls[call_offset:]] == [
                "rotate", "revoke"]
            assert not token.exists()

            # If both exact-token quarantine and exact unlink fail, the final
            # safety layer seals the entire runtime directory mode 000. The
            # test restores its private fixture only after verifying the seal.
            token.write_bytes(b"unrevoked-test-token\n")
            token.chmod(0o600)
            exact_metadata = token.lstat()
            original_quarantine = module._quarantine_exact
            original_cleanup = module._cleanup_exact

            def fail_safety_layer(*_args, **_kwargs) -> None:
                raise OSError(5, "forced safety-layer failure")

            module._quarantine_exact = fail_safety_layer
            module._cleanup_exact = fail_safety_layer
            try:
                module._secure_unrevoked_token(
                    descriptor, [module.TOKEN_NAME], exact_metadata)
                assert stat.S_IMODE(os.fstat(descriptor).st_mode) == 0
            finally:
                module._quarantine_exact = original_quarantine
                module._cleanup_exact = original_cleanup
                os.fchmod(descriptor, 0o700)
            assert token.read_bytes() == b"unrevoked-test-token\n"
            token.unlink()

            current = module.provision_watch(
                descriptor, "codex", "session-rotate-comp-fault", 3600)
            module._sessionctl = reject_compensation
            module._fault = fail_at("rotate.after_publish")
            try:
                module.rotate(descriptor, current, 3600)
            except module.BootstrapError as error:
                assert str(error) == "SESSION_ROTATE_COMPENSATION_FAILED"
            else:
                raise AssertionError(
                    "failed rotate compensation was accepted")
            finally:
                module._fault = original_fault
                module._sessionctl = normal_sessionctl
            assert token.exists()
            assert stat.S_IMODE(token.lstat().st_mode) == 0
            assert calls[-1][0] == "revoke"
            for entry in list(runtime.iterdir()):
                if (entry.name == module.TOKEN_NAME or
                        entry.name == module.FENCE_TOKEN_NAME or
                        entry.name.startswith(".session-token-") or
                        entry.name.startswith(".session-fence-")):
                    entry.unlink()

            # Production pre-handoff compensation is root->Agent, not the
            # same-UID shortcut used by most rootless lifecycle checks. The
            # revoke must bind the bootstrap-owned fence UID, never Agent UID.
            module.AGENT_UID = os.geteuid() + 10_000
            module._fault = fail_at("provision.after_publish")
            call_offset = len(calls)
            try:
                module.provision_watch(
                    descriptor, "codex", "session-distinct-owner", 3600)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_PROVISION_LOCAL_COMMIT_FAILED_REVOKED")
            else:
                raise AssertionError(
                    "distinct-owner precommit fault was accepted")
            finally:
                module._fault = original_fault
                module.AGENT_UID = os.geteuid()
            distinct_owner_calls = calls[call_offset:]
            assert [call[0] for call in distinct_owner_calls] == [
                "provision", "revoke"]
            assert distinct_owner_calls[0][
                distinct_owner_calls[0].index("--peer-uid") + 1
            ] == str(os.geteuid() + 10_000)
            assert distinct_owner_calls[1][
                distinct_owner_calls[1].index("--token-owner-uid") + 1
            ] == str(os.geteuid())
            assert not token.exists()
            assert not fence.exists()

            # Agent-visible delivery material may be truncated or chmodded
            # after an uncertain handoff. The independent fence remains the
            # original bearer and is the only path used for compensation.
            original_commit = module._commit_agent_ownership
            observed_fence_contents: list[bytes] = []

            def record_fence_sessionctl(
                    arguments: list[str]) -> dict[str, object]:
                if arguments[0] == "revoke":
                    fence_path = Path(arguments[
                        arguments.index("--token-file") + 1])
                    observed_fence_contents.append(fence_path.read_bytes())
                return sessionctl(arguments)

            def mutate_delivery_then_fail(
                    _directory_fd: int, _descriptor: int,
                    _expected: os.stat_result) -> bool:
                token.write_bytes(b"agent-mutated-bearer\n")
                token.chmod(0)
                raise module.BootstrapError(
                    "SESSION_TOKEN_AGENT_COMMIT_UNCERTAIN")

            module._sessionctl = record_fence_sessionctl
            module._commit_agent_ownership = mutate_delivery_then_fail
            try:
                module.provision_watch(
                    descriptor, "codex", "session-agent-mutation", 3600)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_PROVISION_AGENT_COMMIT_UNCERTAIN_"
                    "LOCAL_COMMIT_FAILED_REVOKED")
            else:
                raise AssertionError(
                    "Agent-mutated delivery bypassed fence compensation")
            finally:
                module._commit_agent_ownership = original_commit
                module._sessionctl = normal_sessionctl
            assert len(observed_fence_contents) == 1
            assert observed_fence_contents[0] != b"agent-mutated-bearer\n"
            assert 24 <= len(observed_fence_contents[0].rstrip(b"\n")) <= 512
            assert not token.exists()
            assert not fence.exists()

            # A committed supervisor mutation may lose its local return. The
            # candidate generation is reconciled with the root fence before
            # any local bearer is discarded.
            def uncertain_provision(
                    arguments: list[str]) -> dict[str, object]:
                calls.append(list(arguments))
                if arguments[0] == "provision":
                    raise module.BootstrapError("SESSIONCTL_RESULT_INVALID")
                assert arguments[0] == "revoke"
                assert int(arguments[
                    arguments.index("--generation") + 1]) == 1
                return {
                    "accepted": True,
                    "reason_code": "OK",
                    "lease_generation": 1,
                }

            module._sessionctl = uncertain_provision
            try:
                module.provision_watch(
                    descriptor, "codex", "session-provision-return-lost", 3600)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_PROVISION_RESULT_UNCERTAIN_REVOKED")
            else:
                raise AssertionError(
                    "ambiguous provision result was treated as no commit")
            finally:
                module._sessionctl = normal_sessionctl
            assert not token.exists()
            assert not fence.exists()

            def rejected_provision(
                    arguments: list[str]) -> dict[str, object]:
                calls.append(list(arguments))
                if arguments[0] == "provision":
                    raise module.BootstrapError("SESSIONCTL_RESULT_INVALID")
                raise module.SessionNotFoundError("SESSION_LEASE_NOT_FOUND")

            module._sessionctl = rejected_provision
            try:
                module.provision_watch(
                    descriptor, "codex", "session-provision-rejected", 3600)
            except module.BootstrapError as error:
                assert str(error) == "SESSION_PROVISION_NOT_COMMITTED"
            else:
                raise AssertionError(
                    "rejected provision did not reconcile candidate")
            finally:
                module._sessionctl = normal_sessionctl
            assert not token.exists()
            assert not fence.exists()

            current = module.provision_watch(
                descriptor, "codex", "session-rotate-return-lost", 3600)

            def uncertain_rotate(
                    arguments: list[str]) -> dict[str, object]:
                calls.append(list(arguments))
                if arguments[0] == "rotate":
                    raise module.BootstrapError("SESSIONCTL_RESULT_INVALID")
                candidate = int(
                    arguments[arguments.index("--generation") + 1])
                assert candidate == current + 1
                return {
                    "accepted": True,
                    "reason_code": "OK",
                    "lease_generation": candidate,
                }

            module._sessionctl = uncertain_rotate
            try:
                module.rotate(descriptor, current, 3600)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_ROTATE_RESULT_UNCERTAIN_REVOKED")
            else:
                raise AssertionError(
                    "ambiguous committed rotate result was accepted")
            finally:
                module._sessionctl = normal_sessionctl
            assert not token.exists()
            assert not fence.exists()

            current = module.provision_watch(
                descriptor, "codex", "session-rotate-rejected", 3600)
            revoke_generations: list[int] = []

            def rejected_rotate(
                    arguments: list[str]) -> dict[str, object]:
                calls.append(list(arguments))
                if arguments[0] == "rotate":
                    raise module.BootstrapError("SESSIONCTL_RESULT_INVALID")
                candidate = int(
                    arguments[arguments.index("--generation") + 1])
                revoke_generations.append(candidate)
                if candidate == current + 1:
                    raise module.SessionNotFoundError(
                        "SESSION_LEASE_NOT_FOUND")
                assert candidate == current
                return {
                    "accepted": True,
                    "reason_code": "OK",
                    "lease_generation": current,
                }

            module._sessionctl = rejected_rotate
            try:
                module.rotate(descriptor, current, 3600)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_ROTATE_NOT_COMMITTED_OLD_REVOKED")
            else:
                raise AssertionError(
                    "rejected rotate left old authority active")
            finally:
                module._sessionctl = normal_sessionctl
            assert revoke_generations == [current + 1, current]
            assert not token.exists()
            assert not fence.exists()

            current = module.provision_watch(
                descriptor, "codex", "session-revoke-return-lost", 3600)
            token_before_failed_revoke = token.read_bytes()
            token_metadata_before_failed_revoke = token.lstat()
            fence_before_failed_revoke = fence.read_bytes()
            fence_metadata_before_failed_revoke = fence.lstat()

            def fail_revoke(
                    arguments: list[str]) -> dict[str, object]:
                calls.append(list(arguments))
                raise module.BootstrapError("SESSIONCTL_RESULT_INVALID")

            module._sessionctl = fail_revoke
            try:
                module.revoke(descriptor, current)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_REVOKE_UNCERTAIN_RECOVERY_REQUIRED")
            else:
                raise AssertionError(
                    "uncertain revoke falsely reported success")
            finally:
                module._sessionctl = normal_sessionctl
            quarantined_token = token.lstat()
            assert module._same_inode(
                quarantined_token, token_metadata_before_failed_revoke)
            assert stat.S_IMODE(quarantined_token.st_mode) == 0
            assert quarantined_token.st_uid == os.geteuid()
            assert quarantined_token.st_gid == os.getegid()
            assert fence.read_bytes() == fence_before_failed_revoke
            assert module._same_file(
                fence.lstat(), fence_metadata_before_failed_revoke)
            assert token.lstat().st_ino != fence.lstat().st_ino
            assert token_before_failed_revoke == fence_before_failed_revoke
            module.revoke(descriptor, current)
            assert not fence.exists()
            assert not token.exists()

            current = module.provision_watch(
                descriptor, "codex", "session-revoke-fence-lost", 3600)
            module._sessionctl = fail_revoke
            try:
                module.revoke(descriptor, current)
            except module.BootstrapError as error:
                assert str(error) == (
                    "SESSION_REVOKE_UNCERTAIN_RECOVERY_REQUIRED")
            else:
                raise AssertionError(
                    "uncertain revoke falsely reported success")
            finally:
                module._sessionctl = normal_sessionctl
            fence.unlink()
            module._sessionctl = lambda _arguments: (_ for _ in ()).throw(
                module.SessionNotFoundError("SESSION_NOT_FOUND"))
            try:
                module.revoke(descriptor, current)
            finally:
                module._sessionctl = normal_sessionctl
            assert not token.exists()
            assert not fence.exists()

            for call in calls:
                if call[0] == "provision":
                    assert call[1:3] == ["--template", "watch"]
                assert "paper" not in call
                assert "live" not in call
        finally:
            module.AGENT_UID = original_uid
            module.AGENT_GID = original_gid
            module.DOMAIN_ID = original_domain_id
            module.ROOT_UID = original_root_uid
            module.RUNTIME_PARENT = original_runtime
            module._sessionctl = original_sessionctl
            os.close(descriptor)
    print(
        "hepta_agent_session_bootstrap_tests: PASS "
        "fault_injections=36 watch_only=verified compensation=verified "
        "private_publish=verified commit_uncertain=verified "
        "quarantine_fallback=verified root_fence=verified "
        "ambiguous_result=verified crash_residue=verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
