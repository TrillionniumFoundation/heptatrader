"""Cross-physical-host offline verification of frozen four-window FX Ridge evidence.

Research-only. Pinned campaign seal/final from the original observation.
Never contacts IB, imports a trading runtime, trains or promotes a model.
Input: a small original metadata ZIP + 3 portable original-quote model ZIPs.
On Mac and X230, reuse the separately inspected stdlib-only single-window
source/feature/forecast/outcome verifier and independently aggregate all four
windows, including the permanently censored window 00.
"""
from __future__ import print_function
import argparse
from decimal import Decimal, localcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import time
import zipfile

SCHEMA = "heptatrader.fx-ridge-four-window-physical-bundle.v1"
EXPECTED_SEAL_SHA256 = "e5da7854edfe2af4ab00a32ba0b7dc3cf1a41891e1010455de25ddf07c1c28dc"
EXPECTED_FINAL_SHA256 = "448a043e1904192882d1a846132a627893cb3b5e8003d4909ca86a6ca224351e"
EXPECTED_INDEPENDENT_AUDIT_SHA256 = "af46b3c9f3028cac98e790e75a67c0919f4ba0ad192ab9477abb770580ce7a4c"
EXPECTED_OFFLINE_VERIFIER_SHA256 = "ad29a9086226c547a9bbef7e474b08c5b39a9bb12bfc04a9408dd804c3365e3f"
EXPECTED_METADATA_BUNDLE_SHA256 = "dc2d3c416385dccb0561e63b9c2538f5ba6f5947a3c078ef3ec37e85caa5c5f7"
FIRST_ANCHOR_MS = 1791799800000
STEP_MS = 1200000
HORIZON_MS = 900000
REQUIRED_BPS = Decimal("10.9133557")
MAX_BUNDLE_BYTES = 2*1024*1024
MAX_EPISODE_BYTES = 4*1024*1024

class PhysicalAuditHold(ValueError): pass
def need(condition, error):
    if not condition: raise PhysicalAuditHold(error)
def sha(data):
    return hashlib.sha256(data).hexdigest()
def strict(data):
    def pairs(values):
        obj={}
        for k,v in values:
            need(k not in obj, "DUPLICATE_JSON_KEY")
            obj[k]=v
        return obj
    def bad(value): raise PhysicalAuditHold("NONFINITE_JSON")
    obj=json.loads(data, object_pairs_hook=pairs, parse_constant=bad)
    need(type(obj) is dict, "JSON_OBJECT_REQUIRED")
    return obj
def private_read(path, cap):
    path=Path(path)
    need(path.is_absolute() and not path.is_symlink(), "INPUT_PATH_SCOPE")
    fd=os.open(str(path), os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
             and st.st_nlink==1 and not st.st_mode&0o077
             and 0<st.st_size<=cap, "PRIVATE_INPUT_BOUNDS")
        data=b""
        while len(data)<st.st_size:
            chunk=os.read(fd,min(1024*1024,st.st_size-len(data)))
            need(bool(chunk), "INPUT_SHORT_READ")
            data+=chunk
        after=os.fstat(fd)
        need((st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)==
             (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),
             "INPUT_CHANGED_ON_READ")
        return data
    finally:
        os.close(fd)
def evidence_paths():
    paths=["campaign-intent.json","campaign-sealed.json",
           "campaign-worker-start.json","campaign-final.json",
           "final-independent-audit-v2.json"]
    shared=("model.json","manifest.json","plan.json","feature-spec.json",
            "local-registration.json","decision-attempt.json",
            "maturity-attempt.json")
    for i in range(4):
        paths.extend("window-%02d/%s"%(i,f) for f in shared)
        if i:
            paths.extend("window-%02d/%s"%(i,f) for f in (
                "preoutcome-decision.json","qualification.json",
                "mature-outcome.json"))
    return paths
