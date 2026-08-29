#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "hepta_p1_load_probe_validator.py"
SPEC = importlib.util.spec_from_file_location("p1_load_probe_validator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
CAMPAIGN_ID = "p1-load-probe-test"
NOW_MS = 2_000_000


def seal(body):
    return {
        **body,
        "body_sha256": validator.digest_bytes(
            validator.canonical_bytes(body)),
    }


def seal_record(body):
    return {
        **body,
        "record_sha256": validator.digest_bytes(
            validator.canonical_bytes(body)),
    }


def write(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(validator.canonical_bytes(document))


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifact = root / "observer"
        self.host_path = root / "host.json"
        self.controller_path = root / "controller.json"
        self.state_path = self.artifact / "observer-state.json"
        self.prospective_policy_path = root / "prospective-policy.json"
        self.authority_marker_path = root / "prospective-authority.json"
        self.environment = {
            "boot_id": "00000000-0000-0000-0000-000000000000",
            "audit_journal_device": 1,
            "audit_journal_inode": 2,
            "collector_sha256": DIGEST_A,
            "exporter_sha256": DIGEST_B,
            "heptactl_sha256": DIGEST_B,
            "gateway_sha256": DIGEST_C,
            "custodian_sha256": DIGEST_C,
            "observer_sha256": DIGEST_A,
            "host_controller_sha256": DIGEST_B,
            "domain_config_sha256": DIGEST_C,
            "gateway_profile_sha256": DIGEST_A,
            "gateway_process_profile_sha256": DIGEST_B,
            "gateway_invocation_id": "a" * 32,
            "gateway_main_pid": 123,
            "gateway_exec_main_start_timestamp_monotonic_us": 456,
            "gateway_socket_device": 7,
            "gateway_socket_inode": 8,
        }
        self.records: list[dict] = []
        self.record_paths: list[Path] = []
        self._build()

    def _build(self) -> None:
        history = (
            self.artifact / "segments" / "segment-000001" / "history")
        previous = None
        contents: list[bytes] = []
        base_ms = 1_000_000
        for sequence in range(1, validator.REQUIRED_RUNS + 1):
            started = base_ms + (sequence - 1) * validator.CADENCE_MS
            body = {
                "schema": "hepta.shadow-market-history-record.v3",
                "version": 3,
                "sequence": sequence,
                "cadence_ms": validator.CADENCE_MS,
                "maximum_jitter_ms": validator.MAXIMUM_JITTER_MS,
                "domain_id": "alpha",
                "agent_uid": 2104,
                "instrument": "EUR.USD",
                "collection_started_at_ms": started,
                "collection_finished_at_ms": started + 100,
                "generated_at_ms": started + 100,
                "quote_read_finished_at_ms": started + 90,
                "quote_changed": True,
                "quote": {
                    "bid": 1.1,
                    "ask": 1.1002,
                    "observed_at_ms": started - 7_000,
                    "stale_after_ms": started + 53_000,
                    "source": "SIMULATOR",
                    "authoritative": True,
                    "stale": False,
                },
                "catalog_sha256": DIGEST_A,
                "descriptor_sha256": {"system.get_health": DIGEST_B},
                "execution_service_epoch": "epoch-1",
                "execution_service_fencing_generation": 1,
                "snapshot_body_sha256": (
                    "sha256:" + f"{sequence:064x}"),
                "snapshot_file_sha256": (
                    "sha256:" + f"{sequence + 1000:064x}"),
                "watch_generation": 1,
                "watch_lease_operation": "PROVISION",
                "watch_lease_previous_generation": None,
                "watch_lease_previous_receipt_body_sha256": None,
                "watch_lease_receipt_body_sha256": DIGEST_B,
                "watch_lease_receipt_file_sha256": DIGEST_C,
                "watch_lease_accepted_at_ms": base_ms - 60_000,
                "watch_lease_expires_at_ms": base_ms + 3_600_000,
                "watch_lease_ttl_seconds": 3600,
                "watch_export_receipt_body_sha256": (
                    "sha256:" + f"{sequence + 2000:064x}"),
                "watch_export_receipt_file_sha256": (
                    "sha256:" + f"{sequence + 3000:064x}"),
                "watch_exported_at_ms": started + 200,
                "watch_export_reader_uid": 1000,
                "watch_export_reader_gid": 1000,
                "previous_record_sha256": previous,
            }
            record = seal_record(body)
            path = history / f"record-{sequence:020d}.json"
            write(path, record)
            record_contents = validator.canonical_bytes(record)
            self.records.append(record)
            self.record_paths.append(path)
            contents.append(record_contents)
            previous = record["record_sha256"]
        first = self.records[0]
        last = self.records[-1]
        head = seal({
            "schema": "hepta.shadow-market-history-head.v1",
            "version": 1,
            "record_schema": "hepta.shadow-market-history-record.v3",
            "record_count": validator.REQUIRED_RUNS,
            "first_record_sha256": first["record_sha256"],
            "last_record_sha256": last["record_sha256"],
            "last_record_name": self.record_paths[-1].name,
            "last_record_file_sha256":
                validator.digest_bytes(contents[-1]),
            "last_previous_record_sha256":
                last["previous_record_sha256"],
            "last_snapshot_body_sha256": last["snapshot_body_sha256"],
            "last_snapshot_file_sha256": last["snapshot_file_sha256"],
            "cadence_ms": validator.CADENCE_MS,
            "maximum_jitter_ms": validator.MAXIMUM_JITTER_MS,
            "history_record_bytes": sum(map(len, contents)),
            "audit_cursor_sequence": validator.REQUIRED_RUNS,
            "audit_expected_previous_sha256": last["record_sha256"],
        })
        write(history / "history-head.json", head)

        state = seal({
            "schema": "hepta.bounded-shadow-observer-state.v1",
            "version": 1,
            "campaign_id": CAMPAIGN_ID,
            "campaign_sha256": DIGEST_A,
            "policy_sha256": DIGEST_B,
            "policy_body_sha256": DIGEST_C,
            "strategy_id": "strategy",
            "strategy_version": "2.0.0",
            "strategy_sha256": DIGEST_A,
            "status": "RUNNING",
            "collection_cadence_ms": validator.CADENCE_MS,
            "maximum_collection_jitter_ms": validator.MAXIMUM_JITTER_MS,
            "valid_after_ms": 9_000_000,
            "expires_at_ms": 10_000_000,
            "slot_interval_ms": 120_000,
            "maximum_iterations": 241,
            "maximum_lateness_ms": 60_000,
            "segment_index": 1,
            "segment_status": "OPEN",
            "segment_record_count": validator.REQUIRED_RUNS,
            "segment_history_head_sha256": head["body_sha256"],
            "last_collection_started_at_ms":
                last["collection_started_at_ms"],
            "last_generated_at_ms": last["generated_at_ms"],
            "last_snapshot_body_sha256": last["snapshot_body_sha256"],
            "last_watch_generation": 1,
            "last_lease_receipt_body_sha256":
                last["watch_lease_receipt_body_sha256"],
            "last_lease_receipt_file_sha256":
                last["watch_lease_receipt_file_sha256"],
            "completed_iterations": 0,
            "last_receipt_sha256": None,
            "missed_sample_count": 0,
            "missed_decision_count": 0,
            "sample_count": validator.REQUIRED_RUNS,
            "accounted_payload_bytes": sum(map(len, contents)),
            "accounted_payload_files": validator.REQUIRED_RUNS,
            "accounted_payload_accumulator": DIGEST_A,
            "last_storage_audit_sample_count": 64,
            "last_storage_audit_accumulator": DIGEST_B,
            "final_audit_receipt_sha256": None,
            "final_audit_segment_count": 0,
            "audit_events": [{
                "sequence": 1,
                "event": "OBSERVATION_STARTED",
                "at_ms": 9_000_000,
                "reason": None,
                "detail": {"segment_index": 1},
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            }],
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        })
        write(self.state_path, state)

        controller = seal({
            "schema": "hepta.p1-shadow-observer-controller-status.v1",
            "version": 1,
            "campaign_id": CAMPAIGN_ID,
            "controller_pid": 123,
            "controller_uid": 1000,
            "controller_gid": 1000,
            "state": "RUNNING",
            "started_at_ms": base_ms,
            "updated_at_ms": base_ms + 900_000,
            "observer_invocations": validator.REQUIRED_RUNS,
            "last_export_receipt_body_sha256": (
                last["watch_export_receipt_body_sha256"]
            ),
            "last_snapshot_body_sha256": last["snapshot_body_sha256"],
            "last_lease_generation": 1,
            "locked_execution_service_epoch": "epoch-1",
            "locked_execution_service_fencing_generation": 1,
            "observer_status": "RUNNING",
            "observer_outcome": "WARMUP",
            "completed_iterations": 0,
            "reason": None,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        })
        write(self.controller_path, controller)

        close = seal({
            "schema": "hepta.shadow-watch-custodian-closure.v1",
            "version": 1,
            "campaign_id": CAMPAIGN_ID,
            "lease_generation": 1,
            "closed_at_ms": NOW_MS - 1,
            "authoritative_revoke_outcome": "ACCEPTED",
            "local_authority_removed": True,
            "export_evidence_removed": True,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })
        host = seal({
            "schema": "hepta.p1-shadow-load-probe-host-receipt.v1",
            "version": 1,
            "status": "LOAD_PROBE_COMPLETE",
            "campaign_id": CAMPAIGN_ID,
            "lease_generation": 1,
            "collector_runs": validator.REQUIRED_RUNS,
            "required_collector_runs": validator.REQUIRED_RUNS,
            "collection_cadence_ms": validator.CADENCE_MS,
            "maximum_start_jitter_ms": validator.MAXIMUM_JITTER_MS,
            "probe_duration_ms": 900_100,
            "maximum_start_lateness_ms": 20,
            "maximum_collector_elapsed_ms": 100,
            "environment": self.environment,
            "close_result": close,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })
        write(self.host_path, host)
        self.host_path.chmod(0o600)

    def validate(self):
        return validator.validate(
            campaign_id=CAMPAIGN_ID,
            prospective_campaign_id="p1-formal-test",
            prospective_policy_path=self.prospective_policy_path,
            authority_marker_path=self.authority_marker_path,
            host_receipt_path=self.host_path,
            controller_status_path=self.controller_path,
            observer_state_path=self.state_path,
            artifact_root=self.artifact,
            environment=self.environment,
            now_ms=NOW_MS,
            _expected_root_uid=self.host_path.stat().st_uid,
            _expected_root_gid=self.host_path.stat().st_gid,
        )


class LoadProbeValidatorTests(unittest.TestCase):
    def test_exact_91_sample_probe_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            receipt = fixture.validate()
        self.assertEqual(receipt["status"], "GO")
        self.assertEqual(receipt["sample_count"], 91)
        self.assertEqual(receipt["missed_sample_count"], 0)
        self.assertEqual(receipt["probe_execution_service_epoch"], "epoch-1")
        self.assertEqual(
            receipt["probe_execution_service_fencing_generation"], 1)
        self.assertEqual(
            receipt["probe_first_record_sha256"],
            fixture.records[0]["record_sha256"])
        self.assertEqual(
            receipt["probe_last_record_sha256"],
            fixture.records[-1]["record_sha256"])
        body = {
            key: value for key, value in receipt.items()
            if key != "body_sha256"
        }
        self.assertEqual(
            receipt["body_sha256"],
            validator.digest_bytes(validator.canonical_bytes(body)),
        )

    def test_legacy_v2_history_cannot_enter_active_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            legacy_body = dict(fixture.records[0])
            legacy_body.pop("record_sha256")
            legacy_body.pop("quote_changed")
            legacy_body["schema"] = "hepta.shadow-market-history-record.v2"
            legacy_body["version"] = 2
            write(fixture.record_paths[0], seal_record(legacy_body))
            with self.assertRaisesRegex(
                    validator.ValidationError,
                    "P1_LOAD_PROBE_RECORD_FIELDS_INVALID"):
                fixture.validate()

    def test_nonzero_missed_sample_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            state = json.loads(fixture.state_path.read_text())
            state.pop("body_sha256")
            state["missed_sample_count"] = 1
            write(fixture.state_path, seal(state))
            with self.assertRaisesRegex(
                    validator.ValidationError,
                    "P1_LOAD_PROBE_OBSERVER_BINDING_INVALID"):
                fixture.validate()

    def test_absolute_cadence_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            record = json.loads(fixture.record_paths[1].read_text())
            record.pop("record_sha256")
            record["collection_started_at_ms"] += 2_000
            record["collection_finished_at_ms"] += 2_000
            record["generated_at_ms"] += 2_000
            record["watch_exported_at_ms"] += 2_000
            write(fixture.record_paths[1], seal_record(record))
            with self.assertRaisesRegex(
                    validator.ValidationError,
                    "P1_LOAD_PROBE_CADENCE_INVALID"):
                fixture.validate()

    def test_environment_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            drifted = {**fixture.environment, "audit_journal_inode": 999}
            with self.assertRaisesRegex(
                    validator.ValidationError,
                    "P1_LOAD_PROBE_HOST_BINDING_INVALID"):
                validator.validate(
                    campaign_id=CAMPAIGN_ID,
                    prospective_campaign_id="p1-formal-test",
                    prospective_policy_path=fixture.prospective_policy_path,
                    authority_marker_path=fixture.authority_marker_path,
                    host_receipt_path=fixture.host_path,
                    controller_status_path=fixture.controller_path,
                    observer_state_path=fixture.state_path,
                    artifact_root=fixture.artifact,
                    environment=drifted,
                    now_ms=NOW_MS,
                    _expected_root_uid=fixture.host_path.stat().st_uid,
                    _expected_root_gid=fixture.host_path.stat().st_gid,
                )

    def test_existing_formal_policy_fails_before_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.prospective_policy_path.write_text(
                "{}\n", encoding="ascii")
            with self.assertRaisesRegex(
                    validator.ValidationError,
                    "P1_LOAD_PROBE_INPUT_INVALID"):
                fixture.validate()

    def test_incomplete_closure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            host = json.loads(fixture.host_path.read_text())
            host.pop("body_sha256")
            close = dict(host["close_result"])
            close.pop("body_sha256")
            close["export_evidence_removed"] = False
            host["close_result"] = seal(close)
            write(fixture.host_path, seal(host))
            with self.assertRaisesRegex(
                    validator.ValidationError,
                    "P1_LOAD_PROBE_CLOSE_INVALID"):
                fixture.validate()

    def test_stale_closure_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            host = json.loads(fixture.host_path.read_text())
            host.pop("body_sha256")
            close = host["close_result"]
            close.pop("body_sha256")
            close["closed_at_ms"] = NOW_MS - validator.MAXIMUM_RECEIPT_AGE_MS - 1
            host["close_result"] = seal(close)
            write(fixture.host_path, seal(host))
            fixture.host_path.chmod(0o600)
            with self.assertRaisesRegex(
                    validator.ValidationError,
                    "P1_LOAD_PROBE_CLOSE_INVALID"):
                fixture.validate()

    def test_history_cursor_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            head_path = (
                fixture.artifact / "segments" / "segment-000001" /
                "history" / "history-head.json")
            head = json.loads(head_path.read_text())
            head.pop("body_sha256")
            head["audit_cursor_sequence"] = validator.REQUIRED_RUNS - 1
            write(head_path, seal(head))
            with self.assertRaisesRegex(
                    validator.ValidationError,
                    "P1_LOAD_PROBE_HISTORY_BINDING_INVALID"):
                fixture.validate()

    def test_host_receipt_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            link = fixture.root / "host-link.json"
            link.symlink_to(fixture.host_path)
            with self.assertRaisesRegex(
                    validator.ValidationError,
                    "P1_LOAD_PROBE_HOST_FILE_INVALID"):
                validator.validate(
                    campaign_id=CAMPAIGN_ID,
                    prospective_campaign_id="p1-formal-test",
                    prospective_policy_path=fixture.prospective_policy_path,
                    authority_marker_path=fixture.authority_marker_path,
                    host_receipt_path=link,
                    controller_status_path=fixture.controller_path,
                    observer_state_path=fixture.state_path,
                    artifact_root=fixture.artifact,
                    environment=fixture.environment,
                    now_ms=NOW_MS,
                    _expected_root_uid=fixture.host_path.stat().st_uid,
                    _expected_root_gid=fixture.host_path.stat().st_gid,
                )


if __name__ == "__main__":
    unittest.main()
