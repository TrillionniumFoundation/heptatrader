#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import hepta_bounded_shadow_closure_verifier as closure  # noqa: E402
import hepta_bounded_shadow_observer as observer  # noqa: E402
import hepta_bounded_shadow_observer_tests as fixtures  # noqa: E402
import hepta_market_evidence_normalizer as normalizer  # noqa: E402
import hepta_shadow_market_history as history  # noqa: E402
from hepta_strategy_contracts import (  # noqa: E402
    canonical_bytes,
    digest_document,
)


BASE_MS = fixtures.BASE_MS
VERIFIED_AT_MS = BASE_MS + 3_001_000
ROTATED_VALID_AFTER_MS = BASE_MS + 3_300_000
ROTATED_VERIFIED_AT_MS = ROTATED_VALID_AFTER_MS + 1_000
SEVENTY_TWO_HOURS_MS = 72 * 60 * 60 * 1_000


def write(path: Path, value: Any, *, mode: int = 0o600) -> None:
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(canonical_bytes(value))
    path.chmod(mode)


class BoundedShadowClosureVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="hepta-bounded-shadow-closure-")
        cls.root = Path(cls.temporary.name)
        cls.fixture_root = cls.root / "complete-fixture"
        cls.fixture_root.mkdir(mode=0o700)
        cls.original_root_uid = history.ROOT_TRUST_UID
        cls.original_evidence_roots = normalizer.TRUSTED_EVIDENCE_ROOTS
        cls.original_attestation_uid = normalizer.TRUSTED_ATTESTATION_UID
        cls.original_extractors = dict(normalizer.PINNED_EXTRACTORS)
        history.ROOT_TRUST_UID = os.geteuid()
        normalizer.TRUSTED_EVIDENCE_ROOTS = (
            cls.fixture_root.resolve(),)
        normalizer.TRUSTED_ATTESTATION_UID = os.geteuid()
        extractor_digest = (
            "sha256:" + hashlib.sha256(
                b"observer deterministic extractor fixture").hexdigest()
        )
        normalizer.PINNED_EXTRACTORS = {
            ("HEPTA_TEST_OBSERVER_EXTRACTOR", "1.0.0"):
                extractor_digest,
        }

        cls.artifact_root = cls.fixture_root / "artifacts"
        cls.strategy_path = cls.fixture_root / "strategy.json"
        strategy, strategy_sha256 = fixtures.compact_strategy(
            cls.strategy_path)
        cls.policy_path = cls.fixture_root / "policy.json"
        valid_after_ms = BASE_MS + 3_000_000
        fixtures.policy(
            cls.policy_path,
            strategy_config=strategy,
            strategy_sha256=strategy_sha256,
            valid_after_ms=valid_after_ms,
            maximum_iterations=1,
        )
        snapshot_path = cls.fixture_root / "snapshot.json"
        lease_path = cls.fixture_root / "lease.json"
        export_path = cls.fixture_root / "export.json"
        bundle_path = cls.fixture_root / "bundle.json"
        lease_value = fixtures.lease_receipt(
            accepted_at_ms=BASE_MS - 1_000,
            generation=1,
            operation="PROVISION",
        )
        fixtures.write(lease_path, lease_value, mode=0o400)
        fixtures.write(
            bundle_path,
            fixtures.source_bundle(
                cls.fixture_root, valid_after_ms + 350),
        )
        for index in range(301):
            started_at_ms = BASE_MS + index * 10_000
            snapshot_value = fixtures.snapshot(
                started_at_ms, price_index=index)
            fixtures.write(snapshot_path, snapshot_value, mode=0o400)
            fixtures.write(
                export_path,
                fixtures.export_receipt(snapshot_value, lease_value),
                mode=0o400,
            )
            result = observer.observe_once(
                campaign_id=fixtures.CAMPAIGN_ID,
                policy_path=cls.policy_path,
                strategy_path=cls.strategy_path,
                snapshot_path=snapshot_path,
                watch_lease_receipt_path=lease_path,
                watch_export_receipt_path=export_path,
                source_bundle_path=bundle_path,
                artifact_root=cls.artifact_root,
                observed_now_ms=started_at_ms + 450,
            )
            expected = "COMPLETE" if index == 300 else "WARMUP"
            if result["outcome"] != expected:
                raise AssertionError(
                    f"fixture observer outcome={result['outcome']} "
                    f"index={index}")

        cls.rotated_fixture_root = cls.root / "rotated-complete-fixture"
        cls.rotated_fixture_root.mkdir(mode=0o700)
        normalizer.TRUSTED_EVIDENCE_ROOTS = (
            cls.rotated_fixture_root.resolve(),)
        rotated_artifacts = cls.rotated_fixture_root / "artifacts"
        rotated_strategy_path = (
            cls.rotated_fixture_root / "strategy.json")
        rotated_strategy, rotated_strategy_sha256 = (
            fixtures.compact_strategy(rotated_strategy_path))
        rotated_policy_path = cls.rotated_fixture_root / "policy.json"
        fixtures.policy(
            rotated_policy_path,
            strategy_config=rotated_strategy,
            strategy_sha256=rotated_strategy_sha256,
            valid_after_ms=ROTATED_VALID_AFTER_MS,
            maximum_iterations=1,
        )
        rotated_snapshot_path = cls.rotated_fixture_root / "snapshot.json"
        rotated_lease_path = cls.rotated_fixture_root / "lease.json"
        rotated_export_path = cls.rotated_fixture_root / "export.json"
        rotated_bundle_path = cls.rotated_fixture_root / "bundle.json"
        first_lease = fixtures.lease_receipt(
            accepted_at_ms=BASE_MS - 1_000,
            generation=1,
            operation="PROVISION",
        )
        fixtures.write(rotated_lease_path, first_lease, mode=0o400)
        fixtures.write(
            rotated_bundle_path,
            fixtures.source_bundle(
                cls.rotated_fixture_root,
                ROTATED_VALID_AFTER_MS + 350,
            ),
        )

        def observe_rotated(
            started_at_ms: int,
            snapshot_value: dict[str, Any],
            lease_path: Path,
            lease_value: dict[str, Any],
        ) -> dict[str, Any]:
            fixtures.write(
                rotated_snapshot_path, snapshot_value, mode=0o400)
            fixtures.write(
                rotated_export_path,
                fixtures.export_receipt(snapshot_value, lease_value),
                mode=0o400,
            )
            return observer.observe_once(
                campaign_id=fixtures.CAMPAIGN_ID,
                policy_path=rotated_policy_path,
                strategy_path=rotated_strategy_path,
                snapshot_path=rotated_snapshot_path,
                watch_lease_receipt_path=lease_path,
                watch_export_receipt_path=rotated_export_path,
                source_bundle_path=rotated_bundle_path,
                artifact_root=rotated_artifacts,
                observed_now_ms=started_at_ms + 450,
            )

        first_result = observe_rotated(
            BASE_MS,
            fixtures.snapshot(BASE_MS),
            rotated_lease_path,
            first_lease,
        )
        if first_result["outcome"] != "WARMUP":
            raise AssertionError(
                "rotated fixture initial sample was not WARMUP")
        second_lease_path = cls.rotated_fixture_root / "lease-rotate.json"
        second_lease = fixtures.lease_receipt(
            accepted_at_ms=BASE_MS + 5_000,
            generation=2,
            operation="ROTATE",
            previous_body_sha256=first_lease["body_sha256"],
        )
        fixtures.write(second_lease_path, second_lease, mode=0o400)
        rotated_result = observe_rotated(
            BASE_MS + 10_000,
            fixtures.snapshot(BASE_MS + 10_000, price_index=1),
            second_lease_path,
            second_lease,
        )
        if rotated_result["outcome"] != "WARMUP":
            raise AssertionError(
                "in-segment lease rotation was not accepted")
        for index in range(2, 331):
            started_at_ms = BASE_MS + index * 10_000
            result = observe_rotated(
                started_at_ms,
                fixtures.snapshot(started_at_ms, price_index=index),
                second_lease_path,
                second_lease,
            )
            expected = "COMPLETE" if index == 330 else "WARMUP"
            if result["outcome"] != expected:
                rotated_state = json.loads(
                    (rotated_artifacts / "observer-state.json").read_text(
                        encoding="ascii"))
                raise AssertionError(
                    f"rotated fixture observer outcome={result['outcome']} "
                    f"index={index} "
                    f"last_event={rotated_state['audit_events'][-1]}")

    @classmethod
    def tearDownClass(cls) -> None:
        history.ROOT_TRUST_UID = cls.original_root_uid
        normalizer.TRUSTED_EVIDENCE_ROOTS = cls.original_evidence_roots
        normalizer.TRUSTED_ATTESTATION_UID = (
            cls.original_attestation_uid)
        normalizer.PINNED_EXTRACTORS = cls.original_extractors
        cls.temporary.cleanup()

    def case(
        self,
        label: str,
        *,
        fixture_root: Path | None = None,
    ) -> tuple[Path, Path, Path, Path]:
        source_root = (
            self.fixture_root if fixture_root is None else fixture_root)
        case_root = self.root / f"{self._testMethodName}-{label}"
        shutil.copytree(source_root, case_root)
        normalizer.TRUSTED_EVIDENCE_ROOTS = (case_root.resolve(),)
        return (
            case_root,
            case_root / "artifacts",
            case_root / "policy.json",
            case_root / "strategy.json",
        )

    def verify(
        self,
        label: str,
        *,
        mutate: Any = None,
        fixture_root: Path | None = None,
        verified_at_ms: int = VERIFIED_AT_MS,
    ) -> dict[str, Any]:
        case_root, artifacts, policy_path, strategy_path = self.case(
            label, fixture_root=fixture_root)
        if mutate is not None:
            mutate(case_root, artifacts)
        return closure.verify_closure(
            artifact_root=artifacts,
            policy_path=policy_path,
            strategy_path=strategy_path,
            output_path=case_root / "closure.json",
            verified_at_ms=verified_at_ms,
        )

    def test_complete_campaign_emits_deterministic_sealed_closure(self) -> None:
        receipt = self.verify("valid")
        self.assertEqual(
            receipt["schema"],
            "hepta.bounded-shadow-campaign-closure.v1",
        )
        self.assertEqual(receipt["closure_status"],
                         "VERIFIED_WITH_RETAINED_EVIDENCE_RESIDUALS")
        self.assertEqual(receipt["completed_iterations"], 1)
        self.assertEqual(receipt["iteration_count"], 1)
        self.assertEqual(receipt["segment_count"], 1)
        self.assertFalse(receipt["complete_revalidation"])
        self.assertEqual(
            receipt["residual_evidence"],
            [
                "EPHEMERAL_BAR_HISTORY_NOT_RETAINED",
                "EPHEMERAL_QUOTE_HISTORY_NOT_RETAINED",
                "EPHEMERAL_SAMPLED_BARS_NOT_RETAINED",
                "ROOT_WATCH_EXPORT_RECEIPT_METADATA_NOT_REPLAYABLE",
                "ROOT_WATCH_LEASE_RECEIPT_METADATA_NOT_REPLAYABLE",
                "ROOT_WATCH_SNAPSHOT_METADATA_NOT_REPLAYABLE",
            ],
        )
        body = dict(receipt)
        claimed = body.pop("body_sha256")
        self.assertEqual(claimed, digest_document(body))
        for field in closure.ZERO_AUTHORITY_FIELDS:
            self.assertFalse(receipt[field])
        iteration = receipt["iterations"][0]
        self.assertTrue(
            iteration["source_attestation"]["raw_payloads_verified"])
        self.assertEqual(iteration["source_first_sequence"], 1)
        self.assertEqual(iteration["source_last_sequence"], 301)

        second = self.verify("valid-repeat")
        self.assertEqual(second, receipt)

    def test_iteration_set_and_schedule_fail_closed(self) -> None:
        iteration_directory = (
            Path("segments") / "segment-000001" / "iterations" /
            "iteration-000001"
        )

        def missing_iteration(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            shutil.rmtree(artifacts / iteration_directory)

        def nonconsecutive_iteration(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            (artifacts / iteration_directory).rename(
                artifacts / iteration_directory.parent /
                "iteration-000002")

        def mutate_manifest(
            artifacts: Path,
            mutation: Any,
        ) -> None:
            path = (
                artifacts / iteration_directory /
                "source-window-manifest.json")
            document = json.loads(path.read_text(encoding="ascii"))
            mutation(document)
            body = dict(document)
            body.pop("body_sha256")
            document["body_sha256"] = digest_document(body)
            write(path, document)

        def wrong_schedule(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            mutate_manifest(
                artifacts,
                lambda document: document.__setitem__(
                    "scheduled_at_ms",
                    document["scheduled_at_ms"] + 1,
                ),
            )

        def early_evaluation(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            mutate_manifest(
                artifacts,
                lambda document: document.__setitem__(
                    "evaluated_at_ms",
                    document["scheduled_at_ms"] - 1,
                ),
            )

        def late_evaluation(
            case_root: Path,
            artifacts: Path,
        ) -> None:
            policy = json.loads(
                (case_root / "policy.json").read_text(encoding="ascii"))
            mutate_manifest(
                artifacts,
                lambda document: document.__setitem__(
                    "evaluated_at_ms",
                    document["scheduled_at_ms"] +
                    policy["maximum_lateness_ms"] + 1,
                ),
            )

        for label, mutation, reason in (
                (
                    "missing-iteration",
                    missing_iteration,
                    "CLOSURE_ITERATION_SET_INVALID",
                ),
                (
                    "nonconsecutive-iteration",
                    nonconsecutive_iteration,
                    "CLOSURE_ITERATION_SET_INVALID",
                ),
                (
                    "wrong-schedule",
                    wrong_schedule,
                    "CLOSURE_MANIFEST_BINDING_INVALID",
                ),
                (
                    "early-evaluation",
                    early_evaluation,
                    "CLOSURE_MANIFEST_BINDING_INVALID",
                ),
                (
                    "late-evaluation",
                    late_evaluation,
                    "CLOSURE_MANIFEST_BINDING_INVALID",
                )):
            with self.subTest(label=label):
                with self.assertRaisesRegex(closure.ClosureError, reason):
                    self.verify(label, mutate=mutation)

    def test_schedule_endpoints_cover_exactly_seventy_two_hours(self) -> None:
        policy = {
            "valid_after_ms": BASE_MS,
            "slot_interval_ms": 15 * 60 * 1_000,
            "maximum_iterations": 289,
        }
        first = closure._scheduled_at_ms(policy, 1)
        last = closure._scheduled_at_ms(policy, 289)
        self.assertEqual(first, BASE_MS)
        self.assertEqual(last, BASE_MS + SEVENTY_TWO_HOURS_MS)
        self.assertEqual(last - first, SEVENTY_TWO_HOURS_MS)
        for invalid in (False, 0, 290):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                        closure.ClosureError,
                        "CLOSURE_ITERATION_NUMBER_INVALID"):
                    closure._scheduled_at_ms(policy, invalid)

    def test_history_record_sequence_failures_are_rejected(self) -> None:
        history_root = (
            Path("segments") / "segment-000001" / "history")

        def missing_record(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            (
                artifacts / history_root /
                "record-00000000000000000150.json"
            ).unlink()

        def nonconsecutive_record(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            source = (
                artifacts / history_root /
                "record-00000000000000000301.json")
            source.rename(
                artifacts / history_root /
                "record-00000000000000000302.json")

        for label, mutation in (
                ("missing-history-record", missing_record),
                ("nonconsecutive-history-record", nonconsecutive_record)):
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                        closure.ClosureError,
                        "CLOSURE_HISTORY_AUDIT_INVALID"):
                    self.verify(label, mutate=mutation)

    def test_in_segment_rotation_provenance_is_revalidated(self) -> None:
        receipt = self.verify(
            "valid-rotation",
            fixture_root=self.rotated_fixture_root,
            verified_at_ms=ROTATED_VERIFIED_AT_MS,
        )
        self.assertEqual(receipt["segment_count"], 1)
        self.assertEqual(
            [segment["segment_index"] for segment in receipt["segments"]],
            [1],
        )
        self.assertEqual(receipt["iterations"][0]["segment_index"], 1)

        def break_rotation_chain(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            path = (
                artifacts / "segments" / "segment-000001" / "history" /
                "record-00000000000000000002.json")
            document = json.loads(path.read_text(encoding="ascii"))
            document["watch_lease_previous_receipt_body_sha256"] = (
                "sha256:" + "f" * 64)
            body = dict(document)
            body.pop("record_sha256")
            document["record_sha256"] = digest_document(body)
            write(path, document)

        with self.assertRaisesRegex(
                closure.ClosureError,
                "CLOSURE_HISTORY_AUDIT_INVALID") as caught:
            self.verify(
                "tampered-rotation",
                mutate=break_rotation_chain,
                fixture_root=self.rotated_fixture_root,
                verified_at_ms=ROTATED_VERIFIED_AT_MS,
            )
        self.assertIsInstance(caught.exception.__cause__, history.HistoryError)
        self.assertEqual(
            str(caught.exception.__cause__),
            "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID",
        )

    def test_continuity_break_is_terminal_and_cannot_be_certified(self) -> None:
        case_root = self.root / (
            self._testMethodName + "-terminal-break")
        case_root.mkdir(mode=0o700)
        normalizer.TRUSTED_EVIDENCE_ROOTS = (case_root.resolve(),)
        artifacts = case_root / "artifacts"
        strategy_path = case_root / "strategy.json"
        strategy, strategy_sha256 = fixtures.compact_strategy(strategy_path)
        policy_path = case_root / "policy.json"
        fixtures.policy(
            policy_path,
            strategy_config=strategy,
            strategy_sha256=strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        snapshot_path = case_root / "snapshot.json"
        lease_path = case_root / "lease.json"
        export_path = case_root / "export.json"
        bundle_path = case_root / "bundle.json"
        lease_value = fixtures.lease_receipt(
            accepted_at_ms=BASE_MS - 1_000,
            generation=1,
            operation="PROVISION",
        )
        fixtures.write(lease_path, lease_value, mode=0o400)
        fixtures.write(
            bundle_path,
            fixtures.source_bundle(case_root, BASE_MS + 600_350),
        )

        def observe(started_at_ms: int, price_index: int) -> dict[str, Any]:
            snapshot_value = fixtures.snapshot(
                started_at_ms, price_index=price_index)
            fixtures.write(snapshot_path, snapshot_value, mode=0o400)
            fixtures.write(
                export_path,
                fixtures.export_receipt(snapshot_value, lease_value),
                mode=0o400,
            )
            return observer.observe_once(
                campaign_id=fixtures.CAMPAIGN_ID,
                policy_path=policy_path,
                strategy_path=strategy_path,
                snapshot_path=snapshot_path,
                watch_lease_receipt_path=lease_path,
                watch_export_receipt_path=export_path,
                source_bundle_path=bundle_path,
                artifact_root=artifacts,
                observed_now_ms=started_at_ms + 450,
            )

        self.assertEqual(observe(BASE_MS, 0)["outcome"], "WARMUP")
        self.assertEqual(observe(BASE_MS + 20_000, 2)["outcome"], "STOPPED")
        self.assertEqual(observe(BASE_MS + 30_000, 3)["outcome"], "STOPPED")
        state = json.loads(
            (artifacts / "observer-state.json").read_text(encoding="ascii"))
        self.assertEqual(state["status"], "STOPPED")
        self.assertEqual(state["segment_index"], 1)
        self.assertEqual(state["segment_status"], "CLOSED")
        self.assertEqual(state["missed_sample_count"], 2)
        rejected = [
            event for event in state["audit_events"]
            if event["event"] == "SAMPLE_REJECTED"
        ]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(
            rejected[0]["reason"], "MARKET_HISTORY_CADENCE_GAP")
        with self.assertRaisesRegex(
                closure.ClosureError,
                "CLOSURE_OBSERVER_NOT_COMPLETE"):
            closure.verify_closure(
                artifact_root=artifacts,
                policy_path=policy_path,
                strategy_path=strategy_path,
                output_path=case_root / "closure.json",
                verified_at_ms=BASE_MS + 601_000,
            )

    def test_packet_permission_schema_is_recursive_exact_and_zero(self) -> None:
        packet_path = (
            self.artifact_root / "segments" / "segment-000001" /
            "iterations" / "iteration-000001" /
            "information-packet.json"
        )
        packet = json.loads(packet_path.read_text(encoding="ascii"))
        closure._validate_packet_zero_authority(packet)
        self.assertEqual(
            set(packet["authority"]),
            closure.PACKET_AUTHORITY_FIELDS,
        )

        def paper_enabled(document: dict[str, Any]) -> None:
            document["authority"]["paper_authorized"] = True

        def mutation_authority_injected(document: dict[str, Any]) -> None:
            document["authority"]["mutation_authorized"] = False

        def nested_mutation_attempt(document: dict[str, Any]) -> None:
            document["history"]["mutation_attempted"] = False

        def unknown_permission(document: dict[str, Any]) -> None:
            document["features"]["order_permission"] = False

        def unexpected_authority_surface(document: dict[str, Any]) -> None:
            document["market"]["order_authority"] = False

        def unknown_authorization(document: dict[str, Any]) -> None:
            document["authority"]["orders_authorized"] = False

        def missing_required_denial(document: dict[str, Any]) -> None:
            del document["source_snapshot"]["direct_broker_access"]

        for label, mutation, reason in (
                (
                    "paper-enabled",
                    paper_enabled,
                    "CLOSURE_PACKET_AUTHORITY_INVALID",
                ),
                (
                    "mutation-authority-injected",
                    mutation_authority_injected,
                    "CLOSURE_PACKET_AUTHORITY_FIELDS_INVALID",
                ),
                (
                    "nested-mutation-attempt",
                    nested_mutation_attempt,
                    "CLOSURE_PACKET_PERMISSION_SCHEMA_INVALID",
                ),
                (
                    "unknown-permission",
                    unknown_permission,
                    "CLOSURE_PACKET_PERMISSION_SCHEMA_INVALID",
                ),
                (
                    "unexpected-authority-surface",
                    unexpected_authority_surface,
                    "CLOSURE_PACKET_PERMISSION_SCHEMA_INVALID",
                ),
                (
                    "unknown-authorization",
                    unknown_authorization,
                    "CLOSURE_PACKET_AUTHORITY_FIELDS_INVALID",
                ),
                (
                    "missing-required-denial",
                    missing_required_denial,
                    "CLOSURE_PACKET_SNAPSHOT_FIELDS_INVALID",
                )):
            with self.subTest(label=label):
                candidate = copy.deepcopy(packet)
                mutation(candidate)
                with self.assertRaisesRegex(closure.ClosureError, reason):
                    closure._validate_packet_zero_authority(candidate)

    def test_only_first_segment_may_provision_and_rotations_are_exact(self) -> None:
        def sealed_record(
            *,
            generation: int,
            operation: str,
            receipt: str,
            previous_generation: int | None,
            previous_receipt: str | None,
        ) -> dict[str, Any]:
            body = {
                "watch_generation": generation,
                "watch_lease_operation": operation,
                "watch_lease_receipt_body_sha256": receipt,
                "watch_lease_previous_generation": previous_generation,
                "watch_lease_previous_receipt_body_sha256":
                    previous_receipt,
            }
            return {**body, "record_sha256": digest_document(body)}

        generation_one = "sha256:" + "1" * 64
        generation_two = "sha256:" + "2" * 64
        first = sealed_record(
            generation=1,
            operation="PROVISION",
            receipt=generation_one,
            previous_generation=None,
            previous_receipt=None,
        )
        rotated = sealed_record(
            generation=2,
            operation="ROTATE",
            receipt=generation_two,
            previous_generation=1,
            previous_receipt=generation_one,
        )
        closure._validate_segment_authority_transition(
            segment_index=1,
            records=[first],
            previous_records=None,
        )
        closure._validate_segment_authority_transition(
            segment_index=2,
            records=[rotated],
            previous_records=[first],
        )

        reprovisioned = sealed_record(
            generation=2,
            operation="PROVISION",
            receipt=generation_two,
            previous_generation=None,
            previous_receipt=None,
        )
        resealed_forgery = sealed_record(
            generation=2,
            operation="ROTATE",
            receipt=generation_two,
            previous_generation=1,
            previous_receipt="sha256:" + "f" * 64,
        )
        repeated_generation = sealed_record(
            generation=1,
            operation="ROTATE",
            receipt=generation_two,
            previous_generation=1,
            previous_receipt=generation_one,
        )
        first_segment_rotate = sealed_record(
            generation=1,
            operation="ROTATE",
            receipt=generation_one,
            previous_generation=0,
            previous_receipt="sha256:" + "0" * 64,
        )
        for label, segment_index, candidate, previous in (
                ("later-reprovision", 2, reprovisioned, [first]),
                ("resealed-previous-receipt", 2, resealed_forgery, [first]),
                ("repeated-generation", 2, repeated_generation, [first]),
                ("first-segment-rotate", 1, first_segment_rotate, None)):
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                        closure.ClosureError,
                        "CLOSURE_SEGMENT_AUTHORITY_CHAIN_INVALID"):
                    closure._validate_segment_authority_transition(
                        segment_index=segment_index,
                        records=[candidate],
                        previous_records=previous,
                    )

    def test_missing_tampered_extra_and_raw_payload_fail_closed(self) -> None:
        def missing_calendar(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            (
                artifacts / "segments" / "segment-000001" /
                "iterations" / "iteration-000001" / "calendar.json"
            ).unlink()

        def tampered_manifest(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            path = (
                artifacts / "segments" / "segment-000001" /
                "iterations" / "iteration-000001" /
                "source-window-manifest.json"
            )
            document = json.loads(path.read_text(encoding="ascii"))
            document["source_records_sha256"] = "sha256:" + "f" * 64
            body = dict(document)
            body.pop("body_sha256")
            document["body_sha256"] = digest_document(body)
            write(path, document)

        def extra_receipt(
            _case_root: Path,
            artifacts: Path,
        ) -> None:
            write(
                artifacts / "receipts" / "decision-000002.json",
                {"unexpected": True},
            )

        def tampered_raw_payload(
            case_root: Path,
            _artifacts: Path,
        ) -> None:
            payloads = sorted(case_root.glob("evidence-*/fed.html"))
            self.assertEqual(len(payloads), 1)
            path = payloads[0]
            path.chmod(0o600)
            path.write_bytes(b"tampered official payload")
            path.chmod(0o400)

        for label, mutation, reason in (
                (
                    "missing-calendar",
                    missing_calendar,
                    "CLOSURE_ITERATION_ARTIFACT_SET_INVALID",
                ),
                (
                    "tampered-manifest",
                    tampered_manifest,
                    "CLOSURE_SOURCE_WINDOW_BINDING_INVALID",
                ),
                (
                    "extra-receipt",
                    extra_receipt,
                    "CLOSURE_RECEIPT_SET_INVALID",
                ),
                (
                    "tampered-raw",
                    tampered_raw_payload,
                    "CLOSURE_SOURCE_ATTESTATION_INVALID",
                )):
            with self.subTest(label=label):
                with self.assertRaisesRegex(closure.ClosureError, reason):
                    self.verify(label, mutate=mutation)

    def test_source_has_no_execution_or_remote_surface(self) -> None:
        source = (
            ROOT / "scripts" /
            "hepta_bounded_shadow_closure_verifier.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
                "subprocess", "socket.", "urllib", "requests.",
                "trade.place_order", "risk.preview_order",
                "systemctl", "sudo "):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
