"""Live atomic producer heartbeats never hide changes to accounting evidence."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
import hepta_decision_attribution as attribution
from laya_cell import resident_reward_cache as cache_module
from laya_cell import resident_rewards as rewards
from test_pnl_accounting import period

@unittest.skipUnless(sys.platform.startswith('linux'), 'requires Linux inotify')
class RewardHealthHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='hepta-reward-health-')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.now=time.time_ns()//1000000;self.tick=1000.0
        self.guard=cache_module.RewardRefreshGuard();self.addCleanup(self.guard.close)
        self.period_path=self.root/'period.json'
        self.private(self.period_path,attribution.canonical(period()).encode()+b'\n')
        period_sha=hashlib.sha256(self.period_path.read_bytes()).hexdigest()
        self.oms=self.root/'oms.jsonl';self.private(self.oms,b'')
        self.config={'period_path':str(self.period_path),'period_sha256':period_sha,
                     'oms_path':str(self.oms),'reward_instruments':['EUR.USD','BTC.USD'],
                     'decision_attribution_registrations':{}}
        self.health=[];self.streams=[];self.original_health={}
        for instrument,producer in (('EUR.USD','a'*32),('BTC.USD','b'*32)):
            directory=self.root/instrument;directory.mkdir(mode=0o700)
            manifest=self.root/(instrument+'.manifest.json')
            body={'schema':'heptatrader.attribution-producer.v1','instrument':instrument,
                  'execution_domain':'PAPER','period_sha256':period_sha,'journal_directory':str(directory)}
            self.private(manifest,attribution.canonical(body).encode()+b'\n')
            manifest_sha=hashlib.sha256(manifest.read_bytes()).hexdigest()
            self.config['decision_attribution_registrations'][instrument]=[{'manifest':str(manifest),'sha256':manifest_sha}]
            header={'schema':attribution.SCHEMA,'producer_id':producer,'sequence':0,
                    'previous':'0'*64,'dropped_total':0,'captured_at_ms':self.now-5000,
                    'written_at_ms':self.now-5000,'kind':'HEADER',
                    'data':{'manifest_sha256':manifest_sha,'instrument':instrument,
                            'period_sha256':period_sha,'execution_domain':'PAPER',
                            'source_kind':'PINNED_DRIVER_CALLSITE','trading_authority':False}}
            header['sha256']=attribution.digest(header)
            stream=directory/(producer+'-0000.jsonl')
            self.private(stream,attribution.canonical(header).encode()+b'\n');self.streams.append(stream)
            health=directory/(producer+'.health.json')
            health_body={'producer_id':producer,'written_records':1,'dropped':0,'error':None,
                         'at_ms':self.now-1000,'order_operations':0}
            self.private(health,attribution.canonical(health_body).encode()+b'\n')
            self.health.append(health);self.original_health[health]=health_body
    @staticmethod
    def private(path,raw):
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(raw)
    def heartbeat(self,path=None,at_ms=None,**changes):
        path=path or self.health[0]
        body=dict(self.original_health[path],at_ms=time.time_ns()//1000000 if at_ms is None else at_ms)
        body.update(changes);temporary=path.with_suffix('.next')
        self.private(temporary,attribution.canonical(body).encode()+b'\n');os.replace(temporary,path)
        return body
    def prime(self):
        if not self.guard.begin(self.config,self.now,self.tick):
            self.fail('Healthy private real journal fixture could not arm reward guard')
        result=rewards.current(self.config,self.now,source_observer=self.guard.observe)
        self.assertTrue(all(value['ledger_complete'] for value in result['components'].values()),result)
        self.assertTrue(self.guard.finish(self.config,result,self.now))
        return result
    def changed(self):self.assertEqual(self.guard.state(self.config,self.now+1,self.tick+1),'CHANGED')
    def test_repeated_atomic_heartbeats_reuse_real_verified_snapshot(self):
        before=self.prime()
        for index in range(5):
            for path in self.health:self.heartbeat(path)
            self.assertEqual(self.guard.state(self.config,self.now+index+1,self.tick+index+1),'UNCHANGED')
        self.assertEqual(self.guard.full_refreshes,1);self.assertEqual(self.guard.hits,5)
        self.assertGreaterEqual(self.guard.verified_health_heartbeats,10)
        self.assertEqual(rewards.current(self.config,self.now),before)
    def test_finish_two_seconds_later_accepts_heartbeat_beyond_snapshot_clock(self):
        self.assertTrue(self.guard.begin(self.config,self.now,self.tick))
        result=rewards.current(self.config,self.now,source_observer=self.guard.observe)
        with mock.patch.object(cache_module.time,'time_ns',return_value=(self.now+2000)*1000000):
            self.heartbeat(at_ms=self.now+2000)
            self.assertTrue(self.guard.finish(self.config,result,self.now))
            self.assertFalse(self.guard._force_refresh)
            self.assertEqual(self.guard.state(self.config,self.now+2000,self.tick+2),'UNCHANGED')
        self.assertEqual(result['at_ms'],self.now);self.assertEqual(self.guard.status()['last_full_read_at_ms'],self.now)
    def test_later_heartbeat_check_does_not_relax_future_oms_snapshot_bound(self):
        self.assertTrue(self.guard.begin(self.config,self.now,self.tick))
        result=rewards.current(self.config,self.now,source_observer=self.guard.observe)
        self.guard.observe(self.now+1)
        with mock.patch.object(cache_module.time,'time_ns',return_value=(self.now+2000)*1000000):
            self.heartbeat(at_ms=self.now+2000)
            self.assertFalse(self.guard.finish(self.config,result,self.now))
            self.assertEqual(self.guard.state(self.config,self.now+2000,self.tick+2),'UNAVAILABLE')
    def test_genuinely_future_health_is_not_a_heartbeat(self):
        self.prime();self.heartbeat(at_ms=time.time_ns()//1000000+60000);self.changed()
    def test_regressed_health_time_is_not_a_heartbeat(self):
        self.prime();self.heartbeat(at_ms=self.original_health[self.health[0]]['at_ms']-1);self.changed()
    def test_health_business_fields_or_profile_changes_invalidate(self):
        cases=({'written_records':2},{'dropped':1},{'error':'writer failed'},
               {'order_operations':1},{'producer_id':'c'*32},{'status':'UNKNOWN_NEW_FIELD'},{'written_records':True})
        for changes in cases:
            with self.subTest(changes=changes):
                self.guard.close();self.heartbeat();self.prime()
                self.heartbeat(**changes);self.changed()
    def test_removed_and_new_health_files_invalidate(self):
        for mode in ('removed','new'):
            with self.subTest(mode=mode):
                self.guard.close()
                self.private(self.health[0],attribution.canonical(self.original_health[self.health[0]]).encode()+b'\n')
                self.prime()
                if mode=='removed':self.health[0].unlink()
                else:self.private(self.health[0].parent/('c'*32+'.health.json'),b'{}\n')
                self.changed()
    def test_new_stream_or_transient_unknown_file_cannot_hide_in_heartbeat(self):
        for mode in ('new_stream','transient_unknown'):
            with self.subTest(mode=mode):
                self.guard.close();self.prime()
                path=self.health[0].parent/('new.jsonl' if mode=='new_stream' else 'unknown.next')
                self.private(path,b'{}\n')
                if mode=='transient_unknown':path.unlink()
                self.heartbeat();self.changed()
                if path.exists():path.unlink()
    def test_rewritten_and_restored_old_stream_cannot_hide_in_heartbeat(self):
        self.prime();path=self.streams[0];before=path.stat();original=path.read_bytes()
        changed=original.replace(b'HEADER',b'HEADEX');self.assertEqual(len(changed),len(original))
        self.private(path,changed);self.private(path,original);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.heartbeat();self.changed()
    def test_non_atomic_health_write_is_not_treated_as_writer_heartbeat(self):
        self.prime()
        body=dict(self.original_health[self.health[0]],at_ms=time.time_ns()//1000000)
        self.private(self.health[0],attribution.canonical(body).encode()+b'\n');self.changed()
    def test_unfinished_health_temporary_file_cannot_grant_reuse(self):
        self.prime();self.private(self.health[0].with_suffix('.next'),b'{');self.changed()
    def test_wrong_known_temporary_to_health_pair_is_not_a_heartbeat(self):
        self.prime();body=dict(self.original_health[self.health[1]],at_ms=time.time_ns()//1000000)
        temporary=self.health[0].with_suffix('.next')
        self.private(temporary,attribution.canonical(body).encode()+b'\n');os.replace(temporary,self.health[1]);self.changed()
    def test_unchanged_poll_still_reads_each_health_file(self):
        self.prime()
        with mock.patch.object(attribution,'read_private',wraps=attribution.read_private) as reader:
            self.assertEqual(self.guard.state(self.config,self.now+1,self.tick+1),'UNCHANGED')
        read_paths={Path(call.args[0]) for call in reader.call_args_list}
        self.assertTrue(set(self.health)<=read_paths)
    def test_restored_directory_permissions_cannot_hide_in_heartbeat(self):
        self.prime();directory=self.health[0].parent
        directory.chmod(0o500);directory.chmod(0o700);self.heartbeat();self.changed()

if __name__=='__main__':unittest.main()
