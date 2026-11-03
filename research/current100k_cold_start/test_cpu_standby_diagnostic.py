"""No models, orders or signatures: real FD/process lifecycle plus boundary tests."""
import importlib.util, io, json, os
from pathlib import Path
import socket, subprocess, sys, tempfile, time, unittest
from contextlib import ExitStack, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

spec=importlib.util.spec_from_file_location('standby_diagnostic',Path(__file__).with_name('cpu_standby_diagnostic.py'))
diag=importlib.util.module_from_spec(spec);spec.loader.exec_module(diag)


class Boundaries(unittest.TestCase):
    def test_state_parity_includes_buffer_shape_dtype_and_value(self):
        state={'model/buffer/a':{'shape':[1],'dtype':'torch.float32','sha256':'a'}}
        self.assertTrue(diag.diff_state(state,state)['exact'])
        for changed in ({}, {'model/buffer/a':{**state['model/buffer/a'],'dtype':'torch.float16'}},
                        {'model/buffer/a':{**state['model/buffer/a'],'sha256':'b'}}):
            self.assertFalse(diag.diff_state(state,changed)['exact'])

    def test_denied_stale_or_reversed_original_admission_fails(self):
        now=int(time.time()*1000);tick=time.monotonic_ns()
        good={'role':'value-predictor','admit_new_worker':True,'reasons':[],'at_ms':now}
        diag.admission(good,tick)
        for report,stamp in (({**good,'admit_new_worker':False},tick),({**good,'at_ms':now-5001},tick),
                             (good,tick+10000000000),({**good,'reasons':['SOURCE_HOLD']},tick)):
            with self.assertRaises(ValueError):diag.admission(report,stamp)

    def test_changed_original_request_rejected_but_growing_prefix_is_not_a_new_identity(self):
        old={'request':{'r':1},'request_sha256':'r','producer_manifest_sha256':'p','source_record_sha256':'s',
             'source_prefix':{'bytes':10}}
        diag.same_request(old,{**old,'source_prefix':{'bytes':20}})
        for key in ('request','request_sha256','producer_manifest_sha256','source_record_sha256'):
            with self.assertRaises(ValueError):diag.same_request(old,{**old,key:'changed'})

    def test_unexpected_descriptor_is_closed_before_error(self):
        read,write=os.pipe()
        try:
            with self.assertRaisesRegex(ValueError,'DESCRIPTOR_SCOPE'):
                diag.expect(({'kind':'DONE','proof_sha256':'p'},read),'DONE')
            with self.assertRaises(OSError):os.fstat(read)
        finally:os.close(write)


