"""Extra forward trials reuse one artifact; no training or live worker is run."""
import copy
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from historical_training import resident_resource_guard as guard
from historical_training.tests.test_forward_registration_preflight import RegistrationGate
from historical_training.tests.test_forward_registration_stability import StableRegistrationGate
from laya_cell import resident_forward_attempt as attempts
from laya_cell import resident_plan_admission as admission
from laya_cell import resident_queue, resident_owner, resident_budget
from laya_cell.integrity import ContractError, digest, read_json, sha_file
from laya_cell.resident import Supervisor
from laya_cell.resident_store import database, get_meta, put_meta, write_once
from laya_cell.tests import test_resident_plan_admission as fixtures


class ForwardAttemptFixture(unittest.TestCase):
    def setUp(self):
        box=tempfile.TemporaryDirectory();self.addCleanup(box.cleanup)
        self.root=Path(box.name)
        self.service,self.candidate,self.job=fixtures.RegistrationTests.fixture(self,self.root)
        self.addCleanup(self.service.db.close)
        self.service.c['runtime_sha256'][attempts.RUNTIME_PIN]='e'*64
        self.job_path=self.service.root/'jobs'/'job.json'
        write_once(self.job_path,self.job)
        source=patch.object(admission,'capture_source',side_effect=lambda config,plan,now:fixtures.fresh_proof(now))
        self.capture=source.start();self.addCleanup(source.stop)
        storage=patch.object(resident_budget,'inspect',return_value={'admission_ready':True,'blocked_reason':None})
        self.storage=storage.start();self.addCleanup(storage.stop)
        queue=patch.object(admission,'inspect_queue',return_value={'pending_original_trials':0})
        self.queue=queue.start();self.addCleanup(queue.stop)
        clock=patch.object(admission.time,'time',return_value=fixtures.NOW/1000)
        self.clock=clock.start();self.addCleanup(clock.stop)
        first=admission.register(self.service,'job',self.candidate,self.job)
        self.old=first['plan']
        self.old_path=admission.registration_folder(self.service.c,'job')/'plan.json'
        self.request=attempts.build_request(self.service,self.old_path)
        self.now=self.old['outcome_end_ms']+1000
        self.clock.return_value=self.now/1000
        self.qualification_path=self.old_path.parent/'qualification.json'
        self.close(self.old_path,self.old)
        self.original={p:p.read_bytes() for p in (self.old_path,self.qualification_path,self.job_path,
            Path(self.job['output_directory'])/'candidate.json',Path(self.job['output_directory'])/'adapter.safetensors')}
        self.original_jobs=[tuple(r) for r in self.service.db.execute('SELECT * FROM jobs')]
        self.original_consumed=[tuple(r) for r in self.service.db.execute('SELECT * FROM consumed_rows')]
        self.capture.reset_mock()

    def close(self,path,plan):
        write_once(path.parent/'qualification.json',{'state':'REJECTED','candidate_id':plan['candidate_id'],
            'plan_id':plan['plan_id'],'observed':0,'censored':plan['rule']['slots']})

    def wrap(self,decisions,*,stable=False,service=None):
        service=service or self.service
        gate=(StableRegistrationGate if stable else RegistrationGate)(self,decisions)
        gate.service=service
        gate.c={'original_config_sha256':'f'*64}
        gate.original={'runtime_sha256':copy.deepcopy(service.c['runtime_sha256'])}
        service.__class__=guard.guarded_trainer_class(Supervisor,gate)
        return gate

    def register(self,request=None,service=None):
        return (service or self.service).register_evaluation('job',self.candidate,self.job,
                                                            attempt=request or self.request)

    def record(self,request=None):
        return get_meta(self.service.db,admission.registration_key('job',request or self.request))

    def path(self,request=None):
        return admission.registration_folder(self.service.c,'job',request or self.request)/'plan.json'

    def assert_original(self):
        for path,raw in self.original.items():self.assertEqual(path.read_bytes(),raw)
        self.assertEqual([tuple(r) for r in self.service.db.execute('SELECT * FROM jobs')],self.original_jobs)
        self.assertEqual([tuple(r) for r in self.service.db.execute('SELECT * FROM consumed_rows')],self.original_consumed)

    def assert_unallocated(self):
        self.assertFalse(self.service.db.in_transaction)
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),1)
        self.assertFalse(self.path().exists())
        self.assert_original()


