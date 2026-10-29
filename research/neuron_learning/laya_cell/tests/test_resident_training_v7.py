"""CPU-only v7 causal representation and distinct PRICE/parent/ACT wiring."""
import contextlib
import copy
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import torch
from safetensors.torch import load_file

from laya_cell import resident_price_input as inputs
from laya_cell import resident_training_v6 as v6
from laya_cell import resident_training_v7 as v7
from laya_cell import resident_train as worker
from laya_cell import resident_predict as predictor
from laya_cell import resident_plan_admission as admission
from laya_cell import resident_training_v2 as action
from laya_cell import resident_price_calibration_v2 as calibration
from laya_cell.dataset import encode_rows
from laya_cell.integrity import ContractError, digest, read_json
from laya_cell.resident_evaluate import measure
from laya_cell.resident_model import ResidentDecisionCell
from laya_cell.resident_price_baselines import FEATURES, features
from laya_cell.resident_store import get_meta
from laya_cell.tests import test_resident_training_v6_integration as fixture
from laya_cell.tests.test_resident_price_calibration import real_dataset_population

_legacy_encode = fixture._encode


def _encode(tokenizer, rows, *, price_input_profile=None):
    values = _legacy_encode(tokenizer, rows)
    values['marker_mask'] = torch.ones((len(rows), 7), dtype=torch.bool)
    if price_input_profile == inputs.COMPACT:
        # Deliberately distinguish the two input paths without label access.
        values['x'][:, 6, 6] *= .5
        values['x'][:, :, 7] += .25
    elif price_input_profile not in (None, inputs.RAW):
        raise AssertionError('unknown fixture input profile')
    return values


class _MaskedRawDecisionModel(fixture._RawDecisionModel):
    def forward(self, x, marker_mask=None):
        price, act = super().forward(x)
        if marker_mask is not None:
            price = price.masked_fill(~marker_mask, -1e4)
        return price, act


