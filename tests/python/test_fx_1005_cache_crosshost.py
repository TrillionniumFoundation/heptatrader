"""Adversarial physical qian-qi tests for 10:05 cached-vs-original time."""
import json,os,shutil,tempfile,unittest
from pathlib import Path
from train_v2.fx_1005_cache_crosshost import verify, source_status, CrosshostHold

from train_v2.fx_first_three_original_crosshost import PERIOD,RULE,sha,canonical

def monitor_fixture(age=18000):
 capture=1791857102062
 at=capture-age
 item={
  "schema":"heptatrader.quote-readiness-status.v1",
  "period_sha256":PERIOD,
  "rule":dict(RULE),
  "automatic_trial_registration":False,
  "trading_authority":False,
  "orders_sent":0,
  "probes":[{"status":"USABLE"} for _ in range(24)],
  "counts":{"USABLE":24},"usable":24,"unusable":0,
  "source_prefixes":[{"sha256":"a"*64,"bytes":128}],
  "source_prefix_digest":None,
  "at_ms":at,"latest_source_quote_ms":at-1500,
  "latest_quote_age_ms":1500,
  "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION","reasons":[],
 }
 item["source_prefix_digest"]=sha(canonical(item["source_prefixes"]))
 return item,capture

class MonitorContractTests(unittest.TestCase):
 def test_fresh_18sec_is_readonly_ready(self):
  status,at=monitor_fixture()
  x=source_status(status,at)
  self.assertTrue(x["healthy_under_frozen_monitor_rules"])
  self.assertFalse(x["independently_broker_signed"])
 def test_stale_86sec_is_unqualified_even_with_24_usable(self):
  status,at=monitor_fixture(86092)
  x=source_status(status,at)
  self.assertEqual(x["usable"],24)
  self.assertFalse(x["healthy_under_frozen_monitor_rules"])
 def test_19_usable_never_qualified(self):
  status,at=monitor_fixture()
  status["probes"][0]["status"]="PATH_GAP"
  status["counts"]={"USABLE":23,"PATH_GAP":1}
  status["usable"]=23;status["unusable"]=1
  x=source_status(status,at)
  self.assertTrue(x["healthy_under_frozen_monitor_rules"])
  for i in range(1,5):
   status["probes"][i]["status"]="PATH_GAP"
  status["counts"]={"USABLE":19,"PATH_GAP":5}
  status["usable"]=19;status["unusable"]=5
  self.assertFalse(source_status(status,at)["healthy_under_frozen_monitor_rules"])
 def test_invalid_probe_counter_is_refused(self):
  status,at=monitor_fixture()
  status["probes"][0]["status"]="PATH_GAP"
  with self.assertRaisesRegex(CrosshostHold,"READINESS_PROBE_COUNTS_CHANGED"):
   source_status(status,at)
 def test_source_prefix_digest_mutation_refused(self):
  status,at=monitor_fixture()
  status["source_prefixes"][0]["bytes"]+=1
  with self.assertRaisesRegex(CrosshostHold,"SOURCE_PROVENANCE_DIGEST_CHANGED"):
   source_status(status,at)
 def test_noncausal_source_timestamp_refused(self):
  status,at=monitor_fixture(-100)
  with self.assertRaisesRegex(CrosshostHold,"MONITOR_STATE_NONCAUSAL_OR_TOO_OLD"):
   source_status(status,at)
 def test_original_quote_age_mismatch_refused(self):
  status,at=monitor_fixture()
  status["latest_quote_age_ms"]+=1
  with self.assertRaisesRegex(CrosshostHold,"MONITOR_QUOTE_TIMESTAMP_CHANGED"):
   source_status(status,at)

