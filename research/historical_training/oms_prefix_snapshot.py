"""Freeze a verified prefix of an append-only OMS journal, without mutating it.

The observed full-file hash in a past IB receipt can match a historical
newline-aligned prefix even after later appends. Never infer this from size or
timestamps: exact SHA-256 equality is mandatory before and after copy.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from .common import EvidenceError

MAX_JOURNAL_BYTES=64*1024*1024
MAX_RECORDS=250000

def seal_prefix(journal, expected_sha, destination):
    journal=Path(journal);destination=Path(destination)
    if not isinstance(expected_sha,str) or not re.fullmatch(r"[0-9a-f]{64}",expected_sha):
        raise EvidenceError("OMS_PREFIX_SHA_INVALID")
    if (not journal.is_absolute() or journal.is_symlink()
        or destination.exists() or destination.is_symlink()
        or not destination.is_absolute()):
        raise EvidenceError("OMS_PREFIX_PATH_OR_DESTINATION")
    if journal.stat().st_size>MAX_JOURNAL_BYTES:
        raise EvidenceError("OMS_PREFIX_JOURNAL_TOO_LARGE")
    fingerprint=hashlib.sha256()
    count=0;length=0;found=False
    with journal.open("rb") as source:
        for line in source:
            if len(line)>65536 or not line.endswith(b"\n") or count>=MAX_RECORDS:
                raise EvidenceError("OMS_PREFIX_RECORD_BOUND")
            fingerprint.update(line);length+=len(line);count+=1
            if fingerprint.hexdigest()==expected_sha:
                found=True
                break
    if not found:
        raise EvidenceError("OMS_PREFIX_HASH_NOT_FOUND")
    destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    try:
        with os.fdopen(fd,"wb") as out, journal.open("rb") as src:
            remaining=length
            copied=hashlib.sha256()
            while remaining:
                chunk=src.read(min(1024*1024,remaining))
                if not chunk:
                    raise EvidenceError("OMS_PREFIX_COPY_SHORT")
                out.write(chunk);copied.update(chunk);remaining-=len(chunk)
            out.flush();os.fsync(out.fileno())
        if copied.hexdigest()!=expected_sha:
            raise EvidenceError("OMS_PREFIX_CHANGED_DURING_COPY")
        # Re-read the live source prefix, guarding against changes after the
        # original scan but before publication.
        original=hashlib.sha256()
        with journal.open("rb") as src:
            remaining=length
            while remaining:
                chunk=src.read(min(1024*1024,remaining))
                if not chunk:
                    raise EvidenceError("OMS_PREFIX_SOURCE_SHRUNK")
                original.update(chunk);remaining-=len(chunk)
        if original.hexdigest()!=expected_sha:
            raise EvidenceError("OMS_PREFIX_CHANGED_AFTER_COPY")
        parent_fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(parent_fd)
        finally:os.close(parent_fd)
        return {"schema":"heptatrader.oms-verified-prefix-snapshot.v1",
                "source_sha256":expected_sha,"snapshot_sha256":copied.hexdigest(),
                "prefix_bytes":length,"prefix_records":count,
                "original_journal_untouched":True,"broker_requests_sent":0,"orders_sent":0}
    except BaseException:
        # Invalid evidence is not deleted behind an operator's back. It
        # remains untrusted and must never be passed downstream.
        raise

def main():
    ap=argparse.ArgumentParser()
    for name in ("journal","receipt","output"):
        ap.add_argument("--"+name,type=Path,required=True)
    a=ap.parse_args()
    if a.output.exists() or a.output.is_symlink():
        raise SystemExit("OUTPUT_EXISTS")
    from .common import read
    r=read(a.receipt,8*1024*1024)
    if (r.get("schema")!="heptatrader.ib-paper-fx-execution-server-time.v1"
        or r.get("complete") is not True or r.get("broker_end_received") is not True
        or r.get("broker_orders_sent")!=0):
        raise EvidenceError("OMS_PREFIX_SOURCE_RECEIPT_UNTRUSTED")
    info=seal_prefix(a.journal,r["oms_sha256"],a.output)
    print(json.dumps(info,sort_keys=True))

if __name__=="__main__":
    main()
