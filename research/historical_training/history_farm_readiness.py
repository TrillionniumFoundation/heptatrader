"""Bounded same-connection HMDS readiness. Never reconnects or sends a request."""
from contextlib import contextmanager
import threading
import time
from .common import EvidenceError

READY = frozenset(('CONNECTED', 'INACTIVE'))
NOTICES = {2105: 'DISCONNECTED', 2106: 'CONNECTED', 2107: 'INACTIVE'}
FATAL = frozenset((1100, 1300, 326, 502, 504, 2110))


def validate_wait(value):
    if type(value) is not int or not 0 <= value <= 90:
        raise EvidenceError('BOUNDED_CASH_HMDS_STARTUP_WAIT_REQUIRED')
    return value


class FarmReadiness:
    def __init__(self, clock=time.monotonic, wall=time.time):
        self.lock = threading.RLock()
        self.clock, self.wall = clock, wall
        self.started = clock()
        self.state, self.dead = 'UNKNOWN', False
        self.events, self.dispatches = [], []
        self.probe_armed, self.probe = False, None
        self.probe_invalidated = False

    def notice(self, request_id, code, message):
        with self.lock:
            if code in FATAL:
                self.dead = True
            exact_farm = (isinstance(message, str) and ':' in message and
                          message.rsplit(':', 1)[1].strip().casefold() == 'cashhmds')
            if request_id != -1 or code not in NOTICES or not exact_farm:
                return
            if len(self.events) >= 128:
                self.dead = True
                return
            if code == 2105 and (self.probe_armed or self.probe is not None):
                self.probe_invalidated = True
            self.state = NOTICES[code]
            self.events.append({'code': code, 'state': self.state,
                                'elapsed_ms': int((self.clock()-self.started)*1000),
                                'at_ms': int(self.wall()*1000)})

    def wait(self, seconds, alive=lambda: True, sleep=time.sleep, *, allow_unknown_probe=False):
        seconds = validate_wait(seconds)
        if type(allow_unknown_probe) is not bool or (allow_unknown_probe and not seconds):
            raise EvidenceError("BOUNDED_UNKNOWN_PROBE_WAIT_REQUIRED")
        deadline = self.clock()+seconds
        while True:
            with self.lock:
                if self.dead or not alive():
                    raise EvidenceError('HMDS_CONNECTION_LOST_NO_RECONNECT')
                # Zero requests a single nonblocking observation. Otherwise,
                # expiry wins even when a delayed callback just marked READY.
                if seconds and self.clock() >= deadline:
                    if (allow_unknown_probe and self.state == 'UNKNOWN' and
                            not self.events and self.probe is None and not self.probe_armed):
                        self.probe_armed = True
                        return 'UNKNOWN_PROBE_REQUIRED'
                    raise EvidenceError('CASH_HMDS_STARTUP_NOT_READY_NO_HISTORY_DISPATCH')
                if self.state in READY:
                    return self.state
            remaining = deadline-self.clock()
            if remaining <= 0:
                raise EvidenceError('CASH_HMDS_STARTUP_NOT_READY_NO_HISTORY_DISPATCH')
            sleep(min(.05, remaining))

    @contextmanager
    def dispatch(self, code, request_id, *, request_kind=None):
        # Serialize the last local state check with observed disconnect callbacks.
        # This is not a claim of atomicity with the remote broker server.
        with self.lock:
            if self.dead or self.probe_invalidated or self.state == 'DISCONNECTED':
                raise EvidenceError('CASH_HMDS_CHANGED_BEFORE_WIRE_DISPATCH')
            if code not in (20, 87) or len(self.dispatches) >= 31:
                raise EvidenceError('HISTORY_DISPATCH_SCOPE')
            basis = 'FARM_NOTICE'
            if self.state not in READY:
                if self.state == 'UNKNOWN' and self.probe and self.probe.get('verified'):
                    basis = 'COMPLETED_PRICE_BAR_PROBE'
                elif (self.state == 'UNKNOWN' and self.probe_armed and
                      self.probe is None and code == 20 and request_kind == 'bars'):
                    basis = 'ONE_UNKNOWN_PRICE_BAR_PROBE'
                    self.probe_armed = False
                    self.probe = {'request_id': request_id, 'notice_count': len(self.events),
                                  'completed': False, 'verified': False}
                else:
                    raise EvidenceError('CASH_HMDS_CHANGED_BEFORE_WIRE_DISPATCH')
            self.dispatches.append({'code': code, 'request_id': request_id,
                                    'state': self.state, 'notice_count': len(self.events),
                                    'basis': basis,
                                    'elapsed_ms': int((self.clock()-self.started)*1000)})
            yield

    def is_probe(self, request_id):
        with self.lock:
            return self.probe is not None and self.probe['request_id'] == request_id

    def complete_probe(self, request_id, *, received, rows, response_sha256):
        # Only this connection's first dispatched price request can establish
        # demand readiness. Cached receipts and head/schedule callbacks cannot.
        with self.lock:
            if (not self.is_probe(request_id) or self.probe['completed'] or
                    type(received) is not bool or type(rows) is not int):
                raise EvidenceError('UNKNOWN_PROBE_RESPONSE_IDENTITY')
            disconnected = any(e['code'] == 2105
                               for e in self.events[self.probe['notice_count']:])
            verified = (received and rows > 0 and not self.dead and
                        self.state != 'DISCONNECTED' and not disconnected and
                        not self.probe_invalidated)
            self.probe.update(completed=True, verified=verified, rows=rows,
                              response_sha256=response_sha256,
                              completed_at_ms=int(self.wall()*1000))
            if not verified:
                raise EvidenceError('UNKNOWN_PRICE_BAR_PROBE_NOT_CONFIRMED')

    def snapshot(self):
        with self.lock:
            return {'state': self.state, 'fatal': self.dead,
                    'notices': list(self.events), 'dispatch_checks': list(self.dispatches),
                    'elapsed_ms': int((self.clock()-self.started)*1000),
                    'reconnects': 0, 'farm_is_not_broker_signed': True,
                    'unknown_probe_armed': self.probe_armed,
                    'unknown_probe_invalidated': self.probe_invalidated,
                    'unknown_price_bar_probe': dict(self.probe) if self.probe else None}
