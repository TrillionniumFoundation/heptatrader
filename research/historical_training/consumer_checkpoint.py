"""Bounded, read-only input checks for the current100k materialized view.

A checkpoint never replaces config/runtime validation. It may skip a replay
only while every evidence identity and every published receipt is unchanged.
"""
from __future__ import annotations
import os, stat
from pathlib import Path
from .common import EvidenceError, atomic, digest, read, sha

SCHEMA = 'heptatrader.current100k-input-checkpoint.v1'
REVERIFY_MS = 60 * 60 * 1000
MAX_ENTRIES = 8192


def file_identity(path, *, private=False):
    path = Path(path)
    st = path.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
            or (private and (st.st_uid != os.geteuid() or st.st_mode & 0o077))):
        raise EvidenceError('CONSUMER_INPUT_FILE_SCOPE:' + str(path))
    return [str(path), st.st_dev, st.st_ino, st.st_mode, st.st_uid,
            st.st_gid, st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def directory_identity(path):
    path = Path(path)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise EvidenceError('CONSUMER_DIRECTORY_SCOPE:' + str(path))
    # Atomic health heartbeats change directory timestamps, not business input.
    return [str(path), st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid]


def input_identity(config, manifest):
    from hepta_decision_attribution import read_private, strict
    root = Path(manifest['journal_directory'])
    rows = [file_identity(config['reward_config']['oms_path']),
            file_identity(config['period_path']),
            file_identity(config['registration']['manifest'], private=True),
            directory_identity(root)]
    segments = sorted(root.glob('*.jsonl'))
    if len(segments) > 128:
        raise EvidenceError('ATTRIBUTION_STORAGE_BOUND')
    rows.extend(file_identity(p, private=True) for p in segments)
    for path in sorted(root.glob('*.health.json')):
        before = file_identity(path, private=True)
        health = strict(read_private(path, 65536))
        after = file_identity(path, private=True)
        if before != after:
            raise EvidenceError('CONSUMER_HEALTH_CHANGED_DURING_CHECK')
        if not isinstance(health, dict):
            raise EvidenceError('CONSUMER_HEALTH_SHAPE')
        rows.append([str(path), 'HEALTH', after[3:7],
                     {k: v for k, v in health.items() if k != 'at_ms'}])
    if len(rows) > MAX_ENTRIES:
        raise EvidenceError('CONSUMER_INPUT_ENTRY_BOUND')
    return digest({'config': config, 'manifest': manifest, 'evidence': rows})


def output_identity(root):
    root = Path(root)
    store = root / 'incremental-v1'
    entries = store / 'episodes'
    rows = [directory_identity(store), directory_identity(entries)]
    files = sorted(entries.iterdir())
    if len(files) > 4096:
        raise EvidenceError('CONSUMER_OUTPUT_ENTRY_BOUND')
    for path in files:
        if path.suffix != '.json':
            raise EvidenceError('INCREMENTAL_ENTRY_INVALID')
        rows.append(file_identity(path, private=True))
    view = store / 'dataset-current.json'
    rows.append(file_identity(view, private=True))
    return digest(rows)


def load_checkpoint(root, identity, now_ms):
    path = Path(root) / 'input-checkpoint.json'
    try:
        file_identity(path, private=True)
        checkpoint = read(path, 256 * 1024)
        evaluated = checkpoint.get('evaluated_at_ms')
        if (checkpoint.get('schema') != SCHEMA
                or checkpoint.get('input_identity') != identity
                or type(evaluated) is not int
                or not evaluated <= now_ms < evaluated + REVERIFY_MS
                or checkpoint.get('output_identity') != output_identity(root)):
            return None
        status = checkpoint['status']
        if (not isinstance(status, dict)
                or status.get('state') not in ('READY_VALUE_LABELS_NOT_EFFICACY',
                                              'WAITING_CURRENT100K_SETTLED_LABELS')
                or status.get('dataset_path') != str(Path(root) / 'incremental-v1/dataset-current.json')
                or status.get('dataset_sha256') != sha(status['dataset_path'])):
            return None
        return checkpoint
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_checkpoint(root, identity, status, now_ms):
    atomic(Path(root) / 'input-checkpoint.json', {
        'schema': SCHEMA, 'input_identity': identity,
        'output_identity': output_identity(root), 'evaluated_at_ms': now_ms,
        'status': status})


def forget_checkpoint(root):
    path = Path(root) / 'input-checkpoint.json'
    try:
        path.unlink()
    except FileNotFoundError:
        pass
