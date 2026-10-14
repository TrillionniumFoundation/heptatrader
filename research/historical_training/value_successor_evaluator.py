"""Explicitly versioned post-hoc Value scoring continuation.

Does NOT rewrite the original frozen evaluator, registered plan, signatures,
original assessment or prediction files. It invokes original frozen scoring
with a narrowly substituted, checked stream ledger prefix reader and signs
new assessments only inside a distinct successor root. Local evaluator
signature is NOT canonical Agentd authorization or model promotion.
"""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,stat,time
from pathlib import Path
from .value_ledger_stream import stream_execution_index
from laya_cell.integrity import ContractError,read_json,sha_file
from laya_cell.financial_forward_witness import verify_trust,verify_record
from laya_cell import financial_forward_evaluate as ff
from laya_cell.resident_store import write_once,atomic

SCHEMA='heptatrader.value-evaluator-successor.v1'
def allowed_output(path):
    root=Path.home()/'.local/state/heptatrader/neuron-learning'
    path=Path(path)
    if path.is_symlink() or not path.is_absolute() or path.parent != root:
        raise ValueError('SUCCESSOR_ROOT_MUST_BE_NEW_CHILD_OF_NEURON_LEARNING')
    if not path.name.startswith('value-evaluator-successor-'):
        raise ValueError('EXPLICIT_SUCCESSOR_ID_REQUIRED')
    return path

def ensure_successor(c,target,expected_original_plan_sha):
    original=Path(c['forward_root'])
    original_plan=list(original.glob('plans/*/plan.json'))
    if len(original_plan)!=1:raise ContractError('ORIGINAL_SINGLE_PLAN_REQUIRED')
    source=original_plan[0];plan=read_json(source)
    if sha_file(source)!=expected_original_plan_sha:
        raise ContractError('ORIGINAL_PLAN_HASH_CHANGED')
    # Verify the frozen release and pre-input registrar signature unmodified.
    ff.validate_registration(c,source,plan)
    identity=plan['plan_id']
    successor=target/'plans'/identity
    if not target.exists():
        target.mkdir(mode=0o700)
        successor.mkdir(parents=True,mode=0o700)
        shutil.copyfile(source,successor/'plan.json')
        shutil.copyfile(source.parent/'registration.json',successor/'registration.json')
        (successor/'predictions').symlink_to(source.parent/'predictions',target_is_directory=True)
    if sha_file(successor/'plan.json')!=sha_file(source):
        raise ContractError('SUCCESSOR_PLAN_MISMATCH')
    if sha_file(successor/'registration.json')!=sha_file(source.parent/'registration.json'):
        raise ContractError('SUCCESSOR_REGISTRATION_MISMATCH')
    link=successor/'predictions'
    if not link.is_symlink() or os.readlink(link)!=str(source.parent/'predictions'):
        raise ContractError('SUCCESSOR_PREDICTION_LINK_CHANGED')
    # The original predictions remain authoritative, append-only, and are
    # validated individually by the frozen read_prediction() verifier.
    return plan,source

def run(config_path,output_root,expected_original_plan_sha):
    os.umask(0o077)
    c=read_json(Path(config_path))
    if (c.get('schema')!='heptatrader.current100k-forward-worker-config.v1'
        or c.get('signing_role')!='evaluator'
        or c.get('automatic_trading') is not False
        or c.get('actor_update') is not False):
        raise ContractError('READ_ONLY_EVALUATOR_ROLE_REQUIRED')
    target=allowed_output(output_root)
    verify_trust(c)
    if sha_file(Path(c['period_path']))!=c['period_sha256']:
        raise ContractError('FINANCIAL_EVAL_PERIOD_CHANGED')
    original_assessment=list(Path(c['forward_root']).glob('plans/*/assessment.json'))
    before={str(p):sha_file(p) for p in original_assessment}
    plan,source=ensure_successor(c,target,expected_original_plan_sha)
    cc={**c,'forward_root':str(target)}
    previous=ff.execution_index
    try:
        # Sole behavioral repair: strict original pinned-ledger-prefix decode,
        # without reading the entire growing log or rewriting the plan.
        ff.execution_index=stream_execution_index
        result=ff.Evaluator(cc).step()
    finally:
        ff.execution_index=previous
    asses=target/'plans'/plan['plan_id']/'assessment.json'
    signed_path=asses.with_name('assessment-signed.json')
    value=read_json(asses);signed=read_json(signed_path)
    verify_record(c,'evaluator',signed)
    if {k:v for k,v in signed.items() if k not in ('role_witness','signature')} != value:
        raise ContractError('SUCCESSOR_SIGNED_ASSESSMENT_NOT_IDENTICAL')
    if not value.get('current_ledger_prefix') or value.get('financial_candidate_selected') is not False:
        raise ContractError('SUCCESSOR_LEDGER_OR_PROMOTION_SCOPE')
    if {str(p):sha_file(p) for p in original_assessment}!=before:
        raise ContractError('ORIGINAL_ASSESSMENT_MUTATED')
    successor_receipt={
        'schema':SCHEMA,'at_ms':int(time.time()*1000),
        'original_plan_id':plan['plan_id'],
        'original_plan_sha256':expected_original_plan_sha,
        'frozen_evaluator_sha256':sha_file(Path(ff.__file__)),
        'successor_runner_sha256':sha_file(Path(__file__)),
        'stream_reader_sha256':sha_file(Path(__file__).parent/'value_ledger_stream.py'),
        'original_source_root':c['forward_root'],
        'successor_result_root':str(target),
        'result_sha256':sha_file(asses),
        'signed_assessment_sha256':sha_file(signed_path),
        'evaluator_role_witness_verified':True,
        'original_assessment_files_unchanged':True,
        'historical_replay_not_original_precommitted_runtime':True,
        'canonical_agentd_accepted':False,
        'automatic_selection':False,'broker_orders_sent':0,
        'state':value['state'],'observed_trades':value['observed_trades'],
        'total_original_trades':value['total_original_trades'],
        'unresolved_or_censored':value['unresolved_or_censored']
    }
    receipt_root=target/'successor-receipts'
    receipt_root.mkdir(mode=0o700,exist_ok=True)
    from .common import digest
    identity=digest(successor_receipt)
    write_once(receipt_root/(identity+'.json'),successor_receipt)
    atomic(target/'successor-status.json',successor_receipt)
    return successor_receipt

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',required=True)
    p.add_argument('--output-root',required=True)
    p.add_argument('--original-plan-sha256',required=True)
    a=p.parse_args()
    receipt=run(a.config,a.output_root,a.original_plan_sha256)
    print(json.dumps({k:receipt[k] for k in (
        'state','observed_trades','total_original_trades','unresolved_or_censored',
        'evaluator_role_witness_verified','canonical_agentd_accepted','broker_orders_sent')}))
if __name__=='__main__':main()
