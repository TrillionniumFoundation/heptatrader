"""Independent, relocate-safe verifier of COPIED, naturally ended PAPER originals.

Standalone stdlib implementation: it does not call the native Train V2 source
reader, doesn't rewrite absolute checkpoint paths and owns no IB/model/action API.
Copies of complete original files are rehashed; the original absolute path is
a not independently signed identity string in the archive, not a local filesystem path.

This is machine-to-machine byte-custody verification, not host-root attestation
or a new prospective prediction, a financial reward or model promotion.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import stat

SCHEMA="heptatrader.train-v2-crosshost-original-price-custody.v1"
ORIGINAL_SCHEMA="heptatrader.laya-shadow-quotes.v1"
PAGE_SCHEMA="heptatrader.laya-source-archive-page.v1"
LEDGER_SCHEMA="heptatrader.laya-archive-ledger.v1"
NUMERIC_NAME=re.compile(r"^segment-([0-9]{19})-([0-9a-f]{8})$")
GENESIS="0"*64
MAX_SEGMENT_BYTES=64*1024*1024
MAX_ARCHIVE_PAGES=128
MAX_GLOBAL_RECEIPTS=2_000_000

class CustodyError(ValueError):pass

def require(check,why):
    if not check:raise CustodyError(why)

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),
                      ensure_ascii=True,allow_nan=False).encode()

def strict(raw):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise CustodyError("DUPLICATE_JSON_KEY")
            result[k]=v
        return result
    return json.loads(raw,object_pairs_hook=pairs,
                      parse_constant=lambda _:(_ for _ in ()).throw(
                          CustodyError("NONFINITE_JSON")))

def sha(raw):return hashlib.sha256(raw).hexdigest()

def private_file(file,bound):
    file=Path(file)
    require(file.is_file() and not file.is_symlink(),"COPIED_FILE_UNSAFE")
    info=file.stat()
    require(info.st_mode & 0o077==0 and info.st_size<=bound
            and stat.S_ISREG(info.st_mode),"COPIED_FILE_PRIVACY_OR_BOUND")
    return file.read_bytes()

def positive_price(value):
    require(not isinstance(value,bool),"BROKER_PRICE_BOOL")
    try:
        x=Decimal(str(value))
    except (ValueError,TypeError,InvalidOperation) as err:
        raise CustodyError("BROKER_PRICE_INVALID") from err
    require(x.is_finite() and Decimal(0)<x<Decimal(100000000),
            "BROKER_PRICE_NONFINITE_OR_NONPOSITIVE")
    return x

def original_segment(copy_file, origin_name, period):
    """Replay every original line and preserve first locally knowable quote per t."""
    raw=private_file(copy_file,MAX_SEGMENT_BYTES)
    require(raw and raw.endswith(b"\n"),"ORIGINAL_SEGMENT_INCOMPLETE")
    previous=GENESIS
    previous_at=0
    ended=False
    header=None
    by_event={}
    for number,line in enumerate(raw.splitlines()):
        require(len(line)<=65536,"ORIGINAL_LINE_LIMIT")
        record=strict(line)
        require(line==canonical(record)
                and record.get("schema")==ORIGINAL_SCHEMA
                and record.get("sequence")==number
                and record.get("previous")==previous
                and record.get("sha256")==sha(canonical({
                    k:v for k,v in record.items() if k!="sha256"})),
                "ORIGINAL_LINE_HASH_OR_SEQUENCE")
        at=record.get("at_ms")
        require(type(at) is int and previous_at<=at
                and not ended,"ORIGINAL_CLOCK_OR_AFTER_END")
        previous_at=at
        previous=record["sha256"]
        kind=record.get("kind")
        data=record.get("data")
        if number==0:
            require(kind=="HEADER" and isinstance(data,dict)
                    and data.get("period_sha256")==period
                    and data.get("instrument")=="EUR.USD"
                    and data.get("source")=="IB_TOOL_GATEWAY"
                    and data.get("execution_domain")=="PAPER"
                    and data.get("write_operations_allowed")==[]
                    and data.get("broker_orders_sent")==0,
                    "ORIGINAL_HEADER_OR_SCOPE")
            header=data
        elif kind=="QUOTE":
            require(isinstance(data,dict)
                    and data.get("instrument")=="EUR.USD"
                    and data.get("source")=="IB"
                    and data.get("authoritative") is True
                    and data.get("stale") is False
                    and isinstance(data.get("subscription_id"),str)
                    and data["subscription_id"],
                    "ORIGINAL_QUOTE_AUTHORITY")
            event=data.get("observed_at_ms")
            expiry=data.get("stale_after_ms")
            require(type(event) is int and type(expiry) is int
                    and 0<event<=at<=min(event+5000,expiry),
                    "ORIGINAL_QUOTE_EXPIRED_AT_CAPTURE")
            bid=positive_price(data.get("bid"))
            ask=positive_price(data.get("ask"))
            require(bid<=ask,"ORIGINAL_CROSSED_QUOTE")
            current=(at,event,expiry,record["sha256"],(bid+ask)/2)
            old=by_event.get(event)
            if old is not None:
                require(old[4]==current[4],"ORIGINAL_EVENT_PRICE_CONFLICT")
                if current[0]<old[0]:by_event[event]=current
            else:by_event[event]=current
        elif kind=="END":
            require(isinstance(data,dict) and data.get("broker_orders_sent")==0,
                    "ORIGINAL_END_AUTHORITY")
            ended=True
        elif kind!="FAILURE":
            raise CustodyError("ORIGINAL_UNKNOWN_KIND")
    require(header is not None and ended,"ORIGINAL_NOT_NATURALLY_ENDED")
    proof={
        "path":origin_name,"bytes":len(raw),
        "sha256":sha(raw),"chain_head":previous,
        "uncommitted_tail_bytes":0,"ended":True,
    }
    return proof,[by_event[t] for t in sorted(by_event)]

def copied_original_inventory(copy_root, original_root, period, begin, end):
    """Full-chain, page-ledger and source byte proof; only bounded price retention."""
    copy_root=Path(copy_root)
    require(copy_root.is_dir() and not copy_root.is_symlink()
            and copy_root.stat().st_mode & 0o077==0,
            "COPIED_SOURCE_ROOT_UNSAFE")
    require(original_root.startswith("/") and ".." not in
            Path(original_root).parts,"ORIGINAL_ABSOLUTE_ROOT_REQUIRED")
    require(type(begin) is int and type(end) is int and
            0<begin<end and end-begin<=7200000,"COPIED_OUTCOME_WINDOW_BOUND")
    archives=copy_root/"source-archive-v1"
    require(archives.is_dir() and not archives.is_symlink(),
            "COPIED_ARCHIVE_MISSING")
    numeric=[]
    for folder in copy_root.iterdir():
        if not folder.name.startswith("segment-"):continue
        require(not folder.is_symlink() and folder.is_dir(),"SEGMENT_COPY_SYMLINK")
        match=NUMERIC_NAME.fullmatch(folder.name)
        require(match is not None,"SEGMENT_COPY_UNKNOWN_IDENTITY")
        require(folder.stat().st_mode & 0o077==0,"SEGMENT_COPY_PRIVACY")
        numeric.append((int(match.group(1)),folder))
    numeric.sort()
    require(bool(numeric),"COPIED_NUMERIC_SEGMENTS_MISSING")
    require(len({i for i,_ in numeric})==len(numeric),"COPIED_NUMERIC_START_REUSED")
    # Byte and *logical* quote identity inventory is never truncated at 320.
    proofs=[]
    pinned={}
    seen_ids=set()
    by_event={}
    total_quote_count=0
    total_file_bytes=0
    selected_count=0
    for start,folder in numeric:
        file=folder/"quotes.jsonl"
        target=f"{original_root}/{folder.name}/quotes.jsonl"
        proof,rows=original_segment(file,target,period)
        proofs.append({"segment_start_ns":start,
                       "original_sha256":proof["sha256"],
                       "quote_count":len(rows)})
        pinned[target]=proof
        total_file_bytes+=proof["bytes"]
        total_quote_count+=len(rows)
        require(total_quote_count<=MAX_GLOBAL_RECEIPTS,
                "COPIED_SOURCE_RECEIPT_BUDGET")
        for q in rows:
            require(q[3] not in seen_ids,"COPIED_ORIGINAL_RECEIPT_REUSED")
            seen_ids.add(q[3])
            if begin<=q[0]<=end:
                old=by_event.get(q[1])
                if old is not None:
                    require(old[4]==q[4],"COPIED_GLOBAL_EVENT_PRICE_CONFLICT")
                    if q[0]<old[0]:by_event[q[1]]=q
                else:by_event[q[1]]=q
                selected_count+=1
    require(selected_count<=18000,"COPIED_WINDOW_ROW_BUDGET")
    pages=sorted(archives.glob("page-*.json"))
    require(len(pages)<=MAX_ARCHIVE_PAGES,"COPIED_ARCHIVE_PAGE_BUDGET")
    index_names=[f"page-{i:08d}.json" for i in range(len(pages))]
    require([p.name for p in pages]==index_names,"COPIED_ARCHIVE_PAGE_SEQUENCE")
    checkpoint=private_file(archives/"ledger.jsonl",64*1024*1024)
    require(checkpoint.endswith(b"\n"),"COPIED_ARCHIVE_LEDGER_TRUNCATED")
    lines=checkpoint.splitlines()
    require(len(lines)==len(pages),"COPIED_ARCHIVE_LEDGER_PAGE_COUNT")
    previous_page=GENESIS
    previous_ledger=GENESIS
    ordered_pins=[]
    for index,page in enumerate(pages):
        raw=private_file(page,256*1024)
        doc=strict(raw)
        require(raw==canonical(doc)+b"\n"
                and set(doc)=={"schema","index","previous_sha256",
                                "period_sha256","prefixes"}
                and doc.get("schema")==PAGE_SCHEMA
                and doc.get("index")==index
                and doc.get("period_sha256")==period
                and doc.get("previous_sha256")==previous_page
                and isinstance(doc.get("prefixes"),list)
                and len(doc["prefixes"])==32,
                "COPIED_ARCHIVE_PAGE_CHAIN")
        ledger=strict(lines[index])
        require(lines[index]==canonical(ledger)
                and set(ledger)=={"schema","index","page_sha256",
                                   "previous_receipt_sha256","receipt_sha256"}
                and ledger.get("schema")==LEDGER_SCHEMA
                and ledger.get("index")==index
                and ledger.get("page_sha256")==sha(raw)
                and ledger.get("previous_receipt_sha256")==previous_ledger
                and ledger.get("receipt_sha256")==sha(canonical({
                    k:v for k,v in ledger.items() if k!="receipt_sha256"})),
                "COPIED_ARCHIVE_LEDGER_CHECKPOINT_CHANGED")
        previous_page=sha(raw)
        previous_ledger=ledger["receipt_sha256"]
        ordered_pins.extend(doc["prefixes"])
    require(len(ordered_pins)<=len(proofs),"COPIED_ARCHIVE_REFERENCES_MISSING")
    for i,record in enumerate(ordered_pins):
        actual=pinned.get(record.get("path")) if isinstance(record,dict) else None
        # The immutable archive lists exactly the FIRST N original segments.
        expected=original_root+"/"+numeric[i][1].name+"/quotes.jsonl"
        require(actual is not None and record==actual
                and record.get("path")==expected,
                "COPIED_ARCHIVE_SEGMENT_CHECKPOINT_CHANGED")
    return {
        "proofs":proofs,"window_rows":sorted(
            by_event.values(),key=lambda q:(q[1],q[0],q[3])),
        "numeric_segments":len(numeric),
        "archived_segments":len(ordered_pins),
        "archive_pages":len(pages),
        "source_fingerprint_sha256":sha(canonical(proofs)),
        "original_source_bytes":total_file_bytes,
        "original_selected_quote_receipts":selected_count,
        "original_total_unique_quote_receipts":total_quote_count,
        "original_archive_checkpoint_chain_head":previous_ledger,
        "frozen_prefix_sha256":lambda count:sha(canonical(proofs[:count])),
    }

def independent_price_label(window_rows, original):
    """First actual sealed original event >= frozen due, 5s and 30s hard caps."""
    ref=original.get("source_quote_receipt")
    event_at=original.get("source_event_at_ms")
    available=original.get("original_quote_known_at_ms")
    due=original.get("original_price_due_at_ms")
    require(isinstance(ref,str) and len(ref)==64
            and type(event_at) is int and type(available) is int
            and type(due) is int and due==event_at+300000,
            "OUTCOME_FROZEN_SOURCE_OR_HORIZON_INVALID")
    matches=[x for x in window_rows if x[3]==ref]
    require(len(matches)==1,"OUTCOME_ORIGINAL_INPUT_NOT_UNIQUE")
    point=matches[0]
    require(point[0]==available and point[1]==event_at,
            "OUTCOME_ORIGINAL_EVENT_TIME_CHANGED")
    ordered=sorted(window_rows,key=lambda x:(x[1],x[0],x[3]))
    events={row[1]:row for row in ordered}
    times=sorted(events)
    require(event_at in events and events[event_at][3]==ref,
            "OUTCOME_SOURCE_REFERENCE_DIFFERENT")
    target=next((t for t in times if t>=due),None)
    require(target is not None and target<=due+5000,
            "OUTCOME_ORIGINAL_ENDPOINT_MISSING")
    path=[t for t in times if event_at<=t<=target]
    require(path and path[0]==event_at and path[-1]==target,
            "OUTCOME_SOURCE_PATH_INCOMPLETE")
    require(all(b-a<=30000 for a,b in zip(path,path[1:])),
            "OUTCOME_ORIGINAL_PRICE_PATH_GAP")
    actual=(events[target][4]/point[4]-Decimal(1))*Decimal(10000)
    require(actual==Decimal(str(original.get("original_price_change_bps"))),
            "OUTCOME_ORIGINAL_BPS_PRICE_MISMATCH")
    predicted=Decimal(str(original.get("model_score_fixed_bps")))
    direction=((predicted>0)==(actual>0)
               if predicted!=0 and actual!=0 else None)
    require(direction is original.get("price_direction_match"),
            "OUTCOME_ORIGINAL_DIRECTION_CHANGED")
    require(original.get("state")=="OBSERVED_ORIGINAL_PAPER_MID_PRICE_ONLY"
            and original.get("original_price_only_not_executable_fill") is True
            and original.get("actual_broker_fills")==0
            and original.get("actual_executed_net_pnl_samples")==0
            and original.get("native_f64_exact_replay_attested") is False
            and original.get("model_promotion_permitted") is False
            and original.get("broker_orders_sent")==0,
            "OUTCOME_NOT_PRICE_ONLY_OR_AUTHORITY")
    return {
        "plan_id":original["plan_id"],
        "original_source_receipt":ref,
        "frozen_prediction_bps":str(predicted),
        "original_due_at_ms":due,
        "selected_original_price_event_ms":target,
        "original_price_return_bps":str(actual),
        "original_return_verified_exact":True,
        "direction_match":direction,
        "actual_executed_trade_count":0,
        "model_promotion_permitted":False,
    }

def verify_copied_pair(source_root, original_root, period, evidence_root,
                       *, expected_pair_sha256, expected_prefixes):
    evidence_root=Path(evidence_root)
    pair_file=evidence_root/"prior-native-price-pair-paged-crosscheck.json"
    raw=private_file(pair_file,128*1024)
    require(sha(raw)==expected_pair_sha256,"COPIED_PAIR_RECEIPT_HASH_CHANGED")
    prior=strict(raw)
    require(prior.get("schema")==
            "heptatrader.train-v2-paged-original-price-crosscheck.v1"
            and prior.get("state")==
            "TWO_PRIOR_PRICE_LABELS_EXACTLY_REPRODUCED_RESEARCH_ONLY"
            and prior.get("source_period_sha256")==period
            and prior.get("broker_orders_sent")==0
            and prior.get("actually_executed_trades")==0
            and prior.get("original_predictions_resealed") is False
            and isinstance(prior.get("rows"),list)
            and len(prior["rows"])==2,
            "COPIED_PRIOR_PAIR_SCOPE_OR_EFFECT")
    source_files=[
        evidence_root/"real-ticks-01-price-after-natural-seal.json",
        evidence_root/"real-ticks-02-price-after-natural-seal.json",
    ]
    originals=[strict(private_file(p,128*1024)) for p in source_files]
    for row,path in zip(prior["rows"],source_files):
        require(sha(private_file(path,128*1024))==
                row.get("original_outcome_sha256"),
                "COPIED_INDIVIDUAL_OUTCOME_HASH_CHANGED")
    begin=min(o["original_quote_known_at_ms"] for o in originals)-60000
    end=max(o["original_price_due_at_ms"] for o in originals)+65000
    source=copied_original_inventory(source_root,original_root,period,begin,end)
    for n,expected in expected_prefixes.items():
        require(type(n) is int and 0<n<=source["numeric_segments"],
                "COPIED_FROZEN_PREFIX_OUT_OF_RANGE")
        actual=source["frozen_prefix_sha256"](n)
        require(actual==expected,"COPIED_FROZEN_PREFIX_CHANGED")
    checked=[independent_price_label(source["window_rows"],o)
             for o in originals]
    require(len({r["plan_id"] for r in checked})==2 and
            len({r["original_source_receipt"] for r in checked})==2,
            "COPIED_PAIR_REPEATED_DECISION")
    for row,check,old in zip(prior["rows"],checked,originals):
        require(row["plan_id"]==check["plan_id"]
                and row["original_quote_receipt"]==check["original_source_receipt"]
                and Decimal(row["new_paged_price_change_bps"])==
                    Decimal(check["original_price_return_bps"])
                and Decimal(row["old_price_change_bps"])==
                    Decimal(check["original_price_return_bps"])
                and row["direction_match"] is check["direction_match"]
                and source["frozen_prefix_sha256"](
                    old["original_source_sealed_segments"])==
                    old["original_source_fingerprint_sha256"],
                "COPIED_PAIR_PRIOR_SOURCE_OR_PRICE_CHANGED")
    report={
        "schema":SCHEMA,
        "state":"INDEPENDENT_QIAN_QI_BYTES_AND_TWO_PRICE_LABELS_VERIFIED",
        "source_transport":"OPERATOR_CONTROLLED_SEQUENTIAL_SSH_RSYNC_SNAPSHOT",
        "independent_auditor_sha256":sha(Path(__file__).read_bytes()),
        "source_sha256_proven_by_locally_hash_chained_originals":True,
        "all_archive_checkpoints_verified_including_historical_pages":True,
        "original_source_root_identity":original_root,
        "locally_copied_original_segments":source["numeric_segments"],
        "original_archive_pages":source["archive_pages"],
        "original_archived_segments":source["archived_segments"],
        "original_source_bytes":source["original_source_bytes"],
        "original_total_quote_receipts":source["original_total_unique_quote_receipts"],
        "independent_source_full_snapshot_sha256":
            source["source_fingerprint_sha256"],
        "original_page_ledger_chain_head":
            source["original_archive_checkpoint_chain_head"],
        "original_prefix_fingerprints_checked":{
            str(n):value for n,value in expected_prefixes.items()},
        "frozen_individual_price_outcome_sha256":[
            x["original_outcome_sha256"] for x in prior["rows"]],
        "frozen_pair_evidence_sha256":expected_pair_sha256,
        "price_only_outcomes":checked,
        "price_direction_correct_count":sum(x["direction_match"] is True
                                             for x in checked),
        "original_old_f64_exact_failure_requalified":False,
        "strictly_new_prospective_forecasts_performed":0,
        "actual_broker_executed_trades":0,
        "after_cost_profit_attested":False,
        "canonical_owner_admitted":False,
        "trade_authority":False,
        "broker_queries_sent":0,
        "broker_orders_sent":0,
        "model_promoted":False,
    }
    return report

def main():
    parser=argparse.ArgumentParser()
    for arg in ("copy-root","original-root","period","evidence-root",
                "expected-pair-sha256","expected-prefix-282",
                "expected-prefix-283","output"):
        parser.add_argument("--"+arg,required=True)
    a=parser.parse_args()
    r=verify_copied_pair(
        a.copy_root,a.original_root,a.period,a.evidence_root,
        expected_pair_sha256=a.expected_pair_sha256,
        expected_prefixes={282:a.expected_prefix_282,
                           283:a.expected_prefix_283})
    output=Path(a.output)
    require(not output.is_symlink() and not output.exists(),
            "CROSSHOST_CREATE_ONLY_RECEIPT_REQUIRED")
    output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    raw=(json.dumps(r,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    rootfd=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(rootfd)
    finally:os.close(rootfd)
    print(json.dumps({
        "status":r["state"],
        "source_segments":r["locally_copied_original_segments"],
        "page_count":r["original_archive_pages"],
        "verified_source_quote_records":r["original_total_quote_receipts"],
        "original_price_outcomes_reproduced":len(r["price_only_outcomes"]),
        "correct_directions":r["price_direction_correct_count"],
        "after_cost_trading_alpha":False,
        "orders_sent":0,
        "receipt_sha256":sha(raw),
    },sort_keys=True))

if __name__=="__main__":main()
