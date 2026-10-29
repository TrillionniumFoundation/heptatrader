"""The actual driver uses bounded pure reads without another mutation channel."""
import importlib.util
import contextlib
import hashlib
import io
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hepta_native_reader as transport
import heptatrader_attributed_driver as attributed
import test_pnl_native_stream as stream_fixtures

ROOT = Path(__file__).resolve().parents[2]


class DriverNativeReaderTests(unittest.TestCase):
    calls = stream_fixtures.NativeStreamTests.calls
    mode = stream_fixtures.NativeStreamTests.mode

    def setUp(self):
        # The same disposable CLI fixture exercises the shared transport. It
        # never knows a broker socket or a real token's contents.
        stream_fixtures.NativeStreamTests.setUp(self)
        self.reader.close()
        fixture = self.root / 'bin/heptactl'
        fixture.write_text(fixture.read_text().replace("=='2000'", "=='4000'").replace(
            'payload=fixture[tool].copy()',
            "payload=fixture[tool].copy();payload['read_number']=counts[tool]"))
        spec = importlib.util.spec_from_file_location(
            'native_reader_driver', ROOT / 'scripts/heptatrader_paper_driver.py')
        self.d = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.d)
        self.reader = transport.NativeReader(self.root, self.root, Path('/unused-socket'),
            persistent=True, parse_json=self.d.strict_json, error_type=self.d.DriverError,
            timeout_seconds=15, io_timeout_ms=4000)
        self.addCleanup(self.reader.close)
        self.d.NATIVE_READER = self.reader

    def test_driver_reuses_one_process_but_obtains_every_fresh_business_read(self):
        for sequence in (1, 2):
            for tool in ('system.get_health', 'orders.list', 'portfolio.list_positions', 'market.get_quote'):
                fields = ('instrument=EUR.USD',) if tool == 'market.get_quote' else ()
                value = self.d.native('/unused-cli', tool, *fields)
                self.assertEqual(value['read_number'], sequence)
        calls = self.calls()
        self.assertEqual(len([call for call in calls if call[0] == 'START']), 1)
        self.assertEqual(len([call for call in calls if call[0] == 'CALL']), 8)
        self.assertEqual({call[2] for call in calls if call[0] == 'START'}, {'paper.token'})

    def test_crypto_owner_uses_its_own_token_and_process(self):
        self.d.native('/unused-cli', 'orders.list')
        self.d.INSTRUMENT = 'BTC.USD'
        self.d.native('/unused-cli', 'orders.list')
        calls = [call for call in self.calls() if call[0] == 'START']
        self.assertEqual({call[2] for call in calls}, {'paper.token', 'crypto.token'})
        self.assertEqual(len({call[1] for call in calls}), 2)

    def test_stream_refusal_is_not_retried_or_replaced_with_single_call_fallback(self):
        for mode, reason in (('schema', 'SCHEMA_HASH_MISMATCH'), ('mismatch', 'TOOL_MISMATCH'), ('eof', 'CLOSED')):
            with self.subTest(mode=mode), patch.object(self.d, 'run') as fallback:
                self.mode(mode)
                with self.assertRaisesRegex(self.d.DriverError, reason):
                    self.d.native('/unused-cli', 'orders.list')
                self.assertEqual(self.reader._processes, {})
                fallback.assert_not_called()
        self.assertEqual(len([call for call in self.calls() if call[0] == 'CALL']), 3)

    def test_wrong_instrument_or_extra_fields_never_cross_stream(self):
        with patch.object(transport.subprocess, 'Popen') as start:
            for tool, fields in (('market.get_quote', ('instrument=BTC.USD',)),
                                 ('market.get_quote', ('instrument=SPY',)),
                                 ('system.get_health', ('scope=other',))):
                with self.assertRaises(self.d.DriverError):
                    self.d.native('/unused-cli', tool, *fields)
            start.assert_not_called()

    def test_non_object_response_discards_the_original_reader(self):
        self.reader.parse_json = lambda _: []
        with self.assertRaisesRegex(self.d.DriverError, 'RESPONSE_INVALID'):
            self.d.native('/unused-cli', 'orders.list')
        self.assertEqual(self.reader._processes, {})
        self.assertEqual(len([call for call in self.calls() if call[0] == 'CALL']), 1)

    def test_risk_preview_and_command_tools_keep_their_original_transport(self):
        response = subprocess.CompletedProcess([], 0, json.dumps({'status': 'ok', 'payload': {}}), '')
        with patch.object(self.d, 'run', return_value=response) as original, \
                patch.object(self.reader, '_stream_read') as stream:
            self.d.native('/original-cli', 'risk.get_limits')
            self.d.native('/original-cli', 'risk.preview_order', 'instrument=EUR.USD')
            self.assertEqual(original.call_count, 2)
            self.assertTrue(all(call.args[0][:2] == ['/original-cli', 'native']
                                for call in original.call_args_list))
            stream.assert_not_called()
        for tool in ('trade.place_order', 'trade.cancel_order', 'trade.flatten_position', 'risk.preview_order'):
            with self.assertRaisesRegex(self.d.DriverError, 'ALLOWLIST'):
                self.reader('EUR.USD', tool)


