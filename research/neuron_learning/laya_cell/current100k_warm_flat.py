"""Opt-in finite warmth after a real flat request; no trading or close inference.

The CPU parent keeps the original Guard outside the original GPU sandbox. A
single sandboxed child owns one Predictor and inherited copies of both flocks.
Every request has a fresh original admission. Nothing here installs a service,
registers a plan, changes resource thresholds, or renews an expired request.
"""
from __future__ import annotations
import argparse, ctypes, fcntl, gc, json, os, select, signal, stat, subprocess, sys, time, traceback
from pathlib import Path
from .integrity import ContractError, canonical, digest, read_json, sha_file
from .resident_store import atomic, write_once

PROFILE = {
    'schema': 'heptatrader.current100k-warm-flat-worker.v1',
    'position_scope': 'REAL_FLAT_REQUEST_UNTIL_ORIGINAL_PREPARED',
    'max_resident_ms': 180000, 'max_idle_ms': 150000, 'max_requests': 16,
    'poll_ms': 250, 'idle_guard_recheck_ms': 5000, 'max_admission_age_ms': 5000,
    'shutdown_grace_ms': 10000, 'kill_wait_ms': 2000,
    'single_model': True, 'automatic_renewal': False,
}
BINDING = ('session_id', 'config_sha256', 'plan_id', 'candidate_id',
           'producer_manifest_sha256', 'worker_runtime_inventory_digest')
FRAME_LIMIT = 4096


def need(value, reason):
    if not value: raise ContractError('VALUE_WARM_' + reason)


def check_profile(value):
    need(value is None or (type(value) is dict and canonical(value) == canonical(PROFILE)), 'PROFILE_CHANGED')
    return value


def check_binding(config, plan):
    check_profile(config.get('warm_flat_profile')); check_profile(plan.get('warm_flat_profile'))
    need(config.get('warm_flat_profile') == plan.get('warm_flat_profile'), 'PLAN_PROFILE_CHANGED')
    if config.get('warm_flat_profile') is not None:
        need(config.get('hip_runtime') is not None, 'ACCEPTED_HIP_REQUIRED')
        need('current100k_warm_flat.py' in plan.get('runtime_sha256', {}), 'PLAN_SOURCE_PIN_REQUIRED')


def check_source_pin(config):
    check_profile(config.get('warm_flat_profile'))
    if config.get('warm_flat_profile') is None: return
    root = Path(config['worker_source_root'])
    need(root.is_absolute() and root.resolve() == root, 'CANONICAL_RUNTIME_REQUIRED')
    source = Path(__file__).resolve()
    import hepta_decision_attribution as collector
    import sandbox
    from historical_training import resident_resource_guard as guard
    required_paths = [source.with_name(name) for name in (
        'current100k_warm_flat.py', 'current100k_forward_worker.py',
        'financial_forward_predict.py', 'financial_forward_source.py', 'resident_hip_runtime.py')]
    required_paths += [Path(module.__file__).resolve() for module in (collector, guard, sandbox)]
    for required in required_paths:
        need(required.is_relative_to(root), 'MODULE_OUTSIDE_RUNTIME:' + required.name)
        name = required.relative_to(root).as_posix()
        need(config['worker_runtime_sha256'].get(name) == sha_file(required), 'MODULE_NOT_PINNED:' + required.name)
    need(config.get('hip_runtime') is not None, 'ACCEPTED_HIP_REQUIRED')
    guard_path = config.get('guard_config'); guard_pin = config.get('guard_config_sha256')
    need(type(guard_path) is str and Path(guard_path).is_absolute()
         and type(guard_pin) is str and len(guard_pin) == 64
         and all(ch in '0123456789abcdef' for ch in guard_pin), 'GUARD_CONFIG_PIN_REQUIRED')


def lock_identity(fd):
    need(type(fd) is int and fd >= 3, 'LOCK_FD_REQUIRED')
    value = os.fstat(fd)
    need(stat.S_ISREG(value.st_mode) and value.st_uid == os.geteuid()
         and not value.st_mode & 0o077, 'LOCK_OWNER_OR_MODE')
    return [value.st_dev, value.st_ino]


def held_admission(gate):
    """Never call acquire/release while the child may own GPU tensors."""
    need(gate.role == 'value-predictor' and gate.lease.fd is not None, 'HELD_VALUE_LEASE_REQUIRED')
    fd = gate.lease.fd; before = lock_identity(fd); paths = list(sys.path)
    try: report = gate.check()
    finally: sys.path[:] = paths
    need(gate.lease.fd == fd and lock_identity(fd) == before, 'HELD_LEASE_CHANGED')
    gate.last_admission = report
    return report


