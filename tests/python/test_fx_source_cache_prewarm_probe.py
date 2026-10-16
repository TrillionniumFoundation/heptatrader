"""Safety tests for finite cold-prewarm verified source health study.

No broker, no model, no execution or production monitor service mutations.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from train_v2.fx_source_cache_prewarm_probe import (
    N, STEP_MS, LATEST_START_MS, MAX_STATUS_AGE_MS, PREWARM_DONE_LEAD_MS,
    SCHEMA, fixed_grid, decide_cycle, prewarm_health, original_observer,
    code_sha, require, owner_dir, write_once, verify_candidate
)
from train_v2.fx_verified_cache_480 import RULE

def status(now,ready=True):
    probes=[{"status":"USABLE"} for _ in range(24)]
    prefixes=[]
    return {
       "schema":"heptatrader.quote-readiness-status.v1",
       "at_ms":now,"period_sha256":"f"*64,
       "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION" if ready
          else "HOLD_NEW_FUSION_WINDOW",
       "reasons":[] if ready else ["INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE"],
       "probes":probes,"counts":{"USABLE":24},"usable":24,"unusable":0,
       "source_prefixes":prefixes,
       "source_prefix_digest":hashlib.sha256(
           __import__("json").dumps(prefixes,sort_keys=True,separators=(",",":")).encode()).hexdigest(),
       "latest_quote_age_ms":200,"latest_source_quote_ms":now-200,
       "automatic_trial_registration":False,"trading_authority":False,
       "orders_sent":0,"rule":dict(RULE),
    }

class TestPrewarmProbe(unittest.TestCase):
    def test_original_candidate_byte_pins_really_hold(self):
        # A historical 480-cycle experiment remains pinned to its ORIGINAL
        # source, not to every newer mainline implementation. Test the strict
        # verifier with an explicitly bound unit fixture, never authorize a
        # fresh run by silently replacing the frozen constants.
        from train_v2 import fx_verified_cache_480 as frozen
        self.assertEqual(frozen.FAST_MONITOR_SHA,
                         "8cbedb6a5f792891f0141f7b2ae0da91a7738080cd14c45995436ed55473c309")
        self.assertEqual(frozen.VERIFIED_CACHE_SHA,
                         "1d1bdea5702d1fab8c9412eb2cbf0d224dd34d2736d4ab822e71bff5b9acb61c")
        with patch.object(frozen,"FAST_MONITOR_SHA",hashlib.sha256(frozen.source_file().read_bytes()).hexdigest()),\
             patch.object(frozen,"VERIFIED_CACHE_SHA",hashlib.sha256(frozen.cache_file().read_bytes()).hexdigest()):
            self.assertTrue(verify_candidate())
        with patch.object(frozen,"VERIFIED_CACHE_SHA","0"*64):
            with self.assertRaisesRegex(frozen.AuditHold,"CACHE_CANDIDATE_OR_SOURCE_CONTRACT_CHANGED"):
                verify_candidate()
        self.assertEqual(N,12)
        self.assertEqual(STEP_MS,15000)
        self.assertEqual(LATEST_START_MS,3000)
        self.assertEqual(MAX_STATUS_AGE_MS,30000)
        self.assertEqual(PREWARM_DONE_LEAD_MS,10000)
        self.assertEqual(len(code_sha()),64)
    def test_calendar_commits_12_future_nonoverlap_probe_slots(self):
        now=1791900001234
        grid=fixed_grid(now)
        self.assertEqual(len(grid),12)
        self.assertGreaterEqual(grid[0]-now,90000)
        self.assertTrue(all(t%15000==0 for t in grid))
        self.assertEqual(len(set(grid)),12)
        self.assertTrue(all(y-x==15000 for x,y in zip(grid,grid[1:])))
    def test_bool_time_not_valid(self):
        with self.assertRaises(Exception):
            fixed_grid(True)
    def test_foreign_monitor_rule_invalid(self):
        v=status(100000)
        v["rule"]["minimum_usable"]=1
        result=decide_cycle(v,{"period_sha256":"f"*64},100000,100500)
        self.assertFalse(result["source_ready"])
        self.assertIn("HEALTH_RULE_CHANGED",result["issues"])
    def test_false_ready_counter_refused(self):
        v=status(100000)
        v["usable"]=10
        result=decide_cycle(v,{"period_sha256":"f"*64},100000,100500)
        self.assertFalse(result["source_ready"])
        self.assertIn("PROBE_COUNTS_CHANGED",result["issues"])
    def test_true_monitor_hold_still_hold(self):
        v=status(100000,ready=False)
        result=decide_cycle(v,{"period_sha256":"f"*64},100000,100300)
        self.assertFalse(result["source_ready"])
        self.assertEqual(result["state"],"SOURCE_HOLD_RETAINED")
        self.assertEqual(result["issues"],[])
    def test_positive_readiness_is_only_research(self):
        v=status(100000)
        result=decide_cycle(v,{"period_sha256":"f"*64},100000,100250)
        self.assertTrue(result["source_ready"])
        self.assertEqual(result["state"],"SOURCE_READY_RESEARCH_ONLY")
        self.assertEqual(result["status_age_at_publication_ms"],250)
    def test_30s_assessment_timeout_is_hold(self):
        v=status(100000)
        result=decide_cycle(v,{"period_sha256":"f"*64},100000,130001)
        self.assertFalse(result["source_ready"])
        self.assertIn("ORIGINAL_STATUS_STALE_AT_PUBLICATION",result["issues"])
    def test_wrong_period_never_recovers(self):
        v=status(100000)
        result=decide_cycle(v,{"period_sha256":"e"*64},100000,100500)
        self.assertFalse(result["source_ready"])
    def test_forged_prefix_rejected(self):
        v=status(100000)
        v["source_prefix_digest"]="0"*64
        result=decide_cycle(v,{"period_sha256":"f"*64},100000,100500)
        self.assertFalse(result["source_ready"])
    def test_preheat_read_without_publishing(self):
        t=1791900001234
        fake=status(t)
        mocked=Mock(return_value=fake)
        cache=type("Cache",(),{"read_quotes":staticmethod(lambda *args:None)})()
        with patch("train_v2.fx_source_cache_prewarm_probe.now_ms",side_effect=[t,t+18000]):
            started,finished,v,issues=prewarm_health(
                {"period_sha256":"f"*64},cache,assessment=mocked)
        self.assertEqual((started,finished),(t,t+18000))
        self.assertIs(v,fake)
        self.assertEqual(issues,[])
        mocked.assert_called_once()
    def test_actual_frozen_period_field_binds_monitor(self):
        from train_v2.fx_source_cache_prewarm_probe import frozen_period_scope
        frozen={"original_period_sha256":"f"*64}
        scope=frozen_period_scope(frozen)
        self.assertEqual(scope,{"period_sha256":"f"*64})
        actual=decide_cycle(status(100000),scope,100000,100500)
        self.assertTrue(actual["source_ready"])
        with self.assertRaises(Exception):
            frozen_period_scope({"period_sha256":"f"*64})
    def test_original_source_state_unexpected_refused(self):
        v=status(100000)
        v["state"]="STUFFED_READY"
        with self.assertRaises(Exception):
            decide_cycle(v,{"period_sha256":"f"*64},100000,100500)
    def test_private_write_once_receipt_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"private"
            owner_dir(p,create=True)
            f=p/"slot-00.json"
            first=write_once(f,b'{"state":"HOLD"}\n')
            self.assertEqual(first,hashlib.sha256(b'{"state":"HOLD"}\n').hexdigest())
            with self.assertRaises(FileExistsError):
                write_once(f,b'{"state":"READY"}\n')
            self.assertEqual(f.stat().st_mode&0o777,0o600)
            self.assertEqual(f.read_bytes(),b'{"state":"HOLD"}\n')
    def test_no_order_or_restart_code(self):
        import inspect
        from train_v2 import fx_source_cache_prewarm_probe as mod
        source=inspect.getsource(mod)
        for bad in ("systemctl --user restart","placeOrder(", "ib_insync",
                    "subprocess.run(", "os.system(", "NativeCandidateSet"):
            self.assertNotIn(bad,source)

if __name__=="__main__":
    unittest.main()
