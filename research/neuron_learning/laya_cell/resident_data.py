"""Append-only feed prefixes -> mature frozen Laya datasets, separate from orders."""
from __future__ import annotations
from collections import Counter
from decimal import Decimal
from pathlib import Path
import json,time
from .integrity import ContractError,digest,sha_file,read_json
from .shadow_source import read_source
from .dataset import rows_for_training


def collect(config,now_ms):
 from data import make_sample,label
 if sha_file(Path(config['period_path']))!=config['period_sha256']:raise ContractError('RESIDENT_PERIOD_CHANGED')
 paths=sorted(Path(config['feed_directory']).glob('segment-*/quotes.jsonl'))
 # Completed historical probes are training material only for later candidates,
 # never retrospective acceptance of the model that produced those probes.
 paths += [Path(x) for x in config.get('completed_history_sources',[])]
 paths=list(dict.fromkeys(paths))
 if len(paths)>128:raise ContractError('RESIDENT_SEGMENT_BOUND_NO_HISTORY_DELETION')
 quotes={};prefixes=[];exclusions=[];scope=config['period_sha256']+':EUR.USD'
 for path in paths:
  header,records,prefix=read_source(path)
  if header['period_sha256']!=config['period_sha256']:raise ContractError('CROSS_PERIOD_FEED')
  prefixes.append(prefix)
  for q in records:
   if q['known_at_ms']>now_ms:continue
   old=quotes.get(q['t'])
   if old and (Decimal(str(old['bid']))!=Decimal(str(q['bid'])) or Decimal(str(old['ask']))!=Decimal(str(q['ask']))):
    raise ContractError('CROSS_SEGMENT_PRICE_CONFLICT')
   if old is None or q['known_at_ms']<old['known_at_ms']:quotes[q['t']]=q
 ordered=[quotes[t] for t in sorted(quotes)]
 times=[q['t'] for q in ordered]
 sampled=[]
 for q in ordered:
  if not sampled or q['t']-sampled[-1]['t']>=10000:sampled.append(q)
 samples=[]
 for i in range(5,len(sampled)):
  history=sampled[max(0,i-23):i+1]
  s=make_sample(history,history[-1]['known_at_ms'],scope,'CAUSAL_QUOTE_WINDOW')
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


def forecast_input(config,quotes,now_ms):
 from data import make_sample
 from .dataset import causal_state,question
 chosen=[]
 for q in reversed(quotes):
  if q['known_at_ms']<=now_ms and (not chosen or chosen[-1]['t']-q['t']>=10000):chosen.append(q)
 history=list(reversed(chosen[:24]))
 if len(history)<6 or now_ms-history[-1]['t']>5000:raise ContractError('CURRENT_SHADOW_INPUT_NOT_FRESH')
 if any(b['t']-a['t']>30000 for a,b in zip(history,history[1:])):raise ContractError('SHADOW_HISTORY_GAP')
 sample=make_sample(history,now_ms,config['period_sha256']+':EUR.USD','CAUSAL_QUOTE_WINDOW')
 return sample,{'state':causal_state(sample,'EUR.USD',300),'question':question(300)}
