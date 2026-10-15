"""Research-only priority reservation for ORIGINAL Value trial3 opportunities.

Stops NEW background heavyweight trainer/evaluator admissions, never kills
running processes or takes/replaces the original flock. Protected Value worker
still independently authenticates requests, acquires the same heavy-worker.lock
and enforces all original 60-second and signing/market-source checks.
"""
from __future__ import annotations
import hashlib,json,os,re,stat,time
from pathlib import Path
from .common import EvidenceError

EXPECTED_INTERVAL_MS=120000
VALUE_DEADLINE_MS=60000
# Original frozen Laya evaluator.reap() allows 240s, then may spend 5s
# waiting for worker shutdown and up to 2s on its next supervisor step.
# Since the ORIGINAL Value producer fires ~every 120s, an evaluator launched
# >60s before the next slot can still own the shared lock when it arrives.
# Reserve its full *nonpreemptive* budget with fixed shutdown margin, not just
# the Value prediction's own 60s deadline. Existing work is NEVER interrupted.
EVALUATION_BUDGET_MS=250000
MAX_SOURCE_STALE_MS=240000
TRAINING_BUDGET_MS=620000  # original 600s wait plus 15s termination margin
MAX_FILE_BYTES=64*1024*1024
READ_TAIL_BYTES=128*1024
MIN_CADENCE_INTERVALS=4
_ID=re.compile(r'^[0-9a-f]{32}$')
_FILE=re.compile(r'^[0-9a-f]{32}-[0-9]{4}\.jsonl$')
POLICY_SCHEMA='heptatrader.original-value-priority.v1'

def verify_policy(config):
    if not isinstance(config,dict) or set(config)!={'schema','plan_path','plan_sha256'}:
        raise EvidenceError('VALUE_PRIORITY_POLICY_SCOPE')
    if config['schema']!=POLICY_SCHEMA:
        raise EvidenceError('VALUE_PRIORITY_POLICY_SCHEMA')
    p=config['plan_path']
    if not isinstance(p,str) or not Path(p).is_absolute() or '..' in Path(p).parts:
        raise EvidenceError('VALUE_PRIORITY_PLAN_PATH')
    h=config['plan_sha256']
    if not isinstance(h,str) or not re.fullmatch(r'[0-9a-f]{64}',h):
        raise EvidenceError('VALUE_PRIORITY_PLAN_PIN')
    return config

def _private_file(path,limit,expect=None,tail=False):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid()
                or st.st_mode&0o077 or st.st_size>limit):
            raise EvidenceError('VALUE_PRIORITY_FILE_SCOPE')
        if tail:
            size=min(st.st_size,READ_TAIL_BYTES)
            os.lseek(fd,st.st_size-size,os.SEEK_SET)
            raw=os.read(fd,size)
            if st.st_size>size:raw=raw.partition(b'\n')[2]
            if not raw.endswith(b'\n'):raw=raw.rpartition(b'\n')[0]+b'\n'
        else:
            raw=os.read(fd,st.st_size)
        if expect is not None and hashlib.sha256(raw).hexdigest()!=expect:
            raise EvidenceError('VALUE_PRIORITY_PIN_CHANGED')
        return raw
    finally:os.close(fd)

def _load_pinned(config):
    p=Path(config['plan_path'])
    plan=json.loads(_private_file(p,512*1024,expect=config['plan_sha256']))
    if (plan.get('schema')!='heptatrader.current100k-value-forward-plan.v1'
         or plan.get('input_end_ms',0)<=plan.get('input_start_ms',0)
         or not isinstance(plan.get('worker_runtime_inventory_digest'),str)
         or plan.get('automatic_effect_authority') is not False):
        raise EvidenceError('VALUE_PRIORITY_INVALID_PLAN')
    producer=plan['producer_registration']
    path=Path(producer['manifest'])
    manifest=json.loads(_private_file(path,512*1024,expect=producer['sha256']))
    if (manifest.get('schema')!='heptatrader.attribution-producer.v1'
        or manifest.get('instrument')!='EUR.USD'
        or type(manifest.get('trading_behavior_changed')) is not bool
        or manifest.get('period_sha256')!=plan['period_sha256']):
        raise EvidenceError('VALUE_PRIORITY_MANIFEST_SCOPE')
    folder=Path(manifest['journal_directory'])
    st=folder.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid()
            or st.st_mode&0o077):
        raise EvidenceError('VALUE_PRIORITY_JOURNAL_DIR_SCOPE')
    return plan,folder

