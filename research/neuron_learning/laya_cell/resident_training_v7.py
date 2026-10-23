"""Prospective compact causal PRICE input; all v6 budgets and gates persist.

The paired predecessor remains FROZEN_PARENT_RAW_PRICE_SOFTMAX, evaluated on
its own artifact input profile. Its optional output calibrator is not applied;
that is the existing comparison protocol, not a claim of deployed-output value.
"""
from copy import deepcopy
import math
from pathlib import Path

from .integrity import ContractError, digest, read_json, sha_file, validate_candidate_artifact
from . import resident_training_v6 as previous
from . import resident_training_v5 as closed_policy
from . import resident_price_input as price_input
from . import resident_price_calibration_v2 as calibration
from . import resident_price_baselines as refs
from .resident_row_accounting import row_id

SCHEMA = price_input.OBJECTIVE_SCHEMA
SELECTION = previous.SELECTION
MAX_ROW_AGE_MS = previous.MAX_ROW_AGE_MS
PRIMARY_HORIZON_SECONDS = previous.PRIMARY_HORIZON_SECONDS
PRICE_OUTPUT = previous.PRICE_OUTPUT
EPISODE = previous.EPISODE
ROW_ACCOUNTING_SCHEMA = previous.ROW_ACCOUNTING_SCHEMA
ACTION_PHASE = previous.ACTION_PHASE
ACTION_OUTPUT = previous.ACTION_OUTPUT
ACTION_BALANCE = previous.ACTION_BALANCE
OBJECTIVE = {**deepcopy(previous.OBJECTIVE), 'schema': SCHEMA,
             'price_input_profile': price_input.COMPACT,
             'price_input_spec': deepcopy(price_input.SPEC),
             'parent_price_input': 'FROZEN_PARENT_ARTIFACT_OWN_PROFILE',
             'parent_price_output': 'FROZEN_PARENT_RAW_PRICE_SOFTMAX',
             'action_input_profile': price_input.RAW}
RUNTIME_PINS = tuple(dict.fromkeys(previous.RUNTIME_PINS + (
    'learn/laya_cell/resident_training_v7.py', 'learn/laya_cell/resident_price_input.py',
    'learn/laya_cell/dataset.py', 'learn/laya_cell/resident_train.py',
    'learn/laya_cell/resident_predict.py', 'learn/laya_cell/resident_evaluate.py',
    'learn/laya_cell/resident_plan_admission.py', 'learn/laya_cell/resident_data.py',
    'learn/laya_cell/resident_store.py', 'learn/laya_cell/resident_launch.py',
    'learn/laya_cell/resident_row_accounting.py', 'learn/laya_cell/resident_price_baselines.py')))

primary_rows = previous.primary_rows
timed_rows = previous.timed_rows
coverage = previous.coverage
select_covered_rows = previous.select_covered_rows
check_training_rows = previous.check_training_rows
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
        raise ContractError('RESIDENT_V7_OBJECTIVE_CHANGED')
    return value


def _previous_config(config):
    value = deepcopy(config)
    value['objective'] = deepcopy(previous.OBJECTIVE)
    return value


def check_config(config):
    check_objective(config.get('objective'))
    price_input.for_candidate(read_json(Path(config['initial_parent_directory']) / 'candidate.json'))
    return previous.check_config(_previous_config(config))


def check_job(job):
    check_objective(job.get('objective'))
    parent = validate_candidate_artifact(Path(job['parent_directory']))
    price_input.for_candidate(parent)
    if parent['candidate_id'] != job['parent_id']:
        raise ContractError('V7_PARENT_INPUT_ARTIFACT_CHANGED')
    return previous.check_job(_previous_config(job))


def source_for_job(job):
    check_job(job)
    return previous.source_for_job(_previous_config(job))


def context(job):
    check_job(job)
    value = previous.context(_previous_config(job))
    value['objective_digest'] = digest(job['objective'])
    return value


def freeze(job, batches, targets, action_profile, price_baselines):
    check_job(job)
    value = previous.freeze(_previous_config(job), batches, targets, action_profile, price_baselines)
    value.pop('episode_id')
    value['objective_digest'] = digest(job['objective'])
    value['episode_id'] = digest(value)
    return value


def verify_candidate(job, candidate):
    check_job(job)
    closed_policy._verify_training(job, candidate, source_for_job(job))
    profile = calibration.validate(candidate.get('price_calibration'))
    if (candidate.get('price_output_profile') != PRICE_OUTPUT
            or profile['parent_id'] != job['parent_id']
            or profile['adapter_sha256'] != candidate['adapter_sha256']
            or profile['training_cutoff_ms'] != job['window']['training_end_ms']
            or profile['training_row_ids_digest'] != candidate['price_baselines']['training_row_ids_digest']):
        raise ContractError('V7_CANDIDATE_PRICE_CALIBRATION_CHANGED')
    calibration.verify_candidate_fit(job, candidate)
    parent = validate_candidate_artifact(Path(job['parent_directory']))
    expected = price_input.bindings(parent)
    _verify_input_effect(job, candidate, expected, price_input.COMPACT)


def _verify_input_effect(job, candidate, expected, child_input_profile, *, version='V7'):
    if price_input.check_bindings(candidate) != expected:
        raise ContractError(version + '_PARENT_INPUT_PROFILE_CHANGED')
    from .dataset import rows_for_training
    data = read_json(Path(job['dataset']), 64*1024**2)
    rows, _ = rows_for_training(data, job['window'])
    rows = [r for r in rows if row_id(r) in set(job['training_row_ids'])]
    reports = []
    for key, input_profile in (
            ('parent_price_forward_receipts', expected['parent_price_input_profile']),
            ('initial_child_price_forward_receipts', child_input_profile),
            ('final_price_raw_forward_receipts', child_input_profile)):
        values = candidate.get(key)
        if not isinstance(values, list) or len(values) != len(rows):
            raise ContractError(version + '_PRICE_INPUT_FORWARD_RECEIPTS')
        scores = []
        for row, value in zip(rows, values):
            if (value.get('training_row_id') != row_id(row)
                    or value.get('price_input_profile') != input_profile
                    or value.get('price_input_id') != digest(price_input.model_state(row['state'], input_profile))):
                raise ContractError(version + '_PRICE_INPUT_FORWARD_RECEIPTS')
            scores.append(refs.probability_metrics(value['raw_probs'], row['target_class']))
        reports.append({'ce': sum(s['nll'] for s in scores)/len(scores),
                        'brier': sum(s['brier'] for s in scores)/len(scores)})
    initial = candidate.get('initial_child_price_measurement', {})
    curve_report = candidate.get('learning_curve', {})
    if (initial.get('training_only') is not True
            or initial.get('price_input_profile') != child_input_profile
            or curve_report.get('epoch0_scope') != 'FROZEN_PARENT_OWN_INPUT_BASELINE'
            or curve_report.get('input_change_separately_measured') is not True):
        raise ContractError(version + '_INITIAL_INPUT_EFFECT_REQUIRED')
    measurements = [candidate['measurement'], initial, candidate['measurement']]
    for report, measured, prefix in zip(reports, measurements, ('parent_', '', 'candidate_')):
        for metric in ('ce', 'brier'):
            key = prefix + metric
            value = measured.get(key)
            if type(value) not in (int, float) or not math.isclose(value, report[metric], abs_tol=1e-5):
                raise ContractError(version + '_INPUT_EFFECT_MEASUREMENT_CHANGED')


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
