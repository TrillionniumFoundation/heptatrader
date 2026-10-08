"""Executable account accounting/collector/import tests. All fixtures are synthetic."""
from __future__ import annotations
import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import hepta_account_pnl as a
import hepta_account_activity as activity
import hepta_account_observer as observer
import hepta_pnl_report as report
from hepta_pnl_accounting import EvidenceError

T = 1791244800000

def period():
    return {'schema':'heptatrader.pnl-period.v1','kind':'USER_RESET','period_id':'fixture-period',
            'account':'DU_TEST','currency':'USD','execution_domain':'PAPER','started_at_ms':T,
            'instruments':{name:{'agent_id':'fixture-agent','session_id':'fixture-'+name,
                                'broker_client_id':701,'opening_quantity':'0'} for name in ('EUR.USD','BTC.USD')}}


def raw_snapshot(start=T+100, end=T+120):
    def ledger(currency,cash,accrued,crypto,fx):
        values = {key:'0.00' for key in (*a.NAV_KEYS,*a.ASSET_KEYS)}
        values.update(CashBalance=cash,TotalCashBalance=cash,AccruedCash=accrued,Cryptocurrency=crypto,
                      ExchangeRate=fx,NetLiquidationByCurrency=str(Decimal(cash)+Decimal(accrued)+Decimal(crypto)),
                      AccountOrGroup='DU_TEST',RealCurrency='USD' if currency=='BASE' else currency)
        return values
    ledgers={'USD':ledger('USD','1000','-5','100','1'),
             'EUR':ledger('EUR','100','-1','0','1.1'),
             'BASE':ledger('BASE','1110','-6.1','100','1')}
    rows=[]
    def add(req,key,value,currency):rows.append(dict(request_id=req,account='DU_TEST',model='',key=key,value=value,currency=currency,observed_at_ms=start+1))
    for currency,fields in ledgers.items():
        for k,v in fields.items():add(1,k,v,currency)
    for key,value,ccy in [('AccountCode','DU_TEST',''),('AccountReady','true',''),('NetLiquidation','1203.90','USD'),('NLVAndMarginInReview','false',''),('AccruedDividend','0','USD')]:add(2,key,value,ccy)
    # Deliberate aggregate/currency collision in the full subscription.
    add(2,'AccruedCash','-6.1','USD');add(2,'AccruedCash','-5','USD')
    return {'schema':'heptatrader.account-download.v1','read_only':True,'account':'DU_TEST','period_id':'fixture-period',
            'started_at_ms':start,'observed_at_ms':end,'client_id':8950,'connection_id':'fixture-connection',
            'request_ids':{'ledger':1,'account':2,'positions':3},'ends':[1,2,3],
            'rows':rows,'positions':[{'request_id':3,'account':'DU_TEST','model':'','conid':123,'symbol':'BTC','sec_type':'CRYPTO','currency':'USD','quantity':'0.001','average_cost':'100000'}],
            'transport_errors':[],'connection_lost_during_download':False}


def model():
    return {'schema':'heptatrader.exit-cost-model.v1','currency':'USD','version':'fixture-v1',
            'assumed_not_account_qualified':True,'rebates_assumed':False,
            'BTC.USD':{'rate':'0.0018','minimum_usd':'1.75'},'FX':{'rate':'0.00002','minimum_usd':'2'},
            'scenarios':{'base':{'all_in_price_impact_bps':'2','new_order_count_multiplier':1},
                         'stress':{'all_in_price_impact_bps':'10','new_order_count_multiplier':2}}}


def xml_fixture(extra='', nav_overrides=None, cash_rows=''):
    values={'accountId':'DU_TEST','currency':'USD','fromDate':'20261006','toDate':'20261006',
            'startingValue':'1000','endingValue':'1253','mtm':'10','depositsWithdrawals':'250',
            'internalCashTransfers':'0','assetTransfers':'0','linkingAdjustments':'0',
            'interest':'-3','changeInInterestAccruals':'-1','commissions':'-2','withholdingTax':'-1'}
    for k,v in (nav_overrides or {}).items():
        if v is None:values.pop(k,None)
        else:values[k]=v
    attrs=' '.join(k+'="'+v+'"' for k,v in values.items())
    if not cash_rows:
        cash_rows='<CashTransaction accountId="DU_TEST" transactionID="deposit-1" dateTime="20261006;120000" type="Deposits/Withdrawals" currency="USD" fxRateToBase="1" amount="250"/>'
    return ('<FlexQueryResponse type="AF"><FlexStatements><FlexStatement accountId="DU_TEST" fromDate="20261006" toDate="20261006">'
            '<AccountInformation accountId="DU_TEST" currency="USD"/><ChangeInNAV '+attrs+'/>'
            '<CashTransactions>'+cash_rows+'</CashTransactions>'+extra+'</FlexStatement></FlexStatements></FlexQueryResponse>').encode()


