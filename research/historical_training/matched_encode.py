"""CPU-only frozen paired encoder extraction in the existing no-network sandbox."""
from __future__ import annotations
import argparse,gc,hashlib,json,os,sys,time
from pathlib import Path
from .common import EvidenceError,read,sha,write_once
from .matched_data import SIZES,text


def encode(config,config_path):
    out=Path(config['output']);plan=read(out/'plan.json')
    if (out/'features.safetensors').exists():raise EvidenceError('FEATURES_EXIST_NO_REPEAT')
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',USE_TF='0',TOKENIZERS_PARALLELISM='false')
    from sandbox import enter
    proof=enter({'output_dir':str(out),'period_path':str(out/'input.json'),'legacy_capture':str(out/'input.json'),
        'oms_path':str(out/'input.json'),'export_dir':str(out/'input.json'),
        'read_only_paths':[config['source_root'],str(config_path),config['mmbert_directory'],config['english_directory'],
            config['mmbert_receipt'],config['english_receipt'],sys.prefix]})
    try:
        with open(config['forbidden_original'],'rb') as f:f.read(1)
    except PermissionError:proof['old_test_container_denied']=True
    else:raise EvidenceError('OLD_TEST_NOT_DENIED')
    write_once(out/'encoder-sandbox.json',proof)
    if sha(out/'input.json')!=plan['input_sha256']:raise EvidenceError('PAIRED_INPUT_CHANGED')
    data=read(out/'input.json');rows=sum((data['groups'][k] for k in SIZES),[]);texts=[text(r) for r in rows]
    if [hashlib.sha256(t.encode()).hexdigest() for t in texts]!=plan['text_sha256']:raise EvidenceError('PAIRED_TEXT_CHANGED')
    from paired_net_100k_preflight.run import asset_manifest
    for arm in ('mmbert','english'):
        if asset_manifest(Path(config[arm+'_directory']),Path(config[arm+'_receipt']),arm)!=plan['assets'][arm]:raise EvidenceError('PAIRED_BACKBONE_CHANGED')
    import torch
    from torch.nn import functional as F
    from safetensors.torch import load_file,save_file
    from laya.common import build_model
    from transformers import AutoTokenizer
    from .matched_projection import project_fixed64,validate_projection
    for arm in ('mmbert','english'):validate_projection(plan['assets'][arm]['hidden_size'],plan['projection_width'])
    torch.set_num_threads(2);torch.set_num_interop_threads(1)
    tensors={};info={}
    for arm in ('mmbert','english'):
        started=time.monotonic();base=Path(config[arm+'_directory'])
        model=build_model(read(base/'rl_agent_config.json'),encoder_dir=str(base/'encoder'),pretrained=False)
        weights=load_file(str(base/'model.safetensors'),device='cpu')
        model.load_state_dict(weights,strict=True);del weights
        model.requires_grad_(False).float().eval()
        tokenizer=AutoTokenizer.from_pretrained(base/'tokenizer',local_files_only=True,trust_remote_code=False)
        vectors=[];lengths=[]
        for index in range(0,len(texts),2):
            batch=tokenizer(texts[index:index+2],return_tensors='pt',padding=True,truncation=False)
            counts=batch['attention_mask'].sum(1).tolist();lengths.extend(counts)
            if any(n>1024 for n in counts):raise EvidenceError('PAIRED_INPUT_TRUNCATION_FORBIDDEN')
            inputs={k:v for k,v in batch.items() if k in ('input_ids','attention_mask')}
            with torch.inference_mode():
                hidden=model.encoder(**inputs).last_hidden_state
                mask=inputs['attention_mask'].unsqueeze(-1).to(hidden.dtype)
                vec=F.normalize((hidden*mask).sum(1)/mask.sum(1),dim=-1)
            vectors.append(vec.cpu())
            if index%128==0:print(json.dumps({'arm':arm,'encoded':index+2,'total':len(texts),'device':'cpu'}),flush=True)
        raw=torch.cat(vectors,0).contiguous()
        # Preserve raw completed encoding before projection for auditable recovery.
        raw_path=out/(arm+'-raw.safetensors')
        if raw_path.exists():raise EvidenceError('RAW_ENCODER_ARTIFACT_ALREADY_EXISTS_NO_OVERWRITE')
        save_file({'features':raw},str(raw_path));raw_path.chmod(0o400)
        write_once(out/(arm+'-raw-receipt.json'),{'plan_sha256':sha(out/'plan.json'),
            'input_sha256':sha(out/'input.json'),'raw_sha256':sha(raw_path),
            'text_hashes_sha256':hashlib.sha256(json.dumps(plan['text_sha256']).encode()).hexdigest(),
            'asset':plan['assets'][arm],'rows':len(texts),'width':int(raw.shape[1]),'trained':False})
        projected=project_fixed64(raw,plan['projection_seed'],plan['projection_width'])
        if not torch.isfinite(projected).all():raise EvidenceError('NONFINITE_FROZEN_FEATURES')
        tensors[arm]=projected
        info[arm]={'rows':len(texts),'tokens_min':min(lengths),'tokens_max':max(lengths),'wall_seconds':time.monotonic()-started,
            'strict_complete_checkpoint':True,'frozen':True,'gpu_used':False,'feature_sha256':hashlib.sha256(projected.numpy().tobytes()).hexdigest()}
        del model,tokenizer,vectors,raw,projected,hidden,mask,vec,inputs,batch
        gc.collect()
    save_file(tensors,str(out/'features.safetensors'));(out/'features.safetensors').chmod(0o400)
    result={'schema':'heptatrader.matched-cpu-encoder-receipt.v1','plan_sha256':sha(out/'plan.json'),
        'features_sha256':sha(out/'features.safetensors'),'encoders':info,'same_text':True,'no_truncation':True,
        'old_test_rows':0,'orders_sent':0,'trainable_backbone_parameters':0,'torch_version':torch.__version__}
    write_once(out/'encoder-receipt.json',result);print(json.dumps(result,indent=2),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();encode(read(a.config),a.config)
if __name__=='__main__':main()
