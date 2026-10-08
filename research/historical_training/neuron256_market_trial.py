"""Bounded real-history Neuron256 training with frozen numeric/mmBERT experts.
Archive-only, not the live five-minute/7-bin packet or the frozen 3-bin campaign.
Old inspected development periods are not renamed as unseen prospective tests.
"""
from __future__ import annotations
import argparse,hashlib,json,math,os,sys,time
from bisect import bisect_right
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once

SIZES={'fit':512,'tune':128,'calibration':128,'check':256}
EDGES=(-10.,-3.,-1.,1.,3.,10.)
PROFILE='EURUSD_5MIN_LOOKBACK12_LEAD1_HOLD3_V1'


def validate_populations(data):
    if (data.get('schema')!='heptatrader.matched-lead1-input.v1' or data.get('profile')!=PROFILE
        or data.get('old_test_rows_exported')!=0 or data.get('trade_authority') is not False
        or set(data.get('groups',{}))!=set(SIZES)):
        raise EvidenceError('ARCHIVE_SOURCE_SCOPE')
    groups=data['groups'];previous_end=None;seen=set()
    for name,size in SIZES.items():
        rows=groups[name]
        if len(rows)!=size:raise EvidenceError('ARCHIVE_DECLARED_ROLE_SIZE')
        times=[r['time'] for r in rows]
        if times!=sorted(set(times)):raise EvidenceError('ARCHIVE_DUPLICATE_OR_UNORDERED_TIME')
        for row in rows:
            times=[row[k] for k in ('history_start','time','entry_at','exit_at','outcome_end')]
            if any(type(t) is not int for t in times) or not all(a<b for a,b in zip(times,times[1:])):
                raise EvidenceError('ARCHIVE_CAUSAL_TIME')
            if (row['entry_at']!=row['time']+300 or row['exit_at']!=row['time']+1200
                or row['outcome_end']!=row['time']+1500 or row.get('real_execution_reward') is not False):
                raise EvidenceError('ARCHIVE_PROFILE_OR_REWARD_TYPE')
            if row['time'] in seen:raise EvidenceError('ARCHIVE_CROSS_ROLE_DUPLICATE')
            seen.add(row['time'])
            if len(row['features'])!=25 or not all(type(v) in (int,float) and math.isfinite(v) for v in row['features']):
                raise EvidenceError('ARCHIVE_NUMERIC_INPUT')
            for k in ('target_bps','long_after_spread_bps','short_after_spread_bps'):
                if type(row[k]) not in (int,float) or not math.isfinite(row[k]):raise EvidenceError('ARCHIVE_OUTCOME_NONFINITE')
        if previous_end is not None and previous_end>=min(r['history_start'] for r in rows):
            raise EvidenceError('FULL_INPUT_OUTCOME_SPAN_OVERLAP')
        previous_end=max(r['outcome_end'] for r in rows)
    return groups


def verify_bundle(c):
    if (c.get('schema')!='heptatrader.neuron256-archive-trial-config.v1'
        or c.get('trade_authority') is not False or c.get('automatic_selection') is not False
        or c.get('expert_steps')!=128 or c.get('graph_steps')!=64
        or c.get('seed')!=20261009 or c.get('cost_buffer_bps')!=.5):
        raise EvidenceError('ARCHIVE_FIXED_PLAN_REQUIRED')
    pins=c.get('source_files_sha256',{})
    if str(Path(__file__).resolve()) not in pins:raise EvidenceError('ARCHIVE_SOURCE_INVENTORY_REQUIRED')
    for p,want in {**c['input_files_sha256'],**pins}.items():
        if sha(p)!=want:raise EvidenceError('ARCHIVE_PIN_CHANGED:'+Path(p).name)
    root=Path(c['bundle']);data=read(root/'input.json');groups=validate_populations(data)
    plan=read(root/'plan.json');receipt=read(root/'mmbert-raw-receipt.json')
    if (plan['input_sha256']!=sha(root/'input.json') or receipt['input_sha256']!=plan['input_sha256']
        or receipt['plan_sha256']!=sha(root/'plan.json') or receipt['raw_sha256']!=sha(root/'mmbert-raw.safetensors')
        or receipt['rows']!=1024 or receipt['width']!=768 or receipt['trained'] is not False
        or receipt['asset']['frozen_complete_checkpoint'] is not True):
        raise EvidenceError('ARCHIVED_ENCODER_BINDING')
    from .matched_data import text
    rows=sum((groups[k] for k in SIZES),[])
    hashes=[hashlib.sha256(text(r).encode()).hexdigest() for r in rows]
    if (hashes!=plan['text_sha256']
        or hashlib.sha256(json.dumps(hashes).encode()).hexdigest()!=receipt['text_hashes_sha256']):
        raise EvidenceError('ARCHIVED_TEXT_ORDER_CHANGED')
    return data,receipt


