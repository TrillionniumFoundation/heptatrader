"""Prospective recent PRICE rows and a train-only Brier/prior calibration.

The PRICE/ACT budgets and original future qualification rules remain unchanged.
Calibration rows are the same model-training rows, never called held out.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .integrity import ContractError, digest, sha_file
from .resident_row_accounting import MIN_TRAINING_ROWS, MAX_TRAINING_ROWS, row_id
from . import resident_training_v5 as previous
from . import resident_price_calibration_v2 as calibration

SCHEMA = 'heptatrader.laya-resident-objective.v6'
SELECTION = 'RECENT_MATURE_300S_TIME_COVERAGE_24H_V6'
MAX_ROW_AGE_MS = 24 * 60 * 60 * 1000
PRIMARY_HORIZON_SECONDS = previous.PRIMARY_HORIZON_SECONDS
PRICE_OUTPUT = calibration.OUTPUT
EPISODE = previous.EPISODE
ROW_ACCOUNTING_SCHEMA = previous.ROW_ACCOUNTING_SCHEMA
ACTION_PHASE = previous.ACTION_PHASE
ACTION_OUTPUT = previous.ACTION_OUTPUT
ACTION_BALANCE = previous.ACTION_BALANCE
OBJECTIVE = {
    **deepcopy(previous.OBJECTIVE), 'schema': SCHEMA,
    'row_selection': SELECTION,
    'mature_pool': {
        **deepcopy(previous.OBJECTIVE['mature_pool']),
        'maximum_anchor_age_ms': MAX_ROW_AGE_MS,
        'clock': 'FROZEN_JOB_TRAINING_CUTOFF',
        'selection': 'TIME_COVERAGE_WITHIN_24H_NO_LABEL_OR_SCORE_SELECTION',
        'older_rows_fill_shortfall': False,
    },
    'price_output_profile': PRICE_OUTPUT,
    'price_calibration_hyperparameters': 'FIXED_CLOSED_FORM_BRIER_PRIOR_SHRINKAGE',
    'price_calibration_objective': 'SAME_TRAINING_ROW_MULTICLASS_BRIER',
    'price_calibration_prior_fallback': 'EXACT_PRIOR_TIE_NOT_OUTPERFORMANCE',
    'price_calibration_prior_fallback_admission':
        'NO_FORWARD_TRIAL_ONLY_FOR_EXACT_ZERO_BLEND_AND_REQUIRED_STRICT_PRIOR_GAIN',
}
RUNTIME_PINS = tuple(dict.fromkeys(previous.RUNTIME_PINS + (
    'learn/laya_cell/resident_training_v6.py',
    'learn/laya_cell/resident_price_calibration_v2.py')))

primary_rows = previous.primary_rows
timed_rows = previous.timed_rows
coverage = previous.coverage
action_inference = previous.action_inference
action_receipt = previous.action_receipt
verify_action_update = previous.verify_action_update
schedule = previous.schedule
receipt = previous.receipt
point = previous.point
curve = previous.curve


def enabled(value):
    return isinstance(value, dict) and value.get('schema') == SCHEMA


def check_objective(value):
    if value != OBJECTIVE:
        raise ContractError('RESIDENT_V6_OBJECTIVE_CHANGED')
    return value


def _cutoff(value):
    if type(value) is not int or value < 0:
        raise ContractError('V6_TRAINING_CUTOFF')
    return value


def _mature_recent(row, cutoff):
    anchor, available = row.get('anchor_ms'), row.get('outcome_available_at_ms')
    if (type(anchor) is not int or anchor < 0 or type(available) is not int
            or available < anchor + PRIMARY_HORIZON_SECONDS * 1000):
        raise ContractError('V6_TRAINING_ROW_CHRONOLOGY')
    return cutoff - MAX_ROW_AGE_MS <= anchor and available <= cutoff


def select_covered_rows(new_rows, *, cutoff_ms):
    """Wait for recent labels instead of filling a job with older history."""
    cutoff = _cutoff(cutoff_ms)
    selected, seen = [], set()
    for key, row in new_rows:
        if row.get('horizon_sec') != PRIMARY_HORIZON_SECONDS:
            continue
        if key != row_id(row) or key in seen:
            raise ContractError('V6_TRAINING_SELECTION_ROW_IDENTITY')
        seen.add(key)
        if _mature_recent(row, cutoff):
            selected.append((key, row))
    return previous.select_covered_rows(selected) if len(selected) >= MIN_TRAINING_ROWS else []


def check_training_rows(rows, *, cutoff_ms):
    """Validate the real worker/replayed population, not only a job claim."""
    cutoff = _cutoff(cutoff_ms)
    if not MIN_TRAINING_ROWS <= len(rows) <= MAX_TRAINING_ROWS:
        raise ContractError('V6_TRAINING_ROW_BUDGET')
    ids = [row_id(row) for row in rows]
    if (len(set(ids)) != len(ids)
            or any(row.get('horizon_sec') != PRIMARY_HORIZON_SECONDS
                   or not _mature_recent(row, cutoff) for row in rows)):
        raise ContractError('V6_TRAINING_ROWS_NOT_UNIQUE_RECENT_MATURE_PRIMARY')
    return ids


def _previous_config(config):
    # Validation projection only: no stored config, job or receipt is rewritten.
    result = deepcopy(config)
    result['objective'] = deepcopy(previous.OBJECTIVE)
    return result


def check_config(config):
    check_objective(config.get('objective'))
    return previous.check_config(_previous_config(config))


def check_job(job):
    check_objective(job.get('objective'))
    return previous.check_job(_previous_config(job))


def source_for_job(job):
    check_objective(job.get('objective'))
    return previous.source_for_job(_previous_config(job))


def context(job):
    check_job(job)
    value = previous.context(_previous_config(job))
    value['objective_digest'] = digest(job['objective'])
    return value


def freeze(job, batches, targets, action_profile, price_baselines):
    check_job(job)
    value = previous.freeze(_previous_config(job), batches, targets,
                            action_profile, price_baselines)
    value.pop('episode_id')
    value['objective_digest'] = digest(job['objective'])
    value['episode_id'] = digest(value)
    return value


def verify_candidate(job, candidate):
    check_job(job)
    source = source_for_job(job)
    previous._verify_training(job, candidate, source)
    profile = calibration.validate(candidate.get('price_calibration'))
    if (candidate.get('price_output_profile') != PRICE_OUTPUT
            or profile['parent_id'] != job['parent_id']
            or profile['adapter_sha256'] != candidate['adapter_sha256']
            or profile['training_cutoff_ms'] != job['window']['training_end_ms']
            or profile['training_row_ids_digest'] !=
                candidate['price_baselines']['training_row_ids_digest']):
        raise ContractError('V6_CANDIDATE_PRICE_CALIBRATION_CHANGED')
    calibration.verify_candidate_fit(job, candidate)


def configuration_for_release(config, runtime_directory, *, closed_action_source,
                               closed_action_source_sha256=None):
    result = previous.configuration_for_release(config, runtime_directory,
        closed_action_source=closed_action_source,
        closed_action_source_sha256=closed_action_source_sha256)
    runtime = Path(result['runtime_directory'])
    for name in RUNTIME_PINS:
        result['runtime_sha256'][name] = sha_file(runtime / name)
    result['objective'] = deepcopy(OBJECTIVE)
    check_config(result)
    return result
