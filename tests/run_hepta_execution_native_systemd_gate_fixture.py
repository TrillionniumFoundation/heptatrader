#!/usr/bin/env python3

"""Offline contract tests for the native disposable-VM systemd runner."""

from pathlib import Path
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import aggregate_hepta_execution_native_systemd_gate as aggregate  # noqa: E402
import build_hepta_execution_native_vm_bundle as bundle  # noqa: E402
import check_hepta_agent_os_provisioned_host as agent_os  # noqa: E402
import run_hepta_execution_native_systemd_gate as native  # noqa: E402
import run_hepta_execution_rootful_systemd_gate as rootful  # noqa: E402
import verify_hepta_execution_native_vm_bundle as bundle_verify  # noqa: E402


NATIVE_RUNBOOK_HEADER = (
    "## 0.2 当前 native disposable-VM Agent OS WATCH runtime 门")
NATIVE_RUNBOOK_END = "\n## 本地 AI PAPER：DENY_ALL 下的被动部署"
SCHEMA_MAP_MARKER = (
    "本节 schema 角色的 machine-readable 精确映射如下；"
    "键、值和角色不得互换或扩展：")
SENTINEL_MARKER = (
    "`/etc/heptatrader/"
    "hepta-native-systemd-gate.disposable`，精确十二行：")
RUNTIME_PROBES_MARKER = (
    "上述 runtime read probes 的 machine-readable 精确顺序和参数如下；"
    "不得重排、遗漏或追加：")


def _native_runbook_section(runbook: str) -> str:
    if runbook.count(NATIVE_RUNBOOK_HEADER) != 1:
        raise AssertionError("native runbook section header must be unique")
    start = runbook.index(NATIVE_RUNBOOK_HEADER)
    try:
        end = runbook.index(NATIVE_RUNBOOK_END, start)
    except ValueError as error:
        raise AssertionError("native runbook section end is missing") from error
    return runbook[start:end]


def _fenced_payload(section: str, marker: str, language: str) -> str:
    if section.count(marker) != 1:
        raise AssertionError(f"runbook marker must be unique: {marker}")
    prefix = f"{marker}\n\n```{language}\n"
    if section.count(prefix) != 1:
        raise AssertionError(f"runbook fence must follow marker: {marker}")
    start = section.index(prefix) + len(prefix)
    try:
        end = section.index("\n```", start)
    except ValueError as error:
        raise AssertionError(f"runbook fence is not closed: {marker}") from error
    payload = section[start:end]
    if not payload or payload != payload.strip("\n"):
        raise AssertionError(f"runbook fence payload is malformed: {marker}")
    return payload


