"""Actual tiny CPU v8 train/save/replay/register/predict/score, with no live state."""
import contextlib
import copy
import io
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import torch

from laya_cell import resident_train as worker
from laya_cell import resident_predict as predictor
from laya_cell import resident_plan_admission as admission
from laya_cell import resident_price_calibration_v3 as calibration
from laya_cell import resident_price_input as inputs
from laya_cell import resident_parent_calibration as comparator
from laya_cell import resident_training_v8 as policy
from laya_cell.integrity import ContractError, digest, read_json
from laya_cell.resident_store import get_meta
from laya_cell.resident_evaluate import measure
from laya_cell.tests import test_resident_training_v6_integration as fixture
from laya_cell.tests.test_resident_training_v7 import _MaskedRawDecisionModel

_original_encode = fixture._encode


def causal_option(anchor):
    return (2, 3, 4)[anchor//600000 % 3]


def encode_raw(_tokenizer, rows, *, price_input_profile=None):
    if price_input_profile not in (None, inputs.RAW):
        raise AssertionError('v8 fixture never encodes compact PRICE or ACT')
    values = _original_encode(_tokenizer, rows)
    for index, row in enumerate(rows):
        # A deterministic input-only signal for this synthetic positive case.
        # Neither the label nor any outcome field enters tokenization.
        values['x'][index, :, 6] = 0.
        values['x'][index, causal_option(row['state']['anchor_ms']), 6] = 1.
    values['marker_mask'] = torch.ones((len(rows), 7), dtype=torch.bool)
    return values


class RawCenteredIntegrationTests(unittest.TestCase):
    def fixture(self, positive, *, parent_comparator=False):
        def prepare(dataset):
            for sample in dataset['samples']:
                target = {2: -.02, 3: 0., 4: .02}[causal_option(sample['anchor_ms'])]
                sample['targets'][0] = target
                sample['outcome_receipts'][0]['target_scaled'] = target
        def prepare_job(job):
            if parent_comparator:
                job[comparator.FIELD]=comparator.PROFILE
                job['runtime_sha256']={name:'b'*64 for name in comparator.RUNTIME_PINS}
        torch.set_num_threads(2)
        with patch.object(fixture, 'policy', policy), \
                patch.object(fixture, 'calibration', calibration), \
                patch.object(fixture, '_encode', encode_raw), \
                patch.object(fixture, '_RawDecisionModel', _MaskedRawDecisionModel), \
                patch.object(torch.accelerator, 'current_accelerator', return_value=None):
            return fixture.ProspectiveV6IntegrationTests.fixture(self, 3,
                prepare_dataset=prepare if positive else None,prepare_job=prepare_job)

    def test_zero_covariance_is_exact_prior_no_forward_or_alpha(self):
        service, job, candidate, _, _ = self.fixture(False)
        self.assertEqual(candidate['price_calibration']['model_weight'], 0.)
        before = get_meta(service.db, 'evaluation_trial')
        with patch.object(admission, 'capture_source', side_effect=AssertionError('exact tie needs no source')), \
                patch.object(admission, 'inspect_queue', return_value={'pending_original_trials': 1}):
            receipt = admission.register(service, 'v6-job', candidate, job,
                clock=lambda: fixture.source_fixture.CUTOFF+2000)
        self.assertEqual(receipt['state'], 'NO_FORWARD_TRIAL')
        self.assertEqual(receipt['reason'], 'EXACT_REQUIRED_PRIOR_NO_POSSIBLE_STRICT_GAIN')
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), before)
        self.assertFalse(receipt['trial_allocated'])
        self.assertEqual(receipt['price_input_objective'], policy.SCHEMA)
        self.assertEqual(receipt['price_input_profile'], inputs.RAW)
        self.assertFalse((Path(service.c['evaluation_directory'])/'v6-job/plan.json').exists())
        self.assertNotIn(comparator.RECORD,candidate)
        self.assertNotIn(comparator.FIELD,job)

    def test_positive_raw_train_save_replay_predict_and_score(self):
        service, job, candidate, rows, raw = self.fixture(True,parent_comparator=True)
        comparison=candidate[comparator.RECORD]
        self.assertEqual(comparison['calibration']['training_rows'],len(rows))
        self.assertEqual(comparison['calibration']['training_row_ids_digest'],
                         candidate['price_calibration']['training_row_ids_digest'])
        self.assertEqual(comparison['calibration']['raw_logits_scope'],calibration.PARENT_SCOPE)
        self.assertEqual(comparison['additional_model_forwards'],0)
        self.assertFalse(comparison['selection_gate'])
        usage = candidate['resource_usage']
        self.assertGreater(usage['max_process_rss_bytes'], 0)
        self.assertEqual(usage['scope'], 'THIS_WORKER_PROCESS_THROUGH_RECEIPT_CREATION')
        self.assertIs(usage['rss_and_gpu_bytes_are_not_additive'], True)
        self.assertEqual(candidate['candidate_id'], digest({k:v for k,v in candidate.items() if k != 'candidate_id'}))
        profile = candidate['price_calibration']
        self.assertGreater(profile['model_weight'], 0.)
        self.assertEqual(candidate['price_input_profile'], inputs.RAW)
        self.assertEqual(candidate['action_input_profile'], inputs.RAW)
        self.assertEqual([r['raw_probs'] for r in candidate['parent_price_forward_receipts']],
                         [r['raw_probs'] for r in candidate['initial_child_price_forward_receipts']])
        self.assertEqual(candidate['measurement']['parent_brier'],
                         candidate['initial_child_price_measurement']['brier'])
        dataset_path=Path(job['dataset']);dataset_reads=[];read_bytes=Path.read_bytes
        def reading(path):
            if path==dataset_path:dataset_reads.append(path)
            return read_bytes(path)
        with patch.object(Path,'read_bytes',reading):
            policy.verify_candidate(job, candidate)
        self.assertEqual(len(dataset_reads),1,'one independent verification shares its pinned rows')
        original_bytes=dataset_path.read_bytes()
        try:
            dataset_path.write_bytes(original_bytes+b'\n')
            with self.assertRaisesRegex(ContractError,'DATASET_CHANGED'):
                policy.verify_candidate(job,candidate)
        finally:dataset_path.write_bytes(original_bytes)
        legacy_job={**job,'objective':copy.deepcopy(policy.previous.OBJECTIVE)}
        expected_episode=policy.previous.freeze(legacy_job,worker.batches(rows),{},
            candidate['action_training_profile'],candidate['price_baselines'])
        expected_episode.pop('episode_id');expected_episode['objective_digest']=digest(job['objective'])
        expected_episode['episode_id']=digest(expected_episode)
        with patch.object(policy.closed_policy,'_source_metadata',wraps=policy.closed_policy._source_metadata) as reads:
            actual_episode=policy.freeze(job,worker.batches(rows),{},
                candidate['action_training_profile'],candidate['price_baselines'])
        self.assertEqual(actual_episode,expected_episode)
        self.assertEqual(reads.call_count,1,'v8 does not recursively replay v7/v6 adapters')
        calibration.verify_candidate_fit(job, candidate)
        self.assertLess(candidate['price_calibration_training_measurement']['models']['calibrated_child']['mean_brier'],
                        candidate['price_calibration_training_measurement']['models']['train_prior']['mean_brier'])
        for key in ('price_input_profile', 'price_output_profile'):
            changed = copy.deepcopy(candidate)
            changed[key] = 'UNKNOWN'
            with self.subTest(key=key), self.assertRaises(ContractError):
                policy.verify_candidate(job, changed)
        changed = copy.deepcopy(candidate)
        changed['initial_child_price_forward_receipts'][0]['raw_probs'] = [1/7]*7
        with self.assertRaises(ContractError):
            policy.verify_candidate(job, changed)
        now = fixture.source_fixture.CUTOFF+2000
        with patch.object(admission, 'inspect_queue', return_value={'pending_original_trials': 0}), \
                patch.object(admission, 'capture_source', return_value=fixture.fresh_proof(now)):
            receipt = admission.register(service, 'v6-job', candidate, job, clock=lambda: now)
        self.assertEqual(receipt['state'], 'PUBLISHED')
        path = Path(service.c['evaluation_directory'])/'v6-job/plan.json'
        plan = read_json(path)
        self.assertFalse(set(policy.RUNTIME_PINS)-set(plan['evaluation_runtime_sha256']))
        self.assertEqual(plan['price_output_profile'], calibration.OUTPUT)
        self.assertEqual(plan[comparator.RECORD],comparison)
        self.assertFalse(set(comparator.RUNTIME_PINS)-set(plan['evaluation_runtime_sha256']))
        anchor = plan['start_ms']+1
        state = copy.deepcopy(rows[-1]['state'])
        state['anchor_ms'] = anchor
        sample = {'anchor_ms': anchor, 'sample_id': digest(state), 'reference_mid': 1.12,
                  'native_episode': {}, 'raw_features': [0.]*16}
        row = {'state': state}
        with contextlib.ExitStack() as stack:
            replacements = {
                'load_verified_laya': lambda *_: (copy.deepcopy(raw), object(), 'c'*64),
                'encode_rows': lambda tok, selected, **kw: {k: fixture._CPUTransfer(v)
                    for k,v in encode_raw(tok, selected, **kw).items()},
                'NativePorts': fixture._NativeFixture,
                'read_for_future_plan': lambda *_: ([], [], 'ORIGINAL_FULL_HISTORY_V1'),
                'forecast_input': lambda *_: (sample, row)}
            for key, value in replacements.items():
                stack.enter_context(patch.object(predictor, key, value))
            stack.enter_context(patch.object(predictor.time, 'time', return_value=anchor/1000))
            stack.enter_context(patch.object(torch.cuda, 'get_device_properties',
                return_value=types.SimpleNamespace(gcnArchName='CPU_TEST_FIXTURE')))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            prediction = predictor.predict(service.c, path, 0)
        self.assertEqual(prediction['candidate_probs'], calibration.apply(profile, prediction['candidate_raw_probs']))
        self.assertEqual(prediction['calibrated_parent_probs'],
                         calibration.apply(comparison['calibration'],prediction['parent_probs']))
        self.assertEqual(prediction['price_input_profile'], inputs.RAW)
        usage = prediction['resource_usage']
        self.assertGreater(usage['max_process_rss_bytes'], 0)
        self.assertEqual(usage['scope'], 'THIS_WORKER_PROCESS_THROUGH_RECEIPT_CREATION')
        self.assertIs(usage['rss_and_gpu_bytes_are_not_additive'], True)
        self.assertEqual(read_json(path.parent/'prediction-000.json')['resource_usage'], usage)
        committed = read_json(path.parent/'input-000.json')
        inputs.verify_committed_input(plan, committed, prediction)
        quotes = [{'t': anchor+i*10000, 'mid': 1.12, 'receipt': str(i)} for i in range(31)]
        phase=read_json(path.parent/'phase-000.json');publication=phase['publication']
        self.assertEqual(phase['fixed_input_deadline_ms'],plan['start_ms']+plan['rule']['input_grace_ms'])
        self.assertEqual(publication['prediction_digest'],digest(prediction))
        self.assertGreaterEqual(publication['durably_published_at_ms'],prediction['created_at_ms'])
        outcome=measure(plan, 0, prediction, quotes, anchor+300001,
                        committed_input=committed,publication_record=publication)
        self.assertEqual(outcome['status'], 'OBSERVED')
        self.assertEqual(outcome['parent_calibration_comparator_id'],comparison['comparator_id'])
        self.assertEqual(outcome['calibrated_parent_brier'],
                         comparator.baselines.probability_metrics(prediction['calibrated_parent_probs'],3)['brier'])
        report=comparator.assessment(plan,[outcome])
        self.assertEqual(report['observed_pairs'],1)
        self.assertFalse(report['selection_gate']);self.assertFalse(report['execution_profit_proven'])
        changed=copy.deepcopy(prediction);changed['calibrated_parent_probs']=[1/7]*7
        with self.assertRaisesRegex(ContractError,'PARENT_CALIBRATION_PREDICTION_CHANGED'):
            measure(plan,0,changed,quotes,anchor+300001,committed_input=committed)
        changed = copy.deepcopy(prediction)
        changed['candidate_probs'] = [1/7]*7
        with self.assertRaisesRegex(ContractError, 'FORECAST_CHANGED'):
            calibration.verify_prediction(plan, changed)

    def test_raw_prediction_shares_one_encoding_without_compact_or_extra_act(self):
        parent = {'objective': copy.deepcopy(policy.OBJECTIVE),
                  **inputs.bindings({}, objective_schema=policy.SCHEMA)}
        binding = inputs.bindings(parent, objective_schema=policy.SCHEMA)
        calls = []
        def transfer(_tok, selected, **kwargs):
            calls.append(kwargs)
            return {k:fixture._CPUTransfer(v) for k,v in encode_raw(None,selected,**kwargs).items()}
        _, _, _, rows, _ = self.fixture(False)
        with patch.object(predictor, 'encode_rows', side_effect=transfer):
            act, parent_batch, child = predictor._prediction_batches(None, rows[0], binding)
        self.assertIs(act, parent_batch)
        self.assertIs(act, child)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
