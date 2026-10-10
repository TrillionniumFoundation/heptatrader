"""Account-read-only status routing: global HMDS failures are not account errors."""
from __future__ import annotations
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'tests/python'))
import hepta_account_observer as observer
import hepta_account_pnl as account
from hepta_pnl_accounting import EvidenceError
from test_account_cohort_lifecycle import v3_snapshot
from test_account_pnl import period

class HMDSStatusTests(unittest.TestCase):
    def read_with_status(self, request_scoped=False):
        class FakeWrapper:
            pass
        class FakeClient:
            def __init__(self, wrapper):
                self._connected=False
                self._stop=threading.Event()
            def connect(self,host,port,client_id):
                self._connected=True
                self.nextValidId(1)
                self.managedAccounts('DU_TEST,')
            def run(self):
                self._stop.wait(3)
            def isConnected(self):
                return self._connected
            def disconnect(self):
                self._connected=False
                self._stop.set()
            def serverVersion(self):
                return 187
            def sendMsg(self,msg):
                return True
            def reqAccountUpdatesMulti(self,req,account_name,model,ledger):
                self.boundary.subscribed.add((76,req))
                if ledger:
                    self.error(req if request_scoped else -1,2105,'HMDS disconnected')
                self.accountUpdateMultiEnd(req)
            def reqPositionsMulti(self,req,account_name,model):
                self.boundary.subscribed.add((74,req))
                self.positionMultiEnd(req)
            def cancelAccountUpdatesMulti(self,req):
                pass
            def cancelPositionsMulti(self,req):
                pass
        with patch.object(observer,'load_sdk',return_value=(FakeClient,FakeWrapper)):
            return observer._acquire_account('DU_TEST',Path('.'),8950,2,
                'period_id','fixture','heptatrader.account-download.v3')

    def test_global_2105_retained_without_poisoning_complete_account_callbacks(self):
        raw=self.read_with_status(False)
        self.assertEqual(raw['ends'],[1,2,3])
        self.assertEqual(raw['transport_errors'],[])
        self.assertFalse(raw['connection_lost_during_download'])
        self.assertEqual(raw['informational_events'],[{'request_id':-1,'code':2105}])
        self.assertTrue(raw['read_only'])

    def test_request_scoped_2105_still_fails_closed(self):
        raw=self.read_with_status(True)
        self.assertEqual(raw['ends'],[1,2,3])
        self.assertEqual(raw['transport_errors'],[{'request_id':1,'code':2105}])
        self.assertEqual(raw['informational_events'],[])

    def test_normalizer_does_not_ignore_account_transport_errors(self):
        bad=v3_snapshot()
        bad['transport_errors']=[{'request_id':1,'code':2105}]
        with self.assertRaisesRegex(EvidenceError,'ACCOUNT_DOWNLOAD_IDENTITY_OR_BARRIER_INVALID'):
            account.normalize_nav(bad,period())

if __name__=='__main__':
    unittest.main()
