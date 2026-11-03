#!/usr/bin/env python3
"""Frozen layer geometry: no fitting, weight edits, service changes or trading."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, resource, sys, time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('study', 'runtime', 'base', 'out'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--all-original-rows',action='store_true')
    args = p.parse_args(); os.umask(0o077); started = time.monotonic()
    root, out = Path(args.study), Path(args.out)
    if out.exists() and any(out.iterdir()): raise RuntimeError('OUTPUT_MUST_BE_EMPTY')
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    spec = importlib.util.spec_from_file_location('frozen_study', root/'current100k_study.py')
    study = importlib.util.module_from_spec(spec); sys.modules[spec.name] = study; spec.loader.exec_module(study)
    torch = study.configure_cpu(2)
    from safetensors.torch import load_file
    from torch.nn import functional as F
    manifest, candidate, rows = study.snapshot(root)
    identity = json.loads((root/'features/identity.json').read_text())
    fm = json.loads((root/'features/manifest.json').read_text())
    if study.sha(root/'features/original-fp32.safetensors') != fm['features_sha256']: raise RuntimeError('CACHE_HASH')
    if study.sha(Path(args.runtime)/'learn/laya_cell/model.py') != identity['model_loader_sha256']: raise RuntimeError('LOADER_PIN')
    cache = load_file(str(root/'features/original-fp32.safetensors'))['features']
    head = load_file(str(root/'snapshot/value-head.safetensors'))
    indices = list(range(len(rows))) if args.all_original_rows else sorted(set((0, len(rows)//3, 2*len(rows)//3, len(rows)-1)))
    probes = [(i, 'original', rows[i]['feature_text']) for i in indices]
    for i in (() if args.all_original_rows else (indices[0], indices[-1])):
        for name, text in study.perturbations(rows[i]):
            if name in ('action_flip', 'quote_reserialized', 'trend_reversed'): probes.append((i,name,text))
    binding = {'schema':'heptatrader.layer-diagnosis-binding.v1','scope':'KNOWN_DEVELOPMENT_INPUTS',
        'study':str(root),'runtime':args.runtime,'base':args.base,'base_id':candidate['base_id'],
        'dataset_sha256':manifest['files']['dataset-selected.json']['sha256'],
        'feature_cache_sha256':fm['features_sha256'],'model_loader_sha256':identity['model_loader_sha256'],
        'source_sha256':study.sha(Path(__file__)),'selected_indices':indices,'branch_blocks':[0,12,18],
        'probes':[{'index':i,'variant':v,'text_sha256':study.digest(t.encode())} for i,v,t in probes],
        'threads':2,'dtype':'float32','device':'cpu','weights_modified':False,
        'fit_performed':False,'labels_used_for_selection':False,'trade_authority':False}
    study.json_write(out/'binding.json',binding)
    t = time.monotonic(); encoder = study.WarmCpuEncoder(args.runtime,args.base,threads=2)
    if encoder.base_id != candidate['base_id']: raise RuntimeError('BASE_PIN')
    load_seconds = time.monotonic()-t
    study.log('layer_model_loaded',seconds=load_seconds,model_type=encoder.raw.encoder.config.model_type)
    receipts, data = [], {}

    def geometry(vectors):
        x = torch.stack(vectors).double(); z = x-x.mean(0); unit = F.normalize(x,dim=1)
        cosine = unit@unit.T; off = cosine[~torch.eye(len(x),dtype=torch.bool)]
        s = torch.linalg.svdvals(z); power = s.square(); total = power.sum()
        prob = power/total if total>0 else power; nz = prob>0
        return {'n':len(x),'mean_norm':float(x.norm(dim=1).mean()),'mean_feature_norm':float(x.mean(0).norm()),
            'centered_rms_l2':float(total.sqrt()/len(x)**.5),
            'centered_fraction_of_rms_norm':float(total.sqrt()/x.square().sum().sqrt()),
            'pairwise_cosine_min':float(off.min()),'pairwise_cosine_median':float(off.median()),
            'pairwise_cosine_max':float(off.max()),
            'effective_rank':float(torch.exp(-(prob[nz]*prob[nz].log()).sum())) if total>0 else 0.,
            'top1_centered_energy_share':float(power[0]/total) if total>0 else 0.}

    for i, variant, text in probes:
        study.memory_guard(); t = time.monotonic()
        batch = encoder.tokenizer([text],padding=True,truncation=False,return_offsets_mapping=True,return_tensors='pt')
        offsets = batch.pop('offset_mapping')[0].tolist()
        if batch['input_ids'].shape[1]>2048:
            receipt={'index':i,'variant':variant,'supported':False,'reason':'CONTEXT_EXCEEDS_2048_NO_TRUNCATION',
                'token_count':int(batch['attention_mask'].sum())}
            receipts.append(receipt); study.json_write(out/f'row-{i:04d}-{variant}.json',receipt)
            study.log('layer_probe_skipped',**receipt); continue
        batch={k:v.to('cpu') for k,v in batch.items() if k in ('input_ids','attention_mask','token_type_ids')}
        prefix='Recent authoritative quotes='; qs=text.index(prefix)+len(prefix)
        _, qlen=json.JSONDecoder().raw_decode(text[qs:]); qe=qs+qlen
        act=text.index('selected action=')+len('selected action=')
        # BUY/SELL differ in string length after an action-flip probe.
        alen=4 if text[act:].startswith('SELL') else 3
        qm=torch.tensor([a<qe and b>qs and b>a for a,b in offsets])
        am=torch.tensor([a<act+alen and b>act and b>a for a,b in offsets])
        active=batch['attention_mask'][0].bool()
        groups={'all':active,'quotes':active&qm,'template':active&~qm,'action':active&am}
        with torch.inference_mode():
            model=encoder.raw.encoder
            if not all(hasattr(model,n) for n in ('embeddings','layers','final_norm')): raise RuntimeError('UNSUPPORTED_ARCHITECTURE')
            captured=[]; branch={}
            def capture(name):
                def hook(module,inputs,output):
                    if not isinstance(output,torch.Tensor): raise RuntimeError('HOOK_OUTPUT_TYPE:'+name)
                    captured.append((name,output.detach()))
                return hook
            handles=[model.embeddings.register_forward_hook(capture('embedding'))]
            handles += [layer.register_forward_hook(capture(f'transformer_block_{j}')) for j,layer in enumerate(model.layers)]
            handles.append(model.final_norm.register_forward_hook(capture('final_norm')))
            def capture_input(key):
                def hook(module,inputs): branch[key]=inputs[0].detach()
                return hook
            def capture_branch(key):
                def hook(module,inputs,output):
                    branch[key]=(output[0] if isinstance(output,tuple) else output).detach()
                return hook
            for j in (0,12,18):
                handles += [model.layers[j].register_forward_pre_hook(capture_input(f'{j}.input')),
                    model.layers[j].attn.register_forward_hook(capture_branch(f'{j}.attention')),
                    model.layers[j].mlp.register_forward_hook(capture_branch(f'{j}.mlp'))]
            try: result=model(**batch,return_dict=True)
            finally:
                for handle in handles: handle.remove()
            stage_names=[name for name,_ in captured]; states=[hidden for _,hidden in captured]
            if len(states)!=len(model.layers)+2: raise RuntimeError('HIDDEN_STATES_MISSING')
            if not torch.equal(states[-1],result.last_hidden_state): raise RuntimeError('FINAL_HIDDEN_DISAGREES')
            mask=batch['attention_mask'].unsqueeze(-1).to(result.last_hidden_state.dtype)
            mean=(result.last_hidden_state*mask).sum(1)/mask.sum(1).clamp_min(1)
            final=F.normalize(mean.float(),dim=1)
            refpath=root/'features/probes'/f'row-{i:04d}-{variant}.safetensors'
            ref=cache[i:i+1] if variant=='original' else (load_file(str(refpath))['features'] if refpath.exists() else None)
            err=None if ref is None else float((final-ref).abs().max())
            if err is not None and err>2e-5: raise RuntimeError('FINAL_CACHE_PARITY:'+str(err))
            metrics,tensors=[],{}
            for layer,hidden in enumerate(states):
                h=hidden[0].float(); metric={'index':layer,'stage':stage_names[layer],'groups':{}}
                for group,gm in groups.items():
                    if not gm.any(): raise RuntimeError('EMPTY_GROUP:'+group)
                    hs=h[gm]; pool=hs.mean(0); unit=F.normalize(pool,dim=0)
                    tensors[f'pooled.{group}.{layer}']=pool.clone()
                    tensors[f'normalized.{group}.{layer}']=unit.clone()
                    rms2=hs.square().sum(1).mean()
                    metric['groups'][group]={'tokens':int(gm.sum()),'pooled_norm':float(pool.norm()),
                        'token_rms_norm':float(rms2.sqrt()),
                        'token_centered_rms':float((hs-pool).square().sum(1).mean().sqrt()),
                        'mean_component_energy_fraction':float(pool.square().sum()/rms2)}
                metrics.append(metric)
            branch_metrics={}
            for j in (0,12,18):
                pre,attn,mlp=(branch[f'{j}.{name}'] for name in ('input','attention','mlp'))
                post_attn=pre+attn; post_mlp=post_attn+mlp
                actual=dict(captured)[f'transformer_block_{j}']
                if not torch.equal(post_mlp,actual): raise RuntimeError('RESIDUAL_RECONSTRUCTION:'+str(j))
                for name,value in [('input',pre),('attention',attn),('after_attention',post_attn),('mlp',mlp),('output',actual)]:
                    pool=value[0][active].float().mean(0)
                    tensors[f'branch_pooled.{j}.{name}']=pool.clone()
                    tensors[f'branch_normalized.{j}.{name}']=F.normalize(pool,dim=0).clone()
                    branch_metrics[f'{j}.{name}']={'pooled_norm':float(pool.norm()),
                        'token_rms_norm':float(value[0][active].square().sum(1).mean().sqrt())}
            pool_err=float((tensors[f'normalized.all.{len(states)-1}']-final[0]).abs().max())
            if pool_err>2e-6: raise RuntimeError('POOL_PARITY:'+str(pool_err))
            receipt={'index':i,'variant':variant,'supported':True,'row_id':rows[i]['row_id'],
                'text_sha256':study.digest(text.encode()),'tokens':int(active.sum()),'hidden_state_count':len(states),
                'original_cache_max_abs_error':err,'pooling_max_abs_error':pool_err,
                'parent_prediction_usd':float(study.head_forward(head,final)[0]),
                'wall_seconds':time.monotonic()-t,'layers':metrics,'branch_metrics':branch_metrics,
                'residual_reconstruction_exact':True}
            data[i,variant]=tensors; receipts.append(receipt)
            study.tensors_write(out/f'row-{i:04d}-{variant}.safetensors',tensors)
            study.json_write(out/f'row-{i:04d}-{variant}.json',receipt)
        del result,states,captured,branch,hidden,h,hs,batch,pre,attn,mlp,post_attn,post_mlp,actual,value
        study.log('layer_probe_complete',index=i,variant=variant,seconds=receipt['wall_seconds'],parity_error=err)
    originals=[r for r in receipts if r['variant']=='original' and r['supported']]
    layer_count=originals[0]['hidden_state_count']; summary=[]
    for layer in range(layer_count):
        record={'index':layer,'stage':stage_names[layer]}
        for group in ('all','quotes','template','action'):
            record[group]=geometry([data[r['index'],'original'][f'normalized.{group}.{layer}'] for r in originals])
        record['raw_all']=geometry([data[r['index'],'original'][f'pooled.all.{layer}'] for r in originals])
        summary.append(record)
    perturbations=[]
    for r in receipts:
        i,variant=r['index'],r['variant']
        if variant=='original' or not r['supported']: continue
        changed,base=data[i,variant],data[i,'original']; layers=[]
        for layer in range(layer_count):
            record={'index':layer}
            for group in ('all','quotes','template','action'):
                key=f'normalized.{group}.{layer}'; a,b=changed[key].double(),base[key].double()
                record[group]={'normalized_l2_change':float((a-b).norm()),'cosine':float(a@b),
                    'raw_l2_change':float((changed[f'pooled.{group}.{layer}']-base[f'pooled.{group}.{layer}']).norm())}
            layers.append(record)
        perturbations.append({'index':i,'variant':variant,'layers':layers})
    branch_geometry=[]
    for j in (0,12,18):
        for name in ('input','attention','after_attention','mlp','output'):
            branch_geometry.append({'block':j,'component':name,
                'raw':geometry([data[r['index'],'original'][f'branch_pooled.{j}.{name}'] for r in originals]),
                'normalized':geometry([data[r['index'],'original'][f'branch_normalized.{j}.{name}'] for r in originals])})
    report={'schema':'heptatrader.current100k-layer-diagnosis.v1','binding_sha256':study.sha(out/'binding.json'),
        'architecture':encoder.raw.encoder.config.to_dict(),
        'stage_note':'embedding, outputs of zero-based Transformer blocks 0 through 21, then final_norm',
        'original_rows':len(originals),'requested_probes':len(probes),'completed_probes':len(data),
        'skipped_probes':[r for r in receipts if not r['supported']],
        'layer_geometry':summary,'perturbations':perturbations,'branch_geometry':branch_geometry,
        'max_cache_parity_error':max(r['original_cache_max_abs_error'] or 0 for r in receipts if r['supported']),
        'load_seconds':load_seconds,'wall_seconds':time.monotonic()-started,
        'max_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        'gpu_initialized':torch.cuda.is_initialized(),'weights_modified':False,
        'historical_labels_already_viewed':True,'future_efficacy_claim':False,
        'limitations':['Previously viewed historical requests are mechanism probes, not an unbiased test set.',
            'Group pooling is diagnostic; the original model uses all-token masked mean pooling.',
            'Layer geometry localizes contrast loss, not training causality or future predictive value.',
            'The original head is scored only on its original final-layer representation.']}
    study.json_write(out/'report.json',report)
    study.log('layer_diagnosis_complete',completed=len(data),wall_seconds=report['wall_seconds'],max_cache_parity_error=report['max_cache_parity_error'])


if __name__=='__main__': main()
