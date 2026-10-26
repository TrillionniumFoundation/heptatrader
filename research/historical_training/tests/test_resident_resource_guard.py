"""Admission tests use synthetic telemetry and mock supervisors, no models/orders."""
import copy,json,math,os,signal,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from historical_training.common import EvidenceError
from historical_training.resident_resource_guard import (GIB,SOURCE_RULE,RESOURCE_RULE,assess,
    parse_meminfo,parse_full_psi,Lease,Gate,guarded_evaluator_class,guarded_trainer_class,assess_resources)

NOW=1791470000000

def fixture():
    mem={'MemTotal':24*GIB,'MemAvailable':10*GIB,'SwapTotal':8*GIB,'SwapFree':2*GIB}
    psi={'avg10':0.,'avg60':0.}
    src={'schema':'heptatrader.quote-readiness-status.v1','period_sha256':'fixed','at_ms':NOW-1000,
         'latest_source_quote_ms':NOW-1500,'usable':24,'unusable':0,'state':'READY_FOR_REVIEW_NOT_AUTHORIZATION','rule':copy.deepcopy(SOURCE_RULE)}
    return mem,psi,src,NOW,'fixed',False

class AdmissionTests(unittest.TestCase):
    def test_healthy_synthetic_telemetry_admits_research_only(self):
        r=assess(*fixture());self.assertTrue(r['admit_new_worker']);self.assertFalse(r['automatic_trading'])
    def test_manual_hold_wins_even_with_good_telemetry(self):
        args=list(fixture());args[-1]=True;self.assertFalse(assess(*args)['admit_new_worker'])
    def test_memory_reserve_blocks_before_worker(self):
        m,p,s,n,scope,h=fixture();m['MemAvailable']=8*GIB-1
        self.assertIn('HOST_AVAILABLE_MEMORY_BELOW_8GIB',assess(m,p,s,n,scope,h)['reasons'])
    def test_cold_full_swap_does_not_starve_work_with_ram_and_no_pressure(self):
        m,p,s,n,scope,h=fixture();m['SwapFree']=0
        report=assess(m,p,s,n,scope,h)
        self.assertTrue(report['admit_new_worker'])
        self.assertEqual(report['swap_free_policy'],'DIAGNOSTIC_ONLY')
    def test_registration_budget_does_not_authorize_heavy_work(self):
        m,p,s,n,scope,h=fixture();m['SwapFree']=0;m['MemAvailable']=2*GIB
        report=assess(m,p,s,n,scope,h,role='forward_registration')
        self.assertTrue(report['admit_new_worker'])
        self.assertEqual(report['resource_role'],'forward_registration')
        for role in ('trainer','evaluator'):
            self.assertFalse(assess(m,p,s,n,scope,h,role=role)['admit_new_worker'])
        m['MemAvailable']-=1
        self.assertIn('HOST_AVAILABLE_MEMORY_BELOW_REGISTRATION_RESERVE',
                      assess(m,p,s,n,scope,h,role='forward_registration')['reasons'])
    def test_full_swap_with_real_pressure_still_blocks_every_role(self):
        m,p,s,n,scope,h=fixture();m['SwapFree']=0;p['avg10']=1.01
        for role in ('trainer','evaluator','forward_registration'):
            self.assertIn('HOST_FULL_MEMORY_PSI_PRESSURE',
                          assess(m,p,s,n,scope,h,role=role)['reasons'])
    def test_registration_still_needs_fresh_source_and_no_manual_hold(self):
        m,p,s,n,scope,h=fixture();m['MemAvailable']=2*GIB
        self.assertFalse(assess(m,p,s,n,scope,True,role='forward_registration')['admit_new_worker'])
        s['latest_source_quote_ms']=n-5001
        self.assertIn('LATEST_SOURCE_QUOTE_NOT_FRESH',
                      assess(m,p,s,n,scope,h,role='forward_registration')['reasons'])
    def test_unknown_resource_role_is_not_registration(self):
        m,p,_,n,_,h=fixture()
        with self.assertRaises(EvidenceError):
            assess_resources(m,p,n,h,role='other')
    def test_boundary_reserves_allow(self):
        m,p,s,n,scope,h=fixture();m['MemAvailable']=8*GIB;m['SwapFree']=GIB
        self.assertTrue(assess(m,p,s,n,scope,h)['admit_new_worker'])
    def test_pressure_avg60_blocks(self):
        m,p,s,n,scope,h=fixture();p['avg60']=.10001;self.assertFalse(assess(m,p,s,n,scope,h)['admit_new_worker'])
    def test_old_quote_cannot_be_made_fresh_by_new_status(self):
        m,p,s,n,scope,h=fixture();s['at_ms']=n;s['latest_source_quote_ms']=n-5001
        self.assertIn('LATEST_SOURCE_QUOTE_NOT_FRESH',assess(m,p,s,n,scope,h)['reasons'])
    def test_quote_ttl_original_5000ms(self):
        m,p,s,n,scope,h=fixture();s['latest_source_quote_ms']=n-5000
        self.assertTrue(assess(m,p,s,n,scope,h)['admit_new_worker'])
    def test_future_status_denied(self):
        m,p,s,n,scope,h=fixture();s['at_ms']=n+1;self.assertFalse(assess(m,p,s,n,scope,h)['admit_new_worker'])
    def test_future_quote_denied(self):
        m,p,s,n,scope,h=fixture();s['latest_source_quote_ms']=n+1;self.assertFalse(assess(m,p,s,n,scope,h)['admit_new_worker'])
    def test_old_status_denied(self):
        m,p,s,n,scope,h=fixture();s['at_ms']=n-120001;self.assertFalse(assess(m,p,s,n,scope,h)['admit_new_worker'])
    def test_wrong_period_denied(self):
        m,p,s,n,scope,h=fixture();s['period_sha256']='other';self.assertFalse(assess(m,p,s,n,scope,h)['admit_new_worker'])
    def test_boolean_count_denied(self):
        m,p,s,n,scope,h=fixture();s['usable']=True;self.assertIn('SOURCE_READINESS_INVALID',assess(m,p,s,n,scope,h)['reasons'])
    def test_missing_source_denied(self):
        m,p,s,n,scope,h=fixture();self.assertFalse(assess(m,p,{},n,scope,h)['admit_new_worker'])
    def test_insufficient_path_coverage_denied(self):
        m,p,s,n,scope,h=fixture();s['usable']=19;s['unusable']=5
        self.assertFalse(assess(m,p,s,n,scope,h)['admit_new_worker'])
    def test_counts_must_total_twenty_four(self):
        m,p,s,n,scope,h=fixture();s['usable']=24;s['unusable']=2
        self.assertFalse(assess(m,p,s,n,scope,h)['admit_new_worker'])
    def test_changed_quality_threshold_denied(self):
        m,p,s,n,scope,h=fixture();s['rule']['source_ttl_ms']=10000
        self.assertIn('SOURCE_RULE_CHANGED',assess(m,p,s,n,scope,h)['reasons'])
    def test_nan_psi_parser_denied(self):
        with self.assertRaises(EvidenceError):parse_full_psi('full avg10=nan avg60=0.0 avg300=0.0 total=0')
    def test_duplicate_psi_parser_denied(self):
        with self.assertRaises(EvidenceError):parse_full_psi('full avg10=0 avg10=1 avg60=0')
    def test_memory_unit_and_overcommit_parser(self):
        m=parse_meminfo('MemTotal: 100 kB\nMemAvailable: 80 kB\nSwapTotal: 50 kB\nSwapFree: 10 kB')
        self.assertEqual(m['MemAvailable'],80*1024)
        with self.assertRaises(EvidenceError):parse_meminfo('MemTotal: 100 MB\nMemAvailable: 80 kB\nSwapTotal: 50 kB\nSwapFree: 10 kB')
    def test_missing_mem_field_denied(self):
        with self.assertRaises(EvidenceError):parse_meminfo('MemTotal: 100 kB')

