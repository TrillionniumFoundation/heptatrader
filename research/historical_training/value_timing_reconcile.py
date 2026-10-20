"""Verify captured public role signatures and explain pre-open timing.

Read only the supplied capture and public trust. No private-key, OMS, broker,
model or live-source access. Timing boundaries are local witnesses, not IB time.
"""
from __future__ import annotations
import argparse, base64, collections, hashlib, json, math, os, time
from pathlib import Path
from .value_export_reconcile import canonical, digest, read, require


def verify_signature(record, role, trust, trust_digest, now_ms):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    w=record.get('role_witness',{})
    require(w.get('role')==role and w.get('public_key')==trust['roles'][role] and w.get('scope')==trust['scope'] and w.get('trust_digest')==trust_digest and w.get('canonical_agentd_admission') is False,'SIGNATURE_ROLE_SCOPE')
    require(type(w.get('signed_at_ms')) is int and 0<w['signed_at_ms']<=now_ms,'SIGNATURE_TIME')
    sig=base64.b64decode(record['signature'],validate=True)
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(trust['roles'][role])).verify(sig,canonical({k:v for k,v in record.items() if k!='signature'}))


def quantiles(values):
    if not values:return {'count':0}
    a=sorted(values)
    def q(p):
        x=(len(a)-1)*p;i=int(x);return a[i]+(a[min(i+1,len(a)-1)]-a[i])*(x-i)
    return {'count':len(a),'min':a[0],'p50':q(.5),'p90':q(.9),'p95':q(.95),'p99':q(.99),'max':a[-1]}


def open_timing_summary(assessment,items,timings):
    """Timing coverage of the exact signed OPEN cohort, separate from PnL."""
    by_command={row['command_id']:row for row in timings}
    no_commit=collections.Counter();late_reasons=collections.Counter()
    phases={'preopen':collections.defaultdict(list),'late':collections.defaultdict(list)}
    phase_commits=0;missing_binding=0
    for item in items:
        outcome=item['outcome'];row=by_command.get(outcome['command_id'])
        missing_binding+=outcome.get('request_id') is None
        if row is None:
            no_commit[outcome.get('reason') or 'NO_VERIFIED_COMMIT']+=1
            continue
        group='preopen' if row['signed_before_open'] else 'late'
        if group=='late':late_reasons[outcome.get('reason') or 'SIGNED_AT_OR_AFTER_ORIGINAL_OPEN']+=1
        front=item['prediction'].get('front_timing')
        if front is None:continue
        require(isinstance(front,dict) and front.get('schema')=='heptatrader.value-prediction-front-timing.v1','FRONT_TIMING_SCHEMA')
        flat={}
        for scope,field in [('worker','worker_phases_ms'),('prediction','prediction_phases_ms')]:
            values=front.get(field,{})
            require(isinstance(values,dict),'FRONT_TIMING_PHASE_SHAPE')
            for name,value in values.items():
                require(isinstance(name,str) and type(value) in (int,float) and math.isfinite(value) and value>=0,'FRONT_TIMING_DURATION')
                key=scope+'.'+name;flat[key]=value;phases[group][key].append(value)
        entered=front.get('prediction_entered_at_ms')
        if entered is not None:
            require(type(entered) is int and item['prediction']['request_started_at_ms']<=entered<=item['prediction']['computed_at_ms'],'FRONT_TIMING_ENTRY_CLOCK')
            row['request_to_prediction_entry_ms']=entered-item['prediction']['request_started_at_ms']
        started=front.get('worker_started_at_ms')
        if started is not None:
            require(type(started) is int and 0<started<=item['prediction']['computed_at_ms'],'FRONT_TIMING_WORKER_CLOCK')
            row['request_to_worker_start_ms']=started-item['prediction']['request_started_at_ms']
        row['front_phases_ms']=flat;phase_commits+=1
    total=assessment['total_original_trades'];timely=sum(row['signed_before_open'] for row in timings)
    return {'open_coverage':{
        'denominator_scope':'SIGNED_ASSESSMENT_ORIGINAL_OPEN_COHORT',
        'total_original_opens':total,'signed_before_original_open':timely,
        'signed_at_or_after_original_open':len(timings)-timely,'no_verified_commit':total-len(timings),
        'preopen_commit_coverage':timely/total if total else None,
        'signed_commit_coverage':len(timings)/total if total else None,
        'request_binding_missing':missing_binding,'observed_settled_trades':assessment['observed_trades'],
        'timing_only_not_statistical_qualification':True},
        'not_preopen_reason_counts':{'no_verified_commit':dict(no_commit),'late_signed_commit':dict(late_reasons)},
        'front_timing_commits':phase_commits,
        'phase_latency_ms':{group:{name:quantiles(values) for name,values in sorted(rows.items())} for group,rows in phases.items()}}


