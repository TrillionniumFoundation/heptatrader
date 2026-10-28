"""Private-file/fake-transport tests; no real native endpoint or owner config."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_inactive_owner as inactive
import hepta_maintenance_latch as latch


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class InactiveOwnerTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.home = self.root / "home"; self.home.mkdir()
        self.stack.enter_context(patch.object(inactive.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))))
        self.proc = self.root / "proc"; self.proc.mkdir()
        self.stack.enter_context(patch.object(inactive, "PROC_ROOT", self.proc))
        self.control = self.root / "run/heptatrader/control"; self.control.mkdir(parents=True)
        self.state_path = self.home / ".local/state/heptatrader/crypto-driver/state.json"
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(json.dumps({"position": 0.0, "pending": None, "lease_uncertain": False,
            "lease_generation": 101, "gross_pnl": 2.5, "crypto_roundtrip_proof": {"original": "retained"}}))
        self.state_path.chmod(0o600)
        self.original_state = self.state_path.read_bytes()
        self.token = self.home / ".config/heptatrader/crypto.token"; self.token.parent.mkdir(parents=True)
        self.token.write_bytes(b"old-original-bearer-kept-private\n"); self.token.chmod(0o600)
        self.original_token = self.token.read_bytes()
        self.unit_path = self.home / "crypto.service"; self.unit_path.write_text("[Unit]\nRefuseManualStop=yes\n")
        self.unit_path.chmod(0o600)
        self.policy = {name: "" for name in inactive.POLICY_FIELDS}
        self.policy.update(LoadState="loaded", ActiveState="inactive", SubState="dead", UnitFileState="disabled",
            MainPID="0", RefuseManualStop="yes", ExecStart="original driver --manifest original", FragmentPath=str(self.unit_path))
        self.images = {}
        for label in ("gateway", "execution"):
            path = self.home / (label + "-image"); path.write_bytes((label + " image\n").encode()); path.chmod(0o500)
            self.images[label] = path
        self.services = {inactive.GATEWAY_UNIT: {"MainPID": "100", "InvocationID": "original-gateway"},
                         inactive.EXECUTION_UNIT: {"MainPID": "200", "InvocationID": "original-execution"}}
        self.ge = {"HEPTA_IB_PAPER_USER_SCOPE": "1", "HEPTA_EXECUTION_REMOTE_MODE": "PAPER",
            "HEPTA_TOOL_AGENT_ID": "configured-agent", "HEPTA_TOOL_CRYPTO_SESSION_ID": "configured-crypto",
            "HEPTA_TOOL_ACCOUNT": "configured-account", "HEPTA_EXECUTION_DOMAIN_ID": "PAPER",
            "HEPTA_TOOL_SUPERVISOR_UID": str(os.geteuid()), "HEPTA_TOOL_AGENT_UID": str(os.geteuid()),
            "HEPTA_EXECUTION_SERVICE_UID": str(os.geteuid()), "HEPTA_TOOL_SESSION_TEMPLATES": "watch,paper",
            "HEPTA_TOOL_CONTRACT_BINDINGS": "BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD",
            "SECRET_NOT_FOR_THE_CLI": "private-secret"}
        self.ee = {"HEPTA_IB_PAPER_USER_SCOPE": "1", "HEPTA_IB_EXECUTION_MODE": "PAPER",
            "HEPTA_IB_PAPER_CRYPTO_AGENT_ID": "configured-agent", "HEPTA_IB_PAPER_CRYPTO_SESSION_ID": "configured-crypto",
            "HEPTA_IB_PAPER_ACCOUNT": "configured-account"}
        self.write_process("100", "gateway", self.ge)
        self.write_process("200", "execution", self.ee)
        receipt = self.home / "build.json"; receipt.write_text('{"schema":"fixture-build-evidence"}'); receipt.chmod(0o400)
        self.clients = {"files": {"hepta_native_reader.py": sha(ROOT / "scripts/hepta_native_reader.py")},
            "native_clients": {"hepta-sessionctl": {"path": "/fixture/verified-sessionctl", "sha256": "c" * 64}},
            "inactive_owner_runtime": {"execution_binary": {"path": str(self.images["execution"]), "sha256": sha(self.images["execution"])},
                "build_receipt": {"path": str(receipt), "sha256": sha(receipt)}, "source_commit": "a" * 40,
                "source_leaf_sha256": {"fixture": "b" * 64}}}
        self.scope = {"agent_id": "configured-agent", "session_id": "configured-crypto", "account": "configured-account",
                      "execution_domain": "PAPER", "instrument": "BTC.USD"}
        self.calls = []; self.fault = None
        self.original_run_native = inactive.run_native
        self.stack.enter_context(patch.object(inactive, "run_native", side_effect=self.native))

    def write_process(self, pid, label, environment, ticks="5000"):
        directory = self.proc / pid; directory.mkdir(exist_ok=True)
        (directory / "stat").write_text(pid + " (fixture) " + " ".join(["S"] + ["0"] * 18 + [ticks]))
        (directory / "exe").unlink(missing_ok=True); (directory / "exe").symlink_to(self.images[label])
        (directory / "environ").write_bytes(b"\0".join(k.encode() + b"=" + v.encode() for k, v in environment.items()) + b"\0")

    def command(self, argv):
        self.assertEqual(argv[:4], ["systemctl", "--user", "show", inactive.CRYPTO_UNIT])
        return "\n".join(key + "=" + self.policy[key] for key in inactive.POLICY_FIELDS)

    def native(self, argv, environment):
        self.calls.append((argv, environment))
        phase = argv[argv.index("--phase") + 1]
        health = {"owner_fenced": True, "mutation_blocked": False, "native_new_risk_guard_blocked": phase == "held"}
        value = {"schema": "hepta_inactive_owner_audit_v1", "accepted": True,
            "state": "INACTIVE_FENCED_ZERO_NO_LEASE", "phase": phase, "configured_owner": dict(self.scope),
            "preserves_owner_fence": True, "control_mutations": 0, "lease_mutations": 0, "orders_sent": 0,
            "audit_performed": phase == "held", "audit": {} if phase == "held" else None,
            "catalog": {"owner_records": 0, "before_sha256": "d" * 64, "after_sha256": "d" * 64},
            "execution": {"pid": 200, "start_ticks": "5000", "service_epoch": "current-native", "service_fencing_generation": 7},
            "reads": {"health_before": copy.deepcopy(health), "health_after": copy.deepcopy(health)}}
        if self.fault == "catalog": value["catalog"]["owner_records"] = 1
        elif self.fault == "catalog_changed": value["catalog"]["after_sha256"] = "f" * 64
        elif self.fault == "scope": value["configured_owner"]["session_id"] = "another-owner"
        elif self.fault == "fence": value["reads"]["health_after"]["owner_fenced"] = False
        elif self.fault == "state": self.state_path.write_text('{"position":0,"pending":null,"lease_uncertain":false,"lease_generation":2}')
        elif self.fault == "token": self.token.write_bytes(b"different bearer\n")
        elif self.fault == "process": self.write_process("200", "execution", self.ee, ticks="6000")
        elif self.fault == "unit": self.policy["UnitFileState"] = "enabled"
        elif self.fault == "epoch": value["execution"]["service_epoch"] = "another-native"
        elif self.fault == "guard": value["reads"]["health_after"]["native_new_risk_guard_blocked"] = True
        elif self.fault == "timeout": raise inactive.InactiveOwnerError("INACTIVE_OWNER_NATIVE_TRANSPORT_TimeoutExpired")
        return value

    def prove(self, phase="held", previous=None):
        return inactive.prove(self.control, self.services, self.clients, phase, self.command, previous=previous)

    def test_two_current_proofs_preserve_original_state_token_and_owner_scope(self):
        held = self.prove(); released = self.prove("released", held)
        self.assertEqual(released["binding"], held["binding"])
        self.assertEqual(self.state_path.read_bytes(), self.original_state)
        self.assertEqual(self.token.read_bytes(), self.original_token)
        self.assertEqual(len(self.calls), 2)
        for argv, environment in self.calls:
            self.assertNotIn("SECRET_NOT_FOR_THE_CLI", environment)
            self.assertEqual(environment["HEPTA_TOOL_CRYPTO_SESSION_ID"], "configured-crypto")
            self.assertIn("inactive-owner-audit", argv)
            self.assertNotIn("--token-file", argv)
        self.assertNotIn("private-secret", json.dumps(held))

    def test_post_phase_cannot_exist_without_this_calls_held_proof(self):
        with self.assertRaisesRegex(inactive.InactiveOwnerError, "PHASE_INVALID"): self.prove("released")
        self.assertEqual(self.calls, [])

    def test_maintenance_upgrade_audit_cannot_be_used_as_a_native_release_proof(self):
        with self.assertRaisesRegex(inactive.InactiveOwnerError, "PHASE_INVALID"):
            self.prove("upgrade-held")
        original = self.native
        def maintenance(argv, environment):
            proof = original(argv, environment)
            proof.update(schema="hepta_inactive_owner_upgrade_audit_v1",
                         state="INACTIVE_FENCED_OWNER_UPGRADE_AUDIT", phase="upgrade-held")
            return proof
        with patch.object(inactive, "run_native", side_effect=maintenance),\
             self.assertRaisesRegex(inactive.InactiveOwnerError, "NATIVE_PROOF_INVALID"):
            self.prove()
        self.assertEqual(self.state_path.read_bytes(), self.original_state)
        self.assertEqual(self.token.read_bytes(), self.original_token)

    def test_absent_or_changed_catalog_and_wrong_scope_never_form_a_proof(self):
        for fault in ("catalog", "catalog_changed", "scope", "fence", "timeout"):
            self.fault = fault
            with self.subTest(fault=fault), self.assertRaises(inactive.InactiveOwnerError): self.prove()
        self.assertEqual(self.state_path.read_bytes(), self.original_state)
        self.assertEqual(self.token.read_bytes(), self.original_token)

    def test_changed_state_token_process_or_policy_cannot_pass_current_proof(self):
        for fault in ("state", "token", "process", "unit"):
            self.fault = fault
            with self.subTest(fault=fault), self.assertRaises(inactive.InactiveOwnerError): self.prove()
            self.state_path.write_bytes(self.original_state); self.token.write_bytes(self.original_token)
            self.write_process("200", "execution", self.ee); self.policy["UnitFileState"] = "disabled"

    def test_runtime_receipt_and_running_image_must_match_reviewed_binding(self):
        for key in ("execution_binary", "build_receipt"):
            old = self.clients["inactive_owner_runtime"][key]["sha256"]
            self.clients["inactive_owner_runtime"][key]["sha256"] = "0" * 64
            with self.subTest(key=key), self.assertRaises((inactive.InactiveOwnerError, ValueError)): self.prove()
            self.clients["inactive_owner_runtime"][key]["sha256"] = old
        self.assertEqual(self.calls, [])

    def test_gateway_and_execution_config_mismatch_never_reaches_native_transport(self):
        self.ee["HEPTA_IB_PAPER_CRYPTO_SESSION_ID"] = "other-session"
        self.write_process("200", "execution", self.ee)
        with self.assertRaisesRegex(inactive.InactiveOwnerError, "SCOPE_MISMATCH"): self.prove()
        self.assertEqual(self.calls, [])

    def test_effective_paper_domain_is_forwarded_when_gateway_uses_its_default(self):
        self.ge.pop("HEPTA_EXECUTION_DOMAIN_ID")
        self.write_process("100", "gateway", self.ge)
        self.prove()
        self.assertEqual(self.calls[0][1]["HEPTA_EXECUTION_DOMAIN_ID"], "PAPER")

    def test_native_fixed_field_reason_is_preserved_without_reflecting_arbitrary_values(self):
        for code, expected in (("INACTIVE_OWNER_FIELD_INVALID:owner_fenced", "INACTIVE_OWNER_FIELD_INVALID:owner_fenced"),
                               ("INACTIVE_OWNER_FIELD_INVALID:private-secret", "UNCONFIRMED")):
            completed = SimpleNamespace(returncode=4, stdout=json.dumps({"reason_code": code}).encode())
            # The fixture normally replaces transport; exercise its original
            # implementation here without executing a command or endpoint.
            with patch.object(inactive.subprocess, "run", return_value=completed),\
                 self.assertRaisesRegex(inactive.InactiveOwnerError, "PROOF_FAILED:" + expected + "$"):
                self.original_run_native(["/fixture/native-client"], {})

    def test_duplicate_and_invalid_utf8_process_environment_refuse_before_native(self):
        path = self.proc / "100/environ"; original = path.read_bytes()
        for suffix, reason in ((b"HEPTA_TOOL_AGENT_ID=another\0", "ENVIRONMENT_INVALID"),
                               (b"INVALID=\xff\0", "ENVIRONMENT_ENCODING")):
            path.write_bytes(original + suffix)
            with self.subTest(reason=reason), self.assertRaisesRegex(inactive.InactiveOwnerError, reason): self.prove()
        self.assertEqual(self.calls, [])

    def test_disabled_unit_definition_change_after_precheck_is_detected(self):
        held = self.prove(); self.unit_path.write_text("[Unit]\nRefuseManualStop=no\n")
        with self.assertRaisesRegex(inactive.InactiveOwnerError, "BINDING_CHANGED_AFTER_RELEASE"): self.prove("released", held)
        self.assertEqual(len(self.calls), 1)

    def test_unit_definition_replaced_with_fifo_rejects_without_waiting_for_writer(self):
        original_open = os.open
        opening = threading.Event(); finished = threading.Event()
        errors = []; descriptors = []
        def swap_before_open(path, flags, *args, **kwargs):
            if Path(path) == self.unit_path:
                self.unit_path.unlink(); os.mkfifo(self.unit_path, 0o600)
                opening.set()
            fd = original_open(path, flags, *args, **kwargs)
            if Path(path) == self.unit_path: descriptors.append(fd)
            return fd
        def read_definition():
            try: inactive.definitions(self.policy)
            except Exception as error: errors.append(error)
            finally: finished.set()
        reader = threading.Thread(target=read_definition, daemon=True)
        with patch.object(inactive.os, "open", side_effect=swap_before_open):
            reader.start()
            try:
                self.assertTrue(opening.wait(1), "the real unit-file open was not reached")
                without_writer = finished.wait(1)
            finally:
                # Bound the old-code failure too, without signals or a stuck reader.
                if not finished.is_set():
                    writer = original_open(self.unit_path, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                    os.close(writer)
                reader.join(1)
        self.assertFalse(reader.is_alive())
        self.assertTrue(without_writer, "a replacement FIFO blocked before the identity check")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], inactive.InactiveOwnerError)
        self.assertEqual(str(errors[0]), "INACTIVE_OWNER_FILE_CHANGED")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError): os.fstat(descriptors[0])

    def test_replacement_regular_file_still_fails_original_descriptor_identity_check(self):
        replacement = self.home / "replacement.service"
        replacement.write_bytes(self.unit_path.read_bytes()); replacement.chmod(0o600)
        original_open = os.open; descriptors = []
        def replace_before_open(path, flags, *args, **kwargs):
            if Path(path) == self.unit_path: os.replace(replacement, self.unit_path)
            fd = original_open(path, flags, *args, **kwargs)
            if Path(path) == self.unit_path: descriptors.append(fd)
            return fd
        with patch.object(inactive.os, "open", side_effect=replace_before_open),\
             self.assertRaisesRegex(inactive.InactiveOwnerError, "INACTIVE_OWNER_FILE_CHANGED"):
            inactive.definitions(self.policy)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError): os.fstat(descriptors[0])

    def test_epoch_and_guard_postconditions_are_not_satisfied_by_precheck(self):
        held = self.prove()
        for fault in ("epoch", "guard"):
            self.fault = fault
            with self.subTest(fault=fault), self.assertRaises(inactive.InactiveOwnerError): self.prove("released", held)

    def test_running_driver_uses_active_policy_but_abnormal_inactive_state_refuses(self):
        self.policy.update(ActiveState="active", MainPID="123", SubState="running")
        self.assertIsNone(inactive.crypto_policy(self.command))
        self.policy.update(ActiveState="inactive", MainPID="0", SubState="dead", RefuseManualStop="no")
        with self.assertRaisesRegex(inactive.InactiveOwnerError, "NOT_FROZEN_DISABLED"): inactive.crypto_policy(self.command)


class LatchPhaseIntegrationTests(unittest.TestCase):
    def proof(self, phase):
        return {"state": "INACTIVE_FENCED_ZERO_NO_LEASE", "binding": {"original": True},
            "native": {"phase": phase, "execution": {"service_epoch": "native", "service_fencing_generation": 8}}}

    def payload(self, control, owner, method):
        self.assertEqual(owner, "paper", "the absent crypto bearer must not be queried")
        if method == "orders.list": return {"source": "IB", "authoritative": True, "global_active_orders_complete": True,
            "owner_projection_complete": True, "active_order_ids": [], "owned_active_order_ids": []}
        if method == "portfolio.list_positions": return {"source": "IB", "authoritative": True, "positions": []}
        return {"fake_active_health": True}

    def test_latch_uses_inactive_proof_and_preserves_the_active_owner_path(self):
        services = {"gateway": "same", "execution": "same"}
        with patch.object(latch, "_service_snapshot", return_value=services),\
             patch.object(latch, "_health_identity", return_value=("native", 8)),\
             patch.object(latch, "_native_payload", side_effect=self.payload),\
             patch.object(latch, "_inactive_crypto_proof", return_value=self.proof("held")) as proof:
            result = latch._resume_observation(Path("/fixture/control"))
        self.assertEqual(result["owners"]["crypto"]["state"], "INACTIVE_FENCED_ZERO_NO_LEASE")
        self.assertIn("active_orders", result["owners"]["paper"])
        proof.assert_called_once()

    def test_crypto_proof_must_share_active_owner_native_identity(self):
        with patch.object(latch, "_service_snapshot", return_value={}),\
             patch.object(latch, "_health_identity", return_value=("restarted-native", 8)),\
             patch.object(latch, "_native_payload", side_effect=self.payload),\
             patch.object(latch, "_inactive_crypto_proof", return_value=self.proof("held")):
            with self.assertRaisesRegex(latch.HoldError, "INSTANCE_CHANGED"): latch._resume_observation(Path("/fixture/control"))

    def test_postcheck_reuses_current_proof_while_allowing_active_fx_dispatch(self):
        held = self.proof("held")
        observation = {"execution_service_epoch": "native", "execution_service_fencing_generation": 8,
                       "services": {}, "owners": {"paper": {}, "crypto": held}}
        active = {"execution_mode": "PAPER", "gateway_ready": True, "remote_execution_ready": True,
            "execution_service_epoch": "native", "execution_service_fencing_generation": 8,
            "execution_authoritative_health": {"source": "IB", "authoritative": True,
                "native_new_risk_guard_blocked": False, "mutation_ready": False,
                "mutation_reason": "RISK_MUTATION_DISPATCH_IN_FLIGHT"}}
        with patch.object(latch, "_native_payload", return_value=active) as payload,\
             patch.object(latch, "_service_snapshot", return_value={}),\
             patch.object(latch, "_inactive_crypto_proof", return_value=self.proof("released")) as proof:
            value = latch._verify_native_release(Path("/fixture/control"), observation)
        self.assertEqual(value["paper"]["mutation_reason"], "RISK_MUTATION_DISPATCH_IN_FLIGHT")
        payload.assert_called_once_with(Path("/fixture/control"), "paper", "system.get_health")
        proof.assert_called_once_with(Path("/fixture/control"), {}, previous=held)


if __name__ == "__main__": unittest.main()
