"""Precommitted NEW original-PAPER four-horizon price-only cohort.

Finite on-demand source/endpoint observation. No trader/model/agentd/owner API.
Never turn a missed capture or censored endpoint into zero return.
"""
from __future__ import annotations
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import time

from hepta_decision_attribution import read_private, strict
from historical_training.common import sha
from .adapters import make_spec
from .contracts import ContractError, compile_example, digest
from .economics import verify_cost_receipt
from .original_multihorizon import HORIZONS_MIN, GRID_MS, PINNED_COST_SHA256
from .original_policy_audit import write_once
from .registry import register_before_window, verify_registration
from .orchestrator import register_plan

SCHEMA = "heptatrader.train-v2-prospective-multihorizon-v1"
INPUT_SCHEMA = "heptatrader.train-v2-prospective-original-input-v1"
POLICY = "NO_TRADE_PRICE_CAPACITY_NEW_FUTURE_COHORT_V1"
DECISION_DELAY_MS = 1000
LAST_CAPTURE_DELAY_MS = 16000
SLOT_SPACING_MS = 80 * 60_000
SLOT_COUNT = 3
COST_AND_MARGIN_BPS = "10.9133557"
SEGMENT = re.compile(r"segment-[0-9]{19}-[0-9a-f]{8}\Z")
CODE_FILES = ("prospective_multihorizon.py", "original_multihorizon.py",
              "causal_ttl.py", "contracts.py", "adapters.py",
              "postmodel_requote.py", "economics.py")

def _fail(ok, reason):
    if not ok:
        raise ContractError(reason)

def _clock():
    return time.time_ns() // 1_000_000

def _private_dir(path):
    path = Path(path)
    _fail(path.is_absolute() and ".." not in path.parts
          and not path.is_symlink() and path.is_dir(),
          "PROSPECTIVE_DIRECTORY_INVALID")
    st = path.stat()
    _fail(st.st_uid == os.geteuid() and st.st_mode & 0o077 == 0,
          "PROSPECTIVE_PRIVATE_OWNER_REQUIRED")
    return path

def _cost_file(path, period):
    raw = read_private(path, 1024*1024)
    _fail(hashlib.sha256(raw).hexdigest() == PINNED_COST_SHA256,
          "PROSPECTIVE_FROZEN_COST_CHANGED")
    cost = strict(raw)
    spread, commission = verify_cost_receipt(cost, period)
    hurdle = spread[2] + 2*commission[1] + Decimal(4) + Decimal(2)
    _fail(hurdle == Decimal(COST_AND_MARGIN_BPS),
          "PROSPECTIVE_FROZEN_HURDLE_CHANGED")
    return PINNED_COST_SHA256

def _source_code():
    root = Path(__file__).parent
    return {name: sha(root/name) for name in CODE_FILES}

