"""Future signed-plan CPU standby; no GPU phase without a fresh original Gate.

Not wired into an existing launcher/registrar. A new plan must explicitly bind
this profile and source before this entry point can execute. Old plans fail.
The CPU-only parent never imports Torch. One bounded child prepares CPU weights,
releases both initial lease copies, and receives a NEW inherited flock only
after a real fresh flat request passes the original Gate again.
"""
from __future__ import annotations
import argparse, array, fcntl, gc, json, os, select, signal, socket, stat, subprocess, sys, time
from pathlib import Path
from .integrity import ContractError, canonical, digest, read_json, sha_file
from .resident_store import write_once, atomic
from .current100k_warm_flat import (lock_identity, original_guard, original_view,
    request_item, fresh_ids, flat_epoch, set_parent_death_guard, StopChild)

PROFILE = {
    'schema': 'heptatrader.current100k-cpu-standby-worker.v2',
    'position_scope': 'CPU_PREROLL_THEN_ONE_REAL_FLAT_REQUEST',
    'preroll_ms': 30000, 'max_cpu_resident_ms': 180000, 'max_cpu_idle_ms': 150000,
    'max_total_ms': 240000, 'poll_ms': 250, 'guard_recheck_ms': 5000,
    'max_admission_age_ms': 5000, 'shutdown_grace_ms': 10000, 'kill_wait_ms': 2000,
    'cpu_threads': 2, 'gpu_memory_ceiling_bytes': 4*1024**3,
    'max_requests': 1, 'automatic_renewal': False, 'lease_free_gpu_allowed': False,
}
MODULE = 'current100k_cpu_standby.py'
FRAME_LIMIT = 8192


def need(value, reason):
    if not value: raise ContractError('VALUE_CPU_STANDBY_'+reason)


def check_profile(value):
    need(canonical(value) == canonical(PROFILE), 'SIGNED_OPT_IN_REQUIRED')


def check_source_pin(config):
    """Bind the actual loaded authority modules to the staged runtime root."""
    check_profile(config.get('cpu_standby_profile'))
    need(config.get('warm_flat_profile') is None, 'MIXED_PROFILE')
    root = Path(config['worker_source_root'])
    need(root.is_absolute() and root.resolve() == root, 'CANONICAL_RUNTIME_REQUIRED')
    source = Path(__file__).resolve()
    import hepta_decision_attribution as collector
    import sandbox
    from historical_training import resident_resource_guard as guard
    required = [source.with_name(name) for name in (MODULE, 'current100k_warm_flat.py',
        'current100k_forward_worker.py', 'financial_forward_predict.py', 'financial_forward_source.py',
        'resident_hip_runtime.py', 'model.py', 'financial_value_train.py')]
    required += [Path(module.__file__).resolve() for module in (collector, guard, sandbox)]
    inventory = config.get('worker_runtime_sha256', {})
    for path in required:
        need(path.is_relative_to(root), 'MODULE_OUTSIDE_RUNTIME:'+path.name)
        need(inventory.get(path.relative_to(root).as_posix()) == sha_file(path), 'MODULE_NOT_PINNED:'+path.name)
    pin = config.get('guard_config_sha256'); path = config.get('guard_config')
    need(type(path) is str and Path(path).is_absolute() and type(pin) is str
         and len(pin) == 64 and all(ch in '0123456789abcdef' for ch in pin), 'GUARD_CONFIG_PIN_REQUIRED')


def check_binding(config, plan):
    check_profile(config.get('cpu_standby_profile')); check_profile(plan.get('cpu_standby_profile'))
    need(config.get('warm_flat_profile') is None and plan.get('warm_flat_profile') is None, 'MIXED_PROFILE')
    need(config.get('hip_runtime') is not None, 'ACCEPTED_HIP_REQUIRED')
    check_source_pin(config)
    root = Path(config['worker_source_root']).resolve()
    source = Path(__file__).resolve()
    need(source.is_relative_to(root) and config['worker_runtime_sha256'].get(source.relative_to(root).as_posix())
         == plan['runtime_sha256'].get(MODULE) == sha_file(source), 'SOURCE_PIN_REQUIRED')
    need(plan['worker_runtime_inventory_digest'] == config['worker_runtime_inventory_digest'], 'RUNTIME_SCOPE')