class ForwardAttemptTests(ForwardAttemptFixture):
    def test_request_replay_before_original_cutoff_creates_nothing(self):
        self.assertEqual(attempts.build_request(self.service,self.old_path),self.request)
        self.clock.return_value=(self.old['outcome_end_ms']-1)/1000
        gate=self.wrap([])
        result=self.register()
        self.assertEqual(result['reason'],'PREVIOUS_FORWARD_WINDOW_NOT_COMPLETE')
        self.assertEqual(result['next_attempt_at_ms'],self.old['outcome_end_ms'])
        self.assertFalse(self.path().parent.exists())
        self.assertIsNone(self.record())
        self.assertEqual(gate.calls,[]);self.capture.assert_not_called()
        self.storage.assert_not_called()
        self.assert_unallocated()

    def test_closeout_must_exist_and_be_rejected_after_cutoff(self):
        self.wrap([]);self.qualification_path.unlink()
        result=self.register()
        self.assertEqual(result['reason'],'PREVIOUS_FORWARD_QUALIFICATION_NOT_PUBLISHED')
        self.assertIsNone(self.record());self.assertFalse(self.path().parent.exists())
        write_once(self.qualification_path,{'plan_id':self.old['plan_id'],
            'candidate_id':self.candidate['candidate_id'],'state':'QUALIFIED_AWAITING_CANONICAL_OWNER'})
        with self.assertRaisesRegex(ContractError,'PREVIOUS_NOT_CLOSED_REJECTED'):self.register()
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),1)
        self.capture.assert_not_called()

    def test_plain_supervisor_cannot_allocate_additional_trial_without_original_guard(self):
        with self.assertRaisesRegex(ContractError,'ORIGINAL_GUARD_REQUIRED'):self.register()
        self.assert_unallocated();self.assertIsNone(self.record())

    def test_original_storage_quota_refusal_precedes_source_or_alpha(self):
        gate=self.wrap([])
        self.storage.return_value={'admission_ready':False,'blocked_reason':'STATE_HEADROOM_NO_NEW_LEARNING'}
        result=self.register()
        self.assertEqual(result['state'],'DEFERRED')
        self.assertEqual(result['reason'],'STATE_HEADROOM_NO_NEW_LEARNING')
        self.assert_unallocated();self.assertIsNone(self.record())
        self.assertFalse(self.path().parent.exists())
        self.assertEqual(gate.calls,[]);self.capture.assert_not_called()

    def test_stability_then_new_trial_preserves_one_model_and_all_original_bytes(self):
        gate=self.wrap([True,True,True,True],stable=True)
        with patch.object(guard.time,'monotonic') as tick:
            for offset in (0,30000):
                tick.return_value=offset/1000;self.clock.return_value=(self.now+offset)/1000
                self.assertEqual(self.register()['state'],'FORWARD_REGISTRATION_GUARD_HOLD')
                self.assert_unallocated();self.assertIsNone(self.record())
            tick.return_value=60.;self.clock.return_value=(self.now+60000)/1000
            result=self.register()
        self.assertEqual(result['state'],'PUBLISHED');plan=result['plan']
        self.assertEqual(plan['trial'],2);self.assertEqual(plan['job_id'],'job')
        self.assertEqual(plan['evaluation_attempt'],self.request)
        expected=Supervisor._evaluation_plan(self.service,'job',self.candidate,self.job,2,self.now+60000)
        self.assertEqual(plan['rule'],expected['rule'])
        self.assertNotEqual(plan['plan_id'],self.old['plan_id'])
        self.assertGreater(plan['start_ms'],self.old['outcome_end_ms'])
        self.assertFalse(plan['registration_admission']['additional_attempt']['old_alpha_refunded'])
        self.assert_original()
        self.capture.assert_called_once();self.assertEqual(len(gate.calls),4)
        gate.acquire.assert_not_called();gate.release.assert_not_called()
        self.assertEqual(resident_queue.inspect(self.service.c)['viable_pending_evaluations'],1)
        status=self.service._latest_candidate_registration(self.candidate['candidate_id'])
        self.assertEqual(status['evaluation_role'],'ORIGINAL')
        self.assertEqual(status['trial'],1)
        self.assertEqual(status['additional_evaluation_attempts'][0]['trial'],2)

    def test_second_guard_failure_rolls_back_without_debit_or_retraining(self):
        gate=self.wrap([True,False])
        self.assertEqual(self.register()['state'],'FORWARD_REGISTRATION_GUARD_HOLD')
        self.assert_unallocated();self.assertIsNone(self.record())
        self.assertFalse(self.path().parent.exists())
        self.assertEqual(len(gate.calls),2);self.capture.assert_called_once()

    def test_stale_source_keeps_attempt_unallocated_and_retries_same_request(self):
        self.wrap([True]*4)
        self.capture.side_effect=ValueError('ORIGINAL_EUR_SOURCE_STALE')
        first=self.register()
        self.assertEqual(first['state'],'DEFERRED')
        self.assertEqual(first['reason'],'FORWARD_ORIGINAL_SOURCE_UNREADY')
        self.assert_unallocated()
        self.assertEqual(read_json(self.path().parent/'registration-status.json'),first)
        self.assertEqual(self.record(),first)
        self.capture.side_effect=lambda c,p,n:fixtures.fresh_proof(n)
        second=self.register()
        self.assertEqual(second['state'],'PUBLISHED')
        self.assertEqual(second['evaluation_attempt'],first['evaluation_attempt'])
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),2)

    def test_reserved_recovery_uses_attempt_key_without_new_source_or_training(self):
        self.wrap([True,True])
        with patch.object(admission,'_publish',side_effect=OSError('lost publish')):
            with self.assertRaises(OSError):self.register()
        reserved=self.record();self.assertEqual(reserved['state'],'RESERVED')
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),2)
        self.capture.side_effect=AssertionError('NO_NEW_SOURCE_FOR_RESERVED_ATTEMPT')
        self.storage.side_effect=AssertionError('NO_NEW_STORAGE_ADMISSION_FOR_RESERVED_ATTEMPT')
        self.clock.return_value=fixtures.SATURDAY/1000
        gate=self.wrap([])
        with patch.object(self.service,'candidate_done',side_effect=AssertionError('NO_TRAINING_JOB_REWRITE')):
            self.assertEqual(admission.resume_reservations(self.service),1)
        self.assertEqual(self.record()['state'],'PUBLISHED')
        self.assertEqual(read_json(self.path()),reserved['plan'])
        self.assertEqual(gate.calls,[]);self.assert_original()
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),2)

    def test_plan_visible_before_published_reply_is_replayed_without_new_debit(self):
        self.wrap([True,True])
        with patch.object(self.service,'_write_evaluation_handoff',side_effect=OSError('lost handoff')):
            with self.assertRaises(OSError):self.register()
        before=self.path().read_bytes();self.assertEqual(self.record()['state'],'RESERVED')
        self.capture.side_effect=AssertionError('NO_NEW_READ_AFTER_PLAN_PUBLICATION')
        self.wrap([])
        self.assertEqual(self.register()['state'],'PUBLISHED')
        self.assertEqual(self.path().read_bytes(),before)
        self.assertEqual(self.register()['state'],'PUBLISHED')
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),2)
        self.assert_original()

    def test_owner_selects_additional_receipt_without_replacing_original(self):
        self.wrap([True,True]);record=self.register();plan=record['plan']
        self.assertEqual(resident_owner._registration(self.service.c,plan,self.candidate,self.job),record)
        original=get_meta(self.service.db,admission.registration_key('job'))
        self.assertEqual(resident_owner._registration(self.service.c,self.old,self.candidate,self.job),original)
        with self.service.db:put_meta(self.service.db,admission.registration_key('job',self.request),original)
        with self.assertRaisesRegex(ContractError,'RECEIPT_BINDING'):
            resident_owner._registration(self.service.c,plan,self.candidate,self.job)

    def test_request_binding_rejects_changed_model_job_plan_and_scope_before_source(self):
        self.wrap([])
        for path in (self.job_path,Path(self.job['output_directory'])/'adapter.safetensors',self.old_path):
            with self.subTest(path=path.name):
                original=path.read_bytes();path.write_bytes(original+b' ')
                try:
                    with self.assertRaises(ContractError):self.register()
                    self.assertEqual(get_meta(self.service.db,'evaluation_trial'),1)
                finally:path.write_bytes(original)
        wrong={**self.request,'not_before_ms':self.request['not_before_ms']-1}
        wrong['attempt_id']=digest({k:v for k,v in wrong.items() if k!='attempt_id'})
        with self.assertRaisesRegex(ContractError,'PREVIOUS_PLAN_BINDING'):self.register(wrong)
        self.service.c['period_sha256']='f'*64
        with self.assertRaisesRegex(ContractError,'FORWARD_ATTEMPT_PERIOD'):self.register()
        self.capture.assert_not_called();self.assertIsNone(self.record())

    def test_candidate_window_expiry_does_not_extend_or_refund_original_trial(self):
        self.wrap([])
        self.clock.return_value=(self.candidate['window']['validation_end_ms']+1)/1000
        result=self.register()
        self.assertEqual(result['state'],'NO_FORWARD_TRIAL')
        self.assertEqual(result['reason'],'CANDIDATE_WINDOW_EXPIRED')
        self.assert_unallocated();self.capture.assert_not_called()
        self.assertEqual(self.register(),result)

    def test_new_configuration_cannot_change_the_previous_statistical_protocol(self):
        from laya_cell.promotion_power import PROTOCOL
        self.service.c['promotion_protocol']=PROTOCOL
        self.wrap([])
        with self.assertRaisesRegex(ContractError,'STATISTICAL_RULE_CHANGED'):self.register()
        self.assert_unallocated();self.capture.assert_not_called()

    def test_same_candidate_outcomes_cannot_cross_attempt_directories(self):
        self.wrap([True,True]);plan=self.register()['plan']
        old_outcome={'plan_id':self.old['plan_id'],'candidate_id':self.candidate['candidate_id'],
                     'slot':0,'status':'CENSORED','reason':'PREDICTION_PROCESS_FAILED'}
        write_once(self.old_path.parent/'outcome-000.json',old_outcome)
        self.assertEqual(resident_queue.inspect(self.service.c)['viable_pending_evaluations'],1)
        from laya_cell.promotion import assess
        new_assessment=assess(plan,[],plan['outcome_end_ms'])
        self.assertEqual(new_assessment['observed'],0)
        self.assertEqual(new_assessment['censored'],plan['rule']['slots'])
        self.assertEqual(new_assessment['alpha'],.05/(2*3))
        write_once(self.path().parent/'outcome-000.json',old_outcome)
        with self.assertRaisesRegex(ContractError,'FUTILITY_OUTCOME_IDENTITY_OR_STATUS'):
            resident_queue.inspect(self.service.c)

    def observed(self,plan):
        return [{'plan_id':plan['plan_id'],'candidate_id':self.candidate['candidate_id'],
            'slot':slot,'status':'OBSERVED','anchor_ms':plan['start_ms']+slot*plan['rule']['slot_spacing_ms'],
            'prediction_available_ms':plan['start_ms']+slot*plan['rule']['slot_spacing_ms']+1,
            'trained_until_ms':self.candidate['trained_until_ms'],'path_gap':False,'target_class':slot%2,
            'parent_brier':.4,'candidate_brier':.1,'candidate_hit':True,'bootstrap_hit':False}
            for slot in range(plan['rule']['slots'])]

    def test_direct_assessment_requires_attempt_plan_identity_and_preserves_legacy(self):
        from laya_cell.promotion import assess
        self.wrap([True,True]);plan=self.register()['plan']
        good=self.observed(plan)
        self.assertEqual(assess(plan,good,plan['outcome_end_ms'])['state'],'QUALIFIED_AWAITING_CANONICAL_OWNER')
        old=self.observed(self.old)
        with self.assertRaisesRegex(ContractError,'EVALUATION_ATTEMPT_OUTCOME_PLAN_IDENTITY'):
            assess(plan,old,plan['outcome_end_ms'])
        legacy=[{k:v for k,v in row.items() if k!='plan_id'} for row in old]
        with self.assertRaisesRegex(ContractError,'EVALUATION_ATTEMPT_OUTCOME_PLAN_IDENTITY'):
            assess(plan,legacy,plan['outcome_end_ms'])
        self.assertEqual(assess(self.old,legacy,self.old['outcome_end_ms'])['state'],
                         'QUALIFIED_AWAITING_CANONICAL_OWNER')

    def test_owner_final_reassessment_rejects_observations_from_original_plan(self):
        from laya_cell.promotion import assess
        self.wrap([True,True]);plan=self.register()['plan'];path=self.path()
        qualification=assess(plan,self.observed(plan),plan['outcome_end_ms'])
        self.assertEqual(qualification['state'],'QUALIFIED_AWAITING_CANONICAL_OWNER')
        write_once(path.parent/'qualification.json',qualification)
        for row in self.observed(self.old):
            write_once(path.parent/('outcome-%03d.json'%row['slot']),row)
        with self.assertRaisesRegex(ContractError,'EVALUATION_ATTEMPT_OUTCOME_PLAN_IDENTITY'):
            resident_owner._qualification(self.service.c,path,plan['outcome_end_ms'])
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),2)
        self.assert_original()

    def test_changed_attempt_runtime_cannot_redecide_deferred_under_new_pins(self):
        self.wrap([True,True]);self.capture.side_effect=ValueError('synthetic source hold')
        old=self.register();self.assertEqual(old['state'],'DEFERRED')
        prior=self.root/'prior-config.json';write_once(prior,self.service.c)
        self.service.c['retained_registration_configs']=[{'path':str(prior),'sha256':sha_file(prior)}]
        self.service.c['runtime_sha256'][attempts.RUNTIME_PIN]='1'*64
        with self.assertRaisesRegex(ContractError,'FROZEN_REGISTRAR_REQUIRED'):self.register()
        self.assertEqual(self.record(),old);self.assert_unallocated()

    def test_same_request_concurrent_registration_debits_only_once(self):
        def worker(_):
            service=object.__new__(Supervisor);service.root=self.service.root
            service.c=copy.deepcopy(self.service.c);service.db=database(service.root/'scheduler.sqlite')
            self.wrap([True,True],service=service)
            try:return self.register(service=service)
            finally:service.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,range(2)))
        self.assertTrue(all(r['state']=='PUBLISHED' for r in results))
        self.assertEqual(results[0]['plan'],results[1]['plan'])
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),2)
        self.assertEqual(len(list(Path(self.service.c['evaluation_directory']).glob('*/plan.json'))),2)
        self.assert_original()

    def test_later_attempt_waits_for_immediate_previous_window_and_keeps_separate_queue(self):
        self.wrap([True,True]);first=self.register()
        request=attempts.build_request(self.service,self.path())
        self.assertEqual(self.register(request)['reason'],'PREVIOUS_FORWARD_WINDOW_NOT_COMPLETE')
        self.close(self.path(),first['plan']);self.clock.return_value=(first['plan']['outcome_end_ms']+1000)/1000
        self.wrap([True,True]);second=self.register(request)
        self.assertEqual(second['plan']['trial'],3)
        self.assertEqual(second['plan']['evaluation_attempt']['previous_plan_id'],first['plan']['plan_id'])
        self.assertEqual(resident_queue.inspect(self.service.c)['pending_original_trials'],1)
        self.assertEqual(self.register()['plan'],first['plan'])
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),3)
        self.assert_original()


