"""Synthetic negative cases for original-command / Broker identity joining."""
from pathlib import Path
import base64
import copy
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import hepta_recovery_identity_chain as chain


def fixture():
    corr='hepta-v1-sha256:'+('ab'*32)
    order_ref='H1'+base64.urlsafe_b64encode(bytes.fromhex('ab'*32)).decode().rstrip('=')
    period={'account':'DU90001','execution_domain':'PAPER',
            'instruments':{'EUR.USD':{'broker_client_id':701,'agent_id':'fixture-fx','session_id':'session-original'},'BTC.USD':{'broker_client_id':701,'agent_id':'fixture-btc','session_id':'session-btc'}}}
    original={'event':'place_sent','account':'DU90001','execution_domain':'PAPER','venue':'IB',
        'req_id':'original','client_req_id':'original','trace_id':'session-original',
        'request_hash':'sha256:'+('cd'*32),'venue_correlation_id':corr,'order_id':337,
        'instrument':'EUR.USD','side':'BUY','source':'agent:fixture-fx'}
    intent=dict(original,event='order_intent',order_id=-1)
    native=dict(original,event='broker_execution',request_hash='',broker_client_id=701,
        broker_execution_id='exec-1',broker_econ_permanent_order_id=752239501,
        broker_econ_valid=1,broker_econ_version=1,broker_econ_kind='EXECUTION',broker_econ_contract_id=12087792,broker_econ_shares=25,
        broker_econ_execution_price=1.12,broker_econ_cumulative_quantity=25)
    contract={'symbol':'EUR','secType':'CASH','currency':'USD','conId':12087792}
    execution={'kind':'execution','account':'DU90001','client_id':701,'order_id':337,
        'permanent_order_id':752239501,'execution_id':'exec-1','order_ref':order_ref,
        'contract':contract,'side':'BOT','shares':'25','price':'1.12','cumulative_quantity':'25'}
    terminal={'kind':'completed_order','account':'DU90001','client_id':0,'order_id':0,
        'permanent_order_id':752239501,'order_ref':order_ref,'contract':contract,'status':'Filled'}
    cash={'kind':'account_value','request_id':7302,'key':'CashBalance','value':'1025','currency':'EUR'}
    raw={'schema':'heptatrader.broker-recovery-download.v1','account':'DU90001','transport_complete':True,
        'ends':{g:1200 for g in ('open','completed','positions','ledger','aggregate','EUR.USD','BTC.USD')},
        'period_unchanged':True,'journal_stable':True,'order_sends':0,'quote_subscriptions':0,
        'started_at_ms':1000,'observed_at_ms':1400,'events':[execution,terminal,cash]}
    return raw,[intent,original,native],period


