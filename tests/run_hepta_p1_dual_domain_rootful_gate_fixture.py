#!/usr/bin/env python3

"""Rootless fake-Docker contracts for the P1 dual-domain runner."""

from __future__ import annotations

import copy
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve(strict=True).parents[1]
MODULE_PATH = ROOT / "scripts/run_hepta_p1_dual_domain_rootful_gate.py"
SPEC = importlib.util.spec_from_file_location(
    "run_hepta_p1_dual_domain_rootful_gate_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import P1 dual-domain rootful runner")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)

INNER_PATH = (
    ROOT / "tests/p1_dual_domain_rootful_systemd/"
    "hepta_p1_dual_domain_inner_gate.py")
INNER_SPEC = importlib.util.spec_from_file_location(
    "hepta_p1_dual_domain_inner_gate_under_test", INNER_PATH)
if INNER_SPEC is None or INNER_SPEC.loader is None:
    raise RuntimeError("cannot import P1 dual-domain inner gate")
INNER = importlib.util.module_from_spec(INNER_SPEC)
sys.modules[INNER_SPEC.name] = INNER
INNER_SPEC.loader.exec_module(INNER)


RUN_ID = "d" * 32
COMMIT = "c" * 40
BASE_ID = "sha256:" + "b" * 64
IMAGE_ID = "sha256:" + "e" * 64
CONTAINER_ID = "f" * 64
BUILDKIT_ID = "sha256:" + "3" * 64
BUILDER_CONTAINER_ID = "4" * 64
BOOT_ID = "01234567-89ab-cdef-8123-456789abcdef"
FIXTURE_NOW_MS = int(time.time() * 1000)


def pinned() -> str:
    return "registry.example/hepta/systemd@sha256:" + "a" * 64


def pinned_buildkit() -> str:
    return "registry.example/hepta/buildkit@sha256:" + "5" * 64


def base_labels() -> dict[str, str]:
    return {
        "io.hepta.rootful-systemd-base.offline-ready": "true",
        "io.hepta.rootful-systemd-base.version": "1",
    }


def environment_observations() -> dict[str, object]:
    buildx_path = "/usr/libexec/docker/cli-plugins/docker-buildx"
    return {
        "base_image": {
            "image_id": "sha256:" + "b" * 64,
            "repo_digest": pinned(), "repo_digests": [pinned()],
            "labels_sha256":
                RUNNER.ROOT_REVIEW._canonical_object_sha256(base_labels()),
            "os": "linux", "architecture": "amd64",
            "declared_volumes": 0, "onbuild_instructions": 0,
        },
        "isolated_builder": {
            "image_id": "sha256:" + "3" * 64,
            "repo_digest": pinned_buildkit(),
            "repo_digests": [pinned_buildkit()],
            "config_sha256": "sha256:" + "4" * 64,
            "os": "linux", "architecture": "amd64",
            "entrypoint": ["/usr/bin/buildkitd"],
            "buildkit_binary_path": "/usr/bin/buildkitd",
            "buildkit_binary_sha256": "sha256:" + "5" * 64,
            "buildkit_version": "v0.24.0", "buildx_path": buildx_path,
            "buildx_path_sha256": RUNNER.ROOT_REVIEW.sha256_bytes(
                buildx_path.encode("utf-8")),
            "buildx_binary_sha256": "sha256:" + "6" * 64,
            "buildx_version": "0.30.1",
            "docker_server_version": "29.1.3",
            "docker_server_api_version": "1.52",
            "docker_server_git_commit": "fixture-commit",
        },
        "apparmor": {
            "profile": "hepta-systemd-gate", "mode": "enforce",
            "attach": "hepta-systemd-gate", "learning_count": 0,
            "policy_source_sha256": "sha256:" + "7" * 64,
            "profile_sha256": "sha256:" + "8" * 64,
            "raw_sha256": "sha256:" + "9" * 64, "raw_abi": "v8",
            "raw_data_id": "71", "namespace_name": "root",
            "namespace_level": 0, "namespace_stacked": False,
            "profile_inventory_sha256": "sha256:" + "a" * 64,
        },
        "docker_namespace": {
            "docker_daemon_id": "FIXTURE:DAEMON",
            "docker_daemon_pid": 4242,
            "docker_daemon_start_time_ticks": 987654,
            "docker_daemon_exe_sha256": "sha256:" + "b" * 64,
            "host_boot_id": BOOT_ID, "host_namespace_name": "root",
            "host_namespace_level": 0, "host_namespace_stacked": False,
            "daemon_namespace_name": "root", "daemon_namespace_level": 0,
            "daemon_namespace_stacked": False,
            "daemon_apparmor_current": "unconfined",
            "self_user_namespace_inode": 4026531837,
            "daemon_user_namespace_inode": 4026531837,
        },
    }


def environment_trust_bindings() -> dict[str, dict[str, str]]:
    return {
        key: {"path": path, "sha256": "sha256:" + digit * 64}
        for (key, path), digit in zip(
            RUNNER.ROOT_REVIEW.TRUST_BINDING_PATHS.items(), "cdef7")
    }


def buildkit_config() -> dict[str, object]:
    return {
        "OnBuild": None,
        "Volumes": None,
        "ExposedPorts": None,
        "Entrypoint": ["/usr/bin/buildkitd"],
        "Labels": {},
    }


def certification_provenance() -> dict[str, RUNNER.RootProvenanceDocument]:
    validity = {
        "issued_at_ms": FIXTURE_NOW_MS - 60_000,
        "expires_at_ms": FIXTURE_NOW_MS + 60 * 60 * 1000,
    }
    bodies: dict[str, dict[str, object]] = {
        "base": {
            "schema": RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
            "decision": "GO",
            **validity,
            "image_id": BASE_ID,
            "repo_digest": pinned(),
            "labels_sha256": RUNNER.canonical_object_sha256(base_labels()),
        },
        "builder": {
            "schema": RUNNER.REVIEWED_BUILDER_PROVENANCE_SCHEMA,
            "decision": "GO",
            **validity,
            "image_id": BUILDKIT_ID,
            "repo_digest": pinned_buildkit(),
            "config_sha256": RUNNER.canonical_object_sha256(buildkit_config()),
            "buildkit_version": "v0.26.2",
            "buildx_version": "0.30.1",
            "buildx_binary_sha256": "sha256:" + "6" * 64,
            "docker_server_version": "29.1.3",
            "docker_server_api_version": "1.52",
            "docker_server_git_commit": "fixture",
        },
        "apparmor": {
            "schema": RUNNER.REVIEWED_APPARMOR_PROVENANCE_SCHEMA,
            "decision": "GO",
            **validity,
            "profile": RUNNER.APPARMOR_PROFILE,
            "policy_source_sha256": "sha256:" + "7" * 64,
            "profile_sha256": "sha256:" + "8" * 64,
            "raw_sha256": "sha256:" + "9" * 64,
            "raw_abi": "v8",
        },
        "docker_namespace": {
            "schema": RUNNER.REVIEWED_DOCKER_NAMESPACE_PROVENANCE_SCHEMA,
            "decision": "GO",
            **validity,
            "docker_daemon_id": "fixture-daemon",
            "docker_daemon_pid": 1234,
            "docker_daemon_start_time_ticks": 5678,
            "host_boot_id": BOOT_ID,
            "host_namespace_name": "root",
            "host_namespace_level": 0,
            "host_namespace_stacked": False,
            "daemon_namespace_name": "root",
            "daemon_namespace_level": 0,
            "daemon_namespace_stacked": False,
        },
    }
    return {
        kind: RUNNER.RootProvenanceDocument(
            kind=kind,
            path=Path(
                "/root/" +
                ("docker-namespace" if kind == "docker_namespace" else kind) +
                ".json"),
            document_sha256=RUNNER.canonical_sha256(body),
            body=body,
            metadata=(1, index, 0o100400, 1, 0, 0, 100, 10, 10),
        )
        for index, (kind, body) in enumerate(bodies.items(), start=1)
    }


