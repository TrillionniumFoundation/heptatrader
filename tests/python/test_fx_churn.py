"""Synthetic FX anti-churn controls and unchanged real base-driver contracts."""
import copy,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
import hepta_fx_churn as f
NOW=1800000000000

def seed():return {'period_sha256':'a'*64,'last_exit_ms':NOW-3600000,'open_attempts':[]}
def state(seq=1,at=NOW,action='BUY',confidence=.62):return {'decisions':seq,'last_decision_ms':at,'last_decision':{'action':action,'confidence':confidence,'rationale':'fixture'},'position':0,'pending':None,'halt_new_entries':False}
def confirmed():
 c=f.observe(f.fresh_control(seed()),state(at=NOW-120000),NOW-120000)
 return f.observe(c,state(seq=2),NOW)

class PolicyTests(unittest.TestCase):
 def test_two_distinct_fresh_signals_can_enter(self):self.assertTrue(f.admission(confirmed(),state()['last_decision'],NOW)['allowed'])
 def test_one_signal_cannot_enter(self):
  c=f.observe(f.fresh_control(seed()),state(),NOW);self.assertIn('WAIT_SECOND_DIRECTIONAL_CONFIRMATION',f.admission(c,state()['last_decision'],NOW)['reasons'])
 def test_cached_decision_cannot_self_confirm(self):
  c=f.observe(f.fresh_control(seed()),state(),NOW)
  for _ in range(50):c=f.observe(c,state(),NOW)
  self.assertEqual(c['confirmations'],1)
 def test_two_calls_too_close_are_not_confirmation(self):
  c=f.observe(f.fresh_control(seed()),state(at=NOW-1000),NOW);c=f.observe(c,state(2),NOW);self.assertEqual(c['confirmations'],1)
 def test_hold_resets_confirmation(self):
  c=f.observe(confirmed(),state(3,NOW+1000,'HOLD',1.),NOW+1000);self.assertEqual(c['confirmations'],0)
 def test_opposite_signal_starts_new_sequence(self):
  c=f.observe(confirmed(),state(3,NOW+120000,'SELL'),NOW+120000);self.assertEqual(c['confirmations'],1);self.assertEqual(c['signal_side'],'SELL')
 def test_confidence_stays_062(self):
  self.assertFalse(f.valid_direction({'action':'BUY','confidence':.6199}));self.assertTrue(f.valid_direction({'action':'SELL','confidence':.62}));self.assertFalse(f.valid_direction({'action':'HOLD','confidence':1}))
 def test_nan_or_bool_confidence_not_signal(self):
  for x in (float('nan'),float('inf'),True):self.assertFalse(f.valid_direction({'action':'BUY','confidence':x}))
 def test_old_signal_expires(self):
  c=f.observe(confirmed(),state(3,NOW+600000),NOW+600000);self.assertEqual(c['confirmations'],1)
 def test_held_position_never_confirms_reentry(self):
  s=state(3,NOW+120000);s['position']=25000;c=f.observe(confirmed(),s,NOW+120000);self.assertEqual(c['confirmations'],0)
 def test_cooldown_after_flat(self):
  c=confirmed();c['last_exit_ms']=NOW-599999;self.assertIn('AFTER_FLAT_COOLDOWN',f.admission(c,state()['last_decision'],NOW)['reasons'])
 def test_entry_spacing(self):
  c=confirmed();c['open_attempts']=[{'command_id':'x','at_ms':NOW-899999}];self.assertIn('MINIMUM_OPEN_SPACING',f.admission(c,state()['last_decision'],NOW)['reasons'])
 def test_hour_count_includes_unsent_prepared_attempts(self):
  c=confirmed();c['open_attempts']=[{'command_id':'x','at_ms':NOW-1800000},{'command_id':'y','at_ms':NOW-900001}]
  self.assertIn('ROLLING_HOUR_OPEN_LIMIT',f.admission(c,state()['last_decision'],NOW)['reasons'])
 def test_hour_window_expires_no_permanent_latch(self):
  c=confirmed();c['open_attempts']=[{'command_id':'x','at_ms':NOW-3600000}];self.assertTrue(f.admission(c,state()['last_decision'],NOW)['allowed'])
 def test_preinstallation_signal_is_not_confirmation(self):
  c=f.fresh_control(seed());c['activation_ms']=NOW+1;c=f.observe(c,state(),NOW)
  self.assertEqual(c['confirmations'],0)
 def test_stale_decision_refused(self):self.assertIn('FRESH_COMPLETED_DECISION_REQUIRED',f.admission(confirmed(),state()['last_decision'],NOW+60001)['reasons'])
 def test_future_attempt_or_clock_refused(self):
  c=confirmed();c['open_attempts']=[{'command_id':'x','at_ms':NOW+1}]
  with self.assertRaises(f.ChurnError):f.admission(c,state()['last_decision'],NOW)
 def test_policy_mutation_refused(self):
  c=confirmed();c['profile_sha256']='0'*64
  with self.assertRaises(f.ChurnError):f.admission(c,state()['last_decision'],NOW)
 def test_serialized_restart_keeps_cooldown(self):
  c=confirmed();c['last_exit_ms']=NOW-1;c=json.loads(json.dumps(c));self.assertFalse(f.admission(c,state()['last_decision'],NOW)['allowed'])

