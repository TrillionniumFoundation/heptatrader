#!/usr/bin/env python3
"""Long-lived crash-recoverable recorder worker for the P1 safety soak.

The worker drains canonical raw receipts produced by the independent observer
and invokes only the fixed evidence recorder.  Recorder WAL/journal lineage is
used as the source of truth after a worker crash, so an observation is never
projected twice.  Decision projection is accepted only through a sealed
coordinator request and only for the exact receipt digests in a verified
closure.  Reader-owned decision files are copied byte-for-byte into an
immutable root-owned staging directory before they cross the recorder's root
trust boundary.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
import time
from typing import Any, Mapping, Protocol, Sequence


def _bootstrap_pinned_contracts(
    candidate: Path, worker_path: Path, worker_role: str,
) -> tuple[bytes, str]:
    reason = "P1_RECORDER_WORKER_CONTRACT_BOOTSTRAP_INVALID"

    def require(condition: bool) -> None:
        if not condition:
            raise RuntimeError(reason)

    def argument(name: str) -> str:
        positions = [index for index, item in enumerate(sys.argv)
                     if item == name]
        require(len(positions) == 1 and positions[0] + 1 < len(sys.argv))
        value = sys.argv[positions[0] + 1]
        require(bool(value) and not value.startswith("--"))
        return value

    require(sys.argv.count("--run") == 1)
    runtime_path = Path(argument("--runtime-manifest"))
    expected_runtime_sha = argument(
        "--expected-runtime-manifest-file-sha256")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", expected_runtime_sha)
            is not None and runtime_path.is_absolute())

    def payload(path: Path, expected_sha: str, modes: set[int]) -> bytes:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC)
        try:
            before = os.fstat(descriptor)
            require(
                stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                before.st_uid == 0 and before.st_gid == 0 and
                stat.S_IMODE(before.st_mode) in modes and
                0 < before.st_size <= 16 * 1024 * 1024)
            value = b""
            while len(value) <= 16 * 1024 * 1024:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                value += block
            after = os.fstat(descriptor)
            require(
                len(value) <= 16 * 1024 * 1024 and
                "sha256:" + hashlib.sha256(value).hexdigest() == expected_sha and
                (before.st_dev, before.st_ino, before.st_size,
                 before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns))
            return value
        finally:
            os.close(descriptor)

    runtime_payload = payload(runtime_path, expected_runtime_sha, {0o600})
    try:
        runtime = json.loads(runtime_payload.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(reason) from error
    require(
        isinstance(runtime, dict) and runtime_payload ==
        (json.dumps(runtime, ensure_ascii=True, allow_nan=False,
                    sort_keys=True, separators=(",", ":")) + "\n").encode(
                        "ascii"))
    body = dict(runtime)
    claimed = body.pop("body_sha256", None)
    require(
        runtime.get("schema") ==
            "hepta.p1-safety-soak-campaign-runtime.v1" and
        runtime.get("status") == "FROZEN" and
        all(runtime.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access")) and
        isinstance(claimed, str) and
        claimed == "sha256:" + hashlib.sha256(
            (json.dumps(body, ensure_ascii=True, allow_nan=False,
                        sort_keys=True, separators=(",", ":")) + "\n").encode(
                            "ascii")).hexdigest())
    campaign_id = runtime.get("campaign_id")
    require(
        isinstance(campaign_id, str) and
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", campaign_id)
            is not None and
        runtime_path == Path("/var/lib/hepta/p1-safety-soak") /
            campaign_id / "runtime-manifest.json")
    executables = runtime.get("executables")
    require(isinstance(executables, dict))
    coordinator_pin = executables.get("campaign_coordinator")
    worker_pin = executables.get(worker_role)
    require(
        isinstance(coordinator_pin, dict) and
        coordinator_pin.get("path") == str(candidate) and
        isinstance(worker_pin, dict) and
        worker_pin.get("path") == str(worker_path))
    contract_payload = payload(
        candidate, coordinator_pin.get("file_sha256"),
        {0o500, 0o550, 0o555, 0o700, 0o750, 0o755})
    payload(
        worker_path, worker_pin.get("file_sha256"),
        {0o500, 0o550, 0o555, 0o700, 0o750, 0o755})
    return contract_payload, str(candidate)


def _load_contracts() -> Any:
    directory = Path(__file__).absolute().parent
    installed = directory / "hepta-p1-safety-soak-campaign-coordinator"
    source = directory / "hepta_p1_safety_soak_campaign_coordinator.py"
    worker_path = Path("/usr/libexec/hepta-p1-safety-soak-recorder-worker")
    executing = Path(__file__).resolve(strict=True)
    production = executing == worker_path
    candidate = installed if production else source
    metadata = candidate.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("P1_RECORDER_WORKER_CONTRACT_IMAGE_INVALID")
    name = "_hepta_p1_campaign_contracts_recorder"
    loader = importlib.machinery.SourceFileLoader(name, str(candidate))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise RuntimeError("P1_RECORDER_WORKER_CONTRACT_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if production:
        contract_payload, filename = _bootstrap_pinned_contracts(
            candidate, worker_path, "recorder_worker")
        exec(compile(contract_payload, filename, "exec"), module.__dict__)
    else:
        loader.exec_module(module)
    return module


C = _load_contracts()

VERSION = 1
ROOT_UID = 0
ROOT_GID = 0
READER_UID = 1000
READER_GID = 1000
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-recorder-worker")
RECORDER = "/usr/libexec/hepta-p1-safety-soak-evidence-recorder"
RECORDER_JOURNAL_SCHEMA = (
    "hepta.p1-safety-soak-recorder-journal-entry.v1")
SERVICE_SCHEMA = (
    "hepta.p1-safety-soak-independent-service-observation.v1")
CAMPAIGN_CONTINUITY_SCHEMA = (
    "hepta.p1-safety-soak-independent-campaign-continuity-observation.v1")
AUTHORITY_SCHEMA = (
    "hepta.p1-safety-soak-independent-authority-observation.v1")
FAULT_SCHEMA = "hepta.p1-safety-soak-independent-fault-observation.v1"
CLEANUP_SCHEMA = "hepta.p1-safety-soak-independent-cleanup-observation.v1"
VERIFIED_CLOSURE_SCHEMA = "hepta.bounded-shadow-campaign-closure.v1"
MAXIMUM_DECISION_BYTES = 2 * 1024 * 1024
POLL_SECONDS = 0.5

OPERATIONS = {
    "continuity": (
        CAMPAIGN_CONTINUITY_SCHEMA, "CHECKPOINT", "checkpoint"),
    "authority": (AUTHORITY_SCHEMA, "RECORD_AUTHORITY", "record-authority"),
    "fault": (FAULT_SCHEMA, "RECORD_FAULT", "record-fault"),
    "cleanup": (CLEANUP_SCHEMA, "RECORD_CLEANUP", "record-cleanup"),
}


class WorkerError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise WorkerError(reason)


def _notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
            channel.connect(address)
            channel.sendall(message.encode("utf-8"))
    except OSError:
        pass


class Runner(Protocol):
    def run(self, argv: Sequence[str], timeout_seconds: int) -> C.CommandResult: ...
    def sleep(self, seconds: float) -> None: ...


def _prepare_directory(path: Path, uid: int, gid: int) -> None:
    C._trusted_directory(
        path.parent, expected_uid=uid, expected_gid=gid)
    C._trusted_directory(
        path, expected_uid=uid, expected_gid=gid, create=True)


def _stable_reader_file(path: Path) -> tuple[bytes, dict[str, Any], str]:
    reason = "P1_RECORDER_WORKER_DECISION_INPUT_UNTRUSTED"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise WorkerError(reason) from error
    try:
        before = os.fstat(descriptor)
        _require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
            before.st_uid == READER_UID and before.st_gid == READER_GID and
            stat.S_IMODE(before.st_mode) == 0o600 and
            0 < before.st_size <= MAXIMUM_DECISION_BYTES, reason)
        payload = b""
        while len(payload) <= MAXIMUM_DECISION_BYTES:
            block = os.read(descriptor, 256 * 1024)
            if not block:
                break
            payload += block
        after = os.fstat(descriptor)
        _require(
            len(payload) <= MAXIMUM_DECISION_BYTES and
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
             before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
             after.st_ctime_ns), reason)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkerError(reason) from error
    _require(isinstance(value, dict) and C.canonical_bytes(value) == payload,
             reason)
    return payload, value, C.digest_bytes(payload)


class RecorderWorker:
    def __init__(
        self, runtime: C.Snapshot, runner: Runner, *, expected_uid: int,
        expected_gid: int,
        wall_clock: callable = lambda: time.time_ns() // 1_000_000,
        boot_clock: callable = time.monotonic_ns,
    ) -> None:
        C._validate_runtime(runtime.document)
        self.runtime_snapshot = runtime
        self.runtime = runtime.document
        self.runner = runner
        self.uid = expected_uid
        self.gid = expected_gid
        self.wall_clock = wall_clock
        self.boot_clock = boot_clock
        self.root = Path(self.runtime["state_root"])
        self.recorder_root = Path(self.runtime["recorder_root"])
        self.raw = Path(self.runtime["raw_observation_directory"])
        self.control = C.ControlQueue(
            Path(self.runtime["control_directory"]),
            self.runtime["campaign_id"], expected_uid=expected_uid,
            expected_gid=expected_gid)
        journal_directory = self.root / "recorder-worker-journal"
        staging = self.root / "staged-decisions"
        _prepare_directory(journal_directory, expected_uid, expected_gid)
        _prepare_directory(staging, expected_uid, expected_gid)
        self.staging = staging
        self.journal = C.Journal(
            journal_directory, self.runtime["campaign_id"],
            expected_uid=expected_uid, expected_gid=expected_gid,
            wall_clock=wall_clock, boot_clock=boot_clock)
        self.recorder_pin = self.runtime["executables"]["evidence_recorder"]
        _require(self.recorder_pin["path"] == RECORDER,
                 "P1_RECORDER_WORKER_HELPER_PATH_DRIFT")
        self.spec_snapshot = C._open_reference(
            self.runtime["campaign_spec"], expected_uid=self.uid,
            expected_gid=self.gid,
            reason="P1_RECORDER_WORKER_SPEC_REFERENCE_DRIFT")
        self.spec = self.spec_snapshot.document
        self._stop = False
        self._recovered = False

    def stop(self, _signum: int, _frame: Any) -> None:
        self._stop = True

    def _verify_recorder(self) -> None:
        C._secure_executable(
            Path(self.recorder_pin["path"]),
            self.recorder_pin["file_sha256"], expected_uid=self.uid,
            expected_gid=self.gid)

    def _run_recorder(
        self, argv: Sequence[str], *, timeout: int = 180,
        allow_unsafe: bool = False,
    ) -> C.CommandResult:
        _require(argv[0] == RECORDER,
                 "P1_RECORDER_WORKER_COMMAND_PATH_DRIFT")
        self._verify_recorder()
        result = self.runner.run(argv, timeout)
        self._verify_recorder()
        _require(
            result.returncode == 0 or
            (allow_unsafe and result.returncode == 3),
            "P1_RECORDER_WORKER_RECORDER_FAILED")
        return result

    def recover(self) -> None:
        self._run_recorder([
            RECORDER, "--run", "recover", "--root", str(self.recorder_root)],
            timeout=120)
        self._recovered = True

    def _event(self, name: str, status_value: str,
               details: Mapping[str, Any]) -> dict[str, Any]:
        value = self.journal.append(name, status_value, details)
        _notify("WATCHDOG=1\nSTATUS=" + name + ":" + status_value)
        return value

    def fail_closed(self, reason: str) -> None:
        """Make any in-process worker failure durable before a restart."""

        if self.journal.failed:
            return
        self._event("WORKER", "FAILED_CLOSED", {
            "worker": "recorder", "reason": reason, "catch_up": False,
        })

    def _committed(self, event: str, **details: Any) -> bool:
        return any(item["status"] == "COMMITTED"
                   for item in self.journal.matching(event, **details))

    def _intent(self, event: str, **details: Any) -> bool:
        return any(item["status"] == "INTENT"
                   for item in self.journal.matching(event, **details))

    def _recorder_entries(self) -> list[C.Snapshot]:
        directory = self.recorder_root / "journal"
        names = sorted(item.name for item in directory.iterdir()
                       if not item.name.startswith("."))
        _require(
            names == [f"{index:08d}.json" for index in range(len(names))],
            "P1_RECORDER_WORKER_RECORDER_JOURNAL_GAP")
        entries: list[C.Snapshot] = []
        previous: str | None = None
        for index, name in enumerate(names):
            snapshot = C._secure_read(
                directory / name, expected_uid=self.uid,
                expected_gid=self.gid, modes=frozenset({0o600}))
            value = snapshot.document
            _require(
                value.get("schema") == RECORDER_JOURNAL_SCHEMA and
                value.get("sequence") == index and
                value.get("previous_entry_body_sha256") == previous and
                isinstance(value.get("inputs"), list) and
                isinstance(value.get("outputs"), list),
                "P1_RECORDER_WORKER_RECORDER_JOURNAL_INVALID")
            previous = snapshot.body_sha256
            entries.append(snapshot)
        return entries

    def _committed_input(
        self, observation: C.Snapshot, operation: str,
    ) -> list[dict[str, Any]] | None:
        for entry in self._recorder_entries():
            value = entry.document
            if value.get("operation") != operation:
                continue
            if any(
                isinstance(item, dict) and
                item.get("path") == str(observation.path) and
                item.get("file_sha256") == observation.file_sha256 and
                item.get("body_sha256") == observation.body_sha256
                for item in value["inputs"]
            ):
                return [dict(item) for item in value["outputs"]]
        return None

    def _committed_entry(
        self, observation: C.Snapshot, operation: str,
    ) -> C.Snapshot | None:
        for entry in self._recorder_entries():
            value = entry.document
            if value.get("operation") != operation:
                continue
            if any(
                isinstance(item, dict) and
                item.get("path") == str(observation.path) and
                item.get("file_sha256") == observation.file_sha256 and
                item.get("body_sha256") == observation.body_sha256
                for item in value["inputs"]
            ):
                return entry
        return None

    def _raw_snapshots(self, kind: str, schema: str) -> list[C.Snapshot]:
        directory = self.raw / kind
        values: list[C.Snapshot] = []
        for path in sorted(directory.iterdir()):
            if path.name.startswith("."):
                continue
            snapshot = C._secure_read(
                path, expected_uid=self.uid, expected_gid=self.gid,
                modes=frozenset({0o600}))
            _require(
                snapshot.document.get("schema") == schema and
                snapshot.document.get("campaign_id") ==
                    self.runtime["campaign_id"] and
                snapshot.document.get("production_mode") ==
                    "PRODUCTION_ROOT_OBSERVER",
                "P1_RECORDER_WORKER_RAW_OBSERVATION_INVALID")
            values.append(snapshot)
        return values

    def _project_observation(self, kind: str, observation: C.Snapshot) -> None:
        _schema, operation, command = OPERATIONS[kind]
        key = observation.file_sha256
        if self._committed("PROJECT_OBSERVATION", input_file_sha256=key):
            return
        if not self._intent("PROJECT_OBSERVATION", input_file_sha256=key):
            self._event("PROJECT_OBSERVATION", "INTENT", {
                "kind": kind, "input": C._reference(observation),
                "input_file_sha256": key,
            })
        outputs = self._committed_input(observation, operation)
        unsafe = False
        if outputs is None:
            expires = observation.document.get("expires_at_ms")
            _require(type(expires) is int and int(self.wall_clock()) < expires,
                     "P1_RECORDER_WORKER_OBSERVATION_EXPIRED")
            result = self._run_recorder([
                RECORDER, "--run", command, "--root", str(self.recorder_root),
                "--observation", str(observation.path),
            ], allow_unsafe=True)
            unsafe = result.returncode == 3
            outputs = self._committed_input(observation, operation)
        _require(outputs is not None,
                 "P1_RECORDER_WORKER_COMMIT_NOT_FOUND")
        self._event("PROJECT_OBSERVATION", "COMMITTED", {
            "kind": kind, "input_file_sha256": key, "outputs": outputs,
            "unsafe": unsafe,
        })
        _require(not unsafe, "P1_RECORDER_WORKER_UNSAFE_EVIDENCE")

    def drain_observations(self) -> None:
        # Campaign continuity is independent of formal marker/controller/
        # reader state.  Formal service receipts remain raw historical clock
        # anchors and must never be projected into the continuity stream.
        # Project fault recovery first so any gateway identity transition in
        # the next checkpoint is already bound to its immutable fault result.
        for fault in self._raw_snapshots("fault", FAULT_SCHEMA):
            self._project_observation("fault", fault)
        for continuity in self._raw_snapshots(
                "continuity", CAMPAIGN_CONTINUITY_SCHEMA):
            self._project_observation("continuity", continuity)
        services = self._raw_snapshots("service", SERVICE_SCHEMA)
        authorities = {
            item.path.name: item
            for item in self._raw_snapshots("authority", AUTHORITY_SCHEMA)
        }
        for service in services:
            authority = authorities.get(service.path.name)
            # The observer publishes the service receipt first.  Seeing that
            # half of the pair is a normal bounded race, not evidence loss.
            if authority is None:
                continue
            self._project_observation("authority", authority)
        service_names = {item.path.name for item in services}
        _require(set(authorities).issubset(service_names),
                 "P1_RECORDER_WORKER_OBSERVATION_PAIR_GAP")
        for cleanup in self._raw_snapshots("cleanup", CLEANUP_SCHEMA):
            self._project_observation("cleanup", cleanup)

    def _closure(self, reference: Mapping[str, Any]) -> C.Snapshot:
        snapshot = C._open_reference(
            reference, expected_uid=self.uid, expected_gid=self.gid,
            reason="P1_RECORDER_WORKER_CLOSURE_DRIFT")
        value = snapshot.document
        _require(
            value.get("schema") == VERIFIED_CLOSURE_SCHEMA and
            value.get("campaign_id") in {
                item["formal_campaign_id"]
                for item in self.runtime["formal_campaigns"]
            } and isinstance(value.get("iterations"), list) and
            bool(value["iterations"]),
            "P1_RECORDER_WORKER_CLOSURE_INVALID")
        return snapshot

    def _stage_decisions(
        self, formal_id: str, closure: C.Snapshot, artifact_root: Path,
    ) -> list[Path]:
        _require(
            artifact_root.is_absolute() and
            artifact_root == Path(
                "/var/lib/hepta/p1-admission/readers") / formal_id /
                "observer",
            "P1_RECORDER_WORKER_ARTIFACT_ROOT_INVALID")
        required = [
            item.get("decision_receipt_file_sha256")
            for item in closure.document["iterations"]
        ]
        _require(
            all(isinstance(item, str) and C.DIGEST.fullmatch(item)
                for item in required) and len(required) == len(set(required)),
            "P1_RECORDER_WORKER_DECISION_DIGEST_SET_INVALID")
        receipts = artifact_root / "receipts"
        metadata = receipts.lstat()
        _require(
            stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == READER_UID and metadata.st_gid == READER_GID
            and stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
            "P1_RECORDER_WORKER_RECEIPT_DIRECTORY_INVALID")
        by_digest: dict[str, tuple[bytes, dict[str, Any]]] = {}
        for path in sorted(receipts.iterdir()):
            if path.name.startswith("."):
                continue
            payload, value, file_sha = _stable_reader_file(path)
            if file_sha in required:
                _require(file_sha not in by_digest,
                         "P1_RECORDER_WORKER_DECISION_DUPLICATE")
                by_digest[file_sha] = (payload, value)
        _require(set(by_digest) == set(required),
                 "P1_RECORDER_WORKER_DECISION_SET_INCOMPLETE")
        formal_directory = self.staging / formal_id
        _prepare_directory(formal_directory, self.uid, self.gid)
        outputs: list[Path] = []
        for index, file_sha in enumerate(required):
            path = formal_directory / f"{index:08d}.json"
            payload, value = by_digest[file_sha]
            if path.exists():
                snapshot = C._secure_read(
                    path, expected_uid=self.uid, expected_gid=self.gid,
                    modes=frozenset({0o600}), sealed=False)
                _require(snapshot.payload == payload,
                         "P1_RECORDER_WORKER_STAGED_DECISION_DRIFT")
            else:
                C.publish_noreplace(
                    path, value, expected_uid=self.uid, expected_gid=self.gid)
                snapshot = C._secure_read(
                    path, expected_uid=self.uid, expected_gid=self.gid,
                    modes=frozenset({0o600}), sealed=False)
                _require(snapshot.file_sha256 == file_sha,
                         "P1_RECORDER_WORKER_STAGED_DECISION_DRIFT")
            outputs.append(path)
        return outputs

    def _clock_from_projection_entry(
        self, entry: C.Snapshot,
    ) -> C.Snapshot:
        values = [
            item for item in entry.document["inputs"]
            if isinstance(item, dict) and
            item.get("role") == "decision_clock_observation"
        ]
        _require(len(values) == 1,
                 "P1_RECORDER_WORKER_CLOCK_INPUT_MISSING")
        reference = {
            key: values[0].get(key)
            for key in ("path", "file_sha256", "body_sha256")
        }
        return C._open_reference(
            reference, expected_uid=self.uid, expected_gid=self.gid,
            reason="P1_RECORDER_WORKER_CLOCK_INPUT_DRIFT")

    def _used_projection_clock_paths(self) -> set[str]:
        used: set[str] = set()
        for entry in self._recorder_entries():
            if entry.document.get("operation") != "PROJECT_DECISIONS":
                continue
            used.add(str(self._clock_from_projection_entry(entry).path))
        for entry in self.journal.entries:
            value = entry.document
            if (value["event"] == "CONTROL_PROJECT" and
                    value["status"] == "COMMITTED"):
                reference = value["details"].get("clock_observation")
                _require(isinstance(reference, dict) and
                         type(reference.get("path")) is str,
                         "P1_RECORDER_WORKER_CLOCK_LINEAGE_INVALID")
                used.add(reference["path"])
        return used

    def _select_projection_clock(
        self, closure: C.Snapshot,
    ) -> C.Snapshot | None:
        iterations = closure.document.get("iterations")
        _require(isinstance(iterations, list) and bool(iterations) and
                 isinstance(iterations[-1], dict),
                 "P1_RECORDER_WORKER_CLOSURE_INVALID")
        final_evaluated_ms = iterations[-1].get("evaluated_at_ms")
        maximum_gap_ns = self.spec.get("maximum_checkpoint_gap_ns")
        _require(
            type(final_evaluated_ms) is int and
            type(maximum_gap_ns) is int and maximum_gap_ns > 0,
            "P1_RECORDER_WORKER_CLOCK_WINDOW_INVALID")
        maximum_gap_ms = maximum_gap_ns // 1_000_000
        formal = next((
            item for item in self.runtime["formal_campaigns"]
            if item["formal_campaign_id"] ==
                closure.document.get("campaign_id")), None)
        _require(formal is not None,
                 "P1_RECORDER_WORKER_CLOCK_FORMAL_INVALID")
        used = self._used_projection_clock_paths()
        candidates: list[tuple[int, str, C.Snapshot]] = []
        for snapshot in self._raw_snapshots("service", SERVICE_SCHEMA):
            value = snapshot.document
            observed_at_ms = value.get("observed_at_ms")
            if str(snapshot.path) in used:
                continue
            if (
                value.get("clock_id") == "CLOCK_BOOTTIME" and
                value.get("boot_id") == self.runtime["boot_id"] and
                type(observed_at_ms) is int and
                final_evaluated_ms <= observed_at_ms <=
                    final_evaluated_ms + maximum_gap_ms and
                observed_at_ms <= formal["expires_at_ms"]
            ):
                candidates.append(
                    (observed_at_ms, str(snapshot.path), snapshot))
        return min(candidates)[2] if candidates else None

    def _publish_ack(
        self, request: Mapping[str, Any], outputs: Sequence[Mapping[str, Any]],
    ) -> None:
        entries = self.control._load_chain("recorder", "acks")
        if any(item.document["request_id"] == request["request_id"]
               for item in entries):
            return
        document = C.seal({
            "schema": C.ACK_SCHEMA, "version": C.VERSION,
            "campaign_id": self.runtime["campaign_id"],
            "sequence": len(entries), "request_id": request["request_id"],
            "worker": "recorder", "action": request["action"],
            "status": "COMPLETE", "completed_at_ms": int(self.wall_clock()),
            "outputs": [dict(item) for item in outputs],
            "previous_body_sha256": (
                None if not entries else entries[-1].body_sha256),
            **C.boundary(),
        })
        C.publish_noreplace(
            self.control._directory("recorder", "acks") /
            f"{len(entries):08d}.json", document,
            expected_uid=self.uid, expected_gid=self.gid)

    def _process_projection(self, request: Mapping[str, Any]) -> bool:
        arguments = request["arguments"]
        _require(
            isinstance(arguments, dict) and set(arguments) == {
                "formal_campaign_id", "verified_closure", "artifact_root",
            } and C.FORMAL_ID.fullmatch(
                str(arguments.get("formal_campaign_id"))) is not None,
            "P1_RECORDER_WORKER_PROJECTION_REQUEST_INVALID")
        formal_id = arguments["formal_campaign_id"]
        closure = self._closure(arguments["verified_closure"])
        _require(closure.document["campaign_id"] == formal_id,
                 "P1_RECORDER_WORKER_PROJECTION_CAMPAIGN_DRIFT")
        formal = next((
            item for item in self.runtime["formal_campaigns"]
            if item["formal_campaign_id"] == formal_id), None)
        _require(
            formal is not None and
            request["deadline_ms"] == formal["projection_deadline_ms"] and
            int(self.wall_clock()) <= formal["projection_deadline_ms"],
            "P1_RECORDER_WORKER_PROJECTION_DEADLINE_INVALID")
        event_details = {"request_id": request["request_id"]}
        if not self._intent("CONTROL_PROJECT", **event_details):
            self._event("CONTROL_PROJECT", "INTENT", {
                **event_details, "closure": C._reference(closure),
            })
        entry = self._committed_entry(closure, "PROJECT_DECISIONS")
        clock_observation: C.Snapshot | None = None
        if entry is not None:
            outputs = [dict(item) for item in entry.document["outputs"]]
            clock_observation = self._clock_from_projection_entry(entry)
        else:
            outputs = None
            clock_observation = self._select_projection_clock(closure)
            if clock_observation is None:
                _notify("WATCHDOG=1\nSTATUS=waiting for post-closure clock anchor")
                return False
            decisions = self._stage_decisions(
                formal_id, closure, Path(arguments["artifact_root"]))
            argv = [
                RECORDER, "--run", "project-decisions", "--root",
                str(self.recorder_root), "--verified-closure",
                str(closure.path),
            ]
            for path in decisions:
                argv += ["--decision", str(path)]
            argv += ["--clock-observation", str(clock_observation.path)]
            result = self._run_recorder(argv, timeout=1800, allow_unsafe=True)
            _require(result.returncode == 0,
                     "P1_RECORDER_WORKER_PROJECTION_UNSAFE")
            entry = self._committed_entry(closure, "PROJECT_DECISIONS")
            _require(entry is not None,
                     "P1_RECORDER_WORKER_PROJECTION_COMMIT_MISSING")
            outputs = [dict(item) for item in entry.document["outputs"]]
            committed_clock = self._clock_from_projection_entry(entry)
            _require(
                C._reference(committed_clock) ==
                    C._reference(clock_observation),
                "P1_RECORDER_WORKER_CLOCK_COMMIT_DRIFT")
        _require(outputs is not None and bool(outputs) and
                 clock_observation is not None,
                 "P1_RECORDER_WORKER_PROJECTION_COMMIT_MISSING")
        if not self._committed("CONTROL_PROJECT", **event_details):
            self._event("CONTROL_PROJECT", "COMMITTED", {
                **event_details, "outputs": outputs,
                "clock_observation": C._reference(clock_observation),
            })
        self._publish_ack(request, outputs)
        return True

    def _process_drain(self, request: Mapping[str, Any]) -> None:
        arguments = request["arguments"]
        _require(
            isinstance(arguments, dict) and set(arguments) == {
                "required_observer_request_id", "required_output",
            }, "P1_RECORDER_WORKER_DRAIN_REQUEST_INVALID")
        raw_ref = C._validate_reference(
            arguments["required_output"],
            "P1_RECORDER_WORKER_DRAIN_REQUEST_INVALID")
        observation = C._open_reference(
            raw_ref, expected_uid=self.uid, expected_gid=self.gid,
            reason="P1_RECORDER_WORKER_DRAIN_INPUT_DRIFT")
        outputs = self._committed_input(observation, "RECORD_CLEANUP")
        _require(outputs is not None,
                 "P1_RECORDER_WORKER_DRAIN_NOT_COMPLETE")
        self._publish_ack(request, outputs)

    def process_requests(self) -> None:
        requests = self.control._load_chain("recorder", "requests")
        acks = self.control._load_chain("recorder", "acks")
        completed = {item.document["request_id"] for item in acks}
        for snapshot in requests:
            request = snapshot.document
            if request["request_id"] in completed:
                continue
            _require(
                request["target"] == "recorder" and
                request["action"] in {"PROJECT_DECISIONS", "DRAIN"} and
                int(self.wall_clock()) <= request["deadline_ms"],
                "P1_RECORDER_WORKER_REQUEST_INVALID_OR_EXPIRED")
            if request["action"] == "PROJECT_DECISIONS":
                self._process_projection(request)
            else:
                self._process_drain(request)

    def step(self) -> None:
        _require(not self.journal.failed,
                 "P1_RECORDER_WORKER_PREVIOUSLY_FAILED_CLOSED")
        if not self._recovered:
            self.recover()
        self.drain_observations()
        self.process_requests()
        _notify("WATCHDOG=1")

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        try:
            self.recover()
            _notify("READY=1\nSTATUS=recorder worker active")
            while not self._stop:
                self.step()
                self.runner.sleep(POLL_SECONDS)
        except Exception as error:
            reason = getattr(
                error, "reason", "P1_RECORDER_WORKER_UNEXPECTED_FAILURE")
            try:
                self.fail_closed(reason)
            except Exception as durability_error:
                raise WorkerError(
                    "P1_RECORDER_WORKER_FAILURE_DURABILITY_FAILED"
                ) from durability_error
            raise
        _notify("STOPPING=1\nSTATUS=recorder worker stopped")


def _bind_image(runtime: C.Snapshot) -> None:
    reason = "P1_RECORDER_WORKER_INSTALLED_IMAGE_REQUIRED"
    try:
        executing = Path(__file__).resolve(strict=True)
        _require(
            executing == INSTALLED_EXECUTABLE and
            os.path.samefile(executing, INSTALLED_EXECUTABLE), reason)
    except OSError as error:
        raise WorkerError(reason) from error
    pin = runtime.document["executables"]["recorder_worker"]
    _require(pin["path"] == str(INSTALLED_EXECUTABLE), reason)
    C._secure_executable(
        INSTALLED_EXECUTABLE, pin["file_sha256"],
        expected_uid=ROOT_UID, expected_gid=ROOT_GID)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument(
        "--expected-runtime-manifest-file-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    lock: int | None = None
    try:
        _require(arguments.run, "P1_RECORDER_WORKER_EXPLICIT_RUN_REQUIRED")
        _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
                 "P1_RECORDER_WORKER_ROOT_REQUIRED")
        runtime = C._secure_read(
            arguments.runtime_manifest, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o600}))
        _require(
            runtime.file_sha256 ==
                arguments.expected_runtime_manifest_file_sha256,
            "P1_RECORDER_WORKER_RUNTIME_DIGEST_DRIFT")
        C._validate_runtime(runtime.document)
        _bind_image(runtime)
        lock_path = Path(runtime.document["state_root"]) / ".recorder-worker.lock"
        lock = os.open(
            lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) |
            os.O_CLOEXEC, 0o600)
        os.fchmod(lock, 0o600)
        metadata = os.fstat(lock)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_uid == ROOT_UID and
            metadata.st_gid == ROOT_GID and metadata.st_nlink == 1,
            "P1_RECORDER_WORKER_LOCK_INVALID")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkerError("P1_RECORDER_WORKER_ALREADY_RUNNING") from error
        RecorderWorker(
            runtime, C.ProductionAdapter(), expected_uid=ROOT_UID,
            expected_gid=ROOT_GID).run_forever()
        return 0
    except (WorkerError, C.CoordinatorError) as error:
        reason = getattr(error, "reason", "P1_RECORDER_WORKER_FAILED")
        print("hepta_p1_safety_soak_recorder_worker: FAIL " + reason,
              file=sys.stderr)
        _notify("STOPPING=1\nSTATUS=failed closed: " + reason)
        return 4
    except Exception:
        print(
            "hepta_p1_safety_soak_recorder_worker: FAIL "
            "P1_RECORDER_WORKER_UNEXPECTED_FAILURE", file=sys.stderr)
        _notify("STOPPING=1\nSTATUS=failed closed")
        return 4
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
