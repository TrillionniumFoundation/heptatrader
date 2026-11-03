#!/usr/bin/env python3
"""Current100k registration gates and a finite independent native wake candidate.

CPU closure checks remain separate. A fresh registration check is read-only,
reuses the original runtime/source validators and binds the *running* producer.
Preparing a wake writes only a new review directory; it never installs a unit,
starts a worker, registers a plan or changes the frozen trial's runtime.
"""
from __future__ import annotations
import argparse
import ast
import importlib
import importlib.util
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import time

SUPPORT_FILES = (
    'hepta_verified_loader.py', 'hepta_decision_attribution.py', 'hepta_price_bootstrap_gate.py',
    'hepta_native_reader.py', 'hepta_maintenance_latch.py',
    'install_risk_stop_fence.py', 'install_decision_attribution.py',
    'activate_prepared_driver.py', 'precheck_current100k_registration.py',
    'activate_current100k_warm_stage.py', 'current100k_priority_activation.py',
    'finite_plan_calendar.py',
    'current100k_background_guards.py',
    'retire_current100k_pre_fit.py',
)
# The current original worker/warm REQUEST predicate uses this same strict bound.
ORIGINAL_REQUEST_MAX_AGE_MS = 60000
ATOMIC_REGISTRATION_PROFILE = 'ATOMIC_SIGNED_PLAN_DIRECTORY_V1'


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def check_wake_deadline(deadline_ms, *, now_ms=None):
    now = int(time.time() * 1000) if now_ms is None else now_ms
    need(type(now) is int, 'WARM_REGISTRATION_CLOCK_INVALID')
    if deadline_ms is not None:
        need(type(deadline_ms) is int and now <= deadline_ms,
             'WARM_REGISTRATION_WAKE_DEADLINE_EXPIRED')
    return now


def check_precheck_source(c):
    """The imported observer shares the new immutable worker closure."""
    root = Path(c['worker_source_root']).resolve()
    inventory = c['worker_runtime_sha256']
    modules = {name[:-3]: 'scripts/' + name for name in SUPPORT_FILES}
    modules.update({
        'historical_training.common': 'research/historical_training/common.py',
        'historical_training.current100k': 'research/historical_training/current100k.py',
        'historical_training.current100k_value_trial': 'research/historical_training/current100k_value_trial.py',
        'laya_cell.financial_forward_protocol': 'learn/laya_cell/financial_forward_protocol.py',
        'laya_cell.current100k_warm_flat': 'learn/laya_cell/current100k_warm_flat.py',
        'laya_cell.financial_forward_source': 'learn/laya_cell/financial_forward_source.py',
        'laya_cell.current100k_forward': 'learn/laya_cell/current100k_forward.py',
        'laya_cell.stage_current100k_warm_flat': 'learn/laya_cell/stage_current100k_warm_flat.py',
        'laya_cell.resident_store': 'learn/laya_cell/resident_store.py',
    })
    for name, relative in modules.items():
        expected = root / relative; wanted = inventory.get(relative)
        need(wanted and expected.resolve() == expected
             and hashlib.sha256(expected.read_bytes()).hexdigest() == wanted,
             'WARM_REGISTRATION_SOURCE_PIN_CHANGED:' + name)
        loaded = sys.modules.get(name)
        if loaded is not None:
            origin = getattr(loaded, '__file__', None)
        else:
            specification = importlib.util.find_spec(name)
            origin = specification.origin if specification is not None else None
        need(origin is not None and Path(origin).resolve() == expected,
             'WARM_REGISTRATION_IMPORTED_SOURCE_OUTSIDE_RUNTIME:' + name)
        loaded = importlib.import_module(name)
        need(Path(loaded.__file__).resolve() == expected
             and hashlib.sha256(Path(loaded.__file__).read_bytes()).hexdigest() == wanted,
             'WARM_REGISTRATION_IMPORTED_SOURCE_CHANGED:' + name)


