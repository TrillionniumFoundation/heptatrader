#!/usr/bin/env python3
"""Freeze the first 32 verified new-producer labels for the ordinary guarded fit.

This finite watcher reads an existing Current100k consumer. It never refreshes
market data, starts a model, registers a trial, changes a plan, or sends orders.
Only its separate preparation directory is writable. Registration remains the
existing signed NEW_PRODUCER_SCOPE path after a completed guarded centered fit.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from observe_current100k_requests import read

POLICY = 'FIRST_32_CHRONOLOGICAL_CLOSED_NEW_PRODUCER_V1'
MIN_ROWS = 32


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def select_dataset(data, registration, prior, history, validate_rows):
    """Select by input time alone; no reward ranking or historical row reuse."""
    if (data.get('schema') != 'heptatrader.current100k-value-dataset.v1'
            or digest({k:v for k,v in data.items() if k != 'fingerprint'}) != data.get('fingerprint')
            or data.get('producer_manifest_sha256') != registration['sha256']
            or data.get('period_sha256') != prior['period_sha256']
            or registration['sha256'] == prior['producer_registration']['sha256']
            or data.get('synthetic_rewards') != 0 or data.get('old25k_rewards') != 0
            or data.get('trade_authority') is not False):
        raise ValueError('NEW_SCOPE_DATASET_IDENTITY_OR_POLICY')
    rows = data.get('rows', [])
    if not isinstance(rows, list) or len(rows) > 4096:
        raise ValueError('NEW_SCOPE_ROW_BOUND')
    model = prior['behavior_scope']['behavior_model_id']
    for field in ('row_id', 'input_id', 'reward_id', 'episode_id'):
        if len({row[field] for row in rows}) != len(rows):
            raise ValueError('NEW_SCOPE_DUPLICATE_' + field.upper())
    if any(row['behavior_scope']['producer_manifest_sha256'] != registration['sha256']
           or row['behavior_scope']['model_id'] != model
           or row['period_sha256'] != prior['period_sha256'] for row in rows):
        raise ValueError('NEW_SCOPE_MIXED_ROW_IDENTITY')
    if ({value for plan in history for value in plan['training_input_ids']} & {r['input_id'] for r in rows}
            or {value for plan in history for value in plan['training_reward_ids']} & {r['reward_id'] for r in rows}):
        raise ValueError('NEW_SCOPE_HISTORICAL_TRAINING_OVERLAP')
    ordered = sorted(rows, key=lambda row: (row['input_started_at_ms'], row['row_id']))
    summary = {'selection_policy': POLICY, 'minimum_rows': MIN_ROWS,
        'distinct_settled_rows': len(rows), 'remaining_rows': max(0, MIN_ROWS-len(rows)),
        'producer_manifest_sha256': registration['sha256'], 'period_sha256': data['period_sha256'],
        'behavior_model_id': model, 'first_input_at_ms': ordered[0]['input_started_at_ms'] if rows else None,
        'last_input_at_ms': ordered[-1]['input_started_at_ms'] if rows else None,
        'training_started': False, 'trial_registered': False, 'broker_orders_sent': 0,
        'efficacy_claim': False}
    if len(rows) < MIN_ROWS:
        return None, {**summary, 'state': 'WAITING_NEW_PRODUCER_32_SETTLED_LABELS'}
    frozen = {**data, 'rows': ordered[:MIN_ROWS]}
    frozen['fingerprint'] = digest({k:v for k,v in frozen.items() if k != 'fingerprint'})
    # The existing row verifier recomputes executions, fees, exact request
    # provenance and target bounds. The watcher cannot weaken its contract.
    validate_rows(frozen, data)
    return frozen, {**summary, 'state': 'VERIFIED_NEW_PRODUCER_32_READY_FOR_GUARDED_FIT',
        'selected_row_ids': [r['row_id'] for r in frozen['rows']],
        'selected_first_input_at_ms': frozen['rows'][0]['input_started_at_ms'],
        'selected_last_input_at_ms': frozen['rows'][-1]['input_started_at_ms']}


def write_once(path, value):
    raw = canonical(value)+b'\n'
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('NEW_SCOPE_PREPARATION_ALREADY_EXISTS_DIFFERENT_BYTES')
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consumer-config', type=Path, required=True)
    parser.add_argument('--consumer-config-sha256', required=True)
    parser.add_argument('--producer-manifest-sha256', required=True)
    parser.add_argument('--expected-source-commit', required=True)
    parser.add_argument('--legacy-plan', type=Path, action='append', required=True)
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--duration-seconds', type=int, default=300)
    parser.add_argument('--interval-seconds', type=int, default=60)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(); os.umask(0o077); sys.dont_write_bytecode = True
    if not 1 <= args.duration_seconds <= 86400 or not 30 <= args.interval_seconds <= 300:
        raise ValueError('NEW_SCOPE_WATCHER_FINITE_BOUND')
    raw = args.consumer_config.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.consumer_config_sha256:
        raise ValueError('NEW_SCOPE_CONSUMER_CONFIG_CHANGED')
    config = json.loads(raw); registration = config['registration']
    if registration['sha256'] != args.producer_manifest_sha256:
        raise ValueError('NEW_SCOPE_EXPLICIT_PRODUCER_PIN_REQUIRED')
    output = args.output_directory; consumer_root = Path(config['output_root'])
    roots = {path.parent.parent for path in args.legacy_plan}
    if (not output.is_absolute() or output.resolve() != output
            or any(output.is_relative_to(root) or root.is_relative_to(output) for root in [consumer_root, *roots])):
        raise ValueError('NEW_SCOPE_SEPARATE_PREPARATION_ROOT')
    source = Path(__file__).resolve().parent.parent
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True, timeout=20).strip()
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all'], text=True, timeout=20).strip()
    if head != args.expected_source_commit or dirty:
        raise ValueError('NEW_SCOPE_EXACT_CLEAN_SOURCE_REQUIRED')
    sys.path[:0] = [str(source / part) for part in ('research', 'research/neuron_learning', 'scripts')]
    from historical_training.current100k import validate_config
    from historical_training.current100k_value_trial import selected_rows
    from laya_cell.financial_forward_protocol import validate_plan
    validate_config(config)
    history = [read(path) for path in args.legacy_plan]
    for path, plan in zip(args.legacy_plan, history):
        validate_plan(plan)
        if path.name != 'plan.json' or path.parent.name != plan['plan_id']:
            raise ValueError('NEW_SCOPE_LEGACY_PLAN_PATH_IDENTITY')
    if sorted(p['trial'] for p in history) != list(range(1, len(history)+1)) or len({p['plan_id'] for p in history}) != len(history):
        raise ValueError('NEW_SCOPE_COMPLETE_TRIAL_HISTORY_REQUIRED')
    prior = max(history, key=lambda p: p['trial'])
    pins = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in args.legacy_plan}
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_fd = os.open(output / 'preparation.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    write_once(output / 'PREPARATION_REQUEST.json', {'schema': 'heptatrader.current100k-newscope-preparation.v1',
        'source_repository': str(source), 'source_commit': head,
        'selection_policy': POLICY, 'minimum_rows': MIN_ROWS, 'registration_scope': 'NEW_PRODUCER_SCOPE',
        'consumer_config': str(args.consumer_config), 'consumer_config_sha256': args.consumer_config_sha256,
        'producer_registration': registration, 'prior_plan_id': prior['plan_id'], 'legacy_plans_sha256': pins,
        'expected_trial': len(history)+1, 'no_historical_rows_reused': True,
        'training_started': False, 'trial_registered': False, 'broker_orders_sent': 0})
    deadline = time.monotonic()+args.duration_seconds; stopped = False; cached_digest = None; cached_summary = None
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    while not stopped:
        now = int(time.time()*1000)
        try:
            if hashlib.sha256(args.consumer_config.read_bytes()).hexdigest() != args.consumer_config_sha256:
                raise ValueError('NEW_SCOPE_CONSUMER_CONFIG_CHANGED')
            validate_config(config)
            if ({str(p) for root in roots for p in root.glob('*/plan.json')} != set(pins)
                    or any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != sha for path, sha in pins.items())):
                raise ValueError('NEW_SCOPE_TRIAL_HISTORY_CHANGED_NO_AUTOMATIC_REALLOCATION')
            status = read(consumer_root / 'status.json')
            stamp = status.get('input_checked_at_ms', status.get('at_ms'))
            if (type(stamp) is not int or not 0 <= now-stamp <= 180000
                    or status.get('state') not in ('READY_VALUE_LABELS_NOT_EFFICACY', 'WAITING_CURRENT100K_SETTLED_LABELS')):
                raise ValueError('NEW_SCOPE_FRESH_CONSUMER_REQUIRED')
            dataset_path = Path(status['dataset_path'])
            if (dataset_path.resolve() != dataset_path or not dataset_path.is_relative_to(consumer_root)
                    or not dataset_path.is_file() or dataset_path.stat().st_size > 64*1024**2):
                raise ValueError('NEW_SCOPE_CONSUMER_DATASET_PATH')
            raw = dataset_path.read_bytes(); dataset_sha = hashlib.sha256(raw).hexdigest()
            if dataset_sha != status['dataset_sha256']:
                raise ValueError('NEW_SCOPE_CONSUMER_DATASET_CHANGED_DURING_READ')
            if dataset_sha != cached_digest:
                frozen, summary = select_dataset(json.loads(raw), registration, prior, history, selected_rows)
                cached_digest = dataset_sha; cached_summary = summary
                if frozen is not None:
                    # Freeze exactly one predetermined cohort. Fitting/replay
                    # and registration each retain their independent checks.
                    destination = output / 'FROZEN_NEW_PRODUCER_32.json'
                    write_once(destination, frozen)
                    receipt = {**summary, 'at_ms': now, 'dataset_path': str(destination),
                        'dataset_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                        'source_consumer_dataset': str(dataset_path), 'source_consumer_dataset_sha256': dataset_sha,
                        'legacy_plans_sha256': pins, 'expected_trial': len(history)+1,
                        'registration_scope': 'NEW_PRODUCER_SCOPE', 'next_required_step': 'NORMAL_GUARDED_CENTERED_FIT_THEN_WARM_STAGE'}
                    write_once(output / 'READY_FOR_GUARDED_FIT.json', receipt)
                    print(json.dumps(receipt, sort_keys=True), flush=True)
                    return
            summary = {**cached_summary, 'at_ms': now, 'consumer_input_checked_at_ms': stamp,
                'source_dataset_sha256': dataset_sha}
        except (OSError, ValueError, KeyError) as error:
            summary = {'at_ms': now, 'state': 'NEW_SCOPE_PREPARATION_BLOCKED', 'reason': str(error)[:600],
                'training_started': False, 'trial_registered': False, 'broker_orders_sent': 0}
        temporary = output / 'status.json.next'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'wb') as stream: stream.write(canonical(summary)+b'\n')
        os.replace(temporary, output / 'status.json'); print(json.dumps(summary, sort_keys=True), flush=True)
        if args.once or time.monotonic() >= deadline:
            return
        wake = min(deadline, time.monotonic()+args.interval_seconds)
        while not stopped and time.monotonic() < wake:
            time.sleep(min(1, wake-time.monotonic()))


if __name__ == '__main__':
    main()
