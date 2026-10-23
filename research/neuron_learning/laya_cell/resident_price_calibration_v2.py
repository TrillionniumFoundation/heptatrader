"""Prospective Brier projection between the real train prior and raw child.

One bounded coefficient has a closed-form optimum. A zero coefficient means
exactly the required prior baseline, never evidence of model value. These are
the child's own training rows, not held-out calibration or future observations.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from .integrity import ContractError, digest
from . import resident_price_baselines as refs
from .resident_row_accounting import MIN_TRAINING_ROWS, MAX_TRAINING_ROWS

SCHEMA = 'heptatrader.laya-price-calibration.v2'
OUTPUT = 'FROZEN_SAME_PRICE_TRAIN_ROWS_BRIER_BLEND_V2'
SPEC = {
    'method': 'CONVEX_RAW_CHILD_AND_LAPLACE_TRAIN_PRIOR',
    'fit_population': 'SAME_UNIQUE_PRICE_TRAINING_ROWS_AS_CHILD_AND_BASELINES',
    'loss': 'MEAN_MULTICLASS_BRIER',
    'optimizer': 'FLOAT64_CLOSED_FORM_BOUNDED_QUADRATIC_MINIMUM',
    'model_weight_bounds': [0., 1.],
    'zero_denominator_model_weight': 0.,
    'laplace_pseudocount_per_class': 1.,
    'raw_probability_handling': 'NORMALIZE_ROUNDOFF_ONLY_NO_FLOOR',
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


def fit(rows, raw_probabilities, *, cutoff_ms, parent_id, adapter_sha256,
        price_baselines):
    import numpy as np
    from .resident_price_calibration import _fit_population
    n, ids, raw, target, baseline = _fit_population(rows, raw_probabilities,
        cutoff_ms=cutoff_ms, parent_id=parent_id, adapter_sha256=adapter_sha256,
        price_baselines=price_baselines, probability_reader=_probabilities)
    counts = np.bincount(target, minlength=7)
    prior = (counts+1)/(n+7)
    difference = raw-prior
    numerator = float((difference*(np.eye(7)[target]-prior)).sum())
    denominator = float((difference*difference).sum())
    weight = float(np.clip(numerator/denominator, 0., 1.)) if denominator > 0 else 0.
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
        'train_prior_probs': prior.tolist(), 'model_weight': weight,
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
          and profile.get('profile_id') == digest({k: v for k, v in profile.items()
                                                  if k != 'profile_id'})
          and profile.get('raw_logits_scope') == 'ACTUAL_FINAL_CHILD_PRICE_TRAINING_FORWARD_PASSES'
          and profile.get('calibration_is_train_only') is True
          and profile.get('future_value_proven') is False
          and profile.get('additional_independent_training_rows') == 0
          and profile.get('model_or_trading_authority') is False,
          'PRICE_CALIBRATION_V2_FROZEN_PROFILE')
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
    _need(profile.get('train_prior_probs') == prior, 'PRICE_CALIBRATION_V2_PRIOR_CHANGED')
    a, b = profile.get('quadratic_numerator'), profile.get('quadratic_denominator')
    weight = profile.get('model_weight')
    _need(all(type(v) in (int, float) and math.isfinite(v) for v in (a, b, weight))
          and b >= 0 and 0 <= weight <= 1, 'PRICE_CALIBRATION_V2_PARAMETERS')
    expected = min(1., max(0., a/b)) if b > 0 else 0.
    _need(weight == expected and (b != 0 or a == 0), 'PRICE_CALIBRATION_V2_SOLUTION_CHANGED')
    return profile


def apply(profile, raw_probabilities):
    p = validate(profile)
    raw = _probabilities(raw_probabilities)
    weight = p['model_weight']
    return [(1-weight)*prior+weight*value
            for prior, value in zip(p['train_prior_probs'], raw)]


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
    _need(candidate.get('price_output_profile') == OUTPUT,
          'PRICE_CALIBRATION_V2_OUTPUT_CHANGED')
    return verify(job, candidate)


def verify_prediction(plan, prediction):
    from .resident_price_calibration import verify_prediction as verify
    _need(plan.get('price_output_profile') == OUTPUT,
          'PRICE_CALIBRATION_V2_OUTPUT_CHANGED')
    return verify(plan, prediction)
