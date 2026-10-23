"""Read worker resource counters without creating or synchronizing GPU work."""
import contextlib
import resource
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import torch
from laya_cell import model


class WorkerResourceUsageTests(unittest.TestCase):
    def counters(self, initialized):
        with contextlib.ExitStack() as stack:
            for name in ('_lazy_init', 'init', 'synchronize', 'reset_peak_memory_stats', 'empty_cache'):
                stack.enter_context(patch.object(torch.cuda, name,
                    side_effect=AssertionError('Telemetry must not start or alter GPU work: ' + name)))
            stack.enter_context(patch.object(torch.cuda, 'is_initialized', return_value=initialized))
            usage = stack.enter_context(patch.object(resource, 'getrusage',
                return_value=SimpleNamespace(ru_maxrss=12345)))
            allocated = stack.enter_context(patch.object(torch.cuda, 'max_memory_allocated', return_value=345))
            reserved = stack.enter_context(patch.object(torch.cuda, 'max_memory_reserved', return_value=678))
            result = model.worker_resource_usage()
            usage.assert_called_once_with(resource.RUSAGE_SELF)
            if initialized:
                allocated.assert_called_once_with()
                reserved.assert_called_once_with()
            else:
                allocated.assert_not_called()
                reserved.assert_not_called()
        self.assertEqual(result['max_process_rss_bytes'], 12345 * 1024)
        self.assertEqual(result['scope'], 'THIS_WORKER_PROCESS_THROUGH_RECEIPT_CREATION')
        self.assertEqual(result['gpu_peak_scope'], 'CURRENT_PROCESS_PYTORCH_ALLOCATOR_STATS')
        self.assertIs(result['rss_and_gpu_bytes_are_not_additive'], True)
        return result

    def test_uninitialized_gpu_reads_only_linux_process_rss(self):
        result = self.counters(False)
        self.assertIsNone(result['max_gpu_allocated_bytes'])
        self.assertIsNone(result['max_gpu_reserved_bytes'])

    def test_initialized_gpu_reads_existing_allocator_counters(self):
        result = self.counters(True)
        self.assertEqual(result['max_gpu_allocated_bytes'], 345)
        self.assertEqual(result['max_gpu_reserved_bytes'], 678)


if __name__ == '__main__':
    unittest.main()
