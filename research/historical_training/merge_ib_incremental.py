"""Join two independently proven-complete IB intervals with exact overlap.

Never silently replace revised prices, change the prior data set, infer missing
bars, or credit this retrospective history as prospective efficacy.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once
from .dataset import examples,split

def merge(source_a,source_b):
 a=read(source_a/'paired-bars.json');b=read(source_b/'paired-bars.json')
 ac=read(source_a/'coverage.json');bc=read(source_b/'coverage.json')
 for d,c,p in ((a,ac,source_a),(b,bc,source_b)):
  if d.get('schema')!='heptatrader.historical-paired-bars.v1' or d.get('actual_execution_rewards')!=0 or d.get('trade_authority') is not False:
   raise EvidenceError('RESEARCH_HISTORY_SCHEMA')
  if c.get('full_requested_coverage_proven') is not True or c.get('scheduled_missing_count')!=0 or c.get('unpaired_timestamps')!=0:
   raise EvidenceError('SOURCE_WINDOW_NOT_COMPLETE')
  if d.get('coverage')!=c:raise EvidenceError('SOURCE_COVERAGE_NOT_PINNED')
  bars=d['bars']
  if len(bars)!=c['bar_count'] or len(set(x['time'] for x in bars))!=len(bars) or any(x['time']>=y['time'] for x,y in zip(bars,bars[1:])):
   raise EvidenceError('SOURCE_TIME_IDENTITY')
 forward=ac['requested_start_utc']<bc['requested_start_utc']<=ac['requested_end_utc']<bc['requested_end_utc']
 backward=bc['requested_start_utc']<ac['requested_start_utc']<=bc['requested_end_utc']<ac['requested_end_utc']
 if not (forward or backward):raise EvidenceError('UNJOINABLE_CALENDAR_INTERVALS')
 direction='FORWARD_APPEND' if forward else 'BACKWARD_PREPEND'
 ia={x['time']:x for x in a['bars']};ib={x['time']:x for x in b['bars']}
 overlap=sorted(set(ia)&set(ib));added=sorted(set(ib)-set(ia))
 if (len(overlap)<1 or not added or
         (forward and min(added)<=max(ia)) or
         (backward and max(added)>=min(ia))):
  raise EvidenceError('HISTORICAL_INCREMENT_NOT_OUTSIDE_PROVEN_COVERAGE')
 for t in overlap:
  if ia[t]!=ib[t]:raise EvidenceError('HISTORICAL_OVERLAP_REVISED_NO_SILENT_MERGE:'+str(t))
 allbars=[ia.get(t,ib.get(t)) for t in sorted(set(ia)|set(ib))]
 coverage={
  'schema':'heptatrader.historical-coverage.v1',
  'requested_start_utc':min(ac['requested_start_utc'],bc['requested_start_utc']),
  'requested_end_utc':max(ac['requested_end_utc'],bc['requested_end_utc']),
  'actual_first_utc':min(ac['actual_first_utc'],bc['actual_first_utc']),
  'actual_last_utc':max(ac['actual_last_utc'],bc['actual_last_utc']),
  'bar_count':len(allbars),'scheduled_bars':len(allbars),'scheduled_missing_count':0,
  'calendar_response_interval_complete':True,'full_requested_coverage_proven':True,
  'counts_by_kind':{kind:len(allbars) for kind in ('BID','ASK','MIDPOINT')},
  'crossed_open_or_close':0,'unpaired_timestamps':0,'gap_fill_performed':False,
  'calendar_coverage_rule':'UNION_OF_TWO_PROVEN_COMPLETE_INTERVALS_EXACT_BYTE_IDENTICAL_OVERLAP',
  'composite_overlap_exact_rows':len(overlap),'composite_incremental_rows':len(added),
  'revision_conflicts':0,'merge_direction':direction,'revised_historical_data_not_contemporaneous':True,
  'source_windows':[{'path':str(p),'paired_sha256':sha(p/'paired-bars.json'),
    'coverage_sha256':sha(p/'coverage.json'),'requested_start_utc':c['requested_start_utc'],
    'requested_end_utc':c['requested_end_utc'],'bars':c['bar_count']}
    for p,c in ((source_a,ac),(source_b,bc))],
  'research_only':True}
 dataset={'schema':'heptatrader.historical-paired-bars.v1',
          'bars':allbars,'coverage':coverage,'sources':coverage['source_windows'],
          'trade_authority':False,'actual_execution_rewards':0,
          'live_evaluation_repaired':False,
          'research_only_revised_not_contemporaneous':True}
 dataset['dataset_id']=digest(dataset)
 proof={'schema':'heptatrader.historical-exact-overlap-merge.v1',
        'new_dataset_id':dataset['dataset_id'],'merge_direction':direction,
        'old_bars':len(ia),'new_bars_from_increment':len(added),
        'overlap_bars_identical':len(overlap),
        'new_total_bars':len(allbars),'all_input_intervals_proven':True,
        'revision_conflicts':0,'broker_orders_sent':0,
        'source_hashes':coverage['source_windows'],
        'gap_fill_performed':False}
 return dataset,proof

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--old',type=Path,required=True);p.add_argument('--increment',type=Path,required=True)
 p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 d,proof=merge(a.old,a.increment)
 rows,excluded=examples(d['bars']);groups,bounds=split(rows)
 from .common import digest
 e={'schema':'heptatrader.offline-price-examples.v1','lookback_bars':12,'horizon_bars':3,
    'bar_seconds':300,'boundaries':bounds,'exclusions':excluded,'groups':groups,
    'actual_execution_rewards':0,'trade_authority':False}
 a.output.mkdir(parents=True,mode=0o700,exist_ok=False)
 write_once(a.output/'paired-bars.json',d)
 e['paired_bars_sha256']=sha(a.output/'paired-bars.json');e['dataset_id']=digest(e)
 write_once(a.output/'examples.json',e);write_once(a.output/'coverage.json',d['coverage'])
 proof['output_hashes']={n:sha(a.output/n) for n in ('paired-bars.json','examples.json','coverage.json')}
 write_once(a.output/'merge-proof.json',proof)
 print(json.dumps({'bar_count':len(d['bars']),'exact_overlap':proof['overlap_bars_identical'],
   'new_bars':proof['new_bars_from_increment'],'full_requested_coverage_proven':True,
   'new_dataset_id':d['dataset_id']}))
if __name__=='__main__':main()
