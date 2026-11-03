#!/usr/bin/env python3
"""Observe a frozen Current100k trial without becoming its predictor.

Use the installed original ingress and public signature readers. Original
runtime, config, plans, predictions, and Gate state are read-only. The observer
does not acquire a research lease, load Torch, launch workers, or send orders.
Only a separate diagnostics directory is writable; log sync is batched.
"""
from __future__ import annotations
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import stat
import struct
import sys
import time


def read(path, limit=2*1024**2):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > limit:
            raise ValueError('OBSERVER_INPUT_SIZE_OR_TYPE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return json.loads(stream.read(limit+1))
    finally:
        os.close(fd)


def registration_validator(evaluator):
    """Use the original release's strongest available read-only validator.

    Earlier frozen releases predate the header-only shortcut. Their complete
    registration validator remains authoritative; never transplant new runtime
    functions into that frozen import graph to obtain a faster check.
    """
    header = getattr(evaluator, 'validate_registration_header', None)
    if header is not None:
        return header, 'ORIGINAL_HEADER_VALIDATOR'
    return evaluator.validate_registration, 'ORIGINAL_COMPLETE_REGISTRATION_VALIDATOR'


class DirectoryEvents:
    """Use directory events instead of rescanning the denial history each poll."""
    def __init__(self, paths):
        self.lib = ctypes.CDLL(None, use_errno=True)
        self.lib.inotify_init1.argtypes = [ctypes.c_int]
        self.lib.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self.fd = self.lib.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), 'inotify_init1')
        self.paths = {}
        try:
            for path in paths:
                path = Path(path)
                if not path.is_dir():
                    raise ValueError('OBSERVER_EXISTING_SOURCE_DIRECTORY_REQUIRED:' + str(path))
                wd = self.lib.inotify_add_watch(self.fd, os.fsencode(path), 0x00000002 | 0x00000008 | 0x00000080 | 0x00000100)
                if wd < 0:
                    raise OSError(ctypes.get_errno(), 'inotify_add_watch')
                self.paths[wd] = path
        except BaseException:
            self.close(); raise

    def wait(self, seconds):
        changed = []; overflow = False
        if not select.select([self.fd], [], [], seconds)[0]:
            return changed, overflow
        while True:
            try:
                raw = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            offset = 0
            while offset < len(raw):
                wd, mask, cookie, length = struct.unpack_from('iIII', raw, offset)
                offset += 16
                name = raw[offset:offset+length].split(b'\0', 1)[0]; offset += length
                if mask & 0x00004000:
                    overflow = True
                if wd in self.paths and name:
                    changed.append(self.paths[wd] / os.fsdecode(name))
        return changed, overflow

    def close(self):
        if self.fd is not None:
            os.close(self.fd); self.fd = None