class NavTests(unittest.TestCase):
    def setUp(self): self.p=period();self.raw=raw_snapshot()
    def nav(self): return a.normalize_nav(self.raw,self.p)
    def mutate_row(self,key,currency,value,request=1):
        for r in self.raw['rows']:
            if r['key']==key and r['currency']==currency and r['request_id']==request:r['value']=value
    def test_complete_nav_reconciles_and_includes_accruals_once(self):
        n=self.nav();self.assertEqual(Decimal(n['nav']),Decimal('1203.90'));self.assertEqual(Decimal(n['accrued_interest']),Decimal('-6.1'))
        self.assertEqual(Decimal(n['cash'])+Decimal(n['marked_assets'])+Decimal(n['accrued_interest']),Decimal(n['nav']))
        self.assertTrue(n['nav_includes_reported_accruals']);self.assertFalse(n['mutation_authorized'])
    def test_daily_interest_fx_translation_does_not_fake_current_rate_nav_failure(self):
        self.mutate_row('AccruedCash','BASE','-10')
        self.mutate_row('NetLiquidationByCurrency','BASE','1200')
        self.mutate_row('NetLiquidation','USD','1200',2)
        n=self.nav()
        self.assertEqual(n['nav'],'1200')
        self.assertEqual(n['accrued_interest'],'-10')
        self.assertEqual(n['translated_currency_nav'],'1203.9')
        self.assertEqual(n['fx_reconciliation_difference'],'3.9')
        self.assertEqual(n['translated_non_accrual_nav'],'1210.0')
        self.assertEqual(n['non_accrual_fx_reconciliation_difference'],'0')
        self.assertEqual(n['accrued_interest_current_rate_translation_difference'],'3.9')
        self.assertEqual(n['fx_reconciliation_acceptance_basis'],
                         'CURRENT_RATE_NON_ACCRUAL_PLUS_BROKER_BASE_ACCRUED_CASH')
    def test_interest_exception_does_not_relax_non_accrual_fx_mismatch(self):
        self.mutate_row('AccruedCash','BASE','-10')
        self.mutate_row('NetLiquidationByCurrency','BASE','1200')
        self.mutate_row('NetLiquidation','USD','1200',2)
        self.mutate_row('CashBalance','EUR','102')
        self.mutate_row('TotalCashBalance','EUR','102')
        self.mutate_row('NetLiquidationByCurrency','EUR','101')
        with self.assertRaisesRegex(EvidenceError,'FX_RECONCILIATION'):self.nav()
    def test_base_sentinel_resolves_only_through_exact_account_currency(self):
        expected=self.nav()
        self.mutate_row('RealCurrency','BASE','BASE')
        self.assertEqual(self.nav(), {**expected, 'raw_sha256':a.digest(self.raw)})
    def test_base_sentinel_requires_independent_currency_identity(self):
        self.mutate_row('RealCurrency','BASE','BASE')
        for value in ('EUR','BASE',''):
            with self.subTest(value=value):
                self.mutate_row('RealCurrency','USD',value)
                with self.assertRaisesRegex(EvidenceError,'BASE_IDENTITY'):self.nav()
        self.raw['rows']=[r for r in self.raw['rows'] if not(r['request_id']==1 and r['currency']=='USD')]
        with self.assertRaisesRegex(EvidenceError,'BASE_IDENTITY'):self.nav()
    def test_base_sentinel_does_not_relax_fx_reconciliation(self):
        self.mutate_row('RealCurrency','BASE','BASE')
        self.mutate_row('ExchangeRate','EUR','1.2')
        with self.assertRaisesRegex(EvidenceError,'FX_RECONCILIATION'):self.nav()
    def test_non_sentinel_wrong_base_currency_still_rejected(self):
        for value in ('EUR','', 'base'):
            self.mutate_row('RealCurrency','BASE',value)
            with self.subTest(value=value),self.assertRaisesRegex(EvidenceError,'BASE_IDENTITY'):self.nav()
    def test_aggregate_currency_collision_is_not_last_write_wins(self):
        n=self.nav();self.assertEqual(n['ledgers']['USD']['AccruedCash'],'-5');self.assertEqual(n['accrued_interest'],'-6.1')
        self.raw['rows'].reverse();self.assertEqual(self.nav()['nav'],n['nav'])
    def test_prefixed_currency_keys_supported_without_gateway_reconfiguration(self):
        for row in self.raw['rows']:
            if row['request_id']==1:row['key']='$LEDGER-'+row['key']
        self.assertTrue(self.nav()['authoritative'])
    def test_wrong_account_model_request_and_time_fail_closed(self):
        for field,value in [('account','DU_OTHER'),('model','model-1'),('request_id',4),('observed_at_ms',T-1)]:
            with self.subTest(field=field):
                r=copy.deepcopy(self.raw);r['rows'][0][field]=value
                with self.assertRaises(EvidenceError):a.normalize_nav(r,self.p)
    def test_missing_or_boolean_end_marker_does_not_prove_download(self):
        for ends in ([1],[2],[True,2,3],None):
            r=copy.deepcopy(self.raw);r['ends']=ends
            with self.subTest(ends=ends),self.assertRaises((EvidenceError,TypeError)):a.normalize_nav(r,self.p)
    def test_reused_trader_client_id_is_refused(self):
        self.raw['client_id']=701
        with self.assertRaises(EvidenceError):self.nav()
    def test_wrong_period_and_live_account_refused(self):
        for k,v in [('period_id','other'),('account','U_LIVE'),('read_only',False),('connection_id','')]:
            r=copy.deepcopy(self.raw);r[k]=v
            with self.subTest(k=k),self.assertRaises(EvidenceError):a.normalize_nav(r,self.p)
    def test_bad_download_interval_and_disconnect_are_not_fresh(self):
        for k,v in [('observed_at_ms',T-1),('started_at_ms',True),('connection_lost_during_download',True),('transport_errors',[{'code':1100}])]:
            r=copy.deepcopy(self.raw);r[k]=v
            with self.subTest(k=k),self.assertRaises(EvidenceError):a.normalize_nav(r,self.p)
    def test_account_and_currency_dividend_accruals_must_agree(self):
        self.mutate_row('AccruedDividend','USD','10',2)
        with self.assertRaises(EvidenceError):self.nav()
    def test_missing_account_ready_not_zero_nav(self):
        self.raw['rows']=[x for x in self.raw['rows'] if x['key']!='AccountReady']
        with self.assertRaises(EvidenceError):self.nav()
    def test_under_review_or_unknown_review_is_rejected(self):
        for val in ('true','UNKNOWN'):
            self.mutate_row('NLVAndMarginInReview','',val,2)
            with self.assertRaises(EvidenceError):self.nav()
    def test_incomplete_currency_does_not_disappear_from_total(self):
        self.raw['rows']=[x for x in self.raw['rows'] if not(x['request_id']==1 and x['key']=='CashBalance' and x['currency']=='EUR')]
        with self.assertRaises(EvidenceError):self.nav()
    def test_conflicting_ledger_rows_are_not_overwritten(self):
        row=copy.deepcopy(self.raw['rows'][0]);row['value']='123';self.raw['rows'].append(row)
        with self.assertRaises(EvidenceError):self.nav()
    def test_exact_duplicates_in_same_download_are_inert(self):
        self.raw['rows'].append(copy.deepcopy(self.raw['rows'][0]));self.assertEqual(self.nav()['cash'],'1110')
    def test_nonfinite_boolean_and_unset_numbers_refused(self):
        for val in ('NaN','Infinity','1e308','true'):
            self.mutate_row('NetLiquidation','USD',val,2)
            with self.subTest(val=val),self.assertRaises(EvidenceError):self.nav()
    def test_fx_conversion_and_base_identity_are_validated(self):
        for value in ('0','-1','2'):
            self.mutate_row('ExchangeRate','USD',value)
            with self.subTest(value=value),self.assertRaises(EvidenceError):self.nav()
    def test_unexplained_nav_component_is_not_profit(self):
        self.mutate_row('NetLiquidationByCurrency','EUR','200')
        with self.assertRaises(EvidenceError):self.nav()
    def test_full_account_nav_must_agree_with_currency_total(self):
        self.mutate_row('NetLiquidation','USD','1300',2)
        with self.assertRaises(EvidenceError):self.nav()
    def test_missing_position_end_does_not_invent_liquidation_fees(self):
        self.raw['ends']=[1,2];n=self.nav();self.assertTrue(n['authoritative'])
        self.assertFalse(a.liquidation_estimate(n,model())['complete'])
    def test_duplicated_positions_rejected(self):
        self.raw['positions']*=2
        with self.assertRaises(EvidenceError):self.nav()
    def test_stale_nav_does_not_continue_as_current(self):
        n=self.nav()
        with self.assertRaises(EvidenceError):a.validate_nav(n,self.p,n['valid_until_ms'])
    def test_cash_only_reset_does_not_become_opening_nav(self):
        r=a.account_report(self.p,self.nav(),None,model(),None,T+130)
        self.assertTrue(r['nav_complete']);self.assertIsNone(r['broker_reported_account_net_pnl']);self.assertIsNone(r['all_in_account_net_pnl'])
    def test_first_late_observation_change_is_not_profit(self):
        n=self.nav();first=copy.deepcopy(n);first['nav']='1100'
        r=a.account_report(self.p,n,None,model(),first,T+130)
        self.assertFalse(r['observed_nav_change']['is_profit']);self.assertIsNone(r['all_in_account_net_pnl'])