class CompactInputContractTests(unittest.TestCase):
    def row(self):
        from laya_cell.dataset import rows_for_training
        data, window, _ = real_dataset_population()
        return rows_for_training(data, window)[0][0]

    def test_exact_existing_ten_causal_formulas_and_fixed_rounding(self):
        row = self.row(); before = copy.deepcopy(row)
        state = inputs.model_state(row['state'], inputs.COMPACT)
        self.assertEqual(list(state['features']), list(FEATURES))
        self.assertEqual(list(state['features'].values()),
                         [round(x, 6) for x in features(row['state'])])
        changed = copy.deepcopy(row)
        changed.update(target_class=6, teacher_probs=[1/7]*7, outcome_available_at_ms=10**15)
        self.assertEqual(state, inputs.model_state(changed['state'], inputs.COMPACT))
        self.assertEqual(row, before)
        changed['state']['quotes'][-2][1] = '1.11998'
        self.assertNotEqual(state, inputs.model_state(changed['state'], inputs.COMPACT))
        for profile in ('UNKNOWN', None, {}):
            with self.subTest(profile=profile), self.assertRaises(ContractError):
                inputs.model_state(row['state'], profile)
        with self.assertRaisesRegex(ContractError, 'STATE_FIELDS'):
            inputs.model_state({**row['state'], 'target_class': 6}, inputs.COMPACT)

    def test_legacy_default_raw_encoding_is_exact_and_compact_is_opt_in(self):
        seen = []
        def build(_tok, state, _question, **kwargs):
            seen.append(copy.deepcopy(state))
            return [1]*15, list(range(7)), {'options_distinct': 7}, {'truncated': False}
        def collate(items, pad_id):
            self.assertEqual(pad_id, 0)
            self.assertEqual(len(items), 1)
            item, = items[0]
            ids = torch.tensor([item['ids']])
            markers = torch.tensor([item['markers']])
            return {'input_ids': ids, 'attention_mask': torch.ones_like(ids),
                    'marker_pos': markers, 'marker_mask': torch.ones_like(markers, dtype=torch.bool),
                    'qtype': torch.tensor([item['qtype']])}
        # This contract tests our state routing, without the full upstream Laya
        # package, tokenizer or model dependencies in the CPU-only CI lane.
        package = types.ModuleType('laya')
        common = types.ModuleType('laya.common')
        package.common = common
        common.build_sequence = build
        common.collate_items = collate
        with patch.dict(sys.modules, {'laya': package, 'laya.common': common}):
            tokenizer = types.SimpleNamespace(pad_token_id=0)
            default = encode_rows(tokenizer, [self.row()])
            explicit = encode_rows(tokenizer, [self.row()], price_input_profile=inputs.RAW)
            encode_rows(tokenizer, [self.row()], price_input_profile=inputs.COMPACT)
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(set(seen[2]['features']), set(FEATURES))
        self.assertEqual({key: tuple(value.shape) for key, value in default.items()},
                         {'input_ids': (1, 15), 'attention_mask': (1, 15),
                          'marker_pos': (1, 7), 'marker_mask': (1, 7), 'qtype': (1,)})
        self.assertTrue(all(torch.equal(default[k], explicit[k]) for k in default))

    def test_profiles_are_explicit_and_legacy_parent_defaults_to_own_raw(self):
        self.assertEqual(inputs.for_candidate({}), inputs.RAW)
        parent = {'objective': copy.deepcopy(v7.OBJECTIVE), **inputs.bindings({})}
        self.assertEqual(inputs.for_candidate(parent), inputs.COMPACT)
        self.assertEqual(inputs.bindings(parent)['parent_price_input_profile'], inputs.COMPACT)
        with self.assertRaises(ContractError):
            inputs.for_candidate({'price_input_profile': inputs.COMPACT})
        with self.assertRaises(ContractError):
            inputs.for_candidate({'objective': v7.OBJECTIVE})
        with self.assertRaisesRegex(ContractError, 'UNKNOWN_PARENT_PRICE_INPUT_OBJECTIVE'):
            inputs.for_candidate({'objective': {'schema': 'unrecognized.v99'}})
        changed = copy.deepcopy(v7.OBJECTIVE)
        changed['price_input_spec']['decimal_places'] = 3
        with self.assertRaises(ContractError):
            v7.check_objective(changed)
        for key in ('training_episode', 'learning_rate', 'gradient_norm_cap',
                    'price_output_profile', 'future_price_value_rule', 'mature_pool'):
            self.assertEqual(v7.OBJECTIVE[key], v6.OBJECTIVE[key])

    def test_predict_uses_compact_parent_only_when_its_own_artifact_declares_it(self):
        parent = {'objective': copy.deepcopy(v7.OBJECTIVE), **inputs.bindings({})}
        def transfer(tok, rows, **kwargs):
            return {k: fixture._CPUTransfer(v) for k, v in _encode(tok, rows, **kwargs).items()}
        with patch.object(predictor, 'encode_rows', side_effect=transfer):
            act, paired_parent, child = predictor._prediction_batches(None, self.row(), inputs.bindings(parent))
            self.assertIs(paired_parent, child)
            self.assertIsNot(act, child)
            self.assertFalse(torch.equal(act['x'], child['x']))
            act, paired_parent, child = predictor._prediction_batches(None, self.row(), inputs.bindings({}))
            self.assertIs(paired_parent, act)
            self.assertIsNot(paired_parent, child)
            act, paired_parent, child = predictor._prediction_batches(None, self.row(), None)
            self.assertIs(act, paired_parent)
            self.assertIs(act, child)


