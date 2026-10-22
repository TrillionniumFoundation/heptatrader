"""Centered train-only PRICE signal around the same real Laplace prior.

The bounded coefficient can retain only conditional variation: the mean fitted
output is the required prior. This is not held-out or future efficacy evidence.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from .integrity import ContractError, digest
from . import resident_price_baselines as refs
from .resident_row_accounting import MIN_TRAINING_ROWS, MAX_TRAINING_ROWS

SCHEMA = 'heptatrader.laya-price-calibration.v3'
OUTPUT = 'FROZEN_SAME_PRICE_TRAIN_ROWS_CENTERED_BRIER_V3'
SPEC = {
    'method': 'LAPLACE_TRAIN_PRIOR_PLUS_CENTERED_RAW_CHILD',
    'fit_population': 'SAME_UNIQUE_PRICE_TRAINING_ROWS_AS_CHILD_AND_BASELINES',
    'loss': 'MEAN_MULTICLASS_BRIER',
    'optimizer': 'FLOAT64_CLOSED_FORM_BOUNDED_QUADRATIC_MINIMUM',
    'centering': 'NORMALIZED_FINAL_CHILD_TRAINING_PROBABILITY_MEAN',
    'model_weight_upper_bound': 'MIN_ONE_AND_PRIOR_OVER_POSITIVE_TRAINING_MEAN',
    'simplex_bound_roundoff': 'NEXTAFTER_TOWARD_ZERO_MAX_8_ONLY_IF_NEGATIVE_INTERCEPT',
    'constant_training_probabilities': 'EXACT_FIRST_ROW_MEAN_ZERO_COVARIANCE_AND_VARIANCE',
    'constant_training_labels': 'EXACT_ZERO_COVARIANCE_WITH_REAL_VARIANCE_RETAINED',
    'zero_denominator_model_weight': 0.,
    'laplace_pseudocount_per_class': 1.,
    'raw_probability_handling': 'NORMALIZE_ROUNDOFF_ONLY_NO_FLOOR',
    'output_probability_handling': 'NONNEGATIVE_INTERCEPT_NO_CLIP_NORMALIZE_ROUNDOFF',
    'zero_weight_output': 'EXACT_REQUIRED_TRAIN_PRIOR',
    'checkpoint_selection': 'FIXED_SOLUTION_NO_FUTURE_SELECTION',
}
_SHA = re.compile(r'^[0-9a-f]{64}$')


def _need(ok, reason):
    if not ok:
        raise ContractError(reason)


def _probabilities(values):
    _need(isinstance(values, list) and len(values) == 7
          and all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1
                  for p in values) and abs(sum(values)-1) <= 1e-5,
          'PRICE_CALIBRATION_RAW_PROBABILITIES')
    total = sum(values)
    return [float(p)/total for p in values]


def _maximum_weight(prior, mean):
    # Ratios >= 1 cannot tighten the bound and need not divide tiny numbers.
    maximum = min([1.] + [p/m for p, m in zip(prior, mean) if m > p])
    for _ in range(8):
        if all(p-maximum*m >= 0 for p, m in zip(prior, mean)):
            return maximum
        maximum = math.nextafter(maximum, 0.)
    raise ContractError('PRICE_CALIBRATION_V3_SIMPLEX_BOUND_ROUNDOFF')


def fit(rows, raw_probabilities, *, cutoff_ms, parent_id, adapter_sha256,
        price_baselines):
    import numpy as np
    from .resident_price_calibration import _fit_population
    n, ids, raw, target, baseline = _fit_population(rows, raw_probabilities,
        cutoff_ms=cutoff_ms, parent_id=parent_id, adapter_sha256=adapter_sha256,
        price_baselines=price_baselines, probability_reader=_probabilities)
    counts = np.bincount(target, minlength=7)
    prior = (counts+1)/(n+7)
    if np.array_equal(raw, np.broadcast_to(raw[0], raw.shape)):
        # Repeated decimal probabilities can have a rounded np.mean different
        # from each identical row. They still contain no conditional signal.
        mean = raw[0].copy()
        numerator = denominator = 0.
    else:
        mean = raw.mean(axis=0)
        residual = raw-mean
        numerator = float((residual*(np.eye(7)[target]-prior)).sum())
        denominator = float((residual*residual).sum())
        # Centered residuals sum to zero, so one constant target cannot carry
        # covariance. Do not allocate a trial for a rounded positive zero.
        if np.all(target == target[0]):
            numerator = 0.
    maximum = _maximum_weight(prior.tolist(), mean.tolist())
    weight = min(maximum, max(0., numerator/denominator)) if denominator > 0 else 0.
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
        'train_prior_probs': prior.tolist(), 'mean_training_raw_probs': mean.tolist(),
        'model_weight': weight, 'model_weight_max': maximum,
        'quadratic_numerator': numerator, 'quadratic_denominator': denominator,
        'raw_logits_scope': 'ACTUAL_FINAL_CHILD_PRICE_TRAINING_FORWARD_PASSES',
        'calibration_is_train_only': True, 'future_value_proven': False,
        'additional_independent_training_rows': 0, 'model_or_trading_authority': False,
    }
    result['profile_id'] = digest(result)
    return validate(result)


def validate(profile):
    _need(isinstance(profile, dict) and profile.get('schema') == SCHEMA
          and profile.get('output_profile') == OUTPUT and profile.get('fit_spec') == SPEC
          and profile.get('raw_logits_scope') == 'ACTUAL_FINAL_CHILD_PRICE_TRAINING_FORWARD_PASSES'
          and profile.get('calibration_is_train_only') is True
          and profile.get('future_value_proven') is False
          and profile.get('additional_independent_training_rows') == 0
          and profile.get('model_or_trading_authority') is False,
          'PRICE_CALIBRATION_V3_FROZEN_PROFILE')
    for key in ('parent_id', 'adapter_sha256', 'training_row_ids_digest',
                'price_baseline_profile_id', 'raw_probability_receipt_digest'):
        _need(isinstance(profile.get(key), str) and _SHA.fullmatch(profile[key]) is not None,
              'PRICE_CALIBRATION_CONTEXT_DIGEST')
    n, counts = profile.get('training_rows'), profile.get('real_target_class_counts')
    _need(type(n) is int and MIN_TRAINING_ROWS <= n <= MAX_TRAINING_ROWS
          and isinstance(counts, list) and len(counts) == 7
          and all(type(k) is int and k >= 0 for k in counts) and sum(counts) == n
          and type(profile.get('training_cutoff_ms')) is int
          and profile['training_cutoff_ms'] >= 0, 'PRICE_CALIBRATION_COUNTS')
    prior = [(k+1)/(n+7) for k in counts]
    _need(profile.get('train_prior_probs') == prior, 'PRICE_CALIBRATION_V3_PRIOR_CHANGED')
    mean = profile.get('mean_training_raw_probs')
    _need(isinstance(mean, list) and len(mean) == 7
          and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in mean)
          and abs(sum(mean)-1) <= 1e-12, 'PRICE_CALIBRATION_V3_TRAINING_MEAN')
    a, b = profile.get('quadratic_numerator'), profile.get('quadratic_denominator')
    weight, maximum = profile.get('model_weight'), profile.get('model_weight_max')
    _need(all(type(v) in (int, float) and math.isfinite(v) for v in (a, b, weight, maximum))
          and b >= 0 and 0 <= weight <= maximum <= 1,
          'PRICE_CALIBRATION_V3_PARAMETERS')
    _need(maximum == _maximum_weight(prior, mean), 'PRICE_CALIBRATION_V3_BOUND_CHANGED')
    expected = min(maximum, max(0., a/b)) if b > 0 else 0.
    _need(weight == expected and (b != 0 or a == 0), 'PRICE_CALIBRATION_V3_SOLUTION_CHANGED')
    _need(sum(k > 0 for k in counts) != 1 or a == 0.,
          'PRICE_CALIBRATION_V3_CONSTANT_LABEL_COVARIANCE')
    _need(profile.get('profile_id') == digest({k: v for k, v in profile.items() if k != 'profile_id'}),
          'PRICE_CALIBRATION_V3_FROZEN_PROFILE')
    return profile


def apply(profile, raw_probabilities):
    p = validate(profile)
    raw = _probabilities(raw_probabilities)
    weight = p['model_weight']
    if weight == 0:
        return p['train_prior_probs'][:]
    values = [prior-weight*mean+weight*value
              for prior, mean, value in zip(p['train_prior_probs'], p['mean_training_raw_probs'], raw)]
    # The conservative bound makes every intercept nonnegative. Refuse rather
    # than clip any invalid output; normalization handles sum roundoff only.
    total = sum(values)
    _need(all(math.isfinite(v) and v >= 0 for v in values) and abs(total-1) <= 1e-5,
          'PRICE_CALIBRATION_V3_OUTPUT_PROBABILITIES')
    return [v/total for v in values]


def training_measurement(profile, raw_probabilities, rows):
    from .resident_price_calibration import training_measurement as measurement
    result = measurement(profile, raw_probabilities, rows)
    p = validate(profile)
    for name, probabilities in (('train_prior', p['train_prior_probs']),
                                 ('uniform', [1/7]*7)):
        values = [refs.probability_metrics(probabilities, r['target_class']) for r in rows]
        result['models'][name] = {
            'mean_brier': sum(v['brier'] for v in values)/len(values),
            'mean_nll': sum(v['nll'] for v in values)/len(values),
            'hits': sum(v['hit'] for v in values)}
    result.update(model_weight=p['model_weight'],
                  exact_prior_fallback=p['model_weight'] == 0,
                  fit_objective=SPEC['loss'])
    return result


def verify_candidate_fit(job, candidate):
    from .resident_price_calibration import verify_candidate_fit as verify
    _need(candidate.get('price_output_profile') == OUTPUT, 'PRICE_CALIBRATION_V3_OUTPUT_CHANGED')
    return verify(job, candidate)


def verify_prediction(plan, prediction):
    from .resident_price_calibration import verify_prediction as verify
    _need(plan.get('price_output_profile') == OUTPUT, 'PRICE_CALIBRATION_V3_OUTPUT_CHANGED')
    return verify(plan, prediction)
