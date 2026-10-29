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
        self.load_calls, self.to_calls, self.apply_calls = [], [], []
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
        return super().to(*args, **kwargs)

    def _apply(self, fn, recurse=True):
        self.apply_calls.append({'parameter_dtypes': [p.dtype for p in self.parameters()],
                                 'old_parameters_alive': [ref() is not None for ref in self.original_parameters]})
        return super()._apply(fn, recurse=recurse)


class TinyTokenizer:
    def __len__(self): return 4
    def get_vocab(self): return {'a': 0, 'b': 1, 'c': 2, 'd': 3}


def mixed_checkpoint(module):
    return {name: value.detach().clone().to(torch.float16)
            if value.is_floating_point() and name != 'temperature'
            else value.detach().clone()
            for name, value in module.state_dict().items()}


class CpuTransferObserver:
    """CPU-backed destinations test scheduling and bytes, never real H2D/HIP."""
    def __init__(self, case):
        self.case = case
        self.staging_refs, self.target_refs = [], []
        self.fills, self.copies, self.fallbacks = [], [], []
        self.fail_copy = False
        empty, empty_like = torch.empty, torch.empty_like
        copy_, to = torch.Tensor.copy_, torch.Tensor.to

        def observed_empty(*args, **kwargs):
            value = empty(*args, **kwargs)
            if (value.device.type == 'cpu' and value.dtype == torch.float32
                    and value.numel() * value.element_size() == model._PRETRAINED_FP32_STAGING_BYTES):
                self.staging_refs.append(weakref.ref(value))
            return value

        def observed_empty_like(source, *args, **kwargs):
            if torch.device(kwargs.get('device', source.device)).type == 'cuda':
                case.assertEqual(kwargs['dtype'], torch.float32)
                value = empty_like(source, *args, **dict(kwargs, device='cpu'))
                self.target_refs.append((value.untyped_storage().data_ptr(), weakref.ref(value)))
                return value
            return empty_like(source, *args, **kwargs)

        def observed_copy(destination, source, *args, **kwargs):
            pointer = destination.untyped_storage().data_ptr()
            staging = next((ref() for ref in self.staging_refs if ref() is not None), None)
            if staging is not None and pointer == staging.untyped_storage().data_ptr():
                case.assertEqual(source.dtype, torch.float16)
                case.assertEqual(source.device.type, 'cpu')
                self.fills.append(destination.numel() * destination.element_size())
            if any(pointer == ptr and ref() is not None for ptr, ref in self.target_refs):
                case.assertFalse(args)
                case.assertEqual(kwargs, {'non_blocking': False})
                case.assertEqual(source.dtype, torch.float32)
                case.assertIsNotNone(staging)
                case.assertEqual(source.untyped_storage().data_ptr(), staging.untyped_storage().data_ptr())
                self.copies.append({'bytes': source.numel() * source.element_size(),
                                    'staging_pointer': source.untyped_storage().data_ptr()})
                if self.fail_copy:
                    raise RuntimeError('SYNTHETIC_BLOCKING_TRANSFER_FAILURE')
            return copy_(destination, source, *args, **kwargs)

        def observed_to(source, *args, **kwargs):
            device = kwargs.get('device', args[0] if args and isinstance(args[0], (str, torch.device)) else None)
            if device is not None and torch.device(device).type == 'cuda':
                case.assertFalse(args)
                case.assertIs(kwargs['non_blocking'], False)
                self.fallbacks.append({'dtype': source.dtype, 'shape': tuple(source.shape),
                                       'parameter': isinstance(source, torch.nn.Parameter),
                                       'contiguous': source.is_contiguous(), 'neg': source.is_neg()})
                return to(source, **dict(kwargs, device='cpu'))
            return to(source, *args, **kwargs)

        for owner, name, value in ((torch, 'empty', observed_empty),
                                   (torch, 'empty_like', observed_empty_like),
                                   (torch.Tensor, 'copy_', observed_copy),
                                   (torch.Tensor, 'to', observed_to)):
            case.start_patch(patch.object(owner, name, value))


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
        self.transfer = CpuTransferObserver(self)
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

    def test_gpu_assignment_then_one_staging_buffer_matches_old_state_and_rng(self):
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
        self.assertEqual(actual.to_calls, [])
        self.assertEqual(len(actual.apply_calls), 1)
        self.assertEqual(set(actual.apply_calls[0]['parameter_dtypes']), {torch.float16})
        self.assertFalse(any(actual.apply_calls[0]['old_parameters_alive']))
        self.assertEqual(len(self.transfer.staging_refs), 1)
        self.assertIsNone(self.transfer.staging_refs[0]())
        self.assertEqual(len(self.transfer.copies), len(list(actual.parameters())))
        self.assertEqual(len({row['staging_pointer'] for row in self.transfer.copies}), 1)
        self.assertFalse(any(row['parameter'] and row['dtype'] == torch.float16
                             for row in self.transfer.fallbacks))

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

    def test_owned_tokenizer_is_used_only_after_verified_gpu_checkpoint(self):
        def tokenizer(directory):
            self.assertEqual(directory, self.base / 'tokenizer')
            self.verify.assert_called_once_with(self.base, self.manifest)
            self.assertEqual(self.created[-1].load_calls, [{'strict': True, 'assign': True}])
            self.assertEqual(self.created[-1].apply_calls, [])
            return self.tokenizer

        with patch.object(model, 'load_local_tokenizer', side_effect=tokenizer) as load:
            self.assertIs(self.load(gpu=True)[1], self.tokenizer)
            load.assert_called_once()
            load.reset_mock()
            self.assertIs(self.load(gpu=False)[1], self.tokenizer)
            load.assert_not_called()

    def test_owned_tokenizer_still_rejects_embedding_mismatch_before_transfer(self):
        for vocab in ({'high_id': 8}, {str(i): i for i in range(9)}):
            with self.subTest(vocabulary=vocab):
                tokenizer = Mock()
                tokenizer.get_vocab.return_value = vocab
                tokenizer.__len__ = Mock(return_value=len(vocab))
                with patch.object(model, 'load_local_tokenizer', return_value=tokenizer):
                    with self.assertRaisesRegex(ContractError, 'TOKENIZER_EMBEDDING_MISMATCH'):
                        self.load(gpu=True)
                self.assertEqual(self.created[-1].apply_calls, [])
                self.assertEqual(self.transfer.staging_refs, [])

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
                    self.assertEqual(self.created[-1].apply_calls, [])
                    self.assertEqual(self.transfer.staging_refs, [])

    def test_tied_parameters_fail_closed_only_on_gpu_assignment_path(self):
        self.tied = True
        self.checkpoint = mixed_checkpoint(TinyPinnedModel(tied=True))
        with self.assertRaisesRegex(ContractError, 'UNSUPPORTED_TIED_PRETRAINED_PARAMETERS'):
            self.load(gpu=True)
        self.assertEqual(self.created[-1].load_calls, [])
        self.assertEqual(self.created[-1].to_calls, [])
        self.assertEqual(self.created[-1].apply_calls, [])
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

    def test_real_16mib_stage_reused_for_large_tail_and_small_parameter(self):
        previous_threads = torch.get_num_threads()
        self.addCleanup(torch.set_num_threads, previous_threads)
        torch.set_num_threads(2)
        limit = 16 * 1024 * 1024
        self.assertEqual(model._PRETRAINED_FP32_STAGING_BYTES, limit)
        count = limit // 4 + 3
        patterns = torch.arange(65536, dtype=torch.int32, device='cpu').to(torch.int16).view(torch.float16)
        patterns = patterns.roll(-0x7c01)  # The three-element tail contains NaN payloads.
        source = patterns.repeat((count + 65535) // 65536)[:count]
        original_bits = source.view(torch.int16).clone()
        expected = source.to(torch.float32)
        value = torch.nn.Module()
        value.register_parameter('large', torch.nn.Parameter(source))
        value.register_parameter('small', torch.nn.Parameter(torch.tensor([1., -0.], dtype=torch.float16),
                                                              requires_grad=False))
        before = torch.get_rng_state().clone()
        result = model._to_pretrained_gpu_fp32(value, torch.device('cuda:0'))
        self.assertIs(result, value)
        self.assertTrue(torch.equal(before, torch.get_rng_state()))
        self.assertTrue(torch.equal(source.view(torch.int16), original_bits))
        # SIMD/scalar boundaries may quiet NaNs with different payload bits
        # for whole-tensor versus chunked widening. Keep every non-NaN bit
        # exact, including infinities and signed zero, and preserve NaN sites.
        actual_nan = torch.isnan(result.large)
        expected_nan = torch.isnan(expected)
        self.assertTrue(torch.equal(actual_nan, expected_nan))
        non_nan = ~expected_nan
        self.assertTrue(torch.equal(result.large.view(torch.int32)[non_nan],
                                    expected.view(torch.int32)[non_nan]))
        self.assertTrue(torch.equal(result.small.view(torch.int32),
                                    torch.tensor([1., -0.], dtype=torch.float32).view(torch.int32)))
        self.assertTrue(result.large.requires_grad)
        self.assertFalse(result.small.requires_grad)
        self.assertEqual(self.transfer.fills, [limit, 12, 8])
        self.assertEqual([row['bytes'] for row in self.transfer.copies], self.transfer.fills)
        self.assertEqual(len(self.transfer.staging_refs), 1)
        self.assertIsNone(self.transfer.staging_refs[0]())
        self.assertEqual(len({row['staging_pointer'] for row in self.transfer.copies}), 1)
        self.assertEqual(self.transfer.fallbacks, [])

    def test_empty_scalar_and_buffers_keep_shapes_dtypes_and_values(self):
        value = torch.nn.Module()
        value.register_parameter('empty', torch.nn.Parameter(torch.empty((0, 3), dtype=torch.float16)))
        value.register_parameter('scalar', torch.nn.Parameter(torch.tensor(-0., dtype=torch.float16)))
        value.register_buffer('rope', torch.tensor([1., -0.], dtype=torch.float32), persistent=False)
        value.register_buffer('indices', torch.tensor([2, 0], dtype=torch.int64))
        result = model._to_pretrained_gpu_fp32(value, torch.device('cuda:0'))
        self.assertEqual(result.empty.shape, torch.Size([0, 3]))
        self.assertEqual(result.empty.dtype, torch.float32)
        self.assertEqual(result.scalar.shape, torch.Size([]))
        self.assertEqual(int(result.scalar.view(torch.int32)), -2147483648)
        self.assertEqual(result.rope.dtype, torch.float32)
        self.assertEqual(result.indices.dtype, torch.int64)
        self.assertEqual(result.indices.tolist(), [2, 0])
        self.assertNotIn('rope', result.state_dict())
        self.assertEqual(self.transfer.fills, [4])
        self.assertEqual(len(self.transfer.staging_refs), 1)
        self.assertIsNone(self.transfer.staging_refs[0]())
        self.assertEqual({row['dtype'] for row in self.transfer.fallbacks}, {torch.float32, torch.int64})

    def test_noncontiguous_parameter_and_half_buffer_use_original_to_fallback(self):
        value = torch.nn.Module()
        original = torch.arange(6, dtype=torch.float16).reshape(2, 3).transpose(0, 1)
        self.assertFalse(original.is_contiguous())
        value.register_parameter('special', torch.nn.Parameter(original))
        value.register_buffer('half_buffer', torch.tensor([3., -0.], dtype=torch.float16))
        result = model._to_pretrained_gpu_fp32(value, torch.device('cuda:0'))
        self.assertTrue(torch.equal(result.special, original.to(torch.float32)))
        self.assertEqual(result.special.stride(), original.to(torch.float32).stride())
        self.assertTrue(torch.equal(result.half_buffer.view(torch.int32),
                                    torch.tensor([3., -0.], dtype=torch.float32).view(torch.int32)))
        self.assertEqual(self.transfer.staging_refs, [])
        self.assertEqual(self.transfer.copies, [])
        self.assertEqual(len(self.transfer.fallbacks), 2)
        self.assertTrue(any(row['parameter'] and not row['contiguous'] for row in self.transfer.fallbacks))

    def test_staging_is_released_when_blocking_copy_raises(self):
        value = torch.nn.Module()
        value.register_parameter('weight', torch.nn.Parameter(torch.ones(3, dtype=torch.float16)))
        self.transfer.fail_copy = True
        with self.assertRaisesRegex(RuntimeError, 'SYNTHETIC_BLOCKING_TRANSFER_FAILURE'):
            model._to_pretrained_gpu_fp32(value, torch.device('cuda:0'))
        self.assertEqual(value.weight.dtype, torch.float16)
        self.assertEqual(len(self.transfer.staging_refs), 1)
        self.assertIsNone(self.transfer.staging_refs[0]())


if __name__ == '__main__':
    unittest.main()
