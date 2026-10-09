"""Finite, fail-closed original-bar → canonical CPU → signed ROCm research bridge.

Two pre-existing hosts, no broker query, no trading permission or training.
Registration is permitted only after the exact Laya29 terminal rejection.
Never rescue, shift, or replay an expired parent slot.
"""
from __future__ import annotations
import argparse,fcntl,json,os,shlex,subprocess,sys,time
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once,atomic

FIRST_ANCHOR=1791558600
PARENT_ID='da06786816f632ba53c6bd2de822f786408135cef93195a02c7c863e9aa4de16'
LAYA_ID='09f9bb36bd85483dd54d1efc82fa6ea87a7e68a047595086a53a9f87ad095d0c'
LAYA_END_MS=1791553309913
FILES=('plan.json','9101.json','9102.json','9103.json','input.json')
MAX_CPU_SECOND=225
CPU_SOURCE='INDEPENDENT_FROZEN_CPU_QIANQI'


def future_anchors(parent,now_ms,*,require_registration=False):
    if (parent.get('campaign_id')!=PARENT_ID or len(parent.get('anchors',[]))!=24
        or len(set(parent['anchors']))!=24 or parent.get('trade_authority') is not False
        or parent.get('automatic_promotion') is not False):
        raise EvidenceError('ORIGINAL_PARENT_CAMPAIGN_REQUIRED')
    anchors=[a for a in parent['anchors'] if a*1000>LAYA_END_MS]
    if len(anchors)!=18 or anchors[0]!=FIRST_ANCHOR:
        raise EvidenceError('ORIGINAL_EIGHTEEN_FUTURE_SLOTS_CHANGED')
    if require_registration and not LAYA_END_MS<now_ms<anchors[0]*1000-60000:
        raise EvidenceError('AFTER_LAYA_BEFORE_FIRST_SLOT_ONLY')
    return anchors


def terminal_laya(config,now_ms):
    path=Path(config['laya29_plan_path'])
    p=read(path)
    if (p.get('plan_id')!=LAYA_ID or p.get('outcome_end_ms')!=LAYA_END_MS
        or p.get('candidate_id')!=config['laya29_candidate_id']
        or sha(path)!=config['laya29_plan_sha256']
        or now_ms<=LAYA_END_MS):
        raise EvidenceError('LAYA29_FROZEN_TERMINAL_NOT_REACHED')
    rule=p['rule']
    outcomes=[read(path.parent/('outcome-%03d.json'%i)) for i in range(rule['slots'])]
    if any(x.get('plan_id')!=LAYA_ID or x.get('slot')!=i
           or x.get('status') not in ('OBSERVED','CENSORED') for i,x in enumerate(outcomes)):
        raise EvidenceError('LAYA29_OUTCOME_NOT_FINAL')
    observed=sum(x['status']=='OBSERVED' for x in outcomes)
    censored=sum(x['status']=='CENSORED' for x in outcomes)
    if observed+censored!=rule['slots'] or censored<=rule['max_censored'] or observed>=rule['min_observed']:
        raise EvidenceError('LAYA29_REJECTION_NOT_ESTABLISHED')
    q=read(path.parent/'qualification.json')
    if (q.get('plan_id')!=LAYA_ID or q.get('candidate_id')!=p['candidate_id']
        or q.get('state')!='REJECTED'
        or q.get('observed')!=observed or q.get('censored')!=censored
        or q.get('promotion_performed') is not False):
        raise EvidenceError('LAYA29_TERMINAL_REJECTION_NOT_VERIFIED')
    return sha(path.parent/'qualification.json'),observed,censored


def validate_public_cpu_key(config):
    from .neuron256_cpu_custody import public_key
    import hashlib
    pub=public_key(config['cpu_public_key_hex'])
    if config.get('cpu_backend')!='2.11.0+cpu':raise EvidenceError('CANONICAL_BACKEND_CHANGED')
    for path,expected in ((config['cpu_emitter_path'],config['cpu_emitter_sha256']),
                          (config['candidate_template'],config['candidate_template_sha256'])):
        if sha(path)!=expected:raise EvidenceError('STAGED_CANONICAL_INPUT_CHANGED')
    return pub


