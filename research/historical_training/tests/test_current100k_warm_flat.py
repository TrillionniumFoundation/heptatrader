"""CPU-only warm protocol/lifetime tests. Every path/lock/child is private.

Models and sandbox admission are replaced only in phase-order fixtures; real
flocks, anonymous pipes, subprocess termination and parent-death tests remain real.
"""
import copy, gc, io, json, os, signal, subprocess, sys, tempfile, time, unittest, weakref
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from laya_cell import current100k_warm_flat as warm
from laya_cell.integrity import ContractError, digest, sha_file
from historical_training.resident_resource_guard import Lease


class PrivateCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.stack = ExitStack(); self.addCleanup(self.stack.close)
        old = os.umask(0o077); self.addCleanup(os.umask, old)
    def enter(self, value): return self.stack.enter_context(value)
    def pipes(self):
        ar, aw = os.pipe2(os.O_CLOEXEC); br, bw = os.pipe2(os.O_CLOEXEC)
        a, b = warm.Pipe(ar, bw), warm.Pipe(br, aw)
        self.addCleanup(a.close); self.addCleanup(b.close)
        return a, b
    def lease(self, name='heavy-worker.lock'):
        value = Lease(self.root / name); self.addCleanup(value.release)
        self.assertTrue(value.acquire()); return value
    def available(self, path, expected):
        value = Lease(path)
        try: self.assertEqual(value.acquire(), expected)
        finally: value.release()
    def binding(self):
        return {key: str(i) * 64 for i, key in enumerate(warm.BINDING, 1)}


class ProfileBudgetTests(PrivateCase):
    def test_legacy_default_and_exact_opt_in(self):
        self.assertIsNone(warm.check_profile(None))
        self.assertEqual(warm.check_profile(dict(warm.PROFILE)), warm.PROFILE)
        for value in ('warm', {}, dict(warm.PROFILE, max_requests=17), dict(warm.PROFILE, automatic_renewal=0)):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, 'PROFILE_CHANGED'):
                warm.check_profile(value)
    def test_profile_cannot_be_dropped_or_changed_across_config_and_plan(self):
        c = {'warm_flat_profile': dict(warm.PROFILE), 'hip_runtime': {'selected': True}}
        p = {'warm_flat_profile': dict(warm.PROFILE), 'runtime_sha256': {'current100k_warm_flat.py': 'pinned'}}
        warm.check_binding(c, p); warm.check_binding({}, {})
        for config, plan in (({}, p), (c, {}), ({**c, 'hip_runtime': None}, p), (c, {**p, 'runtime_sha256': {}})):
            with self.subTest(config=config), self.assertRaises(ContractError): warm.check_binding(config, plan)
    def budget(self):
        return warm.Budget({'started_at_ms': 1000, 'started_ns': 1000000,
            'expires_at_ms': 181000, 'expires_ns': 180001000000})
    def test_idle_total_lifetime_and_clock_reversal_do_not_renew(self):
        for wall, tick, reason in ((151000, 150001000000, 'IDLE_EXPIRED'),
                                  (181000, 180001000000, 'SESSION_EXPIRED'),
                                  (999, 1000001, 'CLOCK_REVERSED'), (1001, 999999, 'CLOCK_REVERSED')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ContractError, reason): self.budget().check(wall, tick)
        budget = self.budget()
        with patch.object(warm.time, 'time', return_value=100), patch.object(warm.time, 'monotonic_ns', return_value=99001000000):
            budget.completed()
        with self.assertRaisesRegex(ContractError, 'SESSION_EXPIRED'): budget.check(181000, 180001000000)
    def test_request_count_is_fixed(self):
        budget = self.budget()
        with patch.object(warm.time, 'time', return_value=1.001), patch.object(warm.time, 'monotonic_ns', return_value=2000000):
            for seq in range(1, 17): self.assertEqual(budget.dispatch(), seq)
            with self.assertRaisesRegex(ContractError, 'REQUEST_BOUND'): budget.dispatch()