def registration_state(c, request):
    from laya_cell.current100k_forward import registration_state as inspect_existing
    return inspect_existing(c, Path(request['candidate_directory']), Path(request['dataset_path']),
        Path(request['prior_candidate_path']), [Path(path) for path in request['legacy_plans_roots']],
        registration_scope=request.get('registration_scope', 'SAME_PRODUCER_REFIT'), prior_plan_path=Path(request['prior_plan_path']),
        input_start_delay_ms=request['input_start_delay_ms'])


def registration_inputs(c, request_path, request_sha256, *, now_ms=None):
    """The original launcher's pre-write validation, shared without publication."""
    from historical_training.common import read, sha
    from historical_training.current100k import validate_config
    from laya_cell.financial_forward_protocol import validate_plan
    from laya_cell.current100k_warm_flat import check_profile
    need(request_path is not None and request_sha256 and sha(request_path) == request_sha256,
         'EXACT_NEW_SCOPE_REGISTRATION_REQUEST_REQUIRED')
    request = read(request_path)
    need(request['schema'] == 'heptatrader.current100k-warm-registration-request.v1'
         and type(request['expected_trial']) is int and request['expected_trial'] >= 2
         and request.get('registration_scope') in ('SAME_PRODUCER_REFIT', 'NEW_PRODUCER_SCOPE')
         and request['input_start_delay_ms'] == 900000,
         'INDEPENDENT_WARM_REGISTRATION_REQUEST_REQUIRED')
    for path, expected in request['input_sha256'].items():
        need(sha(path) == expected, 'REGISTRATION_ORIGINAL_INPUT_CHANGED')
    if c.get('cpu_standby_profile') is not None:
        from laya_cell.current100k_cpu_standby import check_profile as check_cpu_profile
        check_cpu_profile(c['cpu_standby_profile'])
        need(c.get('warm_flat_profile') is None, 'MIXED_FINITE_WORKER_PROFILE')
    else:
        check_profile(c.get('warm_flat_profile'))
        need(c.get('warm_flat_profile') is not None, 'EXPLICIT_WARM_PROFILE_REQUIRED')
    actual_paths = {str(path) for root in request['legacy_plans_roots']
                    for path in Path(root).glob('*/plan.json')}
    need(actual_paths == set(request['legacy_plan_paths']),
         'WARM_REGISTRATION_HISTORY_CHANGED_BEFORE_WRITE')
    history = [read(path) for path in request['legacy_plan_paths']]
    for plan in history:
        validate_plan(plan)
    need(sorted(plan['trial'] for plan in history) == list(range(1, request['expected_trial']))
         and len({plan['plan_id'] for plan in history}) == len(history),
         'WARM_REGISTRATION_COMPLETE_HISTORY_REQUIRED')
    prior = read(request['prior_plan_path'])
    need(prior in history and prior['trial'] == request['expected_trial'] - 1
         and str(Path(prior['candidate_directory']) / 'candidate.json') == request['prior_candidate_path'],
         'WARM_SAME_PRODUCER_PRIOR_PLAN_CHANGED')
    now = check_wake_deadline(None, now_ms=now_ms)
    if request['registration_scope'] == 'SAME_PRODUCER_REFIT':
        need(prior['producer_registration'] == c['producer_registration'],
             'WARM_SAME_PRODUCER_PRIOR_PLAN_CHANGED')
        not_before = prior['outcome_end_ms']
    else:
        from laya_cell.current100k_forward import validate_value_candidate, _new_producer_lineage
        candidate = validate_value_candidate(Path(request['candidate_directory']))
        previous = validate_value_candidate(Path(request['prior_candidate_path']).parent)
        data = read(request['dataset_path'])
        from historical_training.current100k_value_trial import selected_rows
        selected_rows(data, data)
        need(32 <= candidate['rows'] <= 64 and candidate['dataset_sha256'] == sha(request['dataset_path'])
             and candidate['row_ids'] == [row['row_id'] for row in data['rows']],
             'NEW_PRODUCER_SCOPE_COMPLETED_FIT_REQUIRED')
        _new_producer_lineage(c, data, previous, Path(request['prior_candidate_path']).parent,
            history, Path(request['prior_plan_path']))
        not_before = candidate['created_at_ms']
    need(type(request.get('registration_not_before_ms')) is int
         and request['registration_not_before_ms'] == not_before
         and now >= request['registration_not_before_ms'],
         'WARM_WAIT_PRIOR_OUTCOME_END_NO_REGISTRATION')
    validate_config({'automatic_trading': False, 'automatic_training': False,
                     'period_path': c['period_path'], 'period_sha256': c['period_sha256'],
                     'registration': c['producer_registration']})
    return request, prior


