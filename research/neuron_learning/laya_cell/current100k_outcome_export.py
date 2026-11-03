"""Ordered strict-review export for the weight workbench; no model or effects.

The source evaluator owns the row list and its digest. Export the same ordered
population before publishing its assessment; never reconstruct it from folders.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from historical_training.common import EvidenceError, canonical, digest

SCHEMA = 'hepta.weight.current100k-ordered-outcomes.v1'
MAX_BYTES = 64 * 1024 * 1024
MAX_ROWS = 100_000


def comparison_eligibility(plan, assessment, proofs=(), evidence_errors=()):
    """A separate negative-eligibility fact; never an early final assessment.

    No positive eligibility is inferred from missing or unsupported evidence.
    Original statistics, rows, schedule state and fixed deadlines stay intact.
    """
    maximum = plan['rule']['maximum_unresolved_at_final']
    require(type(maximum) is int and maximum >= 0, 'UNRESOLVED_RULE')
    proofs = sorted(proofs, key=lambda p: p['command_id'])
    require(len({p['command_id'] for p in proofs}) == len(proofs), 'DUPLICATE_FAILURE_PROOF')
    for proof in proofs:
        require(proof['plan_id'] == plan['plan_id'] and proof['proof_digest'] ==
                digest({k: v for k, v in proof.items() if k != 'proof_digest'}), 'FAILURE_PROOF_IDENTITY')
    current = assessment.get('current_replay_complete') is True
    require(current or not proofs, 'FAILURE_PROOF_WITHOUT_CURRENT_SOURCE')
    blocked = current and len(proofs) > maximum
    value = {
        'schema': 'heptatrader.current100k-comparison-eligibility.v1',
        'plan_id': plan['plan_id'], 'as_of_ms': assessment['as_of_ms'],
        'outcome_set_digest': assessment['outcome_set_digest'],
        'status': 'IRRECOVERABLY_INELIGIBLE' if blocked else 'UNKNOWN',
        'formal_success_possible_under_frozen_rule': False if blocked else None,
        'permanent_unresolved_count_lower_bound': len(proofs),
        'maximum_unresolved_at_final': maximum,
        'reason': ('IMMUTABLE_MISSED_PREOPEN_EXCEEDS_FROZEN_UNRESOLVED_LIMIT' if blocked else
                   'NO_VERIFIED_IRREVERSIBLE_BLOCKER' if current else 'CURRENT_SOURCE_UNAVAILABLE'),
        'failure_proofs': proofs, 'evidence_errors': list(evidence_errors),
        'source_state': assessment['state'], 'original_outcome_end_ms': plan['outcome_end_ms'],
        'is_final_protocol_report': False, 'original_window_modified': False,
        'diagnostic_metrics_preserved': True, 'trade_authority': False,
    }
    value['eligibility_digest'] = digest(value)
    return value


def require(condition, reason):
    if not condition:
        raise EvidenceError('SHADOW_OUTCOME_EXPORT_' + reason)


def _read_existing(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_BYTES, 'FILE_TYPE_OR_SIZE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) <= MAX_BYTES and identity(before) == identity(after), 'CHANGED_DURING_READ')
        return raw
    finally:
        os.close(fd)


def _publish_once(path, raw):
    """Flush complete temporary bytes, then publish one create-only link."""
    if path.exists() or path.is_symlink():
        require(_read_existing(path) == raw, 'IMMUTABLE_CONFLICT')
        return
    fd, temporary = tempfile.mkstemp(prefix='.outcome-export-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fchmod(stream.fileno(), 0o400)
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            require(_read_existing(path) == raw, 'IMMUTABLE_CONFLICT')
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


def write_outcome_snapshot(review_root, assessment, rows):
    """Return a pointer after durably exporting this computation's ordered rows.

    Both arguments remain untouched. Snapshot publication precedes assessment or
    terminal publication, so an interrupted export never credits a partial list.
    Canonical bytes retain historical_training.common's ensure_ascii=True rule.
    """
    require(isinstance(rows, list) and len(rows) <= MAX_ROWS, 'ROW_LIMIT')
    require(isinstance(assessment, dict) and assessment.get('schema') ==
            'heptatrader.current100k-weight-shadow-assessment.v1', 'ASSESSMENT_SCHEMA')
    require(assessment.get('strict_preopen_terminal_required') is True, 'STRICT_REVIEW_REQUIRED')
    plan_id = assessment.get('plan_id')
    require(isinstance(plan_id, str) and len(plan_id) == 64 and
            all(c in '0123456789abcdef' for c in plan_id), 'PLAN_ID')
    as_of = assessment.get('as_of_ms')
    require(type(as_of) is int and 0 < as_of < 2**63, 'ASSESSMENT_CLOCK')
    encoded_rows = canonical(rows)
    require(len(encoded_rows) <= MAX_BYTES, 'FILE_TYPE_OR_SIZE')
    outcome_digest = hashlib.sha256(encoded_rows).hexdigest()
    require(outcome_digest == assessment.get('outcome_set_digest'), 'ORDERED_DIGEST_MISMATCH')
    # Hold the captured JSON value, rather than a live reference to caller lists.
    document = {'schema': SCHEMA, 'plan_id': plan_id, 'as_of_ms': as_of,
                'outcome_set_digest': outcome_digest, 'rows': json.loads(encoded_rows)}
    encoded = canonical(document)
    raw = encoded + b'\n'
    require(len(raw) <= MAX_BYTES, 'FILE_TYPE_OR_SIZE')
    root = Path(review_root)
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(), 'REVIEW_ROOT')
    folder = root / 'outcome-sets'
    require(not folder.is_symlink(), 'DIRECTORY_SYMLINK')
    folder.mkdir(mode=0o700, exist_ok=True)
    relative = 'outcome-sets/' + hashlib.sha256(encoded).hexdigest() + '.json'
    _publish_once(root / relative, raw)
    # Sync both the member and newly created outcome-sets directory entries,
    # including an identical retry after an interrupted directory fsync.
    for directory_path in (folder, root):
        directory = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return {'schema': SCHEMA, 'relative_path': relative,
            'sha256': hashlib.sha256(raw).hexdigest(), 'row_count': len(document['rows']),
            'outcome_set_digest': outcome_digest}
