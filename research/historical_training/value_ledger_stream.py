"""Bounded, immutable-prefix OMS replay. Diagnostic only; never trade or alter a frozen plan.

The producer can keep appending beyond the size of a historical receipt. Read
exactly the pinned byte count and verify its SHA256, never reinterpret the tail.
"""
from __future__ import annotations
import hashlib, json, os, stat
from pathlib import Path

BLOCK=1024*1024
MAX_RECORD=1024*1024
MAX_PREFIX=512*1024*1024

def strict(raw):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise ValueError('DUPLICATE_LEDGER_JSON_KEY')
            d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=pairs,
                      parse_constant=lambda _:(_ for _ in ()).throw(ValueError('NONFINITE_JSON')))

def stream_execution_index(source,max_prefix=MAX_PREFIX, *, timestamp_observer=None):
    size=source.get('bytes')
    expected=source.get('sha256')
    path=source.get('path')
    if type(size) is not int or not 0<=size<=max_prefix or type(expected) is not str or len(expected)!=64 or not isinstance(path,str):
        raise ValueError('INVALID_PINNED_LEDGER_PREFIX')
    try:bytes.fromhex(expected)
    except ValueError:raise ValueError('INVALID_PINNED_LEDGER_HASH')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_size<size:
            raise ValueError('LEDGER_PRIVATE_PREFIX_UNAVAILABLE')
        h=hashlib.sha256();tail=b'';left=size;by_id={};by_command={};execution_commands={}
        while left:
            chunk=os.read(fd,min(BLOCK,left))
            if not chunk:raise ValueError('LEDGER_SHORT_PREFIX')
            h.update(chunk);left-=len(chunk)
            payload=tail+chunk
            lines=payload.split(b'\n');tail=lines.pop()
            if len(tail)>MAX_RECORD:raise ValueError('OVERSIZED_LEDGER_RECORD')
            for line in lines:
                if len(line)>MAX_RECORD:raise ValueError('OVERSIZED_LEDGER_RECORD')
                rec=strict(line)
                if timestamp_observer is not None and type(rec.get("ts_ms")) is int:
                    timestamp_observer(rec["ts_ms"])
                if rec.get('event')!='broker_execution' or not rec.get('broker_execution_id') or type(rec.get('ts_ms')) is not int:
                    continue
                eid=rec['broker_execution_id'];at=rec['ts_ms']
                if not isinstance(eid,str) or at<0:raise ValueError('LEDGER_EXECUTION_IDENTITY')
                by_id[eid]=min(by_id.get(eid,at),at)
                if rec.get('execution_domain')=='PAPER' and rec.get('instrument')=='EUR.USD' and rec.get('req_id'):
                    cmd=rec['req_id']
                    if not isinstance(cmd,str):raise ValueError('LEDGER_COMMAND_IDENTITY')
                    by_command[cmd]=min(by_command.get(cmd,at),at)
                    old=execution_commands.get(eid)
                    if old is not None and old!=cmd:raise ValueError('EXECUTION_COMMAND_CHANGED')
                    execution_commands[eid]=cmd
        if tail:raise ValueError('LEDGER_PREFIX_NOT_RECORD_BOUNDARY')
        if h.hexdigest()!=expected:raise ValueError('LEDGER_PINNED_PREFIX_CHANGED')
        return by_id,by_command,execution_commands
    finally:
        os.close(fd)
