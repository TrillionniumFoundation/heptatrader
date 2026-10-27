"""Rebuildable fixed-parent ACT calibration outputs, never model authority.

The worker reads one scheduler-pinned cache and writes only its task output.
After a completed candidate is verified, the scheduler publishes that derived
output for the next job. PRICE-dependent ACT inputs are never cached here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import sys

from .integrity import ContractError, canonical, digest

PROFILE = 'FROZEN_PARENT_CLOSED_ACT_CALIBRATION_CACHE_V1'
FIELD = 'parent_action_cache_profile'
INPUT = 'parent_action_cache_input'
RECEIPT = 'parent_action_cache'
SCHEMA = 'heptatrader.parent-action-calibration-cache.v1'
FILE = 'parent-action-cache.json'
PINS = ('learn/laya_cell/resident_parent_action_cache.py',
        'learn/laya_cell/resident_train.py', 'learn/laya_cell/resident.py',
        'learn/laya_cell/resident_launch.py')
MAX_BYTES = 256 * 1024
ENVIRONMENT = ('HSA_OVERRIDE_GFX_VERSION', 'HSA_ENABLE_SDMA', 'HSA_ENABLE_INTERRUPT',
    'DEBUG_HIP_DYNAMIC_QUEUES', 'GPU_MAX_HW_QUEUES', 'HIP_VISIBLE_DEVICES',
    'ROCR_VISIBLE_DEVICES', 'CUDA_VISIBLE_DEVICES', 'HIPBLASLT_TENSILE_LIBPATH',
    'TORCH_BLAS_PREFER_HIPBLASLT', 'TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL',
    'CUBLAS_WORKSPACE_CONFIG', 'HIP_LAUNCH_BLOCKING', 'AMD_SERIALIZE_KERNEL')


def _need(ok, reason):
    if not ok:
        raise ContractError('PARENT_ACTION_CACHE_' + reason)


def enabled(config):
    value = config.get(FIELD)
    _need(value in (None, PROFILE), 'PROFILE')
    return value is not None


def check_config(config):
    if not enabled(config):
        _need(config.get(INPUT) is None, 'UNDECLARED_INPUT')
        return False
    _need(config.get('objective', {}).get('schema') == 'heptatrader.laya-resident-objective.v8',
          'V8_REQUIRED')
    selected = config.get('hip_runtime', {})
    _need(selected.get('profile') == 'PINNED_HIP_KPACK_V1'
          and isinstance(selected.get('sha256'), str) and len(selected['sha256']) == 64,
          'PINNED_HIP_REQUIRED')
    pins = config.get('runtime_sha256', {})
    _need(all(isinstance(pins.get(name), str) and len(pins[name]) == 64 for name in PINS),
          'RUNTIME_PINS')
    return True


def binding(config):
    """No current PRICE row or job timestamp: only the fixed parent/source."""
    check_config(config)
    return {'profile': PROFILE, 'parent_id': config['parent_id'],
        'parent_manifest_sha256': config['parent_manifest_sha256'],
        'closed_action_source_id': config['closed_action_source_id'],
        'closed_action_source_sha256': config['closed_action_source_sha256'],
        'closed_action_training_cutoff_ms': config['closed_action_training_cutoff_ms'],
        'runtime_inventory_digest': digest(config['runtime_sha256']),
        'hip_manifest_sha256': config['hip_runtime']['sha256'],
        'input_profile': 'ORIGINAL_RAW_QUOTE_INPUT', 'max_len': 1024,
        'batch_rows': 1, 'forward_mode': 'EVAL_NO_GRAD', 'logits_dtype': 'float32'}


def _stamp(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode, value.st_uid, value.st_gid, value.st_nlink)


def _read(path, expected=None):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
              and not before.st_mode & 0o022 and before.st_nlink == 1
              and before.st_size <= MAX_BYTES, 'FILE_TYPE_OR_MODE')
        raw = stream.read(MAX_BYTES + 1)
        _need(len(raw) <= MAX_BYTES and _stamp(before) == _stamp(os.fstat(stream.fileno()))
              == _stamp(path.lstat()), 'FILE_CHANGED_DURING_READ')
    actual = hashlib.sha256(raw).hexdigest()
    _need(expected is None or actual == expected, 'INPUT_HASH_CHANGED')
    def unique(items):
        value = {}
        for key, item in items:
            _need(key not in value, 'DUPLICATE_JSON_KEY')
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        _validate(value)
    except RecursionError as exc:
        # A bounded file can still exceed the decoder/canonicalizer's nesting
        # limit. It is a corrupt derived input, not a training failure.
        raise ContractError('PARENT_ACTION_CACHE_JSON_NESTING') from exc
    return value, raw, actual


def _directory(config, *, create=False):
    root = Path(config['control_directory'])
    _need(root.is_absolute() and root.resolve() == root, 'CONTROL_PATH')
    path = root / 'parent-action-cache'
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
          and not info.st_mode & 0o077, 'DIRECTORY_MODE')
    return path


def attach_input(job):
    """Scheduler-only, before the immutable job is written; absence is a miss."""
    result = dict(job)
    result.pop(INPUT, None)
    if not check_config(result):
        return result
    context = binding(result)
    try:
        path = _directory(result) / (digest(context) + '.json')
        value, _, sha = _read(path)
        if value['binding'] == context:
            result[INPUT] = {'path': str(path), 'sha256': sha, 'key': digest(context)}
    except (OSError, ValueError, TypeError, KeyError):
        # A missing or corrupt derived cache cannot block original training.
        pass
    return result


def input_path(config):
    """Only this exact pinned file is added to the worker's read-only allowlist."""
    value = config.get(INPUT)
    if value is None:
        return None
    check_config(config)
    _need(isinstance(value, dict) and set(value) == {'path', 'sha256', 'key'}, 'INPUT_SHAPE')
    key = digest(binding(config))
    expected = Path(config['control_directory']) / 'parent-action-cache' / (key + '.json')
    _need(value['key'] == key and value['path'] == str(expected)
          and isinstance(value['sha256'], str) and len(value['sha256']) == 64, 'INPUT_BINDING')
    return expected


