#!/usr/bin/env python3
"""Historical CPU standby / fresh original Guard / GPU parity diagnostic.

The existing signed plan is provenance only. This creates no v2 registration,
forward seal, synthetic request, trading command or model update. Both models
use the selected immutable release's actual loader, tokenizer and value API.
"""
from __future__ import annotations
import argparse, array, ctypes, gc, hashlib, importlib.util, json, math, os, struct
from pathlib import Path
import resource, select, signal, socket, subprocess, sys, time

_spec = importlib.util.spec_from_file_location('_current100k_diag_benchmark', Path(__file__).with_name('benchmark.py'))
bench = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(bench)
SCOPE = 'HISTORICAL_CPU_STANDBY_PARITY_NOT_REGISTERED_FORWARD_PROFILE'
LIMIT = 8192


def need(value, reason):
    if not value: raise ValueError('CPU_STANDBY_DIAGNOSTIC_'+reason)


def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def source_pins():
    names = [Path(__file__).name, 'benchmark.py']
    if Path(__file__).with_name('direct_gpu_comparison.py').is_file(): names.append('direct_gpu_comparison.py')
    return {name: bench.sha(Path(__file__).with_name(name)) for name in names}


def comparison_source(path, expected):
    """Pin one optional standalone tokenizer source without importing ML."""
    need((path is None) == (expected is None), 'COMPARISON_TOKENIZER_PATH_AND_SHA_REQUIRED')
    if path is None: return None
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path and path.is_file()
         and path.stat().st_size <= 256*1024 and type(expected) is str and len(expected) == 64,
         'COMPARISON_TOKENIZER_SOURCE_SCOPE')
    raw = path.read_bytes(); need(hashlib.sha256(raw).hexdigest() == expected, 'COMPARISON_TOKENIZER_SOURCE_CHANGED')
    tree = bench.ast.parse(raw)
    need(not any(isinstance(node, bench.ast.ImportFrom) and node.level for node in bench.ast.walk(tree)),
         'COMPARISON_TOKENIZER_MUST_BE_STANDALONE')
    functions = [node for node in tree.body if isinstance(node, bench.ast.FunctionDef) and node.name == 'load_local_tokenizer']
    need(len(functions) == 1 and len(functions[0].args.args) == 1, 'COMPARISON_TOKENIZER_API_REQUIRED')
    return {'path': str(path), 'sha256': expected}


def load_comparison_tokenizer(selection, directory):
    checked = comparison_source(selection['path'], selection['sha256'])
    path = Path(checked['path']); raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == checked['sha256'], 'COMPARISON_TOKENIZER_CHANGED_BEFORE_IMPORT')
    spec = importlib.util.spec_from_file_location('_heptatrader_tokenizer_comparison_'+checked['sha256'][:16], path)
    module = importlib.util.module_from_spec(spec)
    # Compile the verified source bytes, never an unpinned sibling .pyc. This
    # private module does not replace laya_cell.tokenizer or any authority API.
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    return module.load_local_tokenizer(directory)


def fd_snapshot(pid):
    paths = list((Path('/proc')/str(pid)/'fd').iterdir())
    need(len(paths) <= 4096, 'FD_INSPECTION_BOUND')
    rows = []
    for path in paths:
        try: target = os.readlink(path); info = path.stat()
        except FileNotFoundError: continue
        rows.append({'fd': int(path.name), 'target': target, 'identity': [info.st_dev, info.st_ino]})
    need(not any(row['target'] == '/dev/kfd' or row['target'].startswith('/dev/dri/') for row in rows), 'GPU_DEVICE_FD_DURING_CPU_PHASE')
    return rows


def cpu_proof(torch, models):
    need(not torch.cuda.is_initialized(), 'CUDA_INITIALIZED_DURING_CPU_PHASE')
    count = size = 0
    for model in models:
        for tensor in list(model.parameters())+list(model.buffers()):
            need(tensor.device.type == 'cpu' and getattr(tensor, 'grad', None) is None, 'CPU_TENSOR_REQUIRED')
            count += 1; size += tensor.numel()*tensor.element_size()
    need(count > 0, 'EMPTY_MODEL'); fd_snapshot(os.getpid())
    return {'all_parameters_and_buffers_cpu': True, 'cuda_initialized': False,
            'gpu_device_fds': [], 'tensor_count': count, 'tensor_bytes_including_aliases': size}


class Channel:
    """Private pre-sandbox socketpair; exact bounded messages and one CLOEXEC FD."""
    def __init__(self, endpoint, proof_sha): self.socket = endpoint; self.proof_sha = proof_sha
    def send(self, kind, fields=None, fd=None):
        raw = canonical({'kind': kind, 'proof_sha256': self.proof_sha, **(fields or {})})
        need(len(raw) <= LIMIT, 'PACKET_BOUND')
        ancillary = [] if fd is None else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))]
        need(self.socket.sendmsg([raw], ancillary) == len(raw), 'SHORT_PACKET')
    def receive(self, timeout):
        if not select.select([self.socket], [], [], timeout)[0]: return None
        received = []; size = array.array('i').itemsize
        raw, controls, flags, _ = self.socket.recvmsg(LIMIT, socket.CMSG_SPACE(8*size), socket.MSG_CMSG_CLOEXEC)
        try:
            unexpected = False
            for level, kind, data in controls:
                if (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS):
                    values = array.array('i'); values.frombytes(data[:len(data)-len(data)%size]); received.extend(values)
                else: unexpected = True
            need(raw or controls, 'CHILD_CHANNEL_EOF')
            need(not unexpected and raw and not flags & (socket.MSG_TRUNC|socket.MSG_CTRUNC)
                 and len(received) <= 1, 'BAD_CONTROL_OR_TRUNCATED_PACKET')
            value = json.loads(raw)
            need(type(value) is dict and canonical(value) == raw
                 and value.get('proof_sha256') == self.proof_sha, 'PACKET_SCOPE')
            return value, received.pop() if received else None
        finally:
            for fd in received: os.close(fd)
    def close(self): self.socket.close()


def expect(received, kind, fields=(), *, descriptor=False):
    need(received is not None, 'PACKET_REQUIRED')
    message, fd = received
    try:
        need(set(message) == {'kind', 'proof_sha256', *fields} and message['kind'] == kind, 'PACKET_FIELDS')
        need((fd is not None) == descriptor, 'PACKET_DESCRIPTOR_SCOPE')
    except BaseException:
        if fd is not None: os.close(fd)
        raise
    return message, fd


def admission(report, checked_ns):
    need(report.get('role') == 'value-predictor' and report.get('admit_new_worker') is True
         and not report.get('reasons'), 'ORIGINAL_GUARD_DENIED')
    need(0 <= int(time.time()*1000)-report['at_ms'] < 5000
         and 0 <= time.monotonic_ns()-checked_ns < 5000000000, 'ORIGINAL_ADMISSION_EXPIRED')


