"""Same-socket read-only IB FX/NLV witness: fail closed on time or identity drift."""
import copy
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'tests/python'))
from hepta_account_observer import SendBoundary
from hepta_account_pnl import normalize_nav,validate_nav_evidence,digest
from hepta_pnl_accounting import EvidenceError
from test_account_pnl import period,raw_snapshot

def stale_rate_with_fresh_witness():
    raw=raw_snapshot()
    for row in raw['rows']:
        if (row['request_id']==1 and row['key']=='ExchangeRate'
                and row['currency']=='EUR'):
            row['value']='1.2'
    at=next(r['observed_at_ms'] for r in raw['rows'] if
            r['request_id']==2 and r['key']=='NetLiquidation')
    raw['allowed_send_counts']={'71':1,'59':1,'1':1,'76':2,'74':1,
                                 '77':2,'75':1,'2':1}
    raw['fx_quote_witness']={
        'schema':'heptatrader.account-same-socket-eur-quote.v1',
        'request_id':4,'contract':'EUR|CASH|IDEALPRO|USD',
        'market_data_type':1,'source':'IB_LIVE_BID_ASK_TICK_PRICE',
        'bid':'1.09999','ask':'1.10001',
        'bid_observed_at_ms':at-1,'ask_observed_at_ms':at-1,
        'account_nav_observed_at_ms':at,'account_nav_value':'1203.90',
        'account':'DU_TEST','client_id':8950,'connection_id':'fixture-connection'}
    return raw

