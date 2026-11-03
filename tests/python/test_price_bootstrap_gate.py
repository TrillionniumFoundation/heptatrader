"""Real driver/Capture/filesystem; fixture actor/native transport, no orders."""
from contextlib import ExitStack, redirect_stderr
import copy, hashlib, importlib.util, io, json, os
from pathlib import Path
import shutil, signal, stat, subprocess, sys, tempfile, time, unittest
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
import hepta_price_bootstrap_gate as g
import hepta_decision_attribution as capture

class PriceGateTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  box=tempfile.TemporaryDirectory();self.addCleanup(box.cleanup);self.root=Path(box.name)
  spec=importlib.util.spec_from_file_location('strict_price_fixture_driver',ROOT/'scripts/heptatrader_paper_driver.py')
  self.d=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.d)
  self.period=self.root/'period.json';self.period.write_text('{"execution_domain":"PAPER"}\n');self.period.chmod(0o600)
  self.manifest={'schema':'heptatrader.attribution-producer.v1','instrument':'EUR.USD','execution_domain':'PAPER',
   'runtime_loader':'verified-bytes.v1','period_path':str(self.period),'period_sha256':hashlib.sha256(self.period.read_bytes()).hexdigest(),
   'runtime_sha256':{g.MODULE:hashlib.sha256((ROOT/'scripts'/g.MODULE).read_bytes()).hexdigest()},
   'journal_directory':str(self.root/'journal'),
   'price_bootstrap_gate':{'schema':g.SELECTION_SCHEMA,'profile':g.PROFILE,'receipt_root':str(self.root/'price')}}
  self.producer=g.digest(self.manifest);self.calls=[];self.actor_calls=[];self.events=[]
  self.submit_response={'ok':True,'transport_ok':True,'application_status':'ok','result':{'order_id':123}}
  self.submit_hook=None
  self.response={'action':'BUY','confidence':.75,'rationale':'fixture original actor'}
  def actor(argv,*args,**kwargs):
   self.actor_calls.append(list(argv))
   return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'outputs':[{'text':json.dumps(self.response)}]}))
  self.d.run=actor;self.d.paper_cmd=self.native
  self.gate=g.PriceBootstrapGate(self.d,self.manifest,self.producer);self.addCleanup(self.gate.install())
  self.journal=capture.Journal(self.root/'journal',self.manifest,self.producer)
  self.cap=capture.Capture(self.journal,self.d,self.manifest);self.addCleanup(self.cap.install())
  self.stack.enter_context(redirect_stderr(io.StringIO()))
  now=g.wall_ms();bid=1.1;ask=1.1002
  self.history=[{'observed_at_ms':now-200,'bid':bid,'ask':ask,'mid':(bid+ask)/2},
                {'observed_at_ms':now-100,'bid':bid,'ask':ask,'mid':(bid+ask)/2}]
  self.state_path=self.root/'state.json'
 def native(self,binary,socket,token,outbox,operation,extra):
  self.calls.append(operation);self.events.append(('native',operation))
  if operation.startswith('prepare-'):return {'ok':True,'command_id':'fixture-command-'+str(len(self.calls))}
  if operation=='submit':
   if self.submit_hook is not None:self.submit_hook()
   return copy.deepcopy(self.submit_response)
  if operation=='inspect':return {'ok':True,'transport_ok':True,'application_status':'ok','result':{'payload':{
   'authoritative':True,'command_id':extra[extra.index('--command-id')+1],'command_status':'rejected','reason_code':'EXPIRED'}}}
  raise AssertionError('unexpected fixture operation: '+operation)
 def decision(self,position=0):
  self.worker=self.d.ModelWorker('/fixture/actor',self.root,self.history,position)
  self.worker._thread.join(5);self.assertTrue(self.worker.done());value=self.worker.result()
  self.assertEqual(value,self.response);return value
 def state(self,decision):
  state=self.d.load_state(self.state_path);state['last_decision']=decision;return state
 def prepare(self,decision,*,side='BUY',state=None):
  state=self.state(decision) if state is None else state
  self.d.prepare_send(state,self.state_path,'unused','unused','unused','unused','OPEN',self.history[-1],side)
  return state
 def record(self,decision,name):return json.loads((self.gate.entries[id(decision)]['folder']/name).read_bytes())
 def snapshot(self,*,position=0,active=None,rows=None):
  def native(cli,tool,*args):
   if tool=='system.get_health':return {'execution_mode':'PAPER','gateway_ready':True,'remote_execution_ready':True,
    'execution_authoritative_health':{'source':'IB','authoritative':True,'owner_fenced':False,
     'owner_recovery_only':False,'mutation_ready':True},'execution_service_epoch':'fixture','execution_service_fencing_generation':1}
   if tool=='orders.list':return {'source':'IB','authoritative':True,'global_active_orders_complete':True,
    'owner_projection_complete':True,'active_order_ids':active or [],'recent_orders':rows or []}
   if tool=='portfolio.list_positions':return {'source':'IB','authoritative':True,
    'positions':[{'instrument':'EUR.USD','quantity':position}]}
   raise AssertionError('unexpected snapshot tool '+tool)
  with patch.object(self.d,'native',side_effect=native):return self.d.snapshot('unused',require_quote=False)
 def inspect(self,state,snap):
  return self.d.inspect_pending(state,self.state_path,'unused','unused','unused','unused',snap)
 def blocked(self):
  decision=self.decision();entry=self.gate.entries[id(decision)];state=self.state(decision)
  with patch.object(self.d,'now_ms',return_value=entry['sealed_at_ms']-1):
   with self.assertRaisesRegex(g.GateError,'PREPARED_TIME_NOT_AFTER'):self.prepare(decision,state=state)
  return decision,state

 def test_original_input_and_prediction_file_and_directory_fsync_precede_native_prepare(self):
  real=os.fsync
  def fsync(fd):
   path=os.readlink('/proc/self/fd/'+str(fd));real(fd)
   if '.writing-' in Path(path).name:
    path=str(Path(path).with_name(Path(path).name[1:].split('.writing-',1)[0]))
   self.events.append(('dir-fsync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file-fsync',path))
  with patch.object(g.os,'fsync',side_effect=fsync):decision=self.decision();state=self.prepare(decision)
  self.assertEqual(self.calls,['prepare-open','submit'])
  folder=self.gate.entries[id(decision)]['folder'];source=self.record(decision,'input.json');pred=self.record(decision,'prediction.json')
  self.assertEqual(source['request']['prompt'],self.actor_calls[0][self.actor_calls[0].index('--prompt')+1])
  self.assertEqual(source['request']['history_snapshot'],self.history);self.assertEqual(source['request_sha256'],g.digest(source['request']))
  self.assertEqual(pred['point_forecast_mid'],self.history[-1]['mid']);self.assertEqual(pred['target_at_ms'],source['request']['started_at_ms']+300000)
  self.assertEqual(pred['kind'],'PRICE_PERSISTENCE_BASELINE');self.assertFalse(pred['learned_value_head']);self.assertFalse(pred['value_coverage_credit'])
  before_native=self.events.index(('native','prepare-open'))
  for name in ('input.json','prediction.json','seal.json','admission.json'):
   index=self.events.index(('file-fsync',str(folder/name)));self.assertLess(index,before_native)
   self.assertIn(('dir-fsync',str(folder)),self.events[index+1:before_native])
  receipt=state['pending']['price_bootstrap_forecast']
  self.assertLessEqual(receipt['computed_at_ms'],receipt['seal_durable_completed_at_ms'])
  self.assertLess(receipt['admission_durable_completed_at_ms'],state['pending']['created_at_ms'])
  self.assertEqual(receipt['request_sha256'],source['request_sha256'])
  self.assertEqual(self.d.load_state(self.state_path)['pending']['price_bootstrap_forecast'],receipt)
  command_binding=self.record(decision,'command-binding.json')
  self.assertEqual(command_binding['command_id'],state['pending']['command_id'])
  self.assertEqual(command_binding['request_sha256'],source['request_sha256'])
  binding_index=self.events.index(('file-fsync',str(folder/'command-binding.json')))
  before_submit=self.events.index(('native','submit'))
  self.assertLess(before_native,binding_index);self.assertLess(binding_index,before_submit)
  self.assertIn(('dir-fsync',str(folder)),self.events[binding_index+1:before_submit])
  self.journal.q.join()
  rows=[json.loads(line) for p in (self.root/'journal').glob('*.jsonl') for line in p.read_bytes().splitlines()]
  binding=next(r['data'] for r in rows if r['kind']=='COMMAND_BINDING')
  self.assertEqual(binding['price_bootstrap_forecast'],receipt);self.assertEqual(set(decision),{'action','confidence','rationale'})

 def test_equal_clone_and_missing_context_do_not_use_nearest_request(self):
  decision=self.decision()
  with self.assertRaisesRegex(g.GateError,'NO_IDENTICAL_CAPTURED'):self.prepare(copy.deepcopy(decision))
  self.assertEqual(self.calls,[])
  failures=[json.loads(p.read_bytes()) for p in (self.gate.root/'failures').glob('*.json')]
  self.assertIsNone(failures[-1]['request_id'])
 def test_changed_decision_identity_refused(self):
  decision=self.decision();decision['confidence']=.99
  with self.assertRaisesRegex(g.GateError,'ORIGINAL_OBJECT_MUTATED'):self.prepare(decision)
  self.assertEqual(self.calls,[])
 def test_changed_request_identity_refused(self):
  decision=self.decision();self.gate.entries[id(decision)]['request']['prompt']='changed'
  with self.assertRaisesRegex(g.GateError,'ORIGINAL_OBJECT_MUTATED'):self.prepare(decision)
  self.assertEqual(self.calls,[])
 def test_wrong_side_refused(self):
  decision=self.decision()
  with self.assertRaisesRegex(g.GateError,'ORIGINAL_ENTRY_SCOPE'):self.prepare(decision,side='SELL')
  self.assertEqual(self.calls,[])
 def test_original_worker_monotonic_deadline_not_reset(self):
  decision=self.decision()
  with patch.object(self.d.time,'monotonic',return_value=self.worker._deadline+.001):
   with self.assertRaisesRegex(g.GateError,'cancelled'):self.prepare(decision)
  self.assertEqual(self.calls,[])
 def test_wall_clock_rollback_refused(self):
  decision=self.decision();started=self.gate.entries[id(decision)]['request']['started_at_ms']
  with patch.object(g,'wall_ms',return_value=started-1):
   with self.assertRaisesRegex(g.GateError,'CLOCK_REVERSED'):self.prepare(decision)
  self.assertEqual(self.calls,[])
 def test_fsync_failure_retains_real_request_failure_without_prepare(self):
  real=g.sync_directory
  def fail(path):
   if (Path(path)/'prediction.json').exists():raise OSError('fixture fsync failed')
   return real(path)
  with patch.object(g,'sync_directory',side_effect=fail):decision=self.decision()
  entry=self.gate.entries[id(decision)]
  with self.assertRaisesRegex(g.GateError,'NO_COMPLETE_DURABLE_SEAL'):self.prepare(decision)
  failures=[json.loads(p.read_bytes()) for p in (self.gate.root/'failures').glob('*.json')]
  self.assertTrue(any(r['request_id']==entry['request']['request_id'] and r['request_sha256']==entry['request_sha256'] for r in failures))
  self.assertEqual(self.calls,[])
 def test_late_io_completion_refused_without_backdating(self):
  real_write=g.write_once;real_tick=time.monotonic_ns;offset=[0]
  def late(path,value):
   result=real_write(path,value)
   if path.name=='prediction.json':offset[0]+=g.OPERATION_BUDGET_MS*1000000+1
   return result
  with patch.object(g,'write_once',side_effect=late),patch.object(g.time,'monotonic_ns',side_effect=lambda:real_tick()+offset[0]):
   decision=self.decision();self.assertIn('OPERATION_TIMEOUT',self.gate.entries[id(decision)]['error'])
   with self.assertRaisesRegex(g.GateError,'NO_COMPLETE_DURABLE_SEAL'):self.prepare(decision)
  self.assertEqual(self.calls,[])
 def test_tampered_seal_refused(self):
  decision=self.decision();p=self.gate.entries[id(decision)]['folder']/'seal.json'
  p.chmod(0o600);p.write_text('{}\n');p.chmod(0o400)
  with self.assertRaisesRegex(g.GateError,'SEALED_FILE_IDENTITY_CHANGED'):self.prepare(decision)
  self.assertEqual(self.calls,[])
 def test_missing_seal_refused(self):
  decision=self.decision();(self.gate.entries[id(decision)]['folder']/'seal.json').unlink()
  with self.assertRaises(g.GateError):self.prepare(decision)
  self.assertEqual(self.calls,[])
 def test_wrong_producer_refused(self):
  self.journal.manifest_sha='f'*64;decision=self.decision()
  with self.assertRaisesRegex(g.GateError,'NO_COMPLETE_DURABLE_SEAL'):self.prepare(decision)
  self.assertEqual(self.calls,[])
 def test_actor_call_without_original_worker_refused(self):
  decision=self.d.model_decision('/fixture/actor',self.root,self.history,0)
  self.assertIn('ORIGINAL_MODEL_WORKER_REQUIRED',self.gate.entries[id(decision)]['error'])
  with self.assertRaisesRegex(g.GateError,'NO_COMPLETE_DURABLE_SEAL'):self.prepare(decision)
 def test_native_rejection_cannot_reuse_same_request(self):
  decision=self.decision()
  def reject(*args,**kwargs):self.calls.append('prepare-open');return {'ok':False,'reason':'fixture risk rejection'}
  self.d.paper_cmd=reject
  with self.assertRaises(self.d.DriverError):self.prepare(decision)
  with self.assertRaisesRegex(g.GateError,'REQUEST_ALREADY_CONSUMED'):self.prepare(decision)
  self.assertEqual(self.calls,['prepare-open'])
  failures=[json.loads(p.read_bytes()) for p in (self.gate.root/'failures').glob('*.json')]
  self.assertTrue(any(row['phase']=='ORIGINAL_PREPARE_OR_SEND_FAILED' and row['request_id']==
   self.gate.entries[id(decision)]['request']['request_id'] for row in failures))
 def test_close_and_recovery_durable_state_bypass_prediction_gate(self):
  with patch.object(self.gate,'authorize',side_effect=AssertionError('CLOSE must not consult prediction')):
   state=self.state(None);state['position']=1
   self.d.prepare_send(state,self.state_path,'unused','unused','unused','unused','CLOSE')
   self.assertEqual(self.calls,['prepare-close','submit'])
   state['pending']['send_state']='PREPARED';state['pending']['kind']='OPEN'
   state['pending'].pop('price_bootstrap_forecast',None);self.d.atomic_json(self.state_path,state)
   self.assertNotIn('price_bootstrap_forecast',self.d.load_state(self.state_path)['pending'])
 def test_clock_failure_after_prepare_preserves_original_pending_but_blocks_submit(self):
  decision=self.decision();entry=self.gate.entries[id(decision)]
  with patch.object(self.d,'now_ms',return_value=entry['sealed_at_ms']-1):
   with self.assertRaisesRegex(g.GateError,'PREPARED_TIME_NOT_AFTER'):self.prepare(decision)
  self.assertEqual(self.calls,['prepare-open'])
  pending=self.d.load_state(self.state_path)['pending']
  self.assertEqual(pending['send_state'],'LOCAL_PRE_SUBMIT_BLOCKED');self.assertIn('local_pre_submit_blocked_reason',pending)
 def test_lost_live_context_cannot_recover_permission_from_receipt(self):
  decision=self.decision();self.gate.entries.clear()
  with self.assertRaisesRegex(g.GateError,'NO_IDENTICAL_CAPTURED'):self.prepare(decision)
  self.assertEqual(self.calls,[])

 def test_local_unsent_recovery_uses_real_snapshot_and_retains_actual_command(self):
  decision,state=self.blocked();command=state['pending']['command_id'];before={k:state[k] for k in ('entries','exits','closed_trades')}
  self.inspect(state,self.snapshot())
  self.assertIsNone(state['pending']);self.assertEqual(self.calls,['prepare-open'])
  self.assertEqual(before,{k:state[k] for k in before})
  receipt=state['last_price_bootstrap_local_non_send'];archive=json.loads(Path(receipt['archive_path']).read_bytes())
  self.assertEqual(archive['pending']['command_id'],command);self.assertFalse(receipt['native_rejection_claimed'])
  self.assertFalse(archive['replacement_or_resubmit']);self.assertEqual(archive['pending']['price_bootstrap_forecast']['request_id'],
   self.gate.entries[id(decision)]['request']['request_id'])
  self.assertIsNone(self.d.load_state(self.state_path)['pending'])
 def test_local_unsent_recovery_after_restart_does_not_need_live_request_or_reauthorize(self):
  decision,state=self.blocked();self.gate.entries.clear();state=self.d.load_state(self.state_path)
  self.inspect(state,self.snapshot());self.assertIsNone(state['pending']);self.assertEqual(self.calls,['prepare-open'])
  with self.assertRaisesRegex(g.GateError,'NO_IDENTICAL_CAPTURED'):self.prepare(decision)
 def test_local_unsent_recovery_rejects_copied_stale_changed_or_active_snapshot(self):
  decision,state=self.blocked()
  self.inspect(state,copy.deepcopy(self.snapshot()));self.assertIsNotNone(state['pending'])
  self.assertIn('FRESH_ORIGINAL_SNAPSHOT',state['last_error'])
  snap=self.snapshot();self.gate.last_snapshot=(snap,time.monotonic_ns()-3000000000,g.wall_ms(),1)
  self.inspect(state,snap);self.assertIsNotNone(state['pending'])
  for options in ({'position':1},{'active':[19]},{'rows':[{'command_id':state['pending']['command_id']}]}):
   self.inspect(state,self.snapshot(**options));self.assertIsNotNone(state['pending'])
   self.assertIn('UNCHANGED_AUTHORITATIVE_STATE',state['last_error'])
  self.assertEqual(self.calls,['prepare-open'])
  self.inspect(state,self.snapshot());self.assertIsNone(state['pending'])
 def test_local_unsent_recovery_never_accepts_mutated_or_nondurable_boundary(self):
  decision,state=self.blocked();state['pending']['command_id']='different-command'
  self.inspect(state,self.snapshot());self.assertIsNotNone(state['pending'])
  self.assertIn('BOUNDARY_CHANGED',state['last_error']);self.assertEqual(self.calls,['prepare-open'])
 def test_possibly_sent_keeps_original_broker_recovery_even_if_no_rows_or_expired(self):
  decision,state=self.blocked();state['pending']['send_state']='POSSIBLY_SENT';self.d.atomic_json(self.state_path,state)
  for _ in range(3):self.inspect(state,self.snapshot())
  self.assertEqual(state['pending']['send_state'],'POSSIBLY_SENT')
  self.assertIn('awaiting correlation-bound Broker',state['last_error'])
  self.assertEqual(self.calls,['prepare-open','inspect','inspect','inspect'])
  self.assertNotIn('last_price_bootstrap_local_non_send',state)
 def test_command_binding_write_failure_never_submits_and_can_reconcile(self):
  decision=self.decision();state=self.state(decision);real=g.write_once
  def fail(path,value):
   if path.name=='command-binding.json':raise OSError('fixture binding storage unavailable')
   return real(path,value)
  with patch.object(g,'write_once',side_effect=fail):
   with self.assertRaisesRegex(g.GateError,'binding storage'):self.prepare(decision,state=state)
  self.assertEqual(self.calls,['prepare-open']);self.assertEqual(state['pending']['send_state'],'LOCAL_PRE_SUBMIT_BLOCKED')
  self.inspect(state,self.snapshot());self.assertIsNone(state['pending'])
 def test_prepared_fsync_completion_rechecks_original_clock(self):
  decision=self.decision();state=self.state(decision);clock=[None];original=g.wall_ms
  def fsync(fd):
   real_fsync(fd)
   if stat.S_ISDIR(os.fstat(fd).st_mode) and self.state_path.exists() and self.d.load_state(self.state_path).get('pending'):
    clock[0]=self.gate.entries[id(decision)]['request']['started_at_ms']-1
  real_fsync=os.fsync
  with patch.object(g.os,'fsync',side_effect=fsync),patch.object(g,'wall_ms',side_effect=lambda:original() if clock[0] is None else clock[0]):
   with self.assertRaisesRegex(g.GateError,'CLOCK_REVERSED'):self.prepare(decision,state=state)
  self.assertEqual(self.calls,['prepare-open']);self.assertEqual(state['pending']['send_state'],'LOCAL_PRE_SUBMIT_BLOCKED')
 def test_nonflat_and_hold_decisions_do_not_write_price_seals_or_failures(self):
  self.decision(position=1);self.assertFalse(self.gate.root.exists());self.assertEqual(len(self.gate.entries),0)
  self.response['action']='HOLD';self.decision();self.assertFalse(self.gate.root.exists())
  self.assertEqual(len(self.gate.entries),0);self.assertEqual(self.calls,[])
 def test_async_journal_failure_does_not_replace_private_authority(self):
  with patch.object(self.journal,'emit',return_value=False):
   decision=self.decision();state=self.prepare(decision)
  binding=self.record(decision,'command-binding.json');self.assertEqual(binding['command_id'],state['pending']['command_id'])
  state['pending']=None;self.d.atomic_json(self.state_path,state)
  self.assertEqual(self.record(decision,'command-binding.json'),binding)
 def test_killed_after_actual_prepared_fsync_recovers_without_submit_or_broker_rejection(self):
  code='''import json, os, signal, sys
from unittest.mock import patch
from test_price_bootstrap_gate import PriceGateTests, g
t=PriceGateTests(); t.setUp(); decision=t.decision()
print(json.dumps({'root':str(t.root),'manifest':t.manifest,'producer':t.producer}),flush=True)
original=g.write_once
def crash(path,value):
 if path.name=='command-binding.json': os.kill(os.getpid(),signal.SIGKILL)
 return original(path,value)
with patch.object(g,'write_once',side_effect=crash): t.prepare(decision)
raise AssertionError('crash boundary not reached')
'''
  result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,env={**os.environ,
   'PYTHONPATH':os.pathsep.join((str(ROOT/'scripts'),str(ROOT/'tests/python')))},capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,-signal.SIGKILL,result.stderr)
  fixture=json.loads(result.stdout);child_root=Path(fixture['root']);self.addCleanup(shutil.rmtree,child_root)
  state_path=child_root/'state.json';state=self.d.load_state(state_path)
  self.assertEqual(state['pending']['send_state'],'PREPARED')
  folder=Path(state['pending']['price_bootstrap_forecast']['receipt_directory'])
  self.assertFalse((folder/'command-binding.json').exists())
  restored=g.PriceBootstrapGate(self.d,fixture['manifest'],fixture['producer']);snap=self.snapshot()
  restored.last_snapshot=self.gate.last_snapshot
  restored.reconcile_local_unsent(state,state_path,snap)
  self.assertIsNone(state['pending']);self.assertEqual(self.calls,[])
  archive=json.loads((folder/'local-non-send.json').read_bytes())
  self.assertEqual(archive['pending']['send_state'],'PREPARED');self.assertFalse(archive['replacement_or_resubmit'])
 def test_recovery_archive_can_retry_after_clear_state_write_failure(self):
  decision,state=self.blocked();real=self.d.atomic_json;failed=[False]
  def write(path,value):
   if value.get('pending') is None and not failed[0]:failed[0]=True;raise OSError('fixture clear-state write failure')
   return real(path,value)
  with patch.object(self.d,'atomic_json',side_effect=write):self.inspect(state,self.snapshot())
  self.assertIsNotNone(state['pending']);archive=self.record(decision,'local-non-send.json')
  self.inspect(state,self.snapshot());self.assertIsNone(state['pending'])
  self.assertEqual(self.record(decision,'local-non-send.json'),archive);self.assertEqual(self.calls,['prepare-open'])
 def test_last_pretransport_fsync_cannot_extend_original_worker_or_quote_deadline(self):
  now=g.wall_ms()
  for index,quote in enumerate(self.history):quote['observed_at_ms']=now-55200+index*100
  decision=self.decision();state=self.state(decision);offset=[0];real_wall=g.wall_ms
  real_driver_wall=self.d.now_ms;real_tick=time.monotonic_ns;real_mono=time.monotonic;real_fsync=os.fsync
  def fsync(fd):
   real_fsync(fd)
   if stat.S_ISDIR(os.fstat(fd).st_mode) and self.state_path.exists():
    pending=self.d.load_state(self.state_path).get('pending') or {}
    if pending.get('send_state')=='POSSIBLY_SENT':offset[0]=6000
  with patch.object(g.os,'fsync',side_effect=fsync),patch.object(g,'wall_ms',side_effect=lambda:real_wall()+offset[0]), \
       patch.object(self.d,'now_ms',side_effect=lambda:real_driver_wall()+offset[0]), \
       patch.object(g.time,'monotonic_ns',side_effect=lambda:real_tick()+offset[0]*1000000), \
       patch.object(self.d.time,'monotonic',side_effect=lambda:real_mono()+offset[0]/1000):
   with self.assertRaisesRegex(g.GateError,'cancelled|EXPIRED|TIMEOUT'):self.prepare(decision,state=state)
  self.assertEqual(self.calls,['prepare-open']);self.assertEqual(state['pending']['send_state'],'LOCAL_PRE_SUBMIT_BLOCKED')
  self.assertEqual(state['pending']['price_bootstrap_local_non_send']['phase'],'POSSIBLY_SENT_DURABILITY')
  self.assertFalse(state['pending']['price_bootstrap_local_non_send']['transport_call_entered'])
  self.inspect(state,self.snapshot());self.assertIsNone(state['pending'])
 def test_last_pretransport_fsync_clock_rollback_refuses_submit(self):
  decision=self.decision();state=self.state(decision);clock=[None];real_wall=g.wall_ms;real_fsync=os.fsync
  def fsync(fd):
   real_fsync(fd)
   if stat.S_ISDIR(os.fstat(fd).st_mode) and self.state_path.exists():
    if (self.d.load_state(self.state_path).get('pending') or {}).get('send_state')=='POSSIBLY_SENT':
     clock[0]=self.gate.entries[id(decision)]['request']['started_at_ms']-1
  with patch.object(g.os,'fsync',side_effect=fsync),patch.object(g,'wall_ms',side_effect=lambda:real_wall() if clock[0] is None else clock[0]):
   with self.assertRaisesRegex(g.GateError,'CLOCK_REVERSED'):self.prepare(decision,state=state)
  self.assertEqual(self.calls,['prepare-open']);self.assertEqual(state['pending']['send_state'],'LOCAL_PRE_SUBMIT_BLOCKED')
 def test_transport_exception_never_becomes_local_non_send(self):
  def fail():raise OSError('fixture transport entered then lost')
  self.submit_hook=fail;decision=self.decision();state=self.state(decision)
  with self.assertRaisesRegex(OSError,'transport entered'):self.prepare(decision,state=state)
  self.assertEqual(self.calls,['prepare-open','submit']);self.assertEqual(state['pending']['send_state'],'POSSIBLY_SENT')
  self.assertNotIn('price_bootstrap_local_non_send',state['pending'])
  self.inspect(state,self.snapshot());self.assertIsNotNone(state['pending']);self.assertEqual(self.calls[-1],'inspect')
 def test_uncertain_reply_after_entered_transport_keeps_original_recovery(self):
  self.submit_response={'ok':False,'transport_ok':False,'application_status':'uncertain'}
  decision=self.decision();state=self.prepare(decision)
  self.assertEqual(self.calls,['prepare-open','submit']);self.assertEqual(state['pending']['send_state'],'POSSIBLY_SENT')
  self.assertNotIn('price_bootstrap_local_non_send',state['pending'])
  self.inspect(state,self.snapshot());self.assertIsNotNone(state['pending']);self.assertEqual(self.calls[-1],'inspect')
 def test_half_written_local_archive_is_not_published_and_retry_can_finish(self):
  decision,state=self.blocked();real_write=os.write;writes=[0]
  def write(fd,raw):
   if '.local-non-send.json.writing-' in os.readlink('/proc/self/fd/'+str(fd)):
    writes[0]+=1
    if writes[0]>1:raise OSError('fixture disk full after one byte')
    return real_write(fd,raw[:1])
   return real_write(fd,raw)
  with patch.object(g.os,'write',side_effect=write):self.inspect(state,self.snapshot())
  folder=self.gate.entries[id(decision)]['folder'];self.assertFalse((folder/'local-non-send.json').exists())
  self.assertEqual(list(folder.glob('.local-non-send.json.writing-*')),[])
  self.assertIsNotNone(state['pending']);self.inspect(state,self.snapshot());self.assertIsNone(state['pending'])
  self.assertEqual(self.calls,['prepare-open'])
 def test_oversized_period_is_refused_by_original_private_read_bound(self):
  decision=self.decision();raw=b'x'*65537;self.period.write_bytes(raw)
  self.gate.manifest['period_sha256']=hashlib.sha256(raw).hexdigest()
  with self.assertRaises(g.GateError):self.prepare(decision)
  self.assertEqual(self.calls,[])
 def test_killed_after_atomic_archive_publication_has_one_link_and_recovers(self):
  code='''import json, os, signal
from unittest.mock import patch
from test_price_bootstrap_gate import PriceGateTests, g
t=PriceGateTests(); t.setUp(); decision,state=t.blocked()
print(json.dumps({'root':str(t.root),'manifest':t.manifest,'producer':t.producer}),flush=True)
original=g.publish_exclusive
def crash(temporary,path):
 original(temporary,path)
 if path.name=='local-non-send.json': os.kill(os.getpid(),signal.SIGKILL)
with patch.object(g,'publish_exclusive',side_effect=crash): t.inspect(state,t.snapshot())
raise AssertionError('crash boundary not reached')
'''
  result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,env={**os.environ,
   'PYTHONPATH':os.pathsep.join((str(ROOT/'scripts'),str(ROOT/'tests/python')))},capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,-signal.SIGKILL,result.stderr)
  fixture=json.loads(result.stdout);child_root=Path(fixture['root']);self.addCleanup(shutil.rmtree,child_root)
  state_path=child_root/'state.json';state=self.d.load_state(state_path)
  folder=Path(state['pending']['price_bootstrap_forecast']['receipt_directory']);archive=folder/'local-non-send.json'
  self.assertEqual(archive.stat().st_nlink,1);self.assertEqual(list(folder.glob('.local-non-send.json.writing-*')),[])
  expected=archive.read_bytes();restored=g.PriceBootstrapGate(self.d,fixture['manifest'],fixture['producer'])
  snap=self.snapshot();restored.last_snapshot=self.gate.last_snapshot
  restored.reconcile_local_unsent(state,state_path,snap)
  self.assertIsNone(state['pending']);self.assertEqual(archive.read_bytes(),expected);self.assertEqual(self.calls,[])

