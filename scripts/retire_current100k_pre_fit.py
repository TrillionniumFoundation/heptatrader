#!/usr/bin/env python3
"""Retire one pinned, unstarted Current100k continuation before any frozen cohort.

The default is a read-only check. --retire disables only its exact timer, waits
for the oneshot to be idle, and adds a separate service condition which prevents
an accidental restart. Old requests, datasets, executors and registered trials
are never changed. No driver activation, model, registration or order is run.

The original executor performs its own --check-only validation. New dependency
inventories must not be imposed on an already frozen older executor.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import time

SCHEMA = 'heptatrader.current100k-pre-fit-retirement.v1'
CONTINUATION_SCHEMA = 'heptatrader.current100k-newscope-continuation.v1'
WAITING = 'WAITING_NEW_PRODUCER_32_SETTLED_LABELS'
MAX_ACTION_MS = 15 * 60 * 1000
MAX_READ = 64 * 1024 * 1024
UNIT_PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'FragmentPath', 'DropInPaths',
    'NeedDaemonReload', 'ExecStart', 'UnitFileState', 'MainPID', 'NextElapseUSecRealtime',
    'WorkingDirectory', 'Environment', 'Type')


def need(ok, reason):
    if not ok:
        raise ValueError('PRE_FIT_RETIREMENT_' + reason)


def now_ms():
    return time.time_ns() // 1_000_000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value, 'DUPLICATE_JSON_KEY')
            value[key] = item
        return value
    def reject_constant(_):
        need(False, 'NONFINITE_JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)


def canonical_path(value):
    path = Path(value)
    need(path.is_absolute() and path.resolve() == path, 'CANONICAL_PATH_REQUIRED:' + str(path))
    return path


def checked(path, *, limit=MAX_READ):
    path = canonical_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
             and not before.st_mode & 0o077 and before.st_size <= limit, 'PRIVATE_REGULAR_FILE_REQUIRED:' + str(path))
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(identity(before) == identity(after) == identity(path.stat()) and len(raw) == before.st_size,
             'INPUT_CHANGED_DURING_READ:' + str(path))
        return raw
    finally:
        os.close(fd)


def pinned(path, checksum):
    raw = checked(path)
    need(re.fullmatch('[0-9a-f]{64}', checksum) and digest(raw) == checksum, 'INPUT_CHANGED:' + str(path))
    return strict(raw)


def read(path):
    return strict(checked(path))


def immutable(path, raw):
    path = Path(path)
    if path.exists() or path.is_symlink():
        need(checked(path) == raw, 'EXISTING_OUTPUT_CHANGED:' + str(path))
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record(path, value):
    immutable(path, canonical(value) + b'\n')


def remaining(deadline_ms, maximum=30):
    seconds = (deadline_ms - now_ms()) / 1000
    need(seconds > 0, 'DEADLINE_EXPIRED_NO_AUTOMATIC_ROLLBACK')
    return min(seconds, maximum)


def command(argv, deadline_ms, *, env=None, cwd=None, maximum=30):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=remaining(deadline_ms, maximum), check=False, env=env, cwd=cwd)
    need(result.returncode == 0, 'COMMAND_FAILED:' + ' '.join(argv[:4]) + ':' + result.stderr[-600:])
    return result.stdout


def repository(source, commit, deadline_ms):
    source = canonical_path(source)
    need(command(['git', '-C', str(source), 'rev-parse', 'HEAD'], deadline_ms).strip() == commit
         and not command(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all'], deadline_ms).strip(),
         'EXACT_CLEAN_SOURCE_REQUIRED:' + str(source))


def original_check_argv(path, checksum, request):
    return ['/usr/bin/python3', '-B', str(Path(request['source_repository']) / 'scripts/continue_current100k_new_scope.py'),
            '--request', str(path), '--sha256', checksum, '--check-only']


def original_check(path, checksum, request, deadline_ms):
    """Run the frozen source, never today's load_inputs or support inventory."""
    source = canonical_path(request['source_repository'])
    repository(source, request['source_commit'], deadline_ms)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=':'.join(
        str(source / part) for part in ('research', 'research/neuron_learning', 'scripts')))
    proof = strict(command(original_check_argv(path, checksum, request), deadline_ms,
        env=env, cwd=source, maximum=180))
    need(proof.get('state') == 'CONTINUATION_INPUTS_VERIFIED_NO_TRAINING_OR_REGISTRATION'
         and proof.get('source_commit') == request['source_commit'] and proof.get('gpu_loaded') is False
         and proof.get('broker_orders_sent') == 0
         and proof.get('preparation_source_commit') == request['preparation_source_commit'], 'ORIGINAL_CPU_PROOF_REQUIRED')
    name = 'heptatrader-current100k-newscope-' + checksum[:12]
    units = proof.get('unit_candidates')
    need(isinstance(units, dict) and set(units) == {name + '.service', name + '.timer'}
         and all(isinstance(text, str) for text in units.values()), 'ORIGINAL_EXACT_UNIT_CANDIDATES_REQUIRED')
    return proof


