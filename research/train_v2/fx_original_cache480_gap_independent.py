"""Independent stdlib reparse of exact qian-qi archived original quote incident.

No imports from the pocket4 analyzer and absolutely no network, broker or
order API. Inspect only four already mounted byte-exact immutable files.
"""
from __future__ import annotations
import argparse, bisect, collections, hashlib, json, os, pathlib, stat, sys, datetime

EXPECTED={
 "plan.json":"8a77da0fcdd3d251a19d53209fa7a836386848380f92e3681590b33ce2bf98d5",
 "receipt.json":"7f2f88ca47d181a0a8415c427913a848834a487f8b731f8e9fa3183a4206226b",
 "status.json":"10287642a8d196cc7bbed8783dfa9b8fad2531495de10a74f49d090719e94292",
 "original-quote-prefix.jsonl":"b20e5e067c239df5b3696b54200f858d8edc2058ec7bf8826aae6a21d6e2d73f"
}
PERIOD="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
def require(ok,reason):
    if not ok: raise ValueError(reason)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canon(obj):return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def strict(raw):
    def uniq(pairs):
        d={}
        for k,v in pairs:
            require(k not in d,"DUPLICATE_KEY")
            d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=uniq,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")))
def safe_bytes(p):
    require(p.is_absolute() and not p.is_symlink(),"INPUT_PATH")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        require(s.st_uid==os.geteuid() and not s.st_mode&0o077
                and stat.S_ISREG(s.st_mode) and s.st_nlink==1
                and 0<s.st_size<2*1024*1024,"INPUT_NOT_PRIVATE")
        data=bytearray()
        while len(data)<s.st_size:
            part=os.read(fd,min(131072,s.st_size-len(data)))
            require(bool(part),"INPUT_TRUNCATED")
            data.extend(part)
        after=os.fstat(fd)
        require((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),
                "SOURCE_CHANGED_DURING_READ")
        return bytes(data)
    finally:os.close(fd)
