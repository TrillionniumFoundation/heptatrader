"""Tiny CPU fixtures: reuse fingerprints only for identical frozen parameters."""
import unittest
from unittest.mock import patch

import torch
from torch import nn

from laya_cell import model, resident_model
from laya_cell.integrity import ContractError


def tiny_pretrained():
    value = nn.Module()
    value.encoder = nn.Linear(4, 4)
    value.scorer = nn.Sequential(nn.LayerNorm(4), nn.Linear(4, 4),
                                 nn.GELU(), nn.Linear(4, 1))
    value.act_head = nn.Sequential(nn.Linear(8, 256), nn.GELU(),
                                   nn.Linear(256, 2))
    return value


class ResidentFrozenSnapshotTests(unittest.TestCase):
    def test_constructor_hashes_each_frozen_parameter_once(self):
        with patch.object(model, 'tensor_hash', wraps=model.tensor_hash) as hashing, \
                patch.object(resident_model, 'tensor_hash', hashing, create=True):
            cell = resident_model.ResidentDecisionCell(tiny_pretrained(), 'a'*64)
            frozen = {name: parameter for name, parameter in cell.model.named_parameters()
                      if not parameter.requires_grad}
            self.assertEqual(hashing.call_count, len(frozen))
            self.assertEqual({id(call.args[0]) for call in hashing.call_args_list},
                             {id(parameter) for parameter in frozen.values()})
            hashing.reset_mock()
            cell.assert_frozen()
            self.assertEqual(hashing.call_count, len(frozen))

    def test_renamed_action_base_retains_exact_original_hashes(self):
        pretrained = tiny_pretrained()
        weight, bias = pretrained.act_head[2].weight, pretrained.act_head[2].bias
        before = {name: model.tensor_hash(parameter)
                  for name, parameter in pretrained.named_parameters()}
        cell = resident_model.ResidentDecisionCell(pretrained, 'b'*64)
        self.assertIs(cell.model.act_head[2].base.weight, weight)
        self.assertIs(cell.model.act_head[2].base.bias, bias)
        self.assertEqual(cell._frozen['act_head.2.base.weight'], before['act_head.2.weight'])
        self.assertEqual(cell._frozen['act_head.2.base.bias'], before['act_head.2.bias'])
        self.assertEqual(cell._frozen,
                         {name: model.tensor_hash(parameter)
                          for name, parameter in cell.model.named_parameters()
                          if not parameter.requires_grad})

    def test_final_complete_integrity_check_detects_a_frozen_write(self):
        cell = resident_model.ResidentDecisionCell(tiny_pretrained(), 'c'*64)
        cell.assert_frozen()
        with torch.no_grad():
            cell.model.encoder.weight[0, 0].add_(1.)
        with self.assertRaisesRegex(ContractError, 'PRETRAINED_BASE_CHANGED'):
            cell.assert_frozen()

    def test_unknown_frozen_parameter_is_rejected_without_trusting_a_new_hash(self):
        original = resident_model.MaskedActionLinear
        def with_unknown(base):
            wrapped = original(base)
            wrapped.register_parameter('unknown', nn.Parameter(torch.ones(1), requires_grad=False))
            return wrapped
        with patch.object(resident_model, 'MaskedActionLinear', side_effect=with_unknown):
            with self.assertRaisesRegex(ContractError, 'RESIDENT_FROZEN_PARAMETER_IDENTITY_CHANGED'):
                resident_model.ResidentDecisionCell(tiny_pretrained(), 'd'*64)

    def test_missing_frozen_parameter_is_rejected(self):
        original = resident_model.MaskedActionLinear
        def with_missing(base):
            wrapped = original(base)
            wrapped.base.bias = None
            return wrapped
        with patch.object(resident_model, 'MaskedActionLinear', side_effect=with_missing):
            with self.assertRaisesRegex(ContractError, 'RESIDENT_FROZEN_PARAMETER_IDENTITY_CHANGED'):
                resident_model.ResidentDecisionCell(tiny_pretrained(), 'e'*64)

    def test_first_snapshot_still_detects_same_object_write_during_wrapping(self):
        original = resident_model.MaskedActionLinear
        def with_write(base):
            wrapped = original(base)
            with torch.no_grad():
                wrapped.base.weight[0, 0].add_(1.)
            return wrapped
        with patch.object(resident_model, 'MaskedActionLinear', side_effect=with_write):
            cell = resident_model.ResidentDecisionCell(tiny_pretrained(), 'f'*64)
        with self.assertRaisesRegex(ContractError, 'PRETRAINED_BASE_CHANGED'):
            cell.assert_frozen()


if __name__ == '__main__':
    unittest.main()
