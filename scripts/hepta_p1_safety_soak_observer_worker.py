#!/usr/bin/env python3
"""Long-lived root observer worker for a frozen P1 SHADOW safety soak.

The worker samples the fixed independent observer at a cadence no greater
than fifteen minutes, observes each immutable fault-injection receipt once,
and services explicit cleanup requests.  It never synthesizes a late sample:
missed slots are durably recorded as missed ranges and sampling resumes only
at the current/future slot.  The fixed observer helper remains the producer of
every raw receipt and therefore performs its own installed-image attestation.
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
    reason = "P1_OBSERVER_WORKER_CONTRACT_BOOTSTRAP_INVALID"

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

    runtime_payload = payload(
        runtime_path, expected_runtime_sha, {0o600})
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
    worker_path = Path("/usr/libexec/hepta-p1-safety-soak-observer-worker")
    executing = Path(__file__).resolve(strict=True)
    production = executing == worker_path
    candidate = installed if production else source
    metadata = candidate.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("P1_OBSERVER_WORKER_CONTRACT_IMAGE_INVALID")
    name = "_hepta_p1_campaign_contracts_observer"
    loader = importlib.machinery.SourceFileLoader(name, str(candidate))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise RuntimeError("P1_OBSERVER_WORKER_CONTRACT_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if production:
        contract_payload, filename = _bootstrap_pinned_contracts(
            candidate, worker_path, "observer_worker")
        exec(compile(contract_payload, filename, "exec"), module.__dict__)
    else:
        loader.exec_module(module)
    return module


C = _load_contracts()

VERSION = 1
ROOT_UID = 0
ROOT_GID = 0
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-observer-worker")
PRODUCTION_MODE = "PRODUCTION_ROOT_OBSERVER_WORKER"
OBSERVER = "/usr/libexec/hepta-p1-safety-soak-independent-observer"
BROKER_EGRESS_POLICY = "/usr/libexec/hepta-broker-egress-policy"
BROKER_CHECK_ARGV = (BROKER_EGRESS_POLICY, "--check-deny-all")
INJECTION_SCHEMA = (
    "hepta.p1-safety-soak-root-fault-injection-receipt.v1")
SERVICE_SCHEMA = (
    "hepta.p1-safety-soak-independent-service-observation.v1")
CAMPAIGN_CONTINUITY_SCHEMA = (
    "hepta.p1-safety-soak-independent-campaign-continuity-observation.v1")
AUTHORITY_SCHEMA = (
    "hepta.p1-safety-soak-independent-authority-observation.v1")
FAULT_SCHEMA = "hepta.p1-safety-soak-independent-fault-observation.v1"
CLEANUP_SCHEMA = "hepta.p1-safety-soak-independent-cleanup-observation.v1"
MAXIMUM_OUTPUT_BYTES = 16 * 1024 * 1024
POLL_SECONDS = 0.5


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


def _output_reference(path: Path, uid: int, gid: int,
                      expected_schema: str,
                      expected_broker_file_sha256: str | None = None) \
        -> dict[str, str]:
    snapshot = C._secure_read(
        path, expected_uid=uid, expected_gid=gid,
        modes=frozenset({0o600}))
    _require(
        snapshot.document.get("schema") == expected_schema and
        snapshot.document.get("production_mode") ==
            "PRODUCTION_ROOT_OBSERVER" and
        snapshot.document.get("paper_authorized") is False and
        snapshot.document.get("live_authorized") is False and
        snapshot.document.get("mutation_authorized") is False and
        snapshot.document.get("direct_broker_access") is False,
        "P1_OBSERVER_WORKER_OUTPUT_INVALID")
    if expected_broker_file_sha256 is not None:
        evidence = snapshot.document.get("observation_evidence")
        broker = evidence.get("broker_deny_all") \
            if isinstance(evidence, dict) else None
        _require(
            isinstance(broker, dict) and
            broker.get("helper_path") == BROKER_EGRESS_POLICY and
            broker.get("helper_file_sha256") ==
                expected_broker_file_sha256,
            "P1_OBSERVER_WORKER_BROKER_HELPER_PIN_DRIFT")
    return C._reference(snapshot)


def _safe_name(value: Any) -> str:
    _require(
        isinstance(value, str) and C.IDENTIFIER.fullmatch(value) is not None,
        "P1_OBSERVER_WORKER_IDENTIFIER_INVALID")
    return value


class ObserverWorker:
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
        self.raw = Path(self.runtime["raw_observation_directory"])
        self.injections = Path(self.runtime["injector_output_directory"])
        self.control = C.ControlQueue(
            Path(self.runtime["control_directory"]),
            self.runtime["campaign_id"], expected_uid=expected_uid,
            expected_gid=expected_gid)
        journal_directory = self.root / "observer-worker-journal"
        _prepare_directory(journal_directory, expected_uid, expected_gid)
        self.journal = C.Journal(
            journal_directory, self.runtime["campaign_id"],
            expected_uid=expected_uid, expected_gid=expected_gid,
            wall_clock=wall_clock, boot_clock=boot_clock)
        self.observer_pin = self.runtime["executables"][
            "independent_observer"]
        _require(self.observer_pin["path"] == OBSERVER,
                 "P1_OBSERVER_WORKER_HELPER_PATH_DRIFT")
        self.broker_pin = self.runtime["executables"]["broker_egress_policy"]
        _require(self.broker_pin["path"] == BROKER_EGRESS_POLICY,
                 "P1_OBSERVER_WORKER_BROKER_HELPER_PATH_DRIFT")
        self._stop = False

    def stop(self, _signum: int, _frame: Any) -> None:
        self._stop = True

    def _run_helper(
        self, argv: Sequence[str], output: Path, expected_schema: str,
    ) -> dict[str, str]:
        self._validate_helper_argv(argv, output, expected_schema)
        C._secure_executable(
            Path(self.observer_pin["path"]),
            self.observer_pin["file_sha256"], expected_uid=self.uid,
            expected_gid=self.gid)
        C._secure_executable(
            Path(self.broker_pin["path"]), self.broker_pin["file_sha256"],
            expected_uid=self.uid, expected_gid=self.gid)
        result = self.runner.run(argv, 90)
        C._secure_executable(
            Path(self.broker_pin["path"]), self.broker_pin["file_sha256"],
            expected_uid=self.uid, expected_gid=self.gid)
        C._secure_executable(
            Path(self.observer_pin["path"]),
            self.observer_pin["file_sha256"], expected_uid=self.uid,
            expected_gid=self.gid)
        _require(
            result.returncode in {0, 2} and output.exists(),
            "P1_OBSERVER_WORKER_HELPER_FAILED")
        reference = _output_reference(
            output, self.uid, self.gid, expected_schema,
            self.broker_pin["file_sha256"])
        _require(result.returncode == 0,
                 "P1_OBSERVER_WORKER_UNSAFE_OBSERVATION")
        return reference

    def _validate_helper_argv(
        self, argv: Sequence[str], output: Path, expected_schema: str,
    ) -> None:
        """Reject any widening of the pinned observer/helper command surface."""

        command_by_schema = {
            SERVICE_SCHEMA: "service",
            CAMPAIGN_CONTINUITY_SCHEMA: "campaign-continuity",
            AUTHORITY_SCHEMA: "authority",
            FAULT_SCHEMA: "fault",
            CLEANUP_SCHEMA: "cleanup",
        }
        command = command_by_schema.get(expected_schema)
        _require(
            command is not None and isinstance(argv, Sequence) and
            all(type(item) is str and item for item in argv) and
            list(argv[:3]) == [OBSERVER, "--run", command] and
            len(argv[3:]) % 2 == 0,
            "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
        raw = list(argv[3:])
        options: dict[str, str] = {}
        for index in range(0, len(raw), 2):
            name, value = raw[index:index + 2]
            _require(
                name.startswith("--") and name not in options and
                not value.startswith("--"),
                "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
            options[name] = value
        required = {"--campaign-spec", "--output"}
        optional: set[str] = set()
        if command == "service":
            required.add("--formal-campaign-id")
            optional.add("--transition-fault-id")
        elif command == "campaign-continuity":
            required.update({"--campaign-runtime", "--continuity-slot-index"})
            optional.add("--transition-fault-id")
        elif command == "fault":
            required.update({"--fault-plan", "--fault-injection-receipt"})
        elif command == "cleanup":
            required.update({"--subject-type", "--subject-id"})
            optional.update({
                "--formal-campaign-id", "--fault-injection-receipt"})
        _require(
            required.issubset(options) and
            set(options).issubset(required | optional) and
            options["--campaign-spec"] ==
                self.runtime["campaign_spec"]["path"] and
            options["--output"] == str(output) and output.is_absolute() and
            output.parent in {
                self.raw / "continuity", self.raw / "service",
                self.raw / "authority", self.raw / "fault",
                self.raw / "cleanup",
            }, "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
        transition = options.get("--transition-fault-id")
        _require(
            transition is None or C.IDENTIFIER.fullmatch(transition) is not None,
            "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
        if command == "service":
            _require(
                options["--formal-campaign-id"] in {
                    item["formal_campaign_id"]
                    for item in self.runtime["formal_campaigns"]
                }, "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
        elif command == "campaign-continuity":
            try:
                slot = int(options["--continuity-slot-index"])
            except ValueError as error:
                raise WorkerError(
                    "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID") from error
            _require(
                options["--campaign-runtime"] ==
                    str(self.runtime_snapshot.path) and
                0 <= slot <= self._continuity_final_slot() and
                output.name == f"{slot:08d}.json",
                "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
        elif command == "fault":
            _require(
                options["--fault-plan"] == self.runtime["fault_plan"]["path"] and
                Path(options["--fault-injection-receipt"]).parent ==
                    self.injections,
                "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
        elif command == "cleanup":
            subject_type = options["--subject-type"]
            _require(
                subject_type in {"LAUNCHER", "FAULT", "FINAL"} and
                C.IDENTIFIER.fullmatch(options["--subject-id"]) is not None,
                "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
            if subject_type == "FAULT":
                _require(
                    "--formal-campaign-id" in options and
                    "--fault-injection-receipt" in options,
                    "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
            else:
                _require("--fault-injection-receipt" not in options,
                         "P1_OBSERVER_WORKER_HELPER_ARGV_INVALID")
        _require(
            BROKER_CHECK_ARGV ==
                (self.broker_pin["path"], "--check-deny-all"),
            "P1_OBSERVER_WORKER_BROKER_HELPER_ARGV_DRIFT")

    def _event(self, name: str, status_value: str,
               details: Mapping[str, Any]) -> dict[str, Any]:
        result = self.journal.append(name, status_value, details)
        _notify("WATCHDOG=1\nSTATUS=" + name + ":" + status_value)
        return result

    def fail_closed(self, reason: str) -> None:
        """Make any in-process worker failure durable before a restart."""

        if self.journal.failed:
            return
        self._event("WORKER", "FAILED_CLOSED", {
            "worker": "observer", "reason": reason, "catch_up": False,
        })

    def _committed(self, event: str, **details: Any) -> bool:
        return any(
            item["status"] == "COMMITTED"
            for item in self.journal.matching(event, **details))

    def _intent(self, event: str, **details: Any) -> dict[str, Any] | None:
        values = self.journal.matching(event, **details)
        return next((item for item in reversed(values)
                     if item["status"] == "INTENT"), None)

    def _latest_gateway_transition(self, stream_event: str) -> str | None:
        candidates: list[tuple[int, str]] = []
        for item in self.journal.entries:
            value = item.document
            if (value["event"] == "OBSERVE_FAULT" and
                    value["status"] == "COMMITTED" and
                    value["details"].get("fault_type") == "SERVICE_RESTART"):
                candidates.append((
                    value["sequence"], value["details"]["fault_id"]))
        used = {
            item.document["details"].get("transition_fault_id")
            for item in self.journal.entries
            if item.document["event"] == stream_event and
            item.document["status"] == "COMMITTED"
        }
        pending = [item for item in candidates if item[1] not in used]
        return pending[-1][1] if pending else None

    def _formal_for(self, scheduled_ms: int) -> Mapping[str, Any] | None:
        matches = [
            item for item in self.runtime["formal_campaigns"]
            if item["valid_after_ms"] <= scheduled_ms < item["expires_at_ms"]
        ]
        _require(len(matches) <= 1, "P1_OBSERVER_WORKER_SCHEDULE_OVERLAP")
        return matches[0] if matches else None

    def _last_slot(self, formal_campaign_id: str) -> int:
        last = -1
        for item in self.journal.entries:
            value = item.document
            if (value["event"] != "SAMPLE_SLOT" or
                    value["details"].get("formal_campaign_id") !=
                    formal_campaign_id):
                continue
            if value["status"] in {"COMMITTED", "MISSED"}:
                last = max(last, int(value["details"]["last_slot"]))
        return last

    def sample_once(self) -> None:
        now_ms = int(self.wall_clock())
        matches = [
            item for item in self.runtime["formal_campaigns"]
            if item["valid_after_ms"] <= now_ms < item["expires_at_ms"]
        ]
        _require(len(matches) <= 1, "P1_OBSERVER_WORKER_SCHEDULE_OVERLAP")
        if not matches:
            return
        formal = matches[0]
        formal_id = str(formal["formal_campaign_id"])
        origin = int(formal["valid_after_ms"])
        cadence = int(self.runtime["observer_cadence_ms"])
        current = (now_ms - origin) // cadence
        last = self._last_slot(formal_id)
        if last + 1 < current:
            self._event("SAMPLE_SLOT", "MISSED", {
                "first_slot": last + 1, "last_slot": current - 1,
                "formal_campaign_id": formal_id,
                "observed_at_ms": now_ms, "catch_up": False,
                "reason": "WORKER_NOT_RUNNING_AT_SLOT",
            })
            last = current - 1
        if last >= current:
            return
        scheduled = origin + current * cadence
        if now_ms > scheduled + self.runtime["maximum_slot_lateness_ms"]:
            self._event("SAMPLE_SLOT", "MISSED", {
                "first_slot": current, "last_slot": current,
                "formal_campaign_id": formal_id,
                "scheduled_at_ms": scheduled, "observed_at_ms": now_ms,
                "catch_up": False, "reason": "SLOT_WINDOW_MISSED",
            })
            return
        event_details = {
            "first_slot": current, "last_slot": current,
            "formal_campaign_id": formal_id,
        }
        file_name = f"{formal_id}-{current:08d}.json"
        service_output = self.raw / "service" / file_name
        authority_output = self.raw / "authority" / file_name
        intent = self._intent("SAMPLE_SLOT", **event_details)
        transition = (
            self._latest_gateway_transition("SAMPLE_SLOT")
            if intent is None else
            intent["details"].get("transition_fault_id"))
        if intent is None:
            self._event("SAMPLE_SLOT", "INTENT", {
                **event_details, "scheduled_at_ms": scheduled,
                "transition_fault_id": transition,
            })
        service_ref: dict[str, str]
        if service_output.exists():
            service_ref = _output_reference(
                service_output, self.uid, self.gid, SERVICE_SCHEMA)
        else:
            argv = [
                OBSERVER, "--run", "service", "--campaign-spec",
                self.runtime["campaign_spec"]["path"],
                "--output", str(service_output), "--formal-campaign-id",
                formal_id,
            ]
            if transition is not None:
                argv += ["--transition-fault-id", transition]
            service_ref = self._run_helper(argv, service_output, SERVICE_SCHEMA)
        if authority_output.exists():
            authority_ref = _output_reference(
                authority_output, self.uid, self.gid, AUTHORITY_SCHEMA)
        else:
            authority_ref = self._run_helper([
                OBSERVER, "--run", "authority", "--campaign-spec",
                self.runtime["campaign_spec"]["path"],
                "--output", str(authority_output),
            ], authority_output, AUTHORITY_SCHEMA)
        self._event("SAMPLE_SLOT", "COMMITTED", {
            **event_details, "scheduled_at_ms": scheduled,
            "transition_fault_id": transition,
            "service_observation": service_ref,
            "authority_observation": authority_ref,
            "catch_up": False,
        })

    def _continuity_bounds(self) -> tuple[int, int, int]:
        origin = int(self.runtime["formal_campaigns"][0][
            "launcher_dispatch_at_ms"])
        end = int(self.runtime["formal_campaigns"][-1][
            "teardown_deadline_ms"])
        cadence = int(self.runtime["observer_cadence_ms"])
        _require(origin < end and cadence > 0,
                 "P1_OBSERVER_WORKER_CONTINUITY_SCHEDULE_INVALID")
        return origin, end, cadence

    def _continuity_final_slot(self) -> int:
        origin, end, cadence = self._continuity_bounds()
        return (end - origin + cadence - 1) // cadence

    def _continuity_scheduled_at(self, slot: int) -> int:
        origin, end, cadence = self._continuity_bounds()
        _require(type(slot) is int and 0 <= slot <= self._continuity_final_slot(),
                 "P1_OBSERVER_WORKER_CONTINUITY_SLOT_INVALID")
        return min(origin + slot * cadence, end)

    def _last_continuity_slot(self) -> int:
        expected = 0
        for item in self.journal.entries:
            value = item.document
            if value["event"] != "CAMPAIGN_CONTINUITY_SLOT":
                continue
            details = value["details"]
            if value["status"] == "MISSED":
                raise WorkerError(
                    "P1_OBSERVER_WORKER_CAMPAIGN_CONTINUITY_PREVIOUSLY_MISSED")
            if value["status"] != "COMMITTED":
                continue
            scheduled = self._continuity_scheduled_at(expected)
            origin, end, cadence = self._continuity_bounds()
            _require(
                details.get("first_slot") == expected and
                details.get("last_slot") == expected and
                details.get("scheduled_at_ms") == scheduled and
                details.get("origin_ms") == origin and
                details.get("end_ms") == end and
                details.get("cadence_ms") == cadence and
                details.get("maximum_slot_lateness_ms") ==
                    self.runtime["maximum_slot_lateness_ms"] and
                details.get("final_slot") == self._continuity_final_slot() and
                details.get("catch_up") is False,
                "P1_OBSERVER_WORKER_CAMPAIGN_CONTINUITY_ORDER_INVALID")
            expected += 1
        return expected - 1

    def _miss_continuity(
        self, *, first_slot: int, last_slot: int, observed_at_ms: int,
        reason: str,
    ) -> None:
        self._event("CAMPAIGN_CONTINUITY_SLOT", "MISSED", {
            "first_slot": first_slot, "last_slot": last_slot,
            "observed_at_ms": observed_at_ms, "catch_up": False,
            "reason": reason,
        })
        self.fail_closed("P1_OBSERVER_WORKER_CAMPAIGN_CONTINUITY_MISSED")
        raise WorkerError("P1_OBSERVER_WORKER_CAMPAIGN_CONTINUITY_MISSED")

    def sample_campaign_continuity_once(self) -> None:
        """Sample every frozen campaign slot, including both exact anchors."""

        origin, end, cadence = self._continuity_bounds()
        now_ms = int(self.wall_clock())
        if now_ms < origin:
            return
        final_slot = self._continuity_final_slot()
        current = (now_ms - origin) // cadence if now_ms < end else final_slot
        current = min(current, final_slot)
        last = self._last_continuity_slot()
        if last + 1 < current:
            self._miss_continuity(
                first_slot=last + 1, last_slot=current - 1,
                observed_at_ms=now_ms, reason="WORKER_NOT_RUNNING_AT_SLOT")
        if last >= current:
            return
        scheduled = self._continuity_scheduled_at(current)
        if now_ms > scheduled + self.runtime["maximum_slot_lateness_ms"]:
            self._miss_continuity(
                first_slot=current, last_slot=current,
                observed_at_ms=now_ms, reason="SLOT_WINDOW_MISSED")
        event_details = {"first_slot": current, "last_slot": current}
        output = self.raw / "continuity" / f"{current:08d}.json"
        intent = self._intent("CAMPAIGN_CONTINUITY_SLOT", **event_details)
        transition = (
            self._latest_gateway_transition("CAMPAIGN_CONTINUITY_SLOT")
            if intent is None else
            intent["details"].get("transition_fault_id"))
        if intent is None:
            self._event("CAMPAIGN_CONTINUITY_SLOT", "INTENT", {
                **event_details, "scheduled_at_ms": scheduled,
                "origin_ms": origin, "end_ms": end, "cadence_ms": cadence,
                "maximum_slot_lateness_ms":
                    self.runtime["maximum_slot_lateness_ms"],
                "final_slot": final_slot,
                "transition_fault_id": transition,
            })
        else:
            selected = intent["details"]
            _require(
                selected.get("first_slot") == current and
                selected.get("last_slot") == current and
                selected.get("scheduled_at_ms") == scheduled and
                selected.get("origin_ms") == origin and
                selected.get("end_ms") == end and
                selected.get("cadence_ms") == cadence and
                selected.get("maximum_slot_lateness_ms") ==
                    self.runtime["maximum_slot_lateness_ms"] and
                selected.get("final_slot") == final_slot,
                "P1_OBSERVER_WORKER_CONTINUITY_INTENT_DRIFT")
            scheduled = int(selected["scheduled_at_ms"])
            origin = int(selected["origin_ms"])
            end = int(selected["end_ms"])
            cadence = int(selected["cadence_ms"])
            final_slot = int(selected["final_slot"])
        if output.exists():
            reference = _output_reference(
                output, self.uid, self.gid, CAMPAIGN_CONTINUITY_SCHEMA)
        else:
            argv = [
                OBSERVER, "--run", "campaign-continuity",
                "--campaign-spec", self.runtime["campaign_spec"]["path"],
                "--campaign-runtime", str(self.runtime_snapshot.path),
                "--continuity-slot-index", str(current),
                "--output", str(output),
            ]
            if transition is not None:
                argv += ["--transition-fault-id", transition]
            reference = self._run_helper(
                argv, output, CAMPAIGN_CONTINUITY_SCHEMA)
        self._event("CAMPAIGN_CONTINUITY_SLOT", "COMMITTED", {
            **event_details, "scheduled_at_ms": scheduled,
            "origin_ms": origin, "end_ms": end, "cadence_ms": cadence,
            "maximum_slot_lateness_ms":
                self.runtime["maximum_slot_lateness_ms"],
            "final_slot": final_slot,
            "transition_fault_id": transition,
            "continuity_observation": reference, "catch_up": False,
        })

    def _injection_snapshots(self) -> list[C.Snapshot]:
        values: list[C.Snapshot] = []
        for path in sorted(self.injections.iterdir()):
            if path.name.startswith("."):
                continue
            snapshot = C._secure_read(
                path, expected_uid=self.uid, expected_gid=self.gid,
                modes=frozenset({0o600}))
            _require(
                snapshot.document.get("schema") == INJECTION_SCHEMA and
                snapshot.document.get("campaign_id") ==
                    self.runtime["campaign_id"] and
                snapshot.document.get("status") == "COMPLETE",
                "P1_OBSERVER_WORKER_INJECTION_INVALID")
            values.append(snapshot)
        return values

    def observe_faults(self) -> None:
        for injection in self._injection_snapshots():
            value = injection.document
            fault_id = _safe_name(value.get("fault_id"))
            fault_type = _safe_name(value.get("fault_type"))
            if self._committed("OBSERVE_FAULT", fault_id=fault_id):
                continue
            fault_output = self.raw / "fault" / f"{fault_id}.json"
            cleanup_output = self.raw / "cleanup" / f"fault-{fault_id}.json"
            if not self._intent("OBSERVE_FAULT", fault_id=fault_id):
                self._event("OBSERVE_FAULT", "INTENT", {
                    "fault_id": fault_id, "fault_type": fault_type,
                    "injection_receipt": C._reference(injection),
                })
            if fault_output.exists():
                fault_ref = _output_reference(
                    fault_output, self.uid, self.gid, FAULT_SCHEMA)
            else:
                fault_ref = self._run_helper([
                    OBSERVER, "--run", "fault", "--campaign-spec",
                    self.runtime["campaign_spec"]["path"],
                    "--output", str(fault_output), "--fault-plan",
                    self.runtime["fault_plan"]["path"],
                    "--fault-injection-receipt", str(injection.path),
                ], fault_output, FAULT_SCHEMA)
            if cleanup_output.exists():
                cleanup_ref = _output_reference(
                    cleanup_output, self.uid, self.gid, CLEANUP_SCHEMA)
            else:
                cleanup_ref = self._run_helper([
                    OBSERVER, "--run", "cleanup", "--campaign-spec",
                    self.runtime["campaign_spec"]["path"],
                    "--output", str(cleanup_output), "--subject-type", "FAULT",
                    "--subject-id", fault_id,
                    "--formal-campaign-id",
                    self.runtime["pin_formal_campaign_id"],
                    "--fault-injection-receipt", str(injection.path),
                ], cleanup_output, CLEANUP_SCHEMA)
            self._event("OBSERVE_FAULT", "COMMITTED", {
                "fault_id": fault_id, "fault_type": fault_type,
                "fault_observation": fault_ref,
                "cleanup_observation": cleanup_ref,
            })

    def _publish_ack(
        self, request: Mapping[str, Any], outputs: Sequence[Mapping[str, str]],
    ) -> None:
        entries = self.control._load_chain("observer", "acks")
        if any(item.document["request_id"] == request["request_id"]
               for item in entries):
            return
        document = C.seal({
            "schema": C.ACK_SCHEMA, "version": C.VERSION,
            "campaign_id": self.runtime["campaign_id"],
            "sequence": len(entries), "request_id": request["request_id"],
            "worker": "observer", "action": request["action"],
            "status": "COMPLETE", "completed_at_ms": int(self.wall_clock()),
            "outputs": [dict(item) for item in outputs],
            "previous_body_sha256": (
                None if not entries else entries[-1].body_sha256),
            **C.boundary(),
        })
        C.publish_noreplace(
            self.control._directory("observer", "acks") /
            f"{len(entries):08d}.json", document,
            expected_uid=self.uid, expected_gid=self.gid)

    def process_requests(self) -> None:
        requests = self.control._load_chain("observer", "requests")
        acks = self.control._load_chain("observer", "acks")
        completed = {item.document["request_id"] for item in acks}
        for snapshot in requests:
            request = snapshot.document
            if request["request_id"] in completed:
                continue
            _require(
                request["target"] == "observer" and
                request["action"] == "CLEANUP" and
                int(self.wall_clock()) <= request["deadline_ms"],
                "P1_OBSERVER_WORKER_REQUEST_INVALID_OR_EXPIRED")
            arguments = request["arguments"]
            _require(
                isinstance(arguments, dict) and
                set(arguments) == {
                    "subject_type", "subject_id", "formal_campaign_id",
                    "fault_injection_receipt",
                } and arguments["subject_type"] in {"LAUNCHER", "FINAL"},
                "P1_OBSERVER_WORKER_REQUEST_ARGUMENTS_INVALID")
            subject = _safe_name(arguments["subject_id"])
            output = self.raw / "cleanup" / (
                f"request-{request['sequence']:08d}-{subject}.json")
            event_details = {"request_id": request["request_id"]}
            if not self._intent("CONTROL_CLEANUP", **event_details):
                self._event("CONTROL_CLEANUP", "INTENT", event_details)
            if output.exists():
                reference = _output_reference(
                    output, self.uid, self.gid, CLEANUP_SCHEMA)
            else:
                argv = [
                    OBSERVER, "--run", "cleanup", "--campaign-spec",
                    self.runtime["campaign_spec"]["path"], "--output",
                    str(output), "--subject-type", arguments["subject_type"],
                    "--subject-id", subject,
                ]
                if arguments["formal_campaign_id"] is not None:
                    _require(
                        C.FORMAL_ID.fullmatch(arguments["formal_campaign_id"]),
                        "P1_OBSERVER_WORKER_REQUEST_ARGUMENTS_INVALID")
                    argv += ["--formal-campaign-id",
                             arguments["formal_campaign_id"]]
                reference = self._run_helper(argv, output, CLEANUP_SCHEMA)
            if not self._committed("CONTROL_CLEANUP", **event_details):
                self._event("CONTROL_CLEANUP", "COMMITTED", {
                    **event_details, "cleanup_observation": reference,
                })
            self._publish_ack(request, [reference])

    def step(self) -> None:
        _require(not self.journal.failed,
                 "P1_OBSERVER_WORKER_PREVIOUSLY_FAILED_CLOSED")
        self.observe_faults()
        self.sample_campaign_continuity_once()
        self.process_requests()
        self.sample_once()
        _notify("WATCHDOG=1")

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        _notify("READY=1\nSTATUS=observer worker active")
        try:
            while not self._stop:
                self.step()
                self.runner.sleep(POLL_SECONDS)
        except Exception as error:
            reason = getattr(
                error, "reason", "P1_OBSERVER_WORKER_UNEXPECTED_FAILURE")
            try:
                self.fail_closed(reason)
            except Exception as durability_error:
                raise WorkerError(
                    "P1_OBSERVER_WORKER_FAILURE_DURABILITY_FAILED"
                ) from durability_error
            raise
        _notify("STOPPING=1\nSTATUS=observer worker stopped")


def _bind_image(runtime: C.Snapshot) -> None:
    reason = "P1_OBSERVER_WORKER_INSTALLED_IMAGE_REQUIRED"
    try:
        executing = Path(__file__).resolve(strict=True)
        _require(
            executing == INSTALLED_EXECUTABLE and
            os.path.samefile(executing, INSTALLED_EXECUTABLE), reason)
    except OSError as error:
        raise WorkerError(reason) from error
    pin = runtime.document["executables"]["observer_worker"]
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
        _require(arguments.run, "P1_OBSERVER_WORKER_EXPLICIT_RUN_REQUIRED")
        _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
                 "P1_OBSERVER_WORKER_ROOT_REQUIRED")
        runtime = C._secure_read(
            arguments.runtime_manifest, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o600}))
        _require(
            runtime.file_sha256 ==
                arguments.expected_runtime_manifest_file_sha256,
            "P1_OBSERVER_WORKER_RUNTIME_DIGEST_DRIFT")
        C._validate_runtime(runtime.document)
        _bind_image(runtime)
        lock_path = Path(runtime.document["state_root"]) / ".observer-worker.lock"
        lock = os.open(
            lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) |
            os.O_CLOEXEC, 0o600)
        os.fchmod(lock, 0o600)
        metadata = os.fstat(lock)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_uid == ROOT_UID and
            metadata.st_gid == ROOT_GID and metadata.st_nlink == 1,
            "P1_OBSERVER_WORKER_LOCK_INVALID")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkerError("P1_OBSERVER_WORKER_ALREADY_RUNNING") from error
        ObserverWorker(
            runtime, C.ProductionAdapter(), expected_uid=ROOT_UID,
            expected_gid=ROOT_GID).run_forever()
        return 0
    except (WorkerError, C.CoordinatorError) as error:
        reason = getattr(error, "reason", "P1_OBSERVER_WORKER_FAILED")
        print("hepta_p1_safety_soak_observer_worker: FAIL " + reason,
              file=sys.stderr)
        _notify("STOPPING=1\nSTATUS=failed closed: " + reason)
        return 4
    except Exception:
        print(
            "hepta_p1_safety_soak_observer_worker: FAIL "
            "P1_OBSERVER_WORKER_UNEXPECTED_FAILURE", file=sys.stderr)
        _notify("STOPPING=1\nSTATUS=failed closed")
        return 4
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
