#!/usr/bin/env python3
"""Historical Current100k timing under the selected original Guard and sandbox.

No prediction is signed or written to a forward directory. The request must
already exist in the original producer graph before this diagnostic starts.
Model loading/inference runs only after original Gate.acquire, with that same
heavy lock inherited by the child until process exit.
"""
from __future__ import annotations
import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import stat
import subprocess
import sys
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    data = json.dumps(value, sort_keys=True, allow_nan=False, indent=2).encode() + b'\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def bootstrap(config_path, expected):
    if sha(config_path) != expected:
        raise ValueError('CONFIG_SHA256_CHANGED')
    config = read(config_path)
    source = Path(config['worker_source_root'])
    if not source.is_absolute() or source.resolve() != source:
        raise ValueError('CANONICAL_PINNED_SOURCE_REQUIRED')
    sys.path[:0] = [str(source / part) for part in ('research', 'learn', 'scripts')]
    return config


def lock_identity(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('PRIVATE_ORIGINAL_HEAVY_LOCK_REQUIRED')
    return [info.st_dev, info.st_ino]


def select_request(requests, request_id, plan, started_at_ms):
    if request_id == 'latest-flat':
        candidates = [item for item in requests.values()
                      if item['request']['position_at_request'] == 0
                      and item['request']['started_at_ms'] < started_at_ms]
        if not candidates:
            raise ValueError('NO_EXISTING_HISTORICAL_FLAT_REQUEST')
        item = max(candidates, key=lambda x: (x['request']['started_at_ms'], x['request']['request_id']))
    else:
        item = requests[request_id]
    request = item['request']
    if (request['position_at_request'] != 0 or request['started_at_ms'] >= started_at_ms
            or request['model_id'] != plan['behavior_scope']['behavior_model_id']
            or item['producer_manifest_sha256'] != plan['producer_registration']['sha256']):
        raise ValueError('EXISTING_REAL_FLAT_REQUEST_SCOPE_REQUIRED')
    return item


def cgroup_snapshot():
    result = {}
    try:
        memberships = Path('/proc/self/cgroup').read_text().splitlines()
        relative = next(line.split(':', 2)[2] for line in memberships if line.startswith('0::'))
        path = Path('/sys/fs/cgroup') / relative.lstrip('/')
        if '..' in path.parts:
            raise ValueError('CGROUP_PATH')
        result['path'] = str(path)
        for name in ('cpu.max', 'cpu.weight', 'cpu.stat', 'memory.current', 'memory.peak', 'memory.events'):
            if (path / name).exists():
                result[name] = (path / name).read_text()[:4096]
    except (OSError, ValueError, StopIteration) as exc:
        result['unavailable'] = type(exc).__name__
    return result


LEGACY_258_VALUE_SHA = '50c86e6ffa0d4b468cbb62c0320c50f85827f91500248a327865a415bc7c9de3'
LEGACY_258_PREDICT_SHA = 'b744e17dff94db797ca5bef8a17acdae47b93d0d946432bd5b27eefb3c994e4a'


def model_api_from_files(paths, candidate):
    """Recognize actual frozen APIs without importing Torch or guessing output semantics."""
    trees = {name: ast.parse(Path(path).read_text()) for name, path in paths.items()}
    definitions = {name: {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
                   for name, tree in trees.items()}
    hashes = {name: sha(path) for name, path in paths.items()}
    if (not {'load_verified_laya', 'require_pocket4_gpu'} <= definitions['model']
            or 'features_from_laya' not in definitions['financial_value_train']):
        raise ValueError('FROZEN_MODEL_API_UNSUPPORTED')
    if (hashes['financial_value_train'] == LEGACY_258_VALUE_SHA
            and hashes['financial_forward_predict'] == LEGACY_258_PREDICT_SHA):
        if candidate.get('readout_profile') is not None:
            raise ValueError('LEGACY_HEAD_CANNOT_ACCEPT_CENTERED_READOUT_PROFILE')
        mode = 'EXACT_RELEASE_258_INLINE_FINANCIAL_HEAD_UNCLAMPED_X100'
    elif {'load_value_head', 'prediction_usd'} <= definitions['financial_value_train']:
        imports = {alias.name for node in ast.walk(trees['financial_forward_predict'])
                   if isinstance(node, ast.ImportFrom) and node.module == 'financial_value_train'
                   for alias in node.names}
        if not {'load_value_head', 'prediction_usd', 'features_from_laya'} <= imports:
            raise ValueError('FROZEN_PREDICTOR_DOES_NOT_USE_EXPECTED_HEAD_HELPERS')
        mode = 'FROZEN_FINANCIAL_VALUE_TRAIN_HEAD_HELPERS'
    else:
        raise ValueError('FROZEN_READOUT_API_UNSUPPORTED_NO_MODEL_LOADED')
    return {'readout_mode': mode, 'source_sha256': hashes}


def model_api(config, candidate):
    root = Path(config['worker_source_root'])/'learn'/'laya_cell'
    return model_api_from_files({name: root/(name+'.py') for name in
        ('model', 'financial_value_train', 'financial_forward_predict')}, candidate)


def load_benchmark_head(torch, value_module, tensors, device, api, readout_profile):
    if api['readout_mode'] == 'FROZEN_FINANCIAL_VALUE_TRAIN_HEAD_HELPERS':
        return value_module.load_value_head(tensors, device, readout_profile=readout_profile)
    # Exact head restoration in the SHA-pinned 258 release's Predictor.predict.
    # That release has no load_value_head/prediction_usd helper and no clipping.
    head = value_module.FinancialHead(device)
    if set(tensors) != set(head.state_dict()) or any(not torch.isfinite(t).all() for t in tensors.values()):
        raise ValueError('FINANCIAL_TENSOR_SCOPE')
    head.load_state_dict(tensors, strict=True)
    head.requires_grad_(False).eval()
    if float(torch.linalg.vector_norm(torch.cat([t.float().flatten() for t in tensors.values()]))) > .100001:
        raise ValueError('READOUT_NORM_BOUND')
    return head


def benchmark_values(value_module, head, features, api, readout_profile):
    if api['readout_mode'] == 'FROZEN_FINANCIAL_VALUE_TRAIN_HEAD_HELPERS':
        return value_module.prediction_usd(head, features, readout_profile=readout_profile)
    return head(features) * 100


def parent(args):
    started = time.monotonic_ns()
    started_at = int(time.time() * 1000)
    config = bootstrap(args.config, args.sha256)
    from laya_cell.current100k_forward_worker import validate_runtime, acquire_value_resources
    from laya_cell.financial_forward_evaluate import validate_registration
    from laya_cell.financial_forward_source import ingress
    from laya_cell import resident_hip_runtime as hip
    from historical_training.resident_resource_guard import Gate, verify_config
    validate_runtime(config)
    if sha(config['guard_config']) != config['guard_config_sha256']:
        raise ValueError('ORIGINAL_GUARD_CHANGED')
    guard = read(config['guard_config'])
    original = verify_config(guard)
    hip.check_guard_selection(config, original)
    plans = list((Path(config['forward_root']) / 'plans').glob('*/plan.json'))
    if len(plans) != 1:
        raise ValueError('EXACTLY_ONE_ORIGINAL_PLAN_REQUIRED')
    plan = read(plans[0])
    candidate = validate_registration(config, plans[0], plan)
    api = model_api(config, candidate)
    validated_ns = time.monotonic_ns()
    _, requests, _, issues = ingress(plan['producer_registration'], config['period_sha256'])
    if issues:
        raise ValueError('ORIGINAL_INGRESS_PROVENANCE_UNAVAILABLE')
    item = select_request(requests, args.request_id, plan, started_at)
    if args.request_sha256 and item['request_sha256'] != args.request_sha256:
        raise ValueError('REQUEST_SHA256_CHANGED')
    ingress_ns = time.monotonic_ns()
    output = args.output.resolve()
    live = Path(config['forward_root']).resolve()
    if output.is_relative_to(live) or live.is_relative_to(output):
        raise ValueError('DIAGNOSTIC_OUTPUT_MUST_BE_SEPARATE_FROM_FORWARD_ROOT')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    gate = Gate(guard, 'value-predictor')
    process = None
    try:
        admitted = acquire_value_resources(gate, {'diagnostic': 'HISTORICAL_COLD_START_BENCHMARK',
            'plan_id': plan['plan_id'], 'request_id': item['request']['request_id']})
        if not admitted:
            report = {'state': 'HELD_ORIGINAL_GUARD_NO_MODEL_LOADED',
                      'admission': gate.last_admission, 'broker_orders_sent': 0}
            write(output / 'report.json', report)
            print(json.dumps(report, sort_keys=True))
            return 3
        admitted_ns = time.monotonic_ns()
        proof = {'config_path': str(args.config.resolve()), 'config_sha256': args.sha256,
            'plan_path': str(plans[0]), 'plan_sha256': sha(plans[0]), 'plan': plan,
            'candidate': candidate, 'request_item': item, 'admission': gate.last_admission,
            'model_api': api,
            'parent_pid': os.getpid(), 'heavy_lock_path': str(gate.lease.path),
            'heavy_lock_identity': lock_identity(gate.lease.fd),
            'started_at_ms': started_at, 'started_ns': started,
            'front_ms': {'runtime_and_signed_registration': (validated_ns-started)/1e6,
                         'historical_ingress': (ingress_ns-validated_ns)/1e6,
                         'original_resource_admission': (admitted_ns-ingress_ns)/1e6},
            'repeats': args.repeats, 'threads': args.threads,
            'inference_mode': args.inference_mode,
            'scope': 'HISTORICAL_OFFLINE_TIMING_NOT_FORWARD_PREDICTION'}
        proof_path = output / 'input-proof.json'
        write(proof_path, proof)
        environment = hip.worker_environment(config) or dict(os.environ)
        if 'torch' in sys.modules:
            raise ValueError('ORIGINAL_CPU_PARENT_IMPORTED_TORCH')
        environment.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS=str(args.threads),
                           OPENBLAS_NUM_THREADS='1', TOKENIZERS_PARALLELISM='false')
        process = subprocess.Popen([config['python'], '-B', str(Path(__file__).resolve()),
            '--child-proof', str(proof_path), '--child-proof-sha256', sha(proof_path),
            '--heavy-fd', str(gate.lease.fd)], env=environment, close_fds=True,
            pass_fds=(gate.lease.fd,))
        code = process.wait(timeout=args.timeout_seconds)
        gate.record_native_exit(code, {'diagnostic': 'HISTORICAL_COLD_START_BENCHMARK',
                                      'request_id': item['request']['request_id']})
        return code
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        gate.release()


def child(args):
    child_started = time.monotonic_ns()
    if sha(args.child_proof) != args.child_proof_sha256:
        raise ValueError('DIAGNOSTIC_INPUT_PROOF_CHANGED')
    proof = read(args.child_proof)
    parent_pid = proof['parent_pid']
    if type(parent_pid) is not int or parent_pid <= 1 or os.getppid() != parent_pid:
        raise ValueError('ORIGINAL_ADMITTING_PARENT_REQUIRED')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        raise ValueError('PARENT_DEATH_GUARD_REQUIRED')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if lock_identity(args.heavy_fd) != proof['heavy_lock_identity']:
        raise ValueError('ORIGINAL_HEAVY_DESCRIPTOR_CHANGED')
    path_info = Path(proof['heavy_lock_path']).lstat()
    if [path_info.st_dev, path_info.st_ino] != proof['heavy_lock_identity']:
        raise ValueError('ORIGINAL_HEAVY_LOCK_PATH_CHANGED')
    os.set_inheritable(args.heavy_fd, False)
    decision = proof['admission']
    if (decision.get('admit_new_worker') is not True or decision.get('role') != 'value-predictor'
            or not 0 <= int(time.time()*1000) - decision['at_ms'] <= 5000):
        raise ValueError('FRESH_ORIGINAL_ADMISSION_REQUIRED_NO_MODEL_LOADED')
    config = bootstrap(proof['config_path'], proof['config_sha256'])
    plan, candidate, item = proof['plan'], proof['candidate'], proof['request_item']
    api = model_api(config, candidate)
    if api != proof['model_api']:
        raise ValueError('FROZEN_MODEL_API_CHANGED_AFTER_ADMISSION')
    output = args.child_proof.parent
    from laya_cell import resident_hip_runtime as hip
    context = hip.prepare_worker(config, 'predict')
    from sandbox import enter
    restrictions = enter({'output_dir': str(output), 'period_path': config['period_path'],
        'legacy_capture': plan['training_dataset_path'], 'oms_path': config['oms_path'],
        'export_dir': config['attribution_directory'],
        'read_only_paths': config['worker_read_only_paths'] + [config['worker_source_root'],
            str(Path(__file__).resolve()), sys.prefix, str(Path(plan['candidate_directory']))]
            + (context['readonly'] if context is not None else [])})
    # Deliberately do not allowlist any role key. This diagnostic cannot seal.
    write(output / 'sandbox.json', restrictions)
    cgroup_before = cgroup_snapshot()
    before_torch = time.monotonic_ns()
    hip.activate(context)
    import torch
    hip_proof = hip.prove(context, 'AFTER_TORCH_BEFORE_MODEL')
    torch.set_num_threads(proof['threads'])
    from laya_cell.model import load_verified_laya, require_pocket4_gpu
    from laya_cell import financial_value_train as value_module
    features_from_laya = value_module.features_from_laya
    from laya_cell.current100k_forward import feature_rows
    from safetensors.torch import load_file
    device = require_pocket4_gpu()
    torch.cuda.set_per_process_memory_fraction(min(1., 4*1024**3 / torch.cuda.get_device_properties(0).total_memory))
    torch.cuda.reset_peak_memory_stats()
    after_torch = time.monotonic_ns()
    raw, tokenizer, base_id = load_verified_laya(Path(config['base_directory']),
                                               read(Path(config['base_directory'])/'base-manifest.json'))
    raw.requires_grad_(False).eval()
    torch.cuda.synchronize()
    loaded_ns = time.monotonic_ns()
    if base_id != candidate['base_id']:
        raise ValueError('ORIGINAL_CANDIDATE_BASE_CHANGED')
    head_path = Path(plan['candidate_directory'])/'value-head.safetensors'
    if sha(head_path) != plan['head_tensor_sha256']:
        raise ValueError('ORIGINAL_VALUE_HEAD_CHANGED')
    head = load_benchmark_head(torch, value_module, load_file(str(head_path)), device,
                               api, candidate.get('readout_profile'))
    rows = feature_rows(plan, item['request'])
    head_ns = time.monotonic_ns()
    rounds = []
    for index in range(proof['repeats']):
        start = time.monotonic_ns()
        if proof['inference_mode'] == 'serial':
            encoded = [features_from_laya(raw, tokenizer, [row]) for row in rows]
            features = torch.cat([pair[0] for pair in encoded])
            feature_receipt = {'serial_action_features': [pair[1] for pair in encoded]}
        else:
            features, feature_receipt = features_from_laya(raw, tokenizer, rows)
        with torch.no_grad():
            values = benchmark_values(value_module, head, features, api,
                                      candidate.get('readout_profile')).cpu().tolist()
        torch.cuda.synchronize()
        end = time.monotonic_ns()
        if not all(torch.isfinite(parameter).all() for parameter in head.parameters()):
            raise ValueError('NONFINITE_VALUE_HEAD')
        rounds.append({'iteration': index, 'first_inference_in_process': index == 0,
            'inference_ms': (end-start)/1e6, 'values_usd': dict(zip(('BUY','SELL'), values)),
            'feature_receipt': feature_receipt,
            'parent_start_to_result_ms': (end-proof['started_ns'])/1e6})
        del features
        if proof['inference_mode'] == 'serial':
            del encoded
    usage = resource.getrusage(resource.RUSAGE_SELF)
    report = {'state': 'HISTORICAL_TIMING_COMPLETE', 'created_at_ms': int(time.time()*1000),
        'scope': proof['scope'], 'broker_orders_sent': 0, 'forward_seal_created': False,
        'publish_path_exercised': False, 'optimizer_used': False,
        'process_cold_cache_state_uncontrolled': True,
        'config_sha256': proof['config_sha256'], 'plan_id': plan['plan_id'], 'model_api': api,
        'plan_sha256': proof['plan_sha256'], 'candidate_id': candidate['candidate_id'],
        'base_id': base_id, 'head_sha256': plan['head_tensor_sha256'],
        'request_id': item['request']['request_id'], 'request_sha256': item['request_sha256'],
        'producer_manifest_sha256': item['producer_manifest_sha256'],
        'request_started_at_ms': item['request']['started_at_ms'],
        'source_record_sha256': item['source_record_sha256'], 'source_prefix': item['source_prefix'],
        'feature_text_sha256': [hashlib.sha256(row['feature_text'].encode()).hexdigest() for row in rows],
        'inference_mode': proof['inference_mode'], 'threads': torch.get_num_threads(),
        'front_ms': proof['front_ms'],
        'child_phases_ms': {'bootstrap_sandbox': (before_torch-child_started)/1e6,
            'torch_and_gpu_setup': (after_torch-before_torch)/1e6,
            'model_load': (loaded_ns-after_torch)/1e6, 'head_and_features': (head_ns-loaded_ns)/1e6},
        'rounds': rounds, 'hip_runtime_proof': hip_proof,
        'cold_model_head_first_inference_ms': (head_ns-after_torch)/1e6 + rounds[0]['inference_ms'],
        'max_process_rss_bytes': int(usage.ru_maxrss)*1024,
        'process_cpu_seconds': usage.ru_utime+usage.ru_stime,
        'max_gpu_allocated_bytes': torch.cuda.max_memory_allocated(),
        'max_gpu_reserved_bytes': torch.cuda.max_memory_reserved(),
        'rss_and_gpu_bytes_are_not_additive': True,
        'cgroup_before': cgroup_before, 'cgroup_after': cgroup_snapshot()}
    write(output/'report.json', report)
    print(json.dumps(report, sort_keys=True, allow_nan=False), flush=True)
    # Keep the inherited heavy fd through actual process exit, even if a GPU
    # runtime context survives Python tensor cleanup. The parent waits/reaps.
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--request-id', default='latest-flat')
    parser.add_argument('--request-sha256')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repeats', type=int, choices=range(1,6), default=3)
    parser.add_argument('--threads', type=int, choices=range(1,5), default=2)
    parser.add_argument('--inference-mode', choices=('serial','batch'), default='serial')
    parser.add_argument('--timeout-seconds', type=int, default=90)
    parser.add_argument('--child-proof', type=Path)
    parser.add_argument('--child-proof-sha256')
    parser.add_argument('--heavy-fd', type=int)
    args = parser.parse_args()
    os.umask(0o077)
    if not 20 <= args.timeout_seconds <= 180:
        parser.error('--timeout-seconds must be between 20 and 180')
    if args.child_proof is not None:
        if args.child_proof_sha256 is None or args.heavy_fd is None:
            parser.error('child requires pinned proof and inherited heavy fd')
        return child(args)
    if args.config is None or args.sha256 is None or args.output is None:
        parser.error('--config, --sha256 and new --output are required')
    return parent(args)


if __name__ == '__main__':
    raise SystemExit(main())
