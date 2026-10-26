"""Exact small-model checks for the job-local PRICE feature cache."""
import copy
import os
import unittest
from unittest.mock import patch

import torch
from torch import nn
from torch.nn import functional as F

from laya_cell.integrity import ContractError, digest
from laya_cell.model import require_pocket4_gpu, tensor_hash
from laya_cell.resident_model import ResidentDecisionCell
from laya_cell.resident_row_accounting import row_id
from laya_cell.train import supervised_loss

from laya_cell.resident_train import _frozen_price_logits as cache_factory


class CountingEmbedding(nn.Embedding):
    def __init__(self):
        super().__init__(64, 8)
        self.calls = 0

    def forward(self, values):
        self.calls += 1
        return super().forward(values)


class RawDecisionModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = CountingEmbedding()
        self.dropout = nn.Dropout(.8)
        self.type_emb = nn.Embedding(3, 8)
        self.head = nn.Sequential(nn.LayerNorm(8), nn.Linear(8, 8), nn.GELU())
        self.scorer = nn.Sequential(nn.LayerNorm(8), nn.Linear(8, 8), nn.GELU(), nn.Linear(8, 1))
        self.act_head = nn.Sequential(nn.Linear(12, 256), nn.GELU(), nn.Linear(256, 2))

    def forward(self, input_ids, attention_mask, marker_pos, marker_mask, qtype):
        hidden = self.dropout(self.encoder(input_ids)) + self.type_emb(qtype)[:, None, :]
        hidden = self.head(hidden)
        markers = hidden.gather(1, marker_pos[..., None].expand(-1, -1, 8))
        logits = self.scorer(markers).squeeze(-1).float().masked_fill(~marker_mask, -1e4)
        probabilities = logits.detach().softmax(-1)
        top = probabilities.topk(2, -1).values
        entropy = -(probabilities*probabilities.clamp_min(1e-9).log()).sum(-1)/torch.log(torch.tensor(7.))
        features = torch.stack([top[:, 0], top[:, 0]-top[:, 1], entropy,
                                torch.full_like(entropy, 7/255.)], -1)
        return logits, self.act_head(torch.cat([hidden[:, 0].float(), features], -1))


def rows():
    return [{'sample_id': digest({'sample': index}), 'horizon_sec': 300,
             'outcome_receipt_digest': digest({'outcome': index}), 'index': index,
             'state': f'synthetic quote state {index}',
             'question': 'Predict EUR.USD PRICE7 after 300 seconds',
             'target_class': index % 7, 'teacher_probs': [1/7]*7 if index % 3 == 0 else None}
            for index in range(32)]


def encode(selected, offset=0):
    lengths = [7+row['index'] % 4 for row in selected]
    ids = torch.zeros((len(selected), max(lengths)), dtype=torch.long)
    mask = torch.zeros_like(ids)
    for index, (row, length) in enumerate(zip(selected, lengths)):
        ids[index, :length] = (torch.arange(length)+row['index']+offset) % 63+1
        mask[index, :length] = 1
    marker_mask = torch.ones((len(selected), 7), dtype=torch.bool)
    return {'input_ids': ids, 'attention_mask': mask,
            'marker_pos': torch.arange(7).expand(len(selected), -1),
            'marker_mask': marker_mask, 'qtype': torch.zeros(len(selected), dtype=torch.long)}


