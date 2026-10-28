"""Failure-path CPU fixtures; no Torch, GPU, services or retry."""
import builtins
from contextlib import contextmanager
import errno
import io
import json
import os
import unittest
from unittest.mock import patch

from laya_cell import resident_launch as launch
from laya_cell import resident_worker_failure_context as context


class WorkerFailureContextTests(unittest.TestCase):
    def maps(self, text, truncated=False):
        return patch.object(context, '_read', return_value={'text': text, 'truncated': truncated})

    def test_environment_is_an_explicit_whitelist_and_values_are_bounded(self):
        environment = {'AMD_LOG_LEVEL': '1', 'LD_LIBRARY_PATH': 'a' * 1000,
                       'OPENAI_API_KEY': 'secret', 'AWS_SECRET_ACCESS_KEY': 'another-secret'}
        with patch.dict(os.environ, environment, clear=True):
            result = context._environment()
        self.assertEqual(set(result), set(context.ENVIRONMENT_NAMES))
        self.assertEqual(result['AMD_LOG_LEVEL'], {'value': '1', 'truncated': False})
        self.assertEqual(len(result['LD_LIBRARY_PATH']['value']), 512)
        self.assertTrue(result['LD_LIBRARY_PATH']['truncated'])
        self.assertNotIn('secret', json.dumps(result))

    def test_read_handles_short_reads_and_closes_the_descriptor(self):
        with patch.object(context.os, 'open', return_value=42), \
             patch.object(context.os, 'read', side_effect=[b'ab', b'cd', b'e']) as read, \
             patch.object(context.os, 'close') as close:
            result = context._read('/proc/self/maps', 4)
        self.assertEqual(result, {'text': 'abcd', 'truncated': True})
        self.assertEqual(read.call_count, 3); close.assert_called_once_with(42)

    def test_failed_read_still_closes_the_descriptor(self):
        with patch.object(context.os, 'open', return_value=42), \
             patch.object(context.os, 'read', side_effect=OSError(errno.EIO, 'fixture')), \
             patch.object(context.os, 'close') as close:
            with self.assertRaises(OSError):
                context._read('/proc/self/maps', 4)
        close.assert_called_once_with(42)

    def test_maps_groups_segments_and_preserves_actual_deleted_inode(self):
        text = ('1000-2000 r--p 00000000 08:02 81 /sdk/libamdhip64.so.7\n'
                '2000-3000 r-xp 00001000 08:02 81 /sdk/libamdhip64.so.7\n'
                '3000-4000 r-xp 00000000 08:02 82 /old/libamdhip64.so.7 (deleted)\n'
                '4000-5000 r-xp 00000000 08:02 83 /sdk/libtorch_hip.so\n'
                '5000-6000 rw-p 00000000 00:00 0 [heap]\n')
        with self.maps(text):
            result = context._mappings()
        self.assertEqual(len(result['libraries']), 3)
        hip = result['libraries'][0]; old = result['libraries'][1]
        self.assertEqual((hip['device_major'], hip['device_minor'], hip['inode']), (8, 2, 81))
        self.assertTrue(hip['executable_mapping']); self.assertTrue(old['deleted'])
        self.assertEqual(old['inode'], 82)
        self.assertEqual(result['content_sha256'], 'NOT_MEASURED_ON_FAILURE')
        self.assertNotIn('sha256', hip)

    def test_maps_decodes_proc_path_escaping_and_caps_mapping_groups(self):
        text = ''.join(f'1000-2000 r-xp 0 08:02 {n + 1} /sdk\\040{n}/libnuma.so.1\n'
                       for n in range(40))
        with self.maps(text):
            result = context._mappings()
        self.assertEqual(len(result['libraries']), 32)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['libraries'][0]['path'], '/sdk 0/libnuma.so.1')

    def test_fd_exhaustion_keeps_limit_and_errno_without_raising(self):
        with patch.object(context.resource, 'getrlimit', return_value=(64, 64)), \
             patch.object(context.os, 'scandir', side_effect=OSError(errno.EMFILE, 'fixture')):
            result = context._descriptors()
        self.assertEqual(result['rlimit_nofile'], [64, 64])
        self.assertEqual(result['count']['errno'], errno.EMFILE)

    def test_fd_scan_has_an_explicit_bound_and_marks_its_own_descriptor(self):
        @contextmanager
        def entries(_):
            yield iter(range(10000))
        with patch.object(context.os, 'scandir', entries):
            result = context._descriptor_count()
        self.assertEqual(result['entries_while_scanner_open'], 4096)
        self.assertTrue(result['truncated']); self.assertTrue(result['includes_scanner_descriptor'])

    def test_cgroup_path_cannot_escape_the_fixed_mount(self):
        for path in ('/../elsewhere', '/one/../../elsewhere', 'relative'):
            with self.subTest(path=path), self.maps('0::' + path + '\n') as read:
                self.assertIn('unavailable', context._cgroup())
                read.assert_called_once_with('/proc/self/cgroup', 16384)

    def test_cgroup_collects_only_bounded_fixed_files_and_keeps_read_errors(self):
        seen = []
        def read(path, limit):
            seen.append((path, limit))
            if path == '/proc/self/cgroup':
                return {'text': '0::/user.slice/worker.scope\n', 'truncated': False}
            if path.endswith('memory.peak'):
                raise PermissionError(errno.EACCES, 'confinement fixture')
            return {'text': 'low 0\nhigh 4\n', 'truncated': False}
        with patch.object(context, '_read', read):
            result = context._cgroup()
        self.assertEqual(len(seen), 7)
        self.assertTrue(all(p.startswith('/sys/fs/cgroup/user.slice/worker.scope/') and n == 4096
                            for p, n in seen[1:]))
        self.assertEqual(result['files']['memory.peak']['errno'], errno.EACCES)

    def test_previous_hashes_are_references_and_never_failure_time_hashes(self):
        path = '/sdk/libamdhip64.so.7'; expected = '7' * 64
        state = {'hip_context': {'manifest_sha256': 'f' * 64,
                 'proof': {'candidate_path': path, 'candidate_sha256': expected,
                           'original_dependencies': {}},
                 'snapshots': {path: (os.makedev(8, 2), 81, 100, 101, 102, 0, 1000, 1000, 1)}},
                 'after_torch_proved': True}
        result = context._prepared_reference(state)
        reference = result['libraries']['libamdhip64.so']
        self.assertEqual(reference['previously_verified_sha256'], expected)
        self.assertEqual(reference['previously_verified_file_identity']['inode'], 81)
        self.assertIn('NOT_FAILURE_TIME_HASHING', result['scope'])
        self.assertTrue(result['after_torch_mapping_proof_completed'])

    def test_collection_uses_no_torch_and_one_unavailable_section_does_not_hide_others(self):
        real_import = builtins.__import__
        def cpu_import(name, *args, **kwargs):
            self.assertNotIn(name.split('.')[0], ('torch', 'triton'))
            return real_import(name, *args, **kwargs)
        with patch('builtins.__import__', cpu_import), \
             patch.object(context, '_mappings', side_effect=PermissionError(errno.EACCES, 'fixture')), \
             patch.object(context, '_descriptors', return_value={'fixture': True}), \
             patch.object(context, '_cgroup', return_value={'fixture': True}), \
             patch.object(context, '_read', return_value={'text': '', 'truncated': False}):
            result = context.collect({'target': 'train', 'phase': 'RUN_TRAIN'}, RuntimeError('original'))
        self.assertEqual(result['mappings']['errno'], errno.EACCES)
        self.assertEqual(result['descriptors'], {'fixture': True})
        self.assertFalse(result['gpu_probe_performed']); self.assertFalse(result['worker_retry_granted'])

    def test_emit_is_bounded_and_diagnostic_failures_are_suppressed(self):
        stream = io.StringIO()
        with patch.object(context, 'collect', return_value={'oversized': '\x01' * 100000}), \
             patch.object(context.sys, 'stderr', stream):
            context.emit({}, RuntimeError('original'))
        self.assertLess(len(stream.getvalue().encode()), context.MAX_OUTPUT_BYTES)
        self.assertEqual(json.loads(stream.getvalue()[len(context.PREFIX):])['unavailable'],
                         'SNAPSHOT_EXCEEDS_OUTPUT_BOUND')
        with patch.object(context, 'collect', side_effect=MemoryError):
            self.assertIsNone(context.emit({}, RuntimeError('original')))
        with patch.object(context, 'collect', return_value={}), \
             patch.object(context.sys, 'stderr') as stderr:
            stderr.write.side_effect = BrokenPipeError
            self.assertIsNone(context.emit({}, RuntimeError('original')))

    def test_output_bound_includes_prefix_and_newline(self):
        value = {'x': 'a' * (context.MAX_OUTPUT_BYTES - 8)}
        self.assertEqual(len(json.dumps(value, separators=(',', ':'))), context.MAX_OUTPUT_BYTES)
        stream = io.StringIO()
        with patch.object(context, 'collect', return_value=value), patch.object(context.sys, 'stderr', stream):
            context.emit({}, RuntimeError('original'))
        self.assertLessEqual(len(stream.getvalue().encode()), context.MAX_OUTPUT_BYTES)
        self.assertIn('SNAPSHOT_EXCEEDS_OUTPUT_BOUND', stream.getvalue())

    def test_success_never_collects_or_emits(self):
        with patch.object(launch, '_main', return_value=23), patch.object(context, 'emit') as emit:
            self.assertEqual(launch.main(), 23)
        emit.assert_not_called()

    def test_launch_preserves_original_exception_if_diagnostics_fail(self):
        original = RuntimeError('original worker error')
        for diagnostic_error in (MemoryError(), RuntimeError('diagnostic error')):
            with self.subTest(error=type(diagnostic_error).__name__), \
                 patch.object(launch, '_main', side_effect=original), \
                 patch.object(context, 'emit', side_effect=diagnostic_error) as emit:
                with self.assertRaises(RuntimeError) as raised:
                    launch.main()
                self.assertIs(raised.exception, original)
                self.assertIs(emit.call_args.args[1], original)

    def test_intentional_exit_is_not_reclassified_as_a_worker_exception(self):
        for error in (SystemExit(2), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), patch.object(launch, '_main', side_effect=error), \
                 patch.object(context, 'emit') as emit:
                with self.assertRaises(type(error)) as raised:
                    launch.main()
                self.assertIs(raised.exception, error)
                emit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
