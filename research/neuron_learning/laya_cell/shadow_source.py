"""Bounded immutable READ-only PAPER quote evidence for Laya future comparison.

The only gateway operation is market.get_quote. Never manufactures timestamps,
interpolates gaps, starts a trading service, or changes trade authorization.
"""
from __future__ import annotations
import argparse
from decimal import Decimal,InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
import uuid

SCHEMA='heptatrader.laya-shadow-quotes.v1'
MAX_BYTES=64*1024*1024
FIELDS=('instrument','source','authoritative','stale','bid','ask','observed_at_ms',
        'stale_after_ms','subscription_id','subscription_state')
# Exact original collector revision on the immutable Pocket4 segment population.
# It accepted a quote at the inclusive expiry boundary; newer native intake
# MUST continue refusing equality. Do not extend this exception to new hashes.
LEGACY_INCLUSIVE_EXPIRY_COLLECTOR_SHA256 = 'b35f3d118283e453973d654d0b1562b59cf74ae9e7a87a0e32d8141fb19372d6'

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
def strict(raw):
    def unique(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('DUPLICATE_FIELD')
            out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('NONFINITE')))

def valid_quote(q,received,instrument,*,legacy_historical_expiry=False):
    if (q.get('instrument')!=instrument or q.get('source')!='IB' or q.get('authoritative') is not True
        or q.get('stale') is not False or not isinstance(q.get('subscription_id'),str) or not q['subscription_id']):
        raise ValueError('QUOTE_AUTHORITY')
    t=q.get('observed_at_ms');expiry=q.get('stale_after_ms')
    if (type(t)is not int or type(expiry)is not int or not 0<t<=received
        or received>min(t+5000,expiry)
        or (received==min(t+5000,expiry) and not legacy_historical_expiry)):
        raise ValueError('QUOTE_STALE_OR_FUTURE')
    if any(isinstance(q.get(k),bool) for k in ('bid','ask')):raise ValueError('QUOTE_NUMERIC')
    try:bid=Decimal(str(q['bid']));ask=Decimal(str(q['ask']))
    except (InvalidOperation,KeyError):raise ValueError('QUOTE_NUMERIC') from None
    if not bid.is_finite() or not ask.is_finite() or not 0<bid<=ask<Decimal('1e12'):
        raise ValueError('QUOTE_NUMERIC')
    return bid,ask

def parse_native_quote_reply(returncode,stdout,received,instrument):
    """Fail closed on malformed gateway replies; never cache or refresh a quote.

    The original collector still owns its fixed read-only market.get_quote call.
    This pure parser has no broker client, subscription mutator or recovery
    commands. Recovering means accepting a *subsequent actual fresh quote*.
    """
    if type(returncode) is not int or type(stdout) is not str:
        raise ValueError('QUOTE_TOOL_RESPONSE_TYPE')
    try:
        obj=strict(stdout)
    except (ValueError,TypeError) as exc:
        raise ValueError('QUOTE_TOOL_JSON_INVALID') from None
    if type(obj) is not dict:
        raise ValueError('QUOTE_TOOL_RESPONSE_SHAPE')
    if returncode or obj.get('status')!='ok':
        reason=obj.get('reason_code')
        if (type(reason) is not str or not 1<=len(reason)<=100
            or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789'
                   for c in reason)):
            reason='QUOTE_TOOL_UNAVAILABLE'
        raise ValueError(reason)
    q=obj.get('payload')
    if type(q) is not dict:
        raise ValueError('QUOTE_TOOL_PAYLOAD_INVALID')
    if q.get('subscription_state')!='active':
        raise ValueError('QUOTE_SUBSCRIPTION_NOT_ACTIVE')
    valid_quote(q,received,instrument)
    return q

