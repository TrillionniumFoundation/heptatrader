#!/usr/bin/env python3
"""Preregister and evaluate one fixed WATCH-only remote OOS window.

Offline only: no model invocation, broker call, refit, promotion, or driver I/O.
The local manifest/hash chain proves integrity, NOT vendor authentication. A
research PASS never grants execution authority or proves live profitability.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import time
from hepta_cost_capture import read_capture, private_directory
from hepta_cost_policy import (CostError, DEFAULT_POLICY, MODEL_ID, FORECAST_VERSION,
                               FEE_SOURCES, FEE_VERIFIED_DATE, canonical, digest,
                               dec, quote_values, strict_loads)
from hepta_cost_replay import capture_to_tape, chronological_assessment

SCHEMA = 'heptatrader.remote-oos-plan.v1'
WINDOW_KEYS = ('training_end_ms', 'decision_start_ms', 'decision_end_ms', 'outcome_end_ms')
LIMIT_KEYS = ('min_directional_candidates', 'min_completed_round_trips',
              'max_drawdown_usd', 'max_turnover_usd', 'max_reentries', 'size', 'capital')
STRESS_CASES = ('BASE', 'SLIPPAGE_2X', 'SLIPPAGE_5X', 'FEES_125PCT', 'FEES_150PCT_SLIPPAGE_5X')


def implementation_hashes():
    root = Path(__file__).resolve().parent
    names = ('hepta_cost_oos.py', 'hepta_cost_replay.py', 'hepta_cost_policy.py', 'hepta_cost_capture.py')
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}


def watch_identity(capture, cycle_id, reset_sha256):
    header = capture['events'][0]['data']
    context = header.get('observation_context') or {}
    if (capture.get('source_kind') != 'IB_CAPTURE' or capture.get('complete_prefix') is not True or
            header.get('position_neutral_forecasts_required') is not True or
            context.get('mode') != 'WATCH' or context.get('broker_mutation_authorized') is not False or
            context.get('cycle_id') != cycle_id or context.get('reset_sha256') != reset_sha256 or
            not re.fullmatch(r'obs-[a-z0-9-]{8,48}', cycle_id or '') or
            not re.fullmatch(r'[0-9a-f]{64}', reset_sha256 or '')):
        raise CostError('OOS_REAL_WATCH_SCOPE_REQUIRED')
    # Full identity is bound by hash without copying account or token data into reports.
    return digest(context)


def validate_spec(spec):
    if not isinstance(spec, dict) or set(spec) != set(WINDOW_KEYS + LIMIT_KEYS):
        raise CostError('OOS_EXPLICIT_WINDOW_AND_LIMITS_REQUIRED')
    if any(type(spec[k]) is not int or spec[k] <= 0 for k in WINDOW_KEYS):
        raise CostError('OOS_WINDOW_TYPE_INVALID')
    if not (spec['training_end_ms'] <= spec['decision_start_ms'] <
            spec['decision_end_ms'] < spec['outcome_end_ms']):
        raise CostError('OOS_WINDOW_ORDER_INVALID')
    for key in ('min_directional_candidates', 'min_completed_round_trips'):
        if type(spec[key]) is not int or spec[key] < 1:
            raise CostError('OOS_SAMPLE_BOUND_INVALID')
    if type(spec['max_reentries']) is not int or spec['max_reentries'] < 0:
        raise CostError('OOS_REENTRY_BOUND_INVALID')
    for key in ('max_drawdown_usd', 'max_turnover_usd', 'size', 'capital'):
        if dec(spec[key]) <= 0:
            raise CostError('OOS_MONETARY_BOUND_INVALID')
    if dec(spec['size']) > 25000:
        raise CostError('OOS_SIZE_EXCEEDS_REPLAY_BOUND')
    return spec


def make_plan(capture, spec, *, cycle_id, reset_sha256, now_ms=None):
    spec = validate_spec(spec)
    now = int(time.time()*1000) if now_ms is None else now_ms
    if type(now) is not int or not spec['training_end_ms'] <= now < spec['decision_start_ms']:
        raise CostError('OOS_PLAN_MUST_PRECEDE_DECISIONS_NO_BACKDATING')
    identity = watch_identity(capture, cycle_id, reset_sha256)
    end = capture['events'][-1]
    if end['at_ms'] > spec['training_end_ms']:
        raise CostError('OOS_REGISTRATION_PREFIX_AFTER_TRAINING_END')
    # At least one authoritative, causal quote must exist. Its age at future
    # admission is checked separately; registration sends no orders.
    tape = capture_to_tape(capture)
    if not any(e['event'] == 'QUOTE' for e in capture['events']):
        raise CostError('OOS_REGISTRATION_REQUIRES_WATCH_QUOTES')
    valid_quote = False
    for row in tape['rows']:
        try:
            quote_values(row['quote'], tape['instrument'], row['at_ms'])
            valid_quote = True
            break
        except CostError:
            pass
    if not valid_quote:
        raise CostError('OOS_REGISTRATION_REQUIRES_AUTHORITATIVE_QUOTE')
    body = {'schema': SCHEMA, 'registered_at_ms': now, 'spec': dict(spec),
            'cycle_id': cycle_id, 'reset_sha256': reset_sha256, 'instrument': tape['instrument'],
            'watch_context_sha256': identity,
            'registration_prefix_sha256': capture['input_sha256'],
            'registration_event_count': len(capture['events']),
            'registration_chain_head': end['sha256'],
            'registration_last_at_ms': end['at_ms'],
            'model_id': MODEL_ID, 'forecast_version': FORECAST_VERSION,
            'policy': asdict(DEFAULT_POLICY), 'implementation_sha256': implementation_hashes(),
            'fee_sources': FEE_SOURCES, 'fee_verified_date': FEE_VERIFIED_DATE,
            'stress_cases': list(STRESS_CASES),
            'local_training_required': False, 'refit_allowed': False,
            'recalibration_allowed': False, 'cutoff_tuning_allowed': False,
            'authorization_effect': 'NONE'}
    return {**body, 'manifest_sha256': digest(body)}


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('schema') != SCHEMA:
        raise CostError('OOS_PLAN_SCHEMA_INVALID')
    body = {k:v for k,v in plan.items() if k != 'manifest_sha256'}
    if digest(body) != plan.get('manifest_sha256'):
        raise CostError('OOS_PLAN_HASH_MISMATCH')
    spec = validate_spec(plan.get('spec'))
    if (type(plan.get('registered_at_ms')) is not int or
            not spec['training_end_ms'] <= plan['registered_at_ms'] < spec['decision_start_ms'] or
            type(plan.get('registration_last_at_ms')) is not int or
            plan['registration_last_at_ms'] > spec['training_end_ms']):
        raise CostError('OOS_PLAN_MUST_PRECEDE_DECISIONS_NO_BACKDATING')
    if (plan.get('implementation_sha256') != implementation_hashes() or
            plan.get('policy') != asdict(DEFAULT_POLICY) or
            plan.get('model_id') != MODEL_ID or plan.get('forecast_version') != FORECAST_VERSION or
            plan.get('stress_cases') != list(STRESS_CASES) or
            plan.get('fee_sources') != FEE_SOURCES or plan.get('fee_verified_date') != FEE_VERIFIED_DATE):
        raise CostError('OOS_FROZEN_IMPLEMENTATION_OR_POLICY_CHANGED')
    if any(plan.get(k) is not False for k in ('local_training_required','refit_allowed',
                                              'recalibration_allowed','cutoff_tuning_allowed')):
        raise CostError('OOS_REFIT_OR_LOCAL_TRAINING_FORBIDDEN')
    return plan


def evaluate_plan(plan, capture):
    """Inputs to the CLI come exclusively from read_capture, not arbitrary tapes."""
    validate_plan(plan)
    identity = watch_identity(capture, plan['cycle_id'], plan['reset_sha256'])
    n = plan.get('registration_event_count')
    if (identity != plan['watch_context_sha256'] or type(n) is not int or n < 1 or
            n > len(capture['events']) or capture['events'][n-1]['sha256'] != plan['registration_chain_head']):
        raise CostError('OOS_REGISTERED_PREFIX_OR_WATCH_IDENTITY_CHANGED')
    tape = capture_to_tape(capture)
    if tape['instrument'] != plan['instrument']:
        raise CostError('OOS_INSTRUMENT_CHANGED')
    spec = plan['spec']
    common = {'oos_plan_sha256': plan['manifest_sha256'],
              'registered_at_ms': plan['registered_at_ms'],
              'capture_sha256': capture['input_sha256'],
              'verified_capture_event_count': len(capture['events']),
              'source_integrity_scope': 'LOCAL_WATCH_HASH_CHAIN_NOT_VENDOR_ATTESTATION',
              'local_training_required_for_acceptance': False,
              'authorization_effect': 'NONE', 'broker_orders_sent': 0,
              'trading_driver_switched': False, 'live_profitability_proven': False}
    if tape['rows'][-1]['at_ms'] < spec['decision_start_ms']:
        return {'schema':'heptatrader.remote-model-oos.v1', **common, **{k:spec[k] for k in WINDOW_KEYS},
                'status':'PENDING', 'strategy_accepted':False, 'research_acceptance_passed':False,
                'reasons':['OOS_WINDOW_NOT_YET_OBSERVED']}
    report = chronological_assessment(tape, spec['decision_start_ms'], spec['decision_end_ms'],
        **{k:v for k,v in spec.items() if k not in ('decision_start_ms','decision_end_ms')})
    if set(report['stress']) != set(STRESS_CASES):
        raise CostError('OOS_STRESS_CASES_CHANGED')
    # Evidence may continue after outcome_end. Never use a future quote to mark
    # an earlier endpoint, and never extend a plan to rescue an open position.
    rows = [r for r in tape['rows'] if r['at_ms'] <= spec['outcome_end_ms']]
    try:
        quote_values(rows[-1]['quote'], tape['instrument'], spec['outcome_end_ms'])
    except (CostError, IndexError):
        report['reasons'].append('OOS_OUTCOME_END_QUOTE_UNOBSERVABLE')
    report['strategy_accepted'] = report['research_acceptance_passed'] = not report['reasons']
    report.update(common)
    report['status'] = ('PENDING' if tape['rows'][-1]['at_ms'] < spec['outcome_end_ms']
                        else 'PASS' if report['strategy_accepted'] else 'FAIL')
    return report


def write_evidence(path, value):
    path = Path(path)
    private_directory(path.parent)
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
    try:
        raw = (json.dumps(value, indent=2, allow_nan=False)+'\n').encode()
        offset = 0
        while offset < len(raw):
            n = os.write(fd, raw[offset:])
            if n <= 0:
                raise OSError('OOS_EVIDENCE_SHORT_WRITE')
            offset += n
        os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    register = sub.add_parser('register')
    register.add_argument('--capture', type=Path, required=True)
    register.add_argument('--spec', type=Path, required=True)
    register.add_argument('--expected-cycle-id', required=True)
    register.add_argument('--expected-reset-sha256', required=True)
    register.add_argument('--output', type=Path, required=True)
    evaluate = sub.add_parser('evaluate')
    evaluate.add_argument('--capture', type=Path, required=True)
    evaluate.add_argument('--plan', type=Path, required=True)
    evaluate.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    capture = read_capture(args.capture)
    if args.command == 'register':
        if args.spec.stat().st_size > 65536:
            raise CostError('OOS_SPEC_TOO_LARGE')
        result = make_plan(capture, strict_loads(args.spec.read_text()),
                           cycle_id=args.expected_cycle_id, reset_sha256=args.expected_reset_sha256)
    else:
        if args.plan.stat().st_size > 65536:
            raise CostError('OOS_PLAN_TOO_LARGE')
        result = evaluate_plan(strict_loads(args.plan.read_text()), capture)
    write_evidence(args.output, result)
    print(json.dumps({'output':str(args.output), 'status':result.get('status','REGISTERED'),
                      'strategy_accepted':result.get('strategy_accepted',False),
                      'broker_orders_sent':0,'trading_driver_switched':False}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (CostError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error':str(exc),'broker_orders_sent':0}))
        raise SystemExit(2)
