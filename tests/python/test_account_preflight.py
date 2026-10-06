"""Preflight read-only separation: fixtures do not contact a Broker."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'tests/python'))
import hepta_account_observer as observer
import hepta_account_pnl as account
import hepta_pnl_report as report
from hepta_pnl_accounting import EvidenceError, validate_period
from test_account_pnl import period, raw_snapshot, T


def scope():
    return dict(schema='heptatrader.account-preflight-scope.v1', account='DU_TEST',
        currency='USD', execution_domain='PAPER', observation_scope_id='obs-fixture-20261006',
        created_at_ms=T, reset_sha256='a'*64, excluded_client_ids=[701, 712], read_only=True)


def raw():
    value=raw_snapshot()
    value.pop('period_id')
    value.update(schema='heptatrader.account-preflight-download.v1',
                 observation_scope_id=scope()['observation_scope_id'])
    return value


class PreflightValuationTests(unittest.TestCase):
    def test_same_math_has_separate_schema_and_no_period(self):
        expected=account.normalize_nav(raw_snapshot(),period())
        result=account.normalize_preflight_nav(raw(),scope())
        self.assertEqual(result['nav'],expected['nav'])
        self.assertEqual(result['ledgers'],expected['ledgers'])
        self.assertEqual(result['schema'],'heptatrader.account-preflight-nav.v1')
        self.assertNotIn('period_id',result)
        self.assertNotIn('period_sha256',result)
        self.assertFalse(result['opening_nav_eligible'])
        self.assertFalse(result['mutation_authorized'])
    def test_scope_is_never_an_accounting_period(self):
        with self.assertRaises(EvidenceError):validate_period(scope())
    def test_preflight_raw_refused_as_period_source(self):
        with self.assertRaises(EvidenceError):account.normalize_nav(raw(),period())
    def test_relabelled_raw_still_refused_as_period_source(self):
        value=raw();value.update(schema='heptatrader.account-download.v1',period_id=period()['period_id'])
        with self.assertRaisesRegex(EvidenceError,'NOT_ACCOUNTING'):account.normalize_nav(value,period())
    def test_period_raw_refused_as_preflight_source(self):
        with self.assertRaises(EvidenceError):account.normalize_preflight_nav(raw_snapshot(),scope())
    def test_preflight_output_refused_even_with_period_id_injected(self):
        value=account.normalize_preflight_nav(raw(),scope())
        value.update(schema='heptatrader.account-nav.v1',period_id=period()['period_id'])
        with self.assertRaises(EvidenceError):account.validate_nav(value,period(),T+130)
    def test_fx_threshold_unchanged(self):
        value=raw()
        for row in value['rows']:
            if row['request_id']==1 and row['key']=='ExchangeRate' and row['currency']=='EUR':row['value']='1.2'
        with self.assertRaisesRegex(EvidenceError,'FX_RECONCILIATION'):account.normalize_preflight_nav(value,scope())
    def test_wrong_account_barrier_and_client_refused(self):
        for key,value in [('account','DU_OTHER'),('ends',[1]),('client_id',701),('client_id',712),('read_only',False)]:
            sample=raw();sample[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(EvidenceError):account.normalize_preflight_nav(sample,scope())
    def test_invalid_scope_cannot_open_sdk(self):
        for key,value in [('execution_domain','LIVE'),('account','U_LIVE'),('read_only',False),
                          ('excluded_client_ids',[]),('excluded_client_ids',[True]),
                          ('excluded_client_ids',[701,701]),('observation_scope_id','../../escape')]:
            changed=scope();changed[key]=value
            with self.subTest(key=key,value=value),patch.object(observer,'load_sdk') as sdk,self.assertRaises(EvidenceError):
                observer.acquire_preflight(changed,Path('unused'),8950)
            sdk.assert_not_called()
    def test_extra_period_or_owner_keys_rejected(self):
        for key in ('period_id','instruments','opening_nav','started_at_ms'):
            changed=scope();changed[key]='not permitted'
            with self.subTest(key=key),self.assertRaises(EvidenceError):account.validate_preflight_scope(changed)
    def test_current_source_and_actual_expiry_enforced(self):
        value=raw();nav=account.normalize_preflight_nav(value,scope())
        account.validate_preflight_nav_evidence(nav,value,scope(),T+130)
        with self.assertRaises(EvidenceError):account.validate_preflight_nav_evidence(nav,value,scope(),nav['valid_until_ms'])
        bad=copy.deepcopy(nav);bad['nav']='0'
        with self.assertRaises(EvidenceError):account.validate_preflight_nav_evidence(bad,value,scope(),T+130)
    def test_mutating_sends_stay_forbidden(self):
        boundary=observer.SendBoundary('DU_TEST',8950,{'ledger':1,'account':2,'positions':3})
        boundary.check('71\0'+'2\0'+'8950\0')
        for code in (3,4,58,21):
            with self.subTest(code=code),self.assertRaises(EvidenceError):boundary.check(str(code)+'\0'+'1\0')


class PreflightLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.scope=scope();self.root=self.home/'.local/state/heptatrader'
        self.scope_path=self.home/'.config/heptatrader/observations'/self.scope['observation_scope_id']/'account-preflight.json'
        self.config=self.home/'.config/heptatrader/account-observer.json'
        self.state=self.root/'observations'/self.scope['observation_scope_id'];self.state.mkdir(parents=True,mode=0o700)
        self.root.chmod(0o700)
        self.config.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.config.parent.chmod(0o700)
        reset={'time':'2026-10-05T00:00:00+00:00','mode':'PAPER','account':'DU_TEST',
               'state':'factory_accounting_reset_auto_trading_stopped'}
        self.marker=self.root/'RESET-STOPPED.json';report.write_snapshot(self.marker,reset)
        self.scope['reset_sha256']=hashlib.sha256(self.marker.read_bytes()).hexdigest()
        report.write_snapshot(self.scope_path,self.scope)
        report.write_snapshot(self.config,{'schema':'heptatrader.account-observer-config.v1',
                                         'sdk_path':'unused','client_id':8950})
        self.out=self.state/'account-preflight';self.out.mkdir(mode=0o700)
    def collect(self):
        return observer.collect_preflight(self.config,self.scope_path,self.state,home=self.home)
    def test_collection_requires_no_journal_or_fake_owner(self):
        with patch.object(observer,'acquire_preflight',return_value=raw()) as acquire:
            nav,sample=self.collect()
        self.assertNotIn('period_id',sample);self.assertNotIn('journal_fingerprint',sample)
        self.assertTrue(nav['authoritative']);self.assertEqual(sample['scope_sha256'],hashlib.sha256(self.scope_path.read_bytes()).hexdigest())
        self.assertFalse((self.root/'execution').exists())
        self.assertEqual(acquire.call_args[0][0],self.scope)
    def test_pinned_scope_change_between_iterations_prevents_acquisition(self):
        with patch.object(observer,'acquire_preflight') as acquire,self.assertRaisesRegex(EvidenceError,'SCOPE_REPLACED'):
            observer.collect_preflight(self.config,self.scope_path,self.state,home=self.home,expected_scope_sha256='c'*64)
        acquire.assert_not_called()
    def test_existing_period_prevents_acquisition(self):
        report.write_snapshot(self.home/'.config/heptatrader/pnl-period.json',period())
        with patch.object(observer,'acquire_preflight') as acquire,self.assertRaisesRegex(EvidenceError,'ACTIVE_PERIOD'):
            self.collect()
        acquire.assert_not_called()
    def test_changed_reset_prevents_acquisition(self):
        self.marker.write_bytes(self.marker.read_bytes()+b' ')
        with patch.object(observer,'acquire_preflight') as acquire,self.assertRaisesRegex(EvidenceError,'RESET_CHANGED'):
            self.collect()
        acquire.assert_not_called()
    def test_wrong_output_root_refused(self):
        with self.assertRaisesRegex(EvidenceError,'PATH_SCOPE'):
            observer.collect_preflight(self.config,self.scope_path,self.root,home=self.home)
    def test_period_created_during_download_refused(self):
        def acquire(*_):
            report.write_snapshot(self.home/'.config/heptatrader/pnl-period.json',period());return raw()
        with patch.object(observer,'acquire_preflight',side_effect=acquire),self.assertRaisesRegex(EvidenceError,'ACTIVE_PERIOD'):
            self.collect()
    def test_scope_change_during_download_refused(self):
        def acquire(*_):
            self.scope_path.write_bytes(self.scope_path.read_bytes()+b' ');return raw()
        with patch.object(observer,'acquire_preflight',side_effect=acquire),self.assertRaisesRegex(EvidenceError,'SCOPE_CHANGED_DURING_READ'):
            self.collect()
    def test_failed_valuation_retains_namespaced_source(self):
        sample=raw()
        for row in sample['rows']:
            if row['request_id']==1 and row['key']=='ExchangeRate' and row['currency']=='EUR':row['value']='1.2'
        with patch.object(observer,'acquire_preflight',return_value=sample),self.assertRaises(observer.RejectedAccountDownload) as caught:
            self.collect()
        nav=observer.publish_unavailable(self.out,caught.exception,self.scope)
        self.assertFalse(nav['authoritative']);self.assertFalse(nav['opening_nav_eligible'])
        self.assertNotIn('period_id',nav)
        self.assertEqual(nav['schema'],'heptatrader.account-preflight-nav.v1')
        self.assertEqual(report.load_private(self.out/'rejected-raw.json'),caught.exception.raw)
    def test_restart_never_overwrites_first_preflight_source(self):
        first=raw();first['scope_sha256']='b'*64
        observer.retain_first_preflight_download(self.out,first)
        data=(self.out/'first-preflight-download.json').read_bytes()
        later=copy.deepcopy(first);later['connection_id']='second-connection'
        observer.retain_first_preflight_download(self.out,later)
        self.assertEqual((self.out/'first-preflight-download.json').read_bytes(),data)
        self.assertFalse((self.home/'.config/heptatrader/pnl-period.json').exists())
    def test_foreign_first_scope_rejected(self):
        sample=raw();sample['scope_sha256']='b'*64
        observer.retain_first_preflight_download(self.out,sample)
        sample['scope_sha256']='c'*64
        with self.assertRaisesRegex(EvidenceError,'FIRST_SOURCE_SCOPE'):
            observer.retain_first_preflight_download(self.out,sample)

if __name__=='__main__':unittest.main()
