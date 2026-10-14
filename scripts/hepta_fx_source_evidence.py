#!/usr/bin/env python3
"""Offline, hash-checked TOP/TBT replay. Never connects to a Broker or mixes sources."""
from __future__ import annotations
import argparse
import bisect
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from hepta_fx_tick_by_tick_probe import TimedBidAsk

SCHEMA = 'heptatrader.fx-source-replay.v1'
MAX_BYTES = 64 * 1024 * 1024


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError('DUPLICATE_EVIDENCE_KEY')
        out[k] = v
    return out


def load_chain(path: Path, expected_sha256: str | None = None) -> list[dict]:
    data = path.read_bytes()
    if not data.endswith(b'\n') or len(data) > MAX_BYTES:
        raise ValueError('UNCOMMITTED_OR_UNBOUNDED_EVIDENCE')
    if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('RAW_DIGEST_MISMATCH')
    rows = []
    previous = '0' * 64
    for seq, line in enumerate(data.splitlines()):
        row = json.loads(line, object_pairs_hook=unique)
        digest = hashlib.sha256(canonical({k: v for k, v in row.items() if k != 'sha256'})).hexdigest()
        if (row.get('schema') != 'heptatrader.ib-direct-bidask-probe.v1' or
                row.get('seq') != seq or row.get('prev') != previous or row.get('sha256') != digest):
            raise ValueError('EVIDENCE_CHAIN_INVALID')
        rows.append(row)
        previous = digest
    if len(rows) < 2 or rows[0]['kind'] != 'HEADER' or rows[-1]['kind'] != 'END':
        raise ValueError('INCOMPLETE_CAPTURE')
    end = rows[-1]['payload']
    wire = end.get('wire_counts', {})
    if (end.get('broker_orders_sent') != 0 or end.get('account_requests_sent') != 0 or
            wire.get('mutating_codes_sent') != 0 or wire.get('active_requests') or
            set(map(int, wire.get('counts', {}))) - {71, 59, 1, 2, 97, 98}):
        raise ValueError('UNSAFE_OR_UNCLOSED_CAPTURE')
    return rows


