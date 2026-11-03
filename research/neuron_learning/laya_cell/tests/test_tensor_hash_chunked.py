"""CPU byte parity and copy-lifetime checks; no full model or GPU allocation."""
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace
import unittest
import weakref
from unittest.mock import patch

import torch
from laya_cell import model
from laya_cell.integrity import canonical


def original_hash(tensor):
    value = tensor.detach().cpu().contiguous()
    return hashlib.sha256(canonical({'shape': list(value.shape), 'dtype': str(value.dtype)})
                          + value.view(torch.uint8).numpy().tobytes()).hexdigest()


class SyntheticGpuTensor:
    """Exercise the GPU scheduling branch using CPU storage, never HIP."""
    device = SimpleNamespace(type='cuda')

    def __init__(self, value, copy_to_cpu):
        self.value, self.copy_to_cpu = value, copy_to_cpu

    @property
    def shape(self): return self.value.shape
    @property
    def dtype(self): return self.value.dtype
    def detach(self): return SyntheticGpuTensor(self.value.detach(), self.copy_to_cpu)
    def is_contiguous(self): return self.value.is_contiguous()
    def is_conj(self): return self.value.is_conj()
    def is_neg(self): return self.value.is_neg()
    def numel(self): return self.value.numel()
    def dim(self): return self.value.dim()
    def stride(self, dim): return self.value.stride(dim)
    def view(self, dtype): return SyntheticGpuTensor(self.value.view(dtype), self.copy_to_cpu)
    def reshape(self, *shape): return SyntheticGpuTensor(self.value.reshape(*shape), self.copy_to_cpu)
    def narrow(self, *args): return SyntheticGpuTensor(self.value.narrow(*args), self.copy_to_cpu)
    def cpu(self): return self.copy_to_cpu(self.value)


class ChunkedTensorHashTests(unittest.TestCase):
    def setUp(self):
        initialized = torch.cuda.is_initialized()
        self.gpu_guard = patch.object(torch.cuda, '_lazy_init',
                                      side_effect=AssertionError('GPU initialization forbidden'))
        guard = self.gpu_guard.start()
        self.addCleanup(self.gpu_guard.stop)
        self.addCleanup(lambda: self.assertEqual(torch.cuda.is_initialized(), initialized))
        self.addCleanup(guard.assert_not_called)

    def test_exact_bytes_across_dtypes_and_odd_chunk_boundaries(self):
        base = torch.arange(35, device='cpu').reshape(5, 7)
        for dtype in (torch.bool, torch.uint8, torch.int8, torch.int16, torch.int32,
                      torch.int64, torch.float16, torch.bfloat16, torch.float32,
                      torch.float64, torch.complex64, torch.complex128):
            value = base.to(dtype)
            if value.is_complex():
                value = value + 1j * value
            with self.subTest(dtype=dtype), patch.object(model, '_TENSOR_HASH_CHUNK_BYTES', 17):
                self.assertEqual(model.tensor_hash(value), original_hash(value))
                self.assertEqual(model.tensor_hash(SyntheticGpuTensor(value, lambda part: part.clone())),
                                 original_hash(value))

    def test_empty_shapes_and_storage_offset_keep_original_identity(self):
        for shape in ((0,), (0, 3), (3, 0), (3, 0, 2)):
            for dtype in (torch.uint8, torch.float32, torch.bfloat16):
                with self.subTest(shape=shape, dtype=dtype):
                    value = torch.empty(shape, dtype=dtype, device='cpu')
                    self.assertEqual(model.tensor_hash(value), original_hash(value))
        value = torch.arange(40, dtype=torch.float64, device='cpu')[3:31].reshape(4, 7)
        self.assertTrue(value.is_contiguous())
        self.assertGreater(value.storage_offset(), 0)
        with patch.object(model, '_TENSOR_HASH_CHUNK_BYTES', 17):
            self.assertEqual(model.tensor_hash(value), original_hash(value))
        self.assertNotEqual(model.tensor_hash(value), model.tensor_hash(value.reshape(7, 4)))

    def test_noncontiguous_cpu_uses_original_logical_order(self):
        source = torch.arange(80, dtype=torch.float32, device='cpu').reshape(8, 10)
        value = source[1::2, 2::3].transpose(0, 1)
        before = source.clone()
        self.assertFalse(value.is_contiguous())
        with patch.object(model, '_TENSOR_HASH_CHUNK_BYTES', 17):
            self.assertEqual(model.tensor_hash(value), original_hash(value))
        torch.testing.assert_close(source, before)

    def test_synthetic_noncontiguous_gpu_keeps_legacy_host_normalization(self):
        value = torch.arange(40, dtype=torch.float32, device='cpu').reshape(5, 8).transpose(0, 1)
        copies = []

        def copy_to_cpu(part):
            copies.append(part.shape)
            return part.clone()

        fake_gpu = SyntheticGpuTensor(value, copy_to_cpu)
        self.assertFalse(fake_gpu.is_contiguous())
        self.assertEqual(model.tensor_hash(fake_gpu), original_hash(value))
        self.assertEqual(copies, [value.shape])

    def test_contiguous_with_unusual_last_stride_keeps_old_error(self):
        value = torch.as_strided(torch.arange(3, dtype=torch.float32, device='cpu'),
                                (3, 1), (1, 3))
        self.assertTrue(value.is_contiguous())
        self.assertNotEqual(value.stride(-1), 1)
        with self.assertRaises(RuntimeError) as original:
            original_hash(value)
        with self.assertRaises(type(original.exception)) as streamed:
            model.tensor_hash(value)
        self.assertEqual(str(streamed.exception), str(original.exception))

    def test_scalar_success_and_errors_remain_original(self):
        for dtype in (torch.uint8, torch.int8, torch.bool):
            with self.subTest(dtype=dtype):
                value = torch.tensor(1, dtype=dtype, device='cpu')
                self.assertEqual(model.tensor_hash(value), original_hash(value))
        for dtype in (torch.float32, torch.float64, torch.complex64):
            with self.subTest(dtype=dtype):
                value = torch.tensor(1, dtype=dtype, device='cpu')
                with self.assertRaises(RuntimeError) as original:
                    original_hash(value)
                with self.assertRaises(type(original.exception)) as streamed:
                    model.tensor_hash(value)
                self.assertEqual(str(streamed.exception), str(original.exception))

    def test_unresolved_conjugate_keeps_original_error(self):
        value = torch.tensor([1 + 2j, 3 - 4j], dtype=torch.complex64, device='cpu').conj()
        self.assertTrue(value.is_conj())
        with self.assertRaises(RuntimeError) as original:
            original_hash(value)
        with self.assertRaises(type(original.exception)) as streamed:
            model.tensor_hash(value)
        self.assertEqual(str(streamed.exception), str(original.exception))

    def test_special_float_bits_are_unchanged(self):
        value = torch.tensor([0., -0., float('nan'), float('inf'), -float('inf')],
                             dtype=torch.float32, device='cpu', requires_grad=True)
        with patch.object(model, '_TENSOR_HASH_CHUNK_BYTES', 3):
            self.assertEqual(model.tensor_hash(value), original_hash(value))
        self.assertTrue(value.requires_grad)
        self.assertIsNone(value.grad)

    def test_real_chunk_bound_and_previous_host_copy_released(self):
        # CPU-backed proxy takes the actual GPU branch, with allocating copies.
        # This checks scheduling/lifetime, not HIP byte parity or full RSS.
        limit = 16 * 1024 * 1024
        self.assertEqual(model._TENSOR_HASH_CHUNK_BYTES, limit)
        value = torch.full((limit + 13,), 23, dtype=torch.uint8, device='cpu')
        expected = hashlib.sha256(canonical({'shape': list(value.shape), 'dtype': str(value.dtype)}))
        expected.update(memoryview(value.numpy()))
        sizes, copies = [], []

        def copying_cpu(part):
            self.assertTrue(all(ref() is None for ref in copies), 'previous host copy is still alive')
            size = part.numel() * part.element_size()
            self.assertLessEqual(size, limit)
            sizes.append(size)
            copied = part.clone()
            copies.append(weakref.ref(copied))
            return copied

        actual = model.tensor_hash(SyntheticGpuTensor(value, copying_cpu))
        self.assertEqual(actual, expected.hexdigest())
        self.assertEqual(sizes, [limit, 13])
        self.assertTrue(all(ref() is None for ref in copies))