def packet(session, kind, seq, **fields):
    return {'schema': 'heptatrader.current100k-warm-pipe.v1',
            **{key: session[key] for key in BINDING}, 'kind': kind, 'seq': seq, **fields}


def expect(value, session, kind, seq, fields=()):
    keys = {'schema', *BINDING, 'kind', 'seq', *fields}
    need(type(value) is dict and set(value) == keys, 'PIPE_SHAPE')
    need(value['schema'] == 'heptatrader.current100k-warm-pipe.v1'
         and all(value[key] == session[key] for key in BINDING)
         and value['kind'] == kind and type(value['seq']) is int and value['seq'] == seq,
         'PIPE_SCOPE_OR_SEQUENCE')
    return value


class Pipe:
    """One in-flight message, bounded atomic writes, bounded nonblocking reads."""
    def __init__(self, read_fd, write_fd):
        need(read_fd != write_fd, 'PIPE_DESCRIPTORS')
        for fd, direction in ((read_fd, os.O_RDONLY), (write_fd, os.O_WRONLY)):
            need(type(fd) is int and fd >= 3 and stat.S_ISFIFO(os.fstat(fd).st_mode), 'PIPE_FD_TYPE')
            need(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == direction, 'PIPE_FD_DIRECTION')
            need(os.fpathconf(fd, 'PC_PIPE_BUF') >= FRAME_LIMIT, 'PIPE_ATOMIC_BOUND')
            os.set_inheritable(fd, False); os.set_blocking(fd, False)
        self.read_fd = read_fd; self.write_fd = write_fd; self.buffer = b''
    def send(self, value):
        raw = canonical(value) + b'\n'
        need(len(raw) <= FRAME_LIMIT, 'PIPE_FRAME_BOUND')
        try: count = os.write(self.write_fd, raw)
        except (BlockingIOError, BrokenPipeError) as exc:
            raise ContractError('VALUE_WARM_PIPE_UNAVAILABLE') from exc
        need(count == len(raw), 'PIPE_PARTIAL_WRITE')
    def receive(self, seconds=0):
        need(0 <= seconds <= PROFILE['poll_ms'] / 1000, 'PIPE_WAIT_BOUND')
        if b'\n' not in self.buffer:
            if not select.select([self.read_fd], [], [], seconds)[0]: return None
            try: chunk = os.read(self.read_fd, FRAME_LIMIT)
            except BlockingIOError: return None
            need(bool(chunk), 'PIPE_EOF')
            self.buffer += chunk
        need(len(self.buffer) <= 2 * FRAME_LIMIT, 'PIPE_BUFFER_BOUND')
        if b'\n' not in self.buffer:
            need(len(self.buffer) < FRAME_LIMIT, 'PIPE_FRAME_BOUND'); return None
        raw, self.buffer = self.buffer.split(b'\n', 1)
        need(len(raw) + 1 <= FRAME_LIMIT, 'PIPE_FRAME_BOUND')
        def pairs(items):
            result = {}
            for key, value in items:
                need(key not in result, 'PIPE_DUPLICATE_KEY'); result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ContractError('VALUE_WARM_PIPE_NONFINITE')))
        need(type(value) is dict, 'PIPE_SHAPE')
        return value
    def close(self):
        for name in ('read_fd', 'write_fd'):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd); setattr(self, name, None)


class Budget:
    def __init__(self, session):
        self.session = session
        self.last_wall = session['started_at_ms']; self.last_tick = session['started_ns']
        self.activity_ns = self.last_tick; self.count = 0
    def check(self, now_ms=None, tick=None):
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        tick = time.monotonic_ns() if tick is None else tick
        need(type(now_ms) is int and type(tick) is int and now_ms >= self.last_wall
             and tick >= self.last_tick, 'CLOCK_REVERSED')
        need(now_ms < self.session['expires_at_ms'] and tick < self.session['expires_ns'], 'SESSION_EXPIRED')
        need(tick - self.activity_ns < PROFILE['max_idle_ms'] * 1000000, 'IDLE_EXPIRED')
        self.last_wall = now_ms; self.last_tick = tick
        return now_ms, tick
    def dispatch(self):
        self.check(); need(self.count < PROFILE['max_requests'], 'REQUEST_BOUND')
        self.count += 1; self.activity_ns = self.last_tick; return self.count
    def completed(self):
        self.check(); self.activity_ns = self.last_tick


