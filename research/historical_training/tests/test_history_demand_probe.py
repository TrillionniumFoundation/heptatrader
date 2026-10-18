"""An absent farm notice can trigger one bounded price-data request, not a reconnect loop."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from historical_training.common import EvidenceError, digest, sha, write_once
from historical_training.history_farm_readiness import FarmReadiness
from historical_training.ib_history import CONTRACT, _run_locked, plans, validate_probe_options
from historical_training.tests.test_history_farm_readiness import FakeClock
import historical_training.tests.test_ib_history_failclosed as fixtures

class DemandProbeTests(unittest.TestCase):
    def make(self):
        c = FakeClock()
        f = FarmReadiness(c, lambda: 1792288800+c())
        return f, c

    def arm(self, f, c):
        self.assertEqual(f.wait(1, sleep=c.sleep, allow_unknown_probe=True),
                         'UNKNOWN_PROBE_REQUIRED')

    def test_unknown_probe_does_not_invent_connected_notice(self):
        f,c = self.make()
        self.arm(f,c)
        with f.dispatch(20,7800,request_kind='bars'):
            pass
        with self.assertRaises(EvidenceError):
            with f.dispatch(20,7801,request_kind='bars'):
                self.fail('second request before first completion')
        f.complete_probe(7800,received=True,rows=20,response_sha256='a'*64)
        with f.dispatch(20,7801,request_kind='bars'):
            pass
        self.assertEqual(f.state,'UNKNOWN')
        self.assertEqual(f.events,[])
        self.assertTrue(f.snapshot()['unknown_price_bar_probe']['verified'])
        self.assertEqual(f.dispatches[-1]['basis'],'COMPLETED_PRICE_BAR_PROBE')

    def test_head_schedule_and_order_cannot_be_probe(self):
        for code,kind in [(87,'head'),(20,'schedule'),(3,'bars')]:
            f,c=self.make()
            self.arm(f,c)
            with self.assertRaises(EvidenceError):
                with f.dispatch(code,7800,request_kind=kind):
                    self.fail('wrong request entered probe')
            self.assertIsNone(f.probe)

    def test_disconnect_or_fatal_during_wait_cannot_arm(self):
        for code,msg in [(2105,'lost:cashhmds'),(1100,'lost'),(2110,'server lost')]:
            f,c=self.make()
            c.after=lambda _t:f.notice(-1,code,msg)
            with self.assertRaises(EvidenceError):
                f.wait(1,sleep=c.sleep,allow_unknown_probe=True)
            self.assertFalse(f.probe_armed)

    def test_disconnect_between_wait_and_dispatch_blocks(self):
        f,c=self.make()
        self.arm(f,c)
        f.notice(-1,2105,'lost:cashhmds')
        with self.assertRaises(EvidenceError):
            with f.dispatch(20,7800,request_kind='bars'):
                self.fail('disconnected dispatch')

    def test_disconnect_then_recovery_does_not_validate_old_probe(self):
        f,c=self.make()
        self.arm(f,c)
        with f.dispatch(20,7800,request_kind='bars'): pass
        f.notice(-1,2105,'lost:cashhmds')
        f.notice(-1,2106,'connected:cashhmds')
        with self.assertRaisesRegex(EvidenceError,'PROBE_NOT_CONFIRMED'):
            f.complete_probe(7800,received=True,rows=20,response_sha256='a'*64)
        self.assertFalse(f.probe['verified'])

    def test_completed_probe_disconnect_recovery_still_stops_session(self):
        f,c=self.make()
        self.arm(f,c)
        with f.dispatch(20,7800,request_kind='bars'): pass
        f.complete_probe(7800,received=True,rows=20,response_sha256='a'*64)
        f.notice(-1,2105,'lost:cashhmds')
        f.notice(-1,2106,'connected:cashhmds')
        with self.assertRaises(EvidenceError):
            with f.dispatch(20,7801,request_kind='bars'):
                self.fail('a recovered notice cannot revive this probe session')
        self.assertTrue(f.snapshot()['unknown_probe_invalidated'])
        self.assertEqual(len(f.dispatches),1)

    def test_empty_error_and_wrong_identity_cannot_validate(self):
        for received,rows in [(True,0),(False,20)]:
            f,c=self.make()
            self.arm(f,c)
            with f.dispatch(20,7800,request_kind='bars'): pass
            with self.assertRaisesRegex(EvidenceError,'IDENTITY'):
                f.complete_probe(7801,received=True,rows=20,response_sha256='a'*64)
            with self.assertRaisesRegex(EvidenceError,'PROBE_NOT_CONFIRMED'):
                f.complete_probe(7800,received=received,rows=rows,response_sha256='a'*64)
            with self.assertRaises(EvidenceError):
                with f.dispatch(20,7801,request_kind='bars'):
                    self.fail('failed probe admitted next request')

    def test_config_is_explicit_and_legacy_ignored_flags_now_fail(self):
        validate_probe_options({})
        good={'cash_hmds_probe_on_unknown':True,'cash_hmds_startup_wait_sec':30,
              'skip_head_timestamp_requests':True}
        validate_probe_options(good)
        for changes in [{'skip_head_timestamp_requests':False},
                        {'cash_hmds_startup_wait_sec':0},
                        {'cash_hmds_probe_on_unknown':1},
                        {'head_probe_on_unknown':True},
                        {'unknown_probe_kind':'bars'}]:
            with self.assertRaises(EvidenceError):
                validate_probe_options(dict(good,**changes))
        requests=plans(dict(good,chunks=1,window_days=2),
                       datetime(2026,10,16,23,59,59,tzinfo=timezone.utc))
        self.assertEqual([x['what'] for x in requests],['MIDPOINT','BID','ASK','SCHEDULE'])
        self.assertTrue(all(x['kind']!='head' for x in requests))

class ProbeApp(fixtures.HistoricalSessionIOTests.FakeHistoryApp):
    def __init__(self,mode='ok'):
        super().__init__()
        self.mode=mode
        self.clock=FakeClock()
        self.farm=FarmReadiness(self.clock,lambda:1792288800+self.clock())
        wait=self.farm.wait
        self.farm.wait=lambda seconds,**kw:wait(seconds,sleep=self.clock.sleep,**kw)

    def isConnected(self):
        return not self.disconnected

    def reqHistoricalData(self,rid,contract,end,duration,bar_size,what,use_rth,fmt,keep_up,options):
        if self.mode=='disconnect_before_wire':
            self.farm.notice(-1,2105,'lost:cashhmds')
        kind='schedule' if what=='SCHEDULE' else 'bars'
        with self.farm.dispatch(20,rid,request_kind=kind):
            if self.mode=='timeout':
                self.sent[20]=self.sent.get(20,0)+1
                self.calls.append(('HISTORY',rid,what))
                self.events[rid].wait=lambda _timeout:False
                return
            super().reqHistoricalData(rid,contract,end,duration,bar_size,what,
                                      use_rth,fmt,keep_up,options)
            if self.mode=='error':
                self.errors.append({'request_id':rid,'code':162,'message':'pacing violation'})
            if self.mode=='empty':
                self.rows[rid]=[]

class DemandProbeSessionTests(unittest.TestCase):
    def run_session(self,mode='ok',cached_prices=False):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)/'history'
        config={'chunks':1,'window_days':2,'spacing_sec':0,
                'host':'127.0.0.1','port':4002,'client_id':8984,
                'cash_hmds_startup_wait_sec':1,'cash_hmds_probe_on_unknown':True,
                'skip_head_timestamp_requests':True}
        end=datetime(2026,10,16,23,59,59,tzinfo=timezone.utc)
        if cached_prices:
            for plan in plans(config,end)[:3]:
                d=root/'chunks'/digest({'contract':CONTRACT,**plan})
                response={'bars':[{'time':'1792108800'}],'head':None,'schedule':None,
                          'end_callback':{'start':'x','end':'y'},'errors':[]}
                write_once(d/'request.json',plan)
                write_once(d/'response.json',response)
                write_once(d/'receipt.json',{'request':plan,'state':'RECEIVED',
                           'rows':1,'response_sha256':sha(d/'response.json')})
        app=ProbeApp(mode)
        with patch('historical_training.ib_history.make_app',return_value=(app,object())):
            session=_run_locked(config,end,root)
        return session,app,root

    def test_full_download_on_same_connection_after_nonempty_probe(self):
        s,a,r=self.run_session()
        self.assertNotIn('exception',s)
        self.assertEqual(s['requests_sent_this_run'],4)
        self.assertEqual([x[2] for x in a.calls if x[0]=='HISTORY'],
                         ['MIDPOINT','BID','ASK','SCHEDULE'])
        self.assertTrue(s['same_connection_cash_hmds']['unknown_price_bar_probe']['verified'])
        self.assertEqual(s['same_connection_cash_hmds']['state'],'UNKNOWN')
        self.assertEqual(len(s['collector_sources']),4)
        self.assertTrue((r/'session.json').exists())
        self.assertTrue(a.disconnected)

    def test_error_timeout_or_empty_response_stops_after_one_request(self):
        for mode in ['error','timeout','empty']:
            with self.subTest(mode=mode):
                s,a,r=self.run_session(mode)
                self.assertIn('exception',s)
                self.assertEqual(s['requests_sent_this_run'],1)
                if mode == 'error': self.assertIn('PACING_STOP',s['exception'])
                if mode == 'timeout': self.assertIn('REQUEST_TIMEOUT',s['exception'])
                self.assertEqual(len(s['results']),1)
                self.assertEqual(len(list((r/'chunks').glob('*/receipt.json'))),1)
                self.assertTrue(a.disconnected)
                self.assertFalse(s['same_connection_cash_hmds']['unknown_price_bar_probe']['verified'])

    def test_zero_wire_count_if_dispatch_was_blocked(self):
        s,a,r=self.run_session('disconnect_before_wire')
        self.assertIn('exception',s)
        self.assertEqual(s['request_methods_invoked_this_run'],1)
        self.assertEqual(s['requests_sent_this_run'],0)
        self.assertEqual(s['results'],[])

    def test_cached_prices_do_not_authorize_schedule_as_new_probe(self):
        s,a,r=self.run_session(cached_prices=True)
        self.assertIn('exception',s)
        self.assertEqual(s['requests_sent_this_run'],0)
        self.assertTrue(all(x['resumed'] for x in s['results']))
        self.assertEqual(len(s['results']),3)

if __name__=='__main__':
    unittest.main()


class ProbeSourceAcceptanceTests(unittest.TestCase):
    def fixture(self, root):
        from historical_training.common import atomic
        p={'kind':'bars','what':'MIDPOINT'}
        plan={'config':{'contract':CONTRACT,'cash_hmds_probe_on_unknown':True},'requests':[p]}
        key=digest({'contract':CONTRACT,**p})
        receipt={'request':p,'request_id':7800,'state':'RECEIVED','rows':2,'response_sha256':'a'*64}
        atomic(root/'chunks'/key/'receipt.json',receipt)
        session={'config_sha256':digest(plan['config']),'paper_verified':True,
                 'startup_readiness':'UNKNOWN_PROBE_REQUIRED',
                 'same_connection_cash_hmds':{'fatal':False,'reconnects':0,'notices':[],
                     'unknown_price_bar_probe':{'request_id':7800,'notice_count':0,
                         'completed':True,'verified':True,'rows':2,'response_sha256':'a'*64}},
                 'results':[{'request_key':key,'state':'RECEIVED','resumed':False}]}
        return plan, session

    def test_successful_sealed_origin_required_and_recorded(self):
        from historical_training.common import atomic
        from historical_training.dataset import verified_probe_sessions
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,s=self.fixture(root)
            with self.assertRaisesRegex(EvidenceError,'SOURCE_SESSION_NOT_PROVEN'):
                verified_probe_sessions(root,plan)
            atomic(root/'sessions'/'1.json',s)
            self.assertEqual(verified_probe_sessions(root,plan)[0]['sha256'],sha(root/'sessions'/'1.json'))

    def test_failed_origin_cannot_be_recertified_by_cached_success(self):
        from historical_training.common import atomic
        from historical_training.dataset import verified_probe_sessions
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,s=self.fixture(root)
            s['exception']='UNKNOWN_PRICE_BAR_PROBE_NOT_CONFIRMED'
            atomic(root/'sessions'/'1.json',s)
            del s['exception'];s['results'][0]['resumed']=True
            atomic(root/'sessions'/'2.json',s)
            with self.assertRaisesRegex(EvidenceError,'SOURCE_SESSION_NOT_PROVEN'):
                verified_probe_sessions(root,plan)

    def test_invalidated_or_hash_unbound_probe_rejected(self):
        from historical_training.common import atomic
        from historical_training.dataset import verified_probe_sessions
        for mode in ('invalidated','hash','notice'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);plan,s=self.fixture(root);f=s['same_connection_cash_hmds']
                if mode=='invalidated':f['unknown_probe_invalidated']=True
                elif mode=='hash':f['unknown_price_bar_probe']['response_sha256']='b'*64
                else:f['notices']=[{'code':2105},{'code':2106}]
                atomic(root/'sessions'/'1.json',s)
                with self.assertRaisesRegex(EvidenceError,'SOURCE_SESSION_NOT_PROVEN'):
                    verified_probe_sessions(root,plan)

    def test_legacy_sources_do_not_need_probe_session(self):
        from historical_training.dataset import verified_probe_sessions
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(verified_probe_sessions(Path(tmp),{'config':{}}),[])