def register(config):
    from .neuron256_prospective import validate_candidate,validate_plan,BASELINES,FAMILY
    from laya_cell.financial_forward_witness import sign_record,verify_record
    root=Path(config['root']);now=int(time.time()*1000)
    if (root/'plan.json').exists() or (root/'registration.json').exists():
        if not (root/'plan.json').exists() or not (root/'registration.json').exists():
            raise EvidenceError('INCOMPLETE_REGISTRATION_NO_REPLAY')
        from .neuron256_prospective import validate_runtime
        plan,_=validate_runtime(config)
        return {'state':'ALREADY_REGISTERED','plan_id':plan['plan_id']}
    qualification_sha,obs,cen=terminal_laya(config,now)
    parent_file=Path(config['parent_root'])/'plan.json'
    parent=read(parent_file)
    anchors=future_anchors(parent,now,require_registration=True)
    if sha(parent_file)!=config['parent_plan_sha256']:
        raise EvidenceError('ORIGINAL_CAMPAIGN_PLAN_PIN_CHANGED')
    validate_public_cpu_key(config)
    cpu_pins=config.get('cpu_source_inventory',{})
    if (not isinstance(cpu_pins,dict) or len(cpu_pins)<10
        or cpu_pins.get('research/historical_training/neuron256_cpu_custody.py')!=config['cpu_emitter_sha256']):
        raise EvidenceError('CPU_SOURCE_INVENTORY_PREDECLARED_REQUIRED')
    candidate=validate_candidate(read(config['candidate_template']))
    if (candidate['rho']!=.25 or candidate['checkpoint_path']!=config['checkpoint_path']
        or candidate['pinned_files'].get(config['checkpoint_path'])!=sha(config['checkpoint_path'])):
        raise EvidenceError('SINGLE_FROZEN_DEVELOPMENT_CANDIDATE_CHANGED')
    if config['period_sha256']!=parent['period_sha256'] or sha(config['period_path'])!=config['period_sha256']:
        raise EvidenceError('PERIOD_IDENTITY_CHANGED')
    source=Path(config['source_root'])
    pins={relative:sha(source/relative) for relative in config['runtime_relpaths']}
    if not config['runtime_relpaths'] or 'research/historical_training/neuron256_cpu_custody.py' not in pins:
        raise EvidenceError('RESEARCH_RUNTIME_PIN_INCOMPLETE')
    p={'schema':'heptatrader.neuron256-seven-bin-future-plan.v1','family':FAMILY,
       'trial':1,'alpha':.025,'baselines':list(BASELINES),
       'anchors':anchors,'registered_at_ms':now,'laya29_end_ms':LAYA_END_MS,
       'laya29_plan_sha256':config['laya29_plan_sha256'],'laya29_qualification_sha256':qualification_sha,
       'parent_plan_sha256':config['parent_plan_sha256'],'parent_campaign_id':PARENT_ID,
       'candidate_id':candidate['candidate_id'],'candidate_sha256':sha(config['candidate_template']),
       'period_sha256':config['period_sha256'],
       'min_observed':16,'max_censored':2,'min_days':4,'min_mean_brier_gain':.001,
       'cpu_public_key_hex':config['cpu_public_key_hex'],'cpu_backend':'2.11.0+cpu',
       'cpu_emitter_sha256':config['cpu_emitter_sha256'],
       'cpu_source_inventory':cpu_pins,
       'runtime_sha256':pins,'trade_authority':False,'automatic_selection':False,
       'replay_missed_windows':False,'independent_real_future_qualification':False,
       'parent_broker_queries_added':0}
    p['plan_id']=digest(p);validate_plan(p)
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    write_once(root/'candidate.json',candidate)
    write_once(root/'plan.json',p)
    seal=sign_record(config,'registrar',{'plan_id':p['plan_id'],
         'plan_sha256':sha(root/'plan.json'),
         'candidate_sha256':sha(root/'candidate.json'),
         'laya29_qualification_sha256':qualification_sha,
         'original_campaign_id':PARENT_ID,
         'broker_orders_sent':0,'automatic_selection':False})
    write_once(root/'registration.json',seal)
    verify_record(config,'registrar',read(root/'registration.json'))
    return {'state':'REGISTERED_AFTER_LAYA29','plan_id':p['plan_id'],
            'registered_at_ms':now,'first_anchor':anchors[0],'slots':len(anchors),
            'laya29_rejected':True,'laya29_observed':obs,'laya29_censored':cen,
            'broker_orders_sent':0,'trade_authority':False}