def fresh_original_request(requests, registration, behavior_model_id, now_ms):
    """Observe current producer activity; this never allocates a future sample."""
    eligible = []
    for rid, item in requests.items():
        need(isinstance(rid, str) and rid not in ('', '.', '..') and Path(rid).name == rid,
             'WARM_REGISTRATION_REQUEST_PATH_INVALID')
        request = item['request']; started = request.get('started_at_ms')
        if (request.get('request_id') == rid and type(started) is int
                and 0 <= now_ms - started < ORIGINAL_REQUEST_MAX_AGE_MS
                and request.get('position_at_request') == 0
                and request.get('model_id') == behavior_model_id
                and item.get('producer_manifest_sha256') == registration['sha256']):
            eligible.append((started, rid, item))
    need(eligible, 'WARM_REGISTRATION_NO_FRESH_ORIGINAL_FLAT_REQUEST')
    started, rid, item = max(eligible, key=lambda value: (value[0], value[1]))
    return {'request_id': rid, 'request_sha256': item['request_sha256'],
            'started_at_ms': started, 'observed_at_ms': now_ms,
            'age_ms': now_ms - started, 'future_trial_sample': False}


def running_producer(c, prior):
    """Reuse existing process/release and verified journal readers; no controls."""
    import activate_prepared_driver as driver
    from laya_cell.financial_forward_source import VerifiedIngressSnapshot
    registration = c['producer_registration']
    before = driver.running_driver('heptatrader-paper-driver.service', 'EUR.USD')
    need(before['manifest'] == registration['manifest']
         and before['manifest_sha256'] == registration['sha256'],
         'WARM_REGISTRATION_RUNNING_PRODUCER_CHANGED')
    snapshot = VerifiedIngressSnapshot(registration, c['period_sha256'])
    _, requests, _, issues = snapshot.read(registration, c['period_sha256'])
    need(not issues, 'WARM_REGISTRATION_ORIGINAL_SOURCE_UNAVAILABLE')
    after = driver.running_driver('heptatrader-paper-driver.service', 'EUR.USD')
    driver.same_driver(before, after)
    observation = fresh_original_request(requests, registration,
        prior['behavior_scope']['behavior_model_id'], int(time.time() * 1000))
    return {'unit': 'heptatrader-paper-driver.service',
            'manifest': after['manifest'], 'manifest_sha256': after['manifest_sha256'],
            'pid_key': list(after['pid_key']),
            'invocation_id': after['status']['InvocationID'],
            'release': after['release'],
            'runtime_sha256': after['runtime_sha256'], 'request_activity': observation}


