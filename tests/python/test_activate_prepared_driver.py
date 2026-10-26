#!/usr/bin/env python3
"""Fault tests using real private releases, flock, rename and state versions."""
from __future__ import annotations

import contextlib
import copy
import errno
import fcntl
import hashlib
import io
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
        self.frozen_policies = {}
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
            "frozen_driver_snapshot": self._frozen_driver_snapshot,
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

    def _frozen_driver_snapshot(self, name):
        self._boundary()
        self._require(name in self.frozen_policies, "unexpected frozen driver read: " + name)
        return {**self._show_unit(name), **self.frozen_policies[name]}

    def _freeze_crypto_peer(self):
        name = "heptatrader-crypto-driver.service"
        self.statuses[name].update(ActiveState="inactive", SubState="dead", MainPID="0", InvocationID="")
        self.peer_statuses[name] = dict(self.statuses[name])
        self.frozen_policies[name] = {"UnitFileState": "disabled", "ExecStart": "fixture original BTC entry"}
        return name

    def _freeze_interactive_broker(self):
        name = activation.BROKER_UNIT
        self.statuses[name].update(ActiveState="inactive", SubState="dead", MainPID="0", InvocationID="")
        self.peer_statuses[name] = dict(self.statuses[name])
        return {"broker_process": {"pid_key": ("1481499", "8029516")},
                "sockets": {"execution": {"inode": 111}},
                "scope": {"InvocationID": "existing-gnome-scope"},
                "unused_unit": dict(self.statuses[name]), "selected_edges": {},
                "broker_manual_stop_shield_claimed": False}

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

    def test_disabled_inactive_crypto_peer_stays_frozen_during_fx_activation(self):
        peer = self._freeze_crypto_peer()
        before = {**self.statuses[peer], **self.frozen_policies[peer]}
        result = self._upgrade()
        self.assertEqual(result["status"], "ACTIVATED", result)
        self.assertEqual(result["frozen_inactive_peers"], {peer: before})
        self.assertEqual({**self.statuses[peer], **self.frozen_policies[peer]}, before)
        self.assertTrue(result["other_risk_units_preserved"])

    def test_inactive_peer_exception_requires_loaded_disabled_zero_pid_and_guard(self):
        peer = self._freeze_crypto_peer()
        original = dict(self.statuses[peer])
        policy = dict(self.frozen_policies[peer])
        for field, value in (("LoadState", "not-found"), ("MainPID", "41000"),
                             ("RefuseManualStop", "no"), ("UnitFileState", "enabled"),
                             ("ActiveState", "failed")):
            with self.subTest(field=field):
                if field in policy:
                    self.frozen_policies[peer][field] = value
                else:
                    self.statuses[peer][field] = value
                try:
                    with self.assertRaisesRegex(RuntimeError, "INACTIVE_DRIVER_PEER|RISK_PATH_NOT_ACTIVE"):
                        self._upgrade()
                    self.assertEqual(self.events, [])
                finally:
                    self.statuses[peer] = dict(original)
                    self.frozen_policies[peer] = dict(policy)

    def test_frozen_peer_policy_or_identity_drift_prevents_any_selected_stop(self):
        self._freeze_crypto_peer()
        for field in ("ExecStart", "UnitFileState", "MainPID", "InvocationID", "PartOf",
                      "ActiveState", "SubState", "LoadState", "RefuseManualStop"):
            with self.subTest(field=field):
                self.audit_counter = 0; self.audit_credit = None; self.events.clear()
                def changed(name):
                    value = self._frozen_driver_snapshot(name)
                    if self.audit_counter:
                        value[field] = "changed-after-audit"
                    return value
                with mock.patch.object(activation, "frozen_driver_snapshot", side_effect=changed):
                    result = self._upgrade()
                self.assertEqual(result["status"], "FAILED", result)
                self.assertIn("FROZEN_DRIVER_PEER_CHANGED", result["error"])
                self.assertEqual(self._events("stop"), [])
                self.assertEqual(self._events("start"), [])
                self.assertEqual(self.exec_path.read_bytes(), self.old_exec)

    def test_selected_driver_and_non_driver_risk_units_still_require_active(self):
        self._freeze_crypto_peer()
        for name in (self.unit, "heptatrader-execution.service"):
            with self.subTest(unit=name):
                original = dict(self.statuses[name])
                self.statuses[name].update(ActiveState="inactive", MainPID="0")
                try:
                    with self.assertRaisesRegex(RuntimeError, "DRIVER_NOT_ACTIVE|RISK_PATH_NOT_ACTIVE"):
                        self._upgrade()
                    self.assertEqual(self.events, [])
                finally:
                    self.statuses[name] = original

    def test_inactive_broker_unit_still_blocks_without_explicit_pid(self):
        self._freeze_interactive_broker()
        with mock.patch.object(activation, "interactive_broker_snapshot") as inspect:
            with self.assertRaisesRegex(RuntimeError, "RISK_PATH_NOT_ACTIVE:ibgateway.service"):
                self._upgrade()
            inspect.assert_not_called()
        self.assertEqual(self.events, [])

    def test_explicit_interactive_broker_keeps_audit_and_only_switches_selected_driver(self):
        proof = self._freeze_interactive_broker()
        self._freeze_crypto_peer()
        with mock.patch.object(activation, "interactive_broker_snapshot", return_value=proof) as inspect:
            result = self._upgrade(interactive_broker_pid=1481499)
        self.assertEqual(result["status"], "ACTIVATED", result)
        self.assertEqual(result["interactive_broker"], proof)
        self.assertFalse(result["interactive_broker"]["broker_manual_stop_shield_claimed"])
        self.assertGreater(inspect.call_count, 5)
        for call in inspect.call_args_list:
            self.assertEqual(call.args[:2], (1481499, self.unit))
        self.assertEqual([e["kind"] for e in self.events if e["kind"] in ("audit", "stop", "start")],
                         ["audit", "stop", "start"])

    def test_interactive_broker_identity_drift_after_audit_never_authorizes_stop(self):
        proof = self._freeze_interactive_broker()
        for field in ("broker_process", "sockets", "scope", "unused_unit", "selected_edges"):
            with self.subTest(field=field):
                self.audit_counter = 0; self.audit_credit = None; self.events.clear()
                def inspect(*_):
                    value = copy.deepcopy(proof)
                    if self.audit_counter:
                        value[field]["changed-after-audit"] = True
                    return value
                with mock.patch.object(activation, "interactive_broker_snapshot", side_effect=inspect):
                    result = self._upgrade(interactive_broker_pid=1481499)
                self.assertEqual(result["status"], "FAILED", result)
                self.assertIn("INTERACTIVE_BROKER_CHANGED", result["error"])
                self.assertEqual(self._events("stop"), [])
                self.assertEqual(self._events("start"), [])
                self.assertEqual(self.exec_path.read_bytes(), self.old_exec)

    def test_interactive_broker_loss_after_stop_keeps_hold_and_does_not_restart(self):
        proof = self._freeze_interactive_broker()
        def inspect(*_):
            if self.statuses[self.unit]["ActiveState"] == "inactive":
                raise RuntimeError("INTERACTIVE_BROKER_ACCEPTED_SOCKET_REQUIRED")
            return proof
        with mock.patch.object(activation, "interactive_broker_snapshot", side_effect=inspect):
            result = self._upgrade(interactive_broker_pid=1481499)
        self.assertEqual(result["rollback"], "INCOMPLETE_HELD", result)
        self.assertEqual(len(self._events("stop")), 1)
        self.assertEqual(self._events("start"), [])
        self.assertTrue(result["selected_stop_guard_restored"])
        self.assertTrue(result["maintenance_markers_preserved"])
        self.assertEqual(self.exec_path.read_bytes(), self.old_exec)

    def test_interactive_broker_proof_does_not_replace_authoritative_audit(self):
        proof = self._freeze_interactive_broker()
        self.audit_mode = "incomplete"
        with mock.patch.object(activation, "interactive_broker_snapshot", return_value=proof):
            result = self._upgrade(interactive_broker_pid=1481499)
        self.assertEqual(result["status"], "FAILED", result)
        self.assertIn("OWNER_AUDIT_INCOMPLETE", result["error"])
        self.assertEqual(self._events("stop"), [])
        self.assertEqual(self.exec_path.read_bytes(), self.old_exec)

    def test_prepared_stream_manifest_preserves_the_existing_activation_boundary(self):
        record = json.loads(self.candidate.read_bytes())
        record["native_read_stream"] = True
        self.candidate.chmod(0o600)
        _put(self.candidate, _json_bytes(record), 0o400)
        self.candidate_sha = _sha(self.candidate.read_bytes())
        result = self._upgrade()
        self.assertEqual(result["status"], "ACTIVATED", result)
        self.assertEqual([event["kind"] for event in self.events
                          if event["kind"] in ("audit", "stop", "start")],
                         ["audit", "stop", "start"])
        self.assertTrue(json.loads(self.current_manifest.read_bytes())["native_read_stream"])


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