class Observer:
    def __init__(self, root, plan, sink, *, started_ms=None, producer_registration=None):
        self.root = Path(root); self.plan = plan; self.sink = sink
        self.started_ms = int(time.time()*1000) if started_ms is None else started_ms
        self.registration = producer_registration or plan.get('producer_registration')
        self.same_scope = producer_registration is None or producer_registration == plan.get('producer_registration')
        self.scope = ('SAME_PRODUCER_PLAN_IN_OBSERVED_CONFIG' if self.same_scope
                      else 'NO_SAME_PRODUCER_PLAN_IN_OBSERVED_CONFIG')
        self.pending = {}; self.emitted = set(); self.last_service = None

    def emit(self, rid, stage, *, semantic=None, **fields):
        key = (rid, stage, semantic)
        if key in self.emitted:
            return
        self.emitted.add(key)
        item = self.pending.get(rid, {})
        self.sink({'schema': 'heptatrader.current100k-request-observation.v1',
            'observed_at_ms': int(time.time()*1000), 'stage': stage,
            'plan_id': self.plan['plan_id'] if self.same_scope else None,
            'candidate_id': self.plan['candidate_id'] if self.same_scope else None,
            'registration_scope_state': self.scope, 'configured_forward_root': str(self.root),
            'reference_prior_plan_id': None if self.same_scope else self.plan['plan_id'],
            'request_id': rid, **item, 'observational_only': True,
            'prediction_or_admission_authority': False, 'broker_orders_sent': 0, **fields})

    def source(self, requests, bindings, now_ms):
        for rid, item in requests.items():
            request = item['request']; start = request['started_at_ms']
            if self.registration and item.get('producer_manifest_sha256') != self.registration['sha256']:
                raise ValueError('OBSERVER_REQUEST_PRODUCER_CHANGED')
            model = self.plan.get('behavior_scope', {}).get('behavior_model_id')
            if model is not None and request.get('model_id') != model:
                continue
            if not (max(self.started_ms-60000, self.plan['input_start_ms']) <= start <= now_ms
                    and start < self.plan['input_end_ms'] and request['position_at_request'] == 0):
                continue
            if not isinstance(rid, str) or Path(rid).name != rid or rid in ('', '.', '..'):
                raise ValueError('OBSERVER_REQUEST_PATH')
            if rid not in self.pending:
                if len(self.pending) >= 4096:
                    raise ValueError('OBSERVER_REQUEST_CAPACITY')
                self.pending[rid] = {'request_started_at_ms': start,
                    'request_source_written_at_ms': item['source_written_at_ms'],
                    'request_sha256': item['request_sha256'],
                    'producer_manifest_sha256': item['producer_manifest_sha256']}
                self.emit(rid, 'ORIGINAL_REQUEST_OBSERVED', request_age_ms=now_ms-start)
                if not self.same_scope:
                    self.emit(rid, 'NO_SAME_PRODUCER_REGISTERED_PLAN_IN_OBSERVED_CONFIG',
                        candidate_assigned=False, original_trial_prediction_eligible=False)
        for binding in bindings:
            rid = binding['request_id']
            if rid in self.pending and binding['command'].get('kind') == 'OPEN':
                command = binding['command']
                self.emit(rid, 'ORIGINAL_OPEN_COMMAND_OBSERVED', semantic=command['command_id'],
                    command_id=command['command_id'], command_created_at_ms=command.get('created_at_ms'))

    def denial(self, path):
        if not self.same_scope:
            return True
        value = read(path); subject = value.get('subject', {}); decision = value.get('decision', {})
        if subject.get('plan_id') != self.plan['plan_id'] or decision.get('admit_new_worker') is not False:
            return True
        ids = subject.get('requests', [])
        if subject.get('warm_anchor_request') is not None:
            ids = [*ids, subject['warm_anchor_request']]
        handled = False
        for rid in set(ids) & set(self.pending):
            started = self.pending[rid]['request_started_at_ms']; stamp = decision.get('at_ms')
            if type(stamp) is int and started <= stamp < started+60000:
                self.emit(rid, 'EXACT_REQUEST_ADMISSION_DENIAL', semantic=str(path),
                    denial_path=str(path), decision_at_ms=stamp, reasons=decision.get('reasons', []),
                    evidence_scope='ORIGINAL_GUARD_FIRST_DENIAL_PER_SUBJECT_AND_REASON')
                handled = True
        return handled

    def results(self, now_ms, prediction_reader, service=None):
        if not self.same_scope:
            # Never search the old producer's plan for the new producer's RID.
            return
        service_state = {key: service.get(key) for key in ('at_ms', 'state', 'reason')} if service else None
        service_semantic = json.dumps({k:v for k,v in (service_state or {}).items() if k != 'at_ms'}, sort_keys=True)
        service_changed = self.last_service != service_semantic
        self.last_service = service_semantic
        for rid, item in self.pending.items():
            if (rid, 'SIGNED_PREDICTION_OBSERVED', None) in self.emitted:
                continue
            folder = self.root / 'plans' / self.plan['plan_id'] / 'predictions' / rid
            if (folder / 'commit.json').is_file():
                prediction = prediction_reader(rid)
                if prediction is not None:
                    self.emit(rid, 'SIGNED_PREDICTION_OBSERVED',
                        computed_at_ms=prediction['computed_at_ms'], committed_at_ms=prediction['committed_at_ms'],
                        front_timing=prediction.get('front_timing'))
                    continue
            if (folder / 'failure.json').is_file():
                failure = read(folder / 'failure.json')
                self.emit(rid, 'REQUEST_FAILURE_OBSERVED', failure=failure,
                    evidence_scope='FAILURE_OBSERVATION_TIME_NOT_AUTOMATIC_ORIGINAL_CAUSE')
            if now_ms >= item['request_started_at_ms']+60000:
                self.emit(rid, 'REQUEST_FRESHNESS_EXPIRED_WITHOUT_SIGNED_PREDICTION')
                continue
            if service_changed and service_state:
                self.emit(rid, 'PREDICTOR_SERVICE_STATE_OBSERVED', semantic=service_semantic,
                    predictor_service=service_state,
                    evidence_scope='CONCURRENT_SERVICE_STATE_NOT_INDIVIDUAL_REQUEST_ADMISSION')