class LeaseTests(unittest.TestCase):
    def test_failed_post_lock_admission_releases_unused_worker_lease(self):
        with tempfile.TemporaryDirectory() as td:
            gate=Gate({'state_root':td},'trainer')
            other=Lease(Path(td)/'heavy-worker.lock')
            try:
                admission={'admit_new_worker':True,'reasons':[]}
                with patch.object(gate,'check',side_effect=[admission,OSError('admission receipt unavailable')]), \
                        self.assertRaises(OSError):
                    gate.acquire({'role':'trainer'})
                self.assertIsNone(gate.lease.fd)
                self.assertTrue(other.acquire())
            finally:
                gate.lease.release();other.release()

    def test_two_roles_cannot_own_lease_simultaneously(self):
        with tempfile.TemporaryDirectory() as td:
            a=Lease(Path(td)/'lease');b=Lease(Path(td)/'lease')
            try:
                self.assertTrue(a.acquire());self.assertFalse(b.acquire());a.release();self.assertTrue(b.acquire())
            finally:a.release();b.release()
    def test_symlink_lease_refused(self):
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'other';target.write_text('');path=Path(td)/'lease';path.symlink_to(target)
            with self.assertRaises(OSError):Lease(path).acquire()
    def test_release_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            a=Lease(Path(td)/'lease');a.release();self.assertTrue(a.acquire());a.release();a.release()
    def test_inherited_worker_keeps_lock_after_controller_closes_its_descriptor(self):
        with tempfile.TemporaryDirectory() as td:
            a=Lease(Path(td)/'lease');b=Lease(Path(td)/'lease');worker=None
            try:
                self.assertTrue(a.acquire())
                worker=subprocess.Popen([sys.executable,'-c',
                    'import time; print("ready",flush=True); time.sleep(30)'],
                    pass_fds=(a.fd,),stdout=subprocess.PIPE,text=True)
                self.assertEqual(worker.stdout.readline().strip(),'ready')
                a.release()
                self.assertFalse(b.acquire())
                worker.terminate();worker.wait(timeout=5)
                self.assertTrue(b.acquire())
            finally:
                if worker is not None:
                    if worker.poll() is None:worker.kill();worker.wait(timeout=5)
                    worker.stdout.close()
                a.release();b.release()

