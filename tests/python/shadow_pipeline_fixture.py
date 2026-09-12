"""Disposable root-to-reader fixture: mock HTTP transport only, not the pipeline.

Synthetic provider payloads and WATCH snapshots are contract fixtures, not a
claim that a network server, market observer or broker produced these bytes.
"""
from __future__ import annotations
from email.message import Message
import json
import math
import os
from pathlib import Path
import sys
from unittest import mock
from official_source_fixtures import OBSERVED, payloads

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import hepta_official_source_capture as capture
import hepta_shadow_market_history as history
import hepta_strategy_shadow_runner as runner
import hepta_strategy_replay_evaluator as replay
import hepta_eurusd_confirmed_momentum_strategy as strategy
import hepta_shadow_finalize as finalizer
from hepta_strategy_contracts import canonical_bytes, digest_document, digest_bytes, ContractError

UID = GID = 1000
CONFIG = ROOT / 'strategies/eurusd-confirmed-momentum-shadow-v2.json'
COUNT = 1201
CADENCE = 10000
BASE = OBSERVED - (COUNT-1)*CADENCE
DOMAIN = 'synthetic-shadow-test'
FLAGS = dict(paper_authorized=False,live_authorized=False,mutation_attempted=False,direct_broker_access=False)


def sealed(body):
    return {**body, 'body_sha256':digest_document(body)}


def write_root(path, value):
    path.write_bytes(canonical_bytes(value));os.chown(path,0,GID);path.chmod(0o440)


class Response:
    def __init__(self, url, data, content_type):
        self.url=url;self.data=data;self.headers=Message();self.headers['Content-Type']=content_type
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def getcode(self): return 200
    def geturl(self): return self.url
    def read(self,maximum): return self.data[:maximum]


class SyntheticOpener:
    def open(self, request, timeout):
        url=request.full_url
        content_type=next(item[3] for item in capture.SOURCE_ORDER if item[1]==url)
        return Response(url,payloads()[url],content_type)


