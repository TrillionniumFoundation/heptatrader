#!/usr/bin/env python3
"""Package and accept the exact core artifact on a disposable Linux CI VM.

Shared by main CI and tagged release. Not an installer for trading hosts.
The existing process/systemd fixtures enforce their own host interlocks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from verify_build_ownership import canonical_path, load_json

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SHA = "d003f54c7c6c2bd19002627f2bcd9081228b01cd"
CHECKS = ("install", "core-python", "simulator-lifecycle", "distinct-artifact-process",
          "strategy-client-server-pair", "pid1-systemd")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


def validate_client_pair_evidence(path: Path, source: str, core_sha: str, client_sha: str) -> dict:
    """Require the actual paired-package scenario, not an exit code or old badge."""
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("client/server pair evidence is missing or invalid") from error
    expected = {"schema": "hepta.installed-client-server-pair.v1", "result": "PASS",
                "source_sha": source, "core_sha256": core_sha, "client_sha256": client_sha,
                "client_uid": 61003, "concurrent_clients": 4,
                "place_send_attempts": 1, "place_sent_records": 1, "final_position": 0,
                "final_active_orders": [], "authorization_effect": "NONE",
                "broker_io": False, "systemd_manager_exercised": False,
                "restart_status_only": True, "pre_send_crash_status_only": True,
                "intent_conflict_rejected": True, "binding_change_rejected": True,
                "original_request_bytes_preserved": True, "relocated_client": True}
    if not isinstance(value, dict) or any(
        type(value.get(key)) is not type(want) or value[key] != want
        for key, want in expected.items()
    ):
        raise ValueError("client/server pair evidence identity or scenario is invalid")
    commands = value.get("prepared_commands")
    if (not isinstance(commands, list) or len(commands) != 2
        or any(not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", c) for c in commands)
        or len(set(commands)) != 2):
        raise ValueError("client/server pair command identities are invalid")
    info = value.get("client_build_info")
    if (not isinstance(info, dict) or info.get("source_sha") != source
        or info.get("package") != "HeptaStrategyClient"
        or info.get("source_tree_state") != "clean" or info.get("system") != "Linux"
        or info.get("build_type") != "Release" or info.get("wire_protocol") != "HTT1"
        or info.get("execution_authority") != "none" or info.get("broker_transport") != "none"
        or info.get("transport") != "local-unix-only"):
        raise ValueError("client/server pair SDK metadata is invalid")
    processes = value.get("processes")
    if not isinstance(processes, list) or len(processes) != 4:
        raise ValueError("client/server pair requires actual initial and restarted processes")
    pids, identities, counts = set(), {}, {}
    for process in processes:
        if not isinstance(process, dict):
            raise ValueError("invalid paired process evidence")
        name, uid, pid, sha = (process.get(k) for k in ("name", "uid", "pid", "executable_sha256"))
        if (name not in ("hepta-executiond", "hepta-tool-gatewayd")
            or type(uid) is not int or uid != {"hepta-executiond": 61002, "hepta-tool-gatewayd": 61001}[name]
            or type(pid) is not int or pid <= 0 or pid in pids
            or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None):
            raise ValueError("client/server pair process identity is invalid")
        pids.add(pid)
        counts[name] = counts.get(name, 0) + 1
        if identities.setdefault(name, sha) != sha:
            raise ValueError("client/server pair restarted executable differs")
    if counts != {"hepta-executiond": 2, "hepta-tool-gatewayd": 2}:
        raise ValueError("client/server pair restart is incomplete")
    return value


def validate_generation_cost_evidence(
    path: Path, source_sha: str, artifact_sha256: str
) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("generation cost evidence is missing or invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("schema") != "heptatrader.installed-generation-cost-curve.v1"
        or value.get("result") != "PASS"
        or value.get("source_sha") != source_sha
        or value.get("artifact_sha256") != artifact_sha256
        or value.get("synthetic") is not True
        or value.get("installed_processes") is not True
        or value.get("broker_io") is not False
        or value.get("authorization_effect") != "NONE"
        or value.get("oldest_command_duplicate_no_resend") is not True
        or value.get("final_position") != 0
    ):
        raise ValueError("generation cost evidence identity or result is invalid")
    points = value.get("points")
    if not isinstance(points, list) or len(points) != 3:
        raise ValueError("generation cost evidence point inventory is invalid")

    def unsigned(number, *, positive: bool = False) -> bool:
        return type(number) is int and number >= (1 if positive else 0)

    expected_admitted = [8, 40, 168]
    if [point.get("admitted_orders") for point in points
            if isinstance(point, dict)] != expected_admitted:
        raise ValueError("generation cost evidence cardinality is invalid")
    previous_history = -1
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("generation cost evidence point is invalid")
        required_positive = (
            "history_records", "seal_ns", "execution_peak_rss_kib",
            "place_latency_total_samples", "journal_bytes_before_seal",
            "retained_disk_bytes",
        )
        if any(not unsigned(point.get(name), positive=True)
               for name in required_positive):
            raise ValueError("generation cost evidence positive metric is invalid")
        required_nonnegative = (
            "restart_recovery_ns", "simulator_state_recovery_ns",
            "startup_ready_ns", "place_latency_total_max_ns",
            "place_latency_total_p99_upper_ns",
        )
        if any(not unsigned(point.get(name))
               for name in required_nonnegative):
            raise ValueError("generation cost evidence latency metric is invalid")
        if point["startup_ready_ns"] < (
            point["restart_recovery_ns"] +
            point["simulator_state_recovery_ns"]
        ):
            raise ValueError("generation cost evidence startup scope is incomplete")
        if point["history_records"] < previous_history:
            raise ValueError("generation cost evidence history regressed")
        previous_history = point["history_records"]

    before = value.get("retained_disk_bytes_before_rebase")
    after = value.get("retained_disk_bytes_after_rebase")
    if (
        not unsigned(value.get("rebase_ns"), positive=True)
        or not unsigned(before, positive=True)
        or not unsigned(after, positive=True)
        or after >= before
        or not unsigned(value.get("post_rebase_recovery_ns"))
        or not unsigned(value.get("post_rebase_simulator_state_recovery_ns"))
        or not unsigned(value.get("post_rebase_startup_ready_ns"))
        or not unsigned(value.get("post_rebase_execution_peak_rss_kib"), positive=True)
    ):
        raise ValueError("generation cost evidence rebase boundary is invalid")
    if value["post_rebase_startup_ready_ns"] < (
        value["post_rebase_recovery_ns"] +
        value["post_rebase_simulator_state_recovery_ns"]
    ):
        raise ValueError("post-rebase startup scope is incomplete")
    return value


def validate_generation_index_read_evidence(path: Path, source_sha: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("generation index read evidence is missing or invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("schema") != "heptatrader.generation-index-read-cost.v1"
        or value.get("result") != "PASS"
        or value.get("source_sha") != source_sha
        or value.get("fixture_rows") != 20000
        or value.get("row_bytes") != 512
        or value.get("logical_bytes") != 20000 * 512
        or value.get("broker_io") is not False
        or value.get("authorization_effect") != "NONE"
    ):
        raise ValueError("generation index read evidence identity is invalid")
    full, suffix = value.get("full"), value.get("suffix")
    if not isinstance(full, dict) or not isinstance(suffix, dict):
        raise ValueError("generation index read evidence scans are invalid")
    if (
        full.get("lines") != 20000
        or full.get("read_bytes") != value["logical_bytes"]
        or full.get("selected_bytes") != value["logical_bytes"]
        or type(full.get("read_calls")) is not int
        or not 0 < full["read_calls"] <= (value["logical_bytes"] + 65535) // 65536
    ):
        raise ValueError("generation index full-scan evidence is invalid")
    expected_suffix = value["logical_bytes"] - 1234 * 512
    if (
        value.get("suffix_start_bytes") != 1234 * 512
        or suffix.get("lines") != 20000 - 1234
        or suffix.get("read_bytes") != expected_suffix
        or suffix.get("selected_bytes") != expected_suffix
        or type(suffix.get("read_calls")) is not int
        or not 0 < suffix["read_calls"] <= (expected_suffix + 65535) // 65536
    ):
        raise ValueError("generation index suffix evidence is invalid")
    return value


def validate_synthetic_generation_cost_evidence(path: Path, source_sha: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("synthetic generation cost evidence is missing or invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("schema") != "heptatrader.synthetic-generation-cost-curve.v2"
        or value.get("result") != "PASS"
        or value.get("source_sha") != source_sha
        or value.get("synthetic") is not True
        or value.get("broker_io") is not False
        or value.get("commands_per_generation") != 64
        or value.get("generation_count") != 16
        or value.get("maximum_owner_count") != 16
        or value.get("authorization_effect") != "NONE"
    ):
        raise ValueError("synthetic generation cost evidence identity is invalid")
    points = value.get("points")
    if not isinstance(points, list) or len(points) != 3:
        raise ValueError("synthetic generation cost evidence point inventory is invalid")
    expected = ((4, 4, 256, 1024), (8, 8, 512, 2048), (16, 16, 1024, 4096))
    for point, (generation, owners, commands, history) in zip(points, expected):
        if (
            not isinstance(point, dict)
            or point.get("generation_count") != generation
            or point.get("owner_count") != owners
            or point.get("command_records") != commands
            or point.get("history_records") != history
        ):
            raise ValueError("synthetic generation cost evidence cardinality is invalid")
        for name in (
            "seal_ns", "verify_ns", "active_bytes_before_seal",
            "generation_output_bytes", "runtime_command_index_bytes",
            "send_attempt_index_bytes", "logical_event_bytes", "retained_disk_bytes",
        ):
            if type(point.get(name)) is not int or point[name] <= 0:
                raise ValueError("synthetic generation cost evidence metric is invalid")
        if (
            point.get("retained_to_logical_numerator") != point["retained_disk_bytes"]
            or point.get("retained_to_logical_denominator") != point["logical_event_bytes"]
        ):
            raise ValueError("synthetic generation storage amplification evidence is invalid")
    before, after = value.get("retained_disk_bytes_before_rebase"), value.get("retained_disk_bytes_after_rebase")
    if (
        type(value.get("rebase_ns")) is not int or value["rebase_ns"] <= 0
        or type(before) is not int or type(after) is not int
        or before <= 0 or after <= 0 or after >= before
        or type(value.get("test_process_peak_rss_kib")) is not int
        or value["test_process_peak_rss_kib"] <= 0
    ):
        raise ValueError("synthetic generation rebase evidence is invalid")
    return value


def installed_executable_targets(source: Path, build: Path) -> list[str]:
    """Use fresh CMake install ownership, not a second handwritten target list.

    The reference still installs and participates in real process rollback.
    Uninstalled historical test binaries are not needed to build its payload.
    """
    reply = build / ".cmake/api/v1/reply"
    indexes = list(reply.glob("index-*.json"))
    if len(indexes) != 1:
        raise ValueError("reference requires one fresh CMake File API index")

    def document(name):
        leaf = canonical_path(name)
        if "/" in leaf:
            raise ValueError("CMake reply must name a local JSON leaf")
        return load_json(reply / leaf)

    try:
        index = load_json(indexes[0])
        model = document(index["reply"]["codemodel-v2"]["jsonFile"])
        if (Path(model["paths"]["source"]).resolve() != source.resolve()
                or Path(model["paths"]["build"]).resolve() != build.resolve()):
            raise ValueError("reference CMake model source/build mismatch")
        configurations = model["configurations"]
        if len(configurations) != 1 or configurations[0]["name"] != "Release":
            raise ValueError("reference requires one Release configuration")
        targets = []
        seen = set()
        for entry in configurations[0]["targets"]:
            target = document(entry["jsonFile"])
            name = entry["name"]
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*", name)
                    or target["name"] != name or name in seen):
                raise ValueError("invalid or duplicate reference target identity")
            seen.add(name)
            if target["type"] == "EXECUTABLE" and target.get("install", {}).get("destinations"):
                targets.append(name)
        if not targets:
            raise ValueError("reference CMake model has no installed executable targets")
        return sorted(targets)
    except (KeyError, TypeError) as error:
        raise ValueError("incomplete reference CMake model") from error


def accept(build: Path, output: Path, source: str, *, root: Path = ROOT,
           run=subprocess.run) -> dict:
    if re.fullmatch(r"[0-9a-f]{40}", source) is None:
        raise ValueError("exact source SHA required")
    root, build, output = root.resolve(), build.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt_path = output / "core-acceptance.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ValueError("refusing to replace an acceptance receipt")

    def command(argv, **kwargs):
        return run(list(map(str, argv)), cwd=root, check=True, timeout=1800, **kwargs)

    def text(argv):
        return command(argv, capture_output=True, text=True).stdout.strip()

    def checkout_status(*, allow_output: bool = False) -> str:
        """Return porcelain status, excluding acceptance outputs after creation.

        The checkout must be clean before acceptance starts.  The package and
        evidence are intentionally written below ``output`` during acceptance,
        so the final guard excludes only that exact repository-relative tree;
        all other tracked or untracked paths remain fatal.
        """
        argv = ["git", "status", "--porcelain", "--untracked-files=all"]
        if allow_output:
            try:
                relative_output = output.relative_to(root).as_posix()
            except ValueError:
                relative_output = ""
            if relative_output and relative_output != ".":
                argv += ["--", ".", f":(exclude,top){relative_output}"]
        return text(argv)

    if text(["git", "rev-parse", "HEAD"]) != source:
        raise ValueError("checkout differs from candidate source")
    command(["git", "diff", "--exit-code"])
    command(["git", "diff", "--cached", "--exit-code"])
    if checkout_status():
        raise ValueError("checkout contains tracked changes or untracked files")
    version = (root / "VERSION").read_text().strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version) is None:
        raise ValueError("invalid release version")
    candidate = output / f"heptatrader-{version}-core-{source}.tar.gz"
    environment = dict(os.environ)
    environment["HEPTA_RELEASE_INTEGRATION_BUILD_DIR"] = str(build)
    environment["PYTHONWARNINGS"] = "error::ResourceWarning"
    core_evidence = output / "core-evidence"
    for lane in ("install", "core"):
        lane_environment = dict(environment)
        if lane == "core":
            core_evidence.mkdir(mode=0o755)
            lane_environment["HEPTA_CORE_EVIDENCE_DIR"] = str(core_evidence)
            lane_environment["HEPTA_CORE_EVIDENCE_SOURCE_SHA"] = source
        command([sys.executable, "scripts/run_python_tests.py", "--lane", lane],
                env=lane_environment)
        if lane == "core":
            validate_generation_index_read_evidence(
                core_evidence / "generation-index-read-cost.json", source)
            validate_synthetic_generation_cost_evidence(
                core_evidence / "synthetic-generation-cost-curve.json", source)
    epoch = text(["git", "show", "-s", "--format=%ct", source])
    command([sys.executable, "scripts/build_release_package.py", "--build-dir", build,
             "--output", candidate, "--version", version, "--profile", "core",
             "--source-sha", source, "--source-date-epoch", epoch])
    candidate_digest = digest(candidate)
    if Path(str(candidate) + ".sha256").read_text().split()[0] != candidate_digest:
        raise ValueError("package digest sidecar mismatch")
    smoke = output / f"release-simulator-smoke-{source}.json"
    command([sys.executable, "scripts/run_release_simulator_smoke.py", "--artifact", candidate,
             "--expected-sha256", candidate_digest, "--profile", "core",
             "--policy", "docs/preflight-policy-v1.json", "--preflight", "scripts/hepta_preflight.py",
             "--work-root", output / f"release-simulator-smoke-{source}", "--output", smoke])

    # One pinned reference, one source-owned assembly and one pair test. A new
    # reference requires a support-window decision, not an environment override.
    host_source = None
    with tempfile.TemporaryDirectory(prefix="hepta-accept-") as temporary:
        work = Path(temporary)
        # Build an independent developer package through its existing opt-in
        # install component. Never add these artifacts to the core payload.
        client_stage = work / "client-sdk"
        command(["cmake", "--install", build, "--config", "Release", "--prefix", client_stage,
                 "--component", "StrategyClientSDK"])
        client_artifact = core_evidence / f"strategy-client-sdk-{source}.tar.gz"
        if client_artifact.exists() or client_artifact.is_symlink():
            raise ValueError("refusing to replace the client acceptance package")
        command(["tar", "--sort=name", "--mtime=@" + epoch, "--owner=0", "--group=0",
                 "--numeric-owner", "-czf", client_artifact, "-C", client_stage, "."])
        client_digest = digest(client_artifact)
        reference, reference_build = work / "reference", work / "reference-build"
        command(["git", "init", reference])
        command(["git", "-C", reference, "remote", "add", "origin",
                 "https://github.com/TrillionniumFoundation/heptatrader.git"])
        command(["git", "-C", reference, "fetch", "--no-tags", "--depth=1", "origin", REFERENCE_SHA])
        command(["git", "-C", reference, "checkout", "--detach", "FETCH_HEAD"])
        if text(["git", "-C", reference, "rev-parse", "HEAD"]) != REFERENCE_SHA:
            raise ValueError("rollback reference source mismatch")
        query = reference_build / ".cmake/api/v1/query"
        query.mkdir(parents=True)
        (query / "codemodel-v2").touch()
        command(["cmake", "-S", reference, "-B", reference_build, "-G", "Ninja",
                 "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON", "-DBUILD_IB_PROBE=OFF",
                 "-DHEPTA_ENABLE_IBAPI=OFF", "-DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF",
                 "-DHEPTA_BUILD_LEGACY_MONOLITH=OFF", "-DHEPTA_BUILD_LEGACY_SIMULATOR=OFF"])
        reference_targets = installed_executable_targets(reference, reference_build)
        command(["cmake", "--build", reference_build, "--target", *reference_targets, "--parallel", "2"])
        previous = output / f"rollback-reference-{REFERENCE_SHA}.tar.gz"
        command([sys.executable, "scripts/build_release_package.py", "--build-dir", reference_build,
                 "--output", previous, "--version", (reference / "VERSION").read_text().strip(),
                 "--profile", "core", "--source-sha", REFERENCE_SHA,
                 "--source-date-epoch", text(["git", "-C", reference, "show", "-s", "--format=%ct", "HEAD"])])
        previous_digest = digest(previous)
        try:
            # Root trust anchors come only from this exact tracked source, not
            # a loose build directory. Never chown the runner's checkout.
            archive = work / "source.tar"
            with archive.open("xb") as stream:
                command(["git", "archive", source], stdout=stream)
            host_source = Path(text(["sudo", "mktemp", "-d", "/tmp/hepta-accept-source.XXXXXXXX"]))
            if host_source.parent != Path("/tmp") or not re.fullmatch(r"hepta-accept-source\.[A-Za-z0-9]{8}", host_source.name):
                host_source = None
                raise ValueError("unexpected temporary root source path")
            command(["sudo", "tar", "-xf", archive, "--no-same-owner", "--no-same-permissions", "-C", host_source])
            command(["sudo", "chmod", "0755", host_source])
            clean = ["sudo", "env", "-i", "--chdir=" + str(host_source),
                     "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL=C", "PYTHONDONTWRITEBYTECODE=1"]
            command(clean + ["HEPTA_ISOLATED_PROCESS_TESTS=1",
                    "HEPTA_PROCESS_CANDIDATE_ARTIFACT=" + str(candidate),
                    "HEPTA_PROCESS_CANDIDATE_SHA256=" + candidate_digest,
                    "HEPTA_PROCESS_PREVIOUS_ARTIFACT=" + str(previous),
                    "HEPTA_PROCESS_PREVIOUS_SHA256=" + previous_digest,
                    "HEPTA_PROCESS_EVIDENCE_DIR=" + str(output / "process-evidence"),
                    "python3", "scripts/run_python_tests.py", "--lane", "process"])
            validate_generation_cost_evidence(
                output / "process-evidence/installed-generation-cost-curve.json",
                source, candidate_digest)
            pair_evidence = output / "process-evidence/installed-client-server-pair.json"
            command(clean + ["HEPTA_ISOLATED_PROCESS_TESTS=1", "python3",
                    "tests/research/installed_client_server_pair.py",
                    "--core-artifact", candidate, "--core-sha256", candidate_digest,
                    "--client-artifact", client_artifact, "--client-sha256", client_digest,
                    "--source-sha", source, "--output", pair_evidence])
            validate_client_pair_evidence(pair_evidence, source, candidate_digest, client_digest)
            command(clean + ["HEPTA_DISPOSABLE_SYSTEMD_TEST=1", "python3", "tests/systemd_simulator_smoke.py",
                    "--artifact", candidate, "--expected-sha256", candidate_digest,
                    "--evidence-dir", output / "systemd-evidence"])
        finally:
            if host_source is not None:
                command(["sudo", "rm", "-rf", "--", host_source])

    if text(["git", "rev-parse", "HEAD"]) != source:
        raise ValueError("checkout identity changed during acceptance")
    if digest(candidate) != candidate_digest:
        raise ValueError("candidate changed during acceptance")
    if digest(client_artifact) != client_digest:
        raise ValueError("client package changed during acceptance")
    command(["git", "diff", "--exit-code"])
    command(["git", "diff", "--cached", "--exit-code"])
    if checkout_status(allow_output=True):
        raise ValueError("checkout changed or contains untracked files during acceptance")
    receipt = {"schema": "heptatrader.core-artifact-acceptance.v1", "result": "PASS",
               "source_sha": source, "package_sha256": candidate_digest, "version": version,
               "profile": "core", "checks": list(CHECKS), "previous_source_sha": REFERENCE_SHA,
               "previous_package_sha256": previous_digest, "strategy_client_sha256": client_digest,
               "authorization_effect": "NONE",
               "paper_authorized": False, "live_authorized": False}
    descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        accept(args.build_dir, args.output_dir, args.source_sha)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"core artifact acceptance FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
