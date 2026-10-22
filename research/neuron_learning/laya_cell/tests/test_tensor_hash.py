"""CPU byte equivalence and bounded Python allocation for frozen tensor hashes."""
import hashlib
import json
import tracemalloc
import unittest

import torch
from laya_cell.integrity import canonical
from laya_cell.model import tensor_hash


def original_hash(tensor):
    value = tensor.detach().cpu().contiguous()
    return hashlib.sha256(canonical({'shape': list(value.shape), 'dtype': str(value.dtype)})
                          + value.view(torch.uint8).numpy().tobytes()).hexdigest()


class TensorHashTests(unittest.TestCase):
    def test_preserves_original_bytes_across_cpu_dtypes(self):
        base = torch.arange(12, device='cpu').reshape(3, 4)
        dtypes = (torch.bool, torch.uint8, torch.int8, torch.int16, torch.int32,
                  torch.int64, torch.float16, torch.bfloat16, torch.float32,
                  torch.float64, torch.complex64, torch.complex128)
        for dtype in dtypes:
            with self.subTest(dtype=dtype):
                value = base.to(dtype=dtype)
                if dtype in (torch.complex64, torch.complex128):
                    value = value + 1j * value
                self.assertEqual(tensor_hash(value), original_hash(value))

    def test_noncontiguous_cpu_view_has_same_logical_order_and_is_unchanged(self):
        source = torch.arange(48, dtype=torch.float32, device='cpu').reshape(6, 8)
        value = source[::2, 1::2].transpose(0, 1)
        before = source.clone()
        self.assertFalse(value.is_contiguous())
        self.assertEqual(tensor_hash(value), original_hash(value))
        self.assertEqual(tensor_hash(value), tensor_hash(value.contiguous()))
        torch.testing.assert_close(source, before)

    def test_empty_cpu_tensors_keep_shape_and_dtype_identity(self):
        for shape in ((0,), (0, 3), (3, 0), (3, 0, 2)):
            for dtype in (torch.uint8, torch.float32, torch.bfloat16):
                with self.subTest(shape=shape, dtype=dtype):
                    value = torch.empty(shape, dtype=dtype, device='cpu')
                    self.assertEqual(tensor_hash(value), original_hash(value))
        self.assertNotEqual(tensor_hash(torch.empty((0, 3), device='cpu')),
                            tensor_hash(torch.empty((3, 0), device='cpu')))

    def test_metadata_and_special_floating_point_bits_remain_in_hash(self):
        value = torch.tensor([0., -0., float('nan'), float('inf'), -float('inf')],
                             dtype=torch.float32, device='cpu', requires_grad=True)
        self.assertEqual(tensor_hash(value), original_hash(value))
        self.assertTrue(value.requires_grad)
        self.assertIsNone(value.grad)
        raw = torch.arange(6, dtype=torch.uint8, device='cpu')
        self.assertNotEqual(tensor_hash(raw.reshape(2, 3)), tensor_hash(raw.reshape(3, 2)))
        self.assertNotEqual(tensor_hash(raw), tensor_hash(raw.view(torch.int8)))

    def test_streaming_avoids_two_tensor_size_python_byte_copies(self):
        # The input and Torch allocation exist before tracing. Only Python-side
        # hashing copies are compared; this is not a full model/RSS benchmark.
        size = 8 * 1024 * 1024
        value = torch.full((size,), 23, dtype=torch.uint8, device='cpu')
        peaks = {}
        digests = {}
        for name, function in (('original', original_hash), ('streamed', tensor_hash)):
            tracemalloc.start()
            try:
                digests[name] = function(value)
                peaks[name] = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        self.assertEqual(digests['original'], digests['streamed'])
        self.assertGreaterEqual(peaks['original'], 2 * size)
        self.assertLess(peaks['streamed'], 128 * 1024)
        print(json.dumps({'synthetic_cpu_only': True, 'input_bytes': size,
                          'python_tracemalloc_peak_bytes': peaks,
                          'full_model_peak_rss_measured': False}))


if __name__ == '__main__': unittest.main()