class PipeTests(PrivateCase):
    def test_scoped_roundtrip_and_noninheritable_endpoints(self):
        a, b = self.pipes(); binding = self.binding()
        value = warm.packet(binding, 'PREDICT', 1, request_id='r')
        a.send(value); self.assertEqual(b.receive(.01), value)
        warm.expect(value, binding, 'PREDICT', 1, ('request_id',))
        for key in warm.BINDING:
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, 'SCOPE_OR_SEQUENCE'):
                warm.expect({**value, key: 'other'}, binding, 'PREDICT', 1, ('request_id',))
        for seq in (0, 2, True):
            with self.subTest(seq=seq), self.assertRaisesRegex(ContractError, 'SCOPE_OR_SEQUENCE'):
                warm.expect({**value, 'seq': seq}, binding, 'PREDICT', 1, ('request_id',))
        self.assertFalse(os.get_inheritable(a.read_fd)); self.assertFalse(os.get_inheritable(b.write_fd))
    def test_partial_json_replay_frame_bound_duplicate_and_nonfinite_rejected(self):
        for raw, reason in ((b'{"x":1,"x":2}\n', 'DUPLICATE_KEY'), (b'{"x":NaN}\n', 'NONFINITE'),
                            (b'[]\n', 'PIPE_SHAPE'), (b'x' * warm.FRAME_LIMIT, 'FRAME_BOUND')):
            with self.subTest(reason=reason):
                a, b = self.pipes(); os.write(a.write_fd, raw)
                with self.assertRaisesRegex(ContractError, reason): b.receive(.01)
        a, b = self.pipes()
        with self.assertRaisesRegex(ContractError, 'FRAME_BOUND'): a.send({'oversized': 'x' * warm.FRAME_LIMIT})
    def test_eof_cannot_reuse_previous_admission(self):
        a, b = self.pipes(); a.close()
        with self.assertRaisesRegex(ContractError, 'PIPE_EOF'): b.receive(.01)
    def test_two_bounded_frames_remain_distinct(self):
        a, b = self.pipes(); a.send({'seq': 1}); a.send({'seq': 2})
        self.assertEqual(b.receive(.01), {'seq': 1}); self.assertEqual(b.receive(0), {'seq': 2})


class LeaseAndSourceTests(PrivateCase):
    def test_held_guard_denial_and_exception_never_reacquire_or_release(self):
        lease = self.lease(); gate = SimpleNamespace(role='value-predictor', lease=lease, check=Mock())
        report = {'admit_new_worker': False, 'reasons': ['SOURCE_CONTINUITY_NOT_READY']}
        gate.check.return_value = report
        self.assertIs(warm.held_admission(gate), report); self.available(lease.path, False)
        before = list(sys.path)
        def failed():
            sys.path.insert(0, '/fixture-original-runtime'); raise OSError('SOURCE_UNAVAILABLE')
        gate.check.side_effect = failed
        with self.assertRaisesRegex(OSError, 'SOURCE_UNAVAILABLE'): warm.held_admission(gate)
        self.assertEqual(sys.path, before); self.available(lease.path, False)
    def item(self, rid='r', started=1000, position=0):
        request = {'request_id': rid, 'started_at_ms': started, 'position_at_request': position, 'model_id': 'model'}
        return {'request': request, 'request_sha256': digest(request), 'producer_manifest_sha256': 'producer'}
    def test_only_real_flat_epoch_until_prepared_or_nonflat(self):
        item = self.item(); requests = {'r': item}
        session = {'anchor_request_id': 'r', 'anchor_request_sha256': item['request_sha256'], 'anchor_started_at_ms': 1000}
        warm.flat_epoch(requests, [{'command': {'kind': 'OPEN', 'created_at_ms': 999}}], session)
        for bindings in ([{'command': {'kind': 'OPEN', 'created_at_ms': 1000}}], [{'command': {'kind': 'OPEN'}}]):
            with self.assertRaisesRegex(ContractError, 'PREPARED'): warm.flat_epoch(requests, bindings, session)
        with self.assertRaisesRegex(ContractError, 'NONFLAT'):
            warm.flat_epoch({**requests, 'later': self.item('later', 1001, 1)}, [], session)
        with self.assertRaisesRegex(ContractError, 'ANCHOR_CHANGED'):
            warm.flat_epoch({'r': self.item(started=999)}, [], session)
    def test_source_issue_is_not_flat_or_request_authority(self):
        snapshot = SimpleNamespace(read=lambda *args: ({}, {'r': self.item()}, [], [{'reason': 'PRODUCER_GAP'}]))
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_SOURCE_UNAVAILABLE'):
            warm.original_view(snapshot, {'period_sha256': 'period'}, {'producer_registration': {}})
    def test_request_cutoff_and_path_are_not_extended(self):
        item = self.item(); plan = {'input_start_ms': 1, 'input_end_ms': 100000,
            'behavior_scope': {'behavior_model_id': 'model'}, 'producer_registration': {'sha256': 'producer'}}
        self.assertIs(warm.request_item({'r': item}, 'r', plan, 60999), item)
        with self.assertRaisesRegex(ContractError, 'NOT_FRESH_FLAT'): warm.request_item({'r': item}, 'r', plan, 61000)
        with self.assertRaisesRegex(ContractError, 'REQUEST_PATH'): warm.request_item({}, '../r', plan, 1001)


