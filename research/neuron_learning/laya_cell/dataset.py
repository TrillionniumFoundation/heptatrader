"""Existing immutable trading dataset to causal typed questions, never fake rewards."""
from __future__ import annotations
from bisect import bisect_right
from decimal import Decimal
import hashlib,math
from pathlib import Path
from .integrity import ContractError,canonical,digest,read_json
HORIZONS=(300,900)
CAUSAL_STATE_FIELDS=('instrument','anchor_ms','reference_mid','horizon_sec','columns','quotes','quantity')
BIN_EDGES_BPS=(-10.,-3.,-1.,1.,3.,10.)
BIN_TEXT=('return below -10 basis points','return at least -10 and below -3 basis points','return at least -3 and below -1 basis points','return at least -1 and below 1 basis point','return at least 1 and below 3 basis points','return at least 3 and below 10 basis points','return at least 10 basis points')
def finite(x):
 if isinstance(x,bool) or not isinstance(x,(int,float,str)):raise ContractError('NUMBER_REQUIRED')
 try:value=float(x)
 except (ValueError,TypeError):raise ContractError('INVALID_NUMBER') from None
 if not math.isfinite(value):raise ContractError('NONFINITE_NUMBER')
 return value
def integer(x):
 if type(x) is not int or x<0:raise ContractError('NONNEGATIVE_INTEGER_REQUIRED')
 return x
def verify_source_prefixes(dataset):
 sources=dataset.get('source_prefixes')
 if not isinstance(sources,list) or not sources:raise ContractError('REAL_SOURCE_PREFIXES_REQUIRED')
 from .source_archive import verify_archive_pin_sequence,verify_prefix,PIN_SCHEMA
 pages=[];post_page_raw=False
 for source in sources:
  if not isinstance(source,dict):raise ContractError('REAL_SOURCE_PREFIXES_REQUIRED')
  if source.get('schema')==PIN_SCHEMA:
   if post_page_raw:raise ContractError('SOURCE_ARCHIVE_PAGE_PROOF_DISCONTINUITY')
   pages.append(source)
  else:
   if pages:post_page_raw=True
   verify_prefix(source)
 if pages:
  # The archive is one ordered genesis-rooted range, never a selection of
  # independently valid pages. Historical seed prefixes may precede it;
  # unsealed current prefixes may follow it.
  verify_archive_pin_sequence(pages)
  scope=dataset.get('scope')
  if isinstance(scope,str) and len(scope)>=65 and scope[64]==':':
   if any(p['period_sha256']!=scope[:64] for p in pages):
    raise ContractError('SOURCE_ARCHIVE_DATASET_PERIOD_CHANGED')
def causal_state(sample,instrument,horizon):
 inp=sample['input_snapshot']
 if digest(inp)!=sample['sample_id']:raise ContractError('SAMPLE_INPUT_IDENTITY')
 history=inp['quotes']
 if not isinstance(history,list) or not 6<=len(history)<=24:raise ContractError('BOUNDED_QUOTE_HISTORY_REQUIRED')
 anchor=integer(sample['anchor_ms']);previous=-1;rows=[]
 for q in history:
  t=integer(q['t'])
  if t<=previous or t>anchor:raise ContractError('FUTURE_OR_UNORDERED_INPUT')
  previous=t;bid,ask=finite(q['bid']),finite(q['ask'])
  if not 0<bid<=ask:raise ContractError('QUOTE_PRICES_INVALID')
  rows.append([t-anchor,str(q['bid']),str(q['ask'])])
 if history[-1]['t']!=anchor:raise ContractError('ANCHOR_NOT_FINAL_INPUT')
 midpoint=(Decimal(str(history[-1]['bid']))+Decimal(str(history[-1]['ask'])))/2
 if abs(float(midpoint)-finite(sample['reference_mid']))>1e-12:raise ContractError('REFERENCE_MID_MISMATCH')
 return {'instrument':instrument,'anchor_ms':anchor,'reference_mid':str(midpoint),'horizon_sec':horizon,'columns':['offset_ms','bid','ask'],'quotes':rows,'quantity':'underlying MID return; not execution profit'}
def question(horizon):
 if horizon not in HORIZONS:raise ContractError('UNSUPPORTED_HORIZON')
 return {'t':'choice','ins':f'Which interval contains the underlying mid-price return {horizon} seconds after the anchor? One basis point is a fractional return of 0.0001. Predict the price outcome, not a trading action.','crit':{str(i):text for i,text in enumerate(BIN_TEXT)}}
