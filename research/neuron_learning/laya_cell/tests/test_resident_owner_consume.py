"""Finite CPU/OS consumer fixtures, never production enrollment or GPU work."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from historical_training import resident_resource_guard as guard
from laya_cell import resident_owner as owner
from laya_cell import resident_owner_consume as consumer
from laya_cell import resident_launch
from laya_cell.integrity import ContractError, canonical, digest, read_json, sha_file
from laya_cell.resident_store import write_once
from laya_cell.tests.test_resident_owner import fixture


class AdvisoryEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template, _ = fixture()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = deepcopy(self.template); self.raw = canonical(self.payload) + b'\n'
        path = self.root / 'selected.json'; path.write_bytes(self.raw)
        self.prepared = {'schema': 'heptatrader.laya-owner-prepared.v1',
            'plan_id': self.payload['plan']['plan_id'], 'payload_path': str(path),
            'manifest': owner.manifest_for(self.payload, self.raw, 'synthetic-fixture-producer'),
            'support_digest': '4' * 64}
        self.config = {'period_sha256': 'b' * 64, 'control_directory': str(self.root / 'control')}
        self.request = {'request_id': 'synthetic-advisory-request', 'created_at_ms': 55000000,
            'expires_at_ms': 55100000, 'period_sha256': 'b' * 64, 'horizon_sec': 300}
        self.prepared_path = self.root / 'prepared.json'; write_once(self.prepared_path, self.prepared)
        self.request_path = self.root / 'request.json'; write_once(self.request_path, self.request)
        self.args = SimpleNamespace(inside_unit='synthetic-unit-proof-is-mocked',
            prepared=self.prepared_path, prepared_sha256=sha_file(self.prepared_path),
            request=self.request_path, request_sha256=sha_file(self.request_path), wrapper_sha256='a' * 64)
        self.wrapper = {'original_config_sha256': 'c' * 64, 'original_config': str(self.root / 'config.json')}
        c = self.payload['candidate']; m = self.prepared['manifest']
        self.output = {'schema': 'heptatrader.laya-selected-price-output.v1',
            'request_id': self.request['request_id'], 'request_digest': digest(self.request),
            'candidate_id': c['candidate_id'], 'base_id': c['base_id'], 'trained_until_ms': c['trained_until_ms'],
            'price_output_profile': c['price_output_profile'], 'action_output_profile': c['action_output_profile'],
            'input_id': '5' * 64, 'sample_id': '6' * 64, 'source_prefixes_digest': '7' * 64,
            'anchor_ms': 54999000, 'created_at_ms': 55000000, 'horizon_sec': 300,
            'price_probs': [1 / 7] * 7, 'raw_price_probs': [1 / 7] * 7, 'action_correct_probs': None,
            'action_head_is_trade_permission': False, 'broker_orders_sent': 0}
        self.loaded = {'artifact_id': m['artifact_id'], 'artifact_generation': m['generation'],
            'content_digest': m['bytes_digest'], 'support_digest': self.prepared['support_digest'],
            'compatibility_digest': m['compatibility_digest'], 'selection_digest': '8' * 64,
            'registry_head_digest': '9' * 64, 'payload_hex': self.raw.hex(),
            'loaded_registry': {'records': 2, 'head_digest': '9' * 64},
            'loaded_lifecycle': {'records': 7, 'head_digest': '0' * 64}}
        self.folder = self.root / 'control/owner-outbox' / self.prepared['plan_id'] / 'advisory' / digest(self.request)
        self.clock = 55000.; self.calls = []; self.handlers = {}

    def run_controller(self, *, changed_current=False, prediction=None, writer=None):
        def native(config, request):
            self.calls.append(request['operation'])
            value = deepcopy(self.loaded)
            if request['operation'] == 'revalidate_laya_output':
                value.pop('payload_hex')
                value.update(request_digest=request['request_digest'], output_digest=request['output_digest'])
                if changed_current:
                    value['selection_digest'] = 'd' * 64
            return value
        worker = prediction or (lambda *args: deepcopy(self.output))
        with patch.object(consumer, 'check_unit', return_value={'synthetic_fixture': True}), \
             patch.object(consumer, 'enable_subreaper'), \
             patch.object(consumer.signal, 'signal', side_effect=lambda number, handler: self.handlers.update({number: handler})), \
             patch.object(consumer.time, 'time', side_effect=lambda: self.clock), \
             patch.object(guard, 'Gate', return_value=Mock()), \
             patch.object(owner, 'enrollment', return_value={'synthetic_unit_fixture': True}), \
             patch.object(owner, '_selection_request', return_value={}), patch.object(owner, 'native', side_effect=native), \
             patch.object(consumer, 'admitted_prediction', side_effect=worker), \
             patch.object(consumer, 'write_once', side_effect=writer or write_once):
            return consumer.controller(self.args, guard, self.wrapper, self.config)

    def test_normal_entrypoint_binds_real_owner_consume_and_repeated_request_is_only_historical(self):
        value = self.run_controller()
        self.assertTrue(value['product_consumed']); self.assertTrue(value['current_output_released'])
        receipt = read_json(Path(value['receipt_path']))
        self.assertEqual(receipt['binding']['artifact_id'], self.prepared['manifest']['artifact_id'])
        self.assertEqual(receipt['result']['consume_receipt']['selection_digest'], '8' * 64)
        self.assertEqual(self.calls, ['load_laya_selected', 'revalidate_laya_output'])
        repeated = self.run_controller()
        self.assertEqual(repeated['state'], 'CANONICAL_ADVISORY_REQUEST_ALREADY_TERMINAL')
        self.assertFalse(repeated['product_consumed']); self.assertNotIn('output', repeated)
        self.assertEqual(len(self.calls), 2)

    def test_changed_current_discards_model_output_and_never_publishes(self):
        value = self.run_controller(changed_current=True)
        self.assertEqual(value['state'], 'CANONICAL_ADVISORY_BLOCKED')
        self.assertIn('LAYA_FINAL_USE_BINDING', value['reason'])
        self.assertFalse(value['product_consumed']); self.assertNotIn('output', value)
        self.assertEqual(self.calls, ['load_laya_selected', 'revalidate_laya_output'])

    def test_request_expiry_during_worker_blocks_output_before_final_use(self):
        def late(*args):
            self.clock = 55101.
            return deepcopy(self.output)
        value = self.run_controller(prediction=late)
        self.assertFalse(value['product_consumed']); self.assertNotIn('output', value)
        self.assertEqual(self.calls, ['load_laya_selected'])

    def test_slow_final_fsync_does_not_deliver_expired_response(self):
        def slow(path, value):
            write_once(path, value)
            if Path(path).name == 'receipt.json':
                self.clock = 55101.
        value = self.run_controller(writer=slow)
        self.assertEqual(value['state'], 'CANONICAL_ADVISORY_OUTPUT_EXPIRED_BEFORE_DELIVERY')
        self.assertFalse(value['current_output_released']); self.assertNotIn('output', value)
        self.assertTrue(read_json(Path(value['historical_receipt_path']))['product_consume_completed'])

    def test_receipt_write_failure_keeps_intent_and_does_not_rerun_native_or_model(self):
        def fail_final(path, value):
            if Path(path).name == 'receipt.json':
                raise OSError('synthetic ENOSPC after native final check')
            write_once(path, value)
        with self.assertRaisesRegex(OSError, 'synthetic ENOSPC'):
            self.run_controller(writer=fail_final)
        self.assertTrue((self.folder / 'attempt.json').exists())
        again = self.run_controller(prediction=Mock(side_effect=AssertionError('no second model')))
        self.assertEqual(again['state'], 'CANONICAL_ADVISORY_INCOMPLETE_ATTEMPT_REQUIRES_REVIEW')
        self.assertEqual(len(self.calls), 2)

    def test_cancellation_during_final_receipt_write_does_not_deliver_output(self):
        def cancel(path, value):
            write_once(path, value)
            if Path(path).name == 'receipt.json':
                self.handlers[signal.SIGTERM]()
        value = self.run_controller(writer=cancel)
        self.assertEqual(value['state'], 'CANONICAL_ADVISORY_CANCELLED_BEFORE_DELIVERY')
        self.assertFalse(value['current_output_released']); self.assertNotIn('output', value)

    def test_absent_enrollment_exits_normal_cli_without_unit_native_or_input_files(self):
        argv = ['consumer', '--wrapper', str(self.root / 'wrapper.json'),
                '--prepared', str(self.root / 'does-not-exist'), '--request', str(self.root / 'missing-request')]
        wrapper = {'original_config': '/synthetic/config', 'original_config_sha256': 'a' * 64}
        with patch.object(sys, 'argv', argv), patch.object(consumer, '_read_bytes', return_value=canonical(wrapper)), \
             patch.object(consumer, '_read_pinned', return_value={}), \
             patch.object(guard, 'verify_config', return_value={}), \
             patch.object(consumer, 'check_runtime', return_value=None), \
             patch.object(consumer.subprocess, 'run', side_effect=AssertionError('no unit/native')), \
             patch.object(sys, 'stdout', new_callable=io.StringIO) as output:
            consumer.main()
        self.assertEqual(json.loads(output.getvalue())['state'], 'AWAITING_CANONICAL_OWNER_ENROLLMENT')
        self.assertFalse((self.root / 'control').exists())

    def test_pin_hash_and_json_are_the_same_read_even_if_the_path_is_replaced(self):
        path = self.root / 'pin.json'; original = {'public_fixture': 'old'}
        write_once(path, original); expected = sha_file(path)
        read_bytes = consumer._read_bytes
        def replace_after_read(source, maximum):
            raw = read_bytes(source, maximum)
            Path(source).write_bytes(canonical({'public_fixture': 'new'}))
            return raw
        with patch.object(consumer, '_read_bytes', side_effect=replace_after_read):
            self.assertEqual(consumer._read_pinned(path, expected), original)
        with self.assertRaisesRegex(ContractError, 'LAYA_ADVISORY_INPUT_CHANGED'):
            consumer._read_pinned(path, expected)

    def test_worker_preserves_explicit_gpu_visibility_but_drops_credential_environment(self):
        with patch.dict(os.environ, {'HIP_VISIBLE_DEVICES': '', 'SYNTHETIC_SECRET_TOKEN': 'never-forward'}, clear=True):
            result = consumer.worker_environment({'runtime_directory': '/synthetic/runtime'})
        self.assertEqual(result['HIP_VISIBLE_DEVICES'], '')
        self.assertNotIn('SYNTHETIC_SECRET_TOKEN', result)

    def test_systemd_client_timeout_reports_unknown_and_never_retries(self):
        argv = ['consumer', '--wrapper', str(self.root / 'wrapper.json'),
                '--prepared', str(self.prepared_path), '--request', str(self.request_path)]
        wrapper = {'original_config': '/synthetic/config', 'original_config_sha256': 'a' * 64}
        config = {'python': sys.executable, 'runtime_directory': '/synthetic/runtime'}
        with patch.object(sys, 'argv', argv), patch.object(consumer, '_read_bytes', return_value=canonical(wrapper)), \
             patch.object(consumer, '_read_pinned', return_value=config), \
             patch.object(guard, 'verify_config', return_value=config), patch.object(consumer, 'check_runtime', return_value={}), \
             patch.object(consumer.subprocess, 'run', side_effect=subprocess.TimeoutExpired('synthetic systemd client', 225)) as run, \
             patch.object(sys, 'stdout', new_callable=io.StringIO) as output, self.assertRaises(SystemExit) as stopped:
            consumer.main()
        self.assertEqual(stopped.exception.code, 2); run.assert_called_once()
        self.assertEqual(run.call_args.kwargs['timeout'], 225)
        value = json.loads(output.getvalue())
        self.assertFalse(value['product_consumed']); self.assertFalse(value['automatic_retry'])
        self.assertIn('RESULT_UNOBSERVED', value['state'])

    def test_unit_command_is_bounded_and_flag_alone_is_not_accepted(self):
        hashes = {'wrapper': '1' * 64, 'prepared': '2' * 64, 'request': '3' * 64}
        command = consumer.unit_command({'python': sys.executable, 'runtime_directory': '/synthetic/runtime'},
            '/synthetic/wrapper', '/synthetic/prepared', '/synthetic/request', hashes, '/synthetic/guard/historical_training')
        for value in ('--property=KillMode=control-group', '--property=RuntimeMaxSec=180',
                      '--property=Restart=no', '--property=TasksMax=96'):
            self.assertIn(value, command)
        unit = 'heptatrader-laya-advisory-' + '3' * 24 + '-10.service'
        props = {'MainPID': str(os.getpid()), 'ControlGroup': '/synthetic/' + unit,
            'KillMode': 'control-group', 'RuntimeMaxUSec': '3min', 'TimeoutStopUSec': '15s',
            'MemoryMax': str(consumer.MEMORY_BYTES), 'TasksMax': '96', 'Restart': 'no', 'SendSIGKILL': 'yes'}
        for field, bad in ((None, None), ('KillMode', 'process'), ('RuntimeMaxUSec', 'infinity'),
                           ('MainPID', '1'), ('TasksMax', '192')):
            values = {**props, **({field: bad} if field else {})}
            result = SimpleNamespace(stdout='\n'.join(key + '=' + value for key, value in values.items()))
            with self.subTest(field=field), patch.object(consumer.subprocess, 'run', return_value=result), \
                 patch.object(Path, 'read_text', return_value='0::/synthetic/' + unit + '\n'):
                if field is None:
                    self.assertEqual(consumer.check_unit(unit)['KillMode'], 'control-group')
                else:
                    with self.assertRaises((ContractError, ValueError)):
                        consumer.check_unit(unit)

    def test_viable_trial_before_or_after_lease_prevents_a_gpu_process(self):
        gate = Mock(); gate.acquire.return_value = True
        blocked = consumer.Deferred('VIABLE_ORIGINAL_EVALUATION_HAS_PRIORITY', {'viable_pending_evaluations': 1})
        for sequence, acquired in (([blocked], False), ([{}, blocked], True)):
            gate.reset_mock()
            with patch.object(consumer, 'check_queue', side_effect=sequence), \
                 patch.object(consumer.subprocess, 'Popen', side_effect=AssertionError('no GPU process')), \
                 self.assertRaises(consumer.Deferred):
                consumer.admitted_prediction(self.root / 'config.json', self.config, self.folder,
                    self.raw, self.request, gate, Mock(), lambda: False)
            self.assertEqual(gate.acquire.called, acquired)
            self.assertEqual(gate.release.called, acquired)

    def test_advisory_keeps_original_memory_and_value_priority(self):
        memory = {'MemAvailable': 7 * guard.GIB, 'SwapFree': 0, 'MemTotal': 32 * guard.GIB, 'SwapTotal': guard.GIB}
        pressure = {'avg10': 0., 'avg60': 0.}
        for role in ('evaluator', 'owner-advisory'):
            result = guard.assess_resources(memory, pressure, 1, False, role=role)
            self.assertFalse(result['admit_new_worker'])
            self.assertEqual(result['minimum_available_bytes'], 8 * guard.GIB)
        with patch('historical_training.value_priority_admission.verify_policy'), \
             patch('historical_training.value_priority_admission.decision',
                   return_value=(True, 'SYNTHETIC_VALUE_PRIORITY', {})) as decision:
            gate = guard.Gate({'state_root': str(self.root / 'guard'), 'value_priority': {}}, 'owner-advisory')
            report = gate._priority_negative_preflight()
        self.assertFalse(report['admit_new_worker'])
        self.assertEqual(decision.call_args.args[1], 'evaluator')
        self.assertEqual(gate.lease.path, self.root / 'guard/heavy-worker.lock')

    def test_worker_task_has_separate_write_scope_and_no_trial_slot(self):
        input_dir = consumer._private_directory(self.folder / 'input')
        output_dir = consumer._private_directory(self.folder / 'worker')
        payload_path = input_dir / 'selected-model.json'; payload_path.write_bytes(self.raw)
        config_path = self.root / 'config.json'; write_once(config_path, self.config)
        task = {'schema': consumer.TASK_SCHEMA, 'request': self.request,
            'request_digest': digest(self.request), 'payload_path': str(payload_path),
            'payload_sha256': sha_file(payload_path), 'config_sha256': sha_file(config_path),
            'output_directory': str(output_dir), 'controller_pid': os.getppid(),
            'effect_authority': False, 'broker_orders_sent': 0}
        path = input_dir / 'task.json'; write_once(path, task)
        with patch('ctypes.CDLL', return_value=SimpleNamespace(prctl=lambda *args: 0)), \
             patch('resource.setrlimit'):
            checked, writable, selected, raw = resident_launch.owner_task(path, sha_file(path), config_path, self.config, [3, 4])
            self.assertEqual(writable, output_dir); self.assertEqual(selected, payload_path)
            self.assertEqual(raw, self.raw)
            self.assertNotIn('slot', checked)
            task['output_directory'] = str(self.folder); path.write_bytes(canonical(task))
            with self.assertRaisesRegex(ContractError, 'OWNER_PREDICTION_TASK_BINDING'):
                resident_launch.owner_task(path, sha_file(path), config_path, self.config, [3, 4])

    def test_unreaped_hold_preserves_existing_marker_and_uses_actual_cleanup_reason(self):
        folder = consumer._private_directory(self.root / 'failure')
        manual = self.root / 'manual-hold.json'
        gate = SimpleNamespace(c={'manual_hold_path': str(manual)})
        subject = {'request_digest': 'a' * 64}
        cleanup = {'tree_reaped': False, 'remaining_pids': [123456789]}
        consumer.hold_unreaped(gate, folder, subject, cleanup)
        first = manual.read_bytes(); value = json.loads(first)
        self.assertEqual(value['reason'], 'ADVISORY_WORKER_TREE_UNREAPED')
        self.assertNotIn('signal', value)
        consumer.hold_unreaped(gate, folder, subject, cleanup)
        self.assertEqual(manual.read_bytes(), first)
        other = self.root / 'other-owner-hold.json'
        other.write_bytes(b'other existing operator hold\n'); other.chmod(0o400)
        before = other.stat(); before_sha = sha_file(other)
        existing_gate = SimpleNamespace(c={'manual_hold_path': str(other)})
        consumer.hold_unreaped(existing_gate, folder, subject, cleanup)
        self.assertEqual(other.read_bytes(), b'other existing operator hold\n')
        after = other.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_mode, sha_file(other)),
                         (before.st_dev, before.st_ino, before.st_mode, before_sha))
        self.assertEqual(manual.read_bytes(), first)

    def test_unreaped_or_uninspectable_tree_never_releases_the_heavy_lease(self):
        class UnitTeardownFixture(BaseException):
            pass
        config_path = self.root / 'config.json'; write_once(config_path, {})
        config = {**self.config, 'python': sys.executable, 'runtime_directory': str(self.root / 'runtime')}
        request = {**self.request, 'expires_at_ms': 10**16}
        folder = consumer._private_directory(self.root / 'attempt')
        gate = Mock(); gate.acquire.return_value = True
        gate.c = {'original_config_sha256': sha_file(config_path)}
        gate.lease = SimpleNamespace(fd=3)
        process = Mock(); process.poll.return_value = 0; process.returncode = 0
        for outcome in ({'tree_reaped': False}, OSError('synthetic proc read failure')):
            # Each attempt has independent immutable task files.
            current = consumer._private_directory(folder / str(len(list(folder.iterdir()))))
            gate.release.reset_mock()
            with patch.object(consumer, 'check_queue', return_value={}), \
                 patch.object(consumer.subprocess, 'Popen', return_value=process), \
                 patch.object(consumer, 'stop_and_reap', side_effect=outcome if isinstance(outcome, Exception) else None,
                              return_value=outcome), \
                 patch.object(consumer, 'hold_unreaped') as hold, \
                 patch.object(consumer, 'retain_until_unit_teardown', side_effect=UnitTeardownFixture), \
                 self.assertRaises(UnitTeardownFixture):
                consumer.admitted_prediction(config_path, config, current, self.raw, request,
                                             gate, SimpleNamespace(fd=4), lambda: False)
            gate.release.assert_not_called(); self.assertTrue(hold.called)


class AdvisoryTreeTests(unittest.TestCase):
    def isolated(self, body):
        # The finite OS fixture is a subprocess, so subreaper/signal settings
        # cannot change the unittest runner or any real resident service.
        code = "from laya_cell import resident_owner_consume as c\n" + textwrap.dedent(body)
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        return json.loads(result.stdout)

    def test_setsid_orphan_is_reaped_after_leader_exit(self):
        result = self.isolated('''
            import json, os, subprocess, sys, time
            c.enable_subreaper()
            child = "import os,signal,time; p=os.fork(); os._exit(0) if p else None; os.setsid(); signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"
            p = subprocess.Popen([sys.executable,'-c',child],start_new_session=True,stdout=subprocess.PIPE,text=True)
            assert p.stdout.readline().strip() == 'ready'
            p.wait(timeout=3)
            r = c.stop_and_reap(p,grace=.05,kill_grace=2)
            p.stdout.close()
            r['remaining'] = c._children()
            print(json.dumps(r))
        ''')
        self.assertTrue(result['tree_reaped']); self.assertTrue(result['unexpected_descendants'])
        self.assertEqual(result['remaining'], [])
        self.assertIn(-signal.SIGKILL, result['descendant_exit_codes'].values())

    def test_native_signal_status_is_retained_and_tree_is_empty(self):
        result = self.isolated('''
            import json, subprocess, sys
            c.enable_subreaper()
            child = "import os,resource,signal; resource.setrlimit(resource.RLIMIT_CORE,(0,0)); os.kill(os.getpid(),signal.SIGSEGV)"
            p = subprocess.Popen([sys.executable,'-c',child],start_new_session=True)
            p.wait(timeout=3)
            r = c.stop_and_reap(p,grace=.05,kill_grace=2)
            print(json.dumps(r))
        ''')
        self.assertTrue(result['tree_reaped']); self.assertEqual(result['leader_exit_code'], -signal.SIGSEGV)

    def test_timed_out_term_ignoring_leader_is_killed_and_reaped(self):
        result = self.isolated('''
            import json, subprocess, sys, time
            c.enable_subreaper()
            child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"
            p = subprocess.Popen([sys.executable,'-c',child],start_new_session=True,stdout=subprocess.PIPE,text=True)
            assert p.stdout.readline().strip() == 'ready'
            r = c.stop_and_reap(p,grace=.05,kill_grace=2)
            p.stdout.close()
            print(json.dumps(r))
        ''')
        self.assertTrue(result['tree_reaped']); self.assertEqual(result['leader_exit_code'], -signal.SIGKILL)

    def test_dispatch_reaps_before_releasing_even_when_result_json_or_native_worker_fails(self):
        result = self.isolated('''
            import json, os, pathlib, subprocess, sys, tempfile, time
            from types import SimpleNamespace
            from unittest.mock import patch
            from historical_training.resident_resource_guard import Lease
            c.enable_subreaper()
            actual_popen = subprocess.Popen
            reports = []
            with tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td)
                config_path = root/'config.json'; config_path.write_text('{}')
                for mode in ('malformed-result','native-signal'):
                    folder = c._private_directory(root/mode)
                    heavy = Lease(folder/'heavy.lock'); request_lock = Lease(folder/'request.lock'); assert request_lock.acquire()
                    codes = []; released = []
                    def release():
                        assert c._children() == []
                        released.append(True); heavy.release()
                    gate = SimpleNamespace(lease=heavy,c={'original_config_sha256':c.sha_file(config_path)},
                        acquire=lambda subject:heavy.acquire(),release=release,
                        record_native_exit=lambda code,subject:codes.append(code))
                    config = {'python':sys.executable,'runtime_directory':str(root/'runtime')}
                    request = {'request_id':mode,'expires_at_ms':int(time.time()*1000)+10000}
                    script = "import json,sys,pathlib,os,resource,signal; t=json.load(open(sys.argv[1])); resource.setrlimit(resource.RLIMIT_CORE,(0,0)); "
                    script += ("os.kill(os.getpid(),signal.SIGSEGV)" if mode=='native-signal' else
                               "pathlib.Path(t['output_directory'],'result.json').write_text('{invalid json')")
                    def launch(args,**kwargs):
                        task = args[args.index('--owner-task')+1]
                        return actual_popen([sys.executable,'-c',script,task],**kwargs)
                    error = None
                    with patch.object(c,'check_queue',return_value={}), patch.object(c.subprocess,'Popen',side_effect=launch):
                        try:
                            c.admitted_prediction(config_path,config,folder,b'fixture payload',request,gate,request_lock,lambda:False)
                        except (ValueError,KeyError) as exc:
                            error = type(exc).__name__
                    assert released == [True] and heavy.fd is None and c._children() == []
                    contender = Lease(heavy.path); assert contender.acquire(); contender.release(); request_lock.release()
                    reports.append({'mode':mode,'error':error,'codes':codes,'tree_reaped':True,'lease_reacquired':True})
            print(json.dumps(reports))
        ''')
        self.assertEqual(result[0]['error'], 'JSONDecodeError')
        self.assertEqual(result[1]['codes'], [-signal.SIGSEGV])
        self.assertTrue(all(row['tree_reaped'] and row['lease_reacquired'] for row in result))


if __name__ == '__main__':
    unittest.main()
