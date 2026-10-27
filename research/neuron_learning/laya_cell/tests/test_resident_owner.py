"""Synthetic CPU contracts; no owner enrollment, keys, GPU or trade effects."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from safetensors.numpy import save

from laya_cell.integrity import ContractError, canonical, digest, sha_file
from laya_cell import resident_owner as owner
from laya_cell import resident_price_baselines as baselines
from laya_cell import resident_price_calibration_v3 as calibration
from laya_cell import resident_price_input as inputs
from laya_cell import resident_training_v8 as policy
from laya_cell.promotion import assess, rule_for_trial
from laya_cell.promotion_power import PROTOCOL
from laya_cell.resident_store import write_once
from laya_cell.tests.test_resident_price_calibration import population


def fixture():
    rows = population(); cutoff = 20000000
    reference = baselines.freeze(rows, cutoff)
    adapter = save({'synthetic.adapter': np.asarray([.1, .2], dtype=np.float32)})
    raw = [[.2 / 7 + .8 * (k == row['target_class']) for k in range(7)] for row in rows]
    profile = calibration.fit(rows, raw, cutoff_ms=cutoff, parent_id='a' * 64,
                              adapter_sha256=owner._sha(adapter), price_baselines=reference)
    base = {'synthetic_unit_fixture': True, 'weights_or_gpu_execution': False}
    candidate = {'schema': 'heptatrader.laya-resident-candidate.v1',
        'created_at_ms': cutoff + 1, 'trained_until_ms': cutoff, 'base_id': digest(base),
        'parent_id': 'a' * 64, 'dataset_id': 'd' * 64, 'dataset_file_sha256': 'e' * 64,
        'data_fingerprint': 'f' * 64, 'profile': {'synthetic_unit_fixture': True},
        'objective': deepcopy(policy.OBJECTIVE), 'adapter_sha256': owner._sha(adapter),
        'tensor_mask': ['synthetic.adapter'], 'trainable_parameters': 2,
        'price_calibration': profile, 'price_output_profile': policy.PRICE_OUTPUT,
        'price_baselines': reference, 'action_output_profile': policy.ACTION_OUTPUT,
        'action_training_profile': None, 'torch_version': 'fixture', 'rocm_version': 'fixture',
        **inputs.bindings({}, objective_schema=policy.SCHEMA)}
    candidate['candidate_id'] = digest(candidate)
    rule = rule_for_trial(51, PROTOCOL); start = 21000000
    plan = {'schema': 'heptatrader.laya-resident-future-plan.v1', 'scope': 'b' * 64,
        'candidate_id': candidate['candidate_id'], 'parent_id': candidate['parent_id'],
        'trial': 51, 'rule': rule, 'start_ms': start,
        'outcome_end_ms': start + 93 * 360000 + 60000 + 300000 + 5000,
        'evaluation_runtime_sha256': {'learn/laya_cell/resident_train.py': 'c' * 64},
        'price_value_rule': deepcopy(baselines.VALUE_RULE), 'price_baselines': reference,
        'price_calibration': profile, 'price_output_profile': policy.PRICE_OUTPUT,
        'action_output_profile': policy.ACTION_OUTPUT,
        **inputs.bindings({}, objective_schema=policy.SCHEMA)}
    plan['plan_id'] = digest(plan)
    outcomes = []
    for slot in range(rule['slots']):
        anchor = start + slot * 360000
        outcomes.append({'plan_id': plan['plan_id'], 'candidate_id': candidate['candidate_id'],
            'slot': slot, 'status': 'OBSERVED', 'target_class': 2 + slot % 2, 'anchor_ms': anchor,
            'prediction_available_ms': anchor + 1000, 'trained_until_ms': cutoff, 'path_gap': False,
            'candidate_brier': .1, 'parent_brier': .9, 'candidate_hit': True, 'bootstrap_hit': False,
            'horizon_sec': 300, 'price_baseline_profile_id': reference['profile_id'],
            'same_window_price_scores': {name: {'brier': .1 if name == 'laya_candidate' else .9}
                                         for name in baselines.MODELS}})
    qualification = assess(plan, outcomes, 55000000)
    payload = {'schema': owner.PAYLOAD_SCHEMA, 'contract': deepcopy(owner.CONTRACT),
        'candidate': candidate, 'base_manifest': base, 'adapter_hex': adapter.hex(), 'plan': plan,
        'training_job_sha256': '1' * 64, 'training_runtime_sha256': {'train.py': '2' * 64},
        'registration_id': 'a' * 64, 'registration_digest': 'b' * 64,
        'evaluation': {'qualification': qualification, 'qualification_sha256': '3' * 64,
                       'outcome_set_digest': digest(outcomes)}}
    return payload, outcomes


class LayaOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template, cls.outcomes = fixture()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = deepcopy(self.template)
        self.raw = canonical(self.payload) + b'\n'
        path = self.root / 'payload.json'; path.write_bytes(self.raw)
        self.prepared = {'schema': 'heptatrader.laya-owner-prepared.v1', 'payload_path': str(path),
            'manifest': owner.manifest_for(self.payload, self.raw, 'synthetic-fixture-producer'),
            'support_digest': '4' * 64}
        self.config = {'period_sha256': 'b' * 64}
        self.request = {'request_id': 'synthetic-request-1', 'created_at_ms': 55000000,
                        'expires_at_ms': 55100000, 'period_sha256': 'b' * 64, 'horizon_sec': 300}
        c = self.payload['candidate']
        self.output = {'schema': 'heptatrader.laya-selected-price-output.v1',
            'request_id': self.request['request_id'], 'request_digest': digest(self.request),
            'candidate_id': c['candidate_id'], 'base_id': c['base_id'], 'trained_until_ms': c['trained_until_ms'],
            'price_output_profile': c['price_output_profile'], 'action_output_profile': c['action_output_profile'],
            'input_id': '5' * 64, 'sample_id': '6' * 64, 'source_prefixes_digest': '7' * 64,
            'anchor_ms': 54999000, 'created_at_ms': 55000000, 'horizon_sec': 300,
            'price_probs': [1 / 7] * 7, 'raw_price_probs': [1 / 7] * 7, 'action_correct_probs': None,
            'action_head_is_trade_permission': False, 'broker_orders_sent': 0}
        m = self.prepared['manifest']
        self.loaded = {'artifact_id': m['artifact_id'], 'artifact_generation': m['generation'],
            'content_digest': m['bytes_digest'], 'support_digest': self.prepared['support_digest'],
            'compatibility_digest': m['compatibility_digest'], 'selection_digest': '8' * 64,
            'registry_head_digest': '9' * 64, 'payload_hex': self.raw.hex(),
            'loaded_registry': {'records': 2, 'head_digest': '9' * 64},
            'loaded_lifecycle': {'records': 7, 'head_digest': '0' * 64}}

    def run_consume(self, *, final_change=None, worker=None, clock=None):
        calls = []
        def bridge(config, request):
            calls.append(request)
            value = deepcopy(self.loaded)
            if request['operation'] == 'revalidate_laya_output':
                value.update(request_digest=request['request_digest'], output_digest=request['output_digest'])
                value.pop('payload_hex')
                if final_change:
                    final_change(value)
            return value
        with patch.object(owner, 'enrollment', return_value={'synthetic_unit_fixture': True}), \
             patch.object(owner, '_selection_request', return_value={}), patch.object(owner, 'native', side_effect=bridge):
            value = owner.consume(self.config, self.prepared, self.request,
                prediction_worker=worker or (lambda raw, request: deepcopy(self.output)),
                clock=clock or (lambda: 55000000))
        return value, calls

    def test_actual_frozen_qualification_and_safetensors_fixture_validate(self):
        self.assertEqual(self.payload['evaluation']['qualification']['state'], 'QUALIFIED_AWAITING_CANONICAL_OWNER')
        value, adapter = owner.validate_payload(self.raw)
        self.assertEqual(value, self.payload)
        self.assertEqual(owner._sha(adapter), self.payload['candidate']['adapter_sha256'])
        self.assertEqual(owner._prepared(self.prepared), self.payload)

    def test_missing_enrollment_never_invokes_a_model_or_native_bridge(self):
        worker = Mock(side_effect=AssertionError('GPU must not start'))
        with patch.object(owner, 'native', side_effect=AssertionError('native must not run')):
            result = owner.consume(self.config, self.prepared, self.request, prediction_worker=worker)
        self.assertEqual(result['state'], 'AWAITING_CANONICAL_OWNER_ENROLLMENT')
        self.assertFalse(result['product_consumed']); worker.assert_not_called()

    def test_advisory_output_only_after_callback_and_final_current_check(self):
        worker = Mock(return_value=self.output)
        result, calls = self.run_consume(worker=worker)
        worker.assert_called_once_with(self.raw, self.request)
        self.assertEqual([r['operation'] for r in calls], ['load_laya_selected', 'revalidate_laya_output'])
        self.assertEqual(calls[1]['loaded_registry'], self.loaded['loaded_registry'])
        self.assertEqual(calls[1]['loaded_lifecycle'], self.loaded['loaded_lifecycle'])
        receipt = result['consume_receipt']
        self.assertTrue(result['product_consumed']); self.assertFalse(result['canonical_agentd_accepted'])
        self.assertEqual(receipt['output_digest'], digest(self.output))
        self.assertEqual(receipt['artifact_generation'], 51)
        self.assertFalse(receipt['action_head_is_trade_permission'])

    def test_native_read_verification_never_substitutes_for_model_execution(self):
        worker = Mock(side_effect=RuntimeError('actual model failed'))
        with self.assertRaisesRegex(RuntimeError, 'actual model failed'):
            self.run_consume(worker=worker)

    def test_wrong_generation_selection_or_output_digest_is_not_consumed(self):
        for key, value in [('artifact_generation', 52), ('selection_digest', 'a' * 64), ('output_digest', 'b' * 64)]:
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.run_consume(final_change=lambda result: result.update({key: value}))

    def test_final_current_failure_discards_successful_model_output(self):
        def revoked(_):
            raise ContractError('native CURRENT says revoked')
        with self.assertRaisesRegex(ContractError, 'revoked'):
            self.run_consume(final_change=revoked)

    def test_expiry_during_final_native_check_discards_output(self):
        ticks = iter([55000000, 55000000, self.request['expires_at_ms']])
        with self.assertRaisesRegex(ContractError, 'EXPIRED'):
            self.run_consume(clock=lambda: next(ticks))

    def test_callback_cannot_change_request_profile_cutoff_or_trade_meaning(self):
        for key, value in [('request_id', 'other'), ('price_output_profile', 'other'),
                           ('action_output_profile', 'other'), ('trained_until_ms', 0),
                           ('action_head_is_trade_permission', True)]:
            output = {**self.output, key: value}
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.run_consume(worker=lambda raw, request: output)

    def test_changed_selected_payload_and_training_manifest_are_rejected(self):
        self.prepared['manifest']['training_code_digest'] = 'a' * 64
        with self.assertRaisesRegex(ContractError, 'MANIFEST_CHANGED'):
            owner._prepared(self.prepared)
        self.payload['adapter_hex'] = '00'
        with self.assertRaisesRegex(ContractError, 'ADAPTER_IDENTITY'):
            owner.validate_payload(canonical(self.payload))

    def test_original_qualification_is_recomputed_and_not_just_a_state_string(self):
        plan = self.payload['plan']; folder = self.root / 'trial'; folder.mkdir()
        write_once(folder / 'plan.json', plan)
        write_once(folder / 'qualification.json', self.payload['evaluation']['qualification'])
        for row in self.outcomes:
            write_once(folder / f"outcome-{row['slot']:03}.json", row)
        _, good = owner._qualification(self.config, folder / 'plan.json', 55000000)
        self.assertIsNotNone(good)
        path = folder / 'outcome-000.json'; row = json.loads(path.read_bytes())
        row['parent_brier'] = .8; row['same_window_price_scores']['frozen_parent']['brier'] = .8
        path.write_bytes(canonical(row) + b'\n')
        with self.assertRaisesRegex(ContractError, 'NOT_REPRODUCIBLE'):
            owner._qualification(self.config, folder / 'plan.json', 55000000)

    def test_rejected_original_trial_never_produces_a_payload(self):
        folder = self.root / 'rejected'; folder.mkdir()
        write_once(folder / 'plan.json', self.payload['plan'])
        q = {**self.payload['evaluation']['qualification'], 'state': 'REJECTED'}
        write_once(folder / 'qualification.json', q)
        output = self.root / 'must-not-exist'
        result = owner.prepare(self.config, folder / 'plan.json', output, now_ms=55000000)
        self.assertEqual(result['state'], 'AWAITING_ORIGINAL_FUTURE_QUALIFICATION')
        self.assertFalse(output.exists())

    def test_missing_original_scheduler_record_cannot_be_replaced_by_json_files(self):
        with self.assertRaisesRegex(ContractError, 'REGISTERED_SOURCE_REQUIRED'):
            owner._registration({'control_directory': str(self.root)}, self.payload['plan'],
                                self.payload['candidate'], {})

    def test_typed_native_contract_rejects_numeric_boolean_and_fake_consumed(self):
        base = {'status': 'CANONICAL_LAYA_SELECTED_BYTES_LOADED', 'selection_verified': True,
                'native_source_sha': owner.PIN, 'effect_authority': False,
                'canonical_agentd_admitted': False, 'product_consumed': False}
        config = {'canonical_verifier_binary': '/synthetic-fixture', 'canonical_verifier_sha256': 'a' * 64}
        for change in ({'selection_verified': 1}, {'product_consumed': True}, {'canonical_agentd_admitted': True}):
            process = SimpleNamespace(returncode=0, stdout=canonical({**base, **change}))
            with patch.object(owner, 'sha_file', return_value='a' * 64), \
                 patch.object(owner.subprocess, 'run', return_value=process), self.assertRaises(ContractError):
                owner.native(config, {'operation': 'load_laya_selected'})

    def publication_preview_fixture(self):
        context = {'owner_trust': {'registry_id': 'synthetic-registry',
                   'withdrawal_scope_digest': '5' * 64}, 'storage_binding': '6' * 64}
        request = {'operation': 'prepare_laya_publication', 'owner': context,
            'manifest': self.prepared['manifest'], 'payload_path': self.prepared['payload_path'],
            'operation_id': 'synthetic-preview-operation',
            'expected_registry_predecessor_head': '7' * 64, 'expected_withdrawal_head': '8' * 64}
        reply = {'status': 'CANONICAL_LAYA_PUBLICATION_PREVIEW_NOT_PUBLISHED',
            'native_source_sha': owner.PIN, 'selection_verified': False,
            'canonical_agentd_admitted': False, 'product_consumed': False,
            'effect_authority': False, 'published': False,
            'operation_id': request['operation_id'], 'content_digest': self.prepared['manifest']['bytes_digest'],
            'manifest_digest': self.prepared['support_digest'], 'admission_digest': '9' * 64,
            'intent_digest': 'a' * 64, 'owner_trust_digest': 'b' * 64, 'writer_lease_digest': 'c' * 64,
            'withdrawal_scope_digest': context['owner_trust']['withdrawal_scope_digest'],
            'withdrawal_head_digest': request['expected_withdrawal_head'],
            'expected_registry_predecessor_head': request['expected_registry_predecessor_head'],
            'prospective_registry_head_digest': 'd' * 64, 'publication_generation': 2,
            'registry_id': context['owner_trust']['registry_id'], 'storage_binding': context['storage_binding'],
            'admitted_at': 55000000}
        reply['unsigned_publication_head'] = {
            'withdrawal_scope_digest': reply['withdrawal_scope_digest'], 'binding': reply['storage_binding'],
            'witness': {'registry_id': reply['registry_id'], 'generation': reply['publication_generation'],
                'head_digest': reply['prospective_registry_head_digest'],
                'predecessor_head_digest': reply['expected_registry_predecessor_head'],
                'issued_at': reply['admitted_at']}}
        config = {**self.config, 'canonical_verifier_binary': '/synthetic-fixture-never-executed',
                  'canonical_verifier_sha256': 'e' * 64}
        return config, request, reply

    def call_preview_contract(self, request, reply):
        config, _, _ = self.publication_preview_fixture()
        process = SimpleNamespace(returncode=0, stdout=canonical(reply))
        with patch.object(owner, 'sha_file', return_value='e' * 64), \
             patch.object(owner.subprocess, 'run', return_value=process):
            return owner.native(config, request)

    def test_prepare_publication_returns_bound_unsigned_identity_without_writes_or_clock_input(self):
        config, request, reply = self.publication_preview_fixture()
        process = SimpleNamespace(returncode=0, stdout=canonical(reply))
        files = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(owner, 'enrollment', return_value={'owner': request['owner']}), \
             patch.object(owner, 'sha_file', return_value='e' * 64), \
             patch.object(owner.subprocess, 'run', return_value=process) as call, \
             patch.object(owner, 'write_once', side_effect=AssertionError('preview must not write')), \
             patch.object(owner, 'publish', side_effect=AssertionError('preview must not publish')):
            actual = owner.prepare_publication(config, self.prepared,
                operation_id=request['operation_id'],
                expected_registry_predecessor_head=request['expected_registry_predecessor_head'],
                expected_withdrawal_head=request['expected_withdrawal_head'])
        self.assertEqual(actual, reply)
        call.assert_called_once()
        self.assertEqual(call.call_args.args, ([config['canonical_verifier_binary']],))
        self.assertEqual(json.loads(call.call_args.kwargs['input']), request)
        self.assertEqual(call.call_args.kwargs['timeout'], 15)
        self.assertTrue({'now', 'now_ms', 'admitted_at', 'publication_head', 'signature'}.isdisjoint(request))
        self.assertEqual(files, {p.relative_to(self.root): p.read_bytes()
                               for p in self.root.rglob('*') if p.is_file()})

    def test_prepare_publication_without_enrollment_does_not_call_native_or_validate_payload(self):
        with patch.object(owner, 'native', side_effect=AssertionError('native must not run')), \
             patch.object(owner, '_prepared', side_effect=AssertionError('payload must not be read')):
            result = owner.prepare_publication(self.config, self.prepared, operation_id='unused',
                expected_registry_predecessor_head='7' * 64, expected_withdrawal_head='8' * 64)
        self.assertEqual(result['state'], 'AWAITING_CANONICAL_OWNER_ENROLLMENT')
        self.assertFalse(result['canonical_agentd_accepted'])

    def test_prepare_publication_rejects_changed_payload_before_native(self):
        _, request, _ = self.publication_preview_fixture()
        Path(self.prepared['payload_path']).write_bytes(self.raw + b' ')
        with patch.object(owner, 'enrollment', return_value={'owner': request['owner']}), \
             patch.object(owner, 'native', side_effect=AssertionError('native must not run')):
            with self.assertRaisesRegex(ContractError, 'PREPARED_BYTES_CHANGED'):
                owner.prepare_publication(self.config, self.prepared,
                    operation_id=request['operation_id'],
                    expected_registry_predecessor_head=request['expected_registry_predecessor_head'],
                    expected_withdrawal_head=request['expected_withdrawal_head'])

    def test_preview_receipt_cannot_claim_publication_selection_or_authority(self):
        _, request, reply = self.publication_preview_fixture()
        for key, value in [('published', True), ('published', 0), ('effect_authority', True),
                           ('canonical_agentd_admitted', True), ('product_consumed', True),
                           ('selection_verified', 0),
                           ('status', 'CANONICAL_LAYA_MODEL_PUBLISHED_NOT_SELECTED')]:
            with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                self.call_preview_contract(request, {**reply, key: value})

    def test_preview_receipt_rejects_wrong_scope_content_operation_and_typed_digests(self):
        _, request, reply = self.publication_preview_fixture()
        changes = [('operation_id', 'another-operation'), ('content_digest', 'f' * 64),
            ('withdrawal_scope_digest', 'f' * 64), ('withdrawal_head_digest', 'f' * 64),
            ('expected_registry_predecessor_head', 'f' * 64), ('registry_id', 'another-registry'),
            ('storage_binding', 'f' * 64), ('admission_digest', 'not-a-digest'),
            ('intent_digest', None), ('owner_trust_digest', True), ('writer_lease_digest', 1)]
        for key, value in changes:
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.call_preview_contract(request, {**reply, key: value})

    def test_preview_time_and_generation_are_unsigned_integer_identities(self):
        _, request, reply = self.publication_preview_fixture()
        for key, values in [('admitted_at', [None, True, -1, 1.5, 2**64]),
                            ('publication_generation', [None, True, 0, -1, 2**64])]:
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                    self.call_preview_contract(request, {**reply, key: value})

    def test_unsigned_preview_head_cannot_gain_signer_or_drift_from_returned_identity(self):
        _, request, reply = self.publication_preview_fixture()
        for field, value in [('head_digest', 'e' * 64), ('issued_at', reply['admitted_at'] + 1),
                             ('generation', 2.0), ('signature', '00' * 64),
                             ('signer_id', 'not-an-enrolled-signature')]:
            bad = deepcopy(reply); bad['unsigned_publication_head']['witness'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ContractError, 'PREVIEW_UNSIGNED_HEAD'):
                self.call_preview_contract(request, bad)
        bad = deepcopy(reply); bad['publication_generation'] = 1
        bad['unsigned_publication_head']['witness']['generation'] = True
        with self.assertRaisesRegex(ContractError, 'PREVIEW_UNSIGNED_HEAD'):
            self.call_preview_contract(request, bad)

    def test_prepare_preview_support_must_match_original_prepared_manifest(self):
        config, request, reply = self.publication_preview_fixture()
        reply['manifest_digest'] = 'f' * 64
        process = SimpleNamespace(returncode=0, stdout=canonical(reply))
        with patch.object(owner, 'enrollment', return_value={'owner': request['owner']}), \
             patch.object(owner, 'sha_file', return_value='e' * 64), \
             patch.object(owner.subprocess, 'run', return_value=process):
            with self.assertRaisesRegex(ContractError, 'PREVIEW_SUPPORT_BINDING'):
                owner.prepare_publication(config, self.prepared,
                    operation_id=request['operation_id'],
                    expected_registry_predecessor_head=request['expected_registry_predecessor_head'],
                    expected_withdrawal_head=request['expected_withdrawal_head'])


    def qualified_plan(self, *, terminal=True, extra=None):
        plan = deepcopy(self.payload['plan'])
        if extra is not None:
            plan['synthetic_test_variant'] = extra
            plan['plan_id'] = digest({k: v for k, v in plan.items() if k != 'plan_id'})
        folder = self.root / 'evaluation' / plan['plan_id']
        write_once(folder / 'plan.json', plan)
        q = {**self.payload['evaluation']['qualification'], 'plan_id': plan['plan_id']}
        if terminal:
            write_once(folder / 'qualification.json', q)
        return folder / 'plan.json', q

    def enrolled_handoff(self, plan_path, *, signed_request=True):
        request_path = self.root / 'synthetic-publication-request.json'
        request = {'schema': 'heptatrader.laya-owner-publication-request.v1',
            'plan_id': self.payload['plan']['plan_id'], 'operation_id': 'synthetic-fixture-operation',
            'content_digest': self.prepared['manifest']['bytes_digest'], 'support_digest': self.prepared['support_digest'],
            'publication_head': {'witness': {'head_digest': '9' * 64}},
            'expected_registry_predecessor_head': '8' * 64, 'expected_withdrawal_head': '7' * 64}
        write_once(request_path, request)
        host = {'schema': owner.ENROLLMENT_SCHEMA, 'route': owner.ROUTE, 'native_source_sha': owner.PIN,
            'effect_authority': False, 'owner': {'required_current_head': {'synthetic_test_only': True}},
            'publication_requests': ({self.payload['plan']['plan_id']: {'path': str(request_path),
                                      'sha256': sha_file(request_path)}} if signed_request else {})}
        host_path = self.root / 'synthetic-enrollment.json'; write_once(host_path, host)
        config = {**self.config, 'control_directory': str(self.root / 'control'),
                  'canonical_owner_registration': {'path': str(host_path), 'sha256': sha_file(host_path)}}
        receipt = {'status': 'CANONICAL_LAYA_MODEL_PUBLISHED_NOT_SELECTED', 'native_source_sha': owner.PIN,
            'operation_id': request['operation_id'], 'registry_head_digest': '9' * 64,
            'selection_verified': False, 'canonical_agentd_admitted': False,
            'product_consumed': False, 'effect_authority': False}
        return config, host, receipt

    def test_terminal_handoff_without_enrollment_creates_no_outbox_and_calls_no_native(self):
        path, _ = self.qualified_plan()
        config = {**self.config, 'control_directory': str(self.root / 'control')}
        with patch.object(owner, 'prepare', side_effect=AssertionError('prepare must not run')), \
             patch.object(owner, 'native', side_effect=AssertionError('native must not run')):
            result = owner.handoff(config, path, now_ms=55000000)
        self.assertEqual(result['state'], 'AWAITING_CANONICAL_OWNER_ENROLLMENT')
        self.assertFalse((self.root / 'control').exists())

    def test_normal_terminal_handoff_publishes_once_and_preserves_qualification(self):
        path, _ = self.qualified_plan(); config, host, receipt = self.enrolled_handoff(path)
        original = (path.parent / 'qualification.json').read_bytes()
        evaluate, service = self.evaluator(); service.c.update(config)
        with patch.object(owner, 'check_config', return_value=host), \
             patch.object(owner, 'prepare', return_value=self.prepared) as prepare, \
             patch.object(owner, 'native', return_value=receipt) as native, \
             patch.object(evaluate.time, 'time', return_value=55000), \
             patch.object(evaluate, 'sha_file', return_value='b' * 64):
            first_status = service.step(); second_status = service.step()
        first = first_status['canonical_owner_handoffs'][0]; second = second_status['canonical_owner_handoffs'][0]
        self.assertTrue(first_status['canonical_owner_connected']); self.assertTrue(second_status['canonical_owner_connected'])
        self.assertEqual(first['state'], 'CANONICAL_MODEL_PUBLISHED_AWAITING_INDEPENDENT_SELECTION')
        self.assertFalse(first['cached']); self.assertTrue(second['cached'])
        prepare.assert_called_once(); native.assert_called_once()
        self.assertEqual(native.call_args.args[1]['operation'], 'publish_laya_model')
        self.assertFalse(second['product_consumed']); self.assertFalse(second['canonical_agentd_accepted'])
        self.assertEqual((path.parent / 'qualification.json').read_bytes(), original)

    def test_handoff_failure_is_retained_without_a_tick_retry_or_false_connection(self):
        path, _ = self.qualified_plan(); config, host, _ = self.enrolled_handoff(path)
        with patch.object(owner, 'check_config', return_value=host), \
             patch.object(owner, 'prepare', return_value=self.prepared), \
             patch.object(owner, 'native', side_effect=ContractError('WriterFenceBusy')) as native:
            first = owner.handoff(config, path, now_ms=55000000)
            second = owner.handoff(config, path, now_ms=55000001)
        native.assert_called_once()
        self.assertEqual(second['state'], 'CANONICAL_OWNER_HANDOFF_BLOCKED')
        self.assertFalse(first['automatic_retry']); self.assertTrue(second['cached'])
        self.assertNotIn('publication_receipt', second)

    def test_final_handoff_write_failure_cannot_repeat_native_publication_on_the_next_tick(self):
        path, _ = self.qualified_plan(); config, host, receipt = self.enrolled_handoff(path)
        evaluate, service = self.evaluator(); service.c.update(config)
        real_write = owner.write_once
        def interrupted(path, value):
            if Path(path).name.startswith('handoff-'):
                raise OSError('synthetic final receipt ENOSPC')
            return real_write(path, value)
        with patch.object(owner, 'check_config', return_value=host), \
             patch.object(owner, 'prepare', return_value=self.prepared) as prepare, \
             patch.object(owner, 'native', return_value=receipt) as native, \
             patch.object(owner, 'write_once', side_effect=interrupted), \
             patch.object(evaluate.time, 'time', return_value=55000), \
             patch.object(evaluate, 'sha_file', return_value='b' * 64):
            first = service.step(); second = service.step()
        native.assert_called_once(); prepare.assert_called_once()
        self.assertFalse(first['canonical_owner_connected']); self.assertFalse(second['canonical_owner_connected'])
        self.assertEqual(second['canonical_owner_handoffs'][0]['reason'], 'OWNER_HANDOFF_ATTEMPT_WITHOUT_FINAL_RECEIPT')
        self.assertEqual(len(list((self.root / 'control/owner-outbox').rglob('attempt-*.json'))), 1)
        self.assertEqual(list((self.root / 'control/owner-outbox').rglob('handoff-*.json')), [])

    def test_handoff_prepares_without_a_signature_and_obeys_existing_tick_budget(self):
        path, _ = self.qualified_plan(); config, host, _ = self.enrolled_handoff(path, signed_request=False)
        with patch.object(owner, 'check_config', return_value=host), \
             patch.object(owner, 'prepare', return_value=self.prepared) as prepare, \
             patch.object(owner, 'native', side_effect=AssertionError('unsigned publication is forbidden')):
            deferred = owner.handoff(config, path, now_ms=55000000, allow_new=False)
            self.assertFalse((self.root / 'control').exists())
            first = owner.handoff(config, path, now_ms=55000000)
            second = owner.handoff(config, path, now_ms=55000001)
        self.assertEqual(deferred['state'], 'CANONICAL_OWNER_HANDOFF_DEFERRED_NEXT_EVALUATOR_TICK')
        self.assertEqual(first['state'], 'QUALIFIED_PAYLOAD_AWAITING_OWNER_SIGNED_PUBLICATION')
        self.assertTrue(second['cached']); prepare.assert_called_once()

    def test_opt_in_owner_config_requires_exact_runtime_and_binary_pins(self):
        self.assertIsNone(owner.check_config(self.config))
        path, _ = self.qualified_plan(); config, host, _ = self.enrolled_handoff(path)
        runtime = self.root / 'runtime'; pins = {}
        for rel in owner.RUNTIME_PINS:
            file = runtime / rel; file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('# Synthetic fixture; not executable\n'); pins[rel] = sha_file(file)
        binary = self.root / 'not-an-executable'; binary.write_bytes(b'synthetic native hash fixture')
        config.update(runtime_directory=str(runtime), runtime_sha256=pins,
                      canonical_verifier_binary=str(binary), canonical_verifier_sha256=sha_file(binary))
        self.assertEqual(owner.check_config(config), host)
        (runtime / owner.RUNTIME_PINS[0]).write_text('# changed\n')
        with self.assertRaisesRegex(ContractError, 'RUNTIME_PINS_REQUIRED'):
            owner.check_config(config)

    def evaluator(self):
        from laya_cell import resident_evaluate as evaluate
        service = evaluate.Evaluator.__new__(evaluate.Evaluator)
        service.c = {**self.config, 'period_path': '/synthetic-period',
                     'control_directory': str(self.root / 'control')}
        service.root = self.root / 'evaluation'; service.root.mkdir(exist_ok=True)
        service.tasks = {}; service.last_log_ms = 55000000; service.reap = Mock()
        service.operator_stop_binding = Mock(return_value=None)
        return evaluate, service

    def test_existing_evaluator_terminal_path_invokes_handoff_without_reassessing_history(self):
        path, _ = self.qualified_plan(); evaluate, service = self.evaluator()
        with patch.object(evaluate.time, 'time', return_value=55000), \
             patch.object(evaluate, 'sha_file', return_value='b' * 64), \
             patch.object(evaluate, 'assess', side_effect=AssertionError('terminal history must not be rewritten')), \
             patch.object(owner, 'prepare', side_effect=AssertionError('missing enrollment must not prepare')):
            result = service.step()
        self.assertFalse(result['canonical_owner_connected'])
        self.assertEqual(result['canonical_owner_handoffs'][0]['state'], 'AWAITING_CANONICAL_OWNER_ENROLLMENT')
        self.assertFalse((self.root / 'control').exists())

    def test_existing_evaluator_handoff_budget_and_connection_require_actual_publication(self):
        self.qualified_plan(extra=1); self.qualified_plan(extra=2); evaluate, service = self.evaluator()
        published = {'state': 'CANONICAL_MODEL_PUBLISHED_AWAITING_INDEPENDENT_SELECTION', 'attempt_started': True}
        deferred = {'state': 'CANONICAL_OWNER_HANDOFF_DEFERRED_NEXT_EVALUATOR_TICK', 'attempt_started': False}
        with patch.object(evaluate.time, 'time', return_value=55000), \
             patch.object(evaluate, 'sha_file', return_value='b' * 64), \
             patch.object(owner, 'handoff', side_effect=[published, deferred]) as handoff:
            result = service.step()
        self.assertTrue(result['canonical_owner_connected'])
        self.assertEqual([call.kwargs['allow_new'] for call in handoff.call_args_list], [True, False])
        with patch.object(evaluate.time, 'time', return_value=55000), \
             patch.object(evaluate, 'sha_file', return_value='b' * 64), \
             patch.object(owner, 'handoff', return_value={'state': 'QUALIFIED_PAYLOAD_AWAITING_OWNER_SIGNED_PUBLICATION'}):
            result = service.step()
        self.assertFalse(result['canonical_owner_connected'])
        with patch.object(evaluate.time, 'time', return_value=55000), \
             patch.object(evaluate, 'sha_file', return_value='b' * 64), \
             patch.object(owner, 'handoff', side_effect=[OSError('synthetic outbox failure'), deferred]) as handoff:
            result = service.step()
        self.assertFalse(result['canonical_owner_connected'])
        self.assertEqual([call.kwargs['allow_new'] for call in handoff.call_args_list], [True, False])


if __name__ == '__main__':
    unittest.main()
