"""On-host integration with the exact real driver module, mocked venue I/O.

No driver main, credentials, broker connection or live order is used by tests.
Calibration flags here are test doubles and never installed in an operator path.
"""
from pathlib import Path
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import hepta_cost_driver as adapter
import hepta_cost_policy as policy

NOW = 1791260000000
BASE = Path(os.environ.get('HEPTA_COST_BASE_DRIVER', str(ROOT.parents[1] / 'scripts/heptatrader_paper_driver.py')))


def quote(instrument='BTC.USD', at=NOW, price='100000'):
    return {'source': 'IB', 'authoritative': True, 'instrument': instrument,
            'subscription_id': 'IB:TEST:1:1', 'subscription_state': 'active',
            'observed_at_ms': at, 'stale_after_ms': at + 5000, 'stale': False,
            'bid': price, 'ask': price}


def forecast(instrument='BTC.USD', at=NOW, move=80, price='100000', action='BUY'):
    mid = policy.dec(price)
    return {'action': action, 'confidence': .8, 'rationale': 'SYNTHETIC TEST ONLY',
            'horizon_sec': 300, 'scenarios': [{'probability': '.5', 'move_bps': str(move-2)},
                                            {'probability': '.5', 'move_bps': str(move+2)}],
            'invalidation_price': str(mid * (policy.D('.999') if action == 'BUY' else policy.D('1.001'))) if action != 'HOLD' else '0',
            'forecast_version': policy.FORECAST_VERSION, 'model_id': policy.MODEL_ID,
            'issued_at_ms': at, 'reference_mid': price, 'instrument': instrument, 'position_neutral': True}


def test_artifact(instrument='BTC.USD', synthetic=False):
    rows = []
    for i in range(40):
        at = NOW - 4*86400000 + i*2*3600000
        move = 20 if i % 2 else 100
        f = forecast(instrument, at, move)
        end = at + 300000
        rows.append({'forecast': f, 'label_observed_at_ms': end, 'label_available_at_ms': end+1,
                     'forecast_available_at_ms': at+1, 'source_kind': 'SYNTHETIC' if synthetic else 'IB_CAPTURE',
                     'realized_underlying_move_bps': str(move-5)})
    return policy.build_calibration(rows, NOW-1000)


class Recorder:
    def __init__(self):
        self.events = []
        self.fail = False
    def append(self, event, data, **kwargs):
        if self.fail:
            raise OSError('test disk failure')
        self.events.append((event, copy.deepcopy(data)))
    def close(self):
        pass


class RealDriverIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.path = self.root / 'state.json'
        self.loaded_base = BASE
        if 'HEPTA_COST_BASE_DRIVER' not in os.environ:
            self.loaded_base = self.root / 'pinned-source-driver.py'
            self.loaded_base.write_bytes(BASE.read_bytes())
            self.loaded_base.chmod(0o600)
        self.d = adapter.load_pinned_driver(self.loaded_base)
        self.d.INSTRUMENT = 'BTC.USD'
        self.d.QTY = 1.
        self.d.QUANTITY_TOLERANCE = 1e-10
        self.d.now_ms = lambda: NOW
        self.state = self.d.load_state(self.path)
        self.state['last_decision'] = forecast()
        self.q = quote()
        self.calls = []
        self.after_prepare = None
        self.submit_kind = 'ok'
        self.snap = {'position': 0., 'active': [], 'owner_health': {'mutation_ready': True}}
        self.record = Recorder()
        self.calibration = self.root / 'test-only-calibration.json'
        self.calibration.write_text(json.dumps(test_artifact()))
        self.calibration.chmod(0o600)
        def native(_cli, tool, *_fields):
            if tool != 'market.get_quote':
                raise AssertionError('unexpected native request: ' + tool)
            self.calls.append(tool)
            return self.q
        def command(_b, _s, _t, _o, operation, extra):
            self.calls.append(operation)
            if operation.startswith('prepare-'):
                if self.after_prepare:
                    self.after_prepare()
                return {'ok': True, 'command_id': 'test-original-command'}
            if operation == 'submit':
                pending = json.loads(self.path.read_text())['pending']
                self.assertEqual(pending['send_state'], 'POSSIBLY_SENT')
                if self.submit_kind == 'uncertain':
                    return {'ok': False, 'transport_ok': False, 'reason': 'TEST_TIMEOUT', 'result': None}
                return {'ok': True, 'transport_ok': True, 'application_status': 'ok',
                        'result': {'status': 'ok', 'order_id': 42}}
            raise AssertionError('unexpected paper operation: ' + operation)
        self.d.native = native
        self.d.paper_cmd = command
        self.d.snapshot = lambda *_a, **_k: self.snap
        self.close = adapter.attach(self.d, self.calibration, capture_factory=lambda *_a, **_k: self.record)

    def tearDown(self):
        self.close()
        self.temp.cleanup()

    def open(self):
        return self.d.prepare_send(self.state, self.path, 'test-binary', 'test-socket', 'test-token',
                                   str(self.root/'outbox'), 'OPEN', self.q, 'BUY')

    def test_exact_actual_source_loaded_without_main(self):
        self.assertEqual(hashlib.sha256(BASE.read_bytes()).hexdigest(), adapter.BASE_DRIVER_SHA256)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.path.exists())

    def test_real_adapter_forecast_keeps_worker_completion_and_input_identity(self):
        import threading
        from types import SimpleNamespace
        entered = threading.Event()
        finish = threading.Event()
        history = [{"observed_at_ms": NOW, "bid": 100000., "ask": 100000., "mid": 100000.}]
        candidate = {key: forecast()[key] for key in adapter.CANDIDATE_SCHEMA["required"]}

        def infer(_argv, _timeout):
            entered.set()
            if not finish.wait(2):
                raise AssertionError("fixture model completion was not released")
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "ok": True, "outputs": [{"text": json.dumps(candidate)}],
            }))

        with patch.object(self.d, "run", side_effect=infer):
            worker = self.d.ModelWorker("fixture-openclaw", self.root, history, 0.)
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(worker.done())
                finish.set()
                self.assertTrue(worker.wait_for_completion(2))
                answer = worker.result()
                self.assertEqual(answer["issued_at_ms"], NOW)
                self.assertTrue(answer["position_neutral"])
                self.assertEqual(answer["forecast_version"], policy.FORECAST_VERSION)
                self.assertEqual(answer["action"], "BUY")
                self.assertEqual(worker.position, 0.)
                self.assertEqual(worker.observed_at_ms, NOW)
                observed = self.d.record_model_completion({}, worker)
                self.assertEqual(observed["input_observed_at_ms"], NOW)
                self.assertEqual(observed["completed_at_ms"], NOW)
                self.assertGreaterEqual(observed["completion_to_consumption_ms"], 0.)
                self.assertEqual([name for name, _ in self.record.events], ["CANDIDATE"])
                self.assertEqual(self.calls, [])  # No native read, prepare or submit.
            finally:
                finish.set()
                worker.close()

    def test_valid_entry_runs_real_prepare_and_one_submit(self):
        self.open()
        self.assertEqual(self.calls.count('prepare-crypto-open'), 1)
        self.assertEqual(self.calls.count('submit'), 1)
        pending = json.loads(self.path.read_text())['pending']
        self.assertEqual(pending['command_id'], 'test-original-command')
        self.assertEqual(pending['send_state'], 'ACKNOWLEDGED')
        self.assertNotIn('cost_forecast', pending)
        self.assertNotIn('cost_admission', pending)
        self.assertNotIn('cost_final_admission', pending)
        self.assertEqual(pending['order_type'], 'MKT')
        self.assertEqual(pending['cash_quantity'], 25000.)

    def test_missing_local_calibration_has_no_resident_effect(self):
        self.calibration.unlink()
        self.open()
        self.assertEqual(self.calls, ['prepare-crypto-open','submit'])
        self.assertNotIn('cost_last_admission', self.state)

    def test_synthetic_local_calibration_has_no_resident_effect(self):
        self.calibration.write_text(json.dumps(test_artifact(synthetic=True)))
        self.open()
        self.assertEqual(self.calls, ['prepare-crypto-open','submit'])
        self.assertNotIn('cost_last_admission', self.state)

    def test_price_move_after_prepare_is_not_a_cost_veto(self):
        self.after_prepare = lambda: self.q.update(bid='101000', ask='101000')
        self.open()
        self.assertEqual(self.calls, ['prepare-crypto-open','submit'])
        self.assertEqual(self.state['pending']['command_id'], 'test-original-command')
        self.assertEqual(self.state['pending']['send_state'], 'ACKNOWLEDGED')

    def test_forecast_expiry_after_prepare_is_not_a_cost_veto(self):
        self.after_prepare = lambda: setattr(self.d, 'now_ms', lambda: NOW + 2*86400000)
        self.open()
        self.assertIn('submit', self.calls)
        self.assertEqual(self.state['pending']['send_state'], 'ACKNOWLEDGED')

    def test_adapter_does_not_add_post_prepare_cost_readiness_veto(self):
        self.after_prepare = lambda: self.snap['owner_health'].update(mutation_ready=False)
        self.open()
        self.assertIn('submit', self.calls)
        self.assertEqual(self.state['pending']['command_id'], 'test-original-command')

    def test_pending_command_cannot_be_replaced(self):
        original = {'kind': 'OPEN', 'command_id': 'preserve-original-242', 'send_state': 'ACKNOWLEDGED'}
        self.state['pending'] = copy.deepcopy(original)
        with self.assertRaises(self.d.DriverError):
            self.open()
        self.assertEqual(self.state['pending'], original)
        self.assertEqual(self.calls, [])

    def test_ack_loss_remains_unknown_no_second_submit(self):
        self.submit_kind = 'uncertain'
        self.open()
        self.assertEqual(self.state['pending']['send_state'], 'POSSIBLY_SENT')
        with self.assertRaises(self.d.DriverError):
            self.open()
        self.assertEqual(self.calls.count('submit'), 1)
        self.assertEqual(self.state['pending']['command_id'], 'test-original-command')

    def test_capture_failure_has_no_resident_entry_authority(self):
        self.record.fail = True
        self.open()
        self.assertEqual(self.calls, ['prepare-crypto-open', 'submit'])

    def test_missing_calibration_and_cooldown_do_not_block_risk_exit(self):
        self.calibration.unlink()
        self.state.update(position=.25, entry_price=100000., cost_last_exit_ms=NOW)
        close, reason = self.d.should_close(self.state, .25, quote(price='99000'), None)
        self.assertTrue(close)
        self.assertEqual(reason, 'MAX_ADVERSE_MOVE')

    def test_inherited_position_retains_original_reversal_rule(self):
        self.state.update(position=.25, entry_price=100000.)
        f = forecast(action='SELL', move=-80)
        close, reason = self.d.should_close(self.state, .25, self.q, f)
        self.assertEqual((close, reason), (True, 'MODEL_REVERSAL'))

    def test_cost_adapter_does_not_add_direct_prepare_quote_ttl_veto(self):
        self.q['stale_after_ms'] = NOW-1
        self.open()
        self.assertEqual(self.calls, ['prepare-crypto-open', 'submit'])



if __name__ == '__main__':
    unittest.main(verbosity=2)
