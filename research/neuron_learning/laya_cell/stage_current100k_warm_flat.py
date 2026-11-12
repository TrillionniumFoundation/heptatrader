#!/usr/bin/env python3
"""Stage an independent bounded warm-flat runtime; do not register or launch it.

Reuse the existing fit collector, role-configuration builder and sandboxed
registrar. Keep the completed centered candidate, pinned original training dataset,
existing role keys, selected Guard and old trial untouched. No systemctl, Gate
acquisition, Torch import, model fitting or signing occurs during staging.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

LAUNCHER = r'''#!/usr/bin/env python3
"""CPU pre-exec selection or original sandboxed registrar; explicit bounded warm-flat worker."""
import argparse, json, os, sys, time
from pathlib import Path
from historical_training.common import read, sha, digest, write_once
from historical_training.resident_resource_guard import verify_config
from laya_cell.current100k_forward_worker import validate_runtime
from laya_cell import resident_hip_runtime as hip

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True); p.add_argument('--sha256', required=True)
    p.add_argument('--mode', choices=('predict', 'evaluate', 'register'), required=True)
    p.add_argument('--request', type=Path); p.add_argument('--request-sha256')
    p.add_argument('--check-only', action='store_true')
    a = p.parse_args(); os.umask(0o077)
    if sha(a.config) != a.sha256: raise ValueError('FORWARD_CONFIG_CHANGED')
    c = read(a.config); role = {'predict': 'predictor', 'evaluate': 'evaluator', 'register': 'registrar'}[a.mode]
    if c['signing_role'] != role or a.config.name != role + '-' + digest(c) + '.json':
        raise ValueError('FORWARD_ROLE_CONFIGURATION_IDENTITY')
    validate_runtime(c)
    if sha(c['guard_config']) != c['guard_config_sha256']: raise ValueError('ORIGINAL_GUARD_CHANGED')
    hip.check_guard_selection(c, verify_config(read(c['guard_config'])))
    from laya_cell.financial_forward_witness import verify_trust
    verify_trust(c)
    environment = hip.worker_environment(c)
    if 'torch' in sys.modules: raise ValueError('FORWARD_CPU_LAUNCH_IMPORTED_TORCH')
    if a.check_only:
        print(json.dumps({'state': 'CPU_FORWARD_CLOSURE_VERIFIED', 'role': role, 'gpu_loaded': False,
            'mapped_runtime_proven': False, 'worker_admission_granted': False,
            'loader': str(Path(hip.__file__).resolve()), 'config_sha256': a.sha256}))
        return
    if a.mode != 'register':
        python = c['python'] if a.mode == 'predict' else '/usr/bin/python3'
        os.execvpe(python, [python, '-B', '-m', 'laya_cell.current100k_forward_worker',
            '--config', str(a.config), '--mode', a.mode], environment)
        return
    if a.request is None or not a.request_sha256 or sha(a.request) != a.request_sha256:
        raise ValueError('EXACT_NEW_SCOPE_REGISTRATION_REQUEST_REQUIRED')
    request = read(a.request)
    if (request['schema'] != 'heptatrader.current100k-warm-registration-request.v1'
            or type(request['expected_trial']) is not int or request['expected_trial'] < 2
            or request.get('registration_scope') != 'SAME_PRODUCER_REFIT'
            or request['input_start_delay_ms'] != 900000):
        raise ValueError('INDEPENDENT_WARM_REGISTRATION_REQUEST_REQUIRED')
    for path, expected in request['input_sha256'].items():
        if sha(path) != expected: raise ValueError('REGISTRATION_ORIGINAL_INPUT_CHANGED')
    # Check the complete history before the original registrar writes a plan.
    # Appended trials cannot silently consume a different alpha-spending index.
    from laya_cell.financial_forward_protocol import validate_plan
    from laya_cell.current100k_warm_flat import check_profile
    check_profile(c.get('warm_flat_profile'))
    if c.get('warm_flat_profile') is None:
        raise ValueError('EXPLICIT_WARM_PROFILE_REQUIRED')
    actual_paths = {str(path) for root in request['legacy_plans_roots']
                    for path in Path(root).glob('*/plan.json')}
    if actual_paths != set(request['legacy_plan_paths']):
        raise ValueError('WARM_REGISTRATION_HISTORY_CHANGED_BEFORE_WRITE')
    history = [read(path) for path in request['legacy_plan_paths']]
    for plan in history: validate_plan(plan)
    if (sorted(plan['trial'] for plan in history) != list(range(1, request['expected_trial']))
            or len({plan['plan_id'] for plan in history}) != len(history)):
        raise ValueError('WARM_REGISTRATION_COMPLETE_HISTORY_REQUIRED')
    prior = read(request['prior_plan_path'])
    if (prior not in history or prior['trial'] != request['expected_trial'] - 1
            or prior['producer_registration'] != c['producer_registration']
            or str(Path(prior['candidate_directory']) / 'candidate.json') != request['prior_candidate_path']):
        raise ValueError('WARM_SAME_PRODUCER_PRIOR_PLAN_CHANGED')
    if (type(request.get('registration_not_before_ms')) is not int
            or request['registration_not_before_ms'] != prior['outcome_end_ms']
            or int(time.time() * 1000) < request['registration_not_before_ms']):
        raise ValueError('WARM_WAIT_PRIOR_OUTCOME_END_NO_REGISTRATION')
    # A later registration revalidates the original producer, without rollover.
    from historical_training.current100k import validate_config
    validate_config({'automatic_trading': False, 'automatic_training': False,
        'period_path': c['period_path'], 'period_sha256': c['period_sha256'],
        'registration': c['producer_registration']})
    from sandbox import enter
    root = Path(c['forward_root']); root.mkdir(mode=0o700, parents=True, exist_ok=True)
    restrictions = enter({'output_dir': str(root), 'period_path': c['period_path'], 'legacy_capture': request['dataset_path'],
        'oms_path': c['oms_path'], 'export_dir': c['attribution_directory'],
        'read_only_paths': c['worker_read_only_paths'] + [c['worker_source_root'], sys.prefix,
            str(a.config), str(a.request), c['role_key_path'], c['witness_trust_path'],
            request['candidate_directory'], str(Path(request['prior_candidate_path']).parent),
            request['prior_plan_path'], *request['legacy_plans_roots'],
            *request['prior_training_read_only_paths']]})
    denied = {}
    for other, path in request['existing_role_key_paths'].items():
        if other == role: continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC); os.close(fd)
        except PermissionError: denied[other] = True
        else: raise ValueError('OTHER_ROLE_KEY_READABLE:' + other)
    write_once(root / 'sandbox-registrar.json', {**restrictions, 'role': role,
        'other_role_keys_denied': denied, 'canonical_owner_key_access': False})
    from laya_cell.current100k_forward import register, SAME_PRODUCER_REFIT
    plan = register(c, Path(request['candidate_directory']), Path(request['dataset_path']),
        Path(request['prior_candidate_path']), [Path(path) for path in request['legacy_plans_roots']],
        registration_scope=SAME_PRODUCER_REFIT, prior_plan_path=Path(request['prior_plan_path']),
        input_start_delay_ms=request['input_start_delay_ms'])
    if plan['trial'] != request['expected_trial']: raise ValueError('INDEPENDENT_WARM_TRIAL_HISTORY_CHANGED')
    path = root / 'plans' / plan['plan_id'] / 'plan.json'
    print(json.dumps({'state': 'REGISTERED_SAME_PRODUCER_WARM_PROFILE', 'trial': plan['trial'],
        'plan_path': str(path), 'plan_sha256': sha(path), 'plan_id': plan['plan_id'],
        'input_start_ms': plan['input_start_ms'], 'input_end_ms': plan['input_end_ms'],
        'outcome_end_ms': plan['outcome_end_ms'], 'gpu_loaded': False, 'keys_created': 0,
        'old_plans_modified': False, 'broker_orders_sent': 0}, sort_keys=True))

if __name__ == '__main__':
    main()
'''


def need(ok, reason):
    if not ok: raise ValueError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(path, limit=64 * 1024 * 1024):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'CANONICAL_INPUT_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
             and before.st_nlink == 1 and not before.st_mode & 0o022 and before.st_size <= limit,
             'REGULAR_OWNED_UNMODIFIABLE_INPUT_REQUIRED')
        raw = stream.read(limit + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_uid, s.st_nlink)
        need(len(raw) == before.st_size and identity(before) == identity(os.fstat(stream.fileno()))
             == identity(path.lstat()), 'INPUT_CHANGED_DURING_READ')
    return raw

def assemble_files(repo, original, wrapper):
    from laya_cell.install_resident import runtime_files, retained_hip_manifest
    from historical_training.install_consumer import runtime_inventory
    files = runtime_files(repo / 'research/neuron_learning/laya_cell', original['native_binary'])
    # The ordinary consumer collector supplies the authoritative reward/source
    # closure. Its learner layout is mapped to the ordinary resident layout.
    for name in runtime_inventory(repo):
        target = name.replace('research/neuron_learning/', 'learn/', 1) if name.startswith('research/neuron_learning/') else name
        need(target not in files or files[target] == repo / name, 'SOURCE_CLOSURE_COLLISION')
        files[target] = repo / name
    for name in ['current100k_value_trial.py', *wrapper['guard_files_sha256']]:
        need(Path(name).name == name, 'GUARD_SOURCE_PATH_INVALID')
        files['research/historical_training/' + name] = repo / 'research/historical_training' / name
    init = repo / 'research/historical_training/__init__.py'
    if init.is_file():
        files['research/historical_training/__init__.py'] = init
    selected = retained_hip_manifest(original)
    need(selected is not None, 'EXISTING_ACCEPTED_HIP_SELECTION_REQUIRED')
    files[selected[0]] = selected[1]
    return files

def role_configuration(prior, fit, consumer, producer, release, inventory, forward_root, candidate, dataset):
    from historical_training.common import digest
    need(prior['schema'] == 'heptatrader.current100k-forward-worker-config.v1'
         and prior['automatic_trading'] is False and prior['actor_update'] is False
         and prior['period_sha256'] == consumer['period_sha256'] == fit['period_sha256']
         and prior['oms_path'] == consumer['reward_config']['oms_path'], 'ORIGINAL_FORWARD_BEHAVIOR_OR_OMS_CHANGED')
    c = copy.deepcopy(prior)
    registration = copy.deepcopy(consumer['registration'])
    c.update(forward_root=str(forward_root), worker_source_root=str(release), worker_runtime_sha256=inventory,
        worker_runtime_inventory_digest=digest(inventory), runtime_directory=str(release), runtime_sha256=inventory,
        hip_runtime=copy.deepcopy(fit['hip_runtime']), python=fit['python'], guard_config=fit['guard_config'],
        guard_config_sha256=fit['guard_config_sha256'], producer_registration=registration,
        prediction_path_profile='VERIFIED_TURN_SNAPSHOT_V1', source_commit=fit['source_commit'],
        attribution_directory=producer['journal_directory'])
    c['decision_attribution_registrations'] = {**c['decision_attribution_registrations'], 'EUR.USD': [registration]}
    c['worker_read_only_paths'] = list(dict.fromkeys([*c['worker_read_only_paths'], str(candidate), str(dataset),
        str(release), fit['runtime_directory'], registration['manifest']]))
    return c

def training_readonly_paths(data, dataset):
    """Name exact frozen prefix files; appends do not invalidate old prefixes."""
    paths = set()
    for row in data['rows']:
        prefixes = row['reward']['input_attribution']['source_prefixes']
        need(isinstance(prefixes, list) and 0 < len(prefixes) <= 64, 'BOUNDED_SOURCE_PREFIXES_REQUIRED')
        for prefix in prefixes:
            path = Path(prefix['path'])
            need(path.is_absolute() and path.resolve() == path and path.is_file()
                 and type(prefix['bytes']) is int and prefix['bytes'] >= 0
                 and re.fullmatch('[0-9a-f]{64}', prefix['sha256']) is not None,
                 'EXACT_SOURCE_PREFIX_PATH_REQUIRED')
            paths.add(str(path))
    need(len(paths) <= 128, 'BOUNDED_SOURCE_FILES_REQUIRED')
    return [str(dataset), *sorted(paths)]


def check_history(paths, prior_path, data):
    from laya_cell.financial_forward_protocol import validate_plan
    from historical_training.common import read
    history = [read(path) for path in paths]
    for path, plan in zip(paths, history):
        validate_plan(plan)
        need(path.name == 'plan.json' and path.parent.name == plan['plan_id'], 'HISTORICAL_PLAN_PATH_IDENTITY')
    need(history and len({plan['plan_id'] for plan in history}) == len(history)
         and sorted(plan['trial'] for plan in history) == list(range(1, len(history) + 1)),
         'COMPLETE_ORIGINAL_TRIAL_HISTORY_REQUIRED')
    prior = read(prior_path)
    need(prior in history and prior['trial'] == len(history)
         and set(prior.get('old_trial_ids_retained', []))
             == {plan['plan_id'] for plan in history if plan['plan_id'] != prior['plan_id']}
         and prior['producer_registration']['sha256'] == data['producer_manifest_sha256']
         and prior['period_sha256'] == data['period_sha256']
         and {row['behavior_scope']['model_id'] for row in data['rows']}
             == {prior['behavior_scope']['behavior_model_id']}, 'SAME_PRODUCER_BEHAVIOR_AND_PRIOR_REQUIRED')
    # An independent trial may be prepared while the prior trial still runs.
    # The old outcome window need not be over; none of its bytes are changed.
    return prior, len(history) + 1


def finite_unit(mode, config, config_sha, release, python, trial):
    need(mode in ('predict', 'evaluate') and all(not re.search(r'[\s%]', str(p))
         for p in (config, release, python)), 'FINITE_WARM_UNIT_PATH_SCOPE')
    return '\n'.join(('[Unit]', 'StartLimitIntervalSec=10s', 'StartLimitBurst=12',
        f'Description=HeptaTrader independent Current100k warm trial {trial} {mode}', '',
        '[Service]', 'Type=oneshot',
        f'ExecStart={python} -B {release}/bin/current100k-warm-launch.py --config {config} --sha256 {config_sha} --mode {mode}',
        f'WorkingDirectory={release}', f'Environment=PYTHONPATH={release}/research:{release}/learn:{release}/scripts',
        'Environment=PYTHONDONTWRITEBYTECODE=1', 'Environment=OPENBLAS_NUM_THREADS=1',
        'Environment=OMP_NUM_THREADS=2', 'Environment=HF_HUB_OFFLINE=1',
        'Environment=TRANSFORMERS_OFFLINE=1', 'Environment=USE_TF=0', 'UMask=0077',
        'Nice=15', 'CPUQuota=' + ('150%' if mode == 'predict' else '50%'), 'CPUWeight=10',
        'MemoryMax=' + ('4G' if mode == 'predict' else '512M'), 'TasksMax=96',
        'NoNewPrivileges=yes', 'RestrictSUIDSGID=yes', 'RestrictAddressFamilies=AF_UNIX',
        'LockPersonality=yes', 'Restart=no', 'TimeoutStartSec=240', 'TimeoutStopSec=20',
        'KillMode=control-group', 'SendSIGKILL=yes', ''))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'fit-stage', 'prior-forward-stage', 'guard-config', 'forward-root', 'evidence-root'):
        p.add_argument('--' + name, type=Path, required=True)
    for name in ('expected-head', 'fit-stage-sha256', 'prior-forward-stage-sha256',
                 'guard-config-sha256', 'candidate-id', 'dataset-sha256'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--legacy-plan', type=Path, action='append', required=True)
    a = p.parse_args(); os.umask(0o077)
    need(os.geteuid() != 0, 'ORIGINAL_UNPRIVILEGED_USER_REQUIRED')
    for path, wanted in ((a.fit_stage, a.fit_stage_sha256),
                         (a.prior_forward_stage, a.prior_forward_stage_sha256),
                         (a.guard_config, a.guard_config_sha256)):
        need(re.fullmatch('[0-9a-f]{64}', wanted) and hashlib.sha256(checked(path)).hexdigest() == wanted,
             'PINNED_WARM_PREPARATION_INPUT_CHANGED')
    repo = a.repo.resolve(strict=True)
    git = lambda *parts: subprocess.check_output(['git', '-C', str(repo), *parts], text=True, timeout=20).strip()
    need(git('rev-parse', 'HEAD') == a.expected_head and not git('status', '--porcelain', '--untracked-files=all'),
         'EXACT_CLEAN_WARM_SOURCE_COMMIT_REQUIRED')
    need(Path(__file__).resolve() == repo / 'research/neuron_learning/laya_cell/stage_current100k_warm_flat.py',
         'STAGER_MUST_RUN_FROM_SELECTED_CLEAN_SOURCE')
    sys.path[:0] = [str(repo / part) for part in ('research', 'research/neuron_learning', 'scripts')]
    from historical_training.common import read, digest, canonical, write_once
    from historical_training.current100k import validate_config
    from historical_training.resident_resource_guard import verify_config
    from laya_cell.current100k_forward import validate_value_candidate
    from laya_cell.financial_value_profile import CENTERED_RESIDUAL_PROFILE
    from laya_cell.financial_forward_witness import verify_trust
    from laya_cell.current100k_warm_flat import PROFILE
    from laya_cell import resident_hip_runtime as hip
    from laya_cell.immutable_files import write_immutable

    stage = read(a.fit_stage)
    need(stage['state'] == 'STAGED_CPU_VERIFIED_NOT_STARTED' and sha(stage['config']) == stage['config_sha256'],
         'ORIGINAL_COMPLETED_FIT_CONFIGURATION_REQUIRED')
    fit = read(stage['config']); dataset_path = Path(fit['dataset_path']); data = read(dataset_path)
    need(sha(dataset_path) == fit['dataset_sha256'] == stage['dataset_sha256'] == a.dataset_sha256
         and fit['readout_profile'] == CENTERED_RESIDUAL_PROFILE and fit['steps'] == 16
         and fit['max_rows'] == 64 and fit['scale_usd'] == 100, 'ORIGINAL_CENTERED_FIT_REQUIRED')
    for name, wanted in fit['runtime_sha256'].items():
        need(not Path(name).is_absolute() and '..' not in Path(name).parts
             and sha(Path(fit['runtime_directory']) / name) == wanted, 'FROZEN_FIT_RUNTIME_CHANGED')
    candidate_dir = Path(fit['output_root']); candidate = validate_value_candidate(candidate_dir)
    need(candidate['candidate_id'] == a.candidate_id and candidate['dataset_sha256'] == a.dataset_sha256
         and candidate['rows'] == len(data['rows']) and 32 <= candidate['rows'] <= 64
         and candidate['row_ids'] == [row['row_id'] for row in data['rows']]
         and candidate['source_files_sha256'] == fit['source_files_sha256']
         and candidate['readout_profile'] == CENTERED_RESIDUAL_PROFILE
         and candidate['hip_runtime_manifest_sha256'] == fit['hip_runtime']['sha256'], 'COMPLETED_CENTERED_CANDIDATE_CHANGED')
    need(sha(fit['consumer_config']) == fit['consumer_config_sha256'], 'FROZEN_CONSUMER_CHANGED')
    consumer = read(fit['consumer_config']); producer = validate_config(consumer)
    need(consumer['registration']['sha256'] == data['producer_manifest_sha256'], 'FROZEN_PRODUCER_CHANGED')
    wrapper = read(a.guard_config); owner = verify_config(wrapper); hip.check_guard_selection(fit, owner)
    need(owner['period_sha256'] == consumer['period_sha256'] == data['period_sha256'], 'WARM_PERIOD_CHANGED')
    prior_stage = read(a.prior_forward_stage); prior_path = Path(prior_stage['registration']['plan_path'])
    need(sha(prior_path) == prior_stage['registration']['plan_sha256'], 'ORIGINAL_TRIAL_PLAN_CHANGED')
    for path in a.legacy_plan: checked(path)
    prior, trial = check_history(a.legacy_plan, prior_path, data)
    legacy_roots = list(dict.fromkeys(str(path.parent.parent) for path in a.legacy_plan))
    need({str(path) for root in legacy_roots for path in Path(root).glob('*/plan.json')}
         == {str(path) for path in a.legacy_plan}, 'COMPLETE_HISTORY_PATHS_REQUIRED')
    originals = {}; common = None
    for role in ('predictor', 'evaluator', 'registrar'):
        source = prior_stage['configs'][role]
        need(hashlib.sha256(checked(Path(source['path']))).hexdigest() == source['sha256'], 'ORIGINAL_ROLE_CONFIG_CHANGED')
        config = read(source['path']); verify_trust(config)
        need(config['signing_role'] == role and config['forward_root'] == prior['registry_root'], 'ORIGINAL_ROLE_SCOPE_CHANGED')
        key = Path(config['role_key_path']); info = key.lstat()
        need(key.is_absolute() and key.resolve() == key and stat.S_ISREG(info.st_mode)
             and info.st_uid == os.geteuid() and info.st_nlink == 1 and not info.st_mode & 0o077,
             'EXISTING_ROLE_KEY_METADATA_REQUIRED_NO_GENERATION')
        shared = {k: v for k, v in config.items() if k not in ('signing_role', 'role_key_path')}
        if common is None: common = shared
        need(shared == common, 'ORIGINAL_ROLE_CONFIGURATIONS_DISAGREE'); originals[role] = config
    need(len({c['role_key_path'] for c in originals.values()}) == 3, 'ORIGINAL_ROLES_NOT_DISTINCT')
    old_guard = originals['predictor']
    need(sha(old_guard['guard_config']) == old_guard['guard_config_sha256'], 'ORIGINAL_FORWARD_GUARD_CHANGED')
    previous_wrapper = read(old_guard['guard_config'])
    need(all(wrapper[key] == previous_wrapper[key] for key in
             ('state_root', 'manual_hold_path', 'resource_rule', 'max_heavy_workers', 'period_path', 'period_sha256')),
         'ORIGINAL_SHARED_HEAVY_LEASE_OR_RESOURCE_POLICY_CHANGED')
    for directory in (a.forward_root, a.evidence_root):
        need(directory.is_absolute() and directory.resolve() == directory and not directory.exists()
             and not directory.is_relative_to(Path(prior['registry_root'])), 'NEW_SEPARATE_FORWARD_AND_EVIDENCE_ROOTS_REQUIRED')

    files = assemble_files(repo, owner, wrapper)
    inventory = {name: sha(path) for name, path in files.items()}
    launcher = LAUNCHER.encode()
    inventory['bin/current100k-warm-launch.py'] = hashlib.sha256(launcher).hexdigest()
    release_id = digest(inventory)
    release = Path.home() / '.local/share/heptatrader/current100k-forward/releases' / release_id
    for name, wanted in inventory.items():
        target = release / name; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = launcher if name == 'bin/current100k-warm-launch.py' else files[name]
        write_immutable(target, value, 0o500 if name.startswith('bin/hepta-') else 0o400,
                        'IMMUTABLE_WARM_CLOSURE_CONFLICT', wanted)
    write_once(release / 'manifest.json', {'schema': 'heptatrader.current100k-forward-release.v1',
        'release_id': release_id, 'source_commit': a.expected_head, 'fit_source_commit': fit['source_commit'], 'files': inventory})
    new_fit = {**fit, 'source_commit': a.expected_head, 'runtime_directory': str(release),
        'period_sha256': owner['period_sha256'], 'guard_config': str(a.guard_config), 'guard_config_sha256': a.guard_config_sha256}
    configs = {}; paths = {}; a.evidence_root.mkdir(mode=0o700, parents=True)
    for role, original in originals.items():
        config = role_configuration(original, new_fit, consumer, producer, release, inventory,
                                            a.forward_root, candidate_dir, dataset_path)
        config['warm_flat_profile'] = copy.deepcopy(PROFILE)
        path = a.evidence_root / (role + '-' + digest(config) + '.json')
        write_once(path, config); configs[role] = config; paths[role] = path
    prior_training = {'dataset_path': str(dataset_path), 'read_only_paths': training_readonly_paths(data, dataset_path)}
    prior_candidate = Path(prior['candidate_directory']) / 'candidate.json'
    pinned = [*a.legacy_plan, prior_candidate, candidate_dir / 'candidate.json',
              candidate_dir / 'value-head.safetensors', dataset_path, Path(prior_training['dataset_path'])]
    request = {'schema': 'heptatrader.current100k-warm-registration-request.v1', 'expected_trial': trial,
        'registration_scope': 'SAME_PRODUCER_REFIT', 'input_start_delay_ms': 900000,
        'registration_not_before_ms': prior['outcome_end_ms'],
        'candidate_directory': str(candidate_dir), 'dataset_path': str(dataset_path),
        'prior_candidate_path': str(prior_candidate), 'prior_plan_path': str(prior_path),
        'legacy_plans_roots': legacy_roots, 'legacy_plan_paths': [str(path) for path in a.legacy_plan],
        'input_sha256': {str(path): sha(path) for path in pinned},
        'prior_training_read_only_paths': prior_training['read_only_paths'],
        'existing_role_key_paths': {role: config['role_key_path'] for role, config in originals.items()}}
    request_path = a.evidence_root / 'REGISTRATION_REQUEST.json'; write_once(request_path, request)
    environment = dict(os.environ, PYTHONPATH=':'.join(str(release / part) for part in ('research', 'learn', 'scripts')),
        PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='2', HF_HUB_OFFLINE='1',
        TRANSFORMERS_OFFLINE='1', USE_TF='0')
    launch = str(release / 'bin/current100k-warm-launch.py'); argv = {}; proofs = {}
    for role, mode in (('registrar', 'register'), ('predictor', 'predict'), ('evaluator', 'evaluate')):
        python = fit['python'] if mode == 'predict' else '/usr/bin/python3'
        command = [python, '-B', launch, '--config', str(paths[role]), '--sha256', sha(paths[role]), '--mode', mode]
        result = subprocess.run([*command, '--check-only'], cwd=release, env=environment,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        write_once(a.evidence_root / ('CPU_CHECK_' + role + '.json'), {'returncode': result.returncode,
            'stdout': result.stdout[:131072], 'stderr': result.stderr[:131072], 'argv': [*command, '--check-only']})
        need(result.returncode == 0, 'WARM_CPU_CHECK_FAILED:' + role)
        proof = json.loads(result.stdout)
        need(proof['state'] == 'CPU_FORWARD_CLOSURE_VERIFIED' and proof['gpu_loaded'] is False
             and proof['worker_admission_granted'] is False
             and proof['loader'] == str(release / 'learn/laya_cell/resident_hip_runtime.py'), 'WARM_CPU_LOADER_OR_SCOPE_CHANGED')
        argv[role] = command; proofs[role] = proof
    argv['registrar'] += ['--request', str(request_path), '--request-sha256', sha(request_path)]
    units = {}
    for role, mode in (('predictor', 'predict'), ('evaluator', 'evaluate')):
        name = f'heptatrader-current100k-warm-trial{trial}-{role}.service'
        path = a.evidence_root / (name + '.candidate')
        text = finite_unit(mode, paths[role], sha(paths[role]), release,
                           fit['python'] if mode == 'predict' else '/usr/bin/python3', trial)
        write_immutable(path, text.encode(), 0o400, 'FINITE_WARM_UNIT_CANDIDATE_CHANGED')
        units[role] = {'name': name, 'candidate': str(path), 'sha256': sha(path)}
    need(git('rev-parse', 'HEAD') == a.expected_head and not git('status', '--porcelain', '--untracked-files=all'),
         'SOURCE_CHANGED_DURING_WARM_PREPARATION')
    need(not a.forward_root.exists() and 'torch' not in sys.modules, 'STAGING_MUST_NOT_REGISTER_OR_LOAD_GPU')
    receipt = {'schema': 'heptatrader.current100k-warm-flat-stage.v1', 'state': 'STAGED_CPU_VERIFIED_NOT_REGISTERED',
        'source_commit': a.expected_head, 'release': str(release), 'release_id': release_id,
        'fit_stage': str(a.fit_stage), 'fit_stage_sha256': a.fit_stage_sha256,
        'prior_forward_stage': str(a.prior_forward_stage), 'prior_forward_stage_sha256': a.prior_forward_stage_sha256,
        'candidate_id': candidate['candidate_id'], 'dataset_sha256': a.dataset_sha256, 'rows': candidate['rows'],
        'expected_trial': trial, 'forward_root': str(a.forward_root),
        'prior_outcome_end_ms': prior['outcome_end_ms'], 'registration_scheduled': False,
        'configs': {role: {'path': str(path), 'sha256': sha(path)} for role, path in paths.items()},
        'registration_request': str(request_path), 'registration_request_sha256': sha(request_path),
        'explicit_argv': argv, 'pythonpath': environment['PYTHONPATH'], 'cpu_checks': proofs, 'unit_candidates': units,
        'guard_config': str(a.guard_config), 'guard_config_sha256': a.guard_config_sha256,
        'guard_priority_changed': False, 'unit_installed': False, 'services_changed': [], 'keys_created': 0,
        'old_plans_modified': False, 'gpu_loaded': False, 'candidate_retrained': False,
        'trial_registered': False, 'timers_created': False, 'broker_orders_sent': 0}
    write_once(a.evidence_root / 'WARM_STAGE.json', receipt)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