class PriceInputCacheTests(unittest.TestCase):
    def assert_tree_equal(self, expected, actual):
        if isinstance(expected, torch.Tensor):
            self.assertTrue(torch.equal(expected, actual))
        elif isinstance(expected, dict):
            self.assertEqual(expected.keys(), actual.keys())
            for key in expected:
                self.assert_tree_equal(expected[key], actual[key])
        elif isinstance(expected, (list, tuple)):
            self.assertEqual(len(expected), len(actual))
            for left, right in zip(expected, actual):
                self.assert_tree_equal(left, right)
        else:
            self.assertEqual(expected, actual)

    def compare(self, device):
        torch.manual_seed(20261007)
        original = ResidentDecisionCell(RawDecisionModel().to(device), 'a'*64)
        for name, parameter in original.named_parameters():
            if '.act_head.' in name:
                parameter.requires_grad_(False)
        selected_rows = rows()
        batches = [selected_rows[index:index+2] for index in range(0, 32, 2)]

        def run(cell, cached, rejected):
            frozen_before = {name: tensor_hash(value) for name, value in cell.named_parameters()
                             if not value.requires_grad}
            encoded_calls = [0]
            def encoded(selected):
                encoded_calls[0] += 1
                return encode(selected)
            forward = cache_factory(cell, encoded, device, 'COMPACT_CAUSAL_FINANCIAL_FEATURES10_V1')
            def logits(selected):
                if cached:
                    return forward(selected)
                return cell(**{key: value.to(device) for key, value in encoded(selected).items()})[0]
            cell.eval()
            with torch.no_grad():
                initial = [logits([row]).detach().clone() for row in selected_rows]
            optimizer = torch.optim.AdamW([p for p in cell.parameters() if p.requires_grad],
                                          lr=.0001, weight_decay=0.)
            trace = []
            states = []
            checkpoints = []
            cell.train(True)
            for epoch in range(1, 5):
                for selected in batches:
                    saved = cell.trainable_state()
                    previous = copy.deepcopy(optimizer.state_dict())
                    optimizer.zero_grad(set_to_none=True)
                    price = logits(selected)
                    truth = torch.tensor([r['target_class'] for r in selected], device=device)
                    teachers = torch.tensor([r['teacher_probs'] or [0.]*7 for r in selected], device=device)
                    teacher_mask = torch.tensor([r['teacher_probs'] is not None for r in selected], device=device)
                    loss = supervised_loss(price, truth, teachers, teacher_mask)
                    loss.backward()
                    before_clip = torch.nn.utils.clip_grad_norm_(
                        [p for p in cell.parameters() if p.requires_grad], 1., error_if_nonfinite=True)
                    gradient = {name: tensor_hash(p.grad) for name, p in cell.named_parameters() if p.grad is not None}
                    optimizer.step()
                    norm = cell.norm_report()
                    if len(trace)+1 in rejected:
                        norm['within_profile'] = False
                    states.append(copy.deepcopy(optimizer.state_dict()))
                    if not norm['within_profile']:
                        cell.restore_trainable(saved)
                        optimizer.load_state_dict(previous)
                        states.append(copy.deepcopy(optimizer.state_dict()))
                    trace.append({'loss': float(loss.detach()), 'gradient': gradient,
                                  'before_clip': float(before_clip), 'norm': norm})
                if epoch in (1, 2, 4):
                    cell.eval()
                    with torch.no_grad():
                        checkpoints.append([logits([row]).softmax(-1).detach().clone() for row in selected_rows])
                    cell.train(True)
            self.assertEqual(frozen_before, {name: tensor_hash(value) for name, value in cell.named_parameters()
                                            if not value.requires_grad})
            return {'trace': trace, 'optimizer_states': states, 'initial': initial, 'checkpoints': checkpoints,
                    'final': {name: tensor_hash(value) for name, value in cell.trainable_state().items()},
                    'encoder_calls': cell.model.encoder.calls, 'encoded_calls': encoded_calls[0]}

        for rejected in (set(), {2, 5, 17, 39, 64}):
            with self.subTest(device=str(device), rejected=sorted(rejected)):
                old = run(copy.deepcopy(original), False, rejected)
                new = run(copy.deepcopy(original), True, rejected)
                for key in ('trace', 'optimizer_states', 'initial', 'checkpoints', 'final'):
                    self.assert_tree_equal(old[key], new[key])
                self.assertEqual(len(new['trace']), 64)
                self.assertEqual(old['encoder_calls'], 192)
                self.assertEqual(new['encoder_calls'], 48)
                self.assertEqual((old['encoded_calls'], new['encoded_calls']), (192, 48))

    def test_cpu_every_step_optimizer_rollback_and_final_raw_probabilities_exact(self):
        gpu_was_initialized = torch.cuda.is_initialized()
        with patch.object(torch.accelerator, 'current_accelerator', return_value=None):
            self.compare(torch.device('cpu'))
        self.assertEqual(torch.cuda.is_initialized(), gpu_was_initialized)

    @unittest.skipUnless(os.environ.get('HEPTA_PRICE_CACHE_HIP') == '1', 'Requires explicit idle GPU window')
    def test_hip_every_step_optimizer_rollback_and_final_raw_probabilities_exact(self):
        self.compare(require_pocket4_gpu())

    def test_order_padding_grad_mode_and_profile_are_separate(self):
        torch.manual_seed(4)
        cell = ResidentDecisionCell(RawDecisionModel(), 'b'*64)
        selected = rows()[:2]
        compact = cache_factory(cell, encode, torch.device('cpu'), 'COMPACT')
        with torch.no_grad():
            first = compact(selected)
            self.assertTrue(torch.equal(first, compact(selected)))
            self.assertEqual(cell.model.encoder.calls, 1)
            reverse = compact(selected[::-1])
            self.assertEqual(cell.model.encoder.calls, 2)
            self.assertEqual(tuple(reverse.shape), (2, 7))
            compact([selected[0]])
            self.assertEqual(cell.model.encoder.calls, 3)
        compact(selected)
        self.assertEqual(cell.model.encoder.calls, 4)
        raw = cache_factory(cell, lambda selected: encode(selected, 20), torch.device('cpu'), 'RAW')
        with torch.no_grad():
            self.assertFalse(torch.equal(first, raw(selected)))
            self.assertEqual(cell.model.encoder.calls, 5)
        self.assertEqual(len(cell.model.scorer._forward_pre_hooks), 0)

    def test_upstream_gradients_are_rejected_and_hook_is_removed(self):
        cell = ResidentDecisionCell(RawDecisionModel(), 'c'*64)
        cell.model.encoder.requires_grad_(True)
        with self.assertRaisesRegex(ContractError, 'REQUIRES_FROZEN_EVAL_UPSTREAM'):
            cache_factory(cell, encode, torch.device('cpu'), 'COMPACT')
        self.assertEqual(len(cell.model.scorer._forward_pre_hooks), 0)

        # A later upstream forward can still return gradients despite frozen
        # parameters. Its rejection must remove the temporary scorer hook.
        cell.model.encoder.requires_grad_(False)
        forward = cache_factory(cell, encode, torch.device('cpu'), 'COMPACT')
        original_forward = cell.model.encoder.forward
        with patch.object(cell.model.encoder, 'forward',
                          side_effect=lambda values: original_forward(values).requires_grad_(True)):
            with self.assertRaisesRegex(ContractError, 'REQUIRES_FROZEN_FEATURES'):
                forward(rows()[:2])
        self.assertEqual(len(cell.model.scorer._forward_pre_hooks), 0)

    def test_cached_logits_keep_the_original_marker_mask(self):
        cell = ResidentDecisionCell(RawDecisionModel(), 'd'*64)
        def masked(selected):
            batch = encode(selected)
            batch['marker_mask'][0, 6] = False
            return batch
        forward = cache_factory(cell, masked, torch.device('cpu'), 'COMPACT')
        with torch.no_grad():
            first = forward(rows()[:2])
            second = forward(rows()[:2])
        self.assertTrue(torch.equal(first, second))
        self.assertEqual(float(second[0, 6]), -1e4)
        self.assertEqual(cell.model.encoder.calls, 1)


if __name__ == '__main__':
    torch.set_num_threads(1)
    unittest.main()
