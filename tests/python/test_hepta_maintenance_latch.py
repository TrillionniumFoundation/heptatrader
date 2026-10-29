"""Operator new-risk hold tests on isolated private directories only.

Tests never touch real /run/user/UID paths, the IB API, or actual orders.
"""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import tempfile
import time
import unittest
import json
import shutil
from unittest.mock import patch
import hepta_maintenance_latch as latch

from hepta_maintenance_latch import HoldError, perform


class NativeReadDiagnosticTests(unittest.TestCase):
    def envelope(self, **changes):
        value = {"status": "error", "tool": "system.get_health",
                 "reason_code": "EXECUTION_EVENT_RESPONSE_READ_FAILED",
                 "detail": "SECRET_DETAIL", "order_id": -1,
                 "payload": {"account": "SECRET_ACCOUNT"}}
        value.update(changes)
        return value

    def failure(self, cause, *, method="system.get_health"):
        with patch.object(latch.subprocess, "run", side_effect=cause):
            with self.assertRaises(HoldError) as failed:
                latch._native_payload(Path("/fixture/control"), "crypto", method)
        return str(failed.exception)

    def test_only_complete_short_reviewed_stderr_code_is_visible(self):
        for raw in ("DISCOVERY_LIST_REJECTED\n", b"DISCOVERY_LIST_REJECTED\n"):
            with self.subTest(raw=raw):
                result = self.failure(subprocess.CalledProcessError(10, ["SECRET_ARGV"],
                    output="SECRET_NATIVE_STDOUT", stderr=raw))
                self.assertEqual(result,
                    "NATIVE_RESUME_READ_FAILED:crypto:system.get_health:rc=10:DISCOVERY_LIST_REJECTED")
                self.assertNotIn("SECRET", result)

    def test_server_error_projects_only_matching_envelope_reason(self):
        for method in ("system.get_health", "orders.list", "portfolio.list_positions"):
            for payload in (None, {"account": "SECRET_ACCOUNT"}):
                text = json.dumps(self.envelope(tool=method, payload=payload)) + "\n"
                for raw in (text, text.encode("utf-8")):
                    with self.subTest(method=method, payload=payload, encoded=type(raw) is bytes):
                        result = self.failure(subprocess.CalledProcessError(9, ["SECRET_ARGV"],
                            output=raw, stderr="SECRET_STDERR"), method=method)
                        self.assertEqual(result, "NATIVE_RESUME_READ_FAILED:crypto:" + method
                            + ":rc=9:EXECUTION_EVENT_RESPONSE_READ_FAILED")
                        self.assertNotIn("SECRET", result)

    def test_other_exit_codes_do_not_use_stdout_reason(self):
        for rc in (0, 2, 4, 6, 8, 10, 9.0):
            with self.subTest(rc=rc):
                result = self.failure(subprocess.CalledProcessError(rc, ["SECRET_ARGV"],
                    output=json.dumps(self.envelope()), stderr=""))
                self.assertTrue(result.endswith(":UNCLASSIFIED"))

    def test_malformed_mismatched_or_noncanonical_server_errors_are_unclassified(self):
        cases = [self.envelope(**change) for change in (
            {"tool": "orders.list"}, {"status": "ok"}, {"status": "rejected"},
            {"detail": {"secret": "SECRET_DETAIL"}}, {"detail": "bad\0detail"},
            {"payload": []}, {"order_id": True}, {"order_id": -2}, {"order_id": 2**63},
            {"reason_code": ""}, {"reason_code": None}, {"reason_code": "a_REASON"},
            {"reason_code": "1_REASON"}, {"reason_code": "BAD_REASON\nSECRET"},
            {"reason_code": "BAD_REASON:SECRET"}, {"reason_code": "BAD_REAS\u039fN"},
            {"reason_code": "A" * 129}, {"extra": "SECRET_ACCOUNT"})]
        cases.extend([None, [], "SECRET_STDOUT"])
        for field in self.envelope():
            missing = self.envelope()
            del missing[field]
            cases.append(missing)
        for value in cases:
            with self.subTest(value=value):
                result = self.failure(subprocess.CalledProcessError(9, ["SECRET_ARGV"],
                    output=json.dumps(value), stderr=""))
                self.assertEqual(result, "NATIVE_RESUME_READ_FAILED:crypto:system.get_health:rc=9:UNCLASSIFIED")
                self.assertNotIn("SECRET", result)

    def test_duplicate_invalid_or_nonfinite_json_is_not_projected(self):
        text = json.dumps(self.envelope())
        cases = [text + "SECRET_TRAILER", text[:-1],
                 text.replace('"status": "error"', '"status": "ok", "status": "error"'),
                 text.replace('"SECRET_ACCOUNT"', '{"same": 1, "same": 2}'),
                 text.replace('"SECRET_ACCOUNT"', 'NaN'),
                 text.replace('"SECRET_ACCOUNT"', 'Infinity'), b"\xffSECRET_STDOUT",
                 "[" * 1100 + "]" * 1100, None]
        for raw in cases:
            with self.subTest(raw_type=type(raw).__name__):
                result = self.failure(subprocess.CalledProcessError(9, ["SECRET_ARGV"],
                    output=raw, stderr=""))
                self.assertEqual(result, "NATIVE_RESUME_READ_FAILED:crypto:system.get_health:rc=9:UNCLASSIFIED")

    def test_stdout_byte_bound_precedes_json_parse_and_reason_bound_is_exact(self):
        oversized = json.dumps(self.envelope(detail="x" * 4096))
        multibyte = json.dumps(self.envelope(detail="\u00e9" * 2048), ensure_ascii=False)
        self.assertLess(len(multibyte), 4096)
        for raw in (oversized, oversized.encode("utf-8"), multibyte):
            with self.subTest(raw_type=type(raw).__name__), \
                    patch.object(latch.json, "loads", side_effect=AssertionError("UNBOUNDED_PARSE")):
                result = self.failure(subprocess.CalledProcessError(9, ["SECRET_ARGV"], output=raw))
                self.assertTrue(result.endswith(":UNCLASSIFIED"))
        reason = "A" + "_0" * 63 + "Z"
        self.assertEqual(len(reason), 128)
        result = self.failure(subprocess.CalledProcessError(9, ["SECRET_ARGV"],
            output=json.dumps(self.envelope(reason_code=reason))))
        self.assertTrue(result.endswith(":rc=9:" + reason))

    def test_arbitrary_multiline_oversized_or_nonascii_stderr_never_leaves_diagnostic(self):
        for raw in ("SECRET_TOKEN", "DISCOVERY_LIST_REJECTED\nSECRET_TOKEN",
                    "SECRET_TOKEN: DISCOVERY_LIST_REJECTED", "x"*129,
                    b"\xffSECRET_TOKEN", None):
            with self.subTest(raw=raw):
                result = self.failure(subprocess.CalledProcessError(10, ["SECRET_ARGV"],
                    output="SECRET_NATIVE_STDOUT", stderr=raw), method="orders.list")
                self.assertEqual(result, "NATIVE_RESUME_READ_FAILED:crypto:orders.list:rc=10:UNCLASSIFIED")
                self.assertNotIn("SECRET", result)

    def test_timeout_and_os_error_keep_context_without_exception_payload(self):
        cases = [(subprocess.TimeoutExpired(["SECRET_TOKEN_PATH"], 12,
                    output="SECRET_NATIVE_STDOUT", stderr="SECRET_STDERR"), "TIMEOUT"),
                 (FileNotFoundError(2, "SECRET_ENDPOINT_PATH"), "OS_ERROR")]
        for cause, code in cases:
            with self.subTest(code=code):
                self.assertEqual(self.failure(cause, method="portfolio.list_positions"),
                    "NATIVE_RESUME_READ_FAILED:crypto:portfolio.list_positions:"+code)


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.parent = root / "runtime";self.parent.mkdir(mode=0o700)
        self.control = self.parent / "control";self.control.mkdir(mode=0o750);self.control.chmod(0o750)
        self.state = root / "state";self.state.mkdir(mode=0o700)
        self.hold_path = self.state / "operator-maintenance" / "maintenance-hold.json"
        self.kill_path = self.control / "kill-switch"

    def run_action(self, action):
        return perform(action, self.control, self.state)

    def test_original_pause_resume_without_persistent_hold(self):
        self.assertFalse(self.run_action("maintenance-status")["persistent_hold"])
        p = self.run_action("pause")
        self.assertTrue(p["native_kill_switch_engaged"])
        self.assertEqual(self.kill_path.read_bytes(), b"engaged\n")
        self.assertFalse(p["persistent_hold"])
        self.assertFalse(self.run_action("paper-on")["native_kill_switch_engaged"])

    def test_operator_hold_defeats_resident_auto_rearm(self):
        p = self.run_action("maintenance-hold")
        self.assertTrue(p["persistent_hold"])
        self.assertTrue(p["native_kill_switch_engaged"])
        before=self.hold_path.read_bytes()
        for _ in range(3):
            with self.assertRaisesRegex(HoldError,"BLOCKED_BY_PERSISTENT"):
                self.run_action("paper-on")
        self.assertEqual(self.hold_path.read_bytes(),before)
        self.assertEqual(self.kill_path.read_bytes(),b"engaged\n")
        self.assertFalse(self.run_action("maintenance-status")["may_stop_driver"])

    def test_durable_hold_idempotent_and_explicit_release(self):
        self.run_action("maintenance-hold")
        before=self.hold_path.read_bytes()
        self.run_action("maintenance-hold")
        self.assertEqual(before,self.hold_path.read_bytes())
        released=self.run_action("maintenance-release")
        self.assertFalse(released["persistent_hold"])
        self.assertFalse(released["native_kill_switch_engaged"])
        self.assertFalse(self.hold_path.exists())
        self.assertFalse(self.kill_path.exists())
        self.assertFalse(self.run_action("paper-on")["persistent_hold"])

    def test_persisted_hold_blocks_rearm_even_if_native_marker_lost(self):
        self.run_action("maintenance-hold")
        self.kill_path.unlink()
        with self.assertRaisesRegex(HoldError,"BLOCKED_BY_PERSISTENT"):
            self.run_action("paper-on")
        with self.assertRaisesRegex(HoldError,"RELEASE_REQUIRES_BOTH"):
            self.run_action("maintenance-release")
        self.assertTrue(self.hold_path.exists())
        self.run_action("maintenance-hold")
        self.assertTrue(self.kill_path.exists())

    def test_corrupt_hold_fails_closed(self):
        self.run_action("maintenance-hold")
        self.hold_path.chmod(0o600)
        self.hold_path.write_bytes(b"{\"bad\":1}")
        with self.assertRaisesRegex(HoldError,"BAD_HOLD_SCOPE"):
            self.run_action("paper-on")
        self.assertTrue(self.kill_path.exists())

    def test_symlink_hold_refuses_and_does_not_follow(self):
        self.run_action("pause")
        maintenance = self.state / "operator-maintenance"
        maintenance.mkdir(mode=0o700,exist_ok=True)
        foreign=self.state/"foreign";foreign.write_text("untrusted")
        (maintenance/"maintenance-hold.json").symlink_to(foreign)
        with self.assertRaisesRegex(HoldError,"UNSAFE_MARKER"):
            self.run_action("paper-on")
        self.assertTrue(self.kill_path.exists())

    def test_modified_native_marker_does_not_get_erased(self):
        self.run_action("pause")
        self.kill_path.chmod(0o600)
        with self.assertRaisesRegex(HoldError,"UNSAFE_MARKER"):
            self.run_action("paper-on")
        self.assertTrue(self.kill_path.exists())

    def test_unexpected_native_marker_contents_fail_closed(self):
        self.run_action("pause")
        self.kill_path.chmod(0o600)
        self.kill_path.write_bytes(b"not-the-original-native-marker")
        self.kill_path.chmod(0o440)
        with self.assertRaisesRegex(HoldError,"UNKNOWN_EXISTING_NATIVE"):
            self.run_action("maintenance-hold")
        self.assertTrue(self.kill_path.exists())

    def _make_independent_empty_marker(self):
        marker=self.control/"kill-switch"
        marker.write_bytes(b"")
        marker.chmod(0o440)
        return marker

    def test_execution_owned_empty_marker_is_visible_but_not_auto_rearmed(self):
        marker=self._make_independent_empty_marker()
        before=marker.stat()
        status=self.run_action("maintenance-status")
        self.assertTrue(status["native_kill_switch_engaged"])
        self.assertEqual(status["native_marker_origin"], "INDEPENDENT_EMPTY_NATIVE")
        self.assertEqual(status["native_marker_origin_evidence"], "PAYLOAD_SHAPE_ONLY")
        self.assertFalse(status["native_marker_creator_verified"])
        self.assertFalse(status["may_stop_driver"])
        with self.assertRaisesRegex(HoldError,"INDEPENDENT_NATIVE_HOLD_NOT_REARMABLE"):
            self.run_action("paper-on")
        self.assertEqual((marker.stat().st_ino,marker.stat().st_size),
                         (before.st_ino,0))

    def test_empty_native_marker_survives_operator_hold_and_release(self):
        marker=self._make_independent_empty_marker()
        inode=marker.stat().st_ino
        status=self.run_action("maintenance-hold")
        self.assertTrue(status["persistent_hold"])
        self.assertEqual(status["native_marker_origin"], "INDEPENDENT_EMPTY_NATIVE")
        with self.assertRaisesRegex(HoldError,"BLOCKED_BY_PERSISTENT"):
            self.run_action("paper-on")
        release=self.run_action("maintenance-release")
        self.assertFalse(release["persistent_hold"])
        self.assertTrue(release["native_kill_switch_engaged"])
        self.assertEqual(marker.stat().st_ino,inode)
        with self.assertRaisesRegex(HoldError,"INDEPENDENT_NATIVE_HOLD_NOT_REARMABLE"):
            self.run_action("paper-on")

    def test_expected_hold_release_preserves_the_original_empty_native_identity(self):
        marker = self._make_independent_empty_marker()
        original_native = latch._identity(marker.stat())
        created = perform("maintenance-hold", self.control, self.state, task="unique-deployment-owner")
        expected = created["hold_sha256"]
        self.assertEqual(expected, hashlib.sha256(self.hold_path.read_bytes()).hexdigest())
        self.assertEqual(created["hold_details"]["task"], "unique-deployment-owner")
        with patch.object(latch, "_command", side_effect=AssertionError("no native resume/service action")):
            result = perform("maintenance-release", self.control, self.state,
                             expected_hold_sha256=expected)
        self.assertFalse(result["persistent_hold"])
        self.assertIsNone(result["hold_sha256"])
        self.assertTrue(result["native_kill_switch_engaged"])
        self.assertEqual(result["native_marker_origin"], "INDEPENDENT_EMPTY_NATIVE")
        self.assertEqual(latch._identity(marker.stat()), original_native)

    def test_expected_hash_is_compared_after_acquiring_the_original_operator_lock(self):
        self._make_independent_empty_marker()
        perform("maintenance-hold", self.control, self.state, task="our-deployment")
        expected = hashlib.sha256(self.hold_path.read_bytes()).hexdigest()
        native = latch._identity(self.kill_path.stat())
        lock = os.open(self.state / latch.LOCK, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(perform, "maintenance-release", self.control, self.state,
                                     expected_hold_sha256=expected)
                try:
                    time.sleep(0.06)
                    self.assertFalse(future.done())
                    replacement = json.loads(self.hold_path.read_bytes())
                    replacement["task"] = "different-operator-hold"
                    raw = (json.dumps(replacement) + "\n").encode()
                    self.hold_path.write_bytes(raw)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                with self.assertRaisesRegex(HoldError, "MAINTENANCE_HOLD_IDENTITY_MISMATCH"):
                    future.result(timeout=2)
        finally:
            os.close(lock)
        self.assertEqual(self.hold_path.read_bytes(), raw)
        self.assertEqual(latch._identity(self.kill_path.stat()), native)

    def test_expected_hold_hash_is_validated_and_scoped_without_touching_markers(self):
        self.run_action("maintenance-hold")
        before = self.hold_path.read_bytes(), self.kill_path.read_bytes()
        for value in ("", "A" * 64, "x" * 64, "a" * 63):
            with self.subTest(value=value), self.assertRaisesRegex(HoldError, "INVALID_EXPECTED"):
                perform("maintenance-release", self.control, self.state, expected_hold_sha256=value)
        with self.assertRaisesRegex(HoldError, "REQUIRES_MAINTENANCE_RELEASE"):
            perform("paper-on", self.control, self.state, expected_hold_sha256="a" * 64)
        self.assertEqual((self.hold_path.read_bytes(), self.kill_path.read_bytes()), before)

    def test_unrecognized_native_marker_allows_status_only(self):
        marker=self.control/"kill-switch"
        marker.write_bytes(b"unknown-origin")
        marker.chmod(0o440)
        status=self.run_action("maintenance-status")
        self.assertEqual(status["native_marker_origin"], "UNRECOGNIZED_INDEPENDENT_NATIVE")
        self.assertTrue(status["native_kill_switch_engaged"])
        with self.assertRaisesRegex(HoldError,"UNKNOWN_EXISTING_NATIVE"):
            self.run_action("paper-on")
        with self.assertRaisesRegex(HoldError,"UNKNOWN_EXISTING_NATIVE"):
            self.run_action("maintenance-hold")
        self.assertEqual(marker.read_bytes(),b"unknown-origin")

    def test_bad_state_directory_refuses_without_touching_switch(self):
        self.state.chmod(0o777)
        with self.assertRaisesRegex(HoldError,"UNSAFE_DIRECTORY"):
            self.run_action("pause")
        self.assertFalse(self.kill_path.exists())

    def test_lock_serializes_concurrent_paper_on(self):
        self.run_action("pause")
        lock=self.state/"operator-maintenance.lock"
        fd=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.run_action,"paper-on")
                time.sleep(0.06)
                self.assertFalse(future.done())
                fcntl.flock(fd,fcntl.LOCK_UN)
                self.assertFalse(future.result(timeout=2)["native_kill_switch_engaged"])
        finally:
            os.close(fd)

    def test_release_without_hold_must_not_erase_legacy_pause(self):
        self.run_action("pause")
        with self.assertRaisesRegex(HoldError,"RELEASE_REQUIRES_BOTH"):
            self.run_action("maintenance-release")
        self.assertTrue(self.kill_path.exists())

    def test_latch_file_is_private_and_has_no_order_permission(self):
        result=self.run_action("maintenance-hold")
        self.assertEqual(stat.S_IMODE(self.hold_path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.kill_path.stat().st_mode),0o440)
        self.assertEqual(result["broker_orders_sent"],0)
        self.assertFalse(result["may_stop_driver"])
        self.assertEqual(result["schema"],"heptatrader.maintenance-latch-status.v1")

    def test_status_reports_post_action_marker_origin(self):
        paused = self.run_action("pause")
        self.assertEqual(paused["native_marker_origin"], "CLI_TAGGED_PAUSE")
        self.assertTrue(paused["new_paper_risk_blocked"])
        resumed = self.run_action("paper-on")
        self.assertEqual(resumed["native_marker_origin"], "ABSENT")
        self.assertEqual(resumed["blockers"], [])
        self.assertFalse(resumed["new_paper_risk_blocked"])
        self.run_action("maintenance-hold")
        released = self.run_action("maintenance-release")
        self.assertEqual(released["native_marker_origin"], "ABSENT")
        self.assertIsNone(released["hold_details"])

    def test_optional_hold_context_is_visible_and_not_rewritten(self):
        status = perform("maintenance-hold", self.control, self.state,
                         reason="install verified driver", task="driver update",
                         resume_when="original maintenance owner completes review")
        self.assertEqual(status["hold_details"]["reason"], "install verified driver")
        self.assertEqual(status["hold_details"]["uid"], os.geteuid())
        self.assertEqual(status["hold_details"]["created_by"], "heptatrader-cli")
        before = self.hold_path.read_bytes()
        repeated = perform("maintenance-hold", self.control, self.state,
                           reason="later caller cannot replace original ownership")
        self.assertEqual(self.hold_path.read_bytes(), before)
        self.assertEqual(repeated["hold_details"]["created_at_ms"],
                         status["hold_details"]["created_at_ms"])
        self.assertEqual(repeated["hold_details"]["reason"], "install verified driver")
        self.assertFalse(status["may_stop_driver"])

    def test_legacy_hold_age_is_visible_without_inventing_a_reason(self):
        self.run_action("maintenance-hold")
        legacy = {"schema": latch.SCHEMA, "uid": os.geteuid(),
                  "effect": "NEW_PAPER_RISK_HOLD_ONLY",
                  "created_at_ms": int(time.time() * 1000) - 3600000}
        raw = (json.dumps(legacy) + "\n").encode()
        self.hold_path.write_bytes(raw)
        status = self.run_action("maintenance-status")
        self.assertGreaterEqual(status["hold_details"]["age_seconds"], 3600)
        self.assertIsNone(status["hold_details"]["reason"])
        self.assertIsNone(status["hold_details"]["created_by"])
        self.assertEqual(self.hold_path.read_bytes(), raw)
        self.assertTrue(status["new_paper_risk_blocked"])

    def test_failed_release_restores_exact_original_hold_record(self):
        perform("maintenance-hold", self.control, self.state,
                reason="original task", task="retained", resume_when="explicit completion")
        original = self.hold_path.read_bytes()
        real_unlink = latch._unlink
        def fail_native(fd, name, mode):
            if name == latch.SWITCH:
                raise OSError("synthetic unlink failure")
            return real_unlink(fd, name, mode)
        with patch.object(latch, "_unlink", side_effect=fail_native):
            with self.assertRaisesRegex(OSError, "synthetic"):
                self.run_action("maintenance-release")
        self.assertEqual(self.hold_path.read_bytes(), original)
        self.assertEqual(self.kill_path.read_bytes(), b"engaged\n")
        self.assertFalse(self.run_action("maintenance-status")["may_stop_driver"])

    def test_public_wrapper_forwards_explicit_resume_to_its_own_helper(self):
        release = self.parent / "release"
        release.mkdir()
        source = Path(__file__).resolve().parents[2] / "scripts/heptatrader-user"
        wrapper = release / "heptatrader"
        wrapper.write_bytes(source.read_bytes())
        wrapper.chmod(0o700)
        (release / "hepta_maintenance_latch.py").write_text(
            "import json,sys; print(json.dumps(sys.argv[1:]))")
        fake_bin = self.parent / "bin"
        fake_bin.mkdir()
        systemctl = fake_bin / "systemctl"
        systemctl.write_text("#!/bin/sh\nexit 0\n")
        systemctl.chmod(0o700)
        for args in (["paper-on", "--resume-native-hold", "--check"],
                     ["maintenance-release", "--expected-hold-sha256", "a" * 64]):
            with self.subTest(args=args):
                result = subprocess.run([str(wrapper), *args], capture_output=True, text=True,
                                        timeout=5, env={**os.environ, "HOME": str(self.parent),
                                        "PATH": str(fake_bin) + os.pathsep + os.defpath})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), args)