def certification_request() -> RUNNER.CertificationRequest:
    provenance = certification_provenance()
    return RUNNER.CertificationRequest(
        buildkit_image=pinned_buildkit(),
        buildx_binary_sha256="sha256:" + "6" * 64,
        reviewed_base_path=Path("/root/base.json"),
        reviewed_base_sha256=provenance["base"].document_sha256,
        reviewed_builder_path=Path("/root/builder.json"),
        reviewed_builder_sha256=provenance["builder"].document_sha256,
        reviewed_apparmor_path=Path("/root/apparmor.json"),
        reviewed_apparmor_sha256=provenance["apparmor"].document_sha256,
        reviewed_docker_namespace_path=Path("/root/docker-namespace.json"),
        reviewed_docker_namespace_sha256=
            provenance["docker_namespace"].document_sha256,
        environment_review=RUNNER.ROOT_REVIEW.ReviewClosureInputs(
            Path("/root/review-closure.v1.json"),
            Path("/root/review-request.v1.json"),
            Path("/root/review-authorization.v1.json"), Path("/root")),
    )


def environment_review_record() -> dict[str, object]:
    provenance = certification_provenance()
    file_record = {
        "path": "/root/input", "file_sha256": "sha256:" + "1" * 64,
        "mode": "0400", "uid": 0, "gid": 0,
        "identity_sha256": "sha256:" + "2" * 64,
    }
    trust = environment_trust_bindings()
    reviewer_id = "fixture-reviewer"
    fingerprint = RUNNER.ROOT_REVIEW.build_environment_fingerprint(
        source_commit=COMMIT,
        verifier_file_sha256=trust["producer"]["sha256"],
        verifier_source_file_sha256=trust["producer"]["sha256"],
        review_authority=RUNNER.ROOT_REVIEW.REVIEW_AUTHORITY,
        reviewer_id=reviewer_id,
        observations=environment_observations(), trust_bindings=trust)
    record = {
        "schema": RUNNER.ROOT_REVIEW.SCHEMA,
        "status": "VERIFIED_EXTERNALLY_SIGNED_REVIEW_CLOSURE",
        "verified_at_ms": FIXTURE_NOW_MS,
        "expires_at_ms": FIXTURE_NOW_MS + 60 * 60 * 1000,
        "source_commit": COMMIT,
        "base_image_reference": pinned(),
        "buildkit_image_reference": pinned_buildkit(),
        "output_directory": "/root",
        "verifier": {
            **file_record,
            "path": str(RUNNER.ROOT_REVIEW.INSTALLED_VERIFIER),
            "file_sha256": trust["producer"]["sha256"],
            "mode": "0755", "source_path": str(
                ROOT / RUNNER.ROOT_REVIEW.VERIFIER_SOURCE_RELATIVE),
            "source_file_sha256": trust["producer"]["sha256"],
            "source_commit": COMMIT,
        },
        "closure": {
            **file_record, "path": "/root/review-closure.v1.json",
            "closure_sha256": "sha256:" + "4" * 64,
            "review_authority": RUNNER.ROOT_REVIEW.REVIEW_AUTHORITY,
            "reviewer_id": reviewer_id,
        },
        "request": {
            **file_record, "path": "/root/review-request.v1.json",
            "mode": "0600", "request_sha256": "sha256:" + "5" * 64,
            "nonce": "6" * 64,
        },
        "authorization": {
            **file_record, "path": "/root/review-authorization.v1.json",
            "mode": "0600",
            "signed_payload_sha256": "sha256:" + "7" * 64,
            "signature_sha256": "sha256:" + "8" * 64,
            "review_authority": RUNNER.ROOT_REVIEW.REVIEW_AUTHORITY,
            "reviewer_id": reviewer_id,
        },
        "outputs": {
            kind: {
                **file_record,
                "path": "/root/" + RUNNER.ROOT_REVIEW.OUTPUT_FILENAMES[kind],
                "file_sha256": provenance[kind].document_sha256,
                "schema": RUNNER.ROOT_REVIEW.OUTPUT_SCHEMAS[kind],
            }
            for kind in RUNNER.ROOT_REVIEW.OUTPUT_FILENAMES
        },
        "invocation": {
            "argv_sha256": "sha256:" + "9" * 64,
            "stdout_sha256": "sha256:" + "a" * 64,
            "returncode": 0, "duration_ms": 1,
            "exact_success_output": True, "no_shell": True,
        },
        "environment_fingerprint": fingerprint,
        "reopened_after_invocation": True,
        "reopened_at_gate_end": True,
        **RUNNER.ROOT_REVIEW.FALSE_AUTHORITY,
    }
    return RUNNER.ROOT_REVIEW.validate_verification_record(
        record, now_ms=FIXTURE_NOW_MS)


class FakeReviewSession:
    def __init__(self) -> None:
        self.record = environment_review_record()

    def output_reference(self, kind: str) -> dict[str, object]:
        return {
            "path": self.record["outputs"][kind]["path"],
            "file_sha256": self.record["outputs"][kind]["file_sha256"],
        }

    def reopen_at_gate_end(self) -> None:
        return None

    def report_record(self) -> dict[str, object]:
        return copy.deepcopy(self.record)


def apparmor_record(provenance: dict[str, RUNNER.RootProvenanceDocument]):
    return {
        "profile": RUNNER.APPARMOR_PROFILE,
        "mode": "enforce",
        "attach": RUNNER.APPARMOR_PROFILE,
        "learning_count": 0,
        "profile_sha256": "sha256:" + "8" * 64,
        "raw_sha256": "sha256:" + "9" * 64,
        "raw_abi": "v8",
        "raw_data_id": "1",
        "profile_inventory_count": 1,
        "profile_inventory_sha256": "sha256:" + "a" * 64,
        "namespace": {"name": "root", "level": 0, "stacked": False},
        "reviewed_provenance": provenance["apparmor"].report_record(),
    }


