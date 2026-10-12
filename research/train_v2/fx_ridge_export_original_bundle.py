"""Export exact frozen original quote prefixes and non-secret research receipts.

Portable bundle for independent MAC verification. No credentials/accounts, live
API reads, order permission, or mutation to the existing frozen evidence.
"""
from __future__ import annotations
import argparse, hashlib, json, os, stat, zipfile
from pathlib import Path

def fail(msg): raise ValueError(msg)
def sha(b):return hashlib.sha256(b).hexdigest()
def as_json(b):return json.loads(b)
def read_private(p,cap=64*1024*1024):
    p=Path(p)
    if p.is_symlink() or not p.is_absolute():fail("PATH_SCOPE")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if not(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
               and st.st_nlink==1 and not st.st_mode&0o077 and 0<st.st_size<=cap):
            fail("PRIVATE_SOURCE_BOUND")
        chunks=[];left=st.st_size
        while left:
            b=os.read(fd,min(1024*1024,left))
            if not b:fail("SHORT_READ")
            chunks.append(b);left-=len(b)
        b=os.fstat(fd)
        if (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)!=(
                b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns):fail("SOURCE_CHANGED")
        return b"".join(chunks)
    finally:os.close(fd)
def validate_quote_file(raw):
    if not raw.endswith(b"\n"):fail("INCOMPLETE_SOURCE_PREFIX")
    for line in raw.splitlines():
        r=as_json(line)
        if set(r)!={"schema","sequence","previous","at_ms","kind","data","sha256"}:
            fail("SOURCE_RECORD_SCHEMA_INVALID")
        if r["kind"] not in ("HEADER","QUOTE","FAILURE","END"):fail("UNEXPECTED_SOURCE_EVENT")
        if r["kind"]=="QUOTE":
            if not set(r["data"]) <= {
               "instrument","source","authoritative","stale","bid","ask",
               "observed_at_ms","stale_after_ms","subscription_id","subscription_state"
            }:fail("UNEXPECTED_DATA_FIELD")
        if r["kind"]=="HEADER" and not set(r["data"]) <= {
            "source","instrument","execution_domain","period_sha256",
            "write_operations_allowed","broker_orders_sent","collector_sha256"
        }:fail("UNEXPECTED_HEADER_FIELD")
def export(root,cost,out):
    artifacts={}
    for fn in ("model","manifest","plan","feature-spec","local-registration",
               "preoutcome-decision","qualification","mature-outcome"):
        b=read_private(root/(fn+".json"),200000)
        artifacts["artifacts/"+fn+".json"]=b
    cb=read_private(cost,1024*1024)
    artifacts["artifacts/cost.json"]=cb
    m=as_json(artifacts["artifacts/manifest.json"])
    dec=as_json(artifacts["artifacts/preoutcome-decision.json"])
    outcome=as_json(artifacts["artifacts/mature-outcome.json"])
    if sha(cb)!=m["cost_receipt_sha256"]:fail("COST_RECEIPT_CHANGED")
    provenance={}
    for role,rows in (
        ("preaction",dec["source_prefix_receipts"]),
        ("outcome",outcome["mature_source_prefix_receipts"])):
        for receipt in rows:
            path=receipt["path"];n=receipt["complete_prefix_bytes"]
            if not path.startswith(m["source_root"]+"/"):fail("QUOTE_SOURCE_OUTSIDE_ROOT")
            if type(n)is not int or not 0<n<=64*1024*1024:fail("QUOTE_PREFIX_SIZE")
            entry=provenance.setdefault(path,{"max_bytes":0,"receipts":[]})
            entry["max_bytes"]=max(entry["max_bytes"],n)
            entry["receipts"].append({"role":role,"bytes":n,"sha256":receipt["prefix_sha256"]})
    source={}
    for index,(source_path,entry) in enumerate(sorted(provenance.items())):
        raw=read_private(source_path)
        if len(raw)<entry["max_bytes"]:fail("SOURCE_SHORTER_THAN_FROZEN")
        prefix=raw[:entry["max_bytes"]]
        validate_quote_file(prefix)
        for r in entry["receipts"]:
            if sha(prefix[:r["bytes"]])!=r["sha256"]:fail("PINNED_SOURCE_PREFIX_CHANGED")
        name=f"source/{index:03d}.jsonl"
        artifacts[name]=prefix
        source[source_path]={"archive":name,"receipts":entry["receipts"],
                             "max_bytes":entry["max_bytes"],"max_sha256":sha(prefix)}
    index={"schema":"heptatrader.fx-ridge-portable-original-bundle.v1",
           "research_only":True,"broker_requests_sent":0,"broker_orders_sent":0,
           "model_promotion_authorized":False,
           "file_sha256":{k:sha(v) for k,v in sorted(artifacts.items())},
           "source_map":source,
           "original_broker_signatures_attested":False}
    artifacts["index.json"]=(json.dumps(index,sort_keys=True,indent=2)+"\n").encode()
    out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(out,os.O_EXCL|os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    os.close(fd)
    try:
        with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
            for name,b in sorted(artifacts.items()):
                meta=zipfile.ZipInfo(name)
                meta.external_attr=(0o600<<16)
                meta.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(meta,b,compress_type=zipfile.ZIP_DEFLATED,compresslevel=3)
        stat_file=out.stat()
        print(json.dumps({"state":"PORTABLE_READONLY_BUNDLE_CREATED",
                          "source_files":len(source),
                          "frozen_prefix_receipts":sum(len(e["receipts"]) for e in source.values()),
                          "bundle_bytes":stat_file.st_size,
                          "bundle_sha256":sha(out.read_bytes()),
                          "trade_authority":False,"broker_orders_sent":0}))
    except BaseException:
        out.unlink(missing_ok=True)
        raise

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--cost",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    a=ap.parse_args();os.umask(0o077)
    export(a.root,a.cost,a.output)
if __name__=="__main__":main()