def validated_plan(config):
    from .current100k_forward_worker import validate_runtime
    from .financial_forward_predict import RegisteredPlanSnapshot
    from .financial_forward_source import VerifiedIngressSnapshot
    from .financial_forward_witness import verify_trust
    from .current100k_forward import PLAN_SCHEMA
    from . import resident_hip_runtime as hip
    validate_runtime(config); verify_trust(config)
    paths = list((Path(config['forward_root'])/'plans').glob('*/plan.json'))
    need(len(paths) == 1, 'EXACTLY_ONE_PLAN_REQUIRED')
    plan = read_json(paths[0]); need(plan.get('schema') == PLAN_SCHEMA, 'CURRENT100K_PLAN_REQUIRED')
    check_binding(config, plan); hip.check_plan(config, plan)
    prepared = RegisteredPlanSnapshot(config, paths[0], plan)
    return plan, prepared, VerifiedIngressSnapshot(plan['producer_registration'], config['period_sha256'])


def maintenance_hold():
    for path in (Path.home()/'.local/state/heptatrader/operator-maintenance/maintenance-hold.json',
                 Path('/run/user')/str(os.geteuid())/'heptatrader/control/kill-switch'):
        try: path.lstat()
        except FileNotFoundError: continue
        except OSError: return 'MAINTENANCE_STATE_UNAVAILABLE'
        return 'ORIGINAL_MAINTENANCE_OR_KILL_SWITCH'
    return None


def process_fds(pid):
    paths = list((Path('/proc')/str(pid)/'fd').iterdir())
    need(len(paths) <= 4096, 'FD_INSPECTION_BOUND')
    result = []
    for path in paths:
        try:
            target = os.readlink(path); info = path.stat()
        except FileNotFoundError: continue
        result.append({'fd': int(path.name), 'target': target, 'identity': [info.st_dev, info.st_ino]})
    return result


def no_gpu_fds(pid):
    rows = process_fds(pid)
    need(not any(row['target'] == '/dev/kfd' or row['target'].startswith('/dev/dri/') for row in rows),
         'GPU_DEVICE_FD_PRESENT')
    return rows


def cpu_only(torch, models):
    need(not torch.cuda.is_initialized(), 'CUDA_INITIALIZED_DURING_CPU_PHASE')
    count = size = 0
    for model in models:
        for tensor in list(model.parameters()) + list(model.buffers()):
            need(tensor.device.type == 'cpu', 'NON_CPU_TENSOR')
            need(getattr(tensor, 'grad', None) is None, 'UNEXPECTED_GRADIENT')
            count += 1; size += tensor.numel()*tensor.element_size()
    need(count > 0, 'CPU_MODEL_REQUIRED'); no_gpu_fds(os.getpid())
    return {'cuda_initialized': False, 'all_parameters_and_buffers_cpu': True,
            'gpu_device_fds': [], 'tensor_count': count, 'tensor_bytes_including_aliases': size}


class Channel:
    """Pre-created private AF_UNIX socketpair; no socket/connect in the sandbox."""
    def __init__(self, endpoint): self.socket = endpoint
    def send(self, value, fd=None):
        raw = canonical(value); need(len(raw) <= FRAME_LIMIT, 'FRAME_TOO_LARGE')
        ancillary = [] if fd is None else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))]
        need(self.socket.sendmsg([raw], ancillary) == len(raw), 'SHORT_PACKET')
    def receive(self, timeout):
        if not select.select([self.socket], [], [], timeout)[0]: return None
        ints = array.array('i'); received = []
        raw, ancillary, flags, _ = self.socket.recvmsg(FRAME_LIMIT, socket.CMSG_SPACE(8*ints.itemsize), socket.MSG_CMSG_CLOEXEC)
        try:
            for level, kind, data in ancillary:
                need(level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS, 'ANCILLARY_TYPE')
                values = array.array('i'); values.frombytes(data[:len(data)-len(data)%values.itemsize]); received.extend(values)
            need(raw and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) and len(received) <= 1, 'TRUNCATED_OR_EXTRA_FD')
            value = json.loads(raw); need(type(value) is dict and canonical(value) == raw, 'NONCANONICAL_PACKET')
            result = (value, received.pop() if received else None)
            return result
        finally:
            for fd in received: os.close(fd)
    def close(self): self.socket.close()


