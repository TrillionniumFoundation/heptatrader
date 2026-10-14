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
            self.state = NOTICES[code]
            self.events.append({'code': code, 'state': self.state,
                                'elapsed_ms': int((self.clock()-self.started)*1000),
                                'at_ms': int(self.wall()*1000)})

    def wait(self, seconds, alive=lambda: True, sleep=time.sleep):
        seconds = validate_wait(seconds)
        deadline = self.clock()+seconds
        while True:
            with self.lock:
                if self.dead or not alive():
                    raise EvidenceError('HMDS_CONNECTION_LOST_NO_RECONNECT')
                if self.state in READY:
                    return self.state
            remaining = deadline-self.clock()
            if remaining <= 0:
                raise EvidenceError('CASH_HMDS_STARTUP_NOT_READY_NO_HISTORY_DISPATCH')
            sleep(min(.05, remaining))

    @contextmanager
    def dispatch(self, code, request_id):
        # Serialize the last local state check with observed disconnect callbacks.
        # This is not a claim of atomicity with the remote broker server.
        with self.lock:
            if self.dead or self.state not in READY:
                raise EvidenceError('CASH_HMDS_CHANGED_BEFORE_WIRE_DISPATCH')
            if code not in (20, 87) or len(self.dispatches) >= 31:
                raise EvidenceError('HISTORY_DISPATCH_SCOPE')
            self.dispatches.append({'code': code, 'request_id': request_id,
                                    'state': self.state, 'notice_count': len(self.events),
                                    'elapsed_ms': int((self.clock()-self.started)*1000)})
            yield

    def snapshot(self):
        with self.lock:
            return {'state': self.state, 'fatal': self.dead,
                    'notices': list(self.events), 'dispatch_checks': list(self.dispatches),
                    'elapsed_ms': int((self.clock()-self.started)*1000),
                    'reconnects': 0, 'farm_is_not_broker_signed': True}
