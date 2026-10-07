"""Register an independent future window AFTER a candidate has been frozen.

This operation does not change training data, a previous window, model tensors,
a runtime selection, a trading service, or an earlier censored outcome.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import time
from .integrity import ContractError,read_json,sha_file,digest,canonical
from .train import verify_old_learner_inactive,verify_baseline

def write_once(path,value):
    with path.open('xb') as stream:
        import os
        stream.write(canonical(value)+b'\n');stream.flush();os.fsync(stream.fileno())
    path.chmod(0o400)

def register(training_config,shadow_directory,config_output):
    config=read_json(training_config);verify_old_learner_inactive()
    directory=Path(config['output_directory']);candidate=read_json(directory/'candidate.json')
    if candidate.get('candidate_id')!=digest({k:v for k,v in candidate.items() if k!='candidate_id'}):
        raise ContractError('CANDIDATE_MANIFEST_HASH')
    if candidate.get('status')!='REQUIRES_INDEPENDENT_FUTURE_SHADOW' or candidate.get('automatic_cutover') is not False:
        raise ContractError('CANDIDATE_ROLE')
    if sha_file(directory/'adapter.safetensors')!=candidate['adapter_sha256']:
        raise ContractError('CANDIDATE_TENSOR_HASH')
    bootstrap=Path(config['baseline_directory']);frozen_id=verify_baseline(bootstrap)
    baseline=read_json(bootstrap/'model.json');now=int(time.time()*1000)
    if not candidate['created_at_ms']<now:raise ContractError('CANDIDATE_NOT_FROZEN_BEFORE_REGISTRATION')
    from .shadow_source import read_source
    header,_,prefix=read_source(shadow_directory/'quotes.jsonl')
    if prefix['ended']:raise ContractError('SOURCE_ALREADY_ENDED')
    m={'schema':'heptatrader.laya-independent-validation.v1','registered_at_ms':now,
       'candidate_id':candidate['candidate_id'],'candidate_manifest_sha256':sha_file(directory/'candidate.json'),
       'base_id':candidate['base_id'],'bootstrap_model_id':baseline['model_id'],
       'bootstrap_frozen_manifest_id':frozen_id,'horizon_sec':300,
       'validation_start_ms':now+5000,'validation_end_ms':now+900000,
       'source_file':str(shadow_directory/'quotes.jsonl'),'period_sha256':header['period_sha256'],
       'evaluator_source_sha256':sha_file(Path(__file__).parent/'forward_shadow.py'),
       'source_reader_sha256':sha_file(Path(__file__).parent/'shadow_source.py'),
       'input_policy':'FIRST_READY_PROBE_IN_WINDOW_NO_FUTURE_OUTCOME_SELECTION',
       'sample_count_planned':1,'forecast_horizon_seconds':300,'endpoint_tolerance_ms':5000,
       'max_path_gap_ms':30000,'automatic_selection':False,'refit_allowed':False,
       'prior_job_window_eligible':candidate.get('job_window_eligible',False),
       'previous_window_modified':False,'broker_orders_sent':0}
    m['manifest_sha256']=digest(m)
    path=shadow_directory/'validation-plan.json';write_once(path,m)
    forward={**config,'candidate_directory':str(directory),'shadow_directory':str(shadow_directory),
             'validation_plan':str(path),'validation_plan_sha256':sha_file(path)}
    write_once(config_output,forward)
    return m

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--training-config',type=Path,required=True)
    parser.add_argument('--shadow-directory',type=Path,required=True);parser.add_argument('--output-config',type=Path,required=True)
    a=parser.parse_args();import json;print(json.dumps(register(a.training_config,a.shadow_directory,a.output_config),indent=2))
if __name__=='__main__':main()
