"""Typed Laya Model handoff to the existing canonical artifact owner.

Publication uses the native owner, never a second registry. A consumer supplies
its existing bounded prediction worker; only its successful output, followed by
a fresh native CURRENT check, can produce a read-only product consume receipt.
No installation, enrollment, signature, trial or trade action is created here.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import math
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from .integrity import ContractError, canonical, digest, read_json, sha_file, validate_candidate_artifact
from .resident_store import write_once

PIN = '78fdb0cf8537e3a84fc6e0a849707559c80881e8'
PAYLOAD_SCHEMA = 'heptatrader.laya-owner-payload.v1'
ENROLLMENT_SCHEMA = 'heptatrader.canonical-laya-owner-enrollment.v1'
ROUTE = 'CANONICAL_LAYA_PRICE_READONLY_V1'
MAX_PAYLOAD = 8 * 1024**2
RUNTIME_PINS = ('learn/laya_cell/resident_owner.py', 'learn/laya_cell/resident_owner_predict.py')
CONTRACT = {
    'schema': 'heptatrader.laya-model-contract.v1',
    'objective': 'heptatrader.laya-resident-objective.v8',
    'horizon_sec': 300, 'instrument': 'EUR.USD',
    'output': 'CALIBRATED_PRICE7_AND_ACT_ESCALATE_ADVISORY_ONLY',
    'trade_permission': False,
}


def _need(value, reason):
    if not value:
        raise ContractError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _decode(raw):
    _need(len(raw) <= MAX_PAYLOAD, 'LAYA_PAYLOAD_BOUND')
    def unique(items):
        result = {}
        for key, value in items:
            _need(key not in result, 'LAYA_PAYLOAD_DUPLICATE_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ContractError('LAYA_PAYLOAD_NONFINITE')))


def enrollment(config):
    pin = config.get('canonical_owner_registration')
    if pin is None:
        return None
    _need(isinstance(pin, dict) and _digest(pin.get('sha256')), 'LAYA_OWNER_ENROLLMENT_PIN')
    path = Path(pin['path'])
    _need(path.is_absolute() and not path.is_symlink() and sha_file(path) == pin['sha256'],
          'LAYA_OWNER_ENROLLMENT_CHANGED')
    host = read_json(path)
    _need(host.get('schema') == ENROLLMENT_SCHEMA and host.get('route') == ROUTE,
          'LAYA_OWNER_PRODUCT_ROUTE_NOT_ENROLLED')
    _need(host.get('native_source_sha') == PIN and host.get('effect_authority') is False,
          'LAYA_OWNER_ENROLLMENT_CONTRACT')
    _need(isinstance(host.get('owner'), dict) and host['owner'].get('required_current_head') is not None,
          'LAYA_EXISTING_OWNER_CURRENT_REQUIRED')
    return host


def check_config(config):
    """The optional owner route cannot silently add unpinned executable code."""
    host = enrollment(config)
    if host is None:
        return None
    pins = config.get('runtime_sha256', {})
    root = Path(config['runtime_directory'])
    _need(all(_digest(pins.get(rel)) and sha_file(root / rel) == pins[rel] for rel in RUNTIME_PINS),
          'LAYA_OWNER_RUNTIME_PINS_REQUIRED')
    executable = Path(config['canonical_verifier_binary'])
    _need(executable.is_absolute() and _digest(config.get('canonical_verifier_sha256'))
          and sha_file(executable) == config['canonical_verifier_sha256'], 'CANONICAL_VERIFIER_CHANGED')
    requests = host.get('publication_requests', {})
    _need(isinstance(requests, dict) and len(requests) <= 512
          and all(_digest(plan_id) and isinstance(pin, dict) and _digest(pin.get('sha256'))
                  and isinstance(pin.get('path'), str) and Path(pin['path']).is_absolute()
                  for plan_id, pin in requests.items()), 'LAYA_OWNER_PUBLICATION_REQUEST_PINS')
    return host


def _pending(state, **extra):
    return {'state': state, 'canonical_agentd_accepted': False,
            'product_consumed': False, 'promotion_performed': False,
            'effect_authority': False, 'broker_orders_sent': 0, **extra}


def native(config, request):
    executable = Path(config['canonical_verifier_binary'])
    _need(sha_file(executable) == config['canonical_verifier_sha256'], 'CANONICAL_VERIFIER_CHANGED')
    result = subprocess.run([str(executable)], input=canonical(request), capture_output=True, timeout=15)
    _need(len(result.stdout) <= 2 * MAX_PAYLOAD + 65536, 'CANONICAL_VERIFIER_RESPONSE_BOUND')
    value = json.loads(result.stdout)
    _need(isinstance(value, dict) and result.returncode == 0 and value.get('native_source_sha') == PIN
          and value.get('effect_authority') is False and value.get('canonical_agentd_admitted') is False,
          'CANONICAL_LAYA_VERIFIER_REJECTED')
    contracts = {
        'validate_candidate_manifest': ('CANONICAL_MANIFEST_VALID_NOT_OWNER_ADMITTED', False),
        'publish_laya_model': ('CANONICAL_LAYA_MODEL_PUBLISHED_NOT_SELECTED', False),
        'load_laya_selected': ('CANONICAL_LAYA_SELECTED_BYTES_LOADED', True),
        'revalidate_laya_output': ('CANONICAL_LAYA_OUTPUT_REVALIDATED', True),
    }
    expected = contracts.get(request.get('operation'))
    _need(expected is not None and value.get('status') == expected[0]
          and value.get('selection_verified') is expected[1],
          'CANONICAL_LAYA_RECEIPT_CONTRACT')
    if request['operation'] != 'validate_candidate_manifest':
        _need(value.get('product_consumed') is False, 'NATIVE_VERIFIER_IS_NOT_MODEL_EXECUTION')
    if request['operation'] == 'validate_candidate_manifest':
        _need(_digest(value.get('manifest_digest')), 'CANONICAL_LAYA_RECEIPT_DIGEST')
    if request['operation'] == 'publish_laya_model':
        _need(value.get('operation_id') == request['operation_id']
              and value.get('registry_head_digest') == request['publication_head']['witness']['head_digest']
              and all(_digest(value.get(key)) for key in ('admission_digest', 'registry_head_digest',
                                                         'witness_digest', 'publication_state_digest')),
              'CANONICAL_LAYA_PUBLICATION_RECEIPT')
    return value


def _qualification(config, plan_path, now_ms):
    from .promotion import assess, validate_plan_rule
    plan = read_json(plan_path)
    _need(plan.get('plan_id') == digest({k: v for k, v in plan.items() if k != 'plan_id'})
          and plan.get('scope') == config['period_sha256'], 'LAYA_OWNER_PLAN_IDENTITY')
    validate_plan_rule(plan)
    path = plan_path.parent / 'qualification.json'
    if not path.exists():
        return plan, None
    qualification = read_json(path)
    _need(qualification.get('plan_id') == plan['plan_id'] and qualification.get('candidate_id') == plan['candidate_id'],
          'LAYA_OWNER_QUALIFICATION_IDENTITY')
    if qualification.get('state') != 'QUALIFIED_AWAITING_CANONICAL_OWNER':
        return plan, None
    _need(now_ms >= plan['outcome_end_ms'], 'LAYA_OWNER_FUTURE_WINDOW_INCOMPLETE')
    paths = sorted(plan_path.parent.glob('outcome-*.json'))
    results = [read_json(path) for path in paths]
    # Reuse the actual frozen rule. A diagnostic/prior blend/native PASS cannot
    # substitute for the original independently observed qualification.
    assessed = assess(plan, results, now_ms)
    _need(assessed.get('state') == 'QUALIFIED_AWAITING_CANONICAL_OWNER'
          and all(qualification.get(key) == value for key, value in assessed.items()),
          'LAYA_OWNER_QUALIFICATION_NOT_REPRODUCIBLE')
    return plan, {'qualification': qualification, 'qualification_sha256': sha_file(path),
                  'outcome_set_digest': digest([[p.name, sha_file(p)] for p in paths])}


def validate_payload(raw):
    from . import resident_training_v8 as policy
    from . import resident_price_input as price_input
    from . import resident_price_calibration as calibration
    from . import resident_price_baselines as baselines
    value = _decode(raw)
    _need(value.get('schema') == PAYLOAD_SCHEMA and value.get('contract') == CONTRACT, 'LAYA_TYPED_PAYLOAD_REQUIRED')
    candidate = value['candidate']
    _need(candidate.get('candidate_id') == digest({k: v for k, v in candidate.items() if k != 'candidate_id'}),
          'LAYA_OWNER_CANDIDATE_IDENTITY')
    policy.check_objective(candidate['objective'])
    _need(candidate['objective']['schema'] == policy.SCHEMA, 'LAYA_OWNER_V8_REQUIRED')
    price_input.check_bindings(candidate)
    price = calibration.validate(candidate['price_calibration'])
    _need(price['output_profile'] == policy.PRICE_OUTPUT and candidate['price_output_profile'] == policy.PRICE_OUTPUT
          and price['adapter_sha256'] == candidate['adapter_sha256']
          and price['parent_id'] == candidate['parent_id']
          and price['training_cutoff_ms'] == candidate['trained_until_ms'], 'LAYA_OWNER_CALIBRATION_BINDING')
    _need(candidate.get('action_output_profile') == policy.ACTION_OUTPUT, 'LAYA_OWNER_ACTION_SEMANTICS')
    _need(digest(value['base_manifest']) == candidate['base_id'], 'LAYA_OWNER_BASE_IDENTITY')
    _need(_digest(value.get('training_job_sha256')) and isinstance(value.get('training_runtime_sha256'), dict)
          and value['training_runtime_sha256']
          and all(isinstance(k, str) and _digest(v) for k, v in value['training_runtime_sha256'].items()),
          'LAYA_OWNER_TRAINING_CODE_BINDING')
    _need(_digest(value.get('registration_id')) and _digest(value.get('registration_digest')),
          'LAYA_OWNER_ORIGINAL_REGISTRATION_REQUIRED')
    adapter = bytes.fromhex(value['adapter_hex'])
    _need(len(adapter) <= MAX_PAYLOAD // 2 and _sha(adapter) == candidate['adapter_sha256'], 'LAYA_OWNER_ADAPTER_IDENTITY')
    # Safetensors' CPU decoder validates lengths/offsets without loading torch.
    import numpy as np
    from safetensors.numpy import load
    tensors = load(adapter)
    _need(set(tensors) == set(candidate['tensor_mask'])
          and sum(x.size for x in tensors.values()) == candidate['trainable_parameters']
          and all(x.dtype == np.float32 and np.isfinite(x).all() for x in tensors.values()),
          'LAYA_OWNER_ADAPTER_TENSOR_CONTRACT')
    plan = value['plan']
    _need(plan['plan_id'] == digest({k: v for k, v in plan.items() if k != 'plan_id'})
          and plan['candidate_id'] == candidate['candidate_id'], 'LAYA_OWNER_PAYLOAD_PLAN_IDENTITY')
    _need(plan.get('price_value_rule') == baselines.VALUE_RULE
          and plan.get('price_baselines') == candidate.get('price_baselines')
          and plan.get('price_calibration') == candidate.get('price_calibration')
          and plan.get('price_output_profile') == candidate.get('price_output_profile')
          and plan.get('action_output_profile') == candidate.get('action_output_profile')
          and price_input.check_bindings(plan) == price_input.check_bindings(candidate),
          'LAYA_OWNER_PLAN_MODEL_CONTRACT')
    _need(value['evaluation']['qualification'].get('state') == 'QUALIFIED_AWAITING_CANONICAL_OWNER'
          and value['evaluation']['qualification'].get('plan_id') == plan['plan_id'], 'LAYA_OWNER_QUALIFIED_PAYLOAD_REQUIRED')
    return value, adapter


def manifest_for(payload, raw, producer_id):
    candidate = payload['candidate']; plan = payload['plan']
    return {
        'artifact_id': 'laya.' + candidate['candidate_id'], 'generation': plan['trial'],
        'source_dataset_digests': sorted({candidate['dataset_id'], candidate['dataset_file_sha256']}),
        'lineage_digests': sorted({candidate['base_id'], candidate['parent_id'], plan['plan_id'],
                                 payload['registration_id'], payload['registration_digest'], payload['training_job_sha256'],
                                 payload['evaluation']['qualification_sha256'], payload['evaluation']['outcome_set_digest']}),
        'bytes_digest': _sha(raw), 'encoded_size_bytes': len(raw),
        'training_code_digest': digest(payload['training_runtime_sha256']),
        'runtime_tuple_digest': digest({'base': candidate['base_id'], 'torch': candidate['torch_version'],
                                      'rocm': candidate['rocm_version'], 'runtime': plan['evaluation_runtime_sha256']}),
        'device_profile_digest': digest({'gpu_arch': 'gfx1150', 'dtype': 'float32'}),
        'objective_class_digest': digest(candidate['objective']),
        'compatibility_digest': digest({'contract': CONTRACT, 'base_id': candidate['base_id'],
                                       'profile': candidate['profile'], 'price_input_spec': candidate['price_input_spec']}),
        'schema_profile_digest': digest(CONTRACT),
        'normalization_digest': digest({'input': candidate['price_input_spec'], 'price_calibration': candidate['price_calibration']}),
        'producer_id': producer_id, 'created_at': candidate['created_at_ms'],
        'expires_at': plan['outcome_end_ms'] + 86400000,
    }


def _registration(config, plan, candidate, job):
    from .resident_plan_admission import KEY_PREFIX, _identity, _check_record
    from .resident_store import get_meta
    path = Path(config['control_directory']) / 'scheduler.sqlite'
    _need(path.is_file(), 'LAYA_OWNER_REGISTERED_SOURCE_REQUIRED')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        service = SimpleNamespace(c=config, db=connection)
        record = get_meta(connection, KEY_PREFIX + plan['job_id'])
        _check_record(record, _identity(service, plan['job_id'], candidate, job))
        _need(record['state'] == 'PUBLISHED' and record['plan'] == plan
              and get_meta(connection, 'evaluation_trial', 0) >= plan['trial'],
              'LAYA_OWNER_PLAN_NOT_ORIGINALLY_PUBLISHED')
    return record


def prepare(config, plan_path, output_directory, *, now_ms=None):
    """Create an immutable qualified Model payload, never alter the trial."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    plan_path = Path(plan_path); plan, evaluation = _qualification(config, plan_path, now_ms)
    if evaluation is None:
        return _pending('AWAITING_ORIGINAL_FUTURE_QUALIFICATION', plan_id=plan['plan_id'])
    host = enrollment(config)
    if host is None:
        return _pending('AWAITING_CANONICAL_OWNER_ENROLLMENT', plan_id=plan['plan_id'])
    directory = Path(plan['candidate_directory'])
    candidate = validate_candidate_artifact(directory)
    _need(sha_file(directory / 'candidate.json') == plan['candidate_manifest_sha256'], 'LAYA_OWNER_CANDIDATE_CHANGED')
    job_path = Path(config['control_directory']) / 'jobs' / (plan['job_id'] + '.json')
    job = read_json(job_path)
    _need(plan['job_id'] == digest({'fingerprint': candidate['data_fingerprint'], 'parent': candidate['parent_id'],
                                  'profile': candidate['profile'], 'objective': candidate['objective']})
          and job['data_fingerprint'] == candidate['data_fingerprint'] and job['parent_id'] == candidate['parent_id']
          and job['dataset_sha256'] == candidate['dataset_file_sha256']
          and job['native_binary_sha256'] == candidate['native_binary_sha256']
          and job['objective'] == candidate['objective'] and job['profile'] == candidate['profile'],
          'LAYA_OWNER_TRAINING_JOB_BINDING')
    registration = _registration(config, plan, candidate, job)
    payload = {'schema': PAYLOAD_SCHEMA, 'contract': CONTRACT, 'candidate': candidate, 'plan': plan,
               'base_manifest': read_json(Path(config['base_directory']) / 'base-manifest.json'),
               'adapter_hex': (directory / 'adapter.safetensors').read_bytes().hex(), 'evaluation': evaluation,
               'training_job_sha256': sha_file(job_path), 'training_runtime_sha256': job['runtime_sha256'],
               'registration_id': registration['registration_id'], 'registration_digest': digest(registration)}
    raw = canonical(payload)
    validate_payload(raw)
    producer = host['owner']['writer_lease']['producer_id']
    manifest = manifest_for(payload, raw, producer)
    _need(now_ms < manifest['expires_at'], 'LAYA_OWNER_QUALIFIED_ARTIFACT_EXPIRED')
    output = Path(output_directory)
    outbox = (Path(config['control_directory']) / 'owner-outbox').resolve()
    _need(output.is_absolute() and output.resolve().is_relative_to(outbox), 'LAYA_OWNER_OUTPUT_MUST_USE_EXISTING_OUTBOX')
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    # write_once handles JSON objects; its canonical newline is part of bytes.
    encoded = canonical(payload) + b'\n'
    manifest['bytes_digest'] = _sha(encoded); manifest['encoded_size_bytes'] = len(encoded)
    payload_path = output / (manifest['bytes_digest'] + '.laya-model.json')
    write_once(payload_path, payload)
    receipt = native(config, {'operation': 'validate_candidate_manifest', 'manifest': manifest, 'payload_path': str(payload_path)})
    _need(_digest(receipt.get('manifest_digest')), 'LAYA_OWNER_NATIVE_MANIFEST_DIGEST')
    prepared = {'schema': 'heptatrader.laya-owner-prepared.v1', 'plan_id': plan['plan_id'],
                'payload_path': str(payload_path), 'manifest': manifest, 'support_digest': receipt['manifest_digest'],
                'evaluation_support_digest': digest(evaluation), 'canonical_agentd_accepted': False,
                'product_consumed': False, 'effect_authority': False}
    write_once(output / (manifest['bytes_digest'] + '.prepared.json'), prepared)
    return prepared