def _checked_path(path,base):
    p=Path(path).resolve()
    if not p.is_relative_to(Path(base).resolve()):raise EvidenceError('TRANSPORT_PATH_ESCAPE')
    if any(ch in str(p) for ch in ('\n','\r','\x00')):raise EvidenceError('TRANSPORT_PATH_CHAR')
    return p


def _remote(config,path):
    if config.get('remote_host')!='pocket4':raise EvidenceError('ORIGINAL_HOST_REQUIRED')
    root=_checked_path(path,config['remote_allow_root'])
    return config['remote_host']+':'+str(root)


def _transfer(config,remote,local,*,push=False,optional=False):
    local=Path(local)
    local.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    part=local.with_name('.'+local.name+'.incoming')
    if part.exists():part.unlink()
    a=['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=yes']
    a+=([str(local),_remote(config,remote)] if push else [_remote(config,remote),str(part)])
    p=subprocess.run(a,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,timeout=10,check=False)
    if p.returncode:
        if part.exists():part.unlink()
        if optional:return False
        raise EvidenceError('TRUSTED_SSH_TRANSFER_FAILED')
    if not push:
        if local.exists():
            if sha(local)!=sha(part):raise EvidenceError('IMMUTABLE_TRANSPORT_SOURCE_CHANGED')
            part.unlink()
        else:
            os.replace(part,local)
            local.chmod(0o400)
    return True


def _remote_mkdir(config,path):
    p=_checked_path(path,config['remote_allow_root'])
    cmd=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=yes',
         config['remote_host'],'mkdir -p -m 700 -- '+shlex.quote(str(p))]
    subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL,timeout=10,check=True)


def active_anchor(config,now_ms):
    anchors=config.get('anchors')
    if (not isinstance(anchors,list) or len(anchors)!=18 or anchors[0]!=FIRST_ANCHOR
        or any(type(x)is not int for x in anchors) or anchors!=sorted(set(anchors))):
        raise EvidenceError('FIXED_FUTURE_CALENDAR_NOT_PINNED')
    hits=[x for x in anchors if x*1000<=now_ms<(x+239)*1000]
    if len(hits)>1:raise EvidenceError('AMBIGUOUS_FIXED_INPUT_WINDOW')
    return hits[0] if hits else None