def _next_anchor(after_ms):
    _fail(type(after_ms) is int and after_ms > 0,
          "PROSPECTIVE_CLOCK_INVALID")
    return ((after_ms + GRID_MS - 1)//GRID_MS)*GRID_MS

def _persist(path, payload):
    # Existing immutable research writer requires O_EXCL and fsync of file.
    path = Path(path)
    _fail(not path.is_symlink() and not path.exists(),
          "PROSPECTIVE_CREATE_ONCE_REQUIRED")
    write_once(path, payload)
    dirfd = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(dirfd)
    finally: os.close(dirfd)

def preregister(root, source_root, period_file, cost_file, source_config):
    """One exact future cohort; no automatic launch or predicted outcomes."""
    now = _clock()
    _fail(type(now) is int and now > 0, "PROSPECTIVE_CLOCK_INVALID")
    # The real command never supplies a historical/forged timestamp; a
    # controllable clock exists solely in synthetic unit testing.
    source_root = _private_dir(source_root)
    period_raw = read_private(period_file, 65536)
    account_period = strict(period_raw)
    _fail(account_period.get("execution_domain") == "PAPER",
          "PROSPECTIVE_PAPER_PERIOD_REQUIRED")
    period = hashlib.sha256(period_raw).hexdigest()
    configuration = strict(read_private(source_config, 65536))
    _fail(configuration.get("schema") == "heptatrader.laya-resident-config.v1"
          and configuration.get("automatic_trading") is False
          and configuration.get("period_sha256") == period
          and configuration.get("period_path") == str(period_file)
          and configuration.get("feed_directory") == str(source_root),
          "PROSPECTIVE_ORIGINAL_CONFIG_MISMATCH")
    config_sha = sha(source_config)
    _cost_file(cost_file, period)
    spec = make_spec(period, POLICY, broker_reported_expiry=True)
    start = _next_anchor(now + 90_000)
    anchors = [start + n*SLOT_SPACING_MS for n in range(SLOT_COUNT)]
    plan = register_plan(
        spec=spec, registered_at_ms=now,
        first_decision_ms=start + DECISION_DELAY_MS,
        last_decision_ms=anchors[-1] + LAST_CAPTURE_DELAY_MS,
        minimum_mature_rows=1)
    root = Path(root)
    _fail(root.is_absolute() and ".." not in root.parts
          and not root.is_symlink() and not root.exists(),
          "PROSPECTIVE_FRESH_ROOT_REQUIRED")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    _private_dir(root)
    # The canonical-product Owner is NOT created; this local registration
    # still proves that slots are fixed before their first source decision.
    local = register_before_window(root/"registry", plan, spec, source_root)
    manifest = {
        "schema": SCHEMA, "policy_id": POLICY,
        "created_at_ms": now,
        "local_registered_at_ms": local["sealed_at_ms"],
        "local_plan": plan, "feature_spec": spec,
        "local_plan_id": plan["plan_id"],
        "source_root": str(source_root), "period_file": str(period_file),
        "source_config_path": str(source_config),
        "source_config_sha256": config_sha,
        "period_sha256": period, "original_cost_file": str(cost_file),
        "original_cost_file_sha256": PINNED_COST_SHA256,
        "source_code_sha256": _source_code(),
        "horizons_min": list(HORIZONS_MIN),
        "anchors_ms": anchors, "slot_count": SLOT_COUNT,
        "decision_not_before_ms": DECISION_DELAY_MS,
        "decision_no_later_ms": LAST_CAPTURE_DELAY_MS,
        "separation_ms": SLOT_SPACING_MS,
        "time_disjoint_slots_declared_before_any_outcome": True,
        "fixed_cost_plus_margin_bps": COST_AND_MARGIN_BPS,
        "selection": "NO_TRADE_ONLY_PRICE_OBSERVATION",
        "label_creation_before_maturity": False,
        "incomplete_slots_not_zero_return": True,
        "actual_model_predictor": False,
        "current_canonical_agentd_owner_enrolled": False,
        "actual_slippage_attested": False,
        "broker_queries_sent": 0, "broker_orders_sent": 0,
        "trade_authority": False, "automatic_model_promotion": False,
    }
    manifest["campaign_id"] = digest(manifest)
    _persist(root/"campaign.json", manifest)
    return manifest

def _load(root):
    root = _private_dir(root)
    manifest = strict(read_private(root/"campaign.json", 128*1024))
    _fail(manifest.get("schema") == SCHEMA
          and manifest.get("campaign_id") == digest({
              k:v for k,v in manifest.items() if k!="campaign_id"})
          and manifest.get("source_code_sha256") == _source_code()
          and manifest.get("horizons_min") == list(HORIZONS_MIN)
          and manifest.get("anchors_ms") == [
              manifest["anchors_ms"][0] + n*SLOT_SPACING_MS
              for n in range(SLOT_COUNT)]
          and manifest.get("fixed_cost_plus_margin_bps") == COST_AND_MARGIN_BPS
          and manifest.get("policy_id") == POLICY
          and manifest.get("local_plan", {}).get("first_decision_ms") ==
              manifest["anchors_ms"][0] + DECISION_DELAY_MS
          and manifest.get("local_plan", {}).get("last_decision_ms") ==
              manifest["anchors_ms"][-1] + LAST_CAPTURE_DELAY_MS
          and manifest.get("local_plan_id") ==
              manifest.get("local_plan", {}).get("plan_id")
          and manifest.get("feature_spec", {}).get("behavior_policy_id") == POLICY
          and manifest.get("feature_spec", {}).get("period_sha256") ==
              manifest.get("period_sha256")
          and manifest.get("trade_authority") is False,
          "PROSPECTIVE_MANIFEST_OR_CODE_CHANGED")
    _fail(sha(manifest["period_file"]) == manifest["period_sha256"],
          "PROSPECTIVE_ACCOUNT_PERIOD_CHANGED")
    config = strict(read_private(manifest["source_config_path"],65536))
    _fail(sha(manifest["source_config_path"]) == manifest["source_config_sha256"]
          and config.get("schema") == "heptatrader.laya-resident-config.v1"
          and config.get("automatic_trading") is False
          and config.get("period_path") == manifest["period_file"]
          and config.get("period_sha256") == manifest["period_sha256"]
          and config.get("feed_directory") == manifest["source_root"],
          "PROSPECTIVE_ORIGINAL_SOURCE_CONFIGURATION_CHANGED")
    _cost_file(Path(manifest["original_cost_file"]), manifest["period_sha256"])
    return manifest

def _latest_numeric_segment(source_root):
    root = _private_dir(source_root)
    candidates=[]
    for path in root.iterdir():
        name=path.name
        if name=="segment-recovery-live":
            continue
        if not name.startswith("segment-"):
            continue
        _fail(SEGMENT.fullmatch(name) and not path.is_symlink()
              and path.is_dir(), "PROSPECTIVE_MALFORMED_SEGMENT")
        file=path/"quotes.jsonl"
        _fail(not file.is_symlink() and file.is_file(),
              "PROSPECTIVE_SEGMENT_FILE_MISSING")
        candidates.append((int(name[8:27]),file))
    _fail(bool(candidates), "PROSPECTIVE_ORIGINAL_SEGMENT_UNAVAILABLE")
    candidates.sort()
    _fail(len({x[0] for x in candidates})==len(candidates),
          "PROSPECTIVE_DUPLICATE_SEGMENT_START")
    return candidates[-1][1]

def capture_slot(root, slot, *, clock=_clock, original_reader=None):
    """One original locally recorded live price input, entirely no-order.

    A caller must actually invoke this in the fixed time window. A missed
    invocation remains UNKNOWN/MISSED, not an inferred negative quote.
    """
    m=_load(root)
    _fail(type(slot) is int and 0<=slot<SLOT_COUNT,
          "PROSPECTIVE_SLOT_INDEX")
    output=Path(root)/("slot-%02d.input.json"%slot)
    _fail(not output.is_symlink() and not output.exists(),
          "PROSPECTIVE_SLOT_ALREADY_SEALED")
    begin=m["anchors_ms"][slot]+DECISION_DELAY_MS
    end=m["anchors_ms"][slot]+LAST_CAPTURE_DELAY_MS
    before=clock()
    _fail(type(before) is int and begin<=before<=end,
          "PROSPECTIVE_INPUT_NOT_IN_FROZEN_WINDOW")
    segment=_latest_numeric_segment(m["source_root"])
    from .causal_ttl import sealed_original_broker_expiry
    reader=original_reader or sealed_original_broker_expiry
    observations,original=reader(segment,m["feature_spec"])
    recorded=clock()
    _fail(type(recorded) is int and begin<=recorded<=end and recorded>=before,
          "PROSPECTIVE_INPUT_CAPTURE_LATE")
    # Before feature compilation, refuse an all-preregistration quote source.
    # Never attempt to rescue a missed future window with pre-existing data.
    _fail(any(type(q.get("available_at_ms")) is int
              and m["local_registered_at_ms"] < q["available_at_ms"] <= recorded
              for q in observations),
          "PROSPECTIVE_NO_POSTREG_INPUT_SOURCE")
    example=compile_example(m["feature_spec"],observations,recorded,recorded)
    _fail(example["label_status"]=="PENDING_OUTCOME"
          and example["locally_point_in_time"] is True
          and example["candidate_training_eligible"] is False,
          "PROSPECTIVE_INPUT_PREOUTCOME_ONLY")
    witness=example["features"]["input_receipts"][-1]
    selected=[q for q in observations
              if q["source_id"]==witness["source_id"]]
    _fail(len(selected)==1,"PROSPECTIVE_ORIGINAL_QUOTE_NOT_FOUND")
    q=selected[0]
    _fail(m["local_registered_at_ms"]<q["available_at_ms"]<=recorded<=q["valid_until_ms"],
          "PROSPECTIVE_SOURCE_NOT_NEW_OR_EXPIRED")
    verify_registration(Path(root)/"registry",m["local_plan"],
                        m["feature_spec"],segment)
    from .original_multihorizon import price
    mid=(price(q["bid"])+price(q["ask"]))/2
    report={
      "schema":INPUT_SCHEMA, "campaign_id":m["campaign_id"],
      "slot":slot,"anchor_ms":m["anchors_ms"][slot],
      "decision_at_ms":recorded,
      "input_read_started_ms":before,
      "source_available_at_ms":q["available_at_ms"],
      "source_event_at_ms":q["event_at_ms"],
      "source_valid_until_ms":q["valid_until_ms"],
      "original_source_id":q["source_id"],
      "original_source_sha256":q["source_sha256"],
      "original_segment_path":str(segment),
      "original_verified_prefix_sha256":original["verified_prefix_sha256"],
      "original_chain_head_at_input":original["original_chain_head"],
      "input_quote_midpoint":str(mid),
      "feature_id":example["feature_id"],
      "feature_values":example["features"]["values"],
      "original_feature_input_receipts":example["features"]["input_receipts"],
      "outcome_maturity_ms":{
          str(h):recorded + h*60000 for h in HORIZONS_MIN},
      "original_source_available_after_registration":True,
      "all_future_labels_unobserved":True,
      "forecast_model_run":False,
      "selected_original_trading_action":None,
      "price_only_nontrade_reference":True,
      "independent_producer_signature":False,
      "canonical_agentd_owner_enrolled":False,
      "native_ledgerwriter_ack":False,
      "broker_requests_sent":0,
      "broker_orders_sent":0,
      "trade_authority":False,
    }
    report["input_id"]=digest(report)
    _persist(output,report)
    return report

def campaign_status(root, *, at_ms=None):
    m=_load(root)
    now=_clock() if at_ms is None else at_ms
    _fail(type(now) is int and now>0,"PROSPECTIVE_CLOCK_INVALID")
    slots=[]
    for i,anchor in enumerate(m["anchors_ms"]):
        path=Path(root)/("slot-%02d.input.json"%i)
        if path.is_symlink():
            raise ContractError("PROSPECTIVE_SLOT_FILE_SYMLINK")
        if path.exists():
            payload=strict(read_private(path,1024*1024))
            _fail(payload.get("campaign_id")==m["campaign_id"]
                  and payload.get("slot")==i
                  and payload.get("input_id")==digest({
                    k:v for k,v in payload.items() if k!="input_id"}),
                  "PROSPECTIVE_SLOT_FROZEN_RECORD_CHANGED")
            status="PREOUTCOME_ORIGINAL_INPUT_SEALED"
        elif now<anchor+DECISION_DELAY_MS:
            status="FUTURE_SLOT_NOT_DUE"
        elif now<=anchor+LAST_CAPTURE_DELAY_MS:
            status="OPEN_SLOT_NO_SOURCE_YET"
        else:
            status="MISSED_NO_OPERATOR_CAPTURE_NOT_NO_QUOTE"
        slots.append({"slot":i,"anchor_ms":anchor,"state":status})
    return {
      "schema":"heptatrader.train-v2-prospective-price-cohort-status.v1",
      "campaign_id":m["campaign_id"],
      "as_of_ms":now,"slots":slots,
      "missing_inputs_not_assumed_zero":True,
      "independent_efficacy_sample":False,
      "automatic_training":False,
      "broker_orders_sent":0,
    }

def main():
    cli=argparse.ArgumentParser()
    modes=cli.add_subparsers(dest="action",required=True)
    r=modes.add_parser("register")
    for name in ("root","source-root","period","cost","source-config"):
        r.add_argument("--"+name,required=True,type=Path)
    c=modes.add_parser("capture")
    c.add_argument("--root",required=True,type=Path)
    c.add_argument("--slot",type=int,required=True)
    z=modes.add_parser("status")
    z.add_argument("--root",required=True,type=Path)
    args=cli.parse_args()
    os.umask(0o077)
    if args.action=="register":
        m=preregister(args.root,args.source_root,args.period,args.cost,args.source_config)
        result={"status":"LOCAL_PROSPECTIVE_NO_TRADE_REGISTERED",
                "campaign_id":m["campaign_id"],
                "anchors_ms":m["anchors_ms"],
                "automatic_capture_installed":False,
                "broker_orders_sent":0}
    elif args.action=="capture":
        c=capture_slot(args.root,args.slot)
        result={"status":"PREOUTCOME_SOURCE_SEALED",
                "slot":c["slot"],"input_id":c["input_id"],
                "label_observed":False,"broker_orders_sent":0}
    else:
        result=campaign_status(args.root)
    print(json.dumps(result,sort_keys=True))

if __name__=="__main__":
    main()
