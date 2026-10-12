"""Adversarial tests of independent, read-only finite campaign verification."""
from __future__ import annotations
import copy, json, os, tempfile, unittest
from pathlib import Path
from train_v2.fx_ridge_campaign_independent import (
    SCHEMA, INTERVAL_MS, HORIZON_MS, MATURITY_DELAY_MS, AuditReject,
    audit_cell, canonical, digest, planned_anchors,
    replay_exact_original_prefixes, sha, strict, verify_four_calendar,
    verify_original_decision, verify_mature_result
)

START=1791798958028
def frozen_calendar(start=START):
    anchors=planned_anchors(start)
    intent={
      "schema":SCHEMA,"registered_operator_start_ms":start,
      "anchors_ms":anchors,"fixed_count":4,
      "interval_ms":INTERVAL_MS,"outcome_wait_ms":MATURITY_DELAY_MS,
      "shadow_only":True,"automatic_training":False,
      "model_promotion_allowed":False,"trade_authority":False,
      "broker_requests_sent":0,"broker_orders_sent":0}
    sealed={
      "schema":SCHEMA+".sealed.v1",
      "sealed_at_ms":start+50,
      "all_registered_before_first_decision":True,
      "fixed_model_across_all_windows":True,
      "outcomes_known_at_registration":False,
      "trading_or_promotion_authority":False,
      "windows":[{
       "index":i,"anchor_ms":anchor,
       "first_decision_ms":anchor+1000,
       "last_decision_ms":anchor+4500,
       "outcome_due_ms":anchor+HORIZON_MS,
       "receipt_sealed_at_ms":start+51+i,
       "plan_id":f"{i+1:064x}",
       "model_sha256":"a"*64
      } for i,anchor in enumerate(anchors)]
    }
    return intent,sealed

class CalendarProofTests(unittest.TestCase):
    def setUp(self):
        self.intent,self.sealed=frozen_calendar()
    def run_check(self):
        return verify_four_calendar(self.intent,self.sealed,
            time_ms=self.sealed["sealed_at_ms"]+1000)
    def test_exact_four_nonoverlapping(self):
        self.assertEqual(len(self.run_check()),4)
    def test_first_grid_is_before_known_future_not_latest_chosen(self):
        self.assertEqual(self.intent["anchors_ms"],planned_anchors(START))
        self.assertEqual([b-a for a,b in zip(
            self.intent["anchors_ms"],self.intent["anchors_ms"][1:])],
                         [INTERVAL_MS]*3)
    def test_forged_anchor_and_rehashed_intent_rejected(self):
        self.intent["anchors_ms"][0]+=300000
        with self.assertRaisesRegex(AuditReject,"PREDECLARED_GRID_CHANGED"):
            self.run_check()
    def test_repeated_plan_id_does_not_gain_second_sample(self):
        self.sealed["windows"][2]["plan_id"]=self.sealed["windows"][0]["plan_id"]
        with self.assertRaisesRegex(AuditReject,"CAMPAIGN_PLAN_ID_REUSED"):
            self.run_check()
    def test_unqualified_model_alteration_forbidden(self):
        self.sealed["windows"][1]["model_sha256"]="b"*64
        with self.assertRaisesRegex(AuditReject,"CAMPAIGN_MODELS_NOT_FROZEN"):
            self.run_check()
    def test_changed_label_horizon_rejected(self):
        self.sealed["windows"][0]["outcome_due_ms"]+=1
        with self.assertRaisesRegex(AuditReject,"CAMPAIGN_REVISED_OR_UNREGISTERED"):
            self.run_check()
    def test_registration_less_than_30s_not_promoted(self):
        self.sealed["windows"][3]["receipt_sealed_at_ms"]=(
            self.sealed["windows"][3]["first_decision_ms"]-29999)
        with self.assertRaisesRegex(AuditReject,"CAMPAIGN_REVISED_OR_UNREGISTERED"):
            self.run_check()
    def test_trade_authority_ban(self):
        for k in ("automatic_training","model_promotion_allowed",
                  "trade_authority"):
            with self.subTest(flag=k):
                change=copy.deepcopy(self.intent)
                change[k]=True
                with self.assertRaisesRegex(AuditReject,
                                            "CAMPAIGN_EFFECT_AUTHORITY_FORBIDDEN"):
                    verify_four_calendar(change,self.sealed,
                          time_ms=START+1000)
    def test_wrong_window_population_rejected(self):
        self.sealed["windows"].pop()
        with self.assertRaisesRegex(AuditReject,"CAMPAIGN_WINDOW_CARDINALITY"):
            self.run_check()
    def test_false_already_known_outcome_rejected(self):
        self.sealed["outcomes_known_at_registration"]=True
        with self.assertRaisesRegex(AuditReject,"CAMPAIGN_TIMING_OR_AUTHORITY"):
            self.run_check()

class PureWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"campaign"
        self.root.mkdir(mode=0o700)
        self.cell=self.root/"window-00"
        self.cell.mkdir(mode=0o700)
        self.intent,self.sealed=frozen_calendar()
        self.item=self.sealed["windows"][0]
    def receipt(self,name,stage,state,stamp,reason="NONE"):
        r={
            "schema":SCHEMA+".attempt.v1",
            "stage":stage,"state":state,"reason_code":reason,
            "attempt_recorded_at_ms":stamp,"nonretryable":True,
            "trade_authority":False,"model_promotion_allowed":False,
            "broker_orders_sent":0}
        p=self.cell/name
        p.write_text(json.dumps(r,sort_keys=True)+"\n")
        p.chmod(0o600)
        return p
    def test_before_window_is_future_not_loss(self):
        r=audit_cell(self.root,self.item,self.intent,
            self.item["first_decision_ms"]-1)
        self.assertEqual(r["decision_state"],"FUTURE")
        self.assertEqual(r["maturity_state"],"NOT_ADMISSIBLE_NO_DECISION")
    def test_missing_postwindow_receipt_is_unknown_not_fabricated_censor(self):
        r=audit_cell(self.root,self.item,self.intent,
            self.item["last_decision_ms"]+1)
        self.assertEqual(r["decision_state"],
             "UNRESOLVED_AFTER_DEADLINE_NO_ATTEMPT_RECEIPT")
        self.assertNotEqual(r["decision_state"],"CENSORED")
    def test_original_censored_attempt_retained_as_failure(self):
        self.receipt("decision-attempt.json","decision","CENSORED",
            self.item["first_decision_ms"]+1500,
            "EXCEPTION_ResearchHold_LAST_ORIGINAL_QUOTE_TOO_OLD")
        r=audit_cell(self.root,self.item,self.intent,
            self.item["outcome_due_ms"]+9000)
        self.assertEqual(r["decision_state"],"CENSORED")
        self.assertEqual(r["maturity_state"],
                         "UNRESOLVED_AFTER_HORIZON_NO_ATTEMPT_RECEIPT")
        self.assertIn("TOO_OLD",r["censor_reason"])
    def test_censored_matured_receipt_is_never_trading_success(self):
        self.receipt("decision-attempt.json","decision","CENSORED",
            self.item["first_decision_ms"]+1000,"STALE")
        self.receipt("maturity-attempt.json","maturity","CENSORED",
            self.item["outcome_due_ms"]+7000,"NO_PREMATURE_VALID_FROZEN_DECISION")
        r=audit_cell(self.root,self.item,self.intent,
            self.item["outcome_due_ms"]+7100)
        self.assertEqual(r["maturity_state"],"CENSORED")
        self.assertFalse(r["promotion_allowed"])
    def test_censored_with_fabricated_mature_file_rejected(self):
        self.receipt("decision-attempt.json","decision","CENSORED",
            self.item["first_decision_ms"]+1100)
        (self.cell/"mature-outcome.json").write_text("{}")
        with self.assertRaisesRegex(AuditReject,
                                    "CENSORED_DECISION_HAS_SUCCESSFUL"):
            audit_cell(self.root,self.item,self.intent,
                self.item["outcome_due_ms"]+7000)
    def test_missing_original_decision_cannot_be_frozen(self):
        self.receipt("decision-attempt.json","decision","FROZEN_PRICE_ONLY",
            self.item["first_decision_ms"]+1500)
        with self.assertRaises(AuditReject):
            audit_cell(self.root,self.item,self.intent,
                self.item["first_decision_ms"]+1700)
    def test_early_maturity_cannot_be_certified(self):
        self.receipt("decision-attempt.json","decision","CENSORED",
            self.item["first_decision_ms"]+1000)
        self.receipt("maturity-attempt.json","maturity","CENSORED",
            self.item["outcome_due_ms"]+1000)
        with self.assertRaisesRegex(AuditReject,"MATURITY_ATTEMPT_INVALID"):
            audit_cell(self.root,self.item,self.intent,
                self.item["outcome_due_ms"]+7000)
    def test_pending_maturity_not_completed(self):
        self.receipt("decision-attempt.json","decision","CENSORED",
            self.item["first_decision_ms"]+1000)
        r=audit_cell(self.root,self.item,self.intent,
             self.item["outcome_due_ms"]+1000)
        self.assertEqual(r["maturity_state"],"FUTURE")
    def test_duplicate_json_field_fails_closed(self):
        p=self.cell/"dup.json"
        p.write_text('{"x":1,"x":2}')
        with self.assertRaisesRegex(AuditReject,"DUPLICATE_JSON_FIELD"):
            strict(p)

    def test_future_dated_decision_cannot_be_observed(self):
        self.receipt("decision-attempt.json","decision","CENSORED",
            self.item["first_decision_ms"]+3000)
        with self.assertRaisesRegex(AuditReject,"DECISION_ATTEMPT_INVALID"):
            audit_cell(self.root,self.item,self.intent,
                self.item["first_decision_ms"]+1000)

    def test_future_dated_maturity_receipt_cannot_be_observed(self):
        self.receipt("decision-attempt.json","decision","CENSORED",
            self.item["first_decision_ms"]+1000)
        self.receipt("maturity-attempt.json","maturity","CENSORED",
            self.item["outcome_due_ms"]+9000)
        with self.assertRaisesRegex(AuditReject,"MATURITY_ATTEMPT_INVALID"):
            audit_cell(self.root,self.item,self.intent,
                self.item["outcome_due_ms"]+7000)

class ExactOriginalPrefixTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"original"
        self.root.mkdir(mode=0o700)
        self.segment=self.root/"segment-1800000000000000-lab"
        self.segment.mkdir(mode=0o700)
        self.path=self.segment/"quotes.jsonl"
        self.rows=[]
        base=1800000000000
        entries=[
           ("HEADER",{
             "execution_domain":"PAPER","instrument":"EUR.USD",
             "broker_orders_sent":0,"write_operations_allowed":[]},
             base-10000),
           ("QUOTE",{
             "observed_at_ms":base-1000,
             "stale_after_ms":base+3500,
             "source":"IB","authoritative":True,"stale":False,
             "bid":"1.20000","ask":"1.20002"},base-800),
           ("QUOTE",{
             "observed_at_ms":base+1000,
             "stale_after_ms":base+4500,
             "source":"IB","authoritative":True,"stale":False,
             "bid":"1.20005","ask":"1.20007"},base+1200),
        ]
        predecessor="0"*64
        for index,(kind,data,at) in enumerate(entries):
            record={"schema":"heptatrader.laya-shadow-quotes.v1",
                    "sequence":index,"previous":predecessor,
                    "at_ms":at,"kind":kind,"data":data}
            record["sha256"]=digest(record)
            predecessor=record["sha256"]
            self.rows.append(record)
        self.raw=b"\n".join(canonical(x) for x in self.rows)+b"\n"
        self.path.write_bytes(self.raw)
        self.path.chmod(0o600)
        self.receipt=self.proof()

    def proof(self):
        return {"path":str(self.path),
                "complete_prefix_bytes":self.path.stat().st_size,
                "prefix_sha256":sha(self.path),
                "chain_head":self.rows[-1]["sha256"]}

    def test_exact_complete_original_chain(self):
        original=replay_exact_original_prefixes([self.receipt],self.root)
        self.assertEqual(len(original),2)
        self.assertIn(self.rows[1]["sha256"],original)
    def test_last_byte_mutation_denied(self):
        self.path.write_bytes(self.raw[:-5]+b"abcd\n")
        with self.assertRaisesRegex(AuditReject,"PREFIX_SHA_MISMATCH"):
            replay_exact_original_prefixes([self.receipt],self.root)
    def test_prefix_length_cut_rejected(self):
        bad=dict(self.receipt)
        bad["complete_prefix_bytes"]-=1
        with self.assertRaisesRegex(AuditReject,"PREFIX_SHA_MISMATCH"):
            replay_exact_original_prefixes([bad],self.root)
    def test_truncated_original_file_refused(self):
        self.path.write_bytes(self.raw[:-22])
        with self.assertRaisesRegex(AuditReject,"PREFIX_CUSTODY_CHANGED"):
            replay_exact_original_prefixes([self.receipt],self.root)
    def test_unapproved_copy_location_refused(self):
        with self.assertRaisesRegex(AuditReject,"PREFIX_PATH_UNSAFE"):
            replay_exact_original_prefixes([self.receipt],
                                          self.root/"different-owner")
    def test_source_symlink_refused(self):
        replacement=self.segment/"other.jsonl"
        self.path.rename(replacement)
        self.path.symlink_to(replacement)
        with self.assertRaisesRegex(AuditReject,"PREFIX_PATH_UNSAFE"):
            replay_exact_original_prefixes([self.receipt],self.root)
    def test_bogus_chain_head_refused(self):
        bad=dict(self.receipt)
        bad["chain_head"]="f"*64
        with self.assertRaisesRegex(AuditReject,"CHAIN_HEAD_CHANGED"):
            replay_exact_original_prefixes([bad],self.root)
    def test_duplicate_quote_receipt_in_two_snapshots_refused(self):
        with self.assertRaisesRegex(AuditReject,"DUPLICATE_RECEIPT"):
            replay_exact_original_prefixes(
                [self.receipt,self.receipt],self.root)
    def test_unchanged_sha_but_rehashed_malformed_authority_refused(self):
        modified=copy.deepcopy(self.rows)
        modified[0]["data"]["broker_orders_sent"]=1
        prior="0"*64
        for row in modified:
            row["previous"]=prior
            row["sha256"]=digest({k:v for k,v in row.items() if k!="sha256"})
            prior=row["sha256"]
        self.path.write_bytes(b"\n".join(canonical(x) for x in modified)+b"\n")
        fake=self.proof()
        fake["chain_head"]=prior
        with self.assertRaisesRegex(AuditReject,"SCOPE_NOT_PAPER"):
            replay_exact_original_prefixes([fake],self.root)