class StatusDiagnosticTests(unittest.TestCase):
    def test_calendar_wait_reports_persistent_hold_and_memory_pressure_without_source_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);hold=root/'manual.json'
            hold.write_text(json.dumps({'created_ms':NOW-86400000,'reason':'RECURRENT_NATIVE_HIP_STARTUP_SIGSEGV'}))
            gate=Gate({'state_root':str(root/'guard'),'manual_hold_path':str(hold)},'trainer')
            class Base:
                def queue_preflight(self):
                    return self.write_status(state='HOLD_NEW_TRAINING_FORWARD_WINDOW',
                        reason='TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT',next_training_at_ms=NOW+10000)
                def write_status(self,**kw):return kw
                def step(self):raise AssertionError('calendar must not launch')
            mem='MemTotal: 25165824 kB\nMemAvailable: 1048576 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n'
            psi='full avg10=0.00 avg60=0.20 avg300=0.00 total=0\n'
            with patch.object(Path,'read_text',side_effect=[mem,psi]), \
                    patch.object(gate,'acquire',side_effect=AssertionError('no lease admission')), \
                    patch('historical_training.resident_live_source.refresh',side_effect=AssertionError('no source refresh')):
                status=guarded_trainer_class(Base,gate)().step()
            for reason in ('TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT','OPERATOR_RESOURCE_HOLD',
                           'HOST_AVAILABLE_MEMORY_BELOW_8GIB','HOST_FULL_MEMORY_PSI_PRESSURE'):
                self.assertIn(reason,status['current_blockers'])
            self.assertFalse(status['automatic_resume_expected'])
            self.assertEqual(status['next_calendar_eligible_at_ms'],NOW+10000)
            diagnostic=status['resource_diagnostics']
            self.assertFalse(diagnostic['worker_launch_authority'])
            self.assertFalse(diagnostic['historical_cpu_closeout_blocked'])
            self.assertEqual(diagnostic['manual_hold']['reason'],'RECURRENT_NATIVE_HIP_STARTUP_SIGSEGV')
            self.assertFalse((root/'guard/trainer-admission.json').exists())
    def test_missing_resource_telemetry_does_not_hide_a_current_hold(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);hold=root/'manual';hold.touch()
            gate=Gate({'state_root':str(root/'guard'),'manual_hold_path':str(hold)},'trainer')
            with patch.object(Path,'read_text',side_effect=OSError('unavailable')):
                status=gate.status_diagnostics()
            self.assertEqual(set(status['current_resource_blockers']),
                {'OPERATOR_RESOURCE_HOLD','RESOURCE_DIAGNOSTICS_UNAVAILABLE'})
            self.assertTrue(status['manual_hold']['present'])
    def test_calendar_and_transient_pressure_do_not_claim_operator_resume_is_required(self):
        class Base:
            def write_status(self,**kw):return kw
        class DiagnosticGate:
            def status_diagnostics(self):
                return {'current_resource_blockers':['HOST_FULL_MEMORY_PSI_PRESSURE'],'checked_at_ms':NOW}
        engine=guarded_trainer_class(Base,DiagnosticGate())()
        result=engine.write_status(state='HOLD_NEW_TRAINING_FORWARD_WINDOW',
            reason='TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT',next_training_at_ms=NOW+10000)
        self.assertIsNone(result['automatic_resume_expected'])
        self.assertEqual(result['resume_conditions_to_recheck'],
            ['HOST_FULL_MEMORY_PSI_PRESSURE','TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT'])

