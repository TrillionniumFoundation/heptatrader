"""Opt-in future PRICE labels from exact ratios of stored binary64 midpoints.

Absence of FIELD preserves the legacy floating-point label contract. This
contract is carried by new datasets, jobs, candidates, plans and predictions;
neither a config reload nor an exact helper silently relabels old artifacts.
"""
from copy import deepcopy
import math
from pathlib import Path
import time

from .integrity import ContractError, digest, sha_file

FIELD = 'price_return_contract'
SCHEMA = 'heptatrader.price-return-contract.v2'
DATASET_SCHEMA = 'heptatrader.neuron-dataset.v2'
EDGES = (-10, -3, -1, 1, 3, 10)
SPEC = {'schema': SCHEMA,
        'arithmetic': 'EXACT_BINARY64_MID_RATIO_INTEGER_CROSS_PRODUCT',
        'bin_edges_bps': list(EDGES),
        'intervals': 'LOWER_INCLUSIVE_UPPER_EXCLUSIVE',
        'scaled_regression_target': 'LEGACY_PERCENT_RETURN_UNCHANGED'}
WITNESS = ('reference_mid_hex', 'outcome_mid_hex', 'return_bin_class')
ENDPOINT_FIELDS = ('plan_id', 'candidate_id', 'slot', 'anchor_ms', 'anchor_receipt',
                   'anchor_available_ms', 'reference_mid_hex', 'horizon_sec',
                   'outcome_due_ms', 'outcome_ms', 'outcome_available_ms',
                   'outcome_receipt', 'outcome_mid_hex', FIELD)
PINS = tuple('learn/'+name for name in (
    'data.py', 'laya_cell/resident_price_return.py', 'laya_cell/dataset.py',
    'laya_cell/resident_data.py', 'laya_cell/resident.py',
    'laya_cell/resident_train.py', 'laya_cell/resident_training_v8.py',
    'laya_cell/resident_price_calibration.py', 'laya_cell/resident_predict.py',
    'laya_cell/resident_evaluate.py', 'laya_cell/resident_plan_admission.py',
    'laya_cell/resident_price_input.py', 'laya_cell/resident_action_v2_closed_source.py',
    'laya_cell/resident_forward_attempt.py', 'laya_cell/promotion.py'))


def validate(value):
    keys = {*SPEC, 'registered_at_ms', 'effective_from_anchor_ms'}
    if (not isinstance(value, dict) or set(value) != keys
            or any(value.get(key) != expected for key, expected in SPEC.items())
            or type(value.get('bin_edges_bps')) is not list
            or any(type(edge) is not int for edge in value['bin_edges_bps'])
            or type(value.get('registered_at_ms')) is not int
            or type(value.get('effective_from_anchor_ms')) is not int
            or not 0 <= value['registered_at_ms'] < value['effective_from_anchor_ms']):
        raise ContractError('PRICE_RETURN_CONTRACT_CHANGED')
    return value


def for_record(record):
    if not isinstance(record, dict):
        raise ContractError('PRICE_RETURN_RECORD_REQUIRED')
    return validate(record[FIELD]) if FIELD in record else None


def matching(*records):
    values = [for_record(record) for record in records]
    if not values or any(value != values[0] for value in values[1:]):
        raise ContractError('PRICE_RETURN_CONTRACT_BINDING')
    return {FIELD: deepcopy(values[0])} if values[0] is not None else {}


def bind_rule(rule, record):
    """Bind the arithmetic inside the strictly validated qualification rule.

    Old validators reject this extra versioned field rather than accepting an
    exact plan while silently applying legacy arithmetic. Thresholds, slots,
    alpha and horizons retain their original registered values.
    """
    if FIELD in rule:
        raise ContractError('PRICE_RETURN_RULE_ALREADY_BOUND')
    return {**rule, **matching(record)}


def original_rule(plan):
    rule = plan.get('rule')
    if not isinstance(rule, dict):
        raise ContractError('PRICE_RETURN_RULE_REQUIRED')
    matching(plan, rule)
    return {key: value for key, value in rule.items() if key != FIELD}


