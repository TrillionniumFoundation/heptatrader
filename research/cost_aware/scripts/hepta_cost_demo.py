#!/usr/bin/env python3
"""Reproducible SYNTHETIC functional demo. No historical-performance claim.

Prices, forecasts and calibration labels are deliberately constructed fixtures.
The synthetic calibration cannot pass the resident adapter's admission checks.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
from hepta_cost_policy import (D, BPS, FORECAST_VERSION, MODEL_ID, build_calibration, validate_forecast)
from hepta_cost_replay import stress_compare

NOW = 1791260000000


def quote(at, instrument, mid):
    half = mid * D('.00001')
    return {'source': 'IB', 'authoritative': True, 'instrument': instrument,
            'subscription_id': 'IB:SYNTHETIC:ONLY', 'subscription_state': 'active',
            'observed_at_ms': at, 'stale_after_ms': at + 5000, 'stale': False,
            'bid': str(mid - half), 'ask': str(mid + half)}


def forecast(at, instrument, action, move, mid):
    value = D(move)
    row = {'action': action, 'confidence': .8, 'rationale': 'SYNTHETIC FUNCTIONAL DEMO, NOT AN ACTUAL MODEL OUTPUT',
           'horizon_sec': 300, 'scenarios': [{'probability': '.5', 'move_bps': str(value - 2)},
                                           {'probability': '.5', 'move_bps': str(value + 2)}],
           'invalidation_price': str(mid * (D('.999') if action == 'BUY' else D('1.001'))) if action != 'HOLD' else '0',
           'forecast_version': FORECAST_VERSION, 'model_id': MODEL_ID, 'issued_at_ms': at,
           'reference_mid': str(mid), 'instrument': instrument, 'position_neutral': True}
    validate_forecast(row)
    return row


def inputs(instrument):
    anchor = D('85500') if instrument == 'BTC.USD' else D('1.12')
    candidates = {0: ('BUY', '20'), 20: ('SELL', '-20'), 40: ('BUY', '80'), 60: ('SELL', '-20'),
                  80: ('BUY', '80'), 100: ('SELL', '-80'), 120: ('BUY', '80'), 140: ('HOLD', '0'),
                  240: ('BUY', '80'), 260: ('SELL', '-20'), 360: ('SELL', '-80'), 500: ('BUY', '80')}
    rows = []
    for seconds in range(0, 901, 10):
        at = NOW + seconds * 1000
        mid = anchor * (1 + D(seconds) / D('10000000'))
        f = forecast(at, instrument, *candidates[seconds], mid) if seconds in candidates else None
        rows.append({'at_ms': at, 'quote': quote(at, instrument, mid), 'forecast': f})
    tape = {'schema': 'heptatrader.cost-tape.v1', 'instrument': instrument, 'source_kind': 'SYNTHETIC', 'rows': rows}
    samples = []
    for side in ('BUY', 'SELL'):
        for i in range(40):
            at = NOW - 4 * 86400000 + i * 2 * 3600000
            move = D(20 if i % 2 else 100) * (1 if side == 'BUY' else -1)
            f = forecast(at, instrument, side, move, anchor)
            end = at + f['horizon_sec'] * 1000
            samples.append({'forecast': f, 'forecast_available_at_ms': at + 1,
                            'label_observed_at_ms': end, 'label_available_at_ms': end + 1,
                            'source_kind': 'SYNTHETIC', 'realized_underlying_move_bps': str(move - (5 if side == 'BUY' else -5))})
    return tape, build_calibration(samples, NOW - 1000)


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    summary = {'schema': 'heptatrader.synthetic-cost-demo.v1', 'source_kind': 'SYNTHETIC',
               'real_broker_orders_sent': 0, 'live_or_paper_strategy_performance_proven': False,
               'calibration_synthetic_only': True, 'tests_of_real_model_outputs': 0,
               'hypothetical_capital_usd': '100000', 'rows_per_asset': 91,
               'candidate_count_per_asset': 12, 'stress_cases_per_asset': 5, 'paths_per_stress_case': 3,
               'assets': {}}
    for instrument, name in (('BTC.USD', 'btc'), ('EUR.USD', 'fx')):
        tape, calibration = inputs(instrument)
        report = stress_compare(tape, [calibration])
        write_new(args.output_dir / (name + '-synthetic-tape.json'), tape)
        write_new(args.output_dir / (name + '-synthetic-calibration.json'), calibration)
        write_new(args.output_dir / (name + '-synthetic-stress-report.json'), report)
        metrics = ('strategy', 'complete_observed_path', 'net_liquidation_pnl_usd', 'commission_paid_usd',
                   'holding_haircut_usd', 'open_events', 'completed_round_trips', 'turnover_usd',
                   'max_drawdown_usd', 'max_exposure_usd', 'time_exposed_fraction', 'average_holding_ms_closed',
                   'remaining_simulated_quantity', 'rejection_or_hold_reasons')
        summary['assets'][instrument] = {
            'outcomes': dict(Counter(row['reason'] for row in report['BASE']['candidate_outcomes'])),
            'stress_cases': {case: [{key: value for key, value in path.items() if key in metrics}
                                    for path in run['results']] for case, run in report.items()}}
    write_new(args.output_dir / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