def handoff(config, plan_path, *, now_ms=None, allow_new=True):
    """Existing evaluator's terminal hook; CPU/native publication only.

    The immutable outbox record deduplicates one plan/enrollment attempt. A
    failed attempt remains blocked until a new operator-pinned enrollment is
    installed. This receipt has no selection, acceptance or consumption power.
    """
    plan_path = Path(plan_path)
    plan = read_json(plan_path)
    qualification_path = plan_path.parent / 'qualification.json'
    if not qualification_path.exists():
        return _pending('AWAITING_ORIGINAL_FUTURE_QUALIFICATION', plan_id=plan['plan_id'])
    qualification = read_json(qualification_path)
    if qualification.get('state') != 'QUALIFIED_AWAITING_CANONICAL_OWNER':
        return _pending('AWAITING_ORIGINAL_FUTURE_QUALIFICATION', plan_id=plan['plan_id'])
    if config.get('canonical_owner_registration') is None:
        return _pending('AWAITING_CANONICAL_OWNER_ENROLLMENT', plan_id=plan['plan_id'])
    enrollment(config)  # Even a cached receipt cannot hide a changed host pin.
    _need(plan['plan_id'] == digest({k: v for k, v in plan.items() if k != 'plan_id'})
          and qualification.get('plan_id') == plan['plan_id']
          and qualification.get('candidate_id') == plan['candidate_id'], 'LAYA_OWNER_HANDOFF_IDENTITY')
    binding = {'plan_id': plan['plan_id'], 'plan_sha256': sha_file(plan_path),
               'qualification_sha256': sha_file(qualification_path),
               'enrollment_sha256': config['canonical_owner_registration']['sha256']}
    outbox = Path(config['control_directory']) / 'owner-outbox' / plan['plan_id']
    receipt_path = outbox / ('handoff-' + digest(binding) + '.json')
    attempt_path = outbox / ('attempt-' + digest(binding) + '.json')
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        _need(receipt.get('schema') == 'heptatrader.laya-owner-handoff.v1'
              and receipt.get('binding') == binding
              and receipt.get('receipt_id') == digest({k: v for k, v in receipt.items() if k != 'receipt_id'})
              and receipt.get('state') in ('QUALIFIED_PAYLOAD_AWAITING_OWNER_SIGNED_PUBLICATION',
                  'CANONICAL_MODEL_PUBLISHED_AWAITING_INDEPENDENT_SELECTION', 'CANONICAL_OWNER_HANDOFF_BLOCKED')
              and receipt.get('canonical_agentd_accepted') is False
              and receipt.get('product_consumed') is False and receipt.get('effect_authority') is False,
              'LAYA_OWNER_HANDOFF_RECEIPT_CHANGED')
        if receipt['state'] == 'CANONICAL_MODEL_PUBLISHED_AWAITING_INDEPENDENT_SELECTION':
            native_receipt = receipt.get('publication_receipt', {})
            _need(native_receipt.get('status') == 'CANONICAL_LAYA_MODEL_PUBLISHED_NOT_SELECTED'
                  and native_receipt.get('native_source_sha') == PIN
                  and native_receipt.get('selection_verified') is False
                  and native_receipt.get('canonical_agentd_admitted') is False
                  and native_receipt.get('product_consumed') is False
                  and native_receipt.get('effect_authority') is False,
                  'LAYA_OWNER_HANDOFF_PUBLICATION_RECEIPT')
        return {**receipt, 'cached': True, 'attempt_started': False}
    if attempt_path.exists():
        attempt = read_json(attempt_path)
        _need(attempt.get('schema') == 'heptatrader.laya-owner-handoff-attempt.v1'
              and attempt.get('binding') == binding
              and attempt.get('attempt_id') == digest({k: v for k, v in attempt.items() if k != 'attempt_id'}),
              'LAYA_OWNER_HANDOFF_ATTEMPT_CHANGED')
        return _pending('CANONICAL_OWNER_HANDOFF_BLOCKED', plan_id=plan['plan_id'],
                        reason='OWNER_HANDOFF_ATTEMPT_WITHOUT_FINAL_RECEIPT',
                        automatic_retry=False, attempt_started=False)
    if not allow_new:
        return _pending('CANONICAL_OWNER_HANDOFF_DEFERRED_NEXT_EVALUATOR_TICK', plan_id=plan['plan_id'],
                        attempt_started=False)
    # Persist before any preparation/native side effect. A crash or inability
    # to save the final receipt must not turn a prior attempt into a fresh one.
    attempt = {'schema': 'heptatrader.laya-owner-handoff-attempt.v1', 'binding': binding,
               'at_ms': int(time.time() * 1000) if now_ms is None else now_ms,
               'automatic_retry': False}
    attempt['attempt_id'] = digest(attempt)
    write_once(attempt_path, attempt)
    try:
        host = check_config(config)
        prepared = prepare(config, plan_path, outbox, now_ms=now_ms)
        _need(prepared.get('schema') == 'heptatrader.laya-owner-prepared.v1', 'LAYA_OWNER_HANDOFF_NOT_PREPARED')
        request_pin = host.get('publication_requests', {}).get(plan['plan_id'])
        if request_pin is None:
            result = _pending('QUALIFIED_PAYLOAD_AWAITING_OWNER_SIGNED_PUBLICATION')
        else:
            request_path = Path(request_pin['path'])
            _need(not request_path.is_symlink() and sha_file(request_path) == request_pin['sha256'],
                  'LAYA_OWNER_PUBLICATION_REQUEST_CHANGED')
            request = read_json(request_path)
            _need(request.get('schema') == 'heptatrader.laya-owner-publication-request.v1'
                  and request.get('plan_id') == plan['plan_id'], 'LAYA_OWNER_PUBLICATION_PLAN_BINDING')
            result = publish(config, prepared, request)
        result.update(prepared_path=str(outbox / (prepared['manifest']['bytes_digest'] + '.prepared.json')),
                      content_digest=prepared['manifest']['bytes_digest'], support_digest=prepared['support_digest'])
    except (ContractError, OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        result = _pending('CANONICAL_OWNER_HANDOFF_BLOCKED', error_type=type(exc).__name__,
                          reason=str(exc)[:240], automatic_retry=False)
    receipt = {'schema': 'heptatrader.laya-owner-handoff.v1', 'binding': binding,
               'plan_id': plan['plan_id'], 'at_ms': int(time.time() * 1000) if now_ms is None else now_ms,
               **result}
    receipt['receipt_id'] = digest(receipt)
    write_once(receipt_path, receipt)
    return {**receipt, 'cached': False, 'attempt_started': True}


def _prepared(prepared):
    _need(prepared.get('schema') == 'heptatrader.laya-owner-prepared.v1', 'LAYA_PREPARED_SCHEMA')
    raw = Path(prepared['payload_path']).read_bytes()
    _need(_sha(raw) == prepared['manifest']['bytes_digest'] and len(raw) == prepared['manifest']['encoded_size_bytes'], 'LAYA_PREPARED_BYTES_CHANGED')
    payload, _ = validate_payload(raw)
    expected = manifest_for(payload, raw, prepared['manifest']['producer_id'])
    _need(expected == prepared['manifest'], 'LAYA_PREPARED_MANIFEST_CHANGED')
    return payload


def publish(config, prepared, publication_request):
    host = enrollment(config)
    if host is None:
        return _pending('AWAITING_CANONICAL_OWNER_ENROLLMENT')
    _prepared(prepared)
    _need(publication_request.get('content_digest') == prepared['manifest']['bytes_digest']
          and publication_request.get('support_digest') == prepared['support_digest'], 'LAYA_PUBLICATION_REQUEST_BINDING')
    result = native(config, {'operation': 'publish_laya_model', 'owner': host['owner'],
        'manifest': prepared['manifest'], 'payload_path': prepared['payload_path'],
        **{key: publication_request[key] for key in ('operation_id', 'publication_head',
             'expected_registry_predecessor_head', 'expected_withdrawal_head')}})
    return {**_pending('CANONICAL_MODEL_PUBLISHED_AWAITING_INDEPENDENT_SELECTION'), 'publication_receipt': result}


def _selection_request(host, prepared):
    pin = host['selection_bundle']
    _need(sha_file(Path(pin['path'])) == pin['sha256'], 'LAYA_SELECTION_BUNDLE_CHANGED')
    return {'owner': host['owner'], 'manifest': prepared['manifest'],
            'selection_bundle': read_json(Path(pin['path'])), 'selection_trust': host['selection_trust'],
            'selection_head_requirement': host['selection_head_requirement'], 'lifecycle': host['lifecycle']}


def _binding(receipt, prepared):
    m = prepared['manifest']
    _need(receipt.get('artifact_id') == m['artifact_id'] and receipt.get('artifact_generation') == m['generation']
          and receipt.get('content_digest') == m['bytes_digest'] and receipt.get('support_digest') == prepared['support_digest']
          and receipt.get('compatibility_digest') == m['compatibility_digest']
          and _digest(receipt.get('selection_digest')) and _digest(receipt.get('registry_head_digest')),
          'LAYA_SELECTED_VERSION_BINDING')


def _output(value, request, payload, now_ms):
    candidate = payload['candidate']
    _need(isinstance(value, dict) and value.get('schema') == 'heptatrader.laya-selected-price-output.v1'
          and value.get('request_id') == request['request_id'] and value.get('request_digest') == digest(request)
          and value.get('candidate_id') == candidate['candidate_id'] and value.get('base_id') == candidate['base_id']
          and value.get('price_output_profile') == candidate['price_output_profile']
          and value.get('action_output_profile') == candidate['action_output_profile']
          and value.get('trained_until_ms') == candidate['trained_until_ms']
          and value.get('horizon_sec') == 300 and value.get('action_head_is_trade_permission') is False
          and value.get('broker_orders_sent') == 0, 'LAYA_PRODUCT_OUTPUT_BINDING')
    _need(_digest(value.get('input_id')) and _digest(value.get('sample_id')) and _digest(value.get('source_prefixes_digest')),
          'LAYA_PRODUCT_INPUT_BINDING')
    anchor = value.get('anchor_ms'); created = value.get('created_at_ms')
    _need(type(anchor) is int and type(created) is int and candidate['created_at_ms'] < anchor
          and candidate['trained_until_ms'] <= anchor <= created <= now_ms
          and now_ms < min(anchor + 300000, request['expires_at_ms']), 'LAYA_PRODUCT_OUTPUT_EXPIRED_OR_LOOKAHEAD')
    for field, count in (('price_probs', 7), ('raw_price_probs', 7), ('action_correct_probs', 2)):
        values = value.get(field)
        if field == 'action_correct_probs' and values is None:
            continue
        _need(isinstance(values, list) and len(values) == count
              and all(type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1 for x in values)
              and math.isclose(sum(values), 1, rel_tol=0, abs_tol=1e-6), 'LAYA_PRODUCT_PROBABILITIES')


def consume(config, prepared, request, *, prediction_worker, clock=None):
    """Run an existing bounded worker, then revalidate CURRENT before output.

    prediction_worker(raw_payload, request) must be the product's admitted,
    confined worker. resident_owner_predict.predict_payload is its real model
    implementation. This function supplies no scheduler/GPU/trading authority.
    """
    clock = (lambda: int(time.time() * 1000)) if clock is None else clock
    host = enrollment(config)
    if host is None:
        return _pending('AWAITING_CANONICAL_OWNER_ENROLLMENT')
    payload = _prepared(prepared); now = clock()
    _need(isinstance(request.get('request_id'), str) and 0 < len(request['request_id']) <= 128
          and type(request.get('created_at_ms')) is int and type(request.get('expires_at_ms')) is int
          and request['created_at_ms'] <= now < request['expires_at_ms'] <= request['created_at_ms'] + 300000
          and request.get('period_sha256') == config['period_sha256'] and request.get('horizon_sec') == 300,
          'LAYA_PRODUCT_REQUEST')
    loaded = native(config, {'operation': 'load_laya_selected', **_selection_request(host, prepared)})
    _binding(loaded, prepared)
    raw = bytes.fromhex(loaded['payload_hex'])
    _need(_sha(raw) == prepared['manifest']['bytes_digest'], 'LAYA_NATIVE_LOADED_BYTES_CHANGED')
    loaded_payload, _ = validate_payload(raw)
    _need(loaded_payload == payload, 'LAYA_NATIVE_LOADED_PAYLOAD_CHANGED')
    output = prediction_worker(raw, dict(request))
    _output(output, request, payload, clock())
    # No callback output is returned if CURRENT/revocation, selection, version,
    # owner enrollment, request expiry or native verification fails afterward.
    refreshed_host = enrollment(config)
    final = native(config, {'operation': 'revalidate_laya_output', **_selection_request(refreshed_host, prepared),
        'loaded_registry': loaded['loaded_registry'], 'loaded_lifecycle': loaded['loaded_lifecycle'],
        'request_digest': digest(request), 'output_digest': digest(output)})
    _binding(final, prepared)
    _need(final['selection_digest'] == loaded['selection_digest'] and final.get('request_digest') == digest(request)
          and final.get('output_digest') == digest(output), 'LAYA_FINAL_USE_BINDING')
    consumed_at = clock()
    _output(output, request, payload, consumed_at)
    receipt = {'schema': 'heptatrader.laya-product-consume.v1', 'route': ROUTE,
        'request_id': request['request_id'], 'request_digest': digest(request),
        'artifact_id': final['artifact_id'], 'artifact_generation': final['artifact_generation'],
        'content_digest': final['content_digest'], 'selection_digest': final['selection_digest'],
        'registry_head_digest': final['registry_head_digest'], 'output_digest': digest(output),
        'native_final_use': final, 'consumed_at_ms': consumed_at,
        'product_consumed': True, 'canonical_agentd_accepted': False, 'effect_authority': False,
        'action_head_is_trade_permission': False, 'broker_orders_sent': 0}
    return {'state': 'CANONICAL_SELECTED_LAYA_READONLY_OUTPUT_CONSUMED', 'output': output,
            'product_consumed': True, 'consume_receipt': receipt, 'canonical_agentd_accepted': False,
            'effect_authority': False, 'broker_orders_sent': 0}
