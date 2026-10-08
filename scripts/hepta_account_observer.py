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

from hepta_account_pnl import (normalize_nav, validate_period, positive_int, digest,
                               normalize_preflight_nav, validate_preflight_scope,
                               diagnose_rejected_fx_download)
from hepta_pnl_accounting import EvidenceError
from hepta_pnl_report import (load_private, read_private, write_snapshot, now_ms, fingerprint, source_revision)


class RejectedAccountDownload(EvidenceError):
    """Completed raw input that failed valuation; never accepted NAV or an opening."""
    def __init__(self, reason: str, raw: dict):
        super().__init__(reason)
        self.raw = raw


def publish_unavailable(out: Path, error: Exception, preflight_scope: dict | None = None) -> dict:
    """Invalidate current NAV before retaining bounded, explicitly rejected evidence."""
    nav = {'schema': 'heptatrader.account-nav.v1', 'authoritative': False,
           'mutation_authorized': False, 'observed_at_ms': now_ms(),
           'reason_code': str(error) if isinstance(error, EvidenceError) else type(error).__name__}
    if preflight_scope is not None:
        nav.update(schema='heptatrader.account-preflight-nav.v1',
                   observation_scope_id=preflight_scope['observation_scope_id'],
                   reporting_scope='PREFLIGHT_NOT_ACCOUNTING_PERIOD', opening_nav_eligible=False)
    # A diagnostics write failure must not leave a previously valid NAV published.
    write_snapshot(out/'nav.json', nav)
    if isinstance(error, RejectedAccountDownload):
        # Preserve only the latest rejected raw download. It cannot occupy the
        # successful raw-latest/first slots, certify an opening, or bypass checks.
        write_snapshot(out/'rejected-raw.json', error.raw)
        nav.update(rejected_raw_sha256=digest(error.raw),
                   rejected_raw_file='rejected-raw.json',
                   rejected_raw_is_accepted_nav=False)
        if str(error) == 'ACCOUNT_NAV_FX_RECONCILIATION_FAILED':
            try:
                prior_file = out/'raw-latest.json'
                prior = load_private(prior_file) if prior_file.exists() else None
                diagnostics = diagnose_rejected_fx_download(error.raw, prior)
                write_snapshot(out/'rejected-fx-diagnostic.json', diagnostics)
                nav['rejected_fx_diagnostic_file'] = 'rejected-fx-diagnostic.json'
            except (EvidenceError, OSError, ValueError, KeyError, TypeError):
                # Diagnostics cannot authorize or prevent fail-closed NAV.
                pass
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
    return _acquire_account(period['account'], sdk_path, client_id, timeout,
        'period_id', period['period_id'], 'heptatrader.account-download.v1')


def acquire_preflight(scope: dict, sdk_path: Path, client_id: int, timeout: int = 12) -> dict:
    validate_preflight_scope(scope)
    if not positive_int(client_id) or client_id in scope['excluded_client_ids']:
        raise EvidenceError('OBSERVER_CLIENT_COLLIDES_WITH_TRADER')
    return _acquire_account(scope['account'], sdk_path, client_id, timeout,
        'observation_scope_id', scope['observation_scope_id'], 'heptatrader.account-preflight-download.v1')