class ChildFixtures(PrivateCase):
    def fixture(self):
        from laya_cell import resident_hip_runtime as hip, financial_forward_predict as prediction
        self.trace = []; self.references = []; now = int(time.time() * 1000)
        self.plan = {'plan_id': 'plan', 'candidate_id': 'candidate', 'input_start_ms': now-10000,
            'input_end_ms': now+300000, 'producer_registration': {'sha256': 'producer'},
            'worker_runtime_inventory_digest': 'inventory', 'training_dataset_path': str(self.root/'dataset'),
            'behavior_scope': {'behavior_model_id': 'model'}}
        request = {'request_id': 'r', 'started_at_ms': now-1000, 'position_at_request': 0, 'model_id': 'model'}
        self.item = {'request': request, 'request_sha256': digest(request), 'producer_manifest_sha256': 'producer'}
        self.items = {'r': self.item}; self.bindings = []; self.issues = []
        self.snapshot = SimpleNamespace(read=lambda *args: ({}, self.items, self.bindings, self.issues))
        self.config = {'forward_root': str(self.root), 'warm_flat_profile': dict(warm.PROFILE),
            'worker_runtime_inventory_digest': 'inventory', 'period_sha256': 'period',
            'period_path': str(self.root/'period'), 'oms_path': str(self.root/'oms'),
            'attribution_directory': str(self.root/'attribution'), 'worker_read_only_paths': [],
            'worker_source_root': str(self.root/'runtime'), 'role_key_path': str(self.root/'key'),
            'witness_trust_path': str(self.root/'trust'), 'guard_config': str(self.root/'guard.json')}
        (self.root/'guard.json').write_text('{}'); self.config['guard_config_sha256'] = sha_file(self.root/'guard.json')
        self.config_path = self.root/'config.json'; self.config_path.write_text(json.dumps(self.config))
        self.heavy = self.lease(); self.controller = self.lease('predict-worker.lock')
        self.report = {'at_ms': now, 'admit_new_worker': True, 'role': 'value-predictor', 'reasons': []}
        self.gate = SimpleNamespace(lease=self.heavy, last_admission=self.report)
        self.session = warm.make_session(self.config, self.config_path, self.plan, self.item, self.gate, self.controller.fd)
        self.folder = self.root/'warm-sessions'/self.session['session_id']; self.folder.mkdir(parents=True)
        self.session_path = self.folder/'session.json'; warm.write_once(self.session_path, self.session)
        warm.admission_record(self.folder, self.session, 0, self.item, self.report)
        self.parent_pipe, self.child_pipe = self.pipes()
        self.enter(patch.object(warm.os, 'getppid', return_value=self.session['parent_pid']))
        self.death = self.enter(patch.object(warm, 'set_parent_death_guard', side_effect=lambda p:self.trace.append('death_guard')))
        self.enter(patch.object(warm, 'validated_plan', return_value=(self.plan, SimpleNamespace(), self.snapshot)))
        self.enter(patch.object(warm, 'original_guard', return_value={'state_root': str(self.root)}))
        self.enter(patch('resource.setrlimit')); self.enter(patch.object(warm.signal, 'signal'))
        self.enter(patch.object(hip, 'prepare_worker', side_effect=lambda *a:self.trace.append('prepare') or {'readonly': [str(self.root/'hip')]}))
        self.sandbox = self.enter(patch('sandbox.enter', side_effect=lambda c:self.trace.append('sandbox') or {'fixture': True}))
        self.enter(patch.object(hip, 'activate', side_effect=lambda *a:self.trace.append('activate')))
        self.prove = self.enter(patch.object(hip, 'prove', side_effect=lambda *a:self.trace.append('prove')))
        self.cuda = SimpleNamespace(is_initialized=Mock(return_value=True),
            get_device_properties=lambda _:SimpleNamespace(total_memory=8*1024**3),
            set_per_process_memory_fraction=Mock(), synchronize=Mock(), empty_cache=Mock())
        self.enter(patch.dict(sys.modules, {'torch': SimpleNamespace(cuda=self.cuda, set_num_threads=Mock())}))
        actual_gc = gc.collect
        self.enter(patch.object(warm.gc, 'collect', side_effect=lambda:self.trace.append('gc') or actual_gc()))
        def cleaned(name):
            self.trace.append(name); self.available(self.heavy.path, False)
            self.assertTrue(all(value() is None for value in self.references))
        self.cuda.synchronize.side_effect = lambda:cleaned('sync')
        self.cuda.empty_cache.side_effect = lambda:cleaned('empty')
        case = self
        class Allocation:
            def __init__(self): case.references.append(weakref.ref(self)); self.cycle = self
        class Predictor:
            def __init__(self, *args, **kwargs):
                case.trace.append('predictor'); self.raw = None; self.front_timing = {}
            def predict(self, plan, item):
                case.trace.append('predict'); self.raw = self.raw or Allocation()
                return {'fixture_commit': True}
        self.predictor_type = Predictor
        self.enter(patch.object(prediction, 'Predictor', Predictor))
    def send_request(self, seq=1, item=None, report=None):
        item = self.item if item is None else item; report = self.report if report is None else report
        wanted = warm.admission_record(self.folder, self.session, seq, item, report)
        self.parent_pipe.send(warm.packet(self.session, 'PREDICT', seq,
            request_id=item['request']['request_id'], request_sha256=item['request_sha256'], admission_sha256=wanted))
    def run_child(self):
        # The production child creates its own wrapper over the inherited FDs.
        read_fd, write_fd = self.child_pipe.read_fd, self.child_pipe.write_fd
        self.child_pipe.read_fd = self.child_pipe.write_fd = None
        try:
            warm.child(self.config_path, self.session_path, sha_file(self.session_path),
                read_fd, write_fd, self.heavy.fd, self.controller.fd)
        finally:
            # A real failed child exits; emulate its pipe closure in this
            # in-process fixture while retaining the two explicit lock FDs.
            for fd in (read_fd, write_fd):
                try: os.close(fd)
                except OSError: pass