class MaintenanceReleaseWrapperTests(unittest.TestCase):
    """Run the real bash wrapper against isolated manager/helper executables."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-maintenance-wrapper-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = self.root / "release"; self.release.mkdir()
        self.bin = self.root / "bin"; self.bin.mkdir()
        self.wrapper = self.release / "heptatrader"
        self.wrapper.write_bytes((Path(__file__).resolve().parents[2] / "scripts/heptatrader-user").read_bytes())
        (self.release / "hepta_maintenance_latch.py").write_text(
            "import json,sys;print(json.dumps(sys.argv[1:]))\n")
        systemctl = self.bin / "systemctl"
        systemctl.write_text("#!" + sys.executable + "\n" + '''
import json,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
args=sys.argv[1:]
with (root/'calls').open('a') as stream:stream.write(json.dumps(args)+'\\n')
states=json.loads((root/'manager.json').read_text())
assert args[0]=='--user'
if args[1]=='is-active':
    units=[value for value in args[2:] if value.endswith('.service')]
    sys.exit(0 if any(states[name]['ActiveState']=='active' for name in units) else 3)
if args[1]=='show':
    state=states[args[2]]
    for index,value in enumerate(args):
        if value=='-p' and args[index+1] in state:print(args[index+1]+'='+state[args[index+1]])
    for line in state.get('extra_lines',[]):print(line)
    sys.exit(0)
raise AssertionError('non-read-only systemctl operation: '+str(args))
''')
        systemctl.chmod(0o700)
        self.paper = "heptatrader-paper-driver.service"
        self.crypto = "heptatrader-crypto-driver.service"
        self.states = {
            "heptatrader-execution.service": {"ActiveState": "active"},
            "heptatrader-gateway.service": {"ActiveState": "active"},
            self.paper: {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled",
                         "MainPID": "4201", "RefuseManualStop": "yes"},
            self.crypto: {"LoadState": "loaded", "ActiveState": "inactive", "UnitFileState": "disabled",
                          "MainPID": "0", "RefuseManualStop": "yes"},
        }
        self.args = ["maintenance-release", "--expected-hold-sha256", "a" * 64]

    def invoke(self):
        (self.root / "manager.json").write_text(json.dumps(self.states))
        (self.root / "calls").unlink(missing_ok=True)
        result = subprocess.run(["/bin/bash", str(self.wrapper), *self.args], capture_output=True, text=True,
            timeout=5, env={**os.environ, "PATH": str(self.bin) + os.pathsep + os.defpath})
        self.calls = [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]
        self.assertTrue(all(call[1] in ("is-active", "show") for call in self.calls))
        return result

    def test_disabled_inactive_btc_allows_helper_to_release_only_the_expected_hold(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.args)
        self.assertEqual([call[2] for call in self.calls if call[1] == "show"], [self.crypto])

    def test_ordinary_active_drivers_keep_the_active_path(self):
        self.states[self.crypto].update(ActiveState="active", MainPID="4202")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.args)
        self.assertFalse(any(call[1] == "show" for call in self.calls))

    def test_abnormal_inactive_driver_policy_never_invokes_release_helper(self):
        original = dict(self.states[self.crypto])
        cases = [dict(original, UnitFileState="enabled"), dict(original, MainPID="99"),
                 dict(original, LoadState="not-found"), dict(original, RefuseManualStop="no"),
                 dict(original, ActiveState="failed"), dict(original, extra_lines=["MainPID=0"]),
                 {key: value for key, value in original.items() if key != "UnitFileState"}]
        for state in cases:
            with self.subTest(state=state):
                self.states[self.crypto] = state
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_both_core_services_must_still_be_active(self):
        for unit in ("heptatrader-execution.service", "heptatrader-gateway.service"):
            with self.subTest(unit=unit):
                self.states[unit]["ActiveState"] = "inactive"
                try:
                    result = self.invoke()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertFalse(any(call[1] == "show" for call in self.calls))
                finally:
                    self.states[unit]["ActiveState"] = "active"


class NativeHoldResumeTests(unittest.TestCase):
    setUp = HoldTests.setUp
    run_action = HoldTests.run_action
    _make_independent_empty_marker = HoldTests._make_independent_empty_marker

    def health(self):
        value = {
            "execution_mode": "PAPER", "gateway_ready": True,
            "remote_execution_ready": True, "execution_service_epoch": "native-original",
            "execution_service_fencing_generation": 3,
            "execution_authoritative_health": {
                "source": "IB", "authoritative": True, "authorized_connector_count": 1,
                "terminal_download_complete": True, "account_broker_sample_connected": True,
                "account_broker_sample_event_stream_authoritative": True,
                "account_broker_sample_coherent": True, "account_local_drain_observation_present": True,
                "startup_recovery_read_only": False, "owner_fenced": False,
                "owner_recovery_only": False, "account_new_entry_fenced": False,
                "mutation_blocked": False, "risk_mutation_dispatch_in_flight": False,
                "native_new_risk_guard_blocked": True, "mutation_ready": False,
                "mutation_reason": "IB_PAPER_KILL_SWITCH_ENGAGED",
                "account_local_uncertain_commands": 0,
                "account_local_venue_dispatches_in_flight": 0,
                "account_local_unattributed_owned_orders": 0,
                "account_local_unattributed_uncertain_commands": 0}}
        if not self.kill_path.exists():
            value["execution_authoritative_health"].update(
                native_new_risk_guard_blocked=False, mutation_ready=True, mutation_reason="")
        return value

    def command(self, argv):
        if argv[0] == "systemctl":
            props = {"ActiveState": "active", "SubState": "running",
                     "MainPID": "100", "InvocationID": "same-invocation"}
            if argv[3] == "heptatrader-execution.service":
                props.update(Type="simple", KillMode="control-group",
                    ExecStartPre=("{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -B "
                    + str(Path(latch.__file__).resolve())
                    + " native-startup-hold ; ignore_errors=no ; pid=0 ; status=0/0 }"))
            return "".join(key + "=" + value + "\n" for key, value in props.items())
        self.assertEqual(Path(argv[0]).name, "heptactl")
        self.assertEqual(argv[-2], "call")
        method = argv[-1]
        self.assertIn(method, ("system.get_health", "orders.list", "portfolio.list_positions"))
        if method == "system.get_health":
            payload = self.health()
        elif method == "orders.list":
            payload = {"source": "IB", "authoritative": True, "global_active_orders_complete": True,
                       "owner_projection_complete": True, "active_order_ids": [41],
                       "owned_active_order_ids": [41]}
        else:
            payload = {"source": "IB", "authoritative": True,
                       "positions": [{"instrument": "EUR.USD", "quantity": 1000.0}]}
        return json.dumps({"status": "ok", "tool": method, "payload": payload})

    def resume(self, *, check_only=False):
        return perform("paper-on", self.control, self.state,
                       resume_native_hold=True, check_only=check_only)

    def test_explicit_resume_reads_both_owners_and_allows_known_open_risk(self):
        self._make_independent_empty_marker()
        with patch.object(latch, "_command", side_effect=self.command) as calls:
            result = self.resume()
        self.assertFalse(self.kill_path.exists())
        self.assertEqual(result["native_hold_resume"]["state"], "RELEASED_NATIVE_GUARD_VERIFIED")
        self.assertEqual(set(result["native_hold_resume"]["owners"]), {"paper", "crypto"})
        native = [call.args[0] for call in calls.call_args_list if call.args[0][0] != "systemctl"]
        self.assertEqual(len(native), 10)
        self.assertEqual({Path(args[args.index("--token-file") + 1]).name for args in native},
                         {"paper.token", "crypto.token"})
        self.assertEqual(result["broker_orders_sent"], 0)
        self.assertFalse(result["may_stop_driver"])

    def test_crypto_discovery_failure_names_the_owner_and_preserves_original_marker(self):
        marker = self._make_independent_empty_marker()
        before = latch._identity(marker.stat())
        for check_only in (True, False):
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                if argv[-1] == "system.get_health" and any(arg.endswith("/crypto.token") for arg in argv):
                    raise subprocess.CalledProcessError(10, argv, output="SECRET_NATIVE_STDOUT",
                        stderr="DISCOVERY_LIST_REJECTED\n")
                return subprocess.CompletedProcess(argv, 0, stdout=self.command(argv), stderr="")
            with self.subTest(check_only=check_only), \
                    patch.object(latch.subprocess, "run", side_effect=execute), \
                    patch.object(latch.os, "unlink", side_effect=AssertionError("NO_RELEASE_AFTER_FAILED_OWNER_READ")):
                with self.assertRaises(HoldError) as failed:
                    self.resume(check_only=check_only)
            self.assertEqual(str(failed.exception),
                "NATIVE_RESUME_READ_FAILED:crypto:system.get_health:rc=10:DISCOVERY_LIST_REJECTED")
            self.assertEqual(latch._identity(marker.stat()), before)
            native = [argv for argv in calls if argv[0] != "systemctl"]
            self.assertEqual(len(native), 5)
            self.assertEqual({Path(argv[argv.index("--token-file")+1]).name for argv in native},
                             {"paper.token", "crypto.token"})

    def test_server_read_error_is_diagnostic_only_and_preserves_original_marker(self):
        marker = self._make_independent_empty_marker()
        before = latch._identity(marker.stat())
        for check_only in (True, False):
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                if argv[-1] == "system.get_health":
                    reply = {"status": "error", "tool": "system.get_health",
                             "reason_code": "EXECUTION_EVENT_RESPONSE_READ_FAILED",
                             "detail": "SECRET_DETAIL", "order_id": -1,
                             "payload": {"account": "SECRET_ACCOUNT"}}
                    raise subprocess.CalledProcessError(9, argv, output=json.dumps(reply), stderr="")
                return subprocess.CompletedProcess(argv, 0, stdout=self.command(argv), stderr="")
            with self.subTest(check_only=check_only), \
                    patch.object(latch.subprocess, "run", side_effect=execute), \
                    patch.object(latch.os, "unlink", side_effect=AssertionError("NO_RELEASE_AFTER_FAILED_READ")):
                with self.assertRaises(HoldError) as failed:
                    self.resume(check_only=check_only)
            self.assertEqual(str(failed.exception),
                "NATIVE_RESUME_READ_FAILED:paper:system.get_health:rc=9:EXECUTION_EVENT_RESPONSE_READ_FAILED")
            self.assertEqual(latch._identity(marker.stat()), before)
            native = [argv for argv in calls if argv[0] != "systemctl"]
            self.assertEqual(len(native), 1)  # No retry or later owner read was introduced.
            self.assertEqual(native[0][-2:], ["call", "system.get_health"])

    def test_post_unlink_native_failure_reengages_instead_of_claiming_release(self):
        for condition in ("uncertain", "second_owner", "missing", "epoch", "generation", "service",
                          "transport", "json", "encoding"):
            with self.subTest(condition=condition):
                self.kill_path.unlink(missing_ok=True)
                self._make_independent_empty_marker()
                def reply(argv):
                    raw = self.command(argv)
                    if self.kill_path.exists():
                        return raw
                    if argv[0] == "systemctl":
                        return raw.replace("MainPID=100", "MainPID=101") if condition == "service" else raw
                    if argv[-1] != "system.get_health":
                        return raw
                    if condition == "transport":
                        raise HoldError("native postcheck timed out")
                    if condition == "json":
                        return "{"
                    if condition == "encoding":
                        raise UnicodeDecodeError("utf8", b"\xff", 0, 1, "bad native output")
                    item = json.loads(raw)
                    health = item["payload"]["execution_authoritative_health"]
                    if condition == "uncertain" or (condition == "second_owner"
                            and any(arg.endswith("/crypto.token") for arg in argv)):
                        health.update(native_new_risk_guard_blocked=True, mutation_ready=False,
                                      mutation_reason="IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN")
                    elif condition == "missing":
                        del health["native_new_risk_guard_blocked"]
                    elif condition == "epoch":
                        item["payload"]["execution_service_epoch"] = "restarted"
                    elif condition == "generation":
                        item["payload"]["execution_service_fencing_generation"] += 1
                    return json.dumps(item)
                with patch.object(latch, "_command", side_effect=reply):
                    with self.assertRaises(latch.ResumeFailure) as failed:
                        self.resume()
                self.assertEqual(failed.exception.status, "NATIVE_RESUME_FAILED_REENGAGED")
                self.assertEqual(self.kill_path.read_bytes(), b"")

    def test_post_unlink_failure_reports_uncertainty_if_rearm_fails(self):
        self._make_independent_empty_marker()
        def reply(argv):
            if argv[-1] == "system.get_health" and not self.kill_path.exists():
                raise HoldError("native postcheck timed out")
            return self.command(argv)
        with patch.object(latch, "_command", side_effect=reply):
            with patch.object(latch, "_write_once", side_effect=OSError("rearm failed")):
                with self.assertRaises(latch.ResumeFailure) as failed:
                    self.resume()
        self.assertEqual(failed.exception.status, "NATIVE_RESUME_STATE_UNCERTAIN")
        self.assertFalse(self.kill_path.exists())

    def test_post_release_reports_resident_activity_without_reengaging(self):
        self._make_independent_empty_marker()
        def reply(argv):
            raw = self.command(argv)
            if argv[-1] == "system.get_health" and not self.kill_path.exists():
                item = json.loads(raw)
                item["payload"]["execution_authoritative_health"].update(
                    mutation_ready=False, mutation_reason="RISK_MUTATION_DISPATCH_IN_FLIGHT",
                    risk_mutation_dispatch_in_flight=True, account_local_venue_dispatches_in_flight=1)
                return json.dumps(item)
            return raw
        with patch.object(latch, "_command", side_effect=reply):
            result = self.resume()
        self.assertFalse(self.kill_path.exists())
        for health in result["native_hold_resume"]["post_release"].values():
            self.assertFalse(health["native_new_risk_guard_blocked"])
            self.assertFalse(health["mutation_ready"])
            self.assertEqual(health["mutation_reason"], "RISK_MUTATION_DISPATCH_IN_FLIGHT")

    def test_read_only_check_preserves_original_empty_marker(self):
        self._make_independent_empty_marker()
        before = latch._identity(self.kill_path.stat())
        with patch.object(latch, "_command", side_effect=self.command):
            result = self.resume(check_only=True)
        self.assertEqual(latch._identity(self.kill_path.stat()), before)
        self.assertEqual(result["native_hold_resume"]["state"], "PRECHECK_ONLY_NATIVE_RELEASE_UNVERIFIED")

    def _apply_user_tmpfiles(self):
        source = Path(__file__).resolve().parents[2] / "tmpfiles.d/heptatrader-user.conf"
        text = source.read_text()
        entries = [line.split() for line in text.splitlines()
                   if line.strip() and not line.lstrip().startswith("#")]
        # Expand only the user-runtime prefix into this isolated fixture. No
        # real /run/user path or unrelated system tmpfiles source is evaluated.
        self.assertTrue(all(entry[1].startswith("%t/heptatrader") for entry in entries))
        config = Path(self.temporary.name) / "user-tmpfiles.conf"
        config.write_text(text.replace("%t/heptatrader", str(self.parent)))
        result = subprocess.run([shutil.which("systemd-tmpfiles"), "--user", "--create",
                                 "--prefix=" + str(self.parent), str(config)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("systemd-tmpfiles"), "requires systemd-tmpfiles")
    def test_real_user_tmpfiles_creates_independent_empty_hold(self):
        self.control.rmdir(); self.parent.rmdir()
        self._apply_user_tmpfiles()
        self.assertEqual(self.kill_path.read_bytes(), b"")
        self.assertEqual(stat.S_IMODE(self.kill_path.stat().st_mode), 0o440)
        before = latch._identity(self.kill_path.stat())
        startup = self.run_action("native-startup-hold")
        self.assertFalse(startup["created_marker"])
        self.assertEqual(latch._identity(self.kill_path.stat()), before)
        with self.assertRaisesRegex(HoldError, "INDEPENDENT_NATIVE_HOLD_NOT_REARMABLE"):
            self.run_action("paper-on")
        with patch.object(latch, "_command", side_effect=self.command):
            checked = self.resume(check_only=True)
        self.assertEqual(checked["native_hold_resume"]["state"],
                         "PRECHECK_ONLY_NATIVE_RELEASE_UNVERIFIED")
        self.assertEqual(latch._identity(self.kill_path.stat()), before)

    @unittest.skipUnless(shutil.which("systemd-tmpfiles"), "requires systemd-tmpfiles")
    def test_real_user_tmpfiles_reinstall_preserves_existing_unknown_payload(self):
        self.kill_path.write_bytes(b"engaged")  # Historical tmpfiles payload has no newline.
        self.kill_path.chmod(0o440)
        inode = self.kill_path.stat().st_ino
        self._apply_user_tmpfiles()
        self.assertEqual(self.kill_path.read_bytes(), b"engaged")
        self.assertEqual(self.kill_path.stat().st_ino, inode)
        self.assertFalse(self.run_action("native-startup-hold")["created_marker"])
        for check_only in (False, True):
            with self.subTest(check_only=check_only), self.assertRaisesRegex(
                    HoldError, "UNKNOWN_EXISTING_NATIVE_KILLSWITCH"):
                perform("paper-on", self.control, self.state,
                        resume_native_hold=check_only, check_only=check_only)
        self.assertEqual(self.kill_path.read_bytes(), b"engaged")
        self.assertEqual(self.kill_path.stat().st_ino, inode)

    def test_explicit_flag_is_scoped_and_never_clears_persistent_hold(self):
        for action in ("pause", "maintenance-release", "native-startup-hold"):
            with self.subTest(action=action), self.assertRaisesRegex(HoldError, "OPTIONS_REQUIRE"):
                perform(action, self.control, self.state, resume_native_hold=True)
        with self.assertRaisesRegex(HoldError, "OPTIONS_REQUIRE"):
            perform("paper-on", self.control, self.state, check_only=True)
        self._make_independent_empty_marker()
        self.run_action("maintenance-hold")
        with patch.object(latch, "_command") as calls:
            with self.assertRaisesRegex(HoldError, "PERSISTENT"):
                self.resume()
            calls.assert_not_called()
        self.assertTrue(self.kill_path.exists())

    def test_kill_reason_does_not_hide_other_blockers(self):
        changes = {
            "authoritative": False, "terminal_download_complete": False,
            "account_broker_sample_connected": False,
            "account_broker_sample_event_stream_authoritative": False,
            "account_broker_sample_coherent": False, "account_local_drain_observation_present": False,
            "startup_recovery_read_only": True, "owner_fenced": True,
            "owner_recovery_only": True, "account_new_entry_fenced": True,
            "mutation_blocked": True, "risk_mutation_dispatch_in_flight": True,
            "native_new_risk_guard_blocked": False, "mutation_ready": True,
            "authorized_connector_count": True, "account_local_uncertain_commands": 1,
            "account_local_venue_dispatches_in_flight": 1,
            "account_local_unattributed_owned_orders": 1,
            "account_local_unattributed_uncertain_commands": 1}
        self._make_independent_empty_marker()
        for key, value in changes.items():
            with self.subTest(key=key):
                def reply(argv):
                    raw = self.command(argv)
                    if argv[-1] == "system.get_health":
                        item = json.loads(raw)
                        item["payload"]["execution_authoritative_health"][key] = value
                        return json.dumps(item)
                    return raw
                with patch.object(latch, "_command", side_effect=reply):
                    with self.assertRaises(HoldError):
                        self.resume()
                self.assertTrue(self.kill_path.exists())

    def test_missing_fake_boolean_and_live_mode_are_rejected(self):
        self._make_independent_empty_marker()
        for change in ("missing", "fake_bool", "live"):
            def reply(argv):
                raw = self.command(argv)
                if argv[-1] == "system.get_health":
                    item = json.loads(raw)
                    if change == "missing":
                        del item["payload"]["execution_authoritative_health"]["owner_fenced"]
                    elif change == "fake_bool":
                        item["payload"]["gateway_ready"] = 1
                    else:
                        item["payload"]["execution_mode"] = "LIVE"
                    return json.dumps(item)
                return raw
            with self.subTest(change=change), patch.object(latch, "_command", side_effect=reply):
                with self.assertRaises(HoldError):
                    self.resume()
            self.assertTrue(self.kill_path.exists())

    def test_second_owner_fence_and_postfill_snapshot_reject(self):
        self._make_independent_empty_marker()
        for method in ("system.get_health", "orders.list", "portfolio.list_positions"):
            def reply(argv):
                raw = self.command(argv)
                if argv[-1] == method and any(arg.endswith("/crypto.token") for arg in argv):
                    item = json.loads(raw)
                    if method == "system.get_health":
                        item["payload"]["execution_authoritative_health"]["owner_fenced"] = True
                    else:
                        item["payload"]["authoritative"] = False
                    return json.dumps(item)
                return raw
            with self.subTest(method=method), patch.object(latch, "_command", side_effect=reply):
                with self.assertRaises(HoldError):
                    self.resume()
            self.assertTrue(self.kill_path.exists())

    def test_bad_transport_json_and_unbounded_position_refuse(self):
        self._make_independent_empty_marker()
        for condition in ("transport", "json", "nan"):
            def reply(argv):
                raw = self.command(argv)
                if argv[-1] == "portfolio.list_positions":
                    if condition == "transport":
                        raise HoldError("NATIVE_RESUME_READ_FAILED:TimeoutExpired")
                    if condition == "json":
                        return "{"
                    item = json.loads(raw)
                    item["payload"]["positions"][0]["quantity"] = float("nan")
                    return json.dumps(item)
                return raw
            with self.subTest(condition=condition), patch.object(latch, "_command", side_effect=reply):
                with self.assertRaises(HoldError):
                    self.resume()
            self.assertTrue(self.kill_path.exists())

    def test_changed_epoch_or_generation_refuses(self):
        self._make_independent_empty_marker()
        for key, value in (("execution_service_epoch", "restarted"),
                           ("execution_service_fencing_generation", 4)):
            count = 0
            def reply(argv):
                nonlocal count
                raw = self.command(argv)
                if argv[-1] == "system.get_health":
                    count += 1
                    if count == 2:
                        item = json.loads(raw); item["payload"][key] = value
                        return json.dumps(item)
                return raw
            with self.subTest(key=key), patch.object(latch, "_command", side_effect=reply):
                with self.assertRaisesRegex(HoldError, "INSTANCE_CHANGED"):
                    self.resume()
            self.assertTrue(self.kill_path.exists())

    def test_uncoordinated_startup_and_nonstandard_kill_mode_reject(self):
        self._make_independent_empty_marker()
        for old, new in (("native-startup-hold", "legacy-startup-hold"),
                         ("ignore_errors=no", "ignore_errors=yes"),
                         ("KillMode=control-group", "KillMode=process"),
                         ("Type=simple", "Type=forking")):
            def reply(argv):
                return self.command(argv).replace(old, new) if argv[0] == "systemctl" else self.command(argv)
            with self.subTest(new=new), patch.object(latch, "_command", side_effect=reply):
                with self.assertRaisesRegex(HoldError, "NOT_COORDINATED"):
                    self.resume()
            self.assertTrue(self.kill_path.exists())

    def test_changed_service_identity_refuses_but_completed_hook_timestamps_do_not(self):
        self._make_independent_empty_marker()
        count = 0
        def reply(argv):
            nonlocal count
            raw = self.command(argv)
            if argv[0] == "systemctl":
                count += 1
                if count >= 3:
                    return raw.replace("MainPID=100", "MainPID=101")
            return raw
        with patch.object(latch, "_command", side_effect=reply):
            with self.assertRaisesRegex(HoldError, "SERVICE_INSTANCE_CHANGED"):
                self.resume()
        count = 0
        def timestamp(argv):
            nonlocal count
            raw = self.command(argv)
            count += 1
            return raw.replace("pid=0 ; status=0/0", "pid=" + str(count) + " ; status=0/0")
        with patch.object(latch, "_command", side_effect=timestamp):
            self.resume(check_only=True)
        self.assertTrue(self.kill_path.exists())

    def test_same_valid_marker_replacement_is_preserved(self):
        self._make_independent_empty_marker()
        changed = False
        def reply(argv):
            nonlocal changed
            raw = self.command(argv)
            if not changed and argv[-1] == "orders.list":
                changed = True
                replacement = self.control / "replacement"
                replacement.write_bytes(b""); replacement.chmod(0o440)
                os.replace(replacement, self.kill_path)
            return raw
        with patch.object(latch, "_command", side_effect=reply):
            with self.assertRaisesRegex(HoldError, "HOLD_CHANGED"):
                self.resume()
        self.assertEqual(self.kill_path.read_bytes(), b"")

    def test_in_place_marker_change_is_preserved(self):
        self._make_independent_empty_marker()
        changed = False
        def reply(argv):
            nonlocal changed
            raw = self.command(argv)
            if not changed and argv[-1] == "orders.list":
                changed = True
                self.kill_path.chmod(0o640); self.kill_path.chmod(0o440)
            return raw
        with patch.object(latch, "_command", side_effect=reply):
            with self.assertRaisesRegex(HoldError, "HOLD_CHANGED"):
                self.resume()
        self.assertTrue(self.kill_path.exists())

    def test_directory_lock_and_new_maintenance_hold_changes_refuse(self):
        self._make_independent_empty_marker()
        for change in ("control_mode", "lock", "maintenance"):
            changed = False
            def reply(argv):
                nonlocal changed
                raw = self.command(argv)
                if not changed and argv[-1] == "orders.list":
                    changed = True
                    if change == "control_mode":
                        self.control.chmod(0o700)
                    elif change == "lock":
                        lock = self.state / latch.LOCK
                        lock.unlink(); lock.touch(mode=0o600)
                    else:
                        self.hold_path.write_text("{}"); self.hold_path.chmod(0o600)
                return raw
            with self.subTest(change=change), patch.object(latch, "_command", side_effect=reply):
                with self.assertRaises(HoldError):
                    self.resume()
            self.assertTrue(self.kill_path.exists())
            self.control.chmod(0o750)
            self.hold_path.unlink(missing_ok=True)

    def test_unlink_failure_reengages_and_does_not_claim_success(self):
        self._make_independent_empty_marker()
        with patch.object(latch, "_command", side_effect=self.command):
            with patch.object(latch.os, "unlink", side_effect=OSError("unlink failed")):
                with self.assertRaises(latch.ResumeFailure) as failed:
                    self.resume()
        self.assertEqual(failed.exception.status, "NATIVE_RESUME_FAILED_REENGAGED")
        self.assertEqual(self.kill_path.read_bytes(), b"")

    def test_fsync_failure_after_unlink_reengages_create_only(self):
        self._make_independent_empty_marker()
        original_fsync = os.fsync
        failed = False
        def flush(fd):
            nonlocal failed
            if not self.kill_path.exists() and not failed:
                failed = True
                raise OSError("flush failed after unlink")
            return original_fsync(fd)
        with patch.object(latch, "_command", side_effect=self.command):
            with patch.object(latch.os, "fsync", side_effect=flush):
                with self.assertRaises(latch.ResumeFailure) as result:
                    self.resume()
        self.assertTrue(failed)
        self.assertEqual(result.exception.status, "NATIVE_RESUME_FAILED_REENGAGED")
        self.assertEqual(self.kill_path.read_bytes(), b"")

    def test_startup_missing_control_and_corrupt_maintenance_record(self):
        self.control.rmdir()
        self.hold_path.parent.mkdir(mode=0o700)
        self.hold_path.write_text("corrupt record"); self.hold_path.chmod(0o600)
        result = self.run_action("native-startup-hold")
        self.assertTrue(result["created_marker"])
        self.assertEqual(self.kill_path.read_bytes(), b"")
        self.assertEqual(stat.S_IMODE(self.control.stat().st_mode), 0o750)
        self.assertEqual(self.hold_path.read_text(), "corrupt record")

    def test_startup_preserves_any_valid_payload_and_inode(self):
        for payload in (b"", b"engaged\n", b"unknown native hold", b"x" * 5000):
            with self.subTest(length=len(payload)):
                self.kill_path.unlink(missing_ok=True)
                self.kill_path.write_bytes(payload); self.kill_path.chmod(0o440)
                before = latch._identity(self.kill_path.stat())
                result = self.run_action("native-startup-hold")
                self.assertFalse(result["created_marker"])
                self.assertEqual(latch._identity(self.kill_path.stat()), before)
                self.assertEqual(self.kill_path.read_bytes(), payload)

    def test_startup_after_resume_uses_real_shared_process_lock(self):
        self._make_independent_empty_marker()
        entered, finish = threading.Event(), threading.Event()
        def reply(argv):
            if argv[-1] == "system.get_health" and not entered.is_set():
                entered.set()
                if not finish.wait(4):
                    raise RuntimeError("test did not release health read")
            return self.command(argv)
        script = ("from pathlib import Path; import hepta_maintenance_latch as l; "
                  "print('ready',flush=True); "
                  "print(l.perform('native-startup-hold',Path(" + repr(str(self.control))
                  + "),Path(" + repr(str(self.state)) + ")))")
        with patch.object(latch, "_command", side_effect=reply), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.resume)
            self.assertTrue(entered.wait(2))
            process = subprocess.Popen([sys.executable, "-B", "-c", script], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "ready")
                self.assertIsNone(process.poll())
                self.assertTrue(self.kill_path.exists())
                finish.set()
                self.assertEqual(future.result(timeout=5)["native_hold_resume"]["state"],
                                 "RELEASED_NATIVE_GUARD_VERIFIED")
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, err)
                self.assertIn("'created_marker': True", out)
            finally:
                finish.set()
                if process.poll() is None:
                    process.kill(); process.communicate()
        self.assertEqual(self.kill_path.read_bytes(), b"")

    def test_startup_first_does_not_allow_resume_before_native_ready(self):
        self.run_action("native-startup-hold")
        def reply(argv):
            raw = self.command(argv)
            if argv[-1] == "system.get_health":
                item = json.loads(raw); item["payload"]["remote_execution_ready"] = False
                return json.dumps(item)
            return raw
        with patch.object(latch, "_command", side_effect=reply):
            with self.assertRaises(HoldError):
                self.resume()
        self.assertTrue(self.kill_path.exists())


if __name__=="__main__":
    unittest.main()
