"""Explicit trainer-only producer append; CPU configuration/read-only identity checks.

Legacy requests have no binding and retain their original common-wrapper rule.
This profile never selects a service or changes an evaluator registration.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import stat

SCHEMA = 'heptatrader.current100k-background-guards.v1'
PROFILE = 'APPEND_ONLY_TRAINER_PRODUCER_V1'
ROLE_KEYS = {'wrapper', 'wrapper_sha256', 'guard_import_root'}


def need(value, reason):
    if not value:
        raise ValueError('BACKGROUND_GUARDS_' + reason)


def verify_append_relationship(prior, prior_owner, trainer, trainer_owner, evaluator, evaluator_owner, registration):
    """Validate the semantic delta after each independent file has been pinned."""
    strip_owner = lambda wrapper: {key: value for key, value in wrapper.items()
                                  if key not in ('original_config', 'original_config_sha256')}
    need(strip_owner(prior) == strip_owner(trainer) == strip_owner(evaluator)
         and prior_owner == evaluator_owner, 'ORIGINAL_ROLE_POLICY_OR_EVALUATOR_CHANGED')
    expected = copy.deepcopy(prior_owner)
    registrations = expected.get('decision_attribution_registrations', {}).get('EUR.USD')
    need(isinstance(registrations, list) and registrations
         and isinstance(registration, dict) and set(registration) == {'manifest', 'sha256'}
         and all(isinstance(item, dict) and item.get('sha256') != registration['sha256'] for item in registrations),
         'ONE_NEW_EUR_PRODUCER_REQUIRED')
    registrations.append(copy.deepcopy(registration))
    need(trainer_owner == expected, 'ONLY_TRAINER_EUR_APPEND_ALLOWED')


def load_binding(reference, registration, primary_path, primary_sha):
    """Pin both original roles and permit exactly one trainer EUR append."""
    from precheck_current100k_registration import owned_bytes, pinned_json
    from historical_training.resident_resource_guard import verify_config
    need(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}, 'EXPLICIT_PIN_REQUIRED')
    document = pinned_json(reference['path'], reference['sha256'])
    need(set(document) == {'schema', 'profile', 'producer_registration', 'prior_trainer', 'roles'}
         and document['schema'] == SCHEMA and document['profile'] == PROFILE
         and document['producer_registration'] == registration
         and set(document['roles']) == {'trainer', 'evaluator'}, 'PROFILE_OR_PRODUCER_CHANGED')
    records = {'prior_trainer': document['prior_trainer'], **document['roles']}
    values = {}
    for role, record in records.items():
        need(isinstance(record, dict) and set(record) == ROLE_KEYS, 'EXACT_ROLE_PIN_REQUIRED:' + role)
        wrapper = pinned_json(record['wrapper'], record['wrapper_sha256'])
        owner = verify_config(wrapper)
        root = Path(record['guard_import_root'])
        info = root.lstat()
        need(root.is_absolute() and root.resolve() == root and stat.S_ISDIR(info.st_mode)
             and info.st_uid == os.geteuid() and not info.st_mode & 0o022,
             'CANONICAL_GUARD_IMPORT_ROOT_REQUIRED:' + role)
        sources = {}
        for name, checksum in wrapper['guard_files_sha256'].items():
            need(Path(name).name == name, 'GUARD_MODULE_NAME')
            path = root / 'historical_training' / name
            need(hashlib.sha256(owned_bytes(path)).hexdigest() == checksum,
                 'ACTUAL_GUARD_SOURCE_CHANGED:' + role + ':' + name)
            sources[str(path)] = checksum
        values[role] = {**record, 'config': wrapper, 'owner': owner, 'guard_sources_sha256': sources,
            'pythonpath': ':'.join((str(root), owner['runtime_directory'] + '/learn',
                                   owner['runtime_directory'] + '/scripts'))}
    prior = values['prior_trainer']; trainer = values['trainer']; evaluator = values['evaluator']
    need((trainer['wrapper'], trainer['wrapper_sha256']) == (str(primary_path), primary_sha),
         'PRIMARY_MUST_BE_APPENDED_TRAINER')
    # Baseline files may have distinct paths/serialization, but no pre-existing
    # semantic role difference is silently admitted by this narrow profile.
    verify_append_relationship(prior['config'], prior['owner'], trainer['config'], trainer['owner'],
                               evaluator['config'], evaluator['owner'], registration)
    need(prior['guard_import_root'] == trainer['guard_import_root'] == evaluator['guard_import_root'],
         'ORIGINAL_GUARD_IMPORT_ROOT_CHANGED')
    manifest = pinned_json(registration['manifest'], registration['sha256'])
    need(manifest.get('schema') == 'heptatrader.attribution-producer.v1'
         and manifest.get('instrument') == 'EUR.USD' and manifest.get('execution_domain') == 'PAPER'
         and manifest.get('period_sha256') == trainer['owner']['period_sha256'], 'PRODUCER_SCOPE_CHANGED')
    return {'reference': copy.deepcopy(reference), 'document': document,
        'roles': {role: values[role] for role in ('trainer', 'evaluator')}, 'prior_trainer': prior}


def verify_loaded_roles(binding):
    """Verify the initial two-role selection before installing a new controller.