def _acquire_account(account: str, sdk_path: Path, client_id: int, timeout: int,
                     identity_key: str, identity: str, raw_schema: str) -> dict:
    EClient, EWrapper = load_sdk(sdk_path)
    requests = {'ledger': 1, 'account': 2, 'positions': 3}
    started = now_ms()
    boundary = SendBoundary(account, client_id, requests)
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
        while account not in client.accounts and time.monotonic() < deadline: time.sleep(.02)
        if account not in client.accounts: raise EvidenceError('EXACT_PAPER_ACCOUNT_NOT_AVAILABLE')
        client.reqAccountUpdatesMulti(requests['ledger'], account, '', True)
        client.reqAccountUpdatesMulti(requests['account'], account, '', False)
        client.reqPositionsMulti(requests['positions'], account, '')
        if not client.finished.wait(timeout): raise EvidenceError('ACCOUNT_DOWNLOAD_END_TIMEOUT')
        captured = now_ms()
        result = {'schema': raw_schema, 'account': account,
                  identity_key: identity, 'started_at_ms': started, 'observed_at_ms': captured,
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



class PreflightLifecycleEnded(EvidenceError):
    """The pinned reset no longer permits preflight publication."""


def preflight_context(config: Path, scope_path: Path, state_root: Path, *, home=None):
    from datetime import datetime
    home = Path.home() if home is None else Path(home)
    scope_bytes, sf = read_private(scope_path)
    from hepta_pnl_report import strict_json
    scope = strict_json(scope_bytes.decode())
    validate_preflight_scope(scope)
    sid = scope['observation_scope_id']
    expected_config = home/'.config/heptatrader/account-observer.json'
    expected_scope = home/'.config/heptatrader/observations'/sid/'account-preflight.json'
    expected_state = home/'.local/state/heptatrader/observations'/sid
    if (config != expected_config or scope_path != expected_scope or state_root != expected_state or
            any(p.resolve() != p for p in (config, scope_path, state_root))):
        raise PreflightLifecycleEnded('ACCOUNT_PREFLIGHT_PATH_SCOPE_INVALID')
    if (home/'.config/heptatrader/pnl-period.json').exists():
        raise PreflightLifecycleEnded('ACCOUNT_PREFLIGHT_ACTIVE_PERIOD_EXISTS')
    reset_path = home/'.local/state/heptatrader/RESET-STOPPED.json'
    reset_bytes, rf = read_private(reset_path)
    reset = strict_json(reset_bytes.decode())
    stamp = datetime.fromisoformat(reset['time'].replace('Z', '+00:00'))
    if (stamp.tzinfo is None or hashlib.sha256(reset_bytes).hexdigest() != scope['reset_sha256'] or
            reset.get('account') != scope['account'] or reset.get('mode') != 'PAPER' or
            reset.get('state') != 'factory_accounting_reset_auto_trading_stopped' or
            not int(stamp.timestamp()*1000) < scope['created_at_ms'] <= now_ms()):
        raise PreflightLifecycleEnded('ACCOUNT_PREFLIGHT_RESET_CHANGED')
    settings_bytes, cf = read_private(config)
    settings = strict_json(settings_bytes.decode())
    if settings.get('schema') != 'heptatrader.account-observer-config.v1':
        raise EvidenceError('ACCOUNT_OBSERVER_CONFIG_INVALID')
    return scope, settings, (sf, rf, cf), hashlib.sha256(scope_bytes).hexdigest()


def collect_preflight(config: Path, scope_path: Path, state_root: Path, *, home=None, expected_scope_sha256=None):
    scope, settings, fingerprints, scope_sha = preflight_context(config, scope_path, state_root, home=home)
    if expected_scope_sha256 is not None and scope_sha != expected_scope_sha256:
        raise PreflightLifecycleEnded('ACCOUNT_PREFLIGHT_SCOPE_REPLACED_RESTART_REQUIRED')
    raw = acquire_preflight(scope, Path(settings['sdk_path']), settings['client_id'])
    _, _, after, after_sha = preflight_context(config, scope_path, state_root, home=home)
    if fingerprints != after or scope_sha != after_sha:
        raise PreflightLifecycleEnded('ACCOUNT_PREFLIGHT_SCOPE_CHANGED_DURING_READ')
    raw.update(scope_sha256=scope_sha, producer_source_sha=source_revision())
    try:
        return normalize_preflight_nav(raw, scope), raw
    except (ValueError, KeyError, TypeError, ArithmeticError) as error:
        reason = str(error) if isinstance(error, EvidenceError) else type(error).__name__
        raise RejectedAccountDownload(reason, raw) from error


def retain_first_preflight_download(out: Path, raw: dict) -> None:
    # A diagnostic reference only. No reset-opening or accounting-period file.
    path = out/'first-preflight-download.json'
    if not path.exists():
        write_snapshot(path, raw, exclusive=True)
    first = load_private(path)
    if (first.get('schema') != 'heptatrader.account-preflight-download.v1' or
            first.get('observation_scope_id') != raw.get('observation_scope_id') or
            first.get('scope_sha256') != raw.get('scope_sha256') or 'period_id' in first):
        raise PreflightLifecycleEnded('ACCOUNT_PREFLIGHT_FIRST_SOURCE_SCOPE_CONFLICT')


def main(argv=None):
    h = Path.home()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=Path, default=h/'.config/heptatrader/account-observer.json')
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument('--period', type=Path)
    modes.add_argument('--preflight-scope', type=Path)
    ap.add_argument('--state-root', type=Path, default=h/'.local/state/heptatrader')
    ap.add_argument('--watch', action='store_true')
    ap.add_argument('--interval-sec', type=int, default=10)
    args = ap.parse_args(argv)
    if os.geteuid() == 0 or not 5 <= args.interval_sec <= 300: ap.error('unprivileged user; interval 5..300 seconds required')
    os.umask(0o077)
    logging.disable(logging.CRITICAL)  # Never let SDK request logs disclose account IDs.
    scope = None
    scope_sha = None
    if args.preflight_scope:
        scope, _, _, scope_sha = preflight_context(args.config, args.preflight_scope, args.state_root)
    else:
        args.period = args.period or h/'.config/heptatrader/pnl-period.json'
    out = args.state_root / ('account-preflight' if scope is not None else 'pnl/account')
    out.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock = os.open(out/'observer.lock', os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    while not stopped.is_set():
        start = time.monotonic()
        try:
            if scope is not None:
                nav, raw = collect_preflight(args.config, args.preflight_scope, args.state_root,
                                             expected_scope_sha256=scope_sha)
                retain_first_preflight_download(out, raw)
                write_snapshot(out/'raw-latest.json', raw)
                write_snapshot(out/'nav.json', nav)
            else:
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
            status = {'nav_complete': True, 'observed_at_ms': nav['observed_at_ms']}
            if scope is not None:
                status.update(reporting_scope='PREFLIGHT_NOT_ACCOUNTING_PERIOD', opening_nav_eligible=False)
            print(json.dumps(status), flush=True)
        except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as error:
            nav = publish_unavailable(out, error, scope)
            if scope is not None and isinstance(error, RejectedAccountDownload):
                try:
                    retain_first_preflight_download(out, error.raw)
                except PreflightLifecycleEnded as ended:
                    publish_unavailable(out, ended, scope)
                    return 78
            if isinstance(error, PreflightLifecycleEnded):
                return 78
            print(json.dumps({'nav_complete': False, 'reason': nav['reason_code']}), flush=True)
        if not args.watch: return 0 if nav.get('authoritative') else 2
        stopped.wait(max(.2, args.interval_sec-(time.monotonic()-start)))
    return 0

if __name__ == '__main__': raise SystemExit(main())