class ColdCpuProxyProcessTests(unittest.TestCase):
    def test_loader_and_hash_cases_remain_cold_in_fresh_interpreter(self):
        # The learning lane shares one process with other Torch tests. Preserve
        # the cold-state contract in one exec, with the same visible devices,
        # interpreter and import roots, rather than assuming suite order.
        code = r'''
import json
import sys
import unittest
from unittest.mock import patch

sys.path[:] = json.loads(sys.argv[1])
import torch

case = unittest.TestCase()
case.assertFalse(torch.cuda.is_initialized(), 'fresh interpreter already initialized GPU')
names = (
    'laya_cell.tests.test_laya_loader_memory.LayaLoaderMemoryTests',
    'laya_cell.tests.test_pretrained_complex_fallback.PretrainedComplexFallbackTests',
    'laya_cell.tests.test_tensor_hash_chunked.ChunkedTensorHashTests',
    'laya_cell.tests.test_contracts.AdapterTests.test_toy_rng_is_cpu_only_deterministic_and_restored',
)
with patch.object(torch.cuda, '_lazy_init',
                  side_effect=AssertionError('GPU initialization forbidden in cold subprocess')) as guard:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in names:
        selected = loader.loadTestsFromName(name)
        case.assertGreater(selected.countTestCases(), 0, name)
        suite.addTests(selected)
    case.assertFalse(loader.errors, loader.errors)
    case.assertFalse(torch.cuda.is_initialized(), 'CPU test imports initialized GPU')
    expected = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    guard.assert_not_called()
    case.assertFalse(torch.cuda.is_initialized(), 'CPU proxy tests initialized GPU')
    case.assertEqual(result.testsRun, expected)
    case.assertFalse(result.skipped, result.skipped)
    case.assertTrue(result.wasSuccessful())
print(json.dumps({'tests_run': result.testsRun, 'gpu_initialized': torch.cuda.is_initialized()}))
'''
        result = subprocess.run([sys.executable, '-c', code, json.dumps(sys.path)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertGreater(receipt['tests_run'], 0)
        self.assertIs(receipt['gpu_initialized'], False)


if __name__ == '__main__':
    unittest.main()