def original_view(snapshot, config, plan):
    _, requests, bindings, issues = snapshot.read(plan['producer_registration'], config['period_sha256'])
    need(not issues, 'ORIGINAL_SOURCE_UNAVAILABLE')
    return requests, bindings


def request_item(requests, rid, plan, now_ms):
    need(isinstance(rid, str) and rid not in ('', '.', '..') and Path(rid).name == rid, 'REQUEST_PATH')
    need(rid in requests, 'REQUEST_WITHDRAWN')
    item = requests[rid]; request = item['request']; started = request.get('started_at_ms')
    need(request.get('request_id') == rid and type(started) is int
         and plan['input_start_ms'] <= started <= now_ms < plan['input_end_ms']
         and now_ms - started < 60000 and request.get('position_at_request') == 0,
         'REQUEST_NOT_FRESH_FLAT')
    need(request.get('model_id') == plan['behavior_scope']['behavior_model_id']
         and item['producer_manifest_sha256'] == plan['producer_registration']['sha256'], 'REQUEST_BEHAVIOR_SCOPE')
    return item


def flat_epoch(requests, bindings, session):
    anchor = session['anchor_started_at_ms']
    need(type(anchor) is int and anchor > 0, 'FLAT_ANCHOR_TIME')
    item = requests.get(session['anchor_request_id'])
    need(item is not None and item['request_sha256'] == session['anchor_request_sha256']
         and item['request']['started_at_ms'] == anchor and item['request']['position_at_request'] == 0, 'FLAT_ANCHOR_CHANGED')
    for item in requests.values():
        request = item['request']
        need(type(request.get('started_at_ms')) is int, 'ORIGINAL_REQUEST_TIME_REQUIRED')
        if request['started_at_ms'] >= anchor:
            need(request['position_at_request'] == 0, 'ORIGINAL_NONFLAT_OBSERVED')
    for binding in bindings:
        command = binding['command']
        if command.get('kind') == 'OPEN':
            need(type(command.get('created_at_ms')) is int, 'ORIGINAL_PREPARED_TIME_REQUIRED')
            if command['created_at_ms'] >= anchor:
                raise ContractError('VALUE_WARM_ORIGINAL_PREPARED_OBSERVED')


def fresh_ids(root, plan, requests, now_ms, anchor=None):
    result = []
    for rid, item in requests.items():
        request = item['request']; start = request['started_at_ms']
        if not (plan['input_start_ms'] <= start <= now_ms < plan['input_end_ms']
                and now_ms - start < 60000 and request['position_at_request'] == 0
                and (anchor is None or start >= anchor)): continue
        request_item(requests, rid, plan, now_ms)
        folder = root / 'plans' / plan['plan_id'] / 'predictions' / rid
        if not (folder / 'commit.json').exists() and not (folder / 'failure.json').exists(): result.append(rid)
    return sorted(result, key=lambda rid: (requests[rid]['request']['started_at_ms'], rid))


def validated_plan(config):
    from .current100k_forward_worker import validate_runtime
    from .financial_forward_predict import RegisteredPlanSnapshot
    from .financial_forward_source import VerifiedIngressSnapshot
    from .financial_forward_witness import verify_trust
    from .current100k_forward import PLAN_SCHEMA
    from . import resident_hip_runtime as hip
    validate_runtime(config); need(config.get('warm_flat_profile') == PROFILE, 'OPT_IN_REQUIRED')
    verify_trust(config)
    paths = list((Path(config['forward_root']) / 'plans').glob('*/plan.json'))
    need(len(paths) == 1, 'EXACTLY_ONE_PLAN_REQUIRED')
    plan = read_json(paths[0]); check_binding(config, plan); hip.check_plan(config, plan)
    need(plan.get('schema') == PLAN_SCHEMA
         and plan['worker_runtime_inventory_digest'] == config['worker_runtime_inventory_digest'], 'PLAN_RUNTIME_SCOPE')
    prepared = RegisteredPlanSnapshot(config, paths[0], plan)
    snapshot = VerifiedIngressSnapshot(plan['producer_registration'], config['period_sha256'])
    return plan, prepared, snapshot


def admission_record(folder, session, seq, request, report):
    value = packet(session, 'ADMISSION', seq, request_id=request['request']['request_id'],
        request_sha256=request['request_sha256'], checked_ns=time.monotonic_ns(), decision=report)
    need(len(canonical(value)) <= 2 * 1024**2, 'ADMISSION_RECORD_BOUND')
    path = folder / ('admission-%03d.json' % seq); write_once(path, value)
    return sha_file(path)