def input_id(state, record):
    """Version the prediction identity while preserving the raw model input.

    Old direct measure() callers check digest(state), so they reject this v2
    identity before interpreting any outcome under their legacy arithmetic.
    """
    value = for_record(record)
    if value is None:
        return digest(state)
    return digest({'schema': 'heptatrader.laya-price-input-identity.v2',
                   'state': state, FIELD: value})


def check_config(config):
    value = for_record(config)
    if value is None:
        return None
    if config.get('objective', {}).get('schema') != 'heptatrader.laya-resident-objective.v8':
        raise ContractError('PRICE_RETURN_REQUIRES_V8_OBJECTIVE')
    for name in PINS:
        pin = config.get('runtime_sha256', {}).get(name)
        if not isinstance(pin, str) or len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin):
            raise ContractError('PRICE_RETURN_RUNTIME_PINS_REQUIRED')
    return value


def configure(config, runtime_directory, effective_from_anchor_ms, *, now_ms=None):
    """Explicit new-release opt-in. No live write or inference is performed."""
    now = int(time.time()*1000) if now_ms is None else now_ms
    value = {**deepcopy(SPEC), 'registered_at_ms': now,
             'effective_from_anchor_ms': effective_from_anchor_ms}
    validate(value)  # Reject a retrospective activation at construction time.
    result = deepcopy(config)
    if FIELD in result:
        raise ContractError('PRICE_RETURN_CONTRACT_ALREADY_FROZEN')
    result[FIELD] = value
    pins = result.setdefault('runtime_sha256', {})
    for name in PINS:
        pins[name] = sha_file(Path(runtime_directory)/name)
    check_config(result)
    return result


def check_anchor(value, anchor_ms):
    validate(value)
    if type(anchor_ms) is not int or anchor_ms < value['effective_from_anchor_ms']:
        raise ContractError('PRICE_RETURN_ANCHOR_BEFORE_ACTIVATION')


def _mid(value):
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise ContractError('PRICE_RETURN_BINARY64_MID_REQUIRED')
    return value


def classify(anchor_mid, future_mid):
    """Exact seven-bin ratio, using existing IEEE binary64 values as rationals.

    For anchor=a/b and future=c/d, test 10000*(c*b-a*d) >= k*a*d.
    Positive a*d preserves the inequality. Counting passed ordered boundaries
    yields (-inf,-10), [-10,-3), [-3,-1), [-1,1), [1,3), [3,10), [10,inf).
    There is no float division, decimal reinterpretation or epsilon widening.
    """
    a, b = _mid(anchor_mid).as_integer_ratio()
    c, d = _mid(future_mid).as_integer_ratio()
    numerator, denominator = 10000*(c*b-a*d), a*d
    return sum(numerator >= edge*denominator for edge in EDGES)


def witness(value, anchor_ms, anchor_mid, future_mid):
    check_anchor(value, anchor_ms)
    return {FIELD: deepcopy(value), 'reference_mid_hex': _mid(anchor_mid).hex(),
            'outcome_mid_hex': _mid(future_mid).hex(),
            'return_bin_class': classify(anchor_mid, future_mid)}


def _hex_mid(text):
    if not isinstance(text, str):
        raise ContractError('PRICE_RETURN_MID_WITNESS')
    try:
        value = _mid(float.fromhex(text))
    except (ValueError, OverflowError):
        raise ContractError('PRICE_RETURN_MID_WITNESS') from None
    if value.hex() != text:
        raise ContractError('PRICE_RETURN_MID_WITNESS')
    return value


def target_class(dataset, sample, horizon, outcome):
    """Decode v2 receipts; reject missing, mixed, altered or pre-activation ones."""
    value = for_record(dataset)
    matching(dataset, outcome)
    if value is None:
        raise ContractError('PRICE_RETURN_EXACT_RECEIPT_REQUIRED')
    check_anchor(value, sample['anchor_ms'])
    reference = _hex_mid(outcome.get('reference_mid_hex'))
    endpoint = _hex_mid(outcome.get('outcome_mid_hex'))
    if (reference != _mid(sample['reference_mid']) or outcome.get('horizon_sec') != horizon
            or outcome.get('target_scaled') != 10000*(endpoint/reference-1)/100):
        raise ContractError('PRICE_RETURN_RECEIPT_ENDPOINT_CHANGED')
    expected = classify(reference, endpoint)
    if type(outcome.get('return_bin_class')) is not int or outcome['return_bin_class'] != expected:
        raise ContractError('PRICE_RETURN_RECEIPT_CLASS_CHANGED')
    return expected


