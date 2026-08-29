#!/usr/bin/env python3

"""Run the explicit disposable broker-port lifecycle rootful gate.

The runner builds from a preloaded digest-pinned image with build networking
disabled, creates a loopback-only read-only container with no mounts, and
stages neither an IB binary nor any PAPER credential or service. It uses only
inert sentinels and default-engaged authority fixtures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
from typing import Any, Optional
import uuid


SCHEMA = "hepta.broker-network-rootful-gate.v3"
INNER_SCHEMA = "hepta.broker-network-opt-in-rootful.v3"
INNER_MARKER = "HEPTA_BROKER_NETWORK_OPT_IN_ROOTFUL_RESULT="
PURPOSE = "hepta-broker-network-rootful-gate"
PINNED_IMAGE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}$")
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_INPUT_BYTES = 2 * 1024 * 1024
RUNTIME_CAPABILITIES = ("CHOWN", "NET_ADMIN", "SETGID", "SETUID")
RUNTIME_TMPFS = "/run:rw,nosuid,nodev,noexec,mode=0755,size=32m"
COMMAND_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "TZ": "UTC",
}
STAGED_FILES = {
    "scripts/hepta_broker_egress_policy.py":
        ("hepta_broker_egress_policy.py", 0o755),
    "scripts/hepta_ib_paper_domain_authority.py":
        ("hepta_ib_paper_domain_authority.py", 0o755),
    "systemd/hepta-broker-network-policy-v1.json":
        ("hepta-broker-network-policy-v1.json", 0o644),
    "systemd/hepta-service-identities-v1.json":
        ("hepta-service-identities-v1.json", 0o644),
    "tests/broker_network_rootful/hepta_broker_network_opt_in_gate.py":
        ("hepta_broker_network_opt_in_gate.py", 0o755),
    "tests/broker_network_rootful/Dockerfile": ("Dockerfile", 0o644),
}
EXPECTED_CHECKS = {
    "fixed_only_default",
    "all_agent_gateway_simulator_uids_denied",
    "domain_ib_uids_denied_before_opt_in",
    "second_domain_manifest_rejected_without_policy_change",
    "one_domain_ib_uid_allowed_after_exact_opt_in",
    "second_domain_ib_uid_denied_during_opt_in",
    "domain_ib_uids_denied_after_revocation",
    "fixed_ib_uid_disabled_in_templated_mode",
    "agent_non_broker_egress_preserved",
    "nft_syntax_checked_and_applied",
    "exact_live_nft_json_verified",
    "broker_guard_detects_table_flush_and_tightens",
    "broker_guard_detects_manifest_replacement_and_tightens",
    "authority_guard_holds_lifetime_host_lease",
    "second_domain_rejected_while_first_guard_active",
    "foreign_domain_exec_stop_post_is_noop",
    "second_domain_guard_allowed_after_first_stops",
    "clean_broker_guard_stop_revokes_all",
    "broker_exec_stop_post_revokes_all_after_sigkill",
    "authority_exec_stop_post_revokes_after_sigkill",
    "authority_sigkill_tombstone_blocks_competing_start",
    "authority_clean_stop_revokes_domain_preserves_broker_guard",
    "ipv4_and_ipv6_loopback_enforced",
}
EXPECTED_BOUNDARY = {
    "network_only": True,
    "inert_loopback_sentinels": True,
    "loopback_families": ["ipv4", "ipv6"],
    "real_broker_connections": 0,
    "broker_protocol_messages": 0,
    "ib_binaries": 0,
    "paper_units": 0,
    "credentials": 0,
    "default_engaged_kill_switch_fixtures": 2,
    "paper_orders": 0,
    "live_authorized": False,
}
EXPECTED_IDENTITIES = {
    "fixed_ib_uid": 2003,
    "authorized_domain_ib_uid": 2121,
    "rejected_second_domain_ib_uid": 2122,
    "agent_uids": [2004, 2104, 2105],
    "gateway_uids": [2001, 2101, 2102],
    "simulator_uids": [2002, 2111, 2112],
}


class GateError(RuntimeError):
    pass


_DOCKER_CONFIG: Optional[tempfile.TemporaryDirectory[str]] = None


def fail(message: str) -> None:
    raise GateError(message)


def repository_root() -> Path:
    return Path(__file__).resolve(strict=True).parents[1]


def require_pinned_image(value: str) -> str:
    if PINNED_IMAGE.fullmatch(value) is None:
        fail("--base-image must be an exact name@sha256:<64 lowercase hex> reference")
    return value


def initialize_docker_config() -> None:
    global _DOCKER_CONFIG
    if _DOCKER_CONFIG is not None:
        fail("isolated Docker configuration was initialized twice")
    _DOCKER_CONFIG = tempfile.TemporaryDirectory(
        prefix="hepta-broker-network-docker-config-")
    os.chmod(_DOCKER_CONFIG.name, 0o700)


def cleanup_docker_config() -> None:
    global _DOCKER_CONFIG
    holder = _DOCKER_CONFIG
    _DOCKER_CONFIG = None
    if holder is not None:
        holder.cleanup()


def docker_cli(*arguments: str) -> list[str]:
    if _DOCKER_CONFIG is None:
        fail("isolated Docker configuration is not initialized")
    return [
        "docker", "--config", _DOCKER_CONFIG.name,
        "--host=unix:///run/docker.sock", *arguments,
    ]


def command(
        arguments: list[str], *, timeout: int = 300,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=COMMAND_ENV,
        start_new_session=True,
        close_fds=True,
    )
    try:
        output, _unused = process.communicate(timeout=timeout)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    process.wait()
        raise
    if len(output.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES:
        fail("bounded Docker command output exceeded")
    completed = subprocess.CompletedProcess(
        arguments, process.returncode, output, None)
    if check and completed.returncode != 0:
        diagnostic = output[-2048:].replace("\r", "").replace("\n", " | ")
        fail(
            f"Docker command failed rc={completed.returncode}"
            + (f" output_tail={diagnostic}" if diagnostic else ""))
    return completed


def read_stable(path: Path) -> tuple[bytes, dict[str, object]]:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size < 1 or before.st_size > MAX_INPUT_BYTES or
                stat.S_IMODE(before.st_mode) & 0o002):
            fail(f"unsafe staged input metadata: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor, min(65536, MAX_INPUT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                fail(f"staged input exceeds bound: {path}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        fail(f"staged input changed while reading: {path}")
    raw = b"".join(chunks)
    return raw, {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "mode": format(stat.S_IMODE(before.st_mode), "04o"),
    }


def stage_context(root: Path, context: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for source_name, (target_name, mode) in STAGED_FILES.items():
        raw, record = read_stable(root / source_name)
        target = context / target_name
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        try:
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    fail("short context write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(target, mode)
        copied, _copied_record = read_stable(target)
        if copied != raw:
            fail("staged context copy mismatch")
        records[source_name] = record
    if {item.name for item in context.iterdir()} != {
            target for target, _mode in STAGED_FILES.values()}:
        fail("Docker context allowlist mismatch")
    return records


def build_arguments(
        base_image: str, image_tag: str, context: Path,
        iidfile: Path) -> list[str]:
    return [
        "build", "--network", "none", "--pull=false", "--no-cache",
        "--build-arg", f"BASE_IMAGE={base_image}",
        "--label", f"io.hepta.purpose={PURPOSE}",
        "--iidfile", str(iidfile), "--tag", image_tag, str(context),
    ]


def create_arguments(
        image_tag: str, container_name: str, run_id: str) -> list[str]:
    result = [
        "create", "--name", container_name,
        "--label", f"io.hepta.purpose={PURPOSE}",
        "--label", f"io.hepta.run-id={run_id}",
        "--user", "0:0", "--network", "none", "--read-only",
        "--tmpfs", RUNTIME_TMPFS,
        "--cap-drop", "ALL",
    ]
    for capability in RUNTIME_CAPABILITIES:
        result.extend(("--cap-add", capability))
    result.extend((
        "--security-opt", "no-new-privileges",
        "--pids-limit", "128",
        "--restart", "no",
        image_tag,
    ))
    return result


def parse_one_json(output: str) -> Any:
    try:
        value = json.loads(output)
    except json.JSONDecodeError as error:
        fail(f"invalid Docker JSON: {error}")
    return value


def validate_base_image(record: Any, reference: str) -> None:
    if not isinstance(record, list) or len(record) != 1:
        fail("base image inspect must return one record")
    item = record[0]
    if (
            not isinstance(item, dict) or item.get("Os") != "linux" or
            reference not in item.get("RepoDigests", []) or
            item.get("Config", {}).get("OnBuild") not in (None, [])):
        fail("base image inspect does not match the pinned Linux reference")


def validate_container(record: Any, image_id: str) -> None:
    if not isinstance(record, list) or len(record) != 1:
        fail("container inspect must return one record")
    item = record[0]
    host = item.get("HostConfig", {})
    config = item.get("Config", {})
    if (
            item.get("Image") != image_id or item.get("Mounts") != [] or
            host.get("Privileged") is not False or
            host.get("ReadonlyRootfs") is not True or
            host.get("NetworkMode") != "none" or
            host.get("Binds") not in (None, []) or
            host.get("PortBindings") not in ({}, None) or
            host.get("PublishAllPorts") is not False or
            host.get("Tmpfs") != {"/run": RUNTIME_TMPFS.split(":", 1)[1]} or
            sorted(host.get("CapDrop", [])) != ["ALL"] or
            sorted(host.get("CapAdd", [])) != sorted(
                f"CAP_{value}" for value in RUNTIME_CAPABILITIES) or
            host.get("SecurityOpt") not in (
                ["no-new-privileges"], ["no-new-privileges=true"]) or
            host.get("PidsLimit") != 128 or
            config.get("User") != "0:0"):
        fail("container runtime boundary drifted")


def validate_inner(output: str) -> dict[str, object]:
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0].startswith(INNER_MARKER):
        fail("inner gate did not emit exactly one canonical result marker")
    try:
        result = json.loads(lines[0][len(INNER_MARKER):])
    except json.JSONDecodeError as error:
        fail(f"inner result is invalid JSON: {error}")
    if (
            not isinstance(result, dict) or
            set(result) != {
                "schema", "passed", "checks", "identities", "boundary"} or
            result.get("schema") != INNER_SCHEMA or
            result.get("passed") is not True or
            result.get("identities") != EXPECTED_IDENTITIES or
            result.get("boundary") != EXPECTED_BOUNDARY):
        fail("inner network-only result contract mismatch")
    checks = result.get("checks")
    if (
            not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS or
            any(value is not True for value in checks.values())):
        fail("inner network-only checks are incomplete")
    return result


def write_report(path: Path, report: dict[str, object]) -> None:
    raw = (
        json.dumps(report, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, raw)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def execute(base_image: str) -> dict[str, object]:
    root = repository_root()
    run_id = uuid.uuid4().hex
    image_tag = f"hepta-broker-network-gate:{run_id}"
    container_name = f"hepta-broker-network-gate-{run_id}"
    container_id = ""
    image_id = ""
    staged: dict[str, dict[str, object]] = {}
    initialize_docker_config()
    try:
        base_inspect = command(
            docker_cli("image", "inspect", base_image), timeout=60)
        validate_base_image(parse_one_json(base_inspect.stdout), base_image)
        with tempfile.TemporaryDirectory(
                prefix="hepta-broker-network-context-") as directory:
            context = Path(directory)
            staged = stage_context(root, context)
            iidfile = context / ".image-id"
            command(
                docker_cli(*build_arguments(
                    base_image, image_tag, context, iidfile)),
                timeout=900)
            if not iidfile.is_file() or iidfile.is_symlink():
                fail("Docker build did not produce a regular iidfile")
            image_id = iidfile.read_text(
                encoding="ascii", errors="strict").strip()
            if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
                fail("Docker build returned a non-canonical image ID")
        created = command(
            docker_cli(*create_arguments(
                image_tag, container_name, run_id)), timeout=60)
        container_id = created.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
            fail("Docker create returned a non-canonical container ID")
        inspected = command(
            docker_cli("container", "inspect", container_id), timeout=60)
        validate_container(parse_one_json(inspected.stdout), image_id)
        started = command(
            docker_cli("start", "--attach", container_id), timeout=300)
        inner = validate_inner(started.stdout)
        return {
            "schema": SCHEMA,
            "passed": True,
            "run_id": run_id,
            "base_image": base_image,
            "image_id": image_id,
            "container_id": container_id,
            "staged_inputs": staged,
            "inner": inner,
            "actual_rootful_container_run": True,
            "host_policy_applied": False,
            "host_services_started": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "live_authorized": False,
        }
    finally:
        if container_id:
            command(
                docker_cli("container", "rm", "--force", container_id),
                timeout=60, check=False)
        else:
            command(
                docker_cli(
                    "container", "rm", "--force", container_name),
                timeout=60, check=False)
        command(
            docker_cli("image", "rm", "--force", image_tag),
            timeout=120, check=False)
        cleanup_docker_config()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args(argv)
    try:
        base_image = require_pinned_image(arguments.base_image)
        if not arguments.run:
            fail("refusing rootful Docker execution without explicit --run")
        report = execute(base_image)
        if arguments.report is not None:
            write_report(arguments.report, report)
    except (
            GateError, OSError, UnicodeError, ValueError,
            subprocess.SubprocessError) as error:
        message = str(error)
        if not message:
            message = type(error).__name__
        elif len(message) > 2048:
            message = message[:2045] + "..."
        print(
            "hepta_broker_network_rootful_gate: FAIL: " + message,
            file=sys.stderr)
        return 1
    print(
        "hepta_broker_network_rootful_gate: PASS "
        "network_only=1 protected_ports=4 lifecycle_fail_closed=1 "
        "host_lease_serialized=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