class OriginalProducerAdmission:
    """Recheck the qualified producer inside the original registrar lock.

Invocation is bound before the sandbox, where /proc/<pid>/environ is readable.
The retained pidfd plus repeated original startticks, argv and immutable release
checks bind subsequent admission to that process; they do not claim a repeated
Invocation read after confinement. The callback never contacts systemd, refreshes
the request or allocates a different registration intent.
"""
    def __init__(self, c, request, proof, wake_deadline_ms=None):
        import activate_prepared_driver as driver
        self.driver = driver; self.producer = proof['producer']; self.request = request
        self.deadline = wake_deadline_ms; self.pidfd = None
        self.pid = int(self.producer['pid_key'][0]); self.key = tuple(self.producer['pid_key'])
        self.manifest = Path(self.producer['manifest']); self.release = Path(self.producer['release'])
        need(self.manifest == self.release / 'manifest.json'
             and self.manifest == Path(c['producer_registration']['manifest'])
             and self.producer['manifest_sha256'] == c['producer_registration']['sha256']
             and self.release.resolve() == self.release,
             'WARM_REGISTRATION_ADMISSION_PRODUCER_SCOPE_CHANGED')
        need(hasattr(os, 'pidfd_open'), 'WARM_REGISTRATION_PIDFD_REQUIRED')
        try:
            self.pidfd = os.pidfd_open(self.pid, 0)
            self.poller = select.poll(); self.poller.register(self.pidfd, select.POLLIN)
            self.expected_argv = [part.encode() for part in driver.argv_for(self.manifest)]
            values = [part.split(b'=', 1)[1] for part in driver.proc_bytes(self.pid, 'environ').split(b'\0')
                      if part.startswith(b'INVOCATION_ID=')]
            need(values == [self.producer['invocation_id'].encode()],
                 'WARM_REGISTRATION_PRODUCER_INVOCATION_CHANGED')
            self.initial_invocation_id = self.producer['invocation_id']
            self.read_only_paths = [str(self.release),
                *[f'/proc/{self.pid}/{name}' for name in ('stat', 'cmdline')]]
            self()
        except BaseException:
            self.close(); raise

    def close(self):
        if self.pidfd is not None:
            os.close(self.pidfd); self.pidfd = None

    def __enter__(self): return self

    def __exit__(self, *_): self.close()

    def _time(self):
        now = check_wake_deadline(self.deadline)
        need(now >= self.request['registration_not_before_ms'],
             'WARM_WAIT_PRIOR_OUTCOME_END_NO_REGISTRATION')
        started = self.producer['request_activity']['started_at_ms']
        need(0 <= now - started < ORIGINAL_REQUEST_MAX_AGE_MS,
             'WARM_REGISTRATION_REQUEST_EXPIRED_BEFORE_PUBLICATION')

    def __call__(self):
        self._time()
        need(self.pidfd is not None and not self.poller.poll(0)
             and self.driver.pid_key(self.pid) == self.key,
             'WARM_REGISTRATION_PRODUCER_EXITED_OR_CHANGED')
        argv = self.driver.proc_bytes(self.pid, 'cmdline').rstrip(b'\0').split(b'\0')
        need(argv == self.expected_argv, 'WARM_REGISTRATION_PRODUCER_ARGV_CHANGED')
        need(hashlib.sha256(owned_bytes(self.manifest)).hexdigest() == self.producer['manifest_sha256'],
             'WARM_REGISTRATION_PRODUCER_MANIFEST_CHANGED')
        for name, wanted in self.producer['runtime_sha256'].items():
            need(Path(name).name == name and name not in ('', '.', '..')
                 and hashlib.sha256(owned_bytes(self.release / name)).hexdigest() == wanted,
                 'WARM_REGISTRATION_PRODUCER_RUNTIME_CHANGED')
        need(not self.poller.poll(0) and self.driver.pid_key(self.pid) == self.key,
             'WARM_REGISTRATION_PRODUCER_EXITED_OR_CHANGED')
        self._time()


@contextmanager
def prepare_registrar_root(root):
    """Persist the output entry before the sandbox removes parent write access."""
    root = Path(root)
    need(root.is_absolute() and root.resolve() == root and root.parent.is_dir(),
         'WARM_REGISTRATION_EXISTING_CANONICAL_PARENT_REQUIRED')
    parent = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    child = None
    identity = lambda value: (value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode)
    try:
        before = os.fstat(parent)
        need(stat.S_ISDIR(before.st_mode) and before.st_uid == os.geteuid()
             and not before.st_mode & 0o022, 'WARM_REGISTRATION_PARENT_OWNER_OR_MODE')
        try: os.mkdir(root.name, 0o700, dir_fd=parent)
        except FileExistsError: pass
        child = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        opened = os.fstat(child)
        need(stat.S_ISDIR(opened.st_mode) and opened.st_uid == os.geteuid()
             and not opened.st_mode & 0o077, 'WARM_REGISTRATION_ROOT_OWNER_OR_MODE')
        os.fsync(child); os.fsync(parent)
        need(identity(before) == identity(os.fstat(parent)) == identity(root.parent.lstat())
             and identity(opened) == identity(os.fstat(child)) == identity(root.lstat())
             and root.resolve() == root, 'WARM_REGISTRATION_ROOT_CHANGED_DURING_SYNC')
        yield parent
    finally:
        if child is not None: os.close(child)
        os.close(parent)


