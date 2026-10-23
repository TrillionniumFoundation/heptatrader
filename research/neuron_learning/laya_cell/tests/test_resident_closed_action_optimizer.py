"""Real CPU autograd: ACT changes, PRICE momentum and frozen weights do not."""
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

from laya_cell import resident_closed_action_training as closed
from laya_cell import resident_train
from laya_cell.integrity import ContractError, digest
from laya_cell.model import tensor_hash


def uncached_action_phase(cell, source, profile, encoded, device):
    """Pre-cache algorithm retained as an independent numerical reference."""
    original = {n:p.requires_grad for n,p in cell.named_parameters()}
    price_before = {n:p.detach().clone() for n,p in cell.named_parameters()
                    if p.requires_grad and '.act_head.' not in n}
    parameters = [p for n,p in cell.named_parameters()
                  if p.requires_grad and '.act_head.' in n]
    steps = []
    try:
        for name, parameter in cell.named_parameters():
            parameter.grad = None
            if original[name]:
                parameter.requires_grad_('.act_head.' in name)
        optimizer = torch.optim.AdamW(parameters, lr=.0001, weight_decay=0.)
        cell.train(True)
        for epoch in range(1, resident_train.training_v5.ACTION_PHASE['epochs']+1):
            for index, rows in enumerate(closed.batches(source), 1):
                batch = {k:v.to(device) for k,v in encoded(rows).items()}
                correct = torch.tensor([r['parent_correct'] for r in rows], dtype=torch.bool, device=device)
                saved = cell.trainable_state()
                state = copy.deepcopy(optimizer.state_dict())
                optimizer.zero_grad(set_to_none=True)
                logits = cell(**batch)[1]
                loss = closed.auxiliary_action_loss(logits, correct, profile)
                loss.backward()
                if any(p.grad is not None for n,p in cell.named_parameters() if '.act_head.' not in n):
                    raise AssertionError('Reference touched PRICE or encoder')
                norm_before = torch.nn.utils.clip_grad_norm_(parameters, 1., error_if_nonfinite=True)
                gradient = digest({n:tensor_hash(p.grad) for n,p in cell.named_parameters()
                                   if p.requires_grad and p.grad is not None})
                optimizer.step()
                norm = cell.norm_report()
                if not norm['within_profile']:
                    cell.restore_trainable(saved)
                    optimizer.load_state_dict(state)
                if any(not torch.equal(p.detach(), price_before[n])
                       for n,p in cell.named_parameters() if n in price_before):
                    raise AssertionError('Reference changed PRICE')
                steps.append({'epoch':epoch, 'batch_in_epoch':index,
                    'closed_action_row_ids':[r['row_id'] for r in rows],
                    'applied':norm['within_profile'], 'action_loss':float(loss.detach()),
                    'gradient_digest':gradient, 'gradient_norm_before_clip':float(norm_before.detach()),
                    'norm':norm})
    finally:
        for name, parameter in cell.named_parameters():
            parameter.requires_grad_(original[name])
            parameter.grad = None
    cell.assert_frozen()
    return resident_train.training_v5.action_receipt(source, steps, profile,
        price_parameters_unchanged=True, encoder_parameters_unchanged=True)


class TinyCell(nn.Module):
    def __init__(self, reject=False):
        super().__init__()
        self.model = nn.Module()
        self.model.encoder = nn.Linear(2, 2).requires_grad_(False)
        self.model.scorer = nn.Linear(2, 2)
        self.model.act_head = nn.Sequential(nn.Linear(2, 2))
        self.frozen = {n: p.detach().clone() for n, p in self.named_parameters()
                       if not p.requires_grad}
        self.reject = reject

    def forward(self, x):
        h = self.model.encoder(x)
        return self.model.scorer(h), self.model.act_head(h)

    def trainable_state(self):
        return {n: p.detach().clone() for n, p in self.named_parameters() if p.requires_grad}

    def restore_trainable(self, state):
        params = {n: p for n, p in self.named_parameters() if p.requires_grad}
        self.assert_set(params, state)
        with torch.no_grad():
            for n, p in params.items():
                p.copy_(state[n])

    @staticmethod
    def assert_set(a, b):
        if set(a) != set(b):
            raise AssertionError('Rollback parameter scope changed')

    def norm_report(self):
        return {'within_profile': not self.reject}

    def assert_frozen(self):
        current = {n: p for n, p in self.named_parameters() if not p.requires_grad}
        self.assert_set(current, self.frozen)
        if any(not torch.equal(current[n].detach(), p) for n, p in self.frozen.items()):
            raise AssertionError('Frozen encoder changed')


class ClosedActionOptimizerTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(42)
        self.rows = [{'row_id': digest({'fixture_row': i}), 'anchor_ms': 1000+i*100,
                      'future_outcome_available_ms': 1010+i*100,
                      'parent_correct': i < 8,
                      'fixture_features': [1., i/16]} for i in range(16)]
        self.source = {'source_id': 'e'*64, 'training_cutoff_ms': 10000}
        with patch.object(closed, 'calibration_rows', return_value=self.rows):
            self.profile = closed.prepare_profile(self.source, torch.tensor(
                [[.2, -.1] if i < 8 else [-.1, .2] for i in range(16)]))
        self.pairs = [[self.rows[0], self.rows[8]], [self.rows[1], self.rows[9]]]

    @staticmethod
    def encoded(selected):
        return {'x': torch.tensor([r['fixture_features'] for r in selected])}

    def phase(self, cell, implementation=resident_train.run_closed_action_phase, device='cpu', encoded=None):
        def receipt(source, steps, profile, **flags):
            self.assertTrue(flags['price_parameters_unchanged'])
            self.assertTrue(flags['encoder_parameters_unchanged'])
            return {'steps': copy.deepcopy(steps), 'applied_steps': sum(s['applied'] for s in steps)}
        with patch.object(closed, 'batches', return_value=self.pairs), \
                patch.object(resident_train.training_v5, 'action_receipt', side_effect=receipt), \
                contextlib.redirect_stdout(io.StringIO()):
            return implementation(cell, self.source, self.profile, encoded or self.encoded, device)

    def test_act_updates_without_moving_price_parameters_with_existing_momentum(self):
        cell = TinyCell()
        price_optimizer = torch.optim.AdamW(cell.model.scorer.parameters(), lr=.01)
        price_optimizer.zero_grad()
        cell(x=torch.ones(2, 2))[0].sum().backward()
        price_optimizer.step()
        state_before = copy.deepcopy(price_optimizer.state_dict())
        before = {n: p.detach().clone() for n, p in cell.named_parameters()}
        result = self.phase(cell)
        self.assertEqual(result['applied_steps'], 8)
        after = dict(cell.named_parameters())
        self.assertTrue(any(not torch.equal(before[n], p.detach())
                            for n, p in after.items() if '.act_head.' in n))
        for n, p in after.items():
            if '.act_head.' not in n:
                self.assertTrue(torch.equal(before[n], p.detach()), n)
        for key, values in state_before['state'].items():
            for field, value in values.items():
                self.assertTrue(torch.equal(value, price_optimizer.state_dict()['state'][key][field]))
        cell.assert_frozen()

    def test_every_rejected_action_step_rolls_back_only_action_and_restores_flags(self):
        cell = TinyCell(reject=True)
        before = {n: p.detach().clone() for n, p in cell.named_parameters()}
        flags = {n: p.requires_grad for n, p in cell.named_parameters()}
        result = self.phase(cell)
        self.assertEqual(result['applied_steps'], 0)
        self.assertEqual(len(result['steps']), 8)
        for n, p in cell.named_parameters():
            self.assertTrue(torch.equal(p.detach(), before[n]), n)
            self.assertEqual(p.requires_grad, flags[n])

    def test_failed_loss_restores_original_trainable_scope(self):
        cell = TinyCell()
        flags = {n: p.requires_grad for n, p in cell.named_parameters()}
        with patch.object(closed, 'auxiliary_action_loss', side_effect=ContractError('TEST_FAILURE')):
            with self.assertRaisesRegex(ContractError, 'TEST_FAILURE'):
                self.phase(cell)
        self.assertEqual({n: p.requires_grad for n, p in cell.named_parameters()}, flags)

    def test_failed_upstream_forward_removes_hook_and_restores_trainable_scope(self):
        cell = TinyCell()
        flags = {n:p.requires_grad for n,p in cell.named_parameters()}
        with patch.object(cell, 'forward', side_effect=RuntimeError('UPSTREAM_FAILURE')):
            with self.assertRaisesRegex(RuntimeError, 'UPSTREAM_FAILURE'):
                self.phase(cell)
        self.assertEqual(len(cell.model.act_head._forward_pre_hooks), 0)
        self.assertEqual({n:p.requires_grad for n,p in cell.named_parameters()}, flags)

    def assert_tensor_tree_equal(self, left, right):
        if torch.is_tensor(left):
            self.assertTrue(torch.equal(left, right))
        elif isinstance(left, dict):
            self.assertEqual(left.keys(), right.keys())
            for key in left:
                self.assert_tensor_tree_equal(left[key], right[key])
        elif isinstance(left, (list, tuple)):
            self.assertEqual(len(left), len(right))
            for a,b in zip(left, right):
                self.assert_tensor_tree_equal(a, b)
        else:
            self.assertEqual(left, right)

    def compare_cached_and_uncached(self, device='cpu'):
        from laya_cell.resident_model import ResidentDecisionCell

        class PinnedShapeModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder = nn.Sequential(nn.Linear(8, 8), nn.Dropout(.5))
                self.scorer = nn.Sequential(nn.LayerNorm(8), nn.Linear(8, 8), nn.GELU(), nn.Linear(8, 1))
                self.act_head = nn.Sequential(nn.Linear(12, 256), nn.GELU(), nn.Linear(256, 2))
                self.encoder_calls = 0

            def forward(self, x):
                self.encoder_calls += 1
                h = self.encoder(x)
                price = self.scorer(h).squeeze(-1)
                p = price.detach().softmax(-1)
                top2 = p.topk(2, -1).values
                k = torch.full_like(top2[:,0], 7.)
                entropy = -(p*p.clamp_min(1e-9).log()).sum(-1)/k.log()
                features = torch.stack([top2[:,0], top2[:,0]-top2[:,1], entropy, k/255.], -1)
                return price, self.act_head(torch.cat([h[:,0].float(), features], -1))

        self.rows = [{'row_id':digest({'cache_fixture':i}), 'anchor_ms':1000+i*100,
                      'future_outcome_available_ms':1010+i*100, 'parent_correct':i<20,
                      'fixture_features':[1., i/40]} for i in range(40)]
        self.pairs = [[self.rows[i],self.rows[20+i]] for i in range(20)]
        with patch.object(closed, 'calibration_rows', return_value=self.rows):
            self.profile = closed.prepare_profile(self.source, torch.tensor(
                [[.2,-.1] if i<20 else [-.1,.2] for i in range(40)]))

        def encode(rows):
            return {'x':torch.tensor([[[1.,option/7,r['fixture_features'][1],-.2,.1,.05,.3,.2]
                                       for option in range(7)] for r in rows])}

        def run(cell, implementation):
            history = []
            encoding_calls = [0]
            real_optimizer = torch.optim.AdamW

            def observed_encode(rows):
                encoding_calls[0] += 1
                return encode(rows)

            class ObservedAdamW(real_optimizer):
                def step(self, *args, **kwargs):
                    result = super().step(*args, **kwargs)
                    history.append(('step', copy.deepcopy(self.state_dict())))
                    return result

                def load_state_dict(self, state):
                    result = super().load_state_dict(state)
                    history.append(('rollback', copy.deepcopy(self.state_dict())))
                    return result

            with patch.object(torch.optim, 'AdamW', ObservedAdamW):
                receipt = self.phase(cell, implementation, device, observed_encode)
            return receipt, history, encoding_calls[0]

        for reject_steps in (set(), {2,5,17,39,80}):
            with self.subTest(device=str(device), reject_steps=sorted(reject_steps)):
                baseline = ResidentDecisionCell(PinnedShapeModel().to(device), 'c'*64)
                cached = copy.deepcopy(baseline)
                for cell in (baseline, cached):
                    original_norm = cell.norm_report
                    calls = [0]

                    def reject_selected(original_norm=original_norm, calls=calls):
                        calls[0] += 1
                        result = original_norm()
                        if calls[0] in reject_steps:
                            result['within_profile'] = False
                        return result

                    cell.norm_report = reject_selected
                expected, old_states, old_encoding = run(baseline, uncached_action_phase)
                actual, new_states, new_encoding = run(cached, resident_train.run_closed_action_phase)
                # Every step includes loss, clipped gradient digest, pre-clip
                # norm and full effective-weight norm/rollback decision.
                self.assertEqual(actual, expected)
                self.assert_tensor_tree_equal(new_states, old_states)
                self.assertEqual(len(actual['steps']), 80)
                self.assertEqual(sum(kind=='rollback' for kind,_ in new_states), len(reject_steps))
                self.assertEqual(cached.model.encoder_calls, 20)
                self.assertEqual(baseline.model.encoder_calls, 80)
                self.assertEqual((old_encoding,new_encoding), (80,20))
                self.assertEqual(len(cached.model.act_head._forward_pre_hooks), 0)
                self.assertEqual({n:tensor_hash(p) for n,p in cached.named_parameters()},
                                 {n:tensor_hash(p) for n,p in baseline.named_parameters()})
                self.assertEqual({n:p.requires_grad for n,p in cached.named_parameters()},
                                 {n:p.requires_grad for n,p in baseline.named_parameters()})
                self.assertTrue(all(p.grad is None for p in cached.parameters()))

    def test_cached_head_inputs_match_all_80_original_steps_and_rollbacks_exactly(self):
        self.compare_cached_and_uncached()