class ExitCostTests(unittest.TestCase):
    def setUp(self):self.n=a.normalize_nav(raw_snapshot(),period());self.m=model()
    def test_estimate_is_distinct_from_actual_charges(self):
        r=a.liquidation_estimate(self.n,self.m)
        self.assertTrue(r['complete']);self.assertFalse(r['actual_expense']);self.assertFalse(r['mutation_authorized'])
        self.assertEqual(Decimal(r['estimated_future_exit_commissions']),Decimal('3.75'))
        self.assertEqual(self.n['nav'],'1203.90')
    def test_large_virtual_fx_position_not_double_counted(self):
        self.n['positions'].append(dict(conid=5,symbol='EUR',sec_type='CASH',currency='USD',quantity='999999999'))
        r=a.liquidation_estimate(self.n,self.m);self.assertEqual(len(r['rows']),2)
        self.assertEqual(r['estimated_future_exit_commissions'],'3.75')
    def test_dust_is_not_implicitly_free(self):
        self.n['ledgers']['EUR']['CashBalance']='0.00001'
        r=a.liquidation_estimate(self.n,self.m);self.assertEqual(r['estimated_future_exit_commissions'],'3.75')
        self.assertIn('DUST_CONVERSION_FEASIBILITY_NOT_PROVEN',r['reason_codes'])
    def test_partial_new_orders_change_minimum_fee_estimate(self):
        r=a.liquidation_estimate(self.n,self.m);base,stress=r['scenarios']
        self.assertEqual(Decimal(stress['commissions']),2*Decimal(base['commissions']))
    def test_does_not_assume_future_crypto_rebate(self):
        self.m['rebates_assumed']=True
        with self.assertRaises(EvidenceError):a.liquidation_estimate(self.n,self.m)
    def test_unsupported_assets_block_all_asset_exit_total(self):
        self.n['positions'].append(dict(conid=9,symbol='XYZ',sec_type='STK',currency='USD',quantity='2'))
        self.assertFalse(a.liquidation_estimate(self.n,self.m)['complete'])
    def test_missing_model_is_unknown_not_zero(self):self.assertIsNone(a.liquidation_estimate(self.n,None)['estimated_future_exit_commissions'])
    def test_invalid_model_rates_and_scenarios_fail(self):
        for path,value in [('rate','-0.1'),('minimum_usd','-1')]:
            m=copy.deepcopy(self.m);m['BTC.USD'][path]=value
            with self.assertRaises(EvidenceError):a.liquidation_estimate(self.n,m)
        self.m['scenarios']['base']['new_order_count_multiplier']=True
        with self.assertRaises(EvidenceError):a.liquidation_estimate(self.n,self.m)