def precheck_registration(c, request_path, request_sha256, *, wake_deadline_ms=None):
    check_wake_deadline(wake_deadline_ms)
    check_precheck_source(c)
    request, prior = registration_inputs(c, request_path, request_sha256)
    outcome = registration_state(c, request)
    need(outcome['state'] in ('EMPTY', 'PREPARED', 'PUBLISHED'), 'WARM_REGISTRATION_TARGET_STATE_UNKNOWN')
    if outcome['state'] == 'PUBLISHED':
        existing = outcome['plan']
        return request, {'state': 'REGISTRATION_ALREADY_COMPLETE_VERIFIED_NO_WRITE',
            'plan_id': existing['plan_id'], 'trial': existing['trial'],
            'registration_performed': False, 'gpu_loaded': False, 'broker_orders_sent': 0}
    producer = running_producer(c, prior)
    now = check_wake_deadline(wake_deadline_ms)
    need(now >= request['registration_not_before_ms'], 'WARM_WAIT_PRIOR_OUTCOME_END_NO_REGISTRATION')
    need(0 <= now - producer['request_activity']['started_at_ms'] < ORIGINAL_REQUEST_MAX_AGE_MS,
         'WARM_REGISTRATION_REQUEST_EXPIRED_DURING_CHECK')
    return request, {'state': ('RESUMABLE_PREPARED_REGISTRATION_ELIGIBLE_NOW_NO_WRITE'
        if outcome['state'] == 'PREPARED' else 'FRESH_REGISTRATION_ELIGIBLE_NOW_NO_WRITE'),
        'target_registration_state': outcome['state'],
        'prepared_plan_id': outcome['plan']['plan_id'] if outcome['state'] == 'PREPARED' else None,
        'observed_at_ms': now, 'expected_trial': request['expected_trial'],
        'registration_not_before_ms': request['registration_not_before_ms'],
        'request_sha256': request_sha256, 'producer': producer,
        'registration_performed': False, 'future_admission_granted': False,
        'gpu_loaded': False, 'broker_orders_sent': 0, 'old_plans_modified': False}


def owned_bytes(path, limit=16 * 1024 * 1024):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'WAKE_CANONICAL_PATH_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        first = os.fstat(stream.fileno())
        need(stat.S_ISREG(first.st_mode) and first.st_uid == os.geteuid()
             and first.st_nlink == 1 and not first.st_mode & 0o022 and first.st_size <= limit,
             'WAKE_OWNED_REGULAR_INPUT_REQUIRED')
        raw = stream.read(limit + 1)
        identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns, x.st_mode, x.st_uid, x.st_nlink)
        need(len(raw) == first.st_size and identity(first) == identity(os.fstat(stream.fileno()))
             == identity(path.lstat()), 'WAKE_INPUT_CHANGED_DURING_READ')
    return raw


def pinned_json(path, expected):
    raw = owned_bytes(path)
    need(re.fullmatch('[0-9a-f]{64}', expected or '')
         and hashlib.sha256(raw).hexdigest() == expected, 'WAKE_INPUT_SHA256_CHANGED')
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'WAKE_DUPLICATE_JSON_KEY'); out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('WAKE_NONFINITE_JSON')))


