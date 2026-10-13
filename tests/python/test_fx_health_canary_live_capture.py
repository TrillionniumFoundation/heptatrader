"""Offline hostile cases for the already executed monitor-only live-canary readback."""
import contextlib, io, hashlib,json,os
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch

from train_v2 import fx_health_canary_live_capture as m

def source(index):
    t=1234500000+index*15000
    return {"schema":m.SCHEMA,"cycle":index+1,
            "source_asof_ms":t,"published_ms":t+9200,
            "published_source_age_ms":9200,
            "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION","trade_authority":False,
            "orders_sent":0,"usable":24}

def status():
    src=[{"sha256":"a"*64,"complete_prefix_bytes":122}]
    return {"schema":"heptatrader.quote-readiness-status.v1",
            "at_ms":1234500000+3*15000,"period_sha256":
            "61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f",
            "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
            "rule":{"minimum_usable":20},"probes":[{"status":"USABLE"}]*24,
            "counts":{"USABLE":24},"source_prefixes":src,
            "source_prefix_digest":hashlib.sha256(json.dumps(
                src,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest(),
            "automatic_trial_registration":False,"trading_authority":False,"orders_sent":0}

def make_env(t):
    root=Path(t)/"audit";root.mkdir(mode=0o700)
    activation={"schema":"heptatrader.fx-health-monitor-reversible-cutover.v1.activation.v1",
        "new_service_show":{"MainPID":"222"}}
    (root/"activation.json").write_text(json.dumps(activation))
    s=Path(t)/"health.json";s.write_text(json.dumps(status()));s.chmod(0o600)
    return root,s

def run_(root,s,cycles=None,pid="123"):
    vals=cycles or [source(i) for i in range(4)]
    def fake(*args):
        if args[0]=="journalctl":return "\n".join(json.dumps(x) for x in vals)+"\n"
        if args[0]=="systemctl":return "MainPID="+pid+"\n"
        raise AssertionError("unexpected command")
    with patch.object(m,"cmd",side_effect=fake),patch.object(m,"EXPECTED",{"a.service":"123"}):
        with patch.object(m.sys,"argv",["monitor-recheck",str(root),str(s)]):
            with contextlib.redirect_stdout(io.StringIO()):
                m.main()

class AdversarialReadbackTests(unittest.TestCase):
    def test_full_writeonce_four_cycles(self):
        with tempfile.TemporaryDirectory() as t:
            root,s=make_env(t)
            run_(root,s)
            x=json.loads((root/"live-canary-audit.json").read_text())
            self.assertEqual(x["completed_actual_cycles"],4)
            self.assertEqual(x["max_source_age_before_next_publication_ms"],24200)
            self.assertEqual(x["max_source_published_age_ms"],9200)
            self.assertTrue(x["original_paper_trading_and_trainer_pids_unchanged"])
            self.assertEqual((root/"live-canary-audit.json").stat().st_mode&0o777,0o600)
    def test_worst_between_publications_refuses_30s(self):
        with tempfile.TemporaryDirectory() as t:
            root,s=make_env(t)
            vals=[source(i) for i in range(4)]
            vals[1]["published_ms"]=vals[0]["source_asof_ms"]+31000
            vals[1]["published_source_age_ms"]=vals[1]["published_ms"]-vals[1]["source_asof_ms"]
            # Enforce no future publication with already expired previous data.
            with self.assertRaisesRegex(ValueError,"REAL_MONITOR_BETWEEN_PUBLICATIONS_STALE"):
                run_(root,s,vals)
    def test_fewer_than_four_real_cycles_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root,s=make_env(t)
            with self.assertRaisesRegex(ValueError,"MIN_FOUR_MONOTONIC"):
                run_(root,s,[source(i) for i in range(3)])
    def test_repeated_or_out_of_order_cycle_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root,s=make_env(t)
            vals=[source(i) for i in range(4)];vals[-1]["cycle"]=3
            with self.assertRaisesRegex(ValueError,"MIN_FOUR_MONOTONIC"):
                run_(root,s,vals)
    def test_unexpected_trader_restart_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root,s=make_env(t)
            with self.assertRaisesRegex(ValueError,"TRADING_OR_LAYA_MAIN_PROCESS_RESTARTED"):
                run_(root,s,pid="999")
    def test_original_monitor_prefix_tamper_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root,s=make_env(t)
            obj=json.loads(s.read_text());obj["source_prefixes"][0]["complete_prefix_bytes"]=999
            s.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError,"ORIGINAL_SOURCE_PREFIX_DIGEST_CHANGED"):
                run_(root,s)
    def test_model_autoregistration_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root,s=make_env(t)
            obj=json.loads(s.read_text());obj["automatic_trial_registration"]=True
            s.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError,"SOURCE_HEALTH_CONTRACT_CHANGED"):
                run_(root,s)
    def test_no_rerun_overwrites_receipt(self):
        with tempfile.TemporaryDirectory() as t:
            root,s=make_env(t);run_(root,s)
            original=(root/"live-canary-audit.json").read_bytes()
            with self.assertRaises(FileExistsError):run_(root,s)
            self.assertEqual((root/"live-canary-audit.json").read_bytes(),original)
if __name__=="__main__":unittest.main()
