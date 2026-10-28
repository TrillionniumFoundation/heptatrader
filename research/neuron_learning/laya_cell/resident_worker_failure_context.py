"""Bounded, best-effort CPU context after a worker exception.

This observes the failing process only. It grants no retry or admission and does
not import Torch, probe a GPU, rehash large libraries, or weaken confinement.
Prepared SHA values are references to earlier validation, never failure-time
hash measurements of mapped bytes.
"""
from __future__ import annotations

import json
import os
from pathlib import PurePosixPath
import re
import resource
import sys
import time

SCHEMA = 'heptatrader.resident-worker-failure-context.v1'
PREFIX = 'HEPTA_WORKER_FAILURE_CONTEXT '
MAX_OUTPUT_BYTES = 65536
ENVIRONMENT_NAMES = (
    'AMD_COMGR_CACHE_DIR', 'AMD_COMGR_EMIT_VERBOSE_LOGS', 'AMD_LOG_LEVEL',
    'AMD_LOG_MASK', 'AMD_SERIALIZE_KERNEL', 'CUDA_VISIBLE_DEVICES',
    'DEBUG_HIP_DYNAMIC_QUEUES', 'GPU_DEVICE_ORDINAL', 'GPU_MAX_HW_QUEUES',
    'HEPTA_RESIDENT_HIP_MANIFEST_SHA256', 'HIP_ENABLE_DEFERRED_LOADING',
    'HIP_FORCE_SPIRV_CODEOBJECT', 'HIP_LAUNCH_BLOCKING', 'HIP_PATH',
    'HIP_VISIBLE_DEVICES', 'HSA_ENABLE_INTERRUPT', 'HSA_ENABLE_SDMA',
    'HSA_OVERRIDE_GFX_VERSION', 'HSA_XNACK', 'LD_LIBRARY_PATH', 'LD_PRELOAD',
    'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'PYTORCH_HIP_ALLOC_CONF',
    'ROCM_PATH', 'ROCR_VISIBLE_DEVICES', 'TMPDIR', 'XDG_CACHE_HOME',
)
LIBRARIES = ('libamdhip64.so', 'libhsa-runtime64.so', 'libamd_comgr.so',
             'librocm_kpack.so', 'libtorch_hip.so', 'libc10_hip.so',
             'libtorch_rocshmem.so', 'libnuma.so')


def _text(value, limit=512):
    return str(value).encode('utf-8', 'replace')[:limit].decode('utf-8', 'ignore')


def _read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        parts = []; remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            parts.append(chunk); remaining -= len(chunk)
        raw = b''.join(parts)
        return {'text': raw[:limit].decode('utf-8', 'replace'), 'truncated': len(raw) > limit}
    finally:
        os.close(fd)


def _attempt(call):
    try:
        return call()
    except Exception as error:
        return {'unavailable': type(error).__name__, 'errno': getattr(error, 'errno', None)}


def _environment():
    return {name: {'value': _text(os.environ[name]), 'truncated': len(os.environ[name].encode('utf-8', 'replace')) > 512}
            if name in os.environ else None for name in ENVIRONMENT_NAMES}


def _mappings():
    source = _read('/proc/self/maps', 512 * 1024)
    groups = {}
    for line in source['text'].splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6:
            continue
        raw_path = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[5])
        path = raw_path.removesuffix(' (deleted)')
        name = PurePosixPath(path).name
        library = next((item for item in LIBRARIES if name == item or name.startswith(item + '.')), None)
        if library is None:
            continue
        major, minor = (int(part, 16) for part in fields[3].split(':'))
        inode = int(fields[4]); key = (library, major, minor, inode, raw_path)
        if key not in groups:
            if len(groups) >= 32:
                source['truncated'] = True
                break
            groups[key] = {'library': library, 'path': _text(path), 'device_major': major,
                'device_minor': minor, 'inode': inode, 'deleted': raw_path.endswith(' (deleted)'),
                'executable_mapping': False}
        groups[key]['executable_mapping'] |= 'x' in fields[1]
    return {'libraries': list(groups.values()), 'truncated': source['truncated'],
            'content_sha256': 'NOT_MEASURED_ON_FAILURE', 'scope': 'PROC_SELF_MAPS_DEVICE_INODE'}


