import copy,hashlib,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from fixtures import *
from hepta_cost_observation_access import ObservationAccess,SCHEMA
from hepta_cost_capture import Capture,read_capture

class ObservationAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.cycle='obs-synthetic-unit-cycle'
        self.now=int(time.time()*1000);self.reset_time=self.now-20000
        from datetime import datetime,timezone
        self.marker=self.home/'.local/state/heptatrader/RESET-STOPPED.json';self.marker.parent.mkdir(parents=True)
        self.marker.write_text(json.dumps({'time':datetime.fromtimestamp(self.reset_time/1000,timezone.utc).isoformat(),
            'account':'DU123','state':'factory_accounting_reset_auto_trading_stopped'}));self.marker.chmod(0o600)
        root=self.home/'.config/heptatrader/observations'/self.cycle;root.mkdir(parents=True,mode=0o700)
        self.path=root/'BTC.USD.json';self.token=root/'BTC.USD.watch.token';self.token.write_text('SYNTHETIC TOKEN NOT A CREDENTIAL');self.token.chmod(0o600)
        self.lease=root/'BTC.USD.watch-lease.json';self.state=self.home/'.local/state/heptatrader/observations'/self.cycle/'cost/BTC.USD'
        binary=self.home/'.local/share/heptatrader/observation/releases/fixture/bin';binary.mkdir(parents=True)
        p={'schema':SCHEMA,'mode':'WATCH','allow_trade':False,'execution_observation_only':True,'instrument':'BTC.USD',
           'operator_uid':os.geteuid(),'cycle_id':self.cycle,'created_at_ms':self.now-10000,
           'state_dir':str(self.state),'token_file':str(self.token),'lease_file':str(self.lease),
           'socket':str(Path('/run/user')/str(os.geteuid())/self.cycle/'tools.sock'),
           'supervisor_socket':str(Path('/run/user')/str(os.geteuid())/self.cycle/'supervisor.sock'),
           'reset_sha256':hashlib.sha256(self.marker.read_bytes()).hexdigest(),'account':'DU123','agent_id':'unit-agent',
           'session_id':'unit-watch','source_sha':'synthetic-test-only','watch_token_sha256':hashlib.sha256(self.token.read_bytes()).hexdigest()}
        for key in ['heptactl','sessionctl']:
            f=binary/key;f.write_text('#!/bin/sh\nexit 1\n');f.chmod(0o500);p[key]=str(f);p[key+'_sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
        self.profile=p;self.save_profile()
        self.lease.write_text(json.dumps({'mode':'WATCH','cycle_id':self.cycle,'session_id':'unit-watch','token_sha256':p['watch_token_sha256'],
            'lease_generation':1,'issued_at_ms':self.now-9000,'expires_at_ms':self.now+600000,'accepted':True}));self.lease.chmod(0o600)
    def tearDown(self):self.tmp.cleanup()
    def save_profile(self):self.path.write_text(json.dumps(self.profile));self.path.chmod(0o600)
    def access(self):return ObservationAccess(self.path,'BTC.USD',self.state,home=self.home,clock_ms=lambda:self.now)
    def test_explicit_watch_access_does_not_clear_reset(self):
        a=self.access();self.assertEqual(a.check()['mode'],'WATCH');self.assertTrue(self.marker.exists());self.assertFalse(self.state.exists())
    def test_wrong_instrument_rejected(self):
        with self.assertRaises(CostError):ObservationAccess(self.path,'EUR.USD',self.state,home=self.home,clock_ms=lambda:self.now)
    def test_trade_flags_never_allow_observation(self):
        for key,value in [('mode','PAPER'),('allow_trade',True),('execution_observation_only',False)]:
            saved=copy.deepcopy(self.profile);self.profile[key]=value;self.save_profile()
            with self.assertRaises(CostError):self.access()
            self.profile=saved
    def test_old_cycle_marker_rejected(self):
        a=self.access();self.marker.write_text(self.marker.read_text()+' ')
        with self.assertRaisesRegex(CostError,'RESET_CYCLE'):a.check()
    def test_cannot_write_actual_driver_state(self):
        with self.assertRaisesRegex(CostError,'PATH_MISMATCH'):ObservationAccess(self.path,'BTC.USD',self.home/'.local/state/heptatrader/crypto-driver',home=self.home,clock_ms=lambda:self.now)
    def test_old_token_mtime_is_not_fresh_identity(self):
        old=(self.reset_time-1000)/1000;os.utime(self.token,(old,old))
        with self.assertRaisesRegex(CostError,'STALE_OR_CHANGED_TOKEN'):self.access()
    def test_token_replacement_needs_reprovision(self):
        a=self.access();self.token.write_text('another synthetic token')
        with self.assertRaisesRegex(CostError,'STALE_OR_CHANGED_TOKEN'):a.check()
    def test_profile_modification_requires_restart(self):
        a=self.access();self.path.write_text(self.path.read_text()+' ')
        with self.assertRaisesRegex(CostError,'PROFILE_CHANGED'):a.check()
    def test_expired_lease_does_not_self_provision(self):
        a=self.access();self.now+=700000
        with patch('hepta_cost_observation_access.subprocess.run',side_effect=AssertionError('no requests')):
            with self.assertRaisesRegex(CostError,'LEASE_UNVERIFIED'):a.check()
    def test_near_expiry_renews_watch_only_and_persists_generation(self):
        a=self.access();self.now+=550000
        reply=SimpleNamespace(returncode=0,stdout=json.dumps({'accepted':True,'lease_generation':2}))
        with patch('hepta_cost_observation_access.subprocess.run',return_value=reply) as run:
            a.check();argv=run.call_args.args[0]
            self.assertIn('renew',argv);self.assertNotIn('paper-resume',argv);self.assertNotIn('provision',argv)
        self.assertEqual(json.loads(self.lease.read_text())['lease_generation'],2)
    def test_unknown_renew_result_stops_before_future_read(self):
        a=self.access();self.now+=550000
        with patch('hepta_cost_observation_access.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='{}')):
            with self.assertRaisesRegex(CostError,'RENEWAL_UNCONFIRMED'):a.check()
    def test_binary_changes_fail_before_any_call(self):
        Path(self.profile['heptactl']).chmod(0o700);Path(self.profile['heptactl']).write_text('changed')
        with self.assertRaisesRegex(CostError,'BINARY_HASH'):self.access()
    def test_foreign_account_is_not_a_cycle(self):
        self.profile['account']='DUOTHER';self.save_profile()
        with self.assertRaisesRegex(CostError,'RESET_CYCLE'):self.access()
    def test_capture_cannot_reopen_legacy_or_different_cycle(self):
        p=self.home/'capture.jsonl';ctx=self.access().context
        with Capture(p,'BTC.USD',source_kind='SYNTHETIC',min_free_bytes=0):pass
        with self.assertRaisesRegex(CostError,'RESET_CYCLE'):Capture(p,'BTC.USD',source_kind='SYNTHETIC',min_free_bytes=0,observation_context=ctx)
    def test_same_cycle_capture_keeps_header_and_hash_chain(self):
        p=self.home/'capture.jsonl';ctx=self.access().context
        with Capture(p,'BTC.USD',source_kind='SYNTHETIC',min_free_bytes=0,observation_context=ctx) as c:c.append('QUOTE',quote(),at_ms=NOW)
        before=p.read_bytes()
        with Capture(p,'BTC.USD',source_kind='SYNTHETIC',min_free_bytes=0,observation_context=ctx) as c:c.append('QUOTE',quote(NOW+1),at_ms=NOW+1)
        result=read_capture(p);self.assertTrue(p.read_bytes().startswith(before));self.assertEqual(result['events'][0]['data']['observation_context'],ctx)
        changed={**ctx,'cycle_id':'obs-different-unit'}
        with self.assertRaisesRegex(CostError,'RESET_CYCLE'):Capture(p,'BTC.USD',source_kind='SYNTHETIC',min_free_bytes=0,observation_context=changed)
