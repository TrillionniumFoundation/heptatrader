from __future__ import annotations

from decimal import Decimal
import hashlib
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

from research.run_protocol import (
    EventLog,
    ResearchProtocolError,
    evaluate_run,
    sample_quotes,
    sample_run_manifest,
    sample_targets,
    self_test,
    validate_folds,
    validate_static_manifest,
)


class ResearchProtocolTests(unittest.TestCase):
    def test_repository_manifest_is_capability_free(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        validate_static_manifest(manifest, root)

    def test_static_manifest_rejects_nested_capability_and_escape(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        manifest["validation"]["nested"] = {"preview_permit": "forbidden"}
        with self.assertRaises(ResearchProtocolError) as raised:
            validate_static_manifest(manifest, root)
        self.assertEqual(raised.exception.code, "RESEARCH_CAPABILITY_FORBIDDEN")

        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        manifest["strategy"]["definition"] = "..\\outside.json"
        with self.assertRaises(ResearchProtocolError) as raised:
            validate_static_manifest(manifest, root)
        self.assertEqual(raised.exception.code, "RESEARCH_STRATEGY_INPUT_MISSING")

        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        manifest["campaign_id"] = "historical-only"
        with self.assertRaises(ResearchProtocolError) as raised:
            validate_static_manifest(manifest, root)
        self.assertEqual(raised.exception.code, "RESEARCH_CEREMONY_FORBIDDEN")

        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        manifest["strategy"]["definition"] = str(root / "research/strategy-definition-v1.json")
        with self.assertRaises(ResearchProtocolError) as raised:
            validate_static_manifest(manifest, root)
        self.assertEqual(raised.exception.code, "RESEARCH_STRATEGY_INPUT_MISSING")

    def test_fixture_is_deterministic(self) -> None:
        first = self_test()
        second = self_test()
        self.assertEqual(first, second)
        self.assertTrue(first["output_digest"].startswith("sha256:"))
        for field in (
            "input_digest",
            "data_quality",
            "exposure",
            "tail_loss",
            "cost_share",
            "capacity",
            "time_in_market",
            "slices",
            "worst_slice",
            "concentration",
            "validation",
            "failures",
            "digests",
            "regime_slices",
            "walk_forward",
        ):
            self.assertIn(field, first)

    def test_input_and_manifest_digests_bind_semantic_changes(self) -> None:
        baseline = evaluate_run(
            sample_run_manifest(), sample_quotes(), sample_targets()
        )

        changed_target = sample_targets()
        changed_target[0]["target_position"] = "100001"
        changed = evaluate_run(
            sample_run_manifest(), sample_quotes(), changed_target
        )
        self.assertNotEqual(changed["input_digest"], baseline["input_digest"])
        self.assertNotEqual(changed["output_digest"], baseline["output_digest"])

        changed_manifest = sample_run_manifest()
        changed_manifest["costs"]["slippage_bps"] = "0.3"
        changed_cost = evaluate_run(
            changed_manifest, sample_quotes(), sample_targets()
        )
        self.assertNotEqual(
            changed_cost["manifest_digest"], baseline["manifest_digest"]
        )
        self.assertNotEqual(changed_cost["output_digest"], baseline["output_digest"])

    def test_changed_duplicate_quote_fails_closed(self) -> None:
        quotes = sample_quotes()
        quotes.insert(1, {"ts_ms": 1000, "bid": "1.1001", "ask": "1.1002"})
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(sample_run_manifest(), quotes, sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_CHANGED_DUPLICATE_TIMESTAMP")

    def test_decision_cannot_require_future_quote(self) -> None:
        targets = [{"ts_ms": 500, "instrument": "EUR.USD", "target_position": "1"}]
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(sample_run_manifest(), sample_quotes(), targets)
        self.assertEqual(raised.exception.code, "RESEARCH_LOOKAHEAD_REQUIRED")

    def test_noop_target_does_not_require_future_fill_quote(self) -> None:
        manifest = sample_run_manifest()
        manifest["costs"]["decision_to_fill_delay_ms"] = 60_000
        summary = evaluate_run(
            manifest,
            sample_quotes(),
            [{"ts_ms": 3_000, "instrument": "EUR.USD", "target_position": "0"}],
        )
        self.assertEqual(summary["trade_count"], 0)
        self.assertEqual(summary["final_positions"], {"EUR.USD": "0"})

    def test_purge_and_embargo_cover_horizon(self) -> None:
        folds = sample_run_manifest()["folds"]
        with self.assertRaises(ResearchProtocolError) as raised:
            validate_folds(folds, 1000, 2000, 1000, 2000)
        self.assertEqual(raised.exception.code, "RESEARCH_PURGE_EMBARGO_INSUFFICIENT")

    def test_adverse_costs_reduce_result(self) -> None:
        normal = evaluate_run(sample_run_manifest(), sample_quotes(), sample_targets())
        adverse = evaluate_run(
            sample_run_manifest(adverse=True), sample_quotes(), sample_targets())
        self.assertLess(Decimal(adverse["net_pnl"]), Decimal(normal["net_pnl"]))

    def test_optional_fill_costs_are_applied_not_just_reported(self) -> None:
        baseline = evaluate_run(sample_run_manifest(), sample_quotes(), sample_targets())
        manifest = sample_run_manifest()
        manifest["costs"].update({"spread_bps": "5", "fee_bps": "10"})
        stressed = evaluate_run(manifest, sample_quotes(), sample_targets())
        self.assertGreater(Decimal(stressed["explicit_cost"]), Decimal(baseline["explicit_cost"]))
        self.assertLess(Decimal(stressed["net_pnl"]), Decimal(baseline["net_pnl"]))
        self.assertEqual(stressed["cost_model"]["spread_application"], "additive_adverse_fill_bps")
        self.assertEqual(stressed["cost_model"]["fee_application"], "per_fill_notional_bps")

    def test_annualized_borrow_and_funding_costs_reduce_pnl(self) -> None:
        year_ms = 365 * 24 * 60 * 60 * 1000
        quotes = [
            {"ts_ms": 1000, "instrument": "EUR.USD", "bid": "100", "ask": "100"},
            {"ts_ms": 1000 + year_ms, "instrument": "EUR.USD", "bid": "100", "ask": "100"},
        ]
        targets = [{"ts_ms": 1000, "instrument": "EUR.USD", "target_position": "-1"}]
        baseline_manifest = sample_run_manifest()
        baseline_manifest["max_quote_age_ms"] = year_ms + 1000
        baseline = evaluate_run(baseline_manifest, quotes, targets)
        charged_manifest = sample_run_manifest()
        charged_manifest["max_quote_age_ms"] = year_ms + 1000
        charged_manifest["costs"].update({"borrow_bps": "365", "funding_bps": "100"})
        charged = evaluate_run(charged_manifest, quotes, targets)
        self.assertEqual(charged["holding_cost"], "4.65")
        self.assertLess(Decimal(charged["net_pnl"]), Decimal(baseline["net_pnl"]))
        self.assertEqual(charged["cost_model"]["borrow_application"], "annualized_short_exposure_bps")
        self.assertEqual(charged["cost_model"]["funding_application"], "annualized_gross_exposure_bps")

    def test_time_in_market_accounts_for_fill_between_quotes(self) -> None:
        manifest = sample_run_manifest()
        manifest["costs"]["decision_to_fill_delay_ms"] = 1000
        manifest["max_quote_age_ms"] = 5000
        quotes = [
            {"ts_ms": 1000, "instrument": "A", "bid": "1", "ask": "1"},
            {"ts_ms": 3000, "instrument": "A", "bid": "1", "ask": "1"},
        ]
        targets = [{"ts_ms": 1000, "instrument": "A", "target_position": "1"}]
        summary = evaluate_run(manifest, quotes, targets)
        self.assertEqual(summary["time_in_market"], "0.5")

    def test_capacity_violation_fails_closed(self) -> None:
        manifest = sample_run_manifest()
        manifest["capacity"]["max_order_quantity"] = "10"
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(manifest, sample_quotes(), sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_CAPACITY_EXCEEDED")

    def test_final_out_of_sample_boundaries_are_required(self) -> None:
        manifest = sample_run_manifest()
        del manifest["final_out_of_sample"]
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(manifest, sample_quotes(), sample_targets())
        self.assertEqual(raised.exception.code, "RUN_MANIFEST_VALIDATION_INVALID")

    def test_final_out_of_sample_aliases_cannot_disagree(self) -> None:
        manifest = sample_run_manifest()
        manifest["final_out_of_sample"]["start_ts_ms"] = 31_001
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(manifest, sample_quotes(), sample_targets())
        self.assertEqual(raised.exception.code, "RUN_MANIFEST_VALIDATION_INVALID")

    def test_fractional_timestamp_is_not_silently_truncated(self) -> None:
        quotes = sample_quotes()
        quotes[0]["ts_ms"] = 1000.5
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(sample_run_manifest(), quotes, sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_INTEGER_INVALID")

    def test_nonfinite_quote_fails_closed(self) -> None:
        quotes = sample_quotes()
        quotes[0]["bid"] = "NaN"
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(sample_run_manifest(), quotes, sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_DECIMAL_NONFINITE")

    def test_malformed_target_returns_protocol_error(self) -> None:
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(sample_run_manifest(), sample_quotes(), [None])
        self.assertEqual(raised.exception.code, "RESEARCH_TARGET_INVALID")

    def test_multi_instrument_equal_timestamp_is_independent(self) -> None:
        manifest = sample_run_manifest()
        quotes = [
            {"ts_ms": 1000, "instrument": "EUR.USD", "bid": "1.1000", "ask": "1.1002"},
            {"ts_ms": 1000, "instrument": "GBP.USD", "bid": "1.2500", "ask": "1.2502"},
            {"ts_ms": 2000, "instrument": "EUR.USD", "bid": "1.1010", "ask": "1.1012"},
            {"ts_ms": 2000, "instrument": "GBP.USD", "bid": "1.2510", "ask": "1.2512"},
        ]
        targets = [
            {"ts_ms": 1000, "instrument": "EUR.USD", "target_position": "1"},
            {"ts_ms": 1000, "instrument": "GBP.USD", "target_position": "1"},
        ]
        summary = evaluate_run(manifest, quotes, targets)
        self.assertEqual(summary["final_positions"], {"EUR.USD": "1", "GBP.USD": "1"})
        self.assertEqual(summary["data_quality"]["target_count"], 2)
        self.assertEqual(summary["event_count"], 8)

    def test_multi_instrument_books_may_be_interleaved(self) -> None:
        manifest = sample_run_manifest()
        quotes = [
            {"ts_ms": 1000, "instrument": "A", "bid": "1", "ask": "1.1"},
            {"ts_ms": 2000, "instrument": "A", "bid": "1.1", "ask": "1.2"},
            {"ts_ms": 1000, "instrument": "B", "bid": "2", "ask": "2.1"},
            {"ts_ms": 2000, "instrument": "B", "bid": "2.1", "ask": "2.2"},
        ]
        targets = [
            {"ts_ms": 2000, "instrument": "A", "target_position": "1"},
            {"ts_ms": 1000, "instrument": "B", "target_position": "1"},
        ]
        summary = evaluate_run(manifest, quotes, targets)
        self.assertEqual(summary["final_positions"], {"A": "1", "B": "1"})

    def test_same_timestamp_marks_are_atomic_across_instruments(self) -> None:
        manifest = sample_run_manifest()
        quotes = [
            {"ts_ms": 1000, "instrument": "A", "bid": "1", "ask": "1"},
            {"ts_ms": 1000, "instrument": "B", "bid": "2", "ask": "2"},
            {"ts_ms": 2000, "instrument": "A", "bid": "1", "ask": "1"},
            {"ts_ms": 2000, "instrument": "B", "bid": "2", "ask": "2"},
        ]
        targets = [
            {"ts_ms": 1000, "instrument": "A", "target_position": "1"},
            {"ts_ms": 1000, "instrument": "B", "target_position": "1"},
        ]
        summary = evaluate_run(manifest, quotes, targets)
        # With flat marks, the only loss is explicit execution cost; a
        # per-instrument mark loop would invent an additional transient loss.
        self.assertEqual(summary["max_drawdown"], summary["explicit_cost"])

    def test_duplicate_quality_counter_preserves_raw_count(self) -> None:
        quotes = sample_quotes()
        quotes.insert(1, dict(quotes[0]))
        summary = evaluate_run(sample_run_manifest(), quotes, sample_targets())
        self.assertEqual(summary["data_quality"]["raw_quote_count"], 4)
        self.assertEqual(summary["data_quality"]["quote_count"], 3)
        self.assertEqual(summary["data_quality"]["duplicate_quote_count"], 1)

    def test_raw_input_digest_preserves_duplicate_provenance(self) -> None:
        baseline = evaluate_run(
            sample_run_manifest(), sample_quotes(), sample_targets()
        )
        quotes = sample_quotes()
        quotes.insert(1, dict(quotes[0]))
        duplicated = evaluate_run(sample_run_manifest(), quotes, sample_targets())
        self.assertNotEqual(duplicated["raw_input_digest"], baseline["raw_input_digest"])
        # The normalized semantic stream remains unchanged, while the summary
        # still records the quality-counter difference.
        self.assertEqual(duplicated["input_digest"], baseline["input_digest"])
        self.assertNotEqual(duplicated["output_digest"], baseline["output_digest"])

    def test_runtime_capability_fields_are_rejected(self) -> None:
        manifest = sample_run_manifest()
        manifest["nested"] = {"preview_permit": "not-a-permit"}
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(manifest, sample_quotes(), sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_CAPABILITY_FORBIDDEN")

    def test_generic_capability_namespace_is_rejected_in_run_manifest(self) -> None:
        manifest = sample_run_manifest()
        manifest["capability"] = {"enabled": False}
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(manifest, sample_quotes(), sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_CAPABILITY_FORBIDDEN")

    def test_authorization_aliases_are_rejected(self) -> None:
        manifest = sample_run_manifest()
        manifest["live_authorized"] = False
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(manifest, sample_quotes(), sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_CAPABILITY_FORBIDDEN")

    def test_runtime_capability_fields_in_inputs_are_rejected(self) -> None:
        quotes = sample_quotes()
        quotes[0]["session_token"] = "unexpected"
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(sample_run_manifest(), quotes, sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_CAPABILITY_FORBIDDEN")

    def test_unknown_quote_fields_are_not_dropped(self) -> None:
        quotes = sample_quotes()
        quotes[0]["provider_revision"] = "later"
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(sample_run_manifest(), quotes, sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_QUOTE_INVALID")

    def test_unknown_target_fields_are_not_dropped(self) -> None:
        targets = sample_targets()
        targets[0]["feature_value"] = "future"
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(sample_run_manifest(), sample_quotes(), targets)
        self.assertEqual(raised.exception.code, "RESEARCH_TARGET_INVALID")

    def test_installed_runner_requires_source_root_for_static_verify(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary) / "usr/local/share/heptatrader/research"
            install_root.mkdir(parents=True)
            shutil.copy2(root / "research/run_protocol.py", install_root)
            shutil.copy2(root / "research/protocol_support.py", install_root)
            shutil.copy2(root / "research/manifest-v1.json", install_root)
            runner = install_root / "run_protocol.py"
            manifest = install_root / "manifest-v1.json"
            failed = subprocess.run(
                [sys.executable, str(runner), "verify", "--manifest", str(manifest)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(failed.returncode, 2)
            failure = json.loads(failed.stderr)
            self.assertEqual(failure["reason_code"], "RESEARCH_STRATEGY_INPUT_MISSING")
            passed = subprocess.run(
                [
                    sys.executable,
                    str(runner),
                    "verify",
                    "--manifest",
                    str(manifest),
                    "--root",
                    str(root),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual(json.loads(passed.stdout)["status"], "PASS")

            empty_root = subprocess.run(
                [
                    sys.executable,
                    str(runner),
                    "verify",
                    "--manifest",
                    str(manifest),
                    "--root",
                    "",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(empty_root.returncode, 2)
            self.assertEqual(
                json.loads(empty_root.stderr)["reason_code"],
                "RESEARCH_SOURCE_ROOT_INVALID",
            )

    def test_static_manifest_binds_raw_strategy_asset_digests(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            (checkout / "research").mkdir(parents=True)
            for relative in (
                "research/run_protocol.py",
                "research/protocol_support.py",
                "research/strategy-definition-v1.json",
            ):
                destination = checkout / relative
                destination.write_bytes((root / relative).read_bytes())
            definition = checkout / "research/strategy-definition-v1.json"
            definition.write_bytes(definition.read_bytes() + b"\n")
            with self.assertRaises(ResearchProtocolError) as raised:
                validate_static_manifest(manifest, checkout)
            self.assertEqual(raised.exception.code, "RESEARCH_STRATEGY_DIGEST_MISMATCH")

    def test_static_manifest_binds_runner_support_digest(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            (checkout / "research").mkdir(parents=True)
            for relative in (
                "research/run_protocol.py",
                "research/protocol_support.py",
                "research/strategy-definition-v1.json",
            ):
                destination = checkout / relative
                destination.write_bytes((root / relative).read_bytes())
            support = checkout / "research/protocol_support.py"
            support.write_bytes(support.read_bytes() + b"\n")
            with self.assertRaises(ResearchProtocolError) as raised:
                validate_static_manifest(manifest, checkout)
            self.assertEqual(raised.exception.code, "RESEARCH_STRATEGY_DIGEST_MISMATCH")

    def test_static_manifest_rejects_runner_support_escape(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        manifest["runner_support"]["path"] = "..\\protocol_support.py"
        with self.assertRaises(ResearchProtocolError) as raised:
            validate_static_manifest(manifest, root)
        self.assertEqual(raised.exception.code, "RESEARCH_STRATEGY_INPUT_MISSING")

    def test_static_manifest_rejects_historical_script_path(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        manifest["strategy"]["implementation"] = "scripts/hepta_strategy_contracts.py"
        with self.assertRaises(ResearchProtocolError) as raised:
            validate_static_manifest(manifest, root)
        self.assertEqual(raised.exception.code, "RESEARCH_STRATEGY_INPUT_MISSING")

    def test_static_manifest_rejects_capability_descriptor(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            (checkout / "research").mkdir(parents=True)
            for relative in (
                "research/run_protocol.py",
                "research/protocol_support.py",
            ):
                destination = checkout / relative
                destination.write_bytes((root / relative).read_bytes())
            descriptor = checkout / "research/bad-definition.json"
            descriptor.write_text('{"direct_broker_access": false}\n', encoding="utf-8")
            manifest["strategy"]["definition"] = "research/bad-definition.json"
            manifest["strategy_digests"]["definition"] = (
                "sha256:" + hashlib.sha256(descriptor.read_bytes()).hexdigest()
            )
            with self.assertRaises(ResearchProtocolError) as raised:
                validate_static_manifest(manifest, checkout)
            self.assertEqual(raised.exception.code, "RESEARCH_CAPABILITY_FORBIDDEN")

    def test_static_manifest_rejects_legacy_import_in_current_runner(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "research/manifest-v1.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            (checkout / "research").mkdir(parents=True)
            support = checkout / "research/protocol_support.py"
            support.write_bytes((root / "research/protocol_support.py").read_bytes())
            definition = checkout / "research/strategy-definition-v1.json"
            definition.write_bytes((root / "research/strategy-definition-v1.json").read_bytes())
            runner = checkout / "research/bad-runner.py"
            runner.write_text("import hepta_market_context_builder\n", encoding="utf-8")
            manifest["strategy"]["implementation"] = "research/bad-runner.py"
            manifest["strategy_digests"]["implementation"] = (
                "sha256:" + hashlib.sha256(runner.read_bytes()).hexdigest()
            )
            manifest["strategy"]["context_builder"] = "research/bad-runner.py"
            manifest["strategy_digests"]["context_builder"] = manifest["strategy_digests"]["implementation"]
            manifest["strategy"]["replay_evaluator"] = "research/bad-runner.py"
            manifest["strategy_digests"]["replay_evaluator"] = manifest["strategy_digests"]["implementation"]
            with self.assertRaises(ResearchProtocolError) as raised:
                validate_static_manifest(manifest, checkout)
            self.assertEqual(raised.exception.code, "RESEARCH_CEREMONY_FORBIDDEN")

    def test_event_log_allows_distinct_instruments_at_same_timestamp(self) -> None:
        log = EventLog()
        first = log.append("quote", 1000, {"instrument": "A", "bid": "1", "ask": "1.1"})
        second = log.append("quote", 1000, {"instrument": "B", "bid": "2", "ask": "2.1"})
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertIs(
            log.append("quote", 1000, {"instrument": "A", "bid": "1", "ask": "1.1"}),
            first,
        )

    def test_event_payload_is_immutable_after_digest(self) -> None:
        log = EventLog()
        event = log.append("decision", 1000, {"nested": {"values": [1]}})
        digest = log.digest
        with self.assertRaises(TypeError):
            event.payload["nested"] = {}  # type: ignore[index]
        with self.assertRaises(TypeError):
            event.payload["nested"]["values"][0] = 2  # type: ignore[index]
        self.assertEqual(log.digest, digest)

    def test_campaign_and_legacy_ceremony_fields_are_rejected(self) -> None:
        manifest = sample_run_manifest()
        manifest["campaign_id"] = "historical-only"
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(manifest, sample_quotes(), sample_targets())
        self.assertEqual(raised.exception.code, "RESEARCH_CEREMONY_FORBIDDEN")

        targets = sample_targets()
        targets[0]["lease_generation"] = 1
        with self.assertRaises(ResearchProtocolError) as raised:
            evaluate_run(sample_run_manifest(), sample_quotes(), targets)
        self.assertEqual(raised.exception.code, "RESEARCH_CEREMONY_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
