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
from .feed_health import CaptureHealth, QuoteProbeBackoff, atomic_status, UPDATE_SECONDS

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
        or not (received<min(t+5000,expiry) or
                (legacy_historical_expiry and received==min(t+5000,expiry)))):
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

def _committed_size(fd,size):
    """Locate the snapshot's final LF without retaining an unfinished tail."""
    end=size
    while end:
        start=max(0,end-(1 if end==size else 65536))
        os.lseek(fd,start,os.SEEK_SET)
        chunk=os.read(fd,end-start)
        if len(chunk)!=end-start:raise ValueError('SOURCE_SHORT_READ')
        newline=chunk.rfind(b'\n')
        if newline>=0:return start+newline+1
        end=start
    return 0


def _source_chunks(fd,length,checksum):
    remaining=length
    while remaining:
        chunk=os.read(fd,min(remaining,65536))
        if not chunk:raise ValueError('SOURCE_SHORT_READ')
        checksum.update(chunk);remaining-=len(chunk)
        yield chunk


def _source_lines(chunks):
    """Bound scratch bytes while preserving bytes.splitlines() CR/LF rules."""
    pending=b'';skip_lf=False
    for chunk in chunks:
        if skip_lf and chunk.startswith(b'\n'):chunk=chunk[1:]
        skip_lf=False
        if b'\r' not in chunk:
            # Ordinary writer records use LF. split() already removes that
            # terminator, avoiding a second copy and suffix checks per row.
            lines=chunk.split(b'\n');lines[0]=pending+lines[0];pending=lines.pop()
            for line in lines:
                if len(line)>65536:raise ValueError('SHADOW_RECORD_BOUND')
                yield line
            if len(pending)>65536:raise ValueError('SHADOW_RECORD_BOUND')
            continue
        for part in chunk.splitlines(keepends=True):
            ended=part.endswith((b'\r',b'\n'))
            body=part[:-2] if part.endswith(b'\r\n') else part[:-1] if ended else part
            if len(pending)+len(body)>65536:raise ValueError('SHADOW_RECORD_BOUND')
            pending+=body
            if ended:
                yield pending
                pending=b''
        skip_lf=chunk.endswith(b'\r')
    if pending:raise ValueError('SOURCE_UNCOMMITTED_PREFIX')


def _decode_source(lines,now):
    previous='0'*64;header=None;quotes={};last=0;ended=False
    for i,line in enumerate(lines):
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
    return header,[quotes[t] for t in sorted(quotes)],previous,ended


def read_source(path,*,expected_prefix=None):
    """Hash and decode one bounded snapshot; a sealed pin needs no prior read.

    No parsed bytes or SHA receipt survive this call. Sealed reads check the
    original path/size/END policy and bind the parsed bytes to the archive SHA,
    then reject replacement or modification during the read. Open tails retain
    the existing snapshot-at-open and uncommitted-last-line behavior.
    """
    path=Path(path)
    if expected_prefix is not None:
        from .integrity import ContractError
        from .source_archive import _open_prefix,prefix_version
        if type(expected_prefix) is not dict or expected_prefix.get('path')!=str(path):
            raise ContractError('SOURCE_PREFIX_IDENTITY')
        fd,st=_open_prefix(expected_prefix,sealed=True)
    else:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        st=None
    try:
        if st is None:st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size>MAX_BYTES or st.st_uid!=os.geteuid() or st.st_mode&0o077:
            raise ValueError('SHADOW_SOURCE_FILE_BOUND')
        complete_size=_committed_size(fd,st.st_size)
        os.lseek(fd,0,os.SEEK_SET)
        checksum=hashlib.sha256();chunks=_source_chunks(fd,complete_size,checksum)
        error=None
        try:
            decoded=_decode_source(_source_lines(chunks),int(time.time()*1000))
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            if expected_prefix is None:raise
            error=exc
            # Preserve archive integrity rejection before a parse error. The
            # current chunk is already hashed; drain only the unread suffix.
            for _ in chunks:pass
        if expected_prefix is not None:
            if complete_size!=st.st_size or checksum.hexdigest()!=expected_prefix['sha256']:
                raise ContractError('SOURCE_PREFIX_HASH_CHANGED')
            version=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,
                              s.st_mode,s.st_uid,s.st_gid,s.st_nlink)
            if version(os.fstat(fd))!=version(st) or prefix_version(expected_prefix,sealed=True)!=version(st):
                raise ContractError('SOURCE_PREFIX_FILE_CHANGED')
        if error is not None:raise error
        header,quotes,previous,ended=decoded
        prefix={'path':str(path),'bytes':complete_size,'sha256':checksum.hexdigest(),
                'chain_head':previous,'uncommitted_tail_bytes':st.st_size-complete_size,'ended':ended}
        if expected_prefix is not None and prefix!=expected_prefix:
            raise ContractError('SOURCE_ARCHIVE_PREFIX_CHANGED')
        return header,quotes,prefix
    finally:os.close(fd)


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
    health=CaptureHealth(path,period_sha,int(time.time()*1000));last_health_write=0.0
    backoff=QuoteProbeBackoff();probe_started=0.0
    def health_snapshot(at_ms,completed=False):
        return {**health.snapshot(at_ms,completed=completed),"probe_schedule":backoff.snapshot()}
    def write_progress(clock,completed=False):
        nonlocal last_health_write
        atomic_status(root/'collector-status.json',health_snapshot(int(time.time()*1000),completed))
        last_health_write=clock
    def append(kind,data):
        nonlocal seq,previous,last_health_write
        body={'schema':SCHEMA,'sequence':seq,'previous':previous,'at_ms':int(time.time()*1000),'kind':kind,'data':data}
        checksum=digest(body);raw=canonical({**body,'sha256':checksum})+b'\n';off=0
        while off<len(raw):
            written=os.write(fd,raw[off:])
            if written<=0:raise OSError('SOURCE_SHORT_WRITE')
            off+=written
        os.fsync(fd);previous=checksum;seq+=1;counts[kind]=counts.get(kind,0)+1
        health.observe(kind,data,body['at_ms'])
        clock=time.monotonic()
        backoff.observe(kind,data,body['at_ms'],clock-probe_started)
        if kind in ('HEADER','END') or clock-last_health_write>=UPDATE_SECONDS:
            write_progress(clock,completed=kind=='END')
    def finish(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
    append('HEADER',{'source':'IB_TOOL_GATEWAY','instrument':'EUR.USD','execution_domain':'PAPER',
        'period_sha256':period_sha,'write_operations_allowed':[],'broker_orders_sent':0,
        'collector_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    deadline=time.monotonic()+seconds;next_probe=0.0
    try:
        while not stop:
            clock=time.monotonic()
            if clock>=deadline:break
            if clock-last_health_write>=UPDATE_SECONDS:
                write_progress(clock)
            if clock<next_probe:
                # Bounded sleeps keep SIGTERM responsive and publish liveness
                # without appending fake observations or extending quote TTL.
                time.sleep(min(1.0,deadline-clock,next_probe-clock))
                continue
            probe_started=clock
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
            next_probe=time.monotonic()+backoff.delay_seconds
        append('END',{'broker_orders_sent':0})
    finally:os.close(fd)
    report={'path':str(path),'events':seq,'counts':counts,'chain_head':previous,'broker_orders_sent':0,
            'data_health':health_snapshot(int(time.time()*1000),completed=True)}
    with (root/'collector-receipt.json').open('x') as f:f.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--seconds',type=int,default=1200)
    args=parser.parse_args();os.umask(0o077);collect(args.output,args.seconds)
if __name__=='__main__':main()
