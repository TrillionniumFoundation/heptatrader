#!/usr/bin/env python3
"""Pair two digest-pinned installed packages on a disposable broker-free Linux host.

This is test orchestration over the existing InstalledRuntime fixture, not a new
service or SDK. The application/native client comes ONLY from the relocated SDK;
Gateway/Execution come ONLY from the admitted core release. No trading host use.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/python"))
from test_installed_runtime_processes import (AGENT_UID, CLEAN_ENV, HOST_INTERLOCK, TEST_GID, InstalledRuntime, admitted_slot, canonical_digest,
    release_smoke)
from native_sdk_package_behavior import HEADERS, LIBRARIES

# This acceptance profile uses the ordinary Linux SDK install layout, not a
# promise about every custom GNUInstallDirs layout or another OS/architecture.
CLIENT_FILES = ({"include/HeptaStrategyClient/" + name for name in HEADERS}
    | {"lib/" + name for name in LIBRARIES}
    | {"lib/cmake/HeptaStrategyClient/" + name for name in (
        "HeptaStrategyClientConfig.cmake", "HeptaStrategyClientConfigVersion.cmake",
        "HeptaStrategyClientTargets.cmake", "HeptaStrategyClientTargets-release.cmake")}
    | {"share/HeptaStrategyClient/" + name for name in (
        "strategy-client-build-info.txt", "CLIENT_PACKAGE.md", "STRATEGY-GATEWAY.md", "strategy_gateway.py")}
    | {"bin/hepta-strategy-native", "bin/hepta-strategy-gateway"})
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


def client_members(data: bytes, source_sha: str) -> tuple[dict, dict]:
    """Validate ALL captured members before extraction or execution; no links."""
    if len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
        raise ValueError("exact source identity required")
    if len(data) > MAX_PACKAGE_BYTES:
        raise ValueError("client package size bound")
    files, seen, total = {}, set(), 0
    directories = {"."}
    for name in CLIENT_FILES:
        directories.update(str(p) for p in PurePosixPath(name).parents)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            name = member.name[2:] if member.name.startswith("./") else member.name
            path = PurePosixPath(name)
            if (not name or "\\" in name or path.is_absolute() or str(path) != name
                    or ".." in path.parts or name in seen or len(seen) >= 128):
                raise ValueError("noncanonical/duplicate client package member")
            seen.add(name)
            if member.isdir():
                if name not in directories or member.mode != 0o755:
                    raise ValueError("unexpected client directory or mode")
                continue
            if (not member.isfile() or name not in CLIENT_FILES or member.size < 0
                    or member.size > MAX_PACKAGE_BYTES or member.mode not in (0o644, 0o755)):
                raise ValueError("unexpected client file/type/mode")
            if name.startswith("bin/") and member.mode != 0o755:
                raise ValueError("client executable mode required")
            total += member.size
            if total > MAX_PACKAGE_BYTES:
                raise ValueError("client expanded size bound")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("missing client payload")
            with stream:
                payload = stream.read(member.size + 1)
            if len(payload) != member.size:
                raise ValueError("incomplete client payload")
            files[name] = (payload, member.mode)
    if set(files) != CLIENT_FILES:
        raise ValueError("incomplete client package inventory")
    metadata = {}
    for line in files["share/HeptaStrategyClient/strategy-client-build-info.txt"][0].decode("ascii").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in metadata:
            raise ValueError("invalid/duplicate client metadata")
        metadata[key] = value
    expected = {"package": "HeptaStrategyClient", "source_sha": source_sha,
        "source_tree_state": "clean", "system": "Linux", "processor": platform.machine(),
        "build_type": "Release", "wire_protocol": "HTT1", "transport": "local-unix-only",
        "execution_authority": "none", "broker_transport": "none"}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("client source/platform/authority metadata mismatch")
    return files, metadata


def install_client(artifact: Path, digest: str, source_sha: str, work: Path) -> tuple[Path, dict]:
    if not canonical_digest(digest):
        raise ValueError("explicit canonical client SHA-256 required")
    work.mkdir(mode=0o755)
    snapshot = release_smoke._snapshot_artifact(artifact, work / "input/client.tar.gz", digest)
    if snapshot.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("client package size bound")
    files, metadata = client_members(snapshot.read_bytes(), source_sha)
    original, relocated = work / "original", work / "relocated"
    original.mkdir(mode=0o755)
    for name, (payload, mode) in files.items():
        target = original / name
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(payload)
        target.chmod(mode)
    original.rename(relocated)
    if original.exists():
        raise RuntimeError("client original prefix still exists")
    return relocated, metadata


@contextmanager
def isolated_interlock():
    """Use the existing fixture's host path; never adopt or remove prior state."""
    if sys.platform != "linux" or os.geteuid() != 0 or os.environ.get("HEPTA_ISOLATED_PROCESS_TESTS") != "1":
        raise RuntimeError("explicit disposable root Linux fixture required")
    HOST_INTERLOCK.mkdir(mode=0o711)
    HOST_INTERLOCK.chmod(0o711)
    lock = HOST_INTERLOCK / "session-lease-terminal-cleanup.lock"
    lock.touch(mode=0o644, exist_ok=False)
    lock.chmod(0o644)
    identities = [(p, (p.stat().st_dev, p.stat().st_ino)) for p in (lock, HOST_INTERLOCK)]
    try:
        yield
    finally:
        for path, identity in identities:
            observed = path.lstat()
            if (observed.st_dev, observed.st_ino) != identity or observed.st_uid != 0:
                raise RuntimeError("fixture interlock changed; refusing cleanup")
        lock.unlink()
        HOST_INTERLOCK.rmdir()