def inputs(args):
    request_path = canonical_path(args.request); request = pinned(request_path, args.sha256)
    root = canonical_path(request['controller_root']); prep_path = canonical_path(request['preparation_request'])
    need(request_path == root / 'CONTINUATION_REQUEST.json' and request.get('schema') == CONTINUATION_SCHEMA
         and request.get('worker_profile') == 'cpu-standby-v2' and request.get('trade_authority') is False
         and request.get('old_trials_modified') is False, 'EXPLICIT_PINNED_V2_REQUIRED')
    prep = pinned(prep_path, request['preparation_request_sha256'])
    need(prep.get('minimum_rows') == 32 and prep.get('selection_policy') == 'FIRST_32_CHRONOLOGICAL_CLOSED_NEW_PRODUCER_V1'
         and prep.get('registration_scope') == 'NEW_PRODUCER_SCOPE'
         and prep.get('source_commit') == request['preparation_source_commit'], 'ORIGINAL_FIRST32_PREPARATION_REQUIRED')
    consumer = pinned(prep['consumer_config'], prep['consumer_config_sha256'])
    need(consumer['registration'] == prep['producer_registration'], 'ORIGINAL_PRODUCER_CHANGED')
    producer = pinned(consumer['registration']['manifest'], consumer['registration']['sha256'])
    successor = pinned(args.next_producer_manifest, args.next_producer_sha256)
    need(args.next_producer_sha256 != consumer['registration']['sha256']
         and successor.get('prepared_from_manifest_sha256') == consumer['registration']['sha256']
         and isinstance(successor.get('price_bootstrap_gate'), dict)
         and all(successor.get(k) == producer.get(k) for k in
             ('schema', 'instrument', 'execution_domain', 'period_sha256', 'behavior_profile')),
         'EXPLICIT_SEPARATE_BOOTSTRAP_SUCCESSOR_LINEAGE_REQUIRED')
    previous = None
    if request.get('supersedes_continuation') is not None:
        pin = request['supersedes_continuation']; old = pinned(pin['request'], pin['sha256'])
        need(old.get('schema') == CONTINUATION_SCHEMA and old.get('supersedes_continuation') is None
             and old.get('worker_profile', 'warm-flat-v1') == 'warm-flat-v1'
             and old['preparation_request'] == str(prep_path)
             and old['preparation_request_sha256'] == request['preparation_request_sha256'], 'ORIGINAL_V1_PREDECESSOR_REQUIRED')
        previous = {'request': old, 'path': canonical_path(pin['request']), 'sha256': pin['sha256']}
        need(previous['path'] == canonical_path(old['controller_root']) / 'CONTINUATION_REQUEST.json', 'PREDECESSOR_PATH')
    output = canonical_path(args.output_directory)
    protected = [root, prep_path.parent, canonical_path(consumer['output_root']), canonical_path(request['source_repository']),
        *[canonical_path(request[k]) for k in ('fit_output_root', 'forward_root')],
        *[canonical_path(p).parent.parent for p in prep['legacy_plans_sha256']]]
    if previous:
        protected += [canonical_path(previous['request'][k]) for k in
                      ('controller_root', 'source_repository', 'fit_output_root', 'forward_root')]
    need(all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in protected)
         and re.fullmatch(r'[-A-Za-z0-9_./]+', str(output)), 'SEPARATE_CANONICAL_RETIREMENT_ROOT_REQUIRED')
    source = Path(__file__).resolve().parent.parent
    binding = {'schema': SCHEMA, 'source_repository': str(source), 'source_commit': args.expected_source_commit,
        'continuation_request': str(request_path), 'continuation_request_sha256': args.sha256,
        'continuation_source_commit': request['source_commit'], 'preparation_request': str(prep_path),
        'preparation_request_sha256': request['preparation_request_sha256'],
        'producer_registration': consumer['registration'], 'next_producer_registration': {
            'manifest': str(canonical_path(args.next_producer_manifest)), 'sha256': args.next_producer_sha256},
        'output_directory': str(output), 'deadline_ms': args.deadline_ms,
        'reason': 'P0_PRICE_BOOTSTRAP_NEW_PRODUCER_BEFORE_ANY_FROZEN_COHORT_OR_FIT',
        'driver_activation_authorized_by_this_tool': False, 'trade_authority': False}
    return {'request': request, 'path': request_path, 'sha256': args.sha256, 'root': root, 'prep_path': prep_path,
        'prep': prep, 'consumer': consumer, 'previous': previous, 'output': output, 'binding': binding}


