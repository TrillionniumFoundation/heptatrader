from __future__ import annotations

from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'research'/'python'))
from hepta_research.order_flow import run_order_flow
from hepta_research import order_flow
from order_flow_fixture import events, order, write_fixture


class OrderFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hepta-flow-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest, self.sources = write_fixture(self.root)

    def doc(self):
        return json.loads(self.manifest.read_text())

    def store(self, document):
        self.manifest.write_text(json.dumps(document))

    def source_bytes(self, data):
        self.sources['flow0'].write_bytes(data)
        doc = self.doc()
        doc['sources'][0]['sha256'] = hashlib.sha256(data).hexdigest()
        self.store(doc)

    def command(self, output, *extra):
        env = dict(os.environ, PYTHONPATH=str(ROOT/'research'/'python'), PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, '-B', '-m', 'hepta_research.order_flow',
            '--manifest', str(self.manifest), '--source', 'flow0='+str(self.sources['flow0']),
            '--output', str(output), *extra], env=env, cwd=self.root,
            text=True, capture_output=True, timeout=15)

    def test_actual_fixture_report(self):
        report = run_order_flow(self.manifest, self.sources)
        self.assertEqual(report['final']['equity'], D('1011.2'))
        self.assertEqual(report['final']['fees'], D('.8'))
        self.assertEqual(report['final']['realized_pnl'], 6)
        self.assertEqual(report['final']['unrealized_pnl'], 6)
        self.assertEqual(report['active_order_count'], 1)
        self.assertEqual(len(report['fills']), 4)
        self.assertFalse(report['assumptions']['broker_authorized'])
        self.assertFalse(report['assumptions']['margin_model'])
        self.assertFalse(report['assumptions']['exchange_settlement'])
        self.assertNotIn(str(self.root), json.dumps(report, default=str))

    def test_every_split_preserves_queues_identity_and_accounting(self):
        expected = run_order_flow(self.manifest, self.sources)
        expected.pop('input')
        for split in range(1, len(events())):
            with self.subTest(split=split):
                manifest, sources = write_fixture(self.root, split)
                actual = run_order_flow(manifest, sources)
                actual.pop('input')
                self.assertEqual(expected, actual)

    def test_cli_success(self):
        output = self.root/'report.json'
        result = self.command(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(output.read_text())
        self.assertEqual(report['final']['equity'], '1011.2')
        self.assertEqual(report['final']['fees'], '0.8')
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_digest_mismatch_preserves_old_report(self):
        output = self.root/'report.json'
        output.write_bytes(b'previous report')
        self.sources['flow0'].write_bytes(b'changed')
        result = self.command(output)
        self.assertEqual(result.returncode, 2)
        self.assertIn('SHA-256', result.stderr)
        self.assertEqual(output.read_bytes(), b'previous report')
        self.assertEqual(list(self.root.glob('.hepta-report-*')), [])

    def test_bad_late_event_preserves_old_report(self):
        output = self.root/'report.json'
        output.write_bytes(b'previous report')
        self.source_bytes(self.sources['flow0'].read_bytes()+b'{"kind":"not_supported"}\n')
        result = self.command(output)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output.read_bytes(), b'previous report')

    def test_input_output_alias_rejected(self):
        before = self.sources['flow0'].read_bytes()
        result = self.command(self.sources['flow0'])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, self.sources['flow0'].read_bytes())

    def test_hardlink_alias_rejected(self):
        link = self.root/'output.json'
        os.link(self.sources['flow0'], link)
        before = link.read_bytes()
        self.assertEqual(self.command(link).returncode, 2)
        self.assertEqual(link.read_bytes(), before)

    def test_manifest_output_alias_rejected(self):
        before = self.manifest.read_bytes()
        self.assertEqual(self.command(self.manifest).returncode, 2)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_final_source_symlink_rejected(self):
        link = self.root/'link.jsonl'
        link.symlink_to(self.sources['flow0'])
        with self.assertRaises(OSError):
            run_order_flow(self.manifest, {'flow0': link})

    def test_fifo_rejected_without_blocking(self):
        fifo = self.root/'input.fifo'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            run_order_flow(self.manifest, {'flow0': fifo})

    def test_manifest_symlink_rejected(self):
        link = self.root/'manifest-link'
        link.symlink_to(self.manifest)
        with self.assertRaises(OSError):
            run_order_flow(link, self.sources)

    def test_byte_budget(self):
        with self.assertRaises(ValueError):
            run_order_flow(self.manifest, self.sources, max_input_bytes=10)

    def test_shared_cross_file_byte_budget(self):
        manifest, sources = write_fixture(self.root, 5)
        budget = max(path.stat().st_size for path in sources.values())
        with self.assertRaises(ValueError):
            run_order_flow(manifest, sources, max_input_bytes=budget)

    def test_event_budget_not_reset_at_file_boundary(self):
        manifest, sources = write_fixture(self.root, 8)
        with self.assertRaises(ValueError):
            run_order_flow(manifest, sources, max_events=10)

    def test_sequence_not_reset_at_file_boundary(self):
        manifest, sources = write_fixture(self.root, 8)
        rows = sources['flow1'].read_bytes().splitlines()
        first = json.loads(rows[0])
        first['seq'] = 1
        rows[0] = json.dumps(first).encode()
        data = b'\n'.join(rows)+b'\n'
        sources['flow1'].write_bytes(data)
        doc = json.loads(manifest.read_text())
        doc['sources'][1]['sha256'] = hashlib.sha256(data).hexdigest()
        manifest.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, 'regression'):
            run_order_flow(manifest, sources)

    def test_exact_source_binding_set(self):
        for sources in [{}, {**self.sources, 'extra': self.sources['flow0']}, {'wrong': self.sources['flow0']}]:
            with self.subTest(sources=list(sources)), self.assertRaises(ValueError):
                run_order_flow(self.manifest, sources)

    def test_duplicate_instrument_rejected(self):
        doc = self.doc()
        doc['instruments'].append(deepcopy(doc['instruments'][0]))
        self.store(doc)
        with self.assertRaisesRegex(ValueError, 'duplicate instrument'):
            run_order_flow(self.manifest, self.sources)

    def test_duplicate_source_rejected(self):
        doc = self.doc()
        doc['sources'].append(deepcopy(doc['sources'][0]))
        self.store(doc)
        with self.assertRaisesRegex(ValueError, 'duplicate source'):
            run_order_flow(self.manifest, self.sources)

    def test_strict_manifest_fields(self):
        original = self.doc()
        variants = [{**original, 'broker': 'must_not_be_used'}, {**original, 'schema': 'unknown'},
                    {**original, 'currency': 'usd'}, {**original, 'capital': True},
                    {**original, 'instruments': []}, {**original, 'sources': []},
                    {**original, 'max_mark_age_us': 1.0}]
        for doc in variants:
            self.store(doc)
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                run_order_flow(self.manifest, self.sources)

    def test_duplicate_manifest_json_key_rejected(self):
        self.manifest.write_bytes(self.manifest.read_bytes()[:-1]+b',"capital":"2000"}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            run_order_flow(self.manifest, self.sources)

    def test_duplicate_event_json_key_rejected(self):
        first = json.dumps(events()[0])[:-1]+',"quantity":1}'
        self.source_bytes((first+'\n').encode())
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            run_order_flow(self.manifest, self.sources)

    def test_nonfinite_json_rejected(self):
        for value in ('NaN', 'Infinity', '-Infinity'):
            row = json.dumps(events()[0]).replace('"quantity": 4', '"quantity": '+value)
            self.source_bytes((row+'\n').encode())
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_order_flow(self.manifest, self.sources)

    def test_bad_encoding_and_deep_json_rejected(self):
        for data in [b'\xff\n', b'['*2000+b'0'+b']'*2000+b'\n']:
            self.source_bytes(data)
            with self.assertRaises(ValueError):
                run_order_flow(self.manifest, self.sources)

    def test_empty_blank_and_oversized_rows_rejected(self):
        for data in [b'', b'\n', b'\n\n', b' '*8193+b'\n']:
            self.source_bytes(data)
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                run_order_flow(self.manifest, self.sources)

    def test_crlf_and_missing_final_newline(self):
        expected = run_order_flow(self.manifest, self.sources)
        expected.pop('input')
        original = self.sources['flow0'].read_bytes()
        for data in [original.replace(b'\n', b'\r\n'), original.rstrip(b'\n')]:
            self.source_bytes(data)
            result = run_order_flow(self.manifest, self.sources)
            result.pop('input')
            self.assertEqual(expected, result)

    def test_midline_carriage_return_rejected(self):
        self.source_bytes(self.sources['flow0'].read_bytes().replace(b'"kind"', b'\r"kind"', 1))
        with self.assertRaisesRegex(ValueError, 'line ending'):
            run_order_flow(self.manifest, self.sources)

    def test_only_captured_bytes_are_parsed(self):
        capture = order_flow._capture
        def change_after_capture(path, limit):
            result = capture(path, limit)
            if path == self.sources['flow0']:
                path.write_bytes(b'changed after capture')
            return result
        with patch.object(order_flow, '_capture', side_effect=change_after_capture):
            report = run_order_flow(self.manifest, self.sources)
        self.assertEqual(report['final']['equity'], D('1011.2'))

    def test_unknown_input_event_does_not_publish_partial_output(self):
        row = order(16, 'future', 'RESEARCH', 'BUY', 1, 10)
        row['order_route'] = 'DIRECT_CTP'
        self.source_bytes(self.sources['flow0'].read_bytes()+(json.dumps(row)+'\n').encode())
        output = self.root/'result.json'
        self.assertEqual(self.command(output).returncode, 2)
        self.assertFalse(output.exists())

    def test_repeated_cli_bindings_rejected(self):
        output = self.root/'result.json'
        result = self.command(output, '--source', 'flow0='+str(self.sources['flow0']))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())

    def test_cli_invalid_bounds_fail(self):
        output = self.root/'result.json'
        self.assertEqual(self.command(output, '--max-active-orders', '0').returncode, 2)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