This is intentionally not a per-tick prerequisite: the later signed-plan
priority handoff has its own exact original/selected recovery state machine.
"""
    from precheck_current100k_registration import owned_bytes
    import activate_prepared_driver as driver
    from current100k_priority_activation import SHARED_UNITS, SHARED_PROPERTIES, _command, _environment
    import shlex
    result = {}
    for role, unit in SHARED_UNITS.items():
        selected = binding['roles'][role]; owner = selected['owner']
        before = driver.unit_properties(unit, SHARED_PROPERTIES, 'BACKGROUND_GUARDS_UNIT')
        argv = [owner['python'], '-B', '-m', 'historical_training.resident_resource_guard',
                '--config', selected['wrapper'], '--role', role]
        environment = _environment(before['Environment'])
        need(before['Id'] == unit and before['LoadState'] == 'loaded'
             and before['ActiveState'] == 'active' and before['SubState'] == 'running'
             and before['Type'] == 'simple' and before['Restart'] == 'on-failure'
             and before['NeedDaemonReload'] == 'no' and before['WorkingDirectory'] == owner['runtime_directory']
             and _command(before['ExecStart']) == argv and before['InvocationID']
             and environment.get('PYTHONPATH') == selected['pythonpath'], 'LOADED_ROLE_CHANGED:' + role)
        paths = [before['FragmentPath'], *shlex.split(before['DropInPaths'])]
        need(Path(before['FragmentPath']) == Path.home() / '.config/systemd/user' / unit,
             'ORIGINAL_UNIT_PATH_CHANGED:' + role)
        files = {path: hashlib.sha256(owned_bytes(path)).hexdigest() for path in paths}
        need(before['MainPID'].isdigit() and int(before['MainPID']) > 0, 'RUNNING_PID_REQUIRED:' + role)
        pid = int(before['MainPID']); identity = driver.pid_key(pid)
        need(identity is not None, 'PROCESS_GONE:' + role)
        process_argv = [item.decode() for item in driver.proc_bytes(pid, 'cmdline').split(b'\0') if item]
        process_environment = {}
        for item in driver.proc_bytes(pid, 'environ').split(b'\0'):
            if not item: continue
            key, separator, value = item.decode().partition('=')
            need(separator and key not in process_environment, 'PROCESS_ENVIRONMENT_INVALID')
            process_environment[key] = value
        group = before['ControlGroup']
        prefix = f'/user.slice/user-{os.geteuid()}.slice/user@{os.geteuid()}.service/'
        need(group.startswith(prefix) and Path(group).name == unit and '..' not in Path(group).parts
             and '0::' + group in driver.proc_bytes(pid, 'cgroup').decode().splitlines()
             and process_argv == argv and process_environment.get('INVOCATION_ID') == before['InvocationID']
             and all(process_environment.get(key) == value for key, value in environment.items())
             and not process_environment.get('LD_PRELOAD'), 'ACTUAL_ROLE_IDENTITY_CHANGED:' + role)
        need(driver.unit_properties(unit, SHARED_PROPERTIES, 'BACKGROUND_GUARDS_UNIT') == before
             and driver.pid_key(pid) == identity
             and all(hashlib.sha256(owned_bytes(path)).hexdigest() == checksum for path, checksum in files.items()),
             'ROLE_CHANGED_DURING_READ:' + role)
        result[role] = {'unit': unit, 'pid_key': list(identity), 'invocation_id': before['InvocationID'],
            'wrapper': selected['wrapper'], 'wrapper_sha256': selected['wrapper_sha256'],
            'pythonpath': selected['pythonpath'], 'unit_files_sha256': files}
    current = load_binding(binding['reference'], binding['document']['producer_registration'],
                           binding['roles']['trainer']['wrapper'], binding['roles']['trainer']['wrapper_sha256'])
    need(current == binding, 'CONFIG_OR_SOURCE_CHANGED_DURING_RUNTIME_READ')
    return {'state': 'INITIAL_BACKGROUND_ROLES_VERIFIED_NO_WRITE', 'roles': result,
            'services_changed': [], 'evaluator_registration_changed': False, 'broker_orders_sent': 0}