class IdentityChainTests(unittest.TestCase):
    def test_completed_zero_local_ids_do_not_replace_original_owner(self):
        raw,j,p=fixture();x=chain.proof(raw,j,p)
        self.assertEqual(x['conflicts'],[]);self.assertEqual(x['matched_original_commands'],1)
        self.assertEqual(x['execution_proofs'][0]['order_id'],337)
        self.assertEqual(x['execution_proofs'][0]['permanent_order_id'],752239501)
        self.assertEqual(x['execution_proofs'][0]['completed_status'],'Filled')
        self.assertFalse(x['execution_proofs'][0]['native_callback_request_hash_present'])
        self.assertFalse(x['full_runtime_recovery_qualified'])
    def test_owner_order_reference_and_execution_economics_conflicts(self):
        for update in ({'client_id':702},{'permanent_order_id':752239502},{'order_id':338},
                       {'order_ref':'H1'+'A'*43},{'shares':'26'},{'price':'1.13'},
                       {'cumulative_quantity':'26'},{'execution_id':'other'}, {'side':'SLD'}):
            raw,j,p=fixture();raw['events'][0].update(update)
            with self.subTest(update=update):self.assertTrue(chain.proof(raw,j,p)['conflicts'])
    def test_original_intent_and_send_hash_must_match(self):
        for mutate in ('intent','send','callback'):
            raw,j,p=fixture();idx={'intent':0,'send':1,'callback':2}[mutate]
            j[idx]['request_hash']='sha256:'+('ef'*32)
            with self.subTest(mutate=mutate):self.assertTrue(chain.proof(raw,j,p)['conflicts'])
    def test_registered_agent_session_and_economic_version_cannot_drift(self):
        for field,value in (('source','agent:foreign'),('trace_id','foreign'),('broker_econ_version',2),('broker_econ_kind','COMMISSION')):
            raw,j,p=fixture();j[-1][field]=value
            with self.subTest(field=field):self.assertTrue(chain.proof(raw,j,p)['conflicts'])
        raw,j,p=fixture();p['instruments']['EUR.USD']['agent_id']='foreign'
        self.assertTrue(chain.proof(raw,j,p)['conflicts'])

    def test_duplicate_send_is_not_idempotency_proof(self):
        raw,j,p=fixture();j.append(copy.deepcopy(j[1]));self.assertTrue(chain.proof(raw,j,p)['conflicts'])
    def test_exact_duplicate_execution_is_inert_conflicting_duplicate_rejects(self):
        raw,j,p=fixture();raw['events'].append(copy.deepcopy(raw['events'][0]))
        self.assertEqual(chain.proof(raw,j,p)['verified_execution_count'],1)
        raw['events'][-1]['shares']='24';self.assertTrue(chain.proof(raw,j,p)['conflicts'])
    def test_history_absence_remains_unknown_and_positions_not_summed_with_cash(self):
        raw,j,p=fixture();j.append(dict(j[1],req_id='older',client_req_id='older',order_id=100,
            venue_correlation_id='hepta-v1-sha256:'+('ff'*32)))
        raw['events'].append({'kind':'position','account':'DU90001','quantity':'9999',
                             'contract':raw['events'][0]['contract']})
        x=chain.proof(raw,j,p);self.assertEqual(x['sent_commands_outside_returned_execution_scope'],1)
        self.assertFalse(x['outside_scope_is_zero_fill']);self.assertEqual(x['eur_currency_cash'],'1025')
        self.assertEqual(x['btc_physical_position'],'0');self.assertFalse(x['virtual_cash_position_is_strategy_position'])
    def test_moving_journal_missing_end_and_nonfinite_fail(self):
        for key,value in (('journal_stable',False),('period_unchanged',False),('transport_complete',False),('order_sends',1)):
            raw,j,p=fixture();raw[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):chain.proof(raw,j,p)
        raw,j,p=fixture();raw['ends'].pop('positions')
        with self.assertRaises(ValueError):chain.proof(raw,j,p)
        raw,j,p=fixture();raw['events'][0]['price']='NaN'
        self.assertTrue(chain.proof(raw,j,p)['conflicts'])
    def test_integer_valid_one_not_arbitrary_truthy_flag(self):
        for flag in (0,2,'1',True):
            raw,j,p=fixture();j[-1]['broker_econ_valid']=flag
            with self.subTest(flag=flag):self.assertTrue(chain.proof(raw,j,p)['conflicts'])
    def test_cash_checkpoint_is_separate_from_virtual_position_or_nav(self):
        body='DU90001|EUR.USD|EUR|1000|1025|25|1200|sha256:'+('ab'*32)
        text='HFXR1\n'+body+'|sha256:'+chain.sha(body.encode())+'\n'
        x=chain.checkpoint_alignment(text,'DU90001','1025')
        self.assertTrue(x['independent_broker_cash_matches_native_checkpoint'])
        self.assertFalse(x['original_anchor_authenticated_by_this_check'])
        self.assertFalse(x['candidate_restart_position_continuity_proven'])
        self.assertFalse(chain.checkpoint_alignment(text,'DU90001','9999')['independent_broker_cash_matches_native_checkpoint'])
        with self.assertRaises(ValueError):chain.checkpoint_alignment(text.replace('1025','1026'),'DU90001','1026')
        with self.assertRaises(ValueError):chain.checkpoint_alignment(text,'DU99999','1025')

    def test_cohort_stability_flag_cannot_replace_both_file_hashes(self):
        before=b'original\n';after=b'changed\n'
        broker={'journal_before_sha256':chain.sha(before),'journal_after_sha256':chain.sha(before),'journal_stable':True}
        plan={'journal_before_sha256':chain.sha(before)}
        chain.verify_journal_coherence(before,before,broker,plan)
        with self.assertRaises(ValueError):chain.verify_journal_coherence(before,after,broker,plan)
        broker['journal_after_sha256']=chain.sha(after)
        with self.assertRaises(ValueError):chain.verify_journal_coherence(before,after,broker,plan)

    def test_order_reference_must_be_canonical(self):
        raw,_,_=fixture();ref=raw['events'][0]['order_ref']
        self.assertEqual(chain.correlation(ref),'hepta-v1-sha256:'+('ab'*32))
        for invalid in ('',ref+'=', 'H1'+'A'*42+'B','H1'+'/'*43):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):chain.correlation(invalid)


if __name__=='__main__':unittest.main()