@contextmanager
def original_locks(data):
    # Match the frozen runner: selected v2 -> original v1 (if any) -> preparation.
    paths = [data['root'] / 'continuation.lock']
    if data['previous']:
        paths.append(Path(data['previous']['request']['controller_root']) / 'continuation.lock')
    paths.append(data['prep_path'].parent / 'preparation.lock')
    opened = []
    try:
        for path in paths:
            checked(path)
            before = path.stat()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            opened.append(fd)
            observed = os.fstat(fd)
            need((observed.st_dev, observed.st_ino) == (before.st_dev, before.st_ino)
                 == (path.stat().st_dev, path.stat().st_ino), 'ORIGINAL_LOCK_REPLACED:' + str(path))
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                need(False, 'ORIGINAL_LOCK_BUSY_NO_FORCE_STOP:' + str(path))
        yield paths
    finally:
        for fd in reversed(opened):
            os.close(fd)


def absent_effects(data):
    request = data['request']; previous = data['previous']
    need(pinned(data['path'], data['sha256']) == request, 'REQUEST_CHANGED')
    need(pinned(data['prep_path'], request['preparation_request_sha256']) == data['prep'], 'PREPARATION_CHANGED')
    allowed = {'CONTINUATION_REQUEST.json', 'continuation.lock', 'status.json', 'status.json.next'}
    if previous:
        allowed |= {'PREDECESSOR_HANDOFF_INTENT.json', 'PREDECESSOR_HANDOFF.json'}
        handoff = read(data['root'] / 'PREDECESSOR_HANDOFF.json')
        binding = read(data['root'] / 'PREDECESSOR_HANDOFF_INTENT.json')
        need(handoff.get('state') == 'PREDECESSOR_TIMER_RETIRED_NEW_TIMER_VERIFIED'
             and handoff.get('binding') == binding and binding.get('request_sha256') == data['sha256']
             and binding.get('supersedes_continuation') == request['supersedes_continuation']
             and binding.get('preparation_request_sha256') == request['preparation_request_sha256']
             and handoff.get('preserved_cohort_sha256') == {}, 'ORIGINAL_HANDOFF_OR_COHORT_CHANGED')
        old_root = canonical_path(previous['request']['controller_root'])
        need({p.name for p in old_root.iterdir()} <= {'CONTINUATION_REQUEST.json', 'continuation.lock', 'status.json', 'status.json.next'},
             'PREDECESSOR_HAS_STAGING_OR_UNKNOWN_EFFECTS')
        need(pinned(previous['path'], previous['sha256']) == previous['request'], 'PREDECESSOR_CHANGED')
    need({p.name for p in data['root'].iterdir()} <= allowed, 'CONTROLLER_HAS_STAGING_OR_UNKNOWN_EFFECTS')
    need({p.name for p in data['prep_path'].parent.iterdir()}
         <= {'PREPARATION_REQUEST.json', 'preparation.lock', 'status.json', 'status.json.next'},
         'FROZEN32_READY_OR_UNKNOWN_PREPARATION_EFFECT')
    for item in [request, *([previous['request']] if previous else [])]:
        for key in ('fit_output_root', 'forward_root'):
            path = canonical_path(item[key])
            need(not path.exists() and not path.is_symlink(), 'FIT_OR_FORWARD_OUTPUT_EXISTS:' + str(path))


