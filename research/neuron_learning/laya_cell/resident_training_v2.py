"""Explicit successor policy; existing v1 jobs/trials keep their old semantics.

Select only original mature rows. Nonoverlap is a descriptive count, not proof
of independent market observations. No Torch import in the scheduler.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from .integrity import ContractError, digest
from .resident_row_accounting import MAX_TRAINING_ROWS, row_id
from .resident_price_baselines import SCHEMA as PRICE_BASELINE_SCHEMA

SCHEMA = 'heptatrader.laya-resident-objective.v2'
SELECTION = 'MATURE_TIME_COVERAGE_PER_HORIZON_V2'
ACTION_OUTPUT = 'FROZEN_TRAIN_ONLY_ACTION_TEMPERATURE_V2'
RUNTIME_PINS = ('learn/laya_cell/resident_training_v2.py',
                'learn/laya_cell/resident_action_v2_research.py',
                'learn/laya_cell/resident_price_baselines.py')
OBJECTIVE = {'schema': SCHEMA,
    'primary': 'OBSERVED_FUTURE_MID_INTERVAL_CROSS_ENTROPY',
    'auxiliary_teacher_weight': .1, 'action_loss_weight': None,
    'action_target': 'FROZEN_PARENT_ANSWER_CORRECTNESS_ACT_VS_ESCALATE',
    'action_policy': ACTION_OUTPUT, 'row_selection': SELECTION,
    'price_baselines': PRICE_BASELINE_SCHEMA,
    'financial_reward_policy': 'EXACT_BEHAVIOR_BOUND_ROUNDTRIP_ONLY_NO_OFFPOLICY_POLICY_GRADIENT',
    'optimizer': 'AdamW', 'optimizer_reset_each_job': True,
    'learning_rate': .0001, 'weight_decay': 0., 'gradient_norm_cap': 1.,
    'max_steps': 16, 'max_rows': MAX_TRAINING_ROWS, 'minimum_new_rows': 16,
    'automatic_execution_selection': False}


def enabled(value):
    return isinstance(value, dict) and value.get('schema') == SCHEMA


def select_covered_rows(new_rows):
    """Cover each horizon's chronology without looking at labels or scores."""
    groups = defaultdict(list)
    seen = set()
    for key, row in new_rows:
        if key != row_id(row) or key in seen:
            raise ContractError('TRAINING_SELECTION_ROW_IDENTITY')
        if (type(row.get('anchor_ms')) is not int or row['anchor_ms'] < 0
                or row.get('horizon_sec') not in (300, 900)):
            raise ContractError('TRAINING_SELECTION_CHRONOLOGY')
        seen.add(key)
        groups[row['horizon_sec']].append((key, row))
    for values in groups.values():
        values.sort(key=lambda item: (item[1]['anchor_ms'], item[0]))
    quotas = {h: 0 for h in groups}
    for _ in range(min(MAX_TRAINING_ROWS, len(new_rows))):
        h = min((h for h in groups if quotas[h] < len(groups[h])),
                key=lambda h: (quotas[h], h))
        quotas[h] += 1
    chosen = []
    for h, values in sorted(groups.items()):
        n = quotas[h]
        if n == 1:
            chosen.append(values[-1])
        elif n > 1:
            chosen.extend(values[(len(values)-1)*i//(n-1)] for i in range(n))
    return sorted(chosen, key=lambda item: (item[1]['anchor_ms'],
                                          item[1]['horizon_sec'], item[0]))


def timed_rows(rows, dataset):
    """Read actual availability from the immutable dataset, never refreeze it."""
    outcomes = {}
    for sample in dataset['samples']:
        for horizon, outcome in zip((300, 900), sample['outcome_receipts']):
            if outcome is None:
                continue
            key = digest([sample['sample_id'], horizon, digest(outcome)])
            metadata = (outcome['observed_at_ms'], outcome['available_at_ms'])
            if key in outcomes and outcomes[key] != metadata:
                raise ContractError('TRAINING_OUTCOME_TIME_CONFLICT')
            outcomes[key] = metadata
    result = []
    for row in rows:
        if row_id(row) not in outcomes:
            raise ContractError('TRAINING_OUTCOME_TIME_MISSING')
        observed, available = outcomes[row_id(row)]
        result.append({**row, 'outcome_observed_at_ms': observed,
                       'outcome_available_at_ms': available})
    return result


def coverage(rows, cutoff_ms):
    if type(cutoff_ms) is not int or cutoff_ms < 0 or not rows:
        raise ContractError('TRAINING_COVERAGE_INPUT')
    anchors = [r['anchor_ms'] for r in rows]
    available = [r['outcome_available_at_ms'] for r in rows]
    if any(type(x) is not int or x > cutoff_ms for x in anchors+available):
        raise ContractError('TRAINING_COVERAGE_FUTURE_ROW')
    intervals = sorted((r['anchor_ms']+1000*r['horizon_sec'], r['anchor_ms'])
                       for r in rows)
    end = -1
    nonoverlap = 0
    for finish, start in intervals:
        if start >= end:
            nonoverlap += 1
            end = finish
    return {'rows': len(rows), 'unique_anchors': len(set(anchors)),
            'horizon_counts': dict(sorted(Counter(str(r['horizon_sec']) for r in rows).items())),
            'target_class_counts': dict(sorted(Counter(str(r['target_class']) for r in rows).items())),
            'first_anchor_ms': min(anchors), 'last_anchor_ms': max(anchors),
            'anchor_span_ms': max(anchors)-min(anchors),
            'first_outcome_available_ms': min(available),
            'last_outcome_available_ms': max(available),
            'oldest_label_age_at_cutoff_ms': cutoff_ms-min(available),
            'newest_label_age_at_cutoff_ms': cutoff_ms-max(available),
            'maximum_nonoverlapping_target_intervals': nonoverlap,
            'nonoverlap_is_not_statistical_independence': True}


def prepare_action(rows, targets, parent_action_logits, cutoff_ms):
    """Frozen-parent genuine correctness only; every PRICE row stays unique."""
    import torch
    from .resident_action_v2_research import (
        MIN_TRAIN_ROWS, decide_train_only_action, stratified_action_pairs)
    source = []
    logits = []
    by_id = {row_id(r): r for r in rows}
    for row in rows:
        key = digest([row['sample_id'], row['horizon_sec']])
        value = targets[key]
        if value is None:
            continue
        source.append({'row_id': row_id(row), 'anchor_ms': row['anchor_ms'],
            'future_outcome_available_ms': row['outcome_available_at_ms'],
            'parent_correct': value == 0})
        logits.append(parent_action_logits[key])
    calibration = None
    pairs = []
    if len(source) >= MIN_TRAIN_ROWS:
        calibration = decide_train_only_action(source, cutoff_ms=cutoff_ms,
                                               parent_action_logits=torch.stack(logits))
        pairs = stratified_action_pairs(source, calibration)
    paired = [[by_id[source[i]['row_id']], by_id[source[j]['row_id']]]
              for i, j in pairs]
    used = {row_id(r) for pair in paired for r in pair}
    remaining = [r for r in rows if row_id(r) not in used]
    batches = paired + [remaining[i:i+2] for i in range(0, len(remaining), 2)]
    if (len(batches) > 16 or len(used) != len(paired)*2
            or sorted(row_id(r) for batch in batches for r in batch) != sorted(by_id)):
        raise ContractError('TRAINING_ACTION_ROW_REUSE')
    return calibration, batches


def action_inference(logits, calibration):
    """Use the same frozen temperature at inference; absent support has no p."""
    from .resident_action_v2_research import action_support_diagnostic,v2_action_inference
    if calibration is None:
        return {'status': 'INSUFFICIENT_CAUSAL_PARENT_CORRECTNESS_ROWS',
                'action_support_diagnostic': action_support_diagnostic(None),
                'frozen_profile_id': None, 'action_correct_probs': None,
                'recommendations': ['ESCALATE_ONLY_NO_BALANCED_TRAINING']*len(logits),
                'model_or_trade_permission': False}
    return v2_action_inference(logits, calibration)


def action_observation(plan, prediction, *, parent_correct, outcome=None):
    """Validate action support; absence of ACTION says nothing about PRICE."""
    import math
    if type(parent_correct) is not bool:
        raise ContractError('ACTION_PARENT_CORRECTNESS_REQUIRED')
    declared = plan.get('action_output_profile')
    probabilities = prediction.get('candidate_act_escalate_probs')
    abstention = None
    if declared is None:
        if prediction.get('action_output_profile') is not None:
            raise ContractError('ACTION_OUTPUT_PROFILE_CHANGED')
    elif declared == ACTION_OUTPUT:
        report = prediction.get('action_inference')
        admitted = plan.get('action_optimizer_admitted')
        profile_id = plan.get('action_training_profile_id')
        if (type(admitted) is not bool or not isinstance(report, dict)
                or prediction.get('action_output_profile') != declared
                or report.get('frozen_profile_id') != profile_id
                or report.get('model_or_trade_permission') is not False
                or (admitted and not profile_id)):
            raise ContractError('ACTION_OUTPUT_PROFILE_CHANGED')
        values = report.get('action_correct_probs')
        if not admitted:
            if values is not None or probabilities is not None:
                raise ContractError('UNSUPPORTED_ACTION_PROBABILITY')
            abstention = 'NO_BALANCED_ACTION_TRAINING'
        elif values == [None]:
            if probabilities is not None:
                raise ContractError('UNSUPPORTED_ACTION_PROBABILITY')
            abstention = 'ACTION_OOD_ABSTENTION'
        else:
            if (not isinstance(values, list) or len(values) != 1
                    or type(values[0]) not in (int, float)
                    or not math.isfinite(values[0]) or not 0 <= values[0] <= 1
                    or probabilities != [values[0], 1-values[0]]):
                raise ContractError('ACT_ESCALATE_VECTOR')
    else:
        raise ContractError('ACTION_OUTPUT_PROFILE_CHANGED')
    if abstention is None:
        if (not isinstance(probabilities, list) or len(probabilities) != 2
                or any(type(p) not in (int, float) or not math.isfinite(p)
                       or not 0 <= p <= 1 for p in probabilities)
                or abs(sum(probabilities)-1) > 1e-5):
            raise ContractError('ACT_ESCALATE_VECTOR')
        target = 0 if parent_correct else 1
        score = sum((p-(i == target))**2 for i, p in enumerate(probabilities))
        correct_probability = probabilities[0]
    else:
        score = None
        correct_probability = None
    if outcome is not None:
        if not isinstance(outcome, dict) or 'action_head_brier' not in outcome:
            raise ContractError('ACTION_REPORTED_SCORE_MISSING')
        reported = outcome['action_head_brier']
        if score is None:
            if reported is not None:
                raise ContractError('UNSUPPORTED_ACTION_REPORTED_SCORE')
        elif (type(reported) not in (int, float) or not math.isfinite(reported)
                or abs(score-reported) > 1e-5):
            raise ContractError('ACTION_REPORTED_SCORE_CHANGED')
    return {'action_output_profile': declared,
            'action_correct_probability': correct_probability,
            'action_brier': score, 'abstention_reason': abstention}


def configuration_for_release(config, runtime_directory):
    """Opt in a new objective while retaining existing ledgers and policies."""
    from copy import deepcopy
    from pathlib import Path
    from .integrity import sha_file
    if (config.get('schema') != 'heptatrader.laya-resident-config.v1'
            or config.get('automatic_trading') is not False):
        raise ContractError('RESIDENT_ORIGINAL_CONFIG_REQUIRED')
    result = deepcopy(config)
    previous = Path(config['runtime_directory'])
    runtime = Path(runtime_directory).resolve(strict=True)
    names = set(config.get('runtime_sha256', {})) | set(RUNTIME_PINS)
    if not names or any(Path(n).is_absolute() or '..' in Path(n).parts for n in names):
        raise ContractError('RESIDENT_RUNTIME_PATH')
    result['runtime_directory'] = str(runtime)
    result['runtime_sha256'] = {n: sha_file(runtime/n) for n in sorted(names)}
    binary = Path(config['native_binary'])
    try:
        relative = binary.relative_to(previous)
    except ValueError:
        pass
    else:
        binary = runtime/relative
    if sha_file(binary) != config['native_binary_sha256']:
        raise ContractError('RESIDENT_NATIVE_BINARY_MUST_REMAIN_IDENTICAL')
    result['native_binary'] = str(binary)
    result['objective'] = deepcopy(OBJECTIVE)
    return result


def main():
    import argparse, json, os
    from pathlib import Path
    from .integrity import canonical, read_json, sha_file
    from .resident import config_check
    parser = argparse.ArgumentParser(description='Prepare explicit v2 training configuration; no service or GPU actions')
    parser.add_argument('--from-config', type=Path, required=True)
    parser.add_argument('--runtime-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = configuration_for_release(read_json(args.from_config), args.runtime_directory)
    config_check(config)
    raw = canonical(config)+b'\n'
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.output.exists():
        if args.output.read_bytes() != raw:
            raise ContractError('NEW_V2_CONFIG_PATH_REQUIRED')
    else:
        with args.output.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        args.output.chmod(0o400)
    print(json.dumps({'configuration': str(args.output),
        'configuration_sha256': sha_file(args.output), 'objective': SCHEMA,
        'runtime_directory': config['runtime_directory'],
        'existing_state_and_trial_counters_preserved': True,
        'service_changed': False, 'automatic_trading': False}, sort_keys=True))


if __name__ == '__main__':
    main()