class ProspectiveV7IntegrationTests(unittest.TestCase):
    def fixture(self, target):
        torch.set_num_threads(2)
        with patch.object(fixture, 'policy', v7), patch.object(fixture, '_encode', _encode), \
                patch.object(fixture, '_RawDecisionModel', _MaskedRawDecisionModel), \
                patch.object(torch.accelerator, 'current_accelerator', return_value=None):
            return fixture.ProspectiveV6IntegrationTests.fixture(self, target)

    def test_worker_cache_receives_real_marker_masks_and_distinct_full_batches(self):
        original = worker._frozen_price_logits
        misses = []
        def observed(cell, encoded, device, profile):
            self.assertEqual(profile, inputs.COMPACT)
            def capture(selected):
                batch = encoded(selected)
                self.assertEqual(batch['marker_mask'].dtype, torch.bool)
                self.assertEqual(tuple(batch['marker_mask'].shape), (len(selected), 7))
                misses.append(len(selected))
                return batch
            return original(cell, capture, device, profile)
        with patch.object(worker, '_frozen_price_logits', side_effect=observed):
            _, _, candidate, _, _ = self.fixture(6)
        self.assertEqual((misses.count(1), misses.count(2)), (32, 16))
        self.assertEqual(candidate['action_phase_diagnostics']['measured_execution']['encoder_forward_calls'],
                         candidate['action_phase']['unique_rows']//2)

    def test_zero_weight_keeps_same_no_forward_and_no_alpha_allocation(self):
        service, job, candidate, _, _ = self.fixture(3)
        self.assertEqual(candidate['price_calibration']['model_weight'], 0.)
        before = get_meta(service.db, 'evaluation_trial')
        with patch.object(admission, 'capture_source', side_effect=AssertionError('no future read')), \
                patch.object(admission, 'inspect_queue', return_value={'pending_original_trials': 1}):
            receipt = admission.register(service, 'v6-job', candidate, job,
                                         clock=lambda: fixture.source_fixture.CUTOFF + 2000)
        self.assertEqual(receipt['state'], 'NO_FORWARD_TRIAL')
        self.assertEqual(receipt['price_input_profile'], inputs.COMPACT)
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), before)
        self.assertFalse(receipt['trial_allocated'])
        self.assertFalse((Path(service.c['evaluation_directory'])/'v6-job/plan.json').exists())

    def test_real_training_prediction_parent_own_raw_and_child_act_raw(self):
        service, job, candidate, rows, raw = self.fixture(6)
        self.assertGreater(candidate['price_calibration']['model_weight'], 0.)
        self.assertNotEqual(candidate['initial_child_price_measurement']['ce'],
                            candidate['measurement']['parent_ce'])
        self.assertEqual(candidate['learning_curve']['epoch0_scope'],
                         'FROZEN_PARENT_OWN_INPUT_BASELINE')
        self.assertEqual(candidate['parent_price_input_profile'], inputs.RAW)
        v7.verify_candidate(job, candidate)
        for key in ('price_input_profile', 'parent_price_input_profile', 'action_input_profile'):
            changed = copy.deepcopy(candidate); changed[key] = 'UNKNOWN'
            with self.subTest(field=key), self.assertRaises(ContractError):
                v7.verify_candidate(job, changed)
        changed = copy.deepcopy(candidate)
        changed['final_price_raw_forward_receipts'][0]['price_input_id'] = '0'*64
        with self.assertRaisesRegex(ContractError, 'INPUT_RECEIPT_CHANGED'):
            v7.verify_candidate(job, changed)
        with self.assertRaisesRegex(ContractError, 'V7_INPUT_RECEIPT_CHANGED'):
            calibration.verify_candidate_fit(job, changed)
        now = fixture.source_fixture.CUTOFF + 2000
        with patch.object(admission, 'inspect_queue', return_value={'pending_original_trials': 0}), \
                patch.object(admission, 'capture_source', return_value=fixture.fresh_proof(now)):
            result = admission.register(service, 'v6-job', candidate, job, clock=lambda: now)
        self.assertEqual(result['state'], 'PUBLISHED')
        path = Path(service.c['evaluation_directory'])/'v6-job/plan.json'
        plan = read_json(path)
        self.assertFalse(set(v7.RUNTIME_PINS)-set(plan['evaluation_runtime_sha256']))
        self.assertEqual(inputs.check_bindings(plan), inputs.check_bindings(candidate))
        anchor = plan['start_ms']+1
        state = copy.deepcopy(rows[-1]['state']); state['anchor_ms'] = anchor
        sample = {'anchor_ms': anchor, 'sample_id': digest(state), 'reference_mid': 1.12,
                  'native_episode': {}, 'raw_features': [0.]*16}
        row = {'state': state}
        with contextlib.ExitStack() as stack:
            replacements = {
                'load_verified_laya': lambda *_: (copy.deepcopy(raw), object(), digest({})),
                'encode_rows': lambda tok, selected, **kw: {k: fixture._CPUTransfer(v)
                    for k, v in _encode(tok, selected, **kw).items()},
                'NativePorts': fixture._NativeFixture,
                'read_for_future_plan': lambda *_, **kwargs: ([], [], 'ORIGINAL_FULL_HISTORY_V1'),
                'forecast_input': lambda *_: (sample, row)}
            for key, value in replacements.items():
                stack.enter_context(patch.object(predictor, key, value))
            stack.enter_context(patch.object(predictor.time, 'time', return_value=anchor/1000))
            stack.enter_context(patch.object(torch.cuda, 'get_device_properties',
                return_value=types.SimpleNamespace(gcnArchName='CPU_TEST_FIXTURE')))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            prediction = predictor.predict(service.c, path, 0)
        # Independently reconstruct all three model/input combinations.
        cell = ResidentDecisionCell(copy.deepcopy(raw), digest({}))
        cell.restore_parent(load_file(str(Path(job['parent_directory'])/'adapter.safetensors')),
                            allow_scorer_only=True)
        with torch.no_grad():
            expected_parent = cell(**_encode(None, [row]))[0].softmax(-1)[0].tolist()
            cell.restore_trainable(load_file(str(Path(job['output_directory'])/'adapter.safetensors')))
            expected_price = cell(**_encode(None, [row], price_input_profile=inputs.COMPACT))[0].softmax(-1)[0].tolist()
            raw_act = cell(**_encode(None, [row]))[1]
            expected_action = action.action_inference(raw_act, candidate['action_training_profile'])
        self.assertEqual(prediction['parent_probs'], expected_parent)
        self.assertEqual(prediction['candidate_raw_probs'], expected_price)
        self.assertEqual(prediction['action_inference'], expected_action)
        inputs.verify_prediction(plan, prediction)
        quotes = [{'t': anchor+i*10000, 'mid': 1.12, 'receipt': str(i)} for i in range(31)]
        committed = read_json(path.parent/'input-000.json')
        publication=read_json(path.parent/'phase-000.json')['publication']
        inputs.verify_committed_input(plan, committed, prediction)
        self.assertEqual(measure(plan, 0, prediction, quotes, anchor+300001,
                                 committed_input=committed,publication_record=publication)['status'], 'OBSERVED')
        changed_input = copy.deepcopy(committed)
        changed_input['candidate_price_input_id'] = '0'*64
        with self.assertRaisesRegex(ContractError, 'COMMITTED_PRICE_INPUT_CHANGED'):
            measure(plan, 0, prediction, quotes, anchor+300001, committed_input=changed_input)
        with self.assertRaisesRegex(ContractError, 'COMMITTED_PRICE_INPUT_REQUIRED'):
            measure(plan, 0, prediction, quotes, anchor+300001)
        for key in (*inputs.FIELDS, 'candidate_price_input_id', 'parent_price_input_id', 'action_input_id'):
            changed = copy.deepcopy(prediction); changed[key] = 'UNKNOWN'
            with self.subTest(field=key), self.assertRaises(ContractError):
                measure(plan, 0, changed, quotes, anchor+300001, committed_input=committed)
        for key, value in (('anchor_ms', anchor+10000), ('reference_mid', 1.1201),
                           ('sample_id', '0'*64)):
            changed = copy.deepcopy(prediction); changed[key] = value
            with self.subTest(metadata=key), self.assertRaisesRegex(ContractError, 'COMMITTED_PRICE_INPUT_CHANGED'):
                measure(plan, 0, changed, quotes, anchor+300001, committed_input=committed)


if __name__ == '__main__':
    unittest.main()