def packet(session, kind, **fields): return {'session_id': session['session_id'], 'kind': kind, **fields}


def expect(session, message, kind, fields=()):
    need(type(message) is dict and set(message) == {'session_id', 'kind', *fields}
         and message['session_id'] == session['session_id'] and message['kind'] == kind, 'PACKET_SCOPE')


def done_receipt(session, received, item):
    """Account for an already completed child result; grant no new authority."""
    message, extra_fd = received
    if extra_fd is not None:
        os.close(extra_fd); need(False, 'UNEXPECTED_CHILD_DESCRIPTOR')
    expect(session, message, 'DONE', ('request_id', 'request_sha256', 'committed'))
    need(item is not None and message['request_id'] == item['request']['request_id']
         and message['request_sha256'] == item['request_sha256']
         and type(message['committed']) is bool, 'RESULT_REQUEST_SCOPE')
    return message['committed']


class Budget:
    def __init__(self, session):
        self.s = session; self.wall = session['started_at_ms']; self.tick = session['started_ns']; self.idle_started = None
    def check(self, *, gpu=False):
        now = int(time.time()*1000); tick = time.monotonic_ns()
        need(now >= self.wall and tick >= self.tick, 'CLOCK_REVERSED')
        self.wall, self.tick = now, tick
        limit = PROFILE['max_total_ms'] if gpu else PROFILE['max_cpu_resident_ms']
        need(tick-self.s['started_ns'] < limit*1000000 and now < self.s['input_end_ms'], 'SESSION_EXPIRED')
        if not gpu and self.idle_started is not None:
            need(tick-self.idle_started < PROFILE['max_cpu_idle_ms']*1000000, 'CPU_IDLE_EXPIRED')
        return now


def check_admission(value, *, now_ms=None, now_ns=None):
    report = value['decision']; now_ms = int(time.time()*1000) if now_ms is None else now_ms
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    need(report.get('admit_new_worker') is True and report.get('role') == 'value-predictor'
         and not report.get('reasons'), 'ORIGINAL_GUARD_DENIED')
    need(type(report.get('at_ms')) is int and type(value.get('checked_ns')) is int
         and 0 <= now_ms-report['at_ms'] < PROFILE['max_admission_age_ms']
         and 0 <= now_ns-value['checked_ns'] < PROFILE['max_admission_age_ms']*1000000, 'ADMISSION_EXPIRED')


def reject_new_open(bindings, since):
    for binding in bindings:
        command = binding['command']
        if command.get('kind') == 'OPEN':
            need(type(command.get('created_at_ms')) is int, 'PREPARED_TIME_REQUIRED')
            need(command['created_at_ms'] < since, 'ORIGINAL_OPEN_PREPARED')


def make_session(config, config_path, plan, gate, controller_fd):
    value = {'profile': dict(PROFILE), 'config_path': str(config_path), 'config_sha256': sha_file(config_path),
        'plan_id': plan['plan_id'], 'candidate_id': plan['candidate_id'],
        'producer_manifest_sha256': plan['producer_registration']['sha256'],
        'worker_runtime_inventory_digest': config['worker_runtime_inventory_digest'],
        'parent_pid': os.getpid(), 'nonce': os.urandom(16).hex(),
        'started_at_ms': int(time.time()*1000), 'started_ns': time.monotonic_ns(),
        'input_start_ms': plan['input_start_ms'], 'input_end_ms': plan['input_end_ms'],
        'heavy_lock_path': str(gate.lease.path), 'heavy_lock_identity': lock_identity(gate.lease.fd),
        'controller_lock_identity': lock_identity(controller_fd)}
    value['session_id'] = digest(value)
    return value


def record_admission(folder, session, kind, decision, item=None):
    value = {'session_id': session['session_id'], 'kind': kind, 'checked_ns': time.monotonic_ns(),
             'decision': decision, 'request_id': item['request']['request_id'] if item else None,
             'request_sha256': item['request_sha256'] if item else None}
    path = folder/(kind+'.json'); write_once(path, value)
    return path, sha_file(path)


