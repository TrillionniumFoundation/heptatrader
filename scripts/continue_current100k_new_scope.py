#!/usr/bin/env python3
"""Resume one explicitly pinned new-producer preparation through ordinary activation.

The persistent CPU timer waits for 32 genuine settled rows. It preserves the
original preparation request and one frozen cohort. Training uses the original
fixed-objective launcher, a newly pinned runtime, and the unchanged shared
Guard. A completed fit enters the normal NEW_PRODUCER_SCOPE warm stager and
finite registration/activation wake. Unknown or partial fit effects are never
automatically retried. No driver, Gateway, order, label, or old plan is changed.
"""
from __future__ import annotations
import argparse
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

from prepare_current100k_new_scope import (
    POLICY, MIN_ROWS, canonical, digest, prepare_ready, write_once, write_status,
)
from observe_current100k_requests import read

SCHEMA = 'heptatrader.current100k-newscope-continuation.v1'
WORKER_PROFILES = ('warm-flat-v1', 'cpu-standby-v2')


def need(value, reason):
    if not value:
        raise ValueError('NEW_SCOPE_CONTINUATION_' + reason)


def sha(path):
    from laya_cell.stage_current100k_warm_flat import checked
    return hashlib.sha256(checked(Path(path))).hexdigest()


def pinned(path, checksum):
    from laya_cell.stage_current100k_warm_flat import checked
    raw = checked(Path(path))
    need(re.fullmatch('[0-9a-f]{64}', checksum) and hashlib.sha256(raw).hexdigest() == checksum,
         'INPUT_CHANGED:' + str(path))
    return json.loads(raw)


def repository_check(source, expected):
    source = Path(source)
    need(source.is_absolute() and source.resolve() == source, 'CANONICAL_SOURCE_REQUIRED')
    command = ['git', '-C', str(source)]
    head = subprocess.check_output([*command, 'rev-parse', 'HEAD'], text=True, timeout=20).strip()
    dirty = subprocess.check_output([*command, 'status', '--porcelain', '--untracked-files=all'],
        text=True, timeout=20).strip()
    need(head == expected and not dirty, 'EXACT_CLEAN_CONTINUATION_SOURCE_REQUIRED')
    return source


def source_check(request):
    source = Path(request['source_repository'])
    need(Path(__file__).resolve() == source / 'scripts/continue_current100k_new_scope.py', 'SELECTED_SOURCE_REQUIRED')
    return repository_check(source, request['source_commit'])


def worker_profile(request):
    # An absent field retains the semantics of an already sealed v1 request.
    profile = request.get('worker_profile', 'warm-flat-v1')
    need(profile in WORKER_PROFILES, 'WORKER_PROFILE_REQUIRED')
    return profile


def hardware_evidence(request, prior, prior_sha, *, source_root=None):
    pin = request.get('worker_profile_evidence')
    if worker_profile(request) == 'warm-flat-v1':
        need(pin is None, 'V1_HAS_NO_STANDBY_HARDWARE_SELECTION')
        return None
    need(isinstance(pin, dict) and set(pin) == {'report', 'lifecycle'}, 'CPU_STANDBY_HARDWARE_PROOF_REQUIRED')
    for name in ('report', 'lifecycle'):
        need(isinstance(pin[name], dict) and set(pin[name]) == {'path', 'sha256'},
             'CPU_STANDBY_HARDWARE_REPORT_AND_LIFECYCLE_PINS_REQUIRED')
    evidence = pinned(pin['report']['path'], pin['report']['sha256'])
    lifecycle = pinned(pin['lifecycle']['path'], pin['lifecycle']['sha256'])
    need(evidence.get('state') == 'HISTORICAL_PARITY_EXACT'
         and evidence.get('scope') == 'HISTORICAL_CPU_STANDBY_PARITY_NOT_REGISTERED_FORWARD_PROFILE'
         and evidence.get('exact_parity') is True and evidence.get('historical_loader_exact_parity') is True
         and evidence.get('plan_id') == prior['plan_id'] and evidence.get('plan_sha256') == prior_sha
         and all(evidence.get(key) == prior[key] for key in ('candidate_id', 'base_id'))
         and evidence.get('head_sha256') == prior['head_tensor_sha256'],
         'CPU_STANDBY_HARDWARE_PROOF_OR_PRIOR_CHANGED')
    cpu = evidence.get('cpu_proof', {})
    need(cpu.get('all_parameters_and_buffers_cpu') is True and cpu.get('cuda_initialized') is False
         and cpu.get('gpu_device_fds') == [], 'CPU_STANDBY_HARDWARE_NOT_CPU_ONLY')
    need(all(evidence.get(key) is True for key in ('tokenizer_input_ids_and_masks_exact',
         'features_bitwise_equal', 'values_bitwise_equal'))
         and evidence.get('initial_parameters_and_all_buffers', {}).get('exact') is True
         and evidence.get('after_inference_parameters_and_all_buffers', {}).get('exact') is True
         and evidence.get('feature_max_absolute_difference') == 0
         and evidence.get('value_max_absolute_difference_usd') == 0,
         'CPU_STANDBY_HARDWARE_PARITY_NOT_EXACT')
    need(all(evidence.get(key) is False for key in ('forward_seal_created', 'v2_plan_registered',
         'optimizer_used', 'publish_path_exercised')) and evidence.get('broker_orders_sent') == 0,
         'CPU_STANDBY_HARDWARE_DIAGNOSTIC_SCOPE_CHANGED')
    # A child writes report.json before its parent reaps it. A report alone is
    # never evidence that the original lease survived until exit and released.
    need(lifecycle.get('scope') == evidence['scope'] and lifecycle.get('phase') == 'COMPLETE'
         and lifecycle.get('error') is None and 'error' in lifecycle
         and all(lifecycle.get(key) is True for key in ('child_reaped', 'initial_lease_released',
             'fresh_admission_granted', 'fresh_lease_released_after_reap'))
         and lifecycle.get('parent_imported_torch') is False
         and lifecycle.get('completion_receipt', {}).get('report_sha256') == pin['report']['sha256']
         and lifecycle.get('completion_receipt', {}).get('exact_parity') is True
         and lifecycle.get('forward_seal_created') is False and lifecycle.get('broker_orders_sent') == 0,
         'CPU_STANDBY_HARDWARE_LIFECYCLE_NOT_COMPLETE')
    # The selected hardware comparison destroys the first process before a
    # separately admitted baseline. Residual process-owned HIP workspace is
    # recorded, not treated as live memory after the process has been reaped.
    need(evidence.get('comparison_mode') == lifecycle.get('comparison_mode') == 'separate-process'
         and evidence.get('model_processes_never_overlap') is True
         and evidence.get('first_model_memory_released_by') == 'PROCESS_EXIT_AND_REAP'
         and all(lifecycle.get(key) is True for key in ('staged_child_reaped',
             'staged_gpu_lease_released_after_reap', 'direct_original_admission_granted',
             'direct_child_started', 'direct_child_reaped', 'direct_lease_released_after_reap'))
         and lifecycle['completion_receipt'].get('kind') == 'PARENT_TWO_PROCESS_COMPARISON'
         and type(lifecycle.get('staged_child_exit_code')) is int
         and type(evidence.get('staged_child_exit_code')) is int
         and lifecycle['staged_child_exit_code'] == evidence['staged_child_exit_code'] == 0
         and type(lifecycle.get('direct_child_exit_code')) is int
         and type(evidence.get('direct_child_exit_code')) is int
         and lifecycle.get('direct_child_exit_code') == evidence.get('direct_child_exit_code') == 0
         and lifecycle.get('staged_completion_receipt', {}).get('files_sha256')
             == evidence.get('staged_result_files_sha256')
         and isinstance(evidence.get('staged_result_files_sha256'), dict)
         and set(evidence['staged_result_files_sha256']) == {'staged-result.json', 'staged-state-hashes.json',
             'allocation-after-destroy.json', 'future-tokenizer-result.json'},
         'CPU_STANDBY_TWO_CHILD_LIFECYCLE_NOT_COMPLETE')
    # The old baseline used AutoTokenizer on both paths. Require a separate
    # comparison against this future executor's tokenizer implementation too.
    source = Path(source_root if source_root is not None else request['source_repository'])
    future = evidence.get('future_tokenizer_parity', {})
    need(all(future.get(key) is True for key in ('requested', 'exact', 'tokenizer_input_ids_and_masks_exact',
             'features_bitwise_equal', 'values_bitwise_equal', 'private_module_only_no_laya_module_replaced',
             'same_original_gpu_model_and_head'))
         and future.get('feature_max_absolute_difference') == 0
         and future.get('value_max_absolute_difference_usd') == 0
         and future.get('source', {}).get('sha256') == sha(source / 'research/neuron_learning/laya_cell/tokenizer.py'),
         'CPU_STANDBY_FUTURE_TOKENIZER_PARITY_OR_SOURCE_CHANGED')
    return {'report': evidence, 'lifecycle': lifecycle}