class AttributedReaderBindingTests(unittest.TestCase):
    def test_wrapper_supplies_the_verified_helper_and_manifest_mode_to_base(self):
        seen = []
        driver = SimpleNamespace(main=lambda: seen.append(list(sys.argv)) or 7)
        verified_reader = object()
        manifest = {'instrument': 'EUR.USD', 'journal_directory': '/unused-journal',
                    'native_read_stream': True,
                    'native_read_binary': {'path': '/qualified/bin/heptactl', 'sha256': 'd' * 64},
                    'runtime_sha256': {'heptatrader_paper_driver.py': 'a' * 64,
                                       'hepta_native_reader.py': 'b' * 64}}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', ['driver', '--manifest', '/unused-manifest']))
            stack.enter_context(patch.object(attributed.os, 'umask'))
            stack.enter_context(patch.object(attributed, 'load_manifest', return_value=(manifest, 'c' * 64)))
            load = stack.enter_context(patch.object(attributed, 'load_verified_module',
                side_effect=[driver, SimpleNamespace(NativeReader=verified_reader,
                    validate_binary_binding=transport.validate_binary_binding)]))
            stack.enter_context(patch.object(attributed, 'Journal', side_effect=OSError('fixture')))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            self.assertEqual(attributed.main(), 7)
            self.assertIs(driver.NATIVE_READER_CLASS, verified_reader)
            self.assertEqual(driver.NATIVE_READ_BINARY, manifest['native_read_binary'])
            helper_call = load.call_args_list[1]
            self.assertEqual(helper_call.args, ('hepta_attributed_pinned_reader',
                Path(attributed.__file__).resolve().parent / 'hepta_native_reader.py', 'b' * 64))
            self.assertIn('--native-read-stream', seen[0])

    def test_explicit_stream_mode_cannot_load_an_unpinned_helper(self):
        manifest = {'instrument': 'EUR.USD', 'runtime_sha256': {'heptatrader_paper_driver.py': 'a' * 64}}
        with patch.object(sys, 'argv', ['driver', '--manifest', '/unused-manifest', '--native-read-stream']), \
                patch.object(attributed.os, 'umask'), \
                patch.object(attributed, 'load_manifest', return_value=(manifest, 'c' * 64)), \
                patch.object(attributed, 'load_verified_module') as load:
            with self.assertRaisesRegex(ValueError, 'NATIVE_READER_RUNTIME_NOT_PINNED'):
                attributed.main()
            load.assert_not_called()


class PinnedReadBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hepta-read-binary-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / 'qualified-heptactl'
        self.payload = b'fixture executable identity; never executed by this test\n'
        self.binary.write_bytes(self.payload)
        self.binary.chmod(0o500)
        self.binding = {'path': str(self.binary), 'sha256': hashlib.sha256(self.payload).hexdigest()}

    def reader(self, binding=None, **kwargs):
        return transport.NativeReader(self.root, self.root, Path('/original-owner-socket'),
            persistent=True, parse_json=json.loads,
            binary_binding=self.binding if binding is None else binding, **kwargs)

    def test_spawn_executes_verified_inode_even_if_selected_path_is_replaced(self):
        def launch(args, **options):
            self.assertEqual(args[0], str(self.binary))
            self.assertEqual(args[-1], 'pnl-read-stream')
            self.assertEqual(args[args.index('--socket') + 1], '/original-owner-socket')
            self.assertEqual(args[args.index('--token-file') + 1], str(self.root / 'paper.token'))
            fd, = options['pass_fds']
            self.assertEqual(options['executable'], '/proc/self/fd/' + str(fd))
            replacement = self.root / 'replacement'
            replacement.write_bytes(b'other executable')
            replacement.chmod(0o500)
            os.replace(replacement, self.binary)
            self.assertEqual(os.read(fd, len(self.payload)), self.payload)
            self.bound_fd = fd
            return 'disposable-process-fixture'
        with patch.object(transport.subprocess, 'Popen', side_effect=launch):
            self.assertEqual(self.reader()._start_stream(self.root / 'paper.token'),
                             'disposable-process-fixture')
        with self.assertRaises(OSError):
            os.fstat(self.bound_fd)

    def test_each_new_stream_verifies_the_selected_hash_again(self):
        reader = self.reader()
        with patch.object(transport.subprocess, 'Popen', return_value='fixture') as start:
            reader._start_stream(self.root / 'paper.token')
            self.binary.chmod(0o600)
            self.binary.write_bytes(b'changed executable')
            self.binary.chmod(0o500)
            with self.assertRaisesRegex(ValueError, 'NATIVE_READ_BINARY_CHANGED'):
                reader._start_stream(self.root / 'paper.token')
            self.assertEqual(start.call_count, 1)

    def test_invalid_path_hash_permissions_or_symlink_cannot_start(self):
        linked = self.root / 'linked'
        linked.symlink_to(self.binary)
        writable = self.root / 'writable'
        writable.write_bytes(self.payload)
        writable.chmod(0o722)
        bindings = [dict(self.binding, path='relative/heptactl'),
                    dict(self.binding, sha256='not-a-digest'),
                    dict(self.binding, sha256='0' * 64),
                    dict(self.binding, path=str(linked)),
                    dict(self.binding, path=str(writable))]
        with patch.object(transport.subprocess, 'Popen') as start:
            for binding in bindings:
                with self.subTest(binding=binding), self.assertRaises(ValueError):
                    self.reader(binding)._start_stream(self.root / 'paper.token')
            start.assert_not_called()

    def test_selected_binary_is_not_available_to_single_call_transport(self):
        with self.assertRaisesRegex(ValueError, 'REQUIRES_STREAM'):
            transport.NativeReader(self.root, self.root, Path('/unused'),
                persistent=False, parse_json=json.loads, binary_binding=self.binding)

    def assert_fifo_refused_without_blocking(self, replace_regular):
        # The positive regular-file identity/hash path remains usable.
        with transport.verified_binary(self.binding) as fd:
            self.assertEqual(os.read(fd, len(self.payload)), self.payload)
        fifo = self.root / 'reader-fifo'
        os.mkfifo(fifo, 0o600)
        binding = self.binding if replace_regular else dict(self.binding, path=str(fifo))
        child = os.fork()
        if child == 0:
            signal.alarm(1)  # A regressed blocking open cannot hang the suite.
            original_open = os.open
            def swap(path, flags, *args, **kwargs):
                if Path(path) == self.binary:
                    os.replace(fifo, self.binary)
                return original_open(path, flags, *args, **kwargs)
            try:
                with patch.object(transport.os, 'open', side_effect=swap) if replace_regular else contextlib.nullcontext():
                    with transport.verified_binary(binding):
                        pass
            except ValueError as error:
                os._exit(0 if str(error) == 'NATIVE_READ_BINARY_IDENTITY_INVALID' else 2)
            except BaseException:
                os._exit(3)
            os._exit(4)
        _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFEXITED(status), 'verified open blocked on the owned FIFO')
        self.assertEqual(os.WEXITSTATUS(status), 0)

    @unittest.skipUnless(hasattr(os, 'fork') and hasattr(os, 'mkfifo'), 'POSIX FIFO fixture')
    def test_fifo_binary_is_rejected_before_open_can_block(self):
        self.assert_fifo_refused_without_blocking(False)

    @unittest.skipUnless(hasattr(os, 'fork') and hasattr(os, 'mkfifo'), 'POSIX FIFO fixture')
    def test_regular_binary_replaced_with_fifo_cannot_block(self):
        self.assert_fifo_refused_without_blocking(True)


class PendingReaderCleanupTests(unittest.TestCase):
    def test_unreaped_original_blocks_replacement_and_keeps_its_handles(self):
        class Process:
            released = False
            stdin, stdout, stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
            def poll(self):
                return 0 if self.released else None
            def terminate(self):
                pass
            def kill(self):
                pass
            def wait(self, timeout):
                if not self.released:
                    raise subprocess.TimeoutExpired('owned-reader-fixture', timeout)
                return 0
        process = Process()
        reader = transport.NativeReader(Path('/unused'), Path('/unused'), Path('/unused'),
            persistent=True, parse_json=json.loads)
        reader._processes['EUR.USD'] = process
        try:
            with patch.object(transport.subprocess, 'Popen') as start:
                with self.assertRaisesRegex(ValueError, 'CLEANUP_PENDING'):
                    reader._close_process('EUR.USD')
                self.assertIs(reader._processes['EUR.USD'], process)
                self.assertFalse(process.stdin.closed)
                self.assertFalse(process.stdout.closed)
                self.assertFalse(process.stderr.closed)
                with self.assertRaisesRegex(ValueError, 'CLEANUP_PENDING'):
                    reader('EUR.USD', 'orders.list')
                start.assert_not_called()
        finally:
            process.released = True
            reader.close()
        self.assertEqual(reader._processes, {})
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)


if __name__ == '__main__':
    unittest.main()