def wake_candidates(stage_path, stage_sha256, deadline_ms, mode='precheck', readmission_seconds=0,
                    selection_path=None, selection_sha256=None):
    """Generate exact separate units only; a timer event is not eligibility."""
    stage = pinned_json(stage_path, stage_sha256)
    need(stage.get('schema') == 'heptatrader.current100k-warm-flat-stage.v1'
         and stage.get('state') == 'STAGED_CPU_VERIFIED_NOT_REGISTERED'
         and stage.get('trial_registered') is False, 'WAKE_INDEPENDENT_WARM_STAGE_REQUIRED')
    config_info = stage['configs']['registrar']
    c = pinned_json(config_info['path'], config_info['sha256'])
    request = pinned_json(stage['registration_request'], stage['registration_request_sha256'])
    release = Path(stage['release'])
    need(release.is_absolute() and release.resolve() == release
         and c['worker_source_root'] == str(release), 'WAKE_RELEASE_SCOPE_CHANGED')
    # A new immutable stage is required. Never patch the old deployed closure.
    for name in SUPPORT_FILES:
        relative = 'scripts/' + name
        wanted = c['worker_runtime_sha256'].get(relative)
        need(wanted is not None and hashlib.sha256(owned_bytes(release / relative)).hexdigest() == wanted,
             'WAKE_REGISTRATION_CHECKER_NOT_STAGED')
    launcher = release / 'bin/current100k-warm-launch.py'
    need(hashlib.sha256(owned_bytes(launcher)).hexdigest() == c['worker_runtime_sha256'].get('bin/current100k-warm-launch.py'),
         'WAKE_LAUNCHER_CHANGED')
    expected = ['/usr/bin/python3', '-B', str(launcher), '--config', config_info['path'],
                '--sha256', config_info['sha256'], '--mode', 'register',
                '--request', stage['registration_request'], '--request-sha256', stage['registration_request_sha256']]
    need(stage['explicit_argv']['registrar'] == expected, 'WAKE_REGISTRAR_ARGV_CHANGED')
    not_before = request['registration_not_before_ms']
    scope = request.get('registration_scope', 'SAME_PRODUCER_REFIT')
    need(scope in ('SAME_PRODUCER_REFIT', 'NEW_PRODUCER_SCOPE')
         and stage.get('registration_scope', 'SAME_PRODUCER_REFIT') == scope,
         'WAKE_REGISTRATION_SCOPE_CHANGED')
    if scope == 'SAME_PRODUCER_REFIT':
        expected_not_before = stage['prior_outcome_end_ms']
    else:
        # New-producer lineage is checked again by the original registrar.
        # Its earliest registration time is the completed new candidate's
        # creation time, not the previous producer's still-open trial window.
        candidate_path = Path(request['candidate_directory']) / 'candidate.json'
        candidate_sha = request['input_sha256'].get(str(candidate_path))
        need(candidate_sha is not None, 'WAKE_NEW_CANDIDATE_PIN_REQUIRED')
        candidate = pinned_json(candidate_path, candidate_sha)
        expected_not_before = candidate.get('created_at_ms')
        need(candidate.get('schema') == 'heptatrader.current100k-value-candidate.v1'
             and candidate.get('candidate_id') == stage['candidate_id']
             and type(expected_not_before) is int,
             'WAKE_COMPLETED_NEW_CANDIDATE_REQUIRED')
    need(type(not_before) is int and not_before == expected_not_before
         and type(deadline_ms) is int and not_before < deadline_ms,
         'WAKE_EXPLICIT_FINITE_DEADLINE_REQUIRED')
    need(mode in ('precheck', 'register'), 'WAKE_MODE_INVALID')
    if mode == 'register':
        need(stage.get('registration_publish_profile') == ATOMIC_REGISTRATION_PROFILE,
             'WAKE_ATOMIC_REGISTRATION_PROFILE_REQUIRED')
        relative = 'learn/laya_cell/current100k_forward.py'
        source = owned_bytes(release / relative)
        need(hashlib.sha256(source).hexdigest() == c['worker_runtime_sha256'].get(relative),
             'WAKE_REGISTRATION_RUNTIME_CHANGED')
        profiles = [node.value.value for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'REGISTRATION_PUBLISH_PROFILE'
            and isinstance(node.value, ast.Constant)]
        need(profiles == [ATOMIC_REGISTRATION_PROFILE], 'WAKE_ATOMIC_REGISTRATION_SOURCE_REQUIRED')
    if mode == 'register':
        from activate_current100k_warm_stage import stage_inputs, ACTIVATION_PROFILE, SERVICE_TIMEOUT_SECONDS
        need(stage.get('registration_activation_profile') == ACTIVATION_PROFILE,
             'WAKE_COMPLETE_ACTIVATION_PROFILE_REQUIRED')
        relative = 'scripts/activate_current100k_warm_stage.py'
        source = owned_bytes(release / relative)
        profiles = [node.value.value for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'ACTIVATION_PROFILE'
            and isinstance(node.value, ast.Constant)]
        need(profiles == [ACTIVATION_PROFILE], 'WAKE_COMPLETE_ACTIVATION_SOURCE_REQUIRED')
        stage_inputs(stage_path, stage_sha256)
        need(selection_path is not None and selection_sha256 is not None, 'WAKE_PINNED_SELECTION_REQUEST_REQUIRED')
        selection = pinned_json(selection_path, selection_sha256)
        need(selection.get('schema') == 'heptatrader.current100k-shared-priority-selection.v1'
             and selection.get('activation_deadline_ms') == deadline_ms
             and set(selection.get('roles', {})) == {'trainer', 'evaluator'}, 'WAKE_SELECTION_REQUEST_SCOPE_CHANGED')
        argv = ['/usr/bin/python3', '-B', str(release / relative), '--stage', str(stage_path),
                '--stage-sha256', stage_sha256, '--selection-request', str(selection_path),
                '--selection-request-sha256', selection_sha256, '--wake-deadline-ms', str(deadline_ms)]
        timeout_seconds = SERVICE_TIMEOUT_SECONDS
    else:
        need(selection_path is None and selection_sha256 is None, 'WAKE_PRECHECK_DOES_NOT_SELECT_SERVICES')
        argv = expected + ['--registration-precheck', '--wake-deadline-ms', str(deadline_ms)]
        timeout_seconds = 180
    pythonpath = ':'.join(str(release / part) for part in ('research', 'learn', 'scripts'))
    need(stage['pythonpath'] == pythonpath, 'WAKE_PYTHONPATH_CHANGED')
    need(all(re.fullmatch(r'[-A-Za-z0-9_./:]+', value) for value in [*argv, str(release), pythonpath]),
         'WAKE_SYSTEMD_ARGUMENT_REQUIRES_ESCAPING')
    second = (not_before + 999) // 1000
    need(type(readmission_seconds) is int and (readmission_seconds == 0
         or 60 <= readmission_seconds <= 3600), 'WAKE_READMISSION_INTERVAL_INVALID')
    seconds = [second] if readmission_seconds == 0 else list(range(second, deadline_ms // 1000 + 1, readmission_seconds))
    need(1 <= len(seconds) <= 120, 'WAKE_FINITE_CALENDAR_BOUND')
    calendars = [datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') for value in seconds]
    calendar = calendars[0]
    name = 'heptatrader-current100k-registration-' + mode + '-' + stage_sha256[:12]
    service = '\n'.join(('[Unit]', 'Description=HeptaTrader finite Current100k registration ' + mode,
        '', '[Service]', 'Type=oneshot', 'RemainAfterExit=yes', 'ExecStart=' + ' '.join(argv),
        'WorkingDirectory=' + str(release), 'Environment=PYTHONPATH=' + pythonpath,
        'Environment=PYTHONDONTWRITEBYTECODE=1', 'Environment=OPENBLAS_NUM_THREADS=1',
        'Environment=OMP_NUM_THREADS=2', 'Environment=HF_HUB_OFFLINE=1',
        'Environment=TRANSFORMERS_OFFLINE=1', 'Environment=USE_TF=0', 'UMask=0077',
        'Nice=15', 'CPUQuota=50%', 'CPUWeight=10', 'MemoryMax=512M', 'TasksMax=96',
        'NoNewPrivileges=yes', 'RestrictSUIDSGID=yes', 'RestrictAddressFamilies=AF_UNIX',
        'LockPersonality=yes', 'Restart=no', 'TimeoutStartSec=' + str(timeout_seconds), 'TimeoutStopSec=10',
        'KillMode=control-group', 'SendSIGKILL=yes', 'StandardOutput=journal', 'StandardError=journal', ''))
    timer = '\n'.join(('[Unit]', 'Description=HeptaTrader bounded calendar registration ' + mode,
        '', '[Timer]', *['OnCalendar=' + item for item in calendars], 'AccuracySec=1s', 'RandomizedDelaySec=0',
        'Persistent=true', 'RemainAfterElapse=yes', 'WakeSystem=false', 'Unit=' + name + '.service',
        '', '[Install]', 'WantedBy=timers.target', ''))
    return {'schema': 'heptatrader.current100k-registration-wake.v1', 'stage': str(stage_path),
        'stage_sha256': stage_sha256, 'mode': mode, 'not_before_ms': not_before,
        'registration_scope': scope,
        'deadline_ms': deadline_ms, 'calendar_utc': calendar, 'calendars_utc': calendars,
        'readmission_seconds': readmission_seconds, 'calendar_events': len(calendars),
        'success_keeps_oneshot_active_without_a_process': True, 'argv': argv,
        'service_name': name + '.service', 'timer_name': name + '.timer',
        'service': service, 'timer': timer, 'registration_performed': False,
        'installed': False, 'trial4_modified': False, 'automatic_process_restart': False,
        'bounded_calendar_readmission': len(calendars) > 1,
        'exactly_once_execution_guaranteed': False,
        'selection_request': str(selection_path) if selection_path is not None else None,
        'selection_request_sha256': selection_sha256,
        'registration_activation_profile': stage.get('registration_activation_profile') if mode == 'register' else None,
        'physical_wakeup_guaranteed': False, 'catch_up_requires_running_user_manager': True}


def publish_wake(output, result, *, resume=False):
    from laya_cell.stage_current100k_warm_flat import staging_directory
    from laya_cell.immutable_files import write_immutable
    output = Path(output); result = dict(result)
    documents = {result['service_name'] + '.candidate': result['service'].encode(),
                 result['timer_name'] + '.candidate': result['timer'].encode()}
    result['files'] = {name: hashlib.sha256(raw).hexdigest() for name, raw in documents.items()}
    documents['WAKE_STAGE.json'] = (json.dumps(result, sort_keys=True, indent=2) + '\n').encode()
    staging_directory(output, 'WAKE_STAGING_REQUEST.json', {
        'schema': 'heptatrader.current100k-wake-staging-request.v1',
        'stage': result['stage'], 'stage_sha256': result['stage_sha256'], 'mode': result['mode'],
        'selection_request_sha256': result['selection_request_sha256'], 'deadline_ms': result['deadline_ms'],
        'files_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in documents.items()},
        'registration_performed': False, 'services_changed': [],
    }, resume=resume)
    for name, raw in documents.items():
        write_immutable(output / name, raw, 0o400, 'IMMUTABLE_WAKE_STAGING_BYTES_CHANGED')
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--stage-sha256', required=True)
    parser.add_argument('--deadline-ms', type=int, required=True)
    parser.add_argument('--mode', choices=('precheck', 'register'), default='precheck')
    parser.add_argument('--readmission-seconds', type=int, default=0)
    parser.add_argument('--selection-request', type=Path); parser.add_argument('--selection-request-sha256')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume-staging', action='store_true')
    args = parser.parse_args()
    need(os.geteuid() != 0, 'ORIGINAL_UNPRIVILEGED_USER_REQUIRED')
    result = wake_candidates(args.stage, args.stage_sha256, args.deadline_ms, args.mode, args.readmission_seconds,
                             args.selection_request, args.selection_request_sha256)
    output = args.output
    result = publish_wake(output, result, resume=args.resume_staging)
    print(json.dumps({'state': 'WAKE_CANDIDATE_PREPARED_NOT_INSTALLED', 'receipt': str(output / 'WAKE_STAGE.json'),
                      'service': result['service_name'], 'timer': result['timer_name'],
                      'calendar_utc': result['calendar_utc'], 'registration_performed': False}, sort_keys=True))


if __name__ == '__main__':
    main()
