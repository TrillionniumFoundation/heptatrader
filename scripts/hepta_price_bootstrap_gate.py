"""Opt-in PAPER pre-OPEN PRICE persistence seal; no Value/model/order authority.

The real Capture context and returned object are the only admission source.
File/dir fsync completes before admission; native risk/prepare/submit stay in
the original driver. Disk errors reject OPEN without fencing risk exits.
"""
from __future__ import annotations
from collections import OrderedDict
import copy, hashlib, json, math, os, re, stat, sys, threading, time, uuid
from pathlib import Path
from hepta_decision_attribution import read_private

MODULE = 'hepta_price_bootstrap_gate.py'
PROFILE = 'PRICE_PERSISTENCE_300S_PREOPEN_V1'
SELECTION_SCHEMA = 'heptatrader.pre-open-price-bootstrap.v1'
HORIZON_MS = 300000
SIGNAL_TTL_MS = 60000
OPERATION_BUDGET_MS = 2000
MAX_BYTES = 524288


class GateError(ValueError): pass


def need(value, reason):
    if not value: raise GateError('PRICE_BOOTSTRAP_' + reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value): return hashlib.sha256(canonical(value)).hexdigest()
def wall_ms(): return time.time_ns() // 1000000


def validate_selection(manifest):
    """No filesystem mutation, model import, or implicit default enablement."""
    if 'price_bootstrap_gate' not in manifest: return None
    value = manifest['price_bootstrap_gate']
    need(type(value) is dict and set(value) == {'schema','profile','receipt_root'}
         and value['schema'] == SELECTION_SCHEMA and value['profile'] == PROFILE,
         'EXPLICIT_PROFILE_REQUIRED')
    need(manifest.get('instrument') == 'EUR.USD' and manifest.get('execution_domain') == 'PAPER'
         and manifest.get('runtime_loader') == 'verified-bytes.v1'
         and MODULE in manifest.get('runtime_sha256', {}), 'PINNED_PAPER_FX_PROFILE_REQUIRED')
    root = Path(value['receipt_root'])
    need(root.is_absolute() and root.resolve() == root and root != Path('/'), 'CANONICAL_RECEIPT_ROOT_REQUIRED')
    need(re.fullmatch('[0-9a-f]{64}', manifest.get('runtime_sha256', {}).get(MODULE, '')) is not None,
         'MODULE_PIN_REQUIRED')
    return dict(value)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid, info.st_nlink)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def private_directory(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'DIRECTORY_ALIAS')
    if not path.exists():
        if not path.parent.exists(): private_directory(path.parent)
        path.mkdir(mode=0o700)
        sync_directory(path.parent)
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and not info.st_mode & 0o077, 'PRIVATE_DIRECTORY_REQUIRED')


def publish_exclusive(temporary,path):
    """Linux atomic no-replace rename: no partial final or second hard link."""
    import ctypes
    library=ctypes.CDLL(None,use_errno=True)
    rename=getattr(library,'renameat2',None)
    need(rename is not None,'ATOMIC_NO_REPLACE_UNAVAILABLE')
    rename.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint)
    rename.restype=ctypes.c_int
    if rename(-100,os.fsencode(temporary),-100,os.fsencode(path),1)!=0:  # AT_FDCWD, RENAME_NOREPLACE
        error=ctypes.get_errno()
        raise OSError(error,os.strerror(error),str(path))


def write_once(path, value):
    raw = canonical(value) + b'\n'; need(len(raw) <= MAX_BYTES, 'RECORD_BOUND')
    temporary = path.with_name('.'+path.name+'.writing-'+uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            count = os.write(descriptor, raw[offset:]); need(count > 0, 'SHORT_WRITE'); offset += count
        os.fchmod(descriptor, 0o400); os.fsync(descriptor)
        # A partial write is never published as immutable authority. Atomic
        # no-replace rename also avoids a crash leaving two persistent links.
        publish_exclusive(temporary,path)
        sync_directory(path.parent)
        info = os.fstat(descriptor)
    finally:
        os.close(descriptor)
        try:temporary.unlink()
        except FileNotFoundError:pass
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'stat': identity(info)}