class SameSocketTemporalFxTests(unittest.TestCase):
    def test_accepts_real_time_bid_ask_mid_only_with_original_usd_one_bound(self):
        raw=stale_rate_with_fresh_witness()
        nav=normalize_nav(raw,period())
        self.assertTrue(nav['authoritative'])
        self.assertFalse(nav['mutation_authorized'])
        self.assertEqual(nav['nav'],'1203.90')
        self.assertEqual(nav['fx_reconciliation_bound_usd'],'1.00')
        self.assertEqual(nav['temporal_fx_eur_mid'],'1.10000')
        self.assertLessEqual(abs(Decimal(nav['temporal_fx_non_accrual_difference_usd'])),Decimal('1'))
        self.assertEqual(nav['temporal_fx_proof_raw_sha256'],digest(raw))
        validate_nav_evidence(nav,raw,period(),raw['observed_at_ms'])

    def test_rejects_missing_or_inconsistent_source(self):
        original=stale_rate_with_fresh_witness()
        changes=[
            ('no_quote',lambda r:r.pop('fx_quote_witness')),
            ('future_bid',lambda r:r['fx_quote_witness'].update(bid_observed_at_ms=r['observed_at_ms']+1)),
            ('stale_ask',lambda r:r['fx_quote_witness'].update(ask_observed_at_ms=r['started_at_ms']-5100)),
            ('delayed_mode',lambda r:r['fx_quote_witness'].update(market_data_type=3)),
            ('foreign_client',lambda r:r['fx_quote_witness'].update(client_id=8002)),
            ('foreign_connection',lambda r:r['fx_quote_witness'].update(connection_id='foreign')),
            ('wrong_account',lambda r:r['fx_quote_witness'].update(account='DU_OTHER')),
            ('wrong_nlv',lambda r:r['fx_quote_witness'].update(account_nav_value='1204.90')),
            ('wrong_contract',lambda r:r['fx_quote_witness'].update(contract='GBP|CASH|IDEALPRO|USD')),
            ('missing_send',lambda r:r['allowed_send_counts'].pop('1')),
            ('wide_spread',lambda r:r['fx_quote_witness'].update(ask='1.11100')),
            ('disagreeing_mid',lambda r:r['fx_quote_witness'].update(bid='1.07999',ask='1.08001')),
            ('future_nlv_callback',lambda r:r['fx_quote_witness'].update(account_nav_observed_at_ms=r['observed_at_ms']+1)),
        ]
        for name,change in changes:
            with self.subTest(name=name):
                bad=copy.deepcopy(original);change(bad)
                with self.assertRaises(EvidenceError):
                    normalize_nav(bad,period())

    def test_quote_after_nlv_cannot_backdate_mark(self):
        r=stale_rate_with_fresh_witness()
        q=r['fx_quote_witness']
        q['bid_observed_at_ms']=q['account_nav_observed_at_ms']+2
        q['ask_observed_at_ms']=q['account_nav_observed_at_ms']+2
        with self.assertRaises(EvidenceError):normalize_nav(r,period())

    def test_rejected_valuation_remains_rejected_if_usd_diff_above_one(self):
        r=stale_rate_with_fresh_witness()
        r['fx_quote_witness'].update(bid='1.07999',ask='1.08001')
        with self.assertRaisesRegex(EvidenceError,'ACCOUNT_NAV_FX_RECONCILIATION_FAILED'):
            normalize_nav(r,period())

    def test_unpaired_other_material_currency_refuses_eur_only_witness(self):
        r=stale_rate_with_fresh_witness()
        # Add a fully balanced GBP 50 at 1.25 USD. EUR midpoint would fix
        # the stale EUR rate; a EUR-only witness must still be rejected,
        # because GBP has an independently material conversion requirement.
        template={row['key']:row['value'] for row in r['rows']
                  if row['request_id']==1 and row['currency']=='USD'}
        template.update(AccountOrGroup='DU_TEST',RealCurrency='GBP',
                        CashBalance='50',TotalCashBalance='50',
                        AccruedCash='0',NetLiquidationByCurrency='50',
                        ExchangeRate='1.25',Cryptocurrency='0')
        template.update({k:'0' for k in
                         ('StockMarketValue','OptionMarketValue',
                          'FutureOptionValue','FundValue',
                          'MutualFundValue','MoneyMarketFundValue',
                          'CorporateBondValue','TBondValue') if k in template})
        for key,val in template.items():
            r['rows'].append({'request_id':1,'account':'DU_TEST',
                'model':'','key':key,'value':val,'currency':'GBP',
                'observed_at_ms':r['started_at_ms']+1})
        for row in r['rows']:
            if row['request_id']==1 and row['currency']=='BASE':
                if row['key'] in ('CashBalance','TotalCashBalance'):
                    row['value']='1172.5'
                elif row['key']=='NetLiquidationByCurrency':
                    row['value']='1266.4'
            if row['request_id']==2 and row['key']=='NetLiquidation':
                row['value']='1266.4'
        r['fx_quote_witness']['account_nav_value']='1266.4'
        with self.assertRaisesRegex(EvidenceError,'ACCOUNT_NAV_FX_RECONCILIATION_FAILED'):
            normalize_nav(r,period())

    def test_send_boundary_has_no_order_or_delayed_market_data_path(self):
        r={'ledger':1,'account':2,'positions':3}
        b=SendBoundary('DU_TEST',8950,r,allow_fx_quote=True)
        # All checks are against the pinned SDK wire layout.
        def wire(fields):return '\0'.join(fields)+'\0'
        start=wire(['71','2','8950'])
        self.assertEqual(b.check(start),71)
        self.assertEqual(b.check(wire(['59','1','1'])),59)
        quote=['1','11','4','0','EUR','CASH','','0.0','','','IDEALPRO','','USD','','','0','','0','0']
        self.assertEqual(b.check(wire(quote)),1)
        with self.assertRaises(EvidenceError):b.check(wire(['3','1','4']))
        with self.assertRaises(EvidenceError):b.check(wire(quote))
        for i,v in [(4,'GBP'),(10,'SMART'),(17,'1')]:
            changed=list(quote);changed[i]=v
            with self.assertRaises(EvidenceError):b.check(wire(changed))
        self.assertEqual(b.check(wire(['2','2','4'])),2)
        b0=SendBoundary('DU_TEST',8950,r)
        self.assertEqual(b0.check(start),71)
        with self.assertRaises(EvidenceError):b0.check(wire(quote))

if __name__=='__main__':unittest.main()