class FakeGate:
    def __init__(self,allow):self.allow=allow;self.acquisitions=0;self.releases=0
    def acquire(self,subject):self.acquisitions+=1;return self.allow
    def release(self):self.releases+=1
    def check(self):return {'admit_new_worker':self.allow}

class WrapperTests(unittest.TestCase):
    def evaluator(self,gate):
        class Base:
            def __init__(self):self.tasks={};self.launch_calls=0;self.reap_calls=0
            def launch(self,path,slot):self.launch_calls+=1;self.tasks[(str(path),slot)]=object()
            def reap(self):self.reap_calls+=1;self.tasks.clear()
        return guarded_evaluator_class(Base,gate)()
    def test_denied_prediction_never_calls_original_launch(self):
        g=FakeGate(False);e=self.evaluator(g);e.launch(Path('/fixed/plan.json'),1)
        self.assertEqual(e.launch_calls,0);self.assertFalse(e.tasks)
    def test_allowed_prediction_original_launch_exactly_once(self):
        g=FakeGate(True);e=self.evaluator(g);e.launch(Path('/fixed/plan.json'),1)
        self.assertEqual(e.launch_calls,1);self.assertIn(('/fixed/plan.json',1),e.tasks)
    def test_second_prediction_waits_not_canceled_or_overlapped(self):
        g=FakeGate(True);e=self.evaluator(g);e.launch(Path('/fixed/plan.json'),1);e.launch(Path('/fixed/plan.json'),2)
        self.assertEqual(e.launch_calls,1);self.assertEqual(g.acquisitions,1);self.assertEqual(g.releases,0)
    def test_lease_released_only_after_original_reap(self):
        g=FakeGate(True);e=self.evaluator(g);e.launch(Path('/fixed/plan.json'),1);e.reap()
        self.assertEqual(e.reap_calls,1);self.assertEqual(g.releases,1)
    def test_expired_window_during_preflight_releases_unused_lease(self):
        class Base:
            def __init__(self):self.tasks={}
            def launch(self,path,slot):return False
        gate=FakeGate(True);engine=guarded_evaluator_class(Base,gate)()
        engine.launch(Path('/fixed/plan.json'),0)
        self.assertEqual(gate.acquisitions,1);self.assertEqual(gate.releases,1)
        self.assertFalse(engine.tasks)
    def test_denied_trainer_does_not_consume_rows(self):
        class Base:
            def step(self):raise AssertionError('NO_ADMISSION_OR_DATA_CONSUMPTION_ALLOWED')
            def write_status(self,**kw):return kw
        r=guarded_trainer_class(Base,FakeGate(False))().step()
        self.assertEqual(r['state'],'HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE')
    def test_allowed_trainer_releases_on_failure(self):
        class Base:
            def step(self):raise RuntimeError('SYNTHETIC_FAILURE')
        g=FakeGate(True)
        with self.assertRaises(RuntimeError):guarded_trainer_class(Base,g)().step()
        self.assertEqual(g.releases,1)
    def test_unreaped_child_keeps_original_lease_and_prevents_next_admission(self):
        class Child:
            def poll(self):return None
        class Base:
            def step(self):
                self.child=Child()
                raise RuntimeError('TERM_AND_KILL_NOT_YET_REAPED')
            def reap_pending_worker(self):
                return {'state':'WORKER_TERMINATION_PENDING'} if getattr(self,'child',None) else None
        gate=FakeGate(True);engine=guarded_trainer_class(Base,gate)()
        with self.assertRaises(RuntimeError):engine.step()
        self.assertEqual(gate.releases,0)
        self.assertEqual(engine.step()['state'],'WORKER_TERMINATION_PENDING')
        self.assertEqual(gate.acquisitions,1);self.assertEqual(gate.releases,0)
