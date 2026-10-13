"""Fail-closed prewarm 480-slot original PAPER source health candidate."""
from __future__ import annotations
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path

from train_v2.fx_source_cache_prewarm_480 import (
    N, STEP_MS, MIN_LEAD_MS, MAX_STATUS_AGE_MS, PREWARM_DONE_LEAD_MS,
    fixed_grid, frozen_period_scope, require_prior_negative_and_pilot,
    verify_candidate, SCHEMA, original_observer,
)
from train_v2.fx_original_health_sustained import AuditHold

class Frozen480Tests(unittest.TestCase):
    def test_fixed_480_15second_clock(self):
        self.assertEqual(N,480)
        self.assertEqual(STEP_MS,15_000)
        p=fixed_grid(1791912337000)
        self.assertEqual(len(p),480)
        self.assertTrue(all(x%15000==0 for x in p))
        self.assertTrue(all(b-a==15000 for a,b in zip(p,p[1:])))
    def test_all_480_prospectively_after_registration(self):
        t=1791912337000
        self.assertGreaterEqual(fixed_grid(t)[0]-t,MIN_LEAD_MS)
        self.assertGreater(PREWARM_DONE_LEAD_MS,10_000)
    def test_quote_age_bound_unrelaxed(self):
        self.assertEqual(MAX_STATUS_AGE_MS,30000)
    def test_distinct_research_schema(self):
        self.assertNotEqual(SCHEMA,
            "heptatrader.fx-cache-prewarm-source-health.v1")
    def test_exact_existing_source_and_cache_pins(self):
        self.assertTrue(verify_candidate())
    def test_old_failure_and_new_pilot_pinned(self):
        self.assertTrue(require_prior_negative_and_pilot())
    def test_mutated_original_failure_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            bad=Path(td)/"wrong.json"
            bad.write_text('{"count":480}')
            with patch("train_v2.fx_source_cache_prewarm_480.ORIGINAL_480_FINAL",bad):
                with self.assertRaisesRegex(AuditHold,
                        "PRESERVED_ORIGINAL_480_FAILURE_CHANGED"):
                    require_prior_negative_and_pilot()
    def test_mutated_previous_pilot_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            bad=Path(td)/"wrong.json"
            bad.write_text('{"count":12}')
            with patch("train_v2.fx_source_cache_prewarm_480.PILOT_12_FINAL",bad):
                with self.assertRaisesRegex(AuditHold,
                        "PRESERVED_12_CYCLE_PILOT_CHANGED"):
                    require_prior_negative_and_pilot()
    def test_missing_or_malformed_period_rejected(self):
        for x in ({}, {"original_period_sha256":"not_sha"},
                  {"original_period_sha256":"z"*64},
                  {"original_period_sha256":True}):
            with self.subTest(x=x),self.assertRaisesRegex(AuditHold,
                         "FROZEN_ORIGINAL_PAPER_PERIOD_NOT_PINNED"):
                frozen_period_scope(x)
    def test_period_field_bridging(self):
        d={"original_period_sha256":"a"*64}
        self.assertEqual(frozen_period_scope(d),{"period_sha256":"a"*64})
    def test_old_source_digest_exact(self):
        src=Path("/home/alex/.local/share/heptatrader/laya-resident/releases/8dd7ddd-proof-c17f338/learn/laya_cell/source_readiness.py")
        self.assertEqual(original_observer(src),src)

if __name__=="__main__":unittest.main()
