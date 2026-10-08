"""Read-only operational quote readiness, NOT a model-effectiveness window.

Fixed UTC-minute probes test the original 5s quote/endpoint and 30s path rules.
No quotes, times, outcomes or old experimental verdicts are rewritten. A READY
state permits only operator review; it never registers a trial or submits orders.
"""
from __future__ import annotations
import argparse,json,os,signal,time
from bisect import bisect_left,bisect_right
from collections import Counter
from pathlib import Path
from .integrity import read_json,sha_file,ContractError,digest
from .resident_data import read_quotes,forecast_input
from .resident_store import atomic

RULE={'schema':'heptatrader.quote-readiness.v1','input_probes':24,'spacing_ms':60000,'horizon_ms':300000,
      'input_grace_ms':60000,'endpoint_tolerance_ms':5000,'path_gap_max_ms':30000,
      'minimum_usable':20,'maximum_missing':4,'source_ttl_ms':5000,'statistics_scope':'OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE'}

def probe(quotes,start,config):
 # Input chooses the first actually knowable usable quote in this fixed minute.
 # No future outcome is consulted to choose the anchor.
 anchors=sorted((q for q in quotes if start<=q['known_at_ms']<=start+RULE['input_grace_ms'] and q['t']>=start),key=lambda q:(q['known_at_ms'],q['t']))
 failures=Counter();sample=None
 for q in anchors:
  at=q['known_at_ms'];available=[x for x in quotes if x['known_at_ms']<=at and x['t']<=at]
  try:
   candidate,_=forecast_input(config,available,at)
   if candidate['anchor_ms']<start:continue
   sample=candidate;break
  except ContractError as exc:failures[str(exc)]+=1
 if sample is None:return {'start_ms':start,'status':'INPUT_UNAVAILABLE','input_failures':dict(failures)}
 anchor=sample['anchor_ms'];due=anchor+RULE['horizon_ms'];times=[q['t'] for q in quotes]
 a=bisect_left(times,anchor);b=bisect_left(times,due)
 result={'start_ms':start,'anchor_ms':anchor,'due_ms':due,'history_points':len(sample['input_snapshot']['quotes']),
         'history_max_gap_ms':max(y['t']-x['t'] for x,y in zip(sample['input_snapshot']['quotes'],sample['input_snapshot']['quotes'][1:]))}
 if b>=len(times):return {**result,'status':'OUTCOME_UNAVAILABLE'}
 if times[b]>due+RULE['endpoint_tolerance_ms']:return {**result,'status':'ENDPOINT_GAP','actual_delay_ms':times[b]-due}
 if any(y-x>RULE['path_gap_max_ms'] for x,y in zip(times[a:b],times[a+1:b+1])):return {**result,'status':'PATH_GAP'}
 return {**result,'status':'USABLE','actual_endpoint_ms':times[b]}

def assess(config,now=None):
 now=int(time.time()*1000) if now is None else now
 quotes,prefixes=read_quotes(config,now)
 # Latest probe must be fully mature even for a last-grace-second input.
 last=((now-RULE['horizon_ms']-RULE['input_grace_ms']-RULE['endpoint_tolerance_ms'])//RULE['spacing_ms'])*RULE['spacing_ms']
 starts=[last-i*RULE['spacing_ms'] for i in reversed(range(RULE['input_probes']))]
 cutoff=starts[0]-600000;recent=[q for q in quotes if q['t']>=cutoff]
 results=[probe(recent,start,config) for start in starts]
 counts=Counter(x['status'] for x in results);reasons=[]
 if not recent or recent[0]['t']>starts[0]-50000:reasons.append('INSUFFICIENT_COMPLETE_HISTORY_SPAN')
 age=now-quotes[-1]['t'] if quotes else None
 if age is None or not 0<=age<=RULE['source_ttl_ms']:reasons.append('LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH')
 if counts['USABLE']<RULE['minimum_usable']:reasons.append('INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE')
 total=RULE['input_probes']
 return {'schema':'heptatrader.quote-readiness-status.v1','at_ms':now,'period_sha256':config['period_sha256'],
  'state':'HOLD_NEW_FUSION_WINDOW' if reasons else 'READY_FOR_REVIEW_NOT_AUTHORIZATION',
  'reasons':reasons,'rule':RULE,'source_prefixes':prefixes,'source_prefix_digest':digest(prefixes),
  'probes':results,'counts':dict(counts),'usable':counts['USABLE'],'unusable':total-counts['USABLE'],
  'latest_source_quote_ms':quotes[-1]['t'] if quotes else None,'latest_quote_age_ms':age,
  'past_probe_labels_are_not_independent_efficacy_samples':True,'automatic_trial_registration':False,
  'trading_authority':False,'old_failed_windows_changed':False,'orders_sent':0}

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--sandbox',action='store_true');a=p.parse_args()
 os.umask(0o077);c=read_json(a.config);stop=False
 if a.sandbox:
  from sandbox import enter
  a.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  receipt=enter({'output_dir':str(a.output.parent),'period_path':c['period_path'],'legacy_capture':c['seed_dataset'],
   'oms_path':c['oms_path'],'export_dir':c['export_directory'],
   'read_only_paths':[c['feed_directory'],c['runtime_directory'],str(a.config)]})
  atomic(a.output.parent/'sandbox.json',receipt)
 def finish(*_):
  nonlocal stop
  stop=True
 signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
 while not stop:
  try:status=assess(c)
  except Exception as exc:status={'at_ms':int(time.time()*1000),'state':'HOLD_NEW_FUSION_WINDOW','reason':'SOURCE_UNAVAILABLE:'+str(exc)[:180],'orders_sent':0}
  atomic(a.output,status)
  if a.once:print(json.dumps({k:v for k,v in status.items() if k not in ('probes','source_prefixes')}),flush=True);break
  for _ in range(60):
   if stop:break
   time.sleep(1)
if __name__=='__main__':main()
