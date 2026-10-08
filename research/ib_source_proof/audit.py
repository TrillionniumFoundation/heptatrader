"""Independent, read-only quality comparison of pinned native and direct API streams.

Each fixed one-second cell requires a *previously available* BID/ASK pair
not older than 5s. No future tick can be used, gaps cannot be interpolated,
and the broker-second stamp is retained separately from local callback time.
"""
from __future__ import annotations
import argparse,bisect,hashlib,json,math,os,time
from collections import Counter
from pathlib import Path
from .run import SCHEMA,canonical,digest,now_ms
from .wire import valid_pair

def load_direct(root):
 root=Path(root);path=root/'events.jsonl'
 raw=path.read_bytes()
 if len(raw)>64*1024*1024 or not raw.endswith(b'\n'):raise ValueError('UNCOMMITTED_OR_UNBOUNDED_SOURCE')
 lines=raw.splitlines();rows=[];previous='0'*64
 for i,line in enumerate(lines):
  def unique(items):
   d={}
   for k,v in items:
    if k in d:raise ValueError('DUPLICATE_EVIDENCE_KEY')
    d[k]=v
   return d
  d=json.loads(line,object_pairs_hook=unique)
  if d.get('schema')!=SCHEMA or d.get('seq')!=i or d.get('prev')!=previous or d.get('sha256')!=digest({k:v for k,v in d.items() if k!='sha256'}):
   raise ValueError('EVIDENCE_CHAIN_FAILED')
  previous=d['sha256'];rows.append(d)
 if len(rows)<2 or rows[0]['kind']!='HEADER' or rows[-1]['kind']!='END':raise ValueError('INCOMPLETE_FIXED_DURATION')
 end=rows[-1]['payload'];wire=end.get('wire_counts',{})
 if end.get('broker_orders_sent')!=0 or end.get('account_requests_sent')!=0 or wire.get('mutating_codes_sent')!=0 or wire.get('active_requests'):
  raise ValueError('UNPROVEN_READ_ONLY_SEND_BOUNDARY')
 if set(map(int,wire['counts'])) - {71,59,1,2,97,98}:raise ValueError('UNSAFE_OPCODE')
 return rows,{'sha256':hashlib.sha256(raw).hexdigest(),'records':len(rows),'end_state':end['state'],'outbound':wire}

def direct_quotes(rows):
 bid=ask=None;bt=at=0;quotes=[];reject=Counter();tbt_seen=set()
 for row in rows:
  d=row.get('payload',{});k=row['kind'];t=d.get('received_ms')
  if k=='TOP_BID' and type(t)is int and valid_pair(d.get('price'),d.get('price')):
   bid=d['price'];bt=t
  elif k=='TOP_ASK' and type(t)is int and valid_pair(d.get('price'),d.get('price')):
   ask=d['price'];at=t
  elif k=='TBT_BIDASK':
   if not d.get('valid_pair') or not d.get('independent_update'):
    reject['NONINDEPENDENT_OR_STALE_BROKER_TBT']+=1;continue
   if not valid_pair(d['bid'],d['ask']):raise ValueError('MARKED_VALID_NEGATIVE_PAIR')
   q={'t':d['broker_second']*1000,'known_at_ms':t,'bid':d['bid'],'ask':d['ask'],'source':'BROKER_TBT'}
   if not 0<=q['known_at_ms']-q['t']<=5000:raise ValueError('BROKER_TIME_WAS_RELABELLED')
   identity=(q['t'],q['bid'],q['ask'])
   if identity in tbt_seen:raise ValueError('DUPLICATE_TBT_DECLARED_INDEPENDENT')
   tbt_seen.add(identity);quotes.append(q)
  if k in ('TOP_BID','TOP_ASK'):
   if not valid_pair(bid,ask) or not 0<=t-min(bt,at)<=5000:
    reject['SINGLE_OR_STALE_TOP_LEG']+=1;continue
   q={'t':min(bt,at),'known_at_ms':t,'bid':bid,'ask':ask,'source':'CALLBACK_TOP_PAIR'}
   if not quotes or (quotes[-1]['bid'],quotes[-1]['ask'],quotes[-1]['known_at_ms'])!=(q['bid'],q['ask'],q['known_at_ms']):
    quotes.append(q)
 quotes.sort(key=lambda q:(q['known_at_ms'],q['t'],q['source']))
 return quotes,dict(reject)

def cells(quotes,start,end,step=1000):
 if not quotes:return {'count':max(0,(end-start)//step+1),'fresh':0,'stale':max(0,(end-start)//step+1)}
 observed=sorted(quotes,key=lambda q:(q['known_at_ms'],q['t']))
 seen=0;valid=0;points=0;last=[]
 for moment in range(start,end+1,step):
  while seen<len(observed) and observed[seen]['known_at_ms']<=moment:
   if observed[seen]['t']<=moment:last.append(observed[seen])
   seen+=1
  fresh=bool(last) and 0<=moment-last[-1]['t']<=5000
  valid+=int(fresh);points+=1
 return {'count':points,'fresh':valid,'stale':points-valid,'fraction':valid/points if points else None}

def audit(config,roots,operator_repo):
 from laya_cell.resident_data import read_quotes
 c=json.loads(Path(config).read_bytes());p=json.loads(Path(c['period_path']).read_bytes())
 if p['execution_domain']!='PAPER':raise ValueError('NO_LIVE_MODE')
 native,_=read_quotes(c,now_ms());items=[]
 for root in roots:
  rows,evidence=load_direct(root);start=rows[0]['at_ms'];end=rows[-1]['at_ms']
  direct,reject=direct_quotes(rows)
  # Use the same exact UTC second grid within a completed subscription.
  s=((start+999)//1000)*1000;e=(end//1000)*1000
  original=[q for q in native if s-5000<=q['known_at_ms']<=e]
  direct_valid=[q for q in direct if s-5000<=q['known_at_ms']<=e]
  item={'root':str(root),'started_ms':start,'ended_ms':end,'duration_ms':end-start,'chain':evidence,
    'callback_counts':dict(Counter(x['kind'] for x in rows)),'direct_fresh_pair_count':len(direct_valid),
    'direct_pair_rejections':reject,'existing_native_quote_count':len(original),
    'native_1s_grid':cells(original,s,e),'direct_1s_grid':cells(direct_valid,s,e),
    'distinct_api_client_receives_quotes':len(direct_valid)>0,'extra_broker_orders_sent':0,
    'source_is_eligible_to_replace_native':False}
  items.append(item)
 return {'schema':'heptatrader.read-only-independent-source-comparison.v1','as_of_ms':now_ms(),
  'mode':'PAPER','trials':items,'automatic_feed_source_replacement':False,
  'new_Fusion_window_registered':False,'zero_order_or_account_traffic_verified':True,
  'explicit_failure_if_independent_API_has_zero_bidask':any(not i['distinct_api_client_receives_quotes'] for i in items)}

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--root',type=Path,action='append',required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args()
 result=audit(a.config,a.root,Path.cwd());a.output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
 with a.output.open('xb') as f:f.write(canonical(result)+b'\n');f.flush();os.fsync(f.fileno())
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