class ResumeActivationTests(unittest.TestCase):
 def test_stale_oct7_control_reseeds_from_original_oms(self):
  old=f.fresh_control({'period_sha256':'a'*64,'activation_ms':NOW-8*86400000,
   'last_exit_ms':NOW-8*86400000,'open_attempts':[
    {'command_id':'stale','at_ms':NOW-8*86400000},
    {'command_id':'prepared','at_ms':NOW-600000}]})
  old.update(last_seen_decisions=275,confirmations=2,signal_side='BUY',
    signal_first_ms=NOW-80000,signal_latest_ms=NOW-20000)
  updated={'period_sha256':'a'*64,'activation_ms':NOW,'last_exit_ms':NOW,
    'open_attempts':[{'command_id':'actual-oms','at_ms':NOW-400000}]}
  c=f.resume_activation_control(old,updated,'a'*64)
  self.assertEqual(c['activation_ms'],NOW)
  self.assertEqual(c['last_exit_ms'],NOW)
  self.assertEqual(c['last_seen_decisions'],-1)
  self.assertEqual(c['confirmations'],0)
  self.assertEqual([i['command_id'] for i in c['open_attempts']],['prepared','actual-oms'])
  self.assertIn('AFTER_FLAT_COOLDOWN',f.admission(c,state()['last_decision'],NOW)['reasons'])
  self.assertIn('ROLLING_HOUR_OPEN_LIMIT',f.admission(c,state()['last_decision'],NOW)['reasons'])
 def test_same_activation_restores_durable_decision_count(self):
  seed={'period_sha256':'a'*64,'activation_ms':NOW,
      'last_exit_ms':NOW-3600000,'open_attempts':[]}
  c=f.fresh_control(seed);c.update(confirmations=1,signal_side='SELL',last_seen_decisions=231)
  self.assertEqual(f.resume_activation_control(c,seed,'a'*64),c)
 def test_mismatched_profile_and_activation_clock_fail_closed(self):
  seed={'period_sha256':'a'*64,'activation_ms':NOW,'last_exit_ms':NOW,'open_attempts':[]}
  c=f.fresh_control({**seed,'activation_ms':NOW+1})
  with self.assertRaisesRegex(f.ChurnError,'ACTIVATION_CLOCK'):
   f.resume_activation_control(c,seed,'a'*64)
  c['profile_sha256']='bad'
  with self.assertRaisesRegex(f.ChurnError,'FX_CONTROL_SCOPE'):
   f.resume_activation_control(c,seed,'a'*64)
 def test_old_future_attempt_malformed_blocks_migration(self):
  seed={'period_sha256':'a'*64,'activation_ms':NOW,'last_exit_ms':NOW,'open_attempts':[]}
  for at in (NOW+1,'now'):
   c=f.fresh_control({**seed,'activation_ms':NOW-3600000,
                     'open_attempts':[{'command_id':'old','at_ms':at}]})
   with self.assertRaises(f.ChurnError):
    f.resume_activation_control(c,seed,'a'*64)
 def test_old_prepared_attempt_union_uses_earliest_timestamp(self):
  seed={'period_sha256':'a'*64,'activation_ms':NOW,'last_exit_ms':NOW,'open_attempts':[
      {'command_id':'same','at_ms':NOW-10000}]}
  c=f.fresh_control({**seed,'activation_ms':NOW-200000,'last_exit_ms':NOW-100000,
      'open_attempts':[{'command_id':'same','at_ms':NOW-20000}]})
  newer=f.resume_activation_control(c,seed,'a'*64)
  self.assertEqual(newer['open_attempts'],[{'command_id':'same','at_ms':NOW-20000}])