def predecessor_inputs(request):
    pin = request.get('supersedes_continuation')
    if pin is None:
        return None
    need(isinstance(pin, dict) and set(pin) == {'request', 'sha256'}, 'PREDECESSOR_PIN_REQUIRED')
    old = pinned(pin['request'], pin['sha256'])
    need(old.get('schema') == SCHEMA and old.get('supersedes_continuation') is None
         and worker_profile(old) == 'warm-flat-v1', 'ONLY_ORIGINAL_V1_PREDECESSOR_SUPPORTED')
    same = ('preparation_request', 'preparation_request_sha256', 'preparation_source_commit',
            'fit_template', 'fit_template_sha256', 'prior_forward_stage', 'prior_forward_stage_sha256',
            'guard_config', 'guard_config_sha256', 'fit_timeout_seconds', 'activation_readmission_seconds',
            'activation_events', 'automatic_training', 'automatic_normal_registration_activation',
            'trade_authority', 'old_trials_modified')
    need(all(key in old and old[key] == request.get(key) for key in same), 'PREDECESSOR_CONTRACT_CHANGED')
    old_root = Path(old['controller_root'])
    need(Path(pin['request']) == old_root / 'CONTINUATION_REQUEST.json'
         and old_root.is_absolute() and old_root.resolve() == old_root, 'PREDECESSOR_CANONICAL_REQUEST_REQUIRED')
    repository_check(old['source_repository'], old['source_commit'])
    protected = [Path(old[key]) for key in ('controller_root', 'fit_output_root', 'forward_root', 'source_repository')]
    for key in ('controller_root', 'fit_output_root', 'forward_root', 'source_repository'):
        output = Path(request[key])
        need(all(not output.is_relative_to(path) and not path.is_relative_to(output) for path in protected),
             'PREDECESSOR_OUTPUTS_MUST_REMAIN_SEPARATE')
    return {'request': old, 'path': pin['request'], 'sha256': pin['sha256'], 'root': old_root}


def load_inputs(request):
    need(request.get('schema') == SCHEMA and request.get('automatic_training') is True
         and request.get('automatic_normal_registration_activation') is True
         and request.get('trade_authority') is False and request.get('old_trials_modified') is False,
         'EXPLICIT_RESEARCH_CONTINUATION_REQUIRED')
    source = source_check(request)
    prep_path = Path(request['preparation_request'])
    prep = pinned(prep_path, request['preparation_request_sha256'])
    need(prep.get('schema') == 'heptatrader.current100k-newscope-preparation.v1'
         and prep.get('selection_policy') == POLICY and prep.get('minimum_rows') == MIN_ROWS
         and prep.get('registration_scope') == 'NEW_PRODUCER_SCOPE'
         and prep_path.name == 'PREPARATION_REQUEST.json', 'ORIGINAL_PREPARATION_REQUIRED')
    # These are two distinct historical facts. A later executor may continue
    # the pinned preparation, but cannot rewrite the original source identity.
    need(request['preparation_source_commit'] == prep['source_commit'], 'PREPARATION_SOURCE_CHANGED')
    consumer = pinned(prep['consumer_config'], prep['consumer_config_sha256'])
    need(consumer['registration'] == prep['producer_registration'], 'PRODUCER_CHANGED')
    from historical_training.current100k import validate_config
    from historical_training.resident_resource_guard import verify_config
    from laya_cell.financial_forward_protocol import validate_plan
    from laya_cell.financial_value_profile import CENTERED_RESIDUAL_PROFILE
    validate_config(consumer)
    history = [pinned(path, value) for path, value in prep['legacy_plans_sha256'].items()]
    for path, plan in zip(prep['legacy_plans_sha256'], history):
        validate_plan(plan)
        need(Path(path).name == 'plan.json' and Path(path).parent.name == plan['plan_id'], 'HISTORY_PATH_CHANGED')
    need(sorted(plan['trial'] for plan in history) == list(range(1, prep['expected_trial']))
         and len({plan['plan_id'] for plan in history}) == len(history), 'COMPLETE_HISTORY_REQUIRED')
    prior = max(history, key=lambda plan: plan['trial'])
    need(prior['plan_id'] == prep['prior_plan_id'], 'PRIOR_TRIAL_CHANGED')
    prior_sha = next(value for path, value in prep['legacy_plans_sha256'].items()
                     if Path(path).parent.name == prior['plan_id'])
    hardware = hardware_evidence(request, prior, prior_sha, source_root=source)
    predecessor = predecessor_inputs(request)
    roots = {Path(path).parent.parent for path in prep['legacy_plans_sha256']}
    need({str(path) for root in roots for path in root.glob('*/plan.json')}
         == set(prep['legacy_plans_sha256']), 'HISTORY_CHANGED_NO_REALLOCATION')
    prior_stage = pinned(request['prior_forward_stage'], request['prior_forward_stage_sha256'])
    need(prior_stage['registration']['plan_path'] in prep['legacy_plans_sha256']
         and prior_stage['registration']['plan_sha256']
         == prep['legacy_plans_sha256'][prior_stage['registration']['plan_path']]
         and Path(prior_stage['registration']['plan_path']).parent.name == prior['plan_id'], 'PRIOR_STAGE_CHANGED')
    template = pinned(request['fit_template'], request['fit_template_sha256'])
    need(template.get('schema') == 'heptatrader.current100k-value-trial-config.v1'
         and template.get('readout_profile') == CENTERED_RESIDUAL_PROFILE
         and template.get('max_rows') == 64 and template.get('steps') == 16 and template.get('scale_usd') == 100
         and all(template.get(k) is False for k in ('actor_update', 'automatic_selection', 'trade_authority')),
         'FIXED_CENTERED_FIT_REQUIRED')
    wrapper = pinned(request['guard_config'], request['guard_config_sha256'])
    owner = verify_config(wrapper)
    need(template['hip_runtime'] == owner['hip_runtime'] and template['python'] == owner['python']
         and template['base_directory'] == owner['base_directory'], 'ACCEPTED_MODEL_PYTHON_HIP_CHANGED')
    need(type(request['fit_timeout_seconds']) is int and 60 <= request['fit_timeout_seconds'] <= 900
         and type(request['activation_readmission_seconds']) is int
         and 60 <= request['activation_readmission_seconds'] <= 3600
         and type(request['activation_events']) is int and 2 <= request['activation_events'] <= 120,
         'FINITE_CHILD_AND_ACTIVATION_BOUNDS_REQUIRED')
    outputs = [Path(request[key]) for key in ('controller_root', 'fit_output_root', 'forward_root')]
    protected = [source, Path(consumer['output_root']), prep_path.parent,
                 Path(template['output_root']), *roots]
    for output in outputs:
        need(output.is_absolute() and output.resolve() == output
             and all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in protected)
             and all(output == other or not output.is_relative_to(other) for other in outputs),
             'SEPARATE_CANONICAL_OUTPUT_ROOTS_REQUIRED')
    need(len(set(outputs)) == len(outputs), 'DISTINCT_OUTPUT_ROOTS_REQUIRED')
    return {'source': source, 'prep_path': prep_path, 'prep': prep, 'consumer': consumer,
        'history': history, 'prior': prior, 'template': template, 'wrapper': wrapper, 'owner': owner,
        'worker_profile': worker_profile(request), 'hardware_evidence': hardware, 'predecessor': predecessor}