def export(root, independent, episodes, verifier_source, output):
    root,independent,episodes=map(Path,(root,independent,episodes))
    artifacts={}
    for name in evidence_paths():
        if name=="final-independent-audit-v2.json":
            p=independent/name
        else:p=root/name
        artifacts["artifacts/"+name]=private_read(p,150000)
    need(sha(artifacts["artifacts/campaign-sealed.json"])==EXPECTED_SEAL_SHA256,
         "CAMPAIGN_SEAL_UNEXPECTED")
    need(sha(artifacts["artifacts/campaign-final.json"])==EXPECTED_FINAL_SHA256,
         "CAMPAIGN_FINAL_UNEXPECTED")
    need(sha(artifacts["artifacts/final-independent-audit-v2.json"])==
         EXPECTED_INDEPENDENT_AUDIT_SHA256, "FROZEN_INDEPENDENT_RECEIPT_CHANGED")
    episode_hash={}
    for n in (1,2,3):
        data=private_read(episodes/("window-%02d-original.zip"%n),MAX_EPISODE_BYTES)
        episode_hash["%02d"%n]=sha(data)
    verifier_sha=sha(private_read(verifier_source,100000))
    need(verifier_sha==EXPECTED_OFFLINE_VERIFIER_SHA256,
         "UNPINNED_PHYSICAL_VERIFIER_CODE")
    info={"schema":SCHEMA,
          "campaign_seal_sha256":EXPECTED_SEAL_SHA256,
          "campaign_final_sha256":EXPECTED_FINAL_SHA256,
          "file_sha256":{k:sha(v) for k,v in sorted(artifacts.items())},
          "episode_zip_sha256":episode_hash,
          "offline_verifier_sha256":verifier_sha,
          "windows":[0,1,2,3],
          "broker_signed_source":False,
          "independent_product_owner_or_witness_enrolled":False,
          "trading_authority":False,
          "promotion_allowed":False,
          "broker_requests_or_orders_sent":0}
    artifacts["index.json"]=(json.dumps(info,sort_keys=True,indent=2)+"\n").encode()
    os.umask(0o077)
    output=Path(output)
    need(output.is_absolute() and output.parent.is_dir()
         and not output.parent.is_symlink(), "OUTPUT_PATH_SCOPE")
    fd=os.open(str(output),os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    os.close(fd)
    try:
        with zipfile.ZipFile(str(output),"w",zipfile.ZIP_DEFLATED,
                             compresslevel=3) as z:
            for name,body in sorted(artifacts.items()):
                info=zipfile.ZipInfo(name)
                info.external_attr=0o600<<16
                info.compress_type=zipfile.ZIP_DEFLATED
                z.writestr(info,body,compress_type=zipfile.ZIP_DEFLATED,
                           compresslevel=3)
        raw=private_read(output,MAX_BUNDLE_BYTES)
        need(sha(raw)==EXPECTED_METADATA_BUNDLE_SHA256,
             "NONREPRODUCIBLE_PINNED_CAMPAIGN_BUNDLE")
        return {"state":"FOUR_WINDOW_PORTABLE_METADATA_EXPORTED",
                "bundle_sha256":sha(raw), "bundle_bytes":len(raw),
                "windows":4,"episode_zip_sha256":episode_hash,
                "broker_orders_sent":0}
    except BaseException:
        output.unlink(missing_ok=True)
        raise
def read_bundle(path):
    data=private_read(path,MAX_BUNDLE_BYTES)
    need(sha(data)==EXPECTED_METADATA_BUNDLE_SHA256,
         "PINNED_CAMPAIGN_METADATA_BUNDLE_CHANGED")
    with zipfile.ZipFile(str(path)) as z:
        names=z.namelist()
        expected=["artifacts/"+name for name in evidence_paths()]
        need(len(names)==len(set(names))
             and set(names)==set(expected)|{"index.json"},
             "BUNDLE_FILES_MISSING_OR_UNKNOWN")
        need(all(not name.startswith("/") and ".." not in name.split("/")
                 for name in names),"BUNDLE_PATH_TRAVERSAL")
        info=strict(z.read("index.json"))
        need(info["schema"]==SCHEMA
             and info["campaign_seal_sha256"]==EXPECTED_SEAL_SHA256
             and info["campaign_final_sha256"]==EXPECTED_FINAL_SHA256
             and info["broker_signed_source"] is False
             and info["independent_product_owner_or_witness_enrolled"] is False
             and info["trading_authority"] is False
             and info["promotion_allowed"] is False
             and info["broker_requests_or_orders_sent"]==0
             and info["windows"]==[0,1,2,3]
             and set(info["episode_zip_sha256"])=={"01","02","03"}
             and set(info["file_sha256"])==set(expected)
             and info["offline_verifier_sha256"]==EXPECTED_OFFLINE_VERIFIER_SHA256,
             "BUNDLE_MANIFEST_SCOPE")
        result={}
        for name in expected:
            item=z.getinfo(name)
            need(0<item.file_size<150000,"BUNDLE_MEMBER_OVERSIZE")
            raw=z.read(name)
            need(sha(raw)==info["file_sha256"][name],"BUNDLE_MEMBER_CHANGED:"+name)
            result[name[len("artifacts/"):]]=raw
    need(sha(result["campaign-sealed.json"])==EXPECTED_SEAL_SHA256,
         "ORIGINAL_CAMPAIGN_SEAL_CHANGED")
    need(sha(result["campaign-final.json"])==EXPECTED_FINAL_SHA256,
         "ORIGINAL_CAMPAIGN_FINAL_CHANGED")
    need(sha(result["final-independent-audit-v2.json"])==
         EXPECTED_INDEPENDENT_AUDIT_SHA256,
         "ORIGINAL_INDEPENDENT_RECEIPT_CHANGED")
    return info,result,sha(data)
def decimal(value):
    try:v=Decimal(str(value))
    except Exception as exc:raise PhysicalAuditHold("DECIMAL_INVALID") from exc
    need(v.is_finite(),"DECIMAL_NONFINITE")
    return v
def source_verifier(path, expected_sha):
    raw=private_read(path,100000)
    need(sha(raw)==expected_sha==EXPECTED_OFFLINE_VERIFIER_SHA256,
         "OFFLINE_VERIFIER_CODE_CHANGED")
    spec=importlib.util.spec_from_file_location("pinned_original_quote_stdlib_checker",
                                                str(path))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
def check(data_bundle, episodes, verifier_path):
    info,raws,bundle_sha=read_bundle(Path(data_bundle))
    j=lambda name:strict(raws[name])
    intent,seal,worker,final,original_audit=(
        j(n) for n in ("campaign-intent.json","campaign-sealed.json",
                       "campaign-worker-start.json","campaign-final.json",
                       "final-independent-audit-v2.json"))
    need(len(seal["windows"])==len(final["window_results"])==4
         and final["successful_matured_count"]==3
         and final["censored_count"]==1 and final["window_count"]==4
         and seal["all_registered_before_first_decision"] is True
         and seal["fixed_model_across_all_windows"] is True
         and seal["intent_sha256"]==sha(raws["campaign-intent.json"])
         ==final["intent_sha256"]
         and worker["sealed_sha256"]==final["sealed_sha256"]
         ==EXPECTED_SEAL_SHA256
         and worker["automatic_retries"] is False
         and worker["no_order_api_or_model_promotion"] is True
         and final["automatic_promotion"] is False
         and final["model_economic_efficacy_proven"] is False
         and final["broker_orders_sent"]==0,
         "FOUR_WINDOW_SEAL_OR_COUNTS")
    need(intent["fixed_count"]==4 and intent["interval_ms"]==STEP_MS
         and intent["anchors_ms"]==
             [FIRST_ANCHOR_MS+i*STEP_MS for i in range(4)]
         and intent["execution_domain"]=="PAPER"
         and intent["shadow_only"] is True
         and intent["trade_authority"] is False
         and intent["automatic_training"] is False
         and intent["model_promotion_allowed"] is False
         and intent["original_validation_action"]=="NO_TRADE"
         and intent["no_historical_test_reselection"] is True
         and intent["independent_broker_signed_source"] is False
         and intent["canonical_owner_or_witness_enrolled"] is False
         and intent["trader_behavior_propensities_attested"] is False,
         "RESEARCH_ONLY_OR_FIXED_CALENDAR_VIOLATION")
    need(seal["sealed_at_ms"]<seal["windows"][0]["first_decision_ms"]
         and seal["sealed_at_ms"]<=worker["started_at_ms"]
         <seal["windows"][0]["first_decision_ms"]
         and final["completed_at_ms"]>=FIRST_ANCHOR_MS+3*STEP_MS+HORIZON_MS+6000,
         "REGISTRATION_AND_COMPLETION_TIME")
    need(original_audit["campaign_seal_sha256"]==EXPECTED_SEAL_SHA256
         and original_audit["campaign_final_sha256"]==EXPECTED_FINAL_SHA256
         and original_audit["eligible_price_only_predictions"]==3
         and original_audit["censored_windows"]==1
         and original_audit["direction_correct_count"]==1
         and original_audit["executed_trade_pnl_samples"]==0
         and original_audit["live_trading_alpha_proven"] is False
         and original_audit["trade_authority"] is False
         and original_audit["predictions_covering_frozen_cost_hurdle"]==0,
         "ORIGINAL_INDEPENDENT_AUDIT_SCOPE_CHANGED")
    module=source_verifier(Path(verifier_path),info["offline_verifier_sha256"])
    episode_root=Path(episodes)
    eligible=[]
    for pos,s in enumerate(seal["windows"]):
        prefix="window-%02d/"%pos
        f=final["window_results"][pos]
        plan=j(prefix+"plan.json")
        receipt=j(prefix+"local-registration.json")
        manifest=j(prefix+"manifest.json")
        decision_attempt=j(prefix+"decision-attempt.json")
        maturity_attempt=j(prefix+"maturity-attempt.json")
        need(s["index"]==f["window"]==pos
             and s["anchor_ms"]==FIRST_ANCHOR_MS+pos*STEP_MS
             and s["first_decision_ms"]==s["anchor_ms"]+1000
             and s["last_decision_ms"]==s["anchor_ms"]+4500
             and s["outcome_due_ms"]==s["anchor_ms"]+HORIZON_MS
             and s["plan_id"]==plan["plan_id"]==receipt["plan_id"]==f["plan_id"]
             and receipt["sealed_at_ms"]==s["receipt_sealed_at_ms"]
             and receipt["sealed_at_ms"]+30000<=s["first_decision_ms"]
             and receipt["trade_authority"] is False
             and receipt["broker_market_data_signed"] is False
             and receipt["canonical_agentd_owner_enrolled"] is False
             and receipt["independent_owner_signature"] is False
             and manifest["anchor_ms"]==s["anchor_ms"]
             and manifest["validation_trading_edge_qualified"] is False
             and s["model_sha256"]==info["file_sha256"]["artifacts/"+prefix+"model.json"],
             "PER_WINDOW_PRECOMMIT_OR_MODEL_MISMATCH")
        for key,filename in (
            ("registration_sha256","local-registration.json"),
            ("model_sha256","model.json"),
            ("manifest_sha256","manifest.json"),
            ("plan_sha256","plan.json"),
            ("spec_sha256","feature-spec.json"),
        ):
            need(s[key]==sha(raws[prefix+filename]),
                 "FROZEN_WINDOW_FILE_CHANGED")
        need(f["decision_attempt_sha256"]==sha(raws[prefix+"decision-attempt.json"])
             and f["maturity_attempt_sha256"]==sha(raws[prefix+"maturity-attempt.json"])
             and f["decision_state"]==decision_attempt["state"]
             and f["maturity_state"]==maturity_attempt["state"]
             and decision_attempt["stage"]=="decision"
             and maturity_attempt["stage"]=="maturity"
             and decision_attempt["nonretryable"] is True
             and maturity_attempt["nonretryable"] is True
             and decision_attempt["trade_authority"] is False
             and maturity_attempt["trade_authority"] is False
             and decision_attempt["broker_orders_sent"]==0
             and maturity_attempt["broker_orders_sent"]==0
             and decision_attempt["model_promotion_allowed"] is False
             and maturity_attempt["model_promotion_allowed"] is False,
             "NO_RETRY_OR_RECEIPT_IDENTITY_CHANGED")
        if pos==0:
            need(decision_attempt["state"]=="CENSORED"
                 and maturity_attempt["state"]=="CENSORED"
                 and decision_attempt["reason_code"]==
                     "EXCEPTION_ResearchHold_LAST_ORIGINAL_QUOTE_TOO_OLD"
                 and maturity_attempt["reason_code"]==
                     "NO_PREMATURE_VALID_FROZEN_DECISION"
                 and all(not n.startswith(prefix+"preoutcome-decision")
                         and not n.startswith(prefix+"mature-outcome")
                         for n in raws)
                 and not (episode_root/"window-00-original.zip").exists(),
                 "CENSORED_WINDOW_RETROACTIVELY_LABELLED")
            continue
        need(decision_attempt["state"]=="FROZEN_PRICE_ONLY"
             and maturity_attempt["state"]=="MATURE_PRICE_ONLY",
             "MATURE_WINDOW_STATE_CHANGED")
        d=j(prefix+"preoutcome-decision.json")
        qual=j(prefix+"qualification.json")
        out=j(prefix+"mature-outcome.json")
        need(d["selected_trading_action"]==out["selected_trading_action"]==
             qual["selected_trading_action"]=="NO_TRADE"
             and d["actual_execution_reward"] is False
             and d["forecast_magnitude_clears_cost"] is False
             and d["native_owner_current"] is False
             and qual["state"]=="PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE"
             and qual["decision_sha256"]==out["predecision_sha256"]==
                 sha(raws[prefix+"preoutcome-decision.json"])
             and qual["seal_at_ms"]<qual["last_original_quote_expiry_ms"]
             and s["first_decision_ms"]<=d["decision_at_ms"]<=
                 qual["seal_at_ms"]<=s["last_decision_ms"]
             and out["outcome_due_ms"]==s["outcome_due_ms"]
             and out["outcome_due_ms"]<=out["price_event_ms"]<=
                 out["outcome_due_ms"]+5000
             and out["model_promotion_allowed"] is False
             and out["observed_trading_pnl_attested"] is False,
             "MATURE_WINDOW_ECONOMIC_OR_CLOCK_CLAIM_CHANGED")
        zip_name="window-%02d-original.zip"%pos
        zip_path=episode_root/zip_name
        zip_bytes=private_read(zip_path,MAX_EPISODE_BYTES)
        need(sha(zip_bytes)==info["episode_zip_sha256"]["%02d"%pos],
             "PORTABLE_SOURCE_ZIP_CHANGED")
        with zipfile.ZipFile(str(zip_path)) as z:
            for short_name in ("model","manifest","plan","feature-spec",
                               "local-registration","preoutcome-decision",
                               "qualification","mature-outcome"):
                original=z.read("artifacts/"+short_name+".json")
                need(original==raws[prefix+short_name+".json"],
                     "WINDOW_AND_ORIGINAL_SOURCE_BUNDLE_DIFFER")
        replay=module.check(str(zip_path))
        need(replay["state"]=="MAC_INDEPENDENT_FROZEN_SOURCE_AND_PRICE_RECOMPUTED"
             and replay["broker_signed_source"] is False
             and replay["production_witness_admitted"] is False
             and replay["model_economic_alpha_proven"] is False
             and replay["trade_authority"] is False
             and replay["broker_orders_sent"]==0
             and replay["selected_action"]=="NO_TRADE",
             "PHYSICAL_HOST_SOURCE_OR_PRODUCT_PROOF_INVALID")
        pred=decimal(replay["frozen_model_prediction_bps"])
        observed=decimal(replay["observed_original_midpoint_return_bps"])
        original_pred=decimal(d["forecast_midpoint_bps"])
        original_price=decimal(out["observed_original_midpoint_return_bps"])
        need(abs(pred-original_pred)<Decimal("1e-9")
             and observed==original_price
             and abs((pred-observed)-decimal(out["forecast_error_bps"]))<Decimal("1e-9")
             and abs(decimal(replay["cost_required_bps"])-REQUIRED_BPS)<Decimal("1e-8")
             and abs(original_pred)<REQUIRED_BPS,
             "INDEPENDENT_NUMERIC_OR_COST_RESULT_CHANGED")
        correct=(pred>0 and observed>0) or (pred<0 and observed<0)
        need(correct is out["research_direction_correct"],"DIRECTION_LABEL_CHANGED")
        eligible.append({"window":pos,"pred":str(original_pred),
                         "observed":str(observed),
                         "abs_error":str(abs(original_pred-observed)),
                         "direction_correct":correct,
                         "replayed_original_quote_sources":
                             replay["original_quote_prefix_count"]})
    need(len(eligible)==3,"ELIGIBLE_DENOMINATOR_CHANGED")
    with localcontext() as ctx:
        ctx.prec=45
        mae=sum((decimal(r["abs_error"]) for r in eligible),Decimal(0))/Decimal(3)
    correct=sum(r["direction_correct"] is True for r in eligible)
    need(correct==1
         and abs(mae-decimal(original_audit["mean_abs_forecast_error_bps"]))
             <Decimal("1e-12"),"CROSSHOST_STATISTICS_CHANGED")
    return {
        "schema":"heptatrader.fx-ridge-four-window-physical-verified.v1",
        "verified_at_ms":int(time.time()*1000),
        "verifier_host":os.uname().nodename,
        "campaign_metadata_bundle_sha256":bundle_sha,
        "campaign_seal_sha256":EXPECTED_SEAL_SHA256,
        "campaign_final_sha256":EXPECTED_FINAL_SHA256,
        "all_four_original_windows_checked":True,
        "initial_censored_window_preserved":True,
        "eligible_price_only_predictions":3,
        "censored_windows":1,
        "direction_correct_count":correct,
        "mean_abs_forecast_error_bps":str(mae),
        "zero_trade_qualified_prediction_count":3,
        "original_quote_prefixes_recomputed":sum(r["replayed_original_quote_sources"]
                                                  for r in eligible),
        "broker_signed_market_source":False,
        "independent_product_owner_or_witness_enrolled":False,
        "native_product_dataset_qualified":False,
        "actual_after_cost_execution_pnl_observed":False,
        "model_trading_alpha_proven":False,
        "automatic_training_or_promotion":False,
        "trade_authority":False,
        "broker_queries_or_orders_sent":0,
        "windows":eligible,
    }
def write_new(path,data):
    path=Path(path)
    need(path.is_absolute() and path.parent.is_dir() and not path.parent.is_symlink(),
         "OUTPUT_PARENT_PATH")
    st=path.parent.stat()
    need(st.st_uid==os.geteuid() and stat.S_IMODE(st.st_mode)==0o700,
         "OUTPUT_PRIVATE_DIRECTORY_REQUIRED")
    raw=(json.dumps(data,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
    fd=os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw)
        f.flush();os.fsync(f.fileno())
    return sha(raw)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",required=True,choices=("export","verify"))
    p.add_argument("--root",type=Path)
    p.add_argument("--independent",type=Path)
    p.add_argument("--episodes",required=True,type=Path)
    p.add_argument("--original-verifier",required=True,type=Path)
    p.add_argument("--campaign-zip",required=True,type=Path)
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    os.umask(0o077)
    if a.mode=="export":
        need(a.root is not None and a.independent is not None,
             "EXPORT_SOURCE_REQUIRED")
        result=export(a.root,a.independent,a.episodes,a.original_verifier,
                      a.campaign_zip)
    else:
        need(a.output is not None,"NEW_OUTPUT_REQUIRED")
        result=check(a.campaign_zip,a.episodes,a.original_verifier)
        result["report_sha256"]=write_new(a.output,result)
    print(json.dumps({k:v for k,v in result.items()
          if k not in ("windows","episode_zip_sha256")},sort_keys=True))
if __name__=="__main__":
    main()
