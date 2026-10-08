"""Independent expert fit and purged later gate rows from actual quote receipts."""
from __future__ import annotations
import time
from pathlib import Path
from collections import Counter
from laya_cell.resident_data import read_quotes
from laya_cell.dataset import rows_for_training
from laya_cell.integrity import ContractError,digest,canonical
from data import make_sample,label
from .common import PROFILE,require

def build(config,cutoff):
 quote_rows,prefixes=read_quotes(config,cutoff)
 times=[q['t'] for q in quote_rows];sampled=[]
 for q in quote_rows:
  if not sampled or q['t']-sampled[-1]['t']>=10000:sampled.append(q)
 samples=[]
 for i in range(max(5,len(sampled)-PROFILE['max_quote_samples']),len(sampled)):
  s=make_sample(sampled[max(0,i-23):i+1],sampled[i]['known_at_ms'],config['period_sha256']+':EUR.USD','CAUSAL_QUOTE_WINDOW')
  samples.append(label(s,quote_rows,times,cutoff))
 dataset={'schema':'heptatrader.neuron-dataset.v1','source_kind':'REAL_PAPER_RUNTIME','instrument':'EUR.USD',
  'scope':config['period_sha256']+':EUR.USD','samples':samples,'source_prefixes':prefixes}
 rows,errors=rows_for_training(dataset,{'training_end_ms':cutoff,'validation_start_ms':cutoff+1,'validation_end_ms':cutoff+86400000})
 sample_index={s['sample_id']:s for s in samples}
 complete=[]
 for row in rows:
  if row['horizon_sec']!=300:continue
  sample=sample_index[row['sample_id']]
  receipt=sample['outcome_receipts'][0]
  require(receipt and receipt['available_at_ms']<=cutoff,'LABEL_NOT_MATURE')
  complete.append({'row':row,'sample':sample,'last_outcome_known_ms':receipt['available_at_ms'],
   'label_identity':digest((row['sample_id'],row['outcome_receipt_digest'],row['horizon_sec']))})
 complete.sort(key=lambda x:(x['row']['anchor_ms'],x['label_identity']))
 gate=complete[-PROFILE['gate_train_rows']:]
 require(len(gate)==PROFILE['gate_train_rows'],'NOT_ENOUGH_SEPARATE_GATE_LABELS')
 cutoff_expert=gate[0]['row']['anchor_ms']-PROFILE['purge_ms']
 experts=[r for r in complete if r['last_outcome_known_ms']<cutoff_expert][-PROFILE['expert_train_rows']:]
 require(len(experts)==PROFILE['expert_train_rows'],'NOT_ENOUGH_PURGED_EXPERT_LABELS')
 expert_ids={r['label_identity'] for r in experts};gate_ids={r['label_identity'] for r in gate}
 require(not expert_ids&gate_ids,'OVERLAPPING_TRAINING_IDENTITIES')
 require(max(x['last_outcome_known_ms'] for x in experts)<min(x['row']['anchor_ms'] for x in gate),'TRAIN_LABEL_AFTER_GATE_INPUT')
 return {'schema':'heptatrader.price-complementarity-fit-dataset.v1','period_sha256':config['period_sha256'],
  'cutoff_ms':cutoff,'source_prefixes':prefixes,'source_quote_count':len(quote_rows),
  'mature_300s_rows':len(complete),'experts':experts,'gate':gate,'excluded':dict(Counter(x['reason'] for x in errors)),
  'expert_labels_before_gate_first_input':True,'reused_previous_fusion_rows_as_qualification':False,
  'automatic_trading':False}

def model_state_text(state):
 return canonical(state).decode()+'\nPredict EUR.USD five-minute MID return interval, not trading action or USD PnL.'