def original_inputs(args):
    config = bench.bootstrap(args.config, args.sha256)
    need(config.get('hip_runtime') is not None, 'ACCEPTED_HIP_REQUIRED')
    from laya_cell.current100k_forward_worker import validate_runtime
    from laya_cell.financial_forward_evaluate import validate_registration
    from laya_cell.financial_forward_source import ingress
    from laya_cell import resident_hip_runtime as hip
    from historical_training.resident_resource_guard import verify_config
    validate_runtime(config)
    need(bench.sha(config['guard_config']) == config['guard_config_sha256'], 'ORIGINAL_GUARD_CHANGED')
    guard = bench.read(config['guard_config']); hip.check_guard_selection(config, verify_config(guard))
    paths = list((Path(config['forward_root'])/'plans').glob('*/plan.json'))
    need(len(paths) == 1, 'EXACTLY_ONE_ORIGINAL_PLAN_REQUIRED')
    plan = bench.read(paths[0]); candidate = validate_registration(config, paths[0], plan)
    api = bench.model_api(config, candidate)
    tree = bench.ast.parse((Path(config['worker_source_root'])/'learn/laya_cell/model.py').read_text())
    loader = next(node for node in tree.body if isinstance(node, bench.ast.FunctionDef) and node.name == 'load_verified_laya')
    need('enforce_gpu' in {arg.arg for arg in [*loader.args.args, *loader.args.kwonlyargs]}, 'EXPLICIT_CPU_LOADER_API_REQUIRED')
    _, requests, _, issues = ingress(plan['producer_registration'], config['period_sha256'])
    need(not issues, 'ORIGINAL_INGRESS_UNAVAILABLE')
    return config, guard, paths[0], plan, candidate, api, requests


def same_request(item, current):
    need(current is not None and all(item.get(key) == current.get(key) for key in
         ('request', 'request_sha256', 'producer_manifest_sha256', 'source_record_sha256')), 'ORIGINAL_REQUEST_CHANGED')


def float32_record(values, shape):
    need(shape in ([2, 768], [2]), 'CPU_COMPARISON_SHAPE')
    if len(shape) == 2:
        need(type(values) is list and len(values) == shape[0]
             and all(type(row) is list and len(row) == shape[1] for row in values), 'CPU_FEATURE_SHAPE')
        flat = [value for row in values for value in row]
    else:
        need(type(values) is list and len(values) == shape[0], 'CPU_VALUE_SHAPE'); flat = values
    need(all(type(value) in (int, float) and math.isfinite(value) for value in flat), 'CPU_NONFINITE_COMPARISON')
    raw = struct.pack('='+str(len(flat))+'f', *flat)
    return {'shape': shape, 'dtype': 'torch.float32', 'sha256': hashlib.sha256(raw).hexdigest()}


def compare_completed_children(proof, staged, states, direct, future):
    """CPU-only exact comparison after both model processes have been reaped."""
    item = proof['request_item']; plan = proof['plan']; candidate = proof['candidate']
    for document in (staged, direct):
        need(document['scope'] == SCOPE and document['config_sha256'] == proof['config_sha256']
             and document['plan_id'] == plan['plan_id'] and document['candidate_id'] == candidate['candidate_id']
             and document['request_id'] == item['request']['request_id']
             and document['request_sha256'] == item['request_sha256'], 'COMPLETED_CHILD_IDENTITY')
    need(direct['base_id'] == candidate['base_id'] and direct['head_sha256'] == plan['head_tensor_sha256'], 'DIRECT_HEAD_IDENTITY')
    need(all(type(value) is dict and value for value in (states.get('cpu_initial'), states.get('staged_after'),
             direct.get('initial_state'), direct.get('after_state')))
         and all(type(value) is list and len(value) == 2 for value in (states.get('cpu_tokenizer'), direct.get('tokenizer'))),
         'COMPLETED_CHILD_STATE_REQUIRED')
    staged_features = float32_record(staged['features'], [2,768]); direct_features = float32_record(direct['features'], [2,768])
    staged_values = float32_record(staged['values'], [2]); direct_values = float32_record(direct['values'], [2])
    need(staged_features == staged['staged']['feature_record'] and direct_features == direct['inference']['feature_record'],
         'CPU_SERIALIZED_FEATURE_DIGEST_CHANGED')
    initial = diff_state(states['cpu_initial'], direct['initial_state']); after = diff_state(states['staged_after'], direct['after_state'])
    tokens = states['cpu_tokenizer'] == direct['tokenizer']
    historical = initial['exact'] and after['exact'] and tokens and staged_features == direct_features and staged_values == direct_values
    need(type(future) is dict and type(future.get('requested')) is bool, 'FUTURE_TOKENIZER_RESULT_SCOPE')
    if proof.get('comparison_tokenizer') is not None:
        need(future.get('requested') is True and future.get('source') == proof['comparison_tokenizer'], 'FUTURE_TOKENIZER_RESULT_PIN')
    else: need(future['requested'] is False, 'UNREQUESTED_FUTURE_TOKENIZER_RESULT')
    exact = historical and (not future['requested'] or future.get('exact') is True)
    result = {'state': 'HISTORICAL_PARITY_EXACT' if exact else 'HISTORICAL_PARITY_DIFFERENCE_REVIEW_REQUIRED',
        'scope': SCOPE, 'exact_parity': exact, 'historical_loader_exact_parity': historical, 'future_tokenizer_parity': future,
        'forward_seal_created': False, 'broker_orders_sent': 0, 'v2_plan_registered': False, 'optimizer_used': False,
        'publish_path_exercised': False, 'config_sha256': proof['config_sha256'], 'plan_id': plan['plan_id'],
        'plan_sha256': proof['plan_sha256'], 'candidate_id': candidate['candidate_id'], 'base_id': candidate['base_id'],
        'head_sha256': plan['head_tensor_sha256'], 'request_id': item['request']['request_id'], 'request_sha256': item['request_sha256'],
        'producer_manifest_sha256': item['producer_manifest_sha256'], 'model_api': proof['model_api'],
        'source_record_sha256': item['source_record_sha256'], 'source_prefix': item['source_prefix'],
        'source_sha256': proof['source_sha256'], 'cpu_proof': staged['cpu_proof'],
        'initial_parameters_and_all_buffers': initial, 'after_inference_parameters_and_all_buffers': after,
        'tokenizer_input_ids_and_masks_exact': tokens, 'features_bitwise_equal': staged_features == direct_features,
        'values_bitwise_equal': staged_values == direct_values,
        'feature_max_absolute_difference': max(abs(a-b) for left,right in zip(staged['features'],direct['features']) for a,b in zip(left,right)),
        'value_max_absolute_difference_usd': max(abs(a-b) for a,b in zip(staged['values'],direct['values'])),
        'staged': staged['staged'], 'direct': direct['inference'], 'timing_ms': dict(staged['timing_ms']),
        'direct_child_timing_ms': direct['timing_ms'], 'hip_runtime_proof': staged['hip_runtime_proof'],
        'direct_hip_runtime_proof': direct['hip_runtime_proof'], 'comparison_mode': 'separate-process',
        'model_processes_never_overlap': True, 'rss_and_gpu_bytes_are_not_additive': True,
        'process_cold_cache_state_uncontrolled': True, 'cgroup_before': staged['cgroup_before'], 'cgroup_after': direct['cgroup_after']}
    result['timing_ms']['direct_model_and_head_load'] = direct['timing_ms']['model_and_head_load']
    for field in ('max_process_rss_bytes','max_gpu_allocated_bytes','max_gpu_reserved_bytes'):
        result[field] = max(staged[field], direct[field])
    return result


