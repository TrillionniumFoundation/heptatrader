#!/usr/bin/env python3
"""Observe unique recorded training coverage and original-window baselines.

Read-only SQLite access; no training, registration, source repair or trading.
A completed job is not an optimizer continuation, an independent sample, or
proof of forward performance. Original qualification overrides stale progress.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time


class JsonSnapshots:
    def __init__(self):
        self.entries = {}
        self.read_bytes = 0

    def read(self, path):
        path = Path(path)
        before = path.stat()
        identity = (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns)
        prior = self.entries.get(path)
        if prior is not None and prior[0] == identity:
            return prior[1]
        if before.st_size > 2 * 1024 * 1024:
            raise ValueError('OBSERVATION_JSON_SIZE_BOUND')
        raw = path.read_bytes()
        after = path.stat()
        if identity != (after.st_dev, after.st_ino, after.st_size,
                        after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('OBSERVATION_CHANGED_DURING_READ')
        value = json.loads(raw)
        self.read_bytes += len(raw)
        self.entries[path] = (identity, value)
        return value


def snapshot(root, cache):
    started = time.monotonic()
    before_bytes = cache.read_bytes
    connection = sqlite3.connect('file:' + str(root / 'control/scheduler.sqlite') + '?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    try:
        rows = connection.execute(
            'SELECT id,created_ms,candidate_id,payload FROM jobs WHERE status=? ORDER BY created_ms',
            ('COMPLETED',)).fetchall()
    finally:
        connection.close()
    unique, missing, summaries = set(), [], []
    presentations = 0
    for job_id, created, candidate_id, payload in rows:
        job = json.loads(payload)
        candidate = cache.read(Path(job['output_directory']) / 'candidate.json')
        if candidate.get('candidate_id') != candidate_id:
            raise ValueError('OBSERVATION_CANDIDATE_IDENTITY')
        accounting = candidate.get('row_accounting', {})
        ids, shown = accounting.get('applied_row_ids'), accounting.get('applied_presentations')
        if isinstance(ids, list):
            unique.update(ids)
        else:
            missing.append(job_id)
        if type(shown) is int:
            presentations += shown
        summaries.append({
            'job_id': job_id, 'created_ms': created, 'candidate_id': candidate_id,
            'applied_unique_rows': len(set(ids)) if isinstance(ids, list) else None,
            'applied_presentations': shown,
            'row_statistics': candidate.get('row_statistics'),
            'eligible_pool_statistics': candidate.get('eligible_pool_statistics'),
            'calibration_model_weight': candidate.get('price_calibration', {}).get('model_weight'),
            'training_measurement': candidate.get('price_calibration_training_measurement'),
            'training_measurement_is_forward_evidence': False})
    pending, closed = [], {}
    for path in sorted((root / 'evaluation').glob('*/plan.json')):
        plan = cache.read(path)
        qualification = path.parent / 'qualification.json'
        if qualification.exists():
            final = cache.read(qualification)
            if final.get('plan_id') != plan.get('plan_id'):
                raise ValueError('OBSERVATION_QUALIFICATION_IDENTITY')
            label = final.get('state', 'UNRECORDED')
            closed[label] = closed.get(label, 0) + 1
            continue
        progress = path.parent / 'progress.json'
        value = cache.read(progress) if progress.exists() else {}
        if value and value.get('plan_id') != plan.get('plan_id'):
            raise ValueError('OBSERVATION_PROGRESS_IDENTITY')
        pending.append({'trial': plan.get('trial'), 'plan_id': plan.get('plan_id'),
            'candidate_id': plan.get('candidate_id'), 'state': value.get('state', 'UNRECORDED'),
            'coverage_diagnostics': value.get('coverage_diagnostics'),
            'same_window_price_baselines': value.get('same_window_price_baselines'),
            'same_budget_calibrated_parent': value.get('same_budget_calibrated_parent')})
    status = cache.read(root / 'control/status.json')
    return {'schema': 'heptatrader.observational-learning-progress.v1',
        'at_ms': time.time_ns() // 1000000,
        'completed_candidates': len(rows),
        'distinct_recorded_applied_price_row_ids': len(unique),
        'recorded_applied_presentations': presentations,
        'candidate_row_accounting_missing_count': len(missing),
        'candidate_row_accounting_missing': missing,
        'latest_candidates': summaries[-3:],
        'pending_forward_trials': pending, 'closed_trial_counts': closed,
        'trainer': {k: status.get(k) for k in ('state', 'job_counts', 'current_blockers', 'latest_candidate_registration')},
        'observer': {'elapsed_ms': round((time.monotonic() - started) * 1000, 3),
                     'json_content_bytes_read': cache.read_bytes - before_bytes,
                     'cached_json_files': len(cache.entries)},
        'read_only_observation': True, 'broker_calls': 0,
        'nonoverlap_is_not_independence': True,
        'rows_are_not_cumulative_model_updates': True,
        'qualification_overrides_stale_progress': True,
        'old_plans_or_thresholds_modified': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--interval', type=float, default=300)
    args = parser.parse_args()
    if not args.state_root.is_absolute() or not args.state_root.is_dir():
        parser.error('state-root must be an existing absolute directory')
    if not 1 <= args.samples <= 2000 or not 1 <= args.interval <= 3600:
        parser.error('bounded samples 1..2000 and interval 1..3600 required')
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cache = JsonSnapshots()
    previous = None
    with args.output.open('x') as output:
        for index in range(args.samples):
            try:
                value = snapshot(args.state_root, cache)
                if previous is not None:
                    value['distinct_recorded_rows_change_since_previous_sample'] = (
                        value['distinct_recorded_applied_price_row_ids'] - previous)
                previous = value['distinct_recorded_applied_price_row_ids']
            except Exception as exc:
                value = {'at_ms': time.time_ns() // 1000000, 'observation_error': str(exc),
                         'read_only_observation': True, 'broker_calls': 0}
            output.write(json.dumps(value, sort_keys=True, allow_nan=False) + '\n')
            output.flush()
            print(json.dumps({k: value.get(k) for k in ('at_ms', 'completed_candidates',
                'distinct_recorded_applied_price_row_ids', 'observer', 'observation_error')}), flush=True)
            if index + 1 < args.samples:
                time.sleep(args.interval)


if __name__ == '__main__':
    main()
