"""Optional, derived PRICE scorer inputs for offline weight-space experiments.

The archive reuses inputs already computed by an admitted training job. It does
not run the encoder, authorize a worker, select a model, or supply held-out data.
Singleton eval features cannot replace training-mode or differently batched
encoder calls. Replay on another numerical backend must be revalidated.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import time

from .integrity import ContractError, canonical, digest, _parse_json
from .resident_row_accounting import row_id

PROFILE = 'FROZEN_PRICE_SINGLETON_FEATURES_V1'
FIELD = 'price_feature_snapshot_profile'
RECEIPT = 'price_feature_snapshot'
SCHEMA = 'heptatrader.price-feature-snapshot.v1'
FILE = 'price-features.safetensors'
MANIFEST = 'price-feature-snapshot.json'
PINS = ('learn/laya_cell/resident_price_feature_snapshot.py',
        'learn/laya_cell/resident_train.py', 'learn/laya_cell/resident.py',
        'learn/laya_cell/resident_training_v8.py')
MAX_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024


def _need(ok, reason):
    if not ok:
        raise ContractError('PRICE_SNAPSHOT_' + reason)


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def check_config(config):
    value = config.get(FIELD)
    _need(value in (None, PROFILE), 'PROFILE')
    if value is None:
        return False
    _need(config.get('objective', {}).get('schema') == 'heptatrader.laya-resident-objective.v8',
          'V8_REQUIRED')
    pins = config.get('runtime_sha256', {})
    _need(all(_sha(pins.get(name)) for name in PINS), 'RUNTIME_PINS')
    return True


def binding(config, parent, base_id, rows):
    """Bind only identities of inputs; do not duplicate raw quotes or labels."""
    _need(check_config(config), 'NOT_DECLARED')
    _need(parent['candidate_id'] == config['parent_id'] and parent['base_id'] == base_id,
          'PARENT_OR_BASE_CHANGED')
    identities = [row_id(row) for row in rows]
    _need(0 < len(identities) <= 64 and len(set(identities)) == len(identities), 'ROWS')
    from .resident_price_input import RAW, model_state
    return {'profile': PROFILE, 'base_id': base_id, 'base_revision': parent['base_revision'],
        'parent_id': parent['candidate_id'], 'parent_adapter_sha256': parent['adapter_sha256'],
        'parent_manifest_sha256': config['parent_manifest_sha256'],
        'dataset_sha256': config['dataset_sha256'], 'job_digest': digest(config),
        'runtime_inventory_digest': digest(config['runtime_sha256']),
        'price_input_profile': RAW, 'max_len': 1024, 'batch_rows': 1,
        'forward_mode': 'EVAL_NO_GRAD', 'features_dtype': 'float32',
        'rows': [{'row_id': identity, 'input_id': digest(model_state(row['state'], RAW)),
                  'state_question_digest': digest({'state': row['state'], 'question': row['question']})}
                 for identity, row in zip(identities, rows)]}


def _validate_tensors(tensors, count):
    import torch
    _need(isinstance(tensors, dict) and set(tensors) == {'features', 'marker_mask'}, 'TENSORS')
    values, mask = tensors['features'], tensors['marker_mask']
    _need(isinstance(values, torch.Tensor) and isinstance(mask, torch.Tensor)
          and values.dtype == torch.float32 and mask.dtype == torch.bool
          and values.device.type == mask.device.type == 'cpu'
          and values.ndim == 3 and tuple(mask.shape) == tuple(values.shape[:2])
          and values.shape[0] == count and 0 < count <= 64
          and 0 < values.shape[1] <= 256 and 0 < values.shape[2] <= 4096,
          'LAYOUT')
    _need(values.numel() * values.element_size() + mask.numel() <= MAX_BYTES, 'SIZE')
    _need(bool(torch.isfinite(values).all()) and bool(mask.any(dim=1).all()), 'NONFINITE_OR_EMPTY')
    return {'features': list(values.shape), 'marker_mask': list(mask.shape)}


def prepare(config, parent, base_id, rows, cached_forward):
    """Copy existing features once; never fall back to an extra encoder call."""
    import torch
    from safetensors.torch import save
    started = time.monotonic()
    context = binding(config, parent, base_id, rows)
    _need(callable(getattr(cached_forward, 'snapshot', None)), 'CACHE_REQUIRED')
    tensors = cached_forward.snapshot(rows)
    shapes = _validate_tensors(tensors, len(rows))
    raw = save(tensors)
    _need(len(raw) <= MAX_BYTES, 'SIZE')
    manifest = {'schema': SCHEMA, 'binding': context, 'binding_digest': digest(context),
        'file': FILE, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
        'shapes': shapes, 'dtypes': {'features': 'float32', 'marker_mask': 'bool'},
        'backend': {'torch_version': str(torch.__version__), 'hip_version': torch.version.hip,
                    'source_device': str(cached_forward.snapshot_device),
                    'torch_threads': torch.get_num_threads(),
                    'float32_matmul_precision': torch.get_float32_matmul_precision()},
        'scope': 'EXISTING_TRAINING_ROWS_FROZEN_UPSTREAM_PRICE_SCORER_ONLY',
        'replay_contract': 'REVALIDATE_NUMERICS_ON_DIFFERENT_BACKEND_OR_BATCH_SHAPE',
        'held_out': False, 'extra_encoder_forwards': 0, 'extra_optimizer_steps': 0,
        'copy_and_serialize_seconds': time.monotonic() - started}
    manifest['record_digest'] = digest(manifest)
    encoded = canonical(manifest)
    _need(len(encoded) <= MAX_MANIFEST_BYTES, 'MANIFEST_SIZE')
    return raw, encoded


def _write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fchmod(stream.fileno(), 0o400)
        os.fsync(stream.fileno())


def publish(directory, prepared):
    """The candidate manifest is the commit record; partial archives are unusable.

    Storage failure for this derived diagnostic must not discard a trained
    candidate. A failed receipt never authorizes replay, even if a partial file
    remains. Existing paths are never overwritten or removed.
    """
    directory = Path(directory)
    raw, manifest = prepared
    try:
        _write_new(directory / FILE, raw)
        _write_new(directory / MANIFEST, manifest)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        return {'profile': PROFILE, 'state': 'NOT_STORED', 'errno': exc.errno,
                'training_authority': False, 'extra_encoder_forwards': 0}
    value = _parse_json(manifest)
    return {'profile': PROFILE, 'state': 'STORED', 'file': FILE, 'manifest': MANIFEST,
        'sha256': value['sha256'], 'manifest_sha256': hashlib.sha256(manifest).hexdigest(),
        'binding_digest': value['binding_digest'], 'bytes': len(raw),
        'training_authority': False, 'extra_encoder_forwards': 0}


def _stamp(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode, value.st_uid, value.st_nlink)


def _read(path, expected, maximum):
    _need(_sha(expected), 'HASH_SHAPE')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
              and not before.st_mode & 0o022 and before.st_nlink == 1
              and before.st_size <= maximum, 'FILE_TYPE_OR_MODE')
        raw = stream.read(maximum + 1)
        _need(len(raw) <= maximum and _stamp(before) == _stamp(os.fstat(stream.fileno()))
              == _stamp(path.lstat()), 'FILE_CHANGED')
    _need(hashlib.sha256(raw).hexdigest() == expected, 'HASH_CHANGED')
    return raw


def load_snapshot(directory, receipt, expected_binding):
    """Read bounded, hash-verified bytes, then decode; callers supply identities."""
    from safetensors.torch import load
    _need(isinstance(receipt, dict) and receipt.get('profile') == PROFILE
          and receipt.get('state') == 'STORED' and receipt.get('file') == FILE
          and receipt.get('manifest') == MANIFEST and receipt.get('training_authority') is False
          and receipt.get('extra_encoder_forwards') == 0
          and receipt.get('binding_digest') == digest(expected_binding), 'RECEIPT')
    directory = Path(directory)
    manifest = _parse_json(_read(directory / MANIFEST, receipt.get('manifest_sha256'), MAX_MANIFEST_BYTES))
    _need(isinstance(manifest, dict) and manifest.get('schema') == SCHEMA
          and manifest.get('binding') == expected_binding
          and manifest.get('binding_digest') == receipt['binding_digest']
          and manifest.get('record_digest') == digest({k: v for k, v in manifest.items() if k != 'record_digest'})
          and manifest.get('file') == FILE and manifest.get('sha256') == receipt.get('sha256')
          and manifest.get('bytes') == receipt.get('bytes')
          and manifest.get('held_out') is False and manifest.get('extra_encoder_forwards') == 0
          and manifest.get('extra_optimizer_steps') == 0, 'MANIFEST')
    raw = _read(directory / FILE, receipt['sha256'], MAX_BYTES)
    _need(len(raw) == manifest['bytes'], 'SIZE')
    tensors = load(raw)
    shapes = _validate_tensors(tensors, len(expected_binding['rows']))
    _need(shapes == manifest.get('shapes')
          and manifest.get('dtypes') == {'features': 'float32', 'marker_mask': 'bool'}, 'LAYOUT')
    return tensors, manifest
