"""Causal, time-scoped bridge snapshots from real PAPER runtime evidence.

Historical teacher guesses lacking exact input are retained but not distilled.
No missing label/fee is zero reward. This spool is not an Agentd LedgerWriter.
"""
from __future__ import annotations
from bisect import bisect_left,bisect_right
from collections import Counter
import copy
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import stat

HORIZONS=(300,900)
MAX_GAP_MS=30000
TTL_MS=5000
FEATURE_VERSION='causal-fixed-scale-features16-v1'
SAMPLE_SELECTION_PROFILE={
    'name':'causal-observed-horizon-balanced-v1','maximum_samples':512,
    'maximum_training_samples':256,'observed_samples_per_horizon':128,
    'horizons_seconds':list(HORIZONS),'maximum_fresh_samples':256,'fresh_maximum_age_ms':60000,
    'selection':'LATEST_OBSERVED_PER_HORIZON_THEN_LATEST_OBSERVED_FILL',
    'ordering':'ANCHOR_DESC_QUOTE_BEFORE_EXACT_THEN_INPUT_ID_ASC',
    'eligibility':'EXISTING_CAUSAL_PATH_AND_OBSERVED_LABEL_VALIDITY','outcome_values_ranked':False}


def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()

def strict(raw):
    def unique(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('DUPLICATE_FIELD')
            out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def prefix(path,maximum=64*1024*1024):
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or s.st_mode&0o077 or s.st_size>maximum:
            raise ValueError('SOURCE_FILE_BOUND_OR_PERMISSIONS')
        chunks=[];remaining=s.st_size
        while remaining:
            part=os.read(fd,min(remaining,1024*1024))
            if not part:raise ValueError('SOURCE_SHORT_READ')
            chunks.append(part);remaining-=len(part)
        raw=b''.join(chunks)
    finally:os.close(fd)
    end=raw.rfind(b'\n')+1
    complete=raw[:end]
    return complete,{'path':str(path),'bytes':len(complete),'sha256':hashlib.sha256(complete).hexdigest(),
                     'trailing_uncommitted_bytes':len(raw)-end}


def exports(directory,period_sha,now):
    paths=sorted(Path(directory).glob('*.jsonl'))
    if len(paths)>64:raise ValueError('EXPORT_FILE_COUNT_BOUND')
    groups={};manifest=[];excluded=[]
    for path in paths:
        raw,meta=prefix(path,9*1024*1024);manifest.append(meta)
        values=[]
        for line in raw.splitlines():
            if len(line)>65536:raise ValueError('EXPORT_RECORD_BOUND')
            row=strict(line)
            if row.get('schema')!='heptatrader.learning-export.v1':raise ValueError('EXPORT_SCHEMA')
            if row.get('sha256')!=digest({k:v for k,v in row.items() if k!='sha256'}):raise ValueError('EXPORT_HASH')
            values.append(row)
        if not values:continue
        groups.setdefault(values[0]['producer_id'],[]).extend(values)
    result=[]
    for producer,rows in groups.items():
        rows.sort(key=lambda r:r['sequence']);prev='0'*64;context=None;lost=False
        for i,row in enumerate(rows):
            if row['sequence']!=i or row['previous']!=prev or row['producer_id']!=producer:
                raise ValueError('EXPORT_CHAIN_OR_MISSING_SEGMENT')
            prev=row['sha256']
            if type(row.get('at_ms')) is not int or row['at_ms']<=0 or row['at_ms']>now:
                raise ValueError('EXPORT_CLOCK')
            if type(row.get('dropped_total')) is not int or row['dropped_total']<0:raise ValueError('EXPORT_DROP_COUNTER')
            lost|=row['dropped_total']>0
            if row['kind']=='HEADER':
                c=row['data']
                if (c.get('source_kind')!='REAL_PAPER_RUNTIME_EXPORT' or c.get('execution_domain')!='PAPER'
                    or c.get('broker_mutation_authorized') is not False):raise ValueError('REAL_PAPER_EXPORT_REQUIRED')
                if context is not None and c!=context:raise ValueError('EXPORT_SCOPE_CHANGED')
                context=c
        if context is None:raise ValueError('EXPORT_HEADER_REQUIRED')
        if context.get('period_file_sha256')!=period_sha:
            excluded.append({'producer_id':producer,'reason':'DIFFERENT_ACCOUNTING_PERIOD'});continue
        if lost:
            excluded.append({'producer_id':producer,'reason':'EXPORT_LOSS_PRESERVED_NOT_TRAINABLE'});continue
        result.extend({**row,'scope':context} for row in rows if row['kind']!='HEADER')
    return sorted(result,key=lambda r:(r['at_ms'],r['producer_id'],r['sequence'])),manifest,excluded


def legacy_quotes(path,instrument,start,now):
    if not Path(path).exists():return [],None,0
    raw,meta=prefix(path);prev='0'*64;quotes=[];old_teachers=0
    for seq,line in enumerate(raw.splitlines()):
        row=strict(line)
        if (row.get('schema')!='heptatrader.cost-capture.v1' or row.get('sequence')!=seq or
            row.get('previous')!=prev or row.get('sha256')!=digest({k:v for k,v in row.items() if k!='sha256'})):
            raise ValueError('COST_CAPTURE_CHAIN')
        prev=row['sha256']
        if seq==0 and (row.get('event')!='HEADER' or row['data'].get('source_kind')!='IB_CAPTURE'
                       or row['data'].get('instrument')!=instrument):
            raise ValueError('REAL_COST_CAPTURE_REQUIRED')
        if not start<=row['at_ms']<=now:continue
        if row['event']=='QUOTE':quotes.append({'at_ms':row['at_ms'],'data':row['data'],'receipt':row['sha256']})
        if row['event']=='CANDIDATE':old_teachers+=1
    return quotes,meta,old_teachers


def valid_quote(row,instrument):
    q=row['data'];known=row['at_ms']
    if q.get('source')!='IB' or q.get('authoritative') is not True or q.get('stale') is not False or q.get('instrument')!=instrument:
        raise ValueError('QUOTE_AUTHORITY')
    t=q.get('observed_at_ms');expiry=q.get('stale_after_ms')
    if type(t)is not int or type(expiry)is not int or t<=0 or not t<=known<=min(expiry,t+TTL_MS):raise ValueError('QUOTE_STALE')
    bid=float(q['bid']);ask=float(q['ask'])
    if not math.isfinite(bid+ask) or not 0<bid<=ask:raise ValueError('QUOTE_NUMERIC')
    return {'t':t,'bid':bid,'ask':ask,'mid':float((Decimal(str(q['bid']))+Decimal(str(q['ask'])))/2),'known_at_ms':known,
            'receipt':row.get('receipt',row.get('sha256'))}


def features(qs,known):
    if len(qs)<2:raise ValueError('FEATURE_HISTORY_SHORT')
    last=qs[-1];m=last['mid'];mids=[x['mid'] for x in qs]
    returns=[10000*(b/a-1)/100 for a,b in zip(mids,mids[1:])]
    mean=sum(returns)/len(returns)
    gaps=[b['t']-a['t'] for a,b in zip(qs,qs[1:])]
    if any(g<=0 for g in gaps):raise ValueError('FEATURE_TIME_ORDER')
    ret=[10000*(m/mids[max(0,len(mids)-1-k)]-1)/100 for k in (1,2,3,5,10,20)]
    angle=2*math.pi*(last['t']%86400000)/86400000
    values=ret+[(last['ask']-last['bid'])/m*1000,
        math.sqrt(sum((r-mean)**2 for r in returns)/len(returns)),mean,
        (max(mids)-min(mids))/m*100, (known-last['t'])/5000,
        max(gaps)/30000,sum(g>15000 for g in gaps)/len(gaps),math.sin(angle),math.cos(angle),1.0]
    if len(values)!=16 or not all(math.isfinite(x) for x in values):raise ValueError('FEATURE_INVALID')
    return [max(-8,min(8,x)) for x in values]


def sample_identity(qs,scope,kind,input_id=None):
    return {'scope':scope,'source_input_id':input_id,'anchor_ms':qs[-1]['t'],
            'input_kind':kind,'quotes':qs,'feature_version':FEATURE_VERSION}


def observed_receipt(quote,horizon,target):
    return {'source_receipt':quote['receipt'],'observed_at_ms':quote['t'],
            'available_at_ms':quote['known_at_ms'],'target_scaled':target,'horizon_sec':horizon}


def describe_sample(qs,known,scope,kind,input_id=None,teacher=None):
    if not 6<=len(qs)<=24:raise ValueError('INPUT_HISTORY_BOUND')
    if type(known) is not int or any(type(x['t']) is not int or x['t']>known or
        x.get('known_at_ms',x['t'])>known for x in qs):raise ValueError('INPUT_FROM_FUTURE')
    gaps=[b['t']-a['t'] for a,b in zip(qs,qs[1:])]
    if any(g<=0 for g in gaps):raise ValueError('FEATURE_TIME_ORDER')
    if any(not all(math.isfinite(float(x[k])) for k in ('bid','ask','mid')) or
           not 0<x['bid']<=x['ask'] or x['mid']<=0 for x in qs):raise ValueError('QUOTE_NUMERIC')
    anchor=qs[-1];identity=sample_identity(qs,scope,kind,input_id)
    return {'sample_id':digest(identity),'scope':scope,'anchor_ms':anchor['t'],'known_at_ms':known,
        'reference_mid':anchor['mid'],'input_kind':kind,'source_input_id':input_id,
        'input_snapshot':identity,'input_gap':max(gaps)>MAX_GAP_MS,'teacher':teacher,
        'targets':[None,None],'outcome_receipts':[None,None],'label_reasons':['PENDING','PENDING']}


def make_sample(qs,known,scope,kind,input_id=None,teacher=None):
    sample=describe_sample(qs,known,scope,kind,input_id,teacher)
    frame_rows=[];times=[]
    for i in range(2,len(qs)+1):
        frame_rows.append(features(qs[:i],qs[i-1].get('known_at_ms',qs[i-1]['t'])))
        times.append(qs[i-1]['t'])
    return {**sample,'native_episode':{'scope':scope,'rows':frame_rows,'times':times},
        'raw_features':features(qs,known)}


def label(sample,quotes,times,now,*,gap_prefix=None):
    anchor=sample['anchor_ms'];start=bisect_left(times,anchor)
    for j,horizon in enumerate(HORIZONS):
        sample['targets'][j]=None;sample['outcome_receipts'][j]=None
        due=anchor+horizon*1000;end=bisect_left(times,due)
        reason=None
        if sample['input_gap']:reason='INPUT_GAP'
        elif sample['known_at_ms']>anchor+60000:reason='INPUT_AVAILABLE_TOO_LATE'
        elif start>=len(times) or times[start]!=anchor or quotes[start]['mid']!=sample['reference_mid']:reason='ANCHOR_UNVERIFIED'
        elif end>=len(times) or times[end]>due+TTL_MS:
            reason='OUTCOME_NOT_MATURE' if now<=due+TTL_MS else 'OUTCOME_MISSING'
        elif quotes[end]['known_at_ms']>now:reason='OUTCOME_NOT_KNOWN'
        elif ((gap_prefix[end]!=gap_prefix[start]) if gap_prefix is not None else
              any(b-a>MAX_GAP_MS for a,b in zip(times[start:end],times[start+1:end+1]))):reason='PATH_GAP'
        if reason:
            sample['label_reasons'][j]=reason;continue
        target=10000*(quotes[end]['mid']/sample['reference_mid']-1)/100
        if abs(target)>8:
            sample['label_reasons'][j]='RETURN_OUT_OF_PROFILE';continue
        receipt=observed_receipt(quotes[end],horizon,target)
        sample['targets'][j]=target;sample['outcome_receipts'][j]=receipt;sample['label_reasons'][j]='OBSERVED'
    return sample


def freeze(config,now,*,prediction_samples=()):
    instrument=config['instrument'];start=config['period_start_ms'];scope=config['period_sha256']+':'+instrument
    records,manifests,exclusions=exports(config['export_dir'],config['period_sha256'],now)
    legacy,meta,old_teachers=legacy_quotes(config['legacy_capture'],instrument,start,now)
    if meta:manifests.append(meta)
    rawquotes=legacy+[r for r in records if r['kind']=='QUOTE']
    unique={};quote_rejections=Counter()
    for row in sorted(rawquotes,key=lambda r:r['at_ms']):
        try:q=valid_quote(row,instrument)
        except (ValueError,KeyError,TypeError):quote_rejections['UNUSABLE_SOURCE_QUOTE']+=1;continue
        if q['t']<start:continue
        old=unique.get(q['t'])
        if old and (old['bid'],old['ask'])!=(q['bid'],q['ask']):raise ValueError('CONFLICTING_SOURCE_TIMESTAMP')
        if old is None or q['known_at_ms']<old['known_at_ms']:unique[q['t']]=q
    times=sorted(unique);quotes=[unique[t] for t in times]
    gap_prefix=[0]*len(times)
    for i in range(1,len(times)):gap_prefix[i]=gap_prefix[i-1]+int(times[i]-times[i-1]>MAX_GAP_MS)
    exact_samples=[];inputs={};forecasts={};decisions=[];settlements=[]
    for r in records:
        d=r['data']
        if r['kind']=='MODEL_INPUT':
            if d.get('input_id')!=digest(d.get('input')):raise ValueError('MODEL_INPUT_DIGEST')
            inputs[d['input_id']]=d['input']
        elif r['kind']=='FORECAST':
            if d.get('candidate_id')!=digest(d.get('forecast')):raise ValueError('FORECAST_DIGEST')
            forecasts[d['input_id']]=d
        elif r['kind']=='DECISION':decisions.append(d)
        elif r['kind']=='SETTLEMENT':settlements.append(d)
    for input_id,inp in inputs.items():
        try:
            if inp.get('instrument')!=instrument or inp.get('position_neutral') is not True:
                raise ValueError('MODEL_INPUT_SCOPE')
            known=inp['request_started_at_ms'];qs=[]
            for q in inp['quotes']:
                mid=float((Decimal(str(q['bid']))+Decimal(str(q['ask'])))/2)
                qs.append({'t':q['t'],'bid':float(q['bid']),'ask':float(q['ask']),'mid':mid,
                           'known_at_ms':known,'receipt':input_id})
            if known>now:raise ValueError('FUTURE_INPUT')
            teacher=forecasts.get(input_id)
            if teacher:
                f=teacher['forecast'];available=teacher['available_at_ms']
                if (f.get('issued_at_ms')!=qs[-1]['t'] or float(f.get('reference_mid',0))!=qs[-1]['mid'] or
                    available<known or available>now or available>qs[-1]['t']+60000):
                    teacher={**teacher,'distillation_allowed':False,'reason':'LATE_OR_MISALIGNED_TEACHER'}
                else:
                    teacher={**teacher,'distillation_allowed':True}
            sample=describe_sample(qs,known,scope,'EXACT_REMOTE_INPUT',input_id,teacher)
            sample['exact_model_input']=inp
            exact_samples.append(sample)
        except (ValueError,TypeError,KeyError) as exc:
            exclusions.append({'input_id':input_id,'reason':str(exc)})
    exact_samples.sort(key=lambda s:(-s['anchor_ms'],s['source_input_id']))

    def candidates(before,after=None):
        # Skip immature time ranges without constructing features or scanning
        # their outcome paths. Quote-only inputs retain their actual causal rows.
        qi=bisect_right(times,before)-1;ei=0
        while ei<len(exact_samples) and exact_samples[ei]['anchor_ms']>before:ei+=1
        while qi>=5 or ei<len(exact_samples):
            qtime=times[qi] if qi>=5 else -1
            etime=exact_samples[ei]['anchor_ms'] if ei<len(exact_samples) else -1
            anchor=max(qtime,etime)
            if after is not None and anchor<after:break
            if qtime>=etime:
                q=quotes[qi];history=[x for x in quotes[max(0,qi-23):qi+1] if x['known_at_ms']<=q['known_at_ms']]
                qi-=1
                if len(history)<6:continue
                yield describe_sample(history,q['known_at_ms'],scope,'CAUSAL_QUOTE_WINDOW')
            else:
                yield copy.deepcopy(exact_samples[ei]);ei+=1

    selected={}
    for j,horizon in enumerate(HORIZONS):
        observed=0
        for s in candidates(now-horizon*1000):
            label(s,quotes,times,now,gap_prefix=gap_prefix)
            if s['targets'][j] is None:continue
            selected[s['sample_id']]=s;observed+=1
            if observed==128:break
    if len(selected)<256:
        for s in candidates(now-min(HORIZONS)*1000):
            if s['sample_id'] in selected:continue
            label(s,quotes,times,now,gap_prefix=gap_prefix)
            if not any(v is not None for v in s['targets']):continue
            selected[s['sample_id']]=s
            if len(selected)==256:break
    fresh=0
    for s in candidates(now,now-60000):
        selected[s['sample_id']]=label(s,quotes,times,now,gap_prefix=gap_prefix);fresh+=1
        if fresh==256:break
    samples=[]
    for s in sorted(selected.values(),key=lambda s:(s['anchor_ms'],s['sample_id'])):
        identity=s['input_snapshot'];sample=make_sample(identity['quotes'],s['known_at_ms'],scope,
            s['input_kind'],s['source_input_id'],s['teacher'])
        for key in ('targets','outcome_receipts','label_reasons','exact_model_input'):
            if key in s:sample[key]=s[key]
        samples.append(sample)
    # A prediction's original input remains evaluable after it leaves the bounded
    # training window. Only outcomes are refreshed from the verified source.
    evaluation_samples=[]
    if len(prediction_samples)>4096:raise ValueError('PREDICTION_SAMPLE_BOUND')
    for s in prediction_samples:
        snapshot=s['input_snapshot'];qs=snapshot['quotes']
        known=max(q.get('known_at_ms',q['t']) for q in qs)
        restored=describe_sample(qs,known,snapshot['scope'],snapshot['input_kind'],snapshot['source_input_id'])
        fields=('sample_id','scope','anchor_ms','known_at_ms','reference_mid','input_kind','source_input_id','input_gap')
        if s['scope']!=scope or digest(snapshot)!=s['sample_id'] or any(s[k]!=restored[k] for k in fields):
            raise ValueError('PREDICTION_INPUT_IDENTITY')
        evaluation_samples.append(label(copy.deepcopy(s),quotes,times,now,gap_prefix=gap_prefix))
    candidates=Counter(d['forecast'].get('action') for d in forecasts.values())
    reason_counts=Counter(reason for s in samples for reason in s['label_reasons'])
    return {'schema':'heptatrader.neuron-dataset.v1','source_kind':'REAL_PAPER_RUNTIME',
        'frozen_at_ms':now,'scope':scope,'instrument':instrument,'horizons':list(HORIZONS),
        'sample_selection_profile':copy.deepcopy(SAMPLE_SELECTION_PROFILE),
        'source_prefixes':manifests,'samples':samples,'prediction_samples':evaluation_samples,'exclusions':exclusions,
        'quote_rejections':dict(quote_rejections),'label_reason_counts':dict(reason_counts),
        'exact_remote_inputs':len(inputs),'teacher_actions':dict(candidates),
        'legacy_teachers_without_exact_input_not_distilled':old_teachers,
        'latest_quotes':quotes[-24:],'decisions':decisions[-512:],'settlements':settlements[-128:],
        'selected_artifact_modified':False,'effect_authority':False}
