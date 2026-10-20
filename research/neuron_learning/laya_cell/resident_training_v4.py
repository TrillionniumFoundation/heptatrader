"""Opt-in, bounded resident episode; legacy policies remain immutable.

Four passes share one optimizer, one frozen parent and one frozen row population.
Repeated presentations are never reported as additional independent examples.
No Torch import: the scheduler can validate the policy before GPU admission.
"""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import math
from .integrity import ContractError, digest, sha_file
from .resident_row_accounting import checked_ids, row_id, BATCH_SIZE, MIN_TRAINING_ROWS, MAX_TRAINING_ROWS
from . import resident_training_v3 as previous

SCHEMA = 'heptatrader.laya-resident-objective.v4'
ROW_ACCOUNTING_SCHEMA = 'heptatrader.laya-gradient-row-accounting.v2'
EPISODE_SCHEMA = 'heptatrader.laya-training-episode.v1'
CURVE_SCHEMA = 'heptatrader.laya-training-learning-curve.v1'
EPISODE = {
    'schema': 'heptatrader.laya-training-episode-profile.v1',
    'epochs': 4, 'batch_rows': 2, 'max_unique_rows': 32,
    'max_optimizer_steps': 64, 'checkpoint_epochs': [0, 1, 2, 4],
    'optimizer_state': 'CONTINUOUS_WITHIN_EPISODE',
    'batch_order': 'FROZEN_CAUSAL_PARENT_BALANCED_ORDER_REPEATED',
    'checkpoint_selection': 'FIXED_BUDGET_FINAL',
    'measurement_scope': 'TRAINING_ONLY_NOT_FORWARD_EFFICACY'}
OBJECTIVE = {
    **deepcopy(previous.OBJECTIVE), 'schema': SCHEMA,
    'max_steps': EPISODE['max_optimizer_steps'],
    'optimizer_reset_between_epochs': False,
    'training_episode': deepcopy(EPISODE)}
RUNTIME_PINS = tuple(dict.fromkeys(previous.RUNTIME_PINS + (
    'learn/laya_cell/resident_training_v4.py',
    'learn/laya_cell/resident_row_accounting.py',
    'learn/laya_cell/resident_store.py', 'learn/laya_cell/resident.py')))
SELECTION = previous.SELECTION
ACTION_OUTPUT = previous.ACTION_OUTPUT
ACTION_BALANCE = previous.ACTION_BALANCE
PRIMARY_HORIZON_SECONDS = previous.PRIMARY_HORIZON_SECONDS
select_covered_rows = previous.select_covered_rows
primary_rows = previous.primary_rows
timed_rows = previous.timed_rows
coverage = previous.coverage
prepare_action = previous.prepare_action
action_inference = previous.action_inference


def enabled(value):
    return isinstance(value, dict) and value.get('schema') == SCHEMA


def check_objective(value):
    if value != OBJECTIVE:
        raise ContractError('RESIDENT_V4_OBJECTIVE_OR_EPISODE_CHANGED')
    return value


def check_job(job):
    check_objective(job.get('objective'))
    if job.get('training_row_accounting_schema') != ROW_ACCOUNTING_SCHEMA:
        raise ContractError('EPISODE_ROW_ACCOUNTING_SCHEMA')
    ids = checked_ids(job.get('training_row_ids'), MIN_TRAINING_ROWS)
    if job.get('data_fingerprint') != digest(ids):
        raise ContractError('EPISODE_ROW_FINGERPRINT')
    for name in ('parent_id', 'parent_manifest_sha256', 'dataset_sha256'):
        checked_ids([job.get(name)], 1, 1)
    cutoff = job.get('window', {}).get('training_end_ms')
    if type(cutoff) is not int or cutoff < 0:
        raise ContractError('EPISODE_CUTOFF')
    return ids


def context(job):
    ids = check_job(job)
    return {'schema': EPISODE_SCHEMA, 'profile': deepcopy(EPISODE),
            'parent_id': job['parent_id'],
            'parent_manifest_sha256': job['parent_manifest_sha256'],
            'dataset_sha256': job['dataset_sha256'],
            'training_cutoff_ms': job['window']['training_end_ms'],
            'objective_digest': digest(job['objective']),
            'training_row_ids_digest': digest(sorted(ids)),
            'unique_training_rows': len(ids)}