def verify(root,output):
    root=pathlib.Path(root)
    require(root.is_absolute() and not root.is_symlink() and not root.stat().st_mode&0o077,
            "QIAN_ORIGINAL_ROOT_PRIVACY")
    raws={k:safe_bytes(root/k) for k in EXPECTED}
    require(all(sha(raws[k])==v for k,v in EXPECTED.items()),"ORIGINAL_COPIED_SOURCE_HASH_CHANGED")
    plan,rec,status=(strict(raws[k]) for k in ("plan.json","receipt.json","status.json"))
    require(plan["period_sha256"]==status["period_sha256"]==rec["period_sha256"]==PERIOD,
            "ORIGINAL_PERIOD_NOT_BOUND")
    require(rec["index"]==0 and rec["state"]=="CAPTURED_ORIGINAL_CACHE_HEALTH"
            and rec["status_sha256"]==EXPECTED["status.json"]
            and rec["source_ready"] is False
            and status["state"]=="HOLD_NEW_FUSION_WINDOW"
            and status["usable"]==19 and status["unusable"]==5
            and status["counts"]=={"ENDPOINT_GAP":2,"PATH_GAP":3,"USABLE":19}
            and status["automatic_trial_registration"] is False
            and status["trading_authority"] is False,"FROZEN_HEALTH_HOLD_CHANGED")
    fingerprints=status["source_prefixes"]
    require(sha(canon(fingerprints))==status["source_prefix_digest"],
            "ORIGINAL_PREFIX_SET_CHANGED")
    ref=[x for x in fingerprints if x["path"].endswith(
        "/segment-1791882475208755463-94da1e1f/quotes.jsonl")]
    require(len(ref)==1 and ref[0]["bytes"]==len(raws["original-quote-prefix.jsonl"])
            and ref[0]["sha256"]==EXPECTED["original-quote-prefix.jsonl"]
            and ref[0]["ended"] is False,
            "GAP_SEGMENT_PREFIX_IDENTITY_CHANGED")
    data=raws["original-quote-prefix.jsonl"]
    require(data.endswith(b"\n"),"TRUNCATED_JSON_LINE")
    chain="0"*64
    quotes=[];fails=[];last_at=0;header=None
    for i,line in enumerate(data.splitlines()):
        obj=strict(line)
        body={k:v for k,v in obj.items() if k!="sha256"}
        require(obj["schema"]=="heptatrader.laya-shadow-quotes.v1"
                and obj["sequence"]==i and obj["previous"]==chain
                and obj["sha256"]==sha(canon(body)),
                "ORIGINAL_CHAIN_HASH_INVALID")
        chain=obj["sha256"]
        at=obj["at_ms"]
        require(type(at) is int and last_at<=at,
                "ORIGINAL_LOCAL_AVAILABLE_CLOCK_INVALID")
        last_at=at;kind=obj["kind"]
        if i==0:
            header=obj["data"]
            require(kind=="HEADER" and header["execution_domain"]=="PAPER"
                    and header["period_sha256"]==PERIOD
                    and header["instrument"]=="EUR.USD"
                    and header["broker_orders_sent"]==0,
                    "SOURCE_HEADER_INVALID")
        elif kind=="QUOTE":
            q=obj["data"]
            require(q["source"]=="IB" and q["authoritative"] is True
                    and q["stale"] is False and
                    0<q["observed_at_ms"]<=at<=q["stale_after_ms"]
                    and at<=q["observed_at_ms"]+5000,
                    "ORIGINAL_NOT_AUTHORITATIVE")
            quotes.append((q["observed_at_ms"],at,i,q["subscription_id"],
                           str(q["bid"]),str(q["ask"])))
        elif kind=="FAILURE":
            require(obj["data"]["quote_timestamp_rewritten"] is False,
                    "FAILURE_TIMESTAMP_REWRITTEN")
            fails.append((i,at,obj["data"]["reason"]))
        else:
            require(False,"UNEXPECTED_TERMINAL_EVENT")
    require(chain==ref[0]["chain_head"],"ORIGINAL_PREFIX_CHAIN_HEAD_MISMATCH")
    # Full captured SHA prefix may include rows appended during assessment.
    # Only original events locally available as of the status at_ms are
    # eligible for reproducing what the source monitor could have known.
    quotes=[q for q in quotes if q[1]<=status["at_ms"]]
    fails=[f for f in fails if f[1]<=status["at_ms"]]
    unique=sorted({q[0] for q in quotes})
    gaps=sorted([(b-a,a,b) for a,b in zip(unique,unique[1:])],reverse=True)
    require(bool(gaps) and gaps[0]==(123095,1791883752527,1791883875622),
            "ORIGINAL_RECORDED_GAP_CHANGED")
    gap,before,after=gaps[0]
    bef=max((q for q in quotes if q[0]==before),key=lambda q:q[2])
    aft=min((q for q in quotes if q[0]==after),key=lambda q:q[2])
    failure_rows=[x for x in fails if bef[2]<x[0]<aft[2]]
    failure_counts=dict(collections.Counter(x[2] for x in failure_rows))
    require(len(failure_rows)==61 and aft[2]-bef[2]-1==61
            and failure_counts=={
                "AUTHORITATIVE_QUOTE_STALE":32,
                "AUTHORITATIVE_QUOTE_UNREADY":13,
                "EXECUTION_EVENT_RESPONSE_READ_FAILED":6,
                "EXECUTION_EVENT_SERVICE_NOT_READY":1,
                "JSONDecodeError":9},
            "CONTIGUOUS_FAILURE_TYPE_OR_COUNT_CHANGED")
    counts=collections.Counter()
    confirmed=0
    for probe in status["probes"]:
        reason=probe["status"]
        counts[reason]+=1
        if reason=="ENDPOINT_GAP":
            due=probe["due_ms"]
            pos=bisect.bisect_left(unique,due)
            require(pos<len(unique) and
                    unique[pos]-due==probe["actual_delay_ms"]
                    and unique[pos]-due>5000
                    and before<=due<after,"ORIGINAL_ENDPOINT_DIFFERENCE")
            confirmed+=1
        elif reason=="PATH_GAP":
            require(probe["anchor_ms"]<after and
                    probe["due_ms"]>before,"ORIGINAL_PATH_OVERLAP")
            confirmed+=1
    require(dict(counts)==status["counts"] and confirmed==5,
            "ORIGINAL_HEALTH_PROBE_COUNT_CHANGED")
    report={
        "schema":"heptatrader.fx-local-original-gap-qian-independent.v1",
        "checked_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "independent_host":os.uname().nodename,
        "input_sha256":EXPECTED,
        "source_prefix_chain_head":chain,
        "exact_quote_first_before_gap_seq":bef[2],
        "exact_quote_first_after_gap_seq":aft[2],
        "actual_original_event_gap_ms":gap,
        "quote_local_delivery_gap_ms":aft[1]-bef[1],
        "failure_records_between_valid_quotes":len(failure_rows),
        "failure_counts":failure_counts,
        "different_subscription_identity_across_gap":bef[3]!=aft[3],
        "failed_original_probe_count_reproduced":confirmed,
        "original_hold_unmodified":True,
        "upstream_broker_signed":False,
        "production_owner_or_witness_admitted":False,
        "not_evidence_of_ib_market_close":True,
        "source_code_is_not_pocket4_research_verifier":True,
        "trading_or_model_authority":False,"broker_queries_or_orders":0,
    }
    target=pathlib.Path(output)
    require(target.is_absolute() and not target.exists()
            and target.parent==root,"OUTPUT_NOT_WRITE_ONCE_PRIVATE_ROOT")
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(canon(report)+b"\n");f.flush();os.fsync(f.fileno())
    print(json.dumps({k:report[k] for k in (
        "actual_original_event_gap_ms","failure_records_between_valid_quotes",
        "failed_original_probe_count_reproduced","upstream_broker_signed",
        "trading_or_model_authority")},sort_keys=True))
    return report
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    verify(args.root,args.output)