def readonly_input_path(config):
    """A cache lost before sandbox setup is omitted, so ordinary inference wins."""
    path = input_path(config)
    if path is None:
        return None
    try:
        _read(path, config[INPUT]['sha256'])
        return path
    except (OSError, ValueError, TypeError, KeyError):
        return None


def backend_identity(torch):
    """Once per calibration boundary, not per optimizer step.

    The HIP content manifest is in binding(). Torch binaries additionally use
    inode/ctime/mtime/size identities so replacement invalidates derived values
    without rereading gigabytes of shared libraries on each small job.
    """
    package = Path(torch.__file__).resolve().parent
    files = sorted({Path(torch._C.__file__).resolve(),
                    *(p.resolve() for p in (package / 'lib').glob('*.so*') if p.is_file())})
    props = torch.cuda.get_device_properties(torch.cuda.current_device())
    flags = {}
    for owner, names in ((torch.backends.cuda.matmul, ('allow_tf32', 'allow_fp16_reduced_precision_reduction',
                                                      'allow_bf16_reduced_precision_reduction')),
                         (torch.backends.cudnn, ('allow_tf32', 'benchmark', 'deterministic'))):
        for name in names:
            if hasattr(owner, name):
                flags[('matmul.' if owner is torch.backends.cuda.matmul else 'cudnn.') + name] = getattr(owner, name)
    for name in ('flash_sdp_enabled', 'math_sdp_enabled', 'mem_efficient_sdp_enabled'):
        function = getattr(torch.backends.cuda, name, None)
        if function is not None:
            flags[name] = function()
    return {'python': sys.version, 'torch': torch.__version__, 'hip': torch.version.hip,
        'device': props.name, 'architecture': str(props.gcnArchName),
        'threads': torch.get_num_threads(), 'interop_threads': torch.get_num_interop_threads(),
        'float32_matmul_precision': torch.get_float32_matmul_precision(),
        'deterministic_algorithms': torch.are_deterministic_algorithms_enabled(),
        'flags': flags, 'environment': {name: os.environ.get(name) for name in ENVIRONMENT},
        'torch_binary_files': [{'path': str(path), 'identity': list(_stamp(path.stat()))} for path in files]}


def _validate(value):
    _need(isinstance(value, dict) and value.get('schema') == SCHEMA
          and isinstance(value.get('binding'), dict) and isinstance(value.get('backend'), dict)
          and value.get('cache_key') == digest(value['binding']), 'RECORD_CONTEXT')
    rows, logits = value.get('row_ids'), value.get('logits')
    _need(isinstance(rows, list) and 16 <= len(rows) <= 40 and len(set(rows)) == len(rows)
          and all(isinstance(row, str) and len(row) == 64 for row in rows)
          and isinstance(logits, list) and len(logits) == len(rows), 'ROWS')
    for row in logits:
        _need(isinstance(row, list) and len(row) == 2, 'LOGITS_SHAPE')
        for number in row:
            try:
                exact = (type(number) in (int, float) and math.isfinite(number)
                         and struct.unpack('f', struct.pack('f', number))[0] == number)
            except (OverflowError, struct.error):
                exact = False
            _need(exact, 'LOGITS_FLOAT32')
    _need(value.get('record_digest') == digest({k: v for k, v in value.items() if k != 'record_digest'}),
          'RECORD_DIGEST')
    return value