class ChildLifecycleTests(ChildFixtures):
    def test_one_predictor_reuses_model_for_two_individually_admitted_requests(self):
        self.fixture(); second = copy.deepcopy(self.item)
        second['request']['request_id'] = 'r2'; second['request']['started_at_ms'] += 1
        second['request_sha256'] = digest(second['request']); self.items['r2'] = second
        self.send_request(); self.send_request(2, second)
        self.parent_pipe.send(warm.packet(self.session, 'DRAIN', 3, reason='TEST_DONE'))
        self.run_child()
        self.assertEqual(self.trace[:6], ['death_guard', 'prepare', 'sandbox', 'activate', 'prove', 'predictor'])
        self.assertEqual(self.trace.count('predictor'), 1); self.assertEqual(self.trace.count('predict'), 2)
        self.assertEqual(len(self.references), 1)
        replies = [self.parent_pipe.receive(.01) for _ in range(3)]
        self.assertEqual([r['kind'] for r in replies], ['READY', 'DONE', 'DONE'])
        self.assertFalse(replies[0]['model_loaded']); self.assertTrue(all(r['committed'] for r in replies[1:]))
        self.assertEqual(self.trace[-3:], ['gc', 'sync', 'empty'])
        self.available(self.heavy.path, False); self.available(self.controller.path, False)
        sandbox = self.sandbox.call_args.args[0]
        self.assertEqual(sandbox['output_dir'], str(self.root))
        self.assertNotIn(str(self.root/'guard.json'), sandbox['read_only_paths'])
    def test_false_source_admission_cannot_reach_predict(self):
        self.fixture(); denied = {**self.report, 'admit_new_worker': False,
            'reasons': ['SOURCE_CONTINUITY_NOT_READY'], 'source_counts': {'usable': 18, 'unusable': 6}}
        self.send_request(report=denied)
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_ADMISSION_DENIED'): self.run_child()
        self.assertNotIn('predict', self.trace); self.available(self.heavy.path, False)
    def test_original_ingress_hold_after_first_request_drains_before_second(self):
        self.fixture(); self.send_request(); self.send_request(2)
        good = ({}, self.items, [], [])
        self.snapshot.read = Mock(side_effect=[good, good, ({}, self.items, [], [{'reason': 'PRODUCER_GAP'}])])
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_SOURCE_UNAVAILABLE'): self.run_child()
        self.assertEqual(self.trace.count('predict'), 1); self.assertEqual(self.trace[-3:], ['gc', 'sync', 'empty'])
        self.available(self.heavy.path, False)
    def test_replayed_sequence_or_wrong_request_hash_cannot_predict(self):
        self.fixture(); self.send_request(2)
        with self.assertRaisesRegex(ContractError, 'SCOPE_OR_SEQUENCE'): self.run_child()
        self.assertNotIn('predict', self.trace); self.available(self.heavy.path, False)
    def test_expired_admission_cannot_predict(self):
        self.fixture(); self.send_request(report={**self.report, 'at_ms': self.report['at_ms']-5001})
        with self.assertRaisesRegex(ContractError, 'ADMISSION_EXPIRED'): self.run_child()
        self.assertNotIn('predict', self.trace)
    def test_original_prepared_prevents_gpu_setup(self):
        self.fixture(); self.bindings.append({'command': {'kind': 'OPEN', 'created_at_ms': self.item['request']['started_at_ms']+1}})
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_PREPARED'): self.run_child()
        self.assertEqual(self.trace, ['death_guard']); self.sandbox.assert_not_called()
        self.cuda.is_initialized.assert_not_called(); self.available(self.heavy.path, False)
    def test_sandbox_failure_never_activates_gpu(self):
        self.fixture(); self.sandbox.side_effect = PermissionError('SANDBOX_REFUSED')
        with self.assertRaisesRegex(PermissionError, 'SANDBOX_REFUSED'): self.run_child()
        self.assertNotIn('activate', self.trace); self.assertNotIn('prove', self.trace)
        self.cuda.is_initialized.assert_not_called(); self.available(self.heavy.path, False)
    def test_unproven_hip_never_calls_cuda_cleanup_or_releases_flock(self):
        self.fixture(); self.prove.side_effect = ContractError('HIP_UNPROVEN')
        with self.assertRaisesRegex(ContractError, 'HIP_UNPROVEN'): self.run_child()
        self.assertNotIn('predictor', self.trace); self.cuda.is_initialized.assert_not_called()
        self.available(self.heavy.path, False)
    def test_interrupt_clears_model_frames_before_gpu_cleanup(self):
        self.fixture(); self.send_request()
        original = self.predictor_type.predict
        def interrupted(predictor, plan, item):
            original(predictor, plan, item); raise warm.StopChild()
        self.enter(patch.object(self.predictor_type, 'predict', interrupted))
        self.run_child()
        self.assertEqual(self.trace[-3:], ['gc', 'sync', 'empty'])
        self.assertTrue(all(value() is None for value in self.references)); self.available(self.heavy.path, False)
    def test_cleanup_failure_preserves_both_inherited_locks(self):
        self.fixture(); self.send_request(); self.parent_pipe.send(warm.packet(self.session, 'DRAIN', 2, reason='TEST_DONE'))
        self.cuda.synchronize.side_effect = RuntimeError('SYNC_FAILED')
        with self.assertRaisesRegex(RuntimeError, 'SYNC_FAILED'): self.run_child()
        self.cuda.empty_cache.assert_not_called()
        self.available(self.heavy.path, False); self.available(self.controller.path, False)