def waiting_snapshot(data):
    request = data['request']; prep = data['prep']; consumer = data['consumer']; current = now_ms()
    control_raw = checked(data['root'] / 'status.json'); control = strict(control_raw)
    need(control.get('state') == WAITING and control.get('training_started') is False
         and control.get('trial_registered') is False
         and control.get('source_commit') == request['source_commit']
         and control.get('preparation_request_sha256') == request['preparation_request_sha256']
         and control.get('producer_manifest_sha256') == consumer['registration']['sha256']
         and type(control.get('distinct_settled_rows')) is int and 0 <= control['distinct_settled_rows'] < 32
         and type(control.get('at_ms')) is int and 0 <= current - control['at_ms'] <= 180000,
         'FRESH_WAITING_UNSTARTED_STATUS_REQUIRED')
    status_path = canonical_path(consumer['output_root']) / 'status.json'
    status_raw = checked(status_path); status = strict(status_raw)
    stamp = status.get('input_checked_at_ms', status.get('at_ms'))
    need(status.get('state') in ('READY_VALUE_LABELS_NOT_EFFICACY', 'WAITING_CURRENT100K_SETTLED_LABELS')
         and type(stamp) is int and 0 <= current - stamp <= 180000, 'FRESH_CONSUMER_REQUIRED')
    dataset_path = canonical_path(status['dataset_path'])
    need(dataset_path.is_relative_to(Path(consumer['output_root'])), 'DATASET_OUTSIDE_OLD_SCOPE')
    dataset_raw = checked(dataset_path); dataset = strict(dataset_raw)
    need(digest(dataset_raw) == status['dataset_sha256']
         and digest(canonical({k:v for k,v in dataset.items() if k != 'fingerprint'})) == dataset.get('fingerprint')
         and dataset.get('producer_manifest_sha256') == consumer['registration']['sha256']
         and dataset.get('period_sha256') == consumer['period_sha256']
         and dataset.get('synthetic_rewards') == dataset.get('old25k_rewards') == 0
         and dataset.get('trade_authority') is False, 'OLD_DATASET_IDENTITY_CHANGED')
    rows = dataset.get('rows'); need(isinstance(rows, list) and len(rows) < 32, 'OLD_SCOPE_ALREADY_HAS_32_ROWS')
    prior = next(pinned(path, value) for path, value in prep['legacy_plans_sha256'].items()
                 if Path(path).parent.name == prep['prior_plan_id'])
    for row in rows:
        need(row['behavior_scope']['producer_manifest_sha256'] == consumer['registration']['sha256']
             and row['behavior_scope']['model_id'] == prior['behavior_scope']['behavior_model_id']
             and row['period_sha256'] == consumer['period_sha256']
             and row['row_id'] == digest(canonical({k:v for k,v in row.items() if k != 'row_id'})), 'OLD_ROW_IDENTITY_CHANGED')
    for name in ('row_id', 'input_id', 'reward_id', 'episode_id'):
        need(len({row[name] for row in rows}) == len(rows), 'DUPLICATE_OLD_ROW:' + name)
    need(checked(status_path) == status_raw and checked(dataset_path) == dataset_raw
         and checked(data['root'] / 'status.json') == control_raw, 'OLD_SCOPE_CHANGED_DURING_SNAPSHOT')
    summary = {'scope_state': 'INCOMPLETE_NOT_FROZEN_NOT_TRAINED', 'observed_at_ms': current,
        'producer_registration': consumer['registration'], 'consumer_config': prep['consumer_config'],
        'consumer_config_sha256': prep['consumer_config_sha256'], 'consumer_output_root': consumer['output_root'],
        'original_status_path': str(status_path), 'original_status_sha256': digest(status_raw),
        'original_dataset_path': str(dataset_path), 'original_dataset_sha256': digest(dataset_raw),
        'controller_status_sha256': digest(control_raw), 'settled_rows_preserved': len(rows), 'minimum_rows': 32,
        'observed_original_requests': status.get('observed_original_requests'), 'bound_opens': status.get('bound_opens'),
        'rows': [{**{key:row[key] for key in ('row_id', 'input_id', 'reward_id', 'episode_id')},
                  'canonical_row_sha256': digest(canonical(row))} for row in rows],
        'frozen_cohort_created': False, 'training_started': False, 'trial_registered': False,
        'counted_as_successor_training_rows': 0}
    return summary, {'OLD_SCOPE_STATUS.json': status_raw, 'OLD_SCOPE_DATASET.json': dataset_raw,
                     'OLD_CONTROLLER_STATUS.json': control_raw}