def environment(root, layout=('research', 'learn', 'scripts')):
    return dict(os.environ, PYTHONPATH=':'.join(str(Path(root) / part) for part in layout),
        PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='2',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', USE_TF='0')


def child(argv, root, timeout, *, layout=('research', 'learn', 'scripts')):
    return subprocess.run(argv, cwd=root, env=environment(root, layout),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False)


def ready(request, inputs):
    prep = inputs['prep']; output = inputs['prep_path'].parent
    lock = os.open(output / 'preparation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None, {'state': 'WAITING_ORIGINAL_PREPARATION_WATCHER_LOCK'}
        consumer_root = Path(inputs['consumer']['output_root']); status = read(consumer_root / 'status.json')
        now = int(time.time()*1000); stamp = status.get('input_checked_at_ms', status.get('at_ms'))
        need(type(stamp) is int and 0 <= now-stamp <= 180000 and status.get('state')
             in ('READY_VALUE_LABELS_NOT_EFFICACY', 'WAITING_CURRENT100K_SETTLED_LABELS'), 'FRESH_CONSUMER_REQUIRED')
        dataset = Path(status['dataset_path'])
        need(dataset.resolve() == dataset and dataset.is_relative_to(consumer_root), 'CONSUMER_DATASET_PATH')
        data = pinned(dataset, status['dataset_sha256'])
        from historical_training.current100k_value_trial import selected_rows
        receipt, summary = prepare_ready(output, data, prep['producer_registration'], inputs['prior'],
            inputs['history'], prep['legacy_plans_sha256'], dataset, status['dataset_sha256'], now, selected_rows)
        write_status(output, {**summary, 'at_ms': now, 'consumer_input_checked_at_ms': stamp,
            'continuation_source_commit': request['source_commit'],
            'preparation_request_sha256': request['preparation_request_sha256']})
        return receipt, summary
    finally:
        os.close(lock)


def stage_fit(request, inputs, readiness):
    """Build a fresh exact closure; the old fit release remains immutable."""
    from laya_cell.stage_current100k_warm_flat import assemble_files
    from laya_cell.immutable_files import write_immutable
    template = inputs['template']; old = Path(template['runtime_directory'])
    for relative, checksum in template['runtime_sha256'].items():
        need(not Path(relative).is_absolute() and '..' not in Path(relative).parts
             and sha(old / relative) == checksum, 'FIT_TEMPLATE_RUNTIME_CHANGED')
    launcher = old / 'bin/current100k-value-launch.py'
    need('bin/current100k-value-launch.py' in template['runtime_sha256'], 'ORIGINAL_FIT_LAUNCHER_REQUIRED')
    files = assemble_files(inputs['source'], inputs['owner'], inputs['wrapper'])
    files['bin/current100k-value-launch.py'] = launcher
    inventory = {name: sha(path) for name, path in files.items()}
    release_id = digest(inventory)
    release = Path.home() / '.local/share/heptatrader/current100k-value/releases' / release_id
    for name, path in files.items():
        target = release / name; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_immutable(target, path, 0o500 if name.startswith('bin/hepta-') else 0o400,
                        'NEW_SCOPE_FIT_RELEASE_CONFLICT', inventory[name])
    write_once(release / 'manifest.json', {'schema': 'heptatrader.current100k-value-release.v1',
        'release_id': release_id, 'source_commit': request['source_commit'], 'files': inventory,
        'fit_template': request['fit_template'], 'fit_template_sha256': request['fit_template_sha256']})
    config = {**copy.deepcopy(template), 'consumer_config': inputs['prep']['consumer_config'],
        'consumer_config_sha256': inputs['prep']['consumer_config_sha256'],
        'dataset_path': readiness['dataset_path'], 'dataset_sha256': readiness['dataset_sha256'],
        'output_root': request['fit_output_root'], 'source_commit': request['source_commit'],
        'source_root': str(release), 'runtime_directory': str(release), 'runtime_sha256': inventory,
        'source_files_sha256': {str(release / name): value for name, value in inventory.items()},
        'native_binary': str(release / 'bin/hepta-trader-neuron-native'),
        'native_binary_sha256': inventory['bin/hepta-trader-neuron-native'],
        'guard_config': request['guard_config'], 'guard_config_sha256': request['guard_config_sha256']}
    folder = Path(request['controller_root']) / 'fit-stage'; folder.mkdir(mode=0o700, exist_ok=True)
    config_path = folder / (digest(config)+'.json'); write_once(config_path, config)
    command = [config['python'], '-B', str(release / 'bin/current100k-value-launch.py'),
        '--config', str(config_path), '--sha256', sha(config_path)]
    stage_path = folder / 'FIT_STAGE.json'
    if stage_path.exists():
        stage = read(stage_path)
        need(stage['config'] == str(config_path) and stage['config_sha256'] == sha(config_path)
             and stage['dataset_sha256'] == readiness['dataset_sha256'] and stage['launch_argv'] == command,
             'FIT_STAGE_CHANGED')
        return stage_path, stage, config
    result = child([*command, '--check-only'], release, 180)
    need(result.returncode == 0, 'FIT_CPU_CHECK_FAILED:' + result.stderr[-600:])
    proof = json.loads(result.stdout)
    need(proof.get('state') == 'CPU_CLOSURE_AND_ACCEPTED_HIP_INPUTS_VERIFIED'
         and proof.get('rows') == MIN_ROWS and proof.get('gpu_loaded') is False
         and proof.get('gate_admission_granted') is False and proof.get('fit_started') is False,
         'ORIGINAL_CPU_FIT_PROOF_REQUIRED')
    source_check(request)
    stage = {'schema': 'heptatrader.current100k-fit-stage.v1', 'state': 'STAGED_CPU_VERIFIED_NOT_STARTED',
        'source_commit': request['source_commit'], 'config': str(config_path), 'config_sha256': sha(config_path),
        'dataset': readiness['dataset_path'], 'dataset_sha256': readiness['dataset_sha256'],
        'producer_manifest_sha256': readiness['producer_manifest_sha256'], 'rows': MIN_ROWS,
        'fit_output_root': config['output_root'], 'release': str(release), 'release_id': release_id,
        'launch_argv': command, 'pythonpath': environment(release)['PYTHONPATH'], 'cpu_check': proof,
        'readout_profile': config['readout_profile'], 'fit_started': False, 'gpu_loaded': False,
        'guard_config_sha256': request['guard_config_sha256'], 'services_changed': [],
        'holds_changed': False, 'broker_orders_sent': 0, 'training_fit_is_not_efficacy': True}
    write_once(stage_path, stage)
    return stage_path, stage, config


def completed_candidate(config, readiness):
    from laya_cell.current100k_forward import validate_value_candidate
    output = Path(config['output_root'])
    if not (output / 'candidate.json').exists():
        return None
    candidate = validate_value_candidate(output)
    need(read(output / 'plan.json') == config and candidate['dataset_sha256'] == readiness['dataset_sha256']
         and candidate['row_ids'] == readiness['selected_row_ids'] and candidate['rows'] == MIN_ROWS
         and candidate['source_files_sha256'] == config['source_files_sha256']
         and candidate['readout_profile'] == config['readout_profile']
         and candidate['hip_runtime_manifest_sha256'] == config['hip_runtime']['sha256'],
         'COMPLETED_FIT_BINDING_CHANGED')
    return candidate


def guarded_fit(request, stage, config, readiness):
    candidate = completed_candidate(config, readiness)
    if candidate is not None:
        return candidate, 'COMPLETED_FIT_REUSED_NO_RETRAINING'
    output = Path(config['output_root'])
    need(not output.exists(), 'PARTIAL_FIT_OUTPUT_NO_AUTOMATIC_RETRAINING')
    attempts = Path(request['controller_root']) / 'fit-attempts'; attempts.mkdir(mode=0o700, exist_ok=True)
    previous = sorted(attempts.glob('*/intent.json'))
    for path in previous:
        intent = read(path); result_path = path.with_name('result.json')
        need(intent['config_sha256'] == stage['config_sha256'] and intent['output_root'] == str(output),
             'FIT_ATTEMPT_BINDING_CHANGED')
        need(result_path.exists(), 'FIT_EFFECT_UNKNOWN_NO_AUTOMATIC_RETRAINING')
        result = read(result_path)
        need(result.get('outcome') == 'GUARD_DENIED_NO_MODEL_OR_OUTPUT', 'FIT_FAILED_NO_AUTOMATIC_RETRAINING')
    attempt = attempts / f'{len(previous)+1:08d}'; attempt.mkdir(mode=0o700)
    write_once(attempt / 'intent.json', {'config_sha256': stage['config_sha256'],
        'output_root': str(output), 'argv': stage['launch_argv'], 'started_at_ms': int(time.time()*1000)})
    # A killed controller leaves an unresolved intent. Its next invocation may
    # accept a fully verified candidate, but cannot blindly launch another fit.
    result = child(stage['launch_argv'], stage['release'], request['fit_timeout_seconds'])
    denied = (result.returncode != 0 and not output.exists()
        and result.stderr.rstrip().endswith('EvidenceError: RESIDENT_ADMISSION_DENIED_NO_MODEL_LOADED'))
    record = {'returncode': result.returncode, 'stdout': result.stdout[-32768:], 'stderr': result.stderr[-32768:],
        'finished_at_ms': int(time.time()*1000),
        'outcome': 'GUARD_DENIED_NO_MODEL_OR_OUTPUT' if denied else 'FIT_PROCESS_EXITED_REQUIRES_VERIFIED_CANDIDATE'}
    write_once(attempt / 'result.json', record)
    if denied:
        return None, 'WAITING_ORIGINAL_SHARED_GUARD_NO_MODEL_LOADED'
    candidate = completed_candidate(config, readiness)
    need(result.returncode == 0 and candidate is not None, 'FIT_FAILED_NO_AUTOMATIC_RETRAINING')
    return candidate, 'GUARDED_FIT_COMPLETED'


def warm_stage(request, inputs, fit_path, candidate, readiness):
    root = Path(request['controller_root']); output = root / 'warm-stage'; path = output / 'WARM_STAGE.json'
    if not path.exists():
        command = ['/usr/bin/python3', '-B', str(inputs['source'] / 'research/neuron_learning/laya_cell/stage_current100k_warm_flat.py'),
            '--repo', str(inputs['source']), '--expected-head', request['source_commit'],
            '--fit-stage', str(fit_path), '--fit-stage-sha256', sha(fit_path),
            '--prior-forward-stage', request['prior_forward_stage'],
            '--prior-forward-stage-sha256', request['prior_forward_stage_sha256'],
            '--guard-config', request['guard_config'], '--guard-config-sha256', request['guard_config_sha256'],
            '--candidate-id', candidate['candidate_id'], '--dataset-sha256', readiness['dataset_sha256'],
            '--forward-root', request['forward_root'], '--evidence-root', str(output),
            '--registration-scope', 'NEW_PRODUCER_SCOPE', '--resume-staging',
            '--worker-profile', worker_profile(request)]
        for legacy in inputs['prep']['legacy_plans_sha256']:
            command += ['--legacy-plan', legacy]
        result = child(command, inputs['source'], 600, layout=('research', 'research/neuron_learning', 'scripts'))
        need(result.returncode == 0 and path.exists(), 'WARM_STAGE_FAILED:' + result.stderr[-600:])
    stage = read(path)
    need(stage.get('worker_profile', 'warm-flat-v1') == worker_profile(request), 'WARM_STAGE_WORKER_PROFILE_CHANGED')
    need(stage['candidate_id'] == candidate['candidate_id'] and stage['dataset_sha256'] == readiness['dataset_sha256']
         and stage['expected_trial'] == readiness['expected_trial'] and stage['registration_scope'] == 'NEW_PRODUCER_SCOPE'
         and stage['forward_root'] == request['forward_root'] and stage['source_commit'] == request['source_commit']
         and stage['fit_stage_sha256'] == sha(fit_path), 'WARM_STAGE_BINDING_CHANGED')
    write_once(root / 'WARM_STAGE_BOUND.json', {'path': str(path), 'sha256': sha(path)})
    return path, stage


def observed_selection(deadline_ms):
    from current100k_priority_activation import SHARED_UNITS, SHARED_PROPERTIES, _command, selection_request
    snapshot = {'units': {}, 'files': {}}; wrappers = {}
    for name in SHARED_UNITS.values():
        result = subprocess.run(['systemctl', '--user', 'show', name, '--property='+','.join(SHARED_PROPERTIES)],
            capture_output=True, text=True, timeout=20, check=False)
        need(result.returncode == 0, 'SHARED_UNIT_UNAVAILABLE')
        props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        snapshot['units'][name] = {'rc': 0, 'properties': props}
        for spelling in [props['FragmentPath'], *shlex.split(props['DropInPaths'])]:
            path = Path(spelling); text = path.read_text()
            snapshot['files'][spelling] = {'text': text, 'sha256': sha(path), 'mode': oct(path.stat().st_mode & 0o777)}
        spelling = _command(props['ExecStart'])[5]; path = Path(spelling)
        wrappers[spelling] = {'text': path.read_text(), 'sha256': sha(path)}
    return selection_request(snapshot, wrappers, activation_deadline_ms=deadline_ms)


def install_units(units):
    from laya_cell.immutable_files import write_immutable
    directory = Path.home() / '.config/systemd/user'; directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, text in units.items():
        need(re.fullmatch(r'heptatrader-current100k-[-a-z0-9]+\.(service|timer)', name), 'UNIT_NAME')
        write_immutable(directory / name, text.encode(), 0o600, 'NEW_SCOPE_CONTINUATION_UNIT_CONFLICT')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, capture_output=True, text=True, timeout=30)
    targets = [name for name in units if name.endswith('.timer')] or list(units)
    subprocess.run(['systemctl', '--user', 'enable', '--now', *targets], check=True,
        capture_output=True, text=True, timeout=45)
    for name in targets:
        result = subprocess.run(['systemctl', '--user', 'is-active', name], capture_output=True, text=True, timeout=15)
        need(result.returncode == 0 and result.stdout.strip() == 'active', 'INSTALLED_UNIT_NOT_ACTIVE:' + name)


def plan_observer(request, stage, activation):
    """Attach a separate reader to the actual new plan, retaining first-request timing."""
    root = Path(request['controller_root']); output = root / 'request-observer'
    info = activation['configs']['predictor']; config = pinned(info['path'], info['sha256'])
    need(config['forward_root'] == stage['forward_root'] and config['worker_source_root'] == stage['release'],
         'OBSERVER_NEW_PLAN_CONFIG_REQUIRED')
    script = Path(request['source_repository']) / 'scripts/observe_current100k_requests.py'
    name = 'heptatrader-current100k-request-observer-trial' + str(stage['expected_trial']) + '.service'
    command = ['/usr/bin/python3', '-B', str(script), '--config', info['path'],
        '--config-sha256', info['sha256'], '--output-directory', str(output), '--poll-ms', '500']
    need(all(re.fullmatch(r'[-A-Za-z0-9_./:]+', part) for part in command), 'OBSERVER_ARGUMENT_REQUIRES_ESCAPING')
    unit = '\n'.join(('[Unit]', 'Description=HeptaTrader same-plan Current100k request timing observer',
        '', '[Service]', 'Type=simple', 'ExecStart=' + ' '.join(command),
        'WorkingDirectory=' + str(Path(request['source_repository'])), 'Environment=PYTHONDONTWRITEBYTECODE=1',
        'Environment=OPENBLAS_NUM_THREADS=1', 'UMask=0077', 'Nice=15', 'CPUQuota=25%', 'CPUWeight=10',
        'MemoryMax=512M', 'TasksMax=32', 'NoNewPrivileges=yes', 'RestrictSUIDSGID=yes',
        'RestrictAddressFamilies=AF_UNIX', 'Restart=on-failure', 'RestartSec=30',
        'TimeoutStopSec=20', 'KillMode=control-group', '', '[Install]', 'WantedBy=default.target', ''))
    receipt = {'service': name, 'unit_sha256': hashlib.sha256(unit.encode()).hexdigest(),
        'source_commit': request['source_commit'], 'script_sha256': sha(script),
        'config': info['path'], 'config_sha256': info['sha256'], 'output_directory': str(output),
        'plan_id': activation['plan_id'], 'prediction_or_admission_authority': False}
    installed = root / 'OBSERVER_INSTALLED.json'
    if installed.exists():
        need(read(installed) == receipt, 'OBSERVER_BINDING_CHANGED')
    else:
        install_units({name: unit}); write_once(installed, receipt)
    try:
        observed = read(output / 'status.json')
    except FileNotFoundError:
        return receipt, False
    need(observed.get('plan_id') == activation['plan_id']
         and observed.get('producer_registration') == config['producer_registration']
         and observed.get('prediction_worker_launched') is False and observed.get('gpu_loaded') is False,
         'OBSERVER_SCOPE_CHANGED')
    fresh = type(observed.get('at_ms')) is int and 0 <= int(time.time()*1000)-observed['at_ms'] <= 90000
    return receipt, fresh and observed.get('state') == 'OBSERVING_ORIGINAL_TRIAL'


def handoff(request, stage_path, stage):
    profile = worker_profile(request)
    need(stage.get('worker_profile', 'warm-flat-v1') == profile, 'WARM_STAGE_WORKER_PROFILE_CHANGED')
    root = Path(request['controller_root']); selection_path = root / 'SELECTION_REQUEST.json'
    registration = pinned(stage['registration_request'], stage['registration_request_sha256'])
    first_ms = ((registration['registration_not_before_ms']+999)//1000)*1000
    deadline = first_ms + (request['activation_events']-1)*request['activation_readmission_seconds']*1000 + 1000
    if not selection_path.exists():
        write_once(selection_path, observed_selection(deadline))
    selection = read(selection_path)
    need(selection['activation_deadline_ms'] == deadline, 'ACTIVATION_DEADLINE_CHANGED_NO_RENEWAL')
    wake_root = root / 'registration-wake'; wake_path = wake_root / 'WAKE_STAGE.json'
    if not wake_path.exists():
        need(int(time.time()*1000) < deadline, 'ACTIVATION_DEADLINE_EXPIRED_NO_RENEWAL')
        command = ['/usr/bin/python3', '-B', str(Path(stage['release']) / 'scripts/precheck_current100k_registration.py'),
            '--stage', str(stage_path), '--stage-sha256', sha(stage_path), '--deadline-ms', str(deadline),
            '--mode', 'register', '--readmission-seconds', str(request['activation_readmission_seconds']),
            '--selection-request', str(selection_path), '--selection-request-sha256', sha(selection_path),
            '--output', str(wake_root), '--resume-staging']
        result = child(command, stage['release'], 180)
        need(result.returncode == 0 and wake_path.exists(), 'WAKE_PREPARATION_FAILED:' + result.stderr[-600:])
    wake = read(wake_path)
    need(wake['stage_sha256'] == sha(stage_path) and wake['selection_request_sha256'] == sha(selection_path)
         and wake['deadline_ms'] == deadline and wake['calendar_events'] == request['activation_events']
         and wake['mode'] == 'register', 'WAKE_BINDING_CHANGED')
    installed = root / 'WAKE_INSTALLED.json'
    units = {wake['service_name']: wake['service'], wake['timer_name']: wake['timer']}
    installation = {'wake_stage': str(wake_path), 'wake_stage_sha256': sha(wake_path),
        'deadline_ms': deadline, 'unit_sha256': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in units.items()}}
    if not installed.exists():
        need(int(time.time()*1000) < deadline, 'ACTIVATION_DEADLINE_EXPIRED_NO_RENEWAL')
        install_units(units)
        write_once(installed, installation)
    else:
        need(read(installed) == installation, 'WAKE_INSTALLATION_BINDING_CHANGED')
    observed = Path(request['forward_root']) / 'activation-status.json'
    if observed.exists():
        status = read(observed)
        if status.get('state') == 'SAME_PLAN_NATIVE_WARM_EXECUTION_SCHEDULED':
            result = child(['/usr/bin/python3', '-B',
                str(Path(stage['release']) / 'scripts/activate_current100k_warm_stage.py'),
                '--stage', str(stage_path), '--stage-sha256', sha(stage_path), '--verify-native-execution'],
                stage['release'], 180)
            need(result.returncode == 0, 'ACTIVATED_PLAN_VERIFICATION_FAILED:' + result.stderr[-600:])
            proof = json.loads(result.stdout)
            if proof['state'] == 'REGISTERED_INPUT_WINDOW_ELAPSED_NO_LIVE_ACCEPTANCE':
                return proof
            need(proof['state'] == 'REGISTERED_NATIVE_EXECUTION_LIVE_VERIFIED'
                 and proof['plan_id'] == status['plan_id'] and proof['trial'] == stage['expected_trial'],
                 'ACTIVATED_PLAN_IDENTITY_CHANGED')
            observer, healthy = plan_observer(request, stage, status)
            if not healthy:
                return {'state': 'REGISTERED_AND_SCHEDULED_WAITING_READ_ONLY_OBSERVER',
                    'plan_id': proof['plan_id'], 'observer': observer, 'current100k_p0_closed': False}
            complete = {'state': 'VERIFIED_NEW_SCOPE_NATIVE_WARM_EXECUTION_SCHEDULED',
                'plan_id': proof['plan_id'], 'trial': proof['trial'], 'plan_path': status['plan_path'],
                'plan_sha256': sha(status['plan_path']), 'activation_before_input_start': status['activation_before_input_start'],
                'source_commit': request['source_commit'], 'preparation_source_commit': request['preparation_source_commit'],
                'old_trials_modified': False, 'training_fit_is_not_efficacy': True, 'broker_orders_sent': 0,
                'observer': observer, 'current100k_p0_closed': False,
                'live_execution_verified_at_ms': proof['at_ms'],
                'role_configs_verified': proof['role_configs_verified'],
                'timers_enabled_active_and_next_event_verified': proof['timers_enabled_active_and_next_event_verified'],
                'same_request_preopen_prediction_chain_verified': False,
                'worker_profile': profile,
                'worker_profile_evidence': request.get('worker_profile_evidence'),
                'activation_before_predictor_start': status.get('activation_before_predictor_start'),
                'first_flat_request_still_requires_cold_model_start': True if profile == 'warm-flat-v1' else None,
                'cpu_standby_observed_before_first_request': None,
                'required_empirical_validation': 'SAME_IDENTITY_REQUEST_START_LE_COMPUTED_LE_COMMITTED_LT_MIN_OPEN_FIRST_FILL',
                'remaining_timing_risk': ('FIRST_FLAT_REQUEST_COLD_START_MAY_MISS_ORIGINAL_OPEN'
                    if profile == 'warm-flat-v1' else 'CPU_STANDBY_AND_FRESH_ADMISSION_REQUIRE_ORIGINAL_REQUEST_OBSERVATION')}
            write_once(root / 'CONTINUATION_COMPLETE.json', complete)
            return complete
    return {'state': 'WAITING_NORMAL_FINITE_REGISTRATION_AND_ACTIVATION'
        if int(time.time()*1000) < deadline else 'ACTIVATION_DEADLINE_EXPIRED_PINNED_STAGE_RETAINED',
        'wake_stage': str(wake_path), 'timer': wake['timer_name'], 'deadline_ms': deadline,
        'stage': str(stage_path), 'candidate_id': stage['candidate_id'], 'trial_registered': None}


def service_units(request_path, request_sha, request):
    source = Path(request['source_repository']); root = Path(request['controller_root'])
    values = [str(source), str(root), str(request_path), request_sha]
    need(all(re.fullmatch(r'[-A-Za-z0-9_./:]+', value) for value in values), 'SYSTEMD_ARGUMENT_REQUIRES_ESCAPING')
    name = 'heptatrader-current100k-newscope-' + request_sha[:12]
    service = '\n'.join(('[Unit]', 'Description=HeptaTrader pinned Current100k new producer continuation',
        'ConditionPathExists=!' + str(root / 'CONTINUATION_COMPLETE.json'), '', '[Service]', 'Type=oneshot',
        'ExecStart=/usr/bin/python3 -B ' + str(source / 'scripts/continue_current100k_new_scope.py')
        + ' --request ' + str(request_path) + ' --sha256 ' + request_sha,
        'WorkingDirectory=' + str(source), 'Environment=PYTHONPATH=' + ':'.join(str(source / p)
            for p in ('research', 'research/neuron_learning', 'scripts')),
        'Environment=PYTHONDONTWRITEBYTECODE=1', 'Environment=OPENBLAS_NUM_THREADS=1', 'Environment=OMP_NUM_THREADS=2',
        'Environment=HF_HUB_OFFLINE=1', 'Environment=TRANSFORMERS_OFFLINE=1', 'Environment=USE_TF=0',
        'UMask=0077', 'Nice=15', 'CPUQuota=150%', 'CPUWeight=10', 'MemoryHigh=6G', 'MemoryMax=6G', 'TasksMax=96',
        'NoNewPrivileges=yes', 'RestrictSUIDSGID=yes', 'RestrictAddressFamilies=AF_UNIX', 'LockPersonality=yes',
        'Restart=no', 'TimeoutStartSec=2100', 'TimeoutStopSec=20', 'KillMode=control-group', 'SendSIGKILL=yes', ''))
    timer = '\n'.join(('[Unit]', 'Description=HeptaTrader persistent Current100k settled-row continuation',
        '', '[Timer]', 'OnCalendar=*-*-* *:*:00', 'AccuracySec=1s', 'RandomizedDelaySec=0', 'Persistent=true',
        'Unit=' + name + '.service', '', '[Install]', 'WantedBy=timers.target', ''))
    return {name+'.service': service, name+'.timer': timer}


def predecessor_no_effects(previous):
    """A profile handoff is allowed only before the old executor staged a fit."""
    old = previous['request']; root = previous['root']
    need(sha(previous['path']) == previous['sha256'], 'PREDECESSOR_REQUEST_CHANGED')
    allowed = {'CONTINUATION_REQUEST.json', 'continuation.lock', 'status.json', 'status.json.next'}
    need(root.is_dir() and {p.name for p in root.iterdir()} <= allowed,
         'PREDECESSOR_HAS_STAGING_OR_UNKNOWN_EFFECTS')
    need(all(not Path(old[key]).exists() and not Path(old[key]).is_symlink()
             for key in ('fit_output_root', 'forward_root')), 'PREDECESSOR_HAS_FIT_OR_FORWARD_OUTPUT')


def native_continuation_unit(name):
    # The warm-role inspector intentionally accepts only predictor/evaluator
    # names. Continuation inspection has its own narrow, request-derived scope.
    need(re.fullmatch(r'heptatrader-current100k-newscope-[0-9a-f]{12}\.(service|timer)', name),
         'CONTINUATION_UNIT_NAME_SCOPE')
    keys = ('LoadState', 'ActiveState', 'SubState', 'FragmentPath', 'DropInPaths',
            'NeedDaemonReload', 'ExecStart', 'UnitFileState', 'MainPID', 'NextElapseUSecRealtime',
            'WorkingDirectory', 'Environment', 'Type')
    result = subprocess.run(['systemctl', '--user', 'show', name, '--no-pager',
        '--property=' + ','.join(keys)], stdin=subprocess.DEVNULL,
        text=True, capture_output=True, timeout=15)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    need(result.returncode in (0, 1) and values.get('LoadState') in ('loaded', 'not-found'),
         'CONTINUATION_UNIT_INSPECTION_FAILED')
    return values


def verified_continuation_units(expected, *, timer_state=None):
    from activate_current100k_warm_stage import owned_bytes
    observed = {}
    for name, text in expected.items():
        path = Path.home() / '.config/systemd/user' / name
        need(owned_bytes(path) == text.encode(), 'CONTINUATION_UNIT_BYTES_CHANGED:' + name)
        value = native_continuation_unit(name)
        need(value.get('LoadState') == 'loaded' and value.get('FragmentPath') == str(path)
             and not value.get('DropInPaths') and value.get('NeedDaemonReload') == 'no',
             'CONTINUATION_LOADED_UNIT_CHANGED:' + name)
        if name.endswith('.service'):
            command = next(line.removeprefix('ExecStart=') for line in text.splitlines() if line.startswith('ExecStart='))
            directory = next(line.removeprefix('WorkingDirectory=') for line in text.splitlines()
                             if line.startswith('WorkingDirectory='))
            environment = [line.removeprefix('Environment=') for line in text.splitlines()
                           if line.startswith('Environment=')]
            need('argv[]=' + command + ' ;' in value.get('ExecStart', '')
                 and value.get('WorkingDirectory') == directory and value.get('Type') == 'oneshot'
                 and sorted(shlex.split(value.get('Environment', ''))) == sorted(environment),
                 'CONTINUATION_LOADED_EXECUTOR_CHANGED')
        elif timer_state == 'retired':
            need(value.get('ActiveState') == 'inactive' and value.get('UnitFileState') == 'disabled',
                 'PREDECESSOR_TIMER_NOT_RETIRED')
        elif timer_state == 'waiting':
            need(value.get('ActiveState') == 'active' and value.get('SubState') == 'waiting'
                 and value.get('UnitFileState') == 'enabled'
                 and value.get('NextElapseUSecRealtime') not in (None, '', '0', 'infinity'),
                 'CONTINUATION_TIMER_NOT_WAITING')
        observed[name] = value
    return observed


def predecessor_units(previous, *, retired=False):
    expected = service_units(Path(previous['path']), previous['sha256'], previous['request'])
    return verified_continuation_units(expected, timer_state='retired' if retired else None)


def predecessor_idle(observed):
    return all(value.get('MainPID', '0') == '0' and value.get('ActiveState') in ('inactive', 'failed')
               for name, value in observed.items() if name.endswith('.service'))


def supersession_binding(request):
    return {'schema': 'heptatrader.current100k-continuation-supersession.v1',
        'request_sha256': sha(Path(request['controller_root']) / 'CONTINUATION_REQUEST.json'),
        'supersedes_continuation': request['supersedes_continuation'],
        'preparation_request': request['preparation_request'],
        'preparation_request_sha256': request['preparation_request_sha256'],
        'worker_profile': worker_profile(request), 'worker_profile_evidence': request.get('worker_profile_evidence')}


@contextmanager
def predecessor_lock(request, inputs, *, require_installed):
    previous = inputs.get('predecessor')
    if previous is None:
        yield None
        return
    fd = os.open(previous['root'] / 'continuation.lock',
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            need(False, 'PREDECESSOR_BUSY_NO_FORCE_STOP')
        predecessor_no_effects(previous)
        if require_installed:
            marker = Path(request['controller_root']) / 'PREDECESSOR_HANDOFF.json'
            need(marker.is_file(), 'PREDECESSOR_HANDOFF_NOT_COMPLETE')
            proof = read(marker)
            need(proof.get('binding') == supersession_binding(request), 'PREDECESSOR_HANDOFF_BINDING_CHANGED')
            for path, checksum in proof['preserved_cohort_sha256'].items():
                need(sha(path) == checksum, 'PREDECESSOR_FROZEN_COHORT_CHANGED')
            need(predecessor_idle(predecessor_units(previous, retired=True)), 'PREDECESSOR_STILL_RUNNING')
        # Retain the old mutex through the whole new step, including training.
        # An accidentally re-enabled old timer cannot execute in parallel.
        yield previous
    finally:
        os.close(fd)


def install_continuation(request_path, request_sha, request, inputs):
    units = service_units(request_path, request_sha, request)
    if inputs.get('predecessor') is None:
        install_units(units)
        verified_continuation_units(units, timer_state='waiting')
        return None
    from laya_cell.stage_current100k_warm_flat import stage_json
    root = Path(request['controller_root']); binding = supersession_binding(request)
    with predecessor_lock(request, inputs, require_installed=False) as previous:
        prep_fd = os.open(inputs['prep_path'].parent / 'preparation.lock',
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(prep_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                need(False, 'PREPARATION_BUSY_NO_TIMER_HANDOFF')
            predecessor_units(previous)
            need(sha(inputs['prep_path']) == request['preparation_request_sha256'], 'ORIGINAL_PREPARATION_CHANGED')
            cohort = {str(path): sha(path) for name in ('FROZEN_NEW_PRODUCER_32.json', 'READY_FOR_GUARDED_FIT.json')
                      if (path := inputs['prep_path'].parent / name).exists()}
            stage_json(root / 'PREDECESSOR_HANDOFF_INTENT.json', binding)
            timer = next(name for name in service_units(Path(previous['path']), previous['sha256'], previous['request'])
                         if name.endswith('.timer'))
            # Stop only the exact old timer. Never kill a running old service.
            subprocess.run(['systemctl', '--user', 'disable', '--now', timer], check=True,
                capture_output=True, text=True, timeout=30)
            deadline = time.monotonic() + 15
            while True:
                old_units = predecessor_units(previous, retired=True)
                if predecessor_idle(old_units):
                    break
                need(time.monotonic() < deadline, 'PREDECESSOR_DID_NOT_EXIT_NORMALLY_NO_FORCE_STOP')
                time.sleep(.1)
            predecessor_no_effects(previous)
            marker = root / 'PREDECESSOR_HANDOFF.json'
            if marker.exists():
                old_proof = read(marker)
                need(old_proof.get('binding') == binding, 'PREDECESSOR_HANDOFF_BINDING_CHANGED')
                for path, checksum in old_proof['preserved_cohort_sha256'].items():
                    need(sha(path) == checksum, 'PREDECESSOR_FROZEN_COHORT_CHANGED')
            install_units(units)
            loaded = verified_continuation_units(units, timer_state='waiting')
            need(sha(inputs['prep_path']) == request['preparation_request_sha256']
                 and all(sha(path) == checksum for path, checksum in cohort.items()), 'PREPARATION_OR_COHORT_CHANGED_DURING_HANDOFF')
            if marker.exists():
                return read(marker)
            result = {'state': 'PREDECESSOR_TIMER_RETIRED_NEW_TIMER_VERIFIED', 'binding': binding,
                'at_ms': int(time.time()*1000), 'preserved_cohort_sha256': cohort,
                'predecessor_units': old_units, 'new_units': loaded,
                'old_request_modified': False, 'training_started_by_installer': False,
                'current100k_p0_closed': False, 'same_request_preopen_prediction_chain_verified': False,
                'broker_orders_sent': 0}
            stage_json(marker, result)
            return result
        finally:
            os.close(prep_fd)


def step(request, inputs):
    root = Path(request['controller_root'])
    if (root / 'CONTINUATION_COMPLETE.json').exists():
        return read(root / 'CONTINUATION_COMPLETE.json')
    warm_path = root / 'warm-stage/WARM_STAGE.json'
    if (root / 'WARM_STAGE_BOUND.json').exists():
        bound = read(root / 'WARM_STAGE_BOUND.json')
        need(bound['path'] == str(warm_path), 'WARM_STAGE_PATH_CHANGED')
        return handoff(request, warm_path, pinned(warm_path, bound['sha256']))
    receipt, summary = ready(request, inputs)
    if receipt is None:
        return summary
    fit_path, fit, config = stage_fit(request, inputs, receipt)
    candidate, state = guarded_fit(request, fit, config, receipt)
    if candidate is None:
        return {'state': state, 'dataset_sha256': receipt['dataset_sha256'], 'rows': MIN_ROWS}
    warm_path, stage = warm_stage(request, inputs, fit_path, candidate, receipt)
    return handoff(request, warm_path, stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True); parser.add_argument('--sha256', required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check-only', action='store_true'); modes.add_argument('--install', action='store_true')
    args = parser.parse_args(); os.umask(0o077); sys.dont_write_bytecode = True
    need(os.geteuid() != 0, 'ORIGINAL_UNPRIVILEGED_USER_REQUIRED')
    source = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(source / part) for part in ('research', 'research/neuron_learning', 'scripts')]
    request = pinned(args.request, args.sha256); inputs = load_inputs(request)
    need(args.request == Path(request['controller_root']) / 'CONTINUATION_REQUEST.json', 'CANONICAL_REQUEST_PATH_REQUIRED')
    if args.check_only:
        print(json.dumps({'state': 'CONTINUATION_INPUTS_VERIFIED_NO_TRAINING_OR_REGISTRATION',
            'source_commit': request['source_commit'], 'preparation_source_commit': request['preparation_source_commit'],
            'minimum_rows': MIN_ROWS, 'expected_trial': inputs['prep']['expected_trial'], 'gpu_loaded': False,
            'worker_profile': worker_profile(request), 'worker_profile_evidence': request.get('worker_profile_evidence'),
            'predecessor_handoff_required': inputs.get('predecessor') is not None,
            'broker_orders_sent': 0, 'unit_candidates': service_units(args.request, args.sha256, request)}, sort_keys=True))
        return
    root = Path(request['controller_root']); root.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_once(root / 'CONTINUATION_REQUEST.json', request)
    if args.install:
        units = service_units(args.request, args.sha256, request)
        supersession = install_continuation(args.request, args.sha256, request, inputs)
        print(json.dumps({'state': 'PERSISTENT_NEW_SCOPE_CONTINUATION_INSTALLED',
            'request_sha256': args.sha256, 'units': list(units), 'training_started_by_installer': False,
            'worker_profile': worker_profile(request), 'predecessor_handoff': supersession,
            'broker_orders_sent': 0}, sort_keys=True)); return
    lock = os.open(root / 'continuation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with predecessor_lock(request, inputs, require_installed=True):
                summary = step(request, inputs)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            summary = {'state': 'NEW_SCOPE_CONTINUATION_BLOCKED', 'reason': str(error)[-1000:],
                'automatic_retraining_after_unknown_effect': False}
        summary = {**summary, 'at_ms': int(time.time()*1000), 'source_commit': request['source_commit'],
            'worker_profile': worker_profile(request),
            'preparation_source_commit': request['preparation_source_commit'],
            'preparation_request_sha256': request['preparation_request_sha256'], 'broker_orders_sent': 0}
        write_status(root, summary); print(json.dumps(summary, sort_keys=True), flush=True)
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
