"""BTC policy math and unchanged native-driver transport tests. No broker calls."""
import copy,json,sys,tempfile,unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
import hepta_btc_entry_policy as p
import heptatrader_btc_economics_driver as w
NOW=1791356400000

def forecast(move=60,action='BUY',confidence=.72):
 return {'action':action,'confidence':confidence,'rationale':'fixture only','expected_move_bps':move,
 'horizon_sec':1800,'issued_at_ms':NOW-1000,'available_at_ms':NOW-100,'reference_mid':'100000',
 'input_sha256':'1'*64,'model_id':'gpt-6-luna','forecast_schema':'heptatrader.btc-return-forecast.v1'}
def quote():return {'source':'IB','authoritative':True,'stale':False,'instrument':'BTC.USD','bid':'99999.5','ask':'100000.5','observed_at_ms':NOW-10,'stale_after_ms':NOW+4990}
def control():return {'schema':'heptatrader.btc-entry-control.v1','open_reservations':[],'last_flat_at_ms':0}

class CostMathTests(unittest.TestCase):
 def check(self,f=None,q=None,c=None,**kw):return p.evaluate(f or forecast(),q or quote(),c or control(),NOW,**kw)
 def test_costs_not_confidence_are_real_minimum(self):
  r=self.check();self.assertTrue(r['allowed']);self.assertEqual(r['expected_entry_fee_usd'],'45.0000')
  self.assertGreater(Decimal(r['expected_exit_fee_usd']),45)
 def test_ten_bps_not_enough_despite_confidence_one(self):
  r=self.check(forecast(10,confidence=1));self.assertFalse(r['allowed']);self.assertIn('BTC_EDGE_BELOW_ROUNDTRIP_COST',r['reasons'])
 def test_forty_bps_is_not_rounded_up_to_pass(self):self.assertFalse(self.check(forecast(40))['allowed'])
 def test_forty_one_bps_can_clear_documented_assumptions(self):self.assertTrue(self.check(forecast(41))['allowed'])
 def test_historical_confidence_point_six_two_preserved(self):
  self.assertFalse(self.check(forecast(confidence=.619))['allowed']);self.assertTrue(self.check(forecast(confidence=.62))['allowed'])
 def test_hold_never_open(self):self.assertFalse(self.check(forecast(action='HOLD',confidence=1))['allowed'])
 def test_sell_cannot_open_spot_short(self):self.assertFalse(self.check(forecast(action='SELL',confidence=1))['allowed'])
 def test_missing_fee_profile_never_zero(self):
  with patch.dict(p.PROFILE,{'commission_rate':None}):self.assertFalse(self.check()['allowed'])
 def test_no_rebate_or_volume_discount_in_expectation(self):
  r=self.check();self.assertEqual(r['rebate_credit_usd'],'0');self.assertFalse(p.PROFILE['rebates_assumed'])
 def test_minimum_fee_applies_to_small_orders(self):
  r=self.check(cash='200');self.assertEqual(r['expected_entry_fee_usd'],'1.75');self.assertFalse(r['allowed'])
 def test_spread_counted_once_in_prices(self):
  q=quote();q.update(bid='99900',ask='100100');r=self.check(forecast(100),q)
  target=Decimal('101000');qty=Decimal(25000)/Decimal('100100');expected=(target-Decimal(100))*qty-25000
  self.assertEqual(Decimal(r['expected_gross_after_spread_usd']),expected)
 def test_price_move_during_inference_can_consume_edge(self):
  q=quote();q.update(bid='100599.5',ask='100600.5');self.assertFalse(self.check(q=q)['allowed'])
 def test_quote_stale_flag_rejected(self):
  q=quote();q['stale']=True;self.assertFalse(self.check(q=q)['allowed'])
 def test_quote_source_age_rechecked(self):
  q=quote();q['observed_at_ms']=NOW-5001;self.assertIn('BTC_QUOTE_STALE',self.check(q=q)['reasons'])
 def test_future_quote_rejected(self):
  q=quote();q['observed_at_ms']=NOW+1;self.assertFalse(self.check(q=q)['allowed'])
 def test_non_ib_quote_rejected(self):
  q=quote();q['source']='SYNTHETIC';self.assertFalse(self.check(q=q)['allowed'])
 def test_forecast_age_not_refreshed_by_current_quote(self):
  f=forecast();f['issued_at_ms']=NOW-60001;self.assertIn('BTC_FORECAST_STALE',self.check(f)['reasons'])
 def test_missing_expected_move_not_inferred_from_confidence(self):
  f=forecast();del f['expected_move_bps'];self.assertFalse(self.check(f)['allowed'])
 def test_nan_infinite_and_bool_rejected(self):
  for value in (float('nan'),float('inf'),True):
   f=forecast();f['expected_move_bps']=value;self.assertFalse(self.check(f)['allowed'])
 def test_short_horizon_not_silently_rescaled(self):
  f=forecast();f['horizon_sec']=300;self.assertFalse(self.check(f)['allowed'])
 def test_entry_interval_persists_in_state(self):
  c=control();p.reserve(c,'previous',NOW-1000);self.assertIn('BTC_ENTRY_INTERVAL',self.check(c=c)['reasons'])
 def test_final_check_does_not_count_own_pending_as_prior_open(self):
  c=control();p.reserve(c,'this-command',NOW);self.assertTrue(self.check(c=c,ignore_command='this-command')['allowed'])
 def test_duplicate_reservation_is_idempotent(self):
  c=control();p.reserve(c,'same',NOW-10);p.reserve(c,'same',NOW);self.assertEqual(len(c['open_reservations']),1)
 def test_two_opens_in_rolling_hour_rejected(self):
  c=control();p.reserve(c,'one',NOW-3599000);p.reserve(c,'two',NOW-1800000)
  self.assertIn('BTC_ROLLING_OPEN_LIMIT',self.check(c=c)['reasons'])
 def test_cooldown_starts_on_full_flat_not_signal(self):
  c=control();c['last_flat_at_ms']=NOW-100;self.assertIn('BTC_AFTER_FLAT_COOLDOWN',self.check(c=c)['reasons'])
 def test_cooldown_expiry_does_not_stay_forever(self):
  c=control();c['last_flat_at_ms']=NOW-1800000;self.assertTrue(self.check(c=c)['allowed'])
 def test_clock_rollback_fails_closed(self):
  c=control();c['last_flat_at_ms']=NOW+1;self.assertFalse(self.check(c=c)['allowed'])
 def test_no_increased_cash_sizing(self):self.assertFalse(self.check(cash='25001')['allowed'])

class DriverIntegrationTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.path=Path(self.t.name)/'state.json'
  self.m=w.load_base(ROOT/'scripts/heptatrader_paper_driver.py');self.m.INSTRUMENT='BTC.USD';self.m.QTY=1.;self.m.QUANTITY_TOLERANCE=1e-10;self.m.now_ms=lambda:NOW
  self.calls=[];self.quotes=[quote(),quote()];self.submit_kind='ok';self.fail_period=False
  self.health={'execution_mode':'PAPER','execution_authoritative_health':{'mutation_ready':True,'owner_fenced':False,'owner_recovery_only':False,'source':'IB','authoritative':True,'startup_recovery_read_only':False}}
  self.m.native=self.native;self.m.paper_cmd=self.transport
  self.original_exit=self.m.should_close
  self.state={'schema':'heptatrader.paper-driver.v1','position':0.,'pending':None,'halt_new_entries':False,'last_decision':forecast()}
  config={'initial_control':control(),'profile':p.PROFILE}
  def period():
   if self.fail_period:raise p.PolicyError('PERIOD_CHANGED')
  self.close=w.attach(self.m,config,period_check=period);self.addCleanup(self.close)
 def native(self,cli,tool,*args):
  self.calls.append(tool)
  if tool=='system.get_health':return self.health
  if tool=='market.get_quote':return self.quotes.pop(0) if len(self.quotes)>1 else self.quotes[0]
  raise AssertionError(tool)
 def transport(self,b,s,t,o,op,extra):
  self.calls.append(op)
  if op.startswith('prepare-'):return {'ok':True,'command_id':'original-command'}
  if op=='submit':return {'ok':self.submit_kind=='ok','transport_ok':self.submit_kind=='ok','application_status':self.submit_kind,'result':{'order_id':42}}
  raise AssertionError(op)
 def open(self):self.m.prepare_send(self.state,self.path,'b','s','t','o','OPEN',quote(),'BUY')
 def test_actual_base_prepare_and_one_submit(self):
  self.open();self.assertEqual(self.calls.count('submit'),1);self.assertEqual(self.state['pending']['command_id'],'original-command')
  self.assertEqual(self.state['pending']['send_state'],'ACKNOWLEDGED')
  self.assertEqual(len(self.state['btc_entry_control']['open_reservations']),1)
 def test_weak_edge_rejected_before_native_prepare(self):
  self.state['last_decision']=forecast(10)
  with self.assertRaises(self.m.DriverError):self.open()
  self.assertNotIn('prepare-crypto-open',self.calls);self.assertNotIn('submit',self.calls)
 def test_first_check_uses_reacquired_quote_not_passed_quote(self):
  self.quotes=[{**quote(),'stale':True}]
  with self.assertRaises(self.m.DriverError):self.open()
  self.assertNotIn('submit',self.calls)
 def test_second_quote_can_definitely_veto_without_changing_original_id(self):
  self.quotes=[quote(),{**quote(),'bid':'100599.5','ask':'100600.5'}];self.open()
  self.assertNotIn('submit',self.calls);self.assertEqual(self.state['pending']['command_id'],'original-command')
  self.assertEqual(self.state['pending']['send_state'],'DEFINITELY_REJECTED');self.assertIsNone(self.state['pending']['broker_order_id'])
 def test_uncertain_send_remains_original_pending(self):
  self.submit_kind='uncertain';self.open();self.assertEqual(self.state['pending']['send_state'],'POSSIBLY_SENT');self.assertEqual(self.calls.count('submit'),1)
 def test_cannot_prepare_over_pending(self):
  self.state['pending']={'command_id':'older','kind':'OPEN'}
  with self.assertRaises(self.m.DriverError):self.open()
  self.assertEqual(self.calls,[]);self.assertEqual(self.state['pending']['command_id'],'older')
 def test_native_owner_fence_is_not_relaxed(self):
  self.health['execution_authoritative_health']['mutation_ready']=False
  with self.assertRaises(p.PolicyError):self.open()
  self.assertNotIn('submit',self.calls)
 def test_not_live(self):
  self.health['execution_mode']='LIVE'
  with self.assertRaises(p.PolicyError):self.open()
  self.assertNotIn('submit',self.calls)
 def test_short_prohibited(self):
  with self.assertRaises(self.m.DriverError):self.m.prepare_send(self.state,self.path,'b','s','t','o','OPEN',quote(),'SELL')
 def test_close_never_calls_fee_or_quote_admission(self):
  self.state.update(position=.001,btc_entry_control={'malformed':True});self.fail_period=True
  self.m.prepare_send(self.state,self.path,'b','s','t','o','CLOSE')
  self.assertEqual(self.calls,['prepare-crypto-close','submit'])
 def test_risk_exit_function_is_same_object(self):
  self.assertIs(self.m.should_close,self.original_exit)
  self.assertEqual(self.m.should_close({'entry_price':100000},.1,{'bid':99000},None),(True,'MAX_ADVERSE_MOVE'))
 def test_halt_exit_still_allowed_during_negative_edge(self):
  s={'halt_new_entries':True,'entry_price':100000}
  self.assertEqual(self.m.should_close(s,.1,quote(),forecast(-100)),(True,'HALT_REDUCE_ONLY'))
 def test_reversal_below_fees_is_still_exit(self):
  self.assertEqual(self.m.should_close({'entry_price':100000},.1,quote(),forecast(-1,'SELL',.62)),(True,'MODEL_REVERSAL'))
 def test_close_cooldown_persists_after_real_atomic_settlement(self):
  self.state.update(last_reconciliation={'kind':'CLOSE','reconciled_at_ms':NOW,'command_id':'closed'},position=0.,pending=None)
  self.m.atomic_json(self.path,self.state)
  loaded=json.loads(self.path.read_text());self.assertEqual(loaded['btc_entry_control']['last_flat_at_ms'],NOW)
 def test_fx_transport_untouched(self):
  self.m.INSTRUMENT='EUR.USD';self.m.QTY=25000
  self.m.prepare_send(self.state,self.path,'b','s','t','o','OPEN',{'bid':1.1,'ask':1.11},'SELL')
  self.assertEqual(self.calls,['prepare-open','submit']);self.assertNotIn('btc_entry_control',self.state)
 def test_canary_not_repeated_to_generate_fee_data(self):
  with self.assertRaises(self.m.DriverError):self.m.prepare_send(self.state,self.path,'b','s','t','o','OPEN',quote(),'BUY',crypto_cash_limit=150)
 def test_profile_failure_cannot_delete_pending(self):
  self.state['pending']={'command_id':'old','kind':'CLOSE'};self.fail_period=True
  with self.assertRaises(self.m.DriverError):self.open()
  self.assertEqual(self.state['pending']['command_id'],'old')
 def test_json_state_durable_reservation_survives_new_wrapper(self):
  self.open();loaded=json.loads(self.path.read_text());loaded['pending']=None
  r=p.evaluate(forecast(),quote(),loaded['btc_entry_control'],NOW)
  self.assertFalse(r['allowed']);self.assertIn('BTC_ENTRY_INTERVAL',r['reasons'])

 def test_definite_non_send_reconciles_without_new_command(self):
     self.quotes=[quote(),{**quote(),'bid':'100599.5','ask':'100600.5'}];self.open()
     self.assertEqual(self.state['pending']['send_state'],'DEFINITELY_REJECTED')
     self.m.inspect_pending(self.state,self.path,'b','s','t','o',{'active':[],'position':0.})
     self.assertIsNone(self.state['pending']);self.assertNotIn('submit',self.calls)
     self.assertEqual(self.state['btc_entry_control']['open_reservations'][0]['command_id'],'original-command')
 def test_exposed_btc_uses_original_model_without_entry_schema(self):
     from types import SimpleNamespace
     result={'action':'SELL','confidence':.65,'rationale':'existing-position reversal'}
     calls=[]
     self.m.run=lambda argv,*a,**k:(calls.append(argv) or SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'outputs':[{'text':json.dumps(result)}]})))
     history=[{'observed_at_ms':NOW-1000,'bid':99999.5,'ask':100000.5,'mid':100000}]
     actual=self.m.model_decision('model',self.path.parent,history,.1)
     self.assertEqual(actual,result)
     self.assertNotIn('expected_move_bps',calls[0][calls[0].index('--prompt')+1])
 def test_read_only_or_unattributed_owner_cannot_open(self):
     for key,value in (('owner_recovery_only',True),('source','other'),('authoritative',False),('startup_recovery_read_only',True)):
         owner=copy.deepcopy(self.health['execution_authoritative_health']);self.health['execution_authoritative_health'][key]=value
         with self.assertRaises(p.PolicyError):self.open()
         self.health['execution_authoritative_health']=owner
     self.assertNotIn('submit',self.calls)
 def test_final_admission_cannot_replace_forecast_of_original_command(self):
     write=self.m.atomic_json
     def alter(path,value):
         result=write(path,value)
         if (value.get('pending') or {}).get('send_state')=='POSSIBLY_SENT':
             value['last_decision']={**value['last_decision'],'expected_move_bps':100}
         return result
     self.m.atomic_json=alter;self.open()
     self.assertNotIn('submit',self.calls)
     self.assertEqual(self.state['pending']['command_id'],'original-command')
     self.assertEqual(self.state['pending']['send_state'],'DEFINITELY_REJECTED')
 def test_model_direction_without_magnitude_is_rejected(self):
     from types import SimpleNamespace
     raw={'action':'BUY','confidence':.99,'rationale':'no numeric forecast'}
     self.m.run=lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'outputs':[{'text':json.dumps(raw)}]}))
     history=[{'observed_at_ms':NOW-1000,'bid':99999.5,'ask':100000.5,'mid':100000}]
     with self.assertRaises(self.m.DriverError):self.m.model_decision('model',self.path.parent,history,0)
 def test_model_forecast_reference_bound_by_code_not_response(self):
     from types import SimpleNamespace
     raw={k:forecast()[k] for k in w.SCHEMA['required']}
     self.m.run=lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'outputs':[{'text':json.dumps(raw)}]}))
     history=[{'observed_at_ms':NOW-1000,'bid':99999.5,'ask':100000.5,'mid':100000}]
     result=self.m.model_decision('model',self.path.parent,history,0)
     self.assertEqual(result['issued_at_ms'],NOW-1000);self.assertEqual(result['reference_mid'],'100000.0')
     self.assertEqual(result['input_sha256'],p.digest([{'t':NOW-1000,'bid':99999.5,'ask':100000.5,'mid':100000}]))
 def test_low_confidence_reversal_does_not_become_forced_exit(self):
     self.assertEqual(self.m.should_close({'entry_price':100000},.1,quote(),forecast(-100,'SELL',.61)),(False,''))

if __name__=='__main__':unittest.main(verbosity=2)