def native_unit(name, deadline_ms):
    need(re.fullmatch(r'heptatrader-current100k-newscope-[0-9a-f]{12}\.(service|timer)', name), 'UNIT_SCOPE')
    text = command(['systemctl', '--user', 'show', name, '--no-pager', '--property=' + ','.join(UNIT_PROPERTIES)], deadline_ms)
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def service_idle(observed):
    return all(value.get('MainPID') == '0' and value.get('ActiveState') in ('inactive', 'failed')
               for name, value in observed.items() if name.endswith('.service'))


def guard_spec(data, proof):
    service = next(name for name in proof['unit_candidates'] if name.endswith('.service'))
    path = Path.home() / '.config/systemd/user' / (service + '.d') / '90-pre-fit-retired.conf'
    raw = ('[Unit]\n# Explicit pre-fit retirement; original executor and request remain unchanged.\n'
           'ConditionPathExists=!' + str(data['output'] / 'RETIREMENT_INTENT.json') + '\n').encode()
    return path, raw


def verify_units(proof, deadline_ms, *, guard=None, retired=False, allow_transition=False):
    result = {}
    for name, text in proof['unit_candidates'].items():
        path = Path.home() / '.config/systemd/user' / name
        need(checked(path) == text.encode(), 'ORIGINAL_UNIT_BYTES_CHANGED:' + name)
        value = native_unit(name, deadline_ms)
        expected_drop = str(guard[0]) if guard and name.endswith('.service') else ''
        if guard and name.endswith('.service'):
            need(checked(guard[0]) == guard[1], 'RETIREMENT_GUARD_CHANGED')
        drops = shlex.split(value.get('DropInPaths', ''))
        allowed = ([expected_drop] if expected_drop else [])
        need(value.get('LoadState') == 'loaded' and value.get('FragmentPath') == str(path)
             and (drops == allowed or allow_transition and not drops)
             and (value.get('NeedDaemonReload') == 'no' or allow_transition and value.get('NeedDaemonReload') == 'yes'),
             'LOADED_UNIT_CHANGED:' + name)
        if name.endswith('.service'):
            settings = [line.split('=', 1) for line in text.splitlines() if '=' in line]
            values = dict(settings)
            need(value.get('WorkingDirectory') == values['WorkingDirectory'] and value.get('Type') == 'oneshot'
                 and 'argv[]=' + values['ExecStart'] + ' ;' in value.get('ExecStart', '')
                 and sorted(shlex.split(value.get('Environment', ''))) == sorted(v for k,v in settings if k == 'Environment'),
                 'LOADED_EXECUTOR_CHANGED')
        elif retired:
            need(value.get('UnitFileState') == 'disabled' and value.get('ActiveState') == 'inactive', 'TIMER_NOT_RETIRED')
        elif not allow_transition:
            need(value.get('UnitFileState') == 'enabled' and value.get('ActiveState') == 'active'
                 and value.get('SubState') == 'waiting' and value.get('NextElapseUSecRealtime') not in ('', '0', None, 'infinity'),
                 'TIMER_NOT_WAITING')
        else:
            need(value.get('UnitFileState') in ('enabled', 'disabled')
                 and value.get('ActiveState') in ('active', 'inactive'), 'UNKNOWN_TIMER_TRANSITION')
        result[name] = value
    return result