def check_admission(value, session, seq, request_id, request_sha256):
    expect(value, session, 'ADMISSION', seq,
           ('request_id', 'request_sha256', 'checked_ns', 'decision'))
    report = value['decision']; now = int(time.time() * 1000); tick = time.monotonic_ns()
    need(value['request_id'] == request_id and value['request_sha256'] == request_sha256,
         'ADMISSION_REQUEST_CHANGED')
    need(report.get('admit_new_worker') is True and report.get('role') == 'value-predictor'
         and not report.get('reasons'), 'ORIGINAL_ADMISSION_DENIED')
    need(type(report.get('at_ms')) is int and type(value['checked_ns']) is int
         and 0 <= now - report['at_ms'] < PROFILE['max_admission_age_ms']
         and 0 <= tick - value['checked_ns'] < PROFILE['max_admission_age_ms'] * 1000000,
         'ADMISSION_EXPIRED')


def original_guard(config):
    from historical_training.resident_resource_guard import verify_config
    from . import resident_hip_runtime as hip
    need(sha_file(Path(config['guard_config'])) == config.get('guard_config_sha256'), 'GUARD_CONFIG_PIN_REQUIRED')
    wrapper = read_json(Path(config['guard_config'])); original = verify_config(wrapper)
    hip.check_guard_selection(config, original)
    return wrapper


def make_session(config, config_path, plan, item, gate, controller_fd):
    now = int(time.time() * 1000); tick = time.monotonic_ns()
    need(type(gate.last_admission) is dict and gate.last_admission.get('admit_new_worker') is True,
         'INITIAL_ADMISSION_REQUIRED')
    end = min(plan['input_end_ms'], now + PROFILE['max_resident_ms'])
    need(end > now, 'INPUT_WINDOW_CLOSED')
    value = {'schema': 'heptatrader.current100k-warm-session.v1', 'profile': dict(PROFILE),
        'config_path': str(config_path), 'config_sha256': sha_file(config_path),
        'plan_id': plan['plan_id'], 'candidate_id': plan['candidate_id'],
        'producer_manifest_sha256': plan['producer_registration']['sha256'],
        'worker_runtime_inventory_digest': config['worker_runtime_inventory_digest'],
        'parent_pid': os.getpid(), 'nonce': os.urandom(16).hex(),
        'initial_admission_digest': digest(gate.last_admission),
        'started_at_ms': now, 'started_ns': tick, 'expires_at_ms': end,
        'expires_ns': tick + (end - now) * 1000000,
        'anchor_request_id': item['request']['request_id'], 'anchor_request_sha256': item['request_sha256'],
        'anchor_started_at_ms': item['request']['started_at_ms'],
        'heavy_lock_path': str(gate.lease.path), 'heavy_lock_identity': lock_identity(gate.lease.fd),
        'controller_lock_path': str(Path(config['forward_root']) / 'predict-worker.lock'),
        'controller_lock_identity': lock_identity(controller_fd)}
    value['session_id'] = digest(value)
    return value


def validate_session(config, config_path, plan, path, expected, heavy_fd, controller_fd):
    need(sha_file(path) == expected, 'SESSION_PIN_CHANGED')
    value = read_json(path, 65536)
    need(value.get('schema') == 'heptatrader.current100k-warm-session.v1'
         and value.get('profile') == PROFILE
         and value.get('session_id') == digest({k: v for k, v in value.items() if k != 'session_id'}), 'SESSION_IDENTITY')
    need(path.resolve() == path and path == Path(config['forward_root']) / 'warm-sessions' / value['session_id'] / 'session.json',
         'SESSION_PATH')
    need(value['config_path'] == str(config_path) and value['config_sha256'] == sha_file(config_path)
         and value['plan_id'] == plan['plan_id'] and value['candidate_id'] == plan['candidate_id']
         and value['producer_manifest_sha256'] == plan['producer_registration']['sha256']
         and value['worker_runtime_inventory_digest'] == config['worker_runtime_inventory_digest'], 'SESSION_SCOPE')
    need(all(type(value[k]) is int for k in ('parent_pid', 'started_at_ms', 'started_ns', 'expires_at_ms', 'expires_ns'))
         and value['parent_pid'] > 1 and value['parent_pid'] == os.getppid(), 'PARENT_IDENTITY')
    need(value['expires_at_ms'] == min(plan['input_end_ms'], value['started_at_ms'] + PROFILE['max_resident_ms'])
         and value['expires_ns'] == value['started_ns'] + (value['expires_at_ms'] - value['started_at_ms']) * 1000000,
         'SESSION_BOUND_CHANGED')
    need(heavy_fd != controller_fd, 'DISTINCT_LOCKS_REQUIRED')
    for name, fd in (('heavy', heavy_fd), ('controller', controller_fd)):
        path_key = Path(value[name + '_lock_path'])
        need(not path_key.is_symlink() and lock_identity(fd) == value[name + '_lock_identity']
             and [path_key.stat().st_dev, path_key.stat().st_ino] == value[name + '_lock_identity'], 'LOCK_IDENTITY')
        os.set_inheritable(fd, False)
    need(value['controller_lock_path'] == str(Path(config['forward_root']) / 'predict-worker.lock'), 'CONTROLLER_LOCK_SCOPE')
    Budget(value).check()
    return value


