#!/usr/bin/env python3

"""Offline negative tests for native-VM bundle verification."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import build_hepta_execution_native_vm_bundle as bundle  # noqa: E402
import run_hepta_execution_rootful_systemd_gate as shared  # noqa: E402
import verify_hepta_execution_native_vm_bundle as verifier  # noqa: E402


POLICY = REPOSITORY / "tests/native_systemd/platform-policy-v1.json"


def write_payload(rootfs: Path, relative: str, contents: bytes, mode: int) -> None:
    destination = rootfs / relative
    shared.write_private(destination, contents, mode)
    parent = destination.parent
    while parent != rootfs:
        os.chmod(parent, 0o755)
        parent = parent.parent


def fixture_bundle(directory: Path, *, variant: str = "real",
                   extra: str = "",
                   agent_runtime_drift: bool = False,
                   runtime_tool_drift: bool = False,
                   runtime_input_drift: bool = False,
                   staged_source_drift: bool = False,
                   build_source_drift: bool = False,
                   build_evidence_drift: bool = False,
                   ibapi_bytes_drift: bool = False,
                   ibapi_manifest_missing: bool = False,
                   ibapi_manifest_tamper: bool = False,
                   ibapi_path_only_legacy: bool = False,
                   ibapi_off_nonempty: bool = False,
                   causal_output_drift: bool = False) -> argparse.Namespace:
    rootfs = directory / "rootfs"
    rootfs.mkdir(mode=0o755)
    policy = POLICY.read_bytes()
    policy_sha256 = hashlib.sha256(policy).hexdigest()

    source_contents: dict[str, bytes] = {}
    source_records: list[dict[str, object]] = []
    source_paths = set(bundle.SOURCE_STAGE_BINDINGS) | set(
        bundle.REVIEWED_BUILD_SOURCE_PATHS)
    for relative in sorted(source_paths):
        contents = (
            policy if relative == "tests/native_systemd/platform-policy-v1.json"
            else ("clean-source:" + relative + "\n").encode("utf-8"))
        mode = (
            "0755" if any(
                destination_mode == "0755"
                for _destination, destination_mode in
                bundle.SOURCE_STAGE_BINDINGS.get(relative, ()))
            else "0644")
        source_contents[relative] = contents
        source_records.append({
            "path": relative, "mode": mode, "size": len(contents),
            "sha256": hashlib.sha256(contents).hexdigest(),
        })
    source_index = {
        str(record["path"]): record for record in source_records}
    files_sha256 = hashlib.sha256(json.dumps(
        source_records, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode()).hexdigest()
    source_manifest = {
        "schema": "hepta.clean-source-bundle.v2",
        "bundle_class": "strict-source-only",
        "version": "fixture",
        "git_head": "a" * 40,
        "root": "heptatrader-fixture",
        "file_count": len(source_records),
        "files_sha256": files_sha256,
        "files": source_records,
    }
    source_manifest_bytes = (
        json.dumps(source_manifest, indent=2, sort_keys=True) + "\n").encode()
    source_bundle_bytes = b"fixture-clean-source-tar\n"
    source_bundle_path = directory / "heptatrader-fixture.tar"
    source_manifest_path = directory / "heptatrader-fixture.manifest.json"
    shared.write_private(source_bundle_path, source_bundle_bytes, 0o600)
    shared.write_private(source_manifest_path, source_manifest_bytes, 0o600)
    clean_source = {
        "version": "fixture", "git_head": "a" * 40,
        "file_count": len(source_records),
        "files_sha256": files_sha256,
        "security_manifest_sha256": "sha256:" + "b" * 64,
        "bundle_sha256": hashlib.sha256(source_bundle_bytes).hexdigest(),
        "manifest_sha256":
            hashlib.sha256(source_manifest_bytes).hexdigest(),
        "paper_authorized": False,
        "live_authorized": False,
        "bundle_class": "strict-source-only",
        "nonredistributable_vendor_payload_included": False,
        "prebuilt_payload_included": False,
    }

    destination_sources: dict[str, tuple[str, str]] = {}
    for relative, destinations in bundle.SOURCE_STAGE_BINDINGS.items():
        for destination, destination_mode in destinations:
            destination_sources[destination] = (relative, destination_mode)

    binary_payloads = {
        "hepta-executiond": b"\x7fELFfixture-simulator\n",
        "hepta-ib-executiond-disabled": b"\x7fELFfixture-disabled\n",
        "hepta_execution_systemd_client_probe": b"\x7fELFfixture-client\n",
        "hepta_execution_systemd_sandbox_probe": b"\x7fELFfixture-sandbox\n",
        "hepta-tool-gatewayd": b"\x7fELFfixture-gateway\n",
        "hepta-sessionctl": b"\x7fELFfixture-sessionctl\n",
        "heptactl": b"\x7fELFfixture-heptactl\n",
    }
    binary_destinations = {
        "hepta-executiond": ["usr/libexec/hepta-executiond"],
        "hepta-ib-executiond-disabled": [
            "usr/local/libexec/hepta-ib-executiond-disabled",
            *(["usr/libexec/hepta-ib-executiond"]
              if variant != "sandbox" else [])],
        "hepta_execution_systemd_client_probe": [
            "usr/local/libexec/hepta_execution_systemd_client_probe"],
        "hepta_execution_systemd_sandbox_probe": [
            "usr/local/libexec/hepta_execution_systemd_sandbox_probe",
            *(["usr/libexec/hepta-ib-executiond"]
              if variant == "sandbox" else [])],
        "hepta-tool-gatewayd": ["usr/libexec/hepta-tool-gatewayd"],
        "hepta-sessionctl": ["usr/bin/hepta-sessionctl"],
        "heptactl": ["usr/bin/heptactl"],
    }
    destination_binaries = {
        destination: artifact
        for artifact, destinations in binary_destinations.items()
        for destination in destinations
    }

    all_paths = set(verifier.REQUIRED) | set(destination_sources) | set(
        destination_binaries)
    special = {
            verifier.IMAGE_MANIFEST, verifier.IMAGE_DIGEST,
            verifier.PROVISIONING_MANIFEST, verifier.PLATFORM_POLICY,
            verifier.CLEAN_SOURCE_PROVENANCE,
            verifier.CLEAN_SOURCE_MANIFEST,
            verifier.SOURCE_BUILD_LINEAGE,
            verifier.AGENT_OS_INSTALLATION_MANIFEST,
            verifier.AGENT_OS_RUNTIME_INPUT_MANIFEST,
            verifier.VARIANT_FILE, verifier.FORMAL_DIGEST,
            *{
                path.as_posix()
                for paths in bundle.BUILD_EVIDENCE_PATHS.values()
                for path in paths.values()
            },
    }
    for path in sorted(all_paths - special):
        if path == "etc/heptatrader/hepta-supervisor-lease.key":
            contents = bundle.agent_os_contract.UNPROVISIONED_SUPERVISOR_LEASE
            mode = 0o400
        elif path in destination_binaries:
            contents = binary_payloads[destination_binaries[path]]
            mode = 0o755
        elif path in destination_sources:
            relative, destination_mode = destination_sources[path]
            contents = source_contents[relative]
            if (staged_source_drift and path ==
                    "usr/local/libexec/"
                    "run_hepta_execution_native_systemd_gate.py"):
                contents += b"# current-v4-cross-bundle-drift\n"
            mode = int(destination_mode, 8)
        else:
            contents = (path + "\n").encode("ascii")
            mode = (
                0o755 if path.startswith(
                    ("usr/libexec/", "usr/local/libexec/")) else 0o644)
        write_payload(rootfs, path, contents, mode)
    write_payload(rootfs, verifier.PLATFORM_POLICY, policy, 0o444)
    write_payload(rootfs, verifier.VARIANT_FILE,
                  (variant + "\n").encode("ascii"), 0o444)
    formal_sha256 = "a" * 64
    write_payload(rootfs, verifier.FORMAL_DIGEST,
                  (formal_sha256 + "\n").encode("ascii"), 0o444)
    clean_source_bytes = bundle.canonical_json(clean_source)
    clean_source_sha256 = hashlib.sha256(clean_source_bytes).hexdigest()
    write_payload(rootfs, verifier.CLEAN_SOURCE_PROVENANCE,
                  clean_source_bytes, 0o444)
    write_payload(rootfs, verifier.CLEAN_SOURCE_MANIFEST,
                  source_manifest_bytes, 0o444)

    source_root = Path("/tmp/heptatrader-fixture")
    build_roots = {
        "ibapi_on": Path("/tmp/hepta-fixture-build-on"),
        "ibapi_off": Path("/tmp/hepta-fixture-build-off"),
    }
    ibapi_payloads = {
        "EClient.cpp": b"fixture EClient bytes\n",
        "EWrapper.h": b"fixture EWrapper bytes\n",
    }
    ibapi_source_records = [{
        "path": path, "mode": "0644", "size": len(contents),
        "sha256": hashlib.sha256(contents).hexdigest(),
    } for path, contents in sorted(ibapi_payloads.items())]
    ibapi_source_manifest = {
        "schema": bundle.IBAPI_SOURCE_MANIFEST_SCHEMA,
        "root": "fixture-ibapi",
        "file_count": len(ibapi_source_records),
        "files_sha256": hashlib.sha256(json.dumps(
            ibapi_source_records, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode()).hexdigest(),
        "files": ibapi_source_records,
    }
    ibapi_source_index = {
        record["path"]: record for record in ibapi_source_records}
    build_records: dict[str, dict[str, object]] = {}
    for build_key, ibapi in (("ibapi_on", True), ("ibapi_off", False)):
        configured_source_root = (
            Path("/tmp/heptatrader-crossed")
            if build_source_drift and build_key == "ibapi_off"
            else source_root)
        cache = (
            "CMAKE_BUILD_TYPE:STRING=Release\n"
            "BUILD_TESTING:BOOL=ON\n"
            "CMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON\n"
            "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE:BOOL=OFF\n"
            f"HEPTA_ENABLE_IBAPI:BOOL={'ON' if ibapi else 'OFF'}\n"
            f"IBAPI_ROOT:PATH={'/tmp/fixture-ibapi' if ibapi else ''}\n"
            "CMAKE_GENERATOR:INTERNAL=Ninja\n"
            "CMAKE_CXX_COMPILER:FILEPATH=/usr/bin/c++\n"
            f"CMAKE_CACHEFILE_DIR:INTERNAL={build_roots[build_key]}\n"
            f"CMAKE_HOME_DIRECTORY:INTERNAL={configured_source_root}\n").encode()
        clean_compile_source = (
            configured_source_root / bundle.REVIEWED_BUILD_SOURCE_PATHS[0])
        compile_commands = [{
            "directory": str(build_roots[build_key]),
            "command": f"/usr/bin/c++ -c {clean_compile_source}",
            "file": str(clean_compile_source),
        }]
        if ibapi:
            compile_commands.append({
                "directory": str(build_roots[build_key]),
                "command": "/usr/bin/c++ -c /tmp/fixture-ibapi/EClient.cpp",
                "file": "/tmp/fixture-ibapi/EClient.cpp",
            })
        compile_bytes = json.dumps(
            compile_commands, separators=(",", ":")).encode()
        cache_path = bundle.BUILD_EVIDENCE_PATHS[
            build_key]["cmake_cache"].as_posix()
        compile_path = bundle.BUILD_EVIDENCE_PATHS[
            build_key]["compile_commands"].as_posix()
        write_payload(rootfs, cache_path, cache, 0o444)
        write_payload(rootfs, compile_path, compile_bytes, 0o444)
        parsed_cache = verifier.parse_cmake_cache(cache, cache_path)
        compile_lineage = verifier.verify_compile_evidence(
            compile_bytes, cache=parsed_cache, source_index=source_index,
            ibapi=ibapi,
            ibapi_source_index=(ibapi_source_index if ibapi else None))
        build_records[build_key] = {
            "path": build_roots[build_key].name,
            "source_root": source_root.name,
            "source_manifest_sha256": clean_source["manifest_sha256"],
            "source_files_sha256": clean_source["files_sha256"],
            "source_file_count": clean_source["file_count"],
            "cmake_cache_path": cache_path,
            "cmake_cache_sha256": hashlib.sha256(cache).hexdigest(),
            "compile_commands_path": compile_path,
            "compile_commands_sha256":
                hashlib.sha256(compile_bytes).hexdigest(),
            "build_type": "Release",
            "ibapi_enabled": ibapi,
            "ibapi_source_manifest": (
                ibapi_source_manifest if ibapi else None),
            "ibapi_source_manifest_sha256": (
                hashlib.sha256(bundle.canonical_json(
                    ibapi_source_manifest)).hexdigest() if ibapi else None),
            "ibapi_source_file_count": (
                ibapi_source_manifest["file_count"] if ibapi else 0),
            "ibapi_source_files_sha256": (
                ibapi_source_manifest["files_sha256"] if ibapi else None),
            "generator": "Ninja",
            "compiler": "c++",
            **compile_lineage,
        }
        if build_evidence_drift and build_key == "ibapi_off":
            build_records[build_key]["cmake_cache_sha256"] = "f" * 64

    on_record = build_records["ibapi_on"]
    if ibapi_bytes_drift:
        drifted = json.loads(json.dumps(ibapi_source_manifest))
        drifted["files"][0]["sha256"] = hashlib.sha256(
            b"changed SDK bytes at the same path\n").hexdigest()
        drifted["files_sha256"] = hashlib.sha256(json.dumps(
            drifted["files"], ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode()).hexdigest()
        on_record["ibapi_source_manifest"] = drifted
        on_record["ibapi_source_manifest_sha256"] = hashlib.sha256(
            bundle.canonical_json(drifted)).hexdigest()
        on_record["ibapi_source_files_sha256"] = drifted["files_sha256"]
    if ibapi_manifest_missing:
        missing = json.loads(json.dumps(ibapi_source_manifest))
        missing["files"] = missing["files"][1:]
        missing["file_count"] = len(missing["files"])
        missing["files_sha256"] = hashlib.sha256(json.dumps(
            missing["files"], ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode()).hexdigest()
        on_record["ibapi_source_manifest"] = missing
        on_record["ibapi_source_manifest_sha256"] = hashlib.sha256(
            bundle.canonical_json(missing)).hexdigest()
        on_record["ibapi_source_file_count"] = missing["file_count"]
        on_record["ibapi_source_files_sha256"] = missing["files_sha256"]
    if ibapi_manifest_tamper:
        tampered = json.loads(json.dumps(ibapi_source_manifest))
        tampered["files"][0]["size"] += 1
        on_record["ibapi_source_manifest"] = tampered
        on_record["ibapi_source_manifest_sha256"] = hashlib.sha256(
            bundle.canonical_json(tampered)).hexdigest()
    if ibapi_path_only_legacy:
        for key in (
                "ibapi_source_manifest", "ibapi_source_manifest_sha256",
                "ibapi_source_file_count", "ibapi_source_files_sha256"):
            on_record.pop(key)
    if ibapi_off_nonempty:
        off_record = build_records["ibapi_off"]
        off_record["ibapi_source_manifest"] = ibapi_source_manifest
        off_record["ibapi_source_manifest_sha256"] = hashlib.sha256(
            bundle.canonical_json(ibapi_source_manifest)).hexdigest()
        off_record["ibapi_source_file_count"] = (
            ibapi_source_manifest["file_count"])
        off_record["ibapi_source_files_sha256"] = (
            ibapi_source_manifest["files_sha256"])

    staged_sources = verifier.expected_staged_source_records(source_index)
    staged_binaries = []
    for artifact in sorted(binary_destinations):
        payload = binary_payloads[artifact]
        cross_build = (
            "ibapi_off" if artifact in {
                "hepta-tool-gatewayd", "hepta-sessionctl", "heptactl"}
            else None)
        staged_binaries.append({
            "artifact": artifact,
            "build": (
                "ibapi_off"
                if artifact == "hepta-ib-executiond-disabled"
                else "ibapi_on"),
            "build_path": f"bin/Release/{artifact}",
            "destinations": sorted(binary_destinations[artifact]),
            "mode": "0755",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "cross_build": cross_build,
            "cross_build_path": (
                f"bin/Release/{artifact}" if cross_build else None),
        })
    staged_by_artifact = {
        record["artifact"]: record for record in staged_binaries}
    toolchain = [{
        "role": role,
        "path": path,
        "mode": "0755",
        "size": 1024,
        "sha256": hashlib.sha256(role.encode("ascii")).hexdigest(),
    } for role, path in sorted({
        "cmake": "/usr/bin/cmake",
        "c_compiler": "/usr/bin/gcc",
        "cxx_compiler": "/usr/bin/g++",
        "build_program": "/usr/bin/ninja",
        "ar": "/usr/bin/ar",
        "linker": "/usr/bin/ld",
    }.items())]
    for build_key, ibapi in (("ibapi_on", True), ("ibapi_off", False)):
        outputs = []
        for artifact in sorted(bundle.CAUSAL_BUILD_OUTPUTS[build_key]):
            if artifact == "hepta-ib-executiond":
                output = {
                    "artifact": artifact,
                    "build_path": f"bin/Release/{artifact}",
                    "mode": "0755",
                    "size": 128,
                    "sha256": formal_sha256,
                }
            else:
                staged = staged_by_artifact[artifact]
                output = {
                    "artifact": artifact,
                    "build_path": staged["build_path"],
                    "mode": staged["mode"],
                    "size": staged["size"],
                    "sha256": staged["sha256"],
                }
            outputs.append(output)
        build_records[build_key]["causal_build"] = {
            "schema": bundle.CAUSAL_BUILD_RECEIPT_SCHEMA,
            "fresh_build_directory_created_empty": True,
            "prebuilt_artifacts_exactly_matched": True,
            "source_manifest_sha256": clean_source["manifest_sha256"],
            "ibapi_source_manifest_sha256": (
                build_records[build_key].get(
                    "ibapi_source_manifest_sha256") if ibapi else None),
            "configure_argv": bundle._normalized_configure_argv(
                "Ninja", ibapi=ibapi),
            "build_argv": [
                "$CMAKE", "--build", "$BUILD_ROOT", "--config", "Release",
                "--parallel", "1", "--target",
                *bundle.CAUSAL_BUILD_TARGETS],
            "environment": {
                "PATH": "/usr/bin:/bin",
                "HOME": "$CAUSAL_ROOT/.home",
                "TMPDIR": "$CAUSAL_ROOT/.tmp",
                "LANG": "C", "LC_ALL": "C", "TZ": "UTC",
                "SOURCE_DATE_EPOCH": "0",
                "CFLAGS": "", "CXXFLAGS": "", "LDFLAGS": "",
            },
            "toolchain": toolchain,
            "configure_log_size": 0,
            "configure_log_sha256": hashlib.sha256(b"").hexdigest(),
            "build_log_size": 0,
            "build_log_sha256": hashlib.sha256(b"").hexdigest(),
            "outputs": outputs,
        }
    if causal_output_drift:
        build_records["ibapi_on"]["causal_build"]["outputs"][0][
            "sha256"] = "f" * 64
    lineage = {
        "schema": bundle.SOURCE_BUILD_LINEAGE_SCHEMA,
        "variant": variant,
        "clean_source": clean_source,
        "source_manifest": {
            "path": verifier.CLEAN_SOURCE_MANIFEST,
            "schema": source_manifest["schema"],
            "bundle_class": source_manifest["bundle_class"],
            "root": source_manifest["root"],
            "version": source_manifest["version"],
            "git_head": source_manifest["git_head"],
            "file_count": source_manifest["file_count"],
            "files_sha256": source_manifest["files_sha256"],
            "bundle_sha256": clean_source["bundle_sha256"],
            "manifest_sha256": clean_source["manifest_sha256"],
        },
        "source_tree": {
            "root": source_manifest["root"],
            "file_count": source_manifest["file_count"],
            "files_sha256": source_manifest["files_sha256"],
            "manifest_sha256": clean_source["manifest_sha256"],
            "internal_manifest_path": ".hepta/source-bundle-manifest.json",
            "internal_manifest_mode": "0644",
            "git_metadata_present": False,
            "exact_file_closure": True,
        },
        "builds": build_records,
        "reviewed_build_sources": [
            source_index[path] for path in bundle.REVIEWED_BUILD_SOURCE_PATHS],
        "staged_sources": staged_sources,
        "staged_binaries": staged_binaries,
        "formal_ibapi": {
            "artifact": "hepta-ib-executiond",
            "build": "ibapi_on",
            "build_path": "bin/Release/hepta-ib-executiond",
            "size": 128,
            "sha256": formal_sha256,
            "digest_path": verifier.FORMAL_DIGEST,
            "elf_staged": False,
        },
        "boundary": {
            "source_tree_exact": True,
            "source_tree_git_metadata_present": False,
            "build_source_tree_shared": True,
            "repository_staged_sources_match_clean_source": True,
            "formal_ibapi_elf_staged": False,
            "paper_authorized": False,
            "live_enabled": False,
        },
    }
    lineage_bytes = bundle.canonical_json(lineage)
    lineage_sha256 = hashlib.sha256(lineage_bytes).hexdigest()
    write_payload(rootfs, verifier.SOURCE_BUILD_LINEAGE,
                  lineage_bytes, 0o444)

    runtime_records = [
        bundle.rootfs_file_record(rootfs, rootfs / relative)
        for relative in bundle.AGENT_OS_RUNTIME_GATE_PATHS
    ]
    if runtime_input_drift:
        runtime_records[0]["sha256"] = "f" * 64
    runtime_inputs = {
        "schema": bundle.AGENT_OS_RUNTIME_INPUT_SCHEMA,
        "profile": "native-vm-four-uid-watch-runtime-required",
        "inputs": runtime_records,
        "identities": {
            "gateway_uid": 2001,
            "simulator_execution_uid": 2002,
            "ib_execution_uid_reserved_not_started": 2003,
            "agent_uid": 2004,
        },
        "watch_tools": (
            list(bundle.AGENT_OS_WATCH_TOOLS[:-1]) +
            (["trade.place_order"] if runtime_tool_drift else
             [bundle.AGENT_OS_WATCH_TOOLS[-1]])),
        "read_probes": list(bundle.AGENT_OS_READ_PROBES),
        "lifecycle": {
            "service_restart_required": True,
            "socket_restart_required": True,
            "watch_revoke_required": True,
            "runtime_cleanup_required": True,
        },
        "runtime": {
            "inner_gate_path":
                "/" + bundle.AGENT_OS_RUNTIME_INNER_GATE.as_posix(),
            "runtime_preflight_executed": False,
            "runtime_preflight_required": True,
            "runtime_state_provisioned_by_bundle": False,
            "runtime_sentinel_staged": False,
            "runtime_artifacts_staged": False,
        },
        "paper_authorized": False,
        "live_enabled": False,
        "ib_adapter_runtime_authorized": False,
    }
    runtime_inputs_bytes = bundle.canonical_json(runtime_inputs)
    runtime_inputs_sha256 = hashlib.sha256(
        runtime_inputs_bytes).hexdigest()
    write_payload(rootfs, verifier.AGENT_OS_RUNTIME_INPUT_MANIFEST,
                  runtime_inputs_bytes, 0o444)
    agent_os = {
        "schema": bundle.AGENT_OS_INSTALLATION_SCHEMA,
        "profile": "static-installation-only",
        "preflight": {
            "path": "/" + bundle.AGENT_OS_INSTALLATION_PREFLIGHT.as_posix(),
            "arguments": ["--root", "/", "--installation-only"],
        },
        "files": [
            bundle.rootfs_file_record(rootfs, rootfs / relative)
            for relative in bundle.AGENT_OS_STATIC_PATHS
        ],
        "runtime": {
            "tool_socket_staged": False,
            "session_token_staged": False,
            "supervisor_socket_staged": False,
            "runtime_preflight_executed": agent_runtime_drift,
            "runtime_preflight_required": True,
            "runtime_gate_inputs_staged": True,
            "runtime_input_manifest_sha256": runtime_inputs_sha256,
            "runtime_state_provisioned_by_bundle": False,
            "runtime_sentinel_staged": False,
            "supervisor_credential":
                "unprovisioned-non-authorizing-placeholder",
        },
        "paper_authorized": False,
        "live_enabled": False,
    }
    agent_os_bytes = bundle.canonical_json(agent_os)
    agent_os_sha256 = hashlib.sha256(agent_os_bytes).hexdigest()
    write_payload(rootfs, verifier.AGENT_OS_INSTALLATION_MANIFEST,
                  agent_os_bytes, 0o444)
    provisioning = {
        "schema": bundle.PROVISIONING_SCHEMA,
        "variant": variant,
        "builds": build_records,
        "platform_policy_sha256": policy_sha256,
        "clean_source_provenance_sha256": clean_source_sha256,
        "clean_source": clean_source,
        "formal_ibapi_sha256": formal_sha256,
        "agent_os_installation_manifest_sha256": agent_os_sha256,
        "agent_os_runtime_input_manifest_sha256": runtime_inputs_sha256,
        "agent_os_installation_preflight_staged": True,
        "agent_os_runtime_gate_inputs_staged": True,
        "agent_os_runtime_preflight_required": True,
        "agent_os_runtime_artifacts_staged": False,
        "formal_ibapi_elf_staged": False,
        "instance_identity_staged": False,
        "paper_authorized": False,
        "live_enabled": False,
    }
    provisioning_bytes = bundle.canonical_json(provisioning)
    provisioning_sha256 = hashlib.sha256(provisioning_bytes).hexdigest()
    write_payload(rootfs, verifier.PROVISIONING_MANIFEST,
                  provisioning_bytes, 0o444)
    image = {
        "schema": bundle.IMAGE_SCHEMA,
        "variant": variant,
        "platform_policy_sha256": policy_sha256,
        "clean_source_provenance_sha256": clean_source_sha256,
        "clean_source": clean_source,
        "provisioning_manifest_sha256": provisioning_sha256,
        "agent_os_installation_manifest_sha256": agent_os_sha256,
        "agent_os_runtime_input_manifest_sha256": runtime_inputs_sha256,
        "agent_os_installation_preflight_staged": True,
        "agent_os_runtime_gate_inputs_staged": True,
        "agent_os_runtime_preflight_required": True,
        "agent_os_runtime_artifacts_staged": False,
        "files": bundle.rootfs_records(rootfs),
        "formal_ibapi_elf_staged": False,
        "instance_identity_staged": False,
        "paper_authorized": False,
        "live_enabled": False,
    }
    image_bytes = bundle.canonical_json(image)
    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    write_payload(rootfs, verifier.IMAGE_MANIFEST, image_bytes, 0o444)
    write_payload(rootfs, verifier.IMAGE_DIGEST,
                  (image_sha256 + "\n").encode("ascii"), 0o444)
    if extra:
        write_payload(rootfs, extra, b"extra\n", 0o444)
    bundle.normalize_rootfs_directories(rootfs)
    archive = directory / f"hepta-native-vm-{variant}.rootfs.tar"
    bundle.deterministic_tar(rootfs, archive)
    archive_record = shared.stable_file(archive)
    report = {
        "schema": bundle.SCHEMA,
        "passed": True,
        "variant": variant,
        "platform_policy": json.loads(policy),
        "platform_policy_sha256": policy_sha256,
        "clean_source_provenance_sha256": clean_source_sha256,
        "clean_source": clean_source,
        "clean_source_manifest_sha256": clean_source["manifest_sha256"],
        "source_build_lineage_sha256": lineage_sha256,
        "provisioning_manifest_sha256": provisioning_sha256,
        "agent_os_installation_manifest_sha256": agent_os_sha256,
        "agent_os_runtime_input_manifest_sha256": runtime_inputs_sha256,
        "agent_os_runtime_input_file_count":
            len(bundle.AGENT_OS_RUNTIME_GATE_PATHS),
        "agent_os_installation_file_count":
            len(bundle.AGENT_OS_STATIC_PATHS),
        "vm_image_manifest_sha256": image_sha256,
        "rootfs_file_count": len(bundle.rootfs_records(rootfs)),
        "archive": archive_record,
        "boundary": {
            "formal_ibapi_elf_staged": False,
            "instance_identity_staged": False,
            "agent_os_installation_preflight_staged": True,
            "agent_os_runtime_preflight_executed": False,
            "agent_os_runtime_preflight_required": True,
            "agent_os_runtime_gate_inputs_staged": True,
            "agent_os_runtime_state_provisioned": False,
            "agent_os_runtime_sentinel_staged": False,
            "agent_os_runtime_artifacts_staged": False,
            "paper_authorized": False,
            "live_enabled": False,
            "broker_connections": 0,
            "orders": 0,
        },
    }
    report_path = directory / f"hepta-native-vm-{variant}.bundle.json"
    shared.write_private(report_path, (
        json.dumps(report, sort_keys=True) + "\n").encode("utf-8"), 0o600)
    return argparse.Namespace(
        bundle_report=report_path, archive=archive,
        clean_source_bundle=source_bundle_path,
        clean_source_manifest=source_manifest_path,
        clean_source_result=clean_source)


class NativeVmBundleVerificationFixtureTests(unittest.TestCase):
    @staticmethod
    def verify_fixture(args: argparse.Namespace) -> dict[str, object]:
        with mock.patch.object(
                verifier.clean_source, "verify_bundle",
                return_value=args.clean_source_result):
            return verifier.verify(args)

    def test_exact_bundle_passes(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-fixture-") as temporary:
            args = fixture_bundle(Path(temporary))
            result = self.verify_fixture(args)
            self.assertTrue(result["passed"])
            self.assertEqual(
                result["schema"],
                "hepta.execution-native-vm-bundle-verification.v7")
            self.assertFalse(result["boundary"]["paper_authorized"])

    def test_unmanifested_archive_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-fixture-") as temporary:
            args = fixture_bundle(Path(temporary), extra="opt/hepta-extra")
            with self.assertRaisesRegex(
                    verifier.VerificationError, "exactly close"):
                self.verify_fixture(args)

    def test_formal_ib_elf_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-fixture-") as temporary:
            args = fixture_bundle(
                Path(temporary), extra="usr/libexec/hepta-ib-executiond-formal")
            with self.assertRaisesRegex(
                    verifier.VerificationError, "file contract"):
                self.verify_fixture(args)

    def test_authorization_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-fixture-") as temporary:
            args = fixture_bundle(Path(temporary))
            report = json.loads(args.bundle_report.read_text(encoding="utf-8"))
            report["boundary"]["paper_authorized"] = True
            args.bundle_report.unlink()
            shared.write_private(args.bundle_report, (
                json.dumps(report, sort_keys=True) + "\n").encode("utf-8"), 0o600)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "report contract"):
                self.verify_fixture(args)

    def test_runtime_token_in_static_bundle_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-fixture-") as temporary:
            args = fixture_bundle(
                Path(temporary), extra="run/hepta-agent/session.token")
            with self.assertRaisesRegex(
                    verifier.VerificationError, "file contract"):
                self.verify_fixture(args)

    def test_forged_runtime_preflight_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-fixture-") as temporary:
            args = fixture_bundle(
                Path(temporary), agent_runtime_drift=True)
            with self.assertRaisesRegex(
                verifier.VerificationError,
                "Agent OS installation manifest"):
                self.verify_fixture(args)

    def test_mutation_tool_in_runtime_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-fixture-") as temporary:
            args = fixture_bundle(
                Path(temporary), runtime_tool_drift=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "runtime input manifest"):
                self.verify_fixture(args)

    def test_runtime_input_digest_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-fixture-") as temporary:
            args = fixture_bundle(
                Path(temporary), runtime_input_drift=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "runtime input closure"):
                self.verify_fixture(args)

    def test_external_manifest_cannot_be_cross_bundled(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-source-cross-") as temporary:
            args = fixture_bundle(Path(temporary))
            manifest = json.loads(
                args.clean_source_manifest.read_text(encoding="utf-8"))
            manifest["git_head"] = "b" * 40
            args.clean_source_manifest.unlink()
            shared.write_private(
                args.clean_source_manifest,
                (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
                0o600)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "embedded and external"):
                self.verify_fixture(args)

    def test_staged_current_bytes_cannot_claim_old_source(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-stage-cross-") as temporary:
            args = fixture_bundle(
                Path(temporary), staged_source_drift=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "staged source bytes"):
                self.verify_fixture(args)

    def test_ib_builds_cannot_cross_source_trees(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-build-cross-") as temporary:
            args = fixture_bundle(
                Path(temporary), build_source_drift=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError,
                    "CMake path lineage|one exact source tree"):
                self.verify_fixture(args)

    def test_build_evidence_digest_cannot_be_forged(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-build-evidence-") as temporary:
            args = fixture_bundle(
                Path(temporary), build_evidence_drift=True)
            with self.assertRaisesRegex(
                verifier.VerificationError, "build evidence"):
                self.verify_fixture(args)

    def test_fresh_causal_output_cannot_diverge_from_staged_elf(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-causal-output-") as temporary:
            args = fixture_bundle(
                Path(temporary), causal_output_drift=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError,
                    "fresh causal build output|exact fresh causal"):
                self.verify_fixture(args)

    def test_ibapi_sdk_same_path_byte_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-ibapi-bytes-") as temporary:
            args = fixture_bundle(
                Path(temporary), ibapi_bytes_drift=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "build evidence"):
                self.verify_fixture(args)

    def test_ibapi_compile_source_missing_from_manifest_fails_closed(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-ibapi-missing-") as temporary:
            args = fixture_bundle(
                Path(temporary), ibapi_manifest_missing=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "absent from SDK manifest"):
                self.verify_fixture(args)

    def test_ibapi_manifest_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-ibapi-tamper-") as temporary:
            args = fixture_bundle(
                Path(temporary), ibapi_manifest_tamper=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "SDK source aggregate"):
                self.verify_fixture(args)

    def test_path_only_legacy_ibapi_lineage_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-ibapi-legacy-") as temporary:
            args = fixture_bundle(
                Path(temporary), ibapi_path_only_legacy=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError,
                    "SDK source manifest contract"):
                self.verify_fixture(args)

    def test_ibapi_off_requires_empty_source_binding(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-ibapi-off-") as temporary:
            args = fixture_bundle(
                Path(temporary), ibapi_off_nonempty=True)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "IBAPI-off"):
                self.verify_fixture(args)

    def test_old_v5_bundle_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-native-verifier-old-v5-") as temporary:
            args = fixture_bundle(Path(temporary))
            report = json.loads(
                args.bundle_report.read_text(encoding="utf-8"))
            report["schema"] = "hepta.execution-native-vm-bundle.v5"
            args.bundle_report.unlink()
            shared.write_private(
                args.bundle_report,
                (json.dumps(report, sort_keys=True) + "\n").encode(), 0o600)
            with self.assertRaisesRegex(
                    verifier.VerificationError, "report contract"):
                self.verify_fixture(args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
