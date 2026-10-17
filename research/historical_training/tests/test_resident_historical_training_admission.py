"""Live source readiness is scoped to prospective evaluation, not frozen training."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from historical_training import resident_resource_guard as g

NOW=2_000_000_000_000
CONFIG_SHA="a"*64
PERIOD_SHA="b"*64


class HistoricalTrainingAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.config={"state_root":str(self.root/"guard"),"original_config":str(self.root/"original.json"),
            "original_config_sha256":CONFIG_SHA,"period_path":str(self.root/"period.json"),
            "period_sha256":PERIOD_SHA,"source_health_path":str(self.root/"source.json"),
            "manual_hold_path":str(self.root/"manual-hold")}
        self.original={"fixture":"original config"}
        prefixes=[{"path":"/fixture/sealed-source.jsonl","sha256":"c"*64,"bytes":123}]
        self.source={"schema":"heptatrader.quote-readiness-status.v1","period_sha256":PERIOD_SHA,
            "at_ms":NOW,"latest_source_quote_ms":NOW-1500,"usable":24,"unusable":0,
            "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION","rule":dict(g.SOURCE_RULE),
            "source_prefixes":prefixes,"source_prefix_digest":g.digest(prefixes),"reasons":[]}

    def check(self,role,*,held=False,missing=False,available=10*g.GIB,swap=2*g.GIB,
              pressure=0,manual=False,changed_pin=None,changed_prefix=False,draining=False):
        source=copy.deepcopy(self.source)
        if held:
            source.update(latest_source_quote_ms=NOW-3600000,usable=0,unusable=24,
                state="HOLD_NEW_FUSION_WINDOW",reasons=["LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH"])
        if changed_prefix:source["source_prefix_digest"]="changed"
        hold=Path(self.config["manual_hold_path"])
        if manual:hold.touch()
        else:hold.unlink(missing_ok=True)
        mem=f"MemTotal: {24*g.GIB//1024} kB\nMemAvailable: {available//1024} kB\nSwapTotal: {8*g.GIB//1024} kB\nSwapFree: {swap//1024} kB\n"
        psi=f"full avg10=0.00 avg60={pressure} avg300=0.00 total=0\n"
        read_text=Path.read_text
        def telemetry(path,*args,**kwargs):
            if str(path)=="/proc/meminfo":return mem
            if str(path)=="/proc/pressure/memory":return psi
            return read_text(path,*args,**kwargs)
        def pin(path):
            if str(path)==self.config["original_config"]:return "changed" if changed_pin=="config" else CONFIG_SHA
            if str(path)==self.config["period_path"]:return "changed" if changed_pin=="period" else PERIOD_SHA
            raise AssertionError("unexpected pin read")
        def read(path,*args):
            if str(path)==self.config["original_config"]:return self.original
            if str(path)==self.config["source_health_path"]:
                if missing:raise FileNotFoundError("fixture live source missing")
                return source
            raise AssertionError("unexpected JSON read")
        with patch.object(g,"sha",side_effect=pin), patch.object(g,"read",side_effect=read) as reads, \
             patch.object(Path,"read_text",new=telemetry), patch.object(g.time,"time",return_value=NOW/1000), \
             patch("historical_training.resident_live_source.refresh",
                   return_value=(source,{"fixture":"live refresh"})) as refresh:
            gate=g.Gate(self.config,role);gate.draining=draining
            result=gate.check()
        return result,reads,refresh

    def test_trainer_ignores_current_quote_hold_without_claiming_dataset_validation(self):
        r,reads,refresh=self.check("trainer",held=True)
        self.assertTrue(r["admit_new_worker"]);self.assertEqual(r["reasons"],[])
        self.assertEqual(r["admission_scope"],"HISTORICAL_TRAINING_RESOURCES_ONLY")
        self.assertEqual(r["historical_dataset_validation"],"PENDING_IN_RESIDENT_STEP")
        self.assertFalse(r["original_live_source_evidence"]["fresh_original_live_source_proven"])
        self.assertNotIn(self.config["source_health_path"],[str(c.args[0]) for c in reads.call_args_list])
        refresh.assert_not_called()

    def test_same_source_hold_still_denies_prospective_evaluator(self):
        r,_,refresh=self.check("evaluator",held=True)
        self.assertFalse(r["admit_new_worker"])
        for reason in ("LATEST_SOURCE_QUOTE_NOT_FRESH","SOURCE_CONTINUITY_NOT_READY",
                       "SOURCE_QUALITY_HOLD_NO_REFRESH_AUTHORITY"):
            self.assertIn(reason,r["reasons"])
        refresh.assert_not_called()

    def test_missing_live_status_only_blocks_evaluator(self):
        r,_,refresh=self.check("trainer",missing=True)
        self.assertTrue(r["admit_new_worker"]);refresh.assert_not_called()
        r,_,_=self.check("evaluator",missing=True)
        self.assertFalse(r["admit_new_worker"])
        self.assertIn("fixture live source missing"," ".join(r["reasons"]))

    def test_evaluator_refresh_and_prefix_validation_remain(self):
        r,_,refresh=self.check("evaluator")
        self.assertTrue(r["admit_new_worker"]);refresh.assert_called_once()
        r,_,refresh=self.check("evaluator",changed_prefix=True)
        self.assertFalse(r["admit_new_worker"])
        self.assertIn("CONTINUITY_PREFIX_DIGEST_CHANGED"," ".join(r["reasons"]))
        refresh.assert_not_called()

    def test_resources_manual_hold_and_drain_still_apply_to_both_roles(self):
        cases=(
            ({"available":7*g.GIB},"HOST_AVAILABLE_MEMORY_BELOW_8GIB"),
            ({"swap":g.GIB-1024},"HOST_SWAP_RESERVE_BELOW_1GIB"),
            ({"pressure":0.2},"HOST_FULL_MEMORY_PSI_PRESSURE"),
            ({"manual":True},"OPERATOR_RESOURCE_HOLD"),
            ({"draining":True},"SUPERVISOR_DRAIN_REQUESTED"))
        for role in ("trainer","evaluator"):
            for flags,reason in cases:
                with self.subTest(role=role,reason=reason):
                    r,_,_=self.check(role,**flags)
                    self.assertFalse(r["admit_new_worker"]);self.assertIn(reason,r["reasons"])

    def test_original_config_and_period_identity_still_apply_to_both_roles(self):
        for role in ("trainer","evaluator"):
            for pin,reason in (("config","ORIGINAL_CONFIG_CHANGED"),("period","ORIGINAL_PERIOD_CHANGED")):
                with self.subTest(role=role,pin=pin):
                    r,_,_=self.check(role,changed_pin=pin)
                    self.assertFalse(r["admit_new_worker"])
                    self.assertIn(reason," ".join(r["reasons"]))


if __name__=="__main__":unittest.main()
