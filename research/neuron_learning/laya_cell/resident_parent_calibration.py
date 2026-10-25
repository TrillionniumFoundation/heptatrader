"""Prospective, same-row parent calibration comparison without another forward.

The optional declaration belongs to new jobs and immutable plans. Historical
plans retain their original outputs and qualification rules. The comparison is
descriptive; it does not silently change selection or trading authority.
"""
from copy import deepcopy
import math
from pathlib import Path

from .integrity import ContractError, digest, sha_file, validate_candidate_artifact
from .resident_row_accounting import row_id
from . import resident_price_calibration_v3 as calibration
from . import resident_price_baselines as baselines
from . import resident_price_input as price_input

FIELD = 'parent_calibration_comparator_profile'
PROFILE = 'FROZEN_PARENT_SAME_ROWS_CENTERED_BRIER_V1'
RECORD = 'parent_calibration_comparator'
SCHEMA = 'heptatrader.parent-calibration-comparator.v1'
RUNTIME_PINS = ('learn/laya_cell/resident_parent_calibration.py',
    'learn/laya_cell/resident_price_calibration_v3.py',
    'learn/laya_cell/resident_train.py', 'learn/laya_cell/resident_predict.py',
    'learn/laya_cell/resident_evaluate.py', 'learn/laya_cell/promotion.py',
    'learn/laya_cell/resident.py', 'learn/laya_cell/resident_plan_admission.py')


def enabled(value):
    declared = value.get(FIELD)
    if declared is None:
        return False
    if declared != PROFILE:
        raise ContractError('PARENT_CALIBRATION_PROFILE_CHANGED')
    return True


def check_config(config):
    if not enabled(config):
        return False
    if config.get('objective', {}).get('schema') != 'heptatrader.laya-resident-objective.v8':
        raise ContractError('PARENT_CALIBRATION_REQUIRES_V8')
    if any(not isinstance(config.get('runtime_sha256', {}).get(name), str)
           or len(config['runtime_sha256'][name]) != 64 for name in RUNTIME_PINS):
        raise ContractError('PARENT_CALIBRATION_RUNTIME_PINS_REQUIRED')
    return True


def freeze(job, rows, receipts, parent, price_baselines):
    """One fixed CPU fit on the original parent's already recorded forwards."""
    if not enabled(job):
        return None
    if (job.get('objective', {}).get('schema') != 'heptatrader.laya-resident-objective.v8'
            or parent.get('candidate_id') != job['parent_id']
            or len(job['training_row_ids']) != len(rows)
            or set(job['training_row_ids']) != {row_id(r) for r in rows}
            or len(receipts) != len(rows)
            or [r.get('training_row_id') for r in receipts] != [row_id(r) for r in rows]):
        raise ContractError('PARENT_CALIBRATION_TRAINING_BINDING')
    binding = price_input.bindings(parent, objective_schema=job['objective']['schema'])
    if any(receipt.get('price_input_profile') != binding['parent_price_input_profile']
           or receipt.get('price_input_id') != digest(price_input.model_state(
               row['state'], binding['parent_price_input_profile']))
           for row,receipt in zip(rows,receipts)):
        raise ContractError('PARENT_CALIBRATION_INPUT_RECEIPT_CHANGED')
    fitted = calibration.fit(rows, [r['raw_probs'] for r in receipts],
        cutoff_ms=job['window']['training_end_ms'], parent_id=job['parent_id'],
        adapter_sha256=parent['adapter_sha256'], price_baselines=price_baselines,
        raw_logits_scope=calibration.PARENT_SCOPE)
    result = {'schema': SCHEMA, 'profile': PROFILE,
        'parent_manifest_sha256': job['parent_manifest_sha256'], 'calibration': fitted,
        'comparison_scope': 'CHILD_VERSUS_FROZEN_PARENT_SAME_CALIBRATION_BUDGET',
        'child_price_input_profile': binding['price_input_profile'],
        'parent_price_input_profile': binding['parent_price_input_profile'],
        'parameter_update_isolated': binding['price_input_profile'] == binding['parent_price_input_profile'],
        'additional_model_forwards': 0, 'additional_independent_rows': 0,
        'selection_gate': False, 'model_or_trading_authority': False}
    result['comparator_id'] = digest(result)
    return validate(result, price_baselines=price_baselines, parent_id=job['parent_id'])