class Log:
    def __init__(self, directory):
        self.path = directory / 'events.jsonl'
        self.fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        self.last_sync = time.monotonic(); self.dirty = False; self.events = 0

    def emit(self, value):
        raw = (json.dumps(value, sort_keys=True, allow_nan=False) + '\n').encode()
        if len(raw) > 65536 or os.fstat(self.fd).st_size + len(raw) > 32*1024**2:
            raise ValueError('OBSERVER_DIAGNOSTIC_STORAGE_BOUND')
        view = memoryview(raw)
        while view:
            view = view[os.write(self.fd, view):]
        self.dirty = True; self.events += 1
        if time.monotonic()-self.last_sync >= 30:
            self.sync()

    def sync(self):
        if self.dirty:
            os.fdatasync(self.fd); self.dirty = False
        self.last_sync = time.monotonic()

    def close(self):
        self.sync(); os.close(self.fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--producer-manifest', type=Path)
    parser.add_argument('--producer-manifest-sha256')
    parser.add_argument('--poll-ms', type=int, default=500)
    parser.add_argument('--duration-seconds', type=int, default=0)
    args = parser.parse_args(); os.umask(0o077)
    if not 250 <= args.poll_ms <= 5000 or not 0 <= args.duration_seconds <= 86400:
        raise ValueError('OBSERVER_BOUNDED_SAMPLING_REQUIRED')
    if bool(args.producer_manifest) != bool(args.producer_manifest_sha256):
        raise ValueError('OBSERVER_EXPLICIT_PRODUCER_PIN_REQUIRED')
    raw = args.config.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.config_sha256:
        raise ValueError('OBSERVER_ORIGINAL_CONFIG_CHANGED')
    config = json.loads(raw); root = Path(config['forward_root']); output = args.output_directory
    if not output.is_absolute() or output.resolve() != output or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('OBSERVER_SEPARATE_OUTPUT_REQUIRED')
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_fd = os.open(output / 'observer.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    source = Path(config['worker_source_root'])
    # An external reader must not create bytecode inside the original release.
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(source / part) for part in ('research', 'learn', 'scripts')]
    from laya_cell.current100k_forward_worker import validate_runtime
    from laya_cell.financial_forward_source import VerifiedIngressSnapshot
    from laya_cell import financial_forward_evaluate as original_evaluator
    validate_registration, validation_method = registration_validator(original_evaluator)
    read_prediction = original_evaluator.read_prediction
    validate_runtime(config)
    paths = list((root / 'plans').glob('*/plan.json'))
    if len(paths) != 1:
        raise ValueError('OBSERVER_EXACTLY_ONE_ORIGINAL_PLAN')
    plan = read(paths[0]); validate_registration(config, paths[0], plan)
    if (plan['worker_runtime_inventory_digest'] != config['worker_runtime_inventory_digest']
            or plan['producer_registration'] != config['producer_registration']
            or plan['period_sha256'] != config['period_sha256']):
        raise ValueError('OBSERVER_ORIGINAL_PLAN_CONFIG_SCOPE_CHANGED')
    guard_raw = Path(config['guard_config']).read_bytes()
    if config.get('guard_config_sha256') and hashlib.sha256(guard_raw).hexdigest() != config['guard_config_sha256']:
        raise ValueError('OBSERVER_ORIGINAL_GUARD_CHANGED')
    guard = json.loads(guard_raw); denial_root = Path(guard['state_root']) / 'denials'
    registration = (plan['producer_registration'] if args.producer_manifest is None else
        {'manifest': str(args.producer_manifest), 'sha256': args.producer_manifest_sha256})
    if hashlib.sha256(Path(registration['manifest']).read_bytes()).hexdigest() != registration['sha256']:
        raise ValueError('OBSERVER_EXPLICIT_PRODUCER_CHANGED')
    journal = Path(read(registration['manifest'])['journal_directory'])
    events = DirectoryEvents([journal, denial_root]); log = Log(output)
    observer = Observer(root, plan, log.emit, producer_registration=registration)
    snapshot = VerifiedIngressSnapshot(registration, config['period_sha256'])
    stop = False
    def stopped(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, stopped); signal.signal(signal.SIGINT, stopped)
    began = time.monotonic(); checked = began; status_at = 0; last_issue = None
    source_checked = 0; pending_denials = {}
    def predict(rid):
        return read_prediction(root, plan, rid, config)
    try:
        changed = []; overflow = False
        while not stop:
            now = int(time.time()*1000); tick = time.monotonic()
            if now >= plan['input_end_ms'] or (args.duration_seconds and tick-began >= args.duration_seconds):
                break
            if tick-checked >= 60:
                if hashlib.sha256(args.config.read_bytes()).hexdigest() != args.config_sha256:
                    raise ValueError('OBSERVER_ORIGINAL_CONFIG_CHANGED')
                validate_runtime(config); validate_registration(config, paths[0], plan); checked = tick
            if source_checked == 0 or tick-source_checked >= 5 or overflow or any(path.parent == journal for path in changed):
                try:
                    _, requests, bindings, issues = snapshot.read(registration, config['period_sha256'])
                    if issues:
                        raise ValueError('ORIGINAL_INGRESS_ISSUES:' + json.dumps(issues, sort_keys=True)[:500])
                    observer.source(requests, bindings, now); last_issue = None
                except (OSError, ValueError) as error:
                    issue = str(error)[:600]
                    if issue != last_issue:
                        log.emit({'schema': 'heptatrader.current100k-observer-health.v1', 'at_ms': now,
                            'state': 'SOURCE_OBSERVATION_UNAVAILABLE', 'reason': issue, 'broker_orders_sent': 0})
                        last_issue = issue
                source_checked = tick
            for path in set(changed):
                if path.parent == denial_root and path.suffix == '.json' and path.is_file():
                    if len(pending_denials) >= 4096:
                        raise ValueError('OBSERVER_PENDING_DENIAL_BOUND')
                    pending_denials.setdefault(path, tick)
            for path, seen_at in list(pending_denials.items()):
                try:
                    handled = observer.denial(path)
                except (OSError, ValueError):
                    handled = False
                # Keep a denial long enough to recover a racing or temporarily
                # unavailable source capture; never turn it into an admission.
                if handled or tick-seen_at >= 60:
                    del pending_denials[path]
            if overflow:
                log.emit({'schema': 'heptatrader.current100k-observer-health.v1', 'at_ms': now,
                    'state': 'EVENT_OVERFLOW_OBSERVATION_GAP', 'complete_denial_capture': False})
            try: service = read(root / 'predictor-status.json')
            except (OSError, ValueError): service = None
            observer.results(now, predict, service)
            if tick-status_at >= 30:
                if 'torch' in sys.modules:
                    raise ValueError('READ_ONLY_OBSERVER_IMPORTED_TORCH')
                status = {'schema': 'heptatrader.current100k-observer-status.v1', 'at_ms': now,
                    'state': ('OBSERVING_ORIGINAL_TRIAL' if observer.same_scope else
                              'OBSERVING_PRODUCER_WITHOUT_PLAN_IN_CONFIG') if last_issue is None else 'SOURCE_OBSERVATION_UNAVAILABLE',
                    'plan_id': plan['plan_id'] if observer.same_scope else None,
                    'reference_prior_plan_id': None if observer.same_scope else plan['plan_id'],
                    'registration_scope_state': observer.scope, 'producer_registration': registration,
                    'observed_config_forward_root': str(root),
                    'original_registration_validation': validation_method,
                    'tracked_requests': len(observer.pending), 'events_written': log.events,
                    'ingress_full_scans': snapshot.full_scans, 'ingress_snapshot_reuses': snapshot.reuses,
                    'original_trial_modified': False, 'prediction_worker_launched': False, 'gpu_loaded': False,
                    'broker_orders_sent': 0, 'log_sync_interval_seconds': 30}
                temporary = output / 'status.json.next'
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                with os.fdopen(fd, 'w') as stream: json.dump(status, stream, sort_keys=True)
                os.replace(temporary, output / 'status.json'); log.sync(); status_at = tick
            changed, overflow = events.wait(args.poll_ms/1000)
    finally:
        log.close(); events.close(); os.close(lock_fd)


if __name__ == '__main__':
    main()
