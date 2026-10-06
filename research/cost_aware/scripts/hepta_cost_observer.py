#!/usr/bin/env python3
"""Independent bounded quote/forecast capture: reads market.get_quote ONLY.

No driver main(), pending processing, guard release or trade mutation is called.
Schedules do not depend on actual or simulated positions. Every new request is a
price/forecast observation, never an order. Installation and launch require explicit operator action.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import os
import sys
import time
import json
import signal
import re
from hepta_cost_driver import load_pinned_driver, attach
from hepta_cost_policy import CostError, quote_values, DEFAULT_POLICY
from hepta_cost_capture import Capture, private_directory, QUOTE_FIELDS


def forbidden(*_args, **_kwargs):
    raise CostError('READ_ONLY_COST_OBSERVER_MUTATION_FORBIDDEN')


class Sampler:
    """One fixed-schedule step. No position or pending command input."""
    def __init__(self, driver, cli, state_dir, writer, *, decision_ms=120000):
        self.driver, self.cli, self.state_dir, self.writer = driver, cli, state_dir, writer
        self.decision_ms = decision_ms
        self.next_slot, self.history, self.worker = None, [], None
        self.next_history_slot = None  # Model features retain the original ten-second grid.
        self.samples = self.failures = self.forecasts_started = self.slots_skipped = 0
        self.failure_reasons = {}

    def poll(self, at_ms):
        # Denser evidence sampling must not silently change model features or
        # make forecasts run more frequently. Missed history slots stay missing.
        if self.next_history_slot is None:
            self.next_history_slot = at_ms
        history_due = at_ms >= self.next_history_slot
        if history_due:
            self.next_history_slot += ((at_ms - self.next_history_slot) // 10000 + 1) * 10000
        fresh = False
        try:
            q = self.driver.native(self.cli, 'market.get_quote', 'instrument=' + self.driver.INSTRUMENT)
            _, _, mid = quote_values(q, self.driver.INSTRUMENT, self.driver.now_ms())
            self.samples += 1
            if history_due and (not self.history or self.history[-1]['observed_at_ms'] != q['observed_at_ms']):
                self.history.append({'observed_at_ms': q['observed_at_ms'], 'bid': q['bid'], 'ask': q['ask'], 'mid': float(mid)})
                self.history = self.history[-60:]
            fresh = True
        except Exception as exc:
            self.failures += 1
            message = str(exc)
            candidate = message.removeprefix('native rejected: ')
            reason = candidate if re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', candidate) else type(exc).__name__
            self.failure_reasons[reason] = self.failure_reasons.get(reason, 0) + 1
            self.writer.append('SAMPLING_FAILURE', {'failure_type': type(exc).__name__,
                              'reason_code': reason, 'quote_available': False})
        if self.worker is not None and self.worker.done():
            try:
                self.worker.result()  # The forecast wrapper already records all candidates/errors.
            except Exception:
                pass
            self.worker = None
        if self.next_slot is None:
            self.next_slot = at_ms
        if at_ms >= self.next_slot:
            slot = self.next_slot
            self.next_slot += ((at_ms - self.next_slot) // self.decision_ms + 1) * self.decision_ms
            if fresh and len(self.history) >= 6 and self.worker is None:
                # Position-neutral schema ignores this API compatibility argument.
                self.worker = self.driver.ModelWorker(str(Path.home()/'.npm-global/bin/openclaw'), self.state_dir, self.history, 0.0)
                self.forecasts_started += 1
            else:
                self.slots_skipped += 1
                self.writer.append('MODEL_SLOT_SKIPPED', {'scheduled_at_ms': slot,
                    'reason': 'BUSY_OR_MISSING_QUOTES_OR_WARMUP', 'not_a_strategy_hold': True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-driver', type=Path, required=True)
    parser.add_argument('--instrument', choices=('BTC.USD', 'EUR.USD'), required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--duration-sec', type=int, default=900)
    parser.add_argument('--sample-sec', type=int, choices=(1, 2, 5, 10), default=2)
    args = parser.parse_args()
    if os.geteuid() == 0 or not args.state_dir.is_absolute() or not 60 <= args.duration_sec <= 86400:
        parser.error('requires unprivileged owner, absolute independent state directory and bounded 60..86400 second duration')
    home = Path.home()
    forbidden_dirs = [home/'.local/state/heptatrader'/name for name in ('paper-driver','crypto-driver')]
    state_dir = args.state_dir.resolve()
    if any(state_dir == path or path in state_dir.parents for path in forbidden_dirs):
        parser.error('independent observer must not use the actual driver state directories')
    private_directory(state_dir)
    driver = load_pinned_driver(args.base_driver)
    driver.INSTRUMENT = args.instrument
    if args.instrument == 'BTC.USD':
        driver.NATIVE_ENDPOINT = [str(home/'.local/share/heptatrader/current/bin/heptactl'), '--socket',
                                 f'/run/user/{os.geteuid()}/heptatrader/tools.sock', '--token-file',
                                 str(home/'.config/heptatrader/crypto.token')]
    writer = Capture(state_dir/'cost-observations.jsonl', args.instrument)
    original_read = driver.native
    close_adapter = attach(driver, state_dir/'unused-calibration.json', capture_factory=lambda *_a, **_k: writer)
    def only_quote(cli, tool, *fields):
        if tool != 'market.get_quote':
            raise CostError('READ_ONLY_COST_OBSERVER_TOOL_FORBIDDEN')
        value = original_read(cli, tool, *fields)
        writer.append('QUOTE', {key: value.get(key) for key in QUOTE_FIELDS})
        return value
    driver.native = only_quote
    for name in ('main', 'prepare_send', 'paper_cmd', 'inspect_pending', 'recover_owner', 'canary_roundtrip'):
        setattr(driver, name, forbidden)
    sampler = Sampler(driver, str(home/'.local/bin/heptatrader'), state_dir, writer)
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def status(state):
        return {'schema': 'heptatrader.cost-observer-status.v1', 'state': state,
                'observed_at_ms': driver.now_ms(), 'instrument': args.instrument,
                'sample_sec': args.sample_sec, 'feature_sample_sec': 10, 'model_slot_sec': 120,
                'quote_samples': sampler.samples, 'quote_failures': sampler.failures,
                'quote_failure_reasons': dict(sampler.failure_reasons),
                'forecast_requests': sampler.forecasts_started,
                'skipped_forecast_slots': sampler.slots_skipped,
                'broker_order_submissions': 0, 'calibration_activated': False,
                'live_profitability_proven': False}
    driver.atomic_json(state_dir / 'status.json', status('COLLECTING'))
    end, next_sample = time.monotonic()+args.duration_sec, time.monotonic()
    try:
        while not stopped and time.monotonic() < end:
            sampler.poll(driver.now_ms())
            driver.atomic_json(state_dir / 'status.json', status('COLLECTING'))
            # Skip missed poll slots after a slow native response; no catch-up
            # burst that would consume the same stale cached quote repeatedly.
            next_sample += args.sample_sec
            clock = time.monotonic()
            if next_sample < clock:
                next_sample += (int((clock - next_sample) // args.sample_sec) + 1) * args.sample_sec
            time.sleep(max(0, min(next_sample - time.monotonic(), end - time.monotonic())))
    finally:
        # Bound waiting for an already requested forecast; do not start another.
        if sampler.worker is not None:
            sampler.worker._thread.join(timeout=155)
        close_adapter()
        writer.close()
    result = {'mode':'INDEPENDENT_READ_ONLY_COST_OBSERVATIONS', 'quote_samples':sampler.samples,
              'quote_failures':sampler.failures, 'forecast_requests':sampler.forecasts_started,
              'skipped_forecast_slots':sampler.slots_skipped, 'broker_order_submissions':0,
              'calibration_activated':False, 'live_profitability_proven':False}
    result.update(status('STOPPED' if stopped else 'WINDOW_COMPLETED'))
    driver.atomic_json(state_dir / 'status.json', result)
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (CostError, OSError, ValueError) as exc:
        print(json.dumps({'error':str(exc), 'broker_order_submissions':0}), file=sys.stderr)
        raise SystemExit(2)