def docker_namespace_record(
        provenance: dict[str, RUNNER.RootProvenanceDocument]):
    return {
        "docker_daemon_id": "fixture-daemon",
        "docker_daemon_pid": 1234,
        "docker_daemon_start_time_ticks": 5678,
        "docker_daemon_comm": "dockerd",
        "docker_daemon_process_inode": 42,
        "host_boot_id": BOOT_ID,
        "host_namespace": {"name": "root", "level": 0, "stacked": False},
        "daemon_namespace": {"name": "root", "level": 0, "stacked": False},
        "same_apparmor_namespace_attested": True,
        "reviewed_provenance": provenance["docker_namespace"].report_record(),
    }


def buildx_toolchain_record() -> dict[str, object]:
    return {
        "buildx_path_sha256": "sha256:" + "b" * 64,
        "buildx_version": "0.30.1",
        "buildx_binary_sha256": "sha256:" + "6" * 64,
        "docker_server_version": "29.1.3",
        "docker_server_api_version": "1.52",
        "docker_server_git_commit": "fixture",
        "reviewed": True,
    }


def docker_socket_record() -> dict[str, object]:
    return {
        "device": 41,
        "inode": 42,
        "mode": "0660",
        "uid": 0,
        "gid": 999,
        "owner_root": True,
        "world_writable": False,
    }


def valid_fault(plane: str, domain: str, generation: int) -> dict[str, object]:
    return {
        "plane": plane,
        "domain_id": domain,
        "before_pid": 1000 + generation,
        "after_pid": 2000 + generation,
        "before_generation": generation,
        "after_generation": generation + 1,
        "tombstone_generation": generation,
        "restart_observed": True,
        "stale_generation_rejected": True,
    }


def valid_inner() -> dict[str, object]:
    return {
        "schema": RUNNER.INNER_SCHEMA,
        "passed": True,
        "run_id": RUN_ID,
        "checks": {name: True for name in sorted(RUNNER.EXPECTED_CHECKS)},
        "boot": {
            "boot_id": BOOT_ID,
            "pid1_cgroup": "0::/",
            "systemd": "systemd 252 (252.38-1~deb12u1)",
        },
        "identities": copy.deepcopy(RUNNER.EXPECTED_IDENTITIES),
        "faults": {
            name: valid_fault(plane, domain, index + 1)
            for index, (name, (plane, domain)) in enumerate(
                RUNNER.EXPECTED_FAULTS.items())
        },
        "inventory": {
            "immutable_file_count": 1234,
            "immutable_file_inventory_sha256": "1" * 64,
            "inert_daemon_sha256": "2" * 64,
            "forbidden_ib_api_payloads": 0,
            "protected_broker_sockets": 0,
            "network_interfaces": ["lo"],
        },
        "boundary": copy.deepcopy(RUNNER.EXPECTED_BOUNDARY),
    }


def image_record() -> dict[str, object]:
    return {
        "Id": IMAGE_ID,
        "Config": {"Labels": {
            "io.hepta.purpose": RUNNER.PURPOSE,
            RUNNER.RUN_LABEL_KEY: RUN_ID,
        }},
    }


def valid_container_record() -> dict[str, object]:
    return {
        "Id": CONTAINER_ID,
        "Name": "/hepta-p1-dual-domain-" + RUN_ID,
        "Image": IMAGE_ID,
        "AppArmorProfile": RUNNER.APPARMOR_PROFILE,
        "Config": {
            "Image": IMAGE_ID,
            "Hostname": "hepta-p1-dual-domain-systemd",
            "User": "0:0",
            "WorkingDir": "/",
            "Entrypoint": [
                "/usr/local/libexec/"
                "hepta-p1-dual-domain-systemd-entrypoint"],
            "Cmd": [],
            "ExposedPorts": {},
            "Volumes": {},
            "StopSignal": "SIGRTMIN+3",
            "Labels": {
                "io.hepta.purpose": RUNNER.PURPOSE,
                RUNNER.RUN_LABEL_KEY: RUN_ID,
            },
            "Env": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "HEPTA_P1_DUAL_DOMAIN_DISPOSABLE=1",
                "HEPTA_P1_DUAL_DOMAIN_RUN_ID=" + RUN_ID,
            ],
        },
        "HostConfig": {
            "Privileged": False,
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "CgroupnsMode": "private",
            "IpcMode": "private",
            "SecurityOpt": [
                "no-new-privileges",
                "apparmor=" + RUNNER.APPARMOR_PROFILE,
            ],
            "PidsLimit": 256,
            "Memory": 768 * 1024 * 1024,
            "NanoCpus": 2_000_000_000,
            "PublishAllPorts": False,
            "PortBindings": {},
            "Binds": [],
            "Devices": [],
            "DeviceRequests": [],
            "DeviceCgroupRules": [],
            "Links": [],
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "Tmpfs": copy.deepcopy(RUNNER.RUNTIME_TMPFS),
            "CapDrop": ["ALL"],
            "CapAdd": [
                "CAP_" + item for item in RUNNER.RUNTIME_CAPABILITIES],
        },
        "Mounts": [
            {"Type": "tmpfs", "Destination": path}
            for path in RUNNER.RUNTIME_TMPFS
        ],
    }


def buildkit_image_record() -> dict[str, object]:
    return {
        "Id": BUILDKIT_ID,
        "RepoDigests": [pinned_buildkit()],
        "Os": "linux",
        "Architecture": "amd64",
        "Config": buildkit_config(),
    }


def builder_volume_record() -> dict[str, object]:
    names = RUNNER.isolated_builder_names(RUN_ID)
    labels = RUNNER.builder_labels(
        RUN_ID, names["builder"], BUILDKIT_ID,
        RUNNER.BUILDER_STATE_ROLE)
    return {
        "Name": names["volume"],
        "Driver": "local",
        "Scope": "local",
        "Labels": labels,
        "Options": {},
        "Mountpoint": "/var/lib/docker/volumes/fixture/_data",
    }


def builder_container_record(running: bool) -> dict[str, object]:
    names = RUNNER.isolated_builder_names(RUN_ID)
    ownership = RUNNER.builder_labels(
        RUN_ID, names["builder"], BUILDKIT_ID,
        RUNNER.BUILDER_DAEMON_ROLE)
    return {
        "Id": BUILDER_CONTAINER_ID,
        "Name": "/" + names["container"],
        "Image": BUILDKIT_ID,
        "Config": {
            "Image": BUILDKIT_ID.removeprefix("sha256:"),
            "Labels": ownership,
        },
        "State": {"Running": running},
        "HostConfig": {
            "NetworkMode": "none",
            "Privileged": True,
            "Init": True,
            "AutoRemove": False,
            "ReadonlyRootfs": False,
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "Binds": [],
            "Tmpfs": {},
            "VolumesFrom": [],
            "Devices": [],
            "DeviceRequests": [],
            "PortBindings": {},
            "PublishAllPorts": False,
        },
        "Mounts": [{
            "Type": "volume",
            "Name": names["volume"],
            "Destination": RUNNER.BUILDKIT_STATE_DIRECTORY,
            "Driver": "local",
            "RW": True,
        }],
    }


