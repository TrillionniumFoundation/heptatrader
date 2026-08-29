#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hepta_bounded_shadow_observer as observer  # noqa: E402
import hepta_eurusd_confirmed_momentum_strategy as evaluator  # noqa: E402
import hepta_shadow_market_history as history  # noqa: E402
import hepta_strategy_shadow_runner as runner  # noqa: E402
from hepta_strategy_contracts import (  # noqa: E402
    canonical_bytes,
    digest_document,
)
import validate_hepta_strategy_decision_receipt as validator  # noqa: E402


BASE_MS = 1_800_000_000_000
CAMPAIGN_ID = "bounded-shadow-observer-test"


def write(path: Path, value: Any, *, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(canonical_bytes(value))
    path.chmod(mode)


def snapshot(
    started_at_ms: int,
    *,
    price_index: int = 0,
    epoch: str = "epoch-bounded-shadow-1",
    fencing_generation: int = 7,
    catalog_digit: str = "1",
    quote_observed_at_ms: int | None = None,
    quote_stale_after_ms: int | None = None,
) -> dict[str, Any]:
    read_times = {
        tool: started_at_ms + 50 + index * 50
        for index, tool in enumerate(history.READ_ORDER)
    }
    bid = round(1.10000 + price_index * 0.000001, 8)
    ask = round(bid + 0.00004, 8)
    quote_observed_at_ms = (
        started_at_ms + 200
        if quote_observed_at_ms is None else
        quote_observed_at_ms
    )
    quote_stale_after_ms = (
        quote_observed_at_ms + 5_000
        if quote_stale_after_ms is None else
        quote_stale_after_ms
    )
    reads = {
        "account.get_summary": {
            "source": "SIMULATOR",
            "authoritative": True,
            "account_complete": True,
        },
        "portfolio.list_positions": {
            "source": "SIMULATOR",
            "authoritative": True,
            "positions": [],
        },
        "orders.list": {
            "source": "SIMULATOR",
            "authoritative": True,
            "active_order_ids": [],
        },
        "risk.get_limits": {
            "source": "SIMULATOR",
            "authoritative": True,
            "gross_absolute_position": 0,
        },
        "market.get_quote": {
            "source": "SIMULATOR",
            "authoritative": True,
            "instrument": "EUR.USD",
            "bid": bid,
            "ask": ask,
            "observed_at_ms": quote_observed_at_ms,
            "stale_after_ms": quote_stale_after_ms,
            "stale": False,
        },
        "system.get_health": {
            "gateway_ready": True,
            "remote_execution": True,
            "remote_execution_configured": True,
            "remote_execution_ready": True,
            "execution_mode": "SIMULATOR",
            "execution_service_epoch": epoch,
            "execution_service_fencing_generation": fencing_generation,
            "remote_execution_reason": "",
        },
    }
    body = {
        "schema": "hepta.shadow-watch-snapshot.v2",
        "version": 2,
        "domain_id": "alpha",
        "agent_uid": 2104,
        "collection_started_at_ms": started_at_ms,
        "collection_finished_at_ms": started_at_ms + 400,
        "read_finished_at_ms": read_times,
        "generated_at_ms": started_at_ms + 450,
        "instrument": "EUR.USD",
        "catalog_sha256": "sha256:" + catalog_digit * 64,
        "descriptor_sha256": {
            tool: "sha256:" + format(index + 2, "064x")
            for index, tool in enumerate(history.READ_ORDER)
        },
        "reads": reads,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": digest_document(body)}


def lease_receipt(
    *,
    accepted_at_ms: int,
    generation: int,
    operation: str,
    previous_body_sha256: str | None = None,
) -> dict[str, Any]:
    body = {
        "schema": "hepta.shadow-watch-lease-receipt.v1",
        "version": 1,
        "domain_id": "alpha",
        "agent_id": "alpha",
        "agent_uid": 2104,
        "boundary": "WATCH",
        "operation": operation,
        "lease_generation": generation,
        "previous_lease_generation": (
            None if operation == "PROVISION" else generation - 1),
        "previous_receipt_body_sha256": (
            None if operation == "PROVISION" else previous_body_sha256),
        "accepted": True,
        "reason_code": "OK",
        "accepted_at_ms": accepted_at_ms,
        "ttl_seconds": 3_600,
        "expires_at_ms": accepted_at_ms + 3_600_000,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
    }
    return {**body, "body_sha256": digest_document(body)}


def export_receipt(
    snapshot_value: dict[str, Any],
    lease_value: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "schema": "hepta.shadow-watch-export-receipt.v1",
        "version": 1,
        "domain_id": snapshot_value["domain_id"],
        "agent_uid": snapshot_value["agent_uid"],
        "reader_uid": os.geteuid(),
        "reader_gid": os.getegid(),
        "boundary": "WATCH_EXPORT",
        "lease_generation": lease_value["lease_generation"],
        "lease_receipt_body_sha256": lease_value["body_sha256"],
        "lease_receipt_file_sha256":
            observer.digest_bytes(canonical_bytes(lease_value)),
        "snapshot_body_sha256": snapshot_value["body_sha256"],
        "snapshot_file_sha256":
            observer.digest_bytes(canonical_bytes(snapshot_value)),
        "snapshot_generated_at_ms": snapshot_value["generated_at_ms"],
        "exported_at_ms": snapshot_value["generated_at_ms"] + 1,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": digest_document(body)}


def compact_strategy(path: Path) -> tuple[dict[str, Any], str]:
    config = json.loads(
        (
            ROOT /
            "strategies/eurusd-confirmed-momentum-shadow-v2.json"
        ).read_text(encoding="utf-8")
    )
    config["evidence_requirements"].update({
        "minimum_raw_quote_observations": 7,
        "minimum_resampled_quote_observations": 4,
        "minimum_history_span_seconds": 60,
        "minimum_bar_observations": 10,
    })
    config["feature_windows"].update({
        "atr_bars": 3,
        "breakout_bars": 5,
        "fast_ema_bars": 3,
        "quote_confirmation_seconds": 20,
        "quote_lookback_seconds": 60,
        "quote_resample_seconds": 20,
        "slow_ema_bars": 6,
        "slope_lookback_bars": 2,
    })
    write(path, config)
    return config, evaluator.strategy_package_digest(path)


def policy(
    path: Path,
    *,
    strategy_config: dict[str, Any],
    strategy_sha256: str,
    valid_after_ms: int,
    maximum_iterations: int = 2,
    maximum_lateness_ms: int = 2_000,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": runner.POLICY_SCHEMA,
        "version": 1,
        "campaign_id": CAMPAIGN_ID,
        "campaign_sha256": "sha256:" + "0" * 64,
        "strategy_id": strategy_config["strategy_id"],
        "strategy_version": strategy_config["strategy_version"],
        "strategy_sha256": strategy_sha256,
        "valid_after_ms": valid_after_ms,
        "expires_at_ms": (
            valid_after_ms +
            maximum_iterations * runner.SLOT_INTERVAL_MS +
            maximum_lateness_ms + 1),
        "slot_interval_ms": runner.SLOT_INTERVAL_MS,
        "maximum_iterations": maximum_iterations,
        "maximum_lateness_ms": maximum_lateness_ms,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "body_sha256": "sha256:" + "0" * 64,
    }
    document["campaign_sha256"] = digest_document(
        runner._campaign_binding(document))
    body = dict(document)
    body.pop("body_sha256")
    document["body_sha256"] = digest_document(body)
    write(path, document)
    return document


def source_bundle(
    directory: Path,
    observed_at_ms: int,
) -> dict[str, Any]:
    evidence = directory / f"evidence-{observed_at_ms}"
    evidence.mkdir(mode=0o700, exist_ok=True)
    fed_payload = evidence / "fed.html"
    ecb_payload = evidence / "ecb.html"
    for path, contents in (
            (fed_payload, b"<html>official fed observer fixture</html>"),
            (ecb_payload, b"<html>official ecb observer fixture</html>")):
        if path.exists():
            path.chmod(0o600)
        path.write_bytes(contents)
        path.chmod(0o400)
    usd_digest = (
        "sha256:" + hashlib.sha256(fed_payload.read_bytes()).hexdigest())
    eur_digest = (
        "sha256:" + hashlib.sha256(ecb_payload.read_bytes()).hexdigest())
    extractor_digest = (
        "sha256:" + hashlib.sha256(
            b"observer deterministic extractor fixture").hexdigest())
    semantic_output_sha256 = digest_document({
        "events": [],
        "items": [],
    })
    receipt_body = {
        "schema": "hepta.market-source-extraction-receipt.v1",
        "version": 1,
        "observed_at_ms": observed_at_ms,
        "extractor": {
            "extractor_id": "HEPTA_TEST_OBSERVER_EXTRACTOR",
            "extractor_version": "1.0.0",
            "extractor_code_sha256": extractor_digest,
            "deterministic": True,
        },
        "sources": [
            {
                "provider": "FEDERAL_RESERVE",
                "requested_url":
                    "https://www.federalreserve.gov/newsevents.htm",
                "final_url":
                    "https://www.federalreserve.gov/newsevents.htm",
                "http_status": 200,
                "content_type": "text/html",
                "fetch_started_at_ms": observed_at_ms - 200,
                "fetched_at_ms": observed_at_ms - 100,
                "published_at_ms": None,
                "revision": "revision-usd",
                "payload_path": fed_payload.name,
                "content_sha256": usd_digest,
            },
            {
                "provider": "ECB",
                "requested_url":
                    "https://www.ecb.europa.eu/press/calendars/"
                    "mgcgc/html/index.en.html",
                "final_url":
                    "https://www.ecb.europa.eu/press/calendars/"
                    "mgcgc/html/index.en.html",
                "http_status": 200,
                "content_type": "text/html",
                "fetch_started_at_ms": observed_at_ms - 200,
                "fetched_at_ms": observed_at_ms - 100,
                "published_at_ms": None,
                "revision": "revision-eur",
                "payload_path": ecb_payload.name,
                "content_sha256": eur_digest,
            },
        ],
        "completeness": [
            {
                "source_content_sha256": usd_digest,
                "coverage_start_ms": observed_at_ms - 86_400_000,
                "coverage_end_ms": observed_at_ms + 86_400_000,
                "currencies": ["USD"],
                "complete": True,
                "derived_by_extractor": True,
                "rule_id": "fed-observer-fixture",
                "rule_version": "1.0.0",
            },
            {
                "source_content_sha256": eur_digest,
                "coverage_start_ms": observed_at_ms - 86_400_000,
                "coverage_end_ms": observed_at_ms + 86_400_000,
                "currencies": ["EUR"],
                "complete": True,
                "derived_by_extractor": True,
                "rule_id": "ecb-observer-fixture",
                "rule_version": "1.0.0",
            },
        ],
        "events": [],
        "items": [],
        "semantic_output_sha256": semantic_output_sha256,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    receipt = {
        **receipt_body,
        "body_sha256": digest_document(receipt_body),
    }
    receipt_path = evidence / "receipt.json"
    if receipt_path.exists():
        receipt_path.chmod(0o600)
    write(receipt_path, receipt, mode=0o400)
    return {
        "schema": "hepta.market-source-bundle.v2",
        "observed_at_ms": observed_at_ms,
        "extraction_receipt_path": receipt_path.relative_to(
            directory).as_posix(),
        "extraction_receipt_sha256": (
            "sha256:" + hashlib.sha256(
                receipt_path.read_bytes()).hexdigest()),
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }


class BoundedShadowObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="hepta-bounded-shadow-observer-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifacts = self.root / "artifacts"
        self.strategy_path = self.root / "strategy.json"
        self.strategy, self.strategy_sha256 = compact_strategy(
            self.strategy_path)
        self.policy_path = self.root / "policy.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.lease_path = self.root / "lease.json"
        self.export_receipt_path = self.root / "export-receipt.json"
        self.bundle_path = self.root / "bundle.json"
        patcher = mock.patch.object(
            history, "ROOT_TRUST_UID", os.geteuid())
        patcher.start()
        self.addCleanup(patcher.stop)
        observer.evidence_normalizer.TRUSTED_EVIDENCE_ROOTS = (
            self.root.resolve(),)
        observer.evidence_normalizer.TRUSTED_ATTESTATION_UID = os.geteuid()
        observer.evidence_normalizer.PINNED_EXTRACTORS = {
            (
                "HEPTA_TEST_OBSERVER_EXTRACTOR",
                "1.0.0",
            ): (
                "sha256:" + hashlib.sha256(
                    b"observer deterministic extractor fixture").hexdigest()
            ),
        }

    def write_sample(
        self,
        started_at_ms: int,
        index: int = 0,
        *,
        epoch: str = "epoch-bounded-shadow-1",
        quote_observed_at_ms: int | None = None,
        quote_stale_after_ms: int | None = None,
    ) -> None:
        snapshot_value = snapshot(
            started_at_ms,
            price_index=index,
            epoch=epoch,
            quote_observed_at_ms=quote_observed_at_ms,
            quote_stale_after_ms=quote_stale_after_ms,
        )
        write(self.snapshot_path, snapshot_value, mode=0o400)
        lease_value = json.loads(
            self.lease_path.read_text(encoding="ascii"))
        write(
            self.export_receipt_path,
            export_receipt(snapshot_value, lease_value),
            mode=0o400,
        )

    def observe(self, started_at_ms: int) -> dict[str, Any]:
        return observer.observe_once(
            campaign_id=CAMPAIGN_ID,
            policy_path=self.policy_path,
            strategy_path=self.strategy_path,
            snapshot_path=self.snapshot_path,
            watch_lease_receipt_path=self.lease_path,
            watch_export_receipt_path=self.export_receipt_path,
            source_bundle_path=self.bundle_path,
            artifact_root=self.artifacts,
            observed_now_ms=started_at_ms + 450,
        )

    def state(self) -> dict[str, Any]:
        return json.loads(
            (self.artifacts / "observer-state.json").read_text(
                encoding="ascii"))

    def test_receipt_is_emitted_only_after_contiguous_warmup(self) -> None:
        valid_after_ms = BASE_MS + 3_000_000
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=valid_after_ms,
            maximum_iterations=2,
        )
        provision = lease_receipt(
            accepted_at_ms=BASE_MS - 1_000,
            generation=1,
            operation="PROVISION",
        )
        write(self.lease_path, provision, mode=0o400)
        write(
            self.bundle_path,
            source_bundle(self.root, valid_after_ms + 350),
        )

        with mock.patch.object(
                observer,
                "_full_payload_usage",
                wraps=observer._full_payload_usage) as storage_audits:
            for index in range(300):
                started = BASE_MS + index * 10_000
                self.write_sample(started, index)
                result = self.observe(started)
                self.assertEqual(result["outcome"], "WARMUP")
                self.assertFalse(
                    (self.artifacts / "receipts").exists())

            self.write_sample(valid_after_ms, 300)
            result = self.observe(valid_after_ms)
        self.assertIn(result["outcome"], {"NO_TRADE", "SHADOW_TRADE"})
        self.assertEqual(result["status"], "RUNNING")
        receipt_path = (
            self.artifacts / "receipts" / "decision-000001.json")
        receipt = json.loads(receipt_path.read_text(encoding="ascii"))
        validator.validate(receipt)
        self.assertEqual(self.state()["completed_iterations"], 1)
        first_payload_bytes = self.state()["accounted_payload_bytes"]
        first_iteration = (
            self.artifacts / "segments" / "segment-000001" /
            "iterations" / "iteration-000001")
        self.assertTrue(
            (first_iteration / "information-packet.json").is_file())
        self.assertTrue(
            (first_iteration / "source-window-manifest.json").is_file())
        manifest = json.loads(
            (first_iteration / "source-window-manifest.json").read_text(
                encoding="ascii"))
        manifest_body = dict(manifest)
        manifest_digest = manifest_body.pop("body_sha256")
        self.assertEqual(manifest_digest, digest_document(manifest_body))
        self.assertEqual(manifest["source_first_sequence"], 1)
        self.assertEqual(manifest["source_last_sequence"], 301)
        current_export_receipt = json.loads(
            self.export_receipt_path.read_text(encoding="ascii"))
        self.assertEqual(
            manifest["watch_export_receipt_body_sha256"],
            current_export_receipt["body_sha256"],
        )
        self.assertEqual(
            manifest["watch_export_receipt_file_sha256"],
            observer.digest_bytes(
                self.export_receipt_path.read_bytes()),
        )
        for field in (
                "paper_authorized", "live_authorized",
                "mutation_attempted", "direct_broker_access"):
            self.assertFalse(manifest[field])
        for name in (
                "sampled-bars.json", "quote-history.json",
                "bar-history.json"):
            self.assertFalse((first_iteration / name).exists())

        rotate_path = self.root / "lease-second-hour.json"
        rotate = lease_receipt(
            accepted_at_ms=BASE_MS + 3_060_000,
            generation=2,
            operation="ROTATE",
            previous_body_sha256=provision["body_sha256"],
        )
        write(rotate_path, rotate, mode=0o400)
        with mock.patch.object(
                observer,
                "_full_payload_usage",
                wraps=observer._full_payload_usage) as later_audits:
            for index in range(301, 312):
                if index == 306:
                    self.lease_path = rotate_path
                started = BASE_MS + index * 10_000
                self.write_sample(started, index)
                self.assertEqual(
                    self.observe(started)["outcome"], "COLLECTED")
            second_started = valid_after_ms + runner.SLOT_INTERVAL_MS
            write(
                self.bundle_path,
                source_bundle(self.root, second_started + 350),
            )
            self.write_sample(second_started, 312)
            with mock.patch.object(
                    history,
                    "audit_history",
                    side_effect=history.HistoryError(
                        "MARKET_HISTORY_FINAL_AUDIT_TEST_FAILURE"),
            ) as failed_final_audit:
                pending = self.observe(second_started)
            self.assertEqual(failed_final_audit.call_count, 1)
            self.assertEqual(
                pending["outcome"], "FINAL_AUDIT_REQUIRED")
            self.assertEqual(
                pending["status"], "FINAL_AUDIT_REQUIRED")
            self.assertEqual(
                self.state()["status"], "FINAL_AUDIT_REQUIRED")
            self.assertFalse(
                (self.artifacts / "final-audit-receipt.json").exists())

            # A pure retry finalizes already-committed history without
            # consuming, appending, or evaluating the sample again.
            result = self.observe(second_started)
        self.assertLessEqual(
            storage_audits.call_count + later_audits.call_count, 13)
        self.assertEqual(result["outcome"], "COMPLETE")
        self.assertEqual(result["status"], "COMPLETE")
        terminal_state = self.state()
        self.assertEqual(terminal_state["completed_iterations"], 2)
        final_audit_path = self.artifacts / "final-audit-receipt.json"
        final_audit = json.loads(
            final_audit_path.read_text(encoding="ascii"))
        final_audit_body = dict(final_audit)
        final_audit_digest = final_audit_body.pop("body_sha256")
        self.assertEqual(
            final_audit_digest, digest_document(final_audit_body))
        self.assertEqual(
            final_audit["schema"],
            "hepta.bounded-shadow-final-audit-receipt.v2",
        )
        self.assertEqual(final_audit["version"], 2)
        self.assertEqual(final_audit["segment_count"], 1)
        self.assertEqual(final_audit["sample_count"], 313)
        self.assertEqual(final_audit["missed_sample_count"], 0)
        self.assertEqual(final_audit["missed_decision_count"], 0)
        self.assertEqual(final_audit["segments"][0]["segment_index"], 1)
        self.assertEqual(final_audit["segments"][0]["record_count"], 313)
        self.assertEqual(
            final_audit["segments"][0]["history_head_sha256"],
            terminal_state["segment_history_head_sha256"],
        )
        self.assertEqual(
            terminal_state["final_audit_segment_count"], 1)
        self.assertEqual(
            terminal_state["final_audit_receipt_sha256"],
            observer.digest_bytes(canonical_bytes(final_audit)),
        )
        self.assertEqual(
            terminal_state["audit_events"][-1]["event"],
            "FINAL_HISTORY_AUDIT_COMMITTED",
        )
        self.assertLess(
            terminal_state["accounted_payload_bytes"] - first_payload_bytes,
            1024 * 1024,
        )
        second_iteration = (
            self.artifacts / "segments" / "segment-000001" /
            "iterations" / "iteration-000002")
        for name in (
                "sampled-bars.json", "quote-history.json",
                "bar-history.json"):
            self.assertFalse((second_iteration / name).exists())
        for field in (
                "live_authorized", "mutation_attempted",
                "direct_broker_access"):
            self.assertFalse(result[field])
            self.assertFalse(receipt[field])
        self.assertFalse(result["paper_authorized"])
        self.assertTrue(receipt["paper_only"])
        self.assertTrue(receipt["shadow_only"])

    def test_late_first_slot_is_audited_and_never_caught_up(self) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS,
            maximum_iterations=2,
            maximum_lateness_ms=1_000,
        )
        provision = lease_receipt(
            accepted_at_ms=BASE_MS - 1_000,
            generation=1,
            operation="PROVISION",
        )
        write(self.lease_path, provision, mode=0o400)
        write(self.bundle_path, source_bundle(self.root, BASE_MS + 5_000))
        started = BASE_MS + 5_000
        self.write_sample(started)
        first = self.observe(started)
        self.assertEqual(first["outcome"], "STOPPED")
        state = self.state()
        self.assertEqual(state["completed_iterations"], 0)
        self.assertEqual(state["missed_decision_count"], 1)
        self.assertEqual(
            state["audit_events"][-1]["event"],
            "MISSED_DECISION_SLOT",
        )
        self.assertFalse((self.artifacts / "receipts").exists())

        self.write_sample(BASE_MS + runner.SLOT_INTERVAL_MS)
        second = self.observe(BASE_MS + runner.SLOT_INTERVAL_MS)
        self.assertEqual(second["outcome"], "STOPPED")
        self.assertEqual(self.state()["completed_iterations"], 0)
        self.assertFalse((self.artifacts / "receipts").exists())

    def test_generation_rotation_is_receipt_chain_bound(self) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        first_receipt = lease_receipt(
            accepted_at_ms=BASE_MS - 1_000,
            generation=1,
            operation="PROVISION",
        )
        write(self.lease_path, first_receipt, mode=0o400)
        write(self.bundle_path, source_bundle(self.root, BASE_MS))
        self.write_sample(BASE_MS)
        self.assertEqual(self.observe(BASE_MS)["outcome"], "WARMUP")

        second_path = self.root / "lease-rotate.json"
        second_receipt = lease_receipt(
            accepted_at_ms=BASE_MS + 5_000,
            generation=2,
            operation="ROTATE",
            previous_body_sha256=first_receipt["body_sha256"],
        )
        write(second_path, second_receipt, mode=0o400)
        self.lease_path = second_path
        self.write_sample(BASE_MS + 10_000, 1)
        self.assertEqual(
            self.observe(BASE_MS + 10_000)["outcome"], "WARMUP")
        state = self.state()
        self.assertEqual(state["last_watch_generation"], 2)
        self.assertEqual(
            state["audit_events"][-1]["event"], "WATCH_LEASE_ROTATED")

        tampered_path = self.root / "lease-uncertain.json"
        tampered = copy.deepcopy(second_receipt)
        tampered["lease_generation"] = 4
        tampered["previous_lease_generation"] = 3
        tampered["previous_receipt_body_sha256"] = (
            "sha256:" + "f" * 64)
        body = dict(tampered)
        body.pop("body_sha256")
        tampered["body_sha256"] = digest_document(body)
        write(tampered_path, tampered, mode=0o400)
        self.lease_path = tampered_path
        self.write_sample(BASE_MS + 20_000, 2)
        result = self.observe(BASE_MS + 20_000)
        self.assertEqual(result["outcome"], "STOPPED")
        self.assertEqual(result["segment_status"], "CLOSED")
        state = self.state()
        self.assertEqual(state["completed_iterations"], 0)
        self.assertEqual(state["missed_sample_count"], 1)
        self.assertEqual(
            state["audit_events"][-2]["event"], "SAMPLE_REJECTED")

    def test_cadence_gap_stops_once_without_segment_storm(self) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        first_receipt = lease_receipt(
            accepted_at_ms=BASE_MS - 1_000,
            generation=1,
            operation="PROVISION",
        )
        write(self.lease_path, first_receipt, mode=0o400)
        write(self.bundle_path, source_bundle(self.root, BASE_MS))
        self.write_sample(BASE_MS)
        self.assertEqual(self.observe(BASE_MS)["outcome"], "WARMUP")

        rotated = lease_receipt(
            accepted_at_ms=BASE_MS + 5_000,
            generation=2,
            operation="ROTATE",
            previous_body_sha256=first_receipt["body_sha256"],
        )
        rotated_path = self.root / "lease-rotated-gap.json"
        write(rotated_path, rotated, mode=0o400)
        self.lease_path = rotated_path
        self.write_sample(BASE_MS + 10_000, 1)
        self.assertEqual(self.observe(BASE_MS + 10_000)["outcome"], "WARMUP")

        self.write_sample(BASE_MS + 30_000, 2)
        closed = self.observe(BASE_MS + 30_000)
        self.assertEqual(closed["outcome"], "STOPPED")
        self.assertEqual(closed["segment_status"], "CLOSED")
        stopped_state = self.state()
        self.assertEqual(stopped_state["segment_index"], 1)
        self.assertEqual(stopped_state["sample_count"], 2)
        self.assertEqual(stopped_state["missed_sample_count"], 2)
        rejected_events = [
            event for event in stopped_state["audit_events"]
            if event["event"] == "SAMPLE_REJECTED"
        ]
        self.assertEqual(len(rejected_events), 1)
        self.assertEqual(
            rejected_events[0]["detail"]["skipped_capture_slots"], 1)

        self.write_sample(BASE_MS + 40_000, 3)
        resumed = self.observe(BASE_MS + 40_000)
        self.assertEqual(resumed["outcome"], "STOPPED")
        self.assertEqual(resumed["segment_index"], 1)
        self.assertEqual(resumed["segment_status"], "CLOSED")
        self.assertEqual(resumed["segment_record_count"], 2)
        after_retry = self.state()
        self.assertEqual(after_retry["missed_sample_count"], 2)
        self.assertEqual(after_retry["audit_events"],
                         stopped_state["audit_events"])

    def test_repeated_quote_is_non_counting_and_mutation_stops_once(
        self,
    ) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        write(
            self.lease_path,
            lease_receipt(
                accepted_at_ms=BASE_MS - 1_000,
                generation=1,
                operation="PROVISION",
            ),
            mode=0o400,
        )
        write(self.bundle_path, source_bundle(self.root, BASE_MS))
        observed = BASE_MS + 200
        stale_after = observed + 30_000
        self.write_sample(
            BASE_MS,
            quote_observed_at_ms=observed,
            quote_stale_after_ms=stale_after,
        )
        self.assertEqual(self.observe(BASE_MS)["outcome"], "WARMUP")
        self.write_sample(
            BASE_MS + 10_000,
            quote_observed_at_ms=observed,
            quote_stale_after_ms=stale_after,
        )
        self.assertEqual(
            self.observe(BASE_MS + 10_000)["outcome"], "WARMUP")
        history_directory = (
            self.artifacts / "segments" / "segment-000001" / "history")
        records = history.load_history(
            history_directory,
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
        )
        self.assertEqual(
            [record["quote_changed"] for record in records],
            [True, False],
        )

        self.write_sample(
            BASE_MS + 20_000,
            1,
            quote_observed_at_ms=observed,
            quote_stale_after_ms=stale_after,
        )
        rejected = self.observe(BASE_MS + 20_000)
        self.assertEqual(rejected["outcome"], "STOPPED")
        stopped = self.state()
        self.assertEqual(stopped["missed_sample_count"], 1)
        sample_rejections = [
            event for event in stopped["audit_events"]
            if event["event"] == "SAMPLE_REJECTED"
        ]
        self.assertEqual(len(sample_rejections), 1)
        self.assertEqual(
            sample_rejections[0]["reason"],
            "MARKET_HISTORY_QUOTE_MUTATION",
        )
        self.assertEqual(stopped["segment_index"], 1)

        retried = self.observe(BASE_MS + 20_000)
        self.assertEqual(retried["outcome"], "STOPPED")
        self.assertEqual(self.state()["audit_events"], stopped["audit_events"])

    def test_wall_clock_rollback_is_typed_rejected_and_terminal(self) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        write(
            self.lease_path,
            lease_receipt(
                accepted_at_ms=BASE_MS - 1_000,
                generation=1,
                operation="PROVISION",
            ),
            mode=0o400,
        )
        write(self.bundle_path, source_bundle(self.root, BASE_MS))
        self.write_sample(BASE_MS)
        self.assertEqual(self.observe(BASE_MS)["outcome"], "WARMUP")
        self.write_sample(BASE_MS + 10_000, 1)
        rolled_back = observer.observe_once(
            campaign_id=CAMPAIGN_ID,
            policy_path=self.policy_path,
            strategy_path=self.strategy_path,
            snapshot_path=self.snapshot_path,
            watch_lease_receipt_path=self.lease_path,
            watch_export_receipt_path=self.export_receipt_path,
            source_bundle_path=self.bundle_path,
            artifact_root=self.artifacts,
            observed_now_ms=BASE_MS + 10_449,
        )
        self.assertEqual(rolled_back["outcome"], "STOPPED")
        state = self.state()
        self.assertEqual(state["missed_sample_count"], 1)
        rejection = state["audit_events"][-2]
        self.assertEqual(rejection["event"], "SAMPLE_REJECTED")
        self.assertEqual(rejection["detail"]["phase"], "snapshot_read")
        self.assertIn("TIME_INVALID", rejection["reason"])
        self.assertEqual(state["segment_index"], 1)

    def test_production_strategy_is_not_reachable_in_ten_minute_warmup(
        self,
    ) -> None:
        production = json.loads((
            ROOT / "strategies/eurusd-confirmed-momentum-shadow-v2.json"
        ).read_text(encoding="utf-8"))
        quote_path = self.root / "production-warmup-quotes.json"
        bar_path = self.root / "production-warmup-bars.json"
        quotes = [{
            "observed_at_ms": BASE_MS + index * 10_000,
            "quote_changed": True,
        } for index in range(61)]
        write(quote_path, {
            "schema": "hepta.authoritative-quote-history.v3",
            "version": 3,
            "quotes": quotes,
        })
        write(bar_path, {"bars": [{}]})
        ready, evidence = observer._warmup_ready(
            strategy=production,
            quote_history_path=quote_path,
            bar_history_path=bar_path,
        )
        self.assertFalse(ready)
        self.assertEqual(evidence["quote_count"], 61)
        self.assertEqual(evidence["quote_capture_count"], 61)
        self.assertEqual(evidence["quote_span_ms"], 600_000)
        self.assertEqual(evidence["bar_count"], 1)
        self.assertEqual(evidence["required_quote_count"], 361)
        self.assertEqual(evidence["required_quote_span_ms"], 5_400_000)
        self.assertEqual(evidence["required_bar_count"], 40)

    def test_input_failure_stops_with_all_authority_flags_false(self) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        write(
            self.lease_path,
            lease_receipt(
                accepted_at_ms=BASE_MS - 1_000,
                generation=1,
                operation="PROVISION",
            ),
            mode=0o400,
        )
        write(self.bundle_path, source_bundle(self.root, BASE_MS))
        self.write_sample(BASE_MS)
        with mock.patch.object(
                history,
                "append_snapshot",
                side_effect=history.HistoryError(
                    "MARKET_HISTORY_LEASE_RECEIPT_DIGEST_INVALID")):
            result = self.observe(BASE_MS)
        self.assertEqual(result["outcome"], "STOPPED")
        state = self.state()
        self.assertEqual(state["status"], "STOPPED")
        self.assertEqual(state["completed_iterations"], 0)
        self.assertFalse((self.artifacts / "receipts").exists())
        for field in (
                "paper_authorized", "live_authorized",
                "mutation_attempted", "direct_broker_access"):
            self.assertFalse(result[field])
            self.assertFalse(state[field])
            self.assertFalse(state["audit_events"][-1][field])

    def test_root_snapshot_binding_rejects_writable_and_fabricated_input(
        self,
    ) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        lease_value = lease_receipt(
            accepted_at_ms=BASE_MS - 1_000,
            generation=1,
            operation="PROVISION",
        )
        write(self.lease_path, lease_value, mode=0o400)
        write(self.bundle_path, source_bundle(self.root, BASE_MS))
        self.write_sample(BASE_MS)

        self.snapshot_path.chmod(0o600)
        unsafe = self.observe(BASE_MS)
        self.assertEqual(unsafe["outcome"], "STOPPED")
        unsafe_state = self.state()
        self.assertEqual(unsafe_state["missed_sample_count"], 1)
        self.assertEqual(
            unsafe_state["audit_events"][-2]["detail"]["phase"],
            "snapshot_read",
        )
        self.snapshot_path.chmod(0o400)

        self.artifacts = self.root / "fabricated-artifacts"
        different_snapshot = snapshot(BASE_MS, price_index=1)
        write(self.snapshot_path, different_snapshot, mode=0o400)
        result = self.observe(BASE_MS)
        self.assertEqual(result["outcome"], "STOPPED")
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(self.state()["completed_iterations"], 0)
        self.assertFalse((self.artifacts / "receipts").exists())
        for field in (
                "paper_authorized", "live_authorized",
                "mutation_attempted", "direct_broker_access"):
            self.assertFalse(result[field])

    def test_execution_epoch_drift_closes_segment_without_receipt(self) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        write(
            self.lease_path,
            lease_receipt(
                accepted_at_ms=BASE_MS - 1_000,
                generation=1,
                operation="PROVISION",
            ),
            mode=0o400,
        )
        write(self.bundle_path, source_bundle(self.root, BASE_MS))
        self.write_sample(BASE_MS)
        self.assertEqual(self.observe(BASE_MS)["outcome"], "WARMUP")

        self.write_sample(
            BASE_MS + 10_000,
            1,
            epoch="epoch-bounded-shadow-2",
        )
        result = self.observe(BASE_MS + 10_000)
        self.assertEqual(result["outcome"], "STOPPED")
        self.assertEqual(result["segment_status"], "CLOSED")
        state = self.state()
        self.assertEqual(state["completed_iterations"], 0)
        self.assertEqual(state["missed_sample_count"], 1)
        self.assertFalse((self.artifacts / "receipts").exists())

    def test_incremental_accounting_recovers_record_before_state(self) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        write(
            self.lease_path,
            lease_receipt(
                accepted_at_ms=BASE_MS - 1_000,
                generation=1,
                operation="PROVISION",
            ),
            mode=0o400,
        )
        write(self.bundle_path, source_bundle(self.root, BASE_MS))
        self.write_sample(BASE_MS)
        self.assertEqual(self.observe(BASE_MS)["outcome"], "WARMUP")
        before = self.state()

        self.write_sample(BASE_MS + 10_000, 1)
        history_directory = (
            self.artifacts / "segments" / "segment-000001" / "history")
        appended = history.append_snapshot(
            history_directory,
            self.snapshot_path,
            cadence_ms=10_000,
            watch_lease_receipt_path=self.lease_path,
            watch_export_receipt_path=self.export_receipt_path,
            maximum_jitter_ms=1_000,
        )
        self.assertEqual(appended["status"], "appended")
        result = self.observe(BASE_MS + 10_000)
        self.assertEqual(result["outcome"], "WARMUP")
        after = self.state()
        self.assertEqual(after["sample_count"], 2)
        self.assertEqual(after["segment_record_count"], 2)
        self.assertEqual(
            after["accounted_payload_files"],
            before["accounted_payload_files"] + 1,
        )

    def test_payload_tamper_fails_at_next_exponential_audit(self) -> None:
        policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS + 600_000,
            maximum_iterations=1,
        )
        write(
            self.lease_path,
            lease_receipt(
                accepted_at_ms=BASE_MS - 1_000,
                generation=1,
                operation="PROVISION",
            ),
            mode=0o400,
        )
        write(self.bundle_path, source_bundle(self.root, BASE_MS))
        self.write_sample(BASE_MS)
        self.assertEqual(self.observe(BASE_MS)["outcome"], "WARMUP")
        rogue = (
            self.artifacts / "segments" / "segment-000001" / "rogue.json")
        write(rogue, {"unexpected": True})

        self.write_sample(BASE_MS + 10_000, 1)
        result = self.observe(BASE_MS + 10_000)
        self.assertEqual(result["outcome"], "STOPPED")
        state = self.state()
        self.assertEqual(state["status"], "STOPPED")
        self.assertEqual(
            state["audit_events"][-1]["event"], "STORAGE_GUARD_FAILED")
        self.assertFalse((self.artifacts / "receipts").exists())

    def test_twenty_day_schedule_has_logarithmic_full_audits(self) -> None:
        state = {
            "sample_count": 0,
            "accounted_payload_bytes": 0,
            "accounted_payload_files": 0,
            "accounted_payload_accumulator": observer.ZERO_ACCUMULATOR,
            "last_storage_audit_sample_count": 0,
            "last_storage_audit_accumulator": observer.ZERO_ACCUMULATOR,
        }
        expected = {
            "bytes": 0,
            "files": 0,
            "accumulator": observer.ZERO_ACCUMULATOR,
        }
        twenty_days = 20 * 24 * 60 * 6
        with mock.patch.object(
                observer,
                "_full_payload_usage",
                return_value=expected) as audit:
            for sample_count in range(1, twenty_days + 1):
                state["sample_count"] = sample_count
                observer._reconcile_storage(self.artifacts, state)
        self.assertLessEqual(audit.call_count, 19)
        self.assertEqual(
            state["last_storage_audit_sample_count"], 131_072)

    def test_finalizer_audits_every_segment_before_complete(self) -> None:
        policy_document = policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS,
            maximum_iterations=1,
        )
        state = observer._initial_state(
            policy_document,
            "sha256:" + "d" * 64,
        )
        state["status"] = "FINAL_AUDIT_REQUIRED"
        state["segment_index"] = 2
        state["completed_iterations"] = 1
        state["sample_count"] = 3
        state["last_receipt_sha256"] = "sha256:" + "e" * 64
        first_history = (
            self.artifacts / "segments" / "segment-000001" / "history")
        first_history.mkdir(mode=0o700, parents=True)

        def audit_result(path: Path, **_arguments: Any) -> dict[str, Any]:
            index = int(path.parent.name.removeprefix("segment-"))
            return {
                "status": "valid",
                "record_count": index,
                "history_head_sha256":
                    "sha256:" + format(index, "064x"),
                "history_record_bytes": index * 100,
                "history_index_bytes": 50,
                "history_storage_bytes": index * 100 + 50,
                "source_sha256":
                    "sha256:" + format(index + 10, "064x"),
                "directory_entries_scanned": index + 1,
            }

        with mock.patch.object(
                history,
                "audit_history",
                side_effect=audit_result) as incomplete_audit:
            with self.assertRaisesRegex(
                    observer.ObserverError,
                    "BOUNDED_SHADOW_FINAL_AUDIT_SEGMENT_MISSING"):
                observer._finalize_observation(
                    artifact_root=self.artifacts,
                    state=state,
                    policy=policy_document,
                    at_ms=BASE_MS,
                )
        self.assertEqual(incomplete_audit.call_count, 1)
        self.assertEqual(state["status"], "FINAL_AUDIT_REQUIRED")
        self.assertFalse(
            (self.artifacts / "final-audit-receipt.json").exists())

        second_history = (
            self.artifacts / "segments" / "segment-000002" / "history")
        second_history.mkdir(mode=0o700, parents=True)
        with mock.patch.object(
                history,
                "audit_history",
                side_effect=audit_result) as complete_audit:
            receipt = observer._finalize_observation(
                artifact_root=self.artifacts,
                state=state,
                policy=policy_document,
                at_ms=BASE_MS,
            )
        self.assertEqual(complete_audit.call_count, 2)
        self.assertEqual(
            [
                call.args[0].parent.name
                for call in complete_audit.call_args_list
            ],
            ["segment-000001", "segment-000002"],
        )
        self.assertEqual(state["status"], "COMPLETE")
        self.assertEqual(receipt["segment_count"], 2)
        self.assertEqual(
            [segment["record_count"] for segment in receipt["segments"]],
            [1, 2],
        )
        self.assertEqual(
            state["audit_events"][-1]["event"],
            "FINAL_HISTORY_AUDIT_COMMITTED",
        )

    def test_finalizer_rejects_missed_or_unreconciled_samples(self) -> None:
        policy_document = policy(
            self.policy_path,
            strategy_config=self.strategy,
            strategy_sha256=self.strategy_sha256,
            valid_after_ms=BASE_MS,
            maximum_iterations=1,
        )
        history_directory = (
            self.artifacts / "segments" / "segment-000001" / "history")
        history_directory.mkdir(mode=0o700, parents=True)
        audit_result = {
            "status": "valid",
            "record_count": 1,
            "history_head_sha256": "sha256:" + "1" * 64,
            "history_record_bytes": 100,
            "history_index_bytes": 50,
            "history_storage_bytes": 150,
            "source_sha256": "sha256:" + "2" * 64,
            "directory_entries_scanned": 2,
        }

        state = observer._initial_state(
            policy_document,
            "sha256:" + "d" * 64,
        )
        state["status"] = "FINAL_AUDIT_REQUIRED"
        state["segment_index"] = 1
        state["completed_iterations"] = 1
        state["sample_count"] = 1
        state["missed_sample_count"] = 1
        with mock.patch.object(
                history, "audit_history", return_value=audit_result):
            with self.assertRaisesRegex(
                    observer.ObserverError,
                    "BOUNDED_SHADOW_FINAL_AUDIT_MISSED_COUNT_NONZERO"):
                observer._finalize_observation(
                    artifact_root=self.artifacts,
                    state=state,
                    policy=policy_document,
                    at_ms=BASE_MS,
                )

        state["missed_sample_count"] = 0
        state["sample_count"] = 2
        with mock.patch.object(
                history, "audit_history", return_value=audit_result):
            with self.assertRaisesRegex(
                    observer.ObserverError,
                    "BOUNDED_SHADOW_FINAL_AUDIT_SAMPLE_COUNT_DRIFT"):
                observer._finalize_observation(
                    artifact_root=self.artifacts,
                    state=state,
                    policy=policy_document,
                    at_ms=BASE_MS,
                )
        self.assertFalse(
            (self.artifacts / "final-audit-receipt.json").exists())

    def test_source_has_no_privileged_or_remote_execution_surface(self) -> None:
        source = (
            SCRIPTS / "hepta_bounded_shadow_observer.py"
        ).read_text(encoding="utf-8").lower()
        for forbidden in (
                "subprocess", "socket", "systemctl", "sudo",
                "campaignctl", "heptactl", "ibapi", "xtquant"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