def set_parent_death_guard(parent):
    need(type(parent) is int and parent > 1 and parent == os.getppid(), 'PARENT_IDENTITY')
    libc = ctypes.CDLL(None, use_errno=True)
    need(libc.prctl(1, signal.SIGKILL, 0, 0, 0) == 0 and os.getppid() == parent, 'PARENT_DEATH_GUARD')


class StopChild(BaseException):
    pass


def child(config_path, session_path, session_sha, command_fd, reply_fd, heavy_fd, controller_fd):
    # Set the death guard before any expensive source/GPU work. Recheck the
    # same parent afterwards to close the exit-before-prctl race.
    validation_started_ns = time.monotonic_ns()
    header = read_json(session_path, 65536); parent = header.get('parent_pid')
    need(type(parent) is int and parent > 1 and parent == os.getppid(), 'PARENT_IDENTITY')
    set_parent_death_guard(parent)
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    channel = Pipe(command_fd, reply_fd)
    def stopped(*_): raise StopChild()
    signal.signal(signal.SIGTERM, stopped); signal.signal(signal.SIGINT, stopped)
    predictor = None; torch = None; imported = False; proven = False
    try:
        config = read_json(config_path); plan, prepared, snapshot = validated_plan(config)
        session = validate_session(config, config_path, plan, session_path, session_sha, heavy_fd, controller_fd)
        wrapper = original_guard(config)
        need(session['heavy_lock_path'] == str(Path(wrapper['state_root']) / 'heavy-worker.lock'), 'HEAVY_LOCK_SCOPE')
        folder = session_path.parent
        initial = read_json(folder / 'admission-000.json', 2 * 1024**2)
        need(digest(initial['decision']) == session['initial_admission_digest'], 'INITIAL_ADMISSION_CHANGED')
        write_once(folder / 'startup.json', {'schema': 'heptatrader.current100k-warm-startup.v1',
            'session_id': session['session_id'], 'at_ms': int(time.time() * 1000),
            'cpu_validation_ms': (time.monotonic_ns() - validation_started_ns) / 1000000.,
            'initial_admission_age_ms': int(time.time() * 1000) - initial['decision']['at_ms'],
            'phase': 'BEFORE_INITIAL_ADMISSION_EXPIRY_CHECK', 'gpu_loaded': False})
        check_admission(initial, session, 0, session['anchor_request_id'], session['anchor_request_sha256'])
        requests, bindings = original_view(snapshot, config, plan); flat_epoch(requests, bindings, session)
        request_item(requests, session['anchor_request_id'], plan, int(time.time() * 1000))
        from . import resident_hip_runtime as hip
        context = hip.prepare_worker(config, 'predict')
        from sandbox import enter
        receipt = enter({'output_dir': config['forward_root'], 'period_path': config['period_path'],
            'legacy_capture': plan['training_dataset_path'], 'oms_path': config['oms_path'],
            'export_dir': config['attribution_directory'],
            'read_only_paths': config['worker_read_only_paths'] + [config['worker_source_root'], sys.prefix,
                config['role_key_path'], config['witness_trust_path']] + context['readonly']})
        write_once(folder / 'sandbox.json', receipt)
        hip.activate(context); imported = True
        import torch
        hip.prove(context, 'AFTER_TORCH_BEFORE_MODEL'); proven = True
        torch.set_num_threads(2)
        torch.cuda.set_per_process_memory_fraction(min(1., 4 * 1024**3 / torch.cuda.get_device_properties(0).total_memory))
        from .financial_forward_predict import Predictor
        predictor = Predictor(config, ingress_snapshots={plan['plan_id']: snapshot},
            registered_plans={plan['plan_id']: prepared}, front_timing={'warm_session_id': session['session_id'],
                'path_profile': 'VERIFIED_TURN_SNAPSHOT_V1', 'warm_flat_profile': dict(PROFILE)})
        budget = Budget(session); budget.check()
        channel.send(packet(session, 'READY', 0, model_loaded=False))
        while True:
            budget.check()
            message = channel.receive(PROFILE['poll_ms'] / 1000)
            if message is None: continue
            if message.get('kind') == 'DRAIN':
                expect(message, session, 'DRAIN', budget.count + 1, ('reason',)); break
            seq = budget.count + 1
            expect(message, session, 'PREDICT', seq, ('request_id', 'request_sha256', 'admission_sha256'))
            need(seq == budget.dispatch(), 'REQUEST_SEQUENCE')
            admission_path = folder / ('admission-%03d.json' % seq)
            need(sha_file(admission_path) == message['admission_sha256'], 'ADMISSION_PIN_CHANGED')
            admission = read_json(admission_path, 2 * 1024**2)
            check_admission(admission, session, seq, message['request_id'], message['request_sha256'])
            requests, bindings = original_view(snapshot, config, plan); flat_epoch(requests, bindings, session)
            item = request_item(requests, message['request_id'], plan, int(time.time() * 1000))
            need(item['request_sha256'] == message['request_sha256'], 'REQUEST_CHANGED')
            predictor.front_timing.update(warm_request_sequence=seq, warm_model_reused=predictor.raw is not None,
                original_admission_at_ms=admission['decision']['at_ms'])
            result = predictor.predict(plan, item)
            budget.completed()
            channel.send(packet(session, 'DONE', seq, request_id=message['request_id'],
                request_sha256=message['request_sha256'], committed=result is not None,
                model_loaded=predictor.raw is not None))
    except BaseException as exc:
        pending = [exc]; seen = set()
        while pending:
            error = pending.pop()
            if id(error) in seen: continue
            seen.add(id(error)); traceback.clear_frames(error.__traceback__)
            pending.extend(x for x in (error.__cause__, error.__context__) if x is not None)
        normal_deadline = str(exc) in ('VALUE_WARM_SESSION_EXPIRED', 'VALUE_WARM_IDLE_EXPIRED')
        if not isinstance(exc, StopChild) and not normal_deadline: raise
    finally:
        # Interrupts must not run another partial Python cleanup. The bounded
        # parent may escalate to SIGKILL; inherited locks survive until exit.
        signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGINT, signal.SIG_IGN)
        predictor = None
        if imported:
            gc.collect()
            if proven and torch.cuda.is_initialized():
                torch.cuda.synchronize(); torch.cuda.empty_cache()
        channel.close()
        # Do not explicitly close/unlock either inherited flock here. Even an
        # exception or unproven Torch import retains them until process exit.


