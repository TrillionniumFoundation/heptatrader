#!/usr/bin/env python3
"""Pin a detached executor and stage/install one Current100k continuation timer.

All source and original preparation inputs are explicit. A separately pinned
pre-fit predecessor may be retired by --install; only its exact timer is
disabled after both original mutexes are held. A running service is never
killed. No trading unit, producer, preparation request, or cohort is changed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from prepare_current100k_new_scope import write_once


def need(ok, reason):
    if not ok:
        raise ValueError('NEW_SCOPE_CONTINUATION_STAGE_' + reason)


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True, timeout=30).strip()


def exact_source(source, expected):
    need(source.is_absolute() and source.resolve() == source and git(source, 'rev-parse', 'HEAD') == expected
         and not git(source, 'status', '--porcelain', '--untracked-files=all'), 'EXACT_CLEAN_SOURCE_REQUIRED')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'source-directory', 'preparation-request', 'fit-template', 'prior-forward-stage',
                 'guard-config', 'controller-root', 'fit-output-root', 'forward-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('expected-head', 'preparation-request-sha256', 'fit-template-sha256',
                 'prior-forward-stage-sha256', 'guard-config-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--fit-timeout-seconds', type=int, default=600)
    # The original producer has an observed 120-second cadence. A 120-second
    # registration timer could repeatedly sample only its stale half-cycle.
    # A 61-second cadence explores both phases without inventing a request.
    parser.add_argument('--activation-readmission-seconds', type=int, default=61)
    parser.add_argument('--activation-events', type=int, default=120)
    parser.add_argument('--worker-profile', choices=('warm-flat-v1', 'cpu-standby-v2'), default='warm-flat-v1')
    for name in ('report', 'lifecycle'):
        parser.add_argument('--worker-profile-evidence-' + name, type=Path)
        parser.add_argument('--worker-profile-evidence-' + name + '-sha256')
    parser.add_argument('--supersedes-request', type=Path)
    parser.add_argument('--supersedes-request-sha256')
    parser.add_argument('--background-guards', type=Path)
    parser.add_argument('--background-guards-sha256')
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args(); os.umask(0o077); sys.dont_write_bytecode = True
    need(os.geteuid() != 0, 'ORIGINAL_UNPRIVILEGED_USER_REQUIRED')
    source = Path(__file__).resolve().parent.parent
    need(source == args.repo, 'STAGER_SELECTED_SOURCE_REQUIRED'); exact_source(source, args.expected_head)
    sys.path[:0] = [str(source / part) for part in ('research', 'research/neuron_learning', 'scripts')]
    from continue_current100k_new_scope import SCHEMA, pinned, predecessor_inputs, hardware_evidence
    from laya_cell.stage_current100k_warm_flat import checked
    prep = pinned(args.preparation_request, args.preparation_request_sha256)
    template = pinned(args.fit_template, args.fit_template_sha256)
    pinned(args.prior_forward_stage, args.prior_forward_stage_sha256)
    pinned(args.guard_config, args.guard_config_sha256)
    consumer = pinned(prep['consumer_config'], prep['consumer_config_sha256'])
    outputs = [args.source_directory, args.controller_root, args.fit_output_root, args.forward_root]
    protected = [source, args.preparation_request.parent, Path(template['output_root']),
        Path(consumer['output_root']), *{Path(path).parent.parent for path in prep['legacy_plans_sha256']}]
    need(len(set(outputs)) == len(outputs), 'DISTINCT_OUTPUT_ROOTS_REQUIRED')
    for output in outputs:
        need(output.is_absolute() and output.resolve() == output
             and all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in protected)
             and all(output == other or not output.is_relative_to(other) for other in outputs),
             'SEPARATE_OUTPUT_ROOTS_REQUIRED')
    request = {'schema': SCHEMA, 'source_repository': str(args.source_directory), 'source_commit': args.expected_head,
        'worker_profile': args.worker_profile,
        'preparation_request': str(args.preparation_request), 'preparation_request_sha256': args.preparation_request_sha256,
        'preparation_source_commit': prep['source_commit'],
        'fit_template': str(args.fit_template), 'fit_template_sha256': args.fit_template_sha256,
        'prior_forward_stage': str(args.prior_forward_stage), 'prior_forward_stage_sha256': args.prior_forward_stage_sha256,
        'guard_config': str(args.guard_config), 'guard_config_sha256': args.guard_config_sha256,
        'controller_root': str(args.controller_root), 'fit_output_root': str(args.fit_output_root),
        'forward_root': str(args.forward_root), 'fit_timeout_seconds': args.fit_timeout_seconds,
        'activation_readmission_seconds': args.activation_readmission_seconds, 'activation_events': args.activation_events,
        'automatic_training': True, 'automatic_normal_registration_activation': True,
        'trade_authority': False, 'old_trials_modified': False}
    evidence = {}
    for name in ('report', 'lifecycle'):
        path = getattr(args, 'worker_profile_evidence_' + name)
        checksum = getattr(args, 'worker_profile_evidence_' + name + '_sha256')
        need((path is None) == (checksum is None), 'HARDWARE_EVIDENCE_PATH_AND_HASH_REQUIRED')
        if path is not None:
            evidence[name] = {'path': str(path), 'sha256': checksum}
    need((args.supersedes_request is None) == (args.supersedes_request_sha256 is None),
         'PREDECESSOR_PATH_AND_HASH_REQUIRED')
    if evidence:
        request['worker_profile_evidence'] = evidence
    if args.supersedes_request is not None:
        request['supersedes_continuation'] = {'request': str(args.supersedes_request),
            'sha256': args.supersedes_request_sha256}
    need((args.background_guards is None) == (args.background_guards_sha256 is None),
         'BACKGROUND_GUARDS_PATH_AND_HASH_REQUIRED')
    if args.background_guards is not None:
        need(args.supersedes_request is None and args.worker_profile == 'cpu-standby-v2',
             'BACKGROUND_APPEND_REQUIRES_INDEPENDENT_V2_SCOPE')
        request['background_guards'] = {'path': str(args.background_guards),
            'sha256': args.background_guards_sha256}
    predecessor_inputs(request)
    prior_path, prior_sha = next((path, value) for path, value in prep['legacy_plans_sha256'].items()
                                if Path(path).parent.name == prep['prior_plan_id'])
    hardware_evidence(request, pinned(prior_path, prior_sha), prior_sha, source_root=source)
    if args.source_directory.exists():
        exact_source(args.source_directory, args.expected_head)
    else:
        subprocess.run(['git', '-C', str(source), 'worktree', 'add', '--detach', str(args.source_directory), args.expected_head],
            check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        subprocess.run(['git', '-C', str(source), 'worktree', 'lock', '--reason',
            'Pinned Current100k new-producer continuation executor', str(args.source_directory)],
            check=True, capture_output=True, text=True, timeout=30)
    exact_source(args.source_directory, args.expected_head)
    need(hashlib.sha256(checked(args.source_directory / 'scripts/continue_current100k_new_scope.py'))
         .hexdigest() == hashlib.sha256(checked(source / 'scripts/continue_current100k_new_scope.py')).hexdigest(),
         'DETACHED_EXECUTOR_CHANGED')
    request_path = args.controller_root / 'CONTINUATION_REQUEST.json'
    need(not args.controller_root.exists() or request_path.exists() or not any(args.controller_root.iterdir()),
         'NEW_OR_SAME_CONTROLLER_ROOT_REQUIRED')
    args.controller_root.mkdir(mode=0o700, parents=True, exist_ok=True); write_once(request_path, request)
    request_sha = hashlib.sha256(checked(request_path)).hexdigest()
    command = ['/usr/bin/python3', '-B', str(args.source_directory / 'scripts/continue_current100k_new_scope.py'),
        '--request', str(request_path), '--sha256', request_sha]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=':'.join(
        str(args.source_directory / part) for part in ('research', 'research/neuron_learning', 'scripts')))
    check = subprocess.run([*command, '--check-only'], cwd=args.source_directory, env=environment,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=240, check=False)
    need(check.returncode == 0, 'INPUT_CHECK_FAILED:' + check.stderr[-1000:])
    proof = json.loads(check.stdout)
    need(proof['state'] == 'CONTINUATION_INPUTS_VERIFIED_NO_TRAINING_OR_REGISTRATION'
         and proof['gpu_loaded'] is False and proof['worker_profile'] == args.worker_profile,
         'CPU_ONLY_CHECK_REQUIRED')
    installation = None
    if args.install:
        result = subprocess.run([*command, '--install'], cwd=args.source_directory, env=environment,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=240, check=False)
        need(result.returncode == 0, 'INSTALL_RESULT_UNKNOWN_INSPECT_SAME_UNITS:' + result.stderr[-1000:])
        installation = json.loads(result.stdout)
    print(json.dumps({'state': 'PERSISTENT_NEW_SCOPE_CONTINUATION_INSTALLED' if installation else 'CONTINUATION_STAGED_NOT_INSTALLED',
        'request': str(request_path), 'request_sha256': request_sha, 'source_commit': args.expected_head,
        'worker_profile': args.worker_profile, 'worker_profile_evidence': request.get('worker_profile_evidence'),
        'background_guards': request.get('background_guards'),
        'preparation_request_unchanged': hashlib.sha256(checked(args.preparation_request)).hexdigest()
            == args.preparation_request_sha256,
        'explicit_argv': command, 'cpu_check': proof, 'installation': installation,
        'current100k_p0_closed': False, 'broker_orders_sent': 0}, sort_keys=True))


if __name__ == '__main__':
    main()
