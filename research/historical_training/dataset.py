"""Verify immutable IB chunks, classify coverage, and build causal bar examples.
This is historical/revised price data, not a live quote or execution reward.
"""
from __future__ import annotations
import argparse,collections,json,math
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from .common import EvidenceError,read,sha,digest,write_once

FIELDS=('open','high','low','close')

def checked_bar(row):
    raw_time=row['time']
    if isinstance(raw_time,bool) or not (type(raw_time) is int or (isinstance(raw_time,str) and raw_time.isascii() and raw_time.isdecimal())):
        raise EvidenceError('NONINTEGER_BAR_TIME')
    t=int(raw_time);v={k:Decimal(str(row[k])) for k in FIELDS}
    if t<=0 or t%300 or not all(x.is_finite() and x>0 for x in v.values()):
        raise EvidenceError('BAR_TIME_OR_NUMERIC')
    if not v['low']<=min(v['open'],v['close'])<=max(v['open'],v['close'])<=v['high']:
        raise EvidenceError('BAR_OHLC')
    return t,{k:str(v[k]) for k in FIELDS}

def parse_session(value,zone):
    for fmt in ('%Y%m%d-%H:%M:%S','%Y%m%d %H:%M:%S','%Y%m%d:%H:%M:%S'):
        try:
            naive=datetime.strptime(value,fmt)
        except ValueError:
            continue
        name={'US/Eastern':'America/New_York','UTC':'Etc/UTC','GMT':'Etc/GMT'}.get(zone,zone)
        z=ZoneInfo(name);aware=naive.replace(tzinfo=z)
        if aware.utcoffset()!=aware.replace(fold=1).utcoffset():
            raise EvidenceError('AMBIGUOUS_OR_NONEXISTENT_SESSION_TIME')
        if aware.astimezone(timezone.utc).astimezone(z).replace(tzinfo=None)!=naive:
            raise EvidenceError('NONEXISTENT_SESSION_TIME')
        return int(aware.timestamp())
    raise EvidenceError('UNRECOGNIZED_BROKER_SESSION_TIME:'+value)

def interval_epoch(value):
    try:
        stamp=datetime.fromisoformat(value)
    except (TypeError,ValueError) as exc:
        raise EvidenceError('INVALID_REQUEST_INTERVAL') from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise EvidenceError('REQUEST_INTERVAL_TIMEZONE_REQUIRED')
    return stamp.timestamp()

def schedule_extent(schedule,request):
    """Prove the calendar RESPONSE covers the REQUEST, not just its bar subset."""
    if not schedule.get('start') or not schedule.get('end'):
        return None
    a=parse_session(schedule['start'],schedule['timezone'])
    b=parse_session(schedule['end'],schedule['timezone'])
    start=interval_epoch(request['requested_start_utc'])
    end=interval_epoch(request['requested_end_utc'])
    if b<=a or a>start or b<end:
        return None
    return (start,end)

