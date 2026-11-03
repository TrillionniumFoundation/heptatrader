"""CPU-only boundary fixtures plus real SCM_RIGHTS/flock/process lifecycle.

The model/source/sandbox fixtures grant no real admission and create no seals.
Actual GPU equality, installed tokenizer and HIP behavior require Pocket4.
"""
import array, io, json, os, signal, socket, subprocess, sys, tempfile, time, unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from historical_training.resident_resource_guard import Lease
from laya_cell import current100k_cpu_standby as cpu
from laya_cell.integrity import ContractError, sha_file, digest


class Bounds(unittest.TestCase):
    def test_old_signed_plan_cannot_opt_in(self):
        with self.assertRaisesRegex(ContractError, 'SIGNED_OPT_IN_REQUIRED'):
            cpu.check_binding({}, {})
        with self.assertRaisesRegex(ContractError, 'SIGNED_OPT_IN_REQUIRED'):
            cpu.check_binding({'cpu_standby_profile': dict(cpu.PROFILE)}, {})
        with self.assertRaisesRegex(ContractError, 'MIXED_PROFILE'):
            cpu.check_binding({'cpu_standby_profile': dict(cpu.PROFILE), 'warm_flat_profile': {}},
                              {'cpu_standby_profile': dict(cpu.PROFILE)})

    def test_false_expired_wrong_role_or_clock_reversed_admission_rejected(self):
        value = {'decision': {'admit_new_worker': True, 'role': 'value-predictor', 'reasons': [], 'at_ms': 1000},
                 'checked_ns': 1000000000}
        cpu.check_admission(value, now_ms=1001, now_ns=1001000000)
        for now_ms, now_ns in ((999,1001000000), (1001,999000000), (6000,6000000000)):
            with self.assertRaises(ContractError): cpu.check_admission(value, now_ms=now_ms, now_ns=now_ns)
        for field, wrong in (('admit_new_worker', False), ('role','trainer'), ('reasons',['SOURCE_HOLD'])):
            changed = {**value, 'decision': {**value['decision'], field: wrong}}
            with self.assertRaises(ContractError): cpu.check_admission(changed, now_ms=1001, now_ns=1001000000)

    def test_cpu_proof_checks_parameters_buffers_and_runtime_without_gpu_probe(self):
        def tensor(device): return SimpleNamespace(device=SimpleNamespace(type=device), grad=None,
                                                   numel=lambda: 10, element_size=lambda: 4)
        runtime = SimpleNamespace(cuda=SimpleNamespace(is_initialized=Mock(return_value=False)))
        model = SimpleNamespace(parameters=lambda:[tensor('cpu')], buffers=lambda:[tensor('cpu')])
        with patch.object(cpu, 'no_gpu_fds', return_value=[]):
            self.assertEqual(cpu.cpu_only(runtime,[model])['tensor_count'],2)
            model.buffers=lambda:[tensor('cuda')]
            with self.assertRaisesRegex(ContractError,'NON_CPU_TENSOR'): cpu.cpu_only(runtime,[model])
            runtime.cuda.is_initialized.return_value=True
            with self.assertRaisesRegex(ContractError,'CUDA_INITIALIZED'): cpu.cpu_only(runtime,[model])

    def test_cpu_gpu_device_fd_fails_even_without_cuda_init(self):
        with patch.object(cpu,'process_fds',return_value=[{'target':'/dev/kfd'}]):
            with self.assertRaisesRegex(ContractError,'GPU_DEVICE_FD'): cpu.no_gpu_fds(123)
        with patch.object(cpu,'process_fds',return_value=[{'target':'/dev/dri/renderD128'}]):
            with self.assertRaisesRegex(ContractError,'GPU_DEVICE_FD'): cpu.no_gpu_fds(123)

    def test_budget_does_not_renew_cpu_idle_or_allow_clock_reversal(self):
        session={'started_at_ms':1000,'started_ns':1000000000,'input_end_ms':1000000}
        budget=cpu.Budget(session)
        with patch.object(cpu.time,'time',return_value=2.),patch.object(cpu.time,'monotonic_ns',return_value=2000000000):
            budget.check()
        with patch.object(cpu.time,'time',return_value=1.9),patch.object(cpu.time,'monotonic_ns',return_value=2100000000):
            with self.assertRaisesRegex(ContractError,'CLOCK_REVERSED'):budget.check()
        budget=cpu.Budget(session);budget.idle_started=2000000000
        with patch.object(cpu.time,'time',return_value=152.),patch.object(cpu.time,'monotonic_ns',return_value=152000000000):
            with self.assertRaisesRegex(ContractError,'CPU_IDLE_EXPIRED'):budget.check()

    def test_original_prepared_terminates_cpu_standby(self):
        cpu.reject_new_open([{'command':{'kind':'OPEN','created_at_ms':99}}],100)
        with self.assertRaisesRegex(ContractError,'ORIGINAL_OPEN_PREPARED'):
            cpu.reject_new_open([{'command':{'kind':'OPEN','created_at_ms':100}}],100)

    def test_unreaped_child_does_not_claim_successful_cleanup(self):
        process=Mock();process.poll.return_value=None
        process.wait.side_effect=subprocess.TimeoutExpired('fixture',0)
        self.assertFalse(cpu.reap_child(process))
        process.terminate.assert_called_once();process.kill.assert_called_once()
        self.assertEqual([call.kwargs['timeout'] for call in process.wait.call_args_list],[10.,2.])

    def test_wrong_done_request_is_not_accepted(self):
        session={'session_id':'s'};item={'request':{'request_id':'r'},'request_sha256':'sha'}
        message=cpu.packet(session,'DONE',request_id='another',request_sha256='sha',committed=True)
        with self.assertRaisesRegex(ContractError,'RESULT_REQUEST_SCOPE'):
            cpu.done_receipt(session,(message,None),item)

    def test_ordinary_predict_entry_cannot_ignore_cpu_profile(self):
        from laya_cell.current100k_forward_worker import run
        with self.assertRaisesRegex(ContractError,'CPU_STANDBY_PARENT_ENTRY_REQUIRED'):
            run({'cpu_standby_profile':dict(cpu.PROFILE)},'predict')


class DescriptorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'heavy.lock';self.lease=Lease(self.path);self.addCleanup(self.lease.release)
        self.assertTrue(self.lease.acquire())
        left,right=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        self.left=cpu.Channel(left);self.right=cpu.Channel(right)
        self.addCleanup(self.left.close);self.addCleanup(self.right.close)

    def test_received_copy_retains_real_flock_and_is_not_inheritable(self):
        self.left.send({'kind':'fixture'},self.lease.fd)
        message,fd=self.right.receive(1)
        self.assertFalse(os.get_inheritable(fd));self.assertEqual(message,{'kind':'fixture'})
        self.lease.release()
        other=Lease(self.path)
        try:
            self.assertFalse(other.acquire())
            os.close(fd);fd=None
            self.assertTrue(other.acquire())
        finally:
            if fd is not None:os.close(fd)
            other.release()

    def test_extra_and_truncated_descriptors_are_closed(self):
        for count in (2,16):
            before=len(os.listdir('/proc/self/fd'))
            self.left.socket.sendmsg([b'{"kind":"fixture"}'],
                [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[self.lease.fd]*count))])
            with self.assertRaisesRegex(ContractError,'TRUNCATED_OR_EXTRA_FD'):self.right.receive(1)
            self.assertEqual(len(os.listdir('/proc/self/fd')),before)

    def test_bad_payload_does_not_leak_transferred_descriptor(self):
        before=len(os.listdir('/proc/self/fd'))
        self.left.socket.sendmsg([b'{"kind":"a","kind":"b"}'],
            [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[self.lease.fd]))])
        with self.assertRaisesRegex(ContractError,'NONCANONICAL'):self.right.receive(1)
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)


class ParentProcessTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.children=[];self.events=[]
        self.old_umask=os.umask(0o077);self.addCleanup(os.umask,self.old_umask)
        self.controller=Lease(self.root/'predict-worker.lock');self.assertTrue(self.controller.acquire())
        self.addCleanup(self.controller.release)
        now=int(time.time()*1000)
        self.plan={'plan_id':'plan','candidate_id':'head','input_start_ms':now-1000,'input_end_ms':now+60000,
            'producer_registration':{'sha256':'producer'},'behavior_scope':{'behavior_model_id':'actor'}}
        self.item={'request':{'request_id':'r','started_at_ms':now-10,'position_at_request':0,'model_id':'actor'},
                   'request_sha256':'request-sha','producer_manifest_sha256':'producer'}
        guard=self.root/'guard.json';guard.write_text('{}')
        self.config={'forward_root':str(self.root),'guard_config':str(guard),'guard_config_sha256':sha_file(guard),
            'worker_runtime_inventory_digest':'inventory'}
        self.config_path=self.root/'config.json';self.config_path.write_text(json.dumps(self.config))
        self.prepared=Mock();self.gpu_marker=self.root/'fixture-gpu-dispatch';self.deny_fresh=False
        case=self
        class FixtureGate:
            def __init__(self):
                self.lease=Lease(case.root/'heavy-worker.lock');self.calls=0;self.last_admission=None
            def check(self):
                return {'at_ms':int(time.time()*1000),'role':'value-predictor','admit_new_worker':True,'reasons':[]}
            def acquire(self,subject):
                self.calls+=1;case.assertIsNone(self.lease.fd)
                self.last_admission=self.check();case.events.append(('acquire',subject['phase']))
                if self.calls==2 and case.deny_fresh:
                    self.last_admission.update(admit_new_worker=False,reasons=['FIXTURE_ORIGINAL_SOURCE_HOLD']);return False
                return self.lease.acquire()
            def release(self):
                if self.lease.fd is not None:
                    if case.gpu_marker.exists():case.assertTrue(all(child.poll() is not None for child in case.children))
                    case.events.append(('release',self.calls));self.lease.release()
            def record_native_exit(self,*args):pass
        self.gate=FixtureGate();self.addCleanup(self.gate.lease.release)
        from historical_training import resident_resource_guard
        self.stack.enter_context(patch.object(resident_resource_guard,'Gate',return_value=self.gate))
        self.stack.enter_context(patch.object(cpu,'validated_plan',return_value=(self.plan,self.prepared,object())))
        self.stack.enter_context(patch.object(cpu,'original_guard',return_value={'state_root':str(self.root)}))
        self.stack.enter_context(patch.object(cpu,'original_view',return_value=({'r':self.item},[])))
        self.stack.enter_context(patch.object(cpu,'maintenance_hold',return_value=None))
        # The local executor denies cross-process /proc fd inspection. Keep
        # production fail-closed; this fixture replaces that observation only.
        # Real fresh flock acquisition still fails if the child kept the old fd.
        self.stack.enter_context(patch.object(cpu,'no_gpu_fds',return_value=[]))
        script=self.root/'fixture.py'
        script.write_text('''import json,os,socket,sys,time
from pathlib import Path
from laya_cell import current100k_cpu_standby as c
s=json.loads(Path(sys.argv[1]).read_text());c.set_parent_death_guard(s['parent_pid'])
ch=c.Channel(socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET,0,fileno=int(sys.argv[2])));initial=int(sys.argv[3]);marker=Path(sys.argv[4])
ch.send(c.packet(s,'CPU_READY',proof={'cuda_initialized':False,'all_parameters_and_buffers_cpu':True,'gpu_device_fds':[],'tensor_count':2}))
m,fd=ch.receive(3);assert fd is None;c.expect(s,m,'RELEASE_INITIAL');os.close(initial)
ch.send(c.packet(s,'CPU_IDLE_UNLOCKED'))
m,fd=ch.receive(5);assert fd is not None
c.expect(s,m,'PREDICT',('request_id','request_sha256','admission_sha256'))
assert c.lock_identity(fd)==s['heavy_lock_identity']
marker.write_text('CPU fixture, no real GPU or prediction')
ch.send(c.packet(s,'DONE',request_id=m['request_id'],request_sha256=m['request_sha256'],committed=True))
''')
        def spawn(config,config_path,session_path,session,gate,controller_fd):
            left,right=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
            child=subprocess.Popen([sys.executable,'-B',str(script),str(session_path),str(right.fileno()),
                str(gate.lease.fd),str(self.gpu_marker)],pass_fds=(right.fileno(),gate.lease.fd,controller_fd),
                env=dict(os.environ,PYTHONPATH=os.pathsep.join(sys.path)),stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            right.close();self.children.append(child)
            def clean():
                if child.poll() is None:child.kill()
                child.communicate(timeout=3)
            self.addCleanup(clean)
            return child,cpu.Channel(left)
        self.stack.enter_context(patch.object(cpu,'spawn_child',side_effect=spawn))

    def test_cpu_release_then_real_fresh_lock_transfer_and_reap(self):
        with redirect_stdout(io.StringIO()):result=cpu.run_parent(self.config,self.config_path,self.controller.fd)
        self.assertEqual(result['new_predictions'],1);self.assertTrue(self.gpu_marker.exists())
        self.assertEqual(self.events,[('acquire','CPU_PRELOAD'),('release',1),('acquire','GPU_REQUEST'),('release',2)])
        self.assertTrue(result['child_reaped']);self.assertIsNone(self.gate.lease.fd)

    def test_fresh_guard_denial_never_transfers_gpu_request(self):
        self.deny_fresh=True
        with redirect_stdout(io.StringIO()):result=cpu.run_parent(self.config,self.config_path,self.controller.fd)
        self.assertEqual(result['new_predictions'],0);self.assertFalse(self.gpu_marker.exists())
        self.assertEqual(result['reason'],'FRESH_ORIGINAL_GUARD_DENIED_NO_GPU')
        self.assertEqual(self.events,[('acquire','CPU_PRELOAD'),('release',1),('acquire','GPU_REQUEST')])
        self.assertTrue(result['child_reaped']);self.assertIsNone(self.gate.lease.fd)

    def test_buffered_done_is_counted_before_later_open_or_budget_expiry(self):
        send=cpu.Channel.send;check=cpu.Budget.check
        def sent(channel,message,fd=None):
            send(channel,message,fd)
            if message['kind']=='PREDICT':self.children[-1].wait(timeout=3)
        def next_check(budget,**kwargs):
            if self.gpu_marker.exists():raise ContractError('FIXTURE_POST_COMPLETION_WINDOW_ENDED')
            return check(budget,**kwargs)
        def view(*args):
            bindings=[{'command':{'kind':'OPEN','created_at_ms':int(time.time()*1000)}}] if self.gpu_marker.exists() else []
            return {'r':self.item},bindings
        with patch.object(cpu.Channel,'send',sent),patch.object(cpu.Budget,'check',next_check),\
                patch.object(cpu,'original_view',side_effect=view),redirect_stdout(io.StringIO()):
            result=cpu.run_parent(self.config,self.config_path,self.controller.fd)
        self.assertEqual(result['new_predictions'],1);self.assertTrue(result['child_reaped'])
        terminal=json.loads(next((self.root/'cpu-standby-sessions').glob('*/terminal.json')).read_text())
        self.assertEqual(terminal['result_receipt'],'COMMITTED')


class ChildPhaseTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);old=os.umask(0o077);self.addCleanup(os.umask,old)
        self.old=Lease(self.root/'heavy-worker.lock');self.assertTrue(self.old.acquire());self.addCleanup(self.old.release)
        self.fresh=Lease(self.old.path);self.addCleanup(self.fresh.release)
        self.control=Lease(self.root/'predict-worker.lock');self.assertTrue(self.control.acquire());self.addCleanup(self.control.release)
        self.initial=os.dup(self.old.fd);self.fd_copies=[self.initial]
        def close_fds():
            for fd in self.fd_copies:
                try:os.close(fd)
                except OSError:pass
        self.addCleanup(close_fds)
        now=int(time.time()*1000)
        self.plan={'plan_id':'p','candidate_id':'c','input_start_ms':now-1000,'input_end_ms':now+60000,
                   'training_dataset_path':str(self.root/'training'),
                   'candidate_directory':str(self.root),'producer_registration':{'sha256':'producer'},
                   'behavior_scope':{'behavior_model_id':'actor'}}
        self.config={'forward_root':str(self.root),'worker_source_root':str(self.root),
            'worker_runtime_inventory_digest':'inventory','base_directory':str(self.root),
            'period_path':str(self.root/'period'),'oms_path':str(self.root/'oms'),
            'attribution_directory':str(self.root/'attribution'),'worker_read_only_paths':[],
            'role_key_path':str(self.root/'private-predictor-key'),'witness_trust_path':str(self.root/'public-trust')}
        self.path=self.root/'config.json';self.path.write_text(json.dumps(self.config));(self.root/'base-manifest.json').write_text('{}')
        gate=SimpleNamespace(lease=self.old)
        self.session=cpu.make_session(self.config,self.path,self.plan,gate,self.control.fd)
        self.folder=self.root/'cpu-standby-sessions'/self.session['session_id'];self.session_path=self.folder/'session.json'
        cpu.write_once(self.session_path,self.session)
        self.decision={'admit_new_worker':True,'role':'value-predictor','reasons':[],'at_ms':now}
        cpu.record_admission(self.folder,self.session,'CPU_LOAD',self.decision)
        self.item={'request':{'request_id':'r','started_at_ms':now-10,'position_at_request':0,'model_id':'actor'},
                   'request_sha256':'request-sha','producer_manifest_sha256':'producer'}
        self.prepared=Mock();self.prepared.candidate.return_value={'base_id':'base'}
        self.stack.enter_context(patch.object(cpu,'validated_plan',return_value=(self.plan,self.prepared,object())))
        self.view=self.stack.enter_context(patch.object(cpu,'original_view',return_value=({'r':self.item},[])))
        self.stack.enter_context(patch.object(cpu,'original_guard',return_value={'state_root':str(self.root)}))
        self.stack.enter_context(patch.object(cpu,'set_parent_death_guard'))
        self.stack.enter_context(patch.object(cpu.signal,'signal'))
        self.stack.enter_context(patch.object(cpu,'maintenance_hold',return_value=None))
        self.stack.enter_context(patch.object(cpu,'no_gpu_fds',return_value=[]))
        self.stack.enter_context(patch.object(cpu.socket,'socket'))
        self.messages=[];self.gpu_decision=dict(self.decision);case=self
        class FixtureChannel:
            def __init__(self,*args):pass
            def send(self,message,fd=None):
                case.messages.append(message)
                if message['kind']=='CPU_IDLE_UNLOCKED':
                    case.fd_copies.remove(case.initial)
                    case.old.release();case.assertTrue(case.fresh.acquire())
            def receive(self,timeout):
                if case.messages[-1]['kind']=='CPU_READY':return cpu.packet(case.session,'RELEASE_INITIAL'),None
                _,pin=cpu.record_admission(case.folder,case.session,'GPU_REQUEST',case.gpu_decision,case.item)
                fd=os.dup(case.fresh.fd);case.fd_copies.append(fd)
                return cpu.packet(case.session,'PREDICT',request_id='r',request_sha256='request-sha',admission_sha256=pin),fd
            def close(self):pass
        self.stack.enter_context(patch.object(cpu,'Channel',FixtureChannel))
        self.stack.enter_context(patch('resource.setrlimit'))
        from laya_cell import resident_hip_runtime as hip, financial_forward_predict as prediction
        self.trace=[];self.cuda_started=False
        self.stack.enter_context(patch.object(hip,'prepare_worker',return_value={'readonly':[]}))
        self.sandbox=self.stack.enter_context(patch('sandbox.enter',side_effect=lambda *a:self.trace.append('sandbox') or {'fixture':True}))
        self.stack.enter_context(patch.object(hip,'activate',side_effect=lambda *a:self.trace.append('activate')))
        self.prove=self.stack.enter_context(patch.object(hip,'prove',side_effect=lambda *a:self.trace.append('prove')))
        self.cuda=SimpleNamespace(is_initialized=Mock(side_effect=lambda:self.cuda_started),
            get_device_properties=Mock(return_value=SimpleNamespace(total_memory=8*1024**3)),
            set_per_process_memory_fraction=Mock(),synchronize=Mock())
        self.torch=SimpleNamespace(cuda=self.cuda,set_num_threads=Mock(),device=lambda name:name)
        class Model:
            def __init__(self):self.device='cpu'
            def requires_grad_(self,*args):return self
            def eval(self):return self
            def to(self,device):self.device=device;case.trace.append('move_'+device);return self
            def parameters(self):return [self.tensor()]
            def buffers(self):return [self.tensor()]
            def tensor(self):return SimpleNamespace(device=SimpleNamespace(type=self.device),grad=None,
                numel=lambda:10,element_size=lambda:4)
        self.raw=Model();self.head=Model()
        self.loader=Mock(side_effect=lambda *a,**kw:self.trace.append('load_cpu') or (self.raw,object(),'base'))
        self.headloader=Mock(return_value=self.head)
        def require_gpu():
            self.assertIsNone(self.old.fd);self.assertIsNotNone(self.fresh.fd)
            self.assertEqual(cpu.lock_identity(self.fd_copies[-1]),self.session['heavy_lock_identity'])
            self.trace.append('require_gpu');self.cuda_started=True;return 'cuda'
        self.require_gpu=Mock(side_effect=require_gpu)
        self.stack.enter_context(patch.dict(sys.modules,{'torch':self.torch,
            'laya_cell.model':SimpleNamespace(load_verified_laya=self.loader,require_pocket4_gpu=self.require_gpu),
            'laya_cell.financial_value_train':SimpleNamespace(load_value_head=self.headloader),
            'safetensors':SimpleNamespace(),'safetensors.torch':SimpleNamespace(load_file=lambda *a:{})}))
        self.predictor=SimpleNamespace(heads={},predict=Mock(return_value={'fixture_commit':True}))
        self.stack.enter_context(patch.object(prediction,'Predictor',return_value=self.predictor))

    def run_child(self):
        cpu.child(self.path,self.session_path,sha_file(self.session_path),999,self.initial,self.control.fd)

    def test_cpu_phase_releases_both_old_copies_before_new_admission_and_gpu(self):
        self.run_child()
        self.assertEqual(self.trace[:4],['sandbox','activate','prove','load_cpu'])
        self.assertFalse(self.loader.call_args.kwargs['enforce_gpu'])
        self.assertEqual(self.headloader.call_args.args[1],'cpu')
        self.assertEqual([m['kind'] for m in self.messages],['CPU_READY','CPU_IDLE_UNLOCKED','DONE'])
        self.require_gpu.assert_called_once();self.predictor.predict.assert_called_once()
        self.assertTrue(self.messages[-1]['committed'])
        self.assertEqual(self.predictor.heads,{'c':self.head})
        self.fresh.release()
        probe=Lease(self.old.path)
        try:self.assertFalse(probe.acquire()) # Child copy survives until process exit.
        finally:probe.release()

    def test_denied_fresh_admission_never_touches_gpu(self):
        self.gpu_decision.update(admit_new_worker=False,reasons=['ORIGINAL_SOURCE_HOLD'])
        with self.assertRaisesRegex(ContractError,'ORIGINAL_GUARD_DENIED'):self.run_child()
        self.require_gpu.assert_not_called();self.predictor.predict.assert_not_called()
        self.assertEqual(self.raw.device,'cpu')

    def test_expired_fresh_admission_never_touches_gpu(self):
        self.gpu_decision['at_ms']-=5001
        with self.assertRaisesRegex(ContractError,'ADMISSION_EXPIRED'):self.run_child()
        self.require_gpu.assert_not_called();self.predictor.predict.assert_not_called()

    def test_fresh_request_hash_mismatch_never_touches_gpu(self):
        self.item['request_sha256']='different-request-sha'
        with self.assertRaisesRegex(ContractError,'FRESH_ADMISSION_REQUEST_SCOPE'):self.run_child()
        self.require_gpu.assert_not_called();self.predictor.predict.assert_not_called()

    def test_open_after_cpu_preparation_prevents_gpu(self):
        self.view.side_effect=[({'r':self.item},[]),({'r':self.item},[{'command':{
            'kind':'OPEN','created_at_ms':self.session['started_at_ms']}}])]
        with self.assertRaisesRegex(ContractError,'ORIGINAL_PREPARED'):self.run_child()
        self.require_gpu.assert_not_called();self.predictor.predict.assert_not_called()

    def test_sandbox_refusal_never_activates_hip_or_loads_model(self):
        self.sandbox.side_effect=PermissionError('SANDBOX_REFUSED')
        with self.assertRaisesRegex(PermissionError,'SANDBOX_REFUSED'):self.run_child()
        self.assertNotIn('activate',self.trace);self.prove.assert_not_called()
        self.loader.assert_not_called();self.require_gpu.assert_not_called()

    def test_cleanup_error_retains_fresh_child_lock_until_exit(self):
        with patch.object(cpu.gc,'collect',side_effect=RuntimeError('GC_FAILED')):
            with self.assertRaisesRegex(RuntimeError,'GC_FAILED'):self.run_child()
        self.assertEqual(self.messages[-1]['kind'],'DONE')
        self.fresh.release();probe=Lease(self.old.path)
        try:self.assertFalse(probe.acquire())
        finally:probe.release()