def verify_outcome(plan, outcome):
    """Keep already recorded legacy outcomes legacy; exact plans require proof."""
    matching(plan, outcome)
    value = for_record(plan)
    if value is None:
        return
    if (outcome.get('plan_id') != plan.get('plan_id')
            or outcome.get('candidate_id') != plan.get('candidate_id')
            or outcome.get('status') != 'OBSERVED'):
        raise ContractError('PRICE_RETURN_OUTCOME_PLAN_IDENTITY')
    check_anchor(value, outcome.get('anchor_ms'))
    rule = original_rule(plan)
    horizon = rule.get('horizon_ms')
    slot = outcome.get('slot')
    integer_fields = ('anchor_ms', 'anchor_available_ms', 'horizon_sec', 'outcome_due_ms',
                      'outcome_ms', 'outcome_available_ms', 'prediction_available_ms',
                      'trained_until_ms', 'evaluated_at_ms')
    if (type(horizon) is not int or horizon <= 0 or horizon % 1000
            or type(slot) is not int or not 0 <= slot < rule['slots']
            or type(plan.get('start_ms')) is not int
            or any(type(outcome.get(name)) is not int for name in integer_fields)):
        raise ContractError('PRICE_RETURN_OUTCOME_TIME_CONTRACT')
    anchor = outcome['anchor_ms']
    due = anchor+horizon
    start = plan['start_ms']+slot*rule['slot_spacing_ms']
    if (not start <= anchor <= start+rule['input_grace_ms']
            or outcome['horizon_sec'] != horizon//1000
            or outcome['outcome_due_ms'] != due
            or not due <= outcome['outcome_ms'] <= due+5000
            or not anchor <= outcome['anchor_available_ms'] <= outcome['prediction_available_ms'] < due
            or outcome['trained_until_ms'] > anchor
            or not outcome['outcome_ms'] <= outcome['outcome_available_ms'] <= outcome['evaluated_at_ms']
            or outcome.get('path_gap') is not False):
        raise ContractError('PRICE_RETURN_OUTCOME_TIME_CONTRACT')
    for name in ('anchor_receipt', 'outcome_receipt'):
        receipt = outcome.get(name)
        if (not isinstance(receipt, str) or len(receipt) != 64
                or any(c not in '0123456789abcdef' for c in receipt)):
            raise ContractError('PRICE_RETURN_OUTCOME_SOURCE_RECEIPT')
    if outcome.get('return_endpoint_receipt_id') != endpoint_receipt_id(outcome):
        raise ContractError('PRICE_RETURN_OUTCOME_ENDPOINT_BINDING')
    reference = _hex_mid(outcome.get('reference_mid_hex'))
    endpoint = _hex_mid(outcome.get('outcome_mid_hex'))
    expected = classify(reference, endpoint)
    if (type(outcome.get('target_class')) is not int or outcome['target_class'] != expected
            or type(outcome.get('return_bin_class')) is not int or outcome['return_bin_class'] != expected
            or outcome.get('return_bps') != (endpoint/reference-1)*10000
            or outcome.get('return_bps_scope') != 'ROUNDED_DISPLAY_ONLY_EXACT_RATIO_CLASS'):
        raise ContractError('PRICE_RETURN_OUTCOME_CHANGED')


def endpoint_receipt_id(outcome):
    """Bind recorded source references, bits and times, not a new source proof.

    The evaluator reads the original source; a later reader can check this
    immutable receipt and the frozen plan without pretending to reread it.
    """
    return digest({'schema': 'heptatrader.price-return-endpoints.v2',
                   **{name: outcome.get(name) for name in ENDPOINT_FIELDS}})
