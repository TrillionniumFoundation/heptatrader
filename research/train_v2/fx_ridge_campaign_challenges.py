"""Challenge rehashed copies of a previously MATURED original Ridge sample.

Not an independent broker feed; no writes to the genuine source or running
four-window cohort. All challenges are ephemeral private temp copies.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, tempfile
from pathlib import Path

from .fx_ridge_campaign_independent import (
    AuditReject, canonical, sha, strict,
    verify_original_decision, verify_mature_result
)

SOURCE_FILES=("preoutcome-decision.json","qualification.json","manifest.json",
              "model.json","mature-outcome.json")

def run(original_root,cost_path):
    original_root=Path(original_root)
    cost_path=Path(cost_path)
    plan=strict(original_root/"plan.json")
    manifest=strict(original_root/"manifest.json")
    item={
      "plan_id":plan["plan_id"],"anchor_ms":manifest["anchor_ms"],
      "first_decision_ms":plan["first_decision_ms"],
      "last_decision_ms":plan["last_decision_ms"],
      "outcome_due_ms":manifest["outcome_due_ms"]}
    intent={"cost_path":str(cost_path)}
    original_decision,original_audit=verify_original_decision(
        original_root,item,intent)
    original_maturity=verify_mature_result(
        original_root,item,intent,original_decision)
    tests=[
      ("MODEL_FORECAST_REHASH", "decision", lambda r:
       r.update(forecast_midpoint_bps=r["forecast_midpoint_bps"]+1)),
      ("PRICE_FEATURE_REHASH", "decision", lambda r:
       r["feature_vector"].__setitem__(0,r["feature_vector"][0]+1)),
      ("SOURCE_ID_REHASH", "decision", lambda r:
       r["history_quote_samples"][-1].update(source_id="f"*64)),
      ("ACTION_REHASH", "decision", lambda r:
       r.update(selected_trading_action="BUY")),
      ("COST_HURDLE_REHASH", "decision", lambda r:
       r["cost_hurdle"].update(required_absolute_prediction_bps=0)),
      ("MATURE_FIRST_QUOTE_REHASH", "maturity", lambda r:
       r.update(outcome_quote_sha256="f"*64)),
      ("MATURE_PRICE_REHASH", "maturity", lambda r:
       r.update(observed_original_midpoint_return_bps="999999")),
      ("MATURE_DIRECTION_REHASH", "maturity", lambda r:
       r.update(research_direction_correct=not r["research_direction_correct"])),
      ("MATURE_ACTION_REHASH", "maturity", lambda r:
       r.update(selected_trading_action="BUY")),
    ]
    results=[]
    for label,stage,mutator in tests:
        with tempfile.TemporaryDirectory(prefix="train-v2-adversarial-") as td:
            cell=Path(td)
            for name in SOURCE_FILES:
                shutil.copyfile(original_root/name,cell/name)
                (cell/name).chmod(0o600)
            target=(cell/"preoutcome-decision.json"
                    if stage=="decision" else cell/"mature-outcome.json")
            doc=strict(target)
            mutator(doc)
            target.write_bytes(canonical(doc)+b"\n")
            if stage=="decision":
                # A hostile operator can recompute its research-local checksums.
                # A real earlier source and pinned model must still refuse it.
                q=strict(cell/"qualification.json")
                q["decision_sha256"]=sha(target)
                (cell/"qualification.json").write_bytes(canonical(q)+b"\n")
                mature=strict(cell/"mature-outcome.json")
                mature["predecision_sha256"]=sha(target)
                (cell/"mature-outcome.json").write_bytes(
                     canonical(mature)+b"\n")
            try:
                actual,_=verify_original_decision(cell,item,intent)
                verify_mature_result(cell,item,intent,actual)
            except (AuditReject,ValueError,KeyError,TypeError) as exc:
                results.append({
                  "case":label,"refused":True,
                  "denial":str(exc)[:135]})
            else:
                raise AuditReject("MALICIOUS_COPIED_SOURCE_WRONGLY_ADMITTED:"+label)
    report={
      "schema":"heptatrader.fx-ridge-independent-real-copy-attacks.v1",
      "original_reference_predecision_sha256":
         sha(original_root/"preoutcome-decision.json"),
      "original_reference_maturity_sha256":
         sha(original_root/"mature-outcome.json"),
      "original_frozen_prediction_reproduced_bps":
         original_audit["actual_prediction_bps"],
      "original_mature_price_reproduced_bps":
         original_maturity["observed_original_return_bps"],
      "original_trading_action":"NO_TRADE",
      "attack_count":len(results),
      "all_forged_rehashed_copies_refused":all(x["refused"] for x in results),
      "attacks":results,
      "original_files_mutated":False,
      "temp_copies_durable_results":False,
      "real_native_owner_admitted":False,
      "after_cost_edge_proven":False,
      "broker_orders_sent":0,
      "trade_authority":False,
    }
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--prior-original-root",type=Path,required=True)
    p.add_argument("--cost",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    os.umask(0o077)
    data=run(a.prior_original_root,a.cost)
    out=a.output
    if out.exists() or out.is_symlink():
        raise AuditReject("IMMUTABLE_ATTACK_PROOF_DESTINATION_REQUIRED")
    out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    raw=(json.dumps(data,indent=2,sort_keys=True)+"\n").encode()
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    print(json.dumps({"attack_count":data["attack_count"],
      "all_refused":data["all_forged_rehashed_copies_refused"],
      "broker_orders_sent":0,"receipt_sha256":hashlib.sha256(raw).hexdigest()}))

if __name__=="__main__":
    main()
