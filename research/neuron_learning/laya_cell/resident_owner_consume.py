"""Single explicit selected-Laya advisory request; no trading or scheduler.

The native owner is checked outside the confined GPU worker, including a fresh
CURRENT check after prediction. The existing research gate owns admission and
the heavy lease. A bounded user service owns the entire worker process tree.
Missing enrollment never starts a unit or GPU worker. Repeated requests report
historical receipt identity, not a newly current prediction.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from .integrity import ContractError, canonical, digest, read_json, sha_file
from .resident_store import write_once

RUNTIME_PINS = ('learn/laya_cell/resident_owner_consume.py', 'learn/laya_cell/resident_launch.py')
WORKER_SECONDS = 90
UNIT_SECONDS = 180
STOP_SECONDS = 15
MEMORY_BYTES = 6 * 1024**3
TASK_LIMIT = 96
TASK_SCHEMA = 'heptatrader.laya-owner-advisory-task.v1'
RESULT_SCHEMA = 'heptatrader.laya-owner-advisory-worker.v1'
GPU_ENVIRONMENT = ('ROCR_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES', 'CUDA_VISIBLE_DEVICES',
    'GPU_DEVICE_ORDINAL', 'HSA_OVERRIDE_GFX_VERSION', 'PYTORCH_HIP_ALLOC_CONF',
    'HSA_ENABLE_SDMA', 'HSA_ENABLE_INTERRUPT', 'HIP_PATH', 'ROCM_PATH', 'LD_LIBRARY_PATH',
    'HIPBLASLT_TENSILE_LIBPATH', 'TORCH_BLAS_PREFER_HIPBLASLT', 'TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL')


def _need(condition, reason):
    if not condition:
        raise ContractError(reason)


def pending(state, **extra):
    return {'state': state, 'product_consumed': False, 'current_output_released': False,
            'canonical_agentd_accepted': False, 'effect_authority': False,
            'action_head_is_trade_permission': False, 'broker_orders_sent': 0, **extra}


class Deferred(ContractError):
    def __init__(self, reason, evidence):
        super().__init__(reason)
        self.evidence = evidence


def check_runtime(config):
    """The new callable entrypoint cannot silently run out of an old package."""
    from .resident import config_check
    from .resident_owner import check_config
    config_check(config)
    pins = config.get('runtime_sha256', {})
    root = Path(config['runtime_directory'])
    for rel in RUNTIME_PINS:
        expected = pins.get(rel)
        path = root / rel
        _need(isinstance(expected, str) and len(expected) == 64 and path.is_file()
              and not path.is_symlink() and sha_file(path) == expected,
              'LAYA_ADVISORY_RUNTIME_PIN_REQUIRED')
    _need(sha_file(Path(__file__)) == pins[RUNTIME_PINS[0]], 'LAYA_ADVISORY_CALLER_CODE_CHANGED')
    return check_config(config)


def _private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    _need(path.is_absolute() and path.resolve() == path and stat.S_ISDIR(info.st_mode)
          and info.st_uid == os.geteuid() and not info.st_mode & 0o077,
          'LAYA_ADVISORY_PRIVATE_DIRECTORY')
    return path


def _write_bytes(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_bytes(path, maximum=8 * 1024**2):
    path = Path(path)
    _need(path.is_absolute() and path.resolve() == path, 'LAYA_ADVISORY_ABSOLUTE_INPUT_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _need(stat.S_ISREG(info.st_mode) and info.st_size <= maximum, 'LAYA_ADVISORY_INPUT_BOUND')
        raw = stream.read(maximum + 1)
    _need(len(raw) <= maximum, 'LAYA_ADVISORY_INPUT_BOUND')
    return raw


def _read_pinned(path, expected, maximum=8 * 1024**2):
    from .resident_owner import _decode
    raw = _read_bytes(path, maximum)
    _need(hashlib.sha256(raw).hexdigest() == expected, 'LAYA_ADVISORY_INPUT_CHANGED')
    return _decode(raw)


def _duration(value):
    # systemctl show renders usec properties as, for example, "3min" or "15s".
    if value.isdecimal():
        return int(value) / 1000000
    parts = re.findall(r'([0-9]+(?:\.[0-9]+)?)(us|ms|min|s|h)', value)
    _need(parts and ''.join(number + unit for number, unit in parts) == value.replace(' ', ''),
          'LAYA_ADVISORY_UNIT_DURATION')
    return sum(float(number) * {'us': 1e-6, 'ms': .001, 's': 1, 'min': 60, 'h': 3600}[unit]
               for number, unit in parts)


def check_unit(unit):
    """An --inside-unit flag alone is not proof of bounded cgroup ownership."""
    _need(re.fullmatch(r'heptatrader-laya-advisory-[0-9a-f]{24}-[0-9]+\.service', unit),
          'LAYA_ADVISORY_UNIT_NAME')
    keys = ('MainPID', 'ControlGroup', 'KillMode', 'RuntimeMaxUSec', 'TimeoutStopUSec',
            'MemoryMax', 'TasksMax', 'Restart', 'SendSIGKILL')
    result = subprocess.run(['/usr/bin/systemctl', '--user', 'show', unit,
        '--property=' + ','.join(keys)], capture_output=True, text=True, timeout=5, check=True)
    props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    cgroups = [line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::')]
    _need(int(props['MainPID']) == os.getpid() and cgroups == [props['ControlGroup']]
          and props['ControlGroup'].endswith('/' + unit) and props['KillMode'] == 'control-group'
          and props['Restart'] == 'no' and props['SendSIGKILL'] == 'yes'
          and 0 < _duration(props['RuntimeMaxUSec']) <= UNIT_SECONDS
          and 0 < _duration(props['TimeoutStopUSec']) <= STOP_SECONDS
          and 0 < int(props['MemoryMax']) <= MEMORY_BYTES
          and 0 < int(props['TasksMax']) <= TASK_LIMIT, 'LAYA_ADVISORY_BOUNDED_CGROUP_REQUIRED')
    return {key: props[key] for key in keys}


def unit_command(config, wrapper_path, prepared_path, request_path, hashes, guard_directory):
    unit = 'heptatrader-laya-advisory-' + hashes['request'][:24] + '-' + str(os.getpid()) + '.service'
    runtime = Path(config['runtime_directory'])
    paths = ':'.join(map(str, (runtime / 'learn', runtime / 'scripts', Path(guard_directory).parent)))
    command = ['/usr/bin/systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
        '--unit=' + unit, '--property=Type=exec', '--property=Restart=no',
        '--property=KillMode=control-group', '--property=SendSIGKILL=yes',
        '--property=RuntimeMaxSec=' + str(UNIT_SECONDS), '--property=TimeoutStopSec=' + str(STOP_SECONDS),
        '--property=MemoryMax=' + str(MEMORY_BYTES), '--property=TasksMax=' + str(TASK_LIMIT),
        '--property=CPUQuota=150%', '--property=CPUWeight=10', '--property=Nice=15',
        '--property=NoNewPrivileges=yes', '--property=RestrictSUIDSGID=yes',
        '--property=LockPersonality=yes', '--property=UMask=0077',
        '--setenv=PYTHONPATH=' + paths, '--setenv=PYTHONDONTWRITEBYTECODE=1']
    command.extend('--setenv=' + name + '=' + os.environ[name] for name in GPU_ENVIRONMENT if name in os.environ)
    command.extend([
        config['python'], '-B', '-m', 'laya_cell.resident_owner_consume',
        '--wrapper', str(wrapper_path), '--prepared', str(prepared_path), '--request', str(request_path),
        '--inside-unit', unit])
    for name in ('wrapper', 'prepared', 'request'):
        command.extend(('--' + name + '-sha256', hashes[name]))
    return command


def _children():
    return [int(value) for value in Path(f'/proc/self/task/{os.getpid()}/children').read_text().split()]


def enable_subreaper():
    _need(not _children(), 'LAYA_ADVISORY_CONTROLLER_ALREADY_HAS_CHILDREN')
    libc = ctypes.CDLL(None, use_errno=True)
    _need(libc.prctl(36, 1, 0, 0, 0) == 0, 'LAYA_ADVISORY_SUBREAPER_REQUIRED')


def stop_and_reap(process, *, grace=2., kill_grace=8.):
    """Reap the leader and every adopted descendant, including setsid orphans.

    The controller must be a subreaper before Popen. Never infer tree completion
    from the leader's wait result alone. A false return retains both leases.
    """
    started = time.monotonic(); exits = {}; unexpected = False
    while True:
        code = process.poll()  # Popen owns its leader's wait status.
        children = _children()
        for pid in children:
            if pid == process.pid:
                continue
            unexpected = True
            try:
                waited, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                continue
            if waited:
                exits[pid] = os.waitstatus_to_exitcode(status)
        remaining = _children()
        if code is not None and not remaining:
            return {'tree_reaped': True, 'leader_exit_code': code,
                    'descendant_exit_codes': exits, 'unexpected_descendants': unexpected}
        elapsed = time.monotonic() - started
        if elapsed >= grace + kill_grace:
            return {'tree_reaped': False, 'leader_exit_code': code,
                    'descendant_exit_codes': exits, 'remaining_pids': remaining}
        sig = signal.SIGTERM if elapsed < grace else signal.SIGKILL
        if code is None:
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        # Killing adopted parents reveals any further nested/setsid children.
        for pid in remaining:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        time.sleep(.02)


def retain_until_unit_teardown():
    """The verified unit's timer kills its whole cgroup; do not drop the flock.

    SIGSTOP cannot be caught and avoids another Python exception/finally path
    releasing a lease while an unreaped native child still exists.
    """
    while True:
        os.kill(os.getpid(), signal.SIGSTOP)


def hold_unreaped(gate, folder, subject, cleanup):
    """A real cleanup failure retains the existing global admission hold.

    This is not a synthetic native signal or an owner selection. The marker
    prevents a cgroup's forced controller exit from reopening GPU admission
    before an operator has verified that the remaining tree has disappeared.
    """
    from historical_training.common import write_once as durable_once
    evidence = folder / 'unreaped-worker.json'
    if not evidence.exists():
        durable_once(evidence, {'schema': 'heptatrader.laya-advisory-unreaped-worker.v1',
            'subject': subject, 'cleanup': cleanup, 'at_ms': int(time.time() * 1000)})
    _need(read_json(evidence).get('subject') == subject, 'LAYA_ADVISORY_UNREAPED_RECEIPT_CHANGED')
    manual = Path(gate.c['manual_hold_path'])
    if not os.path.lexists(manual):
        marker = {'schema': 'heptatrader.operator-diagnostic-hold.v1',
            'owner': 'heptatrader-resident-owner-advisory', 'reason': 'ADVISORY_WORKER_TREE_UNREAPED',
            'created_ms': int(time.time() * 1000),
            'scope': 'NEW_HEAVY_GPU_WORK_ONLY_ORIGINAL_CPU_CLOSEOUT_CONTINUES',
            'evidence': {'receipt_path': str(evidence), 'receipt_sha256': sha_file(evidence)},
            'release_conditions': 'Verify this bounded unit and all recorded children are gone; review this exact cleanup failure.'}
        try:
            durable_once(manual, marker)
        except (OSError, ValueError):
            if not os.path.lexists(manual):
                raise
    fd = os.open(manual.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def check_queue(config):
    from .resident_queue import inspect
    queue = inspect(config)
    if queue['viable_pending_evaluations']:
        raise Deferred('VIABLE_ORIGINAL_EVALUATION_HAS_PRIORITY', queue)
    return queue


def worker_environment(config):
    runtime = Path(config['runtime_directory'])
    # A model worker needs no credential-bearing inherited process variables.
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()),
            'PYTHONPATH': str(runtime / 'learn') + ':' + str(runtime / 'scripts'),
            'PYTHONDONTWRITEBYTECODE': '1', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '2',
            'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'USE_TF': '0'}
    environment.update({name: os.environ[name] for name in GPU_ENVIRONMENT if name in os.environ})
    from .resident_hip_runtime import worker_environment as selected_runtime_environment
    return selected_runtime_environment(config,environment)


def admitted_prediction(config_path, config, folder, raw, request, gate, request_lock, cancelled):
    """The only GPU dispatch path. No trial or future slot is manufactured."""
    check_queue(config)
    subject = {'request_digest': digest(request), 'payload_sha256': hashlib.sha256(raw).hexdigest()}
    if not gate.acquire(subject):
        raise Deferred('ORIGINAL_RESOURCE_OR_SOURCE_ADMISSION_DENIED', gate.last_admission)
    process = None; safe = True; cleanup = None
    try:
        check_queue(config)  # The same original queue is re-read under the lease.
        _need(not cancelled(), 'LAYA_ADVISORY_CANCELLED')
        input_dir = _private_directory(folder / 'input')
        output_dir = _private_directory(folder / 'worker')
        payload_path = input_dir / 'selected-model.json'; _write_bytes(payload_path, raw)
        task = {'schema': TASK_SCHEMA, 'controller_pid': os.getpid(), 'request': dict(request),
            'request_digest': digest(request), 'payload_path': str(payload_path),
            'payload_sha256': subject['payload_sha256'], 'config_sha256': gate.c['original_config_sha256'],
            'output_directory': str(output_dir), 'effect_authority': False, 'broker_orders_sent': 0}
        task_path = input_dir / 'task.json'; write_once(task_path, task)
        task_sha = sha_file(task_path)
        _need(gate.lease.fd is not None and request_lock.fd is not None, 'LAYA_ADVISORY_LEASES_REQUIRED')
        held = (gate.lease.fd, request_lock.fd)
        args = [config['python'], '-B', '-m', 'laya_cell.resident_launch', 'owner-predict',
            '--config', str(config_path), '--owner-task', str(task_path), '--owner-task-sha256', task_sha]
        for fd in held:
            args.extend(('--held-lock-fd', str(fd)))
        deadline = min(time.monotonic() + WORKER_SECONDS,
                       time.monotonic() + (request['expires_at_ms'] - int(time.time() * 1000)) / 1000)
        _need(deadline > time.monotonic(), 'LAYA_ADVISORY_REQUEST_EXPIRED')
        with (output_dir / 'worker.log').open('xb') as stream:
            process = subprocess.Popen(args, stdout=stream, stderr=stream, start_new_session=True,
                pass_fds=held, env=worker_environment(config))
            safe = False; next_queue_check = time.monotonic() + 2
            try:
                while process.poll() is None:
                    _need(not cancelled(), 'LAYA_ADVISORY_CANCELLED')
                    _need(time.monotonic() < deadline, 'LAYA_ADVISORY_WORKER_TIMEOUT_OR_EXPIRY')
                    if time.monotonic() >= next_queue_check:
                        check_queue(config)  # Yield our own request if a real plan is registered.
                        next_queue_check = time.monotonic() + 2
                    try:
                        process.wait(timeout=.2)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                cleanup = stop_and_reap(process)
                safe = cleanup['tree_reaped']
                if not safe:
                    hold_unreaped(gate, folder, subject, cleanup)
                    retain_until_unit_teardown()
                gate.record_native_exit(cleanup['leader_exit_code'], subject)
                for pid, code in cleanup['descendant_exit_codes'].items():
                    gate.record_native_exit(code, {**subject, 'descendant_pid': pid})
        _need(process.returncode == 0 and not cleanup['unexpected_descendants'],
              'LAYA_ADVISORY_WORKER_FAILED_OR_LEFT_DESCENDANTS')
        _need(sha_file(task_path) == task_sha and sha_file(payload_path) == subject['payload_sha256'],
              'LAYA_ADVISORY_WORKER_INPUT_CHANGED')
        result = read_json(output_dir / 'result.json', 256 * 1024)
        _need(result.get('schema') == RESULT_SCHEMA and result.get('task_sha256') == task_sha
              and result.get('request_digest') == digest(request)
              and result.get('payload_sha256') == subject['payload_sha256'],
              'LAYA_ADVISORY_WORKER_RESULT_BINDING')
        return result['output']
    finally:
        # Includes file/JSON/native-receipt errors after wait. Cleanup exceptions
        # also keep the lease until the already verified cgroup is torn down.
        if not safe:
            try:
                cleanup = stop_and_reap(process)
                safe = cleanup['tree_reaped']
            finally:
                if not safe:
                    try:
                        hold_unreaped(gate, folder, subject, cleanup or {'tree_reaped': False, 'inspection_failed': True})
                    finally:
                        retain_until_unit_teardown()
        if safe:
            gate.release()


def controller(args, guard, wrapper, config):
    from . import resident_owner as owner
    unit = check_unit(args.inside_unit)
    enable_subreaper()
    cancelled = [False]
    def stop(*_):
        cancelled[0] = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    prepared = _read_pinned(args.prepared, args.prepared_sha256)
    request = _read_pinned(args.request, args.request_sha256, 64 * 1024)
    payload = owner._prepared(prepared)
    _need(prepared.get('plan_id') == payload['plan']['plan_id'], 'LAYA_ADVISORY_PREPARED_PLAN')
    folder = _private_directory(Path(config['control_directory']) / 'owner-outbox' /
                                payload['plan']['plan_id'] / 'advisory' / digest(request))
    lock = guard.Lease(folder / 'request.lock')
    if not lock.acquire():
        return pending('CANONICAL_ADVISORY_REQUEST_ALREADY_RUNNING')
    try:
        receipt_path = folder / 'receipt.json'
        if receipt_path.exists():
            return pending('CANONICAL_ADVISORY_REQUEST_ALREADY_TERMINAL',
                historical_receipt_path=str(receipt_path), historical_receipt_sha256=sha_file(receipt_path))
        if (folder / 'attempt.json').exists():
            return pending('CANONICAL_ADVISORY_INCOMPLETE_ATTEMPT_REQUIRES_REVIEW', automatic_retry=False)
        binding = {'request_digest': digest(request), 'request_sha256': args.request_sha256,
            'prepared_sha256': args.prepared_sha256, 'wrapper_sha256': args.wrapper_sha256,
            'config_sha256': wrapper['original_config_sha256'], 'artifact_id': prepared['manifest']['artifact_id'],
            'artifact_generation': prepared['manifest']['generation'],
            'content_digest': prepared['manifest']['bytes_digest']}
        write_once(folder / 'attempt.json', {'schema': 'heptatrader.laya-advisory-attempt.v1',
            'binding': binding, 'started_at_ms': int(time.time() * 1000), 'unit': unit, 'automatic_retry': False})
        gate = guard.Gate(wrapper, 'owner-advisory')
        try:
            value = owner.consume(config, prepared, request, prediction_worker=lambda raw, req:
                admitted_prediction(Path(wrapper['original_config']), config, folder, raw, req,
                                    gate, lock, lambda: cancelled[0]))
            _need(not cancelled[0], 'LAYA_ADVISORY_CANCELLED_BEFORE_RELEASE')
        except Deferred as exc:
            value = pending('CANONICAL_ADVISORY_DEFERRED', reason=str(exc), admission=exc.evidence)
        except (ContractError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
            value = pending('CANONICAL_ADVISORY_BLOCKED', reason=str(exc)[:240], error_type=type(exc).__name__)
        receipt = {'schema': 'heptatrader.laya-advisory-request-receipt.v1', 'binding': binding,
            'completed_at_ms': int(time.time() * 1000), 'result': value,
            'product_consume_completed': value.get('product_consumed') is True}
        # Never emit an output if its durable final receipt cannot be recorded.
        write_once(receipt_path, receipt)
        receipt_sha = sha_file(receipt_path)
        if cancelled[0]:
            return pending('CANONICAL_ADVISORY_CANCELLED_BEFORE_DELIVERY',
                historical_receipt_path=str(receipt_path), historical_receipt_sha256=receipt_sha)
        if value.get('product_consumed') is True:
            # A slow fsync must not turn a previously valid private receipt into
            # a fresh public response after the request's delivery deadline.
            try:
                owner._output(value['output'], request, payload, int(time.time() * 1000))
            except ContractError:
                return pending('CANONICAL_ADVISORY_OUTPUT_EXPIRED_BEFORE_DELIVERY',
                    historical_receipt_path=str(receipt_path), historical_receipt_sha256=receipt_sha)
        return {**value, 'receipt_path': str(receipt_path), 'receipt_sha256': receipt_sha,
                'current_output_released': receipt['product_consume_completed']}
    finally:
        lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('wrapper', 'prepared', 'request'):
        parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--' + name + '-sha256')
    parser.add_argument('--inside-unit')
    args = parser.parse_args(); os.umask(0o077)
    for name in ('wrapper', 'prepared', 'request'):
        path = getattr(args, name)
        _need(path.is_absolute() and path.resolve() == path, 'LAYA_ADVISORY_ABSOLUTE_INPUT_REQUIRED')
    from historical_training import resident_resource_guard as guard
    from .resident_owner import _decode
    raw_wrapper = _read_bytes(args.wrapper)
    wrapper_sha = hashlib.sha256(raw_wrapper).hexdigest()
    if args.wrapper_sha256 is not None:
        _need(wrapper_sha == args.wrapper_sha256, 'LAYA_ADVISORY_WRAPPER_CHANGED')
    wrapper = _decode(raw_wrapper)
    config = _read_pinned(wrapper['original_config'], wrapper['original_config_sha256'])
    _need(guard.verify_config(wrapper) == config, 'LAYA_ADVISORY_ORIGINAL_CONFIG_CHANGED')
    host = check_runtime(config)
    if host is None:
        print(json.dumps(pending('AWAITING_CANONICAL_OWNER_ENROLLMENT')), flush=True)
        return
    if args.inside_unit:
        _need(wrapper_sha == args.wrapper_sha256, 'LAYA_ADVISORY_WRAPPER_CHANGED')
        value = controller(args, guard, wrapper, config)
        print(json.dumps(value, sort_keys=True, allow_nan=False), flush=True)
    else:
        hashes = {'wrapper': wrapper_sha, **{name: sha_file(getattr(args, name)) for name in ('prepared', 'request')}}
        for name in hashes:
            supplied = getattr(args, name + '_sha256')
            _need(supplied is None or supplied == hashes[name], 'LAYA_ADVISORY_CALLER_PIN_CHANGED')
        command = unit_command(config, args.wrapper, args.prepared, args.request, hashes, Path(guard.__file__).parent)
        # systemd owns the finite deadline and all descendants even if this
        # invoking terminal disappears. The service has Restart=no.
        try:
            result = subprocess.run(command, check=False, timeout=UNIT_SECONDS + STOP_SECONDS + 30)
        except subprocess.TimeoutExpired:
            unit = next(item.split('=', 1)[1] for item in command if item.startswith('--unit='))
            print(json.dumps(pending('CANONICAL_ADVISORY_CLIENT_WAIT_EXPIRED_RESULT_UNOBSERVED',
                unit=unit, automatic_retry=False, bounded_unit_teardown_remains_authoritative=True)), flush=True)
            raise SystemExit(2)
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