class SameRoundPreflightTests(unittest.TestCase):
    def engine(self,gate):
        from laya_cell import resident
        service=object.__new__(guarded_trainer_class(resident.Supervisor,gate))
        service.c={};service.child=None;service._pending_worker_completion=None
        service.write_status=lambda **value:value
        service.sync_rewards=Mock(return_value={'reason':'UNCHANGED_REWARDS'})
        service.reconcile_evaluation_queue=Mock(return_value={
            'deferred_training_candidates':0,'viable_pending_evaluations':0,'pending_original_trials':0})
        service._training_wait_preflight=Mock(side_effect=[None,{'state':'STOP_BEFORE_DATA'},None,{'state':'STOP_BEFORE_DATA'}])
        return service

    def test_one_controller_call_reuses_queue_but_storage_and_next_round_are_fresh(self):
        from laya_cell import resident
        gate=FakeGate(True);service=self.engine(gate)
        with patch.object(resident,'config_check') as check, \
                patch.object(resident,'bootstrap_still_off'), \
                patch.object(resident,'inspect_storage',return_value={'admission_ready':True}) as storage, \
                patch.object(resident,'collect',side_effect=AssertionError('no training data')):
            self.assertEqual(service.step()['state'],'STOP_BEFORE_DATA')
            self.assertEqual(check.call_count,1);self.assertEqual(storage.call_count,2)
            self.assertEqual(service.reconcile_evaluation_queue.call_count,1)
            self.assertTrue(service._evaluation_queue_status['same_round_preflight_reused'])
            self.assertIsNone(service._reuse_queue_preflight_once)
            self.assertEqual(service.step()['state'],'STOP_BEFORE_DATA')
            self.assertEqual(check.call_count,2);self.assertEqual(storage.call_count,4)
            self.assertEqual(service.reconcile_evaluation_queue.call_count,2)
        self.assertEqual(gate.acquisitions,2);self.assertEqual(gate.releases,2)

    def test_changed_config_cannot_reuse_prior_checks(self):
        from laya_cell import resident
        gate=FakeGate(True);service=self.engine(gate)
        def changed(_):service.c['changed_inside_gate']=True;return True
        gate.acquire=changed
        with patch.object(resident,'config_check') as check, \
                patch.object(resident,'bootstrap_still_off'), \
                patch.object(resident,'inspect_storage',return_value={'admission_ready':True}):
            self.assertEqual(service.step()['state'],'STOP_BEFORE_DATA')
        self.assertEqual(check.call_count,2)
        self.assertEqual(service.reconcile_evaluation_queue.call_count,2)
        self.assertFalse(service._evaluation_queue_status['same_round_preflight_reused'])

    def test_disk_change_during_gate_still_blocks_actual_admission(self):
        from laya_cell import resident
        gate=FakeGate(True);service=self.engine(gate)
        storage=[{'admission_ready':True},
                 {'admission_ready':False,'blocked_reason':'STATE_BUDGET_FULL_NO_EVIDENCE_PRUNING'}]
        with patch.object(resident,'config_check'),patch.object(resident,'bootstrap_still_off'), \
                patch.object(resident,'inspect_storage',side_effect=storage) as inspect, \
                patch.object(resident,'collect',side_effect=AssertionError('no training data')):
            result=service.step()
        self.assertEqual(result['state'],'HOLD_NEW_TRAINING_STORAGE')
        self.assertEqual(result['storage'],storage[-1]);self.assertEqual(inspect.call_count,2)
        self.assertEqual(gate.releases,1)


if __name__=='__main__':unittest.main()