def reap(process):
    if process.poll() is not None: return True
    try: process.terminate()
    except ProcessLookupError: pass
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try: process.kill()
        except ProcessLookupError: pass
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired: return False
    return process.poll() is not None


def original_guard_recheck(gate, config):
    need(bench.sha(config['guard_config']) == config['guard_config_sha256'], 'ORIGINAL_GUARD_CHANGED')
    paths = list(sys.path)
    try: report = gate.check()
    finally: sys.path[:] = paths
    need(report.get('admit_new_worker') is True, 'ORIGINAL_GUARD_RECHECK_DENIED')


def start_direct_child(config, original_proof_path, proof, output, gate, checked_ns):
    from laya_cell import resident_hip_runtime as hip
    need(gate.lease.fd is not None and source_pins() == proof['source_sha256']
         and 'direct_gpu_comparison.py' in proof['source_sha256'], 'DIRECT_SOURCE_OR_LEASE_CHANGED')
    destination = output/'direct-baseline'; destination.mkdir(mode=0o700, exist_ok=False)
    direct_proof = {'scope': SCOPE, 'original_input_proof_path': str(original_proof_path),
        'original_input_proof_sha256': bench.sha(original_proof_path),
        'staged_result_path': str(output/'staged-result.json'), 'staged_result_sha256': bench.sha(output/'staged-result.json'),
        'staged_state_path': str(output/'staged-state-hashes.json'), 'staged_state_sha256': bench.sha(output/'staged-state-hashes.json'),
        'admission': gate.last_admission, 'checked_ns': checked_ns, 'parent_pid': os.getpid(),
        'heavy_lock_path': str(gate.lease.path), 'heavy_lock_identity': bench.lock_identity(gate.lease.fd),
        'output_dir': str(destination), 'source_sha256': proof['source_sha256']}
    path = output/'direct-proof.json'; bench.write(path, direct_proof); pin = bench.sha(path)
    environment = hip.worker_environment(config) or dict(os.environ)
    environment.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS=str(proof['threads']), OPENBLAS_NUM_THREADS='1',
                       TOKENIZERS_PARALLELISM='false', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    process = subprocess.Popen([config['python'], '-B', str(Path(__file__).with_name('direct_gpu_comparison.py')),
        '--proof', str(path), '--proof-sha256', pin, '--heavy-fd', str(gate.lease.fd)],
        env=environment, stdin=subprocess.DEVNULL, close_fds=True, pass_fds=(gate.lease.fd,))
    return process, path, pin


def wait_direct_child(process, gate, config, deadline, *, timeout_reason='DIRECT_CHILD_FIXED_TIMEOUT'):
    checked = time.monotonic()
    while process.poll() is None:
        need(time.monotonic() < deadline, timeout_reason)
        if time.monotonic()-checked >= 5:
            original_guard_recheck(gate, config); checked = time.monotonic()
        time.sleep(.05)
    return process.returncode


