"""Adversarial fixtures for a new, uninstalled, no-order shadow action policy."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from train_v2 import preaction_shadow_policy as p
from laya_cell.shadow_source import SCHEMA as QUOTE_SCHEMA, canonical, digest


class PreactionShadowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.root_context = tempfile.TemporaryDirectory()
        self.addCleanup(self.root_context.cleanup)
        self.root = Path(self.root_context.name)
        os.chmod(self.root, 0o700)
        self.period = self.root / "period.json"
        self.period.write_text('{"execution_domain":"PAPER"}\n')
        self.period.chmod(0o600)
        self.sha = hashlib.sha256(self.period.read_bytes()).hexdigest()
        self.feed = self.root / "feed"
        self.feed.mkdir(mode=0o700)
        self.segment = self.feed / "segment-original-test"
        self.segment.mkdir(mode=0o700)
        self.quotes = self.segment / "quotes.jsonl"
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({
            "schema": "heptatrader.laya-resident-config.v1",
            "period_path": str(self.period),
            "period_sha256": self.sha,
            "feed_directory": str(self.feed),
            "automatic_trading": False,
        }))
        self.config.chmod(0o600)
        self.now = time.time_ns() // 1000000
        self.registry_root = self.root / "research-trial"
        self.registry_root.mkdir(mode=0o700)
        self.plan_path = self.root / "plan.json"
        self.plan = {"plan_id": "fixture-trial", "behavior_policy_id": p.POLICY_ID}
        self.plan_path.write_text(json.dumps(self.plan))
        self.plan_path.chmod(0o600)
        self.test_registration = {
            "plan_id": self.plan["plan_id"],
            "policy_id": p.POLICY_ID,
            "period_sha256": self.sha,
            "instrument": "EUR.USD",
            "sealed_at_ms": self.now - 40000,
            "first_decision_ms": self.now - 1000,
            "last_decision_ms": self.now + 30000,
            "independent_owner_signature": False,
            "canonical_agentd_owner_enrolled": False,
        }
        self.write_quotes()

    def write_quotes(self, *, count=6, bid="1.00001", ask="1.00003",
                     valid=True, prefix_bad=False, period=None):
        records = []
        previous = "0" * 64
        entries = [("HEADER", {"source": "IB_TOOL_GATEWAY",
            "instrument": "EUR.USD", "execution_domain": "PAPER",
            "period_sha256": period or self.sha, "write_operations_allowed": [],
            "broker_orders_sent": 0})]
        for index in range(count):
            at = self.now - 1300 + 180 * index
            entries.append(("QUOTE", {"instrument": "EUR.USD",
                "source": "IB", "authoritative": True, "stale": False,
                "bid": bid, "ask": ask, "observed_at_ms": at,
                "stale_after_ms": at + 5000, "subscription_id": "fixture",
                "subscription_state": "ACTIVE"}))
        for i, (kind, data) in enumerate(entries):
            at = self.now - 1400 if i == 0 else data["observed_at_ms"] + 10
            body = {"schema": QUOTE_SCHEMA, "sequence": i, "previous": previous,
                    "at_ms": at, "kind": kind, "data": data}
            h = digest(body)
            records.append(canonical({**body, "sha256": h}) + b"\n")
            previous = h
        if prefix_bad:
            records[-1] = records[-1].replace(b'"bid":"1.00001"', b'"bid":"1.09999"')
        self.quotes.write_bytes(b"".join(records))
        self.quotes.chmod(0o600)

    def decide(self, *, clock=None, draw=None, claim=False):
        with patch("train_v2.registry.verify_registration",
                   return_value=self.test_registration):
            return p.decide_from_pinned_source(
                self.config, self.quotes,
                registry_root=self.registry_root, plan_path=self.plan_path,
                claims_root=self.registry_root / "shadow-claims" if claim else None,
                clock=clock or (lambda: self.now),
                draw=draw or (lambda: 0),
            )

    def test_same_shadow_episode_cannot_reroll_after_preselection_claim(self):
        first = self.decide(claim=True, draw=lambda: 2)
        self.assertEqual(first["status"], "RESEARCH_SHADOW_PREACTION_ONLY")
        self.assertTrue(first["local_episode_claimed_before_rng"])
        self.assertEqual(first["selected"], "UP_SIGNAL")
        claim_path = (self.registry_root / "shadow-claims" /
                      (first["shadow_episode_id"] + ".json"))
        self.assertTrue(claim_path.is_file())
        self.assertEqual(claim_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(first["local_episode_preselection_claim_digest"],
                         hashlib.sha256(claim_path.read_bytes()).hexdigest())
        denied = self.decide(claim=True, draw=lambda: self.fail("reroll forbidden"))
        self.assertEqual(denied["reason"],
                         "PREACTION_EPISODE_ALREADY_CLAIMED_INDETERMINATE")
        self.assertFalse(denied["candidate_decision_created"])

    def test_process_style_competition_for_same_episode_has_one_winner(self):
        def attempt():
            return p.decide_from_pinned_source(
                self.config, self.quotes,
                registry_root=self.registry_root, plan_path=self.plan_path,
                claims_root=self.registry_root / "shadow-claims",
                clock=lambda: self.now, draw=lambda: 2)

        with patch("train_v2.registry.verify_registration",
                   return_value=self.test_registration):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(x["status"] for x in results), [
            "HOLD_NO_ELIGIBLE_FRESH_SOURCE",
            "RESEARCH_SHADOW_PREACTION_ONLY",
        ])
        self.assertEqual(
            len(list((self.registry_root/"shadow-claims").glob("*.json"))), 1)
        self.assertEqual(
            [x["reason"] for x in results if "reason" in x],
            ["PREACTION_EPISODE_ALREADY_CLAIMED_INDETERMINATE"])

    def test_crash_like_expiry_after_claim_does_not_retry_randomness(self):
        moments = iter((self.now, self.now, self.now + 7000))
        first = self.decide(claim=True, clock=lambda: next(moments), draw=lambda: 3)
        self.assertEqual(first["reason"], "SOURCE_EXPIRED_DURING_SELECTION")
        second = self.decide(claim=True,
                 draw=lambda: self.fail("must not resample indeterminate claim"))
        self.assertEqual(second["reason"],
                         "PREACTION_EPISODE_ALREADY_CLAIMED_INDETERMINATE")

    def test_research_claim_directory_must_match_registered_root(self):
        with patch("train_v2.registry.verify_registration",
                   return_value=self.test_registration):
            with self.assertRaisesRegex(ValueError, "EPISODE_CLAIM_ROOT_UNBOUND"):
                p.decide_from_pinned_source(
                    self.config, self.quotes,
                    registry_root=self.registry_root, plan_path=self.plan_path,
                    claims_root=self.root / "rogue-claims",
                    clock=lambda: self.now,
                    draw=lambda: self.fail("do not draw"))

    def test_missing_prespecified_plan_fails_closed_before_rng(self):
        result = p.decide_from_pinned_source(self.config, self.quotes,
                    clock=lambda: self.now,
                    draw=lambda: self.fail("rng without registration"))
        self.assertEqual(result["reason"], "PREACTION_PLAN_NOT_REGISTERED")
        self.assertFalse(result["candidate_decision_created"])

    def test_bad_prespecified_plan_is_never_positive(self):
        with patch("train_v2.registry.verify_registration",
                   side_effect=ValueError("REGISTRY_PLAN_SEAL_INVALID")):
            value = p.decide_from_pinned_source(
                self.config, self.quotes,
                registry_root=self.registry_root, plan_path=self.plan_path,
                clock=lambda: self.now,
                draw=lambda: self.fail("rng after invalid plan"))
        self.assertEqual(value["reason"],
                         "PREACTION_PLAN_REVALIDATION_FAILED:ValueError")

    def test_invalid_window_or_same_owner_signature_is_rejected(self):
        for change in (
            {"sealed_at_ms": self.now - 29999},
            {"first_decision_ms": self.now + 1},
            {"last_decision_ms": self.now - 1},
            {"independent_owner_signature": True},
            {"canonical_agentd_owner_enrolled": True},
        ):
            with self.subTest(change=change):
                original = self.test_registration
                self.test_registration = dict(original, **change)
                try:
                    value = self.decide(draw=lambda: self.fail("draw after bad window"))
                    self.assertEqual(value["reason"],
                                     "PREACTION_PLAN_WINDOW_OR_AUTHORITY_MISMATCH")
                finally:
                    self.test_registration = original

    def test_actual_two_bit_mapping_and_exact_q32(self):
        expected = {
            0: ("NO_TRADE", 2**31),
            1: ("NO_TRADE", 2**31),
            2: ("UP_SIGNAL", 2**30),
            3: ("DOWN_SIGNAL", 2**30),
        }
        for word, (action, q32) in expected.items():
            with self.subTest(draw=word):
                result = self.decide(draw=lambda v=word: v)
                self.assertEqual(result["status"], "RESEARCH_SHADOW_PREACTION_ONLY")
                self.assertTrue(result["local_prospective_plan_presealed"])
                self.assertEqual(result["local_prospective_plan_id"], "fixture-trial")
                self.assertFalse(result["independent_plan_registration_authority"])
                self.assertEqual(result["selected"], action)
                self.assertEqual(result["selected_propensity_q32_raw"], q32)
                self.assertEqual(result["selection_word_two_bit"], word)
                self.assertEqual(result["omitted_count_bound"], 0)
                self.assertEqual(result["candidate_set_receipt"]["candidate_count"], 3)
                self.assertFalse(result["original_opaque_llm_trading_policy_propensity_qualified"])
                self.assertFalse(result["product_native_ledger_writer_acked"])
                self.assertFalse(result["trade_authority"])
                self.assertGreater(result["outcome_not_available_before_ms"],
                                   result["decision_at_ms"])
                self.assertEqual(result["decision_digest"],
                                 p._sha(p._canonical({k: v for k, v in result.items()
                                                     if k != "decision_digest"})))

    def test_closed_candidate_grammar_is_complete_for_shadow_only(self):
        decision = self.decide(draw=lambda: 2)
        self.assertEqual(decision["candidate_set_receipt"]["grammar"], list(p.CANDIDATES))
        self.assertEqual(decision["candidates"], list(p.CANDIDATES))
        self.assertEqual(decision["selection_weights"],
                         {"NO_TRADE": 2, "UP_SIGNAL": 1, "DOWN_SIGNAL": 1})
        self.assertEqual(sum(decision["selection_weights"].values()),
                         decision["probability_denominator"])
        self.assertEqual(len(decision["source_history_quote_receipts"]), 6)

    def test_spread_hard_filter_abstains_without_rng(self):
        self.write_quotes(ask="1.01100")
        result = self.decide(draw=lambda: self.fail("no stochastic draw on filtered set"))
        self.assertEqual(result["status"], "RESEARCH_SHADOW_PREACTION_ONLY")
        self.assertEqual(result["candidates"], ["NO_TRADE"])
        self.assertEqual(result["selected_propensity_q32_raw"], 2**32)
        self.assertIsNone(result["selection_word_two_bit"])

    def test_less_than_six_quotes_holds(self):
        self.write_quotes(count=5)
        result = self.decide()
        self.assertEqual(result["reason"], "INSUFFICIENT_ORIGINAL_QUOTE_HISTORY")
        self.assertFalse(result["candidate_decision_created"])

    def test_old_original_source_is_not_forward_qualified(self):
        value = self.decide(clock=lambda: self.now + 6000)
        self.assertEqual(value["reason"], "ORIGINAL_SOURCE_EXPIRED")
        self.assertFalse(value["behavior_propensity_attested"])

    def test_source_future_not_admitted(self):
        value = self.decide(clock=lambda: self.now - 2000)
        self.assertEqual(value["reason"], "ORIGINAL_SOURCE_EXPIRED")

    def test_source_changed_hash_rejected(self):
        self.write_quotes(prefix_bad=True)
        result = self.decide()
        self.assertEqual(result["status"], "HOLD_NO_ELIGIBLE_FRESH_SOURCE")
        self.assertEqual(result["reason"], "SOURCE_FAILED_CANONICAL_READ:ValueError")

    def test_original_period_mismatch_rejected(self):
        self.period.write_text('{"execution_domain":"PAPER", "changed":true}\n')
        self.assertEqual(self.decide()["reason"], "ORIGINAL_PERIOD_CHANGED")

    def test_wrong_quote_header_period_rejected(self):
        self.write_quotes(period="f"*64)
        self.assertEqual(self.decide()["status"], "HOLD_NO_ELIGIBLE_FRESH_SOURCE")

    def test_live_trading_config_rejected(self):
        obj = json.loads(self.config.read_text())
        obj["automatic_trading"] = True
        self.config.write_text(json.dumps(obj))
        self.assertEqual(self.decide()["reason"], "SOURCE_NOT_CONFIGURED_FEED")

    def test_symlink_segment_not_admitted(self):
        moved = self.root / "actual"
        self.segment.rename(moved)
        self.segment.symlink_to(moved, target_is_directory=True)
        self.quotes = self.segment / "quotes.jsonl"
        self.assertEqual(self.decide()["reason"], "SOURCE_NOT_CONFIGURED_FEED")

    def test_unpinned_source_path_rejected(self):
        rogue = self.root / "quotes.jsonl"
        rogue.write_bytes(self.quotes.read_bytes())
        rogue.chmod(0o600)
        self.quotes = rogue
        self.assertEqual(self.decide()["reason"], "SOURCE_NOT_CONFIGURED_FEED")

    def test_invalid_rng_fails_not_a_positive_receipt(self):
        for word in (-1, 4, None, True, 1.0):
            with self.subTest(word=word):
                with self.assertRaisesRegex(ValueError, "SAMPLER_NOT_TWO_BIT_UNBIASED"):
                    self.decide(draw=lambda v=word: v)

    def test_no_predecision_backdating_before_complete_source_read(self):
        stamps = iter((self.now - 300, self.now, self.now + 120))
        result = self.decide(clock=lambda: next(stamps), draw=lambda: 2)
        self.assertEqual(result["status"], "RESEARCH_SHADOW_PREACTION_ONLY")
        self.assertEqual(result["source_snapshot_read_finished_at_ms"], self.now)
        self.assertEqual(result["decision_at_ms"], self.now + 120)
        self.assertGreater(result["decision_at_ms"],
                           result["source_snapshot_read_finished_at_ms"])

    def test_window_expiry_between_draw_and_commit_holds(self):
        stamps = iter((self.now, self.now, self.now + 200))
        previous = self.test_registration
        self.test_registration = dict(previous, last_decision_ms=self.now + 100)
        try:
            result = self.decide(clock=lambda: next(stamps), draw=lambda: 2)
            self.assertEqual(result["reason"], "SOURCE_EXPIRED_DURING_SELECTION")
            self.assertFalse(result["candidate_decision_created"])
        finally:
            self.test_registration = previous

    def test_source_expires_while_selecting_rejected(self):
        moments = iter((self.now, self.now, self.now + 7000))
        result = self.decide(clock=lambda: next(moments), draw=lambda: 2)
        self.assertEqual(result["reason"], "SOURCE_EXPIRED_DURING_SELECTION")

    def test_private_write_once_and_duplicate_conflict(self):
        result = self.decide(draw=lambda: 3)
        directory = self.root / "private"
        directory.mkdir(mode=0o700)
        receipt = directory / "candidate.json"
        sha = p.write_once(receipt, result)
        self.assertEqual(sha, hashlib.sha256(receipt.read_bytes()).hexdigest())
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            p.write_once(receipt, result)

    def test_no_broker_library_or_live_driver_linkage(self):
        self.assertNotIn("broker_orders_sent", self.decide(draw=lambda: 2))
        self.assertEqual(p.POLICY_ID, "SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1")
        self.assertEqual(p.MAX_SOURCE_AGE_MS, 1500)
        self.assertEqual(p.DENOMINATOR, 4)

    def test_cli_on_stale_source_writes_hold_not_a_decision(self):
        root = self.root / "stale-output"
        root.mkdir(mode=0o700)
        receipt = root / "hold.json"
        # Artificially old stored event relative to real current-clock CLI.
        self.now -= 15000
        self.write_quotes()
        env = dict(os.environ,
                   PYTHONPATH="research:research/neuron_learning:scripts")
        command = [sys.executable, "-m", "train_v2.preaction_shadow_policy",
                   "--config", str(self.config), "--source-file", str(self.quotes),
                   "--output", str(receipt)]
        first = subprocess.run(command, capture_output=True, text=True,
                               env=env, timeout=15)
        self.assertEqual(first.returncode, 0, first.stderr)
        result = json.loads(receipt.read_text())
        self.assertFalse(result["candidate_decision_created"])
        self.assertEqual(result["status"], "HOLD_NO_ELIGIBLE_FRESH_SOURCE")
        second = subprocess.run(command, capture_output=True, text=True,
                                env=env, timeout=15)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("FileExistsError", second.stderr)


if __name__ == "__main__":
    unittest.main()