class CompleteV5TrainingIntegrationTests(unittest.TestCase):
    def test_price_episode_action_phase_and_calibration_replay_together(self):
        """Real CPU autograd and receipt validation, with a tiny encoder fixture.

        GPU/model loading, tokenization, native transport and source-prefix IO
        are explicit test doubles; no actual trading/model quality is claimed.
        The immutable source collector, causal dataset, actual adapter class,
        optimizers, norm rollback, calibrator and full v5 verifier are real.
        """
        import test_laya_resident_action_v2_closed_source as source_fixture
        from safetensors.torch import save_file
        from laya_cell.integrity import sha_file
        from laya_cell.resident_model import ResidentDecisionCell
        from laya_cell import resident_price_calibration as calibration
        from laya_cell import resident_training_v5 as v5
        from laya_cell.tests.test_resident_price_calibration import real_dataset_population

        class RawDecisionModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder = nn.Linear(8, 8)
                self.scorer = nn.Sequential(nn.Identity(), nn.Linear(8, 8), nn.Tanh(), nn.Linear(8, 1))
                self.act_head = nn.Sequential(nn.Linear(8, 256), nn.Tanh(), nn.Linear(256, 2))

            def forward(self, x):
                encoded = self.encoder(x)
                return self.scorer(encoded).squeeze(-1), self.act_head(encoded.mean(dim=1))

        class NativeFixture:
            def __init__(self, *args):
                pass

            def predictive_utility(self, *args):
                return {'receipt_digest': 'f'*64}

            def propose(self, *args):
                return {'selection_performed': False, 'status': 'RequiresIndependentAcceptance'}

        def encode(_tokenizer, rows):
            # State and option position only; actual labels never enter input.
            return {'x': torch.tensor([[[1., option/7, (r['state']['anchor_ms'] % 100000)/100000,
                -.2, .1, .05, .3, .2] for option in range(7)] for r in rows])}

        fixture = source_fixture.ClosedOriginalParentSourceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        dataset, window, rows = real_dataset_population()
        window = {key: source_fixture.CUTOFF+(0 if key == 'training_end_ms' else
                  1000000 if key == 'validation_start_ms' else 2000000) for key in window}
        dataset_path = fixture.root/'dataset.json'
        dataset_path.write_text(json.dumps(dataset))
        base = fixture.root/'base'
        base.mkdir()
        (base/'base-manifest.json').write_text('{}')
        parent_dir = fixture.root/'parent'
        parent_dir.mkdir()
        raw = RawDecisionModel()
        initial_cell = ResidentDecisionCell(copy.deepcopy(raw), 'c'*64)
        save_file(initial_cell.trainable_state(), str(parent_dir/'adapter.safetensors'))
        parent = {'base_id': 'c'*64,
                  'adapter_sha256': sha_file(parent_dir/'adapter.safetensors'),
                  'base_revision': 'fixture', 'trained_until_ms': 0,
                  'window': {'training_end_ms': 0}}
        parent['candidate_id'] = digest(parent)
        (parent_dir/'candidate.json').write_text(json.dumps(parent))
        for path in fixture.dir.glob('prediction-*.json'):
            prediction = json.loads(path.read_text())
            prediction['parent_id'] = parent['candidate_id']
            path.write_text(json.dumps(prediction))
            outcome_path = fixture.dir/path.name.replace('prediction-', 'outcome-')
            outcome = json.loads(outcome_path.read_text())
            outcome['prediction_file_sha256'] = sha_file(path)
            outcome_path.write_text(json.dumps(outcome))
        source = closed.freeze_source(fixture.root, parent['candidate_id'], source_fixture.CUTOFF)
        source_path = fixture.root/'source.json'
        source_sha = closed.write_source(source_path, source)
        row_ids = [resident_train.row_id(r) for r in rows]
        job = {'objective': copy.deepcopy(v5.OBJECTIVE), 'profile': resident_train.PROFILE,
            'base_directory': str(base), 'baseline_directory': str(base),
            'output_directory': str(fixture.root/'candidate'), 'parent_directory': str(parent_dir),
            'parent_id': parent['candidate_id'], 'parent_manifest_sha256': sha_file(parent_dir/'candidate.json'),
            'dataset': str(dataset_path), 'dataset_sha256': sha_file(dataset_path), 'window': window,
            'training_row_ids': row_ids, 'training_row_accounting_schema': v5.ROW_ACCOUNTING_SCHEMA,
            'data_fingerprint': digest(row_ids), 'evaluation_directory': str(fixture.root),
            'closed_action_source': str(source_path), 'closed_action_source_sha256': source_sha,
            'closed_action_source_id': source['source_id'], 'closed_action_parent_id': parent['candidate_id'],
            'closed_action_training_cutoff_ms': source_fixture.CUTOFF,
            'generation': 0, 'native_binary': '/fixture/native', 'native_binary_sha256': 'f'*64}
        torch.set_num_threads(2)
        with patch.object(resident_train, 'verify_baseline', return_value='d'*64), \
                patch.object(resident_train, 'verify_source_prefixes'), \
                patch.object(resident_train, 'require_pocket4_gpu', return_value=torch.device('cpu')), \
                patch.object(resident_train, 'load_verified_laya', return_value=(raw, object(), 'c'*64)), \
                patch.object(resident_train, 'encode_rows', side_effect=encode), \
                patch.object(resident_train, 'ResidentNative', NativeFixture), \
                patch.object(torch.cuda, 'reset_peak_memory_stats'), \
                patch.object(torch.cuda, 'max_memory_allocated', return_value=0), \
                patch.object(torch.cuda, 'get_device_properties',
                             return_value=types.SimpleNamespace(gcnArchName='CPU_TEST_FIXTURE')), \
                contextlib.redirect_stdout(io.StringIO()):
            candidate = resident_train.run_job(job, old_learner_verified=True)
        self.assertEqual(len(candidate['steps']), 64)
        self.assertEqual(candidate['row_accounting']['gradient_attempted_count'], 32)
        self.assertEqual(candidate['row_accounting']['gradient_attempted_presentations'], 128)
        self.assertEqual(candidate['action_phase']['unique_rows'], 8)
        self.assertEqual(candidate['action_phase']['attempted_steps'], 16)
        self.assertGreater(candidate['action_update_steps'], 0)
        self.assertTrue(candidate['action_head_trained'])
        self.assertGreater(candidate['action_adapter_validation']['effective_changed_coordinates'], 0)
        self.assertTrue(candidate['action_adapter_validation']['parent_action_adapter_present'])
        self.assertFalse(candidate['action_adapter_validation']['gpu_inference_replayed'])
        self.assertTrue(candidate['action_phase']['price_parameters_unchanged'])
        self.assertEqual(len(candidate['final_price_raw_forward_receipts']), 32)
        calibration.verify_candidate_fit(job, candidate)
        v5.verify_candidate(job, candidate)
        tampered = copy.deepcopy(candidate)
        tampered['action_adapter_validation']['effective_changed_coordinates'] += 1
        with self.assertRaisesRegex(ContractError, 'V5_ACT_NUMERIC_RECEIPT_CHANGED'):
            v5.verify_candidate(job, tampered)
        persisted = json.loads((Path(job['output_directory'])/'candidate.json').read_text())
        self.assertEqual(persisted, candidate)
        self.assertEqual(candidate['candidate_id'], digest({k: v for k, v in candidate.items()
                                                          if k != 'candidate_id'}))


if __name__ == '__main__':
    unittest.main()
