"""Adversarial reconciliation for IB FX CASH ledger versus strategy state."""
import copy
import unittest
from historical_training.ib_fx_cash_reconcile import reconcile,read_exact_input

NOW=1791622800000


def example():
    position='-89264'
    snap={
        'schema':'heptatrader.pnl.v1',
        'scope':'STRATEGY_PERIOD_NOT_ACCOUNT_NAV',
        'period_sha256':'a'*64,'journal_fingerprint':[1,2,3,4],
        'observed_at_ms':NOW-1000,
        'account':{'account':'DU_TEST'},
        'account_nav':'120000',
        'complete':False,
        'components':[{
            'instrument':'EUR.USD','broker_position':position,
            'position':position,'ledger_complete':True,
            'settlement_pending_command_ids_total':0,
            'valuation_complete':False,
        }],
        'observation_details':{'EUR.USD':{
            'authoritative':True,
            'position_scope':'PAPER_BASELINE_DELTA',
            'quantity':position,
            'quote_error':'AUTHORITATIVE_QUOTE_UNREADY',
            'active_order_ids':[],
            'unsettled_native_command_ids':[],
        }},
    }
    nav={
        'schema':'heptatrader.account-nav.v1',
        'period_sha256':'a'*64,'journal_fingerprint':[1,2,3,4],
        'account':'DU_TEST','observed_at_ms':NOW-2000,
        'authoritative':True,'positions_complete':True,
        'source':'IB_ACCOUNT_UPDATES_MULTI_PAIRED_LEDGER',
        'positions':[],'nav':'120000',
        'ledgers':{'EUR':{'CashBalance':'-89264.00'}},
        'capture_cohort':{'broker_valuation_tick_attested':False}
    }
    driver={
        'schema':'heptatrader.paper-driver.v1',
        'instrument':'EUR.USD','position':-89264.
    }
    return snap,nav,driver


class FxCashReconciliationTests(unittest.TestCase):
    def test_zero_portfolio_position_is_not_zero_cash_exposure(self):
        a,b,c=example()
        result=reconcile(a,b,c,NOW)
        self.assertEqual(result['eur_cash_balance'],'-89264.00')
        self.assertEqual(result['securities_positions_count'],0)
        self.assertTrue(result['securities_positions_not_fx_cash_truth'])
        self.assertFalse(result['pnl_complete'])
        self.assertFalse(result['unrealized_pnl_qualified'])
        self.assertFalse(result['broker_valuation_tick_attested'])
        self.assertFalse(result['trade_authority'])
        self.assertEqual(result['broker_orders_sent'],0)
        self.assertNotIn('DU_TEST',str(result))

    def test_ledger_exposure_disagreement_is_not_silently_summed(self):
        a,b,c=example()
        b['ledgers']['EUR']['CashBalance']='0'
        with self.assertRaisesRegex(ValueError,'EXPOSURE_EUR_CASH_STRATEGY_MISMATCH'):
            reconcile(a,b,c,NOW)

    def test_broker_account_cross_scope_refused(self):
        a,b,c=example()
        b['account']='DU_FOREIGN'
        with self.assertRaisesRegex(ValueError,'EXPOSURE_BROKER_ACCOUNT_MISMATCH'):
            reconcile(a,b,c,NOW)

    def test_period_and_journal_fingerprint_mismatch_refused(self):
        a,b,c=example()
        b['period_sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'EXPOSURE_PERIOD_OR_JOURNAL_UNBOUND'):
            reconcile(a,b,c,NOW)
        b['period_sha256']='a'*64
        b['journal_fingerprint']=[0,0,0,0]
        with self.assertRaisesRegex(ValueError,'EXPOSURE_PERIOD_OR_JOURNAL_UNBOUND'):
            reconcile(a,b,c,NOW)

    def test_stale_or_future_snapshots_refused(self):
        a,b,c=example()
        a['observed_at_ms']=NOW-60001
        with self.assertRaisesRegex(ValueError,'EXPOSURE_PNL_STALE_OR_FUTURE'):
            reconcile(a,b,c,NOW)
        a['observed_at_ms']=NOW-1000
        b['observed_at_ms']=NOW+1
        with self.assertRaisesRegex(ValueError,'EXPOSURE_NAV_STALE_OR_FUTURE'):
            reconcile(a,b,c,NOW)

    def test_pending_order_refused_even_with_matching_holdings(self):
        a,b,c=example()
        a['observation_details']['EUR.USD']['active_order_ids']=['canary']
        with self.assertRaisesRegex(ValueError,'EXPOSURE_PENDING_BROKER_ORDERS'):
            reconcile(a,b,c,NOW)

    def test_insufficient_broker_source_and_nonfinite_values_refused(self):
        a,b,c=example()
        b['authoritative']=False
        with self.assertRaisesRegex(ValueError,'EXPOSURE_ACCOUNT_SOURCE_UNREADY'):
            reconcile(a,b,c,NOW)
        b['authoritative']=True
        c['position']=float('nan')
        with self.assertRaisesRegex(ValueError,'UNPROVED_CASH_EXPOSURE'):
            reconcile(a,b,c,NOW)

    def test_no_fx_component_is_not_interpreted_as_flat(self):
        a,b,c=example()
        a['components']=[]
        with self.assertRaisesRegex(ValueError,'EXPOSURE_FX_COMPONENT_AMBIGUOUS'):
            reconcile(a,b,c,NOW)


    def test_exact_byte_digest_and_duplicate_key_rejection(self):
        import hashlib,tempfile,json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'state.json'
            raw=b'{"k":1, "nested":{"y":2}}'
            p.write_bytes(raw)
            document,original_sha=read_exact_input(p)
            self.assertEqual(document['nested']['y'],2)
            self.assertEqual(original_sha,hashlib.sha256(raw).hexdigest())
            p.write_text('{"k":100}')
            self.assertNotEqual(original_sha,hashlib.sha256(p.read_bytes()).hexdigest())
            p.write_text('{"k":1,"k":2}')
            with self.assertRaisesRegex(ValueError,'EXPOSURE_DUPLICATE_JSON_KEY'):
                read_exact_input(p)
            p.write_text('{"x":NaN}')
            with self.assertRaisesRegex(ValueError,'EXPOSURE_NONFINITE_JSON'):
                read_exact_input(p)



if __name__=='__main__':
    unittest.main()