def _descriptor_count():
    count = 0; truncated = False
    with os.scandir('/proc/self/fd') as entries:
        for _ in entries:
            count += 1
            if count >= 4096:
                truncated = True
                break
    return {'entries_while_scanner_open': count, 'truncated': truncated,
            'includes_scanner_descriptor': True}


def _descriptors():
    return {'rlimit_nofile': list(resource.getrlimit(resource.RLIMIT_NOFILE)),
            'count': _attempt(_descriptor_count)}


def _cgroup():
    source = _read('/proc/self/cgroup', 16384)
    if source['truncated']:
        return {'unavailable': 'CGROUP_PATH_TRUNCATED'}
    paths = [line[3:] for line in source['text'].splitlines() if line.startswith('0::')]
    if len(paths) != 1 or not paths[0].startswith('/') or '..' in paths[0].split('/'):
        return {'unavailable': 'UNIFIED_CGROUP_PATH_UNAVAILABLE'}
    root = '/sys/fs/cgroup' + paths[0].rstrip('/')
    names = ('memory.current', 'memory.max', 'memory.peak', 'memory.events',
             'memory.events.local', 'memory.pressure')
    return {'path': _text(paths[0]), 'files': {name: _attempt(lambda name=name: _read(root + '/' + name, 4096))
                                             for name in names}}


def _prepared_reference(state):
    context = state.get('hip_context')
    if not isinstance(context, dict):
        return None
    proof = context.get('proof', {})
    libraries = {'libamdhip64.so': {'path': proof.get('candidate_path'),
                                  'sha256': proof.get('candidate_sha256')},
                 **proof.get('original_dependencies', {})}
    references = {}
    for name, item in libraries.items():
        if name not in LIBRARIES:
            continue
        previous = context.get('snapshots', {}).get(item.get('path'))
        identity = None
        if isinstance(previous, (list, tuple)) and len(previous) == 9:
            identity = {'device_major': os.major(previous[0]), 'device_minor': os.minor(previous[0]),
                        'inode': previous[1], 'size': previous[2],
                        'mtime_ns': previous[3], 'ctime_ns': previous[4]}
        references[name] = {'path': _text(item.get('path', '')),
                            'previously_verified_sha256': item.get('sha256'),
                            'previously_verified_file_identity': identity}
    return {'scope': 'PREVIOUS_PREPARE_WORKER_VALIDATION_NOT_FAILURE_TIME_HASHING',
            'manifest_sha256': context.get('manifest_sha256'),
            'libraries': references,
            'after_torch_mapping_proof_completed': state.get('after_torch_proved') is True}


def collect(state, error):
    """No acceptance verdict is inferred from this failure-time observation."""
    return {'schema': SCHEMA, 'pid': os.getpid(), 'at_ms': time.time_ns() // 1_000_000,
            'target': _text(state.get('target', ''), 32),
            'launch_phase': _text(state.get('phase', ''), 64),
            'exception_type': _text(type(error).__module__ + '.' + type(error).__qualname__, 160),
            'environment': _attempt(_environment), 'mappings': _attempt(_mappings),
            'descriptors': _attempt(_descriptors), 'cgroup': _attempt(_cgroup),
            'host_memory_pressure': _attempt(lambda: _read('/proc/pressure/memory', 4096)),
            'prepared_runtime_reference': _attempt(lambda: _prepared_reference(state)),
            'gpu_probe_performed': False, 'worker_retry_granted': False}


def emit(state, error):
    """Diagnostics must never replace the original exception, including on OOM."""
    try:
        value = collect(state, error)
        raw = json.dumps(value, ensure_ascii=True, separators=(',', ':'))
        if len(PREFIX.encode()) + len(raw.encode('utf-8')) + 1 > MAX_OUTPUT_BYTES:
            raw = json.dumps({'schema': SCHEMA, 'pid': os.getpid(),
                              'unavailable': 'SNAPSHOT_EXCEEDS_OUTPUT_BOUND'})
        print(PREFIX + raw, file=sys.stderr, flush=True)
    except BaseException:
        pass
