#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import hepta_strategy_pipeline_tests as fixtures  # noqa: E402
import hepta_strategy_shadow_runner as runner  # noqa: E402
from hepta_strategy_contracts import (  # noqa: E402
    ContractError,
    canonical_bytes,
    digest_document,
    digest_file,
)
import validate_hepta_strategy_decision_receipt as validator  # noqa: E402


EVALUATED_AT_MS = 1_800_000_000_000
CAMPAIGN_ID = "shadow-runner-test"
STRATEGY_PATH = (
    ROOT / "strategies/eurusd-confirmed-momentum-shadow-v2.json")
SLOT_INTERVAL_MS = 2 * 60 * 1000
MAXIMUM_LATENESS_MS = 60 * 1000
MAXIMUM_ITERATIONS = 4
RISING_MIDS = [
    1.10000, 1.10015, 1.10020, 1.10042,
    1.10050, 1.10062, 1.10080, 1.10100,
]


def input_paths(
    directory: Path,
    *,
    evaluated_at_ms: int = EVALUATED_AT_MS,
    event_window: bool = False,
    rising: bool = True,
    attested: bool = True,
) -> dict[str, Path]:
    paths = {
        name: directory / f"{name}.json"
        for name in (
            "snapshot", "quote_history", "bar_history",
            "calendar", "information",
        )
    }
    mids = (
        RISING_MIDS if rising else
        list(reversed(RISING_MIDS))
    )
    latest = mids[-1]
    snapshot_document = fixtures.snapshot(
        evaluated_at_ms,
        round(latest - 0.00002, 8),
        round(latest + 0.00002, 8),
    )
    fixtures.write_document(
        paths["snapshot"],
        snapshot_document,
    )
    fixtures.write_document(
        paths["quote_history"],
        fixtures.quote_history(
            evaluated_at_ms, mids, snapshot_document),
    )
    fixtures.write_document(
        paths["bar_history"],
        fixtures.bar_history(evaluated_at_ms, ascending=rising),
    )
    if attested:
        calendar, information = fixtures.attested_evidence(
            directory,
            runner.context_builder.evidence_normalizer,
            name=(
                f"runner-{evaluated_at_ms}-"
                f"{'event' if event_window else 'clear'}"
            ),
            evaluated_at_ms=evaluated_at_ms,
            event_window=event_window,
        )
    else:
        calendar = fixtures.calendar(
            evaluated_at_ms, event_window=event_window, version=2)
        information = fixtures.information(evaluated_at_ms, version=2)
    fixtures.write_document(paths["calendar"], calendar)
    fixtures.write_document(paths["information"], information)
    return paths