def cpu_once(config,anchor):
    """Run from qian-qi. Pull immutable original bytes; never call broker."""
    from .neuron256_prospective import validate_plan,validate_candidate,original_input
    from laya_cell.financial_forward_witness import verify_record
    root=Path(config['local_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    remote_root=Path(config['remote_root'])
    local_study=root/'study'
    for name in ('plan.json','candidate.json','registration.json'):
        if not _transfer(config,remote_root/name,local_study/name,optional=True):return 'WAIT_REGISTRATION'
    plan=read(local_study/'plan.json');validate_plan(plan)
    candidate=read(local_study/'candidate.json')
    # Authenticated receipt must bind the FULL signed plan & candidate.
    seal=read(local_study/'registration.json')
    verify_record(config,'registrar',seal)
    if (seal.get('plan_id')!=plan['plan_id'] or seal.get('plan_sha256')!=sha(local_study/'plan.json')
        or seal.get('candidate_sha256')!=sha(local_study/'candidate.json')
        or plan['cpu_public_key_hex']!=config['cpu_public_key_hex']
        or plan['cpu_emitter_sha256']!=sha(Path(config['source_root'])/'research/historical_training/neuron256_cpu_custody.py')
        or plan['candidate_sha256']!=sha(local_study/'candidate.json')):
        raise EvidenceError('CPU_STUDY_SIGNED_SOURCE_MISMATCH')
    if anchor not in plan['anchors']:raise EvidenceError('CPU_FOREIGN_ANCHOR')
    for rel,digest_sha in plan['cpu_source_inventory'].items():
        if sha(Path(config['source_root'])/rel)!=digest_sha:
            raise EvidenceError('CPU_IMPORTED_RUNTIME_CHANGED')
    now=int(time.time()*1000)
    if not anchor*1000<=now<(anchor+MAX_CPU_SECOND)*1000:return 'OUTSIDE_FIXED_CPU_WINDOW'
    parent=root/'parent'
    if not _transfer(config,Path(config['remote_parent_root'])/'plan.json',
                     parent/'plan.json',optional=True):return 'WAIT_ORIGINAL_PARENT'
    if sha(parent/'plan.json')!=plan['parent_plan_sha256']:raise EvidenceError('PARENT_STUDY_PIN_CHANGED')
    remote_proof=Path(config['remote_parent_root'])/'slots'/str(anchor)/'proof'
    local_proof=parent/'slots'/str(anchor)/'proof'
    for name in FILES:
        if not _transfer(config,remote_proof/name,local_proof/name,optional=True):
            return 'WAIT_ORIGINAL_RAW_INPUT'
    row,source_pins=original_input(parent,read(parent/'plan.json'),anchor,now)
    if sha(local_proof/'input.json')!=source_pins[str(local_proof/'input.json')]:
        raise EvidenceError('CPU_ORIGINAL_INPUT_RACE')
    out=root/'feature'/str(anchor);receipt=out/'receipt.json'
    claim=root/'claims'/(str(anchor)+'.json')
    if not receipt.exists():
        if claim.exists():return 'FAILED_INTERRUPTED_CPU_NO_REPLAY'
        write_once(claim,{'plan_id':plan['plan_id'],'anchor':anchor,
                          'started_at_ms':int(time.time()*1000),'retry_encoder':False})
        cfg={'plan_path':str(local_study/'plan.json'),
             'candidate_path':str(local_study/'candidate.json'),
             'anchor':anchor,'source_input_file':str(local_proof/'input.json'),
             'output_directory':str(out),'source_root':config['source_root'],
             'base_directory':config['base_directory'],'key_path':config['key_path']}
        cfg_path=root/'claims'/(str(anchor)+'.config.json')
        write_once(cfg_path,cfg)
        env=os.environ.copy()
        env.update({'PYTHONPATH':str(Path(config['source_root'])/'research')+':'+str(Path(config['source_root'])/'research/neuron_learning')+':'+str(Path(config['source_root'])/'scripts'),
                    'PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1',
                    'TRANSFORMERS_OFFLINE':'1','USE_TF':'0',
                    'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'2'})
        p=subprocess.run([config['cpu_python'],'-B','-m','historical_training.neuron256_cpu_custody',
                           '--config',str(cfg_path)],
                         env=env,stdin=subprocess.DEVNULL,capture_output=True,timeout=45)
        if p.returncode:
            write_once(root/'claims'/(str(anchor)+'.failed.json'),
                       {'status':'FAILED_ENCODER_NO_REPLAY','returncode':p.returncode,
                        'stderr_tail':p.stderr.decode(errors='replace')[-512:]})
            return 'FAILED_ENCODER_NO_REPLAY'
    from .neuron256_cpu_custody import verify_feature
    vector,_=verify_feature(out,plan,candidate,row,anchor,int(time.time()*1000),
                           source_input_file=local_proof/'input.json')
    dest=remote_root/'canonical-cpu'/str(anchor)
    _remote_mkdir(config,dest)
    # Publish immutable tensor FIRST, receipt LAST. A gap remains unqualified.
    for name in ('raw.safetensors','receipt.json'):
        if not _transfer(config,dest/name,out/name,push=True,optional=True):
            return 'HOLD_TRANSPORT_NO_REENCODE'
    receipt_record={'status':'CANONICAL_CPU_SIGNED_DELIVERED','plan_id':plan['plan_id'],
            'anchor':anchor,'cpu_raw_sha256':sha(out/'raw.safetensors'),
            'cpu_receipt_sha256':sha(receipt),'delivered_at_ms':int(time.time()*1000),
            'new_broker_queries':0,'new_training_steps':0}
    write_once(root/'deliveries'/(str(anchor)+'.json'),receipt_record)
    return 'CANONICAL_CPU_SIGNED_DELIVERED'


def cpu_window(config,anchor):
    start=time.monotonic()
    if int(time.time())>anchor+MAX_CPU_SECOND:return 'MISSED_NO_REPLAY'
    root=Path(config['local_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(root/(str(anchor)+'.lock'),os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:os.close(fd);return 'OTHER_ORIGINAL_CPU_WINDOW_ACTIVE'
    try:
        state='WAIT_ORIGINAL_RAW_INPUT'
        while time.time()<anchor+MAX_CPU_SECOND:
            try:state=cpu_once(config,anchor)
            except (OSError,EvidenceError,subprocess.SubprocessError) as exc:
                state='HOLD_ORIGINAL_CPU_BOUNDARY:'+type(exc).__name__+':'+str(exc)[:120]
                if ('CHANGED' in str(exc) or 'MISMATCH' in str(exc)
                    or 'FOREIGN' in str(exc) or 'SIGNATURE' in str(exc)):
                    break
            atomic(root/'status.json',{'at_ms':int(time.time()*1000),'anchor':anchor,
                                      'state':state,'broker_orders_sent':0})
            if state in ('CANONICAL_CPU_SIGNED_DELIVERED','FAILED_INTERRUPTED_CPU_NO_REPLAY',
                         'FAILED_ENCODER_NO_REPLAY'):break
            time.sleep(min(6,max(0.01,anchor+MAX_CPU_SECOND-time.time())))
        return state
    finally:os.close(fd)


def gpu_window(config,anchor):
    from .neuron256_prospective import validate_runtime,predict_one,verify_prediction
    plan,candidate=validate_runtime(config)
    if anchor not in plan['anchors']:raise EvidenceError('GPU_FOREIGN_ANCHOR')
    root=Path(config['root']);folder=root/'slots'/str(anchor)
    if int(time.time())>anchor+239:return 'MISSED_NO_REPLAY'
    while time.time()<anchor+239:
        if (folder/'commit.json').exists():
            verify_prediction(config,plan,anchor);return 'SIGNED_BEFORE_ORIGINAL_TARGET'
        if (folder/'claim.json').exists():return 'GPU_INTERRUPTED_AFTER_CLAIM_NO_REPLAY'
        try:
            predict_one(config,plan,candidate,anchor)
        except EvidenceError as exc:
            if ('INVALID_OR_OVERSIZED_FILE' not in str(exc)
                and 'FUTURE_INPUT_RAW_TIMING_OR_COMPLETENESS' not in str(exc)
                and 'CONTIGUOUS_12_BAR_INPUT_REQUIRED' not in str(exc)):
                atomic(root/'predictor-blocker.json',{'anchor':anchor,'at_ms':int(time.time()*1000),
                         'reason':str(exc)[:220],'trading_authority':False})
                return 'PREDICTION_REJECTED_NO_FALLBACK'
        time.sleep(min(4,max(0.01,anchor+239-time.time())))
    atomic(root/'predictor-status.json',{'at_ms':int(time.time()*1000),'anchor':anchor,
          'state':'MISSED_FIXED_SIGNING_WINDOW_NO_REPLAY','broker_orders_sent':0})
    return 'MISSED_FIXED_SIGNING_WINDOW_NO_REPLAY'


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=('register','cpu-window','gpu-window','evaluate','status'),required=True)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--anchor',type=int)
    a=p.parse_args();os.umask(0o077)
    config=read(a.config)
    if a.mode=='register':result=register(config)
    elif a.mode=='cpu-window':
        anchor=a.anchor if a.anchor is not None else active_anchor(config,int(time.time()*1000))
        result={'state':cpu_window(config,anchor) if anchor is not None else 'SKIP_NO_FIXED_FUTURE_INPUT','anchor':anchor}
    elif a.mode=='gpu-window':
        anchor=a.anchor if a.anchor is not None else active_anchor(config,int(time.time()*1000))
        result={'state':gpu_window(config,anchor) if anchor is not None else 'SKIP_NO_FIXED_FUTURE_INPUT','anchor':anchor}
    elif a.mode=='evaluate':
        from .neuron256_prospective import validate_runtime,evaluate
        plan,_=validate_runtime(config);result=evaluate(config,plan)
    else:
        root=Path(config['root'])
        result=read(root/'status.json') if (root/'status.json').exists() else {'state':'NOT_REGISTERED'}
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__=='__main__':main()
