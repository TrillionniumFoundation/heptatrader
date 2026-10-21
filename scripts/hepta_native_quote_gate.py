#!/usr/bin/env python3
"""Opt-in native quote runner: deny-by-default frame relay to PAPER port 4002.

No order, account, position, global-cancel or arbitrary contract can pass.
Never a production daemon or proof of full Execution/OMS or strict NAV.
"""
from __future__ import annotations
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import struct
import subprocess
import threading
import time


class Denied(ValueError):
    pass


class FrameGate:
    """Validate complete frames before forwarding any of their bytes."""
    def __init__(self, client_id: int):
        if type(client_id) is not int or not 29000 <= client_id <= 29999:
            raise Denied('ISOLATED_CLIENT_ID_REQUIRED')
        self.client_id = client_id
        self.buffer = bytearray()
        self.api_header = self.version = self.started = False
        self.active: dict[int, str] = {}
        self.used: set[int] = set()
        self.counts: Counter[str] = Counter()
        self.wire_bytes = 0

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        if len(self.buffer) > 65536:
            raise Denied('FRAME_BUFFER_BOUND')
        outputs = []
        if not self.api_header:
            if len(self.buffer) < 4:
                return []
            if self.buffer[:4] != b'API\0':
                raise Denied('API_HEADER_REQUIRED')
            del self.buffer[:4]
            self.api_header = True
        while len(self.buffer) >= 4:
            n = struct.unpack('!I', self.buffer[:4])[0]
            if n < 1 or n > 4096:
                raise Denied('FRAME_SIZE_BOUND')
            if len(self.buffer) < n + 4:
                break
            frame = bytes(self.buffer[:n+4]); del self.buffer[:n+4]
            if not self.version:
                if re.fullmatch(rb'v[0-9]{2,3}\.\.[0-9]{2,3}', frame[4:]) is None:
                    raise Denied('PLAIN_SDK_VERSION_HANDSHAKE_ONLY')
                self.version = True
                outputs.append(b'API\0' + frame)
                continue
            self.check(frame[4:])
            self.wire_bytes += len(frame)
            if self.wire_bytes > 1 << 20:
                raise Denied('OUTBOUND_BYTE_BUDGET')
            outputs.append(frame)
        return outputs

    def check(self, payload: bytes) -> None:
        try:
            if not payload.endswith(b'\0'):
                raise Denied('TERMINATED_FIELD_REQUIRED')
            f = payload[:-1].decode('ascii').split('\0')
        except UnicodeError as exc:
            raise Denied('ASCII_FIELDS_REQUIRED') from exc
        op = f[0]; allowed = False
        if op == '71':
            allowed = not self.started and f in (
                ['71','2',str(self.client_id)], ['71','2',str(self.client_id),''])
            if allowed:
                self.started = True
        elif not self.started:
            raise Denied('START_API_REQUIRED')
        elif op == '8':
            allowed = f == ['8','1','1'] and self.counts['8'] < 4
        elif op == '59':
            allowed = f == ['59','1','1']
        elif op == '1':
            if len(f) in (19,20) and (len(f) == 19 or f[19] == ''):
                try:
                    req = int(f[2])
                except ValueError:
                    req = 0
                allowed = (
                    f[0:2] == ['1','11'] and 5100001 <= req < 5200000 and req not in self.used
                    and f[3] == '0' and tuple(f[4:6]) in (('EUR','CASH'),('BTC','CRYPTO'))
                    and f[6] == '' and f[7] in ('0','0.0') and f[8:10] == ['','']
                    and f[10] == ('IDEALPRO' if f[4] == 'EUR' else 'PAXOS')
                    and f[11:15] == ['','USD','',''] and f[15:19] == ['0','','0','0']
                    # Production keeps the active CASH pair while its one
                    # replacement waits for genuine BID+ASK callbacks.
                    and self.counts['1'] < 300 and len(self.active) < 3
                    and list(self.active.values()).count(f[4]) < (2 if f[4] == 'EUR' else 1))
                if allowed:
                    self.used.add(req); self.active[req] = f[4]
        elif op == '2':
            try:
                req = int(f[2]) if len(f) == 3 else 0
            except ValueError:
                req = 0
            allowed = f[:2] == ['2','2'] and len(f) == 3 and req in self.active
            if allowed:
                del self.active[req]
        if not allowed:
            raise Denied('OPCODE_OR_SCOPE_DENIED:' + op[:8])
        self.counts[op] += 1


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exclusive_json(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, sort_keys=True, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())


def validate_owner(client_id: int, home: Path) -> None:
    period = json.loads((home/'.config/heptatrader/pnl-period.json').read_text())
    observer = json.loads((home/'.config/heptatrader/account-observer.json').read_text())
    if period.get('execution_domain') != 'PAPER':
        raise Denied('EXACT_PAPER_PERIOD_REQUIRED')
    owners = {observer['client_id']} | {v['broker_client_id'] for v in period['instruments'].values()}
    if type(client_id) is not int or not 29000 <= client_id <= 29999 or client_id in owners:
        raise Denied('OWNER_CLIENT_COLLISION')


