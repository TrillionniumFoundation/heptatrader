"""Offline rejected-download evidence tests; no Broker, services or credentials."""
from __future__ import annotations
import copy
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
sys.path.insert(0, str(ROOT/'tests/python'))
import hepta_account_observer as observer
import hepta_account_pnl as account
import hepta_pnl_report as report
from hepta_pnl_accounting import EvidenceError
from test_account_pnl import period, raw_snapshot


class RejectedDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = self.root/'observer.json'
        self.pp = self.root/'period.json'
        self.journal = self.root/'execution/oms-journal.jsonl'
        self.journal.parent.mkdir(mode=0o700)
        self.journal.write_text('{}\n'); self.journal.chmod(0o600)
        self.out = self.root/'pnl/account'; self.out.mkdir(parents=True, mode=0o700)
        report.write_snapshot(self.cfg, {'schema':'heptatrader.account-observer-config.v1',
                                        'sdk_path':'UNUSED_OFFLINE_FIXTURE','client_id':8950})
        report.write_snapshot(self.pp, period())
        self.raw = raw_snapshot()
        for row in self.raw['rows']:
            if row['request_id']==1 and row['key']=='ExchangeRate' and row['currency']=='EUR':
                row['value']='1.2'
        self.evidence = EvidenceError('not yet collected')

    def rejected(self):
        with patch.object(observer, 'acquire', return_value=copy.deepcopy(self.raw)):
            with self.assertRaises(observer.RejectedAccountDownload) as caught:
                observer.collect_account(self.cfg,self.pp,self.root)
        return caught.exception

    def test_collector_retains_exact_bad_valuation_and_original_identity(self):
        ex=self.rejected()
        self.assertEqual(str(ex),'ACCOUNT_NAV_FX_RECONCILIATION_FAILED')
        self.assertEqual(ex.raw['rows'],self.raw['rows'])
        self.assertEqual(ex.raw['period_sha256'],hashlib.sha256(self.pp.read_bytes()).hexdigest())
        self.assertEqual(ex.raw['journal_fingerprint'],list(report.fingerprint(self.journal)))
        self.assertFalse((self.out/'rejected-raw.json').exists())

    def test_failure_invalidates_cache_before_diagnostics_and_keeps_hash(self):
        report.write_snapshot(self.out/'nav.json',account.normalize_nav(raw_snapshot(),period()))
        order=[];original=observer.write_snapshot
        def capture(path,value,*args,**kwargs):
            order.append((path.name,value.get('authoritative')))
            return original(path,value,*args,**kwargs)
        ex=self.rejected()
        with patch.object(observer,'write_snapshot',side_effect=capture):
            nav=observer.publish_unavailable(self.out,ex)
        self.assertEqual(order,[('nav.json',False),('rejected-raw.json',None),('nav.json',False)])
        self.assertFalse(nav['authoritative']);self.assertFalse(nav['mutation_authorized'])
        self.assertFalse(nav['rejected_raw_is_accepted_nav'])
        self.assertEqual(nav['rejected_raw_sha256'],account.digest(ex.raw))
        self.assertEqual(report.load_private(self.out/'rejected-raw.json'),ex.raw)
        self.assertEqual((self.out/'rejected-raw.json').stat().st_mode & 0o777,0o600)

    def test_rejected_source_never_becomes_opening_or_successful_source(self):
        original=raw_snapshot();report.write_snapshot(self.out/'raw-latest.json',original)
        period_bytes=self.pp.read_bytes();journal_bytes=self.journal.read_bytes()
        observer.publish_unavailable(self.out,self.rejected())
        self.assertEqual(report.load_private(self.out/'raw-latest.json'),original)
        self.assertEqual(self.pp.read_bytes(),period_bytes);self.assertEqual(self.journal.read_bytes(),journal_bytes)
        self.assertEqual(list(self.out.glob('first-*')),[])

    def test_diagnostics_io_failure_cannot_retain_old_valid_nav(self):
        report.write_snapshot(self.out/'nav.json',account.normalize_nav(raw_snapshot(),period()))
        original=observer.write_snapshot
        def fail(path,*args,**kwargs):
            if path.name=='rejected-raw.json':raise OSError('fixture disk error')
            return original(path,*args,**kwargs)
        with patch.object(observer,'write_snapshot',side_effect=fail),self.assertRaises(OSError):
            observer.publish_unavailable(self.out,self.rejected())
        self.assertFalse(report.load_private(self.out/'nav.json')['authoritative'])

    def test_old_rejected_download_is_bounded_and_replaced(self):
        ex=self.rejected();observer.publish_unavailable(self.out,ex)
        ex.raw['connection_id']='second-rejected-read'
        observer.publish_unavailable(self.out,ex)
        self.assertEqual(len(list(self.out.glob('rejected-*'))),1)
        self.assertEqual(report.load_private(self.out/'rejected-raw.json')['connection_id'],'second-rejected-read')

    def test_transport_error_does_not_claim_a_new_raw_download(self):
        nav=observer.publish_unavailable(self.out,EvidenceError('ACCOUNT_DOWNLOAD_END_TIMEOUT'))
        self.assertNotIn('rejected_raw_sha256',nav)
        self.assertFalse((self.out/'rejected-raw.json').exists())

    def test_non_evidence_error_does_not_disclose_exception_message(self):
        nav=observer.publish_unavailable(self.out,KeyError('private fixture content'))
        self.assertEqual(nav['reason_code'],'KeyError')
        self.assertNotIn('private fixture content',json.dumps(nav))

    def test_period_change_not_wrapped_as_stable_rejected_evidence(self):
        def changed(*args):
            self.pp.write_text(self.pp.read_text()+' ')
            return copy.deepcopy(self.raw)
        with patch.object(observer,'acquire',side_effect=changed),self.assertRaises(EvidenceError) as caught:
            observer.collect_account(self.cfg,self.pp,self.root)
        self.assertNotIsInstance(caught.exception,observer.RejectedAccountDownload)
        self.assertEqual(str(caught.exception),'ACCOUNT_COLLECTION_LEDGER_OR_PERIOD_CHANGED')

    def test_success_preserves_original_collect_contract(self):
        with patch.object(observer,'acquire',return_value=raw_snapshot()):
            nav,raw=observer.collect_account(self.cfg,self.pp,self.root)
        self.assertTrue(nav['authoritative']);self.assertEqual(nav['raw_sha256'],account.digest(raw))

    def test_rejected_nav_is_rejected_by_account_consumer(self):
        nav=observer.publish_unavailable(self.out,self.rejected())
        with self.assertRaises(EvidenceError):
            account.validate_nav(nav,period(),nav['observed_at_ms'])