def prepare(base: Path):
    # Parent test owns an O_EXCL-created /var/lib/hepta interlock. Never call on
    # a trading host; this helper assumes that exclusive disposable boundary.
    inputs=base/'inputs';inputs.mkdir(mode=0o750);os.chown(inputs,0,GID)
    work=base/'work';work.mkdir(mode=0o700);os.chown(work,UID,GID)
    evidence=Path('/var/lib/hepta/market-evidence')
    export=Path('/var/lib/hepta/shadow-observation')
    with mock.patch.object(capture,'_opener',return_value=SyntheticOpener()), mock.patch.object(capture.time,'time',return_value=OBSERVED/1000):
        receipt=capture.capture(evidence,export,evidence/'capture-receipt.json',UID,GID,digest_bytes(Path(capture.__file__).read_bytes()))
    assert receipt['calendar_event_count']>0 and receipt['information_item_count']>0
    lease=None
    for i in range(COUNT+7):
        t=BASE+i*CADENCE
        if i%350==0:
            gen=i//350+1
            lease=sealed(dict(schema='hepta.shadow-watch-lease-receipt.v1',version=1,
                domain_id=DOMAIN,agent_id=DOMAIN,agent_uid=UID,boundary='WATCH',operation='PROVISION' if gen==1 else 'ROTATE',
                lease_generation=gen,previous_lease_generation=None if gen==1 else gen-1,
                previous_receipt_body_sha256=None if lease is None else lease['body_sha256'],
                accepted=True,reason_code='OK',accepted_at_ms=t,ttl_seconds=3600,expires_at_ms=t+3600000,
                paper_authorized=False,live_authorized=False,mutation_authorized=False))
            write_root(inputs/f'lease-{gen}.json',lease)
        mid=1.1+i*0.000003+math.sin(i*0.1)*0.00003
        if i>=COUNT:
            decision_mid=1.1+(COUNT-1)*0.000003+math.sin((COUNT-1)*0.1)*0.00003
            mid=decision_mid-0.00005 if i==COUNT else decision_mid+0.00003
        reads={name:{'authoritative':True} for name in history.READ_ORDER}
        reads['portfolio.list_positions']['positions']=[]
        reads['orders.list']['active_order_ids']=[]
        reads['risk.get_limits']['gross_absolute_position']=0
        reads['system.get_health']=dict(gateway_ready=True,remote_execution=True,remote_execution_configured=True,
            remote_execution_ready=True,execution_mode='SIMULATOR',remote_execution_reason='',
            execution_service_epoch='fixture-epoch',execution_service_fencing_generation=1)
        reads['market.get_quote']=dict(source='SIMULATOR',authoritative=True,stale=False,instrument='EUR.USD',bid=mid-0.000001,ask=mid+0.000001,observed_at_ms=t,stale_after_ms=t+5000)
        snapshot=sealed(dict(schema='hepta.shadow-watch-snapshot.v2',version=2,domain_id=DOMAIN,agent_uid=UID,
            collection_started_at_ms=t,collection_finished_at_ms=t+6,generated_at_ms=t+6,
            read_finished_at_ms={name:t+j for j,name in enumerate(history.READ_ORDER,1)},instrument='EUR.USD',
            catalog_sha256='sha256:'+'1'*64,descriptor_sha256={name:'sha256:'+'2'*64 for name in history.READ_ORDER},reads=reads,**FLAGS))
        write_root(inputs/f'snapshot-{i}.json',snapshot)
        exported=sealed(dict(schema='hepta.shadow-watch-export-receipt.v1',version=1,
            domain_id=DOMAIN,agent_uid=UID,reader_uid=UID,reader_gid=GID,boundary='WATCH_EXPORT',
            lease_generation=lease['lease_generation'],lease_receipt_body_sha256=lease['body_sha256'],
            lease_receipt_file_sha256=digest_bytes(canonical_bytes(lease)),snapshot_body_sha256=snapshot['body_sha256'],
            snapshot_file_sha256=digest_bytes(canonical_bytes(snapshot)),snapshot_generated_at_ms=t+6,exported_at_ms=t+6,**FLAGS))
        write_root(inputs/f'export-{i}.json',exported)
    config=strategy.load_strategy(CONFIG)
    policy=dict(schema=runner.POLICY_SCHEMA,version=1,campaign_id='fixture-campaign',
        strategy_id=config['strategy_id'],strategy_version=config['strategy_version'],strategy_sha256=strategy.strategy_package_digest(CONFIG),
        valid_after_ms=OBSERVED+6,expires_at_ms=OBSERVED+6+runner.SLOT_INTERVAL_MS,slot_interval_ms=runner.SLOT_INTERVAL_MS,
        maximum_iterations=1,maximum_lateness_ms=0,shadow_only=True,**FLAGS)
    policy['campaign_sha256']=digest_document(runner._campaign_binding(policy))
    write_root(inputs/'policy.json',sealed(policy))


def rejected(operation, expected=Exception):
    try: operation()
    except expected: return
    raise AssertionError('hostile input unexpectedly accepted')


