"""Independent, stdlib-only audit of pre-registered FINITE FX Ridge research.

No IB API, model fitting, trade invocation, producer signing or retrospective
source edits. The verifier never turns missing/late evidence into success.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, stat, time
from pathlib import Path

SCHEMA = "heptatrader.fx-ridge-finite-prospective-campaign.v1"
INTERVAL_MS = 20 * 60 * 1000
GRID_MS = 5 * 60 * 1000
LEAD_MS = 10 * 60 * 1000
HORIZON_MS = 15 * 60 * 1000
MATURITY_DELAY_MS = 6500
MAX_WINDOW = 4
MAX_JSON = 65536

class AuditReject(ValueError):
    pass

def require(condition, code):
    if not condition:
        raise AuditReject(code)

def strict(path, *, maximum=MAX_JSON):
    path=Path(path)
    require(not path.is_symlink() and path.is_file(), "UNSAFE_OR_MISSING_FILE")
    require(path.stat().st_size <= maximum,"JSON_SIZE_BOUND")
    def unique(pairs):
        seen={}
        for k,v in pairs:
            require(k not in seen, "DUPLICATE_JSON_FIELD")
            seen[k]=v
        return seen
    return json.loads(path.read_bytes(),object_pairs_hook=unique,
         parse_constant=lambda _:(_ for _ in ()).throw(AuditReject("NONFINITE_JSON")))

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def read_private_root(root):
    p=Path(root)
    require(p.is_absolute() and '..' not in p.parts and not p.is_symlink(),
            "AUDIT_ROOT_UNSAFE")
    st=p.stat()
    require(st.st_uid==os.getuid() and stat.S_ISDIR(st.st_mode)
            and stat.S_IMODE(st.st_mode)==0o700,"AUDIT_ROOT_NOT_PRIVATE")
    return p

def planned_anchors(start):
    require(type(start)is int and start>0,"PLAN_CLOCK_INVALID")
    first=((start+LEAD_MS+GRID_MS-1)//GRID_MS)*GRID_MS
    return [first+i*INTERVAL_MS for i in range(MAX_WINDOW)]

def verify_four_calendar(intent,sealed,*,time_ms):
    require(intent.get("schema")==SCHEMA and
        sealed.get("schema")==SCHEMA+".sealed.v1",
        "CAMPAIGN_SCHEMA_MISMATCH")
    keys=["registered_operator_start_ms","anchors_ms","fixed_count",
          "interval_ms","outcome_wait_ms","shadow_only",
          "automatic_training","model_promotion_allowed","trade_authority",
          "broker_requests_sent","broker_orders_sent"]
    require(all(k in intent for k in keys),"CAMPAIGN_REQUIRED_FIELDS")
    require(intent["anchors_ms"]==planned_anchors(intent["registered_operator_start_ms"])
            and intent["fixed_count"]==MAX_WINDOW
            and intent["interval_ms"]==INTERVAL_MS
            and intent["outcome_wait_ms"]==MATURITY_DELAY_MS,
            "PREDECLARED_GRID_CHANGED")
    require(intent["shadow_only"] is True and
            intent["automatic_training"] is False and
            intent["model_promotion_allowed"] is False and
            intent["trade_authority"] is False and
            intent["broker_orders_sent"]==0 and
            intent["broker_requests_sent"]==0,
            "CAMPAIGN_EFFECT_AUTHORITY_FORBIDDEN")
    require(sealed.get("all_registered_before_first_decision") is True
            and sealed.get("fixed_model_across_all_windows") is True
            and sealed.get("outcomes_known_at_registration") is False
            and sealed.get("trading_or_promotion_authority") is False
            and type(sealed.get("sealed_at_ms")) is int
            and sealed["sealed_at_ms"]<intent["anchors_ms"][0],
            "CAMPAIGN_TIMING_OR_AUTHORITY")
    require(type(sealed.get("windows"))is list
            and len(sealed["windows"])==MAX_WINDOW,
            "CAMPAIGN_WINDOW_CARDINALITY")
    require(time_ms>=sealed["sealed_at_ms"],"AUDITOR_CLOCK_BEFORE_SEAL")
    periods=[]
    for index,item in enumerate(sealed["windows"]):
        anchor=intent["anchors_ms"][index]
        require(item.get("index")==index
            and item.get("anchor_ms")==anchor
            and item.get("first_decision_ms")==anchor+1000
            and item.get("last_decision_ms")==anchor+4500
            and item.get("outcome_due_ms")==anchor+HORIZON_MS
            and type(item.get("receipt_sealed_at_ms"))is int
            and item["receipt_sealed_at_ms"]+30000<=anchor+1000,
            "CAMPAIGN_REVISED_OR_UNREGISTERED_WINDOW")
        periods.append((item["first_decision_ms"],item["outcome_due_ms"]))
    require(all(a[1]<b[0] for a,b in zip(periods,periods[1:])),
            "CAMPAIGN_OVERLAPPING_OUTCOME_WINDOWS")
    require(len({x.get("model_sha256") for x in sealed["windows"]})==1,
            "CAMPAIGN_MODELS_NOT_FROZEN")
    require(len({x.get("plan_id") for x in sealed["windows"]})==MAX_WINDOW,
            "CAMPAIGN_PLAN_ID_REUSED")
    return intent["anchors_ms"]


def verify_registered_windows(root,intent,sealed):
    reg=root/"registry"
    read_private_root(reg)
    dbpath=reg/"registrations.sqlite"
    require(dbpath.is_file() and not dbpath.is_symlink(),"REGISTRY_LEDGER_MISSING")
    uri="file:"+str(dbpath)+"?mode=ro"
    db=sqlite3.connect(uri,uri=True,timeout=5)
    try:
        db.execute("PRAGMA query_only=ON")
        all_records=db.execute(
          "SELECT plan_id,period_sha256,instrument,policy_id,"
          "first_ms,last_ms,receipt_sha256,registration_json "
          "FROM registrations ORDER BY first_ms").fetchall()
    finally:db.close()
    require(len(all_records)==MAX_WINDOW,"REGISTRY_WRONG_POPULATION")
    expected_period=intent["original_period_sha256"]
    model=intent["original_model_file_sha256"]
    for idx,item in enumerate(sealed["windows"]):
        cell=read_private_root(root/("window-%02d"%idx))
        for field,filename in (
          ("registration_sha256","local-registration.json"),
          ("model_sha256","model.json"),
          ("manifest_sha256","manifest.json"),
          ("plan_sha256","plan.json"),("spec_sha256","feature-spec.json")):
            f=cell/filename
            require(f.is_file() and not f.is_symlink() and
                    sha(f)==item[field],"FROZEN_CELL_FILE_CHANGED:"+filename)
        require(item["model_sha256"]==model,
                "CAMPAIGN_MODEL_COPIES_CHANGED")
        registration=strict(cell/"local-registration.json")
        plan=strict(cell/"plan.json")
        spec=strict(cell/"feature-spec.json")
        manifest=strict(cell/"manifest.json")
        frozen_model=strict(cell/"model.json")
        require(plan["plan_id"]==item["plan_id"]
             and digest({k:v for k,v in plan.items() if k!="plan_id"})
                    ==plan["plan_id"]
             and plan["first_decision_ms"]==item["first_decision_ms"]
             and plan["last_decision_ms"]==item["last_decision_ms"]
             and plan["period_sha256"]==expected_period
             and plan["broker_orders_sent"]==0
             and plan["trade_authority"] is False
             and plan["promotion_allowed"] is False
             and plan["automatic_model_training"] is False,
             "FROZEN_PLAN_CLAIM_MISMATCH")
        require(plan["feature_spec_id"]==digest(spec)
             and spec["behavior_policy_id"]==plan["behavior_policy_id"]
             and spec["period_sha256"]==expected_period
             and spec["source_kind"]=="LIVE_CAUSAL_QUOTE"
             and spec["mode"]=="CAUSAL_SHADOW_ONLY"
             and spec["horizon_ms"]==HORIZON_MS
             and spec["lookback"]==13
             and spec["quote_expiry_policy"]=="ORIGINAL_CHAIN_BROKER_EXPIRY_V2"
             and spec["trade_authority"] is False,
             "FROZEN_FEATURE_SPEC_CHANGED")
        require(manifest["anchor_ms"]==item["anchor_ms"]
             and manifest["outcome_due_ms"]==item["outcome_due_ms"]
             and manifest["historical_model_sha256"]==
                    intent["historical_model_sha256"]==digest(frozen_model)
             and manifest["code_sha256"]==
                    intent["original_source_code_sha256"]
             and manifest["cost_receipt_sha256"]==intent["cost_sha256"]
             and manifest["original_period_sha256"]==expected_period
             and manifest["policy_id"]==plan["behavior_policy_id"]
             and manifest["source_kind"]==
                    "LOCAL_SHA_CHAINED_ORIGINAL_IB_PAPER"
             and manifest["model_promotion_allowed"] is False
             and manifest["trade_authority"] is False,
             "FROZEN_MANIFEST_CONTRACT_CHANGED")
        require(frozen_model["historical_validation_selected_action"]==
                    "NO_TRADE"
             and frozen_model["candidate_has_positive_validation_edge"] is False
             and frozen_model["promotion_allowed"] is False
             and frozen_model["trained_rows"]>=40000,
             "HISTORICAL_VALIDATION_RELABELED")
        row=all_records[idx]
        pid,period,inst,policy,first,last,original_hash,record_json=row
        original_file=reg/"sealed-plans"/(item["plan_id"]+".json")
        require(original_file.is_file() and
             not original_file.is_symlink() and
             sha(original_file)==original_hash,
             "ORIGINAL_REGISTRY_SEAL_CHANGED")
        original_receipt=strict(original_file)
        require(pid==item["plan_id"]==registration["plan_id"]
             and period==expected_period and inst=="EUR.USD"
             and policy==plan["behavior_policy_id"]
             and (first,last)==(item["first_decision_ms"],
                                item["last_decision_ms"])
             and original_receipt==registration
             and json.loads(record_json)==registration
             and registration["plan"]==plan
             and registration["feature_spec"]==spec
             and registration["trade_authority"] is False
             and registration["canonical_agentd_owner_enrolled"] is False,
             "REGISTRY_SEAL_OR_DATABASE_DRIFT")
    return MAX_WINDOW

def verify_source_snapshot(root,intent):
    for field in ("config_path","cost_path","original_model_root"):
        p=Path(intent[field])
        require(p.is_absolute() and not p.is_symlink(),
                "FROZEN_EXTERNAL_PATH_UNSAFE")
    require(sha(intent["config_path"])==intent["config_sha256"]
         and sha(intent["cost_path"])==intent["cost_sha256"],
         "FROZEN_CONFIG_OR_FEE_RECEIPT_CHANGED")
    model_root=Path(intent["original_model_root"])
    require(model_root.is_dir() and not model_root.is_symlink()
         and sha(model_root/"model.json")==intent["original_model_file_sha256"],
         "FROZEN_ORIGINAL_MODEL_CHANGED")
    from_here=Path(__file__).resolve().parent
    require(sha(from_here/"fx_ridge_finite_campaign.py")==
            intent["campaign_source_code_sha256"]
        and sha(from_here/"fx_ridge_original.py")==
            intent["original_source_code_sha256"],
            "EXECUTING_CAMPAIGN_SOURCE_VERSION_CHANGED")
    return True


def replay_exact_original_prefixes(receipts,source_root):
    import math
    source_root=Path(source_root)
    require(type(receipts)is list and 1<=len(receipts)<=6,
            "DECISION_FROZEN_PREFIX_COUNT")
    original={}
    for witness in receipts:
        p=Path(witness["path"]);n=witness["complete_prefix_bytes"]
        require(p.is_absolute() and p.parent.parent==source_root
                and not p.is_symlink() and not p.parent.is_symlink()
                and type(n)is int and 0<n<=64*1024*1024,
                "DECISION_ORIGINAL_PREFIX_PATH_UNSAFE")
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            st=os.fstat(fd)
            require(st.st_uid==os.getuid() and st.st_size>=n
                    and not st.st_mode&0o077,
                    "DECISION_ORIGINAL_PREFIX_CUSTODY_CHANGED")
            raw=bytearray()
            while len(raw)<n:
                b=os.pread(fd,min(1024*1024,n-len(raw)),len(raw))
                require(bool(b),"DECISION_ORIGINAL_PREFIX_TRUNCATED")
                raw.extend(b)
        finally:os.close(fd)
        require(raw.endswith(b"\n") and
                hashlib.sha256(raw).hexdigest()==witness["prefix_sha256"],
                "DECISION_ORIGINAL_PREFIX_SHA_MISMATCH")
        previous="0"*64
        header=None
        for index,line in enumerate(raw.splitlines()):
            require(len(line)<65536,"DECISION_ORIGINAL_LINE_BOUND")
            def pairs(rows):
                out={}
                for k,v in rows:
                    require(k not in out,"DECISION_ORIGINAL_DUPLICATE_KEY")
                    out[k]=v
                return out
            rec=json.loads(line,object_pairs_hook=pairs)
            require(rec.get("schema")=="heptatrader.laya-shadow-quotes.v1"
                    and rec.get("sequence")==index
                    and rec.get("previous")==previous
                    and rec.get("sha256")==digest({
                        k:v for k,v in rec.items() if k!="sha256"}),
                    "DECISION_ORIGINAL_CHAIN_INVALID")
            previous=rec["sha256"]
            if index==0:
                header=rec.get("data")
                require(rec.get("kind")=="HEADER"
                        and header.get("execution_domain")=="PAPER"
                        and header.get("instrument")=="EUR.USD"
                        and header.get("broker_orders_sent")==0
                        and header.get("write_operations_allowed")==[],
                        "DECISION_ORIGINAL_SCOPE_NOT_PAPER")
            elif rec.get("kind")=="QUOTE":
                q=rec.get("data")
                require(q.get("source")=="IB"
                        and q.get("authoritative") is True
                        and q.get("stale") is False,
                        "DECISION_ORIGINAL_UNTRUSTED_QUOTE")
                require(rec["sha256"] not in original,
                        "DECISION_ORIGINAL_DUPLICATE_RECEIPT")
                original[rec["sha256"]]=rec
        require(previous==witness["chain_head"],
                "DECISION_ORIGINAL_CHAIN_HEAD_CHANGED")
    return original

def verify_original_decision(cell,item,intent):
    from decimal import Decimal
    import math
    frozen=strict(cell/"preoutcome-decision.json",maximum=128000)
    qualification=strict(cell/"qualification.json")
    manifest=strict(cell/"manifest.json")
    model=strict(cell/"model.json")
    require(qualification.get("decision_sha256")==sha(
                cell/"preoutcome-decision.json")
            and qualification.get("state")=="PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE"
            and qualification.get("sealed_before_broker_reported_quote_expiry") is True
            and qualification.get("model_promotion_allowed") is False
            and qualification.get("trade_authority") is False
            and frozen.get("selected_trading_action")=="NO_TRADE"
            and frozen.get("plan_id")==item["plan_id"]
            and frozen.get("anchor_ms")==item["anchor_ms"]
            and frozen.get("model_sha256")==digest(model)
            and frozen.get("actual_execution_reward") is False
            and frozen.get("promotion_allowed") is False
            and frozen.get("trade_authority") is False
            and item["first_decision_ms"] <= frozen.get("decision_at_ms",0)
                        <= item["last_decision_ms"],
            "DECISION_ORIGINAL_QUALIFICATION_MISMATCH")
    samples=frozen.get("history_quote_samples")
    require(type(samples)is list and len(samples)==13,
            "DECISION_CAUSAL_QUOTE_COUNT_CHANGED")
    receipts=replay_exact_original_prefixes(
        frozen.get("source_prefix_receipts"),
        manifest["source_root"])
    for quote in samples:
        record=receipts.get(quote.get("source_id"))
        require(record is not None,"DECISION_QUOTE_RECEIPT_NOT_IN_PREFIX")
        q=record["data"]
        event=q.get("observed_at_ms")
        available=record["at_ms"]
        expiry=min(event+5000,q.get("stale_after_ms",0))
        require(quote.get("event_at_ms")==event
                and quote.get("available_at_ms")==available
                and quote.get("valid_until_ms")==expiry
                and Decimal(str(q.get("bid")))==Decimal(str(quote.get("bid")))
                and Decimal(str(q.get("ask")))==Decimal(str(quote.get("ask")))
                and quote.get("source_sha256")==record["sha256"]
                and 0<event<=available<=item["anchor_ms"],
                "DECISION_CAUSAL_QUOTE_NOT_REPRODUCIBLE")
    for k,quote in enumerate(samples):
        cutoff=item["anchor_ms"]-(12-k)*300000
        require(cutoff-5000 <= quote["event_at_ms"] < cutoff,
                "DECISION_5MIN_BUCKET_NOT_CAUSAL")
    final_quote=samples[-1]
    require(frozen["decision_at_ms"]<final_quote["valid_until_ms"]
            and qualification.get("seal_at_ms") <
               final_quote["valid_until_ms"]
            and qualification.get("last_original_quote_expiry_ms")==
                final_quote["valid_until_ms"],
            "DECISION_DURABLE_AFTER_BROKER_EXPIRY")
    mids=[(float(q["bid"])+float(q["ask"]))/2 for q in samples]
    returns=[(b-a)/a for a,b in zip(mids,mids[1:])]
    mean=math.fsum(returns)/len(returns)
    sd=math.sqrt(math.fsum((r-mean)**2 for r in returns)/len(returns))
    hour=((item["anchor_ms"]//1000-300)//3600)%24
    vec=[(mids[-1]/mids[-1-lag]-1)*10000 for lag in (1,3,6,12)]
    vec += [sd*10000,math.fsum(abs(r) for r in returns)/len(returns)*10000,
            math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24)]
    require(len(frozen["feature_vector"])==8
            and all(abs(a-b)<1e-7 for a,b in
                     zip(vec,frozen["feature_vector"])),
            "DECISION_NUMERIC_8D_FEATURE_CHANGED")
    norm=[1.]+[max(-5.,min(5.,(v-m)/max(s,1e-8)))
        for v,m,s in zip(vec,model["mean"],model["stddev"])]
    forecast=math.fsum(v*w for v,w in zip(norm,model["weights"]))
    require(abs(forecast-frozen["forecast_midpoint_bps"])<1e-6,
            "DECISION_FROZEN_WEIGHT_PREDICTION_CHANGED")
    direction=("UP" if forecast>0 else "DOWN" if forecast<0 else "FLAT")
    require(frozen.get("forecast_direction_research_only")==direction,
            "DECISION_RESEARCH_DIRECTION_CHANGED")
    cost=strict(intent["cost_path"])
    b,a=float(final_quote["bid"]),float(final_quote["ask"])
    live_spread=(a-b)/((a+b)/2)*10000
    required=max(live_spread,float(cost["quote_spread_full_bps"]["p99"]))+2*float(
        cost["fx_commission_one_way_bps"]["p90"])+6
    hurdle=frozen["cost_hurdle"]
    require(abs(required-hurdle[
          "required_absolute_prediction_bps"])<1e-8
          and abs(live_spread-hurdle["live_original_spread_bps"])<1e-8
          and abs(max(live_spread,float(cost["quote_spread_full_bps"]["p99"]))-
                  hurdle["stressed_spread_bps"])<1e-8
          and abs(2*float(cost["fx_commission_one_way_bps"]["p90"])-
                  hurdle["roundtrip_commission_p90_bps"])<1e-8
          and hurdle["hypothetical_slippage_roundtrip_bps"]==4
          and hurdle["minimum_margin_bps"]==2
          and frozen["forecast_magnitude_clears_cost"]==
              (abs(forecast)>required),
          "DECISION_FROZEN_COST_HURDLE_CHANGED")
    return frozen, {"local_chain_quotes_reverified":len(samples),
                    "actual_prediction_bps":str(forecast),
                    "original_quote_expiry_ms":final_quote["valid_until_ms"],
                    "minimum_hypothetical_hurdle_bps":str(required),
                    "economic_alpha_attested":False}


def verify_mature_result(cell,item,intent,frozen):
    from decimal import Decimal
    result=strict(cell/"mature-outcome.json",maximum=128000)
    require(result.get("schema")==
                "heptatrader.fx-ridge-original-mature-price.v1"
            and result.get("predecision_sha256")==
                sha(cell/"preoutcome-decision.json")
            and result.get("anchor_ms")==item["anchor_ms"]
            and result.get("outcome_due_ms")==item["outcome_due_ms"]
            and result.get("input_quote_sha256")==
                frozen["history_quote_samples"][-1]["source_id"]
            and str(result.get("forecast_midpoint_bps"))==
                str(frozen["forecast_midpoint_bps"])
            and result.get("selected_trading_action")=="NO_TRADE"
            and result.get("observed_trading_pnl_attested") is False
            and result.get("model_efficacy_proven") is False
            and result.get("model_promotion_allowed") is False
            and result.get("trade_authority") is False
            and result.get("broker_orders_sent")==0,
            "MATURE_OUTCOME_NOT_QUALIFIED_AS_PRICE_ONLY")
    manifest=strict(cell/"manifest.json")
    receipts=result.get("mature_source_prefix_receipts")
    quote_map=replay_exact_original_prefixes(
        receipts,manifest["source_root"])
    due=item["outcome_due_ms"]
    observed=[]
    by_timestamp={}
    for rid,rec in quote_map.items():
        q=rec.get("data",{})
        t=q.get("observed_at_ms")
        known=rec.get("at_ms")
        if (type(t)is int and type(known)is int
            and due<=t<=due+5000 and due<=known):
            bid,ask=str(q.get("bid")),str(q.get("ask"))
            prev=by_timestamp.get(t)
            require(prev is None or prev==(bid,ask),
                    "MATURE_ORIGINAL_TIMESTAMP_CONFLICT")
            by_timestamp[t]=(bid,ask)
            observed.append((t,known,rid,Decimal(bid),Decimal(ask)))
    require(observed,"MATURE_ORIGINAL_QUOTE_WINDOW_EMPTY")
    first=min(observed,key=lambda q:(q[0],q[1]))
    require(result.get("price_event_ms")==first[0]
            and result.get("price_available_ms")==first[1]
            and result.get("outcome_quote_sha256")==first[2],
            "MATURE_HINDSIGHT_OR_FAVORABLE_QUOTE_SELECTED")
    origin=frozen["history_quote_samples"][-1]
    anchor_mid=(Decimal(origin["bid"])+Decimal(origin["ask"]))/2
    matured_mid=(first[3]+first[4])/2
    gain=(matured_mid/anchor_mid-1)*Decimal(10000)
    pred=Decimal(str(frozen["forecast_midpoint_bps"]))
    if abs(gain-Decimal(str(result["observed_original_midpoint_return_bps"])))>Decimal("1e-16"):
        raise AuditReject("MATURE_NET_PRICE_NOT_REPRODUCED")
    if abs(pred-gain-Decimal(str(result["forecast_error_bps"])))>Decimal("1e-12"):
        raise AuditReject("MATURE_FORECAST_ERROR_CHANGED")
    direction=(pred>0 and gain>0) or (pred<0 and gain<0)
    require(result["research_direction_correct"]==direction
            and result.get("hypothetical_no_trade_pnl_usd")=="0",
            "MATURE_RESEARCH_DIRECTION_OR_NO_TRADE_CHANGED")
    return {"observed_original_return_bps":str(gain),
            "forecast_error_bps":str(pred-gain),
            "direction_right":direction,
            "model_selected":False,"executed_trading_pnl_attested":False}

def audit_cell(root,item,intent,now):
    cell=root/("window-%02d"%item["index"])
    decision_attempt=cell/"decision-attempt.json"
    maturity_attempt=cell/"maturity-attempt.json"
    qualification=cell/"qualification.json"
    output={
        "window_index":item["index"],"plan_id":item["plan_id"],
        "anchor_ms":item["anchor_ms"],
        "decision_deadline_ms":item["last_decision_ms"],
        "outcome_due_ms":item["outcome_due_ms"],
        "no_trade_only":True,
        "promotion_allowed":False,
        "broker_orders_sent":0,
    }
    if not decision_attempt.exists():
        require(not maturity_attempt.exists() and not qualification.exists(),
                "DECISION_ABSENT_WITH_LATER_EVIDENCE")
        output["decision_state"]=(
          "FUTURE" if now<item["first_decision_ms"]
          else "CURRENT_WINDOW_OPEN" if now<=item["last_decision_ms"]
          else "UNRESOLVED_AFTER_DEADLINE_NO_ATTEMPT_RECEIPT")
        output["maturity_state"]="NOT_ADMISSIBLE_NO_DECISION"
        return output
    d=strict(decision_attempt)
    require(d.get("schema")==SCHEMA+".attempt.v1"
        and d.get("stage")=="decision"
        and d.get("state") in ("CENSORED","FROZEN_PRICE_ONLY")
        and d.get("nonretryable") is True
        and d.get("trade_authority") is False
        and d.get("model_promotion_allowed") is False
        and d.get("broker_orders_sent")==0
        and type(d.get("attempt_recorded_at_ms")) is int
        and 0<d["attempt_recorded_at_ms"]<=now,
        "DECISION_ATTEMPT_INVALID_OR_IN_THE_FUTURE")
    output["decision_attempt_sha256"]=sha(decision_attempt)
    output["decision_state"]=d["state"]
    frozen=None
    if d["state"]=="FROZEN_PRICE_ONLY":
        frozen,extra=verify_original_decision(cell,item,intent)
        q=strict(cell/"qualification.json")
        require(frozen["decision_at_ms"]<=now
                and q["seal_at_ms"]<=now,
                "PREDECISION_PROOF_FUTURE_BACKFILL")
        output["decision_original_recomputed"]=extra
    else:
        output["censor_reason"]=d.get("reason_code")
        require(not (cell/"mature-outcome.json").exists(),
                "CENSORED_DECISION_HAS_SUCCESSFUL_MATURE_RESULT")
    if not maturity_attempt.exists():
        output["maturity_state"]=(
            "FUTURE" if now<item["outcome_due_ms"]+MATURITY_DELAY_MS
            else "UNRESOLVED_AFTER_HORIZON_NO_ATTEMPT_RECEIPT")
        return output
    m=strict(maturity_attempt)
    require(m.get("schema")==SCHEMA+".attempt.v1"
        and m.get("stage")=="maturity"
        and m.get("state") in ("CENSORED","MATURE_PRICE_ONLY")
        and m.get("nonretryable") is True
        and m.get("trade_authority") is False
        and m.get("model_promotion_allowed") is False
        and m.get("broker_orders_sent")==0
        and type(m.get("attempt_recorded_at_ms")) is int
        and item["outcome_due_ms"]+6000 <=
            m["attempt_recorded_at_ms"] <= now,
        "MATURITY_ATTEMPT_INVALID_OR_IN_THE_FUTURE")
    output["maturity_attempt_sha256"]=sha(maturity_attempt)
    output["maturity_state"]=m["state"]
    if m["state"]=="MATURE_PRICE_ONLY":
        require(frozen is not None,"MATURE_WITHOUT_PRIOR_FROZEN_DECISION")
        proof=verify_mature_result(cell,item,intent,frozen)
        mature_quote=strict(cell/"mature-outcome.json")
        require(mature_quote["price_available_ms"]<=
                m["attempt_recorded_at_ms"],
                "MATURITY_RESULT_BACKDATED_BEFORE_ORIGINAL_RECEIPT")
        output["mature_original_recomputed"]=proof
    else:
        require(not (cell/"mature-outcome.json").exists(),
                "CENSORED_MATURITY_WITH_SUCCESS_EVIDENCE")
        output["maturity_censor_reason"]=m.get("reason_code")
    return output


def run_audit(root, at_ms=None):
    root=read_private_root(root)
    now=time.time_ns()//1_000_000 if at_ms is None else at_ms
    require(type(now)is int and now>0,"AUDITOR_INVALID_CLOCK")
    intent=strict(root/"campaign-intent.json")
    sealed=strict(root/"campaign-sealed.json")
    require(sealed.get("intent_sha256")==sha(root/"campaign-intent.json"),
            "CAMPAIGN_INTENT_BYTES_REPLACED")
    verify_four_calendar(intent,sealed,time_ms=now)
    verify_source_snapshot(root,intent)
    verify_registered_windows(root,intent,sealed)
    worker_path=root/"campaign-worker-start.json"
    if worker_path.exists():
        worker=strict(worker_path)
        require(worker.get("schema")==SCHEMA+".worker-start.v1"
                and worker.get("sealed_sha256")==sha(root/"campaign-sealed.json")
                and worker.get("automatic_retries") is False
                and worker.get("trade_authority") is False
                and worker.get("no_order_api_or_model_promotion") is True
                and worker.get("started_at_ms",0)<sealed["windows"][0]["first_decision_ms"],
                "WORKER_NOT_ORIGINAL_NONRETRYING")
    else:
        require(not any((root/("window-%02d"%k)/"decision-attempt.json").exists()
                        for k in range(MAX_WINDOW)),
                "NO_WORKER_BUT_DECISION_RECEIPT_EXISTS")
    states=[audit_cell(root,cell,intent,now) for cell in sealed["windows"]]
    terminal=[c for c in states if c["maturity_state"] in
              ("CENSORED","MATURE_PRICE_ONLY")]
    final_path=root/"campaign-final.json"
    if final_path.exists():
        require(len(terminal)==MAX_WINDOW,
                "FINAL_WITH_MISSING_OUTCOMES")
        final=strict(final_path)
        require(final.get("schema")==SCHEMA+".final.v1"
                and final.get("intent_sha256")==sha(root/"campaign-intent.json")
                and final.get("sealed_sha256")==sha(root/"campaign-sealed.json")
                and final.get("window_count")==MAX_WINDOW
                and type(final.get("completed_at_ms")) is int
                and final["completed_at_ms"]<=now
                and final.get("automatic_promotion") is False
                and final.get("model_economic_efficacy_proven") is False
                and final.get("broker_orders_sent")==0
                and final.get("trade_authority") is False
                and final.get("successful_matured_count")==
                    sum(c["maturity_state"]=="MATURE_PRICE_ONLY" for c in states)
                and final.get("censored_count")==
                    sum(c["maturity_state"]=="CENSORED" for c in states)
                and len(final.get("window_results",[]))==MAX_WINDOW,
                "CAMPAIGN_FINAL_CONFLICT")
        for idx,(saved,actual) in enumerate(
                zip(final["window_results"],states)):
            require(saved.get("window")==idx
                    and saved.get("plan_id")==actual["plan_id"]
                    and saved.get("decision_state")==actual["decision_state"]
                    and saved.get("maturity_state")==actual["maturity_state"]
                    and saved.get("decision_attempt_sha256")==
                        actual.get("decision_attempt_sha256")
                    and saved.get("maturity_attempt_sha256")==
                        actual.get("maturity_attempt_sha256"),
                    "FINAL_SAMPLE_REWRITTEN")
        status="COMPLETE_PRICE_ONLY_NO_ALPHA_OR_TRADING"
    else:
        status="PRESEALED_COHORT_IN_PROGRESS_OR_INCOMPLETE_HOLD"
    report={
        "schema":"heptatrader.fx-ridge-independent-campaign-review.v1",
        "observed_at_ms":now,
        "campaign_intent_sha256":sha(root/"campaign-intent.json"),
        "campaign_sealed_sha256":sha(root/"campaign-sealed.json"),
        "original_cost_receipt_sha256":sha(intent["cost_path"]),
        "frozen_revised_historical_model_sha256":
            intent["original_model_file_sha256"],
        "original_code_sha256":intent["original_source_code_sha256"],
        "campaign_runner_code_sha256":intent["campaign_source_code_sha256"],
        "four_true_future_windows_locally_presealed":True,
        "four_expected_windows":MAX_WINDOW,
        "registered_plan_count":MAX_WINDOW,
        "workers_started":worker_path.exists(),
        "windows":states,
        "predecision_attempts_written":sum(
            c["decision_state"] in ("FROZEN_PRICE_ONLY","CENSORED") for c in states),
        "mature_price_only_outcomes":sum(
            c["maturity_state"]=="MATURE_PRICE_ONLY" for c in states),
        "censored_outcomes":sum(c["maturity_state"]=="CENSORED" for c in states),
        "completed_qualification":status,
        "native_owner_enrolled":False,
        "original_trader_behavior_propensity_attested":False,
        "model_economic_efficacy_proven":False,
        "broker_signed_original_data":False,
        "hypothetical_price_move_is_executable_return":False,
        "model_promotion_allowed":False,
        "broker_requests_sent":0,
        "broker_orders_sent":0,
        "trade_authority":False,
    }
    return {**report,"report_id":digest(report)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    os.umask(0o077)
    result=run_audit(a.root)
    output=a.output
    require(output.is_absolute() and not output.is_symlink()
            and not output.exists(),"INDEPENDENT_AUDIT_OUTPUT_CREATE_ONLY")
    require(not output.is_relative_to(a.root),"INDEPENDENT_AUDIT_CANNOT_MUTATE_COHORT")
    output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    require(output.parent.stat().st_mode & 0o077==0,
            "AUDIT_OUTPUT_NOT_PRIVATE")
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    dr=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(dr)
    finally:os.close(dr)
    print(json.dumps({
        "audit_status":result["completed_qualification"],
        "windows":result["four_expected_windows"],
        "decisions":result["predecision_attempts_written"],
        "mature_price_only":result["mature_price_only_outcomes"],
        "censored":result["censored_outcomes"],
        "report_sha256":hashlib.sha256(raw).hexdigest(),
        "orders":0
    },sort_keys=True))

if __name__=="__main__":
    main()