def teacher_bins(sample,horizon,cutoff):
 t=sample.get('teacher')
 if not isinstance(t,dict) or t.get('distillation_allowed') is not True:return None
 f=t.get('forecast',{})
 if t.get('input_id')!=sample.get('source_input_id') or f.get('horizon_sec')!=horizon or f.get('issued_at_ms')!=sample['anchor_ms'] or not sample['anchor_ms']<=t.get('available_at_ms',-1)<=min(cutoff,sample['anchor_ms']+60000):return None
 if finite(f.get('reference_mid',0))!=finite(sample['reference_mid']):return None
 masses=[0.]*7;scenarios=f.get('scenarios')
 if not isinstance(scenarios,list) or not 2<=len(scenarios)<=7:return None
 for scenario in scenarios:
  p,m=finite(scenario['probability']),finite(scenario['move_bps'])
  if not 0<p<=1:return None
  masses[bisect_right(BIN_EDGES_BPS,m)]+=p
 if abs(sum(masses)-1)>1e-8:return None
 return masses
def rows_for_training(dataset,window):
 if dataset.get('schema')!='heptatrader.neuron-dataset.v1' or dataset.get('source_kind')!='REAL_PAPER_RUNTIME':raise ContractError('REAL_PAPER_DATASET_REQUIRED')
 cutoff=integer(window['training_end_ms']);start=integer(window['validation_start_ms']);end=integer(window['validation_end_ms'])
 if not cutoff<start<end:raise ContractError('FIXED_CHRONOLOGICAL_WINDOW_REQUIRED')
 if dataset.get('instrument') not in {'EUR.USD','BTC.USD'}:raise ContractError('INSTRUMENT_NOT_REGISTERED')
 rows=[];exclusions=[]
 for sample in dataset['samples']:
  anchor=integer(sample['anchor_ms'])
  for j,horizon in enumerate(HORIZONS):
   reason=sample['label_reasons'][j];outcome=sample['outcome_receipts'][j];value=sample['targets'][j];why=None
   if sample.get('scope')!=dataset.get('scope'):raise ContractError('CROSS_PERIOD_SAMPLE')
   if anchor+horizon*1000>cutoff:why='OUTCOME_OVERLAPS_RESERVED_FUTURE'
   elif sample.get('input_gap') or reason!='OBSERVED' or value is None or not isinstance(outcome,dict):why='MISSING_OR_GAPPED_OUTCOME'
   elif not anchor<=sample['known_at_ms']<=cutoff:why='INPUT_UNAVAILABLE_AT_TRAIN_CUTOFF'
   elif not anchor+horizon*1000<=outcome['observed_at_ms']<=anchor+horizon*1000+5000:why='OUTCOME_MISALIGNED'
   elif not outcome['observed_at_ms']<=outcome['available_at_ms']<=cutoff:why='OUTCOME_NOT_KNOWN_AT_TRAIN_CUTOFF'
   if why:exclusions.append({'sample_id':sample['sample_id'],'horizon_sec':horizon,'reason':why});continue
   value=finite(value)
   if abs(value)>8 or abs(value-finite(outcome['target_scaled']))>1e-12:raise ContractError('OUTCOME_VALUE_OR_UNITS_MISMATCH')
   state=causal_state(sample,dataset['instrument'],horizon)
   rows.append({'sample_id':sample['sample_id'],'horizon_sec':horizon,'anchor_ms':anchor,'state':state,'question':question(horizon),'target_class':bisect_right(BIN_EDGES_BPS,value*100),'outcome_receipt_digest':digest(outcome),'teacher_probs':teacher_bins(sample,horizon,cutoff)})
 chosen={}
 for row in rows:
  key=(row['anchor_ms'],row['horizon_sec'],digest(row['state']))
  if key not in chosen or row['teacher_probs'] is not None:chosen[key]=row
 return sorted(chosen.values(),key=lambda r:(r['anchor_ms'],r['horizon_sec'])),exclusions
def encode_rows(tokenizer,rows,max_len=1024,*,price_input_profile=None):
 from laya.common import build_sequence,collate_items
 items=[]
 for row in rows:
  # Upstream serializes insertion order; canonical JSON storage sorts keys.
  # Restore causal_state's original order without mutating the frozen input.
  state=row['state']
  if not isinstance(state,dict) or set(state)!=set(CAUSAL_STATE_FIELDS):raise ContractError('PRICE_MODEL_STATE_FIELDS_CHANGED')
  state={name:state[name] for name in CAUSAL_STATE_FIELDS}
  if price_input_profile is not None:
   from .resident_price_input import model_state
   state=model_state(state,price_input_profile)
  ids,markers,stats,truncation=build_sequence(tokenizer,state,row['question'],max_len=max_len,head_max_len=256,return_stats=True,return_truncation_stats=True)
  if len(markers)!=7 or stats['options_distinct']!=7 or truncation['truncated']:raise ContractError('SILENT_INPUT_OR_OPTIONS_TRUNCATION_FORBIDDEN')
  items.append([{'ids':ids,'markers':markers,'qtype':0}])
 batch=collate_items(items,tokenizer.pad_token_id)
 return {k:batch[k] for k in ('input_ids','attention_mask','marker_pos','marker_mask','qtype')}