class BaseIntegrationTests(unittest.TestCase):
 def setUp(self):
  sp=importlib.util.spec_from_file_location('fx_base_fixture',ROOT/'scripts/heptatrader_paper_driver.py');self.d=importlib.util.module_from_spec(sp);sp.loader.exec_module(self.d)
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.period=self.root/'period.json';self.period.write_text('{}')
  import hashlib
  self.ph=hashlib.sha256(self.period.read_bytes()).hexdigest();seed_data=seed();seed_data['period_sha256']=self.ph
  self.c={'profile':copy.deepcopy(f.PROFILE),'seed':seed_data,'period_sha256':self.ph,'period_path':str(self.period)}
  self.d.now_ms=lambda:NOW;self.d.INSTRUMENT='EUR.USD';self.calls=[];self.writes=[]
  self.original_close=self.d.should_close;self.original_entry=self.d.entry_candidate_allowed
  self.d.atomic_json=lambda path,s:self.writes.append(copy.deepcopy(s))
  def command(binary,socket,token,outbox,operation,extra):
   self.calls.append(operation)
   if operation.startswith('prepare'):return {'ok':True,'command_id':'original-command'}
   return {'ok':True,'transport_ok':True,'application_status':'ok','result':{'status':'ok','order_id':320}}
  self.d.paper_cmd=command
  close=f.attach(self.d,self.c);self.addCleanup(close)
  self.state=state(2);cc=confirmed();cc['period_sha256']=self.ph;self.state['fx_open_control']=cc
 def open(self):return self.d.prepare_send(self.state,self.root/'state','b','s','t','o','OPEN',{'bid':1.1,'ask':1.1001},'BUY')
 def test_new_activation_does_not_reuse_oct7_stale_confirmation(self):
  # Simulates the present attributed PAPER state: same period/profile, but
  # an FX churn snapshot left behind by a prior (now inactive) deployment.
  old=self.state['fx_open_control']
  old.update(activation_ms=NOW-8*86400000,last_exit_ms=NOW-8*86400000,
             last_seen_decisions=275,confirmations=2,signal_side='BUY')
  self.c['seed'].update(activation_ms=NOW,last_exit_ms=NOW,
      open_attempts=[{'command_id':'previous-real-oms','at_ms':NOW-250000}])
  with self.assertRaisesRegex(f.ChurnError,'FX_OPEN_DEFERRED'):
   self.open()
  self.assertEqual(self.calls,[])
  self.assertEqual(self.state['fx_open_control']['activation_ms'],NOW)
  self.assertEqual(self.state['fx_open_control']['confirmations'],1)
  self.assertEqual(self.state['fx_open_control']['open_attempts'],
      [{'command_id':'previous-real-oms','at_ms':NOW-250000}])
  self.assertIn('AFTER_FLAT_COOLDOWN',self.state['fx_last_open_admission']['reasons'])
 def test_confirmed_open_follows_original_command_once(self):
  self.open();self.assertEqual(self.calls,['prepare-open','submit']);self.assertEqual(self.state['pending']['command_id'],'original-command');self.assertEqual(self.state['pending']['send_state'],'ACKNOWLEDGED')
  self.assertEqual(self.state['fx_open_control']['open_attempts'],[{'command_id':'original-command','at_ms':NOW}])
 def test_single_confirmation_has_no_prepare_or_submit(self):
  self.state['fx_open_control']['confirmations']=1
  with self.assertRaises(f.ChurnError):self.open()
  self.assertEqual(self.calls,[])
 def test_old_pending_cannot_be_replaced(self):
  self.state['pending']={'command_id':'old'}
  with self.assertRaises(f.ChurnError):self.open()
  self.assertEqual(self.state['pending']['command_id'],'old');self.assertEqual(self.calls,[])
 def test_close_never_waits_cooldown_or_profit(self):
  self.state['position']=25000;self.state['fx_open_control']['last_exit_ms']=NOW
  self.d.prepare_send(self.state,self.root/'state','b','s','t','o','CLOSE')
  self.assertEqual(self.calls,['prepare-close','submit'])
 def test_original_risk_exit_function_unchanged(self):
  self.assertIs(self.d.should_close,self.original_close);self.assertIs(self.d.entry_candidate_allowed,self.original_entry)
  s={'entry_price':100,'halt_new_entries':False};q={'bid':99.7,'ask':99.8}
  self.assertEqual(self.d.should_close(s,25000,q,None),(True,'MAX_ADVERSE_MOVE'))
  self.assertEqual(self.d.should_close(s,25000,{'bid':100,'ask':101},{'action':'SELL','confidence':.62}),(True,'MODEL_REVERSAL'))
  s['halt_new_entries']=True;self.assertEqual(self.d.should_close(s,25000,q,None),(True,'HALT_REDUCE_ONLY'))
 def test_period_change_blocks_only_open(self):
  self.period.write_text('{"new":1}')
  with self.assertRaisesRegex(f.ChurnError,'PERIOD'):self.open()
  self.d.prepare_send(self.state,self.root/'state','b','s','t','o','CLOSE');self.assertIn('submit',self.calls)
 def test_malformed_control_never_suppresses_exit_persistence(self):
  self.state['fx_open_control']['profile_sha256']='bad';self.state['position']=25000
  self.d.prepare_send(self.state,self.root/'state','b','s','t','o','CLOSE');self.assertEqual(self.state['pending']['send_state'],'ACKNOWLEDGED')
 def test_native_halts_remain_blocking(self):
  for key in ('halt_new_entries','owner_fence_recovery_required','lease_uncertain','risk_halt_reason'):
   self.state[key]=True
   with self.assertRaises(f.ChurnError):self.open()
   self.state[key]=False
  self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main(verbosity=2)