class ParentLifecycleTests(PrivateCase):
    def fixture(self, deny_refresh=False):
        from historical_training import resident_resource_guard as guard
        now = int(time.time()*1000)
        self.plan = {'plan_id': 'plan', 'candidate_id': 'candidate', 'input_start_ms': now-10000,
            'input_end_ms': now+300000, 'producer_registration': {'sha256': 'producer'},
            'worker_runtime_inventory_digest': 'inventory', 'behavior_scope': {'behavior_model_id': 'model'}}
        request = {'request_id': 'r', 'started_at_ms': now-1000, 'position_at_request': 0, 'model_id': 'model'}
        self.item = {'request': request, 'request_sha256': digest(request), 'producer_manifest_sha256': 'producer'}
        self.config = {'forward_root': str(self.root), 'period_sha256': 'period',
            'warm_flat_profile': dict(warm.PROFILE), 'worker_runtime_inventory_digest': 'inventory',
            'guard_config': str(self.root/'guard.json')}
        (self.root/'guard.json').write_text('{}'); self.config['guard_config_sha256'] = sha_file(self.root/'guard.json')
        self.path = self.root/'config.json'; self.path.write_text(json.dumps(self.config))
        self.controller = self.lease('predict-worker.lock')
        self.heavy = Lease(self.root/'heavy-worker.lock'); self.addCleanup(self.heavy.release)
        self.live = self.root/'fixture-model-live'; self.launched = []
        def original_view(*args):
            commands = [{'command': {'kind': 'OPEN', 'created_at_ms': request['started_at_ms']+1}}] if self.live.exists() else []
            return ({}, {'r': self.item}, commands, [])
        snapshot = SimpleNamespace(read=original_view)
        prepared = SimpleNamespace(candidate=Mock(return_value={}))
        self.enter(patch.object(warm, 'validated_plan', return_value=(self.plan, prepared, snapshot)))
        self.enter(patch.object(warm, 'original_guard', return_value={'state_root': str(self.root)}))
        positive = {'at_ms': now, 'role': 'value-predictor', 'admit_new_worker': True, 'reasons': []}
        self.gate = SimpleNamespace(role='value-predictor', lease=self.heavy, last_admission=positive,
            acquire=Mock(side_effect=lambda *a:self.heavy.acquire()),
            check=Mock(return_value={**positive, 'admit_new_worker': not deny_refresh,
                'reasons': ['SOURCE_CONTINUITY_NOT_READY'] if deny_refresh else []}),
            record_native_exit=Mock())
        def release():
            self.assertTrue(self.launched and self.launched[0].poll() is not None)
            self.available(self.heavy.path, False); self.heavy.release()
        self.gate.release = Mock(side_effect=release)
        self.enter(patch.object(guard, 'Gate', return_value=self.gate))
        script = self.root/'finite-child.py'
        script.write_text('''import json,os,sys,time
from pathlib import Path
from laya_cell import current100k_warm_flat as w
session=json.loads(Path(sys.argv[1]).read_text())
pipe=w.Pipe(int(sys.argv[2]),int(sys.argv[3]))
for fd in (int(sys.argv[4]),int(sys.argv[5])):os.set_inheritable(fd,False)
w.set_parent_death_guard(session['parent_pid'])
pipe.send(w.packet(session,'READY',0,model_loaded=False))
stop=time.monotonic()+3
seq=0
while time.monotonic()<stop:
 m=pipe.receive(.05)
 if m is None:continue
 if m['kind']=='DRAIN':break
 seq+=1
 w.expect(m,session,'PREDICT',seq,('request_id','request_sha256','admission_sha256'))
 Path(sys.argv[6]).write_text('private CPU allocation placeholder')
 pipe.send(w.packet(session,'DONE',seq,request_id=m['request_id'],request_sha256=m['request_sha256'],committed=True,model_loaded=True))
''')
        def spawn(config, config_path, session_path, session, gate, controller_fd):
            cr, cw = os.pipe2(os.O_CLOEXEC); rr, rw = os.pipe2(os.O_CLOEXEC)
            channel = warm.Pipe(rr, cw)
            env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
            child = subprocess.Popen([sys.executable, '-B', str(script), str(session_path),
                str(cr), str(rw), str(gate.lease.fd), str(controller_fd), str(self.live)],
                pass_fds=(cr, rw, gate.lease.fd, controller_fd), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            os.close(cr); os.close(rw); self.launched.append(child)
            def clean():
                if child.poll() is None: child.kill()
                child.communicate(timeout=3)
            self.addCleanup(clean)
            return child, channel
        self.enter(patch.object(warm, 'spawn_child', side_effect=spawn))
    def test_parent_stops_on_prepared_and_reaps_before_lease_release(self):
        self.fixture()
        result = warm.run_parent(self.config, self.path, self.controller.fd)
        self.assertEqual(result['state'], 'WARM_SESSION_DRAINED')
        self.assertEqual(result['reason'], 'VALUE_WARM_ORIGINAL_PREPARED_OBSERVED')
        self.assertTrue(self.live.exists()); self.gate.check.assert_called_once()
        self.gate.release.assert_called_once(); self.assertIsNone(self.heavy.fd)
        self.available(self.heavy.path, True)
        terminal = list((self.root/'warm-sessions').glob('*/terminal.json'))
        self.assertEqual(len(terminal), 1); self.assertTrue(json.loads(terminal[0].read_text())['child_reaped'])
    def test_source_refresh_denial_drains_without_dispatch_or_model_placeholder(self):
        self.fixture(deny_refresh=True)
        result = warm.run_parent(self.config, self.path, self.controller.fd)
        self.assertEqual(result['reason'], 'ORIGINAL_GUARD_DENIED'); self.assertFalse(self.live.exists())
        self.assertEqual(result['requests_completed'], 0); self.gate.check.assert_called_once()
        self.gate.release.assert_called_once(); self.available(self.heavy.path, True)
    def test_termination_is_bounded_and_unreaped_child_does_not_claim_exit(self):
        process = Mock(); process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('private-fixture', 0)
        self.assertFalse(warm.stop_child(process, Mock(), self.binding(), 1, 'FIXTURE'))
        process.terminate.assert_called_once(); process.kill.assert_called_once()
        self.assertEqual([c.kwargs['timeout'] for c in process.wait.call_args_list], [10., 2.])


class ParentDeathTests(PrivateCase):
    def test_actual_parent_death_kills_finite_child_and_retains_flock_until_exit(self):
        controller = self.root/'controller.py'
        controller.write_text('''import os,select,subprocess,sys
from historical_training.resident_resource_guard import Lease
lease=Lease(sys.argv[1]);assert lease.acquire()
code="import os,sys,time;from laya_cell.current100k_warm_flat import set_parent_death_guard;set_parent_death_guard(int(sys.argv[1]));os.set_inheritable(int(sys.argv[2]),False);print('READY',flush=True);time.sleep(3)"
child=subprocess.Popen([sys.executable,'-B','-c',code,str(os.getpid()),str(lease.fd)],pass_fds=(lease.fd,),stdout=subprocess.PIPE,text=True)
assert child.stdout.readline().strip()=='READY'
print(child.pid,flush=True)
select.select([sys.stdin],[],[],2)
''')
        path = self.root/'parent-death.lock'; env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
        process = subprocess.Popen([sys.executable, '-B', str(controller), str(path)],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select_ready(process.stdout, 2))
            child_pid = int(process.stdout.readline().strip()); self.assertGreater(child_pid, 1)
            self.available(path, False)
            process.communicate('EXIT\n', timeout=3); self.assertEqual(process.returncode, 0)
            until = time.monotonic()+1
            while True:
                other = Lease(path)
                try: available = other.acquire()
                finally: other.release()
                if available: break
                self.assertLess(time.monotonic(), until, 'parent death did not close the child-held flock')
                time.sleep(.01)
        finally:
            if process.poll() is None: process.kill(); process.communicate(timeout=3)


def select_ready(stream, timeout):
    import select
    return bool(select.select([stream], [], [], timeout)[0])


class AdditionalChildFailures(ChildFixtures):
    def test_initial_admission_is_bound_to_session_before_gpu(self):
        self.fixture()
        path = self.folder/'admission-000.json'; value = json.loads(path.read_text())
        value['decision']['at_ms'] += 1; path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContractError, 'INITIAL_ADMISSION_CHANGED'): self.run_child()
        self.assertEqual(self.trace, ['death_guard']); self.cuda.is_initialized.assert_not_called()
    def test_message_admission_bytes_cannot_change_before_use(self):
        self.fixture(); self.send_request()
        path = self.folder/'admission-001.json'; value = json.loads(path.read_text())
        value['checked_ns'] += 1; path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContractError, 'ADMISSION_PIN_CHANGED'): self.run_child()
        self.assertNotIn('predict', self.trace); self.available(self.heavy.path, False)
    def test_partial_model_constructor_failure_cleans_before_lock_can_be_released(self):
        self.fixture()
        from laya_cell import financial_forward_predict as prediction
        case = self
        class BrokenPredictor:
            def __init__(self, *args, **kwargs):
                self.raw = SimpleAllocation(case.references)
                raise RuntimeError('MODEL_CONSTRUCTOR_FAILED')
        self.enter(patch.object(prediction, 'Predictor', BrokenPredictor))
        with self.assertRaisesRegex(RuntimeError, 'MODEL_CONSTRUCTOR_FAILED'): self.run_child()
        self.assertEqual(self.trace[-3:], ['gc', 'sync', 'empty'])
        self.assertTrue(all(value() is None for value in self.references)); self.available(self.heavy.path, False)
    def test_failed_gc_does_not_touch_cuda_or_release_either_lock(self):
        self.fixture(); self.send_request(); self.parent_pipe.send(warm.packet(self.session, 'DRAIN', 2, reason='TEST_DONE'))
        self.enter(patch.object(warm.gc, 'collect', side_effect=RuntimeError('GC_FAILED')))
        with self.assertRaisesRegex(RuntimeError, 'GC_FAILED'): self.run_child()
        self.cuda.is_initialized.assert_not_called(); self.available(self.heavy.path, False)
        self.available(self.controller.path, False)
    def test_failed_empty_cache_keeps_inherited_lock(self):
        self.fixture(); self.send_request(); self.parent_pipe.send(warm.packet(self.session, 'DRAIN', 2, reason='TEST_DONE'))
        self.cuda.empty_cache.side_effect = RuntimeError('EMPTY_CACHE_FAILED')
        with self.assertRaisesRegex(RuntimeError, 'EMPTY_CACHE_FAILED'): self.run_child()
        self.available(self.heavy.path, False)


