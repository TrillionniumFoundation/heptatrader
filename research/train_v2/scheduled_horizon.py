"""Bounded one-shot original PAPER price source capture for NEW preregistered slots.

A separate local schedule is sealed before any slot starts. This runner is
invoked explicitly by an operator-created systemd --user ONESHOT timer.
It has no timer-install, trader, broker, remote signer, model or Owner API.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import stat
import time
from pathlib import Path

from hepta_decision_attribution import read_private, strict
from historical_training.common import sha
from .contracts import ContractError, digest
from .original_policy_audit import write_once
from .prospective_multihorizon import (
    SLOT_COUNT, DECISION_DELAY_MS, LAST_CAPTURE_DELAY_MS,
    SLOT_SPACING_MS, _load, _persist, capture_slot, campaign_status,
)

SCHEMA = "heptatrader.train-v2-explicit-timer-roster.v1"
RUN_SCHEMA = "heptatrader.train-v2-timer-capture-result.v1"
DELAY_MS = 5000
MIN_SETUP_MS = 45000
MAX_EARLY_TIMER_MS = 500
TIMEOUT_SECONDS = 12


def _now():
    return time.time_ns() // 1_000_000


def _ensure_private(path):
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts or p.is_symlink() or not p.is_dir():
        raise ContractError("SCHEDULE_PRIVATE_ROOT_INVALID")
    st = p.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise ContractError("SCHEDULE_PRIVATE_ROOT_NOT_OWNER_ONLY")
    return p


def _source_sha():
    return sha(Path(__file__))


def _verify_roster(roster, *, manifest_path):
    if (not isinstance(roster, dict) or roster.get("schema") != SCHEMA
        or roster.get("roster_id") != digest({
            k:v for k,v in roster.items() if k != "roster_id"
        }) or roster.get("timer_runner_sha256") != _source_sha()
        or roster.get("campaign_json_sha256") != sha(manifest_path)
        or type(roster.get("roster_created_at_ms")) is not int
        or roster["roster_created_at_ms"] > roster.get("fixed_anchors_ms",[0])[0]
                                            + DELAY_MS - MIN_SETUP_MS
        or roster.get("capture_delay_ms") != DELAY_MS
        or roster.get("timeout_seconds") != TIMEOUT_SECONDS
        or roster.get("automatic_trading") is not False
        or roster.get("model_inference") is not False
        or roster.get("canonical_owner_enrolled") is not False
        or roster.get("broker_orders_sent") != 0
        or roster.get("read_only_paper_source") is not True):
        raise ContractError("SCHEDULE_IMMUTABLE_ROSTER_CHANGED")
    return roster


def create_roster(campaign_root, roster_file):
    """Create one immutable schedule AFTER a fresh campaign registration.

    Caller can install timers only after this has succeeded; the registry and
    campaign remain byte-for-byte unchanged. Never reuse an old missed slot.
    """
    root = _ensure_private(campaign_root)
    manifest = _load(root)
    status = campaign_status(root)
    now = _now()
    if not all(row["state"] == "FUTURE_SLOT_NOT_DUE" for row in status["slots"]):
        raise ContractError("SCHEDULE_OLD_OR_MISSED_SLOT_CANNOT_BE_RESTARTED")
    anchors = manifest["anchors_ms"]
    if anchors[0]+DELAY_MS-now < MIN_SETUP_MS:
        raise ContractError("SCHEDULE_NOT_FROZEN_EARLY_ENOUGH")
    if manifest["selection"] != "NO_TRADE_ONLY_PRICE_OBSERVATION":
        raise ContractError("SCHEDULE_PRICE_ONLY_POLICY_REQUIRED")
    slots = []
    for slot,anchor in enumerate(anchors):
        if slot >= SLOT_COUNT:
            raise ContractError("SCHEDULE_SLOT_COUNT_CHANGED")
        launch_at = anchor+DELAY_MS
        if not anchor+DECISION_DELAY_MS <= launch_at <= anchor+LAST_CAPTURE_DELAY_MS-4000:
            raise ContractError("SCHEDULE_LAUNCH_TOO_NEAR_DEADLINE")
        slots.append({
            "slot":slot, "anchor_ms":anchor, "launch_at_ms":launch_at,
            "last_original_input_ms":anchor+LAST_CAPTURE_DELAY_MS,
            "unit":f"htv2-px-{manifest['campaign_id'][:12]}-s{slot:02d}",
            "time_utc":dt.datetime.fromtimestamp(
                launch_at/1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        })
    file = Path(roster_file)
    if file.parent != root or file.name != "timers-roster.json":
        raise ContractError("SCHEDULE_FILE_NOT_IN_CAMPAIGN_ROOT")
    if file.exists() or file.is_symlink():
        raise ContractError("SCHEDULE_ALREADY_PRESENT")
    body = {
        "schema": SCHEMA, "campaign_id": manifest["campaign_id"],
        "campaign_json_sha256": sha(root/"campaign.json"),
        "campaign_root": str(root),
        "registered_before_any_slot": True,
        "roster_created_at_ms": now,
        "fixed_anchors_ms": list(anchors),
        "capture_delay_ms": DELAY_MS,
        "timeout_seconds": TIMEOUT_SECONDS,
        "timer_runner_sha256": _source_sha(),
        "slots":slots,
        "read_only_paper_source":True,
        "automatic_trading":False,
        "model_inference":False,
        "canonical_owner_enrolled":False,
        "inference_count":0,
        "broker_requests_sent":0,
        "broker_orders_sent":0,
    }
    record={**body,"roster_id":digest(body)}
    _persist(file,record)
    return record


def load_roster(roster_file):
    file=Path(roster_file)
    root=_ensure_private(file.parent)
    if file.name != "timers-roster.json" or file.is_symlink() or not file.is_file():
        raise ContractError("SCHEDULE_ROSTER_FILE_MISSING")
    raw=read_private(file,65536)
    roster=strict(raw)
    _verify_roster(roster,manifest_path=root/"campaign.json")
    m=_load(root)
    if (roster["campaign_root"]!=str(root)
        or roster["campaign_id"]!=m["campaign_id"]
        or roster["fixed_anchors_ms"]!=m["anchors_ms"]
        or len(roster.get("slots",[]))!=SLOT_COUNT):
        raise ContractError("SCHEDULE_CAMPAIGN_BINDING_CHANGED")
    expected_ids=set()
    for i,entry in enumerate(roster["slots"]):
        anchor=m["anchors_ms"][i]
        if (entry.get("slot")!=i or entry.get("anchor_ms")!=anchor
            or entry.get("launch_at_ms")!=anchor+DELAY_MS
            or entry.get("last_original_input_ms")!=anchor+LAST_CAPTURE_DELAY_MS
            or entry.get("unit")!=f"htv2-px-{m['campaign_id'][:12]}-s{i:02d}"):
            raise ContractError("SCHEDULE_SLOT_ROSTER_CHANGED")
        if entry["unit"] in expected_ids:
            raise ContractError("SCHEDULE_DUPLICATE_UNIT")
        expected_ids.add(entry["unit"])
    return roster


def run_slot(roster_file, slot):
    """One finite invocation. Any failure becomes retained error, never a label.

    Does not retry after wrong time, compromised original source or previous
    input. Actual systemd timer delivery is separately evidenced by the host.
    """
    roster=load_roster(roster_file)
    if type(slot) is not int or not 0<=slot<SLOT_COUNT:
        raise ContractError("SCHEDULE_SLOT_OUT_OF_BOUNDS")
    root=Path(roster["campaign_root"])
    expected=roster["slots"][slot]
    now=_now()
    if now<expected["launch_at_ms"]-MAX_EARLY_TIMER_MS:
        raise ContractError("SCHEDULE_TIMER_EARLY")
    if now>expected["last_original_input_ms"]:
        raise ContractError("SCHEDULE_TIMER_LATE_NO_REPLAY")
    result_path=root/f"slot-{slot:02d}.timer-result.json"
    if result_path.is_symlink() or result_path.exists():
        raise ContractError("SCHEDULE_RESULT_ALREADY_SEALED")
    status="FAILED_RESEARCH_ONLY"
    reason=None
    input_hash=None
    try:
        original=capture_slot(root,slot)
        status="SOURCE_INPUT_SEALED_NO_LABEL"
        input_hash=sha(root/f"slot-{slot:02d}.input.json")
        if (original.get("all_future_labels_unobserved") is not True
            or original.get("forecast_model_run") is not False
            or original.get("broker_orders_sent")!=0):
            raise ContractError("SCHEDULE_RESULT_EFFECT_SCOPE_CHANGED")
    except Exception as exc:
        reason=type(exc).__name__+":"+(str(exc).splitlines()[0][:150])
    end=_now()
    payload={
        "schema":RUN_SCHEMA,
        "roster_id":roster["roster_id"],
        "campaign_id":roster["campaign_id"],
        "slot":slot, "original_launch_at_ms":expected["launch_at_ms"],
        "original_last_allowed_ms":expected["last_original_input_ms"],
        "worker_started_at_ms":now,
        "worker_finished_at_ms":end,
        "state":status,
        "reason":reason,
        "source_input_sha256":input_hash,
        "original_label_assigned":False,
        "model_inference":False, "automatic_trading":False,
        "broker_queries_sent":0, "broker_orders_sent":0,
    }
    _persist(result_path,{**payload,"result_id":digest(payload)})
    return payload


def main():
    parser=argparse.ArgumentParser()
    commands=parser.add_subparsers(dest="action",required=True)
    p=commands.add_parser("prepare")
    p.add_argument("--root",required=True,type=Path)
    r=commands.add_parser("run")
    r.add_argument("--roster",required=True,type=Path)
    r.add_argument("--slot",type=int,required=True)
    s=commands.add_parser("status")
    s.add_argument("--roster",required=True,type=Path)
    args=parser.parse_args()
    os.umask(0o077)
    if args.action=="prepare":
        result=create_roster(args.root,args.root/"timers-roster.json")
        print(json.dumps(result,sort_keys=True))
    elif args.action=="run":
        result=run_slot(args.roster,args.slot)
        print(json.dumps(result,sort_keys=True))
        if result["state"]!="SOURCE_INPUT_SEALED_NO_LABEL":
            raise SystemExit(1)
    else:
        roster=load_roster(args.roster)
        print(json.dumps({
          "roster_id":roster["roster_id"],
          "slots":roster["slots"], "authority":False,
          "broker_orders_sent":0},sort_keys=True))

if __name__=="__main__":
    main()