def verified_probe_sessions(root, plan):
    """A RECEIVED chunk is not sufficient to salvage a failed demand probe.

    Admit new opt-in data only through successful immutable sessions that
    actually received each chunk. A later all-cache run cannot certify a
    response originating in a failed session. Legacy datasets are unchanged.
    """
    if not plan['config'].get('cash_hmds_probe_on_unknown', False):
        return []
    wanted = {digest({'contract': plan['config']['contract'], **p})
              for p in plan['requests']}
    proven, witnesses = set(), []
    for path in sorted((root / 'sessions').glob('*.json')):
        session = read(path)
        if (session.get('config_sha256') != digest(plan['config']) or
                session.get('paper_verified') is not True or session.get('exception')):
            continue
        farm = session.get('same_connection_cash_hmds', {})
        if (farm.get('fatal') is not False or farm.get('reconnects') != 0 or
                farm.get('unknown_probe_invalidated', False)):
            continue
        probe = farm.get('unknown_price_bar_probe')
        if session.get('startup_readiness') == 'UNKNOWN_PROBE_REQUIRED':
            if (not isinstance(probe, dict) or probe.get('completed') is not True or
                    probe.get('verified') is not True or type(probe.get('rows')) is not int or
                    probe['rows'] <= 0):
                continue
        if probe:
            if any(e.get('code') == 2105 for e in farm.get('notices', [])[probe['notice_count']:]):
                continue
            bound = False
            for key in wanted:
                receipt_path = root / 'chunks' / key / 'receipt.json'
                if not receipt_path.exists():
                    continue
                receipt = read(receipt_path)
                if (receipt.get('request_id') == probe.get('request_id') and
                        receipt.get('request', {}).get('kind') == 'bars' and
                        receipt.get('state') == 'RECEIVED' and
                        receipt.get('rows') == probe['rows'] and
                        receipt.get('response_sha256') == probe.get('response_sha256')):
                    bound = True
            if not bound:
                continue
        keys = {r['request_key'] for r in session.get('results', [])
                if r.get('state') == 'RECEIVED' and r.get('resumed') is False}
        if keys & wanted:
            proven.update(keys & wanted)
            witnesses.append({'path': str(path), 'sha256': sha(path)})
    if wanted - proven:
        raise EvidenceError('DEMAND_PROBE_SOURCE_SESSION_NOT_PROVEN')
    return witnesses

