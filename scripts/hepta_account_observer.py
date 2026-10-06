#!/usr/bin/env python3
"""Isolated IB PAPER account observer. Broker transport is read-only by construction.

Only startApi, exact-account account/position subscriptions, and cancellation
of this observer's own subscriptions can cross the send boundary. No trading
service is started, restarted, recovered or modified. SDK is operator-installed.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid

from hepta_account_pnl import normalize_nav, validate_period, positive_int, digest
from hepta_pnl_accounting import EvidenceError
from hepta_pnl_report import (load_private, read_private, write_snapshot, now_ms, fingerprint, source_revision)


class RejectedAccountDownload(EvidenceError):
    """Completed raw input that failed valuation; never accepted NAV or an opening."""
    def __init__(self, reason: str, raw: dict):
        super().__init__(reason)
        self.raw = raw


def publish_unavailable(out: Path, error: Exception) -> dict:
    """Invalidate current NAV before retaining bounded, explicitly rejected evidence."""
    nav = {'schema': 'heptatrader.account-nav.v1', 'authoritative': False,
           'mutation_authorized': False, 'observed_at_ms': now_ms(),
           'reason_code': str(error) if isinstance(error, EvidenceError) else type(error).__name__}
    # A diagnostics write failure must not leave a previously valid NAV published.
    write_snapshot(out/'nav.json', nav)
    if isinstance(error, RejectedAccountDownload):
        # Preserve only the latest rejected raw download. It cannot occupy the
        # successful raw-latest/first slots, certify an opening, or bypass checks.
        write_snapshot(out/'rejected-raw.json', error.raw)
        nav.update(rejected_raw_sha256=digest(error.raw),
                   rejected_raw_file='rejected-raw.json',
                   rejected_raw_is_accepted_nav=False)
        write_snapshot(out/'nav.json', nav)
    return nav


class SendBoundary:
    def __init__(self, account: str, client_id: int, requests: dict):
        self.account, self.client_id, self.requests = account, client_id, requests
        self.counts = {}
        self.started = False
        self.subscribed = set()

    def check(self, msg: str) -> int:
        if not isinstance(msg, str) or not msg.endswith('\0'):
            raise EvidenceError('ACCOUNT_OBSERVER_SEND_FORMAT')
        fields = msg.rstrip('\0').split('\0')
        try: code = int(fields[0])
        except (ValueError, IndexError): raise EvidenceError('ACCOUNT_OBSERVER_SEND_FORMAT') from None
        if code == 71:
            valid = len(fields) == 3 and fields[1:] == ['2', str(self.client_id)]
        elif code == 76:
            # 76, version=1, reqId, exact account, empty model, ledgerAndNLV
            valid = (len(fields) == 6 and fields[1] == '1' and fields[3:5] == [self.account, ''] and
                     (fields[2], fields[5]) in [(str(self.requests['ledger']), '1'), (str(self.requests['account']), '0')])
        elif code == 74:
            valid = len(fields) in (4, 5) and fields[1:4] == ['1', str(self.requests['positions']), self.account] and (len(fields) == 4 or fields[4] == '')
        elif code == 77:
            valid = len(fields) == 3 and fields[1] == '1' and fields[2] in [str(self.requests[x]) for x in ('account', 'ledger')]
        elif code == 75:
            valid = len(fields) == 3 and fields[1:] == ['1', str(self.requests['positions'])]
        else:
            valid = False
        if not valid: raise EvidenceError('ACCOUNT_OBSERVER_MUTATING_OR_FOREIGN_SEND_FORBIDDEN')
        if code == 71:
            if self.started: raise EvidenceError('OBSERVER_START_API_REPLAY')
            self.started = True
        elif not self.started:
            raise EvidenceError('OBSERVER_HANDSHAKE_REQUIRED')
        elif code in (74, 76):
            identity = code, int(fields[2])
            if identity in self.subscribed: raise EvidenceError('OBSERVER_SUBSCRIPTION_DUPLICATE')
            self.subscribed.add(identity)
        elif (76 if code == 77 else 74, int(fields[2])) not in self.subscribed:
            raise EvidenceError('OBSERVER_CANCEL_UNOWNED_SUBSCRIPTION')
        self.counts[str(code)] = self.counts.get(str(code), 0) + 1
        return code


def load_sdk(path: Path):
    resolved = path.resolve(strict=True)
    if not (resolved / 'ibapi/client.py').is_file(): raise EvidenceError('IBAPI_SDK_NOT_INSTALLED')
    # Fixed local operator configuration, not a Broker-provided import path.
    sys.path.insert(0, str(resolved))
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    return EClient, EWrapper


def acquire(period: dict, sdk_path: Path, client_id: int, timeout: int = 12) -> dict:
    validate_period(period)
    if not positive_int(client_id) or client_id in {x['broker_client_id'] for x in period['instruments'].values()}:
        raise EvidenceError('OBSERVER_CLIENT_COLLIDES_WITH_TRADER')
    EClient, EWrapper = load_sdk(sdk_path)
    requests = {'ledger': 1, 'account': 2, 'positions': 3}
    started = now_ms()
    boundary = SendBoundary(period['account'], client_id, requests)
    class AccountReader(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.ready, self.finished = threading.Event(), threading.Event()
            self.rows, self.positions, self.errors, self.informational = [], [], [], []
            self.accounts, self.ends = set(), set()
            self.lost, self.intentional_disconnect = False, False
        def sendMsg(self, msg):
            boundary.check(msg)
            return EClient.sendMsg(self, msg)
        def placeOrder(self, *a, **k): raise EvidenceError('ACCOUNT_OBSERVER_READ_ONLY')
        def cancelOrder(self, *a, **k): raise EvidenceError('ACCOUNT_OBSERVER_READ_ONLY')
        def reqGlobalCancel(self, *a, **k): raise EvidenceError('ACCOUNT_OBSERVER_READ_ONLY')
        def nextValidId(self, _): self.ready.set()
        def managedAccounts(self, accounts): self.accounts = set(accounts.strip(',').split(','))
        def connectionClosed(self):
            if not self.intentional_disconnect: self.lost = True
        def error(self, req, code, message, *args):
            # Keep exact numeric diagnostics, without broker message/account disclosure.
            event = {'request_id': req, 'code': code}
            if req == -1 and code in (2104, 2106, 2107, 2108, 2158, 2172): self.informational.append(event)
            else: self.errors.append(event)
        def accountUpdateMulti(self, req, account, model, key, value, currency):
            if req not in (requests['ledger'], requests['account']) or req in self.ends: return
            if len(self.rows) >= 4096:
                self.errors.append({'code': 'ROW_BOUND'}); self.finished.set(); return
            self.rows.append({'request_id': req, 'account': account, 'model': model, 'key': key,
                              'value': value, 'currency': currency, 'observed_at_ms': now_ms()})
        def accountUpdateMultiEnd(self, req):
            if req in (requests['ledger'], requests['account']): self.ends.add(req)
            if set(requests.values()).issubset(self.ends): self.finished.set()
        def positionMulti(self, req, account, model, contract, position, cost):
            if req != requests['positions'] or req in self.ends: return
            if len(self.positions) >= 256:
                self.errors.append({'code': 'POSITION_BOUND'}); self.finished.set(); return
            self.positions.append({'request_id': req, 'account': account, 'model': model,
                'conid': contract.conId, 'symbol': contract.symbol, 'sec_type': contract.secType,
                'currency': contract.currency, 'quantity': str(position), 'average_cost': str(cost)})
        def positionMultiEnd(self, req):
            if req == requests['positions']: self.ends.add(req)
            if set(requests.values()).issubset(self.ends): self.finished.set()
    client = AccountReader()
    worker = None
    try:
        client.connect('127.0.0.1', 4002, client_id)
        worker = threading.Thread(target=client.run, daemon=True); worker.start()
        if not client.ready.wait(min(timeout, 8)): raise EvidenceError('ACCOUNT_OBSERVER_HANDSHAKE_TIMEOUT')
        deadline = time.monotonic() + 2
        while period['account'] not in client.accounts and time.monotonic() < deadline: time.sleep(.02)
        if period['account'] not in client.accounts: raise EvidenceError('EXACT_PAPER_ACCOUNT_NOT_AVAILABLE')
        client.reqAccountUpdatesMulti(requests['ledger'], period['account'], '', True)
        client.reqAccountUpdatesMulti(requests['account'], period['account'], '', False)
        client.reqPositionsMulti(requests['positions'], period['account'], '')
        if not client.finished.wait(timeout): raise EvidenceError('ACCOUNT_DOWNLOAD_END_TIMEOUT')
        captured = now_ms()
        result = {'schema': 'heptatrader.account-download.v1', 'account': period['account'],
                  'period_id': period['period_id'], 'started_at_ms': started, 'observed_at_ms': captured,
                  'request_ids': requests, 'client_id': client_id, 'connection_id': uuid.uuid4().hex,
                  'read_only': True, 'rows': list(client.rows), 'positions': list(client.positions),
                  'ends': sorted(client.ends), 'connection_lost_during_download': client.lost,
                  'transport_errors': list(client.errors), 'informational_events': list(client.informational),
                  'server_version': client.serverVersion(), 'allowed_send_counts': boundary.counts}
        return result
    finally:
        # Cancel only owned read subscriptions, never orders or another client.
        if client.isConnected():
            for key in ('ledger', 'account'):
                if (76, requests[key]) in boundary.subscribed: client.cancelAccountUpdatesMulti(requests[key])
            if (74, requests['positions']) in boundary.subscribed: client.cancelPositionsMulti(requests['positions'])
        client.intentional_disconnect = True
        client.disconnect()
        if worker: worker.join(2)


def collect_account(config: Path, period_path: Path, state_root: Path) -> tuple[dict, dict]:
    settings = load_private(config)
    if settings.get('schema') != 'heptatrader.account-observer-config.v1': raise EvidenceError('ACCOUNT_OBSERVER_CONFIG_INVALID')
    raw_period, pf = read_private(period_path)
    from hepta_pnl_report import strict_json
    period = strict_json(raw_period.decode())
    validate_period(period)
    journal = state_root / 'execution/oms-journal.jsonl'
    jf = fingerprint(journal)
    raw = acquire(period, Path(settings['sdk_path']), settings['client_id'])
    if pf != fingerprint(period_path) or jf != fingerprint(journal):
        raise EvidenceError('ACCOUNT_COLLECTION_LEDGER_OR_PERIOD_CHANGED')
    raw.update(journal_fingerprint=list(jf), period_sha256=hashlib.sha256(raw_period).hexdigest(), producer_source_sha=source_revision())
    try:
        return normalize_nav(raw, period), raw
    except (ValueError, KeyError, TypeError, ArithmeticError) as error:
        reason = str(error) if isinstance(error, EvidenceError) else type(error).__name__
        raise RejectedAccountDownload(reason, raw) from error


def main(argv=None):
    h = Path.home()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=Path, default=h/'.config/heptatrader/account-observer.json')
    ap.add_argument('--period', type=Path, default=h/'.config/heptatrader/pnl-period.json')
    ap.add_argument('--state-root', type=Path, default=h/'.local/state/heptatrader')
    ap.add_argument('--watch', action='store_true')
    ap.add_argument('--interval-sec', type=int, default=10)
    args = ap.parse_args(argv)
    if os.geteuid() == 0 or not 5 <= args.interval_sec <= 300: ap.error('unprivileged user; interval 5..300 seconds required')
    os.umask(0o077)
    logging.disable(logging.CRITICAL)  # Never let SDK request logs disclose account IDs.
    out = args.state_root / 'pnl/account'
    out.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock = os.open(out/'observer.lock', os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    while not stopped.is_set():
        start = time.monotonic()
        try:
            nav, raw = collect_account(args.config, args.period, args.state_root)
            write_snapshot(out/'raw-latest.json', raw)
            write_snapshot(out/'nav.json', nav)
            first = out/('first-' + hashlib.sha256(nav['period_id'].encode()).hexdigest()[:16] + '.json')
            first_raw = first.with_suffix('.raw.json')
            if not first.exists():
                # Keep the first original account read, never fabricate/reset an opening NAV.
                if not first_raw.exists(): write_snapshot(first_raw, raw, exclusive=True)
                if digest(load_private(first_raw)) == nav['raw_sha256']:
                    write_snapshot(first, nav, exclusive=True)
                else:
                    # Interrupted first publication: recover the existing raw observation,
                    # not a later value relabelled with its timestamp.
                    recovered = normalize_nav(load_private(first_raw), load_private(args.period))
                    write_snapshot(first, recovered, exclusive=True)
            # Each completed observation is a bounded file, never overwrite a
            # reset baseline. Journal movement is checked again by the consumer.
            print(json.dumps({'nav_complete': True, 'observed_at_ms': nav['observed_at_ms']}), flush=True)
        except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
            nav = publish_unavailable(out, error)
            print(json.dumps({'nav_complete': False, 'reason': nav['reason_code']}), flush=True)
        if not args.watch: return 0 if nav.get('authoritative') else 2
        stopped.wait(max(.2, args.interval_sec-(time.monotonic()-start)))
    return 0

if __name__ == '__main__': raise SystemExit(main())