def parent(args):
    started = time.monotonic_ns(); started_at = int(time.time()*1000)
    comparison = comparison_source(getattr(args, 'comparison_tokenizer_source', None),
                                   getattr(args, 'comparison_tokenizer_sha256', None))
    comparison_mode = getattr(args, 'comparison_mode', 'separate-process')
    need(comparison_mode in ('same-process', 'separate-process'), 'COMPARISON_MODE')
    if comparison_mode == 'separate-process':
        need('direct_gpu_comparison.py' in source_pins(), 'DIRECT_COMPARISON_SOURCE_REQUIRED')
    config, guard, plan_path, plan, candidate, api, requests = original_inputs(args)
    item = bench.select_request(requests, args.request_id, plan, started_at)
    need(not args.request_sha256 or item['request_sha256'] == args.request_sha256, 'REQUEST_SHA256_CHANGED')
    output = args.output.resolve(); live = Path(config['forward_root']).resolve()
    need(not output.is_relative_to(live) and not live.is_relative_to(output), 'SEPARATE_NEW_OUTPUT_REQUIRED')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    from laya_cell.current100k_forward_worker import acquire_value_resources
    from historical_training.resident_resource_guard import Gate
    from laya_cell.financial_forward_source import ingress
    from laya_cell import resident_hip_runtime as hip
    need('torch' not in sys.modules, 'CPU_PARENT_IMPORTED_TORCH')
    gate = Gate(guard, 'value-predictor'); process = channel = None; reaped = True
    phase = 'INITIAL_ADMISSION'; error = None; code = 3; receipt = None
    deadline = time.monotonic()+args.timeout_seconds; initial_closed = False; idle_until = None; fresh_granted = False
    staged_reaped = staged_released = direct_granted = direct_started = direct_reaped = False
    staged_receipt = None; staged_exit = direct_exit = None
    try:
        if not acquire_value_resources(gate, {'diagnostic': SCOPE, 'phase': 'CPU_PRELOAD', 'plan_id': plan['plan_id']}):
            bench.write(output/'report.json', {'state': 'HELD_ORIGINAL_GUARD_NO_MODEL_LOADED',
                'admission': gate.last_admission, 'scope': SCOPE, 'forward_seal_created': False, 'broker_orders_sent': 0})
            return 3
        admitted_ns = time.monotonic_ns()
        proof = {'schema': 'heptatrader.cpu-standby-historical-diagnostic.v1', 'scope': SCOPE,
            'source_sha256': source_pins(), 'config_path': str(args.config.resolve()), 'config_sha256': args.sha256,
            'comparison_tokenizer': comparison,
            'comparison_mode': comparison_mode,
            'plan_path': str(plan_path), 'plan_sha256': bench.sha(plan_path), 'plan': plan, 'candidate': candidate,
            'request_item': item, 'model_api': api, 'admission': gate.last_admission, 'checked_ns': admitted_ns,
            'parent_pid': os.getpid(), 'heavy_lock_path': str(gate.lease.path),
            'heavy_lock_identity': bench.lock_identity(gate.lease.fd), 'started_at_ms': started_at, 'started_ns': started,
            'threads': args.threads, 'timeout_seconds': args.timeout_seconds,
            'original_front_and_cpu_admission_ms': (admitted_ns-started)/1e6}
        proof_path = output/'input-proof.json'; bench.write(proof_path, proof); pin = bench.sha(proof_path)
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        channel = Channel(left, pin)
        environment = hip.worker_environment(config) or dict(os.environ)
        environment.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS=str(args.threads), OPENBLAS_NUM_THREADS='1',
                           TOKENIZERS_PARALLELISM='false', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        try:
            process = subprocess.Popen([config['python'], '-B', str(Path(__file__).resolve()),
                '--child-proof', str(proof_path), '--child-proof-sha256', pin,
                '--heavy-fd', str(gate.lease.fd), '--socket-fd', str(right.fileno())],
                env=environment, stdin=subprocess.DEVNULL, close_fds=True, pass_fds=(gate.lease.fd, right.fileno()))
        finally: right.close()
        reaped = False; phase = 'CPU_LOADING'; last_guard = time.monotonic()
        while True:
            # Accounting of completed work precedes later resource/drain checks.
            ready = channel.receive(0)
            if ready is not None and phase == 'GPU_PARITY' and ready[0].get('kind') == 'DONE':
                message, _ = expect(ready, 'DONE', ('report_sha256', 'exact_parity'))
                need(comparison_mode == 'same-process', 'SEPARATE_PROCESS_RESULT_REQUIRED')
                need(type(message['exact_parity']) is bool and bench.sha(output/'report.json') == message['report_sha256'], 'REPORT_PIN')
                receipt = message; code = 0 if message['exact_parity'] else 4; phase = 'COMPLETE'; break
            need(time.monotonic() < deadline, 'FIXED_TIMEOUT')
            if time.monotonic()-last_guard >= 5:
                original_guard_recheck(gate, config)
                last_guard = time.monotonic()
            child_code = process.poll()
            if ready is None: ready = channel.receive(0 if child_code is not None else .25)
            need(child_code is None or ready is not None, 'CHILD_EXIT_WITHOUT_RECEIPT')
            if ready is not None:
                if phase == 'CPU_LOADING':
                    message, _ = expect(ready, 'CPU_READY', ('cpu_receipt_sha256',))
                    need(bench.sha(output/'cpu-ready.json') == message['cpu_receipt_sha256'], 'CPU_PROOF_PIN')
                    cpu = bench.read(output/'cpu-ready.json')['cpu_proof']
                    need(cpu['all_parameters_and_buffers_cpu'] is True and cpu['cuda_initialized'] is False
                         and cpu['gpu_device_fds'] == [] and cpu['tensor_count'] > 0, 'CPU_PROOF_REQUIRED')
                    fd_snapshot(process.pid); channel.send('RELEASE_INITIAL'); phase = 'CPU_RELEASING'
                elif phase == 'CPU_RELEASING':
                    expect(ready, 'CPU_IDLE_UNLOCKED')
                    rows = fd_snapshot(process.pid)
                    need(not any(row['identity'] == proof['heavy_lock_identity'] for row in rows), 'CHILD_KEPT_INITIAL_LEASE')
                    gate.release(); need(gate.lease.fd is None, 'PARENT_KEPT_INITIAL_LEASE')
                    initial_closed = True; phase = 'CPU_IDLE'; idle_until = time.monotonic()+1
                    bench.write(output/'cpu-idle.json', {'at_ms': int(time.time()*1000), 'scope': SCOPE,
                        'parent_initial_heavy_fd_closed': True, 'child_initial_heavy_fd_closed': True,
                        'parent_observed_child_gpu_device_fds': [], 'fixed_idle_seconds': 1})
                    print(json.dumps({'phase': phase, 'scope': SCOPE, 'initial_lease_released': True}), flush=True)
                elif phase == 'GPU_PARITY':
                    if comparison_mode == 'same-process':
                        message, _ = expect(ready, 'DONE', ('report_sha256', 'exact_parity'))
                        need(type(message['exact_parity']) is bool and bench.sha(output/'report.json') == message['report_sha256'], 'REPORT_PIN')
                        receipt = message; code = 0 if message['exact_parity'] else 4; phase = 'COMPLETE'; break
                    message, _ = expect(ready, 'STAGED_DONE', ('files_sha256',))
                    files = message['files_sha256']; required = {'staged-result.json','staged-state-hashes.json','allocation-after-destroy.json'}
                    if comparison is not None: required.add('future-tokenizer-result.json')
                    need(type(files) is dict and set(files) == required
                         and all(bench.sha(output/name) == pin for name,pin in files.items()), 'STAGED_RESULT_PINS')
                    staged_receipt = message
                    # The second model cannot exist until the entire first
                    # process (including persistent HIP workspace) has exited.
                    # Give interpreter/ROCm teardown a bounded natural exit;
                    # timeout/error falls through to terminate/kill/reap in
                    # finally, retaining the lease and preventing baseline.
                    phase = 'STAGED_PROCESS_EXIT'
                    staged_exit = wait_direct_child(process, gate, config, min(deadline, time.monotonic()+10),
                        timeout_reason='STAGED_CHILD_NATURAL_EXIT_TIMEOUT')
                    reaped = True; staged_reaped = True
                    need(staged_exit == 0, 'STAGED_CHILD_FAILED:'+str(staged_exit))
                    gate.record_native_exit(staged_exit, {'diagnostic': SCOPE, 'phase': 'STAGED_PROCESS_REAPED'})
                    gate.release(); need(gate.lease.fd is None, 'STAGED_GPU_LEASE_NOT_RELEASED')
                    staged_released = True; channel.close(); channel = None; process = None
                    phase = 'DIRECT_ORIGINAL_ADMISSION'
                    current_config, _, current_plan_path, current_plan, current_candidate, current_api, current = original_inputs(args)
                    need(current_config == config and current_plan_path == plan_path and current_plan == plan
                         and current_candidate == candidate and current_api == api, 'ORIGINAL_INPUT_CHANGED_BETWEEN_CHILDREN')
                    same_request(item, current.get(item['request']['request_id']))
                    need(source_pins() == proof['source_sha256'] and time.monotonic() < deadline, 'DIRECT_SOURCE_OR_DEADLINE_CHANGED')
                    if not acquire_value_resources(gate, {'diagnostic': SCOPE, 'phase': 'DIRECT_GPU_BASELINE',
                            'plan_id': plan['plan_id'], 'request_id': item['request']['request_id'],
                            'request_sha256': item['request_sha256']}):
                        bench.write(output/'report.json', {'state': 'DIRECT_ORIGINAL_GUARD_DENIED_NO_SECOND_GPU_CHILD',
                            'scope': SCOPE, 'admission': gate.last_admission, 'staged_child_reaped': True,
                            'staged_gpu_lease_released_after_reap': True, 'forward_seal_created': False, 'broker_orders_sent': 0})
                        phase = 'DIRECT_GUARD_DENIED'; break
                    direct_granted = True; checked_ns = time.monotonic_ns()
                    process, direct_path, direct_pin = start_direct_child(config, proof_path, proof, output, gate, checked_ns)
                    direct_started = True
                    reaped = False; phase = 'DIRECT_GPU_BASELINE'
                    direct_exit = wait_direct_child(process, gate, config, deadline)
                    reaped = True; direct_reaped = True
                    need(direct_exit == 0, 'DIRECT_CHILD_FAILED:'+str(direct_exit))
                    direct = bench.read(output/'direct-baseline/result.json')
                    need(direct['direct_proof_sha256'] == direct_pin
                         and direct['original_input_proof_sha256'] == bench.sha(proof_path)
                         and direct['source_sha256'] == proof['source_sha256'], 'DIRECT_RESULT_PROOF_PIN')
                    # Check that the completed first-child evidence remained
                    # unchanged through the direct child and original Guard.
                    need(all(bench.sha(output/name) == pin for name,pin in files.items()), 'STAGED_INPUT_CHANGED_BETWEEN_CHILDREN')
                    staged = bench.read(output/'staged-result.json'); states = bench.read(output/'staged-state-hashes.json')
                    future = (bench.read(output/'future-tokenizer-result.json')['future_tokenizer_parity']
                              if comparison is not None else {'requested':False})
                    report = compare_completed_children(proof, staged, states, direct, future)
                    allocation = bench.read(output/'allocation-after-destroy.json')
                    report.update(first_model_gpu_allocated_bytes_after_destroy=allocation['first_model_memory_allocated_bytes'],
                        first_model_memory_released_by='PROCESS_EXIT_AND_REAP', staged_child_exit_code=staged_exit,
                        direct_child_exit_code=direct_exit, direct_proof_sha256=direct_pin,
                        staged_result_files_sha256=files, direct_result_sha256=bench.sha(output/'direct-baseline/result.json'))
                    for field in ('max_process_rss_bytes','max_gpu_allocated_bytes','max_gpu_reserved_bytes'):
                        report[field] = max(report[field], allocation[field])
                    bench.write(output/'parity-state-hashes.json', {'cpu_initial': states['cpu_initial'],
                        'staged_after': states['staged_after'], 'direct_initial': direct['initial_state'],
                        'direct_after': direct['after_state'], 'cpu_tokenizer': states['cpu_tokenizer'],
                        'direct_tokenizer': direct['tokenizer'],
                        'future_tokenizer': (bench.read(output/'future-tokenizer-result.json')['tokenizer_tensors']
                                             if comparison is not None else None)})
                    bench.write(output/'report.json', report)
                    receipt = {'kind':'PARENT_TWO_PROCESS_COMPARISON', 'report_sha256':bench.sha(output/'report.json'),
                               'exact_parity':report['exact_parity']}
                    code = 0 if report['exact_parity'] else 4; phase = 'COMPLETE'; break
                else: expect(ready, 'NO_MESSAGE_IS_VALID_IN_THIS_PHASE')
            if phase == 'CPU_IDLE' and time.monotonic() >= idle_until:
                need(gate.lease.fd is None, 'FRESH_GATE_REQUIRES_CLOSED_INITIAL_LEASE')
                current_config, _, current_plan_path, current_plan, current_candidate, current_api, current = original_inputs(args)
                need(current_config == config and current_plan_path == plan_path and current_plan == plan
                     and current_candidate == candidate and current_api == api, 'ORIGINAL_INPUT_CHANGED_DURING_CPU_IDLE')
                same_request(item, current.get(item['request']['request_id']))
                if not acquire_value_resources(gate, {'diagnostic': SCOPE, 'phase': 'GPU_PARITY', 'plan_id': plan['plan_id'],
                        'historical_request_id': item['request']['request_id'], 'request_sha256': item['request_sha256']}):
                    bench.write(output/'report.json', {'state': 'FRESH_ORIGINAL_GUARD_DENIED_NO_GPU',
                        'scope': SCOPE, 'admission': gate.last_admission, 'forward_seal_created': False, 'broker_orders_sent': 0})
                    phase = 'FRESH_GUARD_DENIED'; break
                fresh_granted = True
                checked_ns = time.monotonic_ns()
                _, current, _, issues = ingress(plan['producer_registration'], config['period_sha256'])
                need(not issues, 'FRESH_ORIGINAL_INGRESS_UNAVAILABLE'); same_request(item, current.get(item['request']['request_id']))
                fresh = {'proof_sha256': pin, 'admission': gate.last_admission, 'checked_ns': checked_ns,
                         'request_id': item['request']['request_id'], 'request_sha256': item['request_sha256']}
                admission(fresh['admission'], fresh['checked_ns'])
                bench.write(output/'fresh-admission.json', fresh)
                channel.send('RUN_GPU_PARITY', {'admission_sha256': bench.sha(output/'fresh-admission.json')}, gate.lease.fd)
                phase = 'GPU_PARITY'; last_guard = time.monotonic()
    except BaseException as exc:
        error = exc
    finally:
        if process is not None:
            reaped = reap(process)
            if direct_started:
                direct_reaped = reaped; direct_exit = process.returncode
            else:
                staged_reaped = reaped; staged_exit = process.returncode
            if reaped: gate.record_native_exit(process.returncode, {'diagnostic': SCOPE, 'phase': phase})
        if channel is not None: channel.close()
        if reaped: gate.release()
        if staged_reaped and fresh_granted and not direct_granted: staged_released = gate.lease.fd is None
        bench.write(output/'lifecycle.json', {'scope': SCOPE, 'phase': phase, 'initial_lease_released': initial_closed,
            'child_reaped': reaped, 'fresh_admission_granted': fresh_granted,
            'fresh_lease_released_after_reap': (reaped and gate.lease.fd is None) if fresh_granted else None,
            'completion_receipt': receipt, 'error': None if error is None else type(error).__name__+':'+str(error)[:400],
            'comparison_mode': comparison_mode, 'staged_completion_receipt': staged_receipt,
            'staged_child_reaped': staged_reaped, 'staged_gpu_lease_released_after_reap': staged_released,
            'direct_original_admission_granted': direct_granted, 'direct_child_started': direct_started, 'direct_child_reaped': direct_reaped,
            'direct_lease_released_after_reap': (reaped and gate.lease.fd is None) if direct_started else None,
            'staged_child_exit_code': staged_exit, 'direct_child_exit_code': direct_exit,
            'forward_seal_created': False, 'broker_orders_sent': 0, 'parent_imported_torch': 'torch' in sys.modules})
    if error is not None: raise error
    need(reaped, 'CHILD_NOT_REAPED'); return code