class ProcessLifecycle(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);previous=os.umask(0o077);self.addCleanup(os.umask,previous)
        from historical_training import resident_resource_guard as guard
        from laya_cell import financial_forward_source as source, resident_hip_runtime as hip
        self.children=[];self.events=[];self.deny_initial=False;self.deny_fresh=False
        self.item={'request':{'request_id':'r','started_at_ms':int(time.time()*1000)-10000,
            'position_at_request':0,'model_id':'actor'},'request_sha256':'request',
            'producer_manifest_sha256':'producer','source_record_sha256':'record','source_prefix':{'fixture':True}}
        plan={'plan_id':'plan','behavior_scope':{'behavior_model_id':'actor'},'producer_registration':{'sha256':'producer'}}
        plan_path=self.root/'old-plan.json';diag.bench.write(plan_path,plan)
        guard_path=self.root/'guard.json';diag.bench.write(guard_path,{})
        config={'forward_root':str(self.root/'original-forward'),'python':sys.executable,
            'guard_config':str(guard_path),'guard_config_sha256':diag.bench.sha(guard_path),'period_sha256':'period'}
        config_path=self.root/'config.json';diag.bench.write(config_path,config)
        self.args=SimpleNamespace(config=config_path,sha256=diag.bench.sha(config_path),request_id='r',
            request_sha256='request',output=self.root/'diagnostic',threads=2,timeout_seconds=20)
        self.stack.enter_context(patch.object(diag,'original_inputs',return_value=(config,{},plan_path,plan,{}, {},{'r':self.item})))
        self.stack.enter_context(patch.object(source,'ingress',return_value=({}, {'r':self.item}, [], [])))
        self.stack.enter_context(patch.object(hip,'worker_environment',return_value=dict(os.environ)))
        # Cross-process /proc FD observation is forbidden by this executor.
        # Actual fresh flock acquisition still proves the old child FD closed.
        self.stack.enter_context(patch.object(diag,'fd_snapshot',return_value=[]))
        self.marker=self.root/'fixture-gpu-request';case=self
        class Gate:
            def __init__(self,*args):self.lease=guard.Lease(case.root/'heavy-worker.lock');self.calls=0
            def check(self):return {'role':'value-predictor','admit_new_worker':True,'reasons':[],'at_ms':int(time.time()*1000)}
            def acquire(self,subject):
                case.assertIsNone(self.lease.fd);self.calls+=1;case.events.append(('acquire',subject['phase']))
                self.last_admission=self.check()
                if (self.calls==1 and case.deny_initial) or (self.calls==2 and case.deny_fresh):
                    self.last_admission.update(admit_new_worker=False,reasons=['FIXTURE_ORIGINAL_HOLD']);return False
                return self.lease.acquire()
            def release(self):
                if self.lease.fd is not None:
                    if case.marker.exists():case.assertTrue(all(child.poll() is not None for child in case.children))
                    case.events.append(('release',self.calls));self.lease.release()
            def record_native_exit(self,*args):pass
        self.gate=Gate();self.addCleanup(self.gate.lease.release)
        self.stack.enter_context(patch.object(guard,'Gate',return_value=self.gate))
        fixture=self.root/'child.py'
        fixture.write_text('''import importlib.util,json,os,socket,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('diag',sys.argv[1]);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
proof=Path(sys.argv[2]);p=d.bench.read(proof);out=proof.parent
ch=d.Channel(socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET,0,fileno=int(sys.argv[3])),d.bench.sha(proof))
d.bench.write(out/'cpu-ready.json',{'test_fixture_not_hardware':True,'cpu_proof':{'all_parameters_and_buffers_cpu':True,'cuda_initialized':False,'gpu_device_fds':[],'tensor_count':2}})
ch.send('CPU_READY',{'cpu_receipt_sha256':d.bench.sha(out/'cpu-ready.json')})
d.expect(ch.receive(4),'RELEASE_INITIAL');os.close(int(sys.argv[4]));ch.send('CPU_IDLE_UNLOCKED')
message,fd=d.expect(ch.receive(6),'RUN_GPU_PARITY',('admission_sha256',),descriptor=True)
assert d.bench.lock_identity(fd)==p['heavy_lock_identity']
Path(sys.argv[5]).write_text('fixture-only: no actual GPU')
d.bench.write(out/'report.json',{'test_fixture_not_hardware':True,'exact_parity':True})
ch.send('DONE',{'report_sha256':d.bench.sha(out/'report.json'),'exact_parity':True})
''')
        actual_popen=subprocess.Popen
        def spawn(argv,**kwargs):
            def arg(name):return argv[argv.index(name)+1]
            process=actual_popen([sys.executable,'-B',str(fixture),str(Path(diag.__file__)),arg('--child-proof'),
                arg('--socket-fd'),arg('--heavy-fd'),str(self.marker)],pass_fds=kwargs['pass_fds'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            self.children.append(process)
            def cleanup():
                if process.poll() is None:process.kill()
                process.communicate(timeout=3)
            self.addCleanup(cleanup);return process
        self.spawn=self.stack.enter_context(patch.object(diag.subprocess,'Popen',side_effect=spawn))

    def test_both_initial_copies_close_then_original_fresh_gate_then_reap(self):
        with redirect_stdout(io.StringIO()):self.assertEqual(diag.parent(self.args),0)
        self.assertEqual(self.events,[('acquire','CPU_PRELOAD'),('release',1),('acquire','GPU_PARITY'),('release',2)])
        receipt=diag.bench.read(self.args.output/'lifecycle.json')
        self.assertTrue(receipt['initial_lease_released']);self.assertTrue(receipt['child_reaped'])
        self.assertTrue(receipt['fresh_lease_released_after_reap']);self.assertFalse(receipt['parent_imported_torch'])

    def test_fresh_original_guard_denial_never_sends_gpu_request(self):
        self.deny_fresh=True
        with redirect_stdout(io.StringIO()):self.assertEqual(diag.parent(self.args),3)
        self.assertFalse(self.marker.exists())
        receipt=diag.bench.read(self.args.output/'lifecycle.json')
        self.assertTrue(receipt['initial_lease_released']);self.assertFalse(receipt['fresh_admission_granted'])
        self.assertTrue(receipt['child_reaped']);self.assertIsNone(self.gate.lease.fd)

    def test_initial_denial_never_starts_model_child(self):
        self.deny_initial=True
        with redirect_stdout(io.StringIO()):self.assertEqual(diag.parent(self.args),3)
        self.spawn.assert_not_called();self.assertFalse(self.marker.exists())


if __name__=='__main__':unittest.main()
