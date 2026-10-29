"""Admission tests use synthetic telemetry and mock supervisors, no models/orders."""
import copy,json,math,os,signal,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from historical_training.common import EvidenceError
from historical_training.resident_resource_guard import (GIB,SOURCE_RULE,RESOURCE_RULE,assess,
    parse_meminfo,parse_full_psi,Lease,Gate,guarded_evaluator_class,guarded_trainer_class,assess_resources,
    collect_resource_attribution,ATTRIBUTION_LIMITS)

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
    def setUp(self):
        attribution=patch('historical_training.resident_resource_guard.collect_resource_attribution',
            return_value={'at_ms':NOW,'diagnostic_only':True,'partial':False})
        attribution.start();self.addCleanup(attribution.stop)
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
    def test_terminal_forward_wait_preserves_hold_but_identifies_owner_next_action(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);hold=root/'manual.json'
            hold.write_text(json.dumps({'created_ms':NOW-86400000,'owner':'original-owner',
                'reason':'PAUSE_NEW_BUDGET_PENDING_SAME_CANDIDATE_FORWARD',
                'release_conditions':'Finite diagnosis and original guard healthy; exact marker only'}))
            before=hold.read_bytes()
            gate=Gate({'state_root':str(root/'guard'),'manual_hold_path':str(hold)},'trainer')
            class Base:
                def write_status(self,**kw):return kw
            with patch('historical_training.resident_live_source.refresh',side_effect=AssertionError('no source refresh')):
                status=guarded_trainer_class(Base,gate)().write_status(
                    state='FORWARD_WORK_COMPLETE_AWAITING_NEW_CANDIDATE',pending_original_trials=0)
            current=status['resource_diagnostics']['manual_hold']
            self.assertEqual(current['waiting_condition_state'],'NO_PENDING_FORWARD_TRIAL')
            self.assertEqual(current['owner'],'original-owner')
            self.assertIn('exact marker',current['release_conditions'])
            self.assertEqual(current['next_action'],'OWNER_CLOSE_TERMINAL_FORWARD_WAIT_THEN_RECHECK_TRAINING_ADMISSION')
            self.assertIn('OPERATOR_RESOURCE_HOLD',status['current_blockers'])
            self.assertFalse(status['automatic_resume_expected']);self.assertEqual(hold.read_bytes(),before)
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


class BoundedControllerTests(unittest.TestCase):
    def run_controller(self,flag,*,failure=None):
        from historical_training import resident_resource_guard as module
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root=Path(td);config={'original_config':str(root/'original.json')}
            original={'control_directory':str(root),'runtime_directory':str(root/'runtime')}
            gate=Mock();gate.root=root;gate.draining=False
            service=Mock();service.stop=False;service.child=None
            service.step.side_effect=failure
            stack.enter_context(patch.object(module,'read',return_value=config))
            stack.enter_context(patch.object(module,'verify_config',return_value=original))
            stack.enter_context(patch.object(module,'Gate',return_value=gate))
            stack.enter_context(patch.object(module,'guarded_trainer_class',return_value=lambda path:service))
            stack.enter_context(patch.object(module.signal,'signal'))
            stack.enter_context(patch.object(module.time,'sleep',side_effect=AssertionError('bounded mode cannot poll')))
            stack.enter_context(patch.object(sys,'argv',['guard','--config',str(root/'wrapper.json'),'--role','trainer',flag]))
            if failure is None:module.main()
            else:
                with self.assertRaisesRegex(RuntimeError,'step failed'):module.main()
            service.db.close.assert_called_once();gate.release.assert_called_once()
            return service

    def test_once_finishes_one_normal_step_before_cleanup_and_never_polls(self):
        service=self.run_controller('--once')
        service.recover.assert_called_once();service.step.assert_called_once()
        service.refresh_status.assert_not_called()
        self.assertLess(service.mock_calls.index(('step',(),{})),service.mock_calls.index(('db.close',(),{})))

    def test_refresh_only_never_recovers_or_dispatches(self):
        service=self.run_controller('--refresh-status')
        service.refresh_status.assert_called_once();service.recover.assert_not_called();service.step.assert_not_called()

    def test_once_failure_has_failing_exit_after_original_error_status(self):
        service=self.run_controller('--once',failure=RuntimeError('step failed'))
        service.step.assert_called_once();service.write_status.assert_called_once()


class ResourceAttributionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.proc=self.root/'proc';self.cg=self.root/'cgroup'
        self.proc.mkdir();self.cg.mkdir()
        (self.proc/'meminfo').write_text('MemAvailable: 100 kB\nSReclaimable: 80 kB\nSwapFree: 1 kB\n')
        (self.proc/'vmstat').write_text('pswpin 42\npgpgin 1000\n')
    def process(self,pid,name,rss,group):
        root=self.proc/str(pid);root.mkdir();fields=['R']+['0']*21
        fields[11]='40';fields[12]='3';fields[19]=str(pid*100);fields[21]=str(rss)
        (root/'stat').write_text(f'{pid} ({name}) '+ ' '.join(fields)+'\n')
        (root/'cgroup').write_text('0::/'+group+'\n')
        (root/'cmdline').write_text('SECRET_ARGUMENTS_NOT_FOR_DIAGNOSTICS')
        (root/'environ').write_text('SECRET_ENV_NOT_FOR_DIAGNOSTICS')
        return root
    def group(self,name):
        root=self.cg/name;root.mkdir(parents=True)
        for file,raw in {'memory.current':'8192\n','memory.stat':'anon 4096\nslab_reclaimable 2048\n',
            'memory.swap.current':'0\n','memory.max':'max\n',
            'memory.events':'high 3\noom_kill 0\n',
            'memory.pressure':'full avg10=1.2 avg60=0.3 avg300=0.2 total=100\n',
            'cpu.stat':'usage_usec 1000\nnr_throttled 2\n',
            'io.stat':'259:0 rbytes=4096 wbytes=32 rios=2 wios=1\n'}.items():
            (root/file).write_text(raw)
        return root
    def sample(self,**overrides):
        return collect_resource_attribution(proc_root=self.proc,cgroup_root=self.cg,
            limits={**ATTRIBUTION_LIMITS,**overrides},clock=lambda:0.,observer_pid=10)
    def test_concurrent_workload_and_kernel_pressure_are_observed_without_claiming_cause(self):
        self.process(10,'model',2,'user.slice/model.service')
        self.process(20,'du ) scan',20,'user.slice/audit.scope/child')
        self.group('user.slice/model.service');self.group('user.slice/audit.scope')
        result=self.sample()
        self.assertFalse(result['partial']);self.assertEqual(result['processes'][0]['name'],'du ) scan')
        self.assertEqual(result['processes'][0]['start_ticks'],2000)
        self.assertEqual(result['processes'][0]['cgroup'],'user.slice/audit.scope')
        self.assertEqual(result['cgroups'][0]['memory_full_psi']['avg60'],.3)
        self.assertEqual(result['cgroups'][0]['io_stat']['rbytes'],4096)
        self.assertEqual(result['host_memory_bytes']['SReclaimable'],80*1024)
        self.assertEqual(result['host_vmstat']['pswpin'],42)
        self.assertEqual(result['causality'],'NOT_ESTABLISHED')
        self.assertEqual(result['counter_semantics'],'CUMULATIVE_NOT_RATES')
        self.assertNotIn('SECRET',json.dumps(result))
    def test_pid_and_cgroup_limits_keep_explicitly_partial_result(self):
        for pid in (10,20,30):
            name=f'user.slice/p{pid}.service';self.process(pid,'worker',pid,name);self.group(name)
        result=self.sample(max_pids=2,max_cgroups=1)
        self.assertEqual({row['pid'] for row in result['processes']},{10,30})
        self.assertEqual(len(result['cgroups']),1);self.assertTrue(result['partial'])
        self.assertEqual(result['cgroups'][0]['path'],'user.slice/p10.service')
        self.assertEqual(result['cgroups'][0]['selection_reason'],'OBSERVER_CGROUP')
        self.assertEqual(set(result['scan']['limits_reached']),{'PID_BUDGET','CGROUP_BUDGET'})
    def test_small_swap_heavy_related_services_survive_rss_and_pid_cutoffs(self):
        parent='user.slice/user-1000.slice/user@1000.service/app.slice/'
        own=parent+'heptatrader-laya-trainer.service'
        evaluator=parent+'heptatrader-laya-evaluator.service'
        health=parent+'heptatrader-learning-source-health.service'
        self.process(10,'guard',1,own);self.group(own);self.group(evaluator)
        health_root=self.group(health)
        (health_root/'memory.current').write_text(str(110*1024**2)+'\n')
        (health_root/'memory.swap.current').write_text(str(285*1024**2)+'\n')
        (health_root/'memory.max').write_text(str(512*1024**2)+'\n')
        (health_root/'memory.events').write_text('max 12680\noom_kill 0\n')
        # Source-health and evaluator have no PID in this bounded proc fixture;
        # their fixed sibling cgroups must still be sampled before RSS ranking.
        for pid in range(20,32):
            group=parent+f'large-{pid}.scope'
            self.process(pid,'large',1000,group);self.group(group)
        for pid_limit in (512,4):
            with self.subTest(pid_limit=pid_limit):
                result=self.sample(max_pids=pid_limit)
                groups={group['path']:group for group in result['cgroups']}
                self.assertTrue({own,evaluator,health}.issubset(groups))
                self.assertEqual(result['cgroups'][0]['path'],own)
                self.assertEqual(groups[health]['selection_reason'],'RELATED_RESEARCH_UNIT')
                self.assertEqual(groups[health]['observed_processes'],0)
                self.assertEqual(groups[health]['memory_events']['max'],12680)
                self.assertEqual(groups[health]['memory_swap_current_bytes'],285*1024**2)
                self.assertEqual(groups[health]['memory_max'],str(512*1024**2))
                self.assertLessEqual(len(groups),8)
                self.assertLessEqual(result['scan']['pids_attempted'],pid_limit)
    def test_observed_related_unit_in_another_slice_is_prioritized_without_guessing_duplicate(self):
        manager='user.slice/user-1000.slice/user@1000.service/'
        own=manager+'app.slice/heptatrader-laya-trainer.service'
        evaluator=manager+'app.slice/heptatrader-laya-evaluator.service'
        health=manager+'background.slice/heptatrader-learning-source-health.service'
        self.process(10,'guard',1,own);self.group(own);self.group(evaluator)
        self.process(20,'source',1,health);self.group(health)
        result=self.sample();groups={group['path']:group for group in result['cgroups']}
        self.assertFalse(result['partial'])
        self.assertEqual(groups[health]['selection_reason'],'RELATED_RESEARCH_UNIT')
        self.assertNotIn(manager+'app.slice/heptatrader-learning-source-health.service',groups)
    def test_file_byte_and_cooperative_time_budgets_stop_reads(self):
        self.process(10,'worker',2,'unit.service');self.group('unit.service')
        byte_limited=self.sample(max_bytes=16)
        self.assertLessEqual(byte_limited['scan']['bytes_read'],16)
        self.assertIn('BYTE_BUDGET',byte_limited['scan']['limits_reached'])
        files=self.sample(max_files=1)
        self.assertEqual(files['scan']['files_attempted'],1)
        self.assertIn('FILE_BUDGET',files['scan']['limits_reached'])
        ticks=iter((0.,.1,.1))
        timeout=collect_resource_attribution(proc_root=self.proc,cgroup_root=self.cg,
            clock=lambda:next(ticks))
        self.assertEqual(timeout['scan']['files_attempted'],0)
        self.assertIn('TIME_BUDGET',timeout['scan']['limits_reached'])
    def test_process_and_cgroup_symlinks_and_parent_traversal_are_not_followed(self):
        outside=self.root/'outside';outside.mkdir()
        (outside/'memory.current').write_text('987654321\n')
        (self.proc/'99').symlink_to(outside,target_is_directory=True)
        self.process(10,'worker',2,'escape.service')
        (self.cg/'escape.service').symlink_to(outside,target_is_directory=True)
        self.process(20,'worker',3,'../outside')
        result=self.sample()
        self.assertTrue(result['partial']);self.assertGreater(result['scan']['read_errors'],0)
        self.assertNotIn('987654321',json.dumps(result))
        self.assertIsNone(next(row for row in result['processes'] if row['pid']==20)['cgroup'])
    def test_cached_denial_snapshot_is_bounded_in_age_and_does_not_change_admission(self):
        gate=Gate({'state_root':str(self.root/'guard')},'evaluator')
        with patch('historical_training.resident_resource_guard.collect_resource_attribution',
                return_value={'at_ms':NOW,'diagnostic_only':True}) as sample, \
                patch('historical_training.resident_resource_guard.time.monotonic',side_effect=[10.,11.,41.]):
            reports=[]
            for _ in range(3):
                reports.append(gate.attach_resource_attribution({'admit_new_worker':False,
                    'reasons':['HOST_FULL_MEMORY_PSI_PRESSURE']}))
        self.assertEqual(sample.call_count,2)
        self.assertEqual(reports[1]['workload_attribution']['sample_age_ms'],1000)
        self.assertTrue(reports[1]['workload_attribution']['reused_sample'])
        self.assertTrue(all(row['admit_new_worker'] is False for row in reports))
        self.assertTrue(all(row['reasons']==['HOST_FULL_MEMORY_PSI_PRESSURE'] for row in reports))
    def test_each_denied_window_keeps_its_subject_and_diagnostic_failure_does_not_admit(self):
        gate=Gate({'state_root':str(self.root/'guard')},'evaluator')
        with patch.object(gate,'check',side_effect=lambda:{'admit_new_worker':False,
                'reasons':['HOST_FULL_MEMORY_PSI_PRESSURE']}), \
                patch('historical_training.resident_resource_guard.collect_resource_attribution',
                      side_effect=OSError('telemetry unavailable')):
            self.assertFalse(gate.acquire({'plan':'frozen','slot':1}))
            self.assertFalse(gate.acquire({'plan':'frozen','slot':2}))
        records=[json.loads(p.read_text()) for p in (self.root/'guard/denials').glob('*.json')]
        self.assertEqual({row['subject']['slot'] for row in records},{1,2})
        for row in records:
            self.assertFalse(row['decision']['admit_new_worker'])
            self.assertEqual(row['decision']['workload_attribution']['error_class'],'OSError')
        with patch('historical_training.resident_resource_guard.collect_resource_attribution',
                side_effect=AssertionError('source/manual denials must stay cheap')):
            for reason in ('OPERATOR_RESOURCE_HOLD','SOURCE_QUALITY_HOLD_NO_REFRESH_AUTHORITY'):
                report=gate.attach_resource_attribution({'admit_new_worker':False,'reasons':[reason]})
                self.assertNotIn('workload_attribution',report)

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
