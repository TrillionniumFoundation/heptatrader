#!/usr/bin/env python3
"""Fault tests using real private releases, flock, rename and state versions."""
from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import activate_prepared_driver as activation


class _ModuleProxy:
    def __init__(self, original, **overrides):
        self._original = original
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._original, name)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def _put(path, raw, mode=0o600):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(raw)
    path.chmod(mode)
    return path


def _identity(path):
    item = path.lstat()
    return item.st_dev, item.st_ino, item.st_ctime_ns, _sha(path.read_bytes())


def _lock_busy(path):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)


class FlatStateCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hepta-flat-state-test-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state.json"
        self.state = {
            "instrument": "EUR.USD", "position": 0, "pending": None,
            "entries": 2, "exits": 2, "net_pnl": -0.50,
            "entry_decision_id": "decision-before", "entry_order_id": 123,
        }

    def _read(self, state):
        _put(self.path, _json_bytes(state))
        return activation.read_flat_state(self.path, "EUR.USD")[1]

    def test_maintenance_pause_diagnostic_refresh_does_not_change_correlation(self):
        expected = self._read(self.state)
        for checked_at_ms in (1, 2):
            with self.subTest(checked_at_ms=checked_at_ms):
                state = dict(self.state, entry_inference_pause={
                    "checked_at_ms": checked_at_ms,
                    "reason_code": "PERSISTENT_MAINTENANCE_HOLD",
                    "scope": "ENTRY_INFERENCE_ONLY",
                })
                self.assertEqual(self._read(state), expected)
        self.assertEqual(self._read(self.state), expected)

    def test_accounting_and_entry_correlations_remain_protected(self):
        examples = (
            ("entries", 2, 3),
            ("exits", 2, 3),
            ("gross_pnl", -0.25, 0.25),
            ("net_pnl", -0.50, 0.10),
            ("closed_trades", 2, 3),
            ("btc_entry_control", {"last_flat_at_ms": 100, "open_reservations": []},
             {"last_flat_at_ms": 101, "open_reservations": []}),
            ("entry_decision_id", "decision-before", "decision-after"),
            ("entry_order_id", 123, 124),
            ("entry_command_id", "open-command-001", "open-command-002"),
            ("entry_future_correlation", {"order_id": 123}, {"order_id": 124}),
        )
        expected = {name: original for name, original, _ in examples}
        state = dict(self.state, **expected)
        self.assertEqual(self._read(state), expected)
        for name, _, changed in examples:
            with self.subTest(field=name):
                updated = dict(state)
                updated[name] = changed
                actual = self._read(updated)
                self.assertNotEqual(actual, expected)
                self.assertEqual(actual[name], changed)

    def test_pending_or_position_drift_still_fails_flat_validation(self):
        for changes in ({"pending": {"order_id": 123}}, {"position": 1},
                        {"position": -1}, {"position": "0"}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(RuntimeError, "DRIVER_STATE_NOT_EXPLICITLY_FLAT"):
                    self._read(dict(self.state, **changes))

    def test_invalid_trade_counts_still_fail_accounting_validation(self):
        for changes in ({"entries": -1}, {"entries": "2"}, {"exits": -1}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(RuntimeError, "DRIVER_ACCOUNTING_INVALID"):
                    self._read(dict(self.state, **changes))


class MaintenanceUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hepta-maintenance-test-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.home.chmod(0o700)
        self.instrument = "EUR.USD"
        self.unit = "heptatrader-paper-driver.service"
        self.state_root = self.home / ".local/state/heptatrader"
        self.state_root.mkdir(mode=0o700, parents=True)
        self.state_root.chmod(0o700)
        self.operator_lock = _put(self.state_root / "operator-maintenance.lock", b"")
        self.driver_lock = _put(self.state_root / "paper-driver/driver.lock", b"")
        self.state_path = self.state_root / "paper-driver/state.json"
        self.initial_state = {
            "instrument": self.instrument, "position": 0, "pending": None,
            "entries": 2, "exits": 2, "gross_pnl": -0.25, "net_pnl": -0.50,
            "entry_decision_id": "existing-correlation", "halt_new_entries": True,
            "history": {"fixture_observation": 0},
        }
        _put(self.state_path, _json_bytes(self.initial_state))
        self.initial_state_raw = self.state_path.read_bytes()
        self.business = {
            "entries": 2, "exits": 2, "gross_pnl": -0.25, "net_pnl": -0.50,
            "entry_decision_id": "existing-correlation",
        }
        self.hold_path = _put(
            self.state_root / "operator-maintenance/maintenance-hold.json",
            _json_bytes({
                "schema": "heptatrader.operator-maintenance-hold.v1",
                "effect": "NEW_PAPER_RISK_HOLD_ONLY", "uid": os.geteuid(),
                "created_at_ms": 1,
            }),
        )
        self.kill_path = _put(
            self.home / "fixture-runtime/control/kill-switch", b"engaged\n", 0o440,
        )
        self.initial_markers = self._marker_snapshot()
        self.period_path = _put(
            self.home / ".config/heptatrader/pnl-period.json",
            _json_bytes({
                "schema": "heptatrader.pnl-period.v1", "execution_domain": "PAPER",
                "period_id": "maintenance-fixture",
            }),
        )
        self.old_manifest = self._make_release("old", "a" * 40)
        old_record = json.loads(self.old_manifest.read_bytes())
        self.candidate = self._make_release("candidate", "b" * 40, prepared_from=old_record)
        self.unknown_manifest = self._make_release("unknown", "c" * 40)
        self.candidate_sha = _sha(self.candidate.read_bytes())
        self.token = _put(
            self.home / ".config/heptatrader/paper.token", b"fixture-private-token\n",
        )
        self.sessionctl = _put(
            self.home / "bin/hepta-sessionctl",
            b"fixture executable bytes; execution is intercepted\n", 0o500,
        )
        self.sessionctl_sha = _sha(self.sessionctl.read_bytes())
        unit_root = self.home / ".config/systemd/user"
        self.guard_paths = {}
        self.statuses = {}
        for index, name in enumerate(activation.fence.RISK_UNITS):
            self.guard_paths[name] = _put(
                unit_root / (name + ".d") / activation.fence.GUARD_NAME,
                activation.GUARD_YES,
            )
            self.statuses[name] = {
                "LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                "MainPID": str(35000 + index), "InvocationID": "peer-invocation-" + str(index),
                "RefuseManualStop": "yes",
                "PartOf": "" if name == "heptatrader.target" else "heptatrader.target",
            }
        self.exec_path = unit_root / (self.unit + ".d") / activation.EXEC_DROPIN
        self.old_exec = self._exec_bytes(self.old_manifest)
        self.candidate_exec = self._exec_bytes(self.candidate)
        _put(self.exec_path, self.old_exec)
        self.initial_exec_identity = _identity(self.exec_path)
        self.loaded_exec = self.old_exec
        self.current_manifest = self.old_manifest
        self.original_key = ("42001", "100")
        self.alive = {self.original_key[0]: self.original_key}
        self.statuses[self.unit].update(
            MainPID=self.original_key[0], InvocationID="driver-invocation-42001",
        )
        self.next_pid = 42002
        self.driver_fd = None
        self.addCleanup(self._release_driver_fd)
        self._acquire_driver_fd()
        self.peer_statuses = {
            name: dict(value) for name, value in self.statuses.items() if name != self.unit
        }
        self.peer_guards = {
            name: _identity(path) for name, path in self.guard_paths.items() if name != self.unit
        }
        self.events = []
        self.violations = []
        self.marker_calls = 0
        self.clock = 100.0
        self.state_writes = 0
        self.observation_mutation = None
        self.has_started = False
        self.observations = 0
        self.die_after_observations = None
        self.audit_counter = 0
        self.audit_credit = None
        self.audit_mode = None
        self.fail_stop = False
        self.start_mode = None
        self.fail_directory_fsync = False
        self.fsync_injected = False
        self.audit_env = {
            "HEPTA_TOOL_ACCOUNT": "DU_FIXTURE", "HEPTA_EXECUTION_DOMAIN_ID": "PAPER",
            "HEPTA_EXECUTION_SOCKET": str(self.home / "fixture-runtime/execution.sock"),
        }
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        replacements = {
            "ctl": self._ctl, "running_driver": self._running_driver,
            "pid_key": lambda pid: self.alive.get(str(pid)),
            "proc_bytes": self._unexpected_proc_read,
            "marker_identity": self._marker_identity,
            "audit_environment": self._audit_environment,
            "fence": _ModuleProxy(activation.fence, show_unit=self._show_unit),
            "subprocess": _ModuleProxy(subprocess, run=self._audit_run),
            "time": _ModuleProxy(time, monotonic=lambda: self.clock, sleep=self._sleep),
            "os": _ModuleProxy(os, replace=self._replace, fsync=self._fsync),
        }
        for name, value in replacements.items():
            self.patches.enter_context(mock.patch.object(activation, name, value))

    def tearDown(self):
        self.assertEqual(self.violations, [])
        self.assertFalse(_lock_busy(self.operator_lock))
        self.assertEqual(self._marker_snapshot(), self.initial_markers)
        for name, path in self.guard_paths.items():
            self.assertEqual(path.read_bytes(), activation.GUARD_YES)
            self.assertEqual(self.statuses[name]["RefuseManualStop"], "yes")
        for name, expected in self.peer_guards.items():
            self.assertEqual(_identity(self.guard_paths[name]), expected)
        for name, expected in self.peer_statuses.items():
            self.assertEqual(self.statuses[name], expected)
        _, actual_business = activation.read_flat_state(self.state_path, self.instrument)
        self.assertEqual(actual_business, self.business)

    def _require(self, condition, message):
        if not condition:
            self.violations.append(message)
            raise AssertionError(message)

    def _marker_snapshot(self):
        return _identity(self.hold_path), _identity(self.kill_path)

    def _boundary(self):
        self._require(_lock_busy(self.operator_lock), "operator lock must cover the host boundary")
        self._require(
            self._marker_snapshot() == self.initial_markers, "maintenance markers changed",
        )

    def _make_release(self, label, source_sha, prepared_from=None):
        release = self.home / "releases" / label / self.instrument
        hashes = {}
        for name in sorted(activation.attribution.runtime_filenames(self.instrument)):
            raw = ("# " + label + " " + name + "\n").encode()
            _put(release / name, raw, 0o400)
            hashes[name] = _sha(raw)
        if prepared_from is None:
            record = {
                "schema": "heptatrader.attribution-producer.v1", "source_sha": source_sha,
                "instrument": self.instrument, "execution_domain": "PAPER",
                "period_path": str(self.period_path),
                "period_sha256": _sha(self.period_path.read_bytes()),
                "runtime_sha256": hashes, "btc_profile_sha256": None,
                "journal_directory": str(self.state_root / "fixture-attribution"),
                "actor_scope": "EXISTING_LOCAL_DRIVER", "trading_behavior_changed": False,
                "behavior_profile": "EXISTING_FIXTURE_POLICY", "created_at_ms": 1,
            }
        else:
            record = dict(prepared_from)
            record.pop("created_at_ms", None)
            changed = hashes != prepared_from["runtime_sha256"]
            record.update(
                source_sha=source_sha, runtime_sha256=hashes,
                prepared_from_manifest_sha256=_sha(self.old_manifest.read_bytes()),
                runtime_bytes_changed=changed,
                trading_behavior_changed=(
                    prepared_from.get("trading_behavior_changed") is not False or changed
                ),
                behavior_equivalence_assessed=False, created_at_ms=2,
            )
        return _put(release / "manifest.json", _json_bytes(record), 0o400)

    @staticmethod
    def _exec_bytes(manifest):
        return (
            "[Service]\nExecStart=\nExecStart="
            + " ".join(activation.argv_for(manifest)) + "\n"
        ).encode()

    def _current_key(self):
        return self.alive.get(self.statuses[self.unit]["MainPID"])

    def _acquire_driver_fd(self):
        if self.driver_fd is not None:
            raise AssertionError("fixture already owns driver.lock")
        fd = os.open(self.driver_lock, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        self.driver_fd = fd

    def _release_driver_fd(self):
        if self.driver_fd is not None:
            os.close(self.driver_fd)
            self.driver_fd = None

    def _vanish(self, state="inactive"):
        old_pid = self.statuses[self.unit]["MainPID"]
        self.alive.pop(old_pid, None)
        self._release_driver_fd()
        self.statuses[self.unit].update(
            ActiveState=state, SubState="failed" if state == "failed" else "dead", MainPID="0",
        )

    def _boot(self, manifest):
        self._require(
            self.driver_fd is None and not _lock_busy(self.driver_lock),
            "controller must release driver.lock before systemd start",
        )
        self._acquire_driver_fd()
        pid = str(self.next_pid)
        self.next_pid += 1
        key = (pid, str(self.next_pid + 100))
        self.alive[pid] = key
        self.current_manifest = manifest
        self.statuses[self.unit].update(
            ActiveState="active", SubState="running", MainPID=pid,
            InvocationID="driver-invocation-" + pid,
        )
        self.has_started = True
        self.observations = 0
        self.die_after_observations = None

    def _write_observation(self):
        self.state_writes += 1
        record = json.loads(self.state_path.read_bytes())
        record["history"] = {"fixture_observation": self.state_writes}
        if self.observation_mutation is not None:
            self.observation_mutation(record)
        temporary = self.state_path.with_name(
            "state.fixture-" + str(self.state_writes) + ".tmp"
        )
        _put(temporary, _json_bytes(record))
        os.replace(temporary, self.state_path)

    def _show_unit(self, name):
        self._require(name in self.statuses, "unexpected unit: " + name)
        return dict(self.statuses[name])

    def _running_driver(self, unit, instrument):
        self._boundary()
        self._require(
            (unit, instrument) == (self.unit, self.instrument), "unexpected running-driver scope",
        )
        if self.statuses[self.unit]["ActiveState"] != "active":
            raise RuntimeError("DRIVER_NOT_ACTIVE")
        if (self.die_after_observations is not None
                and self.observations >= self.die_after_observations):
            self._vanish("failed")
            raise RuntimeError("DRIVER_NOT_ACTIVE")
        self._require(
            self.driver_fd is not None and _lock_busy(self.driver_lock),
            "simulated resident must own real driver.lock",
        )
        if self.has_started:
            self.observations += 1
            self._write_observation()
        observed = activation.inspect_manifest(self.current_manifest, self.instrument)
        return {
            "status": self._show_unit(self.unit), "manifest": str(self.current_manifest),
            "manifest_sha256": observed["manifest_sha256"],
            "runtime_sha256": observed["runtime_sha256"], "pid_key": self._current_key(),
        }

    def _marker_identity(self, home):
        self.marker_calls += 1
        self._boundary()
        self._require(Path(home) == self.home, "wrong marker home")
        return self._marker_snapshot()

    def _audit_environment(self, gateway_pid):
        self._boundary()
        self._require(
            gateway_pid == int(self.peer_statuses["heptatrader-gateway.service"]["MainPID"]),
            "audit environment taken from the wrong gateway",
        )
        return dict(self.audit_env)

    def _unexpected_proc_read(self, *args, **kwargs):
        self._require(False, "unexpected real /proc read")

    def _sleep(self, seconds):
        self._boundary()
        self._require(seconds >= 0, "negative test clock advance")
        self.clock += seconds

    def _replace(self, source, destination):
        self._boundary()
        destination = Path(destination)
        if destination == self.exec_path:
            status = self.statuses[self.unit]
            self._require(
                status["ActiveState"] in ("inactive", "failed")
                and status["MainPID"] in ("", "0"),
                "70 replacement requires the selected driver to be stopped",
            )
            self._require(
                self.driver_fd is None and _lock_busy(self.driver_lock),
                "70 replacement must hold the real stopped-driver lock",
            )
        os.replace(source, destination)
        if destination == self.exec_path:
            self.events.append({"kind": "replace-exec", "bytes": self.exec_path.read_bytes()})

    def _fsync(self, fd):
        item = os.fstat(fd)
        parent = self.exec_path.parent.stat()
        if (self.fail_directory_fsync and stat.S_ISDIR(item.st_mode)
                and (item.st_dev, item.st_ino) == (parent.st_dev, parent.st_ino)
                and self.exec_path.read_bytes() == self.candidate_exec):
            self.fail_directory_fsync = False
            self.fsync_injected = True
            self.events.append({"kind": "directory-fsync-failure"})
            raise OSError(errno.EIO, "fixture parent fsync failure")
        return os.fsync(fd)

    def _ctl(self, *args):
        self._boundary()
        if args == ("daemon-reload",):
            self.loaded_exec = self.exec_path.read_bytes()
            for name, path in self.guard_paths.items():
                raw = path.read_bytes()
                self._require(
                    raw in (activation.GUARD_YES, activation.GUARD_NO), "unexpected guard bytes",
                )
                self.statuses[name]["RefuseManualStop"] = (
                    "yes" if raw == activation.GUARD_YES else "no"
                )
            self.events.append({"kind": "reload"})
            return None
        self._require(
            len(args) == 2 and args[1] == self.unit and args[0] in ("stop", "start"),
            "unexpected systemd operation: " + repr(args),
        )
        if args[0] == "stop":
            key = self._current_key()
            self._require(
                self.statuses[self.unit]["ActiveState"] == "active", "stop of a non-active process",
            )
            self._require(
                self.guard_paths[self.unit].read_bytes() == activation.GUARD_NO
                and self.statuses[self.unit]["RefuseManualStop"] == "no",
                "normal stop requires the selected effective unlock",
            )
            self._require(
                self.audit_credit is not None and self.audit_credit[0] == key,
                "each stopped process needs its own fresh accepted audit",
            )
            command_id = self.audit_credit[1]
            self.audit_credit = None
            self.events.append({
                "kind": "stop", "pid_key": key, "manifest": str(self.current_manifest),
                "audit_command_id": command_id,
            })
            if self.fail_stop:
                self.fail_stop = False
                raise subprocess.CalledProcessError(1, ["systemctl", "--user", *args])
            self._vanish()
            return None
        self._require(
            self.statuses[self.unit]["ActiveState"] in ("inactive", "failed")
            and self.statuses[self.unit]["MainPID"] in ("", "0"),
            "start before the previous selected process is gone",
        )
        self._require(
            self.guard_paths[self.unit].read_bytes() == activation.GUARD_YES
            and self.statuses[self.unit]["RefuseManualStop"] == "yes",
            "stop guard must be effective again before start",
        )
        if self.loaded_exec == self.old_exec:
            manifest = self.old_manifest
        elif self.loaded_exec == self.candidate_exec:
            manifest = self.candidate
        else:
            self._require(False, "start with an unknown loaded ExecStart")
        self.events.append({"kind": "start", "manifest": str(manifest)})
        mode = self.start_mode if manifest == self.candidate else None
        if mode is not None:
            self.start_mode = None
        if mode == "unknown-active":
            self._boot(self.unknown_manifest)
            return None
        self._boot(manifest)
        if mode == "inactive-failure":
            self._vanish("failed")
            raise subprocess.CalledProcessError(1, ["systemctl", "--user", *args])
        if mode == "active-failure":
            raise subprocess.CalledProcessError(1, ["systemctl", "--user", *args])
        if mode == "short-lived":
            self.die_after_observations = 1
        return None

    @staticmethod
    def _accepted_reply(command_id):
        reply = {
            "schema": "hepta_session_owner_audit_v1", "operation": "owner-audit",
            "accepted": True, "native_status": 0, "validation_reason": "", "reason_code": "",
            "command_id": command_id, "agent_id": "fixture-agent", "session_id": "fixture-session",
            "service_epoch": "fixture-service-epoch", "service_fencing_generation": 1,
            "expected_owner_account": "DU_FIXTURE", "expected_owner_execution_domain": "PAPER",
            "owner_account": "DU_FIXTURE", "owner_execution_domain": "PAPER",
            "owner_audit_authoritative": True, "owner_audit_complete": True,
            "broker_recovery_audit_barrier_complete": True,
            "broker_post_fill_risk_reconciliation_pending": False,
            "broker_recovery_audit_new_connection_epoch_required": False,
            "owner_active_order_count": 0, "owner_uncertain_command_count": 0,
            "affected_count": 0, "broker_global_active_order_count": 0,
            "broker_position_quantity": "0", "broker_gross_absolute_position": "0",
            "mutation_blocked": True,
        }
        for name in (
            "broker_connection_epoch", "broker_active_generation", "broker_terminal_generation",
            "broker_risk_generation", "broker_account_generation", "broker_position_generation",
            "broker_fx_cash_generation",
        ):
            reply[name] = 1
        for name in (
            "broker_exposure_generation", "broker_terminal_exposure_generation",
            "broker_risk_absorbed_exposure_generation",
        ):
            reply[name] = 0
        return reply

    def _audit_run(self, args, **kwargs):
        self._boundary()
        self._require(
            args == [str(self.sessionctl), "owner-audit", "--token-file", str(self.token)],
            "unexpected subprocess: " + repr(args),
        )
        self._require(
            kwargs.get("env") == self.audit_env,
            "audit environment was not the selected gateway environment",
        )
        self._require(
            kwargs.get("capture_output") is True and 0 < kwargs.get("timeout", 0) <= 15,
            "audit must capture a bounded response",
        )
        self._require(
            self.statuses[self.unit]["RefuseManualStop"] == "yes"
            and self.guard_paths[self.unit].read_bytes() == activation.GUARD_YES,
            "fresh audit must precede the selected stop unlock",
        )
        self._require(
            self.driver_fd is not None and _lock_busy(self.driver_lock),
            "fresh audit is bound to the still-running selected driver",
        )
        self.audit_counter += 1
        command_id = "fixture-audit-" + str(self.audit_counter)
        key = self._current_key()
        reply = self._accepted_reply(command_id)
        returncode = 0
        rejected = (
            self.audit_mode == "reject-first" and self.audit_counter == 1
        ) or (
            self.audit_mode == "reject-second" and self.audit_counter == 2
        )
        if rejected:
            reply.update(accepted=False, native_status=2, reason_code="FIXTURE_NATIVE_REJECTED")
            returncode = 4
            self.audit_credit = None
        elif self.audit_mode == "incomplete":
            reply["broker_recovery_audit_barrier_complete"] = False
            self.audit_credit = None
        else:
            self.audit_credit = (key, command_id)
        self.events.append({
            "kind": "audit", "pid_key": key, "command_id": command_id, "returncode": returncode,
        })
        return subprocess.CompletedProcess(
            args, returncode, stdout=_json_bytes(reply), stderr=b"",
        )

    def _upgrade(self, **overrides):
        arguments = {
            "instrument": self.instrument, "candidate": self.candidate,
            "candidate_sha": self.candidate_sha, "sessionctl": self.sessionctl,
            "sessionctl_sha": self.sessionctl_sha, "home": self.home,
        }
        arguments.update(overrides)
        result = activation.maintenance_upgrade(**arguments)
        self.assertEqual(self.violations, [])
        return result

    def _events(self, kind):
        return [event for event in self.events if event["kind"] == kind]

    def _assert_original_restored(self, result):
        self.assertEqual(result["status"], "FAILED", result)
        self.assertEqual(result["rollback"], "ORIGINAL_DRIVER_RESTORED", result)
        self.assertEqual(self.exec_path.read_bytes(), self.old_exec)
        self.assertEqual(self.loaded_exec, self.old_exec)
        self.assertEqual(self.current_manifest, self.old_manifest)
        self.assertEqual(self.statuses[self.unit]["ActiveState"], "active")
        self.assertNotEqual(self._current_key(), self.original_key)
        self.assertTrue(result["selected_stop_guard_restored"])
        self.assertTrue(result["maintenance_markers_preserved"])
        self.assertGreaterEqual(result.get("startup_observed_state_versions", 0), 3)

    def test_success_consumes_audit_and_switches_under_real_locks(self):
        result = self._upgrade()
        self.assertEqual(result["status"], "ACTIVATED", result)
        self.assertTrue(result["service_changed"])
        self.assertEqual(self.current_manifest, self.candidate)
        self.assertEqual(self.exec_path.read_bytes(), self.candidate_exec)
        self.assertEqual(self.loaded_exec, self.candidate_exec)
        self.assertNotEqual(self._current_key(), self.original_key)
        self.assertGreaterEqual(result["startup_observed_state_versions"], 3)
        sequence = [
            event["kind"] for event in self.events if event["kind"] in ("audit", "stop", "start")
        ]
        self.assertEqual(sequence, ["audit", "stop", "start"])
        audit = self._events("audit")[0]
        stopped = self._events("stop")[0]
        self.assertEqual(stopped["pid_key"], self.original_key)
        self.assertEqual(stopped["audit_command_id"], audit["command_id"])


    def test_pid_bound_loop_progress_finishes_without_three_state_versions(self):
        def progress(record):
            key = self._current_key()
            record["resident_loop_progress"] = {
                "schema": "heptatrader.driver-loop-progress.v1",
                "pid": int(key[0]), "start_ticks": key[1],
                "iteration": self.state_writes,
                "at_ms": int(activation.time.time() * 1000),
            }
        self.observation_mutation = progress
        result = self._upgrade()
        self.assertEqual(result["status"], "ACTIVATED", result)
        self.assertEqual(result["startup_progress_basis"], "PID_BOUND_RESIDENT_LOOP")
        self.assertLess(result["startup_observed_state_versions"], 3)
        self.assertEqual(str(result["startup_resident_loop_progress"]["pid"]), self._current_key()[0])
        self.assertEqual(result["original_business_correlation"], self.business)
        self.assertEqual(len(self._events("audit")), 1)

    def _write_pause_observation(self, record):
        record["entry_inference_pause"] = {
            "reason_code": "PERSISTENT_MAINTENANCE_HOLD",
            "checked_at_ms": self.state_writes,
            "scope": "ENTRY_INFERENCE_ONLY",
        }

    def _seed_pause_observation(self):
        record = json.loads(self.state_path.read_bytes())
        record["entry_inference_pause"] = {
            "reason_code": "DRIVER_NEW_ENTRY_HALT",
            "checked_at_ms": 0,
            "scope": "ENTRY_INFERENCE_ONLY",
        }
        _put(self.state_path, _json_bytes(record))

    def test_pause_added_during_startup_does_not_change_business_identity(self):
        self.observation_mutation = self._write_pause_observation
        result = self._upgrade()
        self.assertEqual(result["status"], "ACTIVATED", result)
        self.assertGreaterEqual(result["startup_observed_state_versions"], 3)
        raw, business = activation.read_flat_state(self.state_path, self.instrument)
        self.assertEqual(business, self.business)
        self.assertIn("entry_inference_pause", json.loads(raw))
        self.assertEqual(result["original_business_correlation"], self.business)
        self.assertEqual(len(self._events("audit")), 1)

    def test_pause_timestamp_and_reason_can_change_during_startup(self):
        self._seed_pause_observation()
        self.observation_mutation = self._write_pause_observation
        result = self._upgrade()
        self.assertEqual(result["status"], "ACTIVATED", result)
        pause = json.loads(self.state_path.read_bytes())["entry_inference_pause"]
        self.assertGreater(pause["checked_at_ms"], 0)
        self.assertEqual(pause["reason_code"], "PERSISTENT_MAINTENANCE_HOLD")

    def test_pause_removed_during_startup_does_not_change_business_identity(self):
        self._seed_pause_observation()
        self.observation_mutation = lambda record: record.pop("entry_inference_pause", None)
        result = self._upgrade()
        self.assertEqual(result["status"], "ACTIVATED", result)
        self.assertNotIn("entry_inference_pause", json.loads(self.state_path.read_bytes()))

    def test_pause_observation_does_not_block_audited_candidate_rollback(self):
        self.observation_mutation = self._write_pause_observation
        self.start_mode = "active-failure"
        result = self._upgrade()
        self._assert_original_restored(result)
        self.assertEqual(len(self._events("audit")), 2)
        self.assertEqual(len(self._events("stop")), 2)

    def _assert_business_mutation_stays_rejected(self, field, value):
        def mutate(record):
            self._write_pause_observation(record)
            record[field] = value
        self.observation_mutation = mutate
        try:
            result = self._upgrade()
            self.assertEqual(result["status"], "FAILED", result)
            self.assertIn("DRIVER_BUSINESS_CORRELATION_CHANGED", result["error"])
            self.assertEqual(result["business_correlation_changed_fields"], [field])
            self.assertEqual(result["original_business_correlation"], self.business)
            self.assertEqual(result["rollback"], "INCOMPLETE_HELD", result)
            self.assertTrue(result["selected_stop_guard_restored"])
            self.assertTrue(result["maintenance_markers_preserved"])
            # The changed business record cannot authorize a rollback stop.
            self.assertEqual(len(self._events("audit")), 1)
            self.assertEqual(len(self._events("stop")), 1)
        finally:
            self.observation_mutation = None
            _put(self.state_path, self.initial_state_raw)

    def test_trade_correlation_change_still_rejects_with_pause_updates(self):
        self._assert_business_mutation_stays_rejected(
            "entry_decision_id", "unexpected-trade-correlation",
        )

    def test_unknown_entry_observation_lookalike_remains_protected(self):
        self._assert_business_mutation_stays_rejected(
            "entry_inference_pause_extra", {"checked_at_ms": 42},
        )

    def test_native_audit_failure_does_not_stop_or_touch_execstart(self):
        self.audit_mode = "reject-first"
        result = self._upgrade()
        self.assertEqual(result["status"], "FAILED", result)
        self.assertEqual(result["rollback"], "ORIGINAL_DRIVER_RUNNING", result)
        self.assertIn("OWNER_AUDIT_FAILED", result["error"])
        self.assertEqual(self._events("stop"), [])
        self.assertEqual(self._events("start"), [])
        self.assertEqual(self._events("replace-exec"), [])
        self.assertEqual(_identity(self.exec_path), self.initial_exec_identity)
        self.assertEqual(self.state_path.read_bytes(), self.initial_state_raw)
        self.assertEqual(self._current_key(), self.original_key)

    def test_accepted_but_incomplete_audit_cannot_authorize_stop(self):
        self.audit_mode = "incomplete"
        result = self._upgrade()
        self.assertEqual(result["status"], "FAILED", result)
        self.assertEqual(result["rollback"], "ORIGINAL_DRIVER_RUNNING", result)
        self.assertIn("OWNER_AUDIT_INCOMPLETE", result["error"])
        self.assertEqual(self._events("stop"), [])
        self.assertEqual(self._events("replace-exec"), [])
        self.assertEqual(_identity(self.exec_path), self.initial_exec_identity)

    def test_stop_failure_restores_shield_without_touching_execstart(self):
        self.fail_stop = True
        result = self._upgrade()
        self.assertEqual(result["status"], "FAILED", result)
        self.assertEqual(result["rollback"], "ORIGINAL_DRIVER_RUNNING", result)
        self.assertEqual(len(self._events("stop")), 1)
        self.assertEqual(self._events("start"), [])
        self.assertEqual(self._events("replace-exec"), [])
        self.assertEqual(_identity(self.exec_path), self.initial_exec_identity)
        self.assertEqual(self._current_key(), self.original_key)
        self.assertTrue(result["selected_stop_guard_restored"])

    def test_rename_then_parent_fsync_failure_restores_original(self):
        self.fail_directory_fsync = True
        result = self._upgrade()
        self.assertTrue(self.fsync_injected)
        self._assert_original_restored(result)
        self.assertEqual(
            [event["bytes"] for event in self._events("replace-exec")],
            [self.candidate_exec, self.old_exec],
        )
        self.assertEqual(
            [event["manifest"] for event in self._events("start")], [str(self.old_manifest)],
        )
        self.assertEqual(len(self._events("audit")), 1)

    def test_failed_candidate_start_with_pid_zero_restores_original(self):
        self.start_mode = "inactive-failure"
        result = self._upgrade()
        self._assert_original_restored(result)
        self.assertEqual(
            [event["manifest"] for event in self._events("start")],
            [str(self.candidate), str(self.old_manifest)],
        )
        self.assertEqual(len(self._events("audit")), 1)
        self.assertEqual(len(self._events("stop")), 1)

    def test_active_candidate_rollback_consumes_a_second_fresh_audit(self):
        self.start_mode = "active-failure"
        result = self._upgrade()
        self._assert_original_restored(result)
        audits = self._events("audit")
        stops = self._events("stop")
        self.assertEqual(len(audits), 2)
        self.assertEqual(len(stops), 2)
        self.assertNotEqual(audits[0]["command_id"], audits[1]["command_id"])
        self.assertNotEqual(stops[0]["pid_key"], stops[1]["pid_key"])
        self.assertEqual(stops[0]["manifest"], str(self.old_manifest))
        self.assertEqual(stops[1]["manifest"], str(self.candidate))
        for audit, stopped in zip(audits, stops):
            self.assertEqual(audit["pid_key"], stopped["pid_key"])
            self.assertEqual(audit["command_id"], stopped["audit_command_id"])
        self.assertEqual(
            [event["kind"] for event in self.events if event["kind"] in ("audit", "stop", "start")],
            ["audit", "stop", "start", "audit", "stop", "start"],
        )

    def test_failed_rollback_audit_leaves_active_candidate_held(self):
        self.start_mode = "active-failure"
        self.audit_mode = "reject-second"
        result = self._upgrade()
        self.assertEqual(result["status"], "FAILED", result)
        self.assertEqual(result["rollback"], "INCOMPLETE_HELD", result)
        self.assertIn("OWNER_AUDIT_FAILED", result["rollback_error"])
        self.assertEqual(len(self._events("audit")), 2)
        self.assertEqual(len(self._events("stop")), 1)
        self.assertEqual(self.current_manifest, self.candidate)
        self.assertEqual(self.statuses[self.unit]["ActiveState"], "active")
        self.assertEqual(self.exec_path.read_bytes(), self.candidate_exec)
        self.assertTrue(result["selected_stop_guard_restored"])
        self.assertTrue(result["maintenance_markers_preserved"])

    def test_unknown_active_process_is_not_stopped_during_rollback(self):
        self.start_mode = "unknown-active"
        result = self._upgrade()
        self.assertEqual(result["status"], "FAILED", result)
        self.assertEqual(result["rollback"], "INCOMPLETE_HELD", result)
        self.assertIn("UNKNOWN_ACTIVE_DRIVER_NOT_STOPPED", result["rollback_error"])
        stops = self._events("stop")
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["pid_key"], self.original_key)
        self.assertEqual(self.current_manifest, self.unknown_manifest)
        self.assertEqual(self.statuses[self.unit]["ActiveState"], "active")
        self.assertTrue(result["selected_stop_guard_restored"])
        self.assertTrue(result["maintenance_markers_preserved"])

    def test_brief_active_state_before_init_failure_is_not_activation(self):
        self.start_mode = "short-lived"
        result = self._upgrade()
        self._assert_original_restored(result)
        self.assertIn("DRIVER_START_FAILED", result["error"])
        self.assertEqual(
            [event["manifest"] for event in self._events("start")],
            [str(self.candidate), str(self.old_manifest)],
        )
        self.assertEqual(len(self._events("stop")), 1)
        self.assertEqual(len(self._events("audit")), 1)
        self.assertLess(self.clock - 100.0, 5.0)

    def test_already_active_is_noop_without_audit_or_binary_requirement(self):
        self.current_manifest = self.candidate
        _put(self.exec_path, self.candidate_exec)
        self.loaded_exec = self.candidate_exec
        before_exec = _identity(self.exec_path)
        result = self._upgrade(
            sessionctl=self.home / "missing-audit-binary", sessionctl_sha="0" * 64,
        )
        self.assertEqual(result["status"], "ALREADY_ACTIVE", result)
        self.assertFalse(result["service_changed"])
        self.assertEqual(self.events, [])
        self.assertEqual(self.marker_calls, 0)
        self.assertEqual(self.audit_counter, 0)
        self.assertEqual(_identity(self.exec_path), before_exec)
        self.assertEqual(self._current_key(), self.original_key)
        self.assertEqual(self.state_path.read_bytes(), self.initial_state_raw)



class ResidentLoopProgressTests(unittest.TestCase):
    def test_only_current_process_after_start_can_prove_loop_progress(self):
        driver = {"pid_key": ("42002", "42103")}
        progress = {"schema": "heptatrader.driver-loop-progress.v1",
                    "pid": 42002, "start_ticks": "42103", "iteration": 1, "at_ms": 1001}
        def check(value):
            return activation.resident_loop_progress(_json_bytes({"resident_loop_progress": value}),
                                                     driver, 1000, 1010)
        self.assertEqual(check(progress), progress)
        for fields in ({"pid": 42001}, {"start_ticks": "old"}, {"at_ms": 999}, {"at_ms": 1011},
                       {"iteration": 0}, {"pid": True}, {"iteration": True}, {"at_ms": True},
                       {"schema": "other"}):
            with self.subTest(fields=fields):
                self.assertIsNone(check({**progress, **fields}))
        self.assertIsNone(check(None))


if __name__ == "__main__":
    unittest.main()
