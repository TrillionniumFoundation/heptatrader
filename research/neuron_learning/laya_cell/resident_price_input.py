"""Versioned causal PRICE text; raw immutable quotes remain the source of truth.

No fitted statistics, targets, outcomes, model, or Torch are used here. ACT keeps
the original raw quote input even when PRICE uses the compact representation.
"""
from copy import deepcopy
import math

from .integrity import ContractError, digest
from .resident_price_baselines import FEATURES, features

RAW = 'LEGACY_RAW_CAUSAL_QUOTES_V1'
COMPACT = 'COMPACT_CAUSAL_FINANCIAL_FEATURES10_V1'
OBJECTIVE_SCHEMA = 'heptatrader.laya-resident-objective.v7'
V8_OBJECTIVE_SCHEMA = 'heptatrader.laya-resident-objective.v8'
SPEC = {
    'schema': 'heptatrader.laya-price-input.v1',
    'profile': COMPACT, 'features': list(FEATURES),
    'feature_source': 'SEALED_CAUSAL_QUOTE_STATE_ONLY',
    'decimal_places': 6, 'fitted_statistics': False,
    'target_or_outcome_input': False, 'raw_receipt_preserved': True,
    'action_input_profile': RAW,
}
RAW_SPEC = {
    'schema': 'heptatrader.laya-price-input.v1',
    'profile': RAW, 'rendering': 'ORIGINAL_ORDERED_CAUSAL_QUOTE_STATE',
    'feature_source': 'SEALED_CAUSAL_QUOTE_STATE_ONLY',
    'fitted_statistics': False, 'target_or_outcome_input': False,
    'raw_receipt_preserved': True, 'action_input_profile': RAW,
}
FIELDS = ('price_input_profile', 'parent_price_input_profile', 'action_input_profile')


def check_profile(profile):
    if profile not in (RAW, COMPACT):
        raise ContractError('UNKNOWN_PRICE_INPUT_PROFILE')
    return profile


def model_state(state, profile):
    from .dataset import CAUSAL_STATE_FIELDS
    check_profile(profile)
    if not isinstance(state, dict) or set(state) != set(CAUSAL_STATE_FIELDS):
        raise ContractError('PRICE_MODEL_STATE_FIELDS_CHANGED')
    ordered = {name: state[name] for name in CAUSAL_STATE_FIELDS}
    if profile == RAW:
        return ordered
    # The existing baseline's exact ten causal formulas, without its fitted
    # standardization. Units match the fixed seven return-interval options.
    values = features(ordered)
    return {'instrument': state['instrument'], 'anchor_ms': state['anchor_ms'],
            'horizon_sec': state['horizon_sec'],
            'features': {name: round(value, SPEC['decimal_places'])
                         for name, value in zip(FEATURES, values)},
            'quantity': 'underlying MID return; not execution profit'}


def for_candidate(candidate):
    objective = candidate.get('objective', {})
    known = {'heptatrader.laya-objective.v1',
             *(f'heptatrader.laya-resident-objective.v{i}' for i in range(1, 9))}
    if (not isinstance(objective, dict)
            or ('schema' in objective and objective['schema'] not in known)):
        raise ContractError('UNKNOWN_PARENT_PRICE_INPUT_OBJECTIVE')
    v7 = isinstance(objective, dict) and objective.get('schema') == OBJECTIVE_SCHEMA
    v8 = isinstance(objective, dict) and objective.get('schema') == V8_OBJECTIVE_SCHEMA
    profile = candidate.get('price_input_profile', RAW)
    check_profile(profile)
    if v7:
        if (profile != COMPACT or candidate.get('price_input_spec') != SPEC
                or candidate.get('action_input_profile') != RAW):
            raise ContractError('V7_CANDIDATE_PRICE_INPUT_PROFILE')
    elif v8:
        check_bindings(candidate)
        if (candidate.get('price_input_objective') != V8_OBJECTIVE_SCHEMA
                or profile != RAW or candidate.get('price_input_spec') != RAW_SPEC
                or candidate.get('action_input_profile') != RAW):
            raise ContractError('V8_CANDIDATE_PRICE_INPUT_PROFILE')
    elif profile != RAW:
        raise ContractError('LEGACY_CANDIDATE_PRICE_INPUT_PROFILE')
    return profile


