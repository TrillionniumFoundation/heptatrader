#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/hepta_p1_paper_terminal_witness_verifier.py"
SPEC = importlib.util.spec_from_file_location(
    "hepta_p1_paper_terminal_witness_verifier_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import terminal witness verifier")
VERIFIER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFIER
SPEC.loader.exec_module(VERIFIER)


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class FixedObserver:
    def __init__(self, observation, ready=lambda: True):
        self.observation = observation
        self.ready = ready
        self.calls = 0

    def observe(self):
        if not self.ready():
            raise AssertionError("boundary observed before DENY_ALL custodian")
        self.calls += 1
        return self.observation


class TerminalWitnessVerifierTests(unittest.TestCase):
    def test_witness_commit_is_non_authorizing_and_does_not_ack_or_release(
            self) -> None:
        implementation = inspect.getsource(VERIFIER.run_verifier)
        self.assertNotIn("_ack_arguments", implementation)
        self.assertNotIn("checkpoint_and_release_host_owner", implementation)
        self.assertIn("TERMINAL_WITNESS_COMMITTED", implementation)

    def test_cutoff_one_shot_is_recovery_only_and_prepare_precedes_stop(
            self) -> None:
        unit = (
            ROOT / "systemd/hepta-p1-paper-terminal-cutoff@.service"
        ).read_text(encoding="utf-8", errors="strict")
        self.assertIn(" --record-cutoff --request ", unit)
        self.assertIn("PrivateNetwork=yes\n", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX\n", unit)
        self.assertIn("CapabilityBoundingSet=\n", unit)
        self.assertIn("RuntimeDirectoryPreserve=yes\n", unit)
        self.assertIn(
            "LoadCredential=hepta-p1-paper-terminal-cutoff-request:", unit)
        self.assertNotIn("Conflicts=hepta-execution-ib-paper", unit)
        self.assertNotIn("Requires=hepta-broker-egress-policy.service", unit)
        self.assertNotIn("\n[Install]\n", unit)
        fail_close = (
            ROOT / "systemd/hepta-local-paper-fail-close@.service"
        ).read_text(encoding="utf-8", errors="strict")
        self.assertIn(" guardian-fail-close --domain %i", fail_close)
        self.assertIn("PrivateNetwork=yes\n", fail_close)
        self.assertIn("RestrictAddressFamilies=AF_UNIX\n", fail_close)
        self.assertIn("CapabilityBoundingSet=\n", fail_close)
        self.assertNotIn("\n[Install]\n", fail_close)
        cmake = (ROOT / "HeptaTrade/CMakeLists.txt").read_text(
            encoding="utf-8", errors="strict")
        self.assertEqual(
            cmake.count("hepta-p1-paper-terminal-cutoff@.service"), 2)
        self.assertEqual(
            cmake.count("hepta-local-paper-fail-close@.service"), 2)

    def test_record_cutoff_prepares_hpt1_and_replays_exact_owner(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = root / "authority"
            authority.mkdir(mode=0o700)
            authority.chmod(0o700)
            lease = authority / "lease.lock"
            lease.write_bytes(b"")
            lease.chmod(0o600)
            runtime = root / "runtime"
            runtime.mkdir(mode=0o700)
            runtime.chmod(0o700)
            state = root / "state"
            state.mkdir(mode=0o700)
            state.chmod(0o700)
            hpt1_path = state / "ib-paper-terminal-halt.v1"
            token = root / "revoke-token"
            token.write_bytes(b"root-revoke-bearer\n")
            token.chmod(0o400)
            cutoff_path = runtime / "transport-cutoff-receipt.v1.json"
            account = "DU123"
            domain = "PAPER:alpha"
            owner = (
                digest(b"owner-a") + "\t7\t" + account.encode().hex() +
                "\t" + domain.encode().hex() + "\n").encode("ascii")
            hpt1_values = {
                "state": "TERMINALIZING", "finalization_id": "final-a",
                "preliminary_finalization_receipt_sha256": digest(b"prelim"),
                "owner_agent_id": "agent-a", "owner_session_id": "session-a",
                "owner_account": account, "owner_execution_domain": domain,
                "recovery_ingress_fence": "7",
                "terminalization_service_epoch": "epoch-a",
                "terminalization_service_fencing_generation": "11",
                "terminalization_generation": "1",
            }
            hpt1 = (
                "HPT1\n" + "".join(
                    f"{key}={hpt1_values[key]}\n" for key in
                    VERIFIER.HPT1_KEYS)).encode("ascii")
            request_body = {
                "schema": VERIFIER.CUTOFF_REQUEST_SCHEMA, "version": 1,
                "status": "REQUESTED",
                "expected_source_baseline_sha256": digest(b"source"),
                "expected_campaign_id": "campaign-a",
                "expected_cycle_id": "cycle-a",
                "expected_recovery_id": "recovery-a",
                "expected_finalization_id": "final-a",
                "preliminary_finalization_receipt_sha256": digest(b"prelim"),
                "owner_set_sha256": digest(owner), "owner_count": 1,
                "owner_set_canonical_hex": owner.hex(),
                "account_id_sha256": digest(account.encode("ascii")),
                "service_pid": 1234, "service_start_ticks": 5678,
                "broker_socket_identity_sha256": digest(b"socket"),
                "mutation_fence_generation": 7, "token_file": str(token),
                "token_generation": 7,
                "known_mutation_command_set_sha256": digest(b"commands"),
                "known_mutation_command_count": 1,
                "known_correlation_set_sha256": digest(b"correlations"),
                "known_correlation_count": 1,
            }
            request, request_payload = VERIFIER.sealed_document(request_body)
            request_path = root / "cutoff-request.json"
            request_path.write_bytes(request_payload)
            request_path.chmod(0o400)
            observation = VERIFIER.BoundaryObservation(
                boot_id="11111111-2222-4333-8444-555555555555",
                generation=23, state_sha256=digest(b"deny-all-state"),
                receipt_file_sha256=digest(b"boundary-file"),
                receipt_body_sha256=digest(b"boundary-body"),
                publisher_pid=4321, publisher_start_ticks=8765,
                authorized_connector_count=0, broker_socket_count=0,
                broker_process_count=0, broker_credential_count=0,
                execution_service_inactive=True, paper_units_inactive=True,
                kill_switches_engaged=True)
            publisher_pid = 1111
            deny_all_inputs = False
            observer = FixedObserver(
                observation,
                ready=lambda: (
                    publisher_pid == observation.publisher_pid and
                    deny_all_inputs))
            prepare_calls = 0
            runtime_owner = b'{"schema":"hepta.ib-paper-runtime-owner.v1"}\n'
            (authority / "owner.v1").write_bytes(runtime_owner)
            (authority / "owner.v1").chmod(0o600)
            stop_lock_acquired = 0
            deny_all_custodian_started = 0

            def command(arguments, _timeout):
                nonlocal prepare_calls, stop_lock_acquired
                nonlocal deny_all_custodian_started
                nonlocal publisher_pid, deny_all_inputs
                if "paper-terminal-witness-prepare" in arguments:
                    prepare_calls += 1
                    if prepare_calls == 1:
                        self.assertFalse(hpt1_path.exists())
                        hpt1_path.write_bytes(hpt1)
                        hpt1_path.chmod(0o600)
                        accepted = True
                        reason = "PAPER_TERMINAL_WITNESS_PREPARED"
                        returncode = 0
                    else:
                        accepted = False
                        reason = "PAPER_TERMINAL_WITNESS_PREPARE_INTENT_PENDING"
                        returncode = 4
                    response = {
                        "accepted": accepted, "reason_code": reason,
                        "lease_generation": 7,
                        "paper_finalization_state": "AUDIT_SEALED",
                        "paper_finalization_required": True,
                        "recovery_id": "recovery-a",
                        "finalization_id": "final-a",
                        "expected_owner_set_sha256": digest(owner),
                        "expected_owner_count": 1,
                        "finalization_receipt_sha256": digest(b"prelim"),
                    }
                    return subprocess.CompletedProcess(
                        arguments, returncode,
                        json.dumps(response, separators=(",", ":")).encode(),
                        b"")
                self.assertEqual(arguments[0], "/usr/bin/systemctl")
                self.assertTrue(hpt1_path.exists())
                if arguments == (
                        "/usr/bin/systemctl", "stop",
                        VERIFIER.LOCAL_PAPER_AUTHORITY_UNIT):
                    return subprocess.CompletedProcess(
                        arguments, 0, b"", b"")
                if arguments == (
                        "/usr/bin/systemctl", "start",
                        VERIFIER.LOCAL_PAPER_FAIL_CLOSE_UNIT):
                    deny_all_custodian_started += 1
                    # The old ACTIVE supervisor has exited on DENY drift.
                    # The reviewed custodian normalizes its inputs and starts
                    # a different live --supervise-deny-all publisher.
                    publisher_pid = observation.publisher_pid
                    deny_all_inputs = True
                    return subprocess.CompletedProcess(
                        arguments, 0, b"", b"")
                self.assertEqual(arguments[:2], ("/usr/bin/systemctl", "stop"))
                # Model the real preflight stop path: it must acquire the
                # shared host-authority lease, restore DENY_ALL, clear its
                # runtime owner, and only then exit.  This acquisition would
                # fail if record_transport_cutoff held the lease around stop.
                lock_fd = os.open(lease, os.O_RDONLY | os.O_CLOEXEC)
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    stop_lock_acquired += 1
                    owner_path = authority / "owner.v1"
                    if (owner_path.exists() and
                            owner_path.read_bytes() == runtime_owner):
                        owner_path.unlink()
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                return subprocess.CompletedProcess(arguments, 0, b"", b"")

            patches = (
                mock.patch.object(VERIFIER, "ROOT_UID", os.geteuid()),
                mock.patch.object(VERIFIER, "ROOT_GID", os.getegid()),
                mock.patch.object(VERIFIER, "EXEC_UID", os.geteuid()),
                mock.patch.object(VERIFIER, "EXEC_GID", os.getegid()),
                mock.patch.object(VERIFIER, "HOST_AUTHORITY_DIRECTORY", authority),
                mock.patch.object(VERIFIER, "HOST_AUTHORITY_LEASE_PATH", lease),
                mock.patch.object(
                    VERIFIER, "HOST_AUTHORITY_OWNER_PATH", authority / "owner.v1"),
                mock.patch.object(VERIFIER, "HPT1_PATH", hpt1_path),
                mock.patch.object(VERIFIER, "CUTOFF_OUTPUT", cutoff_path),
                mock.patch.object(VERIFIER, "PAPER_UNITS", ("paper.service",)),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                    patches[5], patches[6], patches[7], patches[8], patches[9]:
                validated = VERIFIER.validate_cutoff_request(request_path)
                first = VERIFIER.record_transport_cutoff(
                    validated, command=command, observer=observer)
                owner_first = (authority / "owner.v1").read_bytes()
                cutoff_first = cutoff_path.read_bytes()
                second = VERIFIER.record_transport_cutoff(
                    validated, command=command, observer=observer)
            self.assertEqual(first["status"], "TRANSPORT_CUTOFF_DURABLE")
            self.assertFalse(first["_terminal_replay"])
            self.assertTrue(second["_terminal_replay"])
            self.assertEqual((authority / "owner.v1").read_bytes(), owner_first)
            self.assertEqual(cutoff_path.read_bytes(), cutoff_first)
            self.assertEqual(prepare_calls, 2)
            self.assertEqual(stop_lock_acquired, 2)
            self.assertEqual(deny_all_custodian_started, 2)
            self.assertGreaterEqual(observer.calls, 6)

    def test_cutoff_request_rejects_generation_or_token_mode_drift(self) -> None:
        self.assertIn("token_file", VERIFIER.CUTOFF_REQUEST_FIELDS)
        self.assertIn("token_generation", VERIFIER.CUTOFF_REQUEST_FIELDS)

    def test_terminal_ack_checkpoint_survives_owner_unlink_crash_and_replays(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = root / "authority"
            authority.mkdir(mode=0o700)
            authority.chmod(0o700)
            lease = authority / "lease.lock"
            lease.write_bytes(b"")
            lease.chmod(0o600)
            runtime = root / "runtime"
            runtime.mkdir(mode=0o700)
            runtime.chmod(0o700)
            state = root / "state"
            state.mkdir(mode=0o700)
            state.chmod(0o700)
            owner_path = authority / "owner.v1"
            hpt1_path = state / "ib-paper-terminal-halt.v1"
            hpw1_path = state / "ib-paper-terminal-external-halt.v1"
            capsule_path = runtime / "commit-capsule.v1"
            hpe_path = runtime / "terminal-evidence.v1"
            boot_id = "11111111-2222-4333-8444-555555555555"
            request = {
                "expected_recovery_id": "recovery-a",
                "expected_finalization_id": "final-a",
                "expected_campaign_id": "campaign-a",
                "expected_cycle_id": "cycle-a",
                "token_file": str(root / "token"),
                "token_generation": 7,
            }
            owner_document, owner = VERIFIER.sealed_document({
                "schema": VERIFIER.CHALLENGE_SCHEMA, "version": 1,
                "status": "CHALLENGE_ISSUED",
                "finalization_id": "final-a",
            })
            self.assertEqual(owner_document["finalization_id"], "final-a")
            observation = VERIFIER.BoundaryObservation(
                boot_id=boot_id, generation=23,
                state_sha256=digest(b"deny-all-state"),
                receipt_file_sha256=digest(b"boundary-file"),
                receipt_body_sha256=digest(b"boundary-body"),
                publisher_pid=4321, publisher_start_ticks=8765,
                authorized_connector_count=0, broker_socket_count=0,
                broker_process_count=0, broker_credential_count=0,
                execution_service_inactive=True, paper_units_inactive=True,
                kill_switches_engaged=True)
            stable = {key: "x" for key in VERIFIER.HPE1_KEYS[:-1]}
            stable.update({
                "recovery_id": "recovery-a", "finalization_id": "final-a",
                "campaign_id": "campaign-a", "cycle_id": "cycle-a",
                "expected_owner_set_sha256": digest(b"owner-set"),
                "expected_owner_count": "1",
                "preliminary_finalization_receipt_sha256": digest(b"prelim"),
                "transport_cutoff_receipt_file_sha256": digest(b"cutoff"),
                "post_cutoff_terminal_witness_file_sha256": digest(b"witness"),
                "host_boot_id": boot_id,
                "egress_publisher_pid": "4321",
                "egress_publisher_start_ticks": "8765",
                "egress_policy_generation": "23",
                "egress_policy_sha256": observation.state_sha256,
            })
            hpe = VERIFIER.build_lines(
                "HPE1", VERIFIER.HPE1_KEYS, stable,
                "evidence_body_sha256")
            hpt1 = b"HPT1\nterminalizing=yes\n"
            hpw1 = b"HPW1\nexternal_halt=yes\n"
            capsule = b"HPC1\ncapsule=yes\n"
            terminal_receipt = "terminal-ack-v3"
            ack_response = {
                "accepted": True,
                "finalization_receipt": terminal_receipt,
                "finalization_receipt_sha256":
                    digest(terminal_receipt.encode("ascii")),
                "paper_finalization_state": "ACKED",
                "reason_code": "PAPER_FINALIZATION_TERMINAL_ACKED",
                "preliminary_finalization_receipt_sha256": digest(b"prelim"),
                "terminal_proof_kind": VERIFIER.PROOF_KIND,
                "terminal_latch_sha256": digest(hpt1),
                "terminal_external_halt_latch_sha256": digest(hpw1),
                "transport_cutoff_receipt_file_sha256": digest(b"cutoff"),
                "post_cutoff_terminal_witness_file_sha256": digest(b"witness"),
                "terminal_evidence_file_sha256": digest(hpe),
                "terminal_evidence_body_sha256":
                    VERIFIER.parse_lines(
                        hpe, "HPE1", VERIFIER.HPE1_KEYS, "TEST")[0][
                            "evidence_body_sha256"],
                "terminal_external_latch_loaded": True,
                "terminal_current_evidence_verified": True,
                "terminal_replay": True,
            }

            def command(arguments, _timeout):
                self.assertIn("paper-terminal-witness-ack", arguments)
                return subprocess.CompletedProcess(
                    arguments, 0,
                    json.dumps(ack_response, separators=(",", ":")).encode(),
                    b"")

            patches = (
                mock.patch.object(VERIFIER, "ROOT_UID", os.geteuid()),
                mock.patch.object(VERIFIER, "ROOT_GID", os.getegid()),
                mock.patch.object(VERIFIER, "EXEC_UID", os.geteuid()),
                mock.patch.object(VERIFIER, "EXEC_GID", os.getegid()),
                mock.patch.object(VERIFIER, "HOST_AUTHORITY_DIRECTORY", authority),
                mock.patch.object(VERIFIER, "HOST_AUTHORITY_LEASE_PATH", lease),
                mock.patch.object(VERIFIER, "HOST_AUTHORITY_OWNER_PATH", owner_path),
                mock.patch.object(VERIFIER, "HPT1_PATH", hpt1_path),
                mock.patch.object(VERIFIER, "HPW1_PATH", hpw1_path),
                mock.patch.object(VERIFIER, "CAPSULE_OUTPUT", capsule_path),
                mock.patch.object(VERIFIER, "EVIDENCE_OUTPUT", hpe_path),
                mock.patch.object(VERIFIER, "read_boot_id", return_value=boot_id),
                mock.patch.object(
                    VERIFIER, "validate_hpt1",
                    return_value={"finalization_id": "final-a"}),
                mock.patch.object(VERIFIER, "validate_hpw1", return_value={}),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                    patches[5], patches[6], patches[7], patches[8], patches[9], \
                    patches[10], patches[11], patches[12], patches[13]:
                # Owner absence alone has no terminal meaning.
                self.assertIsNone(VERIFIER.replay_released_host_owner(
                    request, command=command,
                    observer=FixedObserver(observation)))
                owner_path.write_bytes(owner)
                owner_path.chmod(0o600)
                hpt1_path.write_bytes(hpt1)
                hpt1_path.chmod(0o600)
                hpw1_path.write_bytes(hpw1)
                hpw1_path.chmod(0o600)
                capsule_path.write_bytes(capsule)
                capsule_path.chmod(0o400)
                hpe_path.write_bytes(hpe)
                hpe_path.chmod(0o400)
                directory_fd = os.open(
                    authority, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                original_unlink = os.unlink

                def crash_before_owner_unlink(path, *args, **kwargs):
                    if path == owner_path.name:
                        raise OSError("simulated crash seam")
                    return original_unlink(path, *args, **kwargs)

                try:
                    with mock.patch.object(
                            VERIFIER.os, "unlink",
                            side_effect=crash_before_owner_unlink):
                        with self.assertRaisesRegex(
                                VERIFIER.VerifierError,
                                "TERMINAL_WITNESS_OWNER_REMOVE_FAILED"):
                            VERIFIER.checkpoint_and_release_host_owner(
                                directory=directory_fd, owner_payload=owner,
                                request=request, stable=stable,
                                hpt1_sha256=digest(hpt1),
                                hpw1_sha256=digest(hpw1), hpe1=hpe,
                                ack=ack_response, boundary=observation)
                    self.assertTrue(owner_path.exists())
                    completion_path, pointer_path = \
                        VERIFIER.terminal_completion_paths("final-a")
                    self.assertTrue(completion_path.exists())
                    self.assertTrue(pointer_path.exists())
                    VERIFIER.checkpoint_and_release_host_owner(
                        directory=directory_fd, owner_payload=owner,
                        request=request, stable=stable,
                        hpt1_sha256=digest(hpt1),
                        hpw1_sha256=digest(hpw1), hpe1=hpe,
                        ack=ack_response, boundary=observation)
                finally:
                    os.close(directory_fd)
                self.assertFalse(owner_path.exists())
                replay = VERIFIER.replay_released_host_owner(
                    request, command=command,
                    observer=FixedObserver(observation))
                self.assertTrue(replay["terminal_replay"])
                self.assertEqual(
                    replay["finalization_receipt"], terminal_receipt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
