"""Preflight only: existing resident training guard is never modified."""
import copy
import unittest
from historical_training.resident_resource_guard import GIB, SOURCE_RULE
from historical_training.common import EvidenceError
from train_v2.swapless_resource import (
    assess_isolated, verify_cgroup, SPARE_AFTER_CAP_BYTES,
)
NOW=1791888000000
PERIOD="a"*64


def valid():
    host={"MemTotal":24*GIB,"MemAvailable":16*GIB,
          "SwapTotal":8*GIB,"SwapFree":800*1024*1024}
    pressure={"avg10":0.0,"avg60":0.0}
    source={
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":PERIOD,"at_ms":NOW-800,
        "latest_source_quote_ms":NOW-1100,
        "usable":24,"unusable":0,
        "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
        "rule":dict(SOURCE_RULE)
    }
    group={
        "memory.max":6*GIB,"memory.high":5*GIB,
        "memory.current":100*1024*1024,
        "memory.swap.max":0,"memory.swap.current":0,
        "pids.max":32,"cpu.max":"100000 100000"}
    return host,pressure,source,NOW,PERIOD,False,group


class SwaplessNoProductionTests(unittest.TestCase):
    def report(self,data=None):
        return assess_isolated(*(valid() if data is None else data))
    def test_low_swap_uses_current_memory_pressure_policy(self):
        r=self.report()
        self.assertTrue(r["current_guard_admit_new_worker"])
        self.assertEqual(r["current_guard_reasons"],[])
        self.assertEqual(r["resource_profile"],"ROLE_MEMORY_PSI_V2")
        self.assertEqual(r["swap_free_policy"],"DIAGNOSTIC_ONLY")
        self.assertTrue(r["isolated_preflight_eligible"])
        self.assertEqual(r["isolated_research_preflight"],
                         "READY_FOR_OPERATOR_REVIEW_NO_JOB")
        self.assertFalse(r["automatic_training"])
        self.assertFalse(r["trade_authority"])
        self.assertEqual(r["broker_orders_sent"],0)
    def test_swapless_requires_zero_swap_limit_and_usage(self):
        for field,bad in (("memory.swap.max",1),
                          ("memory.swap.max",None),
                          ("memory.swap.current",1),
                          ("memory.swap.current",None)):
            x=list(valid())
            x[-1][field]=bad
            self.assertFalse(self.report(x)["isolated_preflight_eligible"],field)
    def test_oversized_worker_or_high_memory_refused(self):
        for field,bad in (("memory.max",7*GIB),
                          ("memory.max",3*GIB),
                          ("memory.high",None),
                          ("memory.high",7*GIB),
                          ("memory.current",5*GIB+1)):
            x=list(valid())
            x[-1][field]=bad
            self.assertFalse(self.report(x)["isolated_preflight_eligible"],field)
    def test_cgroup_cpu_and_pids_must_be_bounded(self):
        for field,bad in (("pids.max",None),("pids.max",65),
                          ("cpu.max","max 100000"),
                          ("cpu.max","200000 100000")):
            x=list(valid())
            x[-1][field]=bad
            self.assertFalse(self.report(x)["isolated_preflight_eligible"],field)
    def test_adding_max_worker_must_leave_true_host_ram_reserve(self):
        x=list(valid())
        x[0]["MemAvailable"]=SPARE_AFTER_CAP_BYTES + x[-1]["memory.max"]-1
        r=self.report(x)
        self.assertIn("SWAPLESS_INSUFFICIENT_HOST_RAM_AFTER_HARD_CAP",
                      r["isolated_hold_reasons"])
        self.assertFalse(r["isolated_preflight_eligible"])
    def test_current_host_pressure_and_source_rules_remain_unmodified(self):
        for edit in ("source-old","source-expired","source-19",
                     "source-horizon-bypass","psi","manual"):
            x=list(valid())
            if edit=="source-old":x[2]["at_ms"]-=120001
            if edit=="source-expired":x[2]["latest_source_quote_ms"]=NOW-5001
            if edit=="source-19":x[2].update(usable=19,unusable=5)
            if edit=="source-horizon-bypass":x[2]["rule"]["source_ttl_ms"]=10000
            if edit=="psi":x[1]["avg60"]=0.11
            if edit=="manual":x[5]=True
            with self.subTest(edit=edit):
                r=self.report(x)
                self.assertFalse(r["isolated_preflight_eligible"])
                self.assertFalse(r["current_guard_admit_new_worker"])
                self.assertTrue(set(r["current_guard_reasons"]) &
                                set(r["isolated_hold_reasons"]))
    def test_all_lower_bounds_rejected(self):
        for field,bad in (("pids.max",0),
                          ("memory.current",-1),
                          ("memory.swap.current",-1),
                          ("memory.high",0),
                          ("cpu.max","0 100000")):
            x=list(valid())
            x[-1][field]=bad
            with self.subTest(field=field):
                self.assertFalse(self.report(x)["isolated_preflight_eligible"])
    def test_nonswap_violation_never_ignored(self):
        x=list(valid())
        x[0]["MemAvailable"]=7*GIB
        self.assertFalse(self.report(x)["isolated_preflight_eligible"])
        self.assertIn("HOST_AVAILABLE_MEMORY_BELOW_8GIB",
                      self.report(x)["isolated_hold_reasons"])
    def test_kernel_cgroup_proof_report_typed(self):
        x=verify_cgroup(valid()[-1])
        self.assertTrue(x["kernel_enforced_swapless"])
        self.assertEqual(x["cgroup_memory_max_bytes"],6*GIB)
        self.assertEqual(x["cgroup_swap_max_bytes"],0)
    def test_policy_does_not_set_production_guard_ready_with_normal_swap(self):
        x=list(valid())
        x[0]["SwapFree"]=2*GIB
        r=self.report(x)
        self.assertTrue(r["current_guard_admit_new_worker"])
        self.assertTrue(r["isolated_preflight_eligible"])
        self.assertFalse(r["automatic_training"])
    def test_legacy_model_timeline_untouched(self):
        r=self.report()
        self.assertTrue(r["real_quote_gate_unchanged"])
        self.assertTrue(r["original_horizon_rules_unchanged"])
        self.assertFalse(r["existing_services_modified"])
        self.assertFalse(r["actual_model_loaded"])

    def test_no_pinned_original_guard_config_never_counts_cached_quote(self):
        from train_v2.swapless_resource import once
        with self.assertRaisesRegex(EvidenceError,
                                    "SWAPLESS_PINNED_ORIGINAL_GUARD_REQUIRED"):
            once(None,None,None)