class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.p=period();self.open=a.normalize_nav(raw_snapshot(T-20,T),self.p)
        self.close=a.normalize_nav(raw_snapshot(T+80,T+100),self.p)
        self.close['nav']='1503.90'
        self.f={'schema':'heptatrader.external-flows.v1','account':'DU_TEST','currency':'USD',
                'source':'IMPORTED_IB_FLEX_ACTIVITY','complete':True,'cash_transfers_complete':True,
                'position_transfers_complete':True,'from_ms':T-100,'to_ms':T+1000,'source_sha256':'a'*64,
                'events':[{'id':'deposit','account':'DU_TEST','kind':'CASH_TRANSFER','at_ms':T+50,'currency':'USD','signed_value_usd':'250'}]}
    def calc(self,liabilities=None):return a.reconcile_nav_return(self.p,self.open,self.close,self.f,liabilities,T+110)
    def test_deposit_not_counted_as_profit(self):self.assertEqual(self.calc()['broker_reported_account_net_pnl'],'50.00')
    def test_withdrawal_and_asset_transfer_not_losses(self):
        self.f['events'][0].update(kind='POSITION_TRANSFER',signed_value_usd='-250')
        self.assertEqual(self.calc()['broker_reported_account_net_pnl'],'550.00')
    def test_closed_nav_already_contains_fees_no_second_subtraction(self):
        self.close['nav']='1499.90';self.close['accrued_interest']='-10.1'
        r=self.calc();self.assertEqual(r['broker_reported_account_net_pnl'],'46.00');self.assertEqual(r['accrued_interest_change'],'-4.0')
    def test_interest_posting_does_not_double_charge_accrual_release(self):
        self.close['nav']=self.open['nav'];self.close['cash']=str(Decimal(self.open['cash'])-Decimal('6.1'));self.close['accrued_interest']='0'
        self.f['events']=[];self.assertEqual(Decimal(self.calc()['broker_reported_account_net_pnl']),0)
    def test_no_flow_file_is_not_zero_external_funding(self):
        self.f['complete']=False
        with self.assertRaises(EvidenceError):self.calc()
    def test_missing_flow_fields_or_partial_coverage_rejected(self):
        for k,v in [('cash_transfers_complete',None),('position_transfers_complete',False),('to_ms',T+99),('account','DU_OTHER')]:
            f=copy.deepcopy(self.f);f[k]=v
            with self.subTest(k=k),self.assertRaises(EvidenceError):a.reconcile_nav_return(self.p,self.open,self.close,f,None,T+110)
    def test_first_later_nav_cannot_backfill_reset(self):
        self.open['started_at_ms']=T+1;self.open['observed_at_ms']=T+10
        with self.assertRaises(EvidenceError):self.calc()
    def test_exact_flow_duplicate_inert_and_conflict_rejected(self):
        self.f['events']*=2;self.assertEqual(self.calc()['broker_reported_account_net_pnl'],'50.00')
        self.f['events'][-1]=dict(self.f['events'][-1],signed_value_usd='251')
        with self.assertRaises(EvidenceError):self.calc()
    def test_personal_tax_unknown_without_evidence(self):
        r=self.calc();self.assertIsNone(r['personal_income_tax']);self.assertIsNone(r['all_in_account_net_pnl'])
    def liabilities(self):
        return {'schema':'heptatrader.unbooked-liabilities.v1','account':'DU_TEST','currency':'USD','period_id':'fixture-period',
                'complete':True,'personal_tax_coverage_complete':True,'other_cost_coverage_complete':True,
                'all_values_excluded_from_broker_nav':True,'from_ms':T,'to_ms':T+1000,'source_sha256':'b'*64,
                'personal_tax_liability_change':'10','other_unbooked_cost_change':'3'}
    def test_proven_outside_nav_liabilities_deducted_once(self):
        r=self.calc(self.liabilities());self.assertTrue(r['all_in_complete']);self.assertEqual(r['all_in_account_net_pnl'],'37.00')
    def test_explicit_zero_tax_coverage_not_same_as_missing(self):
        x=self.liabilities();x.update(personal_tax_liability_change='0',other_unbooked_cost_change='0')
        self.assertEqual(self.calc(x)['all_in_account_net_pnl'],'50.00');x.pop('personal_tax_liability_change')
        with self.assertRaises(EvidenceError):self.calc(x)
    def test_broker_booked_tax_cannot_be_subtracted_again(self):
        x=self.liabilities();x['all_values_excluded_from_broker_nav']=False
        with self.assertRaises(EvidenceError):self.calc(x)


