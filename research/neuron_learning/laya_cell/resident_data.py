"""Append-only feed prefixes -> mature frozen Laya datasets, separate from orders."""
from __future__ import annotations
from collections import Counter
from bisect import bisect_right
from decimal import Decimal
from pathlib import Path
import json,time
from .integrity import ContractError,digest,sha_file,read_json
from .shadow_source import read_source
from .dataset import rows_for_training


def read_quotes(config,now_ms,*,recent_only=False):
 """Read verified quote sources. The default always retains full history.

 Recent-only is confined to prospective *input selection*, never training
 outcomes or historical qualification. Each archived byte is still SHA256
 verified against the complete checkpoint ledger by inventory(). Archived
 sources were chain-validated before sealing; live/unsealed sources are
 fully chain-validated on every read. No stale source is made fresh.
 """
 if sha_file(Path(config['period_path']))!=config['period_sha256']:raise ContractError('RESIDENT_PERIOD_CHANGED')
 from .source_archive import inventory
 feed_paths=sorted(Path(config['feed_directory']).glob('segment-*/quotes.jsonl'))
 sealed,pages=inventory(config,feed_paths)
 extra=[Path(x) for x in config.get('completed_history_sources',[])]
 if len(extra)>128:raise ContractError('COMPLETED_PROBE_SOURCE_BOUND')
 paths=list(dict.fromkeys(feed_paths+extra))
 if len(paths)!=len(feed_paths)+len(extra):raise ContractError('DUPLICATE_SOURCE_PATH')
 # Historical probes are research sources, not a real-time shortcut.
 if extra:recent_only=False
 quotes={};prefixes=list(pages)
 def include(i):
  path=paths[i]
  header,records,prefix=read_source(path)
  if header['period_sha256']!=config['period_sha256']:raise ContractError('CROSS_PERIOD_FEED')
  if i<len(sealed):
   if prefix!=sealed[i]:raise ContractError('SOURCE_ARCHIVE_PREFIX_CHANGED')
  else:prefixes.append(prefix)
  for q in records:
   if q['known_at_ms']>now_ms:continue
   old=quotes.get(q['t'])
   if old and (Decimal(str(old['bid']))!=Decimal(str(q['bid'])) or Decimal(str(old['ask']))!=Decimal(str(q['ask']))):
    raise ContractError('CROSS_SEGMENT_PRICE_CONFLICT')
   if old is None or q['known_at_ms']<old['known_at_ms']:quotes[q['t']]=q
 def ordered():
  return [{**quotes[t],'bid':float(quotes[t]['bid']),'ask':float(quotes[t]['ask'])} for t in sorted(quotes)]
 if recent_only:
  # Unsealed tail has no archive pin, so validate every byte and chain.
  for i in range(len(sealed),len(paths)):include(i)
  # A just-sealed tail may contain the only usable causal input. Extend
  # backwards only until the original 24-point selector is complete or
  # reaches a real gap; never interpolate or cross an archived gap.
  for i in range(len(sealed)-1,-1,-1):
   history,reset=contiguous_history(ordered(),now_ms)
   if len(history)>=24 or reset:break
   include(i)
 else:
  for i in range(len(paths)):include(i)
 return ordered(),prefixes


def collect(config,now_ms):
 from data import make_sample,label
 ordered,prefixes=read_quotes(config,now_ms)
 times=[q['t'] for q in ordered];exclusions=[];scope=config['period_sha256']+':EUR.USD'
 sampled=[]
 for q in ordered:
  if not sampled or q['t']-sampled[-1]['t']>=10000:sampled.append(q)
 samples=[]
 for i in range(max(5,len(sampled)-512),len(sampled)):
  anchor=sampled[i]
  # Training and forward inference share one causal history selector. Later
  # arrivals and timestamps cannot repair an earlier unavailable input.
  history,reset=contiguous_history(ordered[:bisect_right(times,anchor['t'])],anchor['known_at_ms'])
  if len(history)<6:
   exclusions.append({'anchor_ms':anchor['t'],'reason':'INSUFFICIENT_CONTIGUOUS_INPUT_HISTORY'})
   continue
  s=make_sample(history,anchor['known_at_ms'],scope,'CAUSAL_QUOTE_WINDOW')
  s['history_selection_version']='CONTIGUOUS_SELECTED_SUFFIX_V2'
  s['contiguous_history_reset_at_gap']=reset
  samples.append(label(s,ordered,times,now_ms))
 samples=samples[-512:]
 seed=read_json(Path(config['seed_dataset']),64*1024**2)
 if sha_file(Path(config['seed_dataset']))!=config['seed_dataset_sha256']:raise ContractError('SEED_DATASET_CHANGED')
 if seed.get('scope')!=scope or seed.get('source_kind')!='REAL_PAPER_RUNTIME':raise ContractError('SEED_SCOPE')
 # Source prefixes for historical evidence remain verified by the normal training loader.
 combined={s['sample_id']:s for s in seed['samples']}
 combined.update({s['sample_id']:s for s in samples})
 all_samples=sorted(combined.values(),key=lambda s:(s['anchor_ms'],s['sample_id']))[-768:]
 dataset={'schema':'heptatrader.neuron-dataset.v1','source_kind':'REAL_PAPER_RUNTIME','instrument':'EUR.USD',
  'scope':scope,'frozen_at_ms':now_ms,'samples':all_samples,
  'source_prefixes':seed['source_prefixes']+prefixes,'exclusions':exclusions,
  'source_policy':'IMMUTABLE_REAL_PREFIXES_NO_MODEL_LABELS','broker_orders_sent':0}
 window={'training_end_ms':now_ms,'validation_start_ms':now_ms+1,'validation_end_ms':now_ms+86400000}
 rows,invalid=rows_for_training(dataset,window)
 fingerprint=digest([{'sample':r['sample_id'],'horizon':r['horizon_sec'],'outcome':r['outcome_receipt_digest']} for r in rows])
 return dataset,rows,{'fingerprint':fingerprint,'mature_rows':len(rows),'new_source_quotes':len(ordered),
  'latest_quote_ms':times[-1] if times else None,'exclusion_counts':dict(Counter(x['reason'] for x in invalid)),
  'window':window},ordered


def contiguous_history(quotes,now_ms):
 """Use only the latest contiguous causal suffix; never cross a recorded gap."""
 chosen=[];previous=None;reset=False
 for q in reversed(quotes):
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