def observation_policy(
    *,
    valid_after_ms: int = EVALUATED_AT_MS,
    expires_at_ms: int | None = None,
    maximum_iterations: int = MAXIMUM_ITERATIONS,
    maximum_lateness_ms: int = MAXIMUM_LATENESS_MS,
) -> dict[str, Any]:
    expires = (
        expires_at_ms
        if expires_at_ms is not None
        else valid_after_ms + maximum_iterations * SLOT_INTERVAL_MS
    )
    campaign_binding = {
        "schema": "hepta.strategy-shadow-observation-campaign.v1",
        "campaign_id": CAMPAIGN_ID,
        "valid_after_ms": valid_after_ms,
        "expires_at_ms": expires,
        "slot_interval_ms": SLOT_INTERVAL_MS,
        "maximum_iterations": maximum_iterations,
        "maximum_lateness_ms": maximum_lateness_ms,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    body = {
        "schema": "hepta.strategy-shadow-observation-policy.v1",
        "version": 1,
        "campaign_id": CAMPAIGN_ID,
        "campaign_sha256": digest_document(campaign_binding),
        "strategy_id": "eurusd-confirmed-momentum-shadow",
        "strategy_version": "2.0.0",
        "strategy_sha256":
            runner.strategy_evaluator.strategy_package_digest(STRATEGY_PATH),
        "valid_after_ms": valid_after_ms,
        "expires_at_ms": expires,
        "slot_interval_ms": SLOT_INTERVAL_MS,
        "maximum_iterations": maximum_iterations,
        "maximum_lateness_ms": maximum_lateness_ms,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": digest_document(body)}


def write_policy(
    path: Path,
    policy: dict[str, Any] | None = None,
) -> Path:
    fixtures.write_document(path, policy or observation_policy())
    return path


def execute(
    directory: Path,
    *,
    event_window: bool = False,
    iteration: int = 1,
    evaluated_at_ms: int = EVALUATED_AT_MS,
    policy_path: Path | None = None,
    receipt_name: str | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    paths = input_paths(
        directory,
        evaluated_at_ms=evaluated_at_ms,
        event_window=event_window,
    )
    selected_policy_path = (
        policy_path or write_policy(directory / "policy.json"))
    return runner.run_shadow_iteration(
        campaign_id=CAMPAIGN_ID,
        iteration=iteration,
        evaluated_at_ms=evaluated_at_ms,
        policy_path=selected_policy_path,
        strategy_path=STRATEGY_PATH,
        snapshot_path=paths["snapshot"],
        quote_history_path=paths["quote_history"],
        bar_history_path=paths["bar_history"],
        calendar_path=paths["calendar"],
        information_path=paths["information"],
        receipt_path=directory / (
            receipt_name or f"receipt-{iteration:04d}.json"),
        state_path=state_path or directory / "state.json",
    )


class StrategyShadowRunnerTests(unittest.TestCase):
    def temporary(self) -> Path:
        context = tempfile.TemporaryDirectory(
            prefix="hepta-strategy-shadow-runner-")
        self.addCleanup(context.cleanup)
        return Path(context.name)

    def assert_policy_rejection_without_outputs(
        self,
        *,
        policy: dict[str, Any],
        iteration: int,
        evaluated_at_ms: int,
        reason: str,
    ) -> None:
        directory = self.temporary()
        policy_path = write_policy(directory / "policy.json", policy)
        receipt_path = directory / "receipt.json"
        state_path = directory / "state.json"
        missing = directory / "market-input-must-not-be-read.json"
        with self.assertRaisesRegex(ContractError, reason):
            runner.run_shadow_iteration(
                campaign_id=CAMPAIGN_ID,
                iteration=iteration,
                evaluated_at_ms=evaluated_at_ms,
                policy_path=policy_path,
                strategy_path=STRATEGY_PATH,
                snapshot_path=missing,
                quote_history_path=missing,
                bar_history_path=missing,
                calendar_path=missing,
                information_path=missing,
                receipt_path=receipt_path,
                state_path=state_path,
            )
        self.assertFalse(receipt_path.exists())
        self.assertFalse(state_path.exists())

    def test_trade_receipt_is_canonical_bound_and_zero_authority(self) -> None:
        directory = self.temporary()
        result = execute(directory)
        receipt = result["receipt"]
        state = result["state"]
        validator.validate(receipt)
        self.assertEqual(receipt["decision"], "TRADE")
        self.assertEqual(receipt["final_outcome"], "SHADOW_TRADE")
        self.assertEqual(receipt["trade_intent"]["side"], "BUY")
        self.assertTrue(receipt["paper_only"])
        self.assertTrue(receipt["shadow_only"])
        self.assertFalse(receipt["live_authorized"])
        self.assertFalse(receipt["mutation_attempted"])
        self.assertFalse(receipt["direct_broker_access"])
        self.assertIsNone(receipt["preflight_sha256"])
        self.assertIsNone(receipt["campaign_open_request_id"])
        self.assertIsNone(receipt["campaign_close_request_id"])
        self.assertFalse(state["paper_authorized"])
        self.assertFalse(state["live_authorized"])
        self.assertFalse(state["mutation_attempted"])
        self.assertFalse(state["direct_broker_access"])
        self.assertEqual(state["completed_iterations"], 1)
        self.assertEqual(
            state["schema"], "hepta.strategy-shadow-state.v2")
        policy_path = directory / "policy.json"
        policy = json.loads(policy_path.read_text(encoding="ascii"))
        self.assertEqual(state["policy_sha256"], digest_file(policy_path))
        self.assertEqual(
            state["policy_body_sha256"], policy["body_sha256"])
        self.assertEqual(
            state["campaign_sha256"], policy["campaign_sha256"])
        self.assertIn(state["policy_sha256"], receipt["evidence_refs"])
        self.assertIn(state["campaign_sha256"], receipt["evidence_refs"])
        self.assertEqual(state["valid_after_ms"], EVALUATED_AT_MS)
        self.assertEqual(
            state["expires_at_ms"],
            EVALUATED_AT_MS + MAXIMUM_ITERATIONS * SLOT_INTERVAL_MS,
        )
        self.assertEqual(state["slot_interval_ms"], SLOT_INTERVAL_MS)
        self.assertEqual(
            state["maximum_iterations"], MAXIMUM_ITERATIONS)
        self.assertEqual(
            state["maximum_lateness_ms"], MAXIMUM_LATENESS_MS)
        self.assertEqual(state["last_scheduled_at_ms"], EVALUATED_AT_MS)
        self.assertEqual(state["last_evaluated_at_ms"], EVALUATED_AT_MS)
        receipt_path = directory / "receipt-0001.json"
        state_path = directory / "state.json"
        self.assertEqual(receipt_path.read_bytes(), canonical_bytes(receipt))
        self.assertEqual(
            json.loads(state_path.read_text(encoding="ascii")), state)
        self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)

    def test_high_impact_event_is_canonical_no_trade(self) -> None:
        result = execute(self.temporary(), event_window=True)
        receipt = result["receipt"]
        validator.validate(receipt)
        self.assertEqual(receipt["decision"], "NO_TRADE")
        self.assertEqual(receipt["final_outcome"], "NO_TRADE")
        self.assertIsNone(receipt["cycle_id"])
        self.assertIsNone(receipt["trade_intent"])
        self.assertIsNone(receipt["trade_intent_sha256"])
        self.assertIn(
            "HIGH_IMPACT_EVENT_WINDOW", receipt["reason_codes"])
        self.assertEqual(
            receipt["risk_challenges"], receipt["reason_codes"])

    def test_same_iteration_is_idempotent_and_receipt_is_not_rewritten(
            self) -> None:
        directory = self.temporary()
        first = execute(directory)
        receipt_path = directory / "receipt-0001.json"
        first_stat = receipt_path.stat()
        second = execute(directory)
        second_stat = receipt_path.stat()
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["receipt"], second["receipt"])
        self.assertEqual(first_stat.st_ino, second_stat.st_ino)
        self.assertEqual(first_stat.st_mtime_ns, second_stat.st_mtime_ns)

    def test_iteration_gap_fails_without_state_change(self) -> None:
        directory = self.temporary()
        execute(directory)
        state_path = directory / "state.json"
        before = state_path.read_bytes()
        evaluated_at_ms = EVALUATED_AT_MS + 2 * SLOT_INTERVAL_MS
        paths = input_paths(
            directory,
            evaluated_at_ms=evaluated_at_ms,
        )
        with self.assertRaisesRegex(
                ContractError, "SHADOW_ITERATION_SEQUENCE_INVALID"):
            runner.run_shadow_iteration(
                campaign_id=CAMPAIGN_ID,
                iteration=3,
                evaluated_at_ms=evaluated_at_ms,
                policy_path=directory / "policy.json",
                strategy_path=STRATEGY_PATH,
                snapshot_path=paths["snapshot"],
                quote_history_path=paths["quote_history"],
                bar_history_path=paths["bar_history"],
                calendar_path=paths["calendar"],
                information_path=paths["information"],
                receipt_path=directory / "receipt-0003.json",
                state_path=state_path,
            )
        self.assertEqual(state_path.read_bytes(), before)
        self.assertFalse((directory / "receipt-0003.json").exists())

    def test_policy_rejects_early_late_expired_and_over_maximum_before_inputs(
            self) -> None:
        policy = observation_policy()
        cases = (
            (
                policy,
                1,
                EVALUATED_AT_MS - 1,
                "SHADOW_SLOT_EARLY",
            ),
            (
                policy,
                1,
                EVALUATED_AT_MS + MAXIMUM_LATENESS_MS + 1,
                "SHADOW_SLOT_LATE",
            ),
            (
                policy,
                1,
                policy["expires_at_ms"],
                "SHADOW_POLICY_EXPIRED",
            ),
            (
                observation_policy(maximum_iterations=1),
                2,
                EVALUATED_AT_MS,
                "SHADOW_ITERATION_OVER_MAXIMUM",
            ),
        )
        for selected_policy, iteration, evaluated_at_ms, reason in cases:
            with self.subTest(reason=reason):
                self.assert_policy_rejection_without_outputs(
                    policy=selected_policy,
                    iteration=iteration,
                    evaluated_at_ms=evaluated_at_ms,
                    reason=reason,
                )

    def test_policy_body_tamper_is_rejected_before_market_inputs(self) -> None:
        policy = observation_policy()
        policy["strategy_version"] = "2.0.1"
        self.assert_policy_rejection_without_outputs(
            policy=policy,
            iteration=1,
            evaluated_at_ms=EVALUATED_AT_MS,
            reason="SHADOW_POLICY_BODY_DIGEST_MISMATCH",
        )

    def test_valid_policy_drift_fails_without_receipt_or_state_change(
            self) -> None:
        directory = self.temporary()
        execute(directory)
        state_path = directory / "state.json"
        first_receipt_path = directory / "receipt-0001.json"
        policy_path = directory / "policy.json"
        state_before = state_path.read_bytes()
        receipt_before = first_receipt_path.read_bytes()
        write_policy(
            policy_path,
            observation_policy(maximum_lateness_ms=90_000),
        )
        with self.assertRaisesRegex(
                ContractError, "SHADOW_STATE_BINDING_INVALID"):
            execute(
                directory,
                iteration=2,
                evaluated_at_ms=EVALUATED_AT_MS + SLOT_INTERVAL_MS,
                policy_path=policy_path,
                receipt_name="receipt-0002.json",
                state_path=state_path,
            )
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertEqual(first_receipt_path.read_bytes(), receipt_before)
        self.assertFalse((directory / "receipt-0002.json").exists())

    def test_mid_iteration_policy_drift_fails_before_publication(self) -> None:
        directory = self.temporary()
        policy_path = write_policy(directory / "policy.json")
        paths = input_paths(directory)
        receipt_path = directory / "receipt.json"
        state_path = directory / "state.json"
        original_build_packet = runner.context_builder.build_packet

        def build_then_drift(*args: Any, **kwargs: Any) -> dict[str, Any]:
            packet = original_build_packet(*args, **kwargs)
            write_policy(
                policy_path,
                observation_policy(maximum_lateness_ms=120_000),
            )
            return packet

        with mock.patch.object(
                runner.context_builder,
                "build_packet",
                side_effect=build_then_drift):
            with self.assertRaisesRegex(
                    ContractError, "SHADOW_POLICY_DRIFT"):
                runner.run_shadow_iteration(
                    campaign_id=CAMPAIGN_ID,
                    iteration=1,
                    evaluated_at_ms=EVALUATED_AT_MS,
                    policy_path=policy_path,
                    strategy_path=STRATEGY_PATH,
                    snapshot_path=paths["snapshot"],
                    quote_history_path=paths["quote_history"],
                    bar_history_path=paths["bar_history"],
                    calendar_path=paths["calendar"],
                    information_path=paths["information"],
                    receipt_path=receipt_path,
                    state_path=state_path,
                )
        self.assertFalse(receipt_path.exists())
        self.assertFalse(state_path.exists())

    def test_validator_failure_prevents_receipt_and_state_commit(self) -> None:
        directory = self.temporary()
        paths = input_paths(directory)
        policy_path = write_policy(directory / "policy.json")
        receipt_path = directory / "receipt.json"
        state_path = directory / "state.json"
        with mock.patch.object(
                runner.receipt_validator,
                "validate",
                side_effect=ContractError("INJECTED_VALIDATOR_REJECTION")):
            with self.assertRaisesRegex(
                    ContractError, "INJECTED_VALIDATOR_REJECTION"):
                runner.run_shadow_iteration(
                    campaign_id=CAMPAIGN_ID,
                    iteration=1,
                    evaluated_at_ms=EVALUATED_AT_MS,
                    policy_path=policy_path,
                    strategy_path=STRATEGY_PATH,
                    snapshot_path=paths["snapshot"],
                    quote_history_path=paths["quote_history"],
                    bar_history_path=paths["bar_history"],
                    calendar_path=paths["calendar"],
                    information_path=paths["information"],
                    receipt_path=receipt_path,
                    state_path=state_path,
                )
        self.assertFalse(receipt_path.exists())
        self.assertFalse(state_path.exists())

    def test_validator_rejects_shadow_boundary_and_intent_tampering(
            self) -> None:
        receipt = execute(self.temporary())["receipt"]
        mutations = []
        for field, value in (
                ("live_authorized", True),
                ("shadow_only", False),
                ("mutation_attempted", True),
                ("direct_broker_access", True),
                ("campaign_open_request_id", "forbidden-request")):
            changed = copy.deepcopy(receipt)
            changed[field] = value
            mutations.append(changed)
        changed = copy.deepcopy(receipt)
        changed["trade_intent"]["quantity"] = 2
        mutations.append(changed)
        changed = copy.deepcopy(receipt)
        changed["trade_intent"]["limit_price"] += 0.0001
        changed["trade_intent_sha256"] = (
            "sha256:" + "0" * 64)
        mutations.append(changed)
        for changed_receipt in mutations:
            with self.subTest(changed=changed_receipt):
                with self.assertRaises(ContractError):
                    validator.validate(changed_receipt)

    def test_policy_bound_validator_requires_policy_and_campaign_evidence(
            self) -> None:
        directory = self.temporary()
        result = execute(directory)
        receipt = copy.deepcopy(result["receipt"])
        state = result["state"]
        receipt["evidence_refs"].remove(state["policy_sha256"])
        validator.validate(receipt)
        with self.assertRaisesRegex(
                ContractError,
                "RECEIPT_OBSERVATION_POLICY_BINDING_INVALID"):
            validator.validate_observation_policy_binding(
                receipt,
                policy_sha256=state["policy_sha256"],
                campaign_sha256=state["campaign_sha256"],
            )

    def test_identical_inputs_produce_byte_identical_receipts(self) -> None:
        first_directory = self.temporary()
        second_directory = self.temporary()
        execute(first_directory)
        execute(second_directory)
        self.assertEqual(
            (first_directory / "receipt-0001.json").read_bytes(),
            (second_directory / "receipt-0001.json").read_bytes(),
        )

    def test_cli_fails_closed_to_no_trade_for_legacy_provenance(self) -> None:
        directory = self.temporary()
        paths = input_paths(directory, attested=False)
        policy = write_policy(directory / "policy.json")
        receipt = directory / "cli-receipt.json"
        state = directory / "cli-state.json"
        command = [
            sys.executable,
            str(SCRIPTS / "hepta_strategy_shadow_runner.py"),
            "--campaign-id", CAMPAIGN_ID,
            "--iteration", "1",
            "--evaluated-at-ms", str(EVALUATED_AT_MS),
            "--policy", str(policy),
            "--strategy", str(STRATEGY_PATH),
            "--snapshot", str(paths["snapshot"]),
            "--quote-history", str(paths["quote_history"]),
            "--bar-history", str(paths["bar_history"]),
            "--calendar", str(paths["calendar"]),
            "--information", str(paths["information"]),
            "--receipt-output", str(receipt),
            "--state", str(state),
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "decision=NO_TRADE iteration=1 idempotent=false",
            completed.stdout,
        )
        validated = subprocess.run(
            [
                sys.executable,
                str(
                    SCRIPTS /
                    "validate_hepta_strategy_decision_receipt.py"),
                str(receipt),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertIn(
            "validate_hepta_strategy_decision_receipt: PASS",
            validated.stdout,
        )

    def test_runner_source_has_no_execution_or_network_surface(self) -> None:
        source = (
            SCRIPTS / "hepta_strategy_shadow_runner.py"
        ).read_text(encoding="utf-8").lower()
        for forbidden in (
                "campaignctl", "heptactl", "preview", "systemctl",
                "sudo", "ibapi", "xtquant", "subprocess", "socket"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