class FlexTests(unittest.TestCase):
    def parse(self,raw=None):return activity.parse_flex(raw or xml_fixture(),'DU_TEST','USD','UTC',T+2*86400000)
    def test_current_calendar_day_is_not_closed_statement_coverage(self):
        x=activity.parse_flex(xml_fixture(),'DU_TEST','USD','UTC',T+13*3600000)
        self.assertFalse(x['statements'][0]['complete'])
        self.assertIn('STATEMENT_PERIOD_NOT_CLOSED_AS_OF_IMPORT',x['statements'][0]['reason_codes'])
        with self.assertRaises(EvidenceError):activity.derive_external_flows(x,T+1,T+12*3600000)
    def test_nav_change_and_funding_bridge(self):
        r=self.parse()['statements'][0];self.assertTrue(r['complete']);self.assertEqual(r['broker_reported_account_net_pnl'],'3')
        self.assertEqual(r['components']['interest'],'-3');self.assertEqual(r['components']['withholdingTax'],'-1')
    def test_interest_accrual_release_not_duplicate_expense(self):
        r=self.parse(xml_fixture(nav_overrides={'interest':'-3','changeInInterestAccruals':'3','endingValue':'1257'}))['statements'][0]
        self.assertTrue(r['complete']);self.assertEqual(r['broker_reported_account_net_pnl'],'7')
    def test_omitted_funding_is_unknown_not_zero(self):
        r=self.parse(xml_fixture(nav_overrides={'assetTransfers':None}))['statements'][0]
        self.assertFalse(r['complete']);self.assertIsNone(r['broker_reported_account_net_pnl'])
    def test_unbalanced_statement_does_not_assert_return(self):
        r=self.parse(xml_fixture(nav_overrides={'endingValue':'999'}))['statements'][0]
        self.assertFalse(r['complete'])
    def test_unknown_nav_field_fail_closed(self):
        r=self.parse(xml_fixture(nav_overrides={'unrecognizedAdjustment':'10'}))['statements'][0]
        self.assertFalse(r['complete'])
    def test_date_range_is_explicit_timezone_with_end_exclusive(self):
        r=self.parse()['statements'][0];self.assertEqual(r['to_ms']-r['from_ms'],86400000);self.assertEqual(r['timezone'],'UTC')
    def test_trade_confirmations_not_full_activity(self):
        with self.assertRaises(EvidenceError):self.parse(xml_fixture().replace(b'type="AF"',b'type="TC"'))
    def test_entity_and_doctype_bombs_rejected_before_parse(self):
        with self.assertRaises(EvidenceError):self.parse(b'<!DOCTYPE x [<!ENTITY a "b">]>'+xml_fixture())
    def test_wrong_account_does_not_use_other_accounts_rows(self):
        with self.assertRaises(EvidenceError):activity.parse_flex(xml_fixture(),'DU_OTHER','USD','UTC')
    def test_duplicate_alias_components_rejected(self):
        with self.assertRaises(EvidenceError):self.parse(xml_fixture(nav_overrides={'markToMarket':'10'}))
    def test_missing_cash_fx_rates_never_default_one(self):
        with self.assertRaises(EvidenceError):self.parse(xml_fixture().replace(b'fxRateToBase="1"',b''))
    def test_foreign_currency_fee_conversion(self):
        raw=xml_fixture(cash_rows='<CashTransaction accountId="DU_TEST" transactionID="fee" dateTime="20261006;120000" type="Other Fees" currency="EUR" fxRateToBase="1.1" amount="-2"/>')
        tx=self.parse(raw)['cash_transactions'][0];self.assertEqual(tx['amount_usd'],'-2.2');self.assertEqual(tx['category'],'other_fees')
    def test_cash_transaction_duplicate_id_conflict(self):
        tx='<CashTransaction transactionID="a" dateTime="20261006;120000" type="Other Fees" currency="USD" fxRateToBase="1" amount="-1"/>'
        raw=xml_fixture(cash_rows=tx+tx.replace('amount="-1"','amount="-2"'))
        with self.assertRaises(EvidenceError):self.parse(raw)
    def test_reimport_idempotent_not_double_counted(self):
        x=self.parse();m=activity.merge_activity(x,x);self.assertEqual(len(m['statements']),1);self.assertEqual(len(m['cash_transactions']),1)
    def test_conflicting_statement_revision_refused(self):
        x=self.parse();y=self.parse(xml_fixture(nav_overrides={'interest':'-4','endingValue':'1252'}))
        with self.assertRaises(EvidenceError):activity.merge_activity(x,y)
    def test_closed_statement_not_inferred_current_reset_coverage(self):
        x=self.parse();r=a.account_report(period(),a.normalize_nav(raw_snapshot(),period()),x,None,None,T+130)
        self.assertIsNone(r['broker_reported_account_net_pnl']);self.assertIn('STATEMENT_DOES_NOT_COVER_RESET_TO_LIVE_NAV',r['reason_codes'])
    def test_cash_detail_control_produces_explicit_flows(self):
        x=self.parse();s=x['statements'][0];f=activity.derive_external_flows(x,s['from_ms']+1,s['to_ms']-1)
        self.assertTrue(f['complete']);self.assertEqual(f['events'][0]['signed_value_usd'],'250')
    def test_unsupported_asset_transfers_do_not_become_profit(self):
        x=self.parse(xml_fixture(nav_overrides={'assetTransfers':'1','endingValue':'1254'}));s=x['statements'][0]
        with self.assertRaises(EvidenceError):activity.derive_external_flows(x,s['from_ms']+1,s['to_ms']-1)
    def test_missing_cash_detail_section_not_proof_of_no_deposits(self):
        x=self.parse();x['source_sections'].remove('CashTransactions');s=x['statements'][0]
        with self.assertRaises(EvidenceError):activity.derive_external_flows(x,s['from_ms']+1,s['to_ms']-1)
    def test_missing_deposit_details_block_control_reconciliation(self):
        x=self.parse();x['cash_transactions']=[];s=x['statements'][0]
        with self.assertRaises(EvidenceError):activity.derive_external_flows(x,s['from_ms']+1,s['to_ms']-1)
    def test_date_only_cash_rows_cannot_be_sorted_across_intraday_reset(self):
        with self.assertRaises(EvidenceError):activity.exact_transaction_time('20261006','UTC')
    def test_dst_fold_refused_not_guessed(self):
        with self.assertRaises(EvidenceError):activity.exact_transaction_time('20261101;013000','America/New_York')
    def test_private_import_preserves_raw_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'period.json';f=root/'statement.xml';out=root/'account'
            p.write_text(json.dumps(period()));p.chmod(0o600);f.write_bytes(xml_fixture());f.chmod(0o600)
            x=activity.import_flex(f,p,out,'UTC');activity.import_flex(f,p,out,'UTC')
            saved=out/'statement-sources'/(hashlib.sha256(f.read_bytes()).hexdigest()+'.xml')
            self.assertEqual(saved.read_bytes(),f.read_bytes());self.assertEqual(len(json.loads((out/'activity.json').read_text())['statements']),1)