def historical_policy(rows,probabilities,net_predictions,class_means,buffer=.5):
    """Long/short chosen with frozen forecasts only; one position, no overlap."""
    if len(rows)!=len(probabilities) or len(rows)!=len(net_predictions) or len(class_means)!=7:
        raise EvidenceError('POLICY_ROW_OR_CLASS_ALIGNMENT')
    if not math.isfinite(buffer) or buffer<0 or not all(math.isfinite(float(v)) for v in class_means):
        raise EvidenceError('POLICY_NONFINITE_RULE')
    for p,edge in zip(probabilities,net_predictions):
        if len(p)!=7 or len(edge)!=2 or not all(math.isfinite(float(v)) for v in (*p,*edge)) or any(float(v)<0 for v in p) or abs(sum(p)-1)>1e-5:
            raise EvidenceError('POLICY_PROBABILITY_OR_COST_FORECAST')
    trades=[];last_exit=-1;value=peak=drawdown=0.
    for row,p,edge in zip(rows,probabilities,net_predictions):
        if row['entry_at']<last_exit:continue
        expected=sum(float(a)*float(b) for a,b in zip(p,class_means))
        side=0 if expected>=0 else 1
        if float(edge[side])<buffer:continue
        net=float(row['long_after_spread_bps' if side==0 else 'short_after_spread_bps'])-.9
        usd=net*10.;value+=usd;peak=max(peak,value);drawdown=max(drawdown,peak-value)
        trades.append({'anchor':row['time'],'entry':row['entry_at'],'exit':row['exit_at'],
            'side':'BUY' if side==0 else 'SELL','predicted_net_bps':float(edge[side]),'net_usd':usd})
        last_exit=row['exit_at']
    return {'trades':trades,'count':len(trades),'net_usd':value,'max_drawdown_usd':drawdown,
        'fixed_trade_extra_1bp_net_usd':value-len(trades)*10.,'actual_broker_reward':False}


def checkpoint_tensors(tensors):
    return {name:value.detach().cpu().contiguous() for name,value in tensors.items()}