def read_exact(path, expected):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1
             and stat.S_IMODE(before.st_mode) == 0o400 and 0 < before.st_size <= MAX_BYTES,
             'PRIVATE_IMMUTABLE_FILE_REQUIRED')
        if 'stat' in expected: need(identity(before) == expected['stat'], 'SEALED_FILE_IDENTITY_CHANGED')
        parts = []; remaining = before.st_size
        while remaining:
            raw = os.read(descriptor, min(65536, remaining)); need(raw, 'SHORT_READ')
            parts.append(raw); remaining -= len(raw)
        need(identity(os.fstat(descriptor)) == identity(before), 'SEALED_FILE_CHANGED_DURING_READ')
    finally: os.close(descriptor)
    raw = b''.join(parts)
    need(hashlib.sha256(raw).hexdigest() == expected['sha256'], 'SEALED_BYTES_CHANGED')
    return raw


def finite(value): return type(value) in (int, float) and math.isfinite(value)


def past_quote(request):
    history = request.get('history_snapshot')
    need(type(history) is list and 1 <= len(history) <= 24, 'REAL_PAST_QUOTE_REQUIRED')
    started = request.get('started_at_ms'); need(type(started) is int and started > 0, 'REQUEST_TIME_REQUIRED')
    previous = 0
    for quote in history:
        need(type(quote) is dict and type(quote.get('observed_at_ms')) is int
             and previous <= quote['observed_at_ms'] <= started and quote['observed_at_ms'] > 0,
             'PAST_QUOTE_TIME_REQUIRED')
        need(all(finite(quote.get(key)) and quote[key] > 0 for key in ('bid','ask','mid'))
             and quote['bid'] <= quote['ask'] and quote['mid'] == (quote['bid']+quote['ask'])/2,
             'PAST_QUOTE_VALUES_INVALID')
        previous = quote['observed_at_ms']
    need(0 <= started-previous < SIGNAL_TTL_MS, 'QUOTE_ALREADY_EXPIRED_AT_REQUEST')
    return dict(history[-1])