if __name__=="__main__":
    unittest.main()

class SyntheticFullCampaignTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        from train_v2.adapters import make_spec
        from train_v2.orchestrator import register_plan
        self.td=tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root=Path(self.td.name)/"campaign"
        self.root.mkdir(mode=0o700)
        self.reg=self.root/"registry"
        self.reg.mkdir(mode=0o700)
        (self.reg/"sealed-plans").mkdir(mode=0o700)
        self.intent,self.sealed=frozen_calendar()
        period="a"*64
        model={
         "schema":"heptatrader.fx-ridge-historical-model.v1",
         "trained_rows":41243,"historical_validation_selected_action":"NO_TRADE",
         "candidate_has_positive_validation_edge":False,"promotion_allowed":False,
         "weights":[0.]*9,"mean":[0.]*8,"stddev":[1.]*8}
        def write(path,obj):
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(canonical(obj)+b"\n")
            path.chmod(0o600)
        self.db=sqlite3.connect(self.reg/"registrations.sqlite")
        self.addCleanup(self.db.close)
        self.db.execute("""CREATE TABLE registrations(
         plan_id TEXT PRIMARY KEY,period_sha256 TEXT,instrument TEXT,
         feature_spec_id TEXT,policy_id TEXT,first_ms INT,last_ms INT,
         sealed_at_ms INT,receipt_sha256 TEXT,registration_json TEXT)""")
        self.intent["original_period_sha256"]=period
        self.intent["historical_model_sha256"]=digest(model)
        spec=make_spec(period,"SYNTHETIC_RESEARCH_ONLY",
           horizon_ms=HORIZON_MS,lookback=13,broker_reported_expiry=True)
        for idx,x in enumerate(self.sealed["windows"]):
            cell=self.root/("window-%02d"%idx)
            cell.mkdir(mode=0o700)
            plan=register_plan(spec=spec,registered_at_ms=START+idx,
                first_decision_ms=x["first_decision_ms"],
                last_decision_ms=x["last_decision_ms"],minimum_mature_rows=16)
            registration={
                "schema":"heptatrader.train-v2-local-prospective-registration.v1",
                "plan_id":plan["plan_id"],"plan":plan,"feature_spec":spec,
                "original_source_root":"/synthetic/no-real-broker",
                "sealed_at_ms":START+51+idx,"trade_authority":False,
                "canonical_agentd_owner_enrolled":False}
            manifest={
             "anchor_ms":x["anchor_ms"],
             "outcome_due_ms":x["outcome_due_ms"],
             "historical_model_sha256":digest(model),
             "code_sha256":"b"*64,
             "cost_receipt_sha256":"c"*64,
             "original_period_sha256":period,
             "policy_id":plan["behavior_policy_id"],
             "source_kind":"LOCAL_SHA_CHAINED_ORIGINAL_IB_PAPER",
             "model_promotion_allowed":False,
             "trade_authority":False}
            files={"model.json":model,"manifest.json":manifest,
                "feature-spec.json":spec,"plan.json":plan,
                "local-registration.json":registration}
            for name,obj in files.items():write(cell/name,obj)
            x["plan_id"]=plan["plan_id"]
            x["model_sha256"]=sha(cell/"model.json")
            for name,field in (("manifest.json","manifest_sha256"),
                  ("feature-spec.json","spec_sha256"),
                  ("plan.json","plan_sha256"),
                  ("local-registration.json","registration_sha256")):
                x[field]=sha(cell/name)
            sealpath=self.reg/"sealed-plans"/(plan["plan_id"]+".json")
            write(sealpath,registration)
            self.db.execute("INSERT INTO registrations VALUES(?,?,?,?,?,?,?,?,?,?)",
             (plan["plan_id"],period,"EUR.USD",plan["feature_spec_id"],
              plan["behavior_policy_id"],x["first_decision_ms"],
              x["last_decision_ms"],registration["sealed_at_ms"],sha(sealpath),
              json.dumps(registration,sort_keys=True,separators=(",",":"))))
        self.db.commit()
        self.intent["original_model_file_sha256"]=self.sealed["windows"][0][
            "model_sha256"]
        self.intent["cost_sha256"]="c"*64
        self.intent["original_source_code_sha256"]="b"*64
        write(self.root/"campaign-intent.json",self.intent)
        self.sealed["intent_sha256"]=sha(self.root/"campaign-intent.json")
        write(self.root/"campaign-sealed.json",self.sealed)

    def test_four_registered_exact_model_spec_policy_and_database(self):
        from train_v2.fx_ridge_campaign_independent import verify_registered_windows
        self.assertEqual(
          verify_registered_windows(self.root,self.intent,self.sealed),4)
    def test_extra_fifth_registry_row_denied(self):
        from train_v2.fx_ridge_campaign_independent import verify_registered_windows
        self.db.execute("INSERT INTO registrations VALUES(?,?,?,?,?,?,?,?,?,?)",
          ("f"*64,"a"*64,"EUR.USD","e"*64,"REUSE",1,2,3,"f"*64,"{}"))
        self.db.commit()
        with self.assertRaisesRegex(AuditReject,"REGISTRY_WRONG_POPULATION"):
            verify_registered_windows(self.root,self.intent,self.sealed)
    def test_pinned_model_copy_tampering_refused(self):
        from train_v2.fx_ridge_campaign_independent import verify_registered_windows
        (self.root/"window-01"/"model.json").write_text('{"changed":true}\n')
        with self.assertRaisesRegex(AuditReject,"FROZEN_CELL_FILE_CHANGED"):
            verify_registered_windows(self.root,self.intent,self.sealed)
    def test_missing_original_registration_receipt_denied(self):
        from train_v2.fx_ridge_campaign_independent import verify_registered_windows
        p=self.reg/"sealed-plans"/(self.sealed["windows"][0]["plan_id"]+".json")
        p.unlink()
        with self.assertRaisesRegex(AuditReject,"ORIGINAL_REGISTRY_SEAL_CHANGED"):
            verify_registered_windows(self.root,self.intent,self.sealed)
    def test_changed_registry_row_denied_even_with_same_plan(self):
        from train_v2.fx_ridge_campaign_independent import verify_registered_windows
        id=self.sealed["windows"][2]["plan_id"]
        self.db.execute("UPDATE registrations SET first_ms=first_ms+1 WHERE plan_id=?",(id,))
        self.db.commit()
        with self.assertRaisesRegex(AuditReject,"REGISTRY_SEAL_OR_DATABASE_DRIFT"):
            verify_registered_windows(self.root,self.intent,self.sealed)
    def test_changed_feature_spec_policy_denied(self):
        from train_v2.fx_ridge_campaign_independent import verify_registered_windows
        p=self.root/"window-02"/"feature-spec.json"
        obj=json.loads(p.read_text())
        obj["behavior_policy_id"]="NEW_HISTORY_SELECTED"
        p.write_bytes(canonical(obj)+b"\n")
        x=self.sealed["windows"][2];x["spec_sha256"]=sha(p)
        with self.assertRaisesRegex(AuditReject,"FROZEN_FEATURE_SPEC_CHANGED"):
            verify_registered_windows(self.root,self.intent,self.sealed)