def spawn_child(config, config_path, session_path, session, gate, controller_fd):
    from . import resident_hip_runtime as hip
    command_read, command_write = os.pipe2(os.O_CLOEXEC)
    reply_read, reply_write = os.pipe2(os.O_CLOEXEC)
    channel = None
    try:
        channel = Pipe(reply_read, command_write)
        root = Path(config['worker_source_root'])
        env = dict(hip.worker_environment(config), PYTHONPATH=':'.join(str(root / p) for p in ('research', 'learn', 'scripts')),
            PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='2',
            HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', USE_TF='0')
        argv = [config['python'], '-B', '-m', 'laya_cell.current100k_warm_flat', '--child',
            '--config', str(config_path), '--session', str(session_path), '--session-sha256', sha_file(session_path),
            '--command-fd', str(command_read), '--reply-fd', str(reply_write),
            '--heavy-fd', str(gate.lease.fd), '--controller-fd', str(controller_fd)]
        process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
            close_fds=True, pass_fds=(command_read, reply_write, gate.lease.fd, controller_fd))
        return process, channel
    except BaseException:
        if channel is not None: channel.close()
        else:
            os.close(reply_read); os.close(command_write)
        raise
    finally:
        os.close(command_read); os.close(reply_write)


def stop_child(process, channel, session, next_sequence, reason):
    if process.poll() is not None: return True
    try: channel.send(packet(session, 'DRAIN', next_sequence, reason=str(reason)[:180]))
    except (OSError, ValueError): pass
    try: process.terminate()
    except ProcessLookupError: pass
    try: process.wait(timeout=PROFILE['shutdown_grace_ms'] / 1000)
    except subprocess.TimeoutExpired:
        try: process.kill()
        except ProcessLookupError: pass
        try: process.wait(timeout=PROFILE['kill_wait_ms'] / 1000)
        except subprocess.TimeoutExpired: return False
    return process.poll() is not None