class FrozenDriverReadbackTests(unittest.TestCase):
    def test_loaded_peer_readback_binds_enablement_and_full_execstart(self):
        peer = "heptatrader-crypto-driver.service"
        values = {name: "fixture" for name in activation.fence.SHOW_PROPS}
        values.update(UnitFileState="disabled", ExecStart="{ argv[]=/fixture/driver --manifest=/fixture/m.json ; }")
        raw = "".join(name + "=" + value + "\n" for name, value in values.items())
        with mock.patch.object(activation.subprocess, "check_output", return_value=raw) as read:
            self.assertEqual(activation.frozen_driver_snapshot(peer), values)
            self.assertEqual(read.call_args.args[0][:4], ["systemctl", "--user", "show", peer])
            self.assertIn("UnitFileState", read.call_args.args[0])
            self.assertIn("ExecStart", read.call_args.args[0])
            self.assertIn("--all", read.call_args.args[0])
        for invalid in (raw + "ExecStart=changed\n", raw.replace("UnitFileState=disabled\n", "")):
            with self.subTest(raw=invalid), mock.patch.object(
                    activation.subprocess, "check_output", return_value=invalid):
                with self.assertRaisesRegex(RuntimeError, "FROZEN_DRIVER_READBACK"):
                    activation.frozen_driver_snapshot(peer)
        with mock.patch.object(activation.subprocess, "check_output") as read:
            with self.assertRaisesRegex(RuntimeError, "INVALID_FROZEN_DRIVER_SCOPE"):
                activation.frozen_driver_snapshot("heptatrader-execution.service")
            read.assert_not_called()


