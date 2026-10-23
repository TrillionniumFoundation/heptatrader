"""Explicit operator abort of new trial work; never a performance verdict.

Standard library only so a pinned copy can validate authorization before an
original evaluator package is imported in its CPU-only closeout subprocess.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import time

SCHEMA = 'heptatrader.laya-operator-trial-stop.v1'
PROFILE = 'OPERATOR_ABORT_FROZEN_TRIAL_V1'
RUNTIME_PIN = ('learn/laya_cell/resident_trial_stop.py',)
_SHA = re.compile(r'^[0-9a-f]{64}$')
_COMMIT = re.compile(r'^[0-9a-f]{40}$')
_FLAGS = {'authorization_source': 'EXPLICIT_USER_REQUEST',
          'alpha_refunded': False, 'performance_assessment': False,
          'new_heavy_workers_allowed': False, 'automatic_trading': False,
          'original_plan_rule_deadline_preserved': True}
_KEYS = {'schema', 'profile', 'plan_id', 'candidate_id', 'plan_sha256',
         'period_sha256', 'requested_at_ms', 'user_reason',
         'replacement_source_sha', 'receipt_id'} | set(_FLAGS)


def _need(ok, reason):
    if not ok:
        raise ValueError(reason)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            _need(key not in result, 'OPERATOR_STOP_DUPLICATE_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('OPERATOR_STOP_NONFINITE')))


def check_config(config):
    fields = ('operator_trial_stop_profile', 'operator_trial_stop_replacement_source_sha')
    if not any(key in config for key in fields):
        return
    replacement = config.get(fields[1])
    _need(config.get(fields[0]) == PROFILE and isinstance(replacement, str)
          and _COMMIT.fullmatch(replacement) is not None, 'OPERATOR_STOP_CONFIG_PROFILE')
    runtime = Path(config.get('runtime_directory') or '')
    expected = config.get('runtime_sha256', {}).get(RUNTIME_PIN[0])
    path = runtime / RUNTIME_PIN[0]
    _need(runtime.is_absolute() and not runtime.is_symlink()
          and runtime.resolve() == runtime and path.is_file() and not path.is_symlink()
          and isinstance(expected, str) and _SHA.fullmatch(expected) is not None
          and _sha(path) == expected == _sha(Path(__file__)), 'OPERATOR_STOP_RUNTIME_PIN')


def build_receipt(plan_path, *, requested_at_ms, user_reason, replacement_source_sha):
    path = Path(plan_path)
    plan = _json(path.read_bytes())
    receipt = {'schema': SCHEMA, 'profile': PROFILE,
        'plan_id': plan['plan_id'], 'candidate_id': plan['candidate_id'],
        'plan_sha256': _sha(path), 'period_sha256': plan['scope'],
        'requested_at_ms': requested_at_ms, 'user_reason': user_reason,
        'replacement_source_sha': replacement_source_sha, **_FLAGS}
    receipt['receipt_id'] = _digest(receipt)
    return receipt


def validate_receipt_file(receipt_path, plan_path, *, expected_period_sha256,
                          expected_replacement_source_sha, expected_sha256=None,
                          now_ms=None):
    path, plan_path = Path(receipt_path), Path(plan_path)
    _need(path == plan_path.parent / 'operator-stop.json', 'OPERATOR_STOP_PATH')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
              and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
              and info.st_size <= 16384, 'OPERATOR_STOP_FILE_IDENTITY')
        raw = stream.read(16385)
    actual_sha = hashlib.sha256(raw).hexdigest()
    _need(expected_sha256 is None or expected_sha256 == actual_sha, 'OPERATOR_STOP_RECEIPT_CHANGED')
    plan = _json(plan_path.read_bytes())
    receipt = _json(raw)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    _need(isinstance(plan, dict) and plan.get('plan_id') == _digest(
        {k: v for k, v in plan.items() if k != 'plan_id'}), 'OPERATOR_STOP_PLAN_CHANGED')
    _need(isinstance(receipt, dict) and set(receipt) == _KEYS
          and receipt.get('schema') == SCHEMA and receipt.get('profile') == PROFILE
          and receipt.get('receipt_id') == _digest({k: v for k, v in receipt.items()
                                                   if k != 'receipt_id'})
          and all(type(receipt.get(k)) is type(v) and receipt.get(k) == v
                  for k, v in _FLAGS.items()), 'OPERATOR_STOP_PROFILE_OR_HASH')
    _need(receipt['plan_id'] == plan['plan_id']
          and receipt['candidate_id'] == plan['candidate_id']
          and receipt['plan_sha256'] == _sha(plan_path)
          and receipt['period_sha256'] == plan.get('scope') == expected_period_sha256
          and isinstance(expected_replacement_source_sha, str)
          and _COMMIT.fullmatch(expected_replacement_source_sha) is not None
          and receipt['replacement_source_sha'] == expected_replacement_source_sha,
          'OPERATOR_STOP_PLAN_OR_SOURCE_BINDING')
    requested, reason = receipt['requested_at_ms'], receipt['user_reason']
    _need(type(now) is int and type(requested) is int
          and 0 <= plan.get('created_at_ms', plan['start_ms']) <= requested <= now
          and isinstance(reason, str) and 1 <= len(reason) <= 2000
          and reason.strip() == reason and all(ord(c) >= 32 for c in reason),
          'OPERATOR_STOP_AUTHORIZATION_CONTEXT')
    return {'receipt': receipt, 'path': str(path), 'sha256': actual_sha,
            'validator_sha256': _sha(Path(__file__)),
            'replacement_source_sha': expected_replacement_source_sha}


def read_stop(config, plan_path, plan, *, now_ms=None):
    path = Path(plan_path).parent / 'operator-stop.json'
    if not os.path.lexists(path):
        return None
    check_config(config)
    _need(config.get('operator_trial_stop_profile') == PROFILE, 'OPERATOR_STOP_NOT_ENROLLED')
    _need(Path(plan_path).parent.parent == Path(config['evaluation_directory'])
          and plan.get('scope') == config['period_sha256'], 'OPERATOR_STOP_SCOPE')
    binding = validate_receipt_file(path, plan_path,
        expected_period_sha256=config['period_sha256'],
        expected_replacement_source_sha=config['operator_trial_stop_replacement_source_sha'],
        now_ms=now_ms)
    _need(binding['receipt']['plan_id'] == plan['plan_id'], 'OPERATOR_STOP_PLAN_CHANGED')
    return binding