class SimpleAllocation:
    def __init__(self, references): references.append(weakref.ref(self)); self.cycle = self


from historical_training.tests.test_current100k_forward_scope import ForwardScopeFixture


def required_source_fixture():
    """Use imported source paths in both a standard repo and staged release."""
    import hepta_decision_attribution as collector, sandbox
    from historical_training import resident_resource_guard as guard
    paths = [Path(warm.__file__).resolve().with_name(name) for name in (
        'current100k_warm_flat.py', 'current100k_forward_worker.py',
        'financial_forward_predict.py', 'financial_forward_source.py', 'resident_hip_runtime.py')]
    paths += [Path(module.__file__).resolve() for module in (collector, guard, sandbox)]
    root = Path(os.path.commonpath([str(path.parent) for path in paths])).resolve()
    return root, {path.relative_to(root).as_posix(): sha_file(path) for path in paths}


class WarmPlanPinTests(ForwardScopeFixture, unittest.TestCase):
    def test_old_plan_unchanged_and_new_profile_requires_its_frozen_runtime_pin(self):
        from laya_cell.current100k_forward import validate_bound_plan
        validate_bound_plan(self.plan, self.candidate)
        self.plan['warm_flat_profile'] = dict(warm.PROFILE)
        self.plan['plan_id'] = digest({k:v for k,v in self.plan.items() if k!='plan_id'})
        with self.assertRaisesRegex(ContractError, 'PLAN_SOURCE_PIN_REQUIRED'): validate_bound_plan(self.plan, self.candidate)
        self.plan['runtime_sha256'] = {'current100k_warm_flat.py': sha_file(Path(warm.__file__))}
        self.plan['plan_id'] = digest({k:v for k,v in self.plan.items() if k!='plan_id'})
        validate_bound_plan(self.plan, self.candidate)
        self.plan['warm_flat_profile']['max_resident_ms'] += 1
        self.plan['plan_id'] = digest({k:v for k,v in self.plan.items() if k!='plan_id'})
        with self.assertRaisesRegex(ContractError, 'PROFILE_CHANGED'): validate_bound_plan(self.plan, self.candidate)
    def test_opt_in_requires_actual_worker_and_warm_source_bytes(self):
        root, inventory = required_source_fixture()
        c = {'warm_flat_profile': dict(warm.PROFILE), 'worker_source_root': str(root),
             'worker_runtime_sha256': inventory, 'hip_runtime': {'accepted': True},
             'guard_config': str(self.root/'guard.json'), 'guard_config_sha256': '1'*64}
        warm.check_source_pin(c)
        for name in inventory:
            changed = copy.deepcopy(c); changed['worker_runtime_sha256'].pop(name)
            with self.subTest(name=name), self.assertRaisesRegex(ContractError, 'MODULE_NOT_PINNED'): warm.check_source_pin(changed)
        import hepta_decision_attribution as collector, sandbox
        from historical_training import resident_resource_guard as guard
        for module in (collector, guard, sandbox):
            with self.subTest(module=module.__name__), patch.object(module, '__file__', str(root.parent/'foreign.py')):
                with self.assertRaisesRegex(ContractError, 'MODULE_OUTSIDE_RUNTIME'): warm.check_source_pin(c)
        for key in ('guard_config', 'guard_config_sha256'):
            missing = copy.deepcopy(c); missing.pop(key)
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, 'GUARD_CONFIG_PIN_REQUIRED'): warm.check_source_pin(missing)