class Physical1005Tests(unittest.TestCase):
 def setUp(self):
  src=os.environ.get("FX_1005_PHYSICAL_ARCHIVE")
  if not src:self.skipTest("private original and cache receipts not mounted")
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/"evidence"
  shutil.copytree(src,self.root,ignore=shutil.ignore_patterns("independent-1005-*"))
  self.root.chmod(0o700)
  for p in self.root.rglob("*"):p.chmod(0o700 if p.is_dir() else 0o600)
 def tamper(self,file,key,value):
  p=self.root/file
  record=json.loads(p.read_text())
  record[key]=value
  p.write_text(json.dumps(record,sort_keys=True))
  p.chmod(0o600)
 def test_physical_original_and_cache_both_24_usable(self):
  result=verify(self.root)
  self.assertEqual(result["original_entry_state"],"CENSORED")
  self.assertEqual(result["original_entry_health_age_ms"],85031)
  a=result["source_health_exact_time_comparison"]
  self.assertEqual(a["baseline"]["age_ms"],86092)
  self.assertEqual(a["canary"]["age_ms"],18000)
  self.assertFalse(a["baseline"]["healthy_under_frozen_monitor_rules"])
  self.assertTrue(a["canary"]["healthy_under_frozen_monitor_rules"])
  self.assertFalse(result["production_sla_rollout_authorized"])
  self.assertEqual(result["broker_queries_or_orders_sent_by_auditor"],0)
 def test_changed_initial_plan_refused(self):
  self.tamper("original-plan-pretrial.json","source_minimum_usable",1)
  with self.assertRaisesRegex(CrosshostHold,"PRETRIAL_PHYSICAL_HOST_PIN_CHANGED"):
   verify(self.root)
 def test_changed_cache_plan_refused(self):
  self.tamper("canary-plan-pretrial.json","exact_refresh_seconds",1)
  with self.assertRaisesRegex(CrosshostHold,"PRETRIAL_PHYSICAL_HOST_PIN_CHANGED"):
   verify(self.root)
 def test_relabel_original_censored_refused(self):
  self.tamper("original-entry-attempt.json","state","FROZEN_ORIGINAL_PRICE_ONLY")
  with self.assertRaisesRegex(CrosshostHold,"ORIGINAL_RESEARCH_ENTRY_RELABELED"):
   verify(self.root)
 def test_fake_baseline_timestamp_refused(self):
  self.tamper("comparison-original.json","attempt_at_ms",1791857104600)
  with self.assertRaisesRegex(CrosshostHold,"FROZEN_COMPARISON_CHANGED"):
   verify(self.root)
 def test_fake_canary_healthy_claim_refused(self):
  p=self.root/"comparison-original.json"
  record=json.loads(p.read_text())
  record["canary"]["healthy_at_exact_time"]=False
  p.write_text(json.dumps(record));p.chmod(0o600)
  with self.assertRaisesRegex(CrosshostHold,"FUTURE_HEALTH_REPORTED_VS_RECOMPUTED_MISMATCH"):
   verify(self.root)
 def test_falsified_canary_probe_refused(self):
  p=self.root/"canary-health-original.json"
  record=json.loads(p.read_text());record["probes"][0]["status"]="PATH_GAP"
  p.write_text(json.dumps(record));p.chmod(0o600)
  with self.assertRaisesRegex(CrosshostHold,"READINESS_PROBE_COUNTS_CHANGED"):
   verify(self.root)
 def test_falsified_canary_source_prefix_refused(self):
  p=self.root/"canary-health-original.json"
  record=json.loads(p.read_text())
  record["source_prefixes"].append({"sha256":"e"*64,"bytes":1})
  p.write_text(json.dumps(record));p.chmod(0o600)
  with self.assertRaisesRegex(CrosshostHold,"SOURCE_PROVENANCE_DIGEST_CHANGED"):
   verify(self.root)
 def test_false_stale_baseline_health_replacement_refused(self):
  self.tamper("baseline-health-original.json","at_ms",1791857100000)
  with self.assertRaisesRegex(CrosshostHold,"ORIGINAL_HEALTH_CUSTODY_NOT_MATCHING_COMPARISON"):
   verify(self.root)
 def test_missing_canary_status_refused(self):
  (self.root/"canary-health-original.json").unlink()
  with self.assertRaises(FileNotFoundError):verify(self.root)
if __name__=="__main__":unittest.main()