def reconcile(capture, labels=None):
    trust=capture['trust'];pins=capture['public_pins'];now=capture['captured_at_ms']
    require(digest(trust)==pins['witness_trust_digest'] and trust['scope']==pins['period_sha256'] and trust.get('canonical_agentd_issuer') is False,'TRUST_SCOPE')
    require(set(trust['roles'])=={'registrar','predictor','evaluator','selector'} and len(set(trust['roles'].values()))==4,'DISTINCT_ROLE_KEYS')
    signed=capture['assessment_signed'];verify_signature(signed,'evaluator',trust,pins['witness_trust_digest'],now)
    assessment={k:v for k,v in signed.items() if k not in ('signature','role_witness')}
    require(assessment==capture['assessment'],'ASSESSMENT_CONTENT')
    items=capture['items']
    has_order='outcome_record_ids' in assessment
    ordered_ids=assessment.get('outcome_record_ids')
    if has_order:
        require(type(ordered_ids) is list and len(ordered_ids)==len(items)
                and all(type(x) is str and len(x)==64 for x in ordered_ids)
                and len(set(ordered_ids))==len(ordered_ids),'SIGNED_OUTCOME_RECORD_ORDER')
        by_record={item['outcome']['record_id']:item for item in items}
        require(len(by_record)==len(items) and set(by_record)==set(ordered_ids),
                'SIGNED_OUTCOME_RECORD_MEMBERSHIP')
        items=[by_record[record_id] for record_id in ordered_ids]
    semantic=[];timings=[];counts=collections.Counter();seen=set();linked=0
    labelmap={} if labels is None else {r['open_command_id']:r for r in labels['rows']}
    for item in items:
        o=item['outcome'];cid=o['command_id'];require(cid not in seen,'DUPLICATE_COMMAND');seen.add(cid)
        require(o['record_id']==digest({k:v for k,v in o.items() if k!='record_id'}),'OUTCOME_HASH')
        result={k:v for k,v in o.items() if k not in ('source_ledger_prefix','semantic_id','record_id')}
        body={k:v for k,v in result.items() if k!='observed_at_ms'}
        require(digest(body)==o['semantic_id'],'OUTCOME_SEMANTIC_HASH');semantic.append(body)
        counts[o['status']]+=1
        require(o['status'] in ('OBSERVED','PENDING','CENSORED'),'OUTCOME_STATE')
        require(o['plan_id']==assessment['plan_id'] and o['candidate_id']==assessment['candidate_id'],'PLAN_BINDING')
        commit=item.get('commit');pred=item.get('prediction')
        if commit is None:
            require(pred is None and o['status']!='OBSERVED' and 'prediction_sha256' not in o,'MISSING_COMMIT')
            continue
        verify_signature(commit,'predictor',trust,pins['witness_trust_digest'],now)
        require(item['prediction_file_sha256']==commit['prediction_sha256']==o['prediction_sha256'] and item['commit_file_sha256']==o['commit_sha256'],'PREDICTION_BYTES_BINDING')
        # File byte SHA was bound at capture; reproduce serialization identity in capture separately.
        require(hashlib.sha256(base64.b64decode(item['prediction_raw_b64'],validate=True)).hexdigest()==item['prediction_file_sha256'] and json.loads(base64.b64decode(item['prediction_raw_b64']))==pred,'PREDICTION_CAPTURE_BYTES')
        require(hashlib.sha256(base64.b64decode(item['commit_raw_b64'],validate=True)).hexdigest()==item['commit_file_sha256'] and json.loads(base64.b64decode(item['commit_raw_b64']))==commit,'COMMIT_CAPTURE_BYTES')
        require(commit['request_id']==pred['request_id']==o['request_id'] and commit['candidate_id']==pred['candidate_id']==o['candidate_id'] and commit['plan_id']==pred['plan_id']==o['plan_id'],'PREDICTION_IDENTITY')
        requested=pred['request_started_at_ms'];written=pred['request_source_written_at_ms'];computed=pred['computed_at_ms'];committed=commit['committed_at_ms'];sealed=commit['role_witness']['signed_at_ms'];opened=o['opened_at_ms']
        require(all(type(t) is int and t>0 for t in [requested,written,computed,committed,sealed,opened]) and requested<=written<=computed<=committed<=sealed and requested<=opened,'TIMELINE_ORDER')
        infer=pred['inference_seconds']*1000
        require(math.isfinite(infer) and 0<=infer<=computed-requested+5,'INFERENCE_DURATION')
        row={'command_id':cid,'request_id':o['request_id'],'status':o['status'],'reason':o.get('reason'),'request_to_open_ms':opened-requested,'request_to_seal_ms':sealed-requested,'seal_before_open_margin_ms':opened-sealed,'inference_ms':infer,'unallocated_pre_inference_ms':computed-requested-infer,'compute_to_signed_commit_ms':sealed-computed,'signed_before_open':sealed<opened}
        if o['status']=='OBSERVED':
            require(sealed<opened and o['prediction_committed_at_ms']==sealed,'OBSERVED_MUST_BE_PREOPEN')
            if cid in labelmap:
                label=labelmap[cid]
                require(label['reward_id']==o['reward_id'] and label['request_id']==o['request_id'] and label['input_id']==o['input_id'],'LABEL_IDENTITY')
                require(abs(float(label['net_usd'])-o['actual_net_usd'])<=1e-8 and abs(float(label['actual_commissions_usd'])-o['actual_commission_usd'])<=1e-8,'LABEL_AMOUNT')
                linked+=1
        if o.get('reason')=='NOT_COMMITTED_BEFORE_ORIGINAL_OPEN':require(sealed>=opened,'LATE_REASON_INCONSISTENT')
        timings.append(row)
    require(digest(semantic)==assessment['outcome_set_digest'],'SIGNED_OUTCOME_SET_DIGEST')
    require(counts['OBSERVED']==assessment['observed_trades'] and len(seen)==assessment['total_original_trades'],'SIGNED_COUNTS')
    groups={}
    for name,subset in [('preopen',[x for x in timings if x['signed_before_open']]),('late',[x for x in timings if not x['signed_before_open']])]:
        groups[name]={k:quantiles([x[k] for x in subset]) for k in ('request_to_open_ms','request_to_seal_ms','inference_ms','unallocated_pre_inference_ms','seal_before_open_margin_ms')}
        groups[name]['overhead_alone_exceeds_open_window']=sum(x['unallocated_pre_inference_ms']>=x['request_to_open_ms'] for x in subset)
    return {**open_timing_summary(assessment,capture['items'],timings),'schema':'heptatrader.signed-value-timing-reconcile.v1','captured_at_ms':now,'status_counts':dict(counts),'commands':len(seen),'predictor_signatures_verified':len(timings),'evaluator_signatures_verified':1,'signed_outcome_set_verified':True,'signed_outcome_record_order_verified':has_order,'observed_joined_to_exported_labels':linked,'groups':groups,'timings':timings,'raw_oms_replayed':False,'broker_signature_claim':False,'new_predictions_or_training':False,'old_outcomes_changed':False,'trade_authority':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('--capture',required=True);p.add_argument('--labels',required=True);p.add_argument('--expected-capture-sha256',required=True);p.add_argument('--expected-labels-sha256',required=True);p.add_argument('--output',required=True);a=p.parse_args();os.umask(0o077)
    capture,sha=read(a.capture);labels,lsha=read(a.labels)
    require(sha==a.expected_capture_sha256 and lsha==a.expected_labels_sha256,'EXTERNAL_CAPTURE_PIN')
    from .value_export_reconcile import reconcile as reconcile_labels
    label_checks=reconcile_labels(labels)
    r=reconcile(capture,labels);r['capture_sha256']=sha;r['labels_sha256']=lsha
    r['embedded_labels_rechecked']=label_checks['rows_checked'];r['verifier_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with Path(a.output).open('x') as f:json.dump(r,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps({k:v for k,v in r.items() if k!='timings'},sort_keys=True))
if __name__=='__main__':main()
