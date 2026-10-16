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
