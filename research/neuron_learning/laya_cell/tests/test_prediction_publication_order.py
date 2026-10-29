"""Synthetic publication ordering and snapshot races; no prospective results."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from laya_cell.financial_forward_evaluate import read_prediction
from laya_cell.financial_forward_predict import publish_prediction
from laya_cell.financial_forward_protocol import score_case
from laya_cell.financial_forward_witness import verify_record
from laya_cell.integrity import ContractError, canonical
from laya_cell.resident_store import write_once
from laya_cell.tests.test_financial_forward import fixture
from laya_cell.tests.test_financial_forward_trust import TrustFixture


class PublicationOrderingTests(TrustFixture):
    def setUp(self):
        super().setUp()
        self.plan, self.binding, prediction, _, self.first_fill, self.observed = fixture()
        self.payload = {key: value for key, value in prediction.items() if key != 'committed_at_ms'}
        self.request = {'request_id': self.binding['request_id'],
                        'started_at_ms': self.binding['request_started_at_ms']}
        self.folder = self.root / 'plans' / self.plan['plan_id'] / 'predictions' / self.request['request_id']
        self.opened = self.binding['command']['created_at_ms']
        self.wall = self.request['started_at_ms'] + 400
        self.events = {}

    def publish(self, *, delay_commit=False):
        def observed_write(path, value):
            name = Path(path).name
            if name == 'commit.json' and delay_commit:
                self.wall = self.opened + 25
            write_once(path, value)
            self.events[name] = self.wall

        with patch('laya_cell.financial_forward_predict.time.time', side_effect=lambda: self.wall / 1000), \
             patch('laya_cell.financial_forward_predict.write_once', side_effect=observed_write):
            return publish_prediction(self.role('predictor'), self.plan, self.request,
                                      self.folder, self.payload)

    def read(self):
        with patch('laya_cell.financial_forward_evaluate.time.time', side_effect=lambda: self.wall / 1000):
            return read_prediction(self.root, self.plan, self.request['request_id'], self.c)

    def test_preopen_payload_can_have_a_postopen_commit_file(self):
        commit = self.publish(delay_commit=True)
        self.assertLess(self.events['prediction.json'], self.opened)
        self.assertLess(commit['role_witness']['signed_at_ms'], self.opened)
        self.assertGreater(self.events['commit.json'], self.opened)
        self.assertEqual(json.loads((self.folder / 'prediction.json').read_bytes()), self.payload)
        prediction = self.read()
        result = score_case(self.plan, self.binding, prediction, None, self.first_fill, self.observed)
        self.assertEqual(result['status'], 'PENDING')
        self.assertEqual(result['reason'], 'ORIGINAL_TRADE_NOT_YET_CLOSED_AND_SETTLED')

    def test_postopen_payload_swap_cannot_borrow_restored_original_hash(self):
        self.publish()
        self.wall = self.opened + 50
        path = self.folder / 'prediction.json'
        original = path.read_bytes()
        changed = {**self.payload, 'values_usd': {'BUY': 9.0, 'SELL': 8.0}}
        path.write_bytes(canonical(changed) + b'\n')

        def restore_during_real_signature_check(config, role, record):
            verified = verify_record(config, role, record)
            path.write_bytes(original)
            return verified

        with patch('laya_cell.financial_forward_evaluate.verify_record',
                   side_effect=restore_during_real_signature_check):
            with self.assertRaisesRegex(ContractError, 'PREDICTION_COMMIT_WITNESS'):
                self.read()

    def test_commit_hash_binds_the_snapshot_that_was_verified(self):
        commit = self.publish()
        path = self.folder / 'commit.json'
        original_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

        def replace_after_real_signature_check(config, role, record):
            verified = verify_record(config, role, record)
            path.write_bytes(canonical({**commit, 'unverified_later_field': True}) + b'\n')
            return verified

        with patch('laya_cell.financial_forward_evaluate.verify_record',
                   side_effect=replace_after_real_signature_check):
            prediction = self.read()
        self.assertEqual(prediction['commit_sha256'], original_sha256)
        self.assertNotEqual(prediction['commit_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__':
    import unittest
    unittest.main(verbosity=2)
