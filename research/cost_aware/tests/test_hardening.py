"""Additional failure boundaries and deterministic arithmetic properties."""
import copy
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fixtures import *
from hepta_cost_capture import Capture, read_capture
from hepta_cost_replay import compare
from test_capture_replay import small_tape, full_artifact
from test_driver_adapter import Host, FakeCapture
import hepta_cost_driver as adapter

ROOT = Path(__file__).resolve().parents[1]

class DurableTransitionTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.capture = FakeCapture()
        self.cleanup = adapter.attach(self.host, '/no-live-calibration', capture_factory=lambda *a, **k: self.capture)
    def tearDown(self):
        self.cleanup()
    def test_settlement_commit_remains_atomic_without_cost_metadata(self):
        state = {'position': 0, 'pending': {'kind': 'OPEN', 'command_id': 'original'}}
        self.host.inspect_pending(state, '/unused', 'b', 's', 't', 'o', {'position': 1})
        self.assertEqual(len(self.host.writes), 1)
        self.assertIsNone(self.host.writes[0]['pending'])
        self.assertNotIn('cost_entry_forecast', self.host.writes[0])
    def test_recorder_failure_after_settlement_does_not_undo_commit(self):
        self.capture.failed = True
        state = {'position': 1, 'pending': {'kind': 'CLOSE', 'command_id': 'original'}}
        self.host.inspect_pending(state, '/unused', 'b', 's', 't', 'o', {'position': 0})
        self.assertEqual(state['position'], 0)
    def test_model_failure_is_recorded_without_output_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            self.host.run = lambda *a, **k: type('R', (), {'returncode': 1, 'stdout': 'sensitive-output'})()
            with self.assertRaises(self.host.DriverError):
                self.host.model_decision('model', Path(td), [{'observed_at_ms': NOW, 'bid': 100, 'ask': 100, 'mid': 100}], 0)
        events = self.capture.events
        self.assertEqual(events[-1][0], 'MODEL_FAILURE')
        self.assertFalse(events[-1][1]['candidate_accepted'])
        self.assertNotIn('sensitive-output', str(events))