class SendBoundaryTests(unittest.TestCase):
    def setUp(self):self.b=observer.SendBoundary('DU_TEST',8950,{'ledger':1,'account':2,'positions':3})
    def msg(self,*fields):return '\0'.join(map(str,fields))+'\0'
    def start(self):self.b.check(self.msg(71,2,8950,''))
    def test_only_exact_owned_account_reads_cross_boundary(self):
        self.start()
        for fields in [(76,1,1,'DU_TEST','',1),(76,1,2,'DU_TEST','',0),(74,1,3,'DU_TEST',''),(77,1,1),(77,1,2),(75,1,3)]:
            self.b.check(self.msg(*fields))
        self.assertEqual(self.b.counts,{'71':1,'76':2,'74':1,'77':2,'75':1})
    def test_place_cancel_global_cancel_exercise_and_whatif_all_denied(self):
        self.start()
        for code in (3,4,58,21,64,97):
            with self.subTest(code=code),self.assertRaises(EvidenceError):self.b.check(self.msg(code,1,159))
    def test_other_account_or_model_refused(self):
        self.start()
        for fields in [(76,1,1,'DU_OTHER','',1),(76,1,1,'DU_TEST','strategy-model',1),(74,1,3,'All','')]:
            with self.assertRaises(EvidenceError):self.b.check(self.msg(*fields))
    def test_other_client_handshake_refused(self):
        with self.assertRaises(EvidenceError):self.b.check(self.msg(71,2,701,''))
    def test_unowned_subscription_cancel_refused(self):
        self.start()
        with self.assertRaises(EvidenceError):self.b.check(self.msg(77,1,1))
        with self.assertRaises(EvidenceError):self.b.check(self.msg(77,1,999))
    def test_mutation_cannot_hide_behind_second_packet(self):
        self.start()
        with self.assertRaises(EvidenceError):self.b.check(self.msg(76,1,1,'DU_TEST','',1,3,1,159))
    def test_no_read_before_handshake_or_duplicate_start(self):
        with self.assertRaises(EvidenceError):self.b.check(self.msg(76,1,1,'DU_TEST','',1))
        self.start()
        with self.assertRaises(EvidenceError):self.start()
    def test_malformed_outbound_frame_refused(self):
        for msg in (None,b'71\0', 'no','a\0'):
            with self.assertRaises(EvidenceError):self.b.check(msg)


class IntegrationTests(unittest.TestCase):
    def test_account_render_never_calls_cash_profit(self):
        x=a.account_report(period(),a.normalize_nav(raw_snapshot(),period()),None,model(),None,T+130)
        text=report.render_account(x)
        self.assertIn('Broker NAV',text);self.assertIn('return=UNKNOWN',text);self.assertIn('ESTIMATE ONLY',text)
    def test_collector_detects_journal_change_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg=root/'config.json';pp=root/'period.json';jr=root/'execution/oms-journal.jsonl';jr.parent.mkdir();jr.write_text('{}\n')
            cfg.write_text(json.dumps({'schema':'heptatrader.account-observer-config.v1','sdk_path':'unused','client_id':8950}));cfg.chmod(0o600)
            pp.write_text(json.dumps(period()));pp.chmod(0o600)
            def changed(*args):jr.write_text('{}\n{}\n');return raw_snapshot()
            with patch.object(observer,'acquire',changed),self.assertRaises(EvidenceError):observer.collect_account(cfg,pp,root)
    def test_optional_account_failure_does_not_erase_strategy_data(self):
        # Account truth is added without relabelling the existing strategy scope.
        base={'scope':'STRATEGY_PERIOD_NOT_ACCOUNT_NAV','totals':{'net_total_pnl':'-5'}}
        self.assertEqual(base['totals']['net_total_pnl'],'-5')
        text=report.render(dict(base,period_id='fixture',account={'nav':None,'reason_codes':['NO_SOURCE']}))
        self.assertIn('NO_SOURCE',text);self.assertIn('UNKNOWN',text)

class SourceBackedDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg=self.root/'config';self.cfg.mkdir(mode=0o700)
        self.state=self.root/'state';(self.state/'execution').mkdir(parents=True,mode=0o700)
        self.out=self.state/'pnl/account';self.out.mkdir(parents=True,mode=0o700)
        self.pp=self.cfg/'pnl-period.json'
        self.pp.write_text(json.dumps(period()));self.pp.chmod(0o600)
        self.journal=self.state/'execution/oms-journal.jsonl';self.journal.write_text('{}\n');self.journal.chmod(0o600)
        self.raw=raw_snapshot();self.raw.update(journal_fingerprint=list(report.fingerprint(self.journal)),
                    period_sha256=hashlib.sha256(self.pp.read_bytes()).hexdigest())
        self.nav=a.normalize_nav(self.raw,period())
        self.save('raw-latest.json',self.raw);self.save('nav.json',self.nav)
    def tearDown(self):self.temp.cleanup()
    def save(self,name,value):
        p=self.out/name;p.write_text(json.dumps(value));p.chmod(0o600)
    def attach(self):
        with patch.object(report,'now_ms',lambda:T+130):
            return report.attach_account({'complete':True,'totals':{'net_total_pnl':'-5'}},self.pp,self.state,self.cfg)
    def test_current_nav_and_every_missing_prerequisite_are_separate(self):
        x=self.attach();self.assertEqual(x['totals']['net_total_pnl'],'-5');self.assertTrue(x['account']['nav_complete'])
        e=x['account']['evidence_status'];self.assertEqual(e['current_nav']['status'],'KNOWN')
        for k in ('opening_nav_at_reset','external_cash_and_asset_flows','posted_taxes_in_period','outside_nav_liabilities'):
            self.assertEqual(e[k]['status'],'UNKNOWN')
        self.assertIsNone(x['all_in_account_net_pnl'])
    def test_nav_without_raw_download_cannot_self_certify(self):
        (self.out/'raw-latest.json').unlink();x=self.attach();self.assertIsNone(x['account_nav'])
        self.assertIn('ACCOUNT_NAV_RAW_SOURCE_MISSING',x['account']['reason_codes'])
    def test_normalized_nav_tamper_rejected_even_with_original_digest(self):
        self.nav['nav']='999999';self.save('nav.json',self.nav)
        self.assertIsNone(self.attach()['account_nav'])
    def test_new_raw_with_old_nav_racing_publication_is_unavailable(self):
        self.raw['connection_id']='new-read';self.save('raw-latest.json',self.raw)
        self.assertIsNone(self.attach()['account_nav'])
    def test_activity_index_cannot_invent_source_coverage(self):
        x=activity.parse_flex(xml_fixture(),'DU_TEST','USD','UTC',T+2*86400000)
        self.save('activity.json',x);r=self.attach()
        self.assertTrue(r['account']['nav_complete']);self.assertIsNone(r['account']['broker_reported_account_net_pnl'])
    def test_invalid_cost_model_does_not_hide_valid_nav(self):
        p=self.cfg/'exit-cost-model.json';p.write_text('{bad');p.chmod(0o600)
        x=self.attach();self.assertTrue(x['account']['nav_complete']);self.assertFalse(x['account']['future_exit_costs']['complete'])
    def test_failing_cost_model_keeps_current_nav_not_previous_scenarios(self):
        m=model();m['BTC.USD']['rate']='-1'
        p=self.cfg/'exit-cost-model.json';p.write_text(json.dumps(m));p.chmod(0o600)
        x=self.attach();self.assertTrue(x['account']['nav_complete']);self.assertFalse(x['account']['future_exit_costs']['complete'])
    def test_late_first_observation_without_raw_is_not_profit_or_opening(self):
        name='first-'+hashlib.sha256(period()['period_id'].encode()).hexdigest()[:16]+'.json'
        self.save(name,self.nav);x=self.attach()
        self.assertNotIn('observed_nav_change',x['account']);self.assertIsNone(x['account']['broker_reported_account_net_pnl'])
    def test_stale_raw_and_nav_pair_does_not_remain_current(self):
        with patch.object(report,'now_ms',lambda:T+100000):
            x=report.attach_account({},self.pp,self.state,self.cfg)
        self.assertIsNone(x['account_nav'])
    def test_verified_opening_is_known_even_when_funding_sources_are_missing(self):
        self.save('return-inputs.json',{'opening_nav_raw':raw_snapshot(T-20,T)})
        x=self.attach()['account']
        self.assertEqual(x['evidence_status']['opening_nav_at_reset']['status'],'KNOWN')
        self.assertEqual(x['evidence_status']['external_cash_and_asset_flows']['status'],'UNKNOWN')
        self.assertIsNone(x['broker_reported_account_net_pnl'])
    def test_consumer_has_no_ledger_or_period_writes(self):
        before=(self.journal.read_bytes(),self.pp.read_bytes(),self.nav)
        self.attach();self.assertEqual((self.journal.read_bytes(),self.pp.read_bytes(),self.nav),before)
    def test_statement_section_from_other_period_cannot_prove_this_period(self):
        x=activity.parse_flex(xml_fixture(),'DU_TEST','USD','UTC',T+2*86400000)
        x['statements'][0]['source_sections'].remove('CashTransactions')
        with self.assertRaises(EvidenceError):activity.derive_external_flows(x,T+1,T+12*3600000)
    def test_retained_statement_is_reparsed_not_trusting_index_amounts(self):
        raw=xml_fixture();x=activity.parse_flex(raw,'DU_TEST','USD','UTC',T+2*86400000)
        src=self.out/'statement-sources';src.mkdir(mode=0o700)
        p=src/(hashlib.sha256(raw).hexdigest()+'.xml');p.write_bytes(raw);p.chmod(0o600)
        x['statements'][0]['broker_reported_account_net_pnl']='999'
        rebuilt=activity.verify_activity_sources(x,src,'DU_TEST',T+3*86400000)
        self.assertEqual(rebuilt['statements'][0]['broker_reported_account_net_pnl'],'3')
    def test_retained_statement_altered_bytes_rejected(self):
        raw=xml_fixture();x=activity.parse_flex(raw,'DU_TEST','USD','UTC',T+2*86400000)
        src=self.out/'statement-sources';src.mkdir(mode=0o700)
        p=src/(hashlib.sha256(raw).hexdigest()+'.xml');p.write_bytes(raw+b' ');p.chmod(0o600)
        with self.assertRaises(EvidenceError):activity.verify_activity_sources(x,src,'DU_TEST',T+3*86400000)
    def test_service_has_no_trading_activation_or_ledger_write_authority(self):
        text=(ROOT/'systemd/user/heptatrader-account-nav.service').read_text()
        self.assertNotIn('Requires=',text);self.assertNotIn('Wants=',text)
        self.assertIn('ProtectHome=read-only',text);self.assertIn('IPAddressDeny=any',text)
        writes=[l for l in text.splitlines() if l.startswith('ReadWritePaths=')]
        self.assertEqual(writes,['ReadWritePaths=%h/.local/state/heptatrader/pnl/account'])