class FakeDocker:
    """A command-level fake; production has no switch that selects it."""

    def __init__(
            self, *, dirty: bool = False,
            mutate_inner: bool = False,
            unconfined_container: bool = False,
            leave_builder_residue: bool = False,
            leave_target_image_residue: bool = False) -> None:
        self.dirty = dirty
        self.mutate_inner = mutate_inner
        self.unconfined_container = unconfined_container
        self.leave_builder_residue = leave_builder_residue
        self.leave_target_image_residue = leave_target_image_residue
        self.calls: list[list[str]] = []
        self.builder_container_present = False
        self.builder_volume_present = False
        self.builder_running = False
        self.builder_registered = False
        self.target_image_present = False
        self.target_container_present = False

    @staticmethod
    def completed(
            arguments: list[str], output: str = "", returncode: int = 0,
            *, check: bool = True) -> subprocess.CompletedProcess[str]:
        if check and returncode != 0:
            raise RUNNER.GateError("fake command failure")
        return subprocess.CompletedProcess(
            arguments, returncode, output, None)

    def __call__(
            self, arguments: list[str], *, timeout: int = 120,
            check: bool = True) -> subprocess.CompletedProcess[str]:
        del timeout
        self.calls.append(list(arguments))
        if arguments[0] == "git":
            if "rev-parse" in arguments:
                return self.completed(arguments, COMMIT + "\n", check=check)
            if "status" in arguments:
                output = " M tracked-input\n" if self.dirty else ""
                return self.completed(arguments, output, check=check)
            if "ls-files" in arguments:
                return self.completed(arguments, "input\n", check=check)
        if arguments == ["docker", "--version"]:
            return self.completed(
                arguments, "Docker version 29.1.3, build fixture\n", check=check)
        if arguments[0] != "docker" or len(arguments) < 5:
            return self.completed(arguments, returncode=127, check=check)
        docker = arguments[4:]
        if docker[:2] == ["version", "--format"]:
            return self.completed(arguments, json.dumps({
                "Version": "29.1.3",
                "ApiVersion": "1.52",
            }) + "\n", check=check)
        if docker[:2] == ["info", "--format"]:
            return self.completed(arguments, json.dumps({
                "CgroupVersion": "2",
                "Architecture": "x86_64",
                "OperatingSystem": "Fixture Linux",
                "DefaultRuntime": "runc",
                "SecurityOptions": ["name=seccomp", "name=apparmor"],
                "CgroupDriver": "systemd",
            }) + "\n", check=check)
        if docker[:3] == ["image", "inspect", pinned()]:
            return self.completed(arguments, json.dumps([{
                "Id": BASE_ID,
                "RepoDigests": [pinned()],
                "Os": "linux",
                "Architecture": "amd64",
                "Config": {
                    "OnBuild": None,
                    "Volumes": None,
                    "Labels": base_labels(),
                },
            }]) + "\n", check=check)
        if (
                docker[:3] == ["image", "inspect", pinned_buildkit()] or
                docker[:3] == ["image", "inspect", BUILDKIT_ID]):
            return self.completed(
                arguments, json.dumps([buildkit_image_record()]) + "\n",
                check=check)
        if docker[:2] == ["volume", "create"]:
            self.builder_volume_present = True
            return self.completed(
                arguments, RUNNER.isolated_builder_names(RUN_ID)["volume"] +
                "\n", check=check)
        if docker[:2] == ["volume", "inspect"]:
            if not self.builder_volume_present:
                return self.completed(
                    arguments, "no such volume\n", returncode=1, check=check)
            return self.completed(
                arguments, json.dumps([builder_volume_record()]) + "\n",
                check=check)
        if docker[:2] == ["volume", "rm"]:
            self.builder_volume_present = False
            return self.completed(arguments, docker[-1] + "\n", check=check)
        if docker[:2] == ["container", "create"]:
            self.builder_container_present = True
            self.builder_running = False
            return self.completed(
                arguments, BUILDER_CONTAINER_ID + "\n", check=check)
        if docker[:2] == ["buildx", "create"]:
            self.builder_registered = True
            metadata = RUNNER.builder_metadata_path(
                RUNNER.isolated_builder_names(RUN_ID)["builder"])
            metadata.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_text("{}\n", encoding="ascii")
            metadata.chmod(0o600)
            return self.completed(
                arguments, RUNNER.isolated_builder_names(RUN_ID)["builder"] +
                "\n", check=check)
        if docker[:2] == ["container", "start"]:
            self.builder_running = True
            return self.completed(
                arguments, BUILDER_CONTAINER_ID + "\n", check=check)
        if docker[:2] == ["buildx", "ls"]:
            names = RUNNER.isolated_builder_names(RUN_ID)
            return self.completed(arguments, json.dumps({
                "Name": names["builder"],
                "Driver": "docker-container",
                "Nodes": [{
                    "Name": names["node"],
                    "Status": "running",
                    "Version": "v0.26.2",
                }],
            }) + "\n", check=check)
        if docker[:2] == ["buildx", "rm"]:
            self.builder_registered = False
            RUNNER.builder_metadata_path(
                RUNNER.isolated_builder_names(RUN_ID)["builder"]).unlink(
                    missing_ok=True)
            self.builder_container_present = False
            if not self.leave_builder_residue:
                self.builder_volume_present = False
            self.builder_running = False
            return self.completed(arguments, "\n", check=check)
        if docker[:2] == ["buildx", "build"]:
            iidfile = Path(docker[docker.index("--iidfile") + 1])
            iidfile.write_text(IMAGE_ID + "\n", encoding="ascii")
            self.target_image_present = True
            return self.completed(arguments, "fake isolated build\n", check=check)
        if docker and docker[0] == "build":
            iidfile = Path(docker[docker.index("--iidfile") + 1])
            iidfile.write_text(IMAGE_ID + "\n", encoding="ascii")
            self.target_image_present = True
            return self.completed(arguments, "fake build\n", check=check)
        if docker[:2] == ["image", "inspect"]:
            if not self.target_image_present:
                return self.completed(
                    arguments, "no such image\n", returncode=1, check=check)
            return self.completed(
                arguments, json.dumps([image_record()]) + "\n", check=check)
        if docker and docker[0] == "create":
            self.target_container_present = True
            return self.completed(arguments, CONTAINER_ID + "\n", check=check)
        if docker[:2] == ["container", "inspect"]:
            target = docker[2]
            names = RUNNER.isolated_builder_names(RUN_ID)
            if target in {BUILDER_CONTAINER_ID, names["container"]}:
                if not self.builder_container_present:
                    return self.completed(
                        arguments, "no such container\n", returncode=1,
                        check=check)
                return self.completed(arguments, json.dumps([
                    builder_container_record(self.builder_running)
                ]) + "\n", check=check)
            if not self.target_container_present:
                return self.completed(
                    arguments, "no such container\n", returncode=1,
                    check=check)
            record = valid_container_record()
            if self.unconfined_container:
                record["HostConfig"]["SecurityOpt"] = [
                    "no-new-privileges", "apparmor=unconfined"]
            return self.completed(
                arguments, json.dumps([record]) + "\n", check=check)
        if docker and docker[0] == "start":
            return self.completed(arguments, CONTAINER_ID + "\n", check=check)
        if docker[:3] == ["exec", CONTAINER_ID, "systemctl"]:
            return self.completed(arguments, "252\n", check=check)
        if docker[:3] == ["exec", CONTAINER_ID, "python3"]:
            inner = valid_inner()
            if self.mutate_inner:
                inner["boundary"]["paper_orders"] = 1
            output = RUNNER.INNER_MARKER + json.dumps(
                inner, sort_keys=True, separators=(",", ":")) + "\n"
            return self.completed(arguments, output, check=check)
        if docker[:4] == ["exec", CONTAINER_ID, "cat",
                          "/proc/sys/kernel/random/boot_id"]:
            return self.completed(arguments, BOOT_ID + "\n", check=check)
        if docker[:4] == ["exec", CONTAINER_ID, "cat", "/proc/1/cgroup"]:
            return self.completed(arguments, "0::/\n", check=check)
        if docker and docker[0] in {"stop", "rm", "logs"}:
            if docker[0] == "rm":
                self.target_container_present = False
            return self.completed(arguments, "\n", check=check)
        if docker[:2] == ["container", "rm"]:
            self.builder_container_present = False
            self.builder_running = False
            return self.completed(arguments, BUILDER_CONTAINER_ID + "\n", check=check)
        if docker[:2] == ["image", "rm"]:
            if not self.leave_target_image_residue:
                self.target_image_present = False
            return self.completed(arguments, "\n", check=check)
        return self.completed(arguments, returncode=127, check=check)