def _recent_requests(folder):
    files=[]
    for file in folder.iterdir():
        if not _FILE.fullmatch(file.name):continue
        st=file.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:
            raise EvidenceError('VALUE_PRIORITY_JOURNAL_FILE_SCOPE')
        if st.st_size>MAX_FILE_BYTES:raise EvidenceError('VALUE_PRIORITY_JOURNAL_BOUND')
        files.append((st.st_mtime_ns,file))
    if not files:return []
    samples={}
    for _,file in sorted(files,reverse=True)[:3]:
        for line in _private_file(file,MAX_FILE_BYTES,tail=True).splitlines():
            try:e=json.loads(line)
            except (ValueError,UnicodeError):continue
            if e.get('kind')!='REQUEST' or not isinstance(e.get('data'),dict):continue
            d=e['data'];rid=d.get('request_id')
            started=d.get('started_at_ms');written=e.get('written_at_ms')
            position=d.get('position_at_request')
            if (not isinstance(rid,str) or not _ID.fullmatch(rid)
                or type(started) is not int or type(written) is not int
                or not 0<=written-started<=10000
                or type(position) not in (int,float)
                or not -10**12<=position<=10**12):
                continue
            old=samples.get(started)
            if old is not None and old!=(rid,position==0):
                raise EvidenceError('VALUE_PRIORITY_DUPLICATE_SLOT')
            samples[started]=(rid,position==0)
    return sorted((t,flat) for t,(_,flat) in samples.items())

def decision(config,role,now_ms):
    """Return (deny, reason, redacted evidence) for background role only."""
    verify_policy(config)
    if role not in ('trainer','evaluator') or type(now_ms) is not int:
        raise EvidenceError('VALUE_PRIORITY_ROLE_OR_TIME')
    plan,folder=_load_pinned(config)
    if not plan['input_start_ms']<=now_ms<plan['input_end_ms']:
        return False,'VALUE_TRIAL_OUTSIDE_WINDOW',{'source':'PINNED_ORIGINAL'}
    readings=_recent_requests(folder)
    if not readings:
        return False,'VALUE_PRODUCER_NO_RECENT_REQUEST',{'source':'PINNED_ORIGINAL'}
    t,flat=readings[-1]
    age=now_ms-t
    if age<0:
        raise EvidenceError('VALUE_PRIORITY_SOURCE_FROM_FUTURE')
    if age>MAX_SOURCE_STALE_MS:
        return False,'VALUE_PRODUCER_STALE_NO_RESERVATION',{'source':'PINNED_ORIGINAL','age_ms':age}
    trail=readings[-(MIN_CADENCE_INTERVALS+1):]
    regular=(len(trail)==MIN_CADENCE_INTERVALS+1 and
         all(abs(b[0]-a[0]-EXPECTED_INTERVAL_MS)<=2000
             for a,b in zip(trail,trail[1:])))
    next_slot=t+EXPECTED_INTERVAL_MS
    proof={'source':'PINNED_ORIGINAL','plan_id':plan['plan_id'],
           'latest_request_ms':t,'latest_was_flat':flat,
           'last_observed_age_ms':age,'verified_periodic_intervals':len(trail)-1 if regular else 0,
           'next_expected_from_historical_cadence_ms':next_slot if regular else None,
           'next_original_request_is_only_estimated_not_evidence':True,
           'background_max_nonpreemptible_ms':
               TRAINING_BUDGET_MS if role=='trainer' else EVALUATION_BUDGET_MS,
           'original_shared_flock_unmodified':True,
           'no_future_request_forged':True,'broker_orders_sent':0}
    next_in_trial=regular and next_slot<plan['input_end_ms']
    if role=='trainer' and next_in_trial and 0<=next_slot-now_ms<=TRAINING_BUDGET_MS:
        return True,'VALUE_PRIORITY_DEFER_NEW_LONG_TRAINING',proof
    if role=='evaluator':
        if flat and age<=VALUE_DEADLINE_MS:
            return True,'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST',proof
        if next_in_trial and 0<=next_slot-now_ms<=EVALUATION_BUDGET_MS:
            return True,'VALUE_PRIORITY_DEFER_NEW_LONG_EVALUATION',proof
    return False,'VALUE_PRIORITY_NO_ACTIVE_RESERVATION',proof
