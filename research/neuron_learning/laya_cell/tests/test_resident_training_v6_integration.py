"""Real tiny CPU training through v6 admission, calibration and future wiring.

Only model loading, tokenization, GPU transfer and native/source transport are
test doubles. All data and databases are temporary synthetic fixtures; no
installed runtime, production ledger, broker or GPU is accessed.
"""
import contextlib
import copy
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import torch
from torch import nn
from safetensors.torch import save_file

import test_laya_resident_action_v2_closed_source as source_fixture
from laya_cell import resident_closed_action_training as closed
from laya_cell import resident_plan_admission as admission
from laya_cell import resident_predict as predictor
from laya_cell import resident_price_baselines as baselines
from laya_cell import resident_price_calibration_v2 as calibration
from laya_cell import resident_train as worker
from laya_cell import resident_training_v6 as policy
from laya_cell.dataset import rows_for_training
from laya_cell.integrity import ContractError, digest, read_json, sha_file
from laya_cell.promotion import rule_for_trial
from laya_cell.promotion_power import PROTOCOL
from laya_cell.resident import Supervisor
from laya_cell.resident_evaluate import measure
from laya_cell.resident_model import ResidentDecisionCell
from laya_cell.resident_store import database, admit_job, complete_job, get_meta, put_meta
from laya_cell.tests.test_resident_plan_admission import fresh_proof
from laya_cell.tests.test_resident_price_calibration import real_dataset_population


class _RawDecisionModel(nn.Module):
    """Fixed option-only preference, independent of every target label."""
    def __init__(self):
        super().__init__()
        self.encoder = nn.Linear(8, 8, bias=False)
        self.scorer = nn.Sequential(nn.Identity(), nn.Linear(8, 8, bias=False),
                                    nn.Tanh(), nn.Linear(8, 1, bias=False))
        self.act_head = nn.Sequential(nn.Linear(8, 256), nn.Tanh(), nn.Linear(256, 2))
        with torch.no_grad():
            self.encoder.weight.copy_(torch.eye(8))
            self.scorer[1].weight.copy_(torch.eye(8))
            self.scorer[3].weight.zero_()
            self.scorer[3].weight[0, 6] = 8.

    def forward(self, x):
        encoded = self.encoder(x)
        return self.scorer(encoded).squeeze(-1), self.act_head(encoded.mean(dim=1))


def _encode(_tokenizer, rows):
    # Option identity and frozen quote time only. No labels enter model input.
    values = torch.zeros(len(rows), 7, 8)
    for i, row in enumerate(rows):
        values[i, :, :7] = torch.eye(7)
        values[i, :, 7] = (row['state']['anchor_ms'] % 100000) / 100000
    return {'x': values}


class _NativeFixture:
    def __init__(self, *args):
        pass

    def predictive_utility(self, *args):
        return {'receipt_digest': 'f' * 64}

    def propose(self, *args):
        return {'proposal_digest': 'e' * 64, 'selection_performed': False,
                'status': 'RequiresIndependentAcceptance'}

    def call(self, *args):
        return {'episodes': [{'activation': [0.] * 16}]}


class _CPUTransfer:
    def __init__(self, value):
        self.value = value

    def to(self, device):
        if device != 'cuda':
            raise AssertionError('unexpected predictor fixture transfer')
        return self.value