def tensor_record(torch, tensor):
    value = tensor.detach().cpu().contiguous()
    raw = value.reshape(-1).view(torch.uint8).numpy().tobytes()
    return {'shape': list(value.shape), 'dtype': str(value.dtype), 'sha256': hashlib.sha256(raw).hexdigest()}


def model_state(torch, models):
    """Include all registered buffers, including nonpersistent buffers."""
    state = {}
    for prefix, model in models.items():
        for kind, values in (('parameter', model.named_parameters()), ('buffer', model.named_buffers())):
            for name, tensor in values: state[prefix+'/'+kind+'/'+name] = tensor_record(torch, tensor)
    return state


def tokenizer_state(torch, tokenizer, rows):
    result = []
    for row in rows:
        batch = tokenizer([row['feature_text']], padding=True, truncation=False, return_tensors='pt')
        need({'input_ids', 'attention_mask'} <= set(batch), 'TOKENIZER_FIELDS_REQUIRED')
        need(all(tensor.device.type == 'cpu' for tensor in batch.values()), 'TOKENIZER_MUST_STAY_CPU')
        result.append({name: tensor_record(torch, batch[name]) for name in ('input_ids', 'attention_mask', 'token_type_ids') if name in batch})
    return result


def infer(torch, value_module, raw, tokenizer, head, rows, api, profile):
    start = time.monotonic_ns()
    encoded = [value_module.features_from_laya(raw, tokenizer, [row]) for row in rows]
    features = torch.cat([pair[0] for pair in encoded])
    with torch.no_grad(): values = bench.benchmark_values(value_module, head, features, api, profile)
    need(bool(torch.isfinite(features).all()) and bool(torch.isfinite(values).all()), 'NONFINITE_PARITY_OUTPUT')
    cpu_features = features.detach().cpu(); cpu_values = values.detach().cpu()
    torch.cuda.synchronize(); end = time.monotonic_ns()
    return cpu_features, cpu_values, {'serial_action_features': [pair[1] for pair in encoded],
        'values_usd': dict(zip(('BUY', 'SELL'), cpu_values.tolist())), 'inference_ms': (end-start)/1e6,
        'completed_ns': end, 'feature_record': tensor_record(torch, cpu_features)}


def diff_state(left, right):
    names = sorted(set(left)|set(right))
    different = [name for name in names if left.get(name) != right.get(name)]
    return {'exact': not different, 'tensor_count': len(names), 'different_tensor_names': different}


