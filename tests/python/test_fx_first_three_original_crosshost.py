"""Adversarial checks for physical first-three source-health audit."""
import json,os,shutil,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from train_v2.fx_first_three_original_crosshost import (
 CrosshostHold,PLAN_SHA,SEAL_SHA,SCHEMA,PERIOD,RULE,
 validate_anchor_schedule,inspect_health,strict,sha,canonical,
 private_dir,read_file,write_report,verify)

ANCHORS=[1791822000000+i*195*60000 for i in range(4)]
def policy():
 p={"schema":"heptatrader.fx-original-future-price-cost-calendar.v1",
    "period_sha256":PERIOD,"behavior_policy":"NO_TRADE","trade_authority":False,
    "broker_orders_sent":0,"automatic_training":False,"model_promotion_allowed":False,
    "not_native_train_v2_120min_spec":True,
    "independent_horizons_ms":[900000,1800000,3600000,7200000],
    "source_minimum_usable":20,"source_maximum_missing":4,
    "source_freshness_age_max_ms":30000,"anchors_ms":ANCHORS}
 s={"schema":p["schema"]+".sealed.v1","plan_sha256":PLAN_SHA,
    "windows":[{"index":i,"anchor_ms":a,"entry_first_ms":a+1000,
    "entry_last_ms":a+4500,"mature_at_ms":[a+h*60000 for h in [15,30,60,120]]}
    for i,a in enumerate(ANCHORS)]}
 pre={"schema":"heptatrader.fx-research-crosshost-preseal-copy.v1",
      "checked_at_utc":datetime.fromtimestamp((ANCHORS[0]-500000)/1000,timezone.utc).isoformat(),
      "plan_sha256":PLAN_SHA,"seal_sha256":SEAL_SHA,
      "all_windows_future_at_copy":True,"production_witness_enrolled":False,
      "trade_authority":False}
 return p,s,pre
def health():
 a=ANCHORS[1];at=a-37937
 s={"schema":"heptatrader.quote-readiness-status.v1","period_sha256":PERIOD,
    "rule":dict(RULE),"state":"READY_FOR_REVIEW_NOT_AUTHORIZATION","reasons":[],
    "automatic_trial_registration":False,"trading_authority":False,"orders_sent":0,
    "probes":[{"status":"USABLE"} for _ in range(24)],"counts":{"USABLE":24},
    "usable":24,"unusable":0,"source_prefixes":[{"sha256":"a"*64,"bytes":123}],
    "at_ms":at,"latest_source_quote_ms":at-1000,"latest_quote_age_ms":1000}
 s["source_prefix_digest"]=sha(canonical(s["source_prefixes"]))
 c={"schema":"heptatrader.fx-forward-original-health-custody.v1.snapshot-attempt.v1",
    "window":1,"anchor_ms":a,"attempt_started_at_ms":a+1001,"state":"HOLD",
    "reasons":["READINESS_SNAPSHOT_STALE_OR_FUTURE"],"broker_orders_sent":0,
    "trade_authority":False,"no_retroactive_relabel_of_original_trial":True,
    "original_study_entry_readiness_proven":False,
    "source_health_status_sha256":sha(canonical(s)),"readiness_health_at_ms":at,
    "recomputed_source_usable_probes":24}
 return c,s