class WarmRegistrationTests(unittest.TestCase):
    def test_new_independent_registration_pins_opt_in_and_keeps_original_window_and_history(self):
        from historical_training.tests.test_current100k_forward_scope import NewProducerScopeRegistrationTests
        from historical_training.tests.test_current100k_hip_runtime import proof, HIP_SHA, SELECTION
        from laya_cell.financial_forward_protocol import RULE
        fixture = NewProducerScopeRegistrationTests('test_new_producer_uses_fresh_rows_and_preserves_trial_budget_and_old_bytes')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        previous = fixture.prior_path.read_bytes()
        candidate = {**fixture.candidate, 'hip_runtime_manifest_sha256': HIP_SHA, 'hip_runtime_proof': proof('train')}
        candidate['candidate_id'] = digest({k:v for k,v in candidate.items() if k!='candidate_id'})
        (fixture.directory/'candidate.json').write_text(json.dumps(candidate)); fixture.candidate = candidate
        root, inventory = required_source_fixture()
        fixture.config.update(guard_config=str(fixture.root/'guard.json'), guard_config_sha256='1'*64,
            warm_flat_profile=dict(warm.PROFILE), hip_runtime=copy.deepcopy(SELECTION),
            worker_source_root=str(root), worker_runtime_sha256=inventory, worker_runtime_inventory_digest=digest(inventory))
        plan = fixture.register()
        warm.check_binding(fixture.config, plan)
        self.assertEqual(plan['warm_flat_profile'], warm.PROFILE)
        self.assertEqual(plan['runtime_sha256']['current100k_warm_flat.py'], sha_file(Path(warm.__file__)))
        self.assertEqual(plan['input_end_ms']-plan['input_start_ms'], RULE['window_ms'])
        self.assertEqual(plan['outcome_end_ms']-plan['input_end_ms'], RULE['closure_grace_ms'])
        self.assertEqual(fixture.prior_path.read_bytes(), previous)
        self.assertEqual(plan['old_trial_ids_retained'], [fixture.prior['plan']['plan_id']])


if __name__ == '__main__': unittest.main()
