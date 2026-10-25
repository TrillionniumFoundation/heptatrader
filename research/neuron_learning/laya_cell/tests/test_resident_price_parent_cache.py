"""Behavioral coverage for one-job raw-parent PRICE feature reuse.

Tiny CPU fixtures are numerical references only. No deployed model or source is
used, and these tests do not claim future value, trading authority or GPU parity.
"""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import torch

from laya_cell import resident_train as worker
from laya_cell import resident_price_input as inputs
from laya_cell.integrity import ContractError, digest
from laya_cell.resident_model import ResidentDecisionCell
from laya_cell.tests import test_resident_training_v8 as integration
from laya_cell.tests.test_resident_training_v7 import _MaskedRawDecisionModel


class ParentPriceCacheTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(20261025)
        torch.set_num_threads(2)

    def tiny(self, profile=inputs.RAW):
        cell = ResidentDecisionCell(_MaskedRawDecisionModel(), 'c' * 64)
        rows = [{'sample_id': digest(['sample', i]), 'horizon_sec': 300,
                 'outcome_receipt_digest': digest(['outcome', i]),
                 'question': 'Synthetic price question at 300 seconds',
                 'state': {'anchor_ms': 600000 * (i + 1)}} for i in range(2)]
        calls = []
        handle = cell.model.encoder.register_forward_hook(
            lambda *_: calls.append(torch.is_grad_enabled()))
        self.addCleanup(handle.remove)
        cache = worker._frozen_price_logits(
            cell, lambda selected: integration.encode_raw(None, selected,
                price_input_profile=profile), torch.device('cpu'), profile)
        return cell, rows, cache, calls

    def test_parent_returns_original_action_and_zero_step_reuses_encoder(self):
        cell, rows, cache, calls = self.tiny()
        batch = integration.encode_raw(None, rows[:1], price_input_profile=inputs.RAW)
        with torch.no_grad():
            expected_price, expected_action = cell(**batch)
            price, action = cache(rows[:1], parent=True)
            zero_step = cache(rows[:1])
        self.assertTrue(torch.equal(price, expected_price))
        self.assertTrue(torch.equal(action, expected_action))
        self.assertTrue(torch.equal(zero_step, expected_price))
        self.assertEqual(len(calls), 2, 'the explicit oracle and one priming forward')
        self.assertEqual(len(cell.model.scorer._forward_pre_hooks), 0)

    def test_gradient_mode_and_batch_shape_keep_distinct_upstream_dispatch(self):
        cell, rows, cache, calls = self.tiny()
        with torch.no_grad():
            cache(rows[:1], parent=True)
            cache(rows[:1])
        cell.train(True)
        logits = cache(rows[:1])
        self.assertTrue(logits.requires_grad)
        logits.sum().backward()
        self.assertEqual(calls, [False, True])
        cache(rows)
        self.assertEqual(calls, [False, True, True])
        self.assertTrue(any(p.grad is not None for name, p in cell.named_parameters()
                            if '.scorer.' in name and p.requires_grad))
        self.assertTrue(all(p.grad is None for p in cell.model.encoder.parameters()))

    def test_scorer_update_recomputes_price_without_reusing_old_logits(self):
        cell, rows, cache, calls = self.tiny()
        with torch.no_grad():
            initial, _ = cache(rows[:1], parent=True)
            cell.model.scorer[3].B.add_(0.01)
            updated = cache(rows[:1])
            oracle, _ = cell(**integration.encode_raw(
                None, rows[:1], price_input_profile=inputs.RAW))
        self.assertFalse(torch.equal(initial, updated))
        self.assertTrue(torch.equal(updated, oracle))
        self.assertEqual(len(calls), 2, 'priming and the explicit updated oracle')

    def test_changed_upstream_or_input_cannot_reuse_parent_features(self):
        cell, rows, cache, _ = self.tiny()
        with torch.no_grad():
            cache(rows[:1], parent=True)
            changed = copy.deepcopy(rows[:1])
            changed[0]['state']['anchor_ms'] += 1
            with self.assertRaisesRegex(ContractError, 'PRICE_CACHE_INPUT_CHANGED'):
                cache(changed)
            changed = copy.deepcopy(rows[:1])
            changed[0]['question'] += ' changed'
            with self.assertRaisesRegex(ContractError, 'PRICE_CACHE_INPUT_CHANGED'):
                cache(changed)
            cell.model.encoder.weight.add_(0.001)
            with self.assertRaisesRegex(ContractError, 'PRICE_CACHE_UPSTREAM_CHANGED'):
                cache(rows[:1])

    def test_changed_upstream_training_mode_is_rejected(self):
        cell, rows, cache, _ = self.tiny()
        with torch.no_grad():
            cache(rows[:1], parent=True)
            cell.model.encoder.train(True)
            with self.assertRaisesRegex(ContractError, 'PRICE_CACHE_UPSTREAM_CHANGED'):
                cache(rows[:1])

    def test_upstream_dtype_storage_shape_and_stride_changes_are_rejected(self):
        for change in ('dtype', 'storage', 'shape', 'stride'):
            with self.subTest(change=change):
                cell, rows, cache, _ = self.tiny()
                with torch.no_grad():
                    cache(rows[:1], parent=True)
                    weight = cell.model.encoder.weight
                    if change == 'dtype':
                        cell.model.encoder.half()
                    elif change == 'storage':
                        weight.data = weight.detach().clone()
                    elif change == 'shape':
                        weight.data = weight.detach().reshape(-1)
                    else:
                        weight.data = weight.detach().t().contiguous().t()
                    with self.assertRaisesRegex(ContractError, 'PRICE_CACHE_UPSTREAM_CHANGED'):
                        cache(rows[:1])

    def test_parent_priming_requires_raw_no_grad_and_a_new_entry(self):
        _, rows, cache, _ = self.tiny()
        with self.assertRaisesRegex(ContractError, 'PARENT_PRICE_CACHE_REQUIRES_RAW_NO_GRAD'):
            cache(rows[:1], parent=True)
        with torch.no_grad():
            cache(rows[:1], parent=True)
            with self.assertRaisesRegex(ContractError, 'PARENT_PRICE_CACHE_ALREADY_PRIMED'):
                cache(rows[:1], parent=True)
        _, compact_rows, compact, _ = self.tiny(inputs.COMPACT)
        with torch.no_grad(), self.assertRaisesRegex(
                ContractError, 'PARENT_PRICE_CACHE_REQUIRES_RAW_NO_GRAD'):
            compact(compact_rows[:1], parent=True)

    def test_full_v8_episode_preserves_all_gradients_and_saves_32_forwards(self):
        run_job = worker.run_job
        cache_factory = worker._frozen_price_logits
        comparison = {}

        def run_observed(job, *, reference, **kwargs):
            calls = []
            hooks = []
            def make_cache(cell, encoded, device, profile):
                hooks.append(cell.model.encoder.register_forward_hook(
                    lambda *_: calls.append(torch.is_grad_enabled())))
                cached = cache_factory(cell, encoded, device, profile)
                if not reference:
                    return cached
                def original_dispatch(selected, *, parent=False):
                    if parent:
                        # The pre-optimization path: parent does a full forward,
                        # then the ordinary child cache performs its own first pass.
                        batch = {key: value.to(device)
                                 for key, value in encoded(selected).items()}
                        return cell(**batch)
                    return cached(selected)
                return original_dispatch
            try:
                with patch.object(worker, '_frozen_price_logits', side_effect=make_cache):
                    result = run_job(job, **kwargs)
            finally:
                for hook in hooks:
                    hook.remove()
            return result, len(calls)

        def compare_same_job(job, **kwargs):
            reference_job = {**job, 'output_directory':
                str(Path(job['output_directory']).with_name('reference-candidate'))}
            expected, old_calls = run_observed(reference_job, reference=True, **kwargs)
            actual, new_calls = run_observed(job, reference=False, **kwargs)
            for key in ('steps', 'action_phase', 'adapter_sha256',
                        'parent_price_forward_receipts',
                        'initial_child_price_forward_receipts',
                        'final_price_raw_forward_receipts',
                        'initial_child_price_measurement', 'measurement',
                        'price_calibration', 'price_calibration_training_measurement',
                        'parent_calibration_comparator'):
                self.assertEqual(actual[key], expected[key], key)
            self.assertEqual(len(actual['steps']), 64)
            self.assertEqual(old_calls - new_calls, 32)
            comparison.update(old_encoder_calls=old_calls,
                              new_encoder_calls=new_calls, saved_encoder_calls=32)
            return actual

        with patch.object(worker, 'run_job', side_effect=compare_same_job):
            integration.RawCenteredIntegrationTests.fixture(
                self, True, parent_comparator=True)
        self.assertEqual(comparison['saved_encoder_calls'], 32)


if __name__ == '__main__':
    unittest.main()
