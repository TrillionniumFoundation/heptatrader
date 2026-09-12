#!/usr/bin/env python3
"""Bounded read-only journal/storage diagnostics; never a reconciliation authority."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import stat
import sys
import time
from hepta_evidence_io import EvidenceError, loads, canonical_bytes


def inspect_journal(path: Path, *, max_bytes=8*1024*1024, byte_budget=None, minimum_free_bytes=0):
    if type(max_bytes) is not int or not 1<=max_bytes<=64*1024*1024:raise EvidenceError('scan budget must be 1..64MiB')
    if byte_budget is not None and (type(byte_budget) is not int or byte_budget<=0):raise EvidenceError('positive byte budget required')
    if type(minimum_free_bytes) is not int or minimum_free_bytes<0:raise EvidenceError('invalid free-space reserve')
    started=time.monotonic_ns();fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        initial=os.fstat(fd)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink!=1:raise EvidenceError('journal must be regular single-link')
        target=min(initial.st_size,max_bytes);chunks=[];size=0
        while size<target:
            chunk=os.read(fd,min(65536,target-size))
            if not chunk:break
            chunks.append(chunk);size+=len(chunk)
        payload=b''.join(chunks);final=os.fstat(fd);fs=os.fstatvfs(fd)
    finally:os.close(fd)
    changed=(initial.st_dev,initial.st_ino,initial.st_size,initial.st_mtime_ns)!=(final.st_dev,final.st_ino,final.st_size,final.st_mtime_ns)
    complete=size==initial.st_size and not changed
    tail_incomplete=bool(payload and not payload.endswith(b'\n'))
    if tail_incomplete:payload=payload.rsplit(b'\n',1)[0] if b'\n' in payload else b''
    schemas=Counter();events=Counter();invalid=0;count=0
    for line in payload.splitlines():
        count+=1
        if len(line)>1024*1024:invalid+=1;continue
        try:row=loads(line)
        except EvidenceError:invalid+=1;continue
        if not isinstance(row,dict):invalid+=1;continue
        schema=row.get('schema_version',row.get('schemaVersion','unknown'))
        schemas[str(schema) if type(schema) is int else 'unknown']+=1
        event=row.get('event_type',row.get('eventType','unknown'))
        # Metrics must not turn arbitrary journal strings into unbounded labels
        # or disclose raw account/credential text.
        allowed={'order_intent','place_send_attempt','place_sent','place_outcome_uncertain',
                 'broker_execution','broker_order_status','order_owner_reconciled_terminal','execution_command_resolved'}
        events[event if isinstance(event,str) and event in allowed else 'other']+=1
    free=fs.f_bavail*fs.f_frsize
    warnings=[]
    if byte_budget is not None and initial.st_size>=byte_budget:warnings.append('JOURNAL_BYTE_BUDGET_REACHED')
    if free<minimum_free_bytes:warnings.append('FREE_SPACE_RESERVE_BREACHED')
    if not complete:warnings.append('BOUNDED_OR_CHANGING_SCAN')
    if tail_incomplete:warnings.append('INCOMPLETE_TRAILING_RECORD')
    if invalid:warnings.append('INVALID_RECORDS_OBSERVED')
    return dict(schema='heptatrader.runtime-diagnostics.v1',journal_bytes=initial.st_size,scanned_bytes=size,
                complete_scan=complete and not tail_incomplete,changed_during_scan=changed,records_examined=count,
                invalid_records=invalid,schema_counts=dict(schemas),event_counts=dict(events),free_bytes=free,
                scan_duration_ms=(time.monotonic_ns()-started)/1e6,warnings=warnings,
                authorization_effect='NONE',paper_authorized=False,live_authorized=False,
                reconciliation_claim='NONE')


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--journal',type=Path,required=True)
    parser.add_argument('--max-scan-bytes',type=int,default=8*1024*1024)
    parser.add_argument('--byte-budget',type=int);parser.add_argument('--minimum-free-bytes',type=int,default=0)
    args=parser.parse_args(argv)
    try:
        result=inspect_journal(args.journal,max_bytes=args.max_scan_bytes,byte_budget=args.byte_budget,minimum_free_bytes=args.minimum_free_bytes)
        print(canonical_bytes(result).decode(),end='');return 2 if result['warnings'] else 0
    except (OSError,ValueError) as exc:
        print('[DIAGNOSTICS] FAIL: '+str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
