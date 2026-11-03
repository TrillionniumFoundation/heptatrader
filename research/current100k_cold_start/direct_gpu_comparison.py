#!/usr/bin/env python3
"""One fresh original-Gate GPU child for historical loader comparison.

Importing this module imports no ML runtime. The controller must have reaped
the staged child and obtained a new original lease before invoking this child.
No signing, forward publication, registration, training or broker API is used.
The inherited lease descriptor remains open until this process actually exits.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import stat
import sys
import time


SCOPE = 'HISTORICAL_CPU_STANDBY_PARITY_NOT_REGISTERED_FORWARD_PROFILE'
SOURCE_NAMES = ('direct_gpu_comparison.py', 'cpu_standby_diagnostic.py', 'benchmark.py')
MAX_JSON_BYTES = 8 * 1024 * 1024


def need(value, reason):
    if not value:
        raise ValueError('DIRECT_GPU_COMPARISON_' + reason)


def canonical_file(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'CANONICAL_FILE_REQUIRED')
    need(stat.S_ISREG(path.lstat().st_mode), 'REGULAR_FILE_REQUIRED')
    return path


def pinned_json(path, expected):
    path = canonical_file(path)
    need(type(expected) is str and len(expected) == 64
         and all(c in '0123456789abcdef' for c in expected), 'SHA256_REQUIRED')
    need(path.stat().st_size <= MAX_JSON_BYTES, 'JSON_SIZE_BOUND')
    raw = path.read_bytes()
    need(len(raw) <= MAX_JSON_BYTES and hashlib.sha256(raw).hexdigest() == expected,
         'PINNED_JSON_CHANGED')
    value = json.loads(raw)
    need(type(value) is dict, 'JSON_OBJECT_REQUIRED')
    return value


def source_pins():
    root = Path(__file__).resolve().parent
    return {name: hashlib.sha256(canonical_file(root/name).read_bytes()).hexdigest()
            for name in SOURCE_NAMES}


def load_helpers():
    path = Path(__file__).with_name('cpu_standby_diagnostic.py')
    spec = importlib.util.spec_from_file_location('_current100k_direct_diagnostic', path)
    module = importlib.util.module_from_spec(spec)
    # Execute source, never a sibling .pyc for this helper. Its benchmark module
    # is also included in the controller's exact source inventory.
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def arm_parent_death(parent_pid):
    need(type(parent_pid) is int and parent_pid > 1 and os.getppid() == parent_pid,
         'ADMITTING_PARENT_REQUIRED')
    libc = ctypes.CDLL(None, use_errno=True)
    need(libc.prctl(1, signal.SIGKILL, 0, 0, 0) == 0 and os.getppid() == parent_pid,
         'PARENT_DEATH_GUARD_REQUIRED')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def check_lease(bench, proof, heavy_fd):
    need(type(heavy_fd) is int and heavy_fd >= 3, 'ORIGINAL_HEAVY_FD_REQUIRED')
    need(bench.lock_identity(heavy_fd) == proof['heavy_lock_identity'], 'ORIGINAL_HEAVY_FD_CHANGED')
    path = canonical_file(proof['heavy_lock_path'])
    info = path.stat()
    need([info.st_dev, info.st_ino] == proof['heavy_lock_identity'], 'ORIGINAL_HEAVY_PATH_CHANGED')


def deny_role_key(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except (PermissionError, FileNotFoundError):
        return
    else:
        os.close(descriptor)
        raise ValueError('DIRECT_GPU_COMPARISON_ROLE_KEY_READABLE')


def write_receipt(bench, path, value):
    bench.write(path, value)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_inputs(proof_path, proof_sha256):
    proof_path = canonical_file(proof_path)
    proof = pinned_json(proof_path, proof_sha256)
    need(proof.get('scope') == SCOPE and proof.get('source_sha256') == source_pins(), 'SOURCE_OR_SCOPE_CHANGED')
    original_path = canonical_file(proof['original_input_proof_path'])
    original = pinned_json(original_path, proof['original_input_proof_sha256'])
    need(original.get('scope') == SCOPE and original.get('source_sha256') == proof['source_sha256'],
         'ORIGINAL_SOURCE_OR_SCOPE_CHANGED')
    for key in ('parent_pid', 'heavy_lock_path', 'heavy_lock_identity'):
        need(original.get(key) == proof.get(key), 'ORIGINAL_' + key.upper() + '_CHANGED')
    staged_path = canonical_file(proof['staged_result_path'])
    state_path = canonical_file(proof['staged_state_path'])
    staged = pinned_json(staged_path, proof['staged_result_sha256'])
    state = pinned_json(state_path, proof['staged_state_sha256'])
    plan, candidate, item = original['plan'], original['candidate'], original['request_item']
    expected = {'scope': SCOPE, 'phase': 'STAGED_GPU_INFERENCE_COMPLETE',
                'config_sha256': original['config_sha256'], 'plan_id': plan['plan_id'],
                'candidate_id': candidate['candidate_id'], 'request_id': item['request']['request_id'],
                'request_sha256': item['request_sha256'], 'source_sha256': proof['source_sha256'],
                'forward_seal_created': False, 'broker_orders_sent': 0,
                'direct_loader_parity_proven': False}
    need(all(staged.get(key) == value for key, value in expected.items()), 'STAGED_IDENTITY_CHANGED')
    need(all(type(state.get(key)) is dict and state[key] for key in ('cpu_initial', 'staged_after'))
         and type(state.get('cpu_tokenizer')) is list and len(state['cpu_tokenizer']) == 2,
         'STAGED_STATE_REQUIRED')
    need(type(original.get('threads')) is int and 1 <= original['threads'] <= 4, 'THREAD_BOUND')
    output = Path(proof['output_dir'])
    need(output.is_absolute() and output.resolve() == output and output.is_dir(), 'NEW_OUTPUT_DIRECTORY_REQUIRED')
    need(output != original_path.parent and output.is_relative_to(original_path.parent), 'OUTPUT_MUST_BE_DIAGNOSTIC_SUBDIRECTORY')
    need(not any(path.is_relative_to(output) for path in (proof_path, original_path, staged_path, state_path)),
         'PINNED_INPUTS_MUST_BE_OUTSIDE_WRITABLE_OUTPUT')
    need(not any(output.iterdir()), 'NEW_EMPTY_OUTPUT_DIRECTORY_REQUIRED')
    return proof, original, staged, state, output


def run_direct(proof_path, proof_sha256, heavy_fd):
    started = time.monotonic_ns()
    proof, original, staged, state, output = validate_inputs(proof_path, proof_sha256)
    arm_parent_death(proof['parent_pid'])
    diag = load_helpers(); bench = diag.bench
    check_lease(bench, proof, heavy_fd)
    os.set_inheritable(heavy_fd, False)
    diag.admission(proof['admission'], proof['checked_ns'])
    config = bench.bootstrap(original['config_path'], original['config_sha256'])
    plan, candidate, item = original['plan'], original['candidate'], original['request_item']
    need(bench.sha(original['plan_path']) == original['plan_sha256']
         and bench.read(original['plan_path']) == plan, 'ORIGINAL_PLAN_CHANGED')
    api = bench.model_api(config, candidate)
    need(api == original['model_api'], 'FROZEN_MODEL_API_CHANGED')
    forward = Path(config['forward_root']).resolve()
    need(not output.is_relative_to(forward) and not forward.is_relative_to(output), 'OUTPUT_FORWARD_OVERLAP')
    need(config.get('hip_runtime') is not None, 'ACCEPTED_HIP_REQUIRED')
    from laya_cell import resident_hip_runtime as hip
    from sandbox import enter
    context = hip.prepare_worker(config, 'predict')
    explicit_reads = [str(Path(proof_path)), original['config_path'], original['plan_path'],
        proof['original_input_proof_path'], proof['staged_result_path'], proof['staged_state_path'],
        config['worker_source_root'], sys.prefix, str(Path(__file__).resolve().parent),
        str(Path(plan['candidate_directory']))]
    restrictions = enter({'output_dir': str(output), 'period_path': config['period_path'],
        'legacy_capture': plan['training_dataset_path'], 'oms_path': config['oms_path'],
        'export_dir': config['attribution_directory'],
        'read_only_paths': config['worker_read_only_paths'] + explicit_reads
                           + (context['readonly'] if context is not None else [])})
    deny_role_key(config['role_key_path'])
    restrictions['original_role_key_read_denied'] = True
    write_receipt(bench, output/'sandbox.json', restrictions)
    cgroup_before = bench.cgroup_snapshot(); before_torch = time.monotonic_ns()
    hip.activate(context)
    import torch
    hip_proof = hip.prove(context, 'AFTER_TORCH_BEFORE_MODEL')
    torch.set_num_threads(original['threads']); after_torch = time.monotonic_ns()
    from laya_cell.model import load_verified_laya, require_pocket4_gpu
    from laya_cell import financial_value_train as value_module
    from laya_cell.current100k_forward import feature_rows
    from safetensors.torch import load_file
    base = Path(config['base_directory']); base_manifest = bench.read(base/'base-manifest.json')
    head_path = Path(plan['candidate_directory'])/'value-head.safetensors'
    need(bench.sha(head_path) == plan['head_tensor_sha256'], 'ORIGINAL_HEAD_CHANGED')
    rows = feature_rows(plan, item['request'])
    need(not torch.cuda.is_initialized(), 'DIRECT_CHILD_GPU_INITIALIZED_BEFORE_ADMISSION_RECHECK')
    diag.fd_snapshot(os.getpid())
    check_lease(bench, proof, heavy_fd)
    need(source_pins() == proof['source_sha256'], 'SOURCE_CHANGED_BEFORE_GPU')
    diag.admission(proof['admission'], proof['checked_ns'])
    gpu_start = time.monotonic_ns()
    device = require_pocket4_gpu()
    torch.cuda.set_per_process_memory_fraction(min(1., 4 * 1024**3 / torch.cuda.get_device_properties(0).total_memory))
    torch.cuda.reset_peak_memory_stats(); setup_done = time.monotonic_ns()
    raw, tokenizer, base_id = load_verified_laya(base, base_manifest, enforce_gpu=True)
    raw.requires_grad_(False).eval()
    need(base_id == candidate['base_id'], 'BASE_ID_CHANGED')
    head = bench.load_benchmark_head(torch, value_module, load_file(str(head_path)), device, api,
                                     candidate.get('readout_profile'))
    need(bench.sha(head_path) == plan['head_tensor_sha256'], 'ORIGINAL_HEAD_CHANGED_DURING_LOAD')
    torch.cuda.synchronize(); loaded = time.monotonic_ns()
    initial_state = diag.model_state(torch, {'model': raw, 'head': head})
    tokens = diag.tokenizer_state(torch, tokenizer, rows)
    before_inference = time.monotonic_ns()
    features, values, inference = diag.infer(torch, value_module, raw, tokenizer, head, rows, api,
                                            candidate.get('readout_profile'))
    after_state = diag.model_state(torch, {'model': raw, 'head': head})
    check_lease(bench, proof, heavy_fd)
    report = {'schema': 'heptatrader.direct-gpu-historical-comparison.v1', 'scope': SCOPE,
        'created_at_ms': int(time.time()*1000),
        'phase': 'DIRECT_GPU_INFERENCE_COMPLETE_NOT_YET_COMPARED', 'direct_child_only': True,
        'direct_proof_sha256': proof_sha256, 'source_sha256': proof['source_sha256'],
        'original_input_proof_sha256': proof['original_input_proof_sha256'],
        'staged_result_sha256': proof['staged_result_sha256'], 'staged_state_sha256': proof['staged_state_sha256'],
        'config_sha256': original['config_sha256'], 'plan_id': plan['plan_id'], 'plan_sha256': original['plan_sha256'],
        'candidate_id': candidate['candidate_id'], 'head_sha256': plan['head_tensor_sha256'], 'base_id': base_id,
        'request_id': item['request']['request_id'], 'request_sha256': item['request_sha256'],
        'producer_manifest_sha256': item['producer_manifest_sha256'], 'source_record_sha256': item['source_record_sha256'],
        'model_api': api, 'initial_state': initial_state, 'after_state': after_state, 'tokenizer': tokens,
        'features': features.tolist(), 'values': values.tolist(), 'inference': inference,
        'timing_ms': {'child_start_to_first_result': (inference['completed_ns']-started)/1e6,
            'fresh_admission_to_first_gpu_api': (gpu_start-proof['checked_ns'])/1e6,
            'fresh_admission_to_result': (inference['completed_ns']-proof['checked_ns'])/1e6,
            'torch_cpu_import': (after_torch-before_torch)/1e6, 'gpu_setup': (setup_done-gpu_start)/1e6,
            'model_and_head_load': (loaded-setup_done)/1e6,
            'parity_hashing_and_tokenizer_before_inference': (before_inference-loaded)/1e6},
        'hip_runtime_proof': hip_proof, 'sandbox_receipt_sha256': bench.sha(output/'sandbox.json'),
        'process_id': os.getpid(), 'parent_pid': proof['parent_pid'], 'fresh_original_lease_held_through_result': True,
        'max_process_rss_bytes': int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*1024,
        'max_gpu_allocated_bytes': torch.cuda.max_memory_allocated(), 'max_gpu_reserved_bytes': torch.cuda.max_memory_reserved(),
        'cgroup_before': cgroup_before, 'cgroup_after': bench.cgroup_snapshot(),
        'versions': {'torch': torch.__version__, 'hip': torch.version.hip},
        'rss_and_gpu_bytes_are_not_additive': True, 'process_cold_cache_state_uncontrolled': True,
        'direct_loader_parity_proven': False, 'forward_seal_created': False, 'broker_orders_sent': 0,
        'v2_plan_registered': False, 'optimizer_used': False, 'publish_path_exercised': False,
        'global_runtime_cache_reset': False}
    write_receipt(bench, output/'result.json', report)
    # Deliberately do not close heavy_fd or reset any runtime caches here.
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--proof-sha256', required=True)
    parser.add_argument('--heavy-fd', type=int, required=True)
    args = parser.parse_args(argv)
    report = run_direct(args.proof, args.proof_sha256, args.heavy_fd)
    print(json.dumps({'state': report['phase'], 'broker_orders_sent': 0,
                      'forward_seal_created': False}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