def run(c):
    os.umask(0o077);data,receipt=verify_bundle(c);out=Path(c['output_root'])
    if out.exists():raise EvidenceError('ARCHIVE_OUTPUT_EXISTS_NO_REFIT')
    from .resident_resource_guard import Gate,verify_config
    gc=read(c['guard_config']);verify_config(gc);gate=Gate(gc,'trainer')
    if not gate.acquire({'trial':'neuron256-real-archive','source':c['input_files_sha256']}):
        raise EvidenceError('RESOURCE_ADMISSION_DENIED_NO_MODEL_LOADED')
    started=time.monotonic()
    try:
        out.mkdir(mode=0o700,parents=True,exist_ok=False);write_once(out/'plan.json',c)
        from sandbox import enter
        original=read(gc['original_config']);inp=Path(c['bundle'])/'input.json'
        restrictions=enter({'output_dir':str(out),'period_path':original['period_path'],
            'legacy_capture':str(inp),'oms_path':str(inp),'export_dir':str(inp),
            'read_only_paths':[c['bundle'],c['source_root'],sys.prefix]})
        write_once(out/'sandbox.json',restrictions)
        try:
            fd=os.open(c['forbidden_old_test_file'],os.O_RDONLY);os.close(fd)
        except PermissionError:write_once(out/'old-test-access.json',{'read_denied':True,'old_test_rows':0})
        else:raise EvidenceError('OLD_TEST_ACCESS_NOT_DENIED')
        import numpy as np
        import torch
        from torch import nn
        from torch.nn import functional as F
        from safetensors.numpy import load_file
        from safetensors.torch import save_file,load_file as load_tensors
        from neuron_graph_v2.archive import ArchivedGraph
        if not torch.cuda.is_available() or not torch.version.hip or not str(torch.cuda.get_device_properties(0).gcnArchName).startswith('gfx1150'):
            raise EvidenceError('POCKET4_ROCM_REQUIRED')
        torch.set_num_threads(2);torch.manual_seed(c['seed']);device='cuda'
        torch.cuda.set_per_process_memory_fraction(min(1.,1024**3/torch.cuda.get_device_properties(0).total_memory));torch.cuda.reset_peak_memory_stats()
        raw=load_file(str(Path(c['bundle'])/'mmbert-raw.safetensors'))['features']
        if raw.shape!=(1024,768) or not np.isfinite(raw).all():raise EvidenceError('ARCHIVED_RAW_SHAPE_OR_FINITE')
        groups=data['groups'];rows=sum((groups[k] for k in SIZES),[])
        role={};start=0
        for name,size in SIZES.items():role[name]=slice(start,start+size);start+=size
        numeric=torch.tensor([r['features'] for r in rows],device=device,dtype=torch.float32)
        semantic=torch.tensor(raw,device=device,dtype=torch.float32)
        projection=torch.randn(768,256,device=device)/math.sqrt(768)
        semantic256=F.normalize(semantic@projection,dim=-1);semantic64=semantic256[:,:64]
        normalized=[];normalizers=[]
        for x in (numeric,semantic64):
            mean=x[:512].mean(0);std=x[:512].std(0,unbiased=False).clamp_min(1e-5)
            normalized.append(((x-mean)/std).detach());normalizers.append((mean,std))
        labels=torch.tensor([bisect_right(EDGES,r['target_bps']) for r in rows],device=device)
        heads=[];expert_history=[]
        for features in normalized:
            model=nn.Linear(features.shape[1],7,device=device)
            with torch.no_grad():model.weight.zero_();model.bias.zero_()
            opt=torch.optim.AdamW(model.parameters(),lr=.01,weight_decay=.01);losses=[]
            for _ in range(c['expert_steps']):
                opt.zero_grad(set_to_none=True);loss=F.cross_entropy(model(features[:512]),labels[:512]);loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True);opt.step();losses.append(float(loss.detach()))
            model.requires_grad_(False).eval();heads.append(model);expert_history.append(losses)
        with torch.no_grad():
            expert_probs=torch.stack([m(x).softmax(-1) for m,x in zip(heads,normalized)],dim=1)
            numeric256=F.normalize(F.pad(normalized[0],(0,231)),dim=-1)
            embeddings=torch.stack((numeric256,semantic256),dim=1).detach()
        graph=ArchivedGraph().to(device)
        # Output projection is not in the forecast objective: do not claim it trained.
        graph.to_output.requires_grad_(False)
        initial={n:p.detach().clone() for n,p in graph.state_dict().items()}
        frozen_experts={f'{i}.{n}':p.detach().clone() for i,m in enumerate(heads) for n,p in m.state_dict().items()}
        opt=torch.optim.AdamW((p for p in graph.parameters() if p.requires_grad),lr=.003,weight_decay=.01)
        losses=[];sl=role['tune']
        for _ in range(c['graph_steps']):
            opt.zero_grad(set_to_none=True);result=graph.archived(embeddings[sl],expert_probs[sl])
            loss=F.nll_loss(result['probabilities'].clamp_min(1e-12).log(),labels[sl]);loss.backward()
            torch.nn.utils.clip_grad_norm_(graph.parameters(),1.,error_if_nonfinite=True);opt.step();losses.append(float(loss.detach()))
        changed={n:int(torch.count_nonzero(p.detach()-initial[n])) for n,p in graph.state_dict().items()}
        if not sum(changed.values()):raise EvidenceError('NO_REAL_MARKET_GRAPH_UPDATE')
        for i,m in enumerate(heads):
            for n,p in m.state_dict().items():
                if not torch.equal(p,frozen_experts[f'{i}.{n}']):raise EvidenceError('FROZEN_EXPERT_CHANGED_BY_GRAPH')
        graph.eval()
        with torch.no_grad():allp=graph.archived(embeddings,expert_probs)['probabilities'];allp_cpu=allp.cpu().numpy()
        prior=torch.bincount(labels[:512],minlength=7).float()/512
        references={'numeric':expert_probs[:,0],'mmbert_readout':expert_probs[:,1],
            'frozen_equal_mix':expert_probs.mean(1),'training_prior':prior.expand(1024,7),
            'uniform':torch.full_like(allp,1/7),'graph':allp}
        metrics={}
        check=role['check'];truth=F.one_hot(labels[check],7).float()
        for name,p in references.items():
            score=((p[check]-truth)**2).sum(-1)
            metrics[name]={'brier':float(score.mean()),'accuracy':float((p[check].argmax(-1)==labels[check]).float().mean())}
        # Fixed training-only monetary readout. Historical side prices, not fills.
        design=torch.cat((normalized[0],torch.ones(1024,1,device=device)),dim=1)
        net=torch.tensor([[r['long_after_spread_bps']-.9,r['short_after_spread_bps']-.9] for r in rows],device=device)
        xt=design[:512].double();penalty=torch.eye(26,device=device,dtype=torch.float64)*.1;penalty[-1,-1]=0
        costweights=torch.linalg.solve(xt.T@xt+penalty,xt.T@net[:512].double()).float()
        netpred=(design@costweights).detach().cpu().numpy();class_means=[]
        train_y=labels[:512].cpu().tolist();fallback=(-15,-6,-2,0,2,6,15)
        for k in range(7):
            values=[r['target_bps'] for r,y in zip(rows[:512],train_y) if y==k];class_means.append(sum(values)/len(values) if values else fallback[k])
        calibration=historical_policy(groups['calibration'],allp_cpu[role['calibration']],netpred[role['calibration']],class_means,c['cost_buffer_bps'])
        admitted=calibration['count']>=30 and calibration['net_usd']>0 and calibration['max_drawdown_usd']<=250
        diagnostic=historical_policy(groups['check'],allp_cpu[check],netpred[check],class_means,c['cost_buffer_bps'])
        weights={f'graph.{n}':v.detach().cpu().contiguous() for n,v in graph.state_dict().items()}
        weights.update({f'expert{i}.{n}':v.detach().cpu().contiguous() for i,m in enumerate(heads) for n,v in m.state_dict().items()})
        for i,(mean,std) in enumerate(normalizers):weights[f'mean{i}']=mean.cpu();weights[f'std{i}']=std.cpu()
        weights['semantic_projection']=projection.cpu();weights['cost_weights']=costweights.cpu()
        file=out/'checkpoint.safetensors';save_file(checkpoint_tensors(weights),str(file));file.chmod(0o400)
        reloaded=ArchivedGraph().to(device);stored=load_tensors(str(file),device='cuda:0')
        reloaded.load_state_dict({k.removeprefix('graph.'):v for k,v in stored.items() if k.startswith('graph.')},strict=True)
        with torch.no_grad():difference=float((reloaded.archived(embeddings[check],expert_probs[check])['probabilities']-allp[check]).abs().max())
        if difference>1e-6:raise EvidenceError('GRAPH_CHECKPOINT_RELOAD_MISMATCH')
        np.savez(out/'check-predictions.npz',target=labels[check].cpu().numpy(),times=np.array([r['time'] for r in groups['check']]),**{k:p[check].cpu().numpy() for k,p in references.items()})
        record={'schema':'heptatrader.neuron256-real-archive-candidate.v1','created_at_ms':int(time.time()*1000),
            'status':'DEVELOPMENT_ONLY_NOT_QUALIFIED','source_profile':PROFILE,'target_bins_bps':EDGES,
            'target_schema':'ARCHIVED_LEAD1_HOLD3_MID_BPS_7_BINS_NOT_LIVE_5MIN','live_packet_emitted':False,
            'source_frozen_encoder_prompt_classes':3,'new_readout_classes':7,'new_encoder_forward_passes':0,
            'role_rows':{'expert_fit':512,'graph_fit':128,'economic_calibration':128,'development_check':256},
            'original_test_rows':0,'development_already_inspected':True,'temporal_input_outcome_spans_disjoint':True,
            'checkpoint_sha256':sha(file),'checkpoint_reload_max_difference':difference,
            'expert_losses':expert_history,'graph_losses':losses,'changed_by_tensor':changed,
            'changed_graph_parameters':sum(changed.values()),'graph_trainable_parameters':sum(p.numel() for p in graph.parameters() if p.requires_grad),
            'frozen_experts_unchanged':True,'frozen_backbone_unchanged':True,'output_projection_trained':False,
            'check_metrics':metrics,'calibration_policy':calibration,'diagnostic_check_policy':diagnostic,
            'economic_calibration_admitted':admitted,'admitted_check_policy':diagnostic if admitted else {'count':0,'net_usd':0,'action':'ABSTAIN'},
            'no_trade_baseline_net_usd':0,'cost_assumptions':{'reference_notional_usd':100000,'commission_bps':.4,'slippage_bps':.5,'spread_in_side_prices':True,'minimum_calibration_trades':30,'max_calibration_drawdown_usd':250},
            'real_execution_reward_count':0,'simulated_or_fixture_market_rows':0,'financial_value_labels_mixed':False,
            'actor_updated':False,'canonical_owner_accepted':False,'future_efficacy_proven':False,'broker_orders_sent':0,
            'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName),'max_gpu_allocated_bytes':torch.cuda.max_memory_allocated(),
            'wall_seconds':time.monotonic()-started,'encoder_receipt':receipt,'input_files_sha256':c['input_files_sha256'],'source_files_sha256':c['source_files_sha256']}
        record['candidate_id']=digest(record);write_once(out/'candidate.json',record)
        print(json.dumps({k:record[k] for k in ('candidate_id','status','changed_graph_parameters','check_metrics','economic_calibration_admitted','max_gpu_allocated_bytes','wall_seconds')}),flush=True)
        return record
    finally:gate.release()


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();run(read(a.config))
if __name__=='__main__':main()