def run(args):
    need(type(args.deadline_ms) is int and 0 < args.deadline_ms - now_ms() <= MAX_ACTION_MS, 'FINITE_ACTION_DEADLINE_REQUIRED')
    repository(Path(__file__).resolve().parent.parent, args.expected_source_commit, args.deadline_ms)
    data = inputs(args); output = data['output']; binding = data['binding']
    with original_locks(data) as locks:
        absent_effects(data)
        proof = original_check(data['path'], data['sha256'], data['request'], args.deadline_ms)
        if data['previous']:
            previous = data['previous']
            old_proof = original_check(previous['path'], previous['sha256'], previous['request'], args.deadline_ms)
            need(service_idle(verify_units(old_proof, args.deadline_ms, retired=True)), 'PREDECESSOR_NOT_IDLE')
        guard = guard_spec(data, proof); intent_path = output / 'RETIREMENT_INTENT.json'
        intent = read(intent_path) if intent_path.exists() else None
        need(not guard[0].exists() or intent is not None, 'RETIREMENT_GUARD_WITHOUT_THIS_INTENT')
        need(intent is not None or not output.exists() or not any(output.iterdir()),
             'PARTIAL_STAGING_WITHOUT_INTENT_NO_SYSTEMD_EFFECT_AUTHORIZED')
        if intent is not None:
            need(intent.get('binding') == binding, 'RETIREMENT_INTENT_BINDING_CHANGED')
            for name, checksum in intent['snapshot_sha256'].items():
                need(digest(checked(output / name)) == checksum, 'PRESERVED_SCOPE_SNAPSHOT_CHANGED')
        loaded = verify_units(proof, args.deadline_ms, guard=guard if guard[0].exists() else None,
                              allow_transition=intent is not None)
        complete_path = output / 'PRE_FIT_RETIRED.json'
        if complete_path.exists():
            complete = read(complete_path)
            need(complete.get('binding') == binding and intent is not None
                 and complete.get('retirement_intent_sha256') == digest(checked(intent_path)), 'RETIREMENT_RECEIPT_CHANGED')
            need(service_idle(verify_units(proof, args.deadline_ms, guard=guard, retired=True)), 'RETIRED_SERVICE_NOT_IDLE')
            return {**complete, 'readback_at_ms': now_ms(), 'rechecked': True}
        need(service_idle(loaded), 'SERVICE_BUSY_NO_FORCE_STOP')
        summary, snapshot = waiting_snapshot(data)
        snapshot['ORIGINAL_EXECUTOR_CHECK.json'] = canonical(proof) + b'\n'
        if data['previous']:
            snapshot['ORIGINAL_PREDECESSOR_CHECK.json'] = canonical(old_proof) + b'\n'
        absent_effects(data)
        if not args.retire:
            return {'state': 'PRE_FIT_RETIREMENT_ELIGIBLE_NO_MUTATION' if intent is None else 'PRE_FIT_RETIREMENT_INTENT_REQUIRES_EXPLICIT_RESUME',
                'binding': binding, 'old_scope': summary, 'original_cpu_check': proof,
                'original_lock_order': [str(p) for p in locks], 'units': loaded,
                'planned_service_guard': {'path': str(guard[0]), 'sha256': digest(guard[1]), 'text': guard[1].decode()},
                'driver_activation_performed': False, 'service_changed': False, 'broker_orders_sent': 0}
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        if intent is None:
            need(not any(output.iterdir()), 'SEPARATE_EMPTY_RETIREMENT_ROOT_REQUIRED')
            for name, raw in snapshot.items():
                immutable(output / name, raw)
            intent = {'schema': SCHEMA, 'state': 'RETIREMENT_INTENT_SEALED_BEFORE_SYSTEMD_EFFECT', 'binding': binding,
                'at_ms': now_ms(), 'old_scope': summary, 'snapshot_sha256': {name:digest(raw) for name,raw in snapshot.items()},
                'original_cpu_check_sha256': digest(canonical(proof)), 'original_lock_order': [str(p) for p in locks],
                'service_guard': {'path': str(guard[0]), 'sha256': digest(guard[1])}}
            record(intent_path, intent)
        else:
            preserved = {r['row_id']:r['canonical_row_sha256'] for r in intent['old_scope']['rows']}
            current = {r['row_id']:r['canonical_row_sha256'] for r in summary['rows']}
            need(all(current.get(key) == value for key,value in preserved.items()), 'OLD_SCOPE_WITHDRAWN_OR_REWRITTEN')
        timer = next(name for name in proof['unit_candidates'] if name.endswith('.timer'))
        command(['systemctl', '--user', 'disable', '--now', timer], args.deadline_ms)
        idle_deadline = min(args.deadline_ms, now_ms() + 15000)
        while True:
            loaded = verify_units(proof, args.deadline_ms, guard=guard if guard[0].exists() else None,
                                  retired=True, allow_transition=guard[0].exists())
            if service_idle(loaded):
                break
            remaining(idle_deadline); time.sleep(min(.1, remaining(idle_deadline)))
        absent_effects(data)
        guard[0].parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        immutable(guard[0], guard[1])
        command(['systemctl', '--user', 'daemon-reload'], args.deadline_ms)
        final = verify_units(proof, args.deadline_ms, guard=guard, retired=True)
        need(service_idle(final), 'SERVICE_NOT_IDLE_AFTER_RETIREMENT')
        absent_effects(data)
        repository(Path(__file__).resolve().parent.parent, args.expected_source_commit, args.deadline_ms)
        complete = {'schema': SCHEMA, 'state': 'PRE_FIT_RETIRED', 'binding': binding, 'at_ms': now_ms(),
            'retirement_intent_sha256': digest(checked(intent_path)),
            'old_scope': {**intent['old_scope'], 'scope_state': 'INCOMPLETE_RETIRED_BEFORE_FROZEN_COHORT_OR_FIT'},
            'units': final, 'service_guard': intent['service_guard'],
            'original_requests_and_data_modified': False, 'continuation_complete_written': False,
            'training_started': False, 'trial_registered': False, 'registered_trials_stopped': False,
            'driver_activation_performed': False, 'broker_orders_sent': 0, 'current100k_p0_closed': False,
            'rollback': {'automatic': False, 'intent_and_receipt_must_be_retained': True,
                'require_separate_explicit_review': True, 'require_original_producer_active_and_native_safe': True,
                'require_no_frozen_cohort_fit_or_new_trial': True, 'require_all_successor_continuations_idle': True,
                'remove_only_exact_retirement_dropin_after_review': str(guard[0]),
                'never_delete_intent_to_bypass_condition': True}}
        record(complete_path, complete)
        return complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True); parser.add_argument('--sha256', required=True)
    parser.add_argument('--expected-source-commit', required=True)
    parser.add_argument('--next-producer-manifest', type=Path, required=True); parser.add_argument('--next-producer-sha256', required=True)
    parser.add_argument('--output-directory', type=Path, required=True); parser.add_argument('--deadline-ms', type=int, required=True)
    parser.add_argument('--retire', action='store_true')
    args = parser.parse_args(); os.umask(0o077)
    need(os.geteuid() != 0, 'ORIGINAL_UNPRIVILEGED_USER_REQUIRED')
    try:
        value = run(args)
    except (ValueError, OSError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        # Never infer failure means the disable/reload did not take effect.
        print(json.dumps({'state': 'PRE_FIT_RETIREMENT_HOLD_INSPECT_EXACT_INTENT_AND_UNITS',
            'reason': str(error)[-1200:], 'output_directory': str(args.output_directory),
            'effect_unknown_if_intent_exists': True, 'automatic_rollback': False,
            'driver_activation_performed': False, 'broker_orders_sent': 0}, sort_keys=True), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(value, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
