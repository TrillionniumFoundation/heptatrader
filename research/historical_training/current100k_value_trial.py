"""Isolated current100k monetary readout. Not an actor or on-policy update.
Shares resident heavy-worker admission; revalidates real labels before/after fit.
The frozen pretrained backbone, original datasets and live strategies are read-only.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,os,sys,time
from decimal import Decimal
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once
from .current100k import BEHAVIOR,to_row,validate_config

SCALE_USD=100.0

def selected_rows(frozen,current):
    if (frozen.get('schema')!='heptatrader.current100k-value-dataset.v1'
        or frozen.get('behavior_profile')!=BEHAVIOR
        or frozen.get('synthetic_rewards')!=0 or frozen.get('old25k_rewards')!=0
        or frozen.get('trade_authority') is not False):
        raise EvidenceError('CURRENT100K_VALUE_SCOPE')
    if digest({k:v for k,v in frozen.items() if k!='fingerprint'})!=frozen.get('fingerprint'):
        raise EvidenceError('FROZEN_DATASET_FINGERPRINT')
    rows=frozen.get('rows',[])
    if not 1<=len(rows)<=64 or len({r['row_id'] for r in rows})!=len(rows):
        raise EvidenceError('VALUE_ROW_BOUND_OR_DUPLICATE')
    if (current.get('period_sha256')!=frozen['period_sha256']
        or current.get('producer_manifest_sha256')!=frozen['producer_manifest_sha256']):
        raise EvidenceError('CURRENT_REPLAY_SCOPE_CHANGED')
    by={r['row_id']:r for r in current['rows']}
    for r in rows:
        if by.get(r['row_id'])!=r:raise EvidenceError('REAL_REWARD_WITHDRAWN_OR_CHANGED')
        checked=to_row(r['reward'],r['source_request'],r['source_binding'],
                       frozen['producer_manifest_sha256'],frozen['period_sha256'])
        if checked!=r:raise EvidenceError('CURRENT100K_EXACT_ROW_CHANGED')
        target=Decimal(r['net_usd'])/Decimal(str(SCALE_USD))
        if not target.is_finite() or abs(target)>8:raise EvidenceError('VALUE_TARGET_BOUND_NO_CLIPPING')
    return rows

def validate_trial(c):
    if c.get('schema')!='heptatrader.current100k-value-trial-config.v1':raise EvidenceError('TRIAL_SCHEMA')
    if any(c.get(k) is not False for k in ('actor_update','automatic_selection','trade_authority')):
        raise EvidenceError('VALUE_ONLY_NO_ACTOR_OR_SELECTION')
    if c.get('max_rows')!=64 or c.get('steps')!=16 or c.get('scale_usd')!=SCALE_USD:
        raise EvidenceError('FIXED_VALUE_OBJECTIVE')
    if sha(c['dataset_path'])!=c['dataset_sha256']:raise EvidenceError('DATASET_PIN_CHANGED')
    if not c.get('source_files_sha256') or str(Path(__file__).resolve()) not in c['source_files_sha256']:
        raise EvidenceError('COMPLETE_TRIAL_SOURCE_INVENTORY_REQUIRED')
    for p,want in c['source_files_sha256'].items():
        if sha(p)!=want:raise EvidenceError('TRIAL_SOURCE_CHANGED')

def run(c):
    os.umask(0o077);validate_trial(c)
    consumer=read(c['consumer_config']);manifest=validate_config(consumer)
    frozen=read(c['dataset_path']);out=Path(c['output_root'])
    if out.exists():raise EvidenceError('TRIAL_OUTPUT_ALREADY_EXISTS_NO_REPLAY')
    from .resident_resource_guard import Gate,verify_config
    guard=read(c['guard_config']);verify_config(guard);gate=Gate(guard,'trainer')
    if not gate.acquire({'trial':'current100k-value','dataset_sha256':c['dataset_sha256']}):
        raise EvidenceError('RESIDENT_ADMISSION_DENIED_NO_MODEL_LOADED')
    started=time.monotonic()
    try:
        out.mkdir(mode=0o700,parents=True,exist_ok=False)
        write_once(out/'plan.json',c)
        from sandbox import enter
        original=read(guard['original_config'])
        restrictions=enter({'output_dir':str(out),'period_path':consumer['period_path'],
            'legacy_capture':c['dataset_path'],'oms_path':consumer['reward_config']['oms_path'],
            'export_dir':manifest['journal_directory'],'read_only_paths':[
                c['consumer_config'],consumer['runtime_root'],consumer['registration']['manifest'],
                c['base_directory'],str(Path(c['base_directory']).parent),c['source_root'],
                original['runtime_directory'],c['native_binary'],sys.prefix]})
        write_once(out/'sandbox.json',restrictions)
        from .export_current100k import export
        before=copy.deepcopy(consumer);before['output_root']=str(out/'replay-before')
        before_status=export(before);rows=selected_rows(frozen,read(before_status['dataset_path']))
        # Only after sandbox and shared admission may a GPU package/model be loaded.
        import torch
        from laya_cell.model import load_verified_laya,require_pocket4_gpu,tensor_hash
        from laya_cell.financial_value_train import features_from_laya,train_head
        device=require_pocket4_gpu();torch.set_num_threads(2);torch.manual_seed(20261009)
        torch.cuda.set_per_process_memory_fraction(min(1.,4*1024**3/torch.cuda.get_device_properties(0).total_memory))
        torch.cuda.reset_peak_memory_stats();base=Path(c['base_directory'])
        raw,tok,base_id=load_verified_laya(base,read(base/'base-manifest.json'));raw.requires_grad_(False).eval()
        original_hashes={n:tensor_hash(p) for n,p in raw.named_parameters()}
        features=[];receipts=[]
        # Serial encoding avoids the previous full-batch UMA memory spike.
        for row in rows:
            feature,receipt=features_from_laya(raw,tok,[row]);features.append(feature);receipts.append(receipt)
        x=torch.cat(features);y=torch.tensor([float(Decimal(r['net_usd'])/Decimal(100)) for r in rows],device=device,dtype=torch.float32)
        head,diagnostic=train_head(x,y)
        if any(p.grad is not None or p.requires_grad for p in raw.parameters()):raise EvidenceError('FROZEN_BACKBONE_GRADIENT')
        if original_hashes!={n:tensor_hash(p) for n,p in raw.named_parameters()}:raise EvidenceError('FROZEN_BACKBONE_CHANGED')
        after=copy.deepcopy(consumer);after['output_root']=str(out/'replay-after')
        after_status=export(after);selected_rows(frozen,read(after_status['dataset_path']))
        from safetensors.torch import save_file
        weights=out/'value-head.safetensors';save_file({n:p.detach().cpu().contiguous() for n,p in head.state_dict().items()},str(weights));weights.chmod(0o400)
        from laya_cell.native import NativePorts
        native=NativePorts(Path(c['native_binary']),c['native_binary_sha256'])
        utility=native.call({'op':'utility','dataset_digest':digest(frozen),'objective_digest':digest({'scale_usd':100,'steps':16,'scope':'BEHAVIOR_VALUE_ONLY'}),
            'outcome_digest':digest([r['reward_id'] for r in rows]),'events':[{'instant':float(Decimal(r['net_usd'])/Decimal(100))/len(rows),'discount':1.,'event_digest':r['reward_id']} for r in rows]})
        record={'schema':'heptatrader.current100k-value-candidate.v1','created_at_ms':int(time.time()*1000),
            'status':'UNVALIDATED_BEHAVIOR_VALUE_ONLY','training_scope':BEHAVIOR,'base_id':base_id,
            'dataset_sha256':c['dataset_sha256'],'rows':len(rows),'row_ids':[r['row_id'] for r in rows],
            'target_net_usd':[r['net_usd'] for r in rows],'scale_usd':SCALE_USD,'head_sha256':sha(weights),
            'diagnostic':diagnostic,'feature_receipts':receipts,'before_replay_sha256':before_status['dataset_sha256'],
            'after_replay_sha256':after_status['dataset_sha256'],'native_reward_utility':utility,
            'reward_revalidated_before_and_after':True,'pretrained_backbone_unchanged':True,
            'scorer_or_actor_updated':False,'pnl_reward_used':True,'on_policy_policy_gradient':False,
            'future_validation_observed':False,'canonical_owner_accepted':False,'automatic_selection':False,
            'broker_orders_sent':0,'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName),
            'max_gpu_allocated_bytes':torch.cuda.max_memory_allocated(),'wall_seconds':time.monotonic()-started,
            'training_fit_is_not_efficacy':True,'source_files_sha256':c['source_files_sha256']}
        record['candidate_id']=digest(record);write_once(out/'candidate.json',record)
        print(json.dumps({k:record[k] for k in ('status','candidate_id','rows','pnl_reward_used','scorer_or_actor_updated','max_gpu_allocated_bytes','wall_seconds')}),flush=True)
        return record
    finally:gate.release()

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();run(read(a.config))
if __name__=='__main__':main()