def guard_check(gate):
    paths = list(sys.path)
    try: return gate.check()
    finally: sys.path[:] = paths


def child(config_path, session_path, session_sha, socket_fd, initial_fd, controller_fd):
    need(sha_file(session_path) == session_sha, 'SESSION_PIN_CHANGED')
    session = read_json(session_path); set_parent_death_guard(session['parent_pid'])
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    need(session['session_id'] == digest({k: v for k, v in session.items() if k != 'session_id'})
         and session['profile'] == PROFILE, 'SESSION_IDENTITY')
    need(str(config_path) == session['config_path'] and sha_file(config_path) == session['config_sha256'], 'CONFIG_PIN')
    need(initial_fd != controller_fd and lock_identity(initial_fd) == session['heavy_lock_identity']
         and lock_identity(controller_fd) == session['controller_lock_identity'], 'INHERITED_LOCK_SCOPE')
    os.set_inheritable(initial_fd, False); os.set_inheritable(controller_fd, False)
    channel = Channel(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0, fileno=socket_fd)); budget = Budget(session)
    config = read_json(config_path); plan, prepared, snapshot = validated_plan(config)
    need(plan['plan_id'] == session['plan_id'] and plan['candidate_id'] == session['candidate_id']
         and plan['producer_registration']['sha256'] == session['producer_manifest_sha256']
         and plan['input_end_ms'] == session['input_end_ms'], 'SIGNED_PLAN_SCOPE')
    folder = session_path.parent
    need(folder == Path(config['forward_root'])/'cpu-standby-sessions'/session['session_id'], 'SESSION_PATH')
    wrapper = original_guard(config)
    need(str(Path(wrapper['state_root'])/'heavy-worker.lock') == session['heavy_lock_path'], 'ORIGINAL_HEAVY_LOCK_SCOPE')
    initial = read_json(folder/'CPU_LOAD.json')
    need(initial['session_id'] == session['session_id'] and initial['kind'] == 'CPU_LOAD'
         and initial['request_id'] is None and initial['request_sha256'] is None, 'CPU_ADMISSION_SCOPE')
    check_admission(initial); budget.check(); need(maintenance_hold() is None, 'MAINTENANCE_HOLD')
    requests, bindings = original_view(snapshot, config, plan); reject_new_open(bindings, session['started_at_ms'])
    from . import resident_hip_runtime as hip
    context = hip.prepare_worker(config, 'predict')
    from sandbox import enter
    restrictions = enter({'output_dir': config['forward_root'], 'period_path': config['period_path'],
        'legacy_capture': plan['training_dataset_path'], 'oms_path': config['oms_path'],
        'export_dir': config['attribution_directory'],
        'read_only_paths': config['worker_read_only_paths'] + [config['worker_source_root'], sys.prefix,
            config['role_key_path'], config['witness_trust_path']] + context['readonly']})
    write_once(folder/'sandbox.json', restrictions)
    def stop(*_): raise StopChild()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    fresh_fd = None; predictor = raw = head = None
    try:
        hip.activate(context)
        import torch
        hip.prove(context, 'AFTER_TORCH_BEFORE_MODEL')
        torch.set_num_threads(PROFILE['cpu_threads'])
        from .model import load_verified_laya, require_pocket4_gpu
        from .financial_value_train import load_value_head
        from safetensors.torch import load_file
        begin = time.monotonic_ns(); base = Path(config['base_directory'])
        raw, tokenizer, base_id = load_verified_laya(base, read_json(base/'base-manifest.json'), enforce_gpu=False)
        raw.requires_grad_(False).eval()
        candidate = prepared.candidate(config, plan)
        need(candidate['base_id'] == base_id, 'BASE_ID_CHANGED')
        head = load_value_head(load_file(str(Path(plan['candidate_directory'])/'value-head.safetensors')),
                               torch.device('cpu'), readout_profile=candidate.get('readout_profile'))
        proof = cpu_only(torch, [raw, head]); budget.check()
        write_once(folder/'cpu-ready.json', {'session_id': session['session_id'], 'at_ms': int(time.time()*1000),
            'cpu_load_ms': (time.monotonic_ns()-begin)/1e6, 'base_id': base_id, 'proof': proof,
            'heavy_lease_still_held': True})
        channel.send(packet(session, 'CPU_READY', proof=proof))
        while True:
            budget.check(); message = channel.receive(PROFILE['poll_ms']/1000)
            if message is None: continue
            message, unexpected = message
            if unexpected is not None: os.close(unexpected); need(False, 'EARLY_FRESH_DESCRIPTOR')
            expect(session, message, 'RELEASE_INITIAL')
            cpu_only(torch, [raw, head]); os.close(initial_fd); initial_fd = None
            budget.idle_started = time.monotonic_ns()
            channel.send(packet(session, 'CPU_IDLE_UNLOCKED'))
            break
        while True:
            budget.check(); message = channel.receive(PROFILE['poll_ms']/1000)
            if message is None: continue
            message, fresh_fd = message
            expect(session, message, 'PREDICT', ('request_id', 'request_sha256', 'admission_sha256'))
            need(fresh_fd is not None and lock_identity(fresh_fd) == session['heavy_lock_identity'], 'NEW_ORIGINAL_HEAVY_DESCRIPTOR_REQUIRED')
            os.set_inheritable(fresh_fd, False)
            admission_path = folder/'GPU_REQUEST.json'
            need(sha_file(admission_path) == message['admission_sha256'], 'FRESH_ADMISSION_PIN')
            admission = read_json(admission_path)
            need(admission['session_id'] == session['session_id'] and admission['kind'] == 'GPU_REQUEST'
                 and admission['request_id'] == message['request_id']
                 and admission['request_sha256'] == message['request_sha256'], 'FRESH_ADMISSION_REQUEST_SCOPE')
            check_admission(admission); budget.check(gpu=True)
            need(maintenance_hold() is None, 'MAINTENANCE_HOLD')
            requests, bindings = original_view(snapshot, config, plan)
            item = request_item(requests, message['request_id'], plan, int(time.time()*1000))
            need(item['request_sha256'] == message['request_sha256'], 'REQUEST_CHANGED')
            epoch = {'anchor_request_id': message['request_id'], 'anchor_request_sha256': message['request_sha256'],
                     'anchor_started_at_ms': item['request']['started_at_ms']}
            flat_epoch(requests, bindings, epoch); reject_new_open(bindings, session['started_at_ms'])
            cpu_only(torch, [raw, head]); prepared.candidate(config, plan)
            # Recheck admission immediately before the FIRST GPU API.
            request_item(requests, message['request_id'], plan, int(time.time()*1000))
            budget.check(gpu=True); check_admission(admission)
            device = require_pocket4_gpu()
            torch.cuda.set_per_process_memory_fraction(min(1., PROFILE['gpu_memory_ceiling_bytes']/torch.cuda.get_device_properties(0).total_memory))
            begin = time.monotonic_ns(); raw = raw.to(device); head = head.to(device)
            torch.cuda.synchronize()
            from .financial_forward_predict import Predictor
            predictor = Predictor(config, ingress_snapshots={plan['plan_id']: snapshot},
                registered_plans={plan['plan_id']: prepared}, front_timing={
                    'cpu_standby_profile': dict(PROFILE), 'cpu_standby_session_id': session['session_id'],
                    'cpu_to_gpu_ms': (time.monotonic_ns()-begin)/1e6,
                    'fresh_original_admission_at_ms': admission['decision']['at_ms']})
            predictor.raw = raw; predictor.tokenizer = tokenizer; predictor.base_id = base_id
            predictor.heads[plan['candidate_id']] = head
            result = predictor.predict(plan, item)
            channel.send(packet(session, 'DONE', request_id=message['request_id'],
                                request_sha256=message['request_sha256'], committed=result is not None))
            return
    except StopChild:
        return
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGINT, signal.SIG_IGN)
        predictor = raw = head = None; gc.collect(); channel.close()
        # No child closes a GPU-phase heavy fd here. Both copies survive through
        # exit/reap, including exceptions and asynchronous HIP cleanup. A child
        # never calls Gate.acquire or Gate.release, nor returns to CPU standby.