def valid_price(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def replay_stream(rows: list[dict], source: str) -> dict:
    if source not in ('TOP', 'TBT'):
        raise ValueError('ONE_INDEPENDENT_SOURCE_REQUIRED')
    start = rows[0]['at_ms']
    end = rows[-1]['at_ms']
    if type(start) is not int or type(end) is not int or end <= start:
        raise ValueError('INVALID_WINDOW')
    events = []
    for row in rows:
        payload = row['payload']
        known = payload.get('received_ms', row['at_ms'])
        if type(known) is not int or not start <= known <= end:
            # Out-of-window callback cannot cover any part of this window.
            continue
        events.append((known, row['seq'], row['kind'], payload))
    events.sort(key=lambda item: (item[0], item[1]))
    quote = TimedBidAsk()
    bid = ask = None
    bid_at = ask_at = 0
    top_id = tbt_id = None
    live = False
    transport_ok = True
    must_resubscribe = False
    versions = []
    counts = Counter()
    first = None
    renewals = 0
    for known, _, kind, payload in events:
        relevant = False
        if kind in ('SUBSCRIBED', 'RESUBSCRIBED_READ_ONLY', 'RESUBSCRIBED_AFTER_CONTROL_2104'):
            ids = payload.get('ids', [])
            if len(ids) != 2 or any(type(x) is not int or x <= 0 for x in ids):
                raise ValueError('SUBSCRIPTION_ID_SCOPE_INVALID')
            top_id, tbt_id = ids
            bid = ask = None
            quote.last = None  # Offline-only invalidation preserves source watermark.
            must_resubscribe = False
            renewals += kind != 'SUBSCRIBED'
            relevant = True
        elif kind == 'MARKET_TYPE':
            live = payload.get('market_type') == 1
            relevant = True
        elif kind == 'CONNECTION_CLOSED':
            transport_ok = False
            relevant = True
        elif kind == 'CONTROL':
            code = payload.get('code')
            if code in (1100, 504, 509, 1300) or payload.get('fatal') is True:
                transport_ok = False
                bid = ask = None
                quote.last = None
                relevant = True
            elif code == 1101:
                transport_ok = True
                must_resubscribe = True
                bid = ask = None
                quote.last = None
                relevant = True
            elif code == 1102:
                transport_ok = True
                relevant = True
            elif code == 2103 and payload.get('cash_farm') is True:
                must_resubscribe = True
                bid = ask = None
                quote.last = None
                relevant = True
        elif source == 'TOP' and kind in ('TOP_BID', 'TOP_ASK'):
            if payload.get('request_id') != top_id or top_id is None:
                counts['foreign_request'] += 1
                continue
            counts['callbacks'] += 1
            price = payload.get('price')
            if kind == 'TOP_BID':
                bid, bid_at = (price if valid_price(price) else None), known
            else:
                ask, ask_at = (price if valid_price(price) else None), known
            relevant = True
        elif source == 'TBT' and kind == 'TBT_BIDASK':
            if payload.get('request_id') != tbt_id or tbt_id is None:
                counts['foreign_request'] += 1
                continue
            counts['callbacks'] += 1
            quote.receive(payload.get('broker_second'), payload.get('bid'), payload.get('ask'), known)
            relevant = True
        if not relevant:
            continue
        expiry = -1
        if live and transport_ok and not must_resubscribe:
            if source == 'TBT':
                current = quote.fresh(known)
                if current:
                    expiry = min(current['broker_tick_seconds'] * 1000,
                                 current['received_at_ms']) + 5000
            elif bid is not None and ask is not None and bid <= ask:
                expiry = min(bid_at, ask_at) + 5000
        if expiry >= known and first is None:
            first = known
        versions.append((known, expiry))
    grid_start = ((start + 999) // 1000) * 1000
    grid_end = (end // 1000) * 1000
    keys = [x[0] for x in versions]
    fresh = total = steady_fresh = steady_total = streak = longest = 0
    for at in range(grid_start, grid_end + 1, 1000):
        i = bisect.bisect_right(keys, at) - 1
        good = i >= 0 and versions[i][1] >= at
        total += 1
        fresh += good
        if first is not None and at >= first:
            steady_total += 1
            steady_fresh += good
            streak = 0 if good else streak + 1
            longest = max(longest, streak)
    return {
        'source': source, 'source_callbacks': counts['callbacks'],
        'ignored_foreign_requests': counts['foreign_request'],
        'first_usable_pair_at_ms': first, 'startup_wait_ms': first - start if first else None,
        'full_window_cells': total, 'full_window_fresh_cells': fresh,
        'post_first_pair_cells': steady_total, 'post_first_pair_fresh_cells': steady_fresh,
        'longest_invalid_sample_run_after_first_pair': longest,
        'grid_step_ms': 1000, 'sampled_cells_are_continuous_proof': False,
        'independent_tbt_pairs': quote.accepted if source == 'TBT' else None,
        'duplicate_tbt_callbacks': quote.duplicate_callbacks if source == 'TBT' else None,
        'rejected_tbt_callbacks': quote.rejected if source == 'TBT' else None,
        'tbt_source_regressions': quote.source_regressions if source == 'TBT' else None,
        'observed_resubscriptions': renewals,
        'source_replacement_eligible': False,
    }


def audit(path: Path, expected_sha256: str | None = None) -> dict:
    rows = load_chain(path, expected_sha256)
    return {
        'schema': SCHEMA, 'raw_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'validator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'timestamp_validator_sha256': hashlib.sha256(Path(__file__).with_name(
            'hepta_fx_tick_by_tick_probe.py').read_bytes()).hexdigest(),
        'chain_verified': True, 'capture_start_ms': rows[0]['at_ms'],
        'capture_end_ms': rows[-1]['at_ms'], 'capture_end_state': rows[-1]['payload'].get('state'),
        'config_sha256': rows[0]['payload'].get('config_sha256'),
        'stream_replay_not_exclusive_subscription_trial': True,
        'streams': {source: replay_stream(rows, source) for source in ('TOP', 'TBT')},
        'orders_sent_in_capture': 0, 'account_requests_sent_in_capture': 0,
        'strict_nav_eligible': False, 'source_replacement_eligible': False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--sha256')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    result = audit(args.events, args.sha256)
    text = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        with args.output.open('x') as f:
            f.write(text)
    print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
