"""An explicit additional evaluation of one unchanged completed candidate.

No training job, model update, old-plan rewrite or automatic retry queue is
created. The guarded registrar retains the ordinary scheduler lock and rules.
"""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from types import SimpleNamespace

from .integrity import ContractError, digest, read_json, sha_file
from .resident_store import get_meta

SCHEMA='heptatrader.resident-forward-attempt-request.v1'
PROFILE='CLOSED_REJECTED_SAME_CANDIDATE_FORWARD_V1'
RUNTIME_PIN='learn/laya_cell/resident_forward_attempt.py'
FIELDS={'schema','profile','attempt_id','job_id','job_file_sha256','candidate_id',
    'candidate_manifest_sha256','previous_plan_path','previous_plan_id','previous_plan_sha256',
    'previous_trial','not_before_ms','period_sha256'}


def _need(value,reason):
    if not value:raise ContractError(reason)


def _sha(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def validate_shape(request):
    _need(isinstance(request,dict) and set(request)==FIELDS,'FORWARD_ATTEMPT_REQUEST_FIELDS')
    _need(request['schema']==SCHEMA and request['profile']==PROFILE,'FORWARD_ATTEMPT_PROFILE')
    for key in ('attempt_id','job_file_sha256','candidate_id','candidate_manifest_sha256',
                'previous_plan_id','previous_plan_sha256','period_sha256'):
        _need(_sha(request[key]),'FORWARD_ATTEMPT_DIGEST:'+key)
    job=request['job_id']
    _need(isinstance(job,str) and 0<len(job)<=128 and all(c.isalnum() or c in '-_' for c in job),
          'FORWARD_ATTEMPT_JOB_ID')
    _need(isinstance(request['previous_plan_path'],str) and Path(request['previous_plan_path']).is_absolute(),
          'FORWARD_ATTEMPT_PREVIOUS_PATH')
    _need(type(request['previous_trial']) is int and request['previous_trial']>0
          and type(request['not_before_ms']) is int and request['not_before_ms']>0,
          'FORWARD_ATTEMPT_CLOCK_OR_TRIAL')
    _need(request['attempt_id']==digest({k:v for k,v in request.items() if k!='attempt_id'}),
          'FORWARD_ATTEMPT_REQUEST_ID')
    return request


def check_request(service,request):
    """Bind original bytes and the completed job, without changing that job."""
    validate_shape(request)
    _need(_sha(service.c.get('runtime_sha256',{}).get(RUNTIME_PIN)),
          'FORWARD_ATTEMPT_RUNTIME_NOT_PINNED')
    _need(request['period_sha256']==service.c['period_sha256'],'FORWARD_ATTEMPT_PERIOD')
    row=service.db.execute("SELECT * FROM jobs WHERE id=? AND status='COMPLETED'",
                           (request['job_id'],)).fetchone()
    _need(row is not None and row['candidate_id']==request['candidate_id'],
          'FORWARD_ATTEMPT_COMPLETED_JOB')
    job=json.loads(row['payload'])
    job_path=Path(service.c['control_directory'])/'jobs'/(request['job_id']+'.json')
    _need(sha_file(job_path)==request['job_file_sha256'] and read_json(job_path)==job,
          'FORWARD_ATTEMPT_ORIGINAL_JOB_CHANGED')
    directory=Path(job['output_directory']);manifest=directory/'candidate.json'
    candidate=read_json(manifest)
    _need(candidate.get('candidate_id')==request['candidate_id']
          and candidate['candidate_id']==digest({k:v for k,v in candidate.items() if k!='candidate_id'})
          and sha_file(manifest)==request['candidate_manifest_sha256']
          and sha_file(directory/'adapter.safetensors')==candidate['adapter_sha256'],
          'FORWARD_ATTEMPT_ORIGINAL_CANDIDATE_CHANGED')
    path=Path(request['previous_plan_path']);root=Path(service.c['evaluation_directory'])
    _need(path.parent.parent==root and path.name=='plan.json' and path.resolve()==path,
          'FORWARD_ATTEMPT_PREVIOUS_PATH_SCOPE')
    _need(sha_file(path)==request['previous_plan_sha256'],'FORWARD_ATTEMPT_PREVIOUS_BYTES_CHANGED')
    previous=read_json(path)
    _need(previous.get('plan_id')==request['previous_plan_id']
          and previous['plan_id']==digest({k:v for k,v in previous.items() if k!='plan_id'})
          and previous.get('job_id')==request['job_id'] and previous.get('candidate_id')==request['candidate_id']
          and previous.get('candidate_manifest_sha256')==request['candidate_manifest_sha256']
          and previous.get('scope')==request['period_sha256']
          and previous.get('trial')==request['previous_trial']
          and previous.get('outcome_end_ms')==request['not_before_ms'],
          'FORWARD_ATTEMPT_PREVIOUS_PLAN_BINDING')
    from .resident_plan_admission import registration_key,registration_folder,_check_record,SCHEMA as REGISTRATION_SCHEMA
    prior_attempt=previous.get('evaluation_attempt')
    _need(path==registration_folder(service.c,request['job_id'],prior_attempt)/'plan.json',
          'FORWARD_ATTEMPT_PREVIOUS_REGISTRATION_PATH')
    prior_record=get_meta(service.db,registration_key(request['job_id'],prior_attempt))
    identity={'schema':REGISTRATION_SCHEMA,'job_id':request['job_id'],
              'candidate_id':request['candidate_id'],'candidate_manifest_sha256':request['candidate_manifest_sha256']}
    if prior_attempt is not None:identity['evaluation_attempt']=validate_shape(prior_attempt)
    _check_record(prior_record,identity)
    _need(prior_record['state']=='PUBLISHED' and prior_record.get('plan')==previous
          and get_meta(service.db,'evaluation_trial',0)>=previous['trial'],
          'FORWARD_ATTEMPT_PREVIOUS_NOT_PUBLISHED')
    return candidate,job,previous


def readiness(service,request,now_ms,*,job):
    """An early or not-yet-closed request spends no trial or alpha."""
    if now_ms<request['not_before_ms']:
        return {'ready':False,'reason':'PREVIOUS_FORWARD_WINDOW_NOT_COMPLETE',
                'next_attempt_at_ms':request['not_before_ms']}
    path=Path(request['previous_plan_path']).parent/'qualification.json'
    if not path.exists():
        return {'ready':False,'reason':'PREVIOUS_FORWARD_QUALIFICATION_NOT_PUBLISHED'}
    prior=read_json(path)
    _need(prior.get('plan_id')==request['previous_plan_id']
          and prior.get('candidate_id')==request['candidate_id']
          and prior.get('state')=='REJECTED','FORWARD_ATTEMPT_PREVIOUS_NOT_CLOSED_REJECTED')
    from .promotion import rule_for_trial,protocol_for_job
    previous=read_json(Path(request['previous_plan_path']))
    _need(previous['rule']==rule_for_trial(previous['trial'],protocol_for_job(service.c,job)),
          'FORWARD_ATTEMPT_STATISTICAL_RULE_CHANGED')
    from .resident_plan_admission import KEY_PREFIX,registration_key
    key=registration_key(request['job_id'],request)
    for row in service.db.execute('SELECT key,value FROM meta WHERE key LIKE ?',(KEY_PREFIX+'%',)):
        record=json.loads(row['value'])
        if (row['key']!=key and record.get('job_id')==request['job_id']
                and record.get('trial_allocated') is True
                and record.get('plan',{}).get('trial',0)>request['previous_trial']):
            raise ContractError('FORWARD_ATTEMPT_PREVIOUS_IS_NOT_LATEST')
    return {'ready':True,'previous_qualification_sha256':sha_file(path),
            'old_alpha_refunded':False,'old_observations_combined':False,'training_performed':False}


def build_request(service,previous_plan_path):
    path=Path(previous_plan_path);plan=read_json(path);job_id=plan['job_id']
    job_path=Path(service.c['control_directory'])/'jobs'/(job_id+'.json')
    request={'schema':SCHEMA,'profile':PROFILE,'job_id':job_id,'job_file_sha256':sha_file(job_path),
        'candidate_id':plan['candidate_id'],'candidate_manifest_sha256':plan['candidate_manifest_sha256'],
        'previous_plan_path':str(path),'previous_plan_id':plan['plan_id'],
        'previous_plan_sha256':sha_file(path),'previous_trial':plan['trial'],
        'not_before_ms':plan['outcome_end_ms'],'period_sha256':plan['scope']}
    request['attempt_id']=digest(request)
    check_request(service,request)
    return request


def load_request(path,expected_sha256):
    path=Path(path)
    _need(_sha(expected_sha256),'FORWARD_ATTEMPT_REQUEST_BYTES')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        _need(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and not info.st_mode&0o077
              and info.st_size<=65536,'FORWARD_ATTEMPT_REQUEST_FILE')
        raw=stream.read(65537)
    _need(len(raw)<=65536 and hashlib.sha256(raw).hexdigest()==expected_sha256,
          'FORWARD_ATTEMPT_REQUEST_BYTES')
    return validate_shape(json.loads(raw))


def run_request(service,request_path,request_sha256,*,wait_seconds=90):
    """Operator-only bounded registration, under the caller's original lock.

    The caller owns any exact manual-hold maintenance. This function never
    removes a marker, starts a trainer, or grants an exception to Gate.check.
    """
    _need(type(wait_seconds) is int and 0<=wait_seconds<=300,'FORWARD_ATTEMPT_WAIT_BOUND')
    fd=getattr(service,'_controller_lock_fd',None)
    _need(type(fd) is int and fd>=3,'FORWARD_ATTEMPT_SCHEDULER_LOCK_REQUIRED')
    actual=os.fstat(fd);expected=(Path(service.c['control_directory'])/'scheduler.lock').lstat()
    _need(stat.S_ISREG(actual.st_mode) and stat.S_ISREG(expected.st_mode)
          and actual.st_uid==os.geteuid() and not actual.st_mode&0o077
          and (actual.st_dev,actual.st_ino)==(expected.st_dev,expected.st_ino),
          'FORWARD_ATTEMPT_SCHEDULER_LOCK_IDENTITY')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    _need(getattr(service,'_guarded_forward_registration',False) is True,
          'FORWARD_ATTEMPT_ORIGINAL_GUARD_REQUIRED')
    _need(service.db.execute("SELECT count(*) FROM jobs WHERE status IN ('ADMITTED','RUNNING')").fetchone()[0]==0
          and getattr(service,'child',None) is None,'FORWARD_ATTEMPT_ACTIVE_TRAINING_JOB')
    request=load_request(request_path,request_sha256)
    deadline=time.monotonic()+wait_seconds
    while True:
        if service._forward_attempt_stopping():
            return {'state':'DEFERRED','reason':'FORWARD_ATTEMPT_CONTROLLER_STOPPING',
                    'attempt_id':request['attempt_id'],'trial_allocation_checked':False,'training_performed':False}
        candidate,job,_=check_request(service,request)
        result=service.register_evaluation(request['job_id'],candidate,job,attempt=request)
        result={**result,'attempt_id':request['attempt_id'],'training_performed':False,
                'old_plan_modified':False,'old_alpha_refunded':False,'old_observations_combined':False}
        if result['state'] in ('PUBLISHED','NO_FORWARD_TRIAL','EXISTING_IMMUTABLE_PLAN'):
            from .resident_queue import inspect
            result['queue_after_registration']=inspect(service.c)
            if result['state']=='PUBLISHED':
                # A paused trainer may never refresh status via step(). Use its
                # existing writer, without changing the DB's latest candidate.
                # Failure here leaves the published plan/debit recoverable.
                summary={'attempt_id':request['attempt_id'],'job_id':request['job_id'],
                    'candidate_id':request['candidate_id'],'plan_id':result['plan']['plan_id'],
                    'trial':result['plan']['trial'],'state':'PUBLISHED','training_performed':False}
                with redirect_stdout(io.StringIO()):
                    service.write_status(storage=getattr(service,'storage',None),
                        state='FORWARD_EVALUATION_ATTEMPT_PUBLISHED',
                        forward_attempt_registration=summary,**result['queue_after_registration'])
                result['trainer_status_updated']=True
            return result
        remaining=deadline-time.monotonic()
        next_at=result.get('next_attempt_at_ms')
        if (remaining<30 or service._forward_attempt_stopping()
                or (type(next_at) is int and next_at-int(time.time()*1000)>remaining*1000)):
            return result
        for _ in range(30):
            if service._forward_attempt_stopping():return result
            time.sleep(1)


def main():
    parser=argparse.ArgumentParser(description='Prepare one explicit request; no plan/trial is allocated.')
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--previous-plan',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077)
    from .resident import config_check
    from .resident_plan_admission import publish_immutable
    config=config_check(read_json(args.config))
    path=Path(config['control_directory'])/'scheduler.sqlite'
    connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:request=build_request(SimpleNamespace(c=config,db=connection),args.previous_plan)
    finally:connection.close()
    publish_immutable(args.output,request)
    print(json.dumps({'request':str(args.output),'sha256':sha_file(args.output),
        'attempt_id':request['attempt_id'],'not_before_ms':request['not_before_ms'],
        'trial_allocated':False,'training_performed':False},sort_keys=True))


if __name__=='__main__':main()
