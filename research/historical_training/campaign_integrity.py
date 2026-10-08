"""Validation-only guard for immutable campaign evidence.
No broker/collector/model fitting imports or authority. Existing candidate,
calendar, thresholds, source gate and original receipts remain unchanged.
"""
from __future__ import annotations
import math
from pathlib import Path
from .common import EvidenceError,read,sha


def finite(value, name, minimum=None, maximum=None):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise EvidenceError('NONFINITE_OR_NONTYPED_'+name)
    if minimum is not None and value<minimum or maximum is not None and value>maximum:
        raise EvidenceError('OUT_OF_RANGE_'+name)
    return float(value)


def verify_baseline_metrics(candidate, result):
    label=result.get('label')
    if type(label) is not int or label not in (0,1,2):raise EvidenceError('INVALID_OBSERVED_LABEL')
    if type(result.get('outcome_count')) is not int or result['outcome_count']!=1:
        raise EvidenceError('INVALID_OUTCOME_COUNT')
    prior=candidate['model']['fit_prior']
    if not isinstance(prior,list) or len(prior)!=3:raise EvidenceError('FROZEN_PRIOR_SHAPE')
    prior=[finite(x,'FROZEN_PRIOR',0,1) for x in prior]
    if abs(sum(prior)-1)>1e-10:raise EvidenceError('FROZEN_PRIOR_SUM')
    expected=sum((p-float(k==label))**2 for k,p in enumerate(prior))
    values={'train_prior_brier':expected,'uniform_brier':2/3}
    for name,wanted in values.items():
        actual=finite(result.get(name),name.upper(),0,2)
        if abs(actual-wanted)>1e-10:raise EvidenceError('BASELINE_SCORE_NOT_REPRODUCIBLE:'+name)
    finite(result.get('brier'),'BRIER',0,2)
    finite(result.get('target_bps'),'TARGET_BPS')
    return values


def verify_claim(parent, anchor, child, claim):
    if (claim.get('campaign_id')!=parent['campaign_id'] or claim.get('anchor')!=anchor
        or claim.get('candidate_sha256')!=parent['candidate_sha256']
        or claim.get('resend_or_replay_permitted') is not False):raise EvidenceError('CLAIM_SUBJECT_OR_AUTHORITY')
    times=[parent['registered_at_ms'],claim.get('claimed_at_ms'),child.get('registered_at_ms')]
    if any(type(t) is not int for t in times) or not times[0]<=times[1]<=times[2]<anchor*1000:
        raise EvidenceError('CLAIM_NOT_BEFORE_CHILD_AND_ANCHOR')


def audit_observed(campaign_root,parent,anchor):
    from .multiwindow import verify_child_slot
    from .forward_validate import audit
    root=Path(campaign_root);base=root/'slots'/str(anchor);proofdir=base/'proof'
    child=read(proofdir/'plan.json');result=read(proofdir/'result.json');candidate=read(root/'candidate.json')
    verify_child_slot(parent,anchor,child);verify_claim(parent,anchor,child,read(base/'claim.json'))
    if (result.get('paper_verified') is not True or result.get('candidate_unchanged') is not True
        or result.get('prediction_precedes_target') is not True
        or result.get('input_and_forecast_unchanged') is not True
        or result.get('financial_message_codes_sent')!=[]
        or result.get('statistical_efficacy_proven') is not False):raise EvidenceError('OBSERVATION_ASSURANCE_FLAGS')
    if child.get('sdk_sha256')!=parent['sdk_sha256']:raise EvidenceError('CHILD_SDK_INVENTORY_CHANGED')
    if child.get('producer_sha256')!=sha(Path(__file__).parent/'bar_forward_smoke.py'):
        raise EvidenceError('CHILD_COLLECTION_CODE_CHANGED')
    metrics=verify_baseline_metrics(candidate,result)
    proof=audit(proofdir,root/'candidate.json')
    proof.update(metrics,baseline_scores_independently_recomputed=True,claim_precedes_original_input=True)
    return proof


def validate_final_items(items):
    """Reject duplicated subjects and nonfinite scores before bootstrap."""
    if len(items)!=24 or any(r.get('state') not in ('OBSERVED','CENSORED') for r in items):
        raise EvidenceError('NO_INTERIM_EFFICACY_TEST')
    stamps=[r.get('anchor') for r in items]
    if any(type(x) is not int for x in stamps) or len(set(stamps))!=24:
        raise EvidenceError('DUPLICATE_OR_INVALID_FINAL_ANCHORS')
    from .multiwindow import anchors
    if stamps!=anchors(stamps[0]):raise EvidenceError('FINAL_SUBJECTS_NOT_ORIGINAL_CALENDAR')
    for r in items:
        if r['state']=='CENSORED':continue
        if type(r.get('label')) is not int or r['label'] not in (0,1,2):raise EvidenceError('INVALID_FINAL_LABEL')
        for key in ('brier','train_prior_brier','uniform_brier'):finite(r.get(key),key.upper(),0,2)
        if abs(r['uniform_brier']-2/3)>1e-10:raise EvidenceError('INVALID_UNIFORM_BASELINE')