CRASH_BEFORE_SEND = '''
import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location("installed_application", sys.argv[1])
g = importlib.util.module_from_spec(spec); sys.modules[spec.name] = g; spec.loader.exec_module(g)
t = g.NativeStrategyTransport(sys.argv[2], sys.argv[3], sys.argv[4], 3000)
original = t.call
def intercepted(operation, **kwargs):
    if operation == "submit": os.kill(os.getpid(), signal.SIGKILL)
    return original(operation, **kwargs)
t.call = intercepted
g.StrategyGateway(t, g.ApplicationStore(sys.argv[5])).submit(sys.argv[6])
raise RuntimeError("pre-send crash boundary was not exercised")
'''


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def exercise(core: Path, core_digest: str, client: Path, client_digest: str,
             source_sha: str, output: Path) -> dict:
    if not canonical_digest(core_digest) or len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
        raise ValueError("explicit source/core artifact identity required")
    if output.exists() or output.is_symlink():
        raise ValueError("refusing to replace pair acceptance")
    with isolated_interlock(), tempfile.TemporaryDirectory(prefix="ht-sdk-pair-", dir="/tmp") as temporary:
        root = Path(temporary); root.chmod(0o755)
        slot, manifest = admitted_slot(core, core_digest, root / "core")
        require(manifest["source_sha"] == source_sha, "core source identity differs")
        sdk, info = install_client(client, client_digest, source_sha, root / "client")
        require(info["release_label"] == manifest["version"], "client/core release labels differ")
        require(not any(p.name.startswith("hepta-strategy-") or p.name == "strategy_gateway.py"
                        for p in slot.rglob("*")), "developer client leaked into core payload")
        runtime = InstalledRuntime(root / "runtime")
        store = runtime.root / "agent/applications"
        token = runtime.root / "agent/token"
        socket = runtime.root / "g/tool.sock"
        launcher = sdk / "bin/hepta-strategy-gateway"
        def arguments(operation, key, extra=()):
            return [sys.executable, "-I", "-B", str(launcher), operation,
                "--socket", str(socket), "--token-file", str(token), "--store", str(store),
                "--key", key, "--timeout-ms", "3000", *extra]
        def start(argv):
            return subprocess.Popen(argv, env=CLEAN_ENV, user=AGENT_UID, group=TEST_GID,
                extra_groups=[], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True)
        def finish(child):
            try:
                out, err = child.communicate(timeout=15)
                return child.returncode, out, err
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=5)
                child.stdout.close(); child.stderr.close()
        def call(operation, key, extra=(), reject=False):
            code, out, err = finish(start(arguments(operation, key, extra)))
            if reject:
                require(code == 2, "expected client refusal: " + repr((code, out, err)))
                return None
            require(code == 0, "installed application failed: " + repr((code, out, err)))
            return json.loads(out)
        def records():
            raw = (runtime.root / "es/oms-journal.jsonl").read_bytes()
            if raw.startswith(b"\x1f\x8b"):
                raw = gzip.decompress(raw)
            return [json.loads(line) for line in raw.splitlines()]
        def sends():
            return sum(r.get("event") == "place_send_attempt" for r in records())
        def snapshot(key):
            directory = store / hashlib.sha256(key.encode("ascii")).hexdigest()
            return {p.relative_to(directory).as_posix(): p.read_bytes()
                    for p in directory.rglob("*") if p.is_file()}
        intent = ["--instrument", "EUR.USD", "--symbol", "EUR", "--sec-type", "CASH",
            "--exchange", "IDEALPRO", "--currency", "USD", "--side", "BUY", "--quantity", "7",
            "--limit-price", "1.0", "--reference-price", "1.1001", "--expires-at-ms",
            str(time.time_ns() // 1000000 + 120000)]
        try:
            runtime.start(slot); runtime.provision()
            first = call("prepare", "sent", intent)
            require(first["prepared"] is True and sends() == 0, "preparation caused a send")
            children = []
            try:
                for _ in range(4):
                    children.append(start(arguments("submit", "sent")))
                results = [finish(child) for child in children]
            finally:
                for child in children:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=5)
                    child.stdout.close(); child.stderr.close()
            require(all(code == 0 for code, _, _ in results), "concurrent client failure: " + repr(results))
            envelopes = [json.loads(out) for _, out, _ in results]
            require(sorted(r["tool"] for r in envelopes) == ["execution.get_command_status"] * 3 + ["trade.place_order"],
                    "concurrent application retries selected mutation")
            require(sends() == 1 and runtime.send_count() == 1, "expected exactly one real send: " + repr((envelopes, records())))
            # Exercise non-sending compatibility checks after the timed first
            # send. Do not spend the service's unchanged five-second preview
            # window on unrelated sequential process-startup assertions.
            require(call("prepare", "sent", intent) == first, "preparation identity changed")
            changed = list(intent); changed[changed.index("--quantity") + 1] = "8"
            call("prepare", "sent", changed, reject=True)
            before = snapshot("sent")
            require(before["possibly-sent"].decode() == first["command_id"], "wrong durable marker")
            second = call("prepare", "not-sent", intent)
            child = start([sys.executable, "-I", "-B", "-c", CRASH_BEFORE_SEND,
                str(sdk / "share/HeptaStrategyClient/strategy_gateway.py"), str(sdk / "bin/hepta-strategy-native"),
                str(socket), str(token), str(store), "not-sent"])
            require(finish(child)[0] == -signal.SIGKILL, "did not kill client before native send")
            require(snapshot("not-sent")["possibly-sent"].decode() == second["command_id"], "pre-send marker missing")
            require(call("submit", "not-sent")["tool"] == "execution.get_command_status" and sends() == 1,
                    "unknown command was resubmitted")
            # Kill ONLY our installed Execution process, account for that expected
            # exit, then use the unchanged fixture to stop/restart the services.
            selected = [p for p in runtime.processes if p[3] == "hepta-executiond"]
            require(len(selected) == 1, "missing installed Execution process")
            process, stream, _, _ = selected[0]
            process.kill(); process.wait(timeout=5); stream.close()
            require(process.returncode == -signal.SIGKILL, "Execution crash not exercised")
            runtime.processes.remove(selected[0])
            runtime.stop(); runtime.start(slot)
            for key in ("sent", "not-sent"):
                require(call("submit", key)["tool"] == "execution.get_command_status", "restart caused resend")
            original_token = token.read_bytes()
            try:
                token.write_bytes(b"T" * 32)
                call("inspect", "sent", reject=True)
            finally:
                token.write_bytes(original_token)
            require(snapshot("sent") == before, "recovery changed original application/request bytes")
            require(sends() == 1 and runtime.send_count() == 1, "restart duplicated venue sends")
            require(runtime.position() == 0, "unexpected economic position")
            runtime.wait_no_orders()
            receipt = {"schema": "hepta.installed-client-server-pair.v1", "result": "PASS",
                "source_sha": source_sha, "core_sha256": core_digest, "client_sha256": client_digest,
                "client_build_info": info, "client_uid": AGENT_UID,
                "processes": list(runtime.observed_processes), "concurrent_clients": 4,
                "prepared_commands": [first["command_id"], second["command_id"]],
                "place_send_attempts": sends(), "place_sent_records": runtime.send_count(),
                "restart_status_only": True, "pre_send_crash_status_only": True,
                "intent_conflict_rejected": True, "binding_change_rejected": True,
                "original_request_bytes_preserved": True, "relocated_client": True,
                "final_position": 0, "final_active_orders": [], "broker_io": False,
                "systemd_manager_exercised": False, "authorization_effect": "NONE"}
        finally:
            runtime.stop()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("core-artifact", "core-sha256", "client-artifact", "client-sha256", "source-sha", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    receipt = exercise(Path(args.core_artifact), args.core_sha256, Path(args.client_artifact),
                       args.client_sha256, args.source_sha, Path(args.output))
    print(json.dumps(receipt, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
