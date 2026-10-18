"""New resident objective: one price horizon and one action balancing method.

Legacy v1/v2 artifacts and registered plans retain their declared semantics.
This policy is selected only for newly constructed v3 jobs and candidates.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from .integrity import ContractError, digest, sha_file
from . import resident_training_v2 as previous
from .resident_price_baselines import VALUE_RULE

SCHEMA = 'heptatrader.laya-resident-objective.v3'
SELECTION = 'MATURE_300S_TIME_COVERAGE_V3'
ACTION_BALANCE = 'DISJOINT_ONE_ACT_ONE_ESCALATE_UNWEIGHTED_CE'
ACTION_OUTPUT = previous.ACTION_OUTPUT
PRIMARY_HORIZON_SECONDS = 300
POOL_BUCKET_MS = 6 * 60 * 60 * 1000
POOL_ANCHORS = 512
RUNTIME_PINS = tuple(dict.fromkeys(previous.RUNTIME_PINS + (
    'learn/laya_cell/resident_training_v3.py',
    'learn/laya_cell/resident_data.py', 'learn/laya_cell/resident_train.py',
    'learn/laya_cell/resident_predict.py', 'learn/laya_cell/promotion.py',
    'learn/data.py', 'learn/laya_cell/resident_reward_cache.py',
    'scripts/hepta_pnl_watch.py')))
OBJECTIVE = {**deepcopy(previous.OBJECTIVE), 'schema': SCHEMA,
    'row_selection': SELECTION, 'primary_horizon_seconds': PRIMARY_HORIZON_SECONDS,
    'training_horizons_seconds': [PRIMARY_HORIZON_SECONDS],
    'action_batch_balance': ACTION_BALANCE,
    'mature_pool': {'chronological_bucket_ms': POOL_BUCKET_MS,
                    'anchor_cap': POOL_ANCHORS, 'exclude_reserved_before_cap': True},
    'future_price_value_rule': VALUE_RULE['schema']}


def enabled(value):
    return isinstance(value, dict) and value.get('schema') == SCHEMA


def primary_rows(rows):
    return [row for row in rows if row['horizon_sec'] == PRIMARY_HORIZON_SECONDS]


def select_covered_rows(new_rows):
    return previous.select_covered_rows([
        (key, row) for key, row in new_rows
        if row['horizon_sec'] == PRIMARY_HORIZON_SECONDS])


def cover_time_buckets(values, limit, time_key):
    """Equal quotas across supported six-hour strata, no target/score selection."""
    if type(limit) is not int or limit < 1:
        raise ContractError('TRAINING_TIME_POOL_LIMIT')
    groups = defaultdict(list)
    for value in sorted(values, key=time_key):
        at = time_key(value)
        if type(at) is not int or at < 0:
            raise ContractError('TRAINING_TIME_POOL_CHRONOLOGY')
        groups[at // POOL_BUCKET_MS].append(value)
    keys = sorted(groups)
    if len(keys) > limit:
        keys = ([keys[-1]] if limit == 1 else
                [keys[(len(keys)-1)*i//(limit-1)] for i in range(limit)])
    quota = {key: 0 for key in keys}
    for _ in range(min(limit, sum(len(groups[key]) for key in keys))):
        key = min((key for key in keys if quota[key] < len(groups[key])),
                  key=lambda key: (quota[key], key))
        quota[key] += 1
    chosen = []
    for key in keys:
        population, n = groups[key], quota[key]
        if n == 1:
            chosen.append(population[-1])
        elif n > 1:
            chosen.extend(population[(len(population)-1)*i//(n-1)] for i in range(n))
    return sorted(chosen, key=time_key)


def pair_calibration(calibration):
    if calibration is None:
        return None
    from .resident_action_v2_research import _check_profile
    _check_profile(calibration)
    result = {key: value for key, value in deepcopy(calibration).items()
              if key != 'profile_id'}
    result['batch_balance_strategy'] = ACTION_BALANCE
    result['profile_id'] = digest(result)
    return result


def prepare_action(rows, targets, parent_action_logits, cutoff_ms):
    calibration, batches = previous.prepare_action(
        rows, targets, parent_action_logits, cutoff_ms)
    return pair_calibration(calibration), batches


timed_rows = previous.timed_rows
coverage = previous.coverage
action_inference = previous.action_inference


def configuration_for_release(config, runtime_directory):
    result = previous.configuration_for_release(config, runtime_directory)
    runtime = Path(result['runtime_directory'])
    for name in RUNTIME_PINS:
        result['runtime_sha256'][name] = sha_file(runtime/name)
    result['objective'] = deepcopy(OBJECTIVE)
    return result
