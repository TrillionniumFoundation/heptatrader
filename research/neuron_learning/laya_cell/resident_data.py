"""Append-only feed prefixes -> mature frozen Laya datasets, separate from orders."""
from __future__ import annotations
from collections import Counter
from bisect import bisect_left,bisect_right
from decimal import Decimal
from pathlib import Path
import json,time
from .integrity import ContractError,digest,sha_file,read_json
from .shadow_source import read_source
from .dataset import rows_for_training


def read_quotes(config,now_ms,*,read_hints=None):
    if read_hints is not None:
        read_hints.clear()
    if sha_file(Path(config['period_path']))!=config['period_sha256']:
        raise ContractError('RESIDENT_PERIOD_CHANGED')
    from .source_archive import inventory
    feed_paths=sorted(Path(config['feed_directory']).glob('segment-*/quotes.jsonl'))
    sealed,pages=inventory(config,feed_paths)
    # Completed probes remain separate immutable training sources.
    extra=[Path(x) for x in config.get('completed_history_sources',[])]
    if len(extra)>128:raise ContractError('COMPLETED_PROBE_SOURCE_BOUND')
    paths=list(dict.fromkeys(feed_paths+extra))
    if len(paths)!=len(feed_paths)+len(extra):raise ContractError('DUPLICATE_SOURCE_PATH')
    quotes={};prefixes=list(pages)
    for i,path in enumerate(paths):
        header,records,prefix=read_source(path)
        if header['period_sha256']!=config['period_sha256']:raise ContractError('CROSS_PERIOD_FEED')
        if i<len(sealed):
            if prefix!=sealed[i]:raise ContractError('SOURCE_ARCHIVE_PREFIX_CHANGED')
        else:prefixes.append(prefix)
        for q in records:
            if read_hints is not None:
                for stamp in (q['known_at_ms'],q['t']+300000):
                    if stamp>now_ms:
                        old_stamp=read_hints.get('next_input_ms')
                        read_hints['next_input_ms']=stamp if old_stamp is None else min(old_stamp,stamp)
            if q['known_at_ms']>now_ms:continue
            old=quotes.get(q['t'])
            if old and (Decimal(str(old['bid']))!=Decimal(str(q['bid'])) or Decimal(str(old['ask']))!=Decimal(str(q['ask']))):raise ContractError('CROSS_SEGMENT_PRICE_CONFLICT')
            if old is None or q['known_at_ms']<old['known_at_ms']:quotes[q['t']]=q
    ordered=[{**quotes[t],'bid':float(quotes[t]['bid']),'ask':float(quotes[t]['ask'])} for t in sorted(quotes)]
    if read_hints is not None:
        read_hints['complete']=True
    return ordered,prefixes


