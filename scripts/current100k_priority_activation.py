#!/usr/bin/env python3
"""Prepare the existing guard's priority for one published Current100k plan.

``prepare_priority(stage, registrar_config, plan_path, plan)`` creates only a
content-addressed guard candidate.  The registrar config and registration
request remain unchanged, including on resume.  ``verify_prepared_priority``
repeats the same checks without writing.  ``check_background_wrapper`` checks
whether an observed background wrapper can select this exact candidate; it is
not evidence that a running process has selected it.

The service selector must supply the actual unit/config/drop-in bytes and
PID/start-ticks/Invocation/cgroup identities.  Its bounded sequence is: verify
the old identities, signal only the same controller with SIGUSR1, wait for its
natural exit and empty cgroup, select the reviewed new wrapper, then start and
verify the new identities.  A timeout or identity change leaves this same plan
pending; it never calls registration again.  SIGTERM, stop and restart are not
substitutes for cooperative drain. Only separate plan-named drop-ins are added;
the original fragment and all original drop-ins retain their exact bytes.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shlex
import signal
import stat
import subprocess
import sys
import time


MODULE_FILES = {
    'current100k_background_guards': 'scripts/current100k_background_guards.py',
    'activate_prepared_driver': 'scripts/activate_prepared_driver.py',
    'precheck_current100k_registration': 'scripts/precheck_current100k_registration.py',
    'historical_training.common': 'research/historical_training/common.py',
    'historical_training.resident_resource_guard': 'research/historical_training/resident_resource_guard.py',
    'historical_training.value_priority_admission': 'research/historical_training/value_priority_admission.py',
    'laya_cell.financial_forward_evaluate': 'learn/laya_cell/financial_forward_evaluate.py',
    'laya_cell.financial_forward_protocol': 'learn/laya_cell/financial_forward_protocol.py',
    'laya_cell.financial_forward_witness': 'learn/laya_cell/financial_forward_witness.py',
    'laya_cell.current100k_forward': 'learn/laya_cell/current100k_forward.py',
    'laya_cell.current100k_warm_flat': 'learn/laya_cell/current100k_warm_flat.py',
    'laya_cell.immutable_files': 'learn/laya_cell/immutable_files.py',
    'laya_cell.integrity': 'learn/laya_cell/integrity.py',
    'laya_cell.resident_hip_runtime': 'learn/laya_cell/resident_hip_runtime.py',
}
MODULE_FILES.update({name[:-3]: 'scripts/' + name for name in (
    'hepta_verified_loader.py', 'hepta_decision_attribution.py', 'hepta_native_reader.py', 'hepta_price_bootstrap_gate.py',
    'hepta_maintenance_latch.py', 'install_risk_stop_fence.py', 'install_decision_attribution.py')})

SHARED_UNITS = {role: 'heptatrader-laya-' + role + '.service' for role in ('trainer', 'evaluator')}
SHARED_PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'MainPID', 'InvocationID',
    'ExecStart', 'Environment', 'FragmentPath', 'DropInPaths', 'WorkingDirectory', 'ControlGroup',
    'Restart', 'Type', 'KillMode', 'KillSignal', 'TimeoutStopUSec', 'NeedDaemonReload')


def _environment(value):
    result = {}
    for item in shlex.split(value):
        key, separator, text = item.partition('=')
        need(separator and key and key not in result, 'ENVIRONMENT_INVALID')
        result[key] = text
    return result


def _command(value):
    from activate_prepared_driver import configured_execstart
    normalized = configured_execstart(value, reason='CURRENT100K_PRIORITY_EXECSTART_INVALID')
    match = re.fullmatch(r'\{ path=([^;]+) ; argv\[\]=([^;]+) ; ignore_errors=no ; \}', normalized)
    need(match is not None, 'EXECSTART_SCOPE')
    argv = shlex.split(match[2])
    need(argv and argv[0] == match[1], 'EXECSTART_PATH_MISMATCH')
    return argv


def selection_request(actual_units, original_wrapper_files, drain_timeout_seconds=660,
                      *, activation_deadline_ms=None):
    """Freeze observed static inputs; current process IDs are deliberately absent.

    The caller must set an explicit activation deadline before the request is
    published.  It bounds recovery of this same plan, not a new registration.
    """
    need(type(drain_timeout_seconds) is int and 1 <= drain_timeout_seconds <= 720,
         'DRAIN_BOUND_REQUIRED')
    need(activation_deadline_ms is None or type(activation_deadline_ms) is int,
         'ACTIVATION_DEADLINE_INVALID')
    wrapper_files = original_wrapper_files.get('files', original_wrapper_files)
    roles = {}
    for role, unit in SHARED_UNITS.items():
        snapshot = actual_units['units'][unit]
        need(snapshot['rc'] == 0, 'ORIGINAL_UNIT_UNAVAILABLE')
        properties = snapshot['properties']; argv = _command(properties['ExecStart'])
        need(len(argv) == 8 and argv[1:5] == ['-B', '-m', 'historical_training.resident_resource_guard', '--config']
             and argv[6:] == ['--role', role], 'ORIGINAL_GUARD_ARGV_REQUIRED')
        wrapper_path = argv[5]; wrapper = wrapper_files[wrapper_path]
        need(checksum(wrapper['text'].encode()) == wrapper['sha256'], 'SNAPSHOT_WRAPPER_CHANGED')
        paths = [properties['FragmentPath'], *shlex.split(properties['DropInPaths'])]
        files = {}
        for path in paths:
            value = actual_units['files'][path]
            need(checksum(value['text'].encode()) == value['sha256'], 'SNAPSHOT_UNIT_CHANGED')
            files[path] = {key: value[key] for key in ('sha256', 'text', 'mode')}
        roles[role] = {'unit': unit, 'fragment_path': properties['FragmentPath'], 'files': files,
            'old_wrapper': wrapper_path, 'old_wrapper_sha256': wrapper['sha256'], 'old_argv': argv,
            'old_environment': _environment(properties['Environment']),
            'working_directory': properties['WorkingDirectory'],
            'properties': {key: properties[key] for key in ('KillMode', 'KillSignal', 'TimeoutStopUSec')}}
    return {'schema': 'heptatrader.current100k-shared-priority-selection.v1',
        'activation_deadline_ms': activation_deadline_ms, 'drain_timeout_seconds': drain_timeout_seconds,
        'roles': roles}


def activation_window(request, plan, *, now_ms=None):
    """Late same-plan repair keeps every missed original opportunity visible."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    deadline = request.get('activation_deadline_ms')
    need(type(now) is int and type(deadline) is int and type(plan.get('input_end_ms')) is int,
         'ACTIVATION_TIME_REQUIRED')
    effective = min(deadline, plan['input_end_ms'] - 1)
    need(now <= effective, 'ACTIVATION_WINDOW_EXPIRED')
    return {'effective_deadline_ms': effective,
        'activation_before_input_start': now < plan['input_start_ms'], 'original_window_modified': False}


