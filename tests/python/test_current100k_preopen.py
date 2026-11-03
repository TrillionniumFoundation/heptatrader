"""Synthetic identity, recovery, deadline and admission tests; no model/orders."""
import copy,hashlib,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'research'),str(ROOT/'research/neuron_learning')]
from laya_cell.current100k_preopen import RequestLedger,load,publish_once,run_resident
from laya_cell.integrity import ContractError,digest

class Clock:
 def __init__(self):self.now=10000;self.tick=0
 def wall(self):return self.now
 def ns(self):return self.tick
 def seconds(self):return self.tick/1e9
 def advance(self,seconds):self.now+=int(seconds*1000);self.tick+=int(seconds*1e9)

class Gate:
 def __init__(self,admit=True,continuation=True):self.events=[];self.admit=admit;self.continuation=continuation
 def acquire(self,subject):self.events.append('acquire');return self.admit
 def check(self):self.events.append('check');return {'admit_new_worker':self.continuation,'reasons':[] if self.continuation else ['MANUAL_HOLD']}
 def release(self):self.events.append('release')

class LedgerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.clock=Clock();self.protocol={'plan_id':'1'*64,'input_start_ms':9000,'input_end_ms':1000000,
   'period_sha256':'2'*64,'producer_registration':{'manifest':'/synthetic/producer','sha256':'3'*64},'max_request_age_ms':60000}
  self.ledger=self.open_ledger();self.addCleanup(lambda:self.ledger.close())
 def open_ledger(self):return RequestLedger(self.root/'ledger',self.protocol,clock=self.clock.wall,monotonic=self.clock.ns)
 def item(self,n=1,started=None):
  prompt='Exact synthetic request '+str(n)
  r={'request_id':f'{n:032x}','started_at_ms':self.clock.now if started is None else started,
   'instrument':'EUR.USD','period_sha256':'2'*64,'position_at_request':0,'prompt':prompt,
   'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
  return {'request':r,'request_sha256':digest(r),'producer_manifest_sha256':'3'*64,
          'source_record_sha256':'4'*64,'source_written_at_ms':r['started_at_ms']+1,
          'source_prefix':{'path':'/synthetic/events.jsonl','bytes':100,'sha256':'5'*64}}
 def binding(self,item,created=None,command='original-1'):
  r=item['request']
  return {'request_id':r['request_id'],'request_sha256':item['request_sha256'],
   'producer_manifest_sha256':item['producer_manifest_sha256'],'request_started_at_ms':r['started_at_ms'],
   'decision_sha256':'7'*64,'command':{'command_id':command,'kind':'OPEN','instrument':'EUR.USD',
    'side':'BUY','created_at_ms':self.clock.now if created is None else created}}
 def publish(self,item):
  self.clock.advance(.01)
  receipt={'plan_id':self.protocol['plan_id'],'request_id':item['request']['request_id'],
   'request_sha256':item['request_sha256'],'seal_durable_completed_at_ms':self.clock.now}
  path=self.root/'shadow'/item['request']['request_id']/'publication.json';sha=publish_once(path,receipt)
  observation=path.parent/'observation.json'
  observed_sha=publish_once(observation,{'publication_sha256':sha,'publication_durability_observed_at_ms':self.clock.now})
  return {**receipt,'publication_path':str(path),'publication_sha256':sha,'observation_path':str(observation),'observation_sha256':observed_sha}
 def test_preopen_credit_requires_both_durable_prediction_and_exact_command(self):
  item=self.item();terminal=self.ledger.attempt(item,self.publish)
  self.assertEqual(terminal['status'],'SEALED_AWAITING_ORIGINAL_COMMAND');self.assertFalse(terminal['before_command_confirmed'])
  self.clock.advance(.1);bound=self.ledger.reconcile(self.binding(item))
  self.assertEqual(bound['status'],'PREOPEN_BOUND_RESEARCH');self.assertTrue(bound['before_original_command'])
  self.assertFalse(bound['value_coverage_credit']);self.assertFalse(bound['production_eligible'])
 def test_publication_finishing_after_original_open_is_late(self):
  item=self.item();created=self.clock.now+50
  def slow(i):
   r=self.publish(i);self.clock.advance(.1);return r
  self.ledger.attempt(item,slow)
  self.assertEqual(self.ledger.reconcile(self.binding(item,created))['status'],'MISSING_OR_LATE_NO_CREDIT')
 def test_known_open_is_never_backfilled(self):
  item=self.item();self.ledger.observe(item);self.clock.advance(.02);self.ledger.reconcile(self.binding(item))
  self.ledger.attempt(item,lambda _:self.fail('late forward attempted'))
  self.assertEqual(self.ledger.terminal(item['request']['request_id'])['status'],'NO_PREDICTION_BEFORE_ORIGINAL_COMMAND')
 def test_restart_does_not_rerun_or_retimestamp_sealed_request(self):
  item=self.item();first=self.ledger.attempt(item,self.publish);self.ledger.close();self.ledger=self.open_ledger()
  self.assertEqual(self.ledger.recover(),[])
  self.assertEqual(self.ledger.attempt(item,lambda _:self.fail('duplicate forward')),first)
 def test_interrupted_attempt_is_unknown_not_replayed(self):
  item=self.item()
  def interrupted(_):raise KeyboardInterrupt()
  with self.assertRaises(KeyboardInterrupt):self.ledger.attempt(item,interrupted)
  self.ledger.close();self.ledger=self.open_ledger()
  self.assertEqual(self.ledger.recover()[0]['status'],'INTERRUPTED_UNKNOWN_NO_REPLAY')
  self.ledger.attempt(item,lambda _:self.fail('interrupted forward replayed'))
 def test_wall_clock_cannot_extend_monotonic_deadline(self):
  item=self.item()
  def slow(i):
   receipt=self.publish(i);self.clock.tick+=61000000000;return receipt
  result=self.ledger.attempt(item,slow);self.assertEqual(result['status'],'PREDICTION_FAILED_OR_LATE')
  self.assertIn('EXPIRED_DURING',result['reason'])
 def test_wrong_request_hash_and_producer_are_refused(self):
  item=self.item();item['request_sha256']='a'*64
  with self.assertRaisesRegex(ContractError,'REQUEST_HASH'):self.ledger.observe(item)
  item=self.item();item['producer_manifest_sha256']='b'*64
  with self.assertRaisesRegex(ContractError,'PRODUCER_CHANGED'):self.ledger.observe(item)
 def test_changed_publication_file_never_gets_credit(self):
  def corrupt(i):
   receipt=self.publish(i);Path(receipt['publication_path']).write_text('{}');return receipt
  result=self.ledger.attempt(self.item(),corrupt)
  self.assertEqual(result['status'],'PREDICTION_FAILED_OR_LATE');self.assertIn('FILE_HASH',result['reason'])
 def test_request_and_command_cannot_be_rebound(self):
  item=self.item();self.ledger.attempt(item,self.publish);self.clock.advance(.1);b=self.binding(item);self.ledger.reconcile(b)
  with self.assertRaisesRegex(ContractError,'MULTIPLE_ORIGINAL_COMMANDS'):
   self.ledger.reconcile(self.binding(item,command='original-2'))
  changed=copy.deepcopy(b);changed['request_sha256']='0'*64
  with self.assertRaisesRegex(ContractError,'COMMAND_REQUEST_IDENTITY'):self.ledger.reconcile(changed)
 def test_all_expired_requests_receive_explicit_terminal_reason(self):
  items={str(n):self.item(n,started=9000) for n in range(1,4)};self.clock.advance(60)
  self.assertEqual(self.ledger.pending(items),[])
  self.assertEqual(len(list((self.root/'ledger/requests').glob('*/terminal.json'))),3)
  self.assertEqual({self.ledger.terminal(x['request']['request_id'])['status'] for x in items.values()},
                   {'REQUEST_EXPIRED_NO_BACKDATING'})
 def source(self,items=()):
  class Source:
   def read(inner):return {},{x['request']['request_id']:x for x in items},[],[]
   def statistics(inner):return {}
  return Source()
 def runner(self,source,gate,**kw):
  return run_resident(source,self.ledger,lambda:gate.events.append('warm'),self.publish,
   lambda:gate.events.append('close'),gate,max_seconds=kw.pop('max_seconds',1),
   sleep=self.clock.advance,monotonic=self.clock.seconds,**kw)
 def test_one_warm_load_serves_multiple_original_requests(self):
  gate=Gate();result=self.runner(self.source([self.item(1),self.item(2)]),gate)
  self.assertEqual(result['model_loads'],1);self.assertEqual(result['request_attempts'],2)
  self.assertEqual(gate.events,['acquire','warm','close','release'])
 def test_durable_cooldown_prevents_restart_reacquisition(self):
  source=self.source([self.item()]);self.runner(source,Gate());gate=Gate();result=self.runner(source,gate)
  self.assertEqual(result['model_loads'],0);self.assertNotIn('acquire',gate.events)
  self.assertGreater(result['restart_not_before_ms'],self.clock.now)
 def test_guard_withdrawal_releases_model_and_lease(self):
  gate=Gate(continuation=False);result=self.runner(self.source(),gate,max_seconds=3,guard_check_seconds=1)
  self.assertEqual(result['reason'],'GUARD_WITHDREW:MANUAL_HOLD');self.assertEqual(gate.events[-2:],['close','release'])
 def test_waiting_worker_gets_bounded_handoff(self):
  result=self.runner(self.source(),Gate(),max_seconds=3,yield_requested=lambda:self.clock.seconds()>=.25)
  self.assertEqual(result['reason'],'OTHER_REGISTERED_WORKER_WAITING');self.assertLess(result['resident_seconds'],1)
 def test_early_clean_handoff_uses_actual_release_plus_cooldown(self):
  result=self.runner(self.source(),Gate(),max_seconds=1800,yield_requested=lambda:self.clock.seconds()>=.25)
  self.assertEqual(result['restart_not_before_ms'],self.clock.now+60000)
  closes=list((self.root/'ledger').glob('lease-close-*.json'));self.assertEqual(len(closes),1)
  receipt=load(closes[0]);self.assertTrue(receipt['tensor_cleanup_completed'])
  self.assertTrue(receipt['original_lease_release_completed'])
 def test_unknown_crash_keeps_full_reservation_deadline(self):
  reservation=self.ledger.reserve_lease(1800,60)
  self.assertEqual(self.ledger.restart_not_before(),10000+1860000)
  self.ledger.close();self.ledger=self.open_ledger()
  self.assertEqual(self.ledger.restart_not_before(),reservation['restart_not_before_ms'])
 def test_cleanup_failure_never_releases_heavy_worker_lease(self):
  gate=Gate()
  def fail_cleanup():raise RuntimeError('live GPU tensors')
  with self.assertRaisesRegex(RuntimeError,'live GPU'):
   run_resident(self.source(),self.ledger,lambda:None,self.publish,fail_cleanup,gate,
    max_seconds=1,sleep=self.clock.advance,monotonic=self.clock.seconds)
  self.assertNotIn('release',gate.events)
  self.assertEqual(list((self.root/'ledger').glob('lease-close-*.json')),[])
  self.assertGreater(self.ledger.restart_not_before(),self.clock.now+59000)
 def test_release_failure_never_creates_false_clean_close(self):
  gate=Gate()
  def failed_release():raise OSError('unknown release')
  gate.release=failed_release
  with self.assertRaisesRegex(OSError,'unknown release'):self.runner(self.source(),gate)
  self.assertEqual(list((self.root/'ledger').glob('lease-close-*.json')),[])
 def test_close_receipt_cannot_shorten_another_reservation(self):
  r=self.ledger.reserve_lease(1800,60);self.clock.advance(1);self.ledger.close_lease(r)
  p=next((self.root/'ledger').glob('lease-close-*.json'));value=load(p);value['reservation_sha256']='0'*64
  import json
  p.write_text(json.dumps(value))
  with self.assertRaisesRegex(ContractError,'LEASE_CLOSE_IDENTITY'):self.ledger.restart_not_before()
 def test_resource_denial_records_each_request_without_inference(self):
  item=self.item();result=self.runner(self.source([item]),Gate(admit=False))
  self.assertEqual(result['model_loads'],0);self.assertEqual(result['request_attempts'],0)
  waits=list((self.root/'ledger/requests'/item['request']['request_id']).glob('wait-*.json'))
  self.assertEqual(len(waits),1);self.assertEqual(load(waits[0])['reason'],'SHARED_RESOURCE_OR_SOURCE')

if __name__=='__main__':unittest.main()