def freeze(job, batches, targets, calibration, price_baselines):
    result = context(job)
    order = [[row_id(row) for row in selected] for selected in batches]
    flat = [key for selected in order for key in selected]
    checked_ids(flat, MIN_TRAINING_ROWS)
    if set(flat) != set(job['training_row_ids']) or any(
            not 1 <= len(selected) <= BATCH_SIZE for selected in order):
        raise ContractError('EPISODE_FROZEN_BATCH_POPULATION')
    result.update(
        batch_row_ids=order, batches_per_epoch=len(order),
        planned_optimizer_steps=len(order) * EPISODE['epochs'],
        planned_gradient_presentations=len(flat) * EPISODE['epochs'],
        frozen_parent_action_targets_digest=digest(targets),
        action_training_profile_id=calibration['profile_id'] if calibration else None,
        price_baseline_profile_id=price_baselines['profile_id'])
    if result['planned_optimizer_steps'] > EPISODE['max_optimizer_steps']:
        raise ContractError('EPISODE_STEP_BUDGET')
    result['episode_id'] = digest(result)
    return result


def schedule(batches):
    flat = [row_id(row) for selected in batches for row in selected]
    checked_ids(flat, MIN_TRAINING_ROWS)
    if len(batches) != (len(flat) + BATCH_SIZE - 1) // BATCH_SIZE or any(
            not 1 <= len(selected) <= BATCH_SIZE for selected in batches):
        raise ContractError('EPISODE_BATCH_BUDGET')
    return [(epoch, index + 1, selected)
            for epoch in range(1, EPISODE['epochs'] + 1)
            for index, selected in enumerate(batches)]


def receipt(reserved, steps, measured):
    checked_ids(reserved, MIN_TRAINING_ROWS)
    checked_ids(measured, MIN_TRAINING_ROWS)
    per_epoch = (len(reserved) + BATCH_SIZE - 1) // BATCH_SIZE
    if (not isinstance(steps, list)
            or len(steps) != per_epoch * EPISODE['epochs']
            or len(steps) > EPISODE['max_optimizer_steps']):
        raise ContractError('EPISODE_COMPLETE_STEP_BUDGET')
    first = []
    applied, rolled_back = [], []
    for index, step in enumerate(steps):
        ids = checked_ids(step.get('training_row_ids'), 1, BATCH_SIZE)
        if (type(step.get('applied')) is not bool
                or step.get('epoch') != index // per_epoch + 1
                or step.get('batch_in_epoch') != index % per_epoch + 1
                or step.get('training_examples') != len(ids)):
            raise ContractError('EPISODE_STEP_RECEIPT')
        if index < per_epoch:
            first.extend(ids)
        elif ids != steps[index % per_epoch]['training_row_ids']:
            raise ContractError('EPISODE_BATCH_ORDER_CHANGED')
        (applied if step['applied'] else rolled_back).extend(ids)
    checked_ids(first, MIN_TRAINING_ROWS)
    if set(first) != set(reserved) or set(measured) != set(reserved):
        raise ContractError('EPISODE_RESERVED_GRADIENT_MISMATCH')
    unique = lambda values: [key for key in reserved if key in set(values)]
    return {
        'schema': ROW_ACCOUNTING_SCHEMA, 'reserved_row_ids': list(reserved),
        'gradient_attempted_row_ids': list(first),
        'applied_row_ids': unique(applied), 'rolled_back_row_ids': unique(rolled_back),
        'measurement_row_ids': list(measured), 'reserved_count': len(reserved),
        'gradient_attempted_count': len(first), 'applied_count': len(set(applied)),
        'rolled_back_count': len(set(rolled_back)), 'measurement_count': len(measured),
        'epochs': EPISODE['epochs'],
        'gradient_attempted_presentations': len(applied) + len(rolled_back),
        'applied_presentations': len(applied), 'rolled_back_presentations': len(rolled_back),
        'attempted_steps': len(steps),
        'applied_steps': sum(step['applied'] for step in steps),
        'rolled_back_steps': sum(not step['applied'] for step in steps),
        'unique_applied_and_rolled_back_sets_may_overlap': True,
        'repeated_presentations_are_new_independent_rows': False}


def point(epoch, ce, brier, steps, elapsed):
    if (len(ce) != len(brier) or not MIN_TRAINING_ROWS <= len(ce) <= MAX_TRAINING_ROWS
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   or value < 0 for value in [*ce, *brier, elapsed])):
        raise ContractError('EPISODE_CURVE_MEASUREMENT')
    applied = [step for step in steps if step['applied']]
    return {'epoch': epoch, 'rows': len(ce),
            'optimizer_steps_attempted': len(steps),
            'optimizer_steps_applied': len(applied),
            'gradient_presentations_attempted': sum(len(s['training_row_ids']) for s in steps),
            'gradient_presentations_applied': sum(len(s['training_row_ids']) for s in applied),
            'unique_gradient_rows_applied': len({key for s in applied for key in s['training_row_ids']}),
            'action_optimizer_steps_applied': sum(s['action_optimizer_update'] for s in steps),
            'ce': sum(ce) / len(ce), 'brier': sum(brier) / len(brier),
            'elapsed_seconds': elapsed}


