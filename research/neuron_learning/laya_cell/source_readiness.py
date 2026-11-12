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
 for x,y in zip(times[a:b],times[a+1:b+1]):
  if y-x>RULE['path_gap_max_ms']:
   # These original quote times belong to the outcome path, not input history.
   # assess() binds this diagnostic to the same verified source prefixes.
   return {**result,'status':'PATH_GAP',
           'outcome_path_first_gap':{'from_ms':x,'to_ms':y,'gap_ms':y-x}}
 return {**result,'status':'USABLE','actual_endpoint_ms':times[b]}

def probe_starts(now):
 # Latest probe must be fully mature even for a last-grace-second input.
 last=((now-RULE['horizon_ms']-RULE['input_grace_ms']-RULE['endpoint_tolerance_ms'])//RULE['spacing_ms'])*RULE['spacing_ms']
 return [last-i*RULE['spacing_ms'] for i in reversed(range(RULE['input_probes']))]

def readiness_cutoff_ms(now):
 return probe_starts(now)[0]-600000

def assess(config,now=None,*,quote_loader=None):
 now=int(time.time()*1000) if now is None else now
 quotes,prefixes=(quote_loader or read_quotes)(config,now)
 starts=probe_starts(now)
 cutoff=readiness_cutoff_ms(now);recent=[q for q in quotes if q['t']>=cutoff]
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

def next_monitor_deadline(cadence_seconds, assessment_started, assessment_finished):
 """Preserve 60s default; fast sandbox mode is start-to-start bounded."""
 if (type(cadence_seconds) is not int or not 10<=cadence_seconds<=60
     or assessment_finished<assessment_started):
  raise ContractError('READINESS_CADENCE_INVALID')
 if cadence_seconds==60:return assessment_finished+60
 return max(assessment_started+cadence_seconds,assessment_finished+1)

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--sandbox',action='store_true');p.add_argument('--refresh-seconds',type=int,default=60);p.add_argument('--max-cycles',type=int,default=0);p.add_argument('--verified-sealed-cache',action='store_true');p.add_argument('--stale-hold-refresh-seconds',type=int,default=0);p.add_argument('--original-proof-reverify-seconds',type=int,default=0);a=p.parse_args()
 if not 10<=a.refresh_seconds<=60 or not 0<=a.max_cycles<=24:p.error('READINESS_CADENCE_OR_CYCLE_LIMIT')
 if (a.refresh_seconds!=60 or a.max_cycles>0 or a.verified_sealed_cache or a.stale_hold_refresh_seconds or a.original_proof_reverify_seconds) and not a.sandbox:p.error('FASTER_OR_FINITE_OR_CACHE_MODE_REQUIRES_SANDBOX')
 if a.stale_hold_refresh_seconds and not 30<=a.stale_hold_refresh_seconds<=3600:p.error('STALE_HOLD_REVALIDATION_INTERVAL')
 if a.original_proof_reverify_seconds and (not a.verified_sealed_cache or not 60<=a.original_proof_reverify_seconds<=3600):p.error('ORIGINAL_PROOF_REQUIRES_CACHE_AND_BOUNDED_INTERVAL')
 os.umask(0o077);c=read_json(a.config);stop=False
 # Load the exact shared reader dependency before the existing sandbox.
 cache=None
 if a.verified_sealed_cache:
  from .verified_sealed_readiness_cache import VerifiedSealedReadinessCache
  cache=VerifiedSealedReadinessCache(original_proof_reverify_ms=a.original_proof_reverify_seconds*1000)
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
 heartbeat=None
 if a.stale_hold_refresh_seconds:
  from .readiness_heartbeat import StaleReadinessHeartbeat
  heartbeat=StaleReadinessHeartbeat(a.stale_hold_refresh_seconds)
 def full_assess(config,now=None):
  loader=(lambda c,at:cache.read_quotes(c,at,earliest_ms=readiness_cutoff_ms(at))) if cache is not None else None
  return assess(config,now,quote_loader=loader)
 cycles=0
 while not stop:
  assessment_started=time.monotonic()
  try:status=heartbeat.assess(c,full_assess) if heartbeat is not None else full_assess(c)
  except Exception as exc:
   # Report the same negative source contract consumed by the admission guard.
   # Detection time is not a successful source assessment or live quote time.
   status={'schema':'heptatrader.quote-readiness-status.v1','period_sha256':c.get('period_sha256'),
      'at_ms':int(time.time()*1000),'source_asof_ms':None,'state':'HOLD_NEW_FUSION_WINDOW',
      'reasons':['SOURCE_UNAVAILABLE'],'reason':'SOURCE_UNAVAILABLE:'+str(exc)[:180],
      'cached_result_may_grant_ready':False,'trading_authority':False,'orders_sent':0}
  finished=time.monotonic();published_ms=int(time.time()*1000)
  source_asof=status.get('source_asof_ms',status.get('at_ms'))
  latest=status.get('latest_source_quote_ms')
  status.update({'source_assessed_at_ms':source_asof,'published_at_ms':published_ms,
     'assessment_elapsed_ms':round((finished-assessment_started)*1000),
     'latest_quote_age_at_publish_ms':published_ms-latest if type(latest) is int else None})
  if cache is not None:status['readiness_cache']=cache.counters()
  atomic(a.output,status)
  cycles+=1
  if a.once:print(json.dumps({k:v for k,v in status.items() if k not in ('probes','source_prefixes')}),flush=True);break
  if a.refresh_seconds!=60:
   published_ms=int(time.time()*1000);original_ms=status.get('source_asof_ms',status.get('at_ms'))
   print(json.dumps({'schema':'heptatrader.source-readiness-cadence-lab.v1','cycle':cycles,
      'assess_ms':round((finished-assessment_started)*1000),
      'source_asof_ms':original_ms,'published_ms':published_ms,
       'assessment_kind':status.get('assessment_kind','FULL_SOURCE_ASSESSMENT'),'evaluation_asof_ms':status.get('at_ms'),
      'published_source_age_ms':(published_ms-original_ms if type(original_ms) is int else None),
      'state':status.get('state'),'usable':status.get('usable'),
      **({'reasons':status['reasons'],'reason':status['reason']} if 'reason' in status else {}),
      'trade_authority':False,'orders_sent':0}),flush=True)
  if a.max_cycles and cycles>=a.max_cycles:break
  deadline=next_monitor_deadline(a.refresh_seconds,assessment_started,finished)
  while not stop:
   left=deadline-time.monotonic()
   if left<=0:break
   time.sleep(min(1,left))
 if heartbeat is not None:
  print(json.dumps({'schema':'heptatrader.stale-readiness-heartbeat.v1','full_assessments':heartbeat.full_assessments,'hold_heartbeats':heartbeat.hold_heartbeats,'cached_ready_allowed':False,'orders_sent':0}),flush=True)
 if cache is not None:
  print(json.dumps({'schema':'heptatrader.readiness-verified-sealed-cache-counters.v1',
   **cache.counters()}),flush=True)
  cache.close()
if __name__=='__main__':main()
