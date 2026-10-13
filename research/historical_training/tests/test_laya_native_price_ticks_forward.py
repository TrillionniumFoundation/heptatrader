"""New future-only Native/Numeric price tick canary, with no legacy repair."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training.common import canonical, read, sha
from historical_training import laya_native_price_ticks_forward as p
from historical_training import laya_native_price_forward as legacy
from laya_cell.integrity import ContractError
from historical_training.native_score_ticks import PROFILE


BASE = 1900000000000


class NativeTickForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "feed"
        self.source.mkdir(mode=0o700)
        self.output = self.root / "new-ticks"
        self.baseline = self.root / "baseline"
        self.baseline.mkdir(mode=0o700)
        self.period = self.root / "period.json"
        self.period.write_bytes(b'{"domain":"PAPER"}\n')
        self.binary = self.root / "native.bin"
        self.binary.write_bytes(b"test-only-original-native-bytes")
        model = {
            "model_id": "a" * 64,
            "effect_authority": False,
            "trained_until_ms": BASE - 86400000,
            "weights": [0.01] * 562,
            "generation": 42,
        }
        self.model_file = self.baseline / "model.json"
        self.model_file.write_bytes(canonical(model) + b"\n")
        (self.baseline / "freeze-manifest.json").write_bytes(
            canonical({"inventory_sha256": {"model.json": sha(self.model_file)}})
            + b"\n")
        self.c = {
            "period_path": str(self.period),
            "period_sha256": sha(self.period),
            "feed_directory": str(self.source),
            "completed_history_sources": [],
            "baseline_directory": str(self.baseline),
            "native_binary": str(self.binary),
            "native_binary_sha256": sha(self.binary),
        }
        self.config = self.root / "config.json"
        self.config.write_bytes(canonical(self.c) + b"\n")
        self.slot = BASE + 120000
        self.sample = {
            "sample_id": "b" * 64,
            "anchor_ms": self.slot + 200,
            "known_at_ms": self.slot + 400,
            "native_episode": {"rows": [[0.] * 16]},
            "raw_features": [0.] * 16,
        }
        self.feature = {"state": {"time": self.sample["anchor_ms"],
                                  "event": "PAPER_FX_ORIGINAL"}}
        self.quote = {"t": self.sample["anchor_ms"],
                      "known_at_ms": self.slot + 300,
                      "receipt": "c"*64, "bid": 1.1, "ask": 1.1001}
        self.prefixes = [{"path": "fixture-original-only",
                          "sha256": "f"*64}]
        with patch.object(p, "now_ms", return_value=BASE):
            self.plan = p.register(self.output, self.config, self.slot)

    def clock(self, step=100):
        current = [self.slot + 400]
        def next_clock():
            current[0] += step
            return current[0]
        return next_clock

    def capture(self, *, score=0.6625708695529248, quoted=None):
        with patch.object(p, "now_ms", side_effect=self.clock()), \
             patch.object(p, "read_recent_original_quotes",
                          return_value=(quoted or [self.quote],
                                        self.prefixes)), \
             patch.object(p, "forecast_input",
                          return_value=(self.sample, self.feature)), \
             patch.object(p, "predict_point",
                          return_value=(score, "verified-pinned-native-sha")):
            return p.capture(self.output, self.config)

    def verify(self, *, score=0.6625708695529247, quoted=None):
        with patch.object(p, "verify_source_prefixes", return_value=None), \
             patch.object(p, "read_quotes",
                          return_value=(quoted or [self.quote],
                                        self.prefixes)), \
             patch.object(p, "forecast_input",
                          return_value=(self.sample, self.feature)), \
             patch.object(p, "predict_point",
                          return_value=(score, "verified-pinned-native-sha")):
            return p.verify(self.output, self.config)

    def test_new_preregistered_score_quantization_replays_one_ulp(self):
        receipt = self.capture()
        result = self.verify()
        self.assertEqual(receipt["future_fixed_score_profile"], PROFILE)
        self.assertTrue(result["new_profile_score_reproduced"])
        self.assertTrue(result["original_source_rechecked"])
        self.assertFalse(result["original_f64_bitwise_equal_attested"])
        self.assertFalse(result["old_frozen_plan_qualified"])
        self.assertEqual(result["broker_orders_sent"], 0)
        pred = read(self.output / "prediction.json")
        self.assertEqual(pred["future_fixed_score"]["scaled_bps_ticks"], 66257087)
        self.assertFalse(pred["strict_legacy_f64_replay_upgrade"])
        self.assertTrue(receipt["prediction_sealed_before_deadline"])

    def test_replays_three_ulp_without_retroactive_f64_equivalence(self):
        self.capture(score=0.6690629418227435)
        result = self.verify(score=0.6690629418227438)
        self.assertTrue(result["new_profile_score_reproduced"])
        self.assertFalse(result["original_f64_bitwise_equal_attested"])

    def test_model_drift_larger_than_tick_fails(self):
        self.capture()
        with self.assertRaisesRegex(
                ContractError, "NATIVE_SCORE_CANONICAL_REPLAY_MISMATCH"):
            self.verify(score=0.66257098)

    def test_legacy_original_f64_canary_cannot_load_new_plan(self):
        self.capture()
        with self.assertRaisesRegex(
                ContractError, "NATIVE_FUTURE_FROZEN_PLAN_INVALID"):
            legacy._plan(self.output)

    def test_second_capture_and_duplicate_root_are_refused(self):
        self.capture()
        with self.assertRaisesRegex(
                ContractError, "NATIVE_TICKS_CAPTURE_ALREADY_ATTEMPTED"):
            p.capture(self.output, self.config)
        with patch.object(p, "now_ms", return_value=BASE):
            with self.assertRaises(Exception):
                p.register(self.output, self.config, self.slot)

    def test_early_preflight_cannot_run_original_source(self):
        with patch.object(p, "now_ms", return_value=BASE):
            with self.assertRaisesRegex(
                    ContractError, "NATIVE_TICKS_SLOT_NOT_DUE"):
                p.capture(self.output, self.config)

    def test_registration_after_start_is_refused(self):
        with patch.object(p, "now_ms", return_value=self.slot - 1000):
            with self.assertRaisesRegex(
                    ContractError, "NATIVE_TICKS_NOT_PREDECLARED_AHEAD"):
                p.register(self.root / "late", self.config, self.slot)

    def test_native_model_changed_after_frozen_plan_refuses_capture(self):
        data = read(self.model_file)
        data["weights"][0] = 0.2
        self.model_file.write_bytes(canonical(data) + b"\n")
        with self.assertRaisesRegex(
                ContractError, "BASELINE_FREEZE_CHANGED"):
            p.capture(self.output, self.config)

    def test_source_module_change_after_plan_is_refused(self):
        with patch.object(p, "inventory", return_value={"fake": "0"*64}):
            with self.assertRaisesRegex(
                    ContractError, "NATIVE_TICKS_FROZEN_PLAN_OR_PROFILE_CHANGED"):
                p._plan(self.output)

    def test_source_chain_identity_change_denies_replay(self):
        self.capture()
        altered = [{**self.quote, "receipt": "e"*64}]
        with self.assertRaisesRegex(
                ContractError, "NATIVE_TICKS_ORIGINAL_PRICE_SOURCE_CHANGED"):
            self.verify(quoted=altered)

    def test_sealed_prediction_tamper_denies_replay(self):
        self.capture()
        altered = read(self.output / "prediction.json")
        altered["future_fixed_score"]["scaled_bps_ticks"] += 1
        out = self.output / "prediction.json"
        out.chmod(0o600)
        out.write_bytes(canonical(altered) + b"\n")
        with self.assertRaisesRegex(
                ContractError, "NATIVE_TICKS_ORIGINAL_RECEIPT_CHAIN_INVALID"):
            self.verify()

    def test_rehashed_model_trade_grant_in_plan_is_refused(self):
        plan = read(self.output / "plan.json")
        plan["no_trade_policy"] = "BUY_NOW"
        from laya_cell.integrity import digest
        plan["plan_id"] = digest({k: v for k, v in plan.items()
                                  if k != "plan_id"})
        (self.output / "plan.json").chmod(0o600)
        (self.output / "plan.json").write_bytes(canonical(plan) + b"\n")
        with self.assertRaisesRegex(
                ContractError, "NATIVE_TICKS_FROZEN_PLAN_OR_PROFILE_CHANGED"):
            p._plan(self.output)

    def test_score_replay_exactly_differs_by_one_tick_refused(self):
        self.capture()
        record = read(self.output / "prediction.json")["future_fixed_score"]
        from historical_training.native_score_ticks import verify_future_score_replay
        with self.assertRaisesRegex(
                ContractError, "NATIVE_SCORE_CANONICAL_REPLAY_MISMATCH"):
            verify_future_score_replay(record, .66257088)

    def test_nonfinite_model_output_cannot_seal(self):
        with patch.object(p, "time") as mocked_time:
            # Avoid any network or actual sleep; bounded fake time advances.
            mocked_time.sleep.return_value = None
            self.capture_invalid_score()

    def capture_invalid_score(self):
        with patch.object(p, "now_ms", side_effect=self.clock(step=3000)), \
             patch.object(p, "read_recent_original_quotes",
                          return_value=([self.quote], self.prefixes)), \
             patch.object(p, "forecast_input",
                          return_value=(self.sample, self.feature)), \
             patch.object(p, "predict_point",
                          return_value=(float("nan"), "native-sha")):
            with self.assertRaisesRegex(
                    ContractError, "NATIVE_TICKS_SLOT_MISSED"):
                p.capture(self.output, self.config)
        self.assertFalse((self.output / "prediction.json").exists())
        miss = read(self.output / "failure.json")
        self.assertFalse(miss["prediction_fabricated"])
        self.assertEqual(miss["broker_orders_sent"], 0)

    def test_missing_original_quote_misses_instead_of_zero(self):
        with patch.object(p, "now_ms", side_effect=self.clock(step=3000)), \
             patch.object(p, "read_recent_original_quotes",
                          side_effect=ContractError("SOURCE_UNAVAILABLE")), \
             patch.object(p.time, "sleep", return_value=None):
            with self.assertRaisesRegex(
                    ContractError, "NATIVE_TICKS_SLOT_MISSED"):
                p.capture(self.output, self.config)
        self.assertFalse((self.output / "input.json").exists())
        self.assertFalse((self.output / "prediction.json").exists())
        self.assertTrue((self.output / "failure.json").is_file())