def allocation_snapshot(torch):
    """Metadata only, under the still-held GPU lease; never clear global caches."""
    import itertools, warnings
    report = {'memory_allocated_bytes': torch.cuda.memory_allocated(),
        'memory_reserved_bytes': torch.cuda.memory_reserved(), 'memory_stats': dict(torch.cuda.memory_stats()),
        'live_gpu_tensors': [], 'scan_truncated': False,
        'tensor_contents_captured': False, 'global_runtime_cache_reset': False}
    begin = time.monotonic(); objects = gc.get_objects(); report['gc_objects_observed'] = len(objects)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for obj in objects:
            if time.monotonic()-begin > 5 or len(report['live_gpu_tensors']) >= 256:
                report['scan_truncated'] = True; break
            try:
                if not isinstance(obj, torch.Tensor) or obj.device.type != 'cuda' or obj.is_meta: continue
                refs = []
                for ref in itertools.islice(gc.get_referrers(obj), 16):
                    if ref is objects: continue
                    entry = {'type': type(ref).__module__+'.'+type(ref).__name__}
                    if type(ref) is dict:
                        entry['matching_keys'] = [key for key, value in itertools.islice(ref.items(), 128)
                                                   if value is obj and type(key) is str][:8]
                    refs.append(entry)
                storage = obj.untyped_storage()
                report['live_gpu_tensors'].append({'type': type(obj).__module__+'.'+type(obj).__name__,
                    'shape': list(obj.shape), 'dtype': str(obj.dtype), 'device': str(obj.device),
                    'numel': obj.numel(), 'storage_bytes': storage.nbytes(),
                    'storage_identity_sha256': hashlib.sha256(str(storage.data_ptr()).encode()).hexdigest(),
                    'requires_grad': bool(obj.requires_grad), 'is_leaf': bool(obj.is_leaf),
                    'grad_fn_type': type(obj.grad_fn).__name__ if obj.grad_fn is not None else None, 'referrers': refs})
            except Exception as exc:
                report.setdefault('inspection_errors', []).append(type(exc).__name__)
                if len(report['inspection_errors']) >= 16: report['scan_truncated'] = True; break
    del objects
    try:
        segments = torch.cuda.memory_snapshot()
        report['allocator_segments'] = [{key: segment.get(key) for key in
            ('device', 'total_size', 'allocated_size', 'active_size', 'segment_type', 'stream')}
            | {'blocks': [{key: block.get(key) for key in ('size', 'requested_size', 'state')}
                          for block in segment.get('blocks', [])[:256]]} for segment in segments[:256]]
        report['allocator_segments_truncated'] = len(segments) > 256
    except Exception as exc: report['allocator_snapshot_unavailable'] = type(exc).__name__
    return report