class ForwardAttemptControllerTests(ForwardAttemptFixture):
    def owned_lock(self):
        path=self.service.root/'scheduler.lock'
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC,0o600)
        self.addCleanup(os.close,fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.service._controller_lock_fd=fd
        return path

    def request_file(self):
        path=self.root/'attempt-request.json';write_once(path,self.request)
        return path,sha_file(path)

    def test_controller_requires_original_lock_and_guard(self):
        path,pin=self.request_file()
        with self.assertRaisesRegex(ContractError,'SCHEDULER_LOCK_REQUIRED'):
            attempts.run_request(self.service,path,pin,wait_seconds=0)
        self.owned_lock()
        with self.assertRaisesRegex(ContractError,'ORIGINAL_GUARD_REQUIRED'):
            attempts.run_request(self.service,path,pin,wait_seconds=0)
        self.wrap([])
        with self.service.db:self.service.db.execute("UPDATE jobs SET status='RUNNING'")
        with self.assertRaisesRegex(ContractError,'ACTIVE_TRAINING_JOB'):
            attempts.run_request(self.service,path,pin,wait_seconds=0)
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),1)

    def test_controller_rejects_wrong_lock_inode_and_another_lock_owner(self):
        path,pin=self.request_file();lock=self.owned_lock();self.wrap([])
        other=os.open(lock,os.O_WRONLY|os.O_CLOEXEC);self.addCleanup(os.close,other)
        self.service._controller_lock_fd=other
        with self.assertRaises(BlockingIOError):attempts.run_request(self.service,path,pin,wait_seconds=0)
        self.service._controller_lock_fd=os.open(self.root/'wrong-lock',os.O_WRONLY|os.O_CREAT,0o600)
        self.addCleanup(os.close,self.service._controller_lock_fd)
        with self.assertRaisesRegex(ContractError,'SCHEDULER_LOCK_IDENTITY'):
            attempts.run_request(self.service,path,pin,wait_seconds=0)

    def test_bounded_controller_collects_stability_and_returns_visible_queue_without_training(self):
        path,pin=self.request_file();self.owned_lock()
        gate=self.wrap([True]*4,stable=True)
        tick=[0.]
        def sleep(seconds):
            tick[0]+=seconds;self.clock.return_value=self.now/1000+tick[0]
        with patch.object(attempts.time,'monotonic',side_effect=lambda:tick[0]), \
             patch.object(attempts.time,'sleep',side_effect=sleep), \
             patch.object(self.service,'recover',side_effect=AssertionError('NO_JOB_RECOVERY')), \
             patch.object(self.service,'step',side_effect=AssertionError('NO_TRAINING_STEP')):
            result=attempts.run_request(self.service,path,pin,wait_seconds=90)
        self.assertEqual(result['state'],'PUBLISHED')
        self.assertEqual(result['queue_after_registration']['viable_pending_evaluations'],1)
        self.assertEqual(tick[0],60.)
        self.assertFalse(result['training_performed']);self.assertFalse(result['old_plan_modified'])
        self.assertEqual(len(gate.calls),4);self.assert_original()
        self.assertTrue(result['trainer_status_updated'])
        status=read_json(self.service.root/'status.json')
        self.assertEqual(status['last_candidate_id'],self.candidate['candidate_id'])
        self.assertEqual(status['forward_attempt_registration']['plan_id'],result['plan']['plan_id'])
        self.assertEqual(status['latest_candidate_registration']['additional_evaluation_attempts'][0]['trial'],2)

    def test_status_write_failure_replays_published_plan_without_changing_latest_candidate(self):
        path,pin=self.request_file();self.owned_lock();self.wrap([True,True])
        original_record=get_meta(self.service.db,admission.registration_key('job'))
        latest_candidate='9'*64
        with self.service.db:put_meta(self.service.db,'last_completed_candidate',latest_candidate)
        with patch.object(self.service,'write_status',side_effect=OSError('lost status write')):
            with self.assertRaisesRegex(OSError,'lost status write'):
                attempts.run_request(self.service,path,pin,wait_seconds=0)
        published=self.record();self.assertEqual(published['state'],'PUBLISHED')
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),2)
        before=self.path().read_bytes();self.wrap([])
        self.capture.side_effect=AssertionError('STATUS_REPLAY_MUST_NOT_READ_SOURCE')
        result=attempts.run_request(self.service,path,pin,wait_seconds=0)
        self.assertEqual(result['plan'],published['plan']);self.assertEqual(self.path().read_bytes(),before)
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),2)
        self.assertEqual(get_meta(self.service.db,admission.registration_key('job')),original_record)
        status=read_json(self.service.root/'status.json')
        self.assertEqual(status['last_candidate_id'],latest_candidate)
        self.assertEqual(status['forward_attempt_registration']['candidate_id'],self.candidate['candidate_id'])
        self.assert_original()

    def test_bounded_hold_and_drain_preserve_markers_without_spending_trial(self):
        path,pin=self.request_file();self.owned_lock();gate=self.wrap([False])
        self.service.write_status=Mock(side_effect=AssertionError('HELD_ATTEMPT_HAS_NO_PUBLICATION_STATUS'))
        marker=self.root/'operator-hold.json';write_once(marker,{'reason':'exact operator hold'})
        raw=marker.read_bytes()
        result=attempts.run_request(self.service,path,pin,wait_seconds=0)
        self.assertEqual(result['state'],'FORWARD_REGISTRATION_GUARD_HOLD')
        self.assertEqual(marker.read_bytes(),raw);self.assert_unallocated()
        gate.draining=True
        result=attempts.run_request(self.service,path,pin,wait_seconds=90)
        self.assertEqual(result['reason'],'FORWARD_ATTEMPT_CONTROLLER_STOPPING')
        self.assertEqual(marker.read_bytes(),raw);self.assert_unallocated()

    def test_request_file_is_exact_private_regular_bytes(self):
        path,pin=self.request_file()
        self.assertEqual(attempts.load_request(path,pin),self.request)
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ContractError,'REQUEST_BYTES'):attempts.load_request(path,pin)
        path.chmod(0o644)
        with self.assertRaisesRegex(ContractError,'REQUEST_FILE'):attempts.load_request(path,sha_file(path))
        link=self.root/'request-link';link.symlink_to(path)
        with self.assertRaises(OSError):attempts.load_request(link,sha_file(path))


if __name__=='__main__':unittest.main()
