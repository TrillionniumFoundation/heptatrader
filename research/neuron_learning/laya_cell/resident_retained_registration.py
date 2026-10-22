"""Check one existing DEFERRED registration with its actual frozen runtime.

No recover/step loop, training child, new configuration or replacement ledger.
The caller retains the scheduler lock; this subprocess uses the same database.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def _sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _read(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('RETAINED_REGISTRATION_JSON_SIZE')
    return json.loads(raw)


def run_original(current, entry, job_id, registration_id, operation):
    from .integrity import ContractError
    if operation not in ('register', 'calendar'):
        raise ContractError('RETAINED_REGISTRATION_OPERATION')
    if _sha(entry['path']) != entry['sha256']:
        raise ContractError('RETAINED_REGISTRATION_CONFIG_CHANGED')
    old = _read(entry['path'])
    if any(old.get(k) != current.get(k) for k in
           ('period_sha256', 'evaluation_directory', 'control_directory')):
        raise ContractError('RETAINED_REGISTRATION_SCOPE_CHANGED')
    python = old.get('python')
    if type(python) is not str or not Path(python).is_absolute():
        raise ContractError('RETAINED_REGISTRATION_PYTHON')
    env = dict(os.environ, PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               CUDA_VISIBLE_DEVICES='', HIP_VISIBLE_DEVICES='',
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    completed = subprocess.run([python, '-I', '-B', str(Path(__file__).resolve()),
        '--config', entry['path'], '--config-sha256', entry['sha256'],
        '--job-id', job_id, '--registration-id', registration_id,
        '--operation', operation], env=env, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise ContractError('RETAINED_REGISTRATION_FAILED:' + completed.stderr[-400:])
    result = json.loads(completed.stdout)
    if (type(result) is not dict or set(result) != {'state', 'trial_allocated'}
            or result['state'] not in ('CALENDAR_CHECKED', 'ORIGINAL_TRIAL_PENDING',
                                       'ORIGINAL_FORWARD_RESOURCE_GUARD_REQUIRED')
            or result['trial_allocated'] is not False):
        raise ContractError('RETAINED_REGISTRATION_RESULT')
    return result


def resume_original(config_path, config_sha256, job_id, registration_id, operation):
    # Verify every original source pin before importing any package code.
    if _sha(config_path) != config_sha256:
        raise ValueError('RETAINED_REGISTRATION_CONFIG_CHANGED')
    old = _read(config_path)
    runtime = Path(old['runtime_directory'])
    if (old.get('automatic_trading') is not False or not runtime.is_absolute()
            or any(not Path(old[k]).is_absolute() for k in
                   ('control_directory', 'evaluation_directory'))):
        raise ValueError('RETAINED_REGISTRATION_SCOPE')
    pins = old.get('runtime_sha256')
    required = ('resident.py', 'resident_plan_admission.py', 'resident_plan_calendar.py',
                'resident_queue.py', 'market_session.py', 'resident_store.py',
                'integrity.py', '__init__.py')
    if (not isinstance(pins, dict) or
            any('learn/laya_cell/' + name not in pins for name in required)):
        raise ValueError('RETAINED_REGISTRATION_RUNTIME_PINS_REQUIRED')
    for name, expected in pins.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or _sha(runtime / name) != expected:
            raise ValueError('RETAINED_REGISTRATION_RUNTIME_CHANGED:' + name)
    sys.path[:0] = [str(runtime / 'learn'), str(runtime / 'scripts')]
    from laya_cell.resident import Supervisor
    from laya_cell import resident_plan_admission as admission
    from laya_cell.integrity import read_json
    from laya_cell.resident_store import get_meta
    for module in (sys.modules[Supervisor.__module__], admission):
        expected = runtime / 'learn/laya_cell' / (module.__name__.rsplit('.', 1)[1] + '.py')
        if Path(module.__file__).resolve() != expected.resolve():
            raise ValueError('RETAINED_REGISTRATION_IMPORT_ORIGIN')
    service = Supervisor(config_path)
    try:
        row = service.db.execute("SELECT * FROM jobs WHERE id=? AND status='COMPLETED'",
                                 (job_id,)).fetchone()
        if row is None:
            raise ValueError('RETAINED_REGISTRATION_COMPLETED_JOB_REQUIRED')
        job = json.loads(row['payload'])
        candidate = read_json(Path(job['output_directory']) / 'candidate.json')
        record = get_meta(service.db, admission.KEY_PREFIX + job_id)
        if (not isinstance(record, dict) or record.get('state') != 'DEFERRED'
                or record.get('registration_id') != registration_id):
            raise ValueError('RETAINED_REGISTRATION_RECEIPT_CHANGED')
        admission._check_record(record, admission._identity(service, job_id, candidate, job))
        if operation == 'calendar':
            service._finish_impossible_forward_registration(row)
            return 'CALENDAR_CHECKED'
        elif operation == 'register':
            # The original negative-only calendar can close an impossible
            # window. Otherwise keep a still-blocked receipt byte-for-byte:
            # running register merely to rediscover its pending queue would
            # rewrite its checked_at/registration_id every recovery pass.
            if service._finish_impossible_forward_registration(row):
                return 'CALENDAR_CHECKED'
            queue = admission.inspect_queue(service.c)
            policy = getattr(admission, 'registration_policy', None)
            blocking = (policy(queue, service.c, job, row['created_ms'])['blocking_trials']
                        if policy is not None else queue['pending_original_trials'])
            if blocking:
                return 'ORIGINAL_TRIAL_PENDING'
            # This isolated policy reader does not possess the caller's
            # operational GuardedTrainer gate. Never call bare candidate_done
            # here: it could allocate alpha without the existing resource /
            # source preflight. Leave this receipt for its original guarded
            # registrar; new explicitly enrolled candidates have their own slot.
            return 'ORIGINAL_FORWARD_RESOURCE_GUARD_REQUIRED'
        else:
            raise ValueError('RETAINED_REGISTRATION_OPERATION')
    finally:
        service.db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--registration-id', required=True)
    parser.add_argument('--operation', choices=('register', 'calendar'), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    state = resume_original(args.config, args.config_sha256, args.job_id,
                            args.registration_id, args.operation)
    print(json.dumps({'state':state, 'trial_allocated':False}))


if __name__ == '__main__':
    main()
