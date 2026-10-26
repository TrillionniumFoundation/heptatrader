"""Prospective raw PRICE input with centered conditional Brier calibration.

The qualification predecessor remains FROZEN_PARENT_RAW_PRICE_SOFTMAX on its
own artifact input. New jobs can additionally declare the same-row, same-budget
parent calibration diagnostic without changing historical selection rules.
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
    'learn/laya_cell/resident_parent_calibration.py',
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


def _closed_config(config):
    # v6/v7 add objective projection and input checking to v5. v8 already
    # checks its own exact objective and parent input, so do not recurse back
    # through those adapters and repeat the same file validation at each hop.
    return {**config, 'objective': deepcopy(closed_policy.OBJECTIVE)}


def check_config(config):
    check_objective(config.get('objective'))
    from .resident_parent_calibration import check_config as check_comparator
    check_comparator(config)
    price_input.for_candidate(read_json(Path(config['initial_parent_directory']) / 'candidate.json'))
    return closed_policy.check_config(_closed_config(config))


def _checked_job(job):
    check_objective(job.get('objective'))
    parent = validate_candidate_artifact(Path(job['parent_directory']))
    price_input.for_candidate(parent)
    if parent['candidate_id'] != job['parent_id']:
        raise ContractError('V8_PARENT_INPUT_ARTIFACT_CHANGED')
    ids, source = closed_policy._checked_job(_closed_config(job))
    return ids, parent, source


def check_job(job):
    return _checked_job(job)[0]


def source_for_job(job):
    _, _, source = _checked_job(job)
    return closed_policy.closed_action.check_source(source)


def context(job):
    _checked_job(job)
    value = closed_policy.previous.context(closed_policy._price_job(_closed_config(job)))
    value.update(objective_digest=digest(job['objective']),
                 closed_action_source_id=job['closed_action_source_id'],
                 closed_action_source_sha256=job['closed_action_source_sha256'])
    return value


def freeze(job, batches, targets, action_profile, price_baselines):
    value = closed_policy.previous.freeze(closed_policy._price_job(_closed_config(job)),
        batches, targets, action_profile, price_baselines)
    value.pop('episode_id')
    value.update(context(job))
    value['episode_id'] = digest(value)
    return value


def verify_candidate(job, candidate):
    _, parent, source = _checked_job(job)
    closed_policy.closed_action.check_source(source)
    closed_policy._verify_training(job, candidate, source, source_originals_verified=True)
    profile = calibration.validate(candidate.get('price_calibration'))
    if (candidate.get('price_output_profile') != PRICE_OUTPUT
            or profile['parent_id'] != job['parent_id']
            or profile['adapter_sha256'] != candidate['adapter_sha256']
            or profile['training_cutoff_ms'] != job['window']['training_end_ms']
            or profile['training_row_ids_digest'] != candidate['price_baselines']['training_row_ids_digest']):
        raise ContractError('V8_CANDIDATE_PRICE_CALIBRATION_CHANGED')
    from .resident_price_calibration import training_rows_for_job
    rows = training_rows_for_job(job)
    calibration.verify_candidate_fit(job, candidate, rows=rows)
    expected = price_input.bindings(parent, objective_schema=SCHEMA)
    previous._verify_input_effect(job, candidate, expected, price_input.RAW, version='V8', rows=rows)
    if expected['parent_price_input_profile'] == price_input.RAW:
        if any(a['raw_probs'] != b['raw_probs'] for a, b in zip(
                candidate['parent_price_forward_receipts'], candidate['initial_child_price_forward_receipts'])):
            raise ContractError('V8_RAW_ZERO_STEP_PARENT_MISMATCH')
    from . import resident_parent_calibration
    resident_parent_calibration.verify_candidate(job, candidate, rows=rows, parent=parent)


def configuration_for_release(config, runtime_directory, *, closed_action_source,
                               closed_action_source_sha256=None,
                               parent_calibration_comparator=False):
    result = previous.configuration_for_release(config, runtime_directory,
        closed_action_source=closed_action_source,
        closed_action_source_sha256=closed_action_source_sha256)
    runtime = Path(result['runtime_directory'])
    from .resident_owner import RUNTIME_PINS as owner_runtime_pins
    from .resident_owner_consume import RUNTIME_PINS as advisory_runtime_pins
    # Handoff code belongs in the release, outside the frozen forecast rule.
    for name in (*RUNTIME_PINS, *owner_runtime_pins, *advisory_runtime_pins):
        result['runtime_sha256'][name] = sha_file(runtime / name)
    result['objective'] = deepcopy(OBJECTIVE)
    if parent_calibration_comparator:
        from . import resident_parent_calibration as comparator
        result[comparator.FIELD] = comparator.PROFILE
        for name in comparator.RUNTIME_PINS:
            result['runtime_sha256'][name] = sha_file(runtime / name)
    check_config(result)
    return result