def spawn_child(config, config_path, session_path, session, gate, controller_fd):
    from . import resident_hip_runtime as hip
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        root = Path(config['worker_source_root'])
        env = dict(hip.worker_environment(config), PYTHONPATH=':'.join(str(root/p) for p in ('research', 'learn', 'scripts')),
                   PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='2')
        process = subprocess.Popen([config['python'], '-B', '-m', 'laya_cell.current100k_cpu_standby',
            '--child', '--config', str(config_path), '--session', str(session_path), '--session-sha256', sha_file(session_path),
            '--socket-fd', str(right.fileno()), '--heavy-fd', str(gate.lease.fd), '--controller-fd', str(controller_fd)],
            cwd=root, env=env, stdin=subprocess.DEVNULL, close_fds=True,
            pass_fds=(right.fileno(), gate.lease.fd, controller_fd))
        return process, Channel(left)
    except BaseException:
        left.close(); raise
    finally: right.close()


def reap_child(process):
    if process.poll() is not None: return True
    try: process.terminate()
    except ProcessLookupError: pass
    try: process.wait(timeout=PROFILE['shutdown_grace_ms']/1000)
    except subprocess.TimeoutExpired:
        try: process.kill()
        except ProcessLookupError: pass
        try: process.wait(timeout=PROFILE['kill_wait_ms']/1000)
        except subprocess.TimeoutExpired: return False
    return process.poll() is not None