def curve(episode, points, baselines):
    parent = points[0]
    return {'schema': CURVE_SCHEMA, 'episode_id': episode['episode_id'],
            'training_row_ids_digest': episode['training_row_ids_digest'],
            'training_only': True, 'future_value_proven': False,
            'checkpoint_selection': EPISODE['checkpoint_selection'],
            'optimizer_reset_between_epochs': False,
            'same_training_rows_baselines': baselines,
            'checkpoints': [{**p, 'parent_minus_candidate_ce': parent['ce'] - p['ce'],
                             'parent_minus_candidate_brier': parent['brier'] - p['brier']}
                            for p in points]}


def verify_candidate(job, candidate):
    check_job(job)
    if candidate.get('objective') != job['objective']:
        raise ContractError('EPISODE_CANDIDATE_OBJECTIVE')
    got = candidate.get('row_accounting')
    if not isinstance(got, dict):
        raise ContractError('EPISODE_RECEIPT_MISSING')
    expected = receipt(job['training_row_ids'], candidate.get('steps'),
                       got.get('measurement_row_ids'))
    if got != expected or candidate.get('measurement', {}).get('rows') != len(job['training_row_ids']):
        raise ContractError('EPISODE_RECEIPT_MISMATCH')
    frozen = candidate.get('training_episode')
    if (not isinstance(frozen, dict)
            or frozen.get('episode_id') != digest({k: v for k, v in frozen.items() if k != 'episode_id'})
            or any(frozen.get(k) != v for k, v in context(job).items())):
        raise ContractError('EPISODE_FROZEN_CONTEXT_CHANGED')
    batches = [s['training_row_ids'] for s in candidate['steps'] if s['epoch'] == 1]
    if (frozen.get('batch_row_ids') != batches or frozen.get('batches_per_epoch') != len(batches)
            or frozen.get('planned_optimizer_steps') != len(candidate['steps'])
            or frozen.get('planned_gradient_presentations') != expected['gradient_attempted_presentations']
            or frozen.get('price_baseline_profile_id') != candidate.get('price_baselines', {}).get('profile_id')
            or frozen.get('action_training_profile_id') != (candidate['action_training_profile']['profile_id']
                if candidate.get('action_training_profile') else None)):
        raise ContractError('EPISODE_FROZEN_SCHEDULE_CHANGED')
    report = candidate.get('learning_curve', {})
    points = report.get('checkpoints')
    if (report.get('schema') != CURVE_SCHEMA or report.get('episode_id') != frozen['episode_id']
            or report.get('training_row_ids_digest') != frozen['training_row_ids_digest']
            or report.get('checkpoint_selection') != EPISODE['checkpoint_selection']
            or report.get('training_only') is not True or report.get('future_value_proven') is not False
            or report.get('optimizer_reset_between_epochs') is not False
            or not isinstance(points, list)
            or [p.get('epoch') for p in points] != EPISODE['checkpoint_epochs']):
        raise ContractError('EPISODE_CURVE_CONTEXT')
    previous_elapsed = -1.
    for p in points:
        upto = p['epoch'] * len(batches)
        prefix = candidate['steps'][:upto]
        n = len(job['training_row_ids'])
        for key in ('ce', 'brier', 'elapsed_seconds'):
            if type(p.get(key)) not in (int, float) or not math.isfinite(p[key]) or p[key] < 0:
                raise ContractError('EPISODE_CURVE_NONFINITE')
        rebuilt = point(p['epoch'], [p['ce']] * n, [p['brier']] * n, prefix, p['elapsed_seconds'])
        for key in rebuilt:
            if key not in ('ce', 'brier') and p.get(key) != rebuilt[key]:
                raise ContractError('EPISODE_CURVE_ACCOUNTING')
        if (p['elapsed_seconds'] < previous_elapsed
                or not math.isclose(p.get('parent_minus_candidate_ce', math.inf), points[0]['ce'] - p['ce'], abs_tol=1e-12)
                or not math.isclose(p.get('parent_minus_candidate_brier', math.inf), points[0]['brier'] - p['brier'], abs_tol=1e-12)):
            raise ContractError('EPISODE_CURVE_CHRONOLOGY_OR_DELTA')
        previous_elapsed = p['elapsed_seconds']
    measurement = candidate['measurement']
    if (points[0]['ce'] != measurement.get('parent_ce')
            or points[-1]['ce'] != measurement.get('candidate_ce')
            or points[-1]['brier'] != measurement.get('candidate_brier')):
        raise ContractError('EPISODE_FINAL_MEASUREMENT_CHANGED')


def configuration_for_release(config, runtime_directory):
    result = previous.configuration_for_release(config, runtime_directory)
    runtime = Path(result['runtime_directory'])
    for name in RUNTIME_PINS:
        result['runtime_sha256'][name] = sha_file(runtime / name)
    result['objective'] = deepcopy(OBJECTIVE)
    return result
