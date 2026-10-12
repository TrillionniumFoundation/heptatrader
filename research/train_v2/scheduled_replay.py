"""Independent read-only replay of a finite pre-outcome original source capture.

The currently active source may have appended additional future quotes.
This verifier reconstructs the EXACT original prefix from its recorded chain
head and SHA256, then checks the original causal feature at the PRESEALED
decision time. It does not create a quote, label, model or broker request.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from hepta_decision_attribution import read_private, strict
from historical_training.common import sha, canonical
from laya_cell.shadow_source import MAX_BYTES
from .contracts import ContractError, compile_example, digest
from .causal_ttl import sealed_original_broker_expiry
from .prospective_multihorizon import (
    _load, INPUT_SCHEMA, DECISION_DELAY_MS, LAST_CAPTURE_DELAY_MS,
)
from .scheduled_horizon import (
    load_roster, RUN_SCHEMA, SCHEMA,
)
from .original_multihorizon import HORIZONS_MIN


def require(ok, code):
    if not ok:
        raise ContractError(code)


def recover_original_prefix(source, chain_head, prefix_sha):
    """Find byte prefix through a specific ORIGINAL signed-by-local-chain event.

    Original source is PRIVATE original PAPER file; SHA confirms the exact
    bytes recorded at the earlier decision, not the later appended suffix.
    """
    src=Path(source)
    require(not src.is_symlink() and src.is_file(), "REPLAY_ORIGINAL_SOURCE_UNSAFE")
    fd=os.open(src,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
            and st.st_mode & 0o077 == 0 and 0 < st.st_size <= MAX_BYTES,
            "REPLAY_ORIGINAL_FILE_PERMISSION_OR_SIZE")
        pieces=[]
        remain=st.st_size
        while remain:
            part=os.read(fd,min(remain,1024*1024))
            require(part,"REPLAY_ORIGINAL_INCOMPLETE_READ")
            pieces.append(part)
            remain-=len(part)
    finally:
        os.close(fd)
    raw=b"".join(pieces)
    offset=0
    seen=0
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        offset+=len(line)
        row=strict(line)
        if row.get("sha256")==chain_head:
            seen+=1
            prefix=raw[:offset]
            require(hashlib.sha256(prefix).hexdigest()==prefix_sha,
                    "REPLAY_ORIGINAL_PREFIX_BYTES_CHANGED")
            return prefix
    raise ContractError("REPLAY_ORIGINAL_CHAIN_HEAD_MISSING")


def verify_original_slot(root,slot):
    root=Path(root)
    require(type(slot)is int and 0<=slot<3, "REPLAY_SLOT_NUMBER")
    m=_load(root)
    roster=load_roster(root/"timers-roster.json")
    require(roster["campaign_id"]==m["campaign_id"],"REPLAY_COHORT_ID")
    frozen_path=root/f"slot-{slot:02d}.input.json"
    timer_path=root/f"slot-{slot:02d}.timer-result.json"
    require(frozen_path.is_file() and not frozen_path.is_symlink(),
            "REPLAY_UNSEALED_SOURCE")
    require(timer_path.is_file() and not timer_path.is_symlink(),
            "REPLAY_TIMER_RECEIPT_MISSING")
    source=strict(read_private(frozen_path,128*1024))
    timer=strict(read_private(timer_path,128*1024))
    anchor=m["anchors_ms"][slot]
    decision=source.get("decision_at_ms")
    require(source.get("schema")==INPUT_SCHEMA
        and source.get("campaign_id")==m["campaign_id"]
        and source.get("input_id")==digest({
            k:v for k,v in source.items() if k!="input_id"})
        and source.get("slot")==slot
        and source.get("all_future_labels_unobserved") is True
        and source.get("forecast_model_run") is False
        and source.get("selected_original_trading_action") is None
        and source.get("broker_orders_sent")==0
        and type(decision)is int
        and anchor+DECISION_DELAY_MS<=decision<=anchor+LAST_CAPTURE_DELAY_MS,
        "REPLAY_ORIGINAL_INPUT_IDENTITY_OR_DEADLINE")
    require(timer.get("schema")==RUN_SCHEMA
        and timer.get("result_id")==digest({
            k:v for k,v in timer.items() if k!="result_id"})
        and timer.get("roster_id")==roster["roster_id"]
        and timer.get("slot")==slot
        and timer.get("state")=="SOURCE_INPUT_SEALED_NO_LABEL"
        and timer.get("source_input_sha256")==sha(frozen_path)
        and timer.get("original_label_assigned") is False
        and timer.get("model_inference") is False
        and timer.get("broker_orders_sent")==0
        and timer.get("worker_started_at_ms")<=decision
        <=timer.get("worker_finished_at_ms")<=anchor+LAST_CAPTURE_DELAY_MS,
        "REPLAY_TIMER_OR_SOURCE_RECEIPT_MISMATCH")
    original=Path(source["original_segment_path"])
    source_root=Path(m["source_root"])
    require(original.is_absolute() and not original.is_symlink()
        and original.parent.parent==source_root
        and original.name=="quotes.jsonl"
        and not original.parent.is_symlink(),
        "REPLAY_ORIGINAL_SEGMENT_ROOT_CHANGED")
    prefix=recover_original_prefix(
        original,source["original_chain_head_at_input"],
        source["original_verified_prefix_sha256"])
    # Isolated temporary reconstruction of original source bytes; no change
    # to the running append-only original source or frozen campaign files.
    with tempfile.TemporaryDirectory(prefix="hepta-prospective-replay-") as folder:
        snap=Path(folder)/"quotes.jsonl"
        snap.write_bytes(prefix)
        snap.chmod(0o600)
        rows,proof=sealed_original_broker_expiry(snap,m["feature_spec"])
        require(proof["original_chain_head"]==source["original_chain_head_at_input"]
            and proof["verified_prefix_sha256"]==
                source["original_verified_prefix_sha256"],
            "REPLAY_ORIGINAL_SOURCE_PREFIX_DISAGREES")
        matches=[q for q in rows
                 if q["source_id"]==source["original_source_id"]]
        require(len(matches)==1,"REPLAY_ORIGINAL_QUOTE_MISSING")
        q=matches[0]
        require(q["source_sha256"]==source["original_source_sha256"]
            and q["available_at_ms"]==source["source_available_at_ms"]
            and q["event_at_ms"]==source["source_event_at_ms"]
            and q["valid_until_ms"]==source["source_valid_until_ms"]
            and m["local_registered_at_ms"]<q["available_at_ms"]<=decision
            <=q["valid_until_ms"],
            "REPLAY_ORIGINAL_QUOTE_AVAILABILITY_OR_EXPIRY")
        mid=(Decimal(str(q["bid"]))+Decimal(str(q["ask"])))/2
        require(str(mid)==source["input_quote_midpoint"],
                "REPLAY_INPUT_PRICE_CHANGED")
        reconstructed=compile_example(m["feature_spec"],rows,decision,decision)
        require(reconstructed["feature_id"]==source["feature_id"]
            and reconstructed["features"]["values"]==source["feature_values"]
            and reconstructed["features"]["input_receipts"]==
                source["original_feature_input_receipts"]
            and reconstructed["label_status"]=="PENDING_OUTCOME",
            "REPLAY_CAUSAL_FEATURE_OR_MATURITY_CHANGED")
        require(source["outcome_maturity_ms"]=={
            str(h):decision+h*60000 for h in HORIZONS_MIN},
            "REPLAY_HORIZON_DEADLINES_CHANGED")
    body={
        "schema":"heptatrader.train-v2-postcapture-asof-replay.v1",
        "campaign_id":m["campaign_id"],"roster_id":roster["roster_id"],
        "slot":slot,"original_source_sha256":sha(frozen_path),
        "timer_receipt_sha256":sha(timer_path),
        "original_complete_source_prefix_sha256":
            source["original_verified_prefix_sha256"],
        "actual_original_source_prefix_bytes":len(prefix),
        "original_hash_chain_head_at_decision":
            source["original_chain_head_at_input"],
        "original_quote_id":source["original_source_id"],
        "decision_at_ms":decision,
        "quote_available_at_ms":q["available_at_ms"],
        "original_source_latency_ms":decision-q["available_at_ms"],
        "source_expires_at_ms":q["valid_until_ms"],
        "feature_id":reconstructed["feature_id"],
        "feature_asof_reproduced_after_source_append":True,
        "original_exact_prefix_byte_verified":True,
        "all_future_labels_pending":True,
        "canonical_owner_signature":False,
        "model_prediction_prepared":False,
        "actual_fills_or_execution_pnl":False,
        "automatic_training":False,
        "broker_queries_sent":0,"broker_orders_sent":0,
        "trade_authority":False,
    }
    return {**body,"review_id":digest(body)}


def main():
    cli=argparse.ArgumentParser()
    cli.add_argument("--root",required=True,type=Path)
    cli.add_argument("--slot",required=True,type=int)
    cli.add_argument("--output",required=True,type=Path)
    args=cli.parse_args()
    receipt=verify_original_slot(args.root,args.slot)
    out=args.output
    if out.is_symlink() or out.exists():
        raise ContractError("REPLAY_IMMUTABLE_OUTPUT_ALREADY_EXISTS")
    out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    raw=canonical(receipt)+b"\n"
    fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    print(json.dumps({
        "status":"ORIGINAL_PREOUTCOME_SOURCE_INDEPENDENTLY_REPRODUCED",
        "slot":args.slot,
        "latency_ms":receipt["original_source_latency_ms"],
        "source_prefix_bytes":receipt["actual_original_source_prefix_bytes"],
        "review_id":receipt["review_id"],
        "receipt_sha256":hashlib.sha256(raw).hexdigest(),
        "broker_orders_sent":0
    },sort_keys=True))


if __name__=="__main__":
    main()
