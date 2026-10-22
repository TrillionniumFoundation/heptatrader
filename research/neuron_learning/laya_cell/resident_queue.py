"""Research queue occupancy is separate from immutable trial assessment.

A trial with more irreversible censored windows than its frozen rule allows
cannot qualify. Keep its plan, end time and outcome recording unchanged, but
do not let it occupy the sole slot for NEW training until its wall-clock end.
This module never writes trials, trains a model, or changes an alpha budget.
"""
from pathlib import Path
from .integrity import ContractError, digest, read_json
from .promotion import validate_plan_rule
from .resident_evaluate import suppress_new_work_for_frozen_failure


def inspect(config):
    root = Path(config['evaluation_directory'])
    plans = sorted(root.glob('*/plan.json'))
    if len(plans) > 512:
        raise ContractError('EVALUATION_CAPACITY_NO_PRUNING')
    pending = 0
    viable = 0
    futile = []
    for path in plans:
        plan = read_json(path)
        if plan.get('plan_id') != digest({k:v for k,v in plan.items() if k != 'plan_id'}):
            raise ContractError('REGISTERED_PLAN_CHANGED')
        validate_plan_rule(plan)
        if plan.get('scope') != config['period_sha256']:
            raise ContractError('EVALUATION_PERIOD_SCOPE')
        qualification = path.parent / 'qualification.json'
        if qualification.exists():
            q = read_json(qualification)
            if (q.get('plan_id') != plan['plan_id'] or q.get('candidate_id') != plan['candidate_id']
                    or q.get('state') not in ('REJECTED', 'QUALIFIED_AWAITING_CANONICAL_OWNER')):
                raise ContractError('CLOSED_QUALIFICATION_IDENTITY')
            continue
        pending += 1
        records = [read_json(p) for p in sorted(path.parent.glob('outcome-*.json'))]
        if suppress_new_work_for_frozen_failure(plan, records):
            futile.append(plan['plan_id'])
        else:
            viable += 1
    return {'pending_evaluations': pending, 'viable_pending_evaluations': viable, 'pending_original_trials': pending,
            'futile_pending_trials': len(futile), 'futile_plan_ids': futile,
            'queue_policy': 'ONE_PRETRAINED_CANDIDATE_WITH_ORIGINAL_TRIAL_BOUND_RETAINED',
            'original_trial_assessments_changed': False}

OVERLAP_PROFILE = 'FUTILE_BOOKKEEPING_OVERLAP_V1'
OVERLAP_FIELD = 'future_trial_overlap_profile'
OVERLAP_EFFECTIVE = 'future_trial_overlap_effective_at_ms'


def check_overlap_config(config):
    profile = config.get(OVERLAP_FIELD)
    if profile is None:
        return
    if (profile != OVERLAP_PROFILE
            or config.get('future_plan_admission_profile') != 'REGULAR_FX_FULL_WINDOW_ORIGINAL_SOURCE_V1'):
        raise ContractError('UNREVIEWED_TRIAL_OVERLAP_PROFILE')
    effective = config.get(OVERLAP_EFFECTIVE)
    if type(effective) is not int or effective <= 0:
        raise ContractError('TRIAL_OVERLAP_EFFECTIVE_TIME_REQUIRED')
    pin = config.get('runtime_sha256', {}).get('learn/laya_cell/resident_queue.py')
    if not isinstance(pin, str) or len(pin) != 64:
        raise ContractError('TRIAL_OVERLAP_RUNTIME_NOT_PINNED')


def registration_policy(queue, config, job, created_ms):
    """Only prospectively enrolled training jobs may pass futile bookkeeping.

    Every allocated historical trial and its alpha debit remain in the ledger.
    A candidate trained before this policy keeps the original serial admission.
    """
    check_overlap_config(config)
    enrolled = overlap_enrolled(config, job, created_ms)
    pending = queue['pending_original_trials']
    if not enrolled:
        return {'profile': 'ORIGINAL_TRIAL_SERIAL_ADMISSION', 'blocking_trials': pending,
                'bypassed_futile_plan_ids': [], 'original_trial_assessments_changed': False}
    viable = queue['viable_pending_evaluations']
    futile = queue['futile_plan_ids']
    if (type(viable) is not int or viable < 0 or type(futile) is not list
            or len(set(futile)) != len(futile) or pending != viable + len(futile)):
        raise ContractError('TRIAL_OVERLAP_QUEUE_IDENTITY')
    return {'profile': OVERLAP_PROFILE, 'policy_effective_at_ms': config[OVERLAP_EFFECTIVE],
            'blocking_trials': viable, 'bypassed_futile_plan_ids': list(futile),
            'original_trial_assessments_changed': False, 'alpha_debits_refunded': False,
            'observation_windows_claimed_independent': False}


def overlap_enrolled(config, job, created_ms):
    check_overlap_config(config)
    return (config.get(OVERLAP_FIELD) == OVERLAP_PROFILE
            and job.get(OVERLAP_FIELD) == OVERLAP_PROFILE
            and job.get(OVERLAP_EFFECTIVE) == config[OVERLAP_EFFECTIVE]
            and type(created_ms) is int and created_ms >= config[OVERLAP_EFFECTIVE])
