"""Optional resident CPU batch-two feature path; distinct backend receipt."""
import hashlib,time


def encode_pair(encoder,request):
    from torch.nn import functional as F
    try:from .current100k_study import memory_guard,PROFILE
    except ImportError:from current100k_study import memory_guard,PROFILE
    memory_guard();torch=encoder.torch;started=time.monotonic()
    sides=('BUY','SELL');texts=[encoder.feature_text(request,side) for side in sides]
    batch=encoder.tokenizer(texts,padding=True,truncation=False,return_tensors='pt')
    if batch['input_ids'].shape[1]>2048:raise RuntimeError('CONTEXT_EXCEEDS_2048_NO_TRUNCATION')
    batch={k:v.to('cpu') for k,v in batch.items() if k in ('input_ids','attention_mask','token_type_ids')}
    with torch.inference_mode():
        hidden=encoder.raw.encoder(**batch).last_hidden_state
        mask=batch['attention_mask'].unsqueeze(-1).to(hidden.dtype)
        x=F.normalize(((hidden*mask).sum(1)/mask.sum(1).clamp_min(1)).float(),p=2,dim=1).detach().cpu()
    if x.shape!=(2,768) or not torch.isfinite(x).all():raise RuntimeError('FEATURE_SHAPE_OR_NUMBER')
    seconds=time.monotonic()-started
    return {'features':{side:x[i].tolist() for i,side in enumerate(sides)},
            'feature_text_sha256':{side:hashlib.sha256(texts[i].encode()).hexdigest() for i,side in enumerate(sides)},
            'feature_profile':PROFILE,'backend':'LAYA_CPU_FP32_BATCH2_MAX2048_NO_TRUNCATION_V1',
            'threads':encoder.threads,'batch_size':2,'actual_loader_sha256':encoder.actual_loader_sha256,'base_id':encoder.base_id,'computed_at_ms':int(time.time()*1000),
            'receipts':{side:{'tokens':int(batch['attention_mask'][i].sum()),'batch_seconds':seconds,'shared_batch':True,'dtype':'float32','device':'cpu'} for i,side in enumerate(sides)}}