def _profile_spec(objective_schema):
    if objective_schema == OBJECTIVE_SCHEMA:
        return COMPACT, SPEC, 'V7'
    if objective_schema == V8_OBJECTIVE_SCHEMA:
        return RAW, RAW_SPEC, 'V8'
    raise ContractError('UNKNOWN_PRICE_INPUT_OBJECTIVE')


def bindings(parent, *, objective_schema=OBJECTIVE_SCHEMA):
    profile, spec, _ = _profile_spec(objective_schema)
    return {'price_input_objective': objective_schema,
            'price_input_profile': profile,
            'parent_price_input_profile': for_candidate(parent),
            'action_input_profile': RAW, 'price_input_spec': deepcopy(spec)}


def check_bindings(record):
    if record.get('price_input_objective') not in (OBJECTIVE_SCHEMA, V8_OBJECTIVE_SCHEMA):
        # Preserve the existing malformed-v7 rejection, including its reason.
        raise ContractError('V7_PRICE_INPUT_BINDING')
    profile, spec, version = _profile_spec(record.get('price_input_objective'))
    if (record.get('price_input_profile') != profile
            or record.get('price_input_spec') != spec
            or record.get('action_input_profile') != RAW):
        raise ContractError(version + '_PRICE_INPUT_BINDING')
    check_profile(record.get('parent_price_input_profile'))
    return {key: deepcopy(record[key]) for key in
            ('price_input_objective', *FIELDS, 'price_input_spec')}


def receipt(state, record):
    bound = check_bindings(record)
    if bound['price_input_objective'] == V8_OBJECTIVE_SCHEMA:
        # The explicit RAW contract preserves the original state bytes. Reuse
        # the already required baseline's causal/finite validation only; its
        # fitted transforms and label statistics never enter the model input.
        features(state)
    return {**bound,
            'candidate_price_input_id': digest(model_state(state, bound['price_input_profile'])),
            'parent_price_input_id': digest(model_state(state, bound['parent_price_input_profile'])),
            'action_input_id': digest(model_state(state, RAW))}


def verify_prediction(plan, prediction):
    declared = ('price_input_objective', *FIELDS, 'price_input_spec')
    if not any(key in plan or key in prediction for key in declared):
        return None
    expected = receipt(prediction.get('input'), plan)
    if any(prediction.get(key) != value for key, value in expected.items()):
        _, _, version = _profile_spec(plan.get('price_input_objective'))
        raise ContractError(version + '_PRICE_INPUT_PREDICTION_CHANGED')
    return expected


def verify_committed_input(plan, record, prediction=None):
    expected = receipt(record.get('state'), plan)
    _, _, version = _profile_spec(plan.get('price_input_objective'))
    sample = record.get('sample', {})
    state = record['state']
    if not isinstance(sample, dict):
        raise ContractError(version + '_COMMITTED_PRICE_INPUT_CHANGED')
    try:
        sample_reference = float(sample['reference_mid'])
        state_reference = float(state['reference_mid'])
        reference_matches = (math.isfinite(sample_reference) and math.isfinite(state_reference)
                             and math.isclose(sample_reference, state_reference,
                                              rel_tol=1e-12, abs_tol=1e-12))
    except (KeyError, ValueError, TypeError):
        reference_matches = False
    if (record.get('plan_id') != plan.get('plan_id')
            or record.get('candidate_id') != plan.get('candidate_id')
            or record.get('input_id') != digest(record.get('state'))
            or sample.get('anchor_ms') != state['anchor_ms']
            or not reference_matches
            or any(record.get(key) != value for key, value in expected.items())):
        raise ContractError(version + '_COMMITTED_PRICE_INPUT_CHANGED')
    if prediction is not None:
        verify_prediction(plan, prediction)
        if (record.get('slot') != prediction.get('slot')
                or record.get('state') != prediction.get('input')
                or record.get('input_id') != prediction.get('input_id')
                or prediction.get('anchor_ms') != state['anchor_ms']
                or prediction.get('reference_mid') != sample.get('reference_mid')
                or prediction.get('sample_id') != sample.get('sample_id')):
            raise ContractError(version + '_COMMITTED_PRICE_INPUT_CHANGED')
    return expected
