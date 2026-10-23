"""Separate causal ACT budget and unchanged original PRICE accounting."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import torch
import test_laya_resident_action_v2_closed_source as original
from laya_cell import resident_closed_action_training as closed
from laya_cell import resident_price_baselines as baselines
from laya_cell import resident_price_calibration as calibration
from laya_cell import resident_row_accounting as accounting
from laya_cell import resident_training_v4 as v4
from laya_cell import resident_training_v5 as v5
from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.resident import Supervisor
from laya_cell import resident_plan_admission as admission
from laya_cell.resident_store import database, admit_job, complete_job
from laya_cell.tests.test_resident_price_calibration import population
from laya_cell.tests.test_resident_training_v4 import fixture


class ClosedActionEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.original = original.ClosedOriginalParentSourceTests()
        self.original.setUp()
        self.addCleanup(self.original.doCleanups)
        self.source = closed.freeze_source(self.original.root, original.PARENT, original.CUTOFF)
        self.path = self.original.root / 'closed-action-source.json'
        self.sha = closed.write_source(self.path, self.source)
        self.profile = closed.prepare_profile(self.source, torch.zeros(16, 2))
        # These fixtures isolate policy/row accounting, not saved model math.
        # Numeric replay is exercised with real safetensors below and by the
        # separate complete CPU run_job integration fixture.
        replay = patch.object(v5, 'verify_action_update',
                              side_effect=lambda job, candidate: candidate['action_adapter_validation'])
        replay.start()
        self.addCleanup(replay.stop)

    def steps(self):
        return [{'epoch': epoch, 'batch_in_epoch': index+1,
                 'closed_action_row_ids': ids, 'applied': True,
                 'action_loss': .05, 'gradient_digest': 'f'*64}
                for epoch in range(1, 5)
                for index, ids in enumerate(self.source['pair_row_ids'])]

    def phase(self, steps=None, **changes):
        flags = dict(price_parameters_unchanged=True, encoder_parameters_unchanged=True)
        flags.update(changes)
        return v5.action_receipt(self.source, self.steps() if steps is None else steps,
                                 self.profile, **flags)

    def test_action_counts_unique_rows_once_and_budget_is_separate_from_price(self):
        receipt = self.phase()
        self.assertEqual(receipt['unique_rows'], 8)
        self.assertEqual(receipt['attempted_steps'], 16)
        self.assertEqual(receipt['gradient_presentations'], 32)
        self.assertFalse(receipt['repeated_presentations_are_new_independent_rows'])
        self.assertEqual(v5.EPISODE, v4.EPISODE)
        self.assertEqual(v5.EPISODE['max_optimizer_steps'], 64)
        self.assertEqual(v5.ACTION_PHASE['max_optimizer_steps'], 80)
        self.assertEqual(v5.OBJECTIVE['max_rows'], v4.OBJECTIVE['max_rows'])

    def test_rollbacks_and_reordered_or_short_action_phase_cannot_inflate_receipt(self):
        steps = self.steps()
        for step in steps[:4]:
            step['applied'] = False
        receipt = self.phase(steps)
        self.assertEqual((receipt['applied_steps'], receipt['rolled_back_steps']), (12, 4))
        self.assertEqual(receipt['unique_rows'], 8)
        with self.assertRaisesRegex(ContractError, 'PHASE_CONTEXT'):
            self.phase(steps[:-1])
        steps[4]['closed_action_row_ids'] = steps[5]['closed_action_row_ids']
        with self.assertRaisesRegex(ContractError, 'PHASE_STEP_CHANGED'):
            self.phase(steps)
        for flag in ('price_parameters_unchanged', 'encoder_parameters_unchanged'):
            with self.subTest(flag=flag), self.assertRaisesRegex(ContractError, 'PHASE_CONTEXT'):
                self.phase(**{flag: False})

    def test_loss_uses_exactly_one_real_label_of_each_class(self):
        logits = torch.tensor([[.2, -.1], [-.2, .1]], requires_grad=True)
        result = closed.auxiliary_action_loss(logits, torch.tensor([True, False]), self.profile)
        result.backward()
        self.assertTrue(torch.isfinite(logits.grad).all())
        self.assertGreater(float(logits.grad.abs().sum()), 0)
        self.assertEqual(self.profile['closed_action_source_id'], self.source['source_id'])
        self.assertEqual(self.profile['labels']['ACT_correct'], 4)
        self.assertFalse(self.profile['action_distribution_scope']['natural_population_correct_probability_calibrated'])
        for labels in (torch.tensor([True, True]), torch.tensor([False, False]), torch.tensor([1, 0])):
            with self.assertRaisesRegex(ContractError, 'PAIRED_LOSS_INPUT'):
                closed.auxiliary_action_loss(logits, labels, self.profile)

    def test_self_hashed_profile_cannot_claim_different_genuine_support(self):
        for key, value in (('labels', {'ACT_correct': 8, 'ESCALATE_wrong': 8}),
                           ('training_row_ids_digest', 'f'*64),
                           ('train_cutoff_ms', original.CUTOFF+1)):
            changed = deepcopy(self.profile)
            changed[key] = value
            changed['profile_id'] = digest({k: v for k, v in changed.items() if k != 'profile_id'})
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, 'PROFILE_SOURCE_CHANGED'):
                v5.action_receipt(self.source, self.steps(), changed,
                                 price_parameters_unchanged=True, encoder_parameters_unchanged=True)

    def candidate(self):
        job, candidate = fixture()
        job.update(objective=deepcopy(v5.OBJECTIVE), evaluation_directory=str(self.original.root),
                   closed_action_source=str(self.path), closed_action_source_sha256=self.sha,
                   closed_action_source_id=self.source['source_id'], closed_action_parent_id=original.PARENT,
                   closed_action_training_cutoff_ms=original.CUTOFF)
        job['window']['training_end_ms'] = original.CUTOFF
        # Use the PRICE fixture's exact row identity for its existing 64-step
        # episode; the calibration fit itself has separate integration tests.
        rows = [{'sample_id': str(i), 'horizon_sec': 300, 'outcome_receipt_digest': str(i)}
                for i in range(32)]
        price_rows = population()
        for source, price in zip(rows, price_rows):
            price.update(source)
        refs = baselines.freeze(price_rows, original.CUTOFF)
        raw = [[.6, .1, .05, .05, .07, .08, .05] for _ in price_rows]
        candidate.update(objective=deepcopy(v5.OBJECTIVE), action_training_profile=self.profile,
                         price_baselines=refs, closed_action_source_id=self.source['source_id'],
                         closed_action_source_sha256=self.sha, action_phase=self.phase(),
                         action_update_steps=16, adapter_sha256='b'*64,
                         price_output_profile=calibration.OUTPUT)
        candidate['action_adapter_validation'] = {'scope': 'ISOLATED_POLICY_FIXTURE_NUMERIC_REPLAY_TESTED_SEPARATELY'}
        candidate['price_calibration'] = calibration.fit(price_rows, raw,
            cutoff_ms=original.CUTOFF, parent_id=original.PARENT,
            adapter_sha256='b'*64, price_baselines=refs)
        frozen = v5.freeze(job, accounting.batches(rows), {str(i): i%2 for i in range(32)},
                           self.profile, refs)
        candidate['training_episode'] = frozen
        candidate['learning_curve']['episode_id'] = frozen['episode_id']
        return job, candidate

    def test_v5_verifier_reuses_complete_price_episode_and_isolates_action_counts(self):
        job, candidate = self.candidate()
        with patch.object(calibration, 'verify_candidate_fit') as fit_replay:
            v5.verify_candidate(job, candidate)
            fit_replay.assert_called_once_with(job, candidate)
        self.assertEqual(len(candidate['steps']), 64)
        self.assertEqual(candidate['row_accounting']['gradient_attempted_count'], 32)
        self.assertEqual(candidate['row_accounting']['gradient_attempted_presentations'], 128)
        self.assertEqual(candidate['action_phase']['unique_rows'], 8)
        changed = deepcopy(candidate)
        changed['row_accounting']['gradient_attempted_count'] += 8
        with self.assertRaisesRegex(ContractError, 'EPISODE_RECEIPT_MISMATCH'):
            v5.verify_candidate(job, changed)
        changed = deepcopy(candidate)
        changed['action_update_steps'] += 1
        with self.assertRaisesRegex(ContractError, 'ACTION_RECEIPT_CHANGED'):
            v5.verify_candidate(job, changed)

    def test_source_parent_cutoff_or_public_policy_changes_refuse_admission(self):
        job, _ = self.candidate()
        v5.check_job(job)
        for key, value in (('closed_action_source_sha256', 'f'*64),
                           ('closed_action_parent_id', 'f'*64),
                           ('closed_action_training_cutoff_ms', original.CUTOFF+1)):
            changed = deepcopy(job)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                v5.check_job(changed)
        job['window']['training_end_ms'] = original.CUTOFF-1
        with self.assertRaisesRegex(ContractError, 'FUTURE_CUTOFF'):
            v5.check_job(job)
        objective = deepcopy(v5.OBJECTIVE)
        objective['closed_action_phase']['max_optimizer_steps'] += 1
        with self.assertRaisesRegex(ContractError, 'OBJECTIVE_CHANGED'):
            v5.check_objective(objective)

    def test_new_runtime_configuration_preserves_history_and_all_pins(self):
        from laya_cell.tests.test_resident_price_baselines import ExplicitConfigurationTests
        config, runtime = ExplicitConfigurationTests().fixture(self.original.root/'release')
        for name in v5.RUNTIME_PINS:
            path = runtime/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic pinned module\n')
        parent = self.original.root/'parent'
        parent.mkdir()
        original.save(parent/'candidate.json', {'candidate_id': original.PARENT})
        config.update(initial_parent_directory=str(parent), evaluation_directory=str(self.original.root))
        before = deepcopy(config)
        result = v5.configuration_for_release(config, runtime,
            closed_action_source=self.path, closed_action_source_sha256=self.sha)
        self.assertEqual(config, before)
        self.assertEqual(result['closed_action_source_id'], self.source['source_id'])
        self.assertEqual(result['objective'], v5.OBJECTIVE)
        self.assertEqual(set(v5.RUNTIME_PINS)-set(result['runtime_sha256']), set())
        for key in ('control_directory', 'evaluation_directory', 'candidate_directory',
                    'initial_parent_directory', 'promotion_protocol', 'promotion_legacy_trial_floor',
                    'future_plan_admission_profile', 'profile'):
            self.assertEqual(result[key], config[key])

    def test_future_plan_keeps_full_frozen_transform_and_rejects_rehashed_substitution(self):
        job, candidate = self.candidate()
        service = object.__new__(Supervisor)
        service.c = {'period_sha256': 'a'*64,
                     'runtime_sha256': {name: 'b'*64 for name in v5.RUNTIME_PINS}}
        job.update(output_directory=str(self.original.root/'artifact'), parent_directory='same-parent')
        with patch('laya_cell.resident.sha_file', return_value='c'*64):
            plan = service._evaluation_plan('job', candidate, job, 1, original.CUTOFF+1000)
        self.assertEqual(plan['price_calibration'], candidate['price_calibration'])
        self.assertEqual(plan['price_calibration_profile_id'], candidate['price_calibration']['profile_id'])
        self.assertEqual(plan['closed_action_source_id'], self.source['source_id'])
        self.assertEqual(plan['action_distribution_scope'], closed.ACTION_DISTRIBUTION)
        self.assertIn('learn/laya_cell/resident_price_calibration.py', plan['evaluation_runtime_sha256'])
        self.assertIn('learn/laya_cell/resident_evaluate.py', plan['evaluation_runtime_sha256'])
        plan['plan_id'] = digest(plan)
        identity = {key: plan[key] for key in ('job_id', 'candidate_id', 'candidate_manifest_sha256',
            'price_output_profile', 'price_calibration_profile_id', 'price_calibration', 'closed_action_source_id',
            'action_distribution_scope')}
        record = admission._sealed(identity, 'RESERVED', original.CUTOFF, plan=plan)
        admission._check_record(record, identity)
        changed = deepcopy(record)
        changed['plan']['price_calibration_profile_id'] = 'f'*64
        changed['plan']['plan_id'] = digest({k: v for k, v in changed['plan'].items() if k != 'plan_id'})
        changed['registration_id'] = digest({k: v for k, v in changed.items() if k != 'registration_id'})
        with self.assertRaisesRegex(ContractError, 'V5_RESERVED_PROFILE_CHANGED'):
            admission._check_record(changed, identity)

    def test_store_dispatch_admits_v5_without_consuming_rows_twice(self):
        job, candidate = self.candidate()
        db = database(self.original.root/'isolated-scheduler.sqlite')
        self.addCleanup(db.close)
        self.assertTrue(admit_job(db, 'job', job['data_fingerprint'], original.CUTOFF,
                                  job, job['training_row_ids']))
        with patch.object(calibration, 'verify_candidate_fit'):
            self.assertTrue(complete_job(db, 'job', candidate))
            self.assertFalse(complete_job(db, 'job', candidate))
        self.assertEqual(db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0], 32)
        self.assertFalse(admit_job(db, 'other', job['data_fingerprint'], original.CUTOFF+1,
                                   job, job['training_row_ids']))


class SavedActionAdapterChangeTests(unittest.TestCase):
    """Real tiny CPU safetensors; these fixtures are not GPU training proof."""
    KEYS = ('model.act_head.2.A', 'model.act_head.2.B')

    def setUp(self):
        import numpy as np
        self.np = np
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.parent_dir, self.final_dir = self.root/'parent', self.root/'final'
        self.parent_dir.mkdir()
        self.final_dir.mkdir()

    def fixture(self, *, parent_action=True, parent_tensors=None, final_tensors=None):
        from safetensors.numpy import save_file
        np = self.np
        a = np.ones((1, 64), dtype=np.float32)
        b = np.array([[.125], [.25]], dtype=np.float32)
        previous = ({self.KEYS[0]: a.copy(), self.KEYS[1]: b.copy()} if parent_action
                    else {'model.scorer.2.B': np.zeros((1, 1), dtype=np.float32)})
        if parent_tensors is not None:
            previous = parent_tensors
        final = {self.KEYS[0]: a.copy(), self.KEYS[1]: b+np.float32(.015625)}
        if final_tensors is not None:
            final = final_tensors
        save_file(previous, str(self.parent_dir/'adapter.safetensors'))
        parent = {'scope': 'CPU_SAFETENSORS_FIXTURE_NOT_GPU_PROOF',
                  'adapter_sha256': sha_file(self.parent_dir/'adapter.safetensors')}
        parent['candidate_id'] = digest(parent)
        (self.parent_dir/'candidate.json').write_text(json.dumps(parent))
        save_file(final, str(self.final_dir/'adapter.safetensors'))
        job = {'parent_directory': str(self.parent_dir),
               'parent_manifest_sha256': sha_file(self.parent_dir/'candidate.json'),
               'parent_id': parent['candidate_id'], 'output_directory': str(self.final_dir)}
        changes = {name: int(np.count_nonzero(value != previous[name]))
                   if name in previous and value.shape == previous[name].shape
                   else int(np.count_nonzero(value))
                   for name, value in final.items()}
        candidate = {'parent_id': parent['candidate_id'],
                     'adapter_sha256': sha_file(self.final_dir/'adapter.safetensors'),
                     'tensor_mask': sorted(final), 'action_head_trained': True,
                     'action_phase': {'applied_steps': 1}, 'action_update_steps': 1,
                     'changed_by_tensor': changes}
        return job, candidate

    def test_reads_pinned_real_factors_and_replays_effective_delta(self):
        job, candidate = self.fixture()
        result = v5.verify_action_update(job, candidate)
        self.assertTrue(result['parent_action_adapter_present'])
        self.assertEqual(result['actual_factor_changed_counts'], dict(zip(self.KEYS, (0, 2))))
        self.assertEqual(result['effective_changed_coordinates'], 128)
        self.assertGreater(result['effective_max_absolute_change'], 0)
        self.assertFalse(result['gpu_inference_replayed'])
        self.assertFalse(result['future_value_proven'])

    def test_replay_never_imports_torch_or_loads_a_base(self):
        job, candidate = self.fixture()
        inputs = self.root/'numeric-replay-input.json'
        inputs.write_text(json.dumps({'job': job, 'candidate': candidate}))
        code = '''import builtins, json, sys
original_import = builtins.__import__
def limited_import(name, *args, **kwargs):
    if name == 'torch' or name.startswith('torch.'):
        raise AssertionError('NUMERIC_REPLAY_MUST_NOT_IMPORT_TORCH')
    return original_import(name, *args, **kwargs)
builtins.__import__ = limited_import
from laya_cell.resident_training_v5 import verify_action_update
with open(sys.argv[1]) as stream:
    inputs = json.load(stream)
result = verify_action_update(inputs['job'], inputs['candidate'])
assert result['effective_changed_coordinates'] > 0
assert 'torch' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(inputs)],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scorer_only_parent_has_zero_effect_and_unknown_initial_A(self):
        job, candidate = self.fixture(parent_action=False)
        # The old random A is not stored in this scorer-only parent. No exact
        # count against that absent initial A can be asserted or replayed.
        candidate['changed_by_tensor'][self.KEYS[0]] = 0
        result = v5.verify_action_update(job, candidate)
        self.assertFalse(result['parent_action_adapter_present'])
        self.assertEqual(result['missing_parent_action_effect'], 'ZERO_B_INITIALIZATION')
        self.assertIsNone(result['actual_factor_changed_counts'][self.KEYS[0]])
        self.assertFalse(result['missing_parent_A_changed_count_claimed'])
        self.assertEqual(result['actual_factor_changed_counts'][self.KEYS[1]], 2)

    def test_zero_or_all_rolled_back_or_untrained_cannot_be_admitted(self):
        job, candidate = self.fixture()
        for changes in ({'action_phase': {'applied_steps': 0}, 'action_update_steps': 0},
                        {'action_head_trained': False}, {'action_update_steps': 2},
                        {'action_update_steps': True}):
            changed = dict(candidate, **changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ContractError, 'NO_APPLIED_NUMERIC_UPDATE'):
                v5.verify_action_update(job, changed)
        candidate['changed_by_tensor'] = dict.fromkeys(self.KEYS, 0)
        with self.assertRaisesRegex(ContractError, 'CHANGED_STATISTICS_REQUIRED'):
            v5.verify_action_update(job, candidate)

    def test_tampered_counts_and_invalid_count_types_fail(self):
        job, candidate = self.fixture()
        for counts, error in (({self.KEYS[0]: 1, self.KEYS[1]: 2}, 'STATISTICS_MISMATCH'),
                              ({self.KEYS[0]: 65, self.KEYS[1]: 2}, 'STATISTICS_REQUIRED'),
                              ({self.KEYS[0]: 0, self.KEYS[1]: True}, 'STATISTICS_REQUIRED')):
            with self.subTest(counts=counts), self.assertRaisesRegex(ContractError, error):
                v5.verify_action_update(job, dict(candidate, changed_by_tensor=counts))

    def test_same_effective_reparameterization_is_rejected(self):
        np = self.np
        # Both factor tensors change, while B@A stays exactly equal.
        job, candidate = self.fixture(final_tensors={
            self.KEYS[0]: np.full((1, 64), 2, dtype=np.float32),
            self.KEYS[1]: np.array([[.0625], [.125]], dtype=np.float32)})
        with self.assertRaisesRegex(ContractError, 'EFFECT_UNCHANGED'):
            v5.verify_action_update(job, candidate)

    def test_missing_parent_A_is_not_invented_to_validate_A_only_change(self):
        np = self.np
        job, candidate = self.fixture(parent_action=False, final_tensors={
            self.KEYS[0]: np.full((1, 64), 2, dtype=np.float32),
            self.KEYS[1]: np.zeros((2, 1), dtype=np.float32)})
        with self.assertRaisesRegex(ContractError, 'EFFECT_UNCHANGED'):
            v5.verify_action_update(job, candidate)

    def test_parent_and_final_pins_and_declared_mask_are_required(self):
        job, candidate = self.fixture()
        for target, key, value, error in ((job, 'parent_manifest_sha256', 'f'*64, 'PARENT_MANIFEST_CHANGED'),
              (candidate, 'adapter_sha256', 'f'*64, 'FINAL_ADAPTER_CHANGED'),
              (candidate, 'parent_id', 'f'*64, 'PARENT_ID_CHANGED'),
              (candidate, 'tensor_mask', [], 'ADAPTER_TENSOR_MASK')):
            saved = target[key]
            target[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, error):
                v5.verify_action_update(job, candidate)
            target[key] = saved
        (self.final_dir/'adapter.safetensors').unlink()
        with self.assertRaises(FileNotFoundError):
            v5.verify_action_update(job, candidate)

    def test_partial_parent_or_wrong_final_dtype_or_shape_or_nonfinite_fail(self):
        np = self.np
        cases = [({'parent_tensors': {self.KEYS[0]: np.ones((1, 64), dtype=np.float32)}}, 'TENSOR_MASK'),
                 ({'final_tensors': {self.KEYS[0]: np.ones((1, 64), dtype=np.float64),
                                     self.KEYS[1]: np.ones((2, 1), dtype=np.float32)}}, 'SHAPE_DTYPE'),
                 ({'final_tensors': {self.KEYS[0]: np.ones((2, 32), dtype=np.float32),
                                     self.KEYS[1]: np.ones((2, 1), dtype=np.float32)}}, 'SHAPE_DTYPE'),
                 ({'final_tensors': {self.KEYS[0]: np.full((1, 64), np.nan, dtype=np.float32),
                                     self.KEYS[1]: np.ones((2, 1), dtype=np.float32)}}, 'SHAPE_DTYPE')]
        for arguments, error in cases:
            with self.subTest(arguments=list(arguments)):
                job, candidate = self.fixture(**arguments)
                with self.assertRaisesRegex(ContractError, error):
                    v5.verify_action_update(job, candidate)


if __name__ == '__main__':
    unittest.main()