def collect(root):
    root=Path(root);plan=read(root/'plan.json');series={k:{} for k in ('MIDPOINT','BID','ASK')}
    source_sessions=verified_probe_sessions(root,plan)
    duplicate=collections.Counter();partial=collections.Counter();outside=collections.Counter();sources=[];schedules=[];errors=[];heads={};calendar_ranges=[];calendar_issues=[]
    target_start=min(interval_epoch(p['requested_start_utc']) for p in plan['requests'] if p['kind']=='bars')
    target_end=max(interval_epoch(p['requested_end_utc']) for p in plan['requests'] if p['kind']=='bars')
    wanted={digest({'contract':plan['config']['contract'],**p}):p for p in plan['requests']}
    for key,request in wanted.items():
        directory=root/'chunks'/key
        if not (directory/'receipt.json').exists():
            errors.append({'request_key':key,'state':'NOT_DOWNLOADED'});continue
        receipt=read(directory/'receipt.json');response=read(directory/'response.json')
        if receipt['request']!=request or receipt['response_sha256']!=sha(directory/'response.json'):
            raise EvidenceError('SOURCE_CHUNK_HASH_OR_PLAN')
        sources.append({'request_key':key,'receipt_sha256':sha(directory/'receipt.json'),
            'response_sha256':receipt['response_sha256'],'state':receipt['state']})
        if receipt['state']!='RECEIVED':
            errors.append({'request_key':key,'state':receipt['state'],'errors':response['errors']});continue
        kind=request['kind'];what=request['what']
        if kind=='head':heads[what]=response['head'];continue
        if kind=='schedule':
            schedule=response['schedule']
            if not schedule:raise EvidenceError('SCHEDULE_END_WITHOUT_PAYLOAD')
            extent=schedule_extent(schedule,request)
            if extent is None:calendar_issues.append({'request_key':key,'reason':'CALENDAR_RESPONSE_INTERVAL_NOT_PROVEN'})
            zone=schedule['timezone']
            # Partial per-request ranges may still cover the FULL target jointly.
            # Never discard valid calendar extent just because that one response
            # stops at a weekend boundary; never invent the missing tail either.
            if schedule.get('start') and schedule.get('end'):
                a=parse_session(schedule['start'],zone);b=parse_session(schedule['end'],zone)
                if b<=a:raise EvidenceError('CALENDAR_HEADER_ORDER')
                calendar_ranges.append((a,b))
            for s in schedule['sessions']:
                start=parse_session(s['start'],zone);end=parse_session(s['end'],zone)
                if end<=start:raise EvidenceError('SESSION_ORDER')
                schedules.append((start,end))
            continue
        previous=None
        for raw in response['bars']:
            t,row=checked_bar(raw)
            if previous is not None and t<=previous:raise EvidenceError('NONINCREASING_CHUNK')
            previous=t
            if t+300>interval_epoch(request['requested_end_utc']):
                partial[what]+=1;continue
            if t<target_start or t+300>target_end:
                outside[what]+=1;continue
            if t in series[what]:
                if series[what][t]!=row:raise EvidenceError('CONFLICTING_DUPLICATE_BAR')
                duplicate[what]+=1
            else:series[what][t]=row
    stamps=sorted(set.intersection(*(set(x) for x in series.values())))
    joined=[];crossed=[]
    for t in stamps:
        row={'time':t,**{kind.lower():series[kind][t] for kind in series}}
        if any(Decimal(row['bid'][field])>Decimal(row['ask'][field]) for field in ('open','close')):
            crossed.append(t);continue
        joined.append(row)
    if not joined:raise EvidenceError('NO_VALID_PAIRED_BARS')
    start=min(interval_epoch(p['requested_start_utc']) for p in plan['requests'] if p['kind']=='bars')
    end=max(interval_epoch(p['requested_end_utc']) for p in plan['requests'] if p['kind']=='bars')
    expected=set()
    for a,b in sorted(set(schedules)):
        expected.update(t for t in range(((a+299)//300)*300,b,300) if start<=t and t+300<=end)
    covered_until=start
    for a,b in sorted(calendar_ranges):
        if a>covered_until:break
        covered_until=max(covered_until,b)
    calendar_complete=covered_until>=end
    actual={r['time'] for r in joined};missing=sorted(expected-actual)
    all_times=sorted(set.union(*(set(x) for x in series.values())))
    unpaired=sorted(set(all_times)-set(stamps))
    gaps=[{'previous':a,'next':b,'seconds':b-a,
           'missing_expected_session_bars':sum(t in expected for t in range(a+300,b,300)),
           'classification':'BROKER_SCHEDULE_CLOSED' if expected and not any(t in expected for t in range(a+300,b,300)) else 'UNPROVEN_OR_SESSION_MISSING'}
          for a,b in zip(sorted(actual),sorted(actual)[1:]) if b-a!=300]
    coverage={'schema':'heptatrader.historical-coverage.v1','requested_start_utc':datetime.fromtimestamp(start,timezone.utc).isoformat(),
        'requested_end_utc':datetime.fromtimestamp(end,timezone.utc).isoformat(),
        'actual_first_utc':datetime.fromtimestamp(joined[0]['time'],timezone.utc).isoformat(),
        'actual_last_utc':datetime.fromtimestamp(joined[-1]['time'],timezone.utc).isoformat(),
        'bar_count':len(joined),'counts_by_kind':{k:len(v) for k,v in series.items()},
        'duplicate_equal_counts':dict(duplicate),'partial_endpoint_bars_excluded':dict(partial),
        'outside_target_bars_excluded':dict(outside),'coverage_scope':'FULLY_CLOSED_5MIN_BARS_IN_REQUESTED_INTERVAL','crossed_open_or_close':len(crossed),
        'unpaired_timestamps':len(unpaired),'broker_head_timestamps':heads,
        'schedule_sessions':len(set(schedules)),'scheduled_bars':len(expected),
        'calendar_response_interval_complete':calendar_complete,'calendar_response_issues':calendar_issues,
        'calendar_coverage_rule':'UNION_OF_ACTUAL_RETURNED_HEADER_INTERVALS_NO_ASSUMED_TAIL',
        'scheduled_missing_count':len(missing),'scheduled_missing_first_100':missing[:100],
        'bars_outside_returned_schedule':len(actual-expected) if expected else None,
        'gap_intervals':gaps,'request_errors':errors,'gap_fill_performed':False,
        'full_requested_coverage_proven':calendar_complete and bool(expected) and not missing and not errors and not unpaired and not crossed and not (actual-expected),
        'revised_historical_data_not_contemporaneous':True}
    dataset={'schema':'heptatrader.historical-paired-bars.v1','source_plan_sha256':sha(root/'plan.json'),
        'sources':sources,'bars':joined,'coverage':coverage,'trade_authority':False,
        'actual_execution_rewards':0,'live_evaluation_repaired':False}
    if source_sessions:
        dataset['source_sessions']=source_sessions
    dataset['dataset_id']=digest(dataset)
    return dataset

def examples(bars,lookback=12,horizon=3):
    if lookback!=12 or horizon!=3:raise EvidenceError('FIXED_OFFLINE_PROFILE')
    out=[];excluded=collections.Counter()
    for i in range(lookback-1,len(bars)-horizon-1):
        window=bars[i-lookback+1:i+horizon+2]
        if any(b['time']-a['time']!=300 for a,b in zip(window,window[1:])):
            excluded['INPUT_OR_OUTCOME_GAP']+=1;continue
        history=bars[i-lookback+1:i+1];entry=bars[i+1];exit=bars[i+1+horizon]
        mids=[float(r['midpoint']['close']) for r in history]
        latest=mids[-1]
        features=[(m/latest-1)*10000 for m in mids[:-1]]
        spreads=[(float(r['ask']['close'])-float(r['bid']['close']))/float(r['midpoint']['close'])*10000 for r in history]
        features+=spreads
        stamp=datetime.fromtimestamp(history[-1]['time']+300,timezone.utc)
        features+=[math.sin(stamp.hour*2*math.pi/24),math.cos(stamp.hour*2*math.pi/24)]
        reference=float(entry['midpoint']['open']);future=float(exit['midpoint']['open'])
        label=(future/reference-1)*10000
        long_bps=(float(exit['bid']['open'])-float(entry['ask']['open']))/reference*10000
        short_bps=(float(entry['bid']['open'])-float(exit['ask']['open']))/reference*10000
        text='EUR/USD historical five-minute bars. Forecast the next 15-minute midpoint return. Completed closes and bid-ask spreads, oldest first: '+ '; '.join(f'{v:.6f}/{s:.3f}bps' for v,s in zip(mids,spreads))
        out.append({'time':history[-1]['time']+300,'history_start':history[0]['time'],
            'outcome_end':exit['time']+300,'features':features,'feature_text':text,
            'target_bps':label,'class':0 if label<-1 else (2 if label>1 else 1),
            'long_after_spread_bps':long_bps,'short_after_spread_bps':short_bps,
            'price_model':'NEXT_BAR_OPEN_NOT_GUARANTEED_FILL','commission_roundtrip_bps':0.4,
            'real_execution_reward':False})
    return out,dict(excluded)

def split(rows):
    if len(rows)<100:raise EvidenceError('INSUFFICIENT_OFFLINE_EXAMPLES')
    a=rows[int(len(rows)*.6)]['time'];b=rows[int(len(rows)*.8)]['time']
    groups={'train':[],'validation':[],'test':[]};purged=0
    for row in rows:
        if row['outcome_end']<a:groups['train'].append(row)
        elif row['history_start']>=a and row['outcome_end']<b:groups['validation'].append(row)
        elif row['history_start']>=b:groups['test'].append(row)
        else:purged+=1
    return groups,{'train_validation_boundary':a,'validation_test_boundary':b,'purged_rows':purged,
        'rule':'CHRONOLOGICAL_60_20_20_PURGE_FULL_INPUT_AND_LABEL_SPAN'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    d=collect(a.source);write_once(a.output/'paired-bars.json',d)
    rows,excluded=examples(d['bars']);groups,boundaries=split(rows)
    payload={'schema':'heptatrader.offline-price-examples.v1','paired_bars_sha256':sha(a.output/'paired-bars.json'),
        'lookback_bars':12,'horizon_bars':3,'bar_seconds':300,'boundaries':boundaries,
        'exclusions':excluded,'groups':groups,'actual_execution_rewards':0,'trade_authority':False}
    payload['dataset_id']=digest(payload);write_once(a.output/'examples.json',payload)
    write_once(a.output/'coverage.json',d['coverage'])
    print(json.dumps({'paired_bars':len(d['bars']),'examples':{k:len(v) for k,v in groups.items()},
        'exclusions':excluded,'boundaries':boundaries,'dataset_id':payload['dataset_id'],
        'full_requested_coverage_proven':d['coverage']['full_requested_coverage_proven']}))
if __name__=='__main__':main()