def reader(base: Path):
    assert os.geteuid()==UID
    inputs=base/'inputs';work=base/'work';h=work/'history';export=Path('/var/lib/hepta/shadow-observation')
    def append(i):
        return history.append_snapshot(h,inputs/f'snapshot-{i}.json',cadence_ms=CADENCE,maximum_jitter_ms=0,
            watch_lease_receipt_path=inputs/f'lease-{i//350+1}.json',watch_export_receipt_path=inputs/f'export-{i}.json')
    for i in range(COUNT): append(i)
    audited=history.audit_history(h,cadence_ms=CADENCE,maximum_jitter_ms=0)
    assert audited['record_count']==COUNT
    append(COUNT-1)
    assert history.audit_history(h,cadence_ms=CADENCE,maximum_jitter_ms=0)["record_count"]==COUNT
    q,b=work/'quotes.json',work/'bars.json'
    materialized=history.materialize_bars(h,work/'materialized.json',cadence_ms=CADENCE,maximum_jitter_ms=0,quote_history_output=q,bar_history_output=b)
    kw=dict(campaign_id='fixture-campaign',iteration=1,evaluated_at_ms=OBSERVED+6,policy_path=inputs/'policy.json',
        strategy_path=CONFIG,snapshot_path=inputs/f'snapshot-{COUNT-1}.json',quote_history_path=q,bar_history_path=b,
        calendar_path=export/'economic-calendar.json',information_path=export/'market-information.json',receipt_path=work/'decision.json',state_path=work/'state.json')
    # A durable decision with interrupted state commit is recovered, not
    # recomputed under a new identity or counted twice.
    original=runner.atomic_write_json
    def fail_state(path,value,*a,**k):
        if Path(path)==kw['state_path']: raise OSError('injected state publication failure')
        return original(path,value,*a,**k)
    with mock.patch.object(runner,'atomic_write_json',side_effect=fail_state): rejected(lambda:runner.run_shadow_iteration(**kw),OSError)
    assert kw['receipt_path'].exists()
    result=runner.run_shadow_iteration(**kw)
    before_state=kw['state_path'].read_bytes()
    again=runner.run_shadow_iteration(**kw)
    assert before_state==kw['state_path'].read_bytes()
    receipt=json.loads(kw['receipt_path'].read_text())
    assert receipt['final_outcome']=='SHADOW_TRADE',receipt.get('reason_codes')
    assert json.loads(before_state)['completed_iterations']==1
    # Simulate an actual record publication followed by lost head update.
    with mock.patch.object(history,'_atomic_replace_head',side_effect=OSError('injected head failure')):
        rejected(lambda:append(COUNT),OSError)
    history.recover_history_head(h,cadence_ms=CADENCE,maximum_jitter_ms=0)
    for i in range(COUNT+1,COUNT+7): append(i)
    final_kw=dict(policy_path=inputs/'policy.json',state_path=kw['state_path'],receipt_paths=[kw['receipt_path']],history_directories=[h],
        output_path=work/'final.json',finalized_at_ms=OBSERVED+80000,cadence_ms=CADENCE,maximum_jitter_ms=0)
    rejected(lambda:finalizer.finalize_campaign(**{**final_kw,'receipt_paths':[]} ),ContractError)
    with mock.patch.object(finalizer.history,'_atomic_publish',side_effect=OSError('injected final publication failure')):
        rejected(lambda:finalizer.finalize_campaign(**final_kw),OSError)
    assert not final_kw['output_path'].exists() and before_state==kw['state_path'].read_bytes()
    audit=finalizer.finalize_campaign(**final_kw)
    assert audit==finalizer.finalize_campaign(**final_kw)
    assert audit['sample_count']==COUNT+7 and before_state==kw['state_path'].read_bytes()
    rejected(lambda:finalizer.finalize_campaign(**{**final_kw,'finalized_at_ms':OBSERVED}),ContractError)
    rejected(lambda:finalizer.finalize_campaign(**{**final_kw,'finalized_at_ms':OBSERVED+90000}),ContractError)
    decisions=replay.seal_decision_set(inputs/'policy.json',work/'final.json',[work/'decision.json'])
    marks=replay.seal_mark_set(inputs/'policy.json',work/'final.json',[h],cadence_ms=CADENCE,maximum_jitter_ms=0)
    low=replay.evaluate_replay(decisions,marks,horizon_seconds=30,round_trip_cost_bps=0,entry_slippage_bps=0,exit_slippage_bps=0)
    high=replay.evaluate_replay(decisions,marks,horizon_seconds=30,round_trip_cost_bps=10,entry_slippage_bps=0,exit_slippage_bps=0)
    assert low['filled_count']==1 and low['resolved_count']==1,low
    assert low['average_net_return_bps']>0 and high['average_net_return_bps']<0,(low,high)
    assert abs(low['average_net_return_bps']-high['average_net_return_bps']-10)<1e-9
    # Binding tampering and post-audit source drift must not become a fresh
    # valid seal; restore neither receipt identities nor history head values.
    import copy
    tampered=copy.deepcopy(decisions);tampered['body_sha256']='sha256:'+'0'*64
    rejected(lambda:replay.evaluate_replay(tampered,marks,horizon_seconds=30,round_trip_cost_bps=0),ContractError)
    print(json.dumps({'scope':'synthetic end-to-end, HTTP transport mocked, no broker', 'samples':audit['sample_count'], 'low_cost':low,'high_cost':high},sort_keys=True))

if __name__=='__main__': reader(Path(sys.argv[1]))