def run_parent(config, config_path, controller_fd):
    """One finite invocation of the existing predictor service; no self-restart."""
    need('torch' not in sys.modules, 'CPU_PARENT_IMPORTED_TORCH')
    config_path = Path(config_path)
    need(config_path.resolve() == config_path and read_json(config_path) == config, 'CONFIG_PATH_OR_BYTES')
    root = Path(config['forward_root'])
    need(root.is_absolute() and root.resolve() == root, 'CANONICAL_FORWARD_ROOT_REQUIRED')
    plan, prepared, snapshot = validated_plan(config)
    need(lock_identity(controller_fd) == [os.stat(root / 'predict-worker.lock').st_dev,
         os.stat(root / 'predict-worker.lock').st_ino], 'CONTROLLER_LOCK_SCOPE')
    def status(state, **extra):
        value = {'schema': 'heptatrader.financial-forward-predictor-status.v1',
            'at_ms': int(time.time() * 1000), 'state': state, 'plan_id': plan['plan_id'],
            'input_start_ms': plan['input_start_ms'], 'input_end_ms': plan['input_end_ms'],
            'warm_flat_profile': dict(PROFILE), 'actor_updated': False, 'broker_orders_sent': 0, **extra}
        atomic(root / 'predictor-status.json', value); return value
    now = int(time.time() * 1000)
    if now < plan['input_start_ms']: return status('WAIT_FIXED_FUTURE_START')
    if now >= plan['input_end_ms']: return status('FIXED_INPUT_WINDOW_CLOSED_NO_MODEL_LOAD')
    requests, bindings = original_view(snapshot, config, plan)
    fresh = fresh_ids(root, plan, requests, now)
    if not fresh: return status('WAIT_FRESH_ORIGINAL_REQUEST')
    anchor = request_item(requests, fresh[-1], plan, now)
    epoch = {'anchor_request_id': anchor['request']['request_id'],
             'anchor_request_sha256': anchor['request_sha256'], 'anchor_started_at_ms': anchor['request']['started_at_ms']}
    try: flat_epoch(requests, bindings, epoch)
    except ContractError as exc:
        if str(exc) in ('VALUE_WARM_ORIGINAL_PREPARED_OBSERVED', 'VALUE_WARM_ORIGINAL_NONFLAT_OBSERVED'):
            return status('WAIT_NEXT_REAL_FLAT_REQUEST', reason=str(exc))
        raise
    from historical_training.resident_resource_guard import Gate
    from .current100k_forward_worker import acquire_value_resources
    wrapper = original_guard(config); gate = Gate(wrapper, 'value-predictor')
    if not acquire_value_resources(gate, {'plan_id': plan['plan_id'], 'warm_anchor_request': anchor['request']['request_id']}):
        return status('HOLD_SHARED_RESOURCE_OR_SOURCE', resource_admission=gate.last_admission)
    process = channel = session = None; reaped = True; error = None
    reason = 'SESSION_COMPLETE'; inflight = None; ready = False; completed = 0; committed = 0
    budget = None; previous_signals = {}
    try:
        session = make_session(config, config_path, plan, anchor, gate, controller_fd)
        folder = root / 'warm-sessions' / session['session_id']; session_path = folder / 'session.json'
        write_once(session_path, session)
        admission_record(folder, session, 0, anchor, gate.last_admission)
        budget = Budget(session)
        def stop_parent(*_): raise StopChild()
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
            previous_signals[number] = signal.signal(number, stop_parent)
        process, channel = spawn_child(config, config_path, session_path, session, gate, controller_fd)
        reaped = False; handled = set(); last_guard_tick = time.monotonic_ns()
        status('WARM_CHILD_STARTING', session_id=session['session_id'], model_loaded=False)
        while True:
            now, tick = budget.check()
            exit_code = process.poll()
            if exit_code == 0:
                reason = 'CHILD_FINITE_EXIT'; break
            need(exit_code is None, 'CHILD_EXITED_BEFORE_DRAIN')
            requests, bindings = original_view(snapshot, config, plan)
            flat_epoch(requests, bindings, session)
            message = channel.receive(PROFILE['poll_ms'] / 1000)
            if message is not None:
                if not ready:
                    expect(message, session, 'READY', 0, ('model_loaded',))
                    need(message['model_loaded'] is False, 'UNREQUESTED_MODEL_LOAD')
                    ready = True
                else:
                    need(inflight is not None, 'UNEXPECTED_CHILD_RESULT')
                    expect(message, session, 'DONE', budget.count,
                           ('request_id', 'request_sha256', 'committed', 'model_loaded'))
                    need(message['request_id'] == inflight['request']['request_id']
                         and message['request_sha256'] == inflight['request_sha256']
                         and type(message['committed']) is bool and type(message['model_loaded']) is bool, 'RESULT_REQUEST_CHANGED')
                    committed += int(message['committed']); completed += 1
                    handled.add(message['request_id']); inflight = None; budget.completed()
                    status('WARM_FLAT_BOUNDED', session_id=session['session_id'],
                        new_predictions=committed, requests_completed=completed, model_loaded=message['model_loaded'])
                    if not message['committed']:
                        reason = 'PREDICTION_NOT_COMMITTED'; break
            if ready and inflight is None and budget.count >= PROFILE['max_requests']:
                reason = 'REQUEST_BOUND'; break
            now, tick = budget.check()
            fresh = [rid for rid in fresh_ids(root, plan, requests, now, session['anchor_started_at_ms']) if rid not in handled]
            dispatch = ready and inflight is None and bool(fresh)
            if dispatch or tick - last_guard_tick >= PROFILE['idle_guard_recheck_ms'] * 1000000:
                need(sha_file(config_path) == session['config_sha256']
                     and sha_file(Path(config['guard_config'])) == config['guard_config_sha256'], 'CONFIG_OR_GUARD_CHANGED')
                prepared.candidate(config, plan)
                report = held_admission(gate); last_guard_tick = time.monotonic_ns()
                if not report.get('admit_new_worker'):
                    reason = 'ORIGINAL_GUARD_DENIED'; break
                if dispatch:
                    now, _ = budget.check()
                    requests, bindings = original_view(snapshot, config, plan); flat_epoch(requests, bindings, session)
                    item = request_item(requests, fresh[0], plan, now)
                    seq = budget.dispatch()
                    wanted = admission_record(folder, session, seq, item, report)
                    channel.send(packet(session, 'PREDICT', seq, request_id=item['request']['request_id'],
                        request_sha256=item['request_sha256'], admission_sha256=wanted))
                    inflight = item
    except StopChild:
        reason = 'CONTROLLER_DRAIN_REQUESTED'
    except BaseException as exc:
        error = exc; reason = str(exc)[:220]
    finally:
        # Stop and reap before closing the parent's descriptor. If SIGKILL has
        # not reaped a child, leave this fd open until parent process exit; the
        # child also retains its copy and prevents a new worker handoff.
        if process is not None:
            reaped = stop_child(process, channel, session, (budget.count if budget else 0) + 1, reason)
            if reaped: gate.record_native_exit(process.returncode, {'plan_id': plan['plan_id'], 'warm_session_id': session['session_id']})
        if channel is not None: channel.close()
        if reaped: gate.release()
        for number, handler in previous_signals.items(): signal.signal(number, handler)
        if session is not None:
            terminal = {'schema': 'heptatrader.current100k-warm-terminal.v1',
                'session_id': session['session_id'], 'at_ms': int(time.time() * 1000), 'reason': reason,
                'requests_dispatched': budget.count if budget else 0, 'requests_completed': completed,
                'new_predictions': committed, 'child_reaped': reaped,
                'child_exit_code': process.returncode if process is not None else None,
                'resource_admission': gate.last_admission, 'broker_orders_sent': 0, 'actor_updated': False}
            write_once(root / 'warm-sessions' / session['session_id'] / 'terminal.json', terminal)
    need(reaped, 'CHILD_UNREAPED_LEASE_RETAINED')
    expected = ('VALUE_WARM_SESSION_EXPIRED', 'VALUE_WARM_IDLE_EXPIRED',
                'VALUE_WARM_ORIGINAL_PREPARED_OBSERVED', 'VALUE_WARM_ORIGINAL_NONFLAT_OBSERVED')
    result = status('WARM_SESSION_DRAINED', session_id=session['session_id'] if session else None,
        reason=reason, new_predictions=committed, requests_completed=completed,
        model_loaded=False, child_reaped=True, resource_admission=gate.last_admission)
    if error is not None and str(error) not in expected: raise error
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--child', action='store_true', required=True)
    p.add_argument('--config', type=Path, required=True); p.add_argument('--session', type=Path, required=True)
    p.add_argument('--session-sha256', required=True)
    for name in ('command-fd', 'reply-fd', 'heavy-fd', 'controller-fd'): p.add_argument('--' + name, type=int, required=True)
    a = p.parse_args(); os.umask(0o077)
    child(a.config, a.session, a.session_sha256, a.command_fd, a.reply_fd, a.heavy_fd, a.controller_fd)


if __name__ == '__main__': main()
