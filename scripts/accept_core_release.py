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
CHECKS = ("install", "core-python", "simulator-lifecycle", "distinct-artifact-process", "pid1-systemd")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


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
    for lane in ("install", "core"):
        command([sys.executable, "scripts/run_python_tests.py", "--lane", lane], env=environment)
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
    command(["git", "diff", "--exit-code"])
    command(["git", "diff", "--cached", "--exit-code"])
    if checkout_status(allow_output=True):
        raise ValueError("checkout changed or contains untracked files during acceptance")
    receipt = {"schema": "heptatrader.core-artifact-acceptance.v1", "result": "PASS",
               "source_sha": source, "package_sha256": candidate_digest, "version": version,
               "profile": "core", "checks": list(CHECKS), "previous_source_sha": REFERENCE_SHA,
               "previous_package_sha256": previous_digest, "authorization_effect": "NONE",
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
