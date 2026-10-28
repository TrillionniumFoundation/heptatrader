"""CPU failure evidence for source reads; no model import or worker dispatch."""
import ast
import copy
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest

from laya_cell import integrity, resident_store


SOURCE = Path(__file__).resolve().parents[1] / 'resident_predict.py'
EVALUATOR = SOURCE.parent / 'resident_evaluate.py'


def functions(path, names, namespace):
    """Compile complete production functions, avoiding module-level Torch."""
    body = [node for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in body} != set(names):
        raise AssertionError('MISSING_PRODUCTION_FUNCTION')
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), namespace)


class SourceReadPhaseTests(unittest.TestCase):
    def setUp(self):
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        self.root = Path(box.name)
        self.stage = self.root / 'phase-000.json'
        self.timing = {'phase': 'WAIT_FOR_FIXED_SLOT_INPUT', 'slot_start_ms': 123,
                       'fixed_input_deadline_ms': 456, 'model_ready_at_ms': 120}
        self.config, self.plan = {'config': 'unchanged'}, {'plan': 'unchanged'}
        self.now = 123
        self.calls = []
        self.namespace = {'time': time, 'atomic': resident_store.atomic,
                          'read_for_future_plan': self.read}
        functions(SOURCE, {'_read_fixed_slot_source'}, self.namespace)
        self.observe = self.namespace['_read_fixed_slot_source']
        self.response = (['quotes'], ['prefixes'], 'same-source-profile')

    def read(self, config, plan, now):
        self.calls.append((config, plan, now))
        active = integrity.read_json(self.stage)
        self.assertEqual(active['phase'], 'READ_FIXED_SLOT_SOURCE')
        self.assertIs(active['source_read_active'], True)
        self.assertNotIn('source_read_finished_at_ms', active)
        self.assertNotIn('source_read_error_class', active)
        return self.response

    def call(self):
        return self.observe(self.config, self.plan, self.now, self.stage, self.timing)

    def test_active_boundary_is_durable_before_the_read_and_result_is_unchanged(self):
        original = copy.deepcopy((self.config, self.plan))
        self.assertIs(self.call(), self.response)
        self.assertEqual(self.calls, [(self.config, self.plan, self.now)])
        self.assertEqual((self.config, self.plan), original)
        after = integrity.read_json(self.stage)
        self.assertEqual(after['phase'], 'WAIT_FOR_FIXED_SLOT_INPUT')
        self.assertIs(after['source_read_active'], False)
        self.assertGreaterEqual(after['source_read_finished_at_ms'], after['source_read_started_at_ms'])
        self.assertGreaterEqual(after['last_quote_read_ms'], 0)
        self.assertEqual(after['fixed_input_deadline_ms'], 456)

    def test_original_source_exception_and_elapsed_time_are_preserved(self):
        original = TimeoutError('original source read timeout')
        def read(*args):
            self.read(*args)
            raise original
        self.namespace['read_for_future_plan'] = read
        with self.assertRaises(TimeoutError) as raised:
            self.call()
        self.assertIs(raised.exception, original)
        after = integrity.read_json(self.stage)
        self.assertEqual(after['source_read_error_class'], 'TimeoutError')
        self.assertIs(after['source_read_active'], False)
        self.assertIn('last_quote_read_ms', after)
        self.assertEqual(len(self.calls), 1)

    def test_phase_write_failure_does_not_mask_source_failure_or_success(self):
        original = OSError('original source failure')
        calls = []
        def failed(*args):
            calls.append(args)
            raise original
        self.namespace['atomic'] = lambda *_: (_ for _ in ()).throw(PermissionError('phase read-only'))
        self.namespace['read_for_future_plan'] = failed
        with self.assertRaises(OSError) as raised:
            self.call()
        self.assertIs(raised.exception, original)
        self.assertEqual(len(calls), 1)
        self.namespace['read_for_future_plan'] = lambda *args: self.response
        self.assertIs(self.call(), self.response)

    def test_next_read_clears_prior_failure_and_completion_without_changing_deadline(self):
        self.timing.update(source_read_error_class='OldError',
                           source_read_finished_at_ms=1, last_quote_read_ms=99)
        self.call()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.timing['fixed_input_deadline_ms'], 456)

    def test_original_reaper_records_active_read_on_worker_budget_termination(self):
        # Use the full original reaper with a synthetic process identity. The
        # nested read observes the durable state at exactly the kill boundary.
        evaluator_tree = ast.parse(EVALUATOR.read_text())
        evaluator_class = next(n for n in evaluator_tree.body
                               if isinstance(n, ast.ClassDef) and n.name == 'Evaluator')
        reap = next(n for n in evaluator_class.body
                    if isinstance(n, ast.FunctionDef) and n.name == 'reap')
        namespace = {'Path': Path, 'time': time, 'signal': signal, 'os': os,
                     'subprocess': subprocess, 'read_json': integrity.read_json,
                     'write_once': resident_store.write_once,
                     '_WORKER_DIAGNOSTIC_BYTES': 8192, '_WORKER_STARTUP_LINE_BYTES': 2048,
                     'worker_failure_diagnostic': lambda *_args, **_kwargs: 'NO_PROCESS_DIAGNOSTIC'}
        exec(compile(ast.Module(body=[reap], type_ignores=[]), str(EVALUATOR), 'exec'), namespace)
        plan_path = self.root / 'plan.json'
        log = self.root / 'prediction-000.log'
        stream = log.open('w')
        stream.write('fixture worker blocked in source read\n')
        stream.flush()
        child = SimpleNamespace(rc=None)
        child.poll = lambda: child.rc
        child.terminate = lambda: setattr(child, 'rc', -signal.SIGTERM)
        child.wait = lambda **_: child.rc
        child.kill = lambda: self.fail('unexpected second kill')
        evaluator = SimpleNamespace(tasks={(str(plan_path), 0): (child, stream, time.monotonic()-241)})
        original = RuntimeError('synthetic child gone; no retry')
        def blocked(*args):
            self.read(*args)
            namespace['reap'](evaluator)
            failure = integrity.read_json(self.root / 'failure-000.json')
            self.assertEqual(failure['reason'], 'WORKER_BUDGET_EXCEEDED')
            self.assertEqual(failure['exit_code'], -signal.SIGTERM)
            self.assertEqual(failure['phase']['phase'], 'READ_FIXED_SLOT_SOURCE')
            self.assertIs(failure['phase']['source_read_active'], True)
            self.assertNotIn('source_read_finished_at_ms', failure['phase'])
            self.assertEqual(evaluator.tasks, {})
            raise original
        self.namespace['read_for_future_plan'] = blocked
        with self.assertRaises(RuntimeError) as raised:
            self.call()
        self.assertIs(raised.exception, original)
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