def mature_anchor_indices(ordered,sampled,now_ms,scope,reserved_row_ids):
 """Choose before feature construction; every selected row is checked again below."""
 from data import sample_identity,observed_receipt,MAX_GAP_MS,TTL_MS
 from . import resident_training_v3 as v3
 times=[q['t'] for q in ordered]
 gap_prefix=[0]
 for a,b in zip(ordered,ordered[1:]):
  gap_prefix.append(gap_prefix[-1]+int(b['t']-a['t']>MAX_GAP_MS))
 rejected=Counter();candidates=[];mature=0
 for i,anchor in enumerate(sampled):
  # Match data.label before the anchor cap; late inputs cannot become labels.
  if anchor['known_at_ms']>anchor['t']+60000:
   rejected['INPUT_AVAILABLE_TOO_LATE']+=1;continue
  due=anchor['t']+v3.PRIMARY_HORIZON_SECONDS*1000
  end=bisect_left(times,due);start=bisect_left(times,anchor['t'])
  if due>now_ms or end==len(ordered):
   rejected['OUTCOME_NOT_MATURE_OR_MISSING']+=1;continue
  outcome_quote=ordered[end]
  if outcome_quote['t']>due+TTL_MS or outcome_quote['known_at_ms']>now_ms:
   rejected['OUTCOME_MISALIGNED_OR_NOT_KNOWN']+=1;continue
  if gap_prefix[end]!=gap_prefix[start]:
   rejected['PATH_GAP']+=1;continue
  history,reset=contiguous_history(ordered,anchor['known_at_ms'],end=start+1)
  if len(history)<6:
   rejected['INSUFFICIENT_CONTIGUOUS_INPUT_HISTORY']+=1;continue
  target=10000*(outcome_quote['mid']/anchor['mid']-1)/100
  if abs(target)>8:
   rejected['RETURN_OUT_OF_PROFILE']+=1;continue
  source_id=digest(sample_identity(history,scope,'CAUSAL_QUOTE_WINDOW'))
  outcome=observed_receipt(outcome_quote,v3.PRIMARY_HORIZON_SECONDS,target)
  key=digest([source_id,v3.PRIMARY_HORIZON_SECONDS,digest(outcome)])
  mature+=1
  if key in reserved_row_ids:
   rejected['ALREADY_RESERVED_PRIMARY_ROW']+=1;continue
  candidates.append((i,anchor['t'],key))
 selected=v3.cover_time_buckets(candidates,v3.POOL_ANCHORS,lambda item:item[1])
 stats={'policy':v3.SELECTION,'primary_horizon_seconds':v3.PRIMARY_HORIZON_SECONDS,
  'chronological_bucket_ms':v3.POOL_BUCKET_MS,'anchor_cap':v3.POOL_ANCHORS,
  'mature_primary_anchors_before_reservations':mature,
  'unreserved_mature_primary_anchors_before_cap':len(candidates),
  'selected_primary_anchors':len(selected),
  'first_eligible_anchor_ms':candidates[0][1] if candidates else None,
  'last_eligible_anchor_ms':candidates[-1][1] if candidates else None,
  'first_selected_anchor_ms':selected[0][1] if selected else None,
  'last_selected_anchor_ms':selected[-1][1] if selected else None,
  'reserved_row_ids_digest':digest(sorted(reserved_row_ids)),
  'exclusion_counts':dict(sorted(rejected.items()))}
 return {i:key for i,_,key in selected},stats


def collect(config,now_ms,*,reserved_row_ids=()):
 from data import make_sample,label
 from . import resident_training_v3 as v3
 from . import resident_training_v4 as v4
 from . import resident_training_v5 as v5
 from .resident_row_accounting import row_id
 use_v3=v3.enabled(config.get('objective')) or v4.enabled(config.get('objective')) or v5.enabled(config.get('objective'))
 if v4.enabled(config.get('objective')):v4.check_objective(config['objective'])
 if v5.enabled(config.get('objective')):v5.check_objective(config['objective'])
 reserved=set(reserved_row_ids)
 if any(not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key) for key in reserved):
  raise ContractError('TRAINING_RESERVED_ROW_IDENTITY')
 clock_hints={}
 ordered,prefixes=(read_quotes(config,now_ms,read_hints=clock_hints) if use_v3 else read_quotes(config,now_ms))
 times=[q['t'] for q in ordered];exclusions=[];scope=config['period_sha256']+':EUR.USD'
 sampled=[]
 for q in ordered:
  if not sampled or q['t']-sampled[-1]['t']>=10000:sampled.append(q)
 pool=None
 if use_v3:
  selected,pool=mature_anchor_indices(ordered,sampled,now_ms,scope,reserved)
  indices=list(selected)
 else:
  indices=range(max(5,len(sampled)-512),len(sampled))
 samples=[]
 for i in indices:
  anchor=sampled[i]
  history,reset=contiguous_history(ordered,anchor['known_at_ms'],end=bisect_right(times,anchor['t']))
  if len(history)<6:
   exclusions.append({'anchor_ms':anchor['t'],'reason':'INSUFFICIENT_CONTIGUOUS_INPUT_HISTORY'})
   continue
  s=make_sample(history,anchor['known_at_ms'],scope,'CAUSAL_QUOTE_WINDOW')
  s['history_selection_version']='CONTIGUOUS_SELECTED_SUFFIX_V2'
  s['contiguous_history_reset_at_gap']=reset
  s=label(s,ordered,times,now_ms)
  if use_v3:
   outcome=s['outcome_receipts'][0]
   if (s['label_reasons'][0]!='OBSERVED' or outcome is None
       or digest([s['sample_id'],300,digest(outcome)])!=selected[i]):
    raise ContractError('TRAINING_MATURE_POOL_ROW_IDENTITY')
  samples.append(s)
 samples=samples[-512:]
 seed=read_json(Path(config['seed_dataset']),64*1024**2)
 if sha_file(Path(config['seed_dataset']))!=config['seed_dataset_sha256']:raise ContractError('SEED_DATASET_CHANGED')
 if seed.get('scope')!=scope or seed.get('source_kind')!='REAL_PAPER_RUNTIME':raise ContractError('SEED_SCOPE')
 combined={s['sample_id']:s for s in seed['samples']}
 combined.update({s['sample_id']:s for s in samples})
 all_samples=sorted(combined.values(),key=lambda s:(s['anchor_ms'],s['sample_id']))
 dataset={'schema':'heptatrader.neuron-dataset.v1','source_kind':'REAL_PAPER_RUNTIME','instrument':'EUR.USD',
  'scope':scope,'frozen_at_ms':now_ms,'samples':all_samples,
  'source_prefixes':seed['source_prefixes']+prefixes,'exclusions':exclusions,
  'source_policy':'IMMUTABLE_REAL_PREFIXES_NO_MODEL_LABELS','broker_orders_sent':0}
 window={'training_end_ms':now_ms,'validation_start_ms':now_ms+1,'validation_end_ms':now_ms+86400000}
 if use_v3:
  eligible,_=rows_for_training(dataset,window)
  allowed={r['sample_id'] for r in eligible if r['horizon_sec']==300 and row_id(r) not in reserved}
  dataset['samples']=v3.cover_time_buckets([s for s in all_samples if s['sample_id'] in allowed],768,lambda s:s['anchor_ms'])
  dataset['training_pool']=pool
 else:
  dataset['samples']=all_samples[-768:]
 rows,invalid=rows_for_training(dataset,window)
 if use_v3:rows=v3.primary_rows(rows)
 fingerprint=digest([{'sample':r['sample_id'],'horizon':r['horizon_sec'],'outcome':r['outcome_receipt_digest']} for r in rows])
 quality={'fingerprint':fingerprint,'mature_rows':len(rows),'new_source_quotes':len(ordered),
  'latest_quote_ms':times[-1] if times else None,'exclusion_counts':dict(Counter(x['reason'] for x in invalid)),
  'window':window}
 if use_v3:quality['training_pool']=pool
 from .resident_input_wait import next_timestamp_ms
 seed_next=next_timestamp_ms(seed['samples'],now_ms) if use_v3 else None
 future_times=[stamp for stamp in (clock_hints.get('next_input_ms'),seed_next) if stamp is not None]
 quality['training_input_clock_complete']=bool(use_v3 and clock_hints.get('complete'))
 quality['next_training_input_ms']=min(future_times) if future_times else None
 return dataset,rows,quality,ordered


