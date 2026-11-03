#!/usr/bin/env python3
"""In-memory causal mechanism probes on already-viewed inputs; never a promotion."""
from __future__ import annotations
import argparse, importlib.util, json, os, resource, sys, time
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('study','runtime','base','layer_run','out'): p.add_argument('--'+n,required=True)
    a=p.parse_args(); os.umask(0o077); started=time.monotonic()
    root,layer_run,out=Path(a.study),Path(a.layer_run),Path(a.out)
    if out.exists() and any(out.iterdir()):raise RuntimeError('OUTPUT_MUST_BE_EMPTY')
    out.mkdir(parents=True,exist_ok=True,mode=0o700)
    spec=importlib.util.spec_from_file_location('frozen_study',root/'current100k_study.py')
    study=importlib.util.module_from_spec(spec);sys.modules[spec.name]=study;spec.loader.exec_module(study)
    torch=study.configure_cpu(2)
    from safetensors.torch import load_file
    from torch.nn import functional as F
    _,candidate,rows=study.snapshot(root)
    identity=json.loads((root/'features/identity.json').read_text())
    if study.sha(Path(a.runtime)/'learn/laya_cell/model.py')!=identity['model_loader_sha256']:raise RuntimeError('LOADER_PIN')
    layer_report=json.loads((layer_run/'report.json').read_text())
    if layer_report['original_rows']!=len(rows) or layer_report['max_cache_parity_error']!=0:raise RuntimeError('ALL54_PARITY_REQUIRED')
    vectors=[load_file(str(layer_run/f'row-{i:04d}-original.safetensors'))['branch_pooled.12.mlp'] for i in range(len(rows))]
    u=F.normalize(torch.stack(vectors).double().mean(0),dim=0).float()
    indices=sorted(set((0,len(rows)//3,2*len(rows)//3,len(rows)-1)))
    variants=('parent','half_mlp_common_direction','zero_entire_mlp')
    binding={'schema':'heptatrader.block12-intervention-binding.v1',
        'scope':'KNOWN_HISTORICAL_MECHANISM_ONLY','base_id':candidate['base_id'],
        'layer_report_sha256':study.sha(layer_run/'report.json'),
        'diagnostic_source_sha256':study.sha(Path(__file__)),'selected_indices':indices,
        'variants':variants,'direction_source':'mean of block12 MLP pooled outputs of all frozen54 inputs; labels unused',
        'direction_sha256':study.digest(u.numpy().tobytes()),'weights_modified':False,
        'candidate_registration_modified':False,'future_efficacy_claim':False}
    study.json_write(out/'binding.json',binding);study.tensors_write(out/'direction.safetensors',{'u':u})
    encoder=study.WarmCpuEncoder(a.runtime,a.base,threads=2)
    if encoder.base_id!=candidate['base_id']:raise RuntimeError('BASE_PIN')
    cache=load_file(str(root/'features/original-fp32.safetensors'))['features']
    head=load_file(str(root/'snapshot/value-head.safetensors'))
    results={};receipts=[]
    for variant in variants:
        for i in indices:
            for probe in ('original','action_flip'):
                text=rows[i]['feature_text'] if probe=='original' else dict(study.perturbations(rows[i]))['action_flip']
                study.memory_guard(); t=time.monotonic(); batch=encoder.tokenizer([text],padding=True,truncation=False,return_tensors='pt')
                if batch['input_ids'].shape[1]>2048:raise RuntimeError('CONTEXT_LIMIT')
                batch={k:v.to('cpu') for k,v in batch.items() if k in ('input_ids','attention_mask','token_type_ids')}
                def intervene(module,inputs,output):
                    if variant=='zero_entire_mlp':return torch.zeros_like(output)
                    if variant=='half_mlp_common_direction':return output-.5*(output@u).unsqueeze(-1)*u
                    raise RuntimeError('VARIANT')
                handle=None
                if variant!='parent':handle=encoder.raw.encoder.layers[12].mlp.register_forward_hook(intervene)
                try:
                    with torch.inference_mode():
                        hidden=encoder.raw.encoder(**batch).last_hidden_state
                        mask=batch['attention_mask'].unsqueeze(-1).to(hidden.dtype)
                        pooled=(hidden*mask).sum(1)/mask.sum(1).clamp_min(1)
                        feature=F.normalize(pooled.float(),dim=1).detach().cpu()
                finally:
                    if handle is not None:handle.remove()
                ref=cache[i:i+1] if probe=='original' else load_file(str(root/'features/probes'/f'row-{i:04d}-action_flip.safetensors'))['features']
                parity=float((feature-ref).abs().max())
                if variant=='parent' and parity!=0:raise RuntimeError('PARENT_PARITY')
                results[variant,i,probe]=feature[0].clone()
                receipt={'variant':variant,'index':i,'probe':probe,'seconds':time.monotonic()-t,
                    'feature_l2_change_from_parent':float((feature-ref).norm()),
                    'original_parent_readout_usd':float(study.head_forward(head,feature)[0]),
                    'parent_parity_max_abs':parity if variant=='parent' else None}
                receipts.append(receipt);study.log('block12_intervention',**receipt)
    summary=[]
    for variant in variants:
        x=torch.stack([results[variant,i,'original']for i in indices]).double();z=x-x.mean(0)
        w=head['value.weight'].double();b=head['value.bias'].double()
        pred=100*(x@w.T+b).flatten()
        action_deltas=[float((results[variant,i,'action_flip']-results[variant,i,'original']).norm())for i in indices]
        summary.append({'variant':variant,'final_centered_rms_l2':float(z.square().sum(1).mean().sqrt()),
            'final_mean_feature_norm':float(x.mean(0).norm()),
            'unchanged_parent_readout_std_usd':float(pred.std(correction=0)),
            'action_flip_l2_changes':action_deltas,
            'interpretation':'Representation intervention only; unchanged head is distribution-shifted and scores are not quality evidence.'})
    study.tensors_write(out/'final-features.safetensors',{f'{v}.{i}.{p}':x for (v,i,p),x in results.items()})
    report={'schema':'heptatrader.block12-intervention.v1','binding_sha256':study.sha(out/'binding.json'),
        'summary':summary,'receipts':receipts,'wall_seconds':time.monotonic()-started,
        'max_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        'gpu_initialized':torch.cuda.is_initialized(),'weights_modified':False,
        'future_efficacy_claim':False,'candidate_registration_modified':False,
        'limitations':['Only four fixed historical requests; no independent outcome claim.',
            'Zeroing an entire MLP is a causal localization probe, not a deployable validated patch.',
            'Half common-direction removal is an in-memory hook; original weights and frozen candidate set remain unchanged.']}
    study.json_write(out/'report.json',report);study.log('block12_intervention_complete',summary=summary,seconds=report['wall_seconds'])


if __name__=='__main__':main()