def _strict_json_document(payload: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise AssertionError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise AssertionError(f"non-standard JSON constant: {value}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise AssertionError("runbook JSON is invalid") from error


def _documented_sentinel_contract() -> str:
    return native.sentinel_content(
        "<32 lowercase hex>",
        "<UUID lowercase hex>",
        "<64 lowercase hex>",
        "<64 lowercase hex>",
        "<64 lowercase hex>",
        "<64 lowercase hex>",
        "<64 lowercase hex>",
        "<64 lowercase hex>",
        "<real|sandbox|stub>",
        "<32 lowercase hex>",
        "<64 lowercase hex>",
    ).decode("ascii").removesuffix("\n")


def _replace_fenced_payload(
        runbook: str, marker: str, language: str, replacement: str) -> str:
    section = _native_runbook_section(runbook)
    prefix = f"{marker}\n\n```{language}\n"
    original = _fenced_payload(section, marker, language)
    if replacement == original:
        raise AssertionError("runbook mutation did not change the payload")
    relative_start = section.index(prefix) + len(prefix)
    relative_end = relative_start + len(original)
    section_start = runbook.index(NATIVE_RUNBOOK_HEADER)
    absolute_start = section_start + relative_start
    absolute_end = section_start + relative_end
    return runbook[:absolute_start] + replacement + runbook[absolute_end:]


def validate_native_vm_runbook(runbook: str) -> None:
    section = _native_runbook_section(runbook)
    expected_schemas = {
        "bundle": bundle.SCHEMA,
        "verification": bundle_verify.SCHEMA,
        "provisioning": bundle.PROVISIONING_SCHEMA,
        "image": bundle.IMAGE_SCHEMA,
        "runtime_variant": native.SCHEMA,
        "runtime_aggregate": aggregate.SCHEMA,
    }
    documented_schemas = _strict_json_document(
        _fenced_payload(section, SCHEMA_MAP_MARKER, "json"))
    if documented_schemas != expected_schemas:
        raise AssertionError("runbook schema role mapping drifted")

    documented_sentinel = _fenced_payload(section, SENTINEL_MARKER, "text")
    if documented_sentinel != _documented_sentinel_contract():
        raise AssertionError("runbook sentinel template drifted")

    expected_probes = [
        {"tool": tool_name, "arguments": arguments}
        for tool_name, arguments in agent_os.RUNTIME_READ_PROBES
    ]
    documented_probes = _strict_json_document(
        _fenced_payload(section, RUNTIME_PROBES_MARKER, "json"))
    if documented_probes != expected_probes:
        raise AssertionError("runbook runtime probe sequence drifted")

    tool_count_claims = re.findall(
        r"发现精确 ([0-9]+) 个 WATCH tools", section)
    if tool_count_claims != [str(len(agent_os.WATCH_TOOL_NAMES))]:
        raise AssertionError("runbook WATCH tool count drifted")
    for stale in (
            "bundle/report v5",
            "精确十一行",
            "通过 v5 聚合",
            "发现精确 10 个 WATCH tools"):
        if stale in section:
            raise AssertionError(f"stale runbook contract phrase: {stale}")


def metadata() -> SimpleNamespace:
    return SimpleNamespace(
        st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o400, st_nlink=1,
        st_dev=7, st_ino=11)


def sentinel_bytes(variant: str = "real") -> bytes:
    return native.sentinel_content(
        "a" * 32, "11111111-2222-3333-4444-555555555555",
        "b" * 64, "c" * 64, "d" * 64, "1" * 64, "2" * 64,
        "3" * 64, variant, "e" * 32, "f" * 64)


def inner_result(
        variant: str = "real",
        scope: str = native.NATIVE_SCOPE) -> dict[str, object]:
    mode_evidence = {
        "real": {
            "simulator_socket_activation": True,
            "dual_socket_shared_identity": True,
            "service_epoch_changed_on_restart": True,
            "fencing_generation_stable_on_restart": True,
            "credential_generation_consumed": True,
            "manager_socket_inode_stable_until_socket_stop": True,
            "socket_inode_recreated_after_socket_restart": True,
            "peer_uid_rejection": True,
            "credential_mount_read_only": True,
            "credential_copy_matches_source": True,
            "private_network_loopback_only": True,
            "killmode_control_group_cleanup": True,
            "real_ibapi_elf_executed": False,
        },
        "sandbox": {
            "canonical_ib_units_with_test_probe": True,
            "two_credentials_mounted_read_only": True,
            "credential_copies_match_sources": True,
            "kill_switch_engaged_and_read_only": True,
            "loopback_allowed": True,
            "nonloopback_control_path_reachable": True,
            "nonloopback_denied_by_systemd_ip_policy": True,
            "killmode_control_group_removed_sigterm_ignoring_descendant": True,
            "paper_stop_closed_command_and_event_sockets": True,
            "paper_socket_activation_did_not_restart_service": True,
            "clean_paper_stop_preserved_broker_guard": True,
            "mutation_requests": 0,
            "real_ibapi_elf_executed": False,
        },
        "stub": {
            "canonical_ib_units_with_ibapi_disabled_stub": True,
            "adapter_failure_reason": "IB_PAPER_ADAPTER_CONNECT_FAILED",
            "mutation_plane_never_ready": True,
            "event_plane_never_ready": True,
            "order_journal_bytes": 0,
            "configured_endpoint_connections": 0,
            "paper_stop_closed_command_and_event_sockets": True,
            "paper_socket_activation_did_not_restart_service": True,
            "clean_paper_stop_preserved_broker_guard": True,
            "real_broker_connections": 0,
            "real_ibapi_elf_executed": False,
        },
    }[variant]
    executed_kind = {
        "real": "real_simulator_only_ibapi_not_staged",
        "sandbox": "no_ibapi_sandbox_probe",
        "stub": "ibapi_disabled_stub",
    }[variant]
    return {
        "schema": rootful.INNER_SCHEMA,
        "mode": variant,
        "passed": True,
        "checks": {
            "disposable_sentinel": True,
            "provisioned_host_preflight": True,
            "effective_units_static": True,
            "effective_units_no_dropins": True,
            "effective_units_no_generators": True,
            "journal_available": True,
            "nss_numeric_uid_isolation": True,
            "sensitive_host_bind_mounts_absent": True,
            "tmpfiles_default_engaged": True,
            "tmpfiles_idempotent": True,
            "source_credentials_root_0400": True,
            "source_credentials_service_unreadable": True,
            "kill_switch_stable_engaged": True,
            "binary_inputs_stable": True,
            "credential_content_recorded": False,
            "credential_hash_recorded": False,
            "raw_environment_recorded": False,
            "raw_journal_recorded": False,
            "mode_evidence": mode_evidence,
        },
        "platform": {
            "scope": scope,
            "platform_image_sha256": "b" * 64,
            "systemd_pid1": True,
            "pid1_cgroup_v2_root": True,
        },
        "metrics": {
            "simulator_sha256": "a" * 64,
            "client_probe_sha256": "c" * 64,
            "formal_ibapi_sha256": "d" * 64,
            "executed_ib_path_sha256":
                ("f" if variant == "sandbox" else "e") * 64,
            "executed_kind": executed_kind,
        },
        "boundary": {
            "real_ibapi_elf_executed": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "live_enabled": False,
            "real_ibapi_broker_unreachable":
                "not_run_requires_separate_authorization",
        },
    }


def agent_runtime_result() -> dict[str, object]:
    def phase(
            gateway_pid: int, simulator_pid: int,
            inode_base: int) -> dict[str, int]:
        return {
            "gateway_pid": gateway_pid,
            "simulator_pid": simulator_pid,
            "tool_socket_inode": inode_base,
            "supervisor_socket_inode": inode_base + 1,
            "execution_socket_inode": inode_base + 2,
            "events_socket_inode": inode_base + 3,
        }

    checks = {
        "systemd_pid1", "network_none_loopback_only",
        "no_host_mount_or_docker_socket", "fixed_identity_isolation",
        "ib_paper_surface_absent", "installation_preflight",
        "simulator_dual_socket_activation", "gateway_dual_socket_activation",
        "root_watch_bootstrap", "uid_2004_mcp_initialize",
        "uid_2004_exact_watch_tool_list", "uid_2004_read_only_probes",
        "gateway_service_socket_reactivation",
        "simulator_service_socket_reactivation",
        "socket_stop_removes_paths", "socket_restart_recreates_paths",
        "watch_restart_fails_closed", "runtime_preflight_after_restart",
        "watch_session_revoked",
        "all_runtime_paths_removed",
    }
    return {
        "schema": native.AGENT_OS_RUNTIME_RESULT_SCHEMA,
        "passed": True,
        "identities": {
            "agent_uid": 2004,
            "gateway_uid": 2001,
            "simulator_execution_uid": 2002,
            "ib_execution_uid_reserved_not_started": 2003,
        },
        "checks": {name: True for name in checks},
        "lifecycle": {
            "watch_generation": 7,
            "initial": phase(100, 200, 1000),
            "service_reactivation": phase(101, 201, 1000),
            "socket_reactivation": phase(102, 202, 2000),
        },
        "boundary": {
            "container_network": "none",
            "real_broker_connections": 0,
            "paper_orders": 0,
            "paper_authorized": False,
            "live_authorized": False,
            "ib_adapter_staged": False,
            "host_hepta_units_started": False,
            "host_bind_mounts": 0,
            "raw_session_token_recorded": False,
        },
    }


class NativeSystemdGateFixtureTests(unittest.TestCase):
    def _signed_instance_receipt(
            self, root: Path, *, revoked: bool = False,
            issued_at_ms: int = 1_000_000,
            expires_at_ms: int = 2_000_000) -> tuple[
                Path, Path, dict[str, object], dict[str, str], dict[str, str]]:
        private = root / "instance-private.pem"
        public = root / "instance-public.pem"
        subprocess.run([
            str(native.INSTANCE_OPENSSL), "genpkey", "-algorithm", "ED25519",
            "-out", str(private)], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        subprocess.run([
            str(native.INSTANCE_OPENSSL), "pkey", "-in", str(private),
            "-pubout", "-out", str(public)], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        public.chmod(0o444)
        der = subprocess.run([
            str(native.INSTANCE_OPENSSL), "pkey", "-pubin", "-in",
            str(public), "-outform", "DER"], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        key_digest = hashlib.sha256(der).hexdigest()
        statement: dict[str, object] = {
            "schema": native.INSTANCE_STATEMENT_SCHEMA,
            "challenge": "f" * 64,
            "instance_uuid": "00000000-0000-4000-8000-000000000001",
            "instance_state": "running",
            "provisioner_id": "fixture-provisioner",
            "hypervisor_id": "fixture-hypervisor",
            "variant": "real", "vm_type": "kvm",
            "boot_id": "11111111-2222-3333-8444-555555555555",
            "run_id": "e" * 32,
            "vm_image_manifest_sha256": "b" * 64,
            "provisioning_manifest_sha256": "c" * 64,
            "source_lineage": {
                "bundle_sha256": "1" * 64,
                "manifest_sha256": "2" * 64,
                "files_sha256": "3" * 64,
            },
            "issued_at_ms": issued_at_ms, "expires_at_ms": expires_at_ms,
        }
        statement_path = root / "statement.bin"
        statement_path.write_bytes(native._instance_signature_payload(statement))
        signature_path = root / "statement.sig"
        subprocess.run([
            str(native.INSTANCE_OPENSSL), "pkeyutl", "-sign", "-inkey",
            str(private), "-rawin", "-in", str(statement_path), "-out",
            str(signature_path)], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        body = {
            "schema": native.INSTANCE_RECEIPT_SCHEMA, "version": 1,
            "statement": statement,
            "signature": {
                "algorithm": "ed25519", "key_id": "sha256/" + key_digest,
                "value_hex": signature_path.read_bytes().hex(),
            },
        }
        receipt_document = {
            **body,
            "body_sha256": hashlib.sha256(
                native.canonical_json(body)).hexdigest(),
        }
        receipt = root / "execution-native-systemd-real-instance-receipt.json"
        receipt.write_bytes(native.canonical_json(receipt_document))
        receipt.chmod(0o400)
        trust_document = {
            "schema": native.INSTANCE_TRUST_SCHEMA, "version": 1,
            "production_status": "configured-external",
            "signature_domain": native.INSTANCE_SIGNATURE_DOMAIN,
            "maximum_receipt_lifetime_ms": 1_500_000,
            "maximum_clock_skew_ms": 1_000,
            "keys": [{
                "key_id": "sha256/" + key_digest,
                "algorithm": "ed25519", "public_key_path": public.name,
                "public_key_spki_sha256": key_digest,
                "valid_from_ms": 500_000, "valid_until_ms": 3_000_000,
                "revoked": revoked,
                "allowed_provisioner_ids": ["fixture-provisioner"],
                "allowed_hypervisor_ids": ["fixture-hypervisor"],
            }],
        }
        trust = root / "instance-trust.json"
        trust.write_bytes(native.canonical_json(trust_document))
        trust.chmod(0o400)
        sentinel = {
            "instance_challenge": "f" * 64, "variant": "real",
            "run_id": "e" * 32, "vm_image_manifest_sha256": "b" * 64,
            "provisioning_manifest_sha256": "c" * 64,
            "clean_source_bundle_sha256": "1" * 64,
            "clean_source_manifest_sha256": "2" * 64,
            "clean_source_files_sha256": "3" * 64,
        }
        host = {
            "vm_type": "kvm",
            "boot_id": "11111111-2222-3333-8444-555555555555",
        }
        return receipt, trust, statement, sentinel, host

    def test_external_instance_receipt_real_ed25519_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(
                dir=REPOSITORY,
                prefix="hepta-native-instance-receipt-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            receipt, trust, _statement, sentinel, host = \
                self._signed_instance_receipt(root)
            owners = frozenset({(0, 0), (os.geteuid(), os.getegid())})
            with mock.patch.object(native, "INSTANCE_TRUST_POLICY", trust):
                verification = native.verify_instance_receipt(
                    receipt, evaluated_at_ms=1_500_000,
                    trusted_owner_pairs=owners)
            self.assertTrue(verification["verified"])
            native._validate_instance_receipt_binding(
                verification, sentinel, host, "real")

    def test_external_instance_receipt_wrong_binding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                dir=REPOSITORY,
                prefix="hepta-native-instance-binding-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            receipt, trust, _statement, sentinel, host = \
                self._signed_instance_receipt(root)
            owners = frozenset({(0, 0), (os.geteuid(), os.getegid())})
            with mock.patch.object(native, "INSTANCE_TRUST_POLICY", trust):
                verification = native.verify_instance_receipt(
                    receipt, evaluated_at_ms=1_500_000,
                    trusted_owner_pairs=owners)
            for field, hostile in (
                    ("vm_image_manifest_sha256", "9" * 64),
                    ("run_id", "9" * 32),
                    ("clean_source_manifest_sha256", "9" * 64)):
                modified = dict(sentinel)
                modified[field] = hostile
                with self.subTest(field=field), self.assertRaisesRegex(
                        native.NativeGateError, "exact gate run"):
                    native._validate_instance_receipt_binding(
                        verification, modified, host, "real")

    def test_external_instance_receipt_expired_or_revoked_fails_closed(self) -> None:
        for revoked, evaluated in ((False, 2_000_000), (True, 1_500_000)):
            with self.subTest(revoked=revoked), tempfile.TemporaryDirectory(
                    dir=REPOSITORY,
                    prefix="hepta-native-instance-expiry-") as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                receipt, trust, *_ = self._signed_instance_receipt(
                    root, revoked=revoked)
                owners = frozenset({(0, 0), (os.geteuid(), os.getegid())})
                with mock.patch.object(native, "INSTANCE_TRUST_POLICY", trust), \
                        self.assertRaises(native.NativeGateError):
                    native.verify_instance_receipt(
                        receipt, evaluated_at_ms=evaluated,
                        trusted_owner_pairs=owners)

    def test_external_instance_receipt_tamper_or_wrong_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                dir=REPOSITORY,
                prefix="hepta-native-instance-tamper-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            receipt, trust, *_ = self._signed_instance_receipt(root)
            document = json.loads(receipt.read_text(encoding="utf-8"))
            document["statement"]["run_id"] = "9" * 32
            body = {key: document[key] for key in (
                "schema", "version", "statement", "signature")}
            document["body_sha256"] = hashlib.sha256(
                native.canonical_json(body)).hexdigest()
            receipt.chmod(0o600)
            receipt.write_bytes(native.canonical_json(document))
            receipt.chmod(0o400)
            owners = frozenset({(0, 0), (os.geteuid(), os.getegid())})
            with mock.patch.object(native, "INSTANCE_TRUST_POLICY", trust), \
                    self.assertRaisesRegex(
                        native.NativeGateError, "signature is invalid"):
                native.verify_instance_receipt(
                    receipt, evaluated_at_ms=1_500_000,
                    trusted_owner_pairs=owners)

    def test_stable_root_file_reports_canonical_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-stable-file-fixture-") as temporary:
            source = Path(temporary) / "input.bin"
            source.write_bytes(b"fixture")
            record = SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_nlink=1,
                st_uid=0,
                st_gid=0,
                st_size=7,
                st_dev=7,
                st_ino=11,
            )
            relative = Path(os.path.relpath(source, Path.cwd()))
            with mock.patch.object(
                    native.shared,
                    "read_regular_file",
                    return_value=(record, b"fixture", "a" * 64)):
                result = native.stable_root_file(relative, 0o600)
            self.assertEqual(result["path"], str(source.resolve(strict=True)))
            self.assertTrue(Path(result["path"]).is_absolute())

    def test_native_sentinel_exact_contract_passes(self) -> None:
        result = native.sentinel_record(
            metadata(), sentinel_bytes(), expected_machine_id="a" * 32,
            expected_boot_id="11111111-2222-3333-4444-555555555555",
            expected_variant="real")
        self.assertEqual(result["vm_image_manifest_sha256"], "b" * 64)
        self.assertEqual(result["run_id"], "e" * 32)
        self.assertTrue(result["root_owned"])

    def test_runbook_matches_current_native_vm_contract(self) -> None:
        runbook = (REPOSITORY / "docs/RUNBOOK-STARTUP.md").read_text(
            encoding="utf-8", errors="strict")
        validate_native_vm_runbook(runbook)

    def test_runbook_contract_mutations_fail_closed(self) -> None:
        runbook = (REPOSITORY / "docs/RUNBOOK-STARTUP.md").read_text(
            encoding="utf-8", errors="strict")
        section = _native_runbook_section(runbook)
        schema_payload = _fenced_payload(section, SCHEMA_MAP_MARKER, "json")
        schemas = _strict_json_document(schema_payload)
        self.assertIsInstance(schemas, dict)
        swapped_schemas = dict(schemas)
        swapped_schemas["bundle"], swapped_schemas["verification"] = (
            swapped_schemas["verification"], swapped_schemas["bundle"])
        schema_lines = schema_payload.splitlines()
        duplicate_schema_key = "\n".join(
            schema_lines[:2] + [schema_lines[1]] + schema_lines[2:])

        sentinel_payload = _fenced_payload(section, SENTINEL_MARKER, "text")
        shortened_sentinel = sentinel_payload.replace(
            "machine_id=<32 lowercase hex>",
            "machine_id=<31 lowercase hex>",
            1,
        )

        probes_payload = _fenced_payload(
            section, RUNTIME_PROBES_MARKER, "json")
        probes = _strict_json_document(probes_payload)
        self.assertIsInstance(probes, list)
        reordered_probes = list(probes)
        reordered_probes[0], reordered_probes[1] = (
            reordered_probes[1], reordered_probes[0])
        extended_probes = list(probes) + [
            {"tool": "system.tools.list", "arguments": {}}]
        tool_count_claim = (
            f"发现精确 {len(agent_os.WATCH_TOOL_NAMES)} 个 WATCH tools")
        self.assertEqual(runbook.count(tool_count_claim), 1)
        contradictory_tool_count = runbook.replace(
            tool_count_claim,
            tool_count_claim + "；另一处错误声称发现精确 12 个 WATCH tools",
            1,
        )

        mutations = {
            "schema_role_swap": _replace_fenced_payload(
                runbook, SCHEMA_MAP_MARKER, "json",
                json.dumps(swapped_schemas, indent=2, ensure_ascii=False),
            ),
            "duplicate_schema_key": _replace_fenced_payload(
                runbook, SCHEMA_MAP_MARKER, "json", duplicate_schema_key),
            "sentinel_width": _replace_fenced_payload(
                runbook, SENTINEL_MARKER, "text", shortened_sentinel),
            "probe_reorder": _replace_fenced_payload(
                runbook, RUNTIME_PROBES_MARKER, "json",
                json.dumps(reordered_probes, indent=2, ensure_ascii=False),
            ),
            "extra_probe": _replace_fenced_payload(
                runbook, RUNTIME_PROBES_MARKER, "json",
                json.dumps(extended_probes, indent=2, ensure_ascii=False),
            ),
            "contradictory_tool_count": contradictory_tool_count,
        }
        for name, mutated_runbook in mutations.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                validate_native_vm_runbook(mutated_runbook)

    def test_native_sentinel_identity_and_metadata_drift_fail_closed(self) -> None:
        cases = [
            (metadata(), sentinel_bytes(), "f" * 32, "real"),
            (metadata(), sentinel_bytes(), "a" * 32, "sandbox"),
            (metadata(), sentinel_bytes().replace(b"b" * 64, b"B" * 64),
             "a" * 32, "real"),
            (metadata(), sentinel_bytes() + b"unexpected=1\n", "a" * 32,
             "real"),
        ]
        linked = metadata()
        linked.st_nlink = 2
        cases.append((linked, sentinel_bytes(), "a" * 32, "real"))
        for record, content, machine_id, variant in cases:
            with self.subTest(machine_id=machine_id, variant=variant):
                with self.assertRaises(native.NativeGateError):
                    native.sentinel_record(
                        record, content, expected_machine_id=machine_id,
                        expected_boot_id=
                            "11111111-2222-3333-4444-555555555555",
                        expected_variant=variant)

    def test_loopback_only_network_passes(self) -> None:
        addresses = [
            {"ifname": "lo", "operstate": "UNKNOWN", "addr_info": [
                {"local": "127.0.0.1"}, {"local": "::1"}]},
            {"ifname": "ens3", "operstate": "DOWN", "addr_info": []},
        ]
        routes = [{"dst": "127.0.0.0/8", "dev": "lo"}]
        self.assertEqual(native.parse_network_isolation(addresses, routes), {
            "loopback_present": True,
            "non_loopback_addresses": 0,
            "non_loopback_links_up": 0,
            "default_routes": 0,
            "non_loopback_routes": 0,
        })

    def test_network_escape_shapes_fail_closed(self) -> None:
        base = [
            {"ifname": "lo", "operstate": "UNKNOWN",
             "addr_info": [{"local": "127.0.0.1"}]},
            {"ifname": "ens3", "operstate": "DOWN", "addr_info": []},
        ]
        cases = [
            (base, [{"dst": "default"}]),
            ([base[0], {"ifname": "ens3", "operstate": "UP",
                        "addr_info": []}], []),
            ([base[0], {"ifname": "ens3", "operstate": "UNKNOWN",
                        "addr_info": []}], []),
            ([base[0], {"ifname": "ens3", "operstate": "DOWN",
                        "addr_info": [{"local": "192.0.2.2"}]}], []),
            ([{"ifname": "lo", "operstate": "UNKNOWN",
               "addr_info": [{"local": "192.0.2.1"}]}], []),
            ([base[0], base[1]], [{"dst": "192.0.2.0/24", "dev": "ens3"}]),
            ([], []),
        ]
        for addresses, routes in cases:
            with self.subTest(addresses=addresses, routes=routes):
                with self.assertRaises(native.NativeGateError):
                    native.parse_network_isolation(addresses, routes)

    def test_native_inner_scope_is_exact(self) -> None:
        marker = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT="
        payload = json.dumps(inner_result(), separators=(",", ":"))
        parsed = rootful.parse_inner_result(
            marker + payload + "\n", "real",
            expected_scope=native.NATIVE_SCOPE)
        self.assertEqual(parsed["platform"]["scope"], native.NATIVE_SCOPE)
        wrong = json.dumps(
            inner_result(scope=rootful.CONTAINER_SCOPE),
            separators=(",", ":"))
        with self.assertRaisesRegex(rootful.GateError, "platform contract"):
            rootful.parse_inner_result(
                marker + wrong + "\n", "real",
                expected_scope=native.NATIVE_SCOPE)

    def test_agent_os_runtime_result_is_strictly_parsed(self) -> None:
        payload = json.dumps(agent_runtime_result(), separators=(",", ":"))
        parsed = native.parse_agent_os_runtime_result(
            "HEPTA_AGENT_OS_ROOTFUL_E2E_RESULT=" + payload + "\n")
        self.assertEqual(parsed["identities"]["agent_uid"], 2004)
        self.assertTrue(parsed["checks"]["uid_2004_exact_watch_tool_list"])
        self.assertEqual(parsed["lifecycle"]["watch_generation"], 7)

    def test_agent_os_runtime_forgery_fails_closed(self) -> None:
        cases = []
        identity = agent_runtime_result()
        identity["identities"]["agent_uid"] = 0  # type: ignore[index]
        cases.append(identity)
        float_identity = agent_runtime_result()
        float_identity["identities"][  # type: ignore[index]
            "agent_uid"] = 2004.0
        cases.append(float_identity)
        tool_surface = agent_runtime_result()
        tool_surface["checks"][  # type: ignore[index]
            "uid_2004_exact_watch_tool_list"] = False
        cases.append(tool_surface)
        lifecycle = agent_runtime_result()
        lifecycle["lifecycle"]["socket_reactivation"][  # type: ignore[index]
            "tool_socket_inode"] = 1000
        cases.append(lifecycle)
        float_zero = agent_runtime_result()
        float_zero["boundary"]["paper_orders"] = 0.0  # type: ignore[index]
        cases.append(float_zero)
        boolean_zero = agent_runtime_result()
        boolean_zero["boundary"][  # type: ignore[index]
            "host_bind_mounts"] = False
        cases.append(boolean_zero)
        for forged in cases:
            with self.subTest(forged=forged):
                payload = json.dumps(forged, separators=(",", ":"))
                with self.assertRaises(native.NativeGateError):
                    native.parse_agent_os_runtime_result(
                        "HEPTA_AGENT_OS_ROOTFUL_E2E_RESULT=" +
                        payload + "\n")

    def test_runtime_input_digest_binds_identity_metadata(self) -> None:
        records = [{
            "path": str(native.AGENT_OS_RUNTIME_INNER),
            "sha256": "a" * 64,
            "size": 10,
            "device": 7,
            "inode": 11,
            "mode": "0755",
        }]
        exact = native.input_manifest_sha256(records)
        drift = [dict(records[0], inode=12)]
        self.assertNotEqual(exact, native.input_manifest_sha256(drift))
        self.assertEqual(
            native.input_content_manifest_sha256(records),
            native.input_content_manifest_sha256(drift))

    def test_failure_boundary_is_exact_only_before_inner_start(self) -> None:
        before = native.failure_report(
            native.NativeGateError("blocked"),
            native.NativeGateProgress(phase="disposable_sentinel_validation"),
            "real")
        self.assertEqual(before["boundary"]["real_broker_connections"], 0)
        self.assertEqual(before["boundary"]["paper_orders"], 0)
        after = native.failure_report(
            native.NativeGateError("blocked"),
            native.NativeGateProgress(
                phase="inner_systemd_gate", inner_gate_started=True),
            "real")
        self.assertEqual(
            after["boundary"]["real_broker_connections"], "unknown")
        self.assertEqual(after["boundary"]["paper_orders"], "unknown")
        self.assertFalse(after["boundary"]["paper_authorized"])
        self.assertFalse(
            after["boundary"]["agent_os_runtime_preflight_executed"])
        self.assertTrue(
            after["boundary"]["agent_os_runtime_preflight_required"])

    def test_agent_os_static_contract_does_not_fabricate_runtime_state(
            self) -> None:
        self.assertEqual(
            native.AGENT_OS_STATIC_MODES[
                "/etc/heptatrader/hepta-supervisor-lease.key"], 0o400)
        expected_shadow_closure = {
            "/usr/libexec/hepta-shadow-watch-exporter": 0o755,
            "/usr/lib/systemd/system/"
            "hepta-shadow-watch-export@.service": 0o644,
            "/usr/lib/systemd/system/hepta-tool-gateway@.service": 0o644,
            "/usr/lib/systemd/system/hepta-tool-gateway@.socket": 0o644,
            "/usr/lib/systemd/system/"
            "hepta-tool-session-supervisor@.socket": 0o644,
            "/usr/lib/systemd/system/hepta-broker-egress-policy.service":
                0o644,
            "/usr/lib/systemd/system/hepta-p1-watch-activation.service":
                0o644,
            "/usr/lib/systemd/system/"
            "hepta-p1-watch-activation-reconcile.service": 0o644,
            "/usr/lib/systemd/system/"
            "hepta-p1-watch-activation-reconcile.timer": 0o644,
            "/usr/share/doc/heptatrader/examples/"
            "hepta-shadow-watch-domain.env.example": 0o644,
            "/usr/share/doc/heptatrader/examples/"
            "hepta-tool-gateway-domain.env.example": 0o644,
        }
        for path, mode in expected_shadow_closure.items():
            with self.subTest(path=path):
                self.assertEqual(native.AGENT_OS_STATIC_MODES.get(path), mode)
        self.assertNotIn(
            "/run/hepta-agent/session.token", native.AGENT_OS_STATIC_MODES)
        self.assertNotIn(
            "/run/hepta-agent/tools.sock", native.AGENT_OS_STATIC_MODES)
        for helper in (
                "hepta-p1-shadow-host-controller",
                "hepta-p1-load-probe-validator",
                "build-hepta-p1-observation-policy",
                "hepta-p1-shadow-observer-controller",
                "hepta-p1-shadow-admission-launcher",
                "hepta-shadow-host-installer",
                "hepta-p1-watch-profile-deployer",
                "hepta-p1-watch-activation-transaction",
                "hepta-bounded-shadow-closure-verifier",
                "hepta-official-source-capture",
                "hepta_bounded_shadow_observer.py",
                "hepta_market_context_builder.py",
                "hepta_market_evidence_normalizer.py",
                "hepta_market_official_source_extractor.py",
                "hepta_eurusd_confirmed_momentum_strategy.py",
                "hepta_shadow_market_history.py",
                "hepta_strategy_shadow_runner.py",
                "validate_hepta_strategy_decision_receipt.py"):
            path = "/usr/libexec/" + helper
            self.assertEqual(native.AGENT_OS_STATIC_MODES[path], 0o755)
            self.assertEqual(native.AGENT_OS_RUNTIME_GATE_MODES[path], 0o755)
        self.assertEqual(
            native.AGENT_OS_STATIC_MODES[
                "/usr/libexec/hepta-broker-egress-policy"], 0o755)
        self.assertEqual(
            native.AGENT_OS_RUNTIME_GATE_MODES[
                "/usr/libexec/hepta-broker-egress-policy"], 0o755)
        self.assertEqual(
            native.AGENT_OS_STATIC_MODES[
                "/usr/share/heptatrader/"
                "hepta-broker-network-policy-v1.json"], 0o644)
        self.assertEqual(
            native.AGENT_OS_RUNTIME_GATE_MODES[
                "/usr/share/heptatrader/"
                "hepta-broker-network-policy-v1.json"], 0o644)
        self.assertEqual(
            native.AGENT_OS_STATIC_MODES[
                "/etc/heptatrader/"
                "hepta-agent-trust-domain-paper-identities-v1.json"],
            0o600)
        provisioned_deny_all = str(
            native.AGENT_OS_RUNTIME_PROVISIONING /
            "hepta-agent-trust-domain-paper-identities-v1.json")
        self.assertEqual(
            native.AGENT_OS_STATIC_MODES[provisioned_deny_all], 0o600)
        self.assertEqual(
            native.AGENT_OS_RUNTIME_GATE_MODES[provisioned_deny_all], 0o600)
        for path in (
                "/usr/libexec/hepta_strategy_contracts.py",
                "/usr/share/heptatrader/strategies/"
                "eurusd-confirmed-momentum-shadow-v2.json"):
            self.assertEqual(native.AGENT_OS_STATIC_MODES[path], 0o644)
            self.assertEqual(native.AGENT_OS_RUNTIME_GATE_MODES[path], 0o644)
        self.assertEqual(
            native.UNPROVISIONED_SUPERVISOR_LEASE,
            b"HEPTA_AGENT_OS_UNPROVISIONED_SUPERVISOR_LEASE_V1\n")

    def test_image_manifest_exact_file_closure_passes(self) -> None:
        required = [
            "usr/libexec/hepta-executiond",
            "usr/libexec/hepta-ib-executiond",
            "usr/local/libexec/check_hepta_execution_provisioned_host.py",
            "usr/local/libexec/run_hepta_execution_rootful_systemd_gate.py",
            "usr/local/libexec/run_hepta_execution_native_systemd_gate.py",
            "usr/local/libexec/hepta_execution_rootful_inner_gate.py",
            "usr/local/libexec/hepta_execution_systemd_client_probe",
            "usr/local/libexec/hepta_execution_systemd_sandbox_probe",
            "usr/local/libexec/hepta-ib-executiond-disabled",
            "usr/local/share/hepta-rootful-systemd-gate/formal-ibapi.sha256",
            "usr/local/share/hepta-rootful-systemd-gate/platform-policy.json",
            "usr/local/share/hepta-rootful-systemd-gate/"
            "provisioning-manifest.json",
            "usr/local/share/hepta-rootful-systemd-gate/variant",
            "usr/local/share/hepta-rootful-systemd-gate/clean-source-provenance.json",
            "usr/local/share/hepta-rootful-systemd-gate/"
            "agent-os-installation-manifest.json",
        ] + [
            path.removeprefix("/")
            for path in set(native.AGENT_OS_STATIC_MODES) |
            set(native.AGENT_OS_RUNTIME_GATE_MODES)
        ]
        required = list(dict.fromkeys(required))
        records = [{
            "path": path,
            "mode": format(
                native.AGENT_OS_STATIC_MODES.get(
                    "/" + path,
                    0o755 if "/libexec/" in path else 0o444),
                "04o"),
            "uid": 0, "gid": 0, "size": 1,
            "sha256": hashlib.sha256(path.encode("ascii")).hexdigest(),
        } for path in required]
        manifest = {
            "schema": "hepta.execution-native-vm-image-manifest.v4",
            "variant": "real",
            "platform_policy_sha256": "d" * 64,
            "clean_source_provenance_sha256": "4" * 64,
            "clean_source": {
                "version": "fixture", "git_head": "head", "file_count": 1,
                "files_sha256": "3" * 64, "bundle_sha256": "1" * 64,
                "manifest_sha256": "2" * 64, "paper_authorized": False,
                "live_authorized": False},
            "provisioning_manifest_sha256": "c" * 64,
            "agent_os_installation_manifest_sha256": "5" * 64,
            "agent_os_runtime_input_manifest_sha256": next(
                record["sha256"] for record in records
                if record["path"] ==
                native.AGENT_OS_RUNTIME_INPUT_MANIFEST_PATH.as_posix().
                removeprefix("/")),
            "agent_os_installation_preflight_staged": True,
            "agent_os_runtime_gate_inputs_staged": True,
            "agent_os_runtime_preflight_required": True,
            "agent_os_runtime_artifacts_staged": False,
            "files": records,
            "formal_ibapi_elf_staged": False,
            "instance_identity_staged": False,
            "paper_authorized": False,
            "live_enabled": False,
        }
        encoded = (json.dumps(
            manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-manifest-fixture-") as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_bytes(encoded)
            sentinel = {
                "variant": "real",
                "vm_image_manifest_sha256": hashlib.sha256(encoded).hexdigest(),
                "provisioning_manifest_sha256": "c" * 64,
                "platform_policy_sha256": "d" * 64,
            }
            file_records = {
                "/" + record["path"]: {
                    "size": record["size"], "sha256": record["sha256"]}
                for record in records
            }
            with mock.patch.object(native, "IMAGE_MANIFEST_PATH", path), \
                    mock.patch.object(
                        native, "stable_root_file",
                        side_effect=lambda value, *_args, **_kwargs:
                        file_records[str(value)]):
                native.validate_image_manifest(
                    sentinel,
                    {"sha256": sentinel["vm_image_manifest_sha256"]},
                    {"sha256": "c" * 64}, {"sha256": "d" * 64},
                    {"sha256": "4" * 64}, {"sha256": "5" * 64},
                    manifest["clean_source"])

    def test_image_manifest_forbidden_path_fails_closed(self) -> None:
        manifest = {
            "schema": "hepta.execution-native-vm-image-manifest.v4",
            "variant": "real",
            "platform_policy_sha256": "d" * 64,
            "clean_source_provenance_sha256": "4" * 64,
            "clean_source": {
                "version": "fixture", "git_head": "head", "file_count": 1,
                "files_sha256": "3" * 64, "bundle_sha256": "1" * 64,
                "manifest_sha256": "2" * 64, "paper_authorized": False,
                "live_authorized": False},
            "provisioning_manifest_sha256": "c" * 64,
            "agent_os_installation_manifest_sha256": "5" * 64,
            "agent_os_runtime_input_manifest_sha256": "6" * 64,
            "agent_os_installation_preflight_staged": True,
            "agent_os_runtime_gate_inputs_staged": True,
            "agent_os_runtime_preflight_required": True,
            "agent_os_runtime_artifacts_staged": False,
            "files": [{
                "path": "usr/libexec/hepta-ib-executiond-formal",
                "mode": "0755", "uid": 0, "gid": 0, "size": 1,
                "sha256": "a" * 64}],
            "formal_ibapi_elf_staged": False,
            "instance_identity_staged": False,
            "paper_authorized": False,
            "live_enabled": False,
        }
        encoded = (json.dumps(
            manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-manifest-fixture-") as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_bytes(encoded)
            sentinel = {
                "variant": "real",
                "vm_image_manifest_sha256": hashlib.sha256(encoded).hexdigest(),
                "provisioning_manifest_sha256": "c" * 64,
                "platform_policy_sha256": "d" * 64,
            }
            with mock.patch.object(native, "IMAGE_MANIFEST_PATH", path), \
                    mock.patch.object(
                        native, "stable_root_file",
                        return_value={"sha256": "6" * 64}):
                with self.assertRaisesRegex(native.NativeGateError, "value"):
                    native.validate_image_manifest(
                        sentinel,
                        {"sha256": sentinel["vm_image_manifest_sha256"]},
                        {"sha256": "c" * 64}, {"sha256": "d" * 64},
                        {"sha256": "4" * 64}, {"sha256": "5" * 64},
                        manifest["clean_source"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