def contiguous_history(quotes,now_ms,*,end=None):
 """Use only the latest contiguous causal suffix; never cross a recorded gap."""
 chosen=[];previous=None;reset=False
 for i in range((len(quotes) if end is None else end)-1,-1,-1):
  q=quotes[i]
  if q['known_at_ms']>now_ms or q['t']>now_ms:continue
  if previous is not None and previous-q['t']>30000:
   reset=True;break
  previous=q['t']
  if chosen and chosen[-1]['t']-q['t']>30000:
   # Raw quotes may be continuous while 10s thinning creates a >30s selected
   # gap. Keep only the supported recent suffix; never expand the gap limit,
   # interpolate a point, duplicate a quote, or rewrite its time.
   reset=True;break
  if not chosen or chosen[-1]['t']-q['t']>=10000:chosen.append(q)
  if len(chosen)>=24:break
 return list(reversed(chosen)),reset


def forecast_input(config,quotes,now_ms):
 from data import make_sample
 from .dataset import causal_state,question
 history,reset=contiguous_history(quotes,now_ms)
 history=[{**q,"bid":float(q["bid"]),"ask":float(q["ask"])} for q in history]
 if len(history)<6:raise ContractError('INSUFFICIENT_CONTIGUOUS_INPUT_HISTORY')
 if not 0<=now_ms-history[-1]['t']<=5000:raise ContractError('CURRENT_SHADOW_INPUT_NOT_FRESH')
 if any(b['t']-a['t']>30000 for a,b in zip(history,history[1:])):raise ContractError('SHADOW_HISTORY_GAP')
 sample=make_sample(history,now_ms,config['period_sha256']+':EUR.USD','CAUSAL_QUOTE_WINDOW')
 sample['contiguous_history_reset_at_gap']=reset
 sample['history_selection_version']='CONTIGUOUS_SELECTED_SUFFIX_V2'
 return sample,{'state':causal_state(sample,'EUR.USD',300),'question':question(300)}
