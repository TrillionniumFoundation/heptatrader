"""Prospective raw PRICE input with centered conditional Brier calibration.

The paired predecessor remains FROZEN_PARENT_RAW_PRICE_SOFTMAX, evaluated on
its own artifact input profile. Its optional output calibrator is not applied;
that is the existing comparison protocol, not a claim of deployed-output value.
"""
from copy import deepcopy
from pathlib import Path

from .integrity import ContractError, digest, read_json, sha_file, validate_candidate_artifact
from . import resident_training_v7 as previous
from . import resident_training_v5 as closed_policy
from . import resident_price_input as price_input
from . import resident_price_calibration_v3 as calibration

SCHEMA = price_input.V8_OBJECTIVE_SCHEMA
SELECTION = previous.SELECTION
MAX_ROW_AGE_MS = previous.MAX_ROW_AGE_MS
PRIMARY_HORIZON_SECONDS = previous.PRIMARY_HORIZON_SECONDS
PRICE_OUTPUT = calibration.OUTPUT
EPISODE = previous.EPISODE
ROW_ACCOUNTING_SCHEMA = previous.ROW_ACCOUNTING_SCHEMA
ACTION_PHASE = previous.ACTION_PHASE
ACTION_OUTPUT = previous.ACTION_OUTPUT
ACTION_BALANCE = previous.ACTION_BALANCE
OBJECTIVE = {**deepcopy(previous.OBJECTIVE), 'schema': SCHEMA,
             'price_input_profile': price_input.RAW,
             'price_input_spec': deepcopy(price_input.RAW_SPEC),
             'price_output_profile': calibration.OUTPUT,
             'price_calibration_hyperparameters': 'FIXED_CLOSED_FORM_CENTERED_BRIER_PRIOR_RESIDUAL',
             'price_calibration_objective': 'SAME_TRAINING_ROW_CONDITIONAL_MULTICLASS_BRIER',
             'parent_price_input': 'FROZEN_PARENT_ARTIFACT_OWN_PROFILE',
             'parent_price_output': 'FROZEN_PARENT_RAW_PRICE_SOFTMAX',
             'action_input_profile': price_input.RAW}
RUNTIME_PINS = tuple(dict.fromkeys(previous.RUNTIME_PINS + (
    'learn/laya_cell/resident_training_v8.py', 'learn/laya_cell/resident_price_calibration_v3.py', 'learn/laya_cell/resident_price_input.py',
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
        raise ContractError('RESIDENT_V8_OBJECTIVE_CHANGED')
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
        raise ContractError('V8_PARENT_INPUT_ARTIFACT_CHANGED')
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
        raise ContractError('V8_CANDIDATE_PRICE_CALIBRATION_CHANGED')
    calibration.verify_candidate_fit(job, candidate)
    parent = validate_candidate_artifact(Path(job['parent_directory']))
    expected = price_input.bindings(parent, objective_schema=SCHEMA)
    previous._verify_input_effect(job, candidate, expected, price_input.RAW, version='V8')
    if expected['parent_price_input_profile'] == price_input.RAW:
        if any(a['raw_probs'] != b['raw_probs'] for a, b in zip(
                candidate['parent_price_forward_receipts'], candidate['initial_child_price_forward_receipts'])):
            raise ContractError('V8_RAW_ZERO_STEP_PARENT_MISMATCH')


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
