#!/usr/bin/env python3
"""Prepare a new producer's separate consumer config for the normal installer.

The old config, runtime, materialized episodes, model policy and trial are kept.
Only registration and the producer-named output scope change in the base config.
This command never installs/starts services; its receipt provides the existing
read-only consumer install command for the operator's separately verified step.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def new_scope_config(previous, registration, old_manifest, new_manifest):
    old_registration = previous['registration']
    if (previous.get('schema') != 'heptatrader.current100k-consumer-config.v1'
            or previous.get('automatic_training') is not False
            or previous.get('automatic_trading') is not False
            or registration['sha256'] == old_registration['sha256']
            or new_manifest.get('prepared_from_manifest_sha256') != old_registration['sha256']):
        raise ValueError('CONSUMER_SCOPE_CHANGED_PRODUCER_WITH_ORIGINAL_LINEAGE_REQUIRED')
    for field in ('schema', 'execution_domain', 'instrument', 'period_sha256', 'behavior_profile'):
        if old_manifest.get(field) != new_manifest.get(field):
            raise ValueError('CONSUMER_SCOPE_ORIGINAL_BEHAVIOR_CHANGED:' + field)
    if new_manifest['period_sha256'] != previous['period_sha256']:
        raise ValueError('CONSUMER_SCOPE_ORIGINAL_PERIOD_CHANGED')
    old_root = Path(previous['output_root'])
    if (not old_root.is_absolute() or old_root.parent.name != 'scopes'
            or old_root.name != old_registration['sha256']):
        raise ValueError('CONSUMER_SCOPE_ORIGINAL_PRODUCER_NAMED_OUTPUT_REQUIRED')
    value = copy.deepcopy(previous)
    value['registration'] = copy.deepcopy(registration)
    value['output_root'] = str(old_root.parent / registration['sha256'])
    if {key for key in previous if previous[key] != value[key]} != {'registration', 'output_root'}:
        raise ValueError('CONSUMER_SCOPE_UNEXPECTED_CONFIG_CHANGE')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--expected-head', required=True)
    parser.add_argument('--old-config', type=Path, required=True)
    parser.add_argument('--old-config-sha256', required=True)
    parser.add_argument('--producer-manifest', type=Path, required=True)
    parser.add_argument('--producer-manifest-sha256', required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    args = parser.parse_args(); os.umask(0o077); sys.dont_write_bytecode = True
    repo = args.repo.resolve(strict=True)
    git = lambda *parts: subprocess.check_output(['git', '-C', str(repo), *parts], text=True, timeout=20).strip()
    if git('rev-parse', 'HEAD') != args.expected_head or git('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('CONSUMER_SCOPE_EXACT_CLEAN_INSTALLER_SOURCE_REQUIRED')
    old_raw = args.old_config.read_bytes(); manifest_raw = args.producer_manifest.read_bytes()
    if (hashlib.sha256(old_raw).hexdigest() != args.old_config_sha256
            or hashlib.sha256(manifest_raw).hexdigest() != args.producer_manifest_sha256):
        raise ValueError('CONSUMER_SCOPE_INPUT_CHANGED')
    previous = json.loads(old_raw)
    sys.path[:0] = [str(repo / part) for part in ('research', 'research/neuron_learning', 'scripts')]
    from historical_training.current100k import validate_config
    from historical_training.common import canonical, digest, sha, write_once
    from historical_training.install_consumer import runtime_inventory
    old_manifest = validate_config(previous)
    registration = {'manifest': str(args.producer_manifest), 'sha256': args.producer_manifest_sha256}
    new = new_scope_config(previous, registration, old_manifest, json.loads(manifest_raw))
    validate_config(new)
    out = Path(new['output_root']); evidence = args.evidence_root
    if (out.resolve() != out or out.exists() or not evidence.is_absolute() or evidence.resolve() != evidence
            or evidence.exists() or evidence.is_relative_to(Path(previous['output_root']))
            or evidence.is_relative_to(out) or out.is_relative_to(evidence)):
        raise ValueError('CONSUMER_SCOPE_NEW_SEPARATE_OUTPUTS_REQUIRED')
    inventory = runtime_inventory(repo)
    release_id = digest(inventory)
    release = Path.home() / '.local/share/heptatrader/current100k-consumer/releases' / release_id
    installed = {**new, 'runtime_root': str(release), 'runtime_sha256': inventory}
    installed_path = Path.home() / '.config/heptatrader-current100k-consumer' / (digest(installed)+'.json')
    evidence.mkdir(mode=0o700, parents=True)
    config = evidence / 'consumer-new-producer-base.json'; write_once(config, new)
    # This exact command uses the existing isolated consumer installer. Its
    # runtime is a new immutable release; no old release bytes are overwritten.
    argv = ['/usr/bin/python3', '-B', '-m', 'historical_training.install_consumer',
        '--config', str(config), '--receipt', str(evidence / 'INSTALL_RECEIPT.json')]
    receipt = {'schema': 'heptatrader.current100k-consumer-scope-stage.v1',
        'state': 'PREPARED_NORMAL_CONSUMER_INSTALL_NOT_STARTED',
        'source_commit': args.expected_head, 'source_repository': str(repo),
        'base_config': str(config), 'base_config_sha256': sha(config),
        'installed_config_path': str(installed_path),
        'installed_config_sha256': hashlib.sha256(canonical(installed)+b'\n').hexdigest(),
        'new_runtime_directory': str(release), 'new_runtime_inventory_digest': release_id,
        'new_output_root': str(out), 'new_producer_registration': registration,
        'retained_previous_config': str(args.old_config), 'retained_previous_config_sha256': args.old_config_sha256,
        'retained_previous_output_root': previous['output_root'],
        'retained_previous_runtime_directory': previous['runtime_root'],
        'retained_previous_registration': previous['registration'],
        'base_config_changed_fields': ['registration', 'output_root'],
        'period_unchanged': True, 'reward_config_unchanged': installed['reward_config'] == previous['reward_config'],
        'model_configuration_changed': False, 'row_model_identity_still_required_before_fit': True,
        'install_argv': argv, 'working_directory': str(repo),
        'pythonpath': ':'.join(str(repo / part) for part in ('research', 'research/neuron_learning', 'scripts')),
        'services_changed': [], 'old_outputs_modified': False, 'old_runtime_modified': False,
        'old_trial_modified': False, 'training_started': False, 'broker_orders_sent': 0}
    write_once(evidence / 'CONSUMER_SCOPE_STAGE.json', receipt)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