def run_parent(config, config_path, controller_fd):
    need('torch' not in sys.modules, 'PARENT_MUST_NOT_IMPORT_TORCH')
    plan, prepared, snapshot = validated_plan(config)
    root = Path(config['forward_root']); now = int(time.time()*1000)
    def status(state, **fields):
        value = {'at_ms': int(time.time()*1000), 'state': state, 'plan_id': plan['plan_id'],
                 'cpu_standby_profile': dict(PROFILE), 'actor_updated': False, 'broker_orders_sent': 0, **fields}
        atomic(root/'cpu-standby-status.json', value); print(json.dumps(value, sort_keys=True), flush=True)
        return value
    if now < plan['input_start_ms']-PROFILE['preroll_ms']: return status('WAIT_FIXED_CPU_PREROLL')
    if now >= plan['input_end_ms']: return status('FIXED_INPUT_WINDOW_CLOSED_NO_MODEL_LOAD')
    if maintenance_hold() is not None: return status('ORIGINAL_MAINTENANCE_HOLD_NO_MODEL_LOAD')
    from historical_training.resident_resource_guard import Gate
    from .current100k_forward_worker import acquire_value_resources
    gate = Gate(original_guard(config), 'value-predictor')
    if not acquire_value_resources(gate, {'plan_id': plan['plan_id'], 'phase': 'CPU_PRELOAD'}):
        return status('HOLD_CPU_ADMISSION_NO_MODEL_LOAD', admission=gate.last_admission)
    process = channel = session = None; reaped = True; previous = {}; phase = 'CPU_STARTING'
    committed = False; result_item = None; error = None; reason = 'SESSION_COMPLETE'
    try:
        session = make_session(config, config_path, plan, gate, controller_fd)
        folder = root/'cpu-standby-sessions'/session['session_id']; session_path = folder/'session.json'
        write_once(session_path, session); record_admission(folder, session, 'CPU_LOAD', gate.last_admission)
        budget = Budget(session)
        def stop(*_): raise StopChild()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
            previous[sig] = signal.signal(sig, stop)
        process, channel = spawn_child(config, config_path, session_path, session, gate, controller_fd); reaped = False
        last_guard = time.monotonic_ns()
        status(phase, session_id=session['session_id'], heavy_lease_held=True)
        while True:
            if phase == 'GPU_ACTIVE':
                # A prediction completed before a subsequent OPEN, deadline or
                # Guard change remains a completed result. This consumes only
                # the exact child receipt; it never authorizes more work.
                ready = channel.receive(0)
                if ready is not None:
                    committed = done_receipt(session, ready, result_item)
                    phase = 'GPU_FINISHED'; break
            now = budget.check(gpu=phase == 'GPU_ACTIVE')
            need(maintenance_hold() is None, 'MAINTENANCE_HOLD')
            need(sha_file(config_path) == session['config_sha256'], 'CONFIG_CHANGED')
            code = process.poll()
            if code is not None:
                # A successful child can exit with DONE still buffered in its
                # socket. Consume that exact result before judging completion.
                need(code == 0 and phase == 'GPU_ACTIVE', 'CHILD_EXIT_WITHOUT_EXPECTED_RESULT')
            requests, bindings = original_view(snapshot, config, plan)
            reject_new_open(bindings, session['started_at_ms'])
            if result_item is not None:
                epoch = {'anchor_request_id': result_item['request']['request_id'],
                    'anchor_request_sha256': result_item['request_sha256'],
                    'anchor_started_at_ms': result_item['request']['started_at_ms']}
                flat_epoch(requests, bindings, epoch)
                request_item(requests, epoch['anchor_request_id'], plan, now)
            received = channel.receive(0 if code is not None else PROFILE['poll_ms']/1000)
            need(code is None or received is not None, 'CHILD_EXIT_WITHOUT_DONE')
            if received is not None:
                message, extra_fd = received
                if extra_fd is not None: os.close(extra_fd); need(False, 'UNEXPECTED_CHILD_DESCRIPTOR')
                if phase == 'CPU_STARTING':
                    expect(session, message, 'CPU_READY', ('proof',)); proof = message['proof']
                    need(proof.get('cuda_initialized') is False and proof.get('all_parameters_and_buffers_cpu') is True
                         and proof.get('gpu_device_fds') == [] and type(proof.get('tensor_count')) is int
                         and proof['tensor_count'] > 0, 'CPU_PROOF_REQUIRED')
                    no_gpu_fds(process.pid)
                    channel.send(packet(session, 'RELEASE_INITIAL')); phase = 'WAIT_CPU_UNLOCK'
                elif phase == 'WAIT_CPU_UNLOCK':
                    expect(session, message, 'CPU_IDLE_UNLOCKED')
                    fds = no_gpu_fds(process.pid)
                    need(not any(row['identity'] == session['heavy_lock_identity'] for row in fds), 'CHILD_RETAINS_INITIAL_HEAVY_FD')
                    gate.release(); need(gate.lease.fd is None, 'PARENT_INITIAL_LEASE_NOT_RELEASED')
                    budget.idle_started = time.monotonic_ns(); phase = 'CPU_IDLE'
                    write_once(folder/'cpu-idle.json', {'session_id': session['session_id'], 'at_ms': int(time.time()*1000),
                        'parent_heavy_fd_closed': True, 'child_heavy_fd_closed': True,
                        'parent_observed_child_gpu_device_fds': [], 'gpu_loaded': False})
                    status(phase, session_id=session['session_id'], heavy_lease_held=False)
                elif phase == 'GPU_ACTIVE':
                    committed = done_receipt(session, (message, None), result_item)
                    phase = 'GPU_FINISHED'
                    break
                else: need(False, 'UNEXPECTED_CHILD_MESSAGE')
            tick = time.monotonic_ns()
            if tick-last_guard >= PROFILE['guard_recheck_ms']*1000000:
                need(sha_file(Path(config['guard_config'])) == config['guard_config_sha256'], 'GUARD_CHANGED')
                report = guard_check(gate); last_guard = time.monotonic_ns()
                need(report.get('admit_new_worker') is True, 'ORIGINAL_GUARD_RECHECK_DENIED')
            if phase == 'CPU_IDLE':
                ids = fresh_ids(root, plan, requests, int(time.time()*1000))
                if not ids: continue
                need(gate.lease.fd is None, 'FRESH_ADMISSION_REQUIRES_RELEASED_INITIAL_LEASE')
                prepared.candidate(config, plan)
                item = request_item(requests, ids[0], plan, int(time.time()*1000))
                if not acquire_value_resources(gate, {'plan_id': plan['plan_id'], 'phase': 'GPU_REQUEST',
                        'request_id': item['request']['request_id'], 'request_sha256': item['request_sha256']}):
                    reason = 'FRESH_ORIGINAL_GUARD_DENIED_NO_GPU'
                    status(reason, admission=gate.last_admission); break
                need(gate.lease.fd is not None, 'FRESH_ORIGINAL_LEASE_MISSING')
                requests, bindings = original_view(snapshot, config, plan)
                reject_new_open(bindings, session['started_at_ms'])
                current = request_item(requests, item['request']['request_id'], plan, int(time.time()*1000))
                need(current['request_sha256'] == item['request_sha256'], 'REQUEST_CHANGED_DURING_FRESH_GATE')
                result_item = current
                _, admission_sha = record_admission(folder, session, 'GPU_REQUEST', gate.last_admission, current)
                channel.send(packet(session, 'PREDICT', request_id=current['request']['request_id'],
                    request_sha256=current['request_sha256'], admission_sha256=admission_sha), gate.lease.fd)
                phase = 'GPU_ACTIVE'; last_guard = time.monotonic_ns()
                status(phase, request_id=current['request']['request_id'], heavy_lease_held=True)
    except StopChild:
        reason = 'CONTROLLER_DRAIN_REQUESTED'
    except BaseException as exc:
        error = exc; reason = type(exc).__name__+':'+str(exc)[:180]
    finally:
        # Keep original or fresh lease until the actual child is gone. If reap
        # fails, the fd deliberately stays open through controller process exit.
        if process is not None:
            reaped = reap_child(process)
            if reaped: gate.record_native_exit(process.returncode, {'plan_id': plan['plan_id'], 'cpu_standby_phase': phase})
        receipt_error = None
        if channel is not None and reaped and phase == 'GPU_ACTIVE':
            # Cover DONE arriving between the nonblocking receive and a later
            # drain check. Keep the drain/error reason and the lease until reap.
            try:
                ready = channel.receive(0)
                if ready is not None:
                    committed = done_receipt(session, ready, result_item)
                    phase = 'GPU_FINISHED'
            except Exception as exc:
                receipt_error = type(exc).__name__+':'+str(exc)[:180]
        if channel is not None: channel.close()
        if reaped: gate.release()
        for sig, handler in previous.items(): signal.signal(sig, handler)
        if session is not None:
            terminal = {'session_id': session['session_id'], 'at_ms': int(time.time()*1000),
                'reason': reason, 'last_phase': phase, 'child_reaped': reaped, 'new_predictions': int(committed),
                'result_receipt': ('COMMITTED' if committed else 'NOT_COMMITTED') if phase == 'GPU_FINISHED' else 'NO_VERIFIED_DONE',
                'result_receipt_error': receipt_error,
                'request_id': result_item['request']['request_id'] if result_item else None,
                'automatic_renewal': False, 'broker_orders_sent': 0}
            write_once(root/'cpu-standby-sessions'/session['session_id']/'terminal.json', terminal)
    if error is not None: raise error
    return status('CPU_STANDBY_FINITE_COMPLETE', reason=reason, new_predictions=int(committed), child_reaped=reaped)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True); parser.add_argument('--sha256')
    parser.add_argument('--child', action='store_true'); parser.add_argument('--session', type=Path)
    parser.add_argument('--session-sha256')
    for name in ('socket-fd', 'heavy-fd', 'controller-fd'): parser.add_argument('--'+name, type=int)
    args = parser.parse_args(); os.umask(0o077)
    if args.child:
        need(args.session is not None and args.session_sha256 is not None
             and all(getattr(args, name) is not None for name in ('socket_fd', 'heavy_fd', 'controller_fd')), 'CHILD_ARGUMENTS')
        child(args.config, args.session, args.session_sha256, args.socket_fd, args.heavy_fd, args.controller_fd)
        return
    need(args.sha256 is not None and sha_file(args.config) == args.sha256, 'PARENT_CONFIG_PIN_REQUIRED')
    config = read_json(args.config); root = Path(config['forward_root'])
    fd = os.open(root/'predict-worker.lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
    try:
        lock_identity(fd); fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        run_parent(config, args.config, fd)
    finally: os.close(fd)


if __name__ == '__main__': main()
