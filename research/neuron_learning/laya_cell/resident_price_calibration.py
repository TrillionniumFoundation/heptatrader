"""Explicit train-only correction of the financial price-class prior.

Fit only the final child's actual probabilities on its unique PRICE training
rows. This is neither new independent data nor evidence of future value. The
frozen raw model, calibrator and CPU comparators remain separately observable.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from .integrity import ContractError, digest
from .resident_row_accounting import MAX_TRAINING_ROWS, MIN_TRAINING_ROWS, row_id
from . import resident_price_baselines as price_refs

SCHEMA = 'heptatrader.laya-price-calibration.v1'
OUTPUT = 'FROZEN_SAME_PRICE_TRAIN_ROWS_CALIBRATION_V1'
SPEC = {
    'method': 'SOFTENED_LOG_PROBABILITIES_PLUS_CLASS_BIAS',
    'fit_population': 'SAME_UNIQUE_PRICE_TRAINING_ROWS_AS_CHILD_AND_BASELINES',
    'raw_probability_floor': 1e-12,
    'laplace_pseudocount_per_class': 1.,
    'optimizer': 'PROJECTED_FULL_BATCH_FLOAT64_GRADIENT_DESCENT',
    'steps': 256, 'learning_rate': .05,
    'logit_scale_bounds': [.25, 1.], 'bias_bounds': [-6., 6.],
    'l2_bias': .01, 'l2_scale_toward_one': .01,
    'initial_logit_scale': 1.,
    'initial_bias': 'LOG_LAPLACE_PRIOR_MINUS_LOG_MEAN_RAW_PROBABILITY',
    'checkpoint_selection': 'FIXED_FINAL_NO_FUTURE_SELECTION',
}
_SHA = re.compile(r'^[0-9a-f]{64}$')


def _need(ok, reason):
    if not ok:
        raise ContractError(reason)


def _probabilities(values):
    _need(isinstance(values, list) and len(values) == 7
          and all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1
                  for p in values)
          and abs(sum(values)-1) <= 1e-5, 'PRICE_CALIBRATION_RAW_PROBABILITIES')
    clipped = [max(SPEC['raw_probability_floor'], float(p)) for p in values]
    total = sum(clipped)
    return [p/total for p in clipped]


def _softmax(value):
    import numpy as np
    shifted = value-value.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp/exp.sum(axis=-1, keepdims=True)


def _fit_population(rows, raw_probabilities, *, cutoff_ms, parent_id,
                    adapter_sha256, price_baselines, probability_reader=_probabilities):
    import numpy as np
    baseline = price_refs.validate(price_baselines)
    n = len(rows)
    _need(MIN_TRAINING_ROWS <= n <= MAX_TRAINING_ROWS and
          isinstance(raw_probabilities, list) and len(raw_probabilities) == n,
          'PRICE_CALIBRATION_TRAINING_POPULATION')
    _need(type(cutoff_ms) is int and cutoff_ms >= 0 and
          isinstance(parent_id, str) and _SHA.fullmatch(parent_id) is not None and
          isinstance(adapter_sha256, str) and _SHA.fullmatch(adapter_sha256) is not None,
          'PRICE_CALIBRATION_CONTEXT')
    ids = [row_id(row) for row in rows]
    _need(len(set(ids)) == n and baseline['training_rows'] == n and
          baseline['training_cutoff_ms'] == cutoff_ms and
          baseline['training_row_ids_digest'] == digest(sorted(ids)),
          'PRICE_CALIBRATION_BASELINE_POPULATION_CHANGED')
    for row in rows:
        _need(type(row.get('target_class')) is int and 0 <= row['target_class'] < 7
              and type(row.get('outcome_available_at_ms')) is int
              and row['anchor_ms'] < row['outcome_available_at_ms'] <= cutoff_ms,
              'PRICE_CALIBRATION_NONCAUSAL_TRAINING_ROW')
    raw = np.asarray([probability_reader(v) for v in raw_probabilities], dtype=np.float64)
    target = np.asarray([row['target_class'] for row in rows], dtype=np.int64)
    return n, ids, raw, target, baseline


def fit(rows, raw_probabilities, *, cutoff_ms, parent_id, adapter_sha256,
        price_baselines):
    """No extra inference: probabilities are from the final child checkpoint."""
    import numpy as np
    n, ids, raw, target, baseline = _fit_population(rows, raw_probabilities,
        cutoff_ms=cutoff_ms, parent_id=parent_id, adapter_sha256=adapter_sha256,
        price_baselines=price_baselines)
    counts = np.bincount(target, minlength=7)
    prior = (counts+1)/(n+7)
    logp = np.log(raw)
    bias = np.log(prior)-np.log(raw.mean(axis=0))
    bias -= bias.mean()
    bias = np.clip(bias, *SPEC['bias_bounds'])
    scale = SPEC['initial_logit_scale']
    # One fixed Laplace count per class is a declared regularizer, never an
    # invented observation or an increase in the unique-row accounting.
    targets = (np.eye(7, dtype=np.float64)[target]*n+1)/(n+7)
    for _ in range(SPEC['steps']):
        residual = _softmax(scale*logp+bias)-targets
        bias -= SPEC['learning_rate']*(residual.mean(axis=0)+SPEC['l2_bias']*bias)
        bias = np.clip(bias, *SPEC['bias_bounds'])
        gradient = float((residual*logp).sum(axis=1).mean())
        gradient += SPEC['l2_scale_toward_one']*(scale-1)
        scale = float(np.clip(scale-SPEC['learning_rate']*gradient,
                              *SPEC['logit_scale_bounds']))
    result = {
        'schema': SCHEMA, 'output_profile': OUTPUT, 'fit_spec': deepcopy(SPEC),
        'parent_id': parent_id, 'adapter_sha256': adapter_sha256,
        'training_cutoff_ms': cutoff_ms, 'training_rows': n,
        'training_row_ids_digest': digest(sorted(ids)),
        'price_baseline_profile_id': baseline['profile_id'],
        'raw_probability_receipt_digest': digest([
            {'row_id': key, 'raw_probs': value}
            for key, value in zip(ids, raw_probabilities)]),
        'real_target_class_counts': counts.tolist(),
        'logit_scale': scale, 'bias': bias.tolist(),
        'raw_logits_scope': 'ACTUAL_FINAL_CHILD_PRICE_TRAINING_FORWARD_PASSES',
        'calibration_is_train_only': True, 'future_value_proven': False,
        'additional_independent_training_rows': 0,
        'model_or_trading_authority': False,
    }
    result['profile_id'] = digest(result)
    return validate(result)


def validate(profile):
    if isinstance(profile, dict) and profile.get('schema') == 'heptatrader.laya-price-calibration.v3':
        from . import resident_price_calibration_v3 as v3
        return v3.validate(profile)
    if isinstance(profile, dict) and profile.get('schema') == 'heptatrader.laya-price-calibration.v2':
        from . import resident_price_calibration_v2 as v2
        return v2.validate(profile)
    _need(isinstance(profile, dict) and profile.get('schema') == SCHEMA
          and profile.get('output_profile') == OUTPUT and profile.get('fit_spec') == SPEC
          and profile.get('profile_id') == digest({k: v for k, v in profile.items()
                                                 if k != 'profile_id'})
          and profile.get('raw_logits_scope') ==
              'ACTUAL_FINAL_CHILD_PRICE_TRAINING_FORWARD_PASSES'
          and profile.get('calibration_is_train_only') is True
          and profile.get('future_value_proven') is False
          and profile.get('additional_independent_training_rows') == 0
          and profile.get('model_or_trading_authority') is False,
          'PRICE_CALIBRATION_FROZEN_PROFILE')
    for key in ('parent_id', 'adapter_sha256', 'training_row_ids_digest',
                'price_baseline_profile_id', 'raw_probability_receipt_digest'):
        _need(isinstance(profile.get(key), str) and _SHA.fullmatch(profile[key]) is not None,
              'PRICE_CALIBRATION_CONTEXT_DIGEST')
    n = profile.get('training_rows')
    counts = profile.get('real_target_class_counts')
    _need(type(n) is int and MIN_TRAINING_ROWS <= n <= MAX_TRAINING_ROWS
          and isinstance(counts, list) and len(counts) == 7
          and all(type(k) is int and k >= 0 for k in counts) and sum(counts) == n
          and type(profile.get('training_cutoff_ms')) is int
          and profile['training_cutoff_ms'] >= 0, 'PRICE_CALIBRATION_COUNTS')
    scale, bias = profile.get('logit_scale'), profile.get('bias')
    _need(type(scale) in (int, float) and math.isfinite(scale)
          and SPEC['logit_scale_bounds'][0] <= scale <= SPEC['logit_scale_bounds'][1]
          and isinstance(bias, list) and len(bias) == 7
          and all(type(v) in (int, float) and math.isfinite(v)
                  and SPEC['bias_bounds'][0] <= v <= SPEC['bias_bounds'][1] for v in bias),
          'PRICE_CALIBRATION_PARAMETERS')
    return profile


def apply(profile, raw_probabilities):
    if isinstance(profile, dict) and profile.get('schema') == 'heptatrader.laya-price-calibration.v3':
        from . import resident_price_calibration_v3 as v3
        return v3.apply(profile, raw_probabilities)
    if isinstance(profile, dict) and profile.get('schema') == 'heptatrader.laya-price-calibration.v2':
        from . import resident_price_calibration_v2 as v2
        return v2.apply(profile, raw_probabilities)
    import numpy as np
    p = validate(profile)
    raw = np.asarray(_probabilities(raw_probabilities), dtype=np.float64)
    return _softmax(p['logit_scale']*np.log(raw)+np.asarray(p['bias'])).tolist()


def verify_prediction(plan, prediction):
    """Rebuild the frozen transform from the committed raw future probability."""
    declared = plan.get('price_output_profile')
    if declared is None:
        _need(prediction.get('price_output_profile') is None,
              'UNREGISTERED_PRICE_CALIBRATION')
        return None
    from . import resident_price_calibration_v2 as v2
    from . import resident_price_calibration_v3 as v3
    _need(declared in (OUTPUT, v2.OUTPUT, v3.OUTPUT), 'UNKNOWN_PRICE_OUTPUT_PROFILE')
    if declared == v3.OUTPUT:
        _need(plan.get('price_input_objective') == 'heptatrader.laya-resident-objective.v8',
              'PRICE_CALIBRATION_V3_OBJECTIVE_REQUIRED')
    p = validate(plan.get('price_calibration'))
    _need(p.get('raw_logits_scope') == 'ACTUAL_FINAL_CHILD_PRICE_TRAINING_FORWARD_PASSES',
          'CHILD_PRICE_CALIBRATION_SCOPE_CHANGED')
    baseline = price_refs.validate(plan.get('price_baselines'))
    _need(plan.get('price_calibration_profile_id') == p['profile_id']
          and prediction.get('price_calibration_profile_id') == p['profile_id']
          and p['output_profile'] == declared
          and prediction.get('price_output_profile') == declared
          and plan.get('parent_id') == prediction.get('parent_id') == p['parent_id']
          and prediction.get('trained_until_ms') == p['training_cutoff_ms']
          and baseline['profile_id'] == p['price_baseline_profile_id']
          and baseline['training_row_ids_digest'] == p['training_row_ids_digest']
          and baseline['training_cutoff_ms'] == p['training_cutoff_ms'],
          'PRICE_CALIBRATION_PREDICTION_CONTEXT_CHANGED')
    raw = prediction.get('candidate_raw_probs')
    expected = apply(p, raw)
    _probabilities(prediction.get('candidate_probs'))
    reported = prediction['candidate_probs']
    _need(all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
              for a, b in zip(expected, reported)), 'PRICE_CALIBRATION_FORECAST_CHANGED')
    return p


def training_measurement(profile, raw_probabilities, rows):
    p = validate(profile)
    _need(len(rows) == p['training_rows'] == len(raw_probabilities) and
          digest(sorted(row_id(r) for r in rows)) == p['training_row_ids_digest'],
          'PRICE_CALIBRATION_MEASUREMENT_POPULATION')
    scores = {'raw_child': [], 'calibrated_child': []}
    for raw, row in zip(raw_probabilities, rows):
        scores['raw_child'].append(price_refs.probability_metrics(raw, row['target_class']))
        scores['calibrated_child'].append(price_refs.probability_metrics(
            apply(p, raw), row['target_class']))
    return {'training_only': True, 'held_out': False, 'future_value_proven': False,
            'rows': len(rows), 'additional_independent_rows': 0,
            'models': {name: {
                'mean_brier': sum(v['brier'] for v in values)/len(values),
                'mean_nll': (sum(v['nll'] for v in values)/len(values)
                             if all(v['nll'] is not None for v in values) else None),
                'hits': sum(v['hit'] for v in values)}
                for name, values in scores.items()}}


def training_rows_for_job(job):
    """Read the pinned population once for validators in one verification call."""
    from pathlib import Path
    from .integrity import read_json, sha_file
    from .dataset import rows_for_training
    from .resident_training_v2 import timed_rows
    path = Path(job['dataset'])
    _need(sha_file(path) == job['dataset_sha256'], 'PRICE_CALIBRATION_DATASET_CHANGED')
    dataset = read_json(path, 64*1024**2)
    rows, _ = rows_for_training(dataset, job['window'])
    rows = timed_rows(rows, dataset)
    wanted = set(job['training_row_ids'])
    rows = [row for row in rows if row['horizon_sec'] == 300 and row_id(row) in wanted]
    _need(len(rows) == len(wanted) == len(job['training_row_ids']),
          'PRICE_CALIBRATION_JOB_POPULATION_CHANGED')
    return rows


def verify_candidate_fit(job, candidate, *, rows=None):
    """Rebuild CPU fit from pinned real labels and recorded final-child vectors.

    Standalone calls reread the pinned dataset. A caller validating multiple
    receipts together may pass its own just-verified population; it is never a
    cache across jobs, processes or independent verification calls.
    """
    if rows is None:
        rows = training_rows_for_job(job)
    _need(len(rows) == len(job['training_row_ids'])
          and {row_id(row) for row in rows} == set(job['training_row_ids']),
          'PRICE_CALIBRATION_JOB_POPULATION_CHANGED')
    receipts = candidate.get('final_price_raw_forward_receipts')
    _need(isinstance(receipts, list) and len(receipts) == len(rows),
          'PRICE_CALIBRATION_FORWARD_RECEIPTS_REQUIRED')
    for row, value in zip(rows, receipts):
        _need(isinstance(value, dict) and value.get('training_row_id') == row_id(row)
              and all(value.get(k) == row[k] for k in
                      ('target_class', 'anchor_ms', 'outcome_available_at_ms')),
              'PRICE_CALIBRATION_FORWARD_ROW_CHANGED')
    objective_schema = job.get('objective', {}).get('schema')
    if objective_schema in ('heptatrader.laya-resident-objective.v7', 'heptatrader.laya-resident-objective.v8'):
        from . import resident_price_input as price_input
        price_input.check_bindings(candidate)
        expected_input = price_input.RAW if objective_schema.endswith('.v8') else price_input.COMPACT
        for row, value in zip(rows, receipts):
            _need(value.get('price_input_profile') == expected_input
                  and value.get('price_input_id') == digest(
                      price_input.model_state(row['state'], expected_input)),
                  'PRICE_CALIBRATION_V8_INPUT_RECEIPT_CHANGED' if objective_schema.endswith('.v8') else
                  'PRICE_CALIBRATION_V7_INPUT_RECEIPT_CHANGED')
    raw = [value['raw_probs'] for value in receipts]
    fit_function, measure_function = fit, training_measurement
    if isinstance(candidate.get('price_calibration'),dict) and candidate['price_calibration'].get('schema') == 'heptatrader.laya-price-calibration.v3':
        from . import resident_price_calibration_v3 as policy
        _need(objective_schema == 'heptatrader.laya-resident-objective.v8',
              'PRICE_CALIBRATION_V3_OBJECTIVE_REQUIRED')
        _need(candidate.get('price_output_profile') == policy.OUTPUT, 'UNKNOWN_PRICE_OUTPUT_PROFILE')
        fit_function, measure_function = policy.fit, policy.training_measurement
        from .resident_training_v6 import check_training_rows
        check_training_rows(rows, cutoff_ms=job['window']['training_end_ms'])
    if isinstance(candidate.get('price_calibration'),dict) and candidate['price_calibration'].get('schema') == 'heptatrader.laya-price-calibration.v2':
        from . import resident_price_calibration_v2 as policy
        _need(candidate.get('price_output_profile') == policy.OUTPUT, 'UNKNOWN_PRICE_OUTPUT_PROFILE')
        fit_function, measure_function = policy.fit, policy.training_measurement
        from .resident_training_v6 import check_training_rows
        check_training_rows(rows, cutoff_ms=job['window']['training_end_ms'])
    rebuilt = fit_function(rows, raw, cutoff_ms=job['window']['training_end_ms'],
                  parent_id=job['parent_id'], adapter_sha256=candidate['adapter_sha256'],
                  price_baselines=candidate['price_baselines'])
    _need(rebuilt == candidate.get('price_calibration'), 'PRICE_CALIBRATION_FIT_REPLAY_CHANGED')
    _need(measure_function(rebuilt, raw, rows) ==
          candidate.get('price_calibration_training_measurement'),
          'PRICE_CALIBRATION_TRAIN_MEASUREMENT_CHANGED')
    return rebuilt