def calibration_logits(config, parent, source, rows, compute, *, backend_probe=None):
    """Only pre-update parent ACT logits; the current child's ACT phase stays live."""
    if not enabled(config):
        return compute(), None, None
    import torch
    check_config(config)
    _need(parent['candidate_id'] == config['parent_id']
          and source['source_id'] == config['closed_action_source_id'], 'PARENT_OR_SOURCE')
    row_ids = [row['row_id'] for row in rows]
    context = binding(config)
    probe = backend_probe or (lambda: backend_identity(torch))
    before = probe()
    reason = 'NO_INPUT'
    try:
        path = input_path(config)
        if path is not None:
            value, _, actual = _read(path, config[INPUT]['sha256'])
            if value['binding'] != context or value['row_ids'] != row_ids:
                reason = 'CONTEXT_OR_ROW_ORDER_CHANGED'
            elif value['backend'] != before:
                reason = 'BACKEND_CHANGED'
            elif probe() != before:
                reason = 'BACKEND_CHANGED_DURING_READ'
            else:
                return torch.tensor(value['logits'], dtype=torch.float32), value, {
                    'profile': PROFILE, 'state': 'HIT', 'input_sha256': actual,
                    'cache_key': value['cache_key'], 'parent_forward_rows': 0,
                    'parent_forward_rows_reused': len(rows)}
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        reason = 'INVALID_OR_MUTATED_INPUT'
    logits = compute()
    _need(logits.dtype == torch.float32 and tuple(logits.shape) == (len(rows), 2)
          and logits.device.type == 'cpu' and bool(torch.isfinite(logits).all()), 'COMPUTED_LOGITS')
    result = {'profile': PROFILE, 'state': 'MISS', 'reason': reason,
              'cache_key': digest(context), 'parent_forward_rows': len(rows), 'parent_forward_rows_reused': 0}
    if probe() != before:
        return logits, None, {**result, 'state': 'MISS_NOT_STORED', 'reason': 'BACKEND_CHANGED_DURING_COMPUTE'}
    value = {'schema': SCHEMA, 'binding': context, 'backend': before,
             'cache_key': digest(context), 'row_ids': row_ids, 'logits': logits.tolist()}
    value['record_digest'] = digest(value)
    return logits, _validate(value), result


def write_worker_output(directory, value, receipt):
    """Called only inside the existing task output after successful training."""
    if receipt is None:
        return None
    if value is None:
        return receipt
    _validate(value)
    path = Path(directory) / FILE
    raw = canonical(value) + b'\n'
    try:
        with path.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        path.chmod(0o400)
    except OSError:
        return {**receipt, 'state': 'MISS_NOT_STORED', 'reason': 'WORKER_CACHE_OUTPUT_UNAVAILABLE'}
    return {**receipt, 'output_file': FILE, 'output_sha256': hashlib.sha256(raw).hexdigest()}


def publish_from_candidate(job, candidate):
    """Scheduler-only publication of a verified completed worker's derived output."""
    receipt = candidate.get(RECEIPT)
    if not enabled(job) or not isinstance(receipt, dict) or receipt.get('output_file') != FILE:
        return {'state': 'NO_DERIVED_OUTPUT'}
    try:
        context = binding(job)
        _need(candidate['parent_id'] == context['parent_id']
              and candidate.get('hip_runtime_proof', {}).get('manifest_sha256') == context['hip_manifest_sha256']
              and receipt.get('profile') == PROFILE and receipt.get('cache_key') == digest(context),
              'COMPLETED_CANDIDATE_BINDING')
        value, raw, expected = _read(Path(job['output_directory']) / FILE, receipt['output_sha256'])
        _need(value['binding'] == context, 'OUTPUT_CONTEXT')
        directory = _directory(job, create=True)
        destination = directory / (digest(context) + '.json')
        try:
            _, _, old = _read(destination)
            if old == expected:
                return {'state': 'ALREADY_PUBLISHED', 'sha256': expected}
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            pass
        temporary = destination.with_name(destination.name + '.next-' + str(os.getpid()))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            temporary.chmod(0o400)
            os.replace(temporary, destination)
            parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try: os.fsync(parent)
            finally: os.close(parent)
        finally:
            if temporary.exists(): temporary.unlink()
        return {'state': 'PUBLISHED', 'sha256': expected}
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        # Cache failure never changes the completed candidate or its trial.
        return {'state': 'DERIVED_CACHE_NOT_PUBLISHED'}