def validate(value, *, price_baselines, parent_id):
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA
            or value.get('profile') != PROFILE
            or value.get('comparison_scope') != 'CHILD_VERSUS_FROZEN_PARENT_SAME_CALIBRATION_BUDGET'
            or value.get('child_price_input_profile') != price_input.RAW
            or value.get('parent_price_input_profile') not in (price_input.RAW, price_input.COMPACT)
            or value.get('parameter_update_isolated') is not (value.get('parent_price_input_profile') == price_input.RAW)
            or not isinstance(value.get('parent_manifest_sha256'), str)
            or len(value['parent_manifest_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in value['parent_manifest_sha256'])
            or value.get('additional_model_forwards') != 0
            or value.get('additional_independent_rows') != 0
            or value.get('selection_gate') is not False
            or value.get('model_or_trading_authority') is not False
            or value.get('comparator_id') != digest({k:v for k,v in value.items() if k != 'comparator_id'})):
        raise ContractError('PARENT_CALIBRATION_FROZEN_COMPARATOR')
    p = calibration.validate(value.get('calibration'))
    baseline = baselines.validate(price_baselines)
    if (p['raw_logits_scope'] != calibration.PARENT_SCOPE or p['parent_id'] != parent_id
            or any(p[key] != baseline[key] for key in
                   ('training_cutoff_ms', 'training_rows', 'training_row_ids_digest'))
            or p['price_baseline_profile_id'] != baseline['profile_id']):
        raise ContractError('PARENT_CALIBRATION_SAME_ROW_BUDGET_CHANGED')
    return value


def verify_candidate(job, candidate, *, rows=None, parent=None):
    """An independent boundary rereads originals; a worker may reuse its own rows."""
    if not enabled(job):
        if candidate.get(RECORD) is not None or candidate.get(FIELD) is not None:
            raise ContractError('UNDECLARED_PARENT_CALIBRATION')
        return None
    if candidate.get(FIELD) != PROFILE:
        raise ContractError('PARENT_CALIBRATION_DECLARATION_MISSING')
    if rows is None or parent is None:
        folder = Path(job['parent_directory'])
        if sha_file(folder/'candidate.json') != job['parent_manifest_sha256']:
            raise ContractError('PARENT_CALIBRATION_PARENT_MANIFEST_CHANGED')
    if rows is None:
        from .resident_price_calibration import training_rows_for_job
        rows = training_rows_for_job(job)
    if parent is None:
        parent = validate_candidate_artifact(folder)
    expected = freeze(job, rows, candidate.get('parent_price_forward_receipts', []),
                      parent, candidate['price_baselines'])
    if candidate.get(RECORD) != expected:
        raise ContractError('PARENT_CALIBRATION_REPLAY_CHANGED')
    return expected


def plan_binding(job, candidate):
    value = verify_candidate(job, candidate)
    return {FIELD: PROFILE, RECORD: deepcopy(value)} if value is not None else {}


def verify_plan(plan, candidate, parent, parent_manifest_sha256):
    declared = enabled(plan)
    if declared != enabled(candidate) or plan.get(RECORD) != candidate.get(RECORD):
        raise ContractError('PARENT_CALIBRATION_PLAN_CANDIDATE_CHANGED')
    if not declared:
        if plan.get(RECORD) is not None:
            raise ContractError('UNDECLARED_PARENT_CALIBRATION_PLAN')
        return None
    value = validate(plan.get(RECORD), price_baselines=plan.get('price_baselines'),
                     parent_id=plan.get('parent_id'))
    binding = price_input.bindings(parent, objective_schema=candidate['objective']['schema'])
    if (value['parent_manifest_sha256'] != parent_manifest_sha256
            or value['calibration']['adapter_sha256'] != parent['adapter_sha256']
            or value['parent_price_input_profile'] != binding['parent_price_input_profile']
            or value['child_price_input_profile'] != binding['price_input_profile']):
        raise ContractError('PARENT_CALIBRATION_PLAN_PARENT_CHANGED')
    return value


def prediction_fields(plan, parent_probs):
    if not enabled(plan):
        if plan.get(RECORD) is not None:
            raise ContractError('UNDECLARED_PARENT_CALIBRATION_PLAN')
        return {}
    value = validate(plan.get(RECORD), price_baselines=plan.get('price_baselines'),
                     parent_id=plan.get('parent_id'))
    return {'parent_calibration_comparator_id': value['comparator_id'],
            'calibrated_parent_probs': calibration.apply(value['calibration'], parent_probs)}


def verify_prediction(plan, prediction):
    expected = prediction_fields(plan, prediction.get('parent_probs'))
    for key in ('parent_calibration_comparator_id', 'calibrated_parent_probs'):
        if prediction.get(key) != expected.get(key):
            raise ContractError('PARENT_CALIBRATION_PREDICTION_CHANGED')
    return expected


def measure(plan, prediction, target):
    expected = verify_prediction(plan, prediction)
    if not expected:
        return {}
    return {'parent_calibration_comparator_id': expected['parent_calibration_comparator_id'],
            'calibrated_parent_brier': baselines.probability_metrics(
                expected['calibrated_parent_probs'], target)['brier']}


def assessment(plan, observed):
    if not enabled(plan):
        return None
    value = validate(plan.get(RECORD), price_baselines=plan.get('price_baselines'),
                     parent_id=plan.get('parent_id'))
    deltas=[]
    for row in observed:
        score=row.get('calibrated_parent_brier')
        child=row.get('candidate_brier')
        if (row.get('parent_calibration_comparator_id') != value['comparator_id']
                or any(type(v) not in (int,float) or not math.isfinite(v) or not 0 <= v <= 2
                       for v in (score,child))):
            raise ContractError('PARENT_CALIBRATION_OBSERVATION_MISSING_OR_CHANGED')
        deltas.append(score-child)
    return {'comparator_id': value['comparator_id'], 'observed_pairs': len(deltas),
            'mean_brier_improvement': sum(deltas)/len(deltas) if deltas else None,
            'paired_brier': baselines.paired_brier_diagnostics(deltas),
            'comparison_scope': value['comparison_scope'], 'selection_gate': False,
            'parameter_update_isolated': value['parameter_update_isolated'],
            'execution_profit_proven': False, 'model_or_trading_authority': False}