def source_fixture():
    from historical_training.tests.test_current100k_warm_flat import required_source_fixture
    root,inventory=required_source_fixture()
    for name in (cpu.MODULE,'model.py','financial_value_train.py'):
        path=Path(cpu.__file__).with_name(name)
        inventory[path.relative_to(root).as_posix()]=sha_file(path)
    return root,inventory


from historical_training.tests.test_current100k_forward_scope import ForwardScopeFixture


class SignedProfileTests(ForwardScopeFixture,unittest.TestCase):
    def test_plan_profile_requires_exact_new_source_pin_and_finite_limits(self):
        from laya_cell.current100k_forward import validate_bound_plan
        validate_bound_plan(self.plan,self.candidate)
        self.plan['cpu_standby_profile']=dict(cpu.PROFILE)
        self.plan['plan_id']=digest({k:v for k,v in self.plan.items() if k!='plan_id'})
        with self.assertRaisesRegex(ContractError,'CPU_STANDBY_PLAN_SOURCE_PIN_REQUIRED'):
            validate_bound_plan(self.plan,self.candidate)
        self.plan.setdefault('runtime_sha256',{})[cpu.MODULE]=sha_file(Path(cpu.__file__))
        self.plan['plan_id']=digest({k:v for k,v in self.plan.items() if k!='plan_id'})
        validate_bound_plan(self.plan,self.candidate)
        self.plan['cpu_standby_profile']['max_cpu_idle_ms']+=1
        self.plan['plan_id']=digest({k:v for k,v in self.plan.items() if k!='plan_id'})
        with self.assertRaisesRegex(ContractError,'SIGNED_OPT_IN_REQUIRED'):
            validate_bound_plan(self.plan,self.candidate)

    def test_bound_config_cannot_substitute_old_plan_or_unpinned_authority_module(self):
        root,inventory=source_fixture()
        config={'cpu_standby_profile':dict(cpu.PROFILE),'worker_source_root':str(root),
            'worker_runtime_sha256':inventory,'worker_runtime_inventory_digest':digest(inventory),
            'hip_runtime':{'fixture':True},'guard_config':str(self.root/'guard.json'),'guard_config_sha256':'1'*64}
        plan={'cpu_standby_profile':dict(cpu.PROFILE),'runtime_sha256':{cpu.MODULE:sha_file(Path(cpu.__file__))},
            'worker_runtime_inventory_digest':digest(inventory)}
        cpu.check_binding(config,plan)
        with self.assertRaisesRegex(ContractError,'SIGNED_OPT_IN_REQUIRED'):cpu.check_binding(config,{})
        key=next(name for name in inventory if name.endswith('resident_resource_guard.py'))
        config['worker_runtime_sha256']={k:v for k,v in inventory.items() if k!=key}
        with self.assertRaisesRegex(ContractError,'MODULE_NOT_PINNED'):cpu.check_binding(config,plan)


