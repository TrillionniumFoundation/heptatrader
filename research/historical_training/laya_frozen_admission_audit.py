"""Read-only, evidence-bound cause analysis for frozen Laya prediction slots.

A missed fixed slot is not itself evidence that the GPU Guard caused it.
Only exact, cryptographically self-consistent Guard denial receipts linked
to the registered plan and slot may support an observed denial. A missing
denial receipt leaves the explanation unproven. Never starts jobs or orders.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import time
from collections import Counter
from pathlib import Path

from historical_training.common import EvidenceError, canonical, digest, read, sha
from laya_cell.promotion import validate_plan_rule

SCHEMA = 'heptatrader.laya-frozen-admission-attribution.v1'
OUTCOME_RE = re.compile(r'outcome-([0-9]{3})\.json\Z')
DENIAL_RE = re.compile(r'[0-9a-f]{64}\.json\Z')
MAX_DENIAL_FILES = 4096
MAX_OUTCOME_BYTES = 131072
MAX_DENIAL_BYTES = 131072
PREWARM_MS = 90000


def _identity(path: Path, *, want_dir=False) -> Path:
    if not path.is_absolute() or '..' in path.parts or path.is_symlink():
        raise EvidenceError('FROZEN_AUDIT_PATH_IDENTITY')
    if path.resolve() != path:
        raise EvidenceError('FROZEN_AUDIT_PATH_INDIRECTION')
    st = path.lstat()
    if want_dir:
        if not stat.S_ISDIR(st.st_mode):
            raise EvidenceError('FROZEN_AUDIT_DIRECTORY_REQUIRED')
    elif not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise EvidenceError('FROZEN_AUDIT_REGULAR_FILE_REQUIRED')
    return path


def _small_json(path: Path, maximum: int) -> dict:
    _identity(path)
    value = read(path, maximum)
    if type(value) is not dict:
        raise EvidenceError('FROZEN_AUDIT_JSON_OBJECT_REQUIRED')
    return value


def _positive_ms(value, reason: str) -> int:
    if type(value) is not int or value <= 0:
        raise EvidenceError(reason)
    return value


def audit(plan_file: Path, denials_directory: Path, *, now_ms: int | None = None) -> dict:
    plan_file, denials_directory = Path(plan_file), Path(denials_directory)
    _identity(plan_file)
    _identity(denials_directory, want_dir=True)
    if plan_file.name != 'plan.json':
        raise EvidenceError('FROZEN_AUDIT_PLAN_NAME')
    plan = _small_json(plan_file, MAX_OUTCOME_BYTES)
    if (plan.get('schema') != 'heptatrader.laya-resident-future-plan.v1'
            or plan.get('plan_id') != digest({k: v for k, v in plan.items()
                                              if k != 'plan_id'})):
        raise EvidenceError('FROZEN_AUDIT_PLAN_MUTATED')
    rule = validate_plan_rule(plan)
    slots = rule['slots']
    if type(slots) is not int or not 1 <= slots <= 256:
        raise EvidenceError('FROZEN_AUDIT_SLOT_BOUND')
    if type(plan.get('trial')) is not int or plan['trial'] <= 0:
        raise EvidenceError('FROZEN_AUDIT_TRIAL_INVALID')
    start = _positive_ms(plan.get('start_ms'), 'FROZEN_AUDIT_START_INVALID')
    end = _positive_ms(plan.get('outcome_end_ms'), 'FROZEN_AUDIT_END_INVALID')
    created = _positive_ms(plan.get('created_at_ms'), 'FROZEN_AUDIT_CREATED_INVALID')
    if created > start or end <= start:
        raise EvidenceError('FROZEN_AUDIT_PLAN_CLOCK')
    if now_ms is None:
        now_ms = time.time_ns() // 1_000_000
    _positive_ms(now_ms, 'FROZEN_AUDIT_NOW_INVALID')
    if (type(plan.get('candidate_id')) is not str or
            type(plan.get('plan_id')) is not str):
        raise EvidenceError('FROZEN_AUDIT_CANDIDATE_ID')

    out_dir = plan_file.parent
    all_outcomes = sorted(out_dir.glob('outcome-*.json'))
    if len(all_outcomes) > slots:
        raise EvidenceError('FROZEN_AUDIT_OUTCOME_OVERCOUNT')
    outcomes = {}
    immutable_hashes = []
    for path in all_outcomes:
        _identity(path)
        match = OUTCOME_RE.fullmatch(path.name)
        if match is None:
            raise EvidenceError('FROZEN_AUDIT_OUTCOME_NAME')
        slot = int(match.group(1))
        if not 0 <= slot < slots or slot in outcomes:
            raise EvidenceError('FROZEN_AUDIT_OUTCOME_DUPLICATE_OR_RANGE')
        obj = _small_json(path, MAX_OUTCOME_BYTES)
        if (type(obj.get('slot')) is not int or obj['slot'] != slot or
                obj.get('plan_id') != plan['plan_id'] or
                obj.get('candidate_id') != plan['candidate_id'] or
                obj.get('status') not in ('OBSERVED', 'CENSORED')):
            raise EvidenceError('FROZEN_AUDIT_OUTCOME_IDENTITY')
        stamp = _positive_ms(obj.get('evaluated_at_ms'),
                             'FROZEN_AUDIT_OUTCOME_TIME')
        if stamp > now_ms:
            raise EvidenceError('FROZEN_AUDIT_OUTCOME_FROM_FUTURE')
        if (obj.get('reason') == 'NO_PREDICTION_IN_FIXED_SLOT' and
                stamp <= start + slot * rule['slot_spacing_ms'] + rule['input_grace_ms']):
            raise EvidenceError('FROZEN_AUDIT_MISSED_SLOT_NOT_EXPIRED')
        outcomes[slot] = obj
        immutable_hashes.append({'slot': slot, 'sha256': sha(path),
                                 'status': obj['status']})

    qualification_path = out_dir / 'qualification.json'
    qualification = None
    qualification_sha = None
    if qualification_path.exists() or qualification_path.is_symlink():
        qualification = _small_json(qualification_path, MAX_OUTCOME_BYTES)
        qualification_sha = sha(qualification_path)
        if (qualification.get('plan_id') != plan['plan_id'] or
                qualification.get('candidate_id') != plan['candidate_id'] or
                qualification.get('promotion_performed') is not False or
                qualification.get('canonical_owner_admitted') is not False):
            raise EvidenceError('FROZEN_AUDIT_QUALIFICATION_IDENTITY')

    denial_files = sorted(denials_directory.glob('*.json'))
    if len(denial_files) > MAX_DENIAL_FILES:
        raise EvidenceError('FROZEN_AUDIT_DENIAL_CAPACITY')
    denials = {i: [] for i in range(slots)}
    irrelevant = 0
    for file in denial_files:
        _identity(file)
        if DENIAL_RE.fullmatch(file.name) is None:
            raise EvidenceError('FROZEN_AUDIT_DENIAL_FILENAME')
        doc = _small_json(file, MAX_DENIAL_BYTES)
        subject = doc.get('subject')
        # Trainer and Value use the same directory. These cannot attest
        # that this particular Laya evaluator was allowed or refused.
        if type(subject) is not dict or subject.get('plan') != str(plan_file):
            irrelevant += 1
            continue
        slot = subject.get('slot')
        if (set(subject) != {'plan', 'slot'} or type(slot) is not int or
                not 0 <= slot < slots):
            raise EvidenceError('FROZEN_AUDIT_DENIAL_SUBJECT')
        decision = doc.get('decision')
        if type(decision) is not dict:
            raise EvidenceError('FROZEN_AUDIT_DENIAL_DECISION')
        reasons = decision.get('reasons')
        if (decision.get('role') != 'evaluator' or
                decision.get('admit_new_worker') is not False or
                type(reasons) is not list or not reasons or
                any(type(x) is not str or not 1 <= len(x) <= 200 for x in reasons) or
                type(doc.get('orders_sent')) is not int or
                doc['orders_sent'] != 0):
            raise EvidenceError('FROZEN_AUDIT_DENIAL_NOT_FAIL_CLOSED')
        # The original Gate.acquire() writes under precisely this digest.
        if file.name != digest(['evaluator', subject, reasons]) + '.json':
            raise EvidenceError('FROZEN_AUDIT_DENIAL_KEY_CHANGED')
        at = _positive_ms(decision.get('at_ms'), 'FROZEN_AUDIT_DENIAL_TIME')
        slot_start = start + slot * rule['slot_spacing_ms']
        if not slot_start - PREWARM_MS <= at <= slot_start + rule['input_grace_ms']:
            raise EvidenceError('FROZEN_AUDIT_DENIAL_OUTSIDE_SLOT')
        if at > now_ms:
            raise EvidenceError('FROZEN_AUDIT_DENIAL_FROM_FUTURE')
        denials[slot].append({'sha256': sha(file), 'at_ms': at,
                              'reasons': reasons})

    frozen_censors = [obj['evaluated_at_ms'] for obj in outcomes.values()
                      if obj['status'] == 'CENSORED']
    frozen_censors.sort()
    # A logical threshold, not a claim about what process was dispatched.
    futile_at = (frozen_censors[rule['max_censored']]
                 if len(frozen_censors) > rule['max_censored'] else None)
    details = []
    known_guard_slots = 0
    after_futility = 0
    reasons = Counter()
    for i in range(slots):
        row = outcomes.get(i)
        slot_start = start + i * rule['slot_spacing_ms']
        guard_proof = sorted(denials[i], key=lambda x: x['at_ms'])
        observed_denial = bool(guard_proof)
        known_guard_slots += observed_denial
        for proof in guard_proof:
            reasons.update(set(proof['reasons']))
        futility_preceded = (futile_at is not None and
                            futile_at < slot_start - PREWARM_MS)
        after_futility += futility_preceded
        details.append({
            'slot': i, 'fixed_start_ms': slot_start,
            'fixed_input_deadline_ms': slot_start + rule['input_grace_ms'],
            'outcome_status': row['status'] if row else 'NOT_TERMINAL',
            'original_reason': row.get('reason') if row else None,
            'outcome_sha256': next((x['sha256'] for x in immutable_hashes
                                    if x['slot'] == i), None),
            'guard_denial_observed': observed_denial,
            'guard_denials': guard_proof,
            'irreversible_censor_count_exceeded_before_prewarm': futility_preceded,
            'cause_without_exact_guard_receipt': 'UNPROVEN' if not observed_denial else None,
        })
    censored = sum(row['status'] == 'CENSORED' for row in outcomes.values())
    observed = sum(row['status'] == 'OBSERVED' for row in outcomes.values())
    payload = {
        'schema': SCHEMA, 'plan_sha256': sha(plan_file),
        'plan_id': plan['plan_id'], 'candidate_id': plan['candidate_id'],
        'trial': plan['trial'], 'source_profile': plan.get('future_source_read_profile'),
        'original_qualification_sha256': qualification_sha,
        'original_qualification_state': qualification.get('state') if qualification else None,
        'precommitted_slots': slots, 'precommitted_min_observed': rule['min_observed'],
        'precommitted_max_censored': rule['max_censored'],
        'terminal_outcome_count': len(outcomes), 'observed': observed,
        'censored': censored, 'unresolved': slots - len(outcomes),
        'max_possible_observed': slots - censored,
        'frozen_futility_first_provable_at_ms': futile_at,
        'mathematically_impossible_to_qualify':
            censored > rule['max_censored'] or slots - censored < rule['min_observed'],
        'slot_denial_proof_count': known_guard_slots,
        'distinct_original_guard_denial_files': sum(map(len, denials.values())),
        'guard_reasons_with_actual_receipts': dict(sorted(reasons.items())),
        'future_slots_preceded_by_irreversible_censoring': after_futility,
        'unrelated_denial_files_ignored': irrelevant,
        'asof_ms': now_ms, 'slots': details,
        'source_release_hashes_rechecked_on_host': False,
        'guard_denial_origin_independently_signed': False,
        'absence_of_denial_is_not_positive_admission': True,
        'futility_preceding_slot_does_not_prove_worker_was_suppressed': True,
        'missing_prediction_not_automatically_attributed_to_guard': True,
        'original_plan_or_outcomes_modified': False,
        'future_windows_shifted_or_backdated': False,
        'automatic_trading': False, 'broker_orders_sent': 0,
        'automatic_model_promotion': False,
    }
    return {**payload, 'audit_id': digest(payload)}


def write_audit_once(path: Path, result: dict) -> str:
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path.is_symlink():
        raise EvidenceError('FROZEN_AUDIT_OUTPUT_IDENTITY')
    if path.exists():
        raise EvidenceError('FROZEN_AUDIT_OUTPUT_ALREADY_EXISTS')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.resolve() != path.parent:
        raise EvidenceError('FROZEN_AUDIT_OUTPUT_PARENT_SYMLINK')
    st = path.parent.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise EvidenceError('FROZEN_AUDIT_OUTPUT_PARENT_NOT_PRIVATE')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600)
    raw = canonical(result) + b'\n'
    with os.fdopen(fd, 'wb') as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    parentfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parentfd)
    finally:
        os.close(parentfd)
    return sha(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--guard-denials', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    result = audit(args.plan, args.guard_denials)
    checksum = write_audit_once(args.output, result)
    print(json.dumps({
        'audit_id': result['audit_id'], 'sha256': checksum,
        'terminal_outcomes': result['terminal_outcome_count'],
        'guard_denied_slots_with_receipts': result['slot_denial_proof_count'],
        'frozen_qualification_rewritten': False, 'broker_orders_sent': 0,
    }, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
