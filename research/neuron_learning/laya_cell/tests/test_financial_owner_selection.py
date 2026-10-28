"""Transport-contract tests for the existing canonical Model verifier bridge.

Native replies below are mocked only to test the Python boundary. No CURRENT
head or owner credential is minted, and no mocked receipt is deployment evidence.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import financial_forward_select as selection
from laya_cell import native as neuron_native
from laya_cell.integrity import ContractError, digest, sha_file


PIN = '108bc534d33c7f9e48caa569c254253f6c356271'
MANIFEST_DIGEST = '1' * 64
CONTENT_DIGEST = '2' * 64


def manifest_receipt():
    return {'status': 'CANONICAL_MANIFEST_VALID_NOT_OWNER_ADMITTED',
            'manifest_digest': MANIFEST_DIGEST, 'native_source_sha': PIN,
            'canonical_agentd_admitted': False, 'selection_verified': False,
            'effect_authority': False}


def selected_receipt(content=CONTENT_DIGEST):
    return {'status': 'CANONICAL_SIGNED_SELECTION_VERIFIED_READ_ONLY',
            'selection_digest': '3' * 64, 'content_digest': content,
            'registry_head_digest': '4' * 64, 'native_source_sha': PIN,
            'selection_verified': True, 'canonical_agentd_admitted': False,
            'effect_authority': False}


class NativeReceiptContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / 'verifier-placeholder'
        self.binary.write_bytes(b'synthetic transport marker, never executed')
        self.config = {'canonical_verifier_binary': str(self.binary),
                       'canonical_verifier_sha256': sha_file(self.binary)}

    def call(self, receipt, operation='verify_current_selection', returncode=0):
        response = subprocess.CompletedProcess(
            ['synthetic'], returncode, json.dumps(receipt).encode(), b'')
        request = {'operation': operation, 'expected_content_digest': CONTENT_DIGEST}
        with patch.object(selection.subprocess, 'run', return_value=response):
            return selection.native(self.config, request)

    def test_exact_native_structural_and_selected_receipts_are_distinct(self):
        structural = self.call(manifest_receipt(), 'validate_candidate_manifest')
        selected = self.call(selected_receipt())
        self.assertFalse(structural['selection_verified'])
        self.assertTrue(selected['selection_verified'])
        self.assertFalse(selected['canonical_agentd_admitted'])
        self.assertFalse(selected['effect_authority'])

    def test_generic_pass_does_not_replace_typed_native_result(self):
        for status in ('PASS', 'OperatorAccepted',
                       'CANONICAL_MANIFEST_VALID_NOT_OWNER_ADMITTED'):
            with self.subTest(status=status):
                receipt = selected_receipt()
                receipt['status'] = status
                with self.assertRaisesRegex(ContractError, 'RECEIPT_CONTRACT'):
                    self.call(receipt)

    def test_native_authority_and_selection_flags_require_exact_booleans(self):
        for field, invalid_values in {
            'canonical_agentd_admitted': (True, 0, 'false', None),
            'selection_verified': (False, 1, 'true', None),
            'effect_authority': (True, 0, 'false', None),
        }.items():
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    receipt = selected_receipt()
                    receipt[field] = value
                    with self.assertRaises(ContractError):
                        self.call(receipt)

    def test_structural_receipt_cannot_claim_selected_or_owner_admitted(self):
        for field in ('selection_verified', 'canonical_agentd_admitted'):
            receipt = manifest_receipt()
            receipt[field] = True
            with self.subTest(field=field), self.assertRaisesRegex(
                    ContractError, 'RECEIPT_CONTRACT'):
                self.call(receipt, 'validate_candidate_manifest')

    def test_native_receipt_requires_all_full_lowercase_digests(self):
        for field in ('selection_digest', 'content_digest', 'registry_head_digest'):
            for value in (None, 'f' * 63, 'g' * 64, True):
                receipt = selected_receipt()
                receipt[field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(
                        ContractError, 'RECEIPT_DIGEST'):
                    self.call(receipt)
        receipt = manifest_receipt()
        receipt.pop('manifest_digest')
        with self.assertRaisesRegex(ContractError, 'RECEIPT_DIGEST'):
            self.call(receipt, 'validate_candidate_manifest')

    def test_selected_other_content_is_rejected(self):
        with self.assertRaisesRegex(ContractError, 'NO_CANONICAL_SIGNED_SELECTION'):
            self.call(selected_receipt('5' * 64))

    def test_wrong_pin_failed_process_and_nonobject_are_rejected(self):
        for source_pin in ('78fdb0cf8537e3a84fc6e0a849707559c80881e8',
                           'f8c9419df6ddeb36ff8b522a50219981fb8d9345', '0' * 40):
            receipt = selected_receipt()
            receipt['native_source_sha'] = source_pin
            with self.subTest(source_pin=source_pin), self.assertRaisesRegex(
                    ContractError, 'VERIFIER_REJECTED'):
                self.call(receipt)
        with self.assertRaisesRegex(ContractError, 'VERIFIER_REJECTED'):
            self.call(selected_receipt(), returncode=2)
        with self.assertRaisesRegex(ContractError, 'VERIFIER_REJECTED'):
            self.call([])

    def test_binary_change_is_detected_before_transport(self):
        self.binary.write_bytes(b'changed')
        with patch.object(selection.subprocess, 'run') as run:
            with self.assertRaisesRegex(ContractError, 'VERIFIER_CHANGED'):
                selection.native(self.config, {'operation': 'validate_candidate_manifest'})
        run.assert_not_called()

    def test_owner_sdk_update_preserves_the_existing_neuron_evaluator_contract(self):
        legacy_pin = '78fdb0cf8537e3a84fc6e0a849707559c80881e8'
        evaluator = neuron_native.NativePorts(self.binary, self.config['canonical_verifier_sha256'])
        for source_pin in (legacy_pin, PIN):
            receipt = {'native_source_sha': source_pin, 'authorization_effect': 'NONE'}
            response = subprocess.CompletedProcess(
                ['synthetic'], 0, json.dumps(receipt).encode(), b'')
            with self.subTest(source_pin=source_pin), \
                 patch.object(neuron_native.subprocess, 'run', return_value=response):
                if source_pin == legacy_pin:
                    self.assertEqual(evaluator.call({'op': 'neuron'}), receipt)
                else:
                    with self.assertRaisesRegex(ContractError, 'NATIVE_SOURCE_OR_AUTHORITY_MISMATCH'):
                        evaluator.call({'op': 'neuron'})


class CanonicalSupportBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        candidate_dir = self.root / 'candidate'
        candidate_dir.mkdir()
        head = candidate_dir / 'value-head.safetensors'
        head.write_bytes(b'synthetic head bytes, no inference')
        candidate = {'base_id': 'a' * 64, 'reward_ids': ['reward'],
                     'source_python_sha256': {'source.py': 'b' * 64},
                     'torch_version': 'fixture', 'rocm': 'fixture',
                     'gpu_arch': 'fixture', 'financial_profile': 'fixture',
                     'created_at_ms': 10}
        (candidate_dir / 'candidate.json').write_text(json.dumps(candidate))
        self.plan = {'plan_id': 'c' * 64, 'candidate_id': 'd' * 64, 'trial': 3,
                     'training_dataset_sha256': 'e' * 64,
                     'head_tensor_sha256': sha_file(head),
                     'candidate_directory': str(candidate_dir),
                     'behavior_scope': {'fixture': 'EUR.USD.read-only'},
                     'outcome_end_ms': 100}
        self.assessment = {'outcome_set_digest': 'f' * 64}
        # Empty authority objects are deliberate: transport is mocked and this
        # test is not exercising or claiming cryptographic owner verification.
        bundle = {'signed_selection': {'artifact_generation': 3},
                  'signed_current_head': {}, 'registry_receipt': {},
                  'registry_snapshot_path': str(self.root / 'unused-snapshot'),
                  'payload_path': str(head)}
        bundle_path = self.root / 'bundle.json'
        bundle_path.write_text(json.dumps(bundle))
        host = {'route': 'AGENTD_FINANCIAL_VALUE_READONLY_V1',
                'owner_trust': {}, 'selection_trust': {}, 'head_requirement': {},
                'current_bundle_path': str(bundle_path)}
        host_path = self.root / 'host.json'
        host_path.write_text(json.dumps(host))
        binary = self.root / 'verifier-placeholder'
        binary.write_bytes(b'synthetic transport marker, never executed')
        period = self.root / 'period.json'
        period.write_text('{}')
        self.config = {'canonical_verifier_binary': str(binary),
                       'canonical_verifier_sha256': sha_file(binary),
                       'canonical_owner_registration': {
                           'path': str(host_path), 'sha256': sha_file(host_path)},
                       'period_path': str(period), 'period_sha256': sha_file(period),
                       'forward_root': str(self.root / 'forward')}
        self.requests = []

    def transport(self, argv, *, input, **kwargs):
        request = json.loads(input)
        self.requests.append(request)
        receipt = (manifest_receipt() if request['operation'] == 'validate_candidate_manifest'
                   else selected_receipt(self.plan['head_tensor_sha256']))
        return subprocess.CompletedProcess(argv, 0, json.dumps(receipt).encode(), b'')

    def test_owner_support_is_manifest_digest_and_evaluation_support_is_separate(self):
        with patch.object(selection.subprocess, 'run', side_effect=self.transport):
            result = selection.canonical_selection(self.config, self.plan, self.assessment)
        evaluation_support = digest({
            'plan_id': self.plan['plan_id'], 'candidate_id': self.plan['candidate_id'],
            'outcome_set_digest': self.assessment['outcome_set_digest'],
            'training_dataset_sha256': self.plan['training_dataset_sha256']})
        self.assertNotEqual(MANIFEST_DIGEST, evaluation_support)
        self.assertEqual(result['support_digest'], MANIFEST_DIGEST)
        self.assertEqual(result['evaluation_support_digest'], evaluation_support)
        self.assertEqual(self.requests[1]['expected_support_digest'], MANIFEST_DIGEST)
        self.assertTrue(result['canonical_selection_verified'])
        self.assertFalse(result['canonical_agentd_accepted'])
        self.assertFalse(result['promotion_performed'])
        self.assertFalse(result['product_consumed'])
        self.assertIsNone(result['product_consume_receipt'])
        self.assertEqual(result['state'], 'NATIVE_SELECTED_READONLY_CANDIDATE')

    def test_unenrolled_owner_remains_pending_without_any_selection_request(self):
        config = {**self.config, 'canonical_owner_registration': None}
        with patch.object(selection.subprocess, 'run', side_effect=self.transport):
            result = selection.canonical_selection(config, self.plan, self.assessment)
        self.assertEqual(result['state'], 'AWAITING_CANONICAL_AGENTD_OWNER_ENROLLMENT')
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(result['canonical_selection_verified'])
        self.assertFalse(result['canonical_agentd_accepted'])
        self.assertFalse(result['product_consumed'])

    def test_readonly_selection_current_uses_verified_selection_without_false_promotion(self):
        folder = Path(self.config['forward_root']) / 'plans' / self.plan['plan_id']
        folder.mkdir(parents=True)
        (folder / 'plan.json').write_text(json.dumps(self.plan))
        assessment_path = folder / 'assessment-signed.json'
        assessment_path.write_text(json.dumps(self.assessment))
        with patch.object(selection.subprocess, 'run', side_effect=self.transport), \
             patch.object(selection, 'validate_registration'), \
             patch.object(selection, 'evaluate_gate', return_value=True), \
             patch.object(selection, 'sign_record', side_effect=lambda _c, _role, value: value):
            # Independent evaluator signatures have their own real-crypto suite.
            # Here only the selected-vs-consumed publication behavior is tested.
            engine = selection.Selector(self.config)
            status = engine.step()
            current_path = Path(self.config['forward_root']) / 'selection-current.json'
            current = json.loads(current_path.read_text())
            row = json.loads((folder / 'selector-status.json').read_text())
            self.assertEqual(status['selected_count'], 1)
            self.assertEqual(status['consumed_count'], 0)
            self.assertEqual(current['state'], 'READONLY_AVAILABLE')
            for receipt in (current, row):
                self.assertTrue(receipt['canonical_selection_verified'])
                self.assertFalse(receipt['canonical_agentd_accepted'])
                self.assertFalse(receipt['promotion_performed'])
                self.assertFalse(receipt['product_consumed'])
                self.assertIsNone(receipt['product_consume_receipt'])
            self.assertEqual(current['high_water_generation'], 3)
            assessment_path.unlink()
            engine.step()
            unavailable = json.loads(current_path.read_text())
        self.assertEqual(unavailable['state'], 'UNAVAILABLE_NO_FALLBACK')
        self.assertEqual(unavailable['high_water_generation'], 3)
        self.assertFalse(unavailable['canonical_selection_verified'])
        self.assertFalse(unavailable['product_consumed'])


if __name__ == '__main__':
    unittest.main()