class FutureRegistrationTests(unittest.TestCase):
    def test_new_registration_binds_v2_without_rewriting_historical_trial(self):
        from historical_training.tests.test_current100k_forward_scope import NewProducerScopeRegistrationTests
        from historical_training.tests.test_current100k_hip_runtime import proof,HIP_SHA,SELECTION
        from laya_cell.financial_forward_protocol import RULE
        fixture=NewProducerScopeRegistrationTests('test_new_producer_uses_fresh_rows_and_preserves_trial_budget_and_old_bytes')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        old=fixture.prior_path.read_bytes()
        candidate={**fixture.candidate,'hip_runtime_manifest_sha256':HIP_SHA,'hip_runtime_proof':proof('train')}
        candidate['candidate_id']=digest({k:v for k,v in candidate.items() if k!='candidate_id'})
        (fixture.directory/'candidate.json').write_text(json.dumps(candidate));fixture.candidate=candidate
        root,inventory=source_fixture()
        fixture.config.update(guard_config=str(fixture.root/'guard.json'),guard_config_sha256='1'*64,
            cpu_standby_profile=dict(cpu.PROFILE),hip_runtime=dict(SELECTION),worker_source_root=str(root),
            worker_runtime_sha256=inventory,worker_runtime_inventory_digest=digest(inventory))
        plan=fixture.register();cpu.check_binding(fixture.config,plan)
        self.assertEqual(plan['cpu_standby_profile'],cpu.PROFILE)
        self.assertNotIn('warm_flat_profile',plan)
        self.assertEqual(plan['input_end_ms']-plan['input_start_ms'],RULE['window_ms'])
        self.assertEqual(plan['outcome_end_ms']-plan['input_end_ms'],RULE['closure_grace_ms'])
        self.assertEqual(fixture.prior_path.read_bytes(),old)

    def test_explicit_stage_selection_keeps_default_v1_and_only_new_predictor_preroll(self):
        from laya_cell import stage_current100k_warm_flat as stage
        import activate_current100k_warm_stage as activation
        config=stage.select_worker_profile({},'warm-flat-v1')
        self.assertIn('warm_flat_profile',config);self.assertNotIn('cpu_standby_profile',config)
        stage.select_worker_profile(config,'cpu-standby-v2')
        self.assertEqual(config['cpu_standby_profile'],cpu.PROFILE);self.assertNotIn('warm_flat_profile',config)
        plan={'registered_at_ms':1000,'input_start_ms':901000,'cpu_standby_profile':dict(cpu.PROFILE)}
        self.assertEqual(activation.predictor_start_ms(plan),871000)
        self.assertEqual(activation.predictor_start_ms({'input_start_ms':901000}),901000)
        unit=stage.finite_unit('predict','/new/config','a'*64,'/new/release','/python',6,worker_profile='cpu-standby-v2')
        for text in ('CPUQuota=400%','CPUWeight=500','TimeoutStartSec=240','Restart=no','KillMode=control-group'):
            self.assertIn(text,unit)
        default=stage.finite_unit('predict','/new/config','a'*64,'/new/release','/python',6)
        for text in ('CPUQuota=400%','CPUWeight=500','MemoryLow=768M'):
            self.assertIn(text,default)
