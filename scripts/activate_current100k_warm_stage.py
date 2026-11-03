#!/usr/bin/env python3
"""Activate only the signed plan of one independent Current100k warm stage.

The registrar remains the original sandboxed entry. Resume never shifts its
window or registers a replacement; native units are separate from trial4.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

from precheck_current100k_registration import (
    need, owned_bytes, pinned_json, check_wake_deadline, check_precheck_source,
    precheck_registration, registration_state,
)

ACTIVATION_PROFILE = 'SAME_PLAN_FINITE_WARM_ACTIVATION_V1'
TERMINAL_EVALUATION_DELAY_MS = 261000
ACTIVATION_BUDGET_MS = 1440000
SERVICE_TIMEOUT_SECONDS = 1500
REGISTRAR_TIMEOUT_SECONDS = 180
POST_REGISTRATION_RESERVE_SECONDS = 180


class WakeBudget:
    """One monotonic budget for this invocation, additionally bounded by UTC."""
    def __init__(self, deadline_ms):
        self.deadline_ms = deadline_ms
        self.started = time.monotonic()
        self.stop = self.started + ACTIVATION_BUDGET_MS / 1000
        self.remaining()

    def remaining(self):
        now = check_wake_deadline(self.deadline_ms)
        value = min(self.stop - time.monotonic(), (self.deadline_ms - now) / 1000)
        need(value > 0, 'WARM_ACTIVATION_TOTAL_BUDGET_EXPIRED')
        return value

    def timeout(self, limit): return min(limit, self.remaining())

    def constrain(self, deadline_ms):
        self.deadline_ms = min(self.deadline_ms, deadline_ms)
        self.remaining()

    def require(self, seconds):
        need(self.remaining() >= seconds, 'WARM_ACTIVATION_INSUFFICIENT_REMAINING_BUDGET_NO_REGISTRATION')


def stage_inputs(stage_path, stage_sha256):
    stage = pinned_json(stage_path, stage_sha256)
    need(stage.get('schema') == 'heptatrader.current100k-warm-flat-stage.v1'
         and stage.get('state') == 'STAGED_CPU_VERIFIED_NOT_REGISTERED'
         and stage.get('trial_registered') is False
         and stage.get('registration_activation_profile') == ACTIVATION_PROFILE, 'WARM_ACTIVATION_INDEPENDENT_STAGE_REQUIRED')
    from historical_training.common import digest
    configs = {}; shared = None
    for role in ('registrar', 'predictor', 'evaluator'):
        info = stage['configs'][role]; c = pinned_json(info['path'], info['sha256'])
        need(c['signing_role'] == role
             and Path(info['path']).name == role + '-' + digest(c) + '.json'
             and c['worker_source_root'] == stage['release']
             and c['forward_root'] == stage['forward_root'], 'WARM_ACTIVATION_ROLE_CONFIG_CHANGED')
        common = {key: value for key, value in c.items() if key not in ('signing_role', 'role_key_path')}
        if shared is None: shared = common
        need(common == shared, 'WARM_ACTIVATION_ROLE_CONFIGS_DISAGREE')
        configs[role] = c
    worker_profile = 'cpu-standby-v2' if configs['registrar'].get('cpu_standby_profile') is not None else 'warm-flat-v1'
    need(stage.get('worker_profile', 'warm-flat-v1') == worker_profile, 'WARM_ACTIVATION_WORKER_PROFILE_CHANGED')
    request = pinned_json(stage['registration_request'], stage['registration_request_sha256'])
    need(stage.get('registration_scope', 'SAME_PRODUCER_REFIT')
         == request.get('registration_scope', 'SAME_PRODUCER_REFIT'), 'WARM_ACTIVATION_REGISTRATION_SCOPE_CHANGED')
    release = Path(stage['release']); launcher = release / 'bin/current100k-warm-launch.py'
    need(stage['pythonpath'] == ':'.join(str(release / part) for part in ('research', 'learn', 'scripts')),
         'WARM_ACTIVATION_PYTHONPATH_CHANGED')
    for role, mode in (('registrar', 'register'), ('predictor', 'predict'), ('evaluator', 'evaluate')):
        c = configs[role]; info = stage['configs'][role]
        python = c['python'] if role == 'predictor' else '/usr/bin/python3'
        command = [python, '-B', str(launcher), '--config', info['path'], '--sha256', info['sha256'], '--mode', mode]
        if role == 'registrar': command += ['--request', stage['registration_request'], '--request-sha256', stage['registration_request_sha256']]
        need(stage['explicit_argv'][role] == command, 'WARM_ACTIVATION_ARGV_CHANGED')
    check_precheck_source(configs['registrar'])
    return stage, configs, request


def child_environment(stage):
    return dict(os.environ, PYTHONPATH=stage['pythonpath'], PYTHONDONTWRITEBYTECODE='1',
        OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='2', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', USE_TF='0')


def child(stage, argv, timeout=180):
    result = subprocess.run(argv, cwd=stage['release'], env=child_environment(stage),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False)
    need(result.returncode == 0, 'WARM_ACTIVATION_CHILD_FAILED:' + str(result.returncode) + ':' + result.stderr[-500:])
    need(len(result.stdout) <= 131072, 'WARM_ACTIVATION_CHILD_OUTPUT_BOUND')
    return json.loads(result.stdout)


def cpu_checks(stage, budget):
    for role in ('registrar', 'predictor', 'evaluator'):
        proof = child(stage, [*stage['explicit_argv'][role], '--check-only'], timeout=budget.timeout(120))
        need(proof.get('state') == 'CPU_FORWARD_CLOSURE_VERIFIED'
             and proof.get('role') == role and proof.get('gpu_loaded') is False
             and proof.get('worker_admission_granted') is False,
             'WARM_ACTIVATION_CPU_CLOSURE_FAILED:' + role)


def predictor_start_ms(plan):
    start = plan['input_start_ms']
    if plan.get('cpu_standby_profile') is not None:
        from laya_cell.current100k_cpu_standby import check_profile, PROFILE
        check_profile(plan['cpu_standby_profile'])
        need(plan.get('warm_flat_profile') is None, 'MIXED_FINITE_WORKER_PROFILE')
        start -= PROFILE['preroll_ms']
        need(start > plan['registered_at_ms'], 'CPU_PREROLL_MUST_FOLLOW_REGISTRATION')
    return start


def plan_timer(service, plan, mode):
    from finite_plan_calendar import calendar_terms
    from laya_cell.financial_forward_protocol import validate_plan
    validate_plan(plan)
    need(mode in ('predict', 'evaluate'), 'WARM_ACTIVATION_TIMER_ROLE')
    end = plan['input_end_ms'] if mode == 'predict' else plan['outcome_end_ms']
    start = predictor_start_ms(plan) if mode == 'predict' else plan['input_start_ms']
    terms = calendar_terms(start, end, step_seconds=1 if mode == 'predict' else 60)
    if mode == 'evaluate':
        final = datetime.fromtimestamp((plan['outcome_end_ms'] + TERMINAL_EVALUATION_DELAY_MS + 999) // 1000, timezone.utc)
        terms.append(final.strftime('%Y-%m-%d %H:%M:%S UTC'))
    return '\n'.join(('[Unit]', f'Description=HeptaTrader finite Current100k warm trial {plan["trial"]} {mode}', '',
        '[Timer]', *['OnCalendar=' + term for term in terms], 'AccuracySec=1s', 'RandomizedDelaySec=0',
        'Persistent=false', 'RemainAfterElapse=yes', 'WakeSystem=false', 'Unit=' + service,
        '', '[Install]', 'WantedBy=timers.target', ''))


def role_candidates(stage, configs, plan, priority):
    """Pure deterministic role configuration/unit bytes; no service selection."""
    from historical_training.common import digest, canonical
    from laya_cell.stage_current100k_warm_flat import finite_unit
    need(plan['trial'] == stage['expected_trial'] and plan['registry_root'] == stage['forward_root']
         and plan['worker_runtime_inventory_digest'] == configs['registrar']['worker_runtime_inventory_digest'],
         'WARM_ACTIVATION_SIGNED_PLAN_SCOPE_CHANGED')
    need(plan.get('cpu_standby_profile') == configs['registrar'].get('cpu_standby_profile'),
         'WARM_ACTIVATION_SIGNED_WORKER_PROFILE_CHANGED')
    worker_profile = 'cpu-standby-v2' if plan.get('cpu_standby_profile') is not None else 'warm-flat-v1'
    output = Path(stage['forward_root']) / 'activation' / plan['plan_id']
    documents = {}; units = {}; choices = {}
    for role, mode in (('predictor', 'predict'), ('evaluator', 'evaluate')):
        c = copy.deepcopy(configs[role])
        c.update(guard_config=priority['wrapper'], guard_config_sha256=priority['wrapper_sha256'])
        path = output / (role + '-' + digest(c) + '.json'); data = canonical(c) + b'\n'
        documents[str(path)] = data; checksum = hashlib.sha256(data).hexdigest()
        name = f'heptatrader-current100k-warm-trial{plan["trial"]}-{role}.service'
        text = finite_unit(mode, path, checksum, Path(stage['release']), c['python'] if mode == 'predict' else '/usr/bin/python3',
                           plan['trial'], worker_profile=worker_profile)
        units[name] = text.encode(); units[name.removesuffix('.service') + '.timer'] = plan_timer(name, plan, mode).encode()
        choices[role] = {'path': str(path), 'sha256': checksum, 'service': name,
            'timer': name.removesuffix('.service') + '.timer', 'config': c}
    return {'documents': documents, 'units': units, 'roles': choices, 'directory': str(output),
            'plan_id': plan['plan_id'], 'terminal_evaluation_delay_ms': TERMINAL_EVALUATION_DELAY_MS}


def native_unit(name, budget=None):
    need(re.fullmatch(r'heptatrader-current100k-warm-trial(?:[2-9]|[1-9][0-9]+)-(predictor|evaluator)\.(service|timer)', name),
         'WARM_ACTIVATION_UNIT_NAME_SCOPE')
    keys = ('LoadState', 'ActiveState', 'SubState', 'FragmentPath', 'DropInPaths',
            'NeedDaemonReload', 'ExecStart', 'UnitFileState', 'MainPID', 'NextElapseUSecRealtime',
            'WorkingDirectory', 'Environment', 'Type', 'Result', 'ExecMainStatus')
    result = subprocess.run(['systemctl', '--user', 'show', name, '--no-pager',
        '--property=' + ','.join(keys)], text=True, capture_output=True, timeout=budget.timeout(15) if budget is not None else 15)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    need(result.returncode in (0, 1) and values.get('LoadState') in ('loaded', 'not-found'),
         'WARM_ACTIVATION_UNIT_INSPECTION_FAILED')
    if budget is not None: budget.remaining()
    return values


def empty_role_destinations(stage, budget=None):
    trial = stage['expected_trial']; need(type(trial) is int and trial >= 2, 'WARM_ACTIVATION_TRIAL_NUMBER')
    folder = Path.home() / '.config/systemd/user'
    need(folder.is_dir() and folder.resolve() == folder, 'WARM_ACTIVATION_UNIT_DIRECTORY_SCOPE')
    for role in ('predictor', 'evaluator'):
        for kind in ('service', 'timer'):
            name = f'heptatrader-current100k-warm-trial{trial}-{role}.{kind}'; path = folder / name
            need(not path.exists() and not path.is_symlink(), 'WARM_ACTIVATION_NEW_UNIT_ALREADY_EXISTS')
            current = native_unit(name, budget)
            need(current['LoadState'] == 'not-found' and not current.get('FragmentPath')
                 and not current.get('DropInPaths') and current.get('MainPID', '0') == '0'
                 and current.get('ActiveState') == 'inactive', 'WARM_ACTIVATION_NEW_UNIT_ALREADY_LOADED')


def selected_unit_bytes(candidates, *, require_loaded=False, budget=None):
    folder = Path.home() / '.config/systemd/user'; observed = {}
    for name, raw in candidates['units'].items():
        path = folder / name
        if path.exists() or path.is_symlink():
            need(owned_bytes(path) == raw, 'WARM_ACTIVATION_UNIT_BYTES_CONFLICT:' + name)
        else: need(not require_loaded, 'WARM_ACTIVATION_UNIT_MISSING:' + name)
        current = native_unit(name, budget); observed[name] = current
        if current['LoadState'] == 'loaded':
            need(current.get('FragmentPath') == str(path) and not current.get('DropInPaths'),
                 'WARM_ACTIVATION_UNIT_SELECTION_CONFLICT:' + name)
        if require_loaded:
            need(current['LoadState'] == 'loaded' and current.get('NeedDaemonReload') == 'no',
                 'WARM_ACTIVATION_UNIT_NOT_RELOADED:' + name)
            if name.endswith('.service'):
                command = next(line.removeprefix('ExecStart=') for line in raw.decode().splitlines() if line.startswith('ExecStart='))
                need('argv[]=' + command + ' ;' in current.get('ExecStart', ''), 'WARM_ACTIVATION_LOADED_ARGV_CHANGED:' + name)
            else:
                need(current.get('ActiveState') == 'active' and current.get('SubState') == 'waiting'
                     and current.get('UnitFileState') == 'enabled'
                     and current.get('NextElapseUSecRealtime') not in (None, '', '0', 'infinity'),
                     'WARM_ACTIVATION_TIMER_NOT_WAITING:' + name)
    return observed


def install_roles(stage, candidates, deadline_ms, budget):
    from laya_cell.immutable_files import write_immutable
    check_wake_deadline(deadline_ms); budget.remaining(); selected_unit_bytes(candidates, budget=budget)
    output = Path(candidates['directory']); output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path, raw in candidates['documents'].items():
        budget.remaining()
        write_immutable(Path(path), raw, 0o400, 'WARM_ACTIVATION_ROLE_CONFIG_CONFLICT')
    # All three original role closures passed before registration. The only
    # derived fields are the Guard path/SHA, verified by prepare_priority using
    # the original Guard validator. Do not spend the fixed input lead on a
    # second identical pair of CPU checks.
    budget.remaining()
    check_wake_deadline(deadline_ms)
    folder = Path.home() / '.config/systemd/user'
    for name, raw in candidates['units'].items():
        budget.remaining()
        write_immutable(folder / name, raw, 0o600, 'WARM_ACTIVATION_UNIT_BYTES_CONFLICT')
    from current100k_priority_activation import _sync_selected_files
    documents = {**candidates['documents'], **{str(folder / name): raw for name, raw in candidates['units'].items()}}
    _sync_selected_files(documents, (folder, output, output.parent, output.parent.parent), remaining_seconds=budget.remaining)
    budget.remaining()


def enable_roles(candidates, deadline_ms, budget):
    check_wake_deadline(deadline_ms)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=budget.timeout(30))
    check_wake_deadline(deadline_ms)
    timers = [item['timer'] for item in candidates['roles'].values()]
    subprocess.run(['systemctl', '--user', 'enable', '--now', *timers], check=True,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=budget.timeout(45))
    return selected_unit_bytes(candidates, require_loaded=True, budget=budget)


def verify_native_execution(stage_path, stage_sha256):
    """Read-only live acceptance, distinct from a historical activation receipt."""
    from current100k_priority_activation import _inputs, verify_prepared_priority, _environment
    stage, configs, request = stage_inputs(stage_path, stage_sha256)
    observed = registration_state(configs['registrar'], request)
    need(observed['state'] == 'PUBLISHED', 'WARM_LIVE_SIGNED_PLAN_REQUIRED')
    plan = observed['plan']; root = Path(stage['forward_root'])
    plan_path = root / 'plans' / plan['plan_id'] / 'plan.json'
    plan_sha = hashlib.sha256(owned_bytes(plan_path)).hexdigest()
    status = json.loads(owned_bytes(root / 'activation-status.json'))
    need(status.get('state') == 'SAME_PLAN_NATIVE_WARM_EXECUTION_SCHEDULED'
         and status.get('plan_id') == plan['plan_id'] and status.get('plan_path') == str(plan_path)
         and status.get('plan_sha256') == plan_sha, 'WARM_LIVE_ACTIVATION_RECEIPT_CHANGED')
    now = int(time.time()*1000)
    if now >= plan['input_end_ms']:
        return {'state': 'REGISTERED_INPUT_WINDOW_ELAPSED_NO_LIVE_ACCEPTANCE',
            'plan_id': plan['plan_id'], 'trial': plan['trial'], 'plan_path': str(plan_path),
            'plan_sha256': plan_sha, 'at_ms': now, 'current100k_p0_closed': False,
            'original_window_modified': False, 'broker_orders_sent': 0}
    priority, _ = _inputs(stage, configs['registrar'], plan_path, plan)
    verify_prepared_priority(stage, configs['registrar'], plan_path, plan, priority)
    candidates = role_candidates(stage, configs, plan, priority)
    need(status.get('unit_sha256') == {name: hashlib.sha256(raw).hexdigest() for name, raw in candidates['units'].items()}
         and status.get('configs') == {role: {key: value for key, value in info.items() if key != 'config'}
             for role, info in candidates['roles'].items()}, 'WARM_LIVE_ROLE_BINDING_CHANGED')
    for path, raw in candidates['documents'].items():
        need(owned_bytes(path) == raw, 'WARM_LIVE_ROLE_CONFIG_CHANGED:' + path)
    units = selected_unit_bytes(candidates, require_loaded=True)
    for role, info in candidates['roles'].items():
        current = units[info['service']]
        need(current.get('Type') == 'oneshot' and current.get('WorkingDirectory') == stage['release']
             and current.get('ActiveState') in ('inactive', 'active', 'activating'),
             'WARM_LIVE_ROLE_NOT_RUNNABLE:' + role)
        if current['ActiveState'] == 'inactive':
            need(current.get('Result') == 'success' and current.get('ExecMainStatus') == '0',
                 'WARM_LIVE_ROLE_LAST_RUN_FAILED:' + role)
        expected_environment = {}
        for line in candidates['units'][info['service']].decode().splitlines():
            if line.startswith('Environment='):
                expected_environment.update(_environment(line.removeprefix('Environment=')))
        need(_environment(current.get('Environment', '')) == expected_environment,
             'WARM_LIVE_ROLE_ENVIRONMENT_CHANGED:' + role)
    return {'state': 'REGISTERED_NATIVE_EXECUTION_LIVE_VERIFIED', 'plan_id': plan['plan_id'],
        'trial': plan['trial'], 'plan_path': str(plan_path), 'plan_sha256': plan_sha, 'at_ms': now,
        'activation_before_input_start': status['activation_before_input_start'],
        'units': units, 'role_configs_verified': True, 'timers_enabled_active_and_next_event_verified': True,
        'current100k_p0_closed': False, 'same_request_preopen_prediction_chain_verified': False,
        'registration_performed': False, 'services_changed': [], 'broker_orders_sent': 0}


def activate(stage_path, stage_sha256, selection_path, selection_sha256, deadline_ms):
    import stat
    from current100k_priority_activation import (precheck_shared_priority, prepare_priority,
        select_shared_priority, activation_window)
    from laya_cell.resident_store import atomic
    budget = WakeBudget(deadline_ms)
    stage, configs, request = stage_inputs(stage_path, stage_sha256); config = configs['registrar']
    selection = pinned_json(selection_path, selection_sha256)
    need(selection.get('activation_deadline_ms') == deadline_ms, 'WARM_ACTIVATION_SHARED_DEADLINE_CHANGED')
    cpu_checks(stage, budget)
    _, eligibility = precheck_registration(config, stage['registration_request'],
        stage['registration_request_sha256'], wake_deadline_ms=deadline_ms)
    if eligibility['state'] != 'REGISTRATION_ALREADY_COMPLETE_VERIFIED_NO_WRITE':
        empty_role_destinations(stage, budget)
        precheck_shared_priority(stage, config, selection, remaining_seconds=budget.remaining)
        if eligibility['target_registration_state'] == 'EMPTY':
            budget.require(REGISTRAR_TIMEOUT_SECONDS + selection['drain_timeout_seconds']
                           + POST_REGISTRATION_RESERVE_SECONDS)
    # Exactly one call per invocation. A lost result is inspected on a later
    # bounded native event; it is never blindly reissued in this invocation.
    registration = child(stage, [*stage['explicit_argv']['registrar'], '--wake-deadline-ms', str(deadline_ms)],
                         timeout=budget.timeout(REGISTRAR_TIMEOUT_SECONDS))
    need(registration.get('state') in ('REGISTERED_SAME_PRODUCER_WARM_PROFILE',
         'REGISTERED_NEW_PRODUCER_SCOPE_WARM_PROFILE',
         'REGISTRATION_ALREADY_COMPLETE_DURABLE_VERIFIED'), 'WARM_ACTIVATION_REGISTRAR_RESULT_UNKNOWN')
    observed = registration_state(config, request)
    need(observed['state'] == 'PUBLISHED', 'WARM_ACTIVATION_SIGNED_PLAN_NOT_PUBLISHED')
    plan = observed['plan']; plan_path = Path(config['forward_root']) / 'plans' / plan['plan_id'] / 'plan.json'
    need(registration['plan_id'] == plan['plan_id'] and registration['plan_sha256'] == hashlib.sha256(owned_bytes(plan_path)).hexdigest(),
         'WARM_ACTIVATION_REGISTRAR_RESULT_CHANGED')
    window = activation_window(selection, plan)
    # A newly admitted or prepared intent must finish this first activation
    # before its original input start. A later published-plan invocation may
    # repair only that same window, explicitly reporting lost initial coverage.
    if eligibility['state'] != 'REGISTRATION_ALREADY_COMPLETE_VERIFIED_NO_WRITE':
        budget.constrain(predictor_start_ms(plan) - 1)
    else: budget.constrain(window['effective_deadline_ms'])
    root = Path(config['forward_root'])
    lock_path = root / 'activation.lock'; lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(lock)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
             and not info.st_mode & 0o077, 'WARM_ACTIVATION_LOCK_SCOPE')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = {'state': 'REGISTERED_ACTIVATION_PENDING', 'plan_id': plan['plan_id'],
            'plan_path': str(plan_path), 'plan_sha256': registration['plan_sha256'],
            'input_start_ms': plan['input_start_ms'], 'input_end_ms': plan['input_end_ms'],
            'outcome_end_ms': plan['outcome_end_ms'], 'old_trial_modified': False,
            'broker_orders_sent': 0, 'future_validation_proven': False}
        atomic(root / 'activation-status.json', {**status, 'at_ms': int(time.time() * 1000)})
        candidate = prepare_priority(stage, config, plan_path, plan, remaining_seconds=budget.remaining)
        roles = role_candidates(stage, configs, plan, candidate)
        install_roles(stage, roles, window['effective_deadline_ms'], budget)
        priority = select_shared_priority(stage, config, plan_path, plan, candidate, selection,
                                          remaining_seconds=budget.remaining)
        units = enable_roles(roles, window['effective_deadline_ms'], budget)
        budget.remaining()
        result = {**status, 'state': 'SAME_PLAN_NATIVE_WARM_EXECUTION_SCHEDULED',
            'at_ms': int(time.time() * 1000), 'priority': priority, 'units': units,
            'configs': {role: {key: value for key, value in info.items() if key != 'config'}
                        for role, info in roles['roles'].items()},
            'unit_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in roles['units'].items()},
            'registration_performed': registration['registration_performed'],
            'activation_before_input_start': int(time.time() * 1000) < plan['input_start_ms'],
            'activation_before_predictor_start': int(time.time() * 1000) < predictor_start_ms(plan),
            'wake_elapsed_seconds': time.monotonic() - budget.started,
            'wake_budget_ms': ACTIVATION_BUDGET_MS}
        atomic(root / 'activation-status.json', result)
        return result
    finally: os.close(lock)


def restore_readmission_only(stage_path, stage_sha256, selection_path, selection_sha256, cleanup_deadline_ms):
    """Bounded cleanup of the original timer; no activation deadline renewal."""
    import stat
    from current100k_priority_activation import read_prepared_priority, restore_shared_readmission
    now = int(time.time() * 1000)
    need(type(cleanup_deadline_ms) is int and 0 < cleanup_deadline_ms - now <= 180000,
         'READMISSION_CLEANUP_EXPLICIT_FINITE_DEADLINE_REQUIRED')
    budget = WakeBudget(cleanup_deadline_ms)
    stage, configs, registration_request = stage_inputs(stage_path, stage_sha256)
    config = configs['registrar']; selection = pinned_json(selection_path, selection_sha256)
    need(config.get('background_guards') is not None and selection.get('readmission_timers') is not None,
         'READMISSION_CLEANUP_NEW_PROFILE_ONLY')
    observed = registration_state(config, registration_request)
    need(observed['state'] == 'PUBLISHED', 'READMISSION_CLEANUP_EXISTING_PLAN_REQUIRED')
    plan = observed['plan']; root = Path(config['forward_root'])
    plan_path = root / 'plans' / plan['plan_id'] / 'plan.json'
    # Never create a missing activation lock or candidate as a cleanup side effect.
    lock = os.open(root / 'activation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(lock)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
             and not info.st_mode & 0o077, 'WARM_ACTIVATION_LOCK_SCOPE')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        budget.remaining()
        candidate = read_prepared_priority(stage, config, plan_path, plan)
        result = restore_shared_readmission(stage, config, plan_path, plan, candidate, selection,
                                            remaining_seconds=budget.remaining)
        return {**result, 'cleanup_deadline_ms': cleanup_deadline_ms,
            'original_activation_deadline_ms': selection['activation_deadline_ms']}
    finally:
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True); parser.add_argument('--stage-sha256', required=True)
    parser.add_argument('--selection-request', type=Path); parser.add_argument('--selection-request-sha256')
    parser.add_argument('--wake-deadline-ms', type=int)
    parser.add_argument('--verify-native-execution', action='store_true')
    parser.add_argument('--restore-readmission-only', action='store_true')
    parser.add_argument('--cleanup-deadline-ms', type=int)
    args = parser.parse_args(); os.umask(0o077)
    need(os.geteuid() != 0, 'ORIGINAL_UNPRIVILEGED_USER_REQUIRED')
    if args.verify_native_execution:
        need(args.selection_request is None and args.selection_request_sha256 is None
             and args.wake_deadline_ms is None and not args.restore_readmission_only
             and args.cleanup_deadline_ms is None, 'WARM_LIVE_READ_ONLY_MODE_CONFLICT')
        print(json.dumps(verify_native_execution(args.stage, args.stage_sha256), sort_keys=True)); return
    if args.restore_readmission_only:
        need(args.selection_request is not None and args.selection_request_sha256 is not None
             and args.cleanup_deadline_ms is not None and args.wake_deadline_ms is None,
             'READMISSION_CLEANUP_EXPLICIT_INPUTS_REQUIRED')
        print(json.dumps(restore_readmission_only(args.stage, args.stage_sha256,
            args.selection_request, args.selection_request_sha256, args.cleanup_deadline_ms), sort_keys=True)); return
    need(args.cleanup_deadline_ms is None, 'READMISSION_CLEANUP_MODE_REQUIRED')
    need(args.selection_request is not None and args.selection_request_sha256 is not None
         and args.wake_deadline_ms is not None, 'WARM_ACTIVATION_EXPLICIT_INPUTS_REQUIRED')
    print(json.dumps(activate(args.stage, args.stage_sha256, args.selection_request,
        args.selection_request_sha256, args.wake_deadline_ms), sort_keys=True))


if __name__ == '__main__': main()