class RealClockAndManualHoldTests(unittest.TestCase):
    def checked_once(self, manual_hold, *, end_offset_ms):
        from contextlib import ExitStack
        from pathlib import Path
        from unittest.mock import patch
        from train_v2.swapless_resource import once
        p=Path("/tmp/period-for-swapless-fixture.json")
        status=Path("/tmp/source-for-swapless-fixture.json")
        guard=Path("/tmp/guard-for-swapless-fixture.json")
        fixture=list(valid())
        config={"period_path":str(p),"source_health_path":str(status),
                "period_sha256":PERIOD,"manual_hold_path":str(manual_hold)}
        def fake_read(path,*args,**kwargs):
            if path==guard:return config
            if path==status:return fixture[2]
            raise AssertionError("unexpected file")
        # Source, clock and cgroup are already synthetic. Supply matching host
        # inputs too, while exercising the real parsers and manual-hold lookup.
        proc_inputs={
            Path("/proc/meminfo"):"".join(f"{key}: {value//1024} kB\n"
                                         for key,value in fixture[0].items()),
            Path("/proc/pressure/memory"):
                "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"}
        proc_reads=[]
        def proc_text(path,*args,**kwargs):
            self.assertIn(path,proc_inputs)
            proc_reads.append(path)
            return proc_inputs[path]
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path,"read_text",proc_text))
            stack.enter_context(patch("train_v2.swapless_resource.sha",
                                      return_value=PERIOD))
            stack.enter_context(patch("train_v2.swapless_resource.read",
                                      side_effect=fake_read))
            stack.enter_context(patch(
                "historical_training.resident_resource_guard.verify_config",
                return_value={"original_config_proven":True}))
            refresh=stack.enter_context(patch(
                "historical_training.resident_live_source.refresh",
                return_value=(fixture[2],{"verified_original":True})))
            stack.enter_context(patch(
                "train_v2.swapless_resource.current_cgroup",
                return_value=fixture[-1]))
            stack.enter_context(patch(
                "train_v2.swapless_resource.time.time_ns",
                side_effect=[NOW*1000000,(NOW+end_offset_ms)*1000000]))
            r=once(status,p,manual_hold,guard)
            self.assertEqual(refresh.call_args[0][-1],NOW)
            self.assertCountEqual(proc_reads,proc_inputs)
        return r

    def test_expired_during_original_chain_verification_fails_closed(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            result=self.checked_once(
                Path(td)/"no-manual-hold",end_offset_ms=6000)
        self.assertFalse(result["isolated_preflight_eligible"])
        self.assertIn("LATEST_SOURCE_QUOTE_NOT_FRESH",
                      result["isolated_hold_reasons"])
        self.assertEqual(result["at_ms"],NOW+6000)

    def test_dangling_manual_hold_symlink_still_blocks_isolated_lane(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            hold=Path(td)/"manual-hold.json"
            hold.symlink_to(Path(td)/"intentionally-missing")
            self.assertFalse(hold.exists())
            result=self.checked_once(hold,end_offset_ms=0)
        self.assertFalse(result["isolated_preflight_eligible"])
        self.assertIn("OPERATOR_RESOURCE_HOLD",
                      result["isolated_hold_reasons"])
