"""Admission tests use synthetic telemetry and mock supervisors, no models/orders."""
import copy,math,tempfile,unittest
from pathlib import Path
from historical_training.common import EvidenceError
from historical_training.resident_resource_guard import (GIB,SOURCE_RULE,RESOURCE_RULE,assess,
    parse_meminfo,parse_full_psi,Lease,guarded_evaluator_class,guarded_trainer_class,assess_resources)

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
if __name__=='__main__':unittest.main()