class InteractiveBrokerSnapshotTests(unittest.TestCase):
    """Exercise kernel-table parsing and topology checks, not prebuilt proofs."""
    HEADER = "  sl  local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hepta-interactive-broker-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / "proc"
        self.uid = os.geteuid()
        self.execution_pid, self.broker_pid = 1403827, 1481499
        self.selected = "heptatrader-paper-driver.service"
        self.scope_name = "app-gnome-ibgateway-14659.scope"
        self.scope_group = f"/user.slice/user-{self.uid}.slice/user@{self.uid}.service/app.slice/{self.scope_name}"
        self.execution_group = f"/user.slice/user-{self.uid}.slice/user@{self.uid}.service/app.slice/heptatrader-execution.service"
        namespace = _put(self.root / "netns", b"fixture namespace identity")
        self._process(self.execution_pid, "7709970", "hepta-ib-executiond", self.execution_group, namespace)
        self._process(self.broker_pid, "8029516", "java", self.scope_group, namespace)
        self._fd(self.execution_pid, 9, 111)
        self._fd(self.broker_pid, 92, 222)
        self._fd(self.broker_pid, 87, 333)
        self.environment = (b"HEPTA_IB_EXECUTION_MODE=PAPER\0HEPTA_IB_PAPER_USER_SCOPE=1\0"
                            b"HEPTA_IB_PAPER_HOST=127.0.0.1\0HEPTA_IB_PAPER_PORT=4002\0"
                            b"UNRELATED_PRIVATE_VALUE=must-not-appear-in-proof\0")
        _put(self.proc / str(self.execution_pid) / "environ", self.environment)
        self.tcp = self.HEADER + self._row("0100007F:B6CA", "0100007F:0FA2", "01", 111)
        self.tcp6 = (self.HEADER
            + self._row("0000000000000000FFFF00000100007F:0FA2", "0000000000000000FFFF00000100007F:B6CA", "01", 222)
            + self._row("00000000000000000000000000000000:0FA2", "00000000000000000000000000000000:0000", "0A", 333))
        self._tables()
        base = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                "MainPID": str(self.execution_pid), "InvocationID": "execution-invocation",
                "RefuseManualStop": "yes", "PartOf": "heptatrader.target"}
        unused = dict(base, ActiveState="inactive", SubState="dead", MainPID="0", InvocationID="")
        self.current = {activation.EXECUTION_UNIT: dict(base), activation.BROKER_UNIT: dict(unused)}
        executable = str(self.root / "bin/hepta-ib-executiond")
        self.execution_start = ("{ path=" + executable + " ; argv[]=" + executable
            + " existing-argument ; ignore_errors=no ; start_time=[Mon 2026-10-26 06:01:35 PDT] ; "
            "stop_time=[n/a] ; pid=" + str(self.execution_pid) + " ; code=(null) ; status=0 }")
        self.units = {
            activation.EXECUTION_UNIT: dict(base, ExecStart=self.execution_start, ControlGroup=self.execution_group),
            activation.BROKER_UNIT: dict(unused, UnitFileState="static", ExecStart="unused original launcher", ControlGroup=""),
            self.scope_name: {"Id": self.scope_name, "LoadState": "loaded", "ActiveState": "active",
                "SubState": "running", "InvocationID": "gnome-scope-invocation", "ControlGroup": self.scope_group,
                "PartOf": "graphical-session.target", "RefuseManualStop": "no", "UnitFileState": "transient"},
            self.selected: {**{name: "" for name in activation.STOP_EDGES},
                            "Conflicts": "shutdown.target", "ConflictedBy": ""},
            "shutdown.target": {"LoadState": "loaded", "ActiveState": "inactive"},
        }
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.object(activation, "PROC_ROOT", self.proc))
        self.patches.enter_context(mock.patch.object(activation.subprocess, "check_output", side_effect=self._show))

    def _process(self, pid, ticks, executable, cgroup, namespace):
        base = self.proc / str(pid)
        _put(base / "stat", (f"{pid} (fixture process) S " + " ".join(["0"] * 18 + [ticks] + ["0"] * 30) + "\n").encode())
        _put(base / "status", (f"Name:\tfixture\nState:\tS (sleeping)\nUid:\t{self.uid}\t{self.uid}\t{self.uid}\t{self.uid}\n").encode())
        _put(base / "cmdline", executable.encode() + b"\0existing-argument\0")
        _put(base / "cgroup", ("0::" + cgroup + "\n").encode())
        binary = _put(self.root / "bin" / executable, b"fixture original executable\n", 0o500)
        (base / "exe").symlink_to(binary)
        (base / "ns").mkdir()
        (base / "ns/net").symlink_to(namespace)
        (base / "fd").mkdir()

    def _fd(self, pid, fd, inode):
        path = self.proc / str(pid) / "fd" / str(fd)
        path.unlink(missing_ok=True)
        path.symlink_to(f"socket:[{inode}]")

    def _row(self, local, remote, state, inode):
        return f" 0: {local} {remote} {state} 00000000:00000000 00:00000000 00000000 {self.uid} 0 {inode} 1 0000000000000000\n"

    def _tables(self):
        _put(self.proc / str(self.execution_pid) / "net/tcp", self.tcp.encode())
        _put(self.proc / str(self.execution_pid) / "net/tcp6", self.tcp6.encode())

    def _show(self, command, **_):
        self.assertEqual(command[:3], ["systemctl", "--user", "show"])
        self.assertIn("--all", command)
        unit = command[3]
        properties = [command[i + 1] for i, value in enumerate(command) if value == "-p"]
        return "".join(name + "=" + self.units[unit][name] + "\n" for name in properties)

    def _snapshot(self):
        return activation.interactive_broker_snapshot(self.broker_pid, self.selected, self.current)

    def test_ipv4_execution_and_mapped_ipv6_broker_bind_original_fds_and_scope(self):
        proof = self._snapshot()
        self.assertEqual(proof, self._snapshot())
        self.assertEqual(proof["execution_process"]["pid_key"], (str(self.execution_pid), "7709970"))
        self.assertEqual(proof["broker_process"]["pid_key"], (str(self.broker_pid), "8029516"))
        self.assertEqual(proof["sockets"]["execution"]["fds"], [9])
        self.assertEqual(proof["sockets"]["accepted"]["fds"], [92])
        self.assertEqual(proof["sockets"]["accepted"]["remote"], ("127.0.0.1", 46794))
        self.assertEqual(proof["sockets"]["accepted"]["family"], "tcp6")
        self.assertEqual(proof["sockets"]["listener"]["local"], ("::", 4002))
        self.assertEqual(proof["scope"]["RefuseManualStop"], "no")
        self.assertFalse(proof["broker_manual_stop_shield_claimed"])
        self.assertNotIn("must-not-appear", json.dumps(proof))

    def test_execstart_runtime_metadata_reset_keeps_the_same_actual_execution_proof(self):
        expected = self._snapshot()
        self.units[activation.EXECUTION_UNIT]["ExecStart"] = self.execution_start.replace(
            "start_time=[Mon 2026-10-26 06:01:35 PDT]", "start_time=[n/a]").replace(
            "stop_time=[n/a]", "stop_time=[Mon 2026-10-26 06:01:36 PDT]").replace(
            "pid=" + str(self.execution_pid), "pid=0").replace("code=(null)", "code=exited").replace(
            "status=0", "status=9")
        self.assertEqual(self._snapshot(), expected)
        self.assertEqual(expected["execution_unit"]["MainPID"], str(self.execution_pid))
        self.assertEqual(expected["execution_process"]["pid_key"], (str(self.execution_pid), "7709970"))

    def test_execstart_static_command_changes_and_ambiguous_descriptors_are_not_ignored(self):
        expected = self._snapshot()
        for old, new in (("path=", "path=/different-prefix"),
                         ("existing-argument", "changed-argument"), ("ignore_errors=no", "ignore_errors=yes")):
            with self.subTest(field=old):
                self.units[activation.EXECUTION_UNIT]["ExecStart"] = self.execution_start.replace(old, new, 1)
                self.assertNotEqual(self._snapshot(), expected)
        for value in (self.execution_start + " " + self.execution_start,
                      self.execution_start.replace(" ; pid=", " ; unknown=1 ; pid="),
                      self.execution_start.replace(" ; pid=", " ; pid=1 ; pid="),
                      self.execution_start.replace("existing-argument", "ambiguous;argument")):
            with self.subTest(value=value):
                self.units[activation.EXECUTION_UNIT]["ExecStart"] = value
                with self.assertRaisesRegex(RuntimeError, "EXECSTART_INVALID"):
                    self._snapshot()

    def _use_gnome_session(self):
        self.gnome_unit = "org.gnome.Shell@ubuntu.service"
        group = f"/user.slice/user-{self.uid}.slice/user@{self.uid}.service/session.slice/{self.gnome_unit}"
        self.shell_pid = 1403000
        self._process(self.shell_pid, "7708000", "gnome-shell", group, self.root / "netns")
        _put(self.proc / str(self.broker_pid) / "cgroup", ("0::" + group + "\n").encode())
        self.units[self.gnome_unit] = {
            "Id": self.gnome_unit, "LoadState": "loaded", "ActiveState": "active",
            "SubState": "running", "InvocationID": "gnome-session-invocation",
            "ControlGroup": group, "PartOf": "graphical-session.target",
            "RefuseManualStop": "no", "UnitFileState": "static",
            "MainPID": str(self.shell_pid), "User": str(self.uid),
        }

    def test_gnome_session_binds_shell_java_and_actual_paper_connection(self):
        self._use_gnome_session()
        proof = self._snapshot()
        self.assertEqual(proof, self._snapshot())
        self.assertEqual(proof["scope"]["Id"], self.gnome_unit)
        self.assertEqual(proof["gnome_shell_process"]["pid_key"], (str(self.shell_pid), "7708000"))
        self.assertEqual(proof["broker_process"]["pid_key"], (str(self.broker_pid), "8029516"))
        self.assertEqual(proof["sockets"]["execution"]["fds"], [9])
        self.assertEqual(proof["sockets"]["accepted"]["fds"], [92])
        self.assertEqual(proof["sockets"]["listener"]["fds"], [87])
        self.assertFalse(proof["broker_manual_stop_shield_claimed"])
        self.assertNotIn("must-not-appear", json.dumps(proof))

    def test_gnome_session_requires_current_static_same_owner_service(self):
        self._use_gnome_session()
        for name, changed in (("ActiveState", "inactive"), ("SubState", "dead"),
                              ("InvocationID", ""), ("ControlGroup", "/other.service"),
                              ("UnitFileState", "transient"), ("User", str(self.uid + 1)),
                              ("MainPID", "0"), ("MainPID", str(self.broker_pid))):
            with self.subTest(property=name, value=changed):
                old = self.units[self.gnome_unit][name]
                self.units[self.gnome_unit][name] = changed
                try:
                    with self.assertRaisesRegex(RuntimeError, "SCOPE_NOT_ACTIVE|GNOME_SESSION"):
                        self._snapshot()
                finally:
                    self.units[self.gnome_unit][name] = old

    def test_gnome_session_main_process_must_be_live_shell_in_same_namespace(self):
        self._use_gnome_session()
        base = self.proc / str(self.shell_pid)
        old_exe = os.readlink(base / "exe")
        (base / "exe").unlink()
        (base / "exe").symlink_to(self.proc / str(self.broker_pid) / "exe")
        with self.assertRaisesRegex(RuntimeError, "GNOME_SESSION_PROCESS_INVALID"):
            self._snapshot()
        (base / "exe").unlink()
        (base / "exe").symlink_to(old_exe)
        namespace = _put(self.root / "shell-other-netns", b"other namespace")
        (base / "ns/net").unlink()
        (base / "ns/net").symlink_to(namespace)
        with self.assertRaisesRegex(RuntimeError, "GNOME_SESSION_PROCESS_INVALID"):
            self._snapshot()

    def test_gnome_child_without_listener_or_accepted_connection_is_not_broker(self):
        self._use_gnome_session()
        for fd, inode, reason in ((87, 333, "LISTENER_REQUIRED"),
                                 (92, 222, "ACCEPTED_SOCKET_REQUIRED")):
            with self.subTest(fd=fd):
                self._fd(self.broker_pid, fd, 9999)
                try:
                    with self.assertRaisesRegex(RuntimeError, reason):
                        self._snapshot()
                finally:
                    self._fd(self.broker_pid, fd, inode)

    def test_gnome_session_restart_during_snapshot_is_rejected(self):
        self._use_gnome_session()
        original = activation.unit_properties
        reads = 0
        def changed(unit, *args):
            nonlocal reads
            value = original(unit, *args)
            if unit == self.gnome_unit:
                reads += 1
                if reads > 1:
                    value["InvocationID"] = "restarted-session"
            return value
        with mock.patch.object(activation, "unit_properties", side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, "GNOME_SESSION_CHANGED_DURING_INSPECTION"):
                self._snapshot()

    def test_default_shutdown_conflict_is_allowed_only_while_inactive(self):
        self.units["shutdown.target"]["ActiveState"] = "active"
        with self.assertRaisesRegex(RuntimeError, "INTERACTIVE_BROKER_SHUTDOWN_ACTIVE"):
            self._snapshot()

    def test_every_outgoing_stop_edge_and_nondefault_start_conflict_refuses(self):
        for name in (*activation.STOP_EDGES, "Conflicts", "ConflictedBy"):
            with self.subTest(property=name):
                original = self.units[self.selected][name]
                self.units[self.selected][name] = self.scope_name
                try:
                    with self.assertRaisesRegex(RuntimeError, "DRIVER_STOP_PROPAGATION|DRIVER_START_CONFLICT"):
                        self._snapshot()
                finally:
                    self.units[self.selected][name] = original

    def test_unused_unit_requires_exact_loaded_inactive_zero_pid_and_guard(self):
        for name, changed in (("LoadState", "not-found"), ("ActiveState", "active"),
                              ("MainPID", "123"), ("RefuseManualStop", "no")):
            with self.subTest(property=name):
                old = self.units[activation.BROKER_UNIT][name]
                self.units[activation.BROKER_UNIT][name] = changed
                self.current[activation.BROKER_UNIT][name] = changed
                try:
                    with self.assertRaisesRegex(RuntimeError, "UNUSED_UNIT_REQUIRED"):
                        self._snapshot()
                finally:
                    self.units[activation.BROKER_UNIT][name] = old
                    self.current[activation.BROKER_UNIT][name] = old

    def test_scope_must_be_running_and_exactly_own_the_broker_cgroup(self):
        for name, changed in (("ActiveState", "inactive"), ("SubState", "dead"), ("UnitFileState", "static"),
                              ("InvocationID", ""), ("ControlGroup", "/other.scope")):
            with self.subTest(property=name):
                old = self.units[self.scope_name][name]
                self.units[self.scope_name][name] = changed
                try:
                    with self.assertRaisesRegex(RuntimeError, "SCOPE_NOT_ACTIVE"):
                        self._snapshot()
                finally:
                    self.units[self.scope_name][name] = old

    def test_paper_profile_must_come_from_execution_process_without_overrides(self):
        for old, new in ((b"=PAPER", b"=LIVE"), (b"127.0.0.1", b"192.0.2.1"),
                         (b"=4002", b"=4001"), (b"USER_SCOPE=1", b"USER_SCOPE=0"),
                         (b"UNRELATED_PRIVATE_VALUE", b"HEPTA_IB_PAPER_HOST")):
            with self.subTest(replacement=new):
                _put(self.proc / str(self.execution_pid) / "environ", self.environment.replace(old, new))
                with self.assertRaisesRegex(RuntimeError, "EXECUTION_PROFILE_INVALID"):
                    self._snapshot()

    def test_listener_and_reverse_accepted_socket_must_both_belong_to_given_pid(self):
        for pid, fd, inode, reason in ((self.execution_pid, 9, 111, "EXECUTION_SOCKET_REQUIRED"),
            (self.broker_pid, 92, 222, "ACCEPTED_SOCKET_REQUIRED"),
            (self.broker_pid, 87, 333, "LISTENER_REQUIRED")):
            with self.subTest(fd=fd):
                self._fd(pid, fd, 9999)
                try:
                    with self.assertRaisesRegex(RuntimeError, reason):
                        self._snapshot()
                finally:
                    self._fd(pid, fd, inode)

    def test_wrong_reverse_tuple_and_non_established_execution_are_rejected(self):
        old6, old4 = self.tcp6, self.tcp
        self.tcp6 = old6.replace("7F:B6CA", "7F:B6CB")
        self._tables()
        with self.assertRaisesRegex(RuntimeError, "ACCEPTED_SOCKET_REQUIRED"):
            self._snapshot()
        self.tcp6 = old6
        self.tcp = old4.replace(" 01 ", " 08 ")
        self._tables()
        with self.assertRaisesRegex(RuntimeError, "EXECUTION_SOCKET_REQUIRED"):
            self._snapshot()

    def test_ambiguous_execution_connection_is_not_guessed(self):
        self.tcp += self._row("0100007F:B6CB", "0100007F:0FA2", "01", 444)
        self._fd(self.execution_pid, 10, 444)
        self._tables()
        with self.assertRaisesRegex(RuntimeError, "EXECUTION_SOCKET_REQUIRED"):
            self._snapshot()

    def test_unrelated_socket_churn_does_not_change_the_bound_connection(self):
        expected = self._snapshot()
        self.tcp += self._row("0100007F:1234", "0100007F:5678", "01", 999)
        self._fd(self.execution_pid, 10, 999)
        self._tables()
        self.assertEqual(self._snapshot(), expected)

    def test_fd_reuse_mid_inspection_is_rejected(self):
        original = activation.process_socket_fds
        calls = 0
        def changed(pid):
            nonlocal calls
            calls += 1
            value = original(pid)
            if calls > 2 and pid == self.execution_pid:
                value.pop(111)
            return value
        with mock.patch.object(activation, "process_socket_fds", side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, "SOCKET_CHANGED_DURING_INSPECTION"):
                self._snapshot()

    def test_broker_uid_namespace_and_gnome_scope_are_required(self):
        base = self.proc / str(self.broker_pid)
        status = (base / "status").read_bytes()
        _put(base / "status", status.replace(f"Uid:\t{self.uid}".encode(), f"Uid:\t{self.uid + 1}".encode()))
        with self.assertRaisesRegex(RuntimeError, "PROCESS_OWNER_OR_LIFETIME"):
            self._snapshot()
        _put(base / "status", status)
        namespace = _put(self.root / "other-netns", b"other namespace")
        (base / "ns/net").unlink()
        (base / "ns/net").symlink_to(namespace)
        with self.assertRaisesRegex(RuntimeError, "TOPOLOGY_INVALID"):
            self._snapshot()
        (base / "ns/net").unlink()
        (base / "ns/net").symlink_to(self.root / "netns")
        _put(base / "cgroup", b"0::/unrelated-ibgateway.scope\n")
        with self.assertRaisesRegex(RuntimeError, "TOPOLOGY_INVALID"):
            self._snapshot()


class InteractiveBrokerCliTests(unittest.TestCase):
    def test_pid_is_optional_and_forwarded_only_when_explicit(self):
        command = ["activate_prepared_driver.py", "--instrument", "EUR.USD",
                   "--candidate-manifest", "/fixture/manifest.json", "--candidate-sha256", "a" * 64,
                   "--sessionctl", "/fixture/hepta-sessionctl", "--sessionctl-sha256", "b" * 64]
        for extra, expected in (([], None), (["--interactive-broker-pid", "1481499"], 1481499)):
            with self.subTest(expected=expected), mock.patch.object(sys, "argv", command + extra), \
                 mock.patch.object(activation, "maintenance_upgrade", return_value={"status": "ACTIVATED"}) as upgrade, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(activation.main(), 0)
                self.assertEqual(upgrade.call_args.kwargs["interactive_broker_pid"], expected)


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