def read_source(path):
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size>MAX_BYTES or st.st_uid!=os.geteuid() or st.st_mode&0o077:
            raise ValueError('SHADOW_SOURCE_FILE_BOUND')
        remaining=st.st_size;parts=[]
        while remaining:
            part=os.read(fd,min(remaining,1024*1024))
            if not part:raise ValueError('SOURCE_SHORT_READ')
            parts.append(part);remaining-=len(part)
    finally:os.close(fd)
    raw=b''.join(parts);complete=raw[:raw.rfind(b'\n')+1]
    previous='0'*64;header=None;quotes={};last=0;ended=False
    now=int(time.time()*1000)
    for i,line in enumerate(complete.splitlines()):
        if len(line)>65536:raise ValueError('SHADOW_RECORD_BOUND')
        r=strict(line)
        if (r.get('schema')!=SCHEMA or r.get('sequence')!=i or r.get('previous')!=previous
            or r.get('sha256')!=digest({k:v for k,v in r.items() if k!='sha256'})):
            raise ValueError('SHADOW_SOURCE_CHAIN')
        at=r.get('at_ms')
        if type(at)is not int or not last<=at<=now:raise ValueError('SHADOW_SOURCE_CLOCK')
        last=at;previous=r['sha256'];kind=r.get('kind')
        if ended:raise ValueError('SHADOW_AFTER_END')
        if i==0:
            header=r.get('data')
            if (kind!='HEADER' or not isinstance(header,dict) or header.get('source')!='IB_TOOL_GATEWAY'
                or header.get('execution_domain')!='PAPER' or header.get('instrument')!='EUR.USD'
                or header.get('write_operations_allowed')!=[] or header.get('broker_orders_sent')!=0
                or not isinstance(header.get('period_sha256'),str) or len(header['period_sha256'])!=64):
                raise ValueError('SHADOW_SOURCE_SCOPE')
        elif kind=='QUOTE':
            q=r['data'];bid,ask=valid_quote(
                q,at,header['instrument'],
                legacy_historical_expiry=(header.get('collector_sha256') ==
                    LEGACY_INCLUSIVE_EXPIRY_COLLECTOR_SHA256));t=q['observed_at_ms']
            current={'t':t,'bid':q['bid'],'ask':q['ask'],'mid':float((bid+ask)/2),
                     'known_at_ms':at,'receipt':r['sha256']}
            old=quotes.get(t)
            if old and (Decimal(str(old['bid']))!=bid or Decimal(str(old['ask']))!=ask):
                raise ValueError('CONFLICTING_SOURCE_QUOTE')
            quotes.setdefault(t,current)
        elif kind=='END':ended=True
        elif kind!='FAILURE':raise ValueError('SHADOW_EVENT_KIND')
    if header is None:raise ValueError('SHADOW_HEADER_REQUIRED')
    return header,[quotes[t] for t in sorted(quotes)],{'path':str(path),'bytes':len(complete),
        'sha256':hashlib.sha256(complete).hexdigest(),'chain_head':previous,
        'uncommitted_tail_bytes':len(raw)-len(complete),'ended':ended}


def collect(root,seconds):
    if not 10<=seconds<=2400:raise ValueError('CAPTURE_DURATION_BOUND')
    h=Path.home();root=Path(root);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.is_symlink() or root.stat().st_mode&0o077:raise ValueError('PRIVATE_OUTPUT_REQUIRED')
    path=root/'quotes.jsonl';cli=h/'.local/bin/heptatrader';period=h/'.config/heptatrader/pnl-period.json'
    period_raw=period.read_bytes();p=strict(period_raw)
    if p.get('execution_domain')!='PAPER':raise ValueError('PAPER_PERIOD_REQUIRED')
    period_sha=hashlib.sha256(period_raw).hexdigest()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    seq=0;previous='0'*64;stop=False;counts={}
    def append(kind,data):
        nonlocal seq,previous
        body={'schema':SCHEMA,'sequence':seq,'previous':previous,'at_ms':int(time.time()*1000),'kind':kind,'data':data}
        checksum=digest(body);raw=canonical({**body,'sha256':checksum})+b'\n';off=0
        while off<len(raw):
            written=os.write(fd,raw[off:])
            if written<=0:raise OSError('SOURCE_SHORT_WRITE')
            off+=written
        os.fsync(fd);previous=checksum;seq+=1;counts[kind]=counts.get(kind,0)+1
    def finish(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
    append('HEADER',{'source':'IB_TOOL_GATEWAY','instrument':'EUR.USD','execution_domain':'PAPER',
        'period_sha256':period_sha,'write_operations_allowed':[],'broker_orders_sent':0,
        'collector_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    deadline=time.monotonic()+seconds
    try:
        while not stop and time.monotonic()<deadline:
            begin=time.monotonic()
            if hashlib.sha256(period.read_bytes()).hexdigest()!=period_sha:
                append('FAILURE',{'reason':'ACCOUNTING_PERIOD_CHANGED'});break
            if os.fstat(fd).st_size>MAX_BYTES-65536:
                append('FAILURE',{'reason':'SOURCE_STORAGE_BOUND'});break
            try:
                p=subprocess.run([str(cli),'native','--io-timeout-ms','4000','--call-id','laya-shadow-read-'+uuid.uuid4().hex,
                    'call','market.get_quote','instrument=EUR.USD'],capture_output=True,text=True,timeout=8)
                q=parse_native_quote_reply(p.returncode,p.stdout,
                                            int(time.time()*1000),'EUR.USD')
                append('QUOTE',{k:q[k] for k in FIELDS if k in q})
            except Exception as exc:
                reason=str(exc)
                if len(reason)>100 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for c in reason):reason=type(exc).__name__
                append('FAILURE',{'reason':reason,'quote_timestamp_rewritten':False})
            time.sleep(max(.1,1-(time.monotonic()-begin)))
        append('END',{'broker_orders_sent':0})
    finally:os.close(fd)
    report={'path':str(path),'events':seq,'counts':counts,'chain_head':previous,'broker_orders_sent':0}
    with (root/'collector-receipt.json').open('x') as f:f.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--seconds',type=int,default=1200)
    args=parser.parse_args();os.umask(0o077);collect(args.output,args.seconds)
if __name__=='__main__':main()