class PriceBootstrapGate:
    def __init__(self, driver, manifest, manifest_sha256):
        selection = validate_selection(manifest); need(selection is not None, 'OPT_IN_REQUIRED')
        need(re.fullmatch('[0-9a-f]{64}', manifest_sha256) is not None, 'PRODUCER_PIN_REQUIRED')
        need(driver.INSTRUMENT == 'EUR.USD' and driver.MODEL_SIGNAL_TTL_MS == SIGNAL_TTL_MS,
             'ORIGINAL_DRIVER_SIGNAL_SCOPE')
        self.driver = driver; self.manifest = copy.deepcopy(manifest); self.producer = manifest_sha256
        self.selection = selection; self.root = Path(selection['receipt_root'])/manifest_sha256
        self.entries = OrderedDict(); self.lock = threading.Lock(); self.local = threading.local()
        self.last_snapshot = None

    def _remember(self, decision, entry):
        with self.lock:
            self.entries[id(decision)] = entry
            while len(self.entries) > 32: self.entries.popitem(last=False)

    def _check(self, entry, operation_started=None):
        now = wall_ms(); tick = time.monotonic_ns(); request = entry['request']; worker = entry['worker']
        need(now >= entry['last_wall_ms'] and tick >= entry['last_monotonic_ns'], 'CLOCK_REVERSED')
        need(worker is not None and worker._deadline == entry['worker_deadline']
             and worker.observed_at_ms == entry['quote']['observed_at_ms'] and worker.position == 0,
             'ORIGINAL_MODEL_WORKER_CHANGED')
        worker._check_usable()  # The original quote-based monotonic deadline and cancellation.
        need(request['started_at_ms'] <= now < min(request['started_at_ms']+SIGNAL_TTL_MS,
             entry['quote']['observed_at_ms']+SIGNAL_TTL_MS), 'SIGNAL_EXPIRED')
        need(entry['request_monotonic_ns'] <= tick
             and tick-entry['request_monotonic_ns'] < SIGNAL_TTL_MS*1000000, 'REQUEST_MONOTONIC_EXPIRED')
        if operation_started is not None:
            need(0 <= tick-operation_started < OPERATION_BUDGET_MS*1000000, 'OPERATION_TIMEOUT')
        entry['last_wall_ms'] = now; entry['last_monotonic_ns'] = tick
        return now

    def _failure(self, entry, reason, phase, decision=None, command_id=None):
        reason = (type(reason).__name__+':'+str(reason))[:240]
        if entry is not None: entry['error'] = reason
        request = (entry or {}).get('request') or {}
        event = {'schema':'heptatrader.price-bootstrap-failure.v1','profile':PROFILE,
            'attempt_id':uuid.uuid4().hex,'producer_manifest_sha256':self.producer,
            'request_id':request.get('request_id'), 'request_sha256':(entry or {}).get('request_sha256'),
            'decision_sha256':(entry or {}).get('decision_sha256'), 'phase':phase,'reason':reason,
            'command_id':command_id,'at_ms':wall_ms(),
            'native_prepare_called':phase in ('PREPARED_RETAINED_NO_SUBMIT','ORIGINAL_PREPARE_OR_SEND_FAILED'),
            'value_coverage_credit':False}
        # Disk failure cannot be repaired by claiming a durable receipt. Keep
        # the real identity in stderr and the original driver's error state.
        try:
            private_directory(self.root); destination = self.root/'failures'; private_directory(destination)
            write_once(destination/(event['attempt_id']+'.json'), event)
        except Exception as exc: event['failure_receipt_error'] = type(exc).__name__
        try: print(json.dumps(event, sort_keys=True), file=sys.stderr, flush=True)
        except Exception: pass
        return GateError(reason+' request_id='+str(event['request_id'])+' request_sha256='+str(event['request_sha256']))

    def reject_capture(self, decision, context, reason):
        entry = {'decision':decision,'request':context.get('request'),
            'request_sha256':context.get('request_sha256'),'decision_sha256':digest(decision),'used':False}
        self._remember(decision, entry); self._failure(entry, reason, 'CAPTURE_REJECTED')

    def capture(self, decision, context, info, producer_manifest_sha256):
        """Called only by Capture with its actual single-call context/object."""
        entry = {'decision':decision,'request':context.get('request'),
            'request_sha256':context.get('request_sha256'),'decision_sha256':digest(decision),'used':False}
        self._remember(decision, entry)
        try:
            need(producer_manifest_sha256 == self.producer and not context.get('invalid') and context['calls'] == 1,
                 'EXACT_CAPTURE_CONTEXT_REQUIRED')
            request = entry['request']; response = context['response']; worker = context.get('worker')
            need(type(request) is dict and re.fullmatch('[0-9a-f]{32}', request.get('request_id','')) is not None
                 and request['request_id'] == context['id'] and digest(request) == entry['request_sha256'], 'REQUEST_IDENTITY')
            need(request['instrument'] == 'EUR.USD' and request['period_sha256'] == self.manifest['period_sha256']
                 and request['model_id'] == self.driver.MODEL and request['position_at_request'] == 0
                 and request['prompt_sha256'] == hashlib.sha256(request['prompt'].encode()).hexdigest(), 'REQUEST_SCOPE')
            need(digest(response) == context['response_sha256'] and response['returncode'] == 0
                 and response['request_id'] == request['request_id'] and response['request_sha256'] == entry['request_sha256']
                 and info['request_id'] == request['request_id'] and info['request_sha256'] == entry['request_sha256']
                 and info['response_sha256'] == context['response_sha256'] and info['decision_sha256'] == entry['decision_sha256']
                 and info['decision'] == decision, 'RESPONSE_DECISION_IDENTITY')
            need(worker is not None and type(context.get('request_monotonic_ns')) is int, 'ORIGINAL_MODEL_WORKER_REQUIRED')
            quote = past_quote(request)
            entry.update(worker=worker,worker_deadline=worker._deadline,quote=quote,
                request_monotonic_ns=context['request_monotonic_ns'],last_wall_ms=request['started_at_ms'],
                last_monotonic_ns=context['request_monotonic_ns'])
            begin = time.monotonic_ns(); computed = self._check(entry, begin)
            need(request['started_at_ms'] <= response['available_at_ms'] <= info['available_at_ms'] <= computed,
                 'ACTOR_CAUSAL_TIME')
            folder = self.root/request['request_id']; private_directory(self.root); private_directory(folder)
            source = {'schema':'heptatrader.price-bootstrap-original-input.v1','producer_manifest_sha256':self.producer,
                'request':request,'request_sha256':entry['request_sha256'],'response':response,
                'response_sha256':context['response_sha256'],'decision':info}
            source_pin = write_once(folder/'input.json', source); self._check(entry, begin)
            prediction = {'schema':'heptatrader.price-persistence-prediction.v1','profile':PROFILE,
                'kind':'PRICE_PERSISTENCE_BASELINE','producer_manifest_sha256':self.producer,
                'period_sha256':request['period_sha256'],'instrument':'EUR.USD',
                'request_id':request['request_id'],'request_sha256':entry['request_sha256'],
                'decision_sha256':entry['decision_sha256'],'original_input_sha256':source_pin['sha256'],
                'selection_sha256':digest(self.selection),'implementation_sha256':self.manifest['runtime_sha256'][MODULE],
                'source_quote':quote,'horizon_ms':HORIZON_MS,'target_at_ms':request['started_at_ms']+HORIZON_MS,
                'point_forecast_mid':quote['mid'],'computed_at_ms':computed,'unit':'USD_PER_EUR_PRICE_NOT_PROFIT',
                'parameter_updates':0,'learned_value_head':False,'value_coverage_credit':False,
                'efficacy_claim':False,'forecast_value_selects_action':False}
            prediction_pin = write_once(folder/'prediction.json', prediction); durable = self._check(entry, begin)
            seal = {'schema':'heptatrader.price-bootstrap-seal.v1','producer_manifest_sha256':self.producer,
                'request_id':request['request_id'],'request_sha256':entry['request_sha256'],
                'original_input_sha256':source_pin['sha256'],'prediction_sha256':prediction_pin['sha256'],
                'payload_files_and_directories_fsync_completed_at_ms':durable,
                'custody':'PRIVATE_EXCLUSIVE_FILES_SHA256_AND_LOCAL_POST_FSYNC_ADMISSION_NOT_VALUE_ROLE_SIGNATURE'}
            seal_pin = write_once(folder/'seal.json', seal); sealed = self._check(entry, begin)
            entry.update(folder=folder,pins={'input.json':source_pin,'prediction.json':prediction_pin,'seal.json':seal_pin},
                sealed_at_ms=sealed,computed_at_ms=computed,seal_operation_ms=(time.monotonic_ns()-begin)/1e6,error=None)
        except Exception as exc: self._failure(entry, exc, 'SEAL_REJECTED')

    def authorize(self, state, side):
        decision = state.get('last_decision')
        with self.lock: entry = self.entries.get(id(decision))
        try:
            need(state.get('pending') is None, 'ORIGINAL_COMMAND_PENDING')
            need(entry is not None and entry['decision'] is decision, 'NO_IDENTICAL_CAPTURED_DECISION_OBJECT')
            need(not entry['used'], 'REQUEST_ALREADY_CONSUMED')
            need(not entry.get('error') and 'pins' in entry, 'NO_COMPLETE_DURABLE_SEAL')
            need(digest(decision) == entry['decision_sha256'] and digest(entry['request']) == entry['request_sha256'],
                 'ORIGINAL_OBJECT_MUTATED')
            need(side in ('BUY','SELL') and side == decision['action'] and self.driver.entry_candidate_allowed(decision)
                 and abs(float(state.get('position', math.inf))) <= self.driver.QUANTITY_TOLERANCE, 'ORIGINAL_ENTRY_SCOPE')
            begin = time.monotonic_ns(); self._check(entry, begin)
            need(hashlib.sha256(read_private(self.manifest['period_path'],65536)).hexdigest() == self.manifest['period_sha256'],
                 'PERIOD_CHANGED')
            for name,pin in entry['pins'].items(): read_exact(entry['folder']/name, pin); self._check(entry, begin)
            # Consumed before any native prepare. Neither an exception nor a
            # restart can turn the same returned object into a second command.
            entry['used'] = True
            admission = {'schema':'heptatrader.price-bootstrap-admission.v1','profile':PROFILE,
                'producer_manifest_sha256':self.producer,'request_id':entry['request']['request_id'],
                'request_sha256':entry['request_sha256'],'decision_sha256':entry['decision_sha256'],'side':side,
                'prediction_sha256':entry['pins']['prediction.json']['sha256'],'seal_sha256':entry['pins']['seal.json']['sha256'],
                'computed_at_ms':entry['computed_at_ms'],'seal_durable_completed_at_ms':entry['sealed_at_ms'],
                'verified_at_ms':self._check(entry, begin),'original_signal_expires_at_ms':entry['quote']['observed_at_ms']+SIGNAL_TTL_MS,
                'value_coverage_credit':False,'native_risk_checks_bypassed':False}
            pin = write_once(entry['folder']/'admission.json', admission); completed = self._check(entry, begin)
            read_exact(entry['folder']/'admission.json', pin); self._check(entry, begin)
            # Preserve strict '< OPEN.created_at_ms' even when fsync and the
            # call boundary happen within one millisecond. Never backdate.
            while self._check(entry, begin) <= completed: time.sleep(.001)
            receipt = {**admission,'admission_sha256':pin['sha256'],'receipt_directory':str(entry['folder']),
                'admission_durable_completed_at_ms':completed,'verified_before_native_prepare_at_ms':self._check(entry, begin)}
            return entry, receipt
        except Exception as exc: raise self._failure(entry, exc, 'OPEN_REJECTED', decision) from exc

    def reconcile_local_unsent(self,state,path,snap):
        """Discard only our durable pre-submit boundary, never an unknown send.

        PREPARED precedes the original durable POSSIBLY_SENT transition. Fresh
        observations supplement this local evidence; lack of a broker row is
        never used to prove that a POSSIBLY_SENT command was not executed.
        """
        pending=state['pending']; receipt=pending.get('price_bootstrap_forecast') or {}
        try:
            need(getattr(self.local,'active',None) is None, 'RECOVERY_DURING_PREPARATION')
            need(pending['kind']=='OPEN' and pending['send_state'] in ('PREPARED','LOCAL_PRE_SUBMIT_BLOCKED')
                 and pending.get('broker_order_id') is None, 'LOCAL_UNSENT_STATE_REQUIRED')
            need(not any(key in pending for key in ('last_submit_transport_ok','last_submit_reason','application_status',
                 'application_reason','application_observed_at_ms','last_status','venue_correlation_id','broker_execution_id')),
                 'SEND_OR_BROKER_EVIDENCE_MUST_USE_ORIGINAL_RECOVERY')
            boundary={'schema':'heptatrader.price-bootstrap-pre-submit-boundary.v1',
                'command_id':pending['command_id'],'forecast_receipt_sha256':digest(receipt),
                'required_next_transport_state':'POSSIBLY_SENT'}
            need(pending.get('price_bootstrap_pre_submit_boundary')==boundary, 'LOCAL_UNSENT_BOUNDARY_CHANGED')
            if pending['send_state']=='LOCAL_PRE_SUBMIT_BLOCKED':
                local=pending.get('price_bootstrap_local_non_send') or {}
                need(local.get('schema')=='heptatrader.price-bootstrap-local-transport-boundary.v1'
                     and local.get('command_id')==pending['command_id']
                     and local.get('forecast_receipt_sha256')==digest(receipt)
                     and local.get('transport_call_entered') is False
                     and local.get('phase') in ('PREPARED','POSSIBLY_SENT_DURABILITY','BEFORE_SUBMIT'),
                     'LOCAL_TRANSPORT_NOT_ENTERED_PROOF_REQUIRED')
            need(self.driver.load_state(path).get('pending')==pending, 'DURABLE_PENDING_STATE_CHANGED')
            need(receipt.get('profile')==PROFILE and receipt.get('producer_manifest_sha256')==self.producer
                 and re.fullmatch('[0-9a-f]{32}',receipt.get('request_id','')) is not None, 'LOCAL_UNSENT_PRODUCER_CHANGED')
            folder=self.root/receipt['request_id']
            need(receipt.get('receipt_directory')==str(folder), 'LOCAL_UNSENT_RECEIPT_PATH_CHANGED')
            admission=json.loads(read_exact(folder/'admission.json',{'sha256':receipt['admission_sha256']}))
            need(all(receipt.get(key)==value for key,value in admission.items()), 'LOCAL_UNSENT_ADMISSION_CHANGED')
            seal=json.loads(read_exact(folder/'seal.json',{'sha256':receipt['seal_sha256']}))
            prediction=json.loads(read_exact(folder/'prediction.json',{'sha256':receipt['prediction_sha256']}))
            source=json.loads(read_exact(folder/'input.json',{'sha256':seal['original_input_sha256']}))
            need(seal['prediction_sha256']==receipt['prediction_sha256']
                 and prediction['original_input_sha256']==seal['original_input_sha256']
                 and source['producer_manifest_sha256']==self.producer
                 and digest(source['request'])==receipt['request_sha256']==source['request_sha256']
                 and source['request']['request_id']==receipt['request_id']
                 and source['decision']['decision_sha256']==receipt['decision_sha256'], 'LOCAL_UNSENT_INPUT_CHANGED')
            observed=self.last_snapshot; now=wall_ms(); tick=time.monotonic_ns()
            need(observed is not None and observed[0] is snap and 0<=tick-observed[1]<OPERATION_BUDGET_MS*1000000
                 and now>=observed[2] and observed[3]<OPERATION_BUDGET_MS*1000000, 'FRESH_ORIGINAL_SNAPSHOT_REQUIRED')
            orders=snap.get('orders') or {}; owner=snap.get('owner_health') or {}; before=pending.get('position_before_send')
            need(not snap.get('settlement_only') and owner.get('source')=='IB' and owner.get('authoritative') is True
                 and orders.get('source')=='IB' and orders.get('authoritative') is True
                 and orders.get('global_active_orders_complete') is True and orders.get('owner_projection_complete') is True
                 and snap.get('active')==[] and orders.get('active_order_ids')==[]
                 and finite(before) and finite(snap.get('position'))
                 and abs(snap['position']-before)<=self.driver.QUANTITY_TOLERANCE
                 and not any(row.get('command_id')==pending['command_id'] for row in orders.get('recent_orders',[])),
                 'LOCAL_UNSENT_AWAITS_UNCHANGED_AUTHORITATIVE_STATE')
            archive={'schema':'heptatrader.price-bootstrap-local-non-send.v1','pending':copy.deepcopy(pending),
                'disposition':'LOCAL_PRE_SUBMIT_DISCARD_NOT_NATIVE_REJECTION','broker_order_created':False,
                'replacement_or_resubmit':False,'rule':'DURABLE_PRE_SUBMIT_BOUNDARY_AND_FRESH_UNCHANGED_ORIGINAL_SNAPSHOT'}
            archive_path=folder/'local-non-send.json'
            try: pin=write_once(archive_path,archive)
            except FileExistsError:
                pin={'sha256':hashlib.sha256(canonical(archive)+b'\n').hexdigest()}
                read_exact(archive_path,pin)
                descriptor=os.open(archive_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
                sync_directory(folder)
            need(0<=time.monotonic_ns()-observed[1]<OPERATION_BUDGET_MS*1000000 and wall_ms()>=now,
                 'LOCAL_UNSENT_RECOVERY_OBSERVATION_EXPIRED')
            state['last_price_bootstrap_local_non_send']={'command_id':pending['command_id'],
                'request_id':receipt['request_id'],'request_sha256':receipt['request_sha256'],
                'archive_path':str(archive_path),'archive_sha256':pin['sha256'],'reconciled_at_ms':wall_ms(),
                'native_rejection_claimed':False,'replacement_or_resubmit':False}
            state['position']=snap['position'];state['pending']=None
            state['last_error']='local PRICE pre-submit block reconciled; original command retained, never submitted'
            try:self.driver.atomic_json(path,state)
            except Exception:
                state['pending']=pending
                raise
        except Exception as exc:
            state['last_error']='PRICE local non-send awaits proof: '+str(exc)[:350]
            self.driver.atomic_json(path,state)

    def install(self):
        driver = self.driver; prepare = driver.prepare_send; atomic = driver.atomic_json
        inspect = driver.inspect_pending; snapshot = driver.snapshot; paper = driver.paper_cmd
        def block_local(path,state,active,exc,phase):
            need(not active['submit_started'], 'LOCAL_REFUSAL_AFTER_TRANSPORT_FORBIDDEN')
            pending=state.get('pending') or {};receipt=active['receipt']
            pending['send_state']='LOCAL_PRE_SUBMIT_BLOCKED'
            pending['local_pre_submit_blocked_reason']=str(exc)[:240]
            pending['price_bootstrap_local_non_send']={
                'schema':'heptatrader.price-bootstrap-local-transport-boundary.v1',
                'command_id':pending.get('command_id'),'forecast_receipt_sha256':digest(receipt),
                'transport_call_entered':False,'phase':phase}
            try:atomic(path,state)
            except Exception:pass
            return self._failure(active['entry'],exc,'PREPARED_RETAINED_NO_SUBMIT',command_id=pending.get('command_id'))
        def guarded_prepare(state,path,binary,socket,token,outbox,kind,quote=None,side=None,**kwargs):
            if kind != 'OPEN': return prepare(state,path,binary,socket,token,outbox,kind,quote,side,**kwargs)
            entry, receipt = self.authorize(state, side)
            previous = getattr(self.local,'active',None)
            self.local.active = {'state':state,'path':path,'entry':entry,'receipt':receipt,
                'submit_started':False,'prepared_complete':False,'transport_state_durable':False}
            try: return prepare(state,path,binary,socket,token,outbox,kind,quote,side,**kwargs)
            except Exception as exc:
                if not isinstance(exc,GateError):
                    self._failure(entry,exc,'ORIGINAL_PREPARE_OR_SEND_FAILED',
                        command_id=(state.get('pending') or {}).get('command_id'))
                raise  # Preserve the original native error and recovery state.
            finally: self.local.active = previous
        def guarded_atomic(path,state):
            active = getattr(self.local,'active',None); pending = state.get('pending') or {}
            if active is None or active['state'] is not state or pending.get('kind') != 'OPEN' or active['submit_started']:
                return atomic(path,state)
            entry=active['entry'];receipt=active['receipt']
            if pending.get('send_state')=='POSSIBLY_SENT':
                begin=time.monotonic_ns()
                try:
                    need(active['prepared_complete'] and not active['transport_state_durable']
                         and pending['command_id']==active['command_id'],'PRE_SUBMIT_TRANSITION_CHANGED')
                    self._check(entry,begin);result=atomic(path,state);self._check(entry,begin)
                    active['transport_state_durable']=True;active['transport_operation_started']=begin
                    return result
                except Exception as exc:raise block_local(path,state,active,exc,'POSSIBLY_SENT_DURABILITY') from exc
            if pending.get('send_state')!='PREPARED':return atomic(path,state)
            # This exact original PREPARED state is durable before the existing
            # POSSIBLY_SENT transition. Recovery never reauthorizes it.
            pending['price_bootstrap_forecast'] = dict(receipt)
            pending['price_bootstrap_pre_submit_boundary'] = {
                'schema':'heptatrader.price-bootstrap-pre-submit-boundary.v1',
                'command_id':pending['command_id'],'forecast_receipt_sha256':digest(receipt),
                'required_next_transport_state':'POSSIBLY_SENT'}
            begin = time.monotonic_ns()
            try:
                need(not active['prepared_complete'],'PREPARED_REPEATED')
                now = self._check(entry,begin)
                need(type(pending.get('created_at_ms')) is int and receipt['computed_at_ms'] <= receipt['seal_durable_completed_at_ms']
                     <= receipt['admission_durable_completed_at_ms'] < pending['created_at_ms'] <= now,
                     'PREPARED_TIME_NOT_AFTER_DURABLE_SEAL')
                need(pending.get('side') == receipt['side'] and pending.get('instrument') == 'EUR.USD', 'PREPARED_SCOPE_CHANGED')
                result = atomic(path,state)
                durable = self._check(entry,begin)
                cached=driver._DURABLE_STATE_CACHE
                need(cached is not None and cached[:3]==(str(path),driver.state_file_identity(path),
                     driver.durable_state_signature(state)) and re.fullmatch('[0-9a-f]{64}',cached[3]) is not None,
                     'ORIGINAL_DURABLE_STATE_CACHE_CHANGED')
                binding={'schema':'heptatrader.price-bootstrap-command-binding.v1',
                    'producer_manifest_sha256':self.producer,'request_id':receipt['request_id'],
                    'request_sha256':receipt['request_sha256'],'decision_sha256':receipt['decision_sha256'],
                    'command_id':pending['command_id'],'prepared_command':copy.deepcopy(pending),
                    'original_prepared_state_sha256':cached[3],
                    'prepared_state_file_and_directory_fsync_completed_at_ms':durable,
                    'transport_submit_observed':False,'value_coverage_credit':False}
                pin=write_once(entry['folder']/'command-binding.json',binding)
                self._check(entry,begin)
                pending['price_bootstrap_command_binding']={'path':str(entry['folder']/'command-binding.json'),
                    'sha256':pin['sha256'],'binding_durable_completed_at_ms':self._check(entry,begin)}
                active['prepared_complete']=True;active['command_id']=pending['command_id'];active['binding_pin']=pin
                return result
            except Exception as exc:
                # Local refusal, never a fabricated native rejection. Any
                # successfully persisted PREPARED still carries its explicit
                # boundary; a failed write never claims new durability.
                raise block_local(path,state,active,exc,'PREPARED') from exc
        def guarded_paper(binary,socket,token,outbox,operation,extra):
            active=getattr(self.local,'active',None)
            if active is not None and operation=='submit':
                state=active['state'];pending=state.get('pending') or {};entry=active['entry']
                try:
                    need(not active['submit_started'] and active['transport_state_durable']
                         and pending.get('send_state')=='POSSIBLY_SENT' and pending.get('kind')=='OPEN'
                         and pending.get('command_id')==active['command_id']
                         and extra==['--command-id',active['command_id']], 'EXACT_ONCE_TRANSPORT_BOUNDARY_REQUIRED')
                    read_exact(entry['folder']/'command-binding.json',active['binding_pin'])
                    self._check(entry,active['transport_operation_started'])
                except Exception as exc:
                    if active['submit_started']:raise  # An entered transport is always unknown-send recovery.
                    raise block_local(active['path'],state,active,exc,'BEFORE_SUBMIT') from exc
                # This is the sole transition to transport-entered. Neither an
                # exception nor an uncertain reply below can be a local discard.
                active['submit_started']=True
            return paper(binary,socket,token,outbox,operation,extra)
        def observed_snapshot(*args,**kwargs):
            started=time.monotonic_ns(); value=snapshot(*args,**kwargs); done=time.monotonic_ns()
            self.last_snapshot=(value,done,wall_ms(),done-started)
            return value
        def guarded_inspect(state,path,binary,socket,token,outbox,snap):
            pending=state.get('pending') or {}
            if (pending.get('send_state') in ('PREPARED','LOCAL_PRE_SUBMIT_BLOCKED')
                    and 'price_bootstrap_pre_submit_boundary' in pending):
                return self.reconcile_local_unsent(state,path,snap)
            return inspect(state,path,binary,socket,token,outbox,snap)
        driver.prepare_send = guarded_prepare; driver.atomic_json = guarded_atomic; driver.PRICE_BOOTSTRAP_GATE = self
        driver.inspect_pending=guarded_inspect;driver.snapshot=observed_snapshot;driver.paper_cmd=guarded_paper
        def close():
            driver.prepare_send = prepare; driver.atomic_json = atomic
            driver.inspect_pending=inspect;driver.snapshot=snapshot;driver.paper_cmd=paper
            del driver.PRICE_BOOTSTRAP_GATE
        return close
