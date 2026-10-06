"""Best-effort bounded PAPER learning export; never a trading dependency.

A nonblocking queue separates the strategy thread from private append-only I/O.
Overflow/failure is observable and disqualifies affected learning evidence. No
credentials, order permits, account fields or raw broker messages are exported.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import stat
import threading
import time
import uuid

SCHEMA = 'heptatrader.learning-export.v1'
MAX_RECORD = 65536
MAX_SEGMENT = 8 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
KINDS = {'MODEL_INPUT', 'FORECAST', 'QUOTE', 'DECISION', 'SETTLEMENT'}
STATE_FIELDS = ('instrument', 'position', 'entry_price', 'entry_at_ms', 'entries', 'exits',
    'decisions', 'gross_pnl', 'net_pnl', 'fees_complete', 'halt_new_entries',
    'owner_fence_recovery_required', 'lease_uncertain', 'risk_halt_reason',
    'cost_last_exit_ms', 'last_decision_ms', 'last_exit_reason')
COMMAND_FIELDS = ('kind','command_id','broker_order_id','venue_correlation_id',
    'broker_execution_id','side','phase','request_sha256','position_before_send',
    'quantity_mode','cash_quantity','status','send_state')
RECON_FIELDS = ('command_id','kind','broker_order_id','venue_correlation_id','execution_ids',
    'cumulative_filled_quantity','average_fill_price','broker_status','position_after',
    'position_scope','risk_authoritative','service_epoch','connection_epoch',
    'position_generation','reconciled_at_ms')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def projected_state(state):
    out = {k:copy.deepcopy(state[k]) for k in STATE_FIELDS if k in state}
    out['candidate_id'] = digest(state['last_decision']) if state.get('last_decision') else None
    pending = state.get('pending')
    out['pending'] = None if pending is None else {k:copy.deepcopy(pending[k]) for k in COMMAND_FIELDS if k in pending}
    recon = state.get('last_reconciliation') or {}
    out['reconciliation'] = {k:copy.deepcopy(recon[k]) for k in RECON_FIELDS if k in recon}
    # The driver gross counter is not a complete fee ledger.
    if out.get('fees_complete') is not True:
        out['fees_complete'] = False
        out['net_pnl'] = None
    return out


class LearningExport:
    def __init__(self, directory, instrument, *, capacity=128, clock=None):
        if instrument not in ('EUR.USD','BTC.USD') or not 1 <= capacity <= 1024:
            raise ValueError('LEARNING_EXPORT_SCOPE_INVALID')
        self.root = Path(directory)
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        st = self.root.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise ValueError('LEARNING_EXPORT_DIRECTORY_UNSAFE')
        self.clock = clock or (lambda:int(time.time()*1000))
        self.instrument = instrument
        self.producer = uuid.uuid4().hex
        self.queue = queue.Queue(maxsize=capacity)
        self.stop = threading.Event()
        self.dropped = 0
        self.written = 0
        self.error = None
        self.closed = False
        self.previous_decision = None
        self.fd = None
        self.segment = -1
        self.segment_bytes = 0
        self.sequence = 0
        self.previous = '0'*64
        self.scope = {'instrument':instrument,'execution_domain':'PAPER',
                      'source_kind':'REAL_PAPER_RUNTIME_EXPORT','broker_mutation_authorized':False,
                      'exporter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        period = Path.home()/'.config/heptatrader/pnl-period.json'
        if period.exists():
            raw = period.read_bytes()
            if len(raw) > 65536 or json.loads(raw).get('execution_domain') != 'PAPER':
                raise ValueError('LEARNING_EXPORT_PAPER_PERIOD_INVALID')
            self.scope['period_file_sha256'] = hashlib.sha256(raw).hexdigest()
        else:
            self.scope['period_file_sha256'] = None
        self.worker = threading.Thread(target=self._worker,name='hepta-learning-export',daemon=True)
        self.worker.start()

    def emit(self, kind, data):
        if self.closed or self.error or kind not in KINDS:
            self.dropped += 1
            return False
        try:
            body = {'kind':kind,'at_ms':self.clock(),'data':copy.deepcopy(data)}
            if len(canonical(body).encode()) > MAX_RECORD - 2048:
                raise ValueError('LEARNING_EXPORT_RECORD_BOUND')
            if kind == 'DECISION':
                key = digest(body['data'])
                if key == self.previous_decision:
                    return True
            self.queue.put_nowait(body)
            if kind == 'DECISION':
                self.previous_decision = key
            return True
        except Exception:
            self.dropped += 1
            return False

    def _write(self, item):
        body = {'schema':SCHEMA,'producer_id':self.producer,'segment':self.segment,
                'sequence':self.sequence,'previous':self.previous,
                'dropped_total':self.dropped,**item}
        checksum = digest(body)
        raw = (canonical({**body,'sha256':checksum})+'\n').encode()
        offset = 0
        while offset < len(raw):
            n = os.write(self.fd,raw[offset:])
            if n <= 0:
                raise OSError('LEARNING_EXPORT_SHORT_WRITE')
            offset += n
        os.fsync(self.fd)
        self.previous = checksum
        self.sequence += 1
        self.segment_bytes += len(raw)
        self.written += 1

    def _open(self):
        if sum(p.stat().st_size for p in self.root.glob('*.jsonl')) >= MAX_TOTAL:
            raise OSError('LEARNING_EXPORT_STORAGE_BOUND')
        if self.fd is not None:
            os.close(self.fd)
        self.segment += 1
        self.segment_bytes = 0
        path = self.root/(self.producer+'-'+str(self.segment).zfill(4)+'.jsonl')
        self.fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        self._write({'kind':'HEADER','at_ms':self.clock(),'data':self.scope})

    def _health(self):
        body = {'producer_id':self.producer,'written':self.written,'dropped':self.dropped,
                'error':self.error,'queue_depth':self.queue.qsize(),'at_ms':self.clock(),
                'broker_orders_sent':0,'training_performed':False}
        path = self.root/(self.producer+'.health.json')
        tmp = path.with_suffix('.tmp')
        fd = os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        with os.fdopen(fd,'w') as f:
            f.write(canonical(body)+'\n')
        os.replace(tmp,path)

    def _worker(self):
        try:
            self._open()
            while not self.stop.is_set() or not self.queue.empty():
                try:
                    item = self.queue.get(timeout=0.5)
                except queue.Empty:
                    self._health()
                    continue
                try:
                    if self.segment_bytes >= MAX_SEGMENT:
                        self._open()
                    self._write(item)
                finally:
                    self.queue.task_done()
            self._health()
        except Exception as exc:
            self.error = type(exc).__name__
            try:self._health()
            except Exception:pass
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def close(self):
        self.closed = True
        self.stop.set()
        self.worker.join(timeout=2)