class P1DualDomainRootfulRunnerFixture(unittest.TestCase):
    def tearDown(self) -> None:
        RUNNER.cleanup_docker_config()

    def test_digest_and_commit_pins_are_exact(self) -> None:
        self.assertEqual(RUNNER.require_pinned_image(pinned()), pinned())
        self.assertEqual(RUNNER.require_expected_commit(COMMIT), COMMIT)
        for value in (
                "debian:bookworm",
                "debian@sha256:" + "a" * 63,
                "debian@sha256:" + "A" * 64,
                "sha256:" + "a" * 64):
            with self.subTest(value=value), self.assertRaises(RUNNER.GateError):
                RUNNER.require_pinned_image(value)
        for value in ("c" * 39, "C" * 40, "main"):
            with self.subTest(value=value), self.assertRaises(RUNNER.GateError):
                RUNNER.require_expected_commit(value)

    def test_outer_and_inner_machine_contracts_are_identical(self) -> None:
        self.assertEqual(INNER.SCHEMA, RUNNER.INNER_SCHEMA)
        self.assertEqual(INNER.MARKER, RUNNER.INNER_MARKER)
        self.assertEqual(INNER.IDENTITIES, RUNNER.EXPECTED_IDENTITIES)
        self.assertEqual(INNER.BOUNDARY, RUNNER.EXPECTED_BOUNDARY)
        self.assertEqual(INNER.CHECKS, set(RUNNER.EXPECTED_CHECKS))
        for plane in INNER.PLANES:
            service = (
                ROOT / "tests/p1_dual_domain_rootful_systemd" /
                ("hepta-p1-dual-watch@.service"
                 if plane == "WATCH" else
                 "hepta-p1-dual-paper@.service"))
            text = service.read_text(encoding="utf-8", errors="strict")
            self.assertIn("Type=notify", text)
            self.assertIn("WatchdogSec=1s", text)
            self.assertIn("Restart=on-failure", text)
            self.assertIn("PrivateNetwork=yes", text)
            self.assertIn("RestrictAddressFamilies=AF_UNIX", text)
            self.assertNotIn("[Install]", text)

    def test_context_is_exact_and_contains_no_ib_or_broker_runtime(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-p1-dual-fixture-") as directory:
            context = Path(directory)
            records, generated = RUNNER.stage_context(ROOT, context)
            self.assertEqual(set(records), set(RUNNER.SOURCE_FILES))
            self.assertEqual(set(generated), {
                "identities.json", "boundary.json",
                "watch-codex-a.credential",
                "watch-openclaw-b.credential",
                "paper-codex-a.credential",
                "paper-openclaw-b.credential",
            })
            identities = json.loads(
                (context / "provision-root/identities.json").read_text(
                    encoding="utf-8"))
            self.assertEqual(
                identities["identities"], RUNNER.EXPECTED_IDENTITIES)
            self.assertFalse(identities["paper_authorized"])
        staged = "\n".join(RUNNER.SOURCE_FILES)
        for forbidden in (
                "Interface/IBApi", "hepta-ib-executiond",
                "hepta-execution-ib-paper", "broker-egress-policy"):
            self.assertNotIn(forbidden, staged)
        daemon = (
            ROOT / "tests/p1_dual_domain_rootful_systemd/"
            "hepta_p1_dual_domain_daemon.py").read_text(
                encoding="utf-8", errors="strict")
        for forbidden in (
                "import ibapi", "EClientSocket", "placeOrder(", "reqIds(",
                "trade.place_order", "socket.AF_INET"):
            self.assertNotIn(forbidden, daemon)

    def test_build_and_runtime_boundary_is_exact(self) -> None:
        build = RUNNER.build_arguments(
            pinned(), "hepta:test", Path("/context"), Path("/context/iid"),
            RUN_ID)
        self.assertIn("--pull=false", build)
        self.assertIn("--network=none", build)
        self.assertIn("--no-cache", build)
        certifying_build = RUNNER.build_arguments(
            pinned(), "hepta:test", Path("/context"), Path("/context/iid"),
            RUN_ID,
            builder_name=RUNNER.isolated_builder_names(RUN_ID)["builder"])
        self.assertEqual(certifying_build[:2], ["buildx", "build"])
        self.assertEqual(
            certifying_build[certifying_build.index("--builder") + 1],
            RUNNER.isolated_builder_names(RUN_ID)["builder"])
        for required in (
                "--load", "--platform", "--provenance=false",
                "--pull=false", "--network=none", "--no-cache"):
            self.assertIn(required, certifying_build)
        create = RUNNER.create_arguments(IMAGE_ID, "hepta-test", RUN_ID)
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertIn("--read-only", create)
        self.assertIn(
            "apparmor=" + RUNNER.APPARMOR_PROFILE, create)
        self.assertNotIn("apparmor=unconfined", create)
        for forbidden in (
                "--mount", "--volume", "-v", "--publish", "-p",
                "--privileged", "/run/docker.sock", "/var/run/docker.sock"):
            self.assertNotIn(forbidden, create)

    def test_container_inspect_rejects_unconfined_and_other_drift(self) -> None:
        record = valid_container_record()
        RUNNER.validate_container_inspect_record(
            record, container_id=CONTAINER_ID, image_id=IMAGE_ID,
            name="hepta-p1-dual-domain-" + RUN_ID, run_id=RUN_ID)
        mutations = []
        unconfined = copy.deepcopy(record)
        unconfined["HostConfig"]["SecurityOpt"] = [
            "no-new-privileges", "apparmor=unconfined"]
        mutations.append(unconfined)
        network = copy.deepcopy(record)
        network["HostConfig"]["NetworkMode"] = "bridge"
        mutations.append(network)
        bind = copy.deepcopy(record)
        bind["HostConfig"]["Binds"] = ["/host:/container:ro"]
        mutations.append(bind)
        writable = copy.deepcopy(record)
        writable["HostConfig"]["ReadonlyRootfs"] = False
        mutations.append(writable)
        secret = copy.deepcopy(record)
        secret["Config"]["Env"].append("BROKER_TOKEN=not-real")
        mutations.append(secret)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(
                    RUNNER.GateError):
                RUNNER.validate_container_inspect_record(
                    mutation, container_id=CONTAINER_ID, image_id=IMAGE_ID,
                    name="hepta-p1-dual-domain-" + RUN_ID, run_id=RUN_ID)

    def test_inner_result_is_exact_and_mutations_fail_closed(self) -> None:
        value = valid_inner()
        framed = RUNNER.INNER_MARKER + json.dumps(
            value, sort_keys=True, separators=(",", ":")) + "\n"
        self.assertEqual(
            RUNNER.validate_inner(framed, expected_run_id=RUN_ID), value)
        mutations = []
        order = copy.deepcopy(value)
        order["boundary"]["paper_orders"] = 1
        mutations.append(order)
        authority = copy.deepcopy(value)
        authority["boundary"]["paper_authorized"] = True
        mutations.append(authority)
        missing = copy.deepcopy(value)
        missing["checks"].pop(next(iter(RUNNER.EXPECTED_CHECKS)))
        mutations.append(missing)
        replay = copy.deepcopy(value)
        replay["run_id"] = "0" * 32
        mutations.append(replay)
        bad_fault = copy.deepcopy(value)
        bad_fault["faults"]["watchdog_timeout"]["after_generation"] += 1
        mutations.append(bad_fault)
        bad_inventory = copy.deepcopy(value)
        bad_inventory["inventory"]["forbidden_ib_api_payloads"] = 1
        mutations.append(bad_inventory)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(
                    RUNNER.GateError):
                RUNNER.validate_inner(
                    RUNNER.INNER_MARKER + json.dumps(mutation),
                    expected_run_id=RUN_ID)

    def execute_fake(
            self, fake: FakeDocker, *, allow_dirty: bool = False,
            certify: bool = False):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(RUNNER, "command", fake))
            stack.enter_context(mock.patch.object(
                RUNNER, "repository_root", lambda: ROOT))
            stack.enter_context(mock.patch.object(
                RUNNER.uuid, "uuid4",
                lambda: types.SimpleNamespace(hex=RUN_ID)))
            request = None
            if certify:
                provenance = certification_provenance()
                request = certification_request()
                stack.enter_context(mock.patch.object(
                    RUNNER, "load_certification_provenance",
                    return_value=provenance))
                stack.enter_context(mock.patch.object(
                    RUNNER, "validate_loaded_apparmor",
                    side_effect=lambda _document: apparmor_record(provenance)))
                stack.enter_context(mock.patch.object(
                    RUNNER, "validate_docker_namespace_binding",
                    side_effect=lambda _document, _apparmor:
                        docker_namespace_record(provenance)))
                stack.enter_context(mock.patch.object(
                    RUNNER, "inspect_buildx_toolchain",
                    return_value=buildx_toolchain_record()))
                stack.enter_context(mock.patch.object(
                    RUNNER, "validate_local_docker_socket",
                    side_effect=lambda: docker_socket_record()))
                stack.enter_context(mock.patch.object(
                    RUNNER, "verify_environment_review_for_request",
                    return_value=FakeReviewSession()))
            return RUNNER.execute(
                pinned(), COMMIT, allow_dirty_rehearsal=allow_dirty,
                certification_request=request)

    def test_fake_docker_full_path_is_rehearsal_only_even_for_clean_tree(
            self) -> None:
        fake = FakeDocker()
        report = self.execute_fake(fake)
        self.assertFalse(report["passed"])
        self.assertTrue(report["rehearsal_passed"])
        self.assertFalse(report["certification_ready"])
        self.assertEqual(report["decision"], "REHEARSAL_ONLY")
        self.assertEqual(
            report["certification_blockers"],
            list(RUNNER.CERTIFICATION_BLOCKERS))
        self.assertTrue(report["lineage"]["source_tree_clean"])
        self.assertFalse(report["paper_admission_authorized"])
        self.assertFalse(report["direct_broker_access"])
        self.assertEqual(report["disposable_cleanup"], {
            "container_absent": True,
            "image_tag_absent": True,
            "image_id_absent": True,
        })
        docker_calls = [call[4:] for call in fake.calls if call[0] == "docker"]
        self.assertTrue(any(call and call[0] == "build" for call in docker_calls))
        self.assertTrue(any(call and call[0] == "create" for call in docker_calls))

    def test_certifying_fake_path_closes_provenance_and_builder_evidence(
            self) -> None:
        fake = FakeDocker()
        report = self.execute_fake(fake, certify=True)
        self.assertTrue(report["passed"])
        self.assertTrue(report["certification_ready"])
        self.assertEqual(report["decision"], "GO")
        self.assertEqual(report["certification_blockers"], [])
        self.assertFalse(report["paper_admission_authorized"])
        self.assertFalse(report["live_authorized"])
        self.assertFalse(report["mutation_authorized"])
        self.assertFalse(report["direct_broker_access"])
        certification = report["certification"]
        self.assertTrue(certification["requested"])
        self.assertTrue(certification["eligible"])
        self.assertTrue(certification["provenance_reopened_equal"])
        self.assertTrue(certification["docker_socket_records_equal"])
        self.assertEqual(
            certification["docker_socket_before"],
            certification["docker_socket_after"])
        self.assertEqual(
            set(certification["provenance"]),
            {"base", "builder", "apparmor", "docker_namespace"})
        for record in certification["provenance"].values():
            self.assertTrue(record["root_owned"])
            self.assertTrue(record["canonical_json"])
            self.assertEqual(record["mode"], "0400")
        self.assertEqual(
            certification["isolated_builder_cleanup"]["buildx_rm"],
            "completed")
        self.assertTrue(
            certification["isolated_builder_cleanup"]["container_absent"])
        self.assertTrue(
            certification["isolated_builder_cleanup"]["state_volume_absent"])
        self.assertTrue(
            certification["isolated_builder_cleanup"]
            ["private_builder_metadata_absent"])
        docker_calls = [call[4:] for call in fake.calls if call[0] == "docker"]
        builds = [call for call in docker_calls if call[:2] == ["buildx", "build"]]
        self.assertEqual(len(builds), 1)
        self.assertIn("--network=none", builds[0])
        self.assertIn("--pull=false", builds[0])
        self.assertIn("--provenance=false", builds[0])
        self.assertTrue(any(
            call[:2] == ["buildx", "rm"] for call in docker_calls))

    def test_outer_report_schema_is_exact_and_cannot_be_promoted(self) -> None:
        report = self.execute_fake(FakeDocker())
        self.assertIs(RUNNER.validate_report(report), report)
        mutations = []
        promoted = copy.deepcopy(report)
        promoted["decision"] = "GO"
        promoted["passed"] = True
        mutations.append(promoted)
        missing_blocker = copy.deepcopy(report)
        missing_blocker["certification_blockers"].pop()
        mutations.append(missing_blocker)
        unconfined = copy.deepcopy(report)
        unconfined["container"]["apparmor_profile"] = "unconfined"
        mutations.append(unconfined)
        input_drift = copy.deepcopy(report)
        first = next(iter(input_drift["inputs"].values()))
        first["sha256"] = "0" * 64
        mutations.append(input_drift)
        replay = copy.deepcopy(report)
        replay["inner"]["run_id"] = "0" * 32
        mutations.append(replay)
        extra = copy.deepcopy(report)
        extra["reviewed"] = True
        mutations.append(extra)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(
                    RUNNER.GateError):
                RUNNER.validate_report(mutation)

    def test_report_publication_is_canonical_0600_and_no_replace(self) -> None:
        report = self.execute_fake(FakeDocker())
        with tempfile.TemporaryDirectory(
                prefix="hepta-dual-report-", dir=ROOT) as directory:
            path = Path(directory) / "dual-report.json"
            RUNNER.atomic_report(path, report)
            self.assertEqual(path.read_bytes(), RUNNER.canonical_json(report))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaisesRegex(
                    RUNNER.GateError, "report output already exists"):
                RUNNER.atomic_report(path, report)

    def test_provenance_expiry_is_bounded_and_report_bound(self) -> None:
        now_ms = int(time.time() * 1000)
        valid = certification_provenance()["base"].body
        RUNNER.validate_provenance_time(valid, now_ms=now_ms)
        expired = copy.deepcopy(valid)
        expired["issued_at_ms"] = now_ms - 2_000
        expired["expires_at_ms"] = now_ms - 1_000
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_provenance_time(expired, now_ms=now_ms)
        overlong = copy.deepcopy(valid)
        overlong["issued_at_ms"] = now_ms
        overlong["expires_at_ms"] = (
            now_ms + RUNNER.MAX_PROVENANCE_LIFETIME_MS + 1)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_provenance_time(overlong, now_ms=now_ms)

    def test_certifying_report_cannot_replay_or_drop_evidence(self) -> None:
        report = self.execute_fake(FakeDocker(), certify=True)
        self.assertIs(RUNNER.validate_report(report), report)
        mutations = []
        wrong_document = copy.deepcopy(report)
        wrong_document["certification"]["provenance"]["builder"][
            "document_sha256"] = "sha256:" + "0" * 64
        mutations.append(wrong_document)
        residue = copy.deepcopy(report)
        residue["certification"]["isolated_builder_cleanup"][
            "state_volume_absent"] = False
        mutations.append(residue)
        runtime_residue = copy.deepcopy(report)
        runtime_residue["disposable_cleanup"]["image_id_absent"] = False
        mutations.append(runtime_residue)
        apparmor_drift = copy.deepcopy(report)
        apparmor_drift["certification"]["apparmor_after"]["mode"] = "complain"
        mutations.append(apparmor_drift)
        namespace_drift = copy.deepcopy(report)
        namespace_drift["certification"]["docker_namespace_after"][
            "docker_daemon_start_time_ticks"] += 1
        mutations.append(namespace_drift)
        fingerprint_drift = copy.deepcopy(report)
        fingerprint_drift["environment_review_closure"][
            "environment_fingerprint"]["observations"][
                "docker_namespace"]["docker_daemon_start_time_ticks"] += 1
        mutations.append(fingerprint_drift)
        socket_drift = copy.deepcopy(report)
        socket_drift["certification"]["docker_socket_after"]["inode"] += 1
        mutations.append(socket_drift)
        replay = copy.deepcopy(report)
        replay["inner"]["run_id"] = "0" * 32
        mutations.append(replay)
        downgraded = copy.deepcopy(report)
        downgraded["decision"] = "REHEARSAL_ONLY"
        mutations.append(downgraded)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(
                    RUNNER.GateError):
                RUNNER.validate_report(mutation)

    def test_dirty_tree_needs_opt_in_and_still_cannot_go(self) -> None:
        with self.assertRaises(RUNNER.GateError):
            self.execute_fake(FakeDocker(dirty=True))
        report = self.execute_fake(FakeDocker(dirty=True), allow_dirty=True)
        self.assertFalse(report["passed"])
        self.assertEqual(report["decision"], "REHEARSAL_ONLY")
        self.assertFalse(report["lineage"]["source_tree_clean"])
        self.assertFalse(report["lineage"]["final_lineage"])
        with self.assertRaises(RUNNER.GateError):
            self.execute_fake(FakeDocker(dirty=True), certify=True)
        with self.assertRaises(RUNNER.GateError):
            self.execute_fake(
                FakeDocker(), allow_dirty=True, certify=True)

    def test_fake_docker_cannot_bypass_inspect_or_inner_contract(self) -> None:
        with self.assertRaises(RUNNER.GateError):
            self.execute_fake(FakeDocker(unconfined_container=True))
        with self.assertRaises(RUNNER.GateError):
            self.execute_fake(FakeDocker(mutate_inner=True))
        with self.assertRaises(RUNNER.GateError):
            self.execute_fake(
                FakeDocker(unconfined_container=True), certify=True)
        with self.assertRaises(RUNNER.GateError):
            self.execute_fake(FakeDocker(mutate_inner=True), certify=True)
        with self.assertRaises(RUNNER.GateError):
            self.execute_fake(
                FakeDocker(leave_builder_residue=True), certify=True)
        with self.assertRaises(RUNNER.GateError):
            self.execute_fake(
                FakeDocker(leave_target_image_residue=True), certify=True)

    def test_certification_cli_requires_explicit_complete_pins(self) -> None:
        provenance = certification_provenance()
        values = {
            "buildkit_image": pinned_buildkit(),
            "buildx_binary_sha256": "sha256:" + "6" * 64,
            "reviewed_base_path": Path("/root/base.json"),
            "reviewed_base_sha256": provenance["base"].document_sha256,
            "reviewed_builder_path": Path("/root/builder.json"),
            "reviewed_builder_sha256":
                provenance["builder"].document_sha256,
            "reviewed_apparmor_path": Path("/root/apparmor.json"),
            "reviewed_apparmor_sha256":
                provenance["apparmor"].document_sha256,
            "reviewed_docker_namespace_path":
                Path("/root/docker-namespace.json"),
            "reviewed_docker_namespace_sha256":
                provenance["docker_namespace"].document_sha256,
            "environment_review": certification_request().environment_review,
        }
        request = RUNNER.certification_request_from_values(
            certify=True, **values)
        self.assertEqual(request, certification_request())
        for field in values:
            missing = dict(values)
            missing[field] = None
            with self.subTest(missing=field), self.assertRaises(
                    RUNNER.GateError):
                RUNNER.certification_request_from_values(
                    certify=True, **missing)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.certification_request_from_values(
                certify=False, **values)
        self.assertIsNone(RUNNER.certification_request_from_values(
            certify=False,
            **{field: None for field in values}))

    def test_root_provenance_and_value_contracts_fail_closed(self) -> None:
        provenance = certification_provenance()
        validators = {
            "base": RUNNER.validate_base_provenance,
            "builder": RUNNER.validate_builder_provenance,
            "apparmor": RUNNER.validate_apparmor_provenance,
            "docker_namespace": RUNNER.validate_docker_namespace_provenance,
        }
        for kind, validator in validators.items():
            validator(provenance[kind])
        bad_base = copy.deepcopy(provenance["base"])
        bad_base.body["repo_digest"] = "debian:latest"
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_base_provenance(bad_base)
        bad_builder = copy.deepcopy(provenance["builder"])
        bad_builder.body["buildkit_version"] = "latest"
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_builder_provenance(bad_builder)
        bad_apparmor = copy.deepcopy(provenance["apparmor"])
        bad_apparmor.body["profile"] = "unconfined"
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_apparmor_provenance(bad_apparmor)
        bad_namespace = copy.deepcopy(provenance["docker_namespace"])
        bad_namespace.body["daemon_namespace_stacked"] = True
        with self.assertRaises(RUNNER.GateError):
            RUNNER.validate_docker_namespace_provenance(bad_namespace)
        with (
                mock.patch.object(RUNNER.os, "geteuid", return_value=1000),
                self.assertRaises(RUNNER.GateError)):
            RUNNER.read_root_canonical_provenance(
                Path("/does/not/exist"), "sha256:" + "1" * 64,
                kind="base",
                expected_schema=RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
                expected_keys=RUNNER.REVIEWED_BASE_KEYS)
        with (
                mock.patch.object(RUNNER.os, "geteuid", return_value=0),
                self.assertRaises(RUNNER.GateError)):
            RUNNER.read_root_canonical_provenance(
                Path("relative.json"), "sha256:" + "1" * 64,
                kind="base",
                expected_schema=RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
                expected_keys=RUNNER.REVIEWED_BASE_KEYS)
        request = certification_request()
        duplicate_path = RUNNER.CertificationRequest(
            **{
                **request.__dict__,
                "reviewed_builder_path": request.reviewed_base_path,
            })
        with self.assertRaises(RUNNER.GateError):
            RUNNER.load_certification_provenance(duplicate_path)
        duplicate_digest = RUNNER.CertificationRequest(
            **{
                **request.__dict__,
                "reviewed_builder_sha256": request.reviewed_base_sha256,
            })
        with self.assertRaises(RUNNER.GateError):
            RUNNER.load_certification_provenance(duplicate_digest)

    def test_anchored_reader_rejects_symlinked_parent_and_final(self) -> None:
        body = certification_provenance()["base"].body
        raw = RUNNER.canonical_json(body)
        digest = "sha256:" + RUNNER.hashlib.sha256(raw).hexdigest()

        def directory_identity(metadata, kind):
            del kind
            if not RUNNER.stat.S_ISDIR(metadata.st_mode):
                raise RUNNER.GateError("not a directory")
            return RUNNER.metadata_identity(
                metadata, RUNNER.PROVENANCE_DIRECTORY_FIELDS)

        def file_identity(metadata, kind):
            del kind
            if (
                    not RUNNER.stat.S_ISREG(metadata.st_mode) or
                    metadata.st_nlink != 1 or
                    RUNNER.stat.S_IMODE(metadata.st_mode) != 0o400):
                raise RUNNER.GateError("not a fixed file")
            return RUNNER.metadata_identity(
                metadata, RUNNER.PROVENANCE_FILE_FIELDS)

        with tempfile.TemporaryDirectory(
                prefix="hepta-p1-provenance-anchor-") as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir(mode=0o700)
            document = real / "base.json"
            document.write_bytes(raw)
            document.chmod(0o400)
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            final_alias = real / "base-link.json"
            final_alias.symlink_to(document)
            with (
                    mock.patch.object(RUNNER.os, "geteuid", return_value=0),
                    mock.patch.object(
                        RUNNER, "validate_provenance_directory_metadata",
                        side_effect=directory_identity),
                    mock.patch.object(
                        RUNNER, "validate_provenance_file_metadata",
                        side_effect=file_identity)):
                loaded = RUNNER.read_root_canonical_provenance(
                    document, digest, kind="base",
                    expected_schema=RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
                    expected_keys=RUNNER.REVIEWED_BASE_KEYS)
                self.assertEqual(loaded.body, body)
                with self.assertRaises(RUNNER.GateError):
                    RUNNER.read_root_canonical_provenance(
                        alias / "base.json", digest, kind="base",
                        expected_schema=
                            RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
                        expected_keys=RUNNER.REVIEWED_BASE_KEYS)
                with self.assertRaises(RUNNER.GateError):
                    RUNNER.read_root_canonical_provenance(
                        final_alias, digest, kind="base",
                        expected_schema=
                            RUNNER.REVIEWED_BASE_PROVENANCE_SCHEMA,
                        expected_keys=RUNNER.REVIEWED_BASE_KEYS)

    def test_source_lineage_default_denies_dirty(self) -> None:
        RUNNER.require_source_lineage(True, False)
        RUNNER.require_source_lineage(False, True)
        with self.assertRaises(RUNNER.GateError):
            RUNNER.require_source_lineage(False, False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
