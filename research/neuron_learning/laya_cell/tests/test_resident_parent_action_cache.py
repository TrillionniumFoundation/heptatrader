"""CPU-only cache equivalence, mutation fallback and scheduler publication."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

from laya_cell import resident_parent_action_cache as cache
from laya_cell import resident_closed_action_training as closed
from laya_cell.integrity import ContractError, digest


class ParentActionCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.control = self.root / 'control'; self.control.mkdir(mode=0o700)
        self.output = self.root / 'worker'; self.output.mkdir(mode=0o700)
        self.job = {cache.FIELD: cache.PROFILE,
            'objective': {'schema': 'heptatrader.laya-resident-objective.v8'},
            'hip_runtime': {'profile': 'PINNED_HIP_KPACK_V1', 'sha256': '1'*64},
            'runtime_sha256': {name: '2'*64 for name in cache.PINS},
            'control_directory': str(self.control), 'output_directory': str(self.output),
            'parent_id': '3'*64, 'parent_manifest_sha256': '4'*64,
            'closed_action_source_id': '5'*64, 'closed_action_source_sha256': '6'*64,
            'closed_action_training_cutoff_ms': 100000}
        self.parent = {'candidate_id': self.job['parent_id']}
        self.source = {'source_id': self.job['closed_action_source_id'], 'training_cutoff_ms': 100000}
        self.rows = [{'row_id': digest({'row': i}), 'anchor_ms': 1000+i*100,
                      'future_outcome_available_ms': 1010+i*100,
                      'parent_correct': i < 20} for i in range(40)]
        self.logits = (torch.arange(80, dtype=torch.float32).reshape(40, 2)-23)/13
        self.backend = {'torch': 'fixture-cpu', 'flags': {'tf32': False}, 'files': ['immutable-test']}
        self.calls = 0

    def compute(self):
        self.calls += 1
        return self.logits.clone()

    def run_cache(self, job=None, rows=None, probe=None, compute=None):
        return cache.calibration_logits(job or self.job, self.parent, self.source,
            rows or self.rows, compute or self.compute,
            backend_probe=probe or (lambda: copy.deepcopy(self.backend)))

    def publish(self):
        logits, value, receipt = self.run_cache()
        receipt = cache.write_worker_output(self.output, value, receipt)
        self.assertFalse((self.control/'parent-action-cache').exists())
        candidate = {'parent_id': self.job['parent_id'], cache.RECEIPT: receipt,
            'hip_runtime_proof': {'manifest_sha256': self.job['hip_runtime']['sha256']}}
        result = cache.publish_from_candidate(self.job, candidate)
        self.assertEqual(result['state'], 'PUBLISHED')
        attached = cache.attach_input(self.job)
        self.assertIn(cache.INPUT, attached)
        return logits, attached, candidate

    def test_miss_hit_identical_logits_calibration_loss_and_gradient(self):
        original, attached, candidate = self.publish()
        def forbidden():
            self.fail('Hit must not repeat fixed-parent model forwards')
        cached, value, receipt = self.run_cache(attached, compute=forbidden)
        self.assertTrue(torch.equal(cached, original))
        self.assertEqual(receipt['state'], 'HIT')
        self.assertEqual(receipt['parent_forward_rows_reused'], 40)
        self.assertEqual(receipt['parent_forward_rows'], 0)
        self.assertEqual(self.calls, 1)
        with patch.object(closed, 'calibration_rows', return_value=self.rows):
            expected = closed.prepare_profile(self.source, original)
            actual = closed.prepare_profile(self.source, cached)
        self.assertEqual(actual, expected)
        # Exercise the actual ACT objective and autograd with changing child
        # logits. The cache contains the parent calibration, not these inputs.
        left = torch.tensor([[.31, -.2], [-.11, .7]], requires_grad=True)
        right = left.detach().clone().requires_grad_(True)
        labels = torch.tensor([True, False])
        a = closed.auxiliary_action_loss(left, labels, expected)
        b = closed.auxiliary_action_loss(right, labels, actual)
        a.backward(); b.backward()
        self.assertTrue(torch.equal(a, b))
        self.assertTrue(torch.equal(left.grad, right.grad))
        self.assertEqual(cache.publish_from_candidate(self.job, candidate)['state'], 'ALREADY_PUBLISHED')

    def test_current_price_dataset_and_output_path_do_not_prevent_parent_reuse(self):
        _, attached, _ = self.publish()
        later = {**attached, 'dataset_sha256': 'a'*64,
                 'window': {'training_end_ms': 999999}, 'output_directory': str(self.root/'later')}
        _, _, receipt = self.run_cache(later)
        self.assertEqual(receipt['state'], 'HIT')
        self.assertEqual(self.calls, 1)

    def test_parent_source_runtime_or_hip_change_misses(self):
        self.publish()
        changes = [('parent_manifest_sha256', 'a'*64), ('closed_action_source_sha256', 'b'*64),
                   ('closed_action_source_id', 'c'*64), ('parent_id', 'd'*64),
                   ('closed_action_training_cutoff_ms', 100001)]
        for field, value in changes:
            with self.subTest(field=field):
                changed = {**self.job, field: value}
                self.assertNotIn(cache.INPUT, cache.attach_input(changed))
        changed = copy.deepcopy(self.job)
        changed['runtime_sha256'][cache.PINS[0]] = 'e'*64
        self.assertNotIn(cache.INPUT, cache.attach_input(changed))
        changed = copy.deepcopy(self.job)
        changed['hip_runtime']['sha256'] = 'f'*64
        self.assertNotIn(cache.INPUT, cache.attach_input(changed))

    def test_backend_change_recomputes_and_keeps_row_order(self):
        _, attached, _ = self.publish()
        self.backend['flags']['tf32'] = True
        logits, value, receipt = self.run_cache(attached)
        self.assertEqual(receipt['state'], 'MISS')
        self.assertEqual(receipt['reason'], 'BACKEND_CHANGED')
        self.assertEqual(value['row_ids'], [row['row_id'] for row in self.rows])
        self.assertTrue(torch.equal(logits, self.logits))
        self.assertEqual(self.calls, 2)

    def test_backend_mutation_during_compute_is_not_published(self):
        calls = []
        def unstable():
            calls.append(1)
            return {'generation': len(calls)}
        logits, value, receipt = self.run_cache(probe=unstable)
        self.assertIsNone(value)
        self.assertEqual(receipt['state'], 'MISS_NOT_STORED')
        self.assertEqual(receipt['reason'], 'BACKEND_CHANGED_DURING_COMPUTE')
        self.assertTrue(torch.equal(logits, self.logits))
        self.assertEqual(cache.write_worker_output(self.output, value, receipt), receipt)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_row_reordering_recomputes_instead_of_borrowing_logits(self):
        _, attached, _ = self.publish()
        rows = list(reversed(self.rows))
        _, value, receipt = self.run_cache(attached, rows=rows)
        self.assertEqual(receipt['reason'], 'CONTEXT_OR_ROW_ORDER_CHANGED')
        self.assertEqual(value['row_ids'], [row['row_id'] for row in rows])
        self.assertEqual(self.calls, 2)

    def test_pinned_input_mutation_falls_back_to_original_forward(self):
        _, attached, _ = self.publish()
        path = Path(attached[cache.INPUT]['path'])
        path.chmod(0o600); path.write_text('{bad json'); path.chmod(0o400)
        logits, _, receipt = self.run_cache(attached)
        self.assertEqual(receipt['reason'], 'INVALID_OR_MUTATED_INPUT')
        self.assertTrue(torch.equal(logits, self.logits))
        self.assertEqual(self.calls, 2)
        self.assertNotIn(cache.INPUT, cache.attach_input(self.job))

    def test_symlink_cache_is_not_trusted_or_followed(self):
        _, attached, _ = self.publish()
        path = Path(attached[cache.INPUT]['path'])
        original = path.read_bytes()
        path.unlink()
        foreign = self.root/'foreign.json'; foreign.write_bytes(original); foreign.chmod(0o400)
        path.symlink_to(foreign)
        self.assertNotIn(cache.INPUT, cache.attach_input(self.job))
        _, _, receipt = self.run_cache(attached)
        self.assertEqual(receipt['reason'], 'INVALID_OR_MUTATED_INPUT')

    def test_deeply_nested_cache_falls_back_before_or_after_job_pin(self):
        _, attached, _ = self.publish()
        path = Path(attached[cache.INPUT]['path'])
        raw = b'[' * 2000 + b'0' + b']' * 2000
        path.chmod(0o600); path.write_bytes(raw); path.chmod(0o400)
        self.assertNotIn(cache.INPUT, cache.attach_input(self.job))
        # Also exercise parsing, rather than an earlier hash mismatch, at the
        # sandbox/worker boundary with an exactly pinned malformed payload.
        attached[cache.INPUT]['sha256'] = hashlib.sha256(raw).hexdigest()
        self.assertIsNone(cache.readonly_input_path(attached))
        logits, _, receipt = self.run_cache(attached)
        self.assertEqual(receipt['reason'], 'INVALID_OR_MUTATED_INPUT')
        self.assertTrue(torch.equal(logits, self.logits))
        self.assertEqual(self.calls, 2)

    def test_input_allowlist_is_one_exact_file_not_cache_directory(self):
        _, attached, _ = self.publish()
        path = cache.input_path(attached)
        self.assertEqual(path.name, attached[cache.INPUT]['key']+'.json')
        changed = copy.deepcopy(attached)
        changed[cache.INPUT]['path'] = str(self.control)
        with self.assertRaisesRegex(ContractError, 'INPUT_BINDING'):
            cache.input_path(changed)

    def test_cache_lost_before_sandbox_setup_does_not_expand_read_access(self):
        _, attached, _ = self.publish()
        path = cache.readonly_input_path(attached)
        self.assertEqual(path, Path(attached[cache.INPUT]['path']))
        path.unlink()
        self.assertIsNone(cache.readonly_input_path(attached))
        logits, _, receipt = self.run_cache(attached)
        self.assertTrue(torch.equal(logits, self.logits))
        self.assertEqual(receipt['reason'], 'INVALID_OR_MUTATED_INPUT')

    def test_candidate_wrong_runtime_cannot_publish(self):
        _, value, receipt = self.run_cache()
        receipt = cache.write_worker_output(self.output, value, receipt)
        candidate = {'parent_id': self.job['parent_id'], cache.RECEIPT: receipt,
                     'hip_runtime_proof': {'manifest_sha256': 'b'*64}}
        self.assertEqual(cache.publish_from_candidate(self.job, candidate)['state'], 'DERIVED_CACHE_NOT_PUBLISHED')
        self.assertFalse((self.control/'parent-action-cache').exists())

    def test_corrupt_worker_output_cannot_publish(self):
        _, value, receipt = self.run_cache()
        receipt = cache.write_worker_output(self.output, value, receipt)
        path = self.output/cache.FILE
        path.chmod(0o600); path.write_text('{}'); path.chmod(0o400)
        candidate = {'parent_id': self.job['parent_id'], cache.RECEIPT: receipt,
                     'hip_runtime_proof': {'manifest_sha256': self.job['hip_runtime']['sha256']}}
        self.assertEqual(cache.publish_from_candidate(self.job, candidate)['state'], 'DERIVED_CACHE_NOT_PUBLISHED')
        self.assertFalse((self.control/'parent-action-cache').exists())

    def test_disabled_profile_preserves_original_path(self):
        job = dict(self.job); job.pop(cache.FIELD)
        logits, value, receipt = self.run_cache(job)
        self.assertTrue(torch.equal(logits, self.logits))
        self.assertIsNone(value); self.assertIsNone(receipt)
        self.assertEqual(self.calls, 1)
        self.assertEqual(cache.attach_input(job), job)

    def test_unknown_profile_and_unpinned_hip_fail_at_config_boundary(self):
        for changed in ({**self.job, cache.FIELD: 'unknown'}, {**self.job, 'hip_runtime': {}},
                        {**self.job, 'runtime_sha256': {}}):
            with self.subTest(changed=changed):
                with self.assertRaises(ContractError):
                    cache.check_config(changed)


if __name__ == '__main__':
    unittest.main()