class AccountCaptureCohortTests(unittest.TestCase):
    def setUp(self):
        self.raw = raw_snapshot()
        start, end = self.raw['started_at_ms'], self.raw['observed_at_ms']
        self.raw['capture_cohort'] = {
            'schema': 'heptatrader.account-capture-cohort.v1',
            'request_end_at_ms': {'ledger': end, 'account': end, 'positions': end},
            'first_row_callback_at_ms': start+1,
            'last_row_callback_at_ms': start+1,
            'broker_valuation_tick_attested': False,
            'time_basis': 'LOCAL_CALLBACK_ARRIVAL_NOT_BROKER_VALUATION_TICK'}

    def test_exact_request_cohort_reconciles_without_claiming_atomic_broker_tick(self):
        result = a.normalize_nav(self.raw, period())
        self.assertEqual(result['capture_cohort'], self.raw['capture_cohort'])
        self.assertFalse(result['fx_translation_atomic_tick'])
        self.assertFalse(result['capture_cohort']['broker_valuation_tick_attested'])

    def test_missing_request_end_or_false_tick_attestation_cannot_pass(self):
        cohort = self.raw['capture_cohort']
        for key, value in [('request_end_at_ms', {'ledger':T+120,'account':T+120}),
                           ('broker_valuation_tick_attested',True),
                           ('time_basis','BROKER_ATOMIC')]:
            with self.subTest(key=key):
                original = copy.deepcopy(cohort[key])
                cohort[key] = value
                with self.assertRaisesRegex(EvidenceError,'CAPTURE_COHORT'):
                    a.normalize_nav(self.raw,period())
                cohort[key] = original

    def test_late_cross_request_row_and_forged_timestamp_rejected(self):
        self.raw['capture_cohort']['request_end_at_ms']['ledger']=T+100
        with self.assertRaisesRegex(EvidenceError,'CAPTURE_COHORT'):
            a.normalize_nav(self.raw,period())
        self.raw['capture_cohort']['request_end_at_ms']['ledger']=T+120
        self.raw['rows'][0]['observed_at_ms']=T+101
        self.raw['capture_cohort']['last_row_callback_at_ms']=T+102
        with self.assertRaisesRegex(EvidenceError,'CAPTURE_COHORT'):
            a.normalize_nav(self.raw,period())

    def test_capture_cohort_cannot_widen_fx_dollar_limit(self):
        for row in self.raw['rows']:
            if row['key']=='CashBalance' and row['currency']=='EUR':row['value']='104'
            if row['key']=='TotalCashBalance' and row['currency']=='EUR':row['value']='104'
            if row['key']=='NetLiquidationByCurrency' and row['currency']=='EUR':row['value']='103'
        with self.assertRaisesRegex(EvidenceError,'ACCOUNT_NAV_FX_RECONCILIATION_FAILED'):
            a.normalize_nav(self.raw,period())


if __name__=='__main__':unittest.main()