class ContractTests(unittest.TestCase):
 def test_schedule_and_frozen_horizons(self):
  self.assertEqual(validate_anchor_schedule(*policy()),ANCHORS)
 def test_future_anchor_mutation_denied(self):
  p,s,r=policy();s["windows"][1]["entry_first_ms"]+=1
  with self.assertRaisesRegex(CrosshostHold,"ORIGINAL_SEALED_WINDOWS_CHANGED"):
   validate_anchor_schedule(p,s,r)
 def test_pretrial_after_first_denied(self):
  p,s,r=policy();r["checked_at_utc"]=datetime.fromtimestamp(ANCHORS[0]/1000,timezone.utc).isoformat()
  with self.assertRaisesRegex(CrosshostHold,"PRETRIAL_CUSTODY_NOT_BEFORE_FIRST_WINDOW"):
   validate_anchor_schedule(p,s,r)
 def test_real_causally_stale_health_reproduces(self):
  c,s=health();self.assertEqual(inspect_health(1,ANCHORS[1],c,s)["asof_age_at_frozen_decision_ms"],38938)
 def test_not_stale_cannot_substitute(self):
  c,s=health();s["at_ms"]=ANCHORS[1];s["latest_source_quote_ms"]=s["at_ms"]-1000;c["readiness_health_at_ms"]=s["at_ms"]
  with self.assertRaisesRegex(CrosshostHold,"HEALTH_SNAPSHOT_NOT_ACTUALLY_STALE"):
   inspect_health(1,ANCHORS[1],c,s)
 def test_mismatched_probe_denominator_denied(self):
  c,s=health();s["probes"][0]["status"]="PATH_GAP"
  with self.assertRaisesRegex(CrosshostHold,"HEALTH_COUNTS_NOT_REPRODUCIBLE"):
   inspect_health(1,ANCHORS[1],c,s)
 def test_rewritten_source_prefix_digest_denied(self):
  c,s=health();s["source_prefixes"][0]["bytes"]=456
  with self.assertRaisesRegex(CrosshostHold,"HEALTH_ORIGINAL_PREFIX_DIGEST_CHANGED"):
   inspect_health(1,ANCHORS[1],c,s)
 def test_wrong_period_denied(self):
  c,s=health();s["period_sha256"]="b"*64
  with self.assertRaisesRegex(CrosshostHold,"CAPTURED_SOURCE_MONITOR_CHANGED"):
   inspect_health(1,ANCHORS[1],c,s)
 def test_duplicate_json_denied(self):
  with self.assertRaisesRegex(CrosshostHold,"DUPLICATE_JSON_KEY"):strict(b'{"a":1,"a":2}')
 def test_nonfinite_denied(self):
  with self.assertRaisesRegex(CrosshostHold,"NONFINITE_JSON"):strict(b'{"a":NaN}')
 def test_public_file_denied(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"file";p.write_text("{}");p.chmod(0o644)
   with self.assertRaisesRegex(CrosshostHold,"UNSAFE_OR_OVERSIZE_SOURCE"):read_file(p)
 def test_receipt_nonoverwrite(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"private";p.mkdir(mode=0o700)
   q=p/"report.json";write_report(q,{"schema":SCHEMA})
   self.assertEqual(q.stat().st_mode&0o777,0o600)
   with self.assertRaisesRegex(CrosshostHold,"OUTPUT_MUST_BE_NEW"):write_report(q,{"schema":"false"})
class PhysicalArchiveTests(unittest.TestCase):
 def setUp(self):
  v=os.environ.get("FX_FIRST3_PRIVATE_FIXTURE")
  if not v:self.skipTest("exact private physical archive unavailable")
  self.temp=tempfile.TemporaryDirectory()
  self.root=Path(self.temp.name)/"copy"
  shutil.copytree(v,self.root,ignore=shutil.ignore_patterns("independent-first3-*.json"))
  self.root.chmod(0o700)
  for p in self.root.rglob("*"):p.chmod(0o700 if p.is_dir() else 0o600)
  self.addCleanup(self.temp.cleanup)
 def alter(self,path,key,value):
  p=self.root/path;d=json.loads(p.read_text());d[key]=value
  p.write_text(json.dumps(d));p.chmod(0o600)
 def test_all_twelve_censored_physically(self):
  r=verify(self.root)
  self.assertEqual(r["original_maturity_attempts_censored"],12)
  self.assertEqual(r["price_only_matured_labels"],0)
  self.assertEqual([w["original_health"]["asof_age_at_frozen_decision_ms"] for w in r["first_three"][1:]],[37938,86233])
 def test_source_plan_hash_mutation_denied(self):
  self.alter("plan.json","source_minimum_usable",1)
  with self.assertRaisesRegex(CrosshostHold,"CROSSHOST_FROZEN_PLAN_SHA_CHANGED"):
   verify(self.root)
 def test_changed_maturity_state_denied(self):
  self.alter("window-01/maturity-030-attempt.json","state","MATURE_PRICE_ONLY")
  with self.assertRaisesRegex(CrosshostHold,"MATURITY_CENSORED_RELABEL_OR_BACKFILL"):
   verify(self.root)
 def test_health_original_bytes_mutation_denied(self):
  p=self.root/"window-02-health-original.json";d=json.loads(p.read_text());d["at_ms"]+=50000
  p.write_text(json.dumps(d));p.chmod(0o600)
  with self.assertRaisesRegex(CrosshostHold,"ORIGINAL_HEALTH_BYTES_REPLACED"):
   verify(self.root)
 def test_fabricated_first_health_denied(self):
  (self.root/"window-00-health-original.json").write_text("{}")
  with self.assertRaisesRegex(CrosshostHold,"OLD_UNATTESTED_WINDOW_ZERO_CANNOT_BE_BACKFILLED"):
   verify(self.root)
 def test_missing_original_maturity_denied(self):
  (self.root/"window-02/maturity-120-attempt.json").unlink()
  with self.assertRaises((FileNotFoundError,CrosshostHold)):
   verify(self.root)
if __name__=="__main__":unittest.main()
