"""Preserve Module.float semantics for fallback buffers without a GPU."""
import copy
import unittest
from unittest.mock import patch
import torch
from laya_cell import model


class PretrainedComplexFallbackTests(unittest.TestCase):
    def test_complex_buffers_keep_dtype_and_imaginary_values(self):
        self.assertFalse(torch.cuda.is_initialized())
        original = torch.nn.Module()
        for dtype, name in ((torch.complex64, 'c64'), (torch.complex128, 'c128')):
            original.register_buffer(name, torch.tensor([1+2j, -3+4j, 0-5j], dtype=dtype, device='cpu'))
        original.register_buffer('floating', torch.tensor([1.25, -2.5], dtype=torch.float64, device='cpu'))
        original.register_buffer('indices', torch.tensor([1, 3], dtype=torch.int64, device='cpu'))
        expected = copy.deepcopy(original).float().to('cpu')
        actual = copy.deepcopy(original)
        tensor_to = torch.Tensor.to
        observed = []
        def transfer(tensor, *args, **kwargs):
            device = kwargs.get('device')
            if device is not None and torch.device(device).type == 'cuda':
                self.assertFalse(args)
                self.assertIs(kwargs.get('non_blocking'), False)
                observed.append((tensor.dtype, kwargs['dtype']))
                return tensor_to(tensor, **dict(kwargs, device='cpu'))
            return tensor_to(tensor, *args, **kwargs)
        with patch.object(torch.cuda, '_lazy_init', side_effect=AssertionError('GPU forbidden')), \
                patch.object(torch.Tensor, 'to', transfer):
            returned = model._to_pretrained_gpu_fp32(actual, torch.device('cuda'))
        self.assertIs(returned, actual)
        self.assertEqual(observed, [(torch.complex64, None), (torch.complex128, None),
                                    (torch.float64, torch.float32), (torch.int64, None)])
        for name, wanted in expected.named_buffers():
            value = actual.get_buffer(name)
            self.assertEqual(value.dtype, wanted.dtype)
            self.assertEqual(value.device.type, 'cpu')
            self.assertTrue(torch.equal(value.view(torch.uint8), wanted.view(torch.uint8)), name)
        self.assertFalse(torch.cuda.is_initialized())


if __name__ == '__main__':
    unittest.main()
