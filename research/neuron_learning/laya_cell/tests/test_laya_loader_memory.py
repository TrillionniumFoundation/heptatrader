"""Actual loader with bound tiny stubs; no external Laya or GPU execution."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
import weakref
from unittest.mock import Mock, patch

import torch
from laya_cell import model
from laya_cell.integrity import ContractError, git_blob


class TinyEncoder(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = torch.nn.Embedding(8, 4, device='cpu')
        self.register_buffer('rope', torch.arange(4, dtype=torch.float32, device='cpu'),
                             persistent=False)

    def get_input_embeddings(self):
        return self.embedding


class TinyPinnedModel(torch.nn.Module):
    def __init__(self, *, tied=False):
        super().__init__()
        self.encoder = TinyEncoder()
        self.scorer = torch.nn.Linear(4, 2, device='cpu')
        self.scorer.bias.requires_grad_(False)
        self.register_buffer('temperature', torch.tensor([1., -0., 2.], device='cpu'))
        self.register_buffer('indices', torch.arange(3, dtype=torch.int64, device='cpu'))
        if tied:
            self.scorer_alias = self.scorer
        self.original_parameters = [weakref.ref(p) for p in self.parameters()]
        self.load_calls, self.to_calls = [], []
        self.float_calls = 0

    def load_state_dict(self, state_dict, strict=True, assign=False):
        self.load_calls.append({'strict': strict, 'assign': assign})
        return super().load_state_dict(state_dict, strict=strict, assign=assign)

    def float(self):
        self.float_calls += 1
        return super().float()

    def to(self, *args, **kwargs):
        device = kwargs.get('device', args[0] if args else None)
        self.to_calls.append({'device': str(device), 'dtype': kwargs.get('dtype'),
                              'non_blocking': kwargs.get('non_blocking'),
                              'parameter_dtypes': [p.dtype for p in self.parameters()],
                              'old_parameters_alive': [ref() is not None for ref in self.original_parameters]})
        if device is not None and torch.device(device).type == 'cuda':
            # Exercise the real loader's GPU branch using CPU casts only.
            # Actual H2D conversion order and GPU byte parity require a real probe.
            return super().to(device='cpu', dtype=kwargs['dtype'],
                              non_blocking=kwargs['non_blocking'])
        return super().to(*args, **kwargs)


class TinyTokenizer:
    def __len__(self): return 4
    def get_vocab(self): return {'a': 0, 'b': 1, 'c': 2, 'd': 3}


def mixed_checkpoint(module):
    return {name: value.detach().clone().to(torch.float16)
            if value.is_floating_point() and name != 'temperature'
            else value.detach().clone()
            for name, value in module.state_dict().items()}


class LayaLoaderMemoryTests(unittest.TestCase):
    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        self.assertFalse(torch.cuda.is_initialized())
        self.start_patch(patch.object(torch.cuda, '_lazy_init',
                                      side_effect=AssertionError('GPU initialization forbidden')))
        rng = torch.get_rng_state().clone()
        self.addCleanup(torch.set_rng_state, rng)
        self.future_flags = (torch.__future__.get_swap_module_params_on_conversion(),
                             torch.__future__.get_overwrite_module_params_on_conversion())
        self.assertEqual(self.future_flags, (False, False),
                         'Verify the pinned default conversion policy without changing global flags')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'stub_laya'
        self.package.mkdir()
        self.base = self.root / 'base'
        (self.base / 'encoder').mkdir(parents=True)
        (self.base / 'tokenizer').mkdir()
        (self.base / 'rl_agent_config.json').write_text('{}')
        (self.base / 'encoder/config.json').write_text(json.dumps({'hidden_size': 768}))
        self.blobs = {}
        for name in ('__init__.py', 'common.py', 'revisions.py'):
            raw = ('# bound tiny loader stub: ' + name + '\n').encode()
            (self.package / name).write_bytes(raw)
            self.blobs[name] = git_blob(raw)
        self.template = TinyPinnedModel()
        self.checkpoint = mixed_checkpoint(self.template)
        self.created = []
        self.tokenizer = TinyTokenizer()

        def build(cfg, *, encoder_dir, pretrained):
            self.assertEqual(cfg, {})
            self.assertEqual(encoder_dir, str(self.base / 'encoder'))
            self.assertFalse(pretrained)
            value = TinyPinnedModel(tied=self.tied)
            self.created.append(value)
            return value

        self.tied = False
        self.builder = Mock(side_effect=build)
        laya = ModuleType('laya')
        laya.__file__ = str(self.package / '__init__.py')
        laya.__path__ = [str(self.package)]
        laya.__version__ = model.LAYA_VERSION
        common = ModuleType('laya.common')
        common.build_model = self.builder
        transformers = ModuleType('transformers')
        transformers.AutoTokenizer = type('AutoTokenizer', (), {
            'from_pretrained': Mock(return_value=self.tokenizer)})
        safe = ModuleType('safetensors')
        safe.__path__ = []
        safe_torch = ModuleType('safetensors.torch')
        self.file_loader = Mock(side_effect=lambda path, *, device: self.checkpoint)
        safe_torch.load_file = self.file_loader
        self.start_patch(patch.dict(sys.modules, {'laya': laya, 'laya.common': common,
                                                'transformers': transformers, 'safetensors': safe,
                                                'safetensors.torch': safe_torch}))
        self.start_patch(patch.dict(os.environ))
        self.start_patch(patch.object(model, 'CODE_BLOBS', self.blobs))
        self.verify = self.start_patch(patch.object(model, 'verify_snapshot', return_value='a' * 64))
        self.require_gpu = self.start_patch(patch.object(model, 'require_pocket4_gpu',
                                                        return_value=torch.device('cuda:0')))
        self.manifest = {'tiny_stub_manifest': True}

    def tearDown(self):
        self.assertFalse(torch.cuda.is_initialized())
        self.assertEqual(self.future_flags,
                         (torch.__future__.get_swap_module_params_on_conversion(),
                          torch.__future__.get_overwrite_module_params_on_conversion()))

    def load(self, *, gpu):
        result = model.load_verified_laya(self.base, self.manifest, enforce_gpu=gpu)
        self.verify.assert_called_with(self.base, self.manifest)
        self.file_loader.assert_called_with(str(self.base / 'model.safetensors'), device='cpu')
        return result

    def assert_exact_state(self, left, right):
        a = dict(list(left.named_parameters()) + list(left.named_buffers()))
        b = dict(list(right.named_parameters()) + list(right.named_buffers()))
        self.assertEqual(set(a), set(b))
        for name, value in a.items():
            other = b[name]
            with self.subTest(tensor=name):
                self.assertEqual(value.shape, other.shape)
                self.assertEqual(value.dtype, other.dtype)
                self.assertEqual(value.requires_grad, other.requires_grad)
                self.assertTrue(torch.equal(value.detach().view(torch.uint8),
                                            other.detach().view(torch.uint8)))
        # The pinned test graph is untied, matching the reviewed real graph.
        for value in (left, right):
            ids = [id(p) for _, p in value.named_parameters(remove_duplicate=False)]
            self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn('encoder.rope', left.state_dict())
        self.assertNotIn('encoder.rope', right.state_dict())

    def reference(self):
        value = TinyPinnedModel()
        value.load_state_dict(self.checkpoint, strict=True)
        return value.float().to(torch.device('cpu')).eval()

    def test_gpu_assignment_then_one_blocking_conversion_matches_old_state_and_rng(self):
        before = torch.get_rng_state().clone()
        expected = self.reference()
        expected_rng = torch.get_rng_state().clone()
        torch.set_rng_state(before)
        actual, tokenizer, base_id = self.load(gpu=True)
        self.assertTrue(torch.equal(expected_rng, torch.get_rng_state()))
        self.assert_exact_state(actual, expected)
        self.assertIs(tokenizer, self.tokenizer)
        self.assertEqual(base_id, 'a' * 64)
        self.assertFalse(actual.training)
        self.assertEqual(actual.load_calls, [{'strict': True, 'assign': True}])
        self.assertEqual(actual.float_calls, 0)
        self.assertEqual(len(actual.to_calls), 1)
        transfer = actual.to_calls[0]
        self.assertEqual(transfer['device'], 'cuda:0')
        self.assertEqual(transfer['dtype'], torch.float32)
        self.assertIs(transfer['non_blocking'], False)
        self.assertEqual(set(transfer['parameter_dtypes']), {torch.float16})
        self.assertFalse(any(transfer['old_parameters_alive']))

    def test_cpu_fallback_keeps_original_copy_float_to_path(self):
        expected = self.reference()
        actual, _, _ = self.load(gpu=False)
        self.assert_exact_state(actual, expected)
        self.require_gpu.assert_not_called()
        self.assertEqual(actual.load_calls, [{'strict': True, 'assign': False}])
        self.assertEqual(actual.float_calls, 1)
        self.assertEqual(len(actual.to_calls), 1)
        self.assertEqual(actual.to_calls[0]['device'], 'cpu')
        self.assertIsNone(actual.to_calls[0]['dtype'])
        self.assertTrue(all(actual.to_calls[0]['old_parameters_alive']))

    def test_strict_missing_unexpected_and_shape_errors_precede_transfer(self):
        original = self.checkpoint
        mutations = []
        missing = dict(original); missing.pop('scorer.weight'); mutations.append(missing)
        unexpected = dict(original); unexpected['extra.weight'] = torch.ones(1, device='cpu'); mutations.append(unexpected)
        shape = dict(original); shape['scorer.weight'] = torch.ones((3, 4), dtype=torch.float16, device='cpu'); mutations.append(shape)
        for gpu in (False, True):
            for changed in mutations:
                with self.subTest(gpu_branch=gpu, keys=sorted(changed)):
                    self.checkpoint = changed
                    with self.assertRaises(RuntimeError):
                        self.load(gpu=gpu)
                    self.assertEqual(self.created[-1].to_calls, [])

    def test_tied_parameters_fail_closed_only_on_gpu_assignment_path(self):
        self.tied = True
        self.checkpoint = mixed_checkpoint(TinyPinnedModel(tied=True))
        with self.assertRaisesRegex(ContractError, 'UNSUPPORTED_TIED_PRETRAINED_PARAMETERS'):
            self.load(gpu=True)
        self.assertEqual(self.created[-1].load_calls, [])
        self.assertEqual(self.created[-1].to_calls, [])
        actual, _, _ = self.load(gpu=False)
        self.assertIs(actual.scorer.weight, actual.scorer_alias.weight)
        self.assertIs(actual.scorer.bias, actual.scorer_alias.bias)

    def test_code_blob_and_snapshot_guards_still_precede_model_build(self):
        self.verify.side_effect = ContractError('SNAPSHOT_HASH_MISMATCH:test')
        with self.assertRaisesRegex(ContractError, 'SNAPSHOT_HASH_MISMATCH'):
            self.load(gpu=False)
        self.builder.assert_not_called()
        self.verify.side_effect = None
        (self.package / 'common.py').write_text('# changed stub\n')
        with self.assertRaisesRegex(ContractError, 'LAYA_CODE_BLOB_MISMATCH:common.py'):
            self.load(gpu=False)
        self.builder.assert_not_called()

    def test_cpu_widening_matches_copy_for_every_binary16_bit_pattern(self):
        source = torch.arange(65536, dtype=torch.int32, device='cpu').to(torch.int16).view(torch.float16)
        expected = torch.empty(source.shape, dtype=torch.float32, device='cpu')
        expected.copy_(source)
        actual = source.to(dtype=torch.float32)
        self.assertTrue(torch.equal(expected.view(torch.int32), actual.view(torch.int32)))


if __name__ == '__main__':
    unittest.main()