def need(ok, reason):
    if not ok:
        raise ValueError('CURRENT100K_PRIORITY_' + reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def checksum(raw):
    return hashlib.sha256(raw).hexdigest()


def check_source(config):
    """Bind executed helpers to the same immutable new worker closure."""
    root = Path(config['worker_source_root'])
    inventory = config['worker_runtime_sha256']
    need(root.is_absolute() and root.resolve() == root, 'CANONICAL_SOURCE_REQUIRED')
    need(config['worker_runtime_inventory_digest'] == checksum(canonical(inventory)),
         'WORKER_INVENTORY_CHANGED')
    own = root / 'scripts/current100k_priority_activation.py'
    need(Path(__file__).resolve() == own and inventory.get(own.relative_to(root).as_posix())
         == checksum(own.read_bytes()), 'IMPORTED_HELPER_NOT_PINNED')
    for name, relative in MODULE_FILES.items():
        expected = root / relative
        need(expected.resolve() == expected and inventory.get(relative) == checksum(expected.read_bytes()),
             'SOURCE_PIN_CHANGED:' + name)
        loaded = sys.modules.get(name)
        if loaded is None:
            specification = importlib.util.find_spec(name)
            origin = specification.origin if specification is not None else None
        else:
            origin = getattr(loaded, '__file__', None)
        need(origin is not None and Path(origin).resolve() == expected,
             'IMPORTED_SOURCE_OUTSIDE_RUNTIME:' + name)
        loaded = importlib.import_module(name)
        need(Path(loaded.__file__).resolve() == expected
             and checksum(Path(loaded.__file__).read_bytes()) == inventory[relative],
             'IMPORTED_SOURCE_CHANGED:' + name)


def _inputs(stage, config, plan_path, plan):
    """Validate and freeze all candidate bytes before creating any directory."""
    check_source(config)
    from precheck_current100k_registration import owned_bytes, pinned_json
    from historical_training.resident_resource_guard import verify_config
    from historical_training.value_priority_admission import verify_policy, _load_pinned
    from laya_cell.financial_forward_evaluate import validate_registration
    from laya_cell.resident_hip_runtime import check_guard_selection, check_plan

    need(stage.get('schema') == 'heptatrader.current100k-warm-flat-stage.v1'
         and stage.get('state') == 'STAGED_CPU_VERIFIED_NOT_REGISTERED'
         and stage.get('trial_registered') is False, 'ORIGINAL_WARM_STAGE_REQUIRED')
    config_info = stage['configs']['registrar']
    need(pinned_json(config_info['path'], config_info['sha256']) == config,
         'ORIGINAL_REGISTRAR_CONFIG_CHANGED')
    need(stage['release'] == config['worker_source_root']
         and stage['release_id'] == config['worker_runtime_inventory_digest']
         and stage['source_commit'] == config['source_commit'], 'STAGE_SOURCE_CHANGED')
    need(config.get('automatic_trading') is False and config.get('actor_update') is False,
         'RESEARCH_SCOPE_REQUIRED')
    need(stage['guard_config'] == config['guard_config']
         and stage['guard_config_sha256'] == config['guard_config_sha256'], 'ORIGINAL_GUARD_CHANGED')
    need(stage.get('background_guards') == config.get('background_guards'), 'ORIGINAL_BACKGROUND_BINDING_CHANGED')
    path = Path(plan_path)
    plan_id = plan.get('plan_id')
    need(isinstance(plan_id, str) and re.fullmatch('[0-9a-f]{64}', plan_id) is not None,
         'PUBLISHED_PLAN_ID_REQUIRED')
    need(path == Path(config['forward_root']) / 'plans' / plan_id / 'plan.json'
         and stage['forward_root'] == config['forward_root'], 'PUBLISHED_PLAN_PATH_REQUIRED')
    plan_sha = checksum(owned_bytes(path))
    need(pinned_json(path, plan_sha) == plan, 'PLAN_OBJECT_CHANGED')
    seal_path = path.with_name('registration.json')
    seal_sha = checksum(owned_bytes(seal_path))
    need(plan.get('schema') == 'heptatrader.current100k-value-forward-plan.v1'
         and plan.get('registration_scope', 'SAME_PRODUCER_REFIT') in ('SAME_PRODUCER_REFIT', 'NEW_PRODUCER_SCOPE')
         and plan.get('registration_scope', 'SAME_PRODUCER_REFIT')
             == stage.get('registration_scope', 'SAME_PRODUCER_REFIT')
         and type(plan.get('trial')) is int and plan['trial'] == stage['expected_trial']
         and plan.get('candidate_id') == stage['candidate_id']
         and plan.get('registry_root') == config['forward_root']
         and plan.get('worker_runtime_inventory_digest') == config['worker_runtime_inventory_digest']
         and plan.get('producer_registration') == config['producer_registration']
         and plan.get('period_sha256') == config['period_sha256']
         and plan.get('automatic_effect_authority') is False, 'PLAN_SCOPE_CHANGED')
    validate_registration(config, path, plan)
    check_plan(config, plan)
    wrapper = pinned_json(config['guard_config'], config['guard_config_sha256'])
    owner = verify_config(wrapper)
    background = None
    if config.get('background_guards') is not None:
        from current100k_background_guards import load_binding
        background = load_binding(config['background_guards'], config['producer_registration'],
                                  config['guard_config'], config['guard_config_sha256'])
    check_guard_selection(config, owner)
    selected = copy.deepcopy(wrapper)
    selected['value_priority'] = {**wrapper['value_priority'],
        'plan_path': str(path), 'plan_sha256': plan_sha}
    verify_policy(selected['value_priority'])
    _load_pinned(selected['value_priority'])
    # Also validates unchanged owner/resource/runtime fields against this loader.
    verify_config(selected)
    sources = {}
    for name, expected in selected['guard_files_sha256'].items():
        relative = 'research/historical_training/' + name
        need(Path(name).name == name and config['worker_runtime_sha256'].get(relative) == expected,
             'GUARD_SOURCE_NOT_IN_WORKER_CLOSURE')
        value = owned_bytes(Path(config['worker_source_root']) / relative)
        need(checksum(value) == expected, 'GUARD_SOURCE_CHANGED')
        sources[relative] = value
    sources['wrapper.json'] = canonical(selected) + b'\n'
    if background is not None:
        evaluator = copy.deepcopy(background['roles']['evaluator']['config'])
        evaluator['value_priority'] = copy.deepcopy(selected['value_priority'])
        verify_config(evaluator)
        sources['evaluator-wrapper.json'] = canonical(evaluator) + b'\n'
    files = {name: checksum(value) for name, value in sources.items()}
    identity = checksum(canonical(files))
    release = Path.home() / '.local/share/heptatrader/laya-guard/releases' / identity
    need(release.is_absolute() and release.resolve() == release, 'CANONICAL_RELEASE_REQUIRED')
    manifest = {'schema': 'heptatrader.current100k-value-priority-guard-stage.v1',
        'release_id': identity, 'source_wrapper': config['guard_config'],
        'source_wrapper_sha256': config['guard_config_sha256'],
        'source_commit': config['source_commit'], 'worker_release': config['worker_source_root'],
        'plan_id': plan_id, 'plan_path': str(path), 'plan_sha256': plan_sha,
        'registration_sha256': seal_sha, 'files': files,
        'changed_fields': ['value_priority.plan_path', 'value_priority.plan_sha256'],
        'resources_owner_keys_holds_changed': False, 'activated': False}
    if background is not None:
        manifest['background_guards'] = config['background_guards']
        manifest['evaluator_registration_preserved'] = True
    sources['MANIFEST.json'] = canonical(manifest) + b'\n'
    candidate = {'schema': 'heptatrader.current100k-priority-candidate.v1',
        'state': 'PRIORITY_PREPARED_NOT_SELECTED', 'release': str(release),
        'release_id': identity, 'wrapper': str(release / 'wrapper.json'),
        'wrapper_sha256': files['wrapper.json'], 'manifest': str(release / 'MANIFEST.json'),
        'manifest_sha256': checksum(sources['MANIFEST.json']),
        'plan_id': plan_id, 'plan_path': str(path), 'plan_sha256': plan_sha,
        'registration_sha256': seal_sha, 'python': owner['python'],
        'resident_config': selected['original_config'],
        'resident_config_sha256': selected['original_config_sha256'],
        'pythonpath': ':'.join([str(release / 'research'), owner['runtime_directory'] + '/learn',
                               owner['runtime_directory'] + '/scripts']),
        'shared_service_selection_required': True, 'activated': False,
        'registrar_config_modified': False, 'registration_performed': False,
        'broker_orders_sent': 0}
    if background is not None:
        candidate['background_guards'] = config['background_guards']
        candidate['role_wrappers'] = {}
        for role, filename in (('trainer', 'wrapper.json'), ('evaluator', 'evaluator-wrapper.json')):
            original_role = background['roles'][role]['config']
            candidate['role_wrappers'][role] = {
                'wrapper': str(release / filename), 'wrapper_sha256': files[filename],
                'resident_config': original_role['original_config'],
                'resident_config_sha256': original_role['original_config_sha256'],
                'python': candidate['python'], 'pythonpath': candidate['pythonpath']}
    # No wrapper or stage may drift between the signed validation and preparation.
    need(pinned_json(config_info['path'], config_info['sha256']) == config
         and pinned_json(config['guard_config'], config['guard_config_sha256']) == wrapper
         and checksum(owned_bytes(path)) == plan_sha
         and checksum(owned_bytes(seal_path)) == seal_sha, 'INPUT_CHANGED_DURING_PREPARATION')
    return candidate, sources


def _private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    need(path.resolve() == path and stat.S_ISDIR(info.st_mode)
         and info.st_uid == os.geteuid() and not info.st_mode & 0o077,
         'PRIVATE_RELEASE_DIRECTORY_REQUIRED')


def prepare_priority(stage, registrar_config, plan_path, plan, *, remaining_seconds=None):
    """Publish the same immutable candidate on every verified same-plan resume."""
    if remaining_seconds is not None: remaining_seconds()
    candidate, sources = _inputs(stage, registrar_config, plan_path, plan)
    if remaining_seconds is not None: remaining_seconds()
    from laya_cell.immutable_files import write_immutable
    release = Path(candidate['release'])
    _private_directory(release)
    for name, value in sources.items():
        if remaining_seconds is not None: remaining_seconds()
        target = release / name
        directory = release
        for part in Path(name).parent.parts:
            directory /= part
            _private_directory(directory)
        write_immutable(target, value, 0o400, 'IMMUTABLE_PRIORITY_CANDIDATE_CONFLICT', checksum(value))
    targets = {str(release / name): value for name, value in sources.items()}
    directories = {Path(path).parent for path in targets}
    directory = release
    while directory != Path.home():
        need(directory.is_relative_to(Path.home()), 'RELEASE_ANCESTOR_SCOPE')
        directories.add(directory); directory = directory.parent
    directories.add(directory)
    _sync_selected_files(targets, directories, remaining_seconds=remaining_seconds)
    return verify_prepared_priority(stage, registrar_config, plan_path, plan, candidate)


def verify_prepared_priority(stage, registrar_config, plan_path, plan, candidate):
    """Read-only validation; a valid candidate still grants no service selection."""
    expected, sources = _inputs(stage, registrar_config, plan_path, plan)
    need(candidate == expected, 'CANDIDATE_IDENTITY_CHANGED')
    from precheck_current100k_registration import owned_bytes
    for name, value in sources.items():
        path = Path(candidate['release']) / name
        need(owned_bytes(path) == value and stat.S_IMODE(path.lstat().st_mode) == 0o400,
             'PREPARED_BYTES_CHANGED')
    return expected


def role_candidate(candidate, role):
    if 'role_wrappers' not in candidate:
        return candidate
    need(role in ('trainer', 'evaluator') and set(candidate['role_wrappers']) == set(SHARED_UNITS),
         'EXPLICIT_BACKGROUND_ROLE_REQUIRED')
    return {**candidate, **candidate['role_wrappers'][role]}


def check_background_wrapper(candidate, observed_path, observed_sha256, *, role=None):
    """Pure compatibility preflight for an actually observed background config.

    The caller must obtain the path/SHA from the actual process and unit.  A
    different owner/config/runtime/hold policy needs a separate reviewed guard
    candidate, not an automatic substitution with the stage's trainer wrapper.
    """
    from precheck_current100k_registration import pinned_json
    from historical_training.resident_resource_guard import verify_config
    from historical_training.value_priority_admission import verify_policy
    candidate = role_candidate(candidate, role)
    selected = pinned_json(candidate['wrapper'], candidate['wrapper_sha256'])
    observed = pinned_json(observed_path, observed_sha256)
    verify_config(observed)
    verify_config(selected)
    verify_policy(selected['value_priority'])
    verify_policy(observed['value_priority'])
    before = copy.deepcopy(observed)
    before['value_priority']['plan_path'] = selected['value_priority']['plan_path']
    before['value_priority']['plan_sha256'] = selected['value_priority']['plan_sha256']
    need(before == selected, 'BACKGROUND_WRAPPER_REQUIRES_DISTINCT_CANDIDATE')
    need(selected['value_priority']['plan_path'] == candidate['plan_path']
         and selected['value_priority']['plan_sha256'] == candidate['plan_sha256'],
         'CANDIDATE_PLAN_CHANGED')
    return {'state': 'BACKGROUND_CONFIG_COMPATIBLE_RUNTIME_UNVERIFIED',
        'observed_wrapper': str(observed_path), 'observed_wrapper_sha256': observed_sha256,
        'selected_wrapper': candidate['wrapper'], 'selected_wrapper_sha256': candidate['wrapper_sha256'],
        'runtime_identity_verified': False, 'cooperative_drain_verified': False,
        'activated': False, 'service_actions': []}


def precheck_shared_priority(stage, config, request, candidate=None, *, _roles=None, remaining_seconds=None):
    """Read-only pre-registration gate for the observed original shared services.

    This proves current unit/process/config compatibility, not a completed
    drain, a service selection or future resource admission.  It creates no
    plan, candidate, checkpoint, unit or timer.  An inactive/changed controller
    needs same-plan selection recovery; this entry point does not infer it.
    """
    if remaining_seconds is not None: remaining_seconds()
    check_source(config)
    from precheck_current100k_registration import owned_bytes, pinned_json
    from historical_training.resident_resource_guard import verify_config
    import activate_prepared_driver as driver
    need(request.get('schema') == 'heptatrader.current100k-shared-priority-selection.v1'
         and set(request['roles']) == set(SHARED_UNITS), 'SELECTION_REQUEST_SCOPE')
    deadline = request.get('activation_deadline_ms')
    need(type(deadline) is int and int(time.time() * 1000) <= deadline,
         'ACTIVATION_DEADLINE_EXPIRED_OR_UNSET')
    need(type(request.get('drain_timeout_seconds')) is int
         and 1 <= request['drain_timeout_seconds'] <= 720, 'DRAIN_BOUND_REQUIRED')
    registrar = stage['configs']['registrar']
    need(pinned_json(registrar['path'], registrar['sha256']) == config
         and stage['guard_config'] == config['guard_config']
         and stage['guard_config_sha256'] == config['guard_config_sha256']
         and stage.get('background_guards') == config.get('background_guards'), 'ORIGINAL_REGISTRAR_CONFIG_CHANGED')
    original = pinned_json(config['guard_config'], config['guard_config_sha256'])
    owner = verify_config(original)
    background = None
    if config.get('background_guards') is not None:
        from current100k_background_guards import load_binding
        background = load_binding(config['background_guards'], config['producer_registration'],
                                  config['guard_config'], config['guard_config_sha256'])
    if candidate is not None:
        plan = pinned_json(candidate['plan_path'], candidate['plan_sha256'])
        verify_prepared_priority(stage, config, Path(candidate['plan_path']), plan, candidate)
    observations = {}
    for role, unit in SHARED_UNITS.items():
        if _roles is not None and role not in _roles: continue
        expected = request['roles'][role]
        need(expected['unit'] == unit and Path(expected['fragment_path']).name == unit,
             'SHARED_UNIT_SCOPE_CHANGED')
        for path, pinned in expected['files'].items():
            raw = owned_bytes(path)
            need(checksum(raw) == pinned['sha256'] and raw == pinned['text'].encode()
                 and stat.S_IMODE(Path(path).lstat().st_mode) == int(pinned['mode'], 0),
                 'ORIGINAL_UNIT_BYTES_CHANGED:' + role)
        wrapper = pinned_json(expected['old_wrapper'], expected['old_wrapper_sha256'])
        choice = role_candidate(candidate, role) if candidate is not None else None
        is_selected = choice is not None and expected['old_wrapper'] == choice['wrapper']
        if background is not None:
            role_binding = background['roles'][role]
            owner = role_binding['owner']
            if not is_selected:
                need((expected['old_wrapper'], expected['old_wrapper_sha256'])
                     == (role_binding['wrapper'], role_binding['wrapper_sha256']),
                     'EXACT_BACKGROUND_ROLE_PIN_CHANGED:' + role)
            selected_wrapper = (pinned_json(choice['wrapper'], choice['wrapper_sha256']) if is_selected
                                else role_binding['config'])
        else:
            selected_wrapper = (pinned_json(choice['wrapper'], choice['wrapper_sha256']) if is_selected else original)
        need(wrapper == selected_wrapper, 'BACKGROUND_WRAPPER_REQUIRES_DISTINCT_CANDIDATE')
        verify_config(wrapper)
        need(expected['old_argv'] == [owner['python'], '-B', '-m', 'historical_training.resident_resource_guard',
             '--config', expected['old_wrapper'], '--role', role]
             and set(expected['properties']) == {'KillMode', 'KillSignal', 'TimeoutStopUSec'},
             'ORIGINAL_GUARD_ARGUMENTS_CHANGED')
        guard_root = (Path(background['roles'][role]['guard_import_root'])
            if background is not None and not is_selected else Path(expected['old_wrapper']).parent / 'research')
        old_pythonpath = ':'.join([str(guard_root), owner['runtime_directory'] + '/learn',
                                  owner['runtime_directory'] + '/scripts'])
        need(expected['old_environment'].get('PYTHONPATH') == old_pythonpath,
             'ORIGINAL_GUARD_IMPORT_SELECTION_CHANGED')
        for name, wanted in wrapper['guard_files_sha256'].items():
            source = guard_root / 'historical_training' / name
            need(checksum(owned_bytes(source)) == wanted, 'ORIGINAL_GUARD_SOURCE_CHANGED')
        if candidate is not None:
            check_background_wrapper(candidate, expected['old_wrapper'], expected['old_wrapper_sha256'], role=role)
        before = _unit_properties(unit, remaining_seconds)
        expected_dropins = set(expected['files']) - {expected['fragment_path']}
        need(before['Id'] == unit and before['LoadState'] == 'loaded'
             and before['FragmentPath'] == expected['fragment_path']
             and set(shlex.split(before['DropInPaths'])) == expected_dropins
             and before['Restart'] == 'on-failure' and before['Type'] == 'simple'
             and before['NeedDaemonReload'] == 'no'
             and before['WorkingDirectory'] == expected['working_directory'] == owner['runtime_directory']
             and all(before[key] == value for key, value in expected['properties'].items()),
             'LOADED_UNIT_POLICY_CHANGED:' + role)
        need(_command(before['ExecStart']) == expected['old_argv']
             and _environment(before['Environment']) == expected['old_environment'],
             'LOADED_GUARD_SELECTION_CHANGED:' + role)
        need(before['ActiveState'] == 'active' and before['SubState'] == 'running'
             and before['MainPID'].isdigit() and int(before['MainPID']) > 0
             and before['InvocationID'], 'ORIGINAL_CONTROLLER_NOT_RUNNING:' + role)
        pid = int(before['MainPID']); identity = driver.pid_key(pid)
        need(identity is not None, 'CONTROLLER_PROCESS_GONE:' + role)
        argv = [item.decode() for item in driver.proc_bytes(pid, 'cmdline').split(b'\0') if item]
        environment = {}
        for item in driver.proc_bytes(pid, 'environ').split(b'\0'):
            if not item: continue
            key, separator, value = item.decode().partition('=')
            need(separator and key not in environment, 'PROCESS_ENVIRONMENT_INVALID')
            environment[key] = value
        group = before['ControlGroup']
        prefix = f'/user.slice/user-{os.geteuid()}.slice/user@{os.geteuid()}.service/'
        need(group.startswith(prefix) and Path(group).name == unit and '..' not in Path(group).parts,
             'CONTROLLER_CGROUP_SCOPE')
        cgroups = driver.proc_bytes(pid, 'cgroup').decode().splitlines()
        need('0::' + group in cgroups and argv == expected['old_argv']
             and all(environment.get(key) == value for key, value in expected['old_environment'].items())
             and environment.get('INVOCATION_ID') == before['InvocationID']
             and not environment.get('LD_PRELOAD'), 'RUNNING_GUARD_SELECTION_CHANGED:' + role)
        after = _unit_properties(unit, remaining_seconds)
        need(after == before and driver.pid_key(pid) == identity,
             'CONTROLLER_CHANGED_DURING_PRECHECK:' + role)
        observations[role] = {'unit': unit, 'pid_key': list(identity), 'invocation_id': before['InvocationID'],
            'control_group': group, 'wrapper': expected['old_wrapper'],
            'wrapper_sha256': expected['old_wrapper_sha256'], 'runtime_identity_verified': True}
    need(int(time.time() * 1000) <= deadline, 'ACTIVATION_DEADLINE_EXPIRED_DURING_PRECHECK')
    return {'state': 'SHARED_PRIORITY_ORIGINAL_RUNTIME_VERIFIED_NO_WRITE',
        'request_sha256': checksum(canonical(request)), 'roles': observations,
        'cooperative_drain_verified': False, 'activated': False, 'service_actions': [],
        'registration_performed': False, 'future_admission_granted': False}


def _unit_properties(unit, remaining_seconds=None):
    """Retain the existing exact parser; its fixed 15s call fits the budget."""
    import activate_prepared_driver as driver
    if remaining_seconds is not None:
        need(remaining_seconds() >= 15, 'INSUFFICIENT_BUDGET_BEFORE_UNIT_READ')
    observed = driver.unit_properties(unit, SHARED_PROPERTIES, 'CURRENT100K_PRIORITY_UNIT')
    if remaining_seconds is not None: remaining_seconds()
    return observed


def selection_dropins(request, candidate):
    """Create exact new drop-in bytes; never replace an original file."""
    from precheck_current100k_registration import pinned_json
    selected = copy.deepcopy(request); documents = {}
    for role, unit in SHARED_UNITS.items():
        choice = role_candidate(candidate, role)
        owner = pinned_json(choice['resident_config'], choice['resident_config_sha256'])
        old = request['roles'][role]; fragment = Path.home() / '.config/systemd/user' / unit
        need(Path(old['fragment_path']) == fragment, 'ORIGINAL_FRAGMENT_PATH_SCOPE')
        path = fragment.with_name(unit + '.d') / ('99-current100k-priority-' + candidate['plan_id'] + '.conf')
        argv = [choice['python'], '-B', '-m', 'historical_training.resident_resource_guard',
                '--config', choice['wrapper'], '--role', role]
        need(all(re.fullmatch(r'[-A-Za-z0-9_./:]+', value) for value in
            [*argv, choice['pythonpath'], owner['runtime_directory']]), 'SELECTION_ARGUMENT_ESCAPE_REQUIRED')
        raw = ('[Service]\nEnvironment=PYTHONPATH=' + choice['pythonpath']
            + '\nExecStart=\nExecStart=' + ' '.join(argv)
            + '\nWorkingDirectory=' + owner['runtime_directory'] + '\n').encode()
        value = selected['roles'][role]
        value['files'][str(path)] = {'sha256': checksum(raw), 'text': raw.decode(), 'mode': '0o600'}
        value.update(old_wrapper=choice['wrapper'], old_wrapper_sha256=choice['wrapper_sha256'], old_argv=argv)
        value['old_environment']['PYTHONPATH'] = choice['pythonpath']
        documents[str(path)] = raw
    return selected, documents


def _original_files(request, documents):
    from precheck_current100k_registration import owned_bytes
    for expected in request['roles'].values():
        for path, pinned in expected['files'].items():
            raw = owned_bytes(path)
            need(raw == pinned['text'].encode() and checksum(raw) == pinned['sha256']
                 and stat.S_IMODE(Path(path).lstat().st_mode) == int(pinned['mode'], 0),
                 'ORIGINAL_UNIT_BYTES_CHANGED')
    for path, raw in documents.items():
        if Path(path).exists() or Path(path).is_symlink():
            need(owned_bytes(path) == raw and stat.S_IMODE(Path(path).lstat().st_mode) == 0o600,
                 'SELECTION_DROPIN_CONFLICT')


def _empty_group(group):
    prefix = f'/user.slice/user-{os.geteuid()}.slice/user@{os.geteuid()}.service/'
    need(group.startswith(prefix) and Path(group).name in set(SHARED_UNITS.values())
         and '..' not in Path(group).parts, 'DRAIN_CGROUP_SCOPE')
    path = Path('/sys/fs/cgroup') / group.lstrip('/')
    if not path.exists(): return True
    need(path.resolve() == path and path.is_dir(), 'DRAIN_CGROUP_PATH_CHANGED')
    return all(not child.read_text().strip() for child in
               [path / 'cgroup.procs', *path.rglob('*/cgroup.procs')])


def _inactive_role(request, selected, role, original, properties):
    import activate_prepared_driver as driver
    old = request['roles'][role]; new = selected['roles'][role]; p = properties
    need(p['Id'] == old['unit'] and p['LoadState'] == 'loaded'
         and p['FragmentPath'] == old['fragment_path']
         and set(shlex.split(p['DropInPaths'])) in (
             set(old['files']) - {old['fragment_path']}, set(new['files']) - {new['fragment_path']})
         and p['WorkingDirectory'] == old['working_directory']
         and p['Type'] == 'simple' and p['Restart'] == 'on-failure'
         and all(p[key] == value for key, value in old['properties'].items()),
         'DRAINED_UNIT_POLICY_CHANGED:' + role)
    pair = (_command(p['ExecStart']), _environment(p['Environment']))
    need(pair in ((old['old_argv'], old['old_environment']), (new['old_argv'], new['old_environment'])),
         'DRAINED_UNIT_SELECTION_CHANGED:' + role)
    need(p['ActiveState'] == 'inactive' and p['SubState'] == 'dead' and p['MainPID'] == '0'
         and p['ControlGroup'] in ('', original['control_group'])
         and driver.pid_key(int(original['pid_key'][0])) != tuple(original['pid_key'])
         and _empty_group(original['control_group']), 'ORIGINAL_CONTROLLER_NOT_DRAINED:' + role)


def _selection_states(stage, config, request, selected, candidate, intent, documents, remaining_seconds=None):
    import activate_prepared_driver as driver
    _original_files(request, documents); states = {}; proofs = {}
    for role, unit in SHARED_UNITS.items():
        p = _unit_properties(unit, remaining_seconds)
        if p['ActiveState'] == 'inactive':
            _inactive_role(request, selected, role, intent['original_roles'][role], p)
            states[role] = 'DRAINED'; continue
        chosen = selected if _command(p['ExecStart']) == selected['roles'][role]['old_argv'] else request
        proof = precheck_shared_priority(stage, config, chosen, candidate, _roles=(role,), remaining_seconds=remaining_seconds)['roles'][role]
        if chosen is selected:
            states[role] = 'SELECTED'; proofs[role] = proof
        else:
            original = intent['original_roles'][role]
            need(all(proof[key] == original[key] for key in ('unit', 'pid_key', 'invocation_id', 'control_group')),
                 'ORIGINAL_CONTROLLER_RESTARTED_DURING_SELECTION:' + role)
            states[role] = 'ORIGINAL'; proofs[role] = proof
    return states, proofs


def select_shared_priority(stage, config, plan_path, plan, candidate, request, *, remaining_seconds=None):
    """Resume one plan; the caller holds its activation lock throughout."""
    from precheck_current100k_registration import owned_bytes
    from laya_cell.immutable_files import write_immutable
    if remaining_seconds is not None: remaining_seconds()
    verify_prepared_priority(stage, config, plan_path, plan, candidate)
    if remaining_seconds is not None: remaining_seconds()
    activation_window(request, plan)
    selected, documents = selection_dropins(request, candidate); _original_files(request, documents)
    output = Path(config['forward_root']) / 'activation' / plan['plan_id']
    intent_path = output / 'PRIORITY_SELECTION.json'
    binding = {'schema': 'heptatrader.current100k-shared-priority-intent.v1',
        'plan_id': plan['plan_id'], 'plan_sha256': candidate['plan_sha256'],
        'request_sha256': checksum(canonical(request)), 'candidate_sha256': checksum(canonical(candidate)),
        'dropins_sha256': {path: checksum(raw) for path, raw in documents.items()}}
    if intent_path.exists() or intent_path.is_symlink():
        intent = json.loads(owned_bytes(intent_path))
        need({key: intent.get(key) for key in binding} == binding
             and set(intent) == {*binding, 'original_roles'}
             and set(intent['original_roles']) == set(SHARED_UNITS), 'SELECTION_INTENT_CHANGED')
    else:
        observed = precheck_shared_priority(stage, config, request, candidate, remaining_seconds=remaining_seconds)
        intent = {**binding, 'original_roles': observed['roles']}
        _private_directory(output.parent)
        _private_directory(output)
        write_immutable(intent_path, canonical(intent) + b'\n', 0o400, 'SELECTION_INTENT_CONFLICT')
    _sync_selection_intent(Path(config['forward_root']), output, intent_path, intent, remaining_seconds=remaining_seconds)
    states, proofs = _selection_states(stage, config, request, selected, candidate, intent, documents, remaining_seconds)
    return _select_observed(stage, config, plan, candidate, request, selected, documents,
                            intent, intent_path, states, proofs, remaining_seconds)


def _sync_selection_intent(root, output, path, intent, *, remaining_seconds=None):
    """Every entry to controls confirms the same intent and ancestor entries."""
    from precheck_current100k_registration import owned_bytes
    need(output == root / 'activation' / intent['plan_id'] and path == output / 'PRIORITY_SELECTION.json',
         'SELECTION_INTENT_PATH_CHANGED')
    expected = canonical(intent) + b'\n'
    need(owned_bytes(path) == expected, 'SELECTION_INTENT_CHANGED')
    _sync_selected_files({str(path): expected}, (output, output.parent, root), remaining_seconds=remaining_seconds)


def _sync_selected_files(files, directories, *, remaining_seconds=None):
    """Reconfirm visible activation bytes and parent entries before selection."""
    from precheck_current100k_registration import owned_bytes
    def identity(value):
        core = (value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode)
        return core if stat.S_ISDIR(value.st_mode) else (*core, value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    descriptors = []
    try:
        targets = [Path(path) for path in files]
        targets += sorted(set(map(Path, directories)), key=lambda path: (-len(path.parts), str(path)))
        for target in targets:
            if remaining_seconds is not None: remaining_seconds()
            directory = str(target) not in files
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            if directory: flags |= os.O_DIRECTORY
            fd = os.open(target, flags); item = os.fstat(fd)
            descriptors.append((target, fd, identity(item)))
            need(item.st_uid == os.geteuid() and not item.st_mode & 0o022
                 and target.resolve() == target and (directory or stat.S_ISREG(item.st_mode)
                 and item.st_nlink == 1), 'SELECTION_INTENT_OWNER_OR_PATH')
            if not directory: need(owned_bytes(target) == files[str(target)], 'SELECTION_BYTES_CHANGED')
            os.fsync(fd)
        if remaining_seconds is not None: remaining_seconds()
        for path, expected in files.items():
            need(owned_bytes(path) == expected, 'SELECTION_BYTES_CHANGED_DURING_SYNC')
        for target, fd, before in descriptors:
            need(identity(os.fstat(fd)) == identity(target.lstat()) == before,
                 'SELECTION_INTENT_IDENTITY_CHANGED_DURING_SYNC')
    finally:
        for _, fd, _ in reversed(descriptors): os.close(fd)


def _select_observed(stage, config, plan, candidate, request, selected, documents,
                     intent, intent_path, states, proofs, remaining_seconds=None):
    import activate_prepared_driver as driver
    from precheck_current100k_registration import owned_bytes
    from laya_cell.immutable_files import write_immutable
    def timeout(cap):
        window = activation_window(request, plan)
        remaining = (window['effective_deadline_ms'] - int(time.time() * 1000)) / 1000
        if remaining_seconds is not None: remaining = min(remaining, remaining_seconds())
        need(remaining > 0, 'ACTIVATION_TOTAL_BUDGET_EXPIRED')
        return min(cap, remaining)
    def finished():
        proof = precheck_shared_priority(stage, config, selected, candidate, remaining_seconds=remaining_seconds)
        window = activation_window(request, plan)
        return {'state': 'SAME_PLAN_SHARED_PRIORITY_SELECTED', 'plan_id': plan['plan_id'],
            'plan_sha256': candidate['plan_sha256'], 'roles': proof['roles'],
            'intent_sha256': checksum(owned_bytes(intent_path)), 'dropins_sha256': intent['dropins_sha256'],
            'activated': True, 'cooperative_drain_verified': True, 'original_unit_files_modified': False,
            'registration_performed': False, 'future_admission_granted': False, 'broker_orders_sent': 0, **window}
    if all(state == 'SELECTED' for state in states.values()): return finished()
    descriptors = []
    remaining = timeout(request['drain_timeout_seconds'] + 150)
    # One drain budget shared by both controllers, leaving control/readback time.
    need(remaining > 150, 'INSUFFICIENT_BUDGET_BEFORE_COOPERATIVE_DRAIN')
    stop = time.monotonic() + min(request['drain_timeout_seconds'], remaining - 150)
    try:
        for role, state in states.items():
            if state != 'ORIGINAL': continue
            activation_window(request, plan)
            need(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'PIDFD_SIGNAL_REQUIRED')
            proof = proofs[role]; pid = int(proof['pid_key'][0]); fd = os.pidfd_open(pid, 0)
            descriptors.append(fd)
            current = precheck_shared_priority(stage, config, request, candidate, _roles=(role,), remaining_seconds=remaining_seconds)['roles'][role]
            need(current == proof and driver.pid_key(pid) == tuple(proof['pid_key']), 'CONTROLLER_CHANGED_BEFORE_DRAIN')
            timeout(1)
            signal.pidfd_send_signal(fd, signal.SIGUSR1)
        while True:
            timeout(1)
            activation_window(request, plan)
            states, proofs = _selection_states(stage, config, request, selected, candidate, intent, documents, remaining_seconds)
            if all(state in ('DRAINED', 'SELECTED') for state in states.values()): break
            need(time.monotonic() < stop, 'COOPERATIVE_DRAIN_TIMEOUT_SAME_PLAN_PENDING')
            time.sleep(min(1., max(0., stop - time.monotonic())))
        activation_window(request, plan)
        for path, raw in documents.items():
            timeout(1)
            directory = Path(path).parent; info = directory.lstat()
            need(directory.resolve() == directory and stat.S_ISDIR(info.st_mode)
                 and info.st_uid == os.geteuid() and not info.st_mode & 0o022, 'DROPIN_DIRECTORY_SCOPE')
            write_immutable(Path(path), raw, 0o600, 'SELECTION_DROPIN_CONFLICT')
        _sync_selected_files(documents, [Path(path).parent for path in documents], remaining_seconds=remaining_seconds)
        activation_window(request, plan)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout(30))
        states, _ = _selection_states(stage, config, request, selected, candidate, intent, documents, remaining_seconds)
        pending = [SHARED_UNITS[role] for role, state in states.items() if state == 'DRAINED']
        if pending:
            activation_window(request, plan)
            subprocess.run(['systemctl', '--user', 'start', *pending], check=True,
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout(45))
        return finished()
    finally:
        for descriptor in descriptors: os.close(descriptor)