def child(args):
    child_started = time.monotonic_ns()
    need(bench.sha(args.child_proof) == args.child_proof_sha256, 'INPUT_PROOF_CHANGED')
    proof = bench.read(args.child_proof)
    need(proof['scope'] == SCOPE and proof['source_sha256'] == source_pins(), 'DIAGNOSTIC_CODE_CHANGED')
    parent_pid = proof['parent_pid']
    need(type(parent_pid) is int and parent_pid > 1 and os.getppid() == parent_pid, 'ADMITTING_PARENT_REQUIRED')
    libc = ctypes.CDLL(None, use_errno=True)
    need(libc.prctl(1, signal.SIGKILL, 0, 0, 0) == 0 and os.getppid() == parent_pid, 'PARENT_DEATH_GUARD_REQUIRED')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    need(bench.lock_identity(args.heavy_fd) == proof['heavy_lock_identity'], 'ORIGINAL_HEAVY_FD_CHANGED')
    info = Path(proof['heavy_lock_path']).lstat()
    need([info.st_dev, info.st_ino] == proof['heavy_lock_identity'], 'ORIGINAL_HEAVY_PATH_CHANGED')
    os.set_inheritable(args.heavy_fd, False)
    channel = Channel(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0, fileno=args.socket_fd), args.child_proof_sha256)
    admission(proof['admission'], proof['checked_ns'])
    config = bench.bootstrap(proof['config_path'], proof['config_sha256'])
    plan, candidate, item = proof['plan'], proof['candidate'], proof['request_item']; output = args.child_proof.parent
    need(bench.sha(proof['plan_path']) == proof['plan_sha256'], 'ORIGINAL_PLAN_CHANGED')
    api = bench.model_api(config, candidate); need(api == proof['model_api'], 'FROZEN_MODEL_API_CHANGED')
    comparison = proof.get('comparison_tokenizer')
    if comparison is not None:
        need(comparison_source(comparison['path'], comparison['sha256']) == comparison, 'COMPARISON_TOKENIZER_PIN')
    from laya_cell import resident_hip_runtime as hip
    from sandbox import enter
    context = hip.prepare_worker(config, 'predict')
    restrictions = enter({'output_dir': str(output), 'period_path': config['period_path'],
        'legacy_capture': plan['training_dataset_path'], 'oms_path': config['oms_path'],
        'export_dir': config['attribution_directory'],
        'read_only_paths': config['worker_read_only_paths'] + [config['worker_source_root'], sys.prefix,
            str(Path(__file__).resolve().parent), str(Path(plan['candidate_directory']))]
            + ([comparison['path']] if comparison is not None else [])
            + (context['readonly'] if context is not None else [])})
    # Do not merely omit the signing call: verify this diagnostic cannot read
    # the original role key through an accidentally broad allowlist either.
    try: fd = os.open(config['role_key_path'], os.O_RDONLY|os.O_CLOEXEC)
    except (PermissionError, FileNotFoundError): restrictions['original_role_key_read_denied'] = True
    else: os.close(fd); raise ValueError('CPU_STANDBY_DIAGNOSTIC_ROLE_KEY_READABLE')
    bench.write(output/'sandbox.json', restrictions)
    before_torch = time.monotonic_ns(); cgroup_before = bench.cgroup_snapshot()
    hip.activate(context)
    import torch
    hip_proof = hip.prove(context, 'AFTER_TORCH_BEFORE_MODEL')
    torch.set_num_threads(proof['threads']); after_torch = time.monotonic_ns()
    from laya_cell.model import load_verified_laya, require_pocket4_gpu
    from laya_cell import financial_value_train as value_module
    from laya_cell.current100k_forward import feature_rows
    from safetensors.torch import load_file
    base = Path(config['base_directory']); base_manifest = bench.read(base/'base-manifest.json')
    raw, tokenizer, base_id = load_verified_laya(base, base_manifest, enforce_gpu=False)
    raw.requires_grad_(False).eval(); need(base_id == candidate['base_id'], 'BASE_ID_CHANGED')
    head_path = Path(plan['candidate_directory'])/'value-head.safetensors'
    need(bench.sha(head_path) == plan['head_tensor_sha256'], 'ORIGINAL_HEAD_CHANGED')
    head = bench.load_benchmark_head(torch, value_module, load_file(str(head_path)), torch.device('cpu'), api, candidate.get('readout_profile'))
    cpu_loaded = time.monotonic_ns(); rows = feature_rows(plan, item['request'])
    cpu_only = cpu_proof(torch, [raw, head])
    cpu_state = model_state(torch, {'model': raw, 'head': head}); cpu_tokens = tokenizer_state(torch, tokenizer, rows)
    cpu_proof(torch, [raw, head]); cpu_ready_ns = time.monotonic_ns()
    bench.write(output/'cpu-ready.json', {'scope': SCOPE, 'at_ms': int(time.time()*1000), 'cpu_proof': cpu_only,
        'parameter_and_buffer_hashes': cpu_state, 'tokenizer': cpu_tokens,
        'cpu_model_and_head_load_ms': (cpu_loaded-after_torch)/1e6,
        'extra_parity_instrumentation_ms': (cpu_ready_ns-cpu_loaded)/1e6,
        'initial_heavy_lease_still_held': True})
    channel.send('CPU_READY', {'cpu_receipt_sha256': bench.sha(output/'cpu-ready.json')})
    def receive():
        while True:
            need(time.monotonic_ns()-proof['started_ns'] < proof['timeout_seconds']*1000000000, 'CHILD_FIXED_TIMEOUT')
            result = channel.receive(.25)
            if result is not None: return result
    expect(receive(), 'RELEASE_INITIAL')
    cpu_proof(torch, [raw, head]); os.close(args.heavy_fd)
    channel.send('CPU_IDLE_UNLOCKED')
    message, fresh_fd = expect(receive(), 'RUN_GPU_PARITY', ('admission_sha256',), descriptor=True)
    need(bench.lock_identity(fresh_fd) == proof['heavy_lock_identity'], 'NEW_ORIGINAL_HEAVY_FD_REQUIRED')
    os.set_inheritable(fresh_fd, False)
    fresh_path = output/'fresh-admission.json'; need(bench.sha(fresh_path) == message['admission_sha256'], 'FRESH_ADMISSION_PIN')
    fresh = bench.read(fresh_path)
    need(fresh['proof_sha256'] == args.child_proof_sha256 and fresh['request_id'] == item['request']['request_id']
         and fresh['request_sha256'] == item['request_sha256'], 'FRESH_ADMISSION_SCOPE')
    cpu_proof(torch, [raw, head]); admission(fresh['admission'], fresh['checked_ns'])
    # The first GPU API follows the second original admission and new FD.
    gpu_start = time.monotonic_ns(); device = require_pocket4_gpu()
    torch.cuda.set_per_process_memory_fraction(min(1., 4*1024**3/torch.cuda.get_device_properties(0).total_memory))
    torch.cuda.reset_peak_memory_stats(); setup_done = time.monotonic_ns()
    raw = raw.to(device); head = head.to(device); torch.cuda.synchronize(); copied = time.monotonic_ns()
    staged_features, staged_values, staged = infer(torch, value_module, raw, tokenizer, head, rows, api, candidate.get('readout_profile'))
    staged_timing = {'original_front_and_cpu_admission': proof['original_front_and_cpu_admission_ms'],
        'child_bootstrap_sandbox': (before_torch-child_started)/1e6,
        'torch_cpu_import': (after_torch-before_torch)/1e6, 'cpu_model_and_head_load': (cpu_loaded-after_torch)/1e6,
        'cpu_parity_hashing': (cpu_ready_ns-cpu_loaded)/1e6,
        'fresh_admission_to_first_gpu_api': (gpu_start-fresh['checked_ns'])/1e6,
        'fresh_gpu_setup': (setup_done-gpu_start)/1e6, 'cpu_to_gpu_copy': (copied-setup_done)/1e6,
        'fresh_admission_to_staged_result': (staged['completed_ns']-fresh['checked_ns'])/1e6,
        'parent_start_to_staged_result': (staged['completed_ns']-proof['started_ns'])/1e6}
    # Persist completed evidence before optional comparisons or model cleanup.
    bench.write(output/'staged-result.json', {'scope': SCOPE, 'phase': 'STAGED_GPU_INFERENCE_COMPLETE',
        'config_sha256': proof['config_sha256'], 'plan_id': plan['plan_id'], 'candidate_id': candidate['candidate_id'],
        'request_id': item['request']['request_id'], 'request_sha256': item['request_sha256'],
        'source_sha256': proof['source_sha256'], 'cpu_proof': cpu_only, 'staged': staged,
        'features': staged_features.tolist(), 'values': staged_values.tolist(), 'timing_ms': staged_timing,
        'hip_runtime_proof': hip_proof, 'max_process_rss_bytes': int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*1024,
        'max_gpu_allocated_bytes': torch.cuda.max_memory_allocated(), 'max_gpu_reserved_bytes': torch.cuda.max_memory_reserved(),
        'cgroup_before': cgroup_before, 'cgroup_after': bench.cgroup_snapshot(),
        'direct_loader_parity_proven': False, 'forward_seal_created': False, 'broker_orders_sent': 0})
    staged_state = model_state(torch, {'model': raw, 'head': head})
    bench.write(output/'staged-state-hashes.json', {'cpu_initial': cpu_state, 'staged_after': staged_state, 'cpu_tokenizer': cpu_tokens})
    future_parity = {'requested': False}; future_tokens = None
    if comparison is not None:
        comparison_start = time.monotonic_ns()
        comparison_tokenizer = load_comparison_tokenizer(comparison, base/'tokenizer')
        comparison_load_ms = (time.monotonic_ns()-comparison_start)/1e6
        future_tokens = tokenizer_state(torch, comparison_tokenizer, rows)
        future_features, future_values, future_inference = infer(torch, value_module, raw, comparison_tokenizer,
            head, rows, api, candidate.get('readout_profile'))
        token_equal = cpu_tokens == future_tokens
        feature_equal = torch.equal(staged_features, future_features); value_equal = torch.equal(staged_values, future_values)
        future_parity = {'requested': True, 'source': comparison, 'exact': token_equal and feature_equal and value_equal,
            'private_module_only_no_laya_module_replaced': True, 'same_original_gpu_model_and_head': True,
            'tokenizer_input_ids_and_masks_exact': token_equal, 'features_bitwise_equal': feature_equal,
            'values_bitwise_equal': value_equal,
            'feature_max_absolute_difference': float((staged_features-future_features).abs().max()),
            'value_max_absolute_difference_usd': float((staged_values-future_values).abs().max()),
            'tokenizer_class': type(comparison_tokenizer).__module__+'.'+type(comparison_tokenizer).__name__,
            'versions': {name: getattr(sys.modules.get(name), '__version__', None) for name in ('transformers', 'tokenizers')},
            'adapter_fast_path_instrumented': False, 'tokenizer_load_ms': comparison_load_ms, 'inference': future_inference}
        bench.write(output/'future-tokenizer-result.json', {'scope': SCOPE, 'future_tokenizer_parity': future_parity,
            'tokenizer_tensors': future_tokens, 'features': future_features.tolist(), 'values': future_values.tolist(),
            'forward_seal_created': False, 'broker_orders_sent': 0})
        comparison_tokenizer = None
    raw = head = tokenizer = None; gc.collect(); torch.cuda.synchronize(); torch.cuda.empty_cache()
    released_bytes = torch.cuda.memory_allocated()
    allocation = allocation_snapshot(torch)
    bench.write(output/'allocation-after-destroy.json', {'scope': SCOPE,
        'fresh_original_lease_still_held': True, 'first_model_memory_allocated_bytes': released_bytes,
        'max_process_rss_bytes': int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*1024,
        'max_gpu_allocated_bytes': torch.cuda.max_memory_allocated(), 'max_gpu_reserved_bytes': torch.cuda.max_memory_reserved(),
        'direct_model_load_permitted': released_bytes == 0, **allocation})
    if proof.get('comparison_mode') == 'separate-process':
        names = ['staged-result.json','staged-state-hashes.json','allocation-after-destroy.json']
        if comparison is not None: names.append('future-tokenizer-result.json')
        channel.send('STAGED_DONE', {'files_sha256': {name:bench.sha(output/name) for name in names}})
        # Persistent native workspace is released by real process exit. No
        # second model is admitted or loaded in this interpreter, at any size.
        return 0
    need(released_bytes == 0, 'FIRST_MODEL_GPU_ALLOCATION_NOT_RELEASED:'+str(released_bytes))
    direct_start = time.monotonic_ns()
    raw, tokenizer, direct_base = load_verified_laya(base, base_manifest, enforce_gpu=True)
    raw.requires_grad_(False).eval(); need(direct_base == base_id, 'DIRECT_LOADER_BASE_CHANGED')
    head = bench.load_benchmark_head(torch, value_module, load_file(str(head_path)), device, api, candidate.get('readout_profile'))
    torch.cuda.synchronize(); direct_loaded = time.monotonic_ns()
    direct_initial_state = model_state(torch, {'model': raw, 'head': head})
    direct_tokens = tokenizer_state(torch, tokenizer, rows)
    direct_features, direct_values, direct = infer(torch, value_module, raw, tokenizer, head, rows, api, candidate.get('readout_profile'))
    direct_after_state = model_state(torch, {'model': raw, 'head': head})
    initial_parity = diff_state(cpu_state, direct_initial_state); after_parity = diff_state(staged_state, direct_after_state)
    historical_exact = (initial_parity['exact'] and after_parity['exact'] and cpu_tokens == direct_tokens
                        and torch.equal(staged_features, direct_features) and torch.equal(staged_values, direct_values))
    exact = historical_exact and (not future_parity['requested'] or future_parity['exact'])
    usage = resource.getrusage(resource.RUSAGE_SELF)
    report = {'state': 'HISTORICAL_PARITY_EXACT' if exact else 'HISTORICAL_PARITY_DIFFERENCE_REVIEW_REQUIRED',
        'scope': SCOPE, 'exact_parity': exact, 'forward_seal_created': False, 'broker_orders_sent': 0,
        'historical_loader_exact_parity': historical_exact, 'future_tokenizer_parity': future_parity,
        'v2_plan_registered': False, 'optimizer_used': False, 'publish_path_exercised': False,
        'config_sha256': proof['config_sha256'], 'plan_id': plan['plan_id'], 'plan_sha256': proof['plan_sha256'],
        'candidate_id': candidate['candidate_id'], 'head_sha256': plan['head_tensor_sha256'], 'base_id': base_id,
        'request_id': item['request']['request_id'], 'request_sha256': item['request_sha256'],
        'producer_manifest_sha256': item['producer_manifest_sha256'], 'model_api': api,
        'source_record_sha256': item['source_record_sha256'], 'source_prefix': item['source_prefix'],
        'source_sha256': proof['source_sha256'], 'cpu_proof': cpu_only,
        'initial_parameters_and_all_buffers': initial_parity, 'after_inference_parameters_and_all_buffers': after_parity,
        'tokenizer_input_ids_and_masks_exact': cpu_tokens == direct_tokens,
        'features_bitwise_equal': torch.equal(staged_features, direct_features), 'values_bitwise_equal': torch.equal(staged_values, direct_values),
        'feature_max_absolute_difference': float((staged_features-direct_features).abs().max()),
        'value_max_absolute_difference_usd': float((staged_values-direct_values).abs().max()),
        'first_model_gpu_allocated_bytes_after_destroy': released_bytes, 'staged': staged, 'direct': direct,
        'timing_ms': {'child_bootstrap_sandbox': (before_torch-child_started)/1e6, 'torch_cpu_import': (after_torch-before_torch)/1e6,
            'original_front_and_cpu_admission': proof['original_front_and_cpu_admission_ms'],
            'cpu_model_and_head_load': (cpu_loaded-after_torch)/1e6, 'cpu_parity_hashing': (cpu_ready_ns-cpu_loaded)/1e6,
            'fresh_admission_to_first_gpu_api': (gpu_start-fresh['checked_ns'])/1e6,
            'fresh_gpu_setup': (setup_done-gpu_start)/1e6, 'cpu_to_gpu_copy': (copied-setup_done)/1e6,
            'fresh_admission_to_staged_result': (staged['completed_ns']-fresh['checked_ns'])/1e6,
            'parent_start_to_staged_result': (staged['completed_ns']-proof['started_ns'])/1e6,
            'direct_model_and_head_load': (direct_loaded-direct_start)/1e6},
        'hip_runtime_proof': hip_proof, 'max_process_rss_bytes': int(usage.ru_maxrss)*1024,
        'max_gpu_allocated_bytes': torch.cuda.max_memory_allocated(), 'max_gpu_reserved_bytes': torch.cuda.max_memory_reserved(),
        'rss_and_gpu_bytes_are_not_additive': True, 'process_cold_cache_state_uncontrolled': True,
        'cgroup_before': cgroup_before, 'cgroup_after': bench.cgroup_snapshot()}
    bench.write(output/'parity-state-hashes.json', {'cpu_initial': cpu_state, 'staged_after': staged_state,
        'direct_initial': direct_initial_state, 'direct_after': direct_after_state,
        'cpu_tokenizer': cpu_tokens, 'direct_tokenizer': direct_tokens, 'future_tokenizer': future_tokens})
    bench.write(output/'report.json', report)
    channel.send('DONE', {'report_sha256': bench.sha(output/'report.json'), 'exact_parity': exact})
    # Keep fresh_fd through actual process exit, even on numerical differences.
    return 0 if exact else 4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path); parser.add_argument('--sha256')
    parser.add_argument('--request-id', default='latest-flat'); parser.add_argument('--request-sha256')
    parser.add_argument('--output', type=Path); parser.add_argument('--threads', type=int, choices=range(1,5), default=2)
    parser.add_argument('--comparison-tokenizer-source', type=Path); parser.add_argument('--comparison-tokenizer-sha256')
    parser.add_argument('--comparison-mode', choices=('same-process','separate-process'), default='separate-process')
    parser.add_argument('--timeout-seconds', type=int, default=120)
    parser.add_argument('--child-proof', type=Path); parser.add_argument('--child-proof-sha256')
    parser.add_argument('--heavy-fd', type=int); parser.add_argument('--socket-fd', type=int)
    args = parser.parse_args(); os.umask(0o077)
    need(20 <= args.timeout_seconds <= 180, 'TIMEOUT_BOUND')
    if args.child_proof is not None:
        need(args.child_proof_sha256 is not None and args.heavy_fd is not None and args.socket_fd is not None, 'CHILD_ARGUMENTS')
        return child(args)
    need(args.config is not None and args.sha256 is not None and args.output is not None, 'PARENT_ARGUMENTS')
    return parent(args)


if __name__ == '__main__': raise SystemExit(main())