class Relay:
    def __init__(self, listener: socket.socket, client_id: int, log):
        self.listener = listener; self.client_id = client_id; self.log = log
        self.lock = threading.Lock(); self.stop = threading.Event(); self.unsafe = threading.Event()
        self.active: list[socket.socket] = []
        self.sessions = 0; self.forwarded: Counter[str] = Counter()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def emit(self, **fields):
        with self.lock:
            self.log.write(json.dumps(dict(at_ms=time.time_ns()//1_000_000, **fields), sort_keys=True)+'\n')
            self.log.flush()

    def drop(self, reason: str) -> None:
        with self.lock:
            pair = list(self.active)
        self.emit(kind='isolated_socket_close', reason=reason, active_sockets=len(pair))
        for sock in pair:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass

    def serve(self):
        self.listener.settimeout(.2)
        while not self.stop.is_set():
            try: downstream, address = self.listener.accept()
            except socket.timeout: continue
            except OSError: break
            self.sessions += 1
            if self.sessions > 8 or address[0] != '127.0.0.1':
                downstream.close(); self.unsafe.set(); break
            upstream = None
            try:
                upstream = socket.create_connection(('127.0.0.1',4002), timeout=5)
                for sock in (downstream,upstream): sock.settimeout(.2)
                with self.lock: self.active = [downstream,upstream]
                self.emit(kind='relay_connected', session=self.sessions)
                ended = threading.Event(); gate = FrameGate(self.client_id)
                def incoming():
                    try:
                        while not ended.is_set() and not self.stop.is_set():
                            try: data = upstream.recv(65536)
                            except socket.timeout: continue
                            if not data: break
                            downstream.sendall(data)  # Never log Broker account messages.
                    except OSError: pass
                    finally:
                        ended.set()
                        try: downstream.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                reader = threading.Thread(target=incoming, daemon=True); reader.start()
                try:
                    while not ended.is_set() and not self.stop.is_set():
                        try: data = downstream.recv(65536)
                        except socket.timeout: continue
                        if not data: break
                        for frame in gate.feed(data):
                            upstream.sendall(frame)
                            if not frame.startswith(b'API\0'):
                                opcode = frame[4:].split(b'\0',1)[0].decode('ascii')
                                self.forwarded[opcode] += 1
                                self.emit(kind='allowed_frame_forwarded', session=self.sessions, opcode=opcode,
                                          frame_sha256=hashlib.sha256(frame).hexdigest())
                except Denied as exc:
                    self.emit(kind='DENIED_BEFORE_FORWARDING', reason=str(exc), session=self.sessions)
                    self.unsafe.set()
                finally:
                    ended.set()
                    for sock in (upstream,downstream):
                        try: sock.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                    reader.join(2)
                    if reader.is_alive():
                        self.unsafe.set(); self.stop.set()
                    self.emit(kind='relay_session_end', session=self.sessions, validated_opcode_counts=dict(gate.counts))
            except OSError as exc:
                self.emit(kind='relay_transport_error', error_type=type(exc).__name__, session=self.sessions)
            finally:
                with self.lock: self.active = []
                downstream.close()
                if upstream is not None: upstream.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--candidate-root', type=Path, required=True)
    ap.add_argument('--candidate-sha', required=True)
    ap.add_argument('--binary', type=Path, required=True)
    ap.add_argument('--binary-sha256', required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--client-id', type=int, required=True)
    ap.add_argument('--proxy-port', type=int, default=14028)
    ap.add_argument('--duration-sec', type=int, default=1800)
    ap.add_argument('--disconnect-at-sec', default='600,1200')
    args = ap.parse_args(argv)
    if not 30 <= args.duration_sec <= 3600 or not 14000 <= args.proxy_port <= 14999:
        ap.error('duration 30..3600 and proxy port 14000..14999')
    drop_times = sorted(set(int(x) for x in args.disconnect_at_sec.split(',') if x))
    if any(t < 10 or t >= args.duration_sec-10 for t in drop_times):
        ap.error('disconnect times inside predeclared window')
    if os.geteuid() == 0: raise Denied('UNPRIVILEGED_USER_REQUIRED')
    os.umask(0o077); home = Path.home(); validate_owner(args.client_id,home)
    root = args.candidate_root.resolve(strict=True)
    def git(*a): return subprocess.check_output(['git','-C',str(root),*a],text=True,timeout=15).strip()
    if git('rev-parse','HEAD') != args.candidate_sha or git('status','--porcelain'):
        raise Denied('EXACT_CLEAN_CANDIDATE_REQUIRED')
    binary = args.binary.resolve(strict=True)
    if sha(binary) != args.binary_sha256 or not os.access(binary,os.X_OK):
        raise Denied('BINARY_DIGEST_MISMATCH')
    if args.output.exists() or args.output.is_symlink(): raise Denied('NEW_EVIDENCE_DIRECTORY_REQUIRED')
    args.output.mkdir(parents=True,mode=0o700)
    if args.output.resolve() != args.output.absolute(): raise Denied('NO_OUTPUT_SYMLINKS')
    lock_path = home/'.local/state/heptatrader/operator-evidence/quote-validation.lock'
    lock_fd = os.open(lock_path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    info = os.fstat(lock_fd)
    if info.st_uid!=os.geteuid() or not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:
        raise Denied('UNSAFE_COORDINATION_LOCK')
    fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest = dict(schema='heptatrader.native-quote-live-plan.v1',candidate_sha=args.candidate_sha,
        source_tree=git('rev-parse','HEAD^{tree}'),binary_sha256=args.binary_sha256,binary_path=str(binary),
        client_id=args.client_id,proxy_port=args.proxy_port,upstream='127.0.0.1:4002',
        duration_sec=args.duration_sec,isolated_disconnect_at_sec=drop_times,source='NATIVE_REQ_MKT_DATA',
        eur_tbt_subscription=False,quote_age_limit_ms=5000,public_nav_publication=False,
        full_execution_started=False,full_runtime_qualification=False,code_sha256=sha(Path(__file__)),
        start_ms=time.time_ns()//1_000_000)
    exclusive_json(args.output/'plan.json',manifest)
    listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.bind(('127.0.0.1',args.proxy_port));listener.listen(1)
    log=(args.output/'egress.jsonl').open('x');relay=Relay(listener,args.client_id,log)
    out=(args.output/'native.jsonl').open('x');err=(args.output/'native.stderr').open('x')
    env={k:os.environ[k] for k in ('HOME','PATH','LANG','LC_ALL') if k in os.environ}
    env['HEPTA_IB_MARKET_DATA_TYPE']='1'
    began=time.monotonic();child=None;failure=None
    interrupted=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:interrupted.set())
    signal.signal(signal.SIGINT,lambda *_:interrupted.set())
    try:
        relay.thread.start()
        child=subprocess.Popen([str(binary),str(args.proxy_port),str(args.client_id),str(args.duration_sec)],
            stdout=out,stderr=err,env=env,start_new_session=True)
        drops=list(drop_times)
        while child.poll() is None:
            elapsed=time.monotonic()-began
            if interrupted.is_set(): failure='OPERATOR_INTERRUPTED';break
            if relay.unsafe.is_set(): failure='EGRESS_DENIED';break
            if elapsed>args.duration_sec+30: failure='TOTAL_DEADLINE_EXCEEDED';break
            if drops and elapsed>=drops[0]:
                relay.drop('PREDECLARED_ISOLATED_DISCONNECT_'+str(drops.pop(0)))
            if out.tell()>64*1024*1024: failure='EVIDENCE_SIZE_LIMIT';break
            time.sleep(.1)
    finally:
        if child and child.poll() is None:
            os.killpg(child.pid,signal.SIGTERM)
            try: child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait()
        relay.stop.set();relay.drop('RUNNER_CLEANUP');listener.close();relay.thread.join(3)
        for f in (out,err,log): f.flush();os.fsync(f.fileno());f.close()
        fcntl.flock(lock_fd,fcntl.LOCK_UN);os.close(lock_fd)
    native=[]
    for line in (args.output/'native.jsonl').read_text().splitlines():
        try: native.append(json.loads(line))
        except ValueError: failure=failure or 'INVALID_NATIVE_RECEIPT'
    ended=native[-1] if native and native[-1].get('kind')=='end' else {}
    receipt=dict(schema='heptatrader.native-quote-live-result.v1',candidate_sha=args.candidate_sha,
        finished_ms=time.time_ns()//1_000_000,child_exit=child.returncode if child else None,failure=failure,
        duration_completed=ended.get('duration_completed',False),
        source_tree_unchanged=git('rev-parse','HEAD')==args.candidate_sha and not git('status','--porcelain'),
        sessions=relay.sessions,forwarded_opcodes=dict(relay.forwarded),
        eur_ever_fresh=ended.get('eur_fresh_samples',0)>0,
        btc_ever_fresh=ended.get('btc_fresh_samples',0)>0,
        forbidden_frame_forwarded=bool(set(relay.forwarded)-{'71','8','59','1','2'}),native_summary=ended,
        full_runtime_qualification=False,broker_oms_idempotency_proven=False,strict_nav_eligible=False,
        files={p.name:sha(p) for p in args.output.iterdir() if p.is_file()})
    exclusive_json(args.output/'result.json',receipt);print(json.dumps(receipt,sort_keys=True),flush=True)
    return 0 if (not failure and receipt['duration_completed'] and child.returncode==0
                 and receipt['eur_ever_fresh'] and receipt['btc_ever_fresh']) else 2


if __name__=='__main__':
    raise SystemExit(main())
