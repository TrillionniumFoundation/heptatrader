#!/usr/bin/env python3
"""Bounded Flex Activity XML ingestion: raw provenance, NAV bridge, cash categories.

No account login/trading. An omitted Flex field is unknown, not zero. Closed
statement periods never silently extend to the current live accounting period.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import hashlib
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from hepta_pnl_accounting import EvidenceError, number, amount

FUNDING = ('depositsWithdrawals', 'internalCashTransfers', 'assetTransfers', 'linkingAdjustments')
EFFECTS = ('mtm', 'realized', 'changeInUnrealized', *FUNDING, 'dividends', 'withholdingTax',
           'withholding871m', 'changeInDividendAccruals', 'interest', 'changeInInterestAccruals',
           'advisorFees', 'clientFees', 'otherFees', 'feesReceivable', 'commissions',
           'commissionsReceivable', 'forexCommissions', 'transactionTax', 'taxReceivables',
           'salesTax', 'softDollars', 'netForexTrading', 'forexTranslation', 'other')
ALIASES = {'markToMarket': 'mtm', 'depositsAndWithdrawals': 'depositsWithdrawals',
           'positionTransfers': 'assetTransfers', 'changeInInterestAccrual': 'changeInInterestAccruals'}
META = {'accountId', 'accountAlias', 'model', 'modelCode', 'currency', 'fromDate', 'toDate',
        'startingValue', 'endingValue', 'twr', 'reportDate', 'levelOfDetail'}
CASH_TYPES = {'Deposits/Withdrawals': 'external_cash_flow', 'Deposits': 'external_cash_flow',
              'Withdrawals': 'external_cash_flow', 'Broker Interest Paid': 'financing',
              'Broker Interest Received': 'financing', 'Bond Interest Received': 'interest_income',
              'Withholding Tax': 'withholding_tax', 'Transaction Tax': 'transaction_tax',
              'Sales Tax': 'sales_tax', 'Other Fees': 'other_fees', 'Advisor Fees': 'advisor_fees',
              'Client Fees': 'client_fees', 'Dividends': 'dividends',
              'Payment In Lieu Of Dividends': 'payment_in_lieu'}


def day(value: str, zone: ZoneInfo) -> datetime:
    if not isinstance(value, str): raise EvidenceError('STATEMENT_DATE_MISSING')
    for fmt in ('%Y%m%d', '%Y-%m-%d'):
        try: return datetime.strptime(value, fmt).replace(tzinfo=zone)
        except ValueError: pass
    raise EvidenceError('STATEMENT_DATE_INVALID')


def parse_flex(raw: bytes, account: str, currency: str, timezone_name: str, available_at_ms: int | None = None) -> dict:
    if len(raw) > 16*1024*1024 or not raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise EvidenceError('UNSAFE_OR_OVERSIZE_FLEX_XML')
    if not account.startswith('DU') or currency != 'USD': raise EvidenceError('PAPER_USD_ACCOUNT_REQUIRED')
    if available_at_ms is None: available_at_ms = time.time_ns() // 1000000
    if type(available_at_ms) is not int or available_at_ms <= 0: raise EvidenceError("STATEMENT_AVAILABILITY_TIME_INVALID")
    zone = ZoneInfo(timezone_name)  # explicit report timezone; never assume location
    try: root = ET.fromstring(raw)
    except ET.ParseError: raise EvidenceError('FLEX_XML_INVALID') from None
    if root.tag != 'FlexQueryResponse' or root.get('type') not in ('AF', 'Activity'):
        raise EvidenceError('ACTIVITY_FLEX_REQUIRED_NOT_TRADE_CONFIRMATION')
    all_statements = list(root.iter('FlexStatement'))
    if len(all_statements) > 366: raise EvidenceError('FLEX_STATEMENT_BOUND')
    selected = [s for s in all_statements if s.get('accountId') == account]
    if not selected: raise EvidenceError('EXACT_ACCOUNT_MISSING_FROM_FLEX')
    reports, transactions = [], []
    source_sha = hashlib.sha256(raw).hexdigest()
    transaction_signatures = {}
    for statement in selected:
        if statement.get('model') or statement.get('modelCode'): raise EvidenceError('MODEL_STATEMENT_NOT_ACCOUNT_TOTAL')
        statement_from = day(statement.get('fromDate'), zone)
        statement_to = day(statement.get('toDate'), zone) + timedelta(days=1)
        statement_key = str(int(statement_from.timestamp()*1000)) + ":" + str(int(statement_to.timestamp()*1000))
        statement_sections = sorted({x.tag for x in statement})
        for info in statement.iter('AccountInformation'):
            if info.get('accountId', account) != account or info.get('currency', info.get('baseCurrency', currency)) != currency:
                raise EvidenceError('STATEMENT_ACCOUNT_BASE_CURRENCY_CONFLICT')
        for node in statement.iter('ChangeInNAV'):
            if 'startingValue' not in node.attrib: continue
            attributes = {}
            for k, v in node.attrib.items():
                key = ALIASES.get(k, k)
                if key in attributes: raise EvidenceError('DUPLICATE_NAV_COMPONENT_ALIAS')
                attributes[key] = v
            if attributes.get('accountId', account) != account or attributes.get('currency', currency) != currency or attributes.get('model') or attributes.get('modelCode'):
                raise EvidenceError('NAV_STATEMENT_SCOPE_CONFLICT')
            from_day = day(attributes.get('fromDate', statement.get('fromDate')), zone)
            to_day = day(attributes.get('toDate', statement.get('toDate')), zone) + timedelta(days=1)
            if not from_day < to_day: raise EvidenceError('STATEMENT_DATE_RANGE_INVALID')
            components = {k: amount(number(v)) for k, v in attributes.items() if k in EFFECTS}
            missing = [x for x in FUNDING if x not in components]
            unknown = sorted(set(attributes) - set(EFFECTS) - META)
            start, end = number(attributes.get('startingValue')), number(attributes.get('endingValue'))
            delta = end - start
            calculated = sum((number(v) for v in components.values()), Decimal(0))
            residual = delta - calculated
            reasons = ['MISSING_FUNDING_FIELD:' + k for k in missing] + ['UNKNOWN_NAV_FIELD:' + k for k in unknown]
            if int(to_day.timestamp()*1000) > available_at_ms:
                reasons.append('STATEMENT_PERIOD_NOT_CLOSED_AS_OF_IMPORT')
            tolerance = min(Decimal('0.50'), Decimal('0.01') * (len(components)+2))
            if abs(residual) > tolerance: reasons.append('STATEMENT_NAV_BRIDGE_NOT_BALANCED')
            if 'mtm' not in components and not {'realized', 'changeInUnrealized'}.issubset(components):
                reasons.append('STATEMENT_PNL_COMPONENTS_MISSING')
            funding = None if missing else sum((number(components[k]) for k in FUNDING), Decimal(0))
            reports.append({'account': account, 'currency': currency, 'source': 'IMPORTED_IB_FLEX_ACTIVITY',
                'source_sha256': source_sha, 'timezone': timezone_name,
                'statement_key': statement_key, 'source_sections': statement_sections,
                'from_ms': int(from_day.timestamp()*1000), 'to_ms': int(to_day.timestamp()*1000),
                'date_range_convention': '[from_local_midnight,to_next_local_midnight)',
                'starting_nav': amount(start), 'ending_nav': amount(end), 'nav_change': amount(delta),
                'net_external_contributions': amount(funding),
                'broker_reported_account_net_pnl': amount(delta-funding) if funding is not None and not reasons else None,
                'components': components, 'bridge_residual': amount(residual), 'complete': not reasons,
                'reason_codes': reasons, 'all_in_account_net_pnl': None,
                'personal_income_tax': None, 'unreported_costs': None,
                'expenses_already_in_nav_do_not_subtract_again': True})
        for node in statement.iter('CashTransaction'):
            if len(transactions) >= 50000: raise EvidenceError('CASH_TRANSACTION_BOUND')
            a = node.attrib
            if a.get('accountId', account) != account or a.get('model') or a.get('modelCode'):
                raise EvidenceError('CASH_TRANSACTION_SCOPE_CONFLICT')
            xid = a.get('transactionID') or a.get('transactionId')
            if not xid: raise EvidenceError('CASH_TRANSACTION_ID_MISSING')
            c = a.get('currency'); rate = number(a.get('fxRateToBase'))
            if not isinstance(c, str) or not re.fullmatch('[A-Z]{3}', c) or rate <= 0 or (c == currency and rate != 1):
                raise EvidenceError('CASH_TRANSACTION_FX_INVALID')
            n = number(a.get('amount'))
            signature = (statement_key, a.get('dateTime'), a.get('type'), c, amount(n), amount(rate))
            if xid in transaction_signatures:
                if transaction_signatures[xid] != signature: raise EvidenceError('CASH_TRANSACTION_ID_CONFLICT')
                continue
            transaction_signatures[xid] = signature
            transactions.append({'transaction_id': xid, 'raw_date_time': a.get('dateTime'), 'type': a.get('type'),
                'category': CASH_TYPES.get(a.get('type'), 'UNCLASSIFIED_NOT_ZERO'), 'currency': c,
                'amount': amount(n), 'fx_rate_to_base': amount(rate), 'amount_usd': amount(n*rate),
                'source_sha256': source_sha, 'statement_key': statement_key})
    if not reports: raise EvidenceError('FLEX_CHANGE_IN_NAV_SECTION_REQUIRED')
    return {'schema': 'heptatrader.account-activity-index.v1', 'account': account, 'currency': currency,
            'source_sha256': source_sha, 'statements': reports, 'cash_transactions': transactions,
            'source_documents': [{'sha256': source_sha, 'timezone': timezone_name, 'imported_at_ms': available_at_ms}],
            'source_sections': sorted({x.tag for s in selected for x in s}),
            'live_period_coverage_inferred': False, 'mutation_authorized': False}


def merge_activity(existing: dict | None, incoming: dict) -> dict:
    if existing is None: return incoming
    if existing.get('account') != incoming['account'] or existing.get('currency') != incoming['currency']:
        raise EvidenceError('ACTIVITY_IMPORT_ACCOUNT_CONFLICT')
    result = dict(existing)
    statements = list(existing.get('statements', []))
    for s in incoming['statements']:
        same = [x for x in statements if (x['from_ms'],x['to_ms']) == (s['from_ms'],s['to_ms'])]
        def economics(x): return {k:v for k,v in x.items() if k != 'source_sha256'}
        if same and any(economics(x) != economics(s) for x in same):
            raise EvidenceError('STATEMENT_REVISION_REQUIRES_RECONCILIATION')
        if not same: statements.append(s)
    if len(statements) > 366: raise EvidenceError('ACTIVITY_STATEMENT_BOUND')
    txs = {x['transaction_id']: x for x in existing.get('cash_transactions', [])}
    for x in incoming.get('cash_transactions', []):
        old = txs.get(x['transaction_id'])
        if old and {k:v for k,v in old.items() if k!='source_sha256'} != {k:v for k,v in x.items() if k!='source_sha256'}:
            raise EvidenceError('CASH_TRANSACTION_IMPORT_CONFLICT')
        txs[x['transaction_id']] = x
    documents = {x['sha256']: x for x in existing.get('source_documents', [])}
    for x in incoming.get('source_documents', []):
        if x['sha256'] in documents and documents[x['sha256']]['timezone'] != x['timezone']:
            raise EvidenceError('STATEMENT_SOURCE_TIMEZONE_CONFLICT')
        documents.setdefault(x['sha256'], x)
    if len(documents) > 366: raise EvidenceError('ACTIVITY_SOURCE_BOUND')
    result.update(statements=sorted(statements, key=lambda s: (s['from_ms'],s['to_ms'])),
                  cash_transactions=list(txs.values()), source_documents=list(documents.values()))
    return result


def import_flex(path: Path, period_path: Path, destination: Path, timezone_name: str) -> dict:
    import fcntl, os
    from hepta_pnl_report import load_private, read_private, write_snapshot
    from hepta_pnl_accounting import validate_period
    period = load_private(period_path); validate_period(period)
    raw, _ = read_private(path, 16*1024*1024)
    incoming = parse_flex(raw, period['account'], period['currency'], timezone_name)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(destination/'import.lock', os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        index = destination/'activity.json'
        result = merge_activity(load_private(index) if index.exists() else None, incoming)
        sources = destination/'statement-sources'; sources.mkdir(mode=0o700, exist_ok=True)
        source = sources/(incoming['source_sha256'] + '.xml')
        if source.exists():
            if read_private(source, 16*1024*1024)[0] != raw: raise EvidenceError('STATEMENT_SOURCE_HASH_CONFLICT')
        else:
            fd = os.open(source, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        import json
        if len(json.dumps(result, ensure_ascii=False, indent=2).encode()) > 900000:
            raise EvidenceError("ACTIVITY_INDEX_CAPACITY_REQUIRES_ARCHIVE")
        write_snapshot(index, result)
        return result
    finally: os.close(lock)


def exact_transaction_time(value: str, timezone_name: str) -> int:
    """Require seconds and an explicit report timezone; dates alone are insufficient."""
    if not isinstance(value, str): raise EvidenceError('CASH_TRANSACTION_TIME_MISSING')
    for fmt in ('%Y%m%d;%H%M%S', '%Y%m%d;%H:%M:%S', '%Y-%m-%d;%H:%M:%S'):
        try:
            naive = datetime.strptime(value, fmt)
            z = ZoneInfo(timezone_name)
            local = naive.replace(tzinfo=z)
            # DST ambiguity/gaps require a source with offset, not a guessed fold.
            if local.utcoffset() != naive.replace(tzinfo=z, fold=1).utcoffset():
                raise EvidenceError('AMBIGUOUS_CASH_TRANSACTION_TIME')
            return int(local.timestamp()*1000)
        except ValueError as error:
            if isinstance(error, EvidenceError): raise
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None: raise EvidenceError('CASH_TRANSACTION_TIMEZONE_MISSING')
        return int(parsed.timestamp()*1000)
    except ValueError as error:
        if isinstance(error, EvidenceError): raise
        raise EvidenceError('CASH_TRANSACTION_TIMESTAMP_INVALID') from None


def derive_external_flows(activity: dict, begin_ms: int, end_ms: int) -> dict:
    """Derive cash flows from parsed transaction rows and statement control totals.

    Nonzero asset/internal/linking transfers remain blocked until their dated
    detail is supported; never mistake them for profit or silently ignore them.
    """
    if activity.get('schema') != 'heptatrader.account-activity-index.v1':
        raise EvidenceError('ACTIVITY_INDEX_INVALID')
    sections = set(activity.get('source_sections', []))
    if 'CashTransactions' not in sections:
        raise EvidenceError('CASH_TRANSACTION_SECTION_MISSING')
    candidates = [s for s in activity.get('statements', []) if s.get('complete') is True and
                  s['from_ms'] <= begin_ms < end_ms <= s['to_ms']]
    if len(candidates) != 1: raise EvidenceError('EXACT_NONOVERLAPPING_STATEMENT_COVERAGE_REQUIRED')
    s = candidates[0]
    if 'CashTransactions' not in s.get('source_sections', []):
        raise EvidenceError('CASH_SECTION_MISSING_FOR_MATCHING_STATEMENT')
    if any(number(s['components'][k]) != 0 for k in FUNDING if k != 'depositsWithdrawals'):
        raise EvidenceError('NONCASH_OR_INTERNAL_TRANSFER_DETAILS_REQUIRED')
    events = []
    for row in activity.get('cash_transactions', []):
        if row.get('source_sha256') != s['source_sha256'] or row.get('statement_key') != s.get('statement_key'): continue
        if row.get('category') != 'external_cash_flow': continue
        at = exact_transaction_time(row.get('raw_date_time'), s['timezone'])
        if not s['from_ms'] <= at < s['to_ms']: raise EvidenceError('CASH_TRANSACTION_OUTSIDE_STATEMENT')
        events.append({'id': row['transaction_id'], 'account': s['account'], 'kind': 'CASH_TRANSFER',
                       'at_ms': at, 'currency': s['currency'], 'signed_value_usd': row['amount_usd']})
    actual = sum((number(e['signed_value_usd']) for e in events), Decimal(0))
    if abs(actual - number(s['components']['depositsWithdrawals'])) > Decimal('0.01'):
        raise EvidenceError('DEPOSIT_DETAIL_DOES_NOT_MATCH_STATEMENT_CONTROL')
    return {'schema': 'heptatrader.external-flows.v1', 'account': s['account'], 'currency': s['currency'],
            'source': 'IMPORTED_IB_FLEX_ACTIVITY', 'complete': True,
            'cash_transfers_complete': True, 'position_transfers_complete': True,
            'from_ms': s['from_ms'], 'to_ms': s['to_ms'], 'source_sha256': s['source_sha256'], 'events': events}


def verify_activity_sources(index: dict, source_root: Path, account: str, at_ms: int) -> dict:
    """An edited index cannot self-certify financial coverage; reparse retained originals."""
    from hepta_pnl_report import read_private
    docs = index.get('source_documents', [])
    if (index.get('schema') != 'heptatrader.account-activity-index.v1' or index.get('account') != account or
            index.get('currency') != 'USD' or not isinstance(docs, list) or not 1 <= len(docs) <= 366):
        raise EvidenceError('ACTIVITY_RETAINED_SOURCE_MANIFEST_INVALID')
    result = None
    for doc in docs:
        sha = doc.get('sha256')
        imported = doc.get('imported_at_ms')
        if (not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha) or
                type(imported) is not int or not 0 < imported <= at_ms):
            raise EvidenceError('ACTIVITY_SOURCE_IDENTITY_INVALID')
        raw = read_private(source_root / (sha + '.xml'), 16*1024*1024)[0]
        if hashlib.sha256(raw).hexdigest() != sha:
            raise EvidenceError('ACTIVITY_RAW_DIGEST_CONFLICT')
        result = merge_activity(result, parse_flex(raw, account, 'USD', doc['timezone'], imported))
    return result