class ProspectiveV6IntegrationTests(unittest.TestCase):
    def fixture(self, target_class):
        original = source_fixture.ClosedOriginalParentSourceTests()
        original.setUp()
        self.addCleanup(original.doCleanups)
        root = original.root
        cutoff = source_fixture.CUTOFF
        dataset, old_window, _ = real_dataset_population()
        shift = cutoff - old_window['training_end_ms']
        target = .2 if target_class == 6 else 0.
        for sample in dataset['samples']:
            for key in ('anchor_ms', 'known_at_ms'):
                sample[key] += shift
            for quote in sample['input_snapshot']['quotes']:
                quote['t'] += shift
            sample['sample_id'] = digest(sample['input_snapshot'])
            sample['targets'][0] = target
            outcome = sample['outcome_receipts'][0]
            outcome['observed_at_ms'] += shift
            outcome['available_at_ms'] += shift
            outcome['target_scaled'] = target
        window = {'training_end_ms': cutoff, 'validation_start_ms': cutoff + 1,
                  'validation_end_ms': cutoff + 86400000}
        rows, _ = rows_for_training(dataset, window)
        rows = policy.timed_rows(rows, dataset)
        self.assertEqual({row['target_class'] for row in rows}, {target_class})
        dataset_path = root / 'dataset.json'
        dataset_path.write_text(json.dumps(dataset))
        base = root / 'base'
        base.mkdir()
        (base / 'base-manifest.json').write_text('{}')
        bootstrap = {'trained_until_ms': 0, 'model_id': 'cpu-bootstrap',
                     'weights': [0.] * 562}
        (base / 'model.json').write_text(json.dumps(bootstrap))
        (base / 'freeze-manifest.json').write_text(json.dumps({
            'inventory_sha256': {'model.json': sha_file(base / 'model.json')}}))
        parent_dir = root / 'parent'
        parent_dir.mkdir()
        torch.manual_seed(20261022)
        raw = _RawDecisionModel()
        initial = ResidentDecisionCell(copy.deepcopy(raw), 'c' * 64)
        save_file(initial.trainable_state(), str(parent_dir / 'adapter.safetensors'))
        parent = {'base_id': 'c' * 64, 'base_revision': 'tiny-cpu-fixture',
                  'adapter_sha256': sha_file(parent_dir / 'adapter.safetensors'),
                  'trained_until_ms': 0, 'window': {'training_end_ms': 0}}
        parent['candidate_id'] = digest(parent)
        (parent_dir / 'candidate.json').write_text(json.dumps(parent))
        for path in original.dir.glob('prediction-*.json'):
            prediction = read_json(path)
            prediction['parent_id'] = parent['candidate_id']
            path.write_text(json.dumps(prediction))
            outcome_path = path.with_name(path.name.replace('prediction-', 'outcome-'))
            outcome = read_json(outcome_path)
            outcome['prediction_file_sha256'] = sha_file(path)
            outcome_path.write_text(json.dumps(outcome))
        source = closed.freeze_source(root, parent['candidate_id'], cutoff)
        source_path = root / 'source.json'
        source_sha = closed.write_source(source_path, source)
        ids = policy.check_training_rows(rows, cutoff_ms=cutoff)
        job = {'objective': copy.deepcopy(policy.OBJECTIVE), 'profile': worker.PROFILE,
            'base_directory': str(base), 'baseline_directory': str(base),
            'output_directory': str(root / 'candidate'), 'parent_directory': str(parent_dir),
            'parent_id': parent['candidate_id'],
            'parent_manifest_sha256': sha_file(parent_dir / 'candidate.json'),
            'dataset': str(dataset_path), 'dataset_sha256': sha_file(dataset_path),
            'window': window, 'training_row_ids': ids,
            'training_row_accounting_schema': policy.ROW_ACCOUNTING_SCHEMA,
            'data_fingerprint': digest(ids), 'evaluation_directory': str(root),
            'closed_action_source': str(source_path), 'closed_action_source_sha256': source_sha,
            'closed_action_source_id': source['source_id'],
            'closed_action_parent_id': parent['candidate_id'],
            'closed_action_training_cutoff_ms': cutoff, 'generation': 0,
            'native_binary': '/synthetic/native', 'native_binary_sha256': 'f' * 64}
        service = object.__new__(Supervisor)
        service.root = root / 'control'
        service.root.mkdir()
        service.db = database(service.root / 'scheduler.sqlite')
        self.addCleanup(service.db.close)
        service.c = {**job, 'control_directory': str(service.root), 'period_sha256': 'a' * 64,
            'future_plan_admission_profile': admission.PROFILE,
            'future_plan_admission_effective_at_ms': cutoff - 10000,
            'promotion_protocol': PROTOCOL,
            'runtime_sha256': {n: 'b' * 64 for n in
                set(policy.RUNTIME_PINS) | set(admission.RUNTIME_PIN)}}
        # The genuine closed-source fixture already contains historical trial1.
        with service.db:
            put_meta(service.db, 'evaluation_trial', 1)
        self.assertTrue(admit_job(service.db, 'v6-job', digest(ids), cutoff, job, ids))
        self.assertEqual(service.db.execute('SELECT status FROM jobs').fetchone()[0], 'ADMITTED')
        with contextlib.ExitStack() as stack:
            replacements = {
                'verify_baseline': lambda *_: 'd' * 64, 'verify_source_prefixes': lambda *_: None,
                'require_pocket4_gpu': lambda: torch.device('cpu'),
                'load_verified_laya': lambda *_: (copy.deepcopy(raw), object(), 'c' * 64),
                'encode_rows': _encode, 'ResidentNative': _NativeFixture}
            for name, value in replacements.items():
                stack.enter_context(patch.object(worker, name, value))
            stack.enter_context(patch.object(torch.cuda, 'reset_peak_memory_stats'))
            stack.enter_context(patch.object(torch.cuda, 'max_memory_allocated', return_value=0))
            stack.enter_context(patch.object(torch.cuda, 'get_device_properties',
                return_value=types.SimpleNamespace(gcnArchName='CPU_TEST_FIXTURE')))
            stack.enter_context(patch.object(worker.time, 'time', return_value=(cutoff + 1000) / 1000))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            candidate = worker.run_job(job, old_learner_verified=True)
        self.assertEqual(len(candidate['steps']), 64)
        self.assertEqual(candidate['row_accounting']['gradient_attempted_count'], 32)
        self.assertEqual(candidate['row_accounting']['gradient_attempted_presentations'], 128)
        self.assertGreater(candidate['action_update_steps'], 0)
        measured = candidate['action_phase_diagnostics']['measured_execution']
        self.assertEqual(measured['encoder_forward_calls'], len(source['pair_row_ids']))
        self.assertEqual(measured['action_head_forward_calls'], len(candidate['action_phase']['steps']))
        self.assertGreater(measured['wall_seconds'], 0.)
        self.assertFalse(measured['gpu_synchronized'])
        self.assertEqual(candidate['price_output_profile'], calibration.OUTPUT)
        calibration.verify_candidate_fit(job, candidate)
        policy.verify_candidate(job, candidate)
        self.assertTrue(complete_job(service.db, 'v6-job', candidate))
        self.assertFalse(complete_job(service.db, 'v6-job', candidate))
        return service, job, candidate, rows, raw

    def test_actual_v6_prior_fallback_finishes_without_trial_or_source_capture(self):
        service, job, candidate, _, _ = self.fixture(3)
        self.assertEqual(candidate['price_calibration']['model_weight'], 0.)
        before = get_meta(service.db, 'evaluation_trial')
        with patch.object(admission, 'capture_source', side_effect=AssertionError('prior tie needs no source')), \
                patch.object(admission, 'inspect_queue', return_value={'pending_original_trials': 1}):
            result = admission.register(service, 'v6-job', candidate, job,
                                        clock=lambda: source_fixture.CUTOFF + 2000)
            self.assertEqual(result['state'], 'NO_FORWARD_TRIAL')
            self.assertEqual(admission.register(service, 'v6-job', candidate, job,
                clock=lambda: source_fixture.CUTOFF + 3000), result)
        self.assertEqual(result['reason'], 'EXACT_REQUIRED_PRIOR_NO_POSSIBLE_STRICT_GAIN')
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), before)
        folder = Path(service.c['evaluation_directory']) / 'v6-job'
        self.assertFalse((folder / 'plan.json').exists())
        self.assertEqual(read_json(folder / 'no-forward-trial.json'), result)
        waiting, report = admission.waiting_candidates(service,
            service.db.execute('SELECT * FROM jobs').fetchall())
        self.assertEqual(waiting, [])
        self.assertEqual(report['completed_candidates_without_forward_trial'], 1)

    def test_actual_v6_training_registers_and_predicts_with_frozen_calibration(self):
        torch.set_num_threads(2)
        service, job, candidate, rows, raw = self.fixture(6)
        self.assertGreater(candidate['price_calibration']['model_weight'], 0.)
        now = source_fixture.CUTOFF + 2000
        with patch.object(admission, 'inspect_queue', return_value={'pending_original_trials': 0}), \
                patch.object(admission, 'capture_source', return_value=fresh_proof(now)) as capture:
            result = admission.register(service, 'v6-job', candidate, job, clock=lambda: now)
        self.assertEqual(result['state'], 'PUBLISHED')
        capture.assert_called_once()
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), 2)
        path = Path(service.c['evaluation_directory']) / 'v6-job/plan.json'
        plan = read_json(path)
        self.assertEqual(plan['rule'], rule_for_trial(2, PROTOCOL))
        self.assertEqual(plan['price_calibration'], candidate['price_calibration'])
        self.assertEqual(plan['price_output_profile'], calibration.OUTPUT)
        self.assertFalse(set(policy.RUNTIME_PINS) - set(plan['evaluation_runtime_sha256']))
        anchor = plan['start_ms'] + 1
        state = copy.deepcopy(rows[-1]['state'])
        state['anchor_ms'] = anchor
        sample = {'anchor_ms': anchor, 'sample_id': digest(state), 'reference_mid': 1.12,
                  'native_episode': {}, 'raw_features': [0.] * 16}
        row = {'state': state}
        with contextlib.ExitStack() as stack:
            replacements = {
                'load_verified_laya': lambda *_: (copy.deepcopy(raw), object(), 'c' * 64),
                'encode_rows': lambda tok, selected: {k: _CPUTransfer(v)
                    for k, v in _encode(tok, selected).items()},
                'NativePorts': _NativeFixture,
                'read_for_future_plan': lambda *_: ([], [], 'ORIGINAL_FULL_HISTORY_V1'),
                'forecast_input': lambda *_: (sample, row)}
            for name, value in replacements.items():
                stack.enter_context(patch.object(predictor, name, value))
            stack.enter_context(patch.object(predictor.time, 'time', return_value=anchor / 1000))
            stack.enter_context(patch.object(torch.cuda, 'get_device_properties',
                return_value=types.SimpleNamespace(gcnArchName='CPU_TEST_FIXTURE')))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            prediction = predictor.predict(service.c, path, 0)
        self.assertEqual(prediction['price_output_profile'], calibration.OUTPUT)
        self.assertEqual(prediction['candidate_probs'], calibration.apply(
            candidate['price_calibration'], prediction['candidate_raw_probs']))
        calibration.verify_prediction(plan, prediction)
        quotes = [{'t': anchor + i * 10000, 'mid': 1.12, 'receipt': str(i)} for i in range(31)]
        outcome = measure(plan, 0, prediction, quotes, anchor + 300001)
        self.assertEqual(outcome['status'], 'OBSERVED')
        self.assertEqual(outcome['same_window_price_scores']['laya_candidate'],
            baselines.probability_metrics(prediction['candidate_probs'], 3))
        self.assertEqual(outcome['raw_child_price_scores'],
            baselines.probability_metrics(prediction['candidate_raw_probs'], 3))
        changed = copy.deepcopy(prediction)
        changed['price_calibration_profile_id'] = 'e' * 64
        with self.assertRaisesRegex(ContractError, 'PREDICTION_CONTEXT_CHANGED'):
            measure(plan, 0, changed, quotes, anchor + 300001)


if __name__ == '__main__':
    unittest.main()
