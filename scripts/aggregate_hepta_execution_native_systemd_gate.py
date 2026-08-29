#!/usr/bin/env python3

"""Aggregate three independently produced native systemd gate variants."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Optional


REPOSITORY = Path(__file__).resolve(strict=True).parents[1]
sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))
import run_hepta_execution_native_systemd_gate as native  # noqa: E402
import run_hepta_execution_rootful_systemd_gate as shared  # noqa: E402


RUNTIME_AGGREGATE_SCHEMA = "hepta.execution-native-systemd-aggregate.v6"
SCHEMA = RUNTIME_AGGREGATE_SCHEMA
RUNTIME_CERTIFICATION_LEVEL = (
    "native-disposable-vm-agent-os-watch-runtime-rootful-systemd")
VARIANT_CERTIFICATION_LEVEL = (
    "native-disposable-vm-agent-os-watch-runtime-systemd-variant")
VARIANTS = ("real", "sandbox", "stub")
HEX_64 = re.compile(r"[0-9a-f]{64}")
MAX_VARIANT_REPORT_BYTES = shared.MAX_REPORT_BYTES
VARIANT_REPORT_INPUT_FIELDS = {
    "variant", "path", "sha256", "size", "mode"}
EXPECTED_EXECUTED_KINDS = {
    "real": "real_simulator_only_ibapi_not_staged",
    "sandbox": "no_ibapi_sandbox_probe",
    "stub": "ibapi_disabled_stub",
}
TRUSTED_REPORT_OWNER_PAIRS = frozenset({(0, 0)})


class AggregateError(RuntimeError):
    """A fail-closed native evidence aggregation error."""


def fail(message: str) -> None:
    raise AggregateError(message)


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or HEX_64.fullmatch(value) is None:
        fail(f"native variant {field} is not a SHA-256 digest")
    return value


def _unique_json_object(
        pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key in native report: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    fail(f"non-finite JSON value in native report: {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except AggregateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        fail(f"{label} encoding is invalid")


def parse_variant_report_inputs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(VARIANTS):
        fail("native runtime aggregate requires three raw variant reports")
    parsed: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for expected_variant, record in zip(VARIANTS, value, strict=True):
        if (not isinstance(record, dict) or
                set(record) != VARIANT_REPORT_INPUT_FIELDS or
                record.get("variant") != expected_variant or
                not isinstance(record.get("path"), str) or
                not record["path"] or "\0" in record["path"]):
            fail("native raw variant report binding fields mismatch")
        path = record["path"]
        candidate = PurePosixPath(path)
        expected_name = (
            f"execution-native-systemd-{expected_variant}.json")
        if (not candidate.is_absolute() or
                candidate.name != expected_name or
                candidate.as_posix() != path or
                os.path.abspath(path) != path or
                any(part in {"", ".", ".."} for part in candidate.parts) or
                path in seen_paths):
            fail("native raw variant report path is not canonical")
        require_sha256(
            record.get("sha256"),
            f"native {expected_variant} raw report sha256")
        if (type(record.get("size")) is not int or
                record["size"] <= 0 or
                record["size"] > MAX_VARIANT_REPORT_BYTES or
                record.get("mode") != "0600"):
            fail("native raw variant report metadata mismatch")
        seen_paths.add(path)
        parsed.append(record)
    return parsed


def parse_input_records(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        fail(f"{label} input manifest is missing")
    paths: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in value:
        if (not isinstance(record, dict) or set(record) != {
                "path", "sha256", "size", "device", "inode", "mode"} or
                not isinstance(record.get("path"), str) or
                not record["path"].startswith("/") or
                record["path"] in paths or
                not isinstance(record.get("mode"), str) or
                re.fullmatch(r"0[0-7]{3}", record["mode"]) is None or
                type(record.get("size")) is not int or record["size"] < 0 or
                type(record.get("device")) is not int or
                record["device"] < 0 or
                type(record.get("inode")) is not int or
                record["inode"] <= 0):
            fail(f"{label} input record contract mismatch")
        require_sha256(record.get("sha256"), f"{label} input sha256")
        paths.add(record["path"])
        result.append(record)
    return result


def parse_variant_report(report: Any, expected_variant: str) -> dict[str, Any]:
    expected_top = {
        "schema", "passed", "certification_level", "variant", "host",
        "instance_identity", "disposable_sentinel", "network_isolation", "agent_os",
        "agent_os_runtime", "runtime_inputs", "runtime_input_stability",
        "inputs", "input_stability", "inner", "boundary",
    }
    if (not isinstance(report, dict) or set(report) != expected_top or
            report.get("schema") != native.SCHEMA or
            report.get("passed") is not True or
            report.get("certification_level") !=
            VARIANT_CERTIFICATION_LEVEL or
            report.get("variant") != expected_variant or
            report.get("input_stability") is not True or
            report.get("runtime_input_stability") is not True):
        fail(f"native {expected_variant} report top-level contract mismatch")

    host = report["host"]
    if (not isinstance(host, dict) or set(host) != {
            "vm_type", "systemd_pid1", "cgroup_v2_root",
            "docker_socket_absent", "kernel_release"} or
            host.get("vm_type") not in native.VM_TYPES or
            any(host.get(key) is not True for key in (
                "systemd_pid1", "cgroup_v2_root", "docker_socket_absent")) or
            not isinstance(host.get("kernel_release"), str) or
            not host["kernel_release"]):
        fail(f"native {expected_variant} host contract mismatch")

    instance_identity = report["instance_identity"]
    expected_instance_identity = {
        "schema", "verified", "verified_at_ms", "statement", "receipt",
        "trust_policy", "verification_key", "signature_verifier", "key_id",
    }
    reference_fields = {
        "path", "file_sha256", "body_sha256", "size", "mode", "device",
        "inode",
    }
    if (not isinstance(instance_identity, dict) or
            set(instance_identity) != expected_instance_identity or
            instance_identity.get("schema") !=
                native.INSTANCE_VERIFICATION_SCHEMA or
            instance_identity.get("verified") is not True or
            type(instance_identity.get("verified_at_ms")) is not int or
            instance_identity["verified_at_ms"] <= 0 or
            native.INSTANCE_KEY_ID.fullmatch(
                str(instance_identity.get("key_id", ""))) is None):
        fail(f"native {expected_variant} instance identity contract mismatch")
    for label in (
            "receipt", "trust_policy", "verification_key",
            "signature_verifier"):
        reference = instance_identity.get(label)
        if (not isinstance(reference, dict) or
                set(reference) != reference_fields or
                not isinstance(reference.get("path"), str) or
                not PurePosixPath(reference["path"]).is_absolute() or
                type(reference.get("size")) is not int or
                reference["size"] <= 0 or
                not isinstance(reference.get("mode"), str) or
                re.fullmatch(r"0[0-7]{3}", reference["mode"]) is None or
                type(reference.get("device")) is not int or
                reference["device"] < 0 or
                type(reference.get("inode")) is not int or
                reference["inode"] <= 0):
            fail(f"native {expected_variant} instance {label} binding mismatch")
        require_sha256(reference.get("file_sha256"), f"instance {label} file")
        require_sha256(reference.get("body_sha256"), f"instance {label} body")
    if (Path(instance_identity["trust_policy"]["path"]) !=
            native.INSTANCE_TRUST_POLICY or
            Path(instance_identity["signature_verifier"]["path"]) !=
            native.INSTANCE_OPENSSL or
            Path(instance_identity["receipt"]["path"]).name !=
            f"execution-native-systemd-{expected_variant}-instance-receipt.json"):
        fail(f"native {expected_variant} instance fixed path mismatch")

    sentinel = report["disposable_sentinel"]
    expected_sentinel = {
        "contract", "root_owned", "mode", "single_link",
        "machine_id_bound", "boot_id_bound", "machine_id_sha256",
        "boot_id_sha256", "vm_image_manifest_sha256",
        "provisioning_manifest_sha256", "platform_policy_sha256",
        "clean_source_bundle_sha256", "clean_source_manifest_sha256",
        "clean_source_files_sha256",
        "run_id_bound", "run_id_sha256", "instance_challenge_bound",
        "instance_challenge_sha256",
    }
    if (not isinstance(sentinel, dict) or set(sentinel) != expected_sentinel or
            sentinel.get("contract") != native.SENTINEL_HEADER or
            sentinel.get("mode") != "0400" or
            any(sentinel.get(key) is not True for key in (
                "root_owned", "single_link", "machine_id_bound",
                "boot_id_bound", "run_id_bound", "instance_challenge_bound"))):
        fail(f"native {expected_variant} sentinel contract mismatch")
    for key in (
            "machine_id_sha256", "boot_id_sha256",
            "vm_image_manifest_sha256",
            "provisioning_manifest_sha256", "platform_policy_sha256",
            "clean_source_bundle_sha256", "clean_source_manifest_sha256",
            "clean_source_files_sha256",
            "run_id_sha256", "instance_challenge_sha256"):
        require_sha256(sentinel.get(key), key)

    network = report["network_isolation"]
    expected_network_fields = {
        "loopback_present", "non_loopback_addresses",
        "non_loopback_links_up", "default_routes", "non_loopback_routes",
    }
    if (not isinstance(network, dict) or
            set(network) != expected_network_fields or
            network.get("loopback_present") is not True or
            any(type(network.get(field)) is not int or network[field] != 0
                for field in (
                    "non_loopback_addresses", "non_loopback_links_up",
                    "default_routes", "non_loopback_routes"))):
        fail(f"native {expected_variant} network isolation mismatch")
    parse_input_records(report["inputs"], f"native {expected_variant} gate")
    runtime_input_records = parse_input_records(
        report["runtime_inputs"], f"native {expected_variant} runtime")

    agent_os = report["agent_os"]
    expected_agent_os = {
        "installation_manifest_sha256", "installation_file_count",
        "gateway_sha256", "sessionctl_sha256", "mcp_server_sha256",
        "installation_preflight", "runtime_preflight_executed",
        "runtime_preflight_required", "runtime_gate_inputs_staged",
        "runtime_input_manifest_sha256", "runtime_input_file_count",
        "runtime_artifacts_staged",
    }
    if (not isinstance(agent_os, dict) or set(agent_os) != expected_agent_os or
            type(agent_os.get("installation_file_count")) is not int or
            agent_os["installation_file_count"] <= 0 or
            agent_os.get("installation_preflight") is not True or
            agent_os.get("runtime_preflight_executed") is not True or
            agent_os.get("runtime_preflight_required") is not True or
            agent_os.get("runtime_gate_inputs_staged") is not True or
            type(agent_os.get("runtime_input_file_count")) is not int or
            agent_os["runtime_input_file_count"] != len(runtime_input_records) or
            agent_os.get("runtime_artifacts_staged") is not False):
        fail(f"native {expected_variant} Agent OS runtime contract mismatch")
    for field in (
            "installation_manifest_sha256", "gateway_sha256",
            "sessionctl_sha256", "mcp_server_sha256",
            "runtime_input_manifest_sha256"):
        require_sha256(agent_os.get(field), field)

    runtime = report["agent_os_runtime"]
    expected_runtime = {
        "source", "result_schema", "result_parse_verified",
        "runtime_preflight_executed", "runtime_preflight_required",
        "runtime_input_manifest_sha256", "runtime_input_records_sha256",
        "runtime_input_content_sha256", "runtime_inner_gate_sha256",
        "runtime_result_sha256", "runtime_lifecycle_sha256",
        "identities", "watch_tools", "read_probes", "lifecycle", "checks",
        "watch_session_revoked", "runtime_cleanup_complete",
        "ib_adapter_visible_during_runtime", "paper_authorized",
        "live_authorized", "real_broker_connections", "paper_orders",
        "inner",
    }
    if (not isinstance(runtime, dict) or set(runtime) != expected_runtime or
            runtime.get("source") != "real-native-vm-rootful-inner-process" or
            runtime.get("result_schema") !=
            native.AGENT_OS_RUNTIME_RESULT_SCHEMA or
            runtime.get("result_parse_verified") is not True or
            runtime.get("runtime_preflight_executed") is not True or
            runtime.get("runtime_preflight_required") is not True or
            runtime.get("runtime_input_manifest_sha256") !=
            agent_os["runtime_input_manifest_sha256"] or
            runtime.get("watch_tools") != list(native.AGENT_OS_WATCH_TOOLS) or
            runtime.get("read_probes") != list(native.AGENT_OS_READ_PROBES) or
            runtime.get("watch_session_revoked") is not True or
            runtime.get("runtime_cleanup_complete") is not True or
            runtime.get("ib_adapter_visible_during_runtime") is not False or
            runtime.get("paper_authorized") is not False or
            runtime.get("live_authorized") is not False or
            type(runtime.get("real_broker_connections")) is not int or
            runtime["real_broker_connections"] != 0 or
            type(runtime.get("paper_orders")) is not int or
            runtime["paper_orders"] != 0):
        fail(f"native {expected_variant} Agent OS runtime evidence mismatch")
    for field in (
            "runtime_input_manifest_sha256", "runtime_input_records_sha256",
            "runtime_input_content_sha256", "runtime_inner_gate_sha256",
            "runtime_result_sha256", "runtime_lifecycle_sha256"):
        require_sha256(runtime.get(field), field)
    if (runtime["runtime_input_records_sha256"] !=
            native.input_manifest_sha256(runtime_input_records) or
            runtime["runtime_input_content_sha256"] !=
            native.input_content_manifest_sha256(runtime_input_records)):
        fail(f"native {expected_variant} runtime input digest mismatch")
    inner_path_records = [
        record for record in runtime_input_records
        if record["path"] == str(native.AGENT_OS_RUNTIME_INNER)]
    if (len(inner_path_records) != 1 or
            inner_path_records[0]["sha256"] !=
            runtime["runtime_inner_gate_sha256"]):
        fail(f"native {expected_variant} runtime inner input mismatch")
    runtime_marker = "HEPTA_AGENT_OS_ROOTFUL_E2E_RESULT="
    try:
        parsed_runtime = native.parse_agent_os_runtime_result(
            runtime_marker + json.dumps(
                runtime["inner"], sort_keys=True, separators=(",", ":")) +
            "\n")
    except native.NativeGateError:
        fail(f"native {expected_variant} runtime result is invalid")
    if (runtime["runtime_result_sha256"] !=
            native.hashlib.sha256(
                native.canonical_json(parsed_runtime)).hexdigest() or
            runtime["runtime_lifecycle_sha256"] !=
            native.hashlib.sha256(
                native.canonical_json(parsed_runtime["lifecycle"])).hexdigest() or
            runtime.get("identities") != parsed_runtime["identities"] or
            runtime.get("checks") != parsed_runtime["checks"] or
            runtime.get("lifecycle") != parsed_runtime["lifecycle"]):
        fail(f"native {expected_variant} runtime result binding mismatch")

    marker = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT="
    try:
        inner = shared.parse_inner_result(
            marker + json.dumps(
                report["inner"], sort_keys=True, separators=(",", ":")) +
            "\n",
            expected_variant, expected_scope=native.NATIVE_SCOPE)
    except shared.GateError:
        fail(f"native {expected_variant} execution result is invalid")
    boundary = report["boundary"]
    expected_boundary_fields = {
        "real_ibapi_elf_executed", "real_broker_connections",
        "paper_orders", "live_enabled", "paper_authorized",
        "agent_os_installation_preflight",
        "agent_os_runtime_preflight_executed",
        "agent_os_runtime_preflight_required",
        "agent_os_runtime_evidence_fabricated", "final_native_gate",
    }
    if (not isinstance(boundary, dict) or
            set(boundary) != expected_boundary_fields or
            type(boundary.get("real_broker_connections")) is not int or
            boundary["real_broker_connections"] != 0 or
            type(boundary.get("paper_orders")) is not int or
            boundary["paper_orders"] != 0 or
            any(boundary.get(field) is not False
                for field in (
                    "real_ibapi_elf_executed", "live_enabled",
                    "paper_authorized",
                    "agent_os_runtime_evidence_fabricated")) or
            any(boundary.get(field) is not True
                for field in (
                    "agent_os_installation_preflight",
                    "agent_os_runtime_preflight_executed",
                    "agent_os_runtime_preflight_required")) or
            boundary.get("final_native_gate") !=
            "four_uid_watch_runtime_variant_requires_three_distinct_"
            "native_vm_runtime_aggregation"):
        fail(f"native {expected_variant} boundary contract mismatch")
    return {
        **report,
        "inner": inner,
        "agent_os_runtime": {**runtime, "inner": parsed_runtime},
        "instance_identity": instance_identity,
    }


def _reverify_instance_identity(
        parsed: dict[str, Any], variant: str) -> dict[str, Any]:
    recorded = parsed["instance_identity"]
    try:
        current = native.verify_instance_receipt(
            Path(recorded["receipt"]["path"]),
            trusted_owner_pairs=TRUSTED_REPORT_OWNER_PAIRS)
    except native.NativeGateError as error:
        fail(f"native {variant} instance receipt revalidation failed: {error}")
    recorded_stable = {
        key: value for key, value in recorded.items()
        if key != "verified_at_ms"}
    current_stable = {
        key: value for key, value in current.items()
        if key != "verified_at_ms"}
    if recorded_stable != current_stable:
        fail(f"native {variant} instance receipt binding changed")
    statement = current["statement"]
    sentinel = parsed["disposable_sentinel"]
    source = statement["source_lineage"]
    if (statement["variant"] != variant or
            statement["vm_type"] != parsed["host"]["vm_type"] or
            native.sha256_text(statement["boot_id"]) !=
                sentinel["boot_id_sha256"] or
            native.sha256_text(statement["run_id"]) !=
                sentinel["run_id_sha256"] or
            native.sha256_text(statement["challenge"]) !=
                sentinel["instance_challenge_sha256"] or
            statement["vm_image_manifest_sha256"] !=
                sentinel["vm_image_manifest_sha256"] or
            statement["provisioning_manifest_sha256"] !=
                sentinel["provisioning_manifest_sha256"] or
            source != {
                "bundle_sha256": sentinel["clean_source_bundle_sha256"],
                "manifest_sha256": sentinel["clean_source_manifest_sha256"],
                "files_sha256": sentinel["clean_source_files_sha256"],
            } or
            not (statement["issued_at_ms"] <= recorded["verified_at_ms"] <
                 statement["expires_at_ms"])):
        fail(f"native {variant} instance receipt lineage mismatch")
    return current


def aggregate_reports(
        reports: dict[str, Any],
        aggregation_inputs: Optional[list[dict[str, Any]]] = None,
        ) -> dict[str, Any]:
    if set(reports) != set(VARIANTS):
        fail("native aggregate requires exactly real, sandbox and stub reports")
    parsed = {
        variant: parse_variant_report(reports[variant], variant)
        for variant in VARIANTS
    }
    instance_identities = {
        variant: _reverify_instance_identity(parsed[variant], variant)
        for variant in VARIANTS
    }
    instance_statements = {
        variant: instance_identities[variant]["statement"]
        for variant in VARIANTS
    }
    if len({instance_statements[variant]["instance_uuid"]
            for variant in VARIANTS}) != 3:
        fail("native variants do not prove three distinct external instances")
    if len({instance_statements[variant]["challenge"]
            for variant in VARIANTS}) != 3:
        fail("native variants reuse an external instance challenge")
    if max(instance_statements[variant]["issued_at_ms"]
           for variant in VARIANTS) >= min(
            instance_statements[variant]["expires_at_ms"]
            for variant in VARIANTS):
        fail("native instance attestation validity windows do not overlap")
    sentinels = {
        variant: parsed[variant]["disposable_sentinel"]
        for variant in VARIANTS
    }
    for field in ("machine_id_sha256", "boot_id_sha256", "run_id_sha256",
                  "vm_image_manifest_sha256"):
        if len({sentinels[variant][field] for variant in VARIANTS}) != 3:
            fail(f"native variants do not have three distinct {field} values")
    for field in (
            "platform_policy_sha256", "clean_source_bundle_sha256",
            "clean_source_manifest_sha256", "clean_source_files_sha256"):
        if len({sentinels[variant][field] for variant in VARIANTS}) != 1:
            fail(f"native variants disagree on {field}")
    if (len({parsed[variant]["host"]["vm_type"]
             for variant in VARIANTS}) != 1 or
            len({parsed[variant]["host"]["kernel_release"]
                 for variant in VARIANTS}) != 1):
        fail("native variants disagree on VM type or kernel release")

    metrics = {
        variant: parsed[variant]["inner"]["metrics"]
        for variant in VARIANTS
    }
    agent_os = {
        variant: parsed[variant]["agent_os"] for variant in VARIANTS}
    runtime = {
        variant: parsed[variant]["agent_os_runtime"] for variant in VARIANTS}
    for field in (
            "installation_manifest_sha256", "installation_file_count",
            "gateway_sha256", "sessionctl_sha256", "mcp_server_sha256",
            "runtime_input_manifest_sha256", "runtime_input_file_count"):
        if len({agent_os[variant][field] for variant in VARIANTS}) != 1:
            fail(f"native variants disagree on Agent OS {field}")
    for field in (
            "runtime_input_manifest_sha256",
            "runtime_input_content_sha256", "runtime_inner_gate_sha256"):
        if len({runtime[variant][field] for variant in VARIANTS}) != 1:
            fail(f"native variants disagree on Agent OS runtime {field}")
    for field in (
            "simulator_sha256", "client_probe_sha256",
            "formal_ibapi_sha256"):
        if len({metrics[variant][field] for variant in VARIANTS}) != 1:
            fail(f"native variants disagree on {field}")
    if (metrics["real"]["executed_ib_path_sha256"] !=
            metrics["stub"]["executed_ib_path_sha256"] or
            metrics["sandbox"]["executed_ib_path_sha256"] in {
                metrics["real"]["executed_ib_path_sha256"],
                metrics["real"]["formal_ibapi_sha256"]}):
        fail("native variant executed-binary closure mismatch")

    return {
        "schema": SCHEMA,
        "passed": True,
        "certification_level": RUNTIME_CERTIFICATION_LEVEL,
        "variants": {
            variant: {
                "vm_type": parsed[variant]["host"]["vm_type"],
                "kernel_release": parsed[variant]["host"]["kernel_release"],
                "vm_image_manifest_sha256":
                    sentinels[variant]["vm_image_manifest_sha256"],
                "provisioning_manifest_sha256":
                    sentinels[variant]["provisioning_manifest_sha256"],
                "machine_id_sha256": sentinels[variant]["machine_id_sha256"],
                "boot_id_sha256": sentinels[variant]["boot_id_sha256"],
                "run_id_sha256": sentinels[variant]["run_id_sha256"],
                "instance_uuid":
                    instance_statements[variant]["instance_uuid"],
                "instance_challenge_sha256":
                    sentinels[variant]["instance_challenge_sha256"],
                "instance_provisioner_id":
                    instance_statements[variant]["provisioner_id"],
                "instance_hypervisor_id":
                    instance_statements[variant]["hypervisor_id"],
                "instance_receipt_file_sha256":
                    instance_identities[variant]["receipt"]["file_sha256"],
                "instance_receipt_body_sha256":
                    instance_identities[variant]["receipt"]["body_sha256"],
                "instance_receipt_issued_at_ms":
                    instance_statements[variant]["issued_at_ms"],
                "instance_receipt_expires_at_ms":
                    instance_statements[variant]["expires_at_ms"],
                "agent_os_installation_manifest_sha256":
                    agent_os[variant]["installation_manifest_sha256"],
                "agent_os_runtime_input_manifest_sha256":
                    runtime[variant]["runtime_input_manifest_sha256"],
                "agent_os_runtime_input_records_sha256":
                    runtime[variant]["runtime_input_records_sha256"],
                "agent_os_runtime_result_sha256":
                    runtime[variant]["runtime_result_sha256"],
                "agent_os_runtime_lifecycle_sha256":
                    runtime[variant]["runtime_lifecycle_sha256"],
                "agent_os_runtime_watch_generation":
                    runtime[variant]["lifecycle"]["watch_generation"],
                "agent_os_runtime_preflight_executed": True,
                "agent_os_watch_session_revoked": True,
                "agent_os_runtime_cleanup_complete": True,
                "executed_kind": metrics[variant]["executed_kind"],
                "executed_ib_path_sha256":
                    metrics[variant]["executed_ib_path_sha256"],
            }
            for variant in VARIANTS
        },
        "common_closure": {
            "platform_policy_sha256":
                sentinels["real"]["platform_policy_sha256"],
            "clean_source_bundle_sha256":
                sentinels["real"]["clean_source_bundle_sha256"],
            "clean_source_manifest_sha256":
                sentinels["real"]["clean_source_manifest_sha256"],
            "clean_source_files_sha256":
                sentinels["real"]["clean_source_files_sha256"],
            "simulator_sha256": metrics["real"]["simulator_sha256"],
            "client_probe_sha256": metrics["real"]["client_probe_sha256"],
            "formal_ibapi_sha256": metrics["real"]["formal_ibapi_sha256"],
            "agent_os_installation_manifest_sha256":
                agent_os["real"]["installation_manifest_sha256"],
            "agent_os_installation_file_count":
                agent_os["real"]["installation_file_count"],
            "agent_os_gateway_sha256": agent_os["real"]["gateway_sha256"],
            "agent_os_sessionctl_sha256": agent_os["real"]["sessionctl_sha256"],
            "agent_os_mcp_server_sha256":
                agent_os["real"]["mcp_server_sha256"],
            "agent_os_runtime_input_manifest_sha256":
                runtime["real"]["runtime_input_manifest_sha256"],
            "agent_os_runtime_input_content_sha256":
                runtime["real"]["runtime_input_content_sha256"],
            "agent_os_runtime_inner_gate_sha256":
                runtime["real"]["runtime_inner_gate_sha256"],
            "agent_os_runtime_input_file_count":
                agent_os["real"]["runtime_input_file_count"],
            "agent_os_fixed_identities": {
                "agent_uid": 2004,
                "gateway_uid": 2001,
                "simulator_execution_uid": 2002,
                "ib_execution_uid_reserved_not_started": 2003,
            },
            "agent_os_watch_tools": list(native.AGENT_OS_WATCH_TOOLS),
            "agent_os_read_probes": list(native.AGENT_OS_READ_PROBES),
            "all_agent_os_runtime_preflights_executed": True,
            "all_agent_os_watch_sessions_revoked": True,
            "all_agent_os_runtime_cleanup_complete": True,
            "distinct_native_vms": 3,
            "distinct_provisioner_attested_instances": 3,
            "external_instance_receipts_verified": True,
            "instance_receipt_validity_windows_overlap": True,
            "all_networks_loopback_only": True,
            "all_inputs_stable": True,
        },
        "aggregation_inputs": (
            aggregation_inputs if aggregation_inputs is not None else []),
        "boundary": {
            "real_ibapi_elf_executed": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "live_enabled": False,
            "paper_authorized": False,
            "native_agent_os_installation_gate_satisfied": True,
            "native_agent_os_runtime_gate_satisfied": True,
            "agent_os_runtime_preflight_executed": True,
            "agent_os_runtime_preflight_required": True,
            "agent_os_runtime_evidence_fabricated": False,
            "agent_os_runtime_source":
                "three-distinct-externally-attested-native-vms",
            "ib_adapter_visible_during_agent_os_runtime": False,
            "paper_certification": "requires_separate_explicit_authorization",
        },
    }


def parse_runtime_aggregate(report: Any) -> dict[str, Any]:
    expected_top = {
        "schema", "passed", "certification_level", "variants",
        "common_closure", "aggregation_inputs", "boundary",
    }
    if (not isinstance(report, dict) or set(report) != expected_top or
            report.get("schema") != RUNTIME_AGGREGATE_SCHEMA or
            report.get("passed") is not True or
            report.get("certification_level") !=
            RUNTIME_CERTIFICATION_LEVEL):
        fail("native runtime aggregate top-level contract mismatch")
    variants = report.get("variants")
    if not isinstance(variants, dict) or set(variants) != set(VARIANTS):
        fail("native runtime aggregate requires exactly three variants")
    expected_variant = {
        "vm_type", "kernel_release", "vm_image_manifest_sha256",
        "provisioning_manifest_sha256", "machine_id_sha256",
        "boot_id_sha256", "run_id_sha256", "instance_uuid",
        "instance_challenge_sha256", "instance_provisioner_id",
        "instance_hypervisor_id", "instance_receipt_file_sha256",
        "instance_receipt_body_sha256", "instance_receipt_issued_at_ms",
        "instance_receipt_expires_at_ms",
        "agent_os_installation_manifest_sha256",
        "agent_os_runtime_input_manifest_sha256",
        "agent_os_runtime_input_records_sha256",
        "agent_os_runtime_result_sha256",
        "agent_os_runtime_lifecycle_sha256",
        "agent_os_runtime_watch_generation",
        "agent_os_runtime_preflight_executed",
        "agent_os_watch_session_revoked",
        "agent_os_runtime_cleanup_complete",
        "executed_kind", "executed_ib_path_sha256",
    }
    for variant in VARIANTS:
        record = variants[variant]
        if (not isinstance(record, dict) or set(record) != expected_variant or
                record.get("vm_type") not in native.VM_TYPES or
                not isinstance(record.get("kernel_release"), str) or
                not record["kernel_release"] or
                type(record.get("agent_os_runtime_watch_generation")) is not
                int or record["agent_os_runtime_watch_generation"] < 1 or
                record.get("agent_os_runtime_preflight_executed") is not True or
                record.get("agent_os_watch_session_revoked") is not True or
                record.get("agent_os_runtime_cleanup_complete") is not True or
                not isinstance(record.get("executed_kind"), str) or
                not record["executed_kind"]):
            fail(f"native runtime aggregate {variant} record mismatch")
        if (native.INSTANCE_UUID.fullmatch(
                str(record.get("instance_uuid", ""))) is None or
                native.INSTANCE_IDENTITY.fullmatch(
                    str(record.get("instance_provisioner_id", ""))) is None or
                native.INSTANCE_IDENTITY.fullmatch(
                    str(record.get("instance_hypervisor_id", ""))) is None or
                type(record.get("instance_receipt_issued_at_ms")) is not int or
                type(record.get("instance_receipt_expires_at_ms")) is not int or
                record["instance_receipt_issued_at_ms"] <= 0 or
                record["instance_receipt_expires_at_ms"] <=
                    record["instance_receipt_issued_at_ms"]):
            fail(f"native runtime aggregate {variant} instance record mismatch")
        for field in expected_variant & {
                "vm_image_manifest_sha256",
                "provisioning_manifest_sha256", "machine_id_sha256",
                "boot_id_sha256", "run_id_sha256",
                "instance_challenge_sha256", "instance_receipt_file_sha256",
                "instance_receipt_body_sha256",
                "agent_os_installation_manifest_sha256",
                "agent_os_runtime_input_manifest_sha256",
                "agent_os_runtime_input_records_sha256",
                "agent_os_runtime_result_sha256",
                "agent_os_runtime_lifecycle_sha256",
                "executed_ib_path_sha256"}:
            require_sha256(record.get(field), field)
    for field in (
            "vm_image_manifest_sha256", "machine_id_sha256",
            "boot_id_sha256", "run_id_sha256", "instance_challenge_sha256"):
        if len({variants[variant][field] for variant in VARIANTS}) != 3:
            fail(f"native runtime aggregate lacks three distinct {field}")
    if len({variants[variant]["instance_uuid"]
            for variant in VARIANTS}) != 3:
        fail("native runtime aggregate lacks three external instance UUIDs")
    if max(variants[variant]["instance_receipt_issued_at_ms"]
           for variant in VARIANTS) >= min(
            variants[variant]["instance_receipt_expires_at_ms"]
            for variant in VARIANTS):
        fail("native runtime aggregate instance receipt windows do not overlap")
    if (len({variants[variant]["vm_type"] for variant in VARIANTS}) != 1 or
            len({variants[variant]["kernel_release"]
                 for variant in VARIANTS}) != 1):
        fail("native runtime aggregate variants disagree on host identity")
    if any(variants[variant]["executed_kind"] != EXPECTED_EXECUTED_KINDS[variant]
           for variant in VARIANTS):
        fail("native runtime aggregate executed-kind contract mismatch")

    common = report.get("common_closure")
    expected_common = {
        "platform_policy_sha256", "clean_source_bundle_sha256",
        "clean_source_manifest_sha256", "clean_source_files_sha256",
        "simulator_sha256", "client_probe_sha256", "formal_ibapi_sha256",
        "agent_os_installation_manifest_sha256",
        "agent_os_installation_file_count", "agent_os_gateway_sha256",
        "agent_os_sessionctl_sha256", "agent_os_mcp_server_sha256",
        "agent_os_runtime_input_manifest_sha256",
        "agent_os_runtime_input_content_sha256",
        "agent_os_runtime_inner_gate_sha256",
        "agent_os_runtime_input_file_count", "agent_os_fixed_identities",
        "agent_os_watch_tools", "agent_os_read_probes",
        "all_agent_os_runtime_preflights_executed",
        "all_agent_os_watch_sessions_revoked",
        "all_agent_os_runtime_cleanup_complete",
        "distinct_native_vms", "distinct_provisioner_attested_instances",
        "external_instance_receipts_verified",
        "instance_receipt_validity_windows_overlap",
        "all_networks_loopback_only",
        "all_inputs_stable",
    }
    if (not isinstance(common, dict) or set(common) != expected_common or
            type(common.get("agent_os_installation_file_count")) is not int or
            common["agent_os_installation_file_count"] <= 0 or
            type(common.get("agent_os_runtime_input_file_count")) is not int or
            common["agent_os_runtime_input_file_count"] <= 0 or
            common.get("agent_os_fixed_identities") != {
                "agent_uid": 2004,
                "gateway_uid": 2001,
                "simulator_execution_uid": 2002,
                "ib_execution_uid_reserved_not_started": 2003,
            } or
            common.get("agent_os_watch_tools") !=
            list(native.AGENT_OS_WATCH_TOOLS) or
            common.get("agent_os_read_probes") !=
            list(native.AGENT_OS_READ_PROBES) or
            common.get("all_agent_os_runtime_preflights_executed") is not
            True or
            common.get("all_agent_os_watch_sessions_revoked") is not True or
            common.get("all_agent_os_runtime_cleanup_complete") is not True or
            common.get("distinct_native_vms") != 3 or
            common.get("distinct_provisioner_attested_instances") != 3 or
            common.get("external_instance_receipts_verified") is not True or
            common.get("instance_receipt_validity_windows_overlap") is not True or
            common.get("all_networks_loopback_only") is not True or
            common.get("all_inputs_stable") is not True):
        fail("native runtime aggregate common closure mismatch")
    for field in (
            "platform_policy_sha256", "clean_source_bundle_sha256",
            "clean_source_manifest_sha256", "clean_source_files_sha256",
            "simulator_sha256", "client_probe_sha256", "formal_ibapi_sha256",
            "agent_os_installation_manifest_sha256",
            "agent_os_gateway_sha256", "agent_os_sessionctl_sha256",
            "agent_os_mcp_server_sha256",
            "agent_os_runtime_input_manifest_sha256",
            "agent_os_runtime_input_content_sha256",
            "agent_os_runtime_inner_gate_sha256"):
        require_sha256(common.get(field), field)
    if any(
            variants[variant]["agent_os_installation_manifest_sha256"] !=
            common["agent_os_installation_manifest_sha256"] or
            variants[variant]["agent_os_runtime_input_manifest_sha256"] !=
            common["agent_os_runtime_input_manifest_sha256"]
            for variant in VARIANTS):
        fail("native runtime aggregate variant/common lineage mismatch")
    if (variants["real"]["executed_ib_path_sha256"] !=
            variants["stub"]["executed_ib_path_sha256"] or
            variants["sandbox"]["executed_ib_path_sha256"] in {
                variants["real"]["executed_ib_path_sha256"],
                common["formal_ibapi_sha256"]}):
        fail("native runtime aggregate executed-binary closure mismatch")
    parse_variant_report_inputs(report.get("aggregation_inputs"))
    boundary = report.get("boundary")
    expected_boundary_fields = {
        "real_ibapi_elf_executed", "real_broker_connections",
        "paper_orders", "live_enabled", "paper_authorized",
        "native_agent_os_installation_gate_satisfied",
        "native_agent_os_runtime_gate_satisfied",
        "agent_os_runtime_preflight_executed",
        "agent_os_runtime_preflight_required",
        "agent_os_runtime_evidence_fabricated",
        "agent_os_runtime_source",
        "ib_adapter_visible_during_agent_os_runtime",
        "paper_certification",
    }
    if (not isinstance(boundary, dict) or
            set(boundary) != expected_boundary_fields or
            type(boundary.get("real_broker_connections")) is not int or
            boundary["real_broker_connections"] != 0 or
            type(boundary.get("paper_orders")) is not int or
            boundary["paper_orders"] != 0 or
            any(boundary.get(field) is not False
                for field in (
                    "real_ibapi_elf_executed", "live_enabled",
                    "paper_authorized",
                    "agent_os_runtime_evidence_fabricated",
                    "ib_adapter_visible_during_agent_os_runtime")) or
            any(boundary.get(field) is not True
                for field in (
                    "native_agent_os_installation_gate_satisfied",
                    "native_agent_os_runtime_gate_satisfied",
                    "agent_os_runtime_preflight_executed",
                    "agent_os_runtime_preflight_required")) or
            boundary.get("agent_os_runtime_source") !=
            "three-distinct-externally-attested-native-vms" or
            boundary.get("paper_certification") !=
            "requires_separate_explicit_authorization"):
        fail("native runtime aggregate offline authority boundary mismatch")
    return report


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _require_trusted_report_directory(
        metadata: os.stat_result, label: str) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    if (not stat.S_ISDIR(metadata.st_mode) or
            (metadata.st_uid, metadata.st_gid) not in
            TRUSTED_REPORT_OWNER_PAIRS or
            mode & 0o022 or mode & 0o7000):
        fail(f"{label} must be a root-owned protected directory")


def _require_root_report_file(
        metadata: os.stat_result, label: str) -> None:
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            (metadata.st_uid, metadata.st_gid) not in
            TRUSTED_REPORT_OWNER_PAIRS or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_size <= 0 or
            metadata.st_size > MAX_VARIANT_REPORT_BYTES):
        fail(f"{label} must be a root-owned single-link 0600 report")


@dataclass(frozen=True)
class CapturedVariantReport:
    variant: str
    path: Path
    identity: tuple[int, ...]
    data: bytes
    document: dict[str, Any]

    @property
    def binding(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "path": str(self.path),
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "size": len(self.data),
            "mode": "0600",
        }


def capture_variant_report(
        path: Path, expected_variant: str) -> CapturedVariantReport:
    if expected_variant not in VARIANTS:
        fail("native variant report selector is invalid")
    absolute = Path(os.path.abspath(path))
    expected_name = (
        f"execution-native-systemd-{expected_variant}.json")
    try:
        canonical = absolute.resolve(strict=True)
    except OSError:
        fail(f"native {expected_variant} report path is unavailable")
    if absolute != canonical or canonical.name != expected_name:
        fail(f"native {expected_variant} report path is not canonical")

    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    file_flags = (
        os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    report_descriptor = -1
    try:
        current = os.open("/", directory_flags)
        descriptors.append(current)
        _require_trusted_report_directory(
            os.fstat(current), "native report path root")
        for component in canonical.parent.parts[1:]:
            before = os.stat(
                component, dir_fd=current, follow_symlinks=False)
            _require_trusted_report_directory(
                before, f"native report parent {component}")
            child = os.open(component, directory_flags, dir_fd=current)
            opened = os.fstat(child)
            if _directory_identity(before) != _directory_identity(opened):
                os.close(child)
                fail("native report parent changed while opening")
            components.append((component, _directory_identity(before)))
            descriptors.append(child)
            current = child

        before_file = os.stat(
            canonical.name, dir_fd=current, follow_symlinks=False)
        _require_root_report_file(
            before_file, f"native {expected_variant} report")
        report_descriptor = os.open(
            canonical.name, file_flags, dir_fd=current)
        opened_file = os.fstat(report_descriptor)
        if _file_identity(before_file) != _file_identity(opened_file):
            fail("native variant report changed while opening")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                report_descriptor,
                min(1024 * 1024,
                    MAX_VARIANT_REPORT_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_VARIANT_REPORT_BYTES:
                fail("native variant report exceeds its size limit")
            chunks.append(chunk)
        after_descriptor = os.fstat(report_descriptor)
        after_path = os.stat(
            canonical.name, dir_fd=current, follow_symlinks=False)
        if (_file_identity(opened_file) !=
                _file_identity(after_descriptor) or
                _file_identity(after_descriptor) !=
                _file_identity(after_path) or
                total != opened_file.st_size):
            fail("native variant report changed during stable read")
        for index, (component, expected) in enumerate(components):
            observed = os.stat(
                component,
                dir_fd=descriptors[index],
                follow_symlinks=False,
            )
            if _directory_identity(observed) != expected:
                fail("native variant report parent changed during read")
        data = b"".join(chunks)
    except AggregateError:
        raise
    except OSError:
        fail(f"native {expected_variant} report path is unsafe or unstable")
    finally:
        if report_descriptor >= 0:
            try:
                os.close(report_descriptor)
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass

    document = strict_json(data, f"native {expected_variant} report")
    if not isinstance(document, dict):
        fail(f"native {expected_variant} report is not an object")
    parsed = parse_variant_report(document, expected_variant)
    if parsed != document:
        fail(f"native {expected_variant} report normalization drift")
    return CapturedVariantReport(
        variant=expected_variant,
        path=canonical,
        identity=_file_identity(opened_file),
        data=data,
        document=document,
    )


def _recheck_variant_report(captured: CapturedVariantReport) -> None:
    confirmed = capture_variant_report(
        captured.path, captured.variant)
    if confirmed != captured:
        fail(
            f"native {captured.variant} report changed across aggregation")


def verify_runtime_aggregate(report: Any) -> dict[str, Any]:
    """Rebuild one aggregate from its three raw, stable variant reports."""
    parsed = parse_runtime_aggregate(report)
    bindings = parse_variant_report_inputs(parsed["aggregation_inputs"])
    captures: dict[str, CapturedVariantReport] = {}
    reports: dict[str, Any] = {}
    for binding in bindings:
        variant = binding["variant"]
        captured = capture_variant_report(
            Path(binding["path"]), variant)
        if captured.binding != binding:
            fail(f"native {variant} raw report binding drift")
        captures[variant] = captured
        reports[variant] = captured.document
    rebuilt = aggregate_reports(reports, bindings)
    if rebuilt != parsed:
        fail("native runtime aggregate differs from raw-report reconstruction")
    for variant in VARIANTS:
        _recheck_variant_report(captures[variant])
    return parsed


def read_report(path: Path, expected_variant: str) -> dict[str, Any]:
    """Compatibility wrapper returning a fully parsed stable raw report."""
    return capture_variant_report(path, expected_variant).document


def validate_output_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    if absolute.name != "execution-native-systemd-aggregate.json":
        fail("native aggregate output name is outside the allowlist")
    parent = absolute.parent.resolve(strict=True)
    metadata = os.lstat(parent)
    if (absolute.parent != parent or
            (metadata.st_uid, metadata.st_gid) not in
            TRUSTED_REPORT_OWNER_PAIRS or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        fail("native aggregate output directory is unsafe")
    try:
        target = os.lstat(absolute)
    except FileNotFoundError:
        target = None
    if target is not None and (
            not stat.S_ISREG(target.st_mode) or
            (target.st_uid, target.st_gid) not in
            TRUSTED_REPORT_OWNER_PAIRS or target.st_nlink != 1 or
            stat.S_IMODE(target.st_mode) != 0o600):
        fail("existing native aggregate output is unsafe")
    return absolute


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="aggregate three native disposable-VM systemd gates")
    for variant in VARIANTS:
        parser.add_argument(f"--{variant}-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = validate_output_path(args.report)
        captures = {
            variant: capture_variant_report(
                getattr(args, f"{variant}_report"), variant)
            for variant in VARIANTS
        }
        reports = {
            variant: captures[variant].document for variant in VARIANTS}
        aggregation_inputs = [
            captures[variant].binding for variant in VARIANTS]
        aggregate = aggregate_reports(reports, aggregation_inputs)
        verify_runtime_aggregate(aggregate)
        shared.atomic_report(output, aggregate)
    except Exception as error:
        print(f"hepta_native_systemd_aggregate: FAIL {error}", file=sys.stderr)
        return 1
    print("hepta_native_systemd_aggregate: PASS "
          f"level={RUNTIME_CERTIFICATION_LEVEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
