#!/usr/bin/env python3
"""Verify actual round-trip relationships, not self-reported rollout booleans.

v2 binds one externally admitted campaign identity. It cross-checks journaled
intent/send/reconciliation, broker execution IDs and two complete flat barriers
for EVERY cycle. A digest proves bytes; these checks prove their consistency.
They do not authenticate a malicious host/harness and never grant authority.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import math
from pathlib import Path
import re
import sys
from typing import Any

from hepta_evidence_io import (EvidenceError, canonical_bytes, load_json as _load_json,
                              loads, read_bytes, read_relative, controller_digest, sha256_file as _sha256_file, write_json)

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'docs/ib-paper-rollout-policy-v1.json'
SHA40 = re.compile(r'^[0-9a-f]{40}$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$')
VerificationError = EvidenceError
BINDING_KEYS = {'campaign_id', 'candidate_sha', 'binary_sha256', 'artifact_sha256',
                'harness_sha256', 'driver_sha256', 'controller_sha256', 'profile_sha256', 'account_fingerprint',
                'host_fingerprint', 'instrument', 'quote_currency', 'base_currency'}
EVIDENCE_KEYS = {'kind', 'path', 'sha256', 'size'}
REQUIRED_EVIDENCE_KINDS = {'authoritative-snapshot', 'broker-callbacks', 'oms-journal'}
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
RESULT_KEYS = {'schema', 'binding', 'stage', 'account_mode', 'profile_order_mode',
               'mutation_cycles', 'successful_round_trips', 'final_active_orders',
               'final_uncertain_commands', 'final_position_quantity',
               'authoritative_reconciliation_complete', 'live_authorized', 'evidence'}


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise VerificationError(reason)


def exact(value: Any, keys: set[str], label: str) -> dict:
    require(isinstance(value, dict) and set(value) == keys, f'{label}: non-canonical fields')
    return value


def number(value: Any, label: str) -> Decimal:
    require(not isinstance(value, bool) and isinstance(value, (int, float)), f'{label}: number required')
    require(math.isfinite(value), f'{label}: finite number required')
    return Decimal(str(value))


def integer(value: Any, label: str, minimum: int = 1) -> int:
    require(type(value) is int and value >= minimum, f'{label}: integer >= {minimum} required')
    return value


def identifier(value: Any, label: str) -> str:
    require(isinstance(value, str) and ID.fullmatch(value) is not None, f'{label}: invalid identity')
    return value


def validate_binding(binding: Any) -> dict:
    exact(binding, BINDING_KEYS, 'campaign binding')
    require(isinstance(binding['candidate_sha'], str) and SHA40.fullmatch(binding['candidate_sha']) is not None,
            'invalid candidate SHA')
    for key in ('binary_sha256', 'artifact_sha256', 'harness_sha256', 'driver_sha256', 'controller_sha256', 'profile_sha256',
                'account_fingerprint', 'host_fingerprint'):
        require(isinstance(binding[key], str) and SHA256.fullmatch(binding[key]) is not None, f'invalid {key}')
    identifier(binding['campaign_id'], 'campaign_id')
    identifier(binding['instrument'], 'instrument')
    # P1 is still the reviewed single CASH/USD envelope, not multi-asset authority.
    require(binding['quote_currency'] == binding['base_currency'] == 'USD',
            'P1 evidence currently requires USD quoted/base currency')
    return binding


def load_campaign(path: Path) -> dict:
    campaign = _load_json(path)
    exact(campaign, {'schema', 'binding', 'created_at_ms', 'account_mode'}, 'campaign')
    require(campaign['schema'] == 'heptatrader.paper-campaign.v1', 'unsupported campaign schema')
    require(campaign['account_mode'] == 'PAPER', 'campaign must remain PAPER-only')
    integer(campaign['created_at_ms'], 'campaign timestamp')
    validate_binding(campaign['binding'])
    require(campaign['binding']['controller_sha256'] == controller_digest(), 'portable controller code/policy changed')
    return campaign


def _stage_policy(policy: dict, stage: str) -> dict:
    require(policy.get('schema') == 'heptatrader.ib-paper-rollout-policy.v1', 'unsupported rollout policy schema')
    require(policy.get('execution_mode') == 'PAPER' and policy.get('live_authorized') is False,
            'rollout policy must remain PAPER-only')
    require(policy.get('profile_order_mode') == 'EXTERNAL_P1_CANARY_LMT_DAY', 'rollout must use PAPER-V4')
    stages = policy.get('stages')
    require(isinstance(stages, list), 'rollout stages must be an array')
    matches = [item for item in stages if isinstance(item, dict) and item.get('id') == stage]
    require(len(matches) == 1, f'unknown or duplicate rollout stage: {stage}')
    result = matches[0]
    required = {'canary': 1, 'pilot': 3, 'extended': 10}[stage]
    require(result.get('min_mutation_cycles') == result.get('max_mutation_cycles') == required,
            'stage minimum and maximum must match the reviewed 1/3/10 cycle counts')
    for name, bound in [('max_order_quantity', 1), ('max_order_notional', 5000),
                        ('max_active_orders', 1), ('max_gross_position', 1)]:
        require(number(result.get(name), name) == bound, f'{name}: policy may not widen P1')
    require(result.get('require_flat_between_cycles') is True, 'flatness between cycles is required')
    return result


def _verify_evidence(root: Path, entries: Any) -> tuple[list[dict], dict[str, bytes]]:
    require(isinstance(entries, list) and len(entries) == 3, 'exactly three evidence kinds are required')
    data = {}
    paths = set()
    normalized = []
    for item in entries:
        exact(item, EVIDENCE_KEYS, 'evidence entry')
        kind = item['kind']
        require(isinstance(kind, str) and kind in REQUIRED_EVIDENCE_KINDS and kind not in data, 'missing or duplicate evidence kind')
        require(isinstance(item['path'], str) and item['path'] not in paths, 'duplicate evidence path')
        require(type(item['size']) is int and 0 < item['size'] <= MAX_EVIDENCE_BYTES, 'invalid evidence size')
        payload = read_relative(root, item['path'], MAX_EVIDENCE_BYTES)
        require(len(payload) == item['size'], 'evidence size mismatch')
        require(hashlib.sha256(payload).hexdigest() == item['sha256'], 'evidence digest mismatch')
        paths.add(item['path'])
        data[kind] = payload
        normalized.append(item)
    require(set(data) == REQUIRED_EVIDENCE_KINDS, 'missing evidence kinds')
    return normalized, data


def _records(payload: bytes, schema: str, binding: dict) -> list[dict]:
    lines = payload.splitlines()
    require(0 < len(lines) <= 10000, 'invalid evidence record count')
    result = []
    for line in lines:
        record = loads(line)
        require(isinstance(record, dict) and record.get('schema') == schema, 'invalid evidence record schema')
        require(record.get('binding') == binding, 'record campaign/account/profile/host mismatch')
        result.append(record)
    return result


def _barrier(value: Any, binding: dict, created: int) -> tuple[int, int, int]:
    exact(value, {'observed_at_ms', 'connection_epoch', 'generation', 'complete',
                  'positions', 'active_order_ids', 'uncertain_command_ids'}, 'barrier')
    time = integer(value['observed_at_ms'], 'barrier timestamp')
    epoch = integer(value['connection_epoch'], 'barrier epoch')
    generation = integer(value['generation'], 'barrier generation')
    require(time >= created, 'barrier predates this campaign')
    require(value['complete'] is True, 'incomplete authoritative barrier')
    require(value['active_order_ids'] == [] and value['uncertain_command_ids'] == [],
            'barrier retains active or uncertain commands')
    require(value['positions'] == [{'instrument': binding['instrument'], 'quantity': 0}],
            'barrier is not completely flat for the bound instrument')
    require(type(value['positions'][0]['quantity']) in (int, float), 'invalid position quantity')
    return time, epoch, generation


def verify(result_path: Path, evidence_root: Path, expected_git_sha: str,
           expected_binary: Path, expected_harness: Path, expected_stage: str,
           policy_path: Path = POLICY, campaign_path: Path | None = None) -> dict:
    require(campaign_path is not None, 'independently admitted --campaign is required')
    campaign = load_campaign(campaign_path)
    binding = campaign['binding']
    require(binding['candidate_sha'] == expected_git_sha, 'candidate SHA mismatch')
    require(binding['binary_sha256'] == _sha256_file(expected_binary), 'binary digest mismatch')
    require(binding['harness_sha256'] == _sha256_file(expected_harness), 'harness digest mismatch')
    result_bytes = read_bytes(result_path)
    result = exact(loads(result_bytes), RESULT_KEYS, 'rollout result')
    require(result['schema'] == 'heptatrader.ib-paper-rollout-result.v2', 'unsupported rollout result schema')
    require(result['binding'] == binding, 'result campaign/account/profile/host mismatch')
    require(result['stage'] == expected_stage, 'rollout stage mismatch')
    require(result['account_mode'] == 'PAPER' and result['live_authorized'] is False, 'rollout must remain PAPER-only')
    require(result['profile_order_mode'] == 'EXTERNAL_P1_CANARY_LMT_DAY', 'profile mismatch')
    policy = _stage_policy(_load_json(policy_path), expected_stage)
    entries, payloads = _verify_evidence(evidence_root, result['evidence'])
    snapshots = exact(loads(payloads['authoritative-snapshot']), {'schema', 'binding', 'cycles'}, 'snapshots')
    require(snapshots['schema'] == 'heptatrader.rollout-snapshots.v1' and snapshots['binding'] == binding,
            'snapshot schema or campaign binding mismatch')
    cycles = snapshots['cycles']
    require(isinstance(cycles, list) and len(cycles) == policy['min_mutation_cycles'],
            'insufficient or excessive independently flat cycles')
    journal = _records(payloads['oms-journal'], 'heptatrader.rollout-journal.v1', binding)
    callbacks = _records(payloads['broker-callbacks'], 'heptatrader.rollout-callback.v1', binding)
    journal_by_cycle = defaultdict(list)
    callbacks_by_cycle = defaultdict(list)
    previous_seq = 0
    previous_journal_time = 0
    for row in journal:
        exact(row, {'schema', 'binding', 'cycle_id', 'command_id', 'event', 'sequence',
                    'observed_at_ms', 'order_id', 'side', 'quantity', 'limit_price', 'order_type', 'tif'}, 'journal')
        seq = integer(row['sequence'], 'journal sequence')
        require(seq > previous_seq, 'journal sequence is not strictly increasing')
        previous_seq = seq
        journal_time = integer(row['observed_at_ms'], 'journal timestamp')
        require(journal_time >= previous_journal_time, 'journal timestamps go backwards')
        previous_journal_time = journal_time
        identifier(row['cycle_id'], 'journal cycle')
        identifier(row['command_id'], 'command identity')
        journal_by_cycle[row['cycle_id']].append(row)
    for row in callbacks:
        exact(row, {'schema', 'binding', 'cycle_id', 'command_id', 'order_id', 'event',
                    'execution_id', 'side', 'quantity', 'price', 'status', 'observed_at_ms',
                    'connection_epoch'}, 'callback')
        identifier(row['cycle_id'], 'callback cycle')
        callbacks_by_cycle[row['cycle_id']].append(row)
    seen_cycles, seen_commands, seen_orders, executions = set(), set(), set(), {}
    previous_end = 0
    for cycle in cycles:
        exact(cycle, {'cycle_id', 'before', 'after'}, 'cycle')
        cid = identifier(cycle['cycle_id'], 'cycle')
        require(cid not in seen_cycles, 'duplicate cycle')
        seen_cycles.add(cid)
        start, epoch, generation = _barrier(cycle['before'], binding, campaign['created_at_ms'])
        end, end_epoch, end_generation = _barrier(cycle['after'], binding, campaign['created_at_ms'])
        require(start >= previous_end and end > start and end_epoch == epoch and end_generation > generation,
                'overlapping cycles or invalid refresh barriers')
        previous_end = end
        commands = defaultdict(list)
        for row in journal_by_cycle[cid]:
            commands[row['command_id']].append(row)
        # The portable P1 harness issues one opening BUY and one exact exit.
        # Counting barriers alone would let multiple round trips hide inside a
        # single declared cycle while evading its operation budget.
        require(len(commands) == 2, 'cycle must contain exactly two mutation legs')
        net = Decimal(0)
        total_fills = 0
        last_terminal = start
        for leg, (command, rows) in enumerate(commands.items()):
            require(command not in seen_commands, 'command reused across cycles')
            seen_commands.add(command)
            require([r['event'] for r in rows] == ['intent', 'send_attempt', 'reconciled'],
                    'command lacks ordered durable intent/send/reconciliation')
            intent, sent, done = rows
            order = identifier(sent['order_id'], 'order identity')
            require(order not in seen_orders, 'order identity reused')
            seen_orders.add(order)
            shape = ('order_id', 'side', 'quantity', 'limit_price', 'order_type', 'tif')
            require(all(tuple(r[k] for k in shape) == tuple(sent[k] for k in shape) for r in rows),
                    'command normalized intent changed')
            quantity = number(sent['quantity'], 'order quantity')
            price = number(sent['limit_price'], 'limit price')
            require(sent['side'] in ('BUY', 'SELL') and sent['order_type'] == 'LMT' and sent['tif'] == 'DAY',
                    'unsupported P1 order')
            require(0 < quantity <= 1 and price > 0 and quantity * price <= 5000,
                    'order exceeds P1 quantity/notional envelope')
            if leg == 0:
                require(sent['side'] == 'BUY', 'P1 cycle requires an opening BUY')
            else:
                require(sent['side'] == 'SELL' and net > 0 and quantity == net,
                        'exit must SELL the exact observed opening position')
            times = [integer(r['observed_at_ms'], 'journal timestamp') for r in rows]
            require(last_terminal <= times[0] <= times[1] <= times[2] <= end,
                    'journal timing/one-active-order violation')
            observed = [r for r in callbacks_by_cycle[cid] if r['command_id'] == command]
            require(bool(observed), 'send has no broker evidence')
            fills = Decimal(0)
            terminal = None
            last_callback = times[1]
            for row in observed:
                require(row['order_id'] == order and row['side'] == sent['side'], 'broker/order correlation mismatch')
                require(type(row['connection_epoch']) is int and row['connection_epoch'] == epoch, 'callback epoch mismatch')
                timestamp = integer(row['observed_at_ms'], 'callback timestamp')
                require(times[1] <= timestamp <= times[2], 'callback outside durable command interval')
                if row['event'] == 'fill':
                    eid = identifier(row['execution_id'], 'execution id')
                    if eid in executions:
                        require(executions[eid] == row, 'conflicting duplicate execution')
                        continue
                    require(terminal is None and timestamp >= last_callback, 'fill after terminal or out of order')
                    executions[eid] = row
                    amount = number(row['quantity'], 'fill quantity')
                    fill_price = number(row['price'], 'fill price')
                    require(amount > 0 and fill_price > 0 and row['status'] == 'execution', 'invalid economic fill')
                    require((sent['side'] == 'BUY' and fill_price <= price) or
                            (sent['side'] == 'SELL' and fill_price >= price), 'execution violates limit price')
                    require(amount * fill_price <= 5000, 'fill exceeds notional envelope')
                    fills += amount
                    require(fills <= quantity, 'overfill')
                    net += amount if row['side'] == 'BUY' else -amount
                    require(abs(net) <= 1, 'intermediate gross position exceeds P1')
                    total_fills += 1
                elif row['event'] == 'terminal':
                    require(terminal is None and timestamp >= last_callback, 'duplicate or out-of-order terminal')
                    require(row['execution_id'] == '' and row['status'] in ('Filled', 'Cancelled', 'Rejected'),
                            'invalid terminal evidence')
                    require(number(row['quantity'], 'terminal filled quantity') == fills, 'terminal/fill quantity mismatch')
                    require(number(row['price'], 'terminal price') == 0, 'terminal price must be zero')
                    if row['status'] == 'Filled':
                        require(fills == quantity, 'Filled text lacks complete execution proof')
                    if row['status'] == 'Rejected':
                        require(fills == 0, 'rejected order has economic fills')
                    terminal = row
                else:
                    raise VerificationError('unsupported callback event')
                last_callback = timestamp
            require(terminal is not None, 'order has no authoritative terminal')
            last_terminal = times[2]
        require(net == 0 and total_fills >= 2, 'cycle did not economically round-trip to flat')
        require({r['command_id'] for r in callbacks_by_cycle[cid]} == set(commands), 'unexplained broker command')
    require(set(journal_by_cycle) == seen_cycles and set(callbacks_by_cycle) == seen_cycles,
            'unexplained evidence outside the declared cycles')
    count = len(cycles)
    require(type(result['mutation_cycles']) is int and result['mutation_cycles'] == count and
            type(result['successful_round_trips']) is int and result['successful_round_trips'] == count,
            'reported cycle count differs from economic evidence')
    require(type(result['final_active_orders']) is int and result['final_active_orders'] == 0 and
            type(result['final_uncertain_commands']) is int and result['final_uncertain_commands'] == 0,
            'result retained active or uncertain commands')
    require(number(result['final_position_quantity'], 'final position') == 0 and
            result['authoritative_reconciliation_complete'] is True, 'result is not reconciled flat')
    return {'schema': 'heptatrader.ib-paper-rollout-verification.v2', 'binding': binding,
            'stage': expected_stage, 'start_at_ms': cycles[0]['before']['observed_at_ms'],
            'end_at_ms': cycles[-1]['after']['observed_at_ms'], 'mutation_cycles': count, 'successful_round_trips': count,
            'final_active_orders': 0, 'final_uncertain_commands': 0, 'final_position_quantity': 0,
            'authoritative_reconciliation_complete': True, 'evidence': entries,
            'result_sha256': hashlib.sha256(result_bytes).hexdigest(),
            'authorization_effect': 'NONE', 'paper_authorized': False, 'live_authorized': False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    for name in ('result', 'evidence-root', 'expected-binary', 'expected-harness', 'campaign', 'receipt'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--expected-git-sha', required=True)
    parser.add_argument('--expected-stage', choices=('canary', 'pilot', 'extended'), required=True)
    parser.add_argument('--policy', type=Path, default=POLICY)
    args = parser.parse_args(argv)
    try:
        receipt = verify(args.result, args.evidence_root, args.expected_git_sha, args.expected_binary,
                         args.expected_harness, args.expected_stage, args.policy, args.campaign)
        write_json(args.receipt, receipt)
    except (OSError, ValueError, OverflowError) as error:
        print(f'[IB-PAPER-ROLLOUT] FAIL: {error}', file=sys.stderr)
        return 1
    print(f'[IB-PAPER-ROLLOUT] PASS stage={args.expected_stage}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