class RecordShapeTests(unittest.TestCase):
    def test_malformed_but_rehashed_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'capture'
            with Capture(path, 'BTC.USD', min_free_bytes=0):
                pass
            row = json.loads(path.read_text())
            row['data'] = []
            row['sha256'] = hashlib.sha256(canonical({k: v for k, v in row.items() if k != 'sha256'}).encode()).hexdigest()
            path.write_text(canonical(row) + '\n')
            with self.assertRaisesRegex(CostError, 'EVENT_SCHEMA'):
                read_capture(path)
    def test_event_name_is_not_arbitrary(self):
        with tempfile.TemporaryDirectory() as td:
            with Capture(Path(td) / 'capture', 'BTC.USD', min_free_bytes=0) as writer:
                with self.assertRaisesRegex(CostError, 'EVENT_SCHEMA'):
                    writer.append('FAKE_SOURCE_APPROVED', {}, at_ms=NOW)
    def test_event_payload_must_be_object(self):
        with tempfile.TemporaryDirectory() as td:
            with Capture(Path(td) / 'capture', 'BTC.USD', min_free_bytes=0) as writer:
                with self.assertRaisesRegex(CostError, 'EVENT_SCHEMA'):
                    writer.append('QUOTE', [], at_ms=NOW)
    def test_duplicate_header_refused_at_write(self):
        with tempfile.TemporaryDirectory() as td:
            with Capture(Path(td) / 'capture', 'BTC.USD', min_free_bytes=0) as writer:
                with self.assertRaisesRegex(CostError, 'HEADER_ORDER'):
                    writer.append('HEADER', {}, at_ms=NOW)
    def test_loosened_permissions_poison_writer(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'capture'
            with Capture(path, 'BTC.USD', min_free_bytes=0) as writer:
                path.chmod(0o644)
                with self.assertRaisesRegex(CostError, 'PERMISSIONS_CHANGED'):
                    writer.append('QUOTE', quote(), at_ms=NOW)
                with self.assertRaisesRegex(CostError, 'POISONED'):
                    writer.append('QUOTE', quote(), at_ms=NOW)
    def test_threaded_capture_retains_one_contiguous_chain(self):
        import threading
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'capture'
            with Capture(path, 'BTC.USD', min_free_bytes=0) as writer:
                failures = []
                def append_many():
                    try:
                        for _ in range(15):
                            writer.append('MODEL_SLOT_SKIPPED', {'not_a_strategy_hold': True})
                    except Exception as exc:
                        failures.append(str(exc))
                threads = [threading.Thread(target=append_many) for _ in range(3)]
                for thread in threads:thread.start()
                for thread in threads:thread.join()
                self.assertEqual(failures, [])
            self.assertEqual(len(read_capture(path)['events']), 46)

    def test_unsupported_capture_instrument(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(CostError):
                Capture(Path(td) / 'capture', 'OTHER.USD', min_free_bytes=0)

class ReplayCausalityTests(unittest.TestCase):
    def test_stale_candidate_blocked_by_all_paths(self):
        tape = small_tape()
        for row in tape['rows']:
            row['at_ms'] += 120000
            row['quote'] = quote(row['at_ms'])
        result = compare(tape, [full_artifact()])
        self.assertTrue(all(path['open_events'] == 0 for path in result['results']))
        self.assertTrue(all('FORECAST_STALE' in path['rejection_or_hold_reasons'] for path in result['results']))
    def test_nonobject_tape_row_rejected(self):
        tape = small_tape()
        tape['rows'][0] = []
        with self.assertRaisesRegex(CostError, 'ROW_SCHEMA'):
            compare(tape)
    def test_cost_equation_matches_executable_cashflow_300_cases(self):
        rng = random.Random(61006)
        for i in range(300):
            instrument = 'BTC.USD' if i % 3 == 0 else 'EUR.USD'
            side = 'BUY' if instrument == 'BTC.USD' or i % 2 == 0 else 'SELL'
            mid = D('80000') + D(rng.randrange(10000)) if instrument == 'BTC.USD' else D('1.05') + D(rng.randrange(10000)) / D('100000')
            q = quote(NOW, instrument, str(mid), str(mid * D(rng.randrange(1, 20)) / D('100000')))
            move = str(rng.randrange(-500, 501))
            result = estimate_round_trip(instrument, side, '25000', q, move, 900)
            sign = D(1 if side == 'BUY' else -1)
            executable = sign * (dec(result['estimated_exit_price']) - dec(result['estimated_entry_price'])) * dec(result['quantity_estimate'])
            net = executable - dec(result['entry_commission_usd']) - dec(result['exit_commission_usd']) - dec(result['holding_reserve_usd'])
            self.assertLess(abs(net - dec(result['net_edge_usd'])), D('1e-18'))

class CliTests(unittest.TestCase):
    def test_replay_command_generates_no_orders(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tape, cal, output = root/'tape.json', root/'cal.json', root/'out.json'
            tape.write_text(json.dumps(small_tape()))
            cal.write_text(json.dumps(full_artifact()))
            result = subprocess.run([sys.executable, str(ROOT/'scripts/hepta_cost_replay.py'), '--tape', str(tape),
                                     '--calibration', str(cal), '--stress', '--output', str(output)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['broker_orders_sent'], 0)
            self.assertEqual(output.stat().st_mode & 0o077, 0)
    def test_remote_oos_cli_never_accepts_calibration_or_refits(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);tape=root/'tape.json';out=root/'remote-oos.json';cal=root/'cal.json'
            data=small_tape();tape.write_text(json.dumps(data));cal.write_text(json.dumps(full_artifact()))
            start=data['rows'][1]['at_ms'];decision_end=data['rows'][50]['at_ms']
            argv=[sys.executable,str(ROOT/'scripts/hepta_cost_replay.py'),'--tape',str(tape),
                  '--remote-oos-start-ms',str(start),'--holdout-decision-end-ms',str(decision_end),
                  '--max-drawdown-usd','1000','--output',str(out)]
            result=subprocess.run(argv,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads(out.read_text())
            self.assertFalse(report['remote_model_refit_performed'])
            self.assertFalse(report['calibration_artifact_used'])
            self.assertFalse(report['local_training_required_for_acceptance'])
            self.assertEqual(report['broker_orders_sent'],0)
            rejected=subprocess.run(argv[:-2]+['--calibration',str(cal),'--output',str(root/'bad.json')],
                                    capture_output=True,text=True,timeout=20)
            self.assertEqual(rejected.returncode,2)
            self.assertIn('never accepts fit/calibration',rejected.stderr)
    def test_cli_refuses_overwriting_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); tape, output = root/'tape.json', root/'out.json'
            tape.write_text(json.dumps(small_tape())); output.write_text('KEEP')
            result = subprocess.run([sys.executable, str(ROOT/'scripts/hepta_cost_replay.py'), '--tape', str(tape), '--output', str(output)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_text(), 'KEEP')
    def test_adapter_requires_explicit_optin(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/hepta_cost_driver.py'), '--base-driver', '/unavailable'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--enable-paper-hooks', result.stderr)

if __name__ == '__main__':
    unittest.main(verbosity=2)
