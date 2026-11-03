"""Full existing candidate/row/signature gates behind the new precheck adapter.

Live process observation and staged source selection have separate tests. Here
the real native registrar backend owns all disk/candidate/seal validation.
"""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import precheck_current100k_registration as precheck
from historical_training.tests import test_current100k_registration_atomic as fixtures
from laya_cell import current100k_forward as forward
from laya_cell import financial_forward_witness as witness
from laya_cell.integrity import ContractError, sha_file


class RegistrationBackendIntegrationTests(unittest.TestCase):
    scope_identifiers = fixtures.AtomicRegistrationTests.scope_identifiers
    complete_candidate = fixtures.AtomicRegistrationTests.complete_candidate
    prepare_new = fixtures.AtomicRegistrationTests.prepare_new
    rebind_dataset = fixtures.AtomicRegistrationTests.rebind_dataset

    def setUp(self):
        fixtures.AtomicRegistrationTests.setUp(self)
        self.directory = self.prior['directory']; self.dataset = self.prior['dataset']
        self.config['producer_registration'] = {'manifest': str(self.prior['manifest']),
                                                'sha256': sha_file(self.prior['manifest'])}
        self.request = {'candidate_directory': str(self.directory), 'dataset_path': str(self.dataset),
            'prior_candidate_path': str(self.prior['directory'] / 'candidate.json'),
            'legacy_plans_roots': [str(self.history)], 'prior_plan_path': str(self.prior_path),
            'input_start_delay_ms': 900000, 'registration_not_before_ms': 1, 'expected_trial': 2}
        for name, kwargs in [('check_precheck_source', {}),
                ('registration_inputs', {'return_value': (self.request, self.prior['plan'])}),
                ('running_producer', {'side_effect': lambda *_: {
                    'request_activity': {'started_at_ms': int(time.time() * 1000)}}})]:
            patched = patch.object(precheck, name, **kwargs); patched.start(); self.addCleanup(patched.stop)

    def register(self):
        return forward.register(self.config, self.directory, self.dataset,
            self.prior['directory'] / 'candidate.json', [self.history],
            registration_scope=forward.SAME_PRODUCER_REFIT, prior_plan_path=self.prior_path,
            input_start_delay_ms=900000)

    def check(self):
        return precheck.precheck_registration(self.config, Path('/fixture-request.json'), 'a' * 64)[1]

    def test_empty_target_executes_full_original_validation_without_root(self):
        self.assertEqual(self.check()['state'], 'FRESH_REGISTRATION_ELIGIBLE_NOW_NO_WRITE')
        self.assertFalse(self.output.exists())

    def test_invalid_prior_tensor_is_rejected_before_producer_or_root(self):
        (self.prior['directory'] / 'value-head.safetensors').write_bytes(b'changed')
        with self.assertRaisesRegex(ContractError, 'HEAD_OR_ROWS'): self.check()
        precheck.running_producer.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_complete_original_preparation_is_reported_resumable_without_signing(self):
        with patch.object(witness, 'sign_record', side_effect=TimeoutError('fixture interruption')):
            with self.assertRaises(TimeoutError): self.register()
        before = (self.pending / 'plan.json').read_bytes()
        with patch.object(witness, 'sign_record', side_effect=AssertionError('precheck must not sign')):
            proof = self.check()
        self.assertEqual(proof['state'], 'RESUMABLE_PREPARED_REGISTRATION_ELIGIBLE_NOW_NO_WRITE')
        self.assertEqual(proof['prepared_plan_id'], json.loads(before)['plan_id'])
        self.assertEqual((self.pending / 'plan.json').read_bytes(), before)
        self.assertFalse((self.pending / 'registration.json').exists())

    def test_complete_signed_result_is_reobserved_without_new_producer_or_write(self):
        plan = self.register(); folder = self.output / 'plans' / plan['plan_id']
        before = {p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in folder.iterdir()}
        proof = self.check()
        self.assertEqual(proof['state'], 'REGISTRATION_ALREADY_COMPLETE_VERIFIED_NO_WRITE')
        self.assertEqual(proof['plan_id'], plan['plan_id'])
        precheck.running_producer.assert_not_called()
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in folder.iterdir()}, before)

    def test_unknown_published_signature_cannot_be_reclassified_as_clean(self):
        plan = self.register(); seal = self.output / 'plans' / plan['plan_id'] / 'registration.json'
        seal.write_text('{}\n')
        with self.assertRaises(ContractError): self.check()
        precheck.running_producer.assert_not_called()
        self.assertEqual(len(list((self.output / 'plans').glob('*/plan.json'))), 1)


if __name__ == '__main__': unittest.main()
