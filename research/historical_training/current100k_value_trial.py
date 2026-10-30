"""Isolated current100k monetary readout. Not an actor or on-policy update.
Shares resident heavy-worker admission; revalidates real labels before/after fit.
The frozen pretrained backbone, original datasets and live strategies are read-only.
"""
from __future__ import annotations
import argparse,copy,gc,hashlib,json,os,sys,time,traceback
from decimal import Decimal
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once
from .current100k import BEHAVIOR,to_row,validate_config
from laya_cell.financial_value_profile import check_readout_profile

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
    check_readout_profile(c.get('readout_profile'))
    if sha(c['dataset_path'])!=c['dataset_sha256']:raise EvidenceError('DATASET_PIN_CHANGED')
    required_sources={str(Path(__file__).resolve())}
    if c.get('readout_profile') is not None:
        from laya_cell import financial_value_profile
        profile_source=Path(financial_value_profile.__file__).resolve()
        required_sources.update((str(profile_source),str(profile_source.with_name('financial_value_train.py'))))
    if not c.get('source_files_sha256') or not required_sources<=set(c['source_files_sha256']):
        raise EvidenceError('COMPLETE_TRIAL_SOURCE_INVENTORY_REQUIRED')
    for p,want in c['source_files_sha256'].items():
        if sha(p)!=want:raise EvidenceError('TRIAL_SOURCE_CHANGED')

def run(c):
    os.umask(0o077);validate_trial(c)
    consumer=read(c['consumer_config']);manifest=validate_config(consumer)
    frozen=read(c['dataset_path']);out=Path(c['output_root'])
    if out.exists():raise EvidenceError('TRIAL_OUTPUT_ALREADY_EXISTS_NO_REPLAY')
    from .resident_resource_guard import Gate,verify_config
    guard=read(c['guard_config']);original=verify_config(guard)
    from laya_cell import resident_hip_runtime as hip_runtime
    hip_runtime.check_guard_selection(c,original)
    gate=Gate(guard,'trainer')
    # The original source guard may prepend its own runtime for CPU freshness
    # checks. Keep subsequent model/sandbox imports in this pinned package.
    source_paths=list(sys.path)
    try:admitted=gate.acquire({'trial':'current100k-value','dataset_sha256':c['dataset_sha256']})
    finally:sys.path[:]=source_paths
    if not admitted:
        raise EvidenceError('RESIDENT_ADMISSION_DENIED_NO_MODEL_LOADED')
    started=time.monotonic();gpu_import_attempted=False;gpu_runtime_ready=False;torch=None
    raw=tok=head=x=y=features=feature=None
    try:
        hip_context=hip_runtime.prepare_worker(c,'train')
        out.mkdir(mode=0o700,parents=True,exist_ok=False)
        write_once(out/'plan.json',c)
        from sandbox import enter
        restrictions=enter({'output_dir':str(out),'period_path':consumer['period_path'],
            'legacy_capture':c['dataset_path'],'oms_path':consumer['reward_config']['oms_path'],
            'export_dir':manifest['journal_directory'],'read_only_paths':[
                c['consumer_config'],consumer['runtime_root'],consumer['registration']['manifest'],
                c['base_directory'],str(Path(c['base_directory']).parent),c['source_root'],
                original['runtime_directory'],c['native_binary'],sys.prefix]
                +(hip_context['readonly'] if hip_context is not None else [])})
        write_once(out/'sandbox.json',restrictions)
        hip_runtime.activate(hip_context)
        from .export_current100k import export
        before=copy.deepcopy(consumer);before['output_root']=str(out/'replay-before')
        before_status=export(before);rows=selected_rows(frozen,read(before_status['dataset_path']))
        # Only after sandbox and shared admission may a GPU package/model be loaded.
        gpu_import_attempted=True
        import torch
        hip_runtime.prove(hip_context,'AFTER_TORCH_BEFORE_MODEL')
        gpu_runtime_ready=True
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
        head,diagnostic=train_head(x,y,readout_profile=c.get('readout_profile'))
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
        if c.get('readout_profile') is not None:
            record['readout_profile']=copy.deepcopy(c['readout_profile'])
        hip_proof=hip_runtime.prove_before_publish(c)
        if hip_proof is not None:
            record.update(hip_runtime_manifest_sha256=hip_proof['manifest_sha256'],hip_runtime_proof=hip_proof)
        record['candidate_id']=digest(record);write_once(out/'candidate.json',record)
        print(json.dumps({k:record[k] for k in ('status','candidate_id','rows','pnl_reward_used','scorer_or_actor_updated','max_gpu_allocated_bytes','wall_seconds')}),flush=True)
        return record
    except BaseException as exc:
        # Chained encoder/optimizer errors can retain tensors outside the
        # outer traceback. Clear finished frames throughout the error chain.
        pending=[exc];seen=set()
        while pending:
            error=pending.pop()
            if id(error) in seen:continue
            seen.add(id(error));traceback.clear_frames(error.__traceback__)
            pending.extend(e for e in (error.__cause__,error.__context__) if e is not None)
        raise
    finally:
        raw=tok=head=x=y=features=feature=None
        if gpu_import_attempted:
            gc.collect()
            if gpu_runtime_ready and torch.cuda.is_initialized():
                torch.cuda.synchronize();torch.cuda.empty_cache()
        # No handoff after failed cleanup or an imported, unproven runtime.
        # The original lease FD then stays open until the finite process exits.
        # This sandboxed finite worker owns only its heavy lease. Cross-task
        # cache maintenance belongs to the unconfined resident Guard parent.
        if not gpu_import_attempted or gpu_runtime_ready:gate.lease.release()

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();run(read(a.config))
if __name__=='__main__':main()