class QuoteAndSelectionTests(unittest.TestCase):
 def test_atomic_publication_never_replaces_existing_authority(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'seal.json';pin=g.write_once(path,{'first':'authority'});raw=path.read_bytes()
   with self.assertRaises(FileExistsError):g.write_once(path,{'replacement':'refused'})
   self.assertEqual(g.read_exact(path,pin),raw);self.assertEqual(path.stat().st_nlink,1)
   self.assertEqual(list(Path(directory).glob('.*.writing-*')),[])

 def test_past_quote_validation(self):
  q={'observed_at_ms':1000,'bid':1.,'ask':1.2,'mid':1.1}
  self.assertEqual(g.past_quote({'started_at_ms':1001,'history_snapshot':[q]}),q)
  for quotes in ([],[{**q,'observed_at_ms':1002}],[{**q,'mid':1.12}],[{**q,'bid':float('nan')}],[{**q,'bid':2.}],[{**q,'observed_at_ms':True}]):
   with self.subTest(quotes=quotes),self.assertRaises(g.GateError):g.past_quote({'started_at_ms':1001,'history_snapshot':quotes})
 def test_profile_is_explicit(self):
  self.assertIsNone(g.validate_selection({}))
  with self.assertRaises(g.GateError):g.validate_selection({'price_bootstrap_gate':None})

if __name__=='__main__':unittest.main()
