#!/usr/bin/env python3
"""Read original request/command/prediction times without changing a trial.

Runs outside the frozen producer and predictor. Journal messages near a request
are temporal evidence only; an explicit request id is required for attribution.
No signing keys, model imports, orders, trial registration or trial writes.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def read(path):
    return json.loads(Path(path).read_text())


def original_outcome(folder):
    current = folder / 'current.json'
    if not current.is_file():
        return None
    pointer = read(current)
    target = Path(pointer['path'])
    if target.parent != folder or target.name == 'current.json':
        raise ValueError('OUTCOME_POINTER_OUTSIDE_COMMAND')
    record = read(target)
    if record.get('record_id') != pointer['record_id']:
        raise ValueError('OUTCOME_POINTER_IDENTITY')
    return record


def journal_near(started_ms, request_id, unit, *, runner=subprocess.run):
    """Bound the interval and returned events, retaining unambiguous attribution."""
    process = runner(['journalctl', '--user', '-u', unit, '--no-pager', '-o', 'json',
        '--since', '@' + str(max(0, started_ms // 1000 - 30)),
        '--until', '@' + str(started_ms // 1000 + 90)],
        text=True, capture_output=True, timeout=20)
    if process.returncode:
        return {'state': 'JOURNAL_UNAVAILABLE', 'events': [], 'complete': False}
    events = []
    for line in process.stdout.splitlines():
        try:
            entry = json.loads(line)
            value = json.loads(entry.get('MESSAGE', ''))
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        admission = value.get('resource_admission') or {}
        ids = value.get('request_ids', [])
        explicit = (value.get('request_id') == request_id or request_id in ids
            or request_id in admission.get('subject', {}).get('requests', []))
        if not any(key in value for key in ('state', 'stage', 'reason')):
            continue
        events.append({'journal_at_us': entry.get('__REALTIME_TIMESTAMP'),
            'invocation_id': entry.get('_SYSTEMD_INVOCATION_ID'),
            'scope': 'EXACT_REQUEST_ID' if explicit else 'NEARBY_SERVICE_EVENT_NOT_CAUSAL_PROOF',
            **{k: value[k] for k in ('at_ms', 'observed_at_ms', 'state', 'stage', 'reason',
                'request_id', 'request_ids', 'front_timing') if k in value},
            'admission_reasons': admission.get('reasons', [])})
    return {'state': 'READ_ONLY_JOURNAL', 'events': events[:256],
        'complete': len(events) <= 256, 'matched_events': len(events)}


def inspect(root, plans, requests_by_plan, *, journal=None):
    cases = []
    for plan in plans:
        folder = root / 'plans' / plan['plan_id']
        requests = requests_by_plan.get(plan['plan_id'], {})
        for path in sorted((folder / 'seen-commands').glob('*.json')):
            binding = read(path)
            rid = binding['request_id']
            if not isinstance(rid, str) or Path(rid).name != rid or rid in ('', '.', '..'):
                raise ValueError('REQUEST_PATH')
            command_id = binding['command']['command_id']
            if path.stem != command_id:
                raise ValueError('COMMAND_PATH_IDENTITY')
            outcome = original_outcome(folder / 'outcomes' / command_id)
            item = requests.get(rid)
            request = item['request'] if item else {}
            started = request.get('started_at_ms', binding.get('request_started_at_ms'))
            opened = outcome.get('opened_at_ms') if outcome else None
            prediction_path = folder / 'predictions' / rid / 'prediction.json'
            commit_path = prediction_path.with_name('commit.json')
            failure_path = prediction_path.with_name('failure.json')
            prediction = read(prediction_path) if prediction_path.is_file() else None
            commit = read(commit_path) if commit_path.is_file() else None
            failure = read(failure_path) if failure_path.is_file() else None
            computed = prediction.get('computed_at_ms') if prediction else None
            committed = (commit.get('role_witness', {}).get('signed_at_ms', commit.get('committed_at_ms'))
                if commit else None)
            case = {'plan_id': plan['plan_id'], 'request_id': rid, 'command_id': command_id,
                'producer_manifest_sha256': binding.get('producer_manifest_sha256'),
                'original_request_available_now': item is not None,
                'prediction_exists': prediction is not None, 'commit_exists': commit is not None,
                'failure': failure, 'outcome_status': outcome.get('status') if outcome else None,
                'outcome_reason': outcome.get('reason') if outcome else None,
                'request_started_at_ms': started, 'opened_at_ms': opened,
                'computed_at_ms': computed, 'committed_at_ms': committed,
                'request_source_written_at_ms': item.get('source_written_at_ms') if item else None,
                'front_timing': prediction.get('front_timing') if prediction else None,
                'evidence_paths': [str(path), str(prediction_path), str(commit_path), str(failure_path)]}
            for name, end, start in [('request_to_open_ms', opened, started),
                ('request_to_computed_ms', computed, started), ('request_to_commit_ms', committed, started),
                ('commit_after_open_ms', committed, opened), ('computed_to_commit_ms', committed, computed)]:
                case[name] = end - start if type(end) is int and type(start) is int else None
            if journal is not None and type(started) is int:
                case['journal'] = journal(started, rid)
            if prediction is None and failure is None:
                case['root_cause_status'] = 'UNRECORDED_BEFORE_PREDICTOR_NO_CAUSE_INVENTED'
            elif failure is not None:
                stale = 'REQUEST_ALREADY_OLD_NO_BACKDATING' in str(failure.get('reason', ''))
                case['root_cause_status'] = ('POSTHOC_STALE_FAILURE_NOT_CAUSAL_PROOF' if stale
                    else 'RECORDED_REQUEST_FAILURE_NOT_AUTOMATIC_CAUSAL_PROOF')
                stamp = failure.get('observed_at_ms')
                case['failure_observed_after_open_ms'] = stamp - opened if type(stamp) is int and type(opened) is int else None
            else:
                case['root_cause_status'] = 'PHASE_TIMING_AVAILABLE'
            cases.append(case)
    return {'schema': 'heptatrader.current100k-request-timing-audit.v1',
        'observed_at_ms': int(time.time() * 1000), 'read_only': True,
        'trial_bytes_modified': False, 'predictions_created': 0, 'broker_orders_sent': 0,
        'command_count': len(cases), 'outcome_reasons': dict(Counter(x['outcome_reason'] for x in cases)),
        'cases': cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--journal-unit', default='heptatrader-current100k-forward-predictor.service')
    parser.add_argument('--with-journal', action='store_true')
    args = parser.parse_args()
    raw = args.config.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.config_sha256:
        raise ValueError('CONFIG_CHANGED')
    config = json.loads(raw)
    source = Path(config['worker_source_root'])
    sys.path[:0] = [str(source / part) for part in ('research', 'learn', 'scripts')]
    from laya_cell.current100k_forward_worker import validate_runtime
    from laya_cell.financial_forward_source import ingress
    validate_runtime(config)
    root = Path(config['forward_root'])
    plans = [read(path) for path in sorted((root / 'plans').glob('*/plan.json'))]
    requests = {}; issues = {}
    for plan in plans:
        _, requests[plan['plan_id']], _, issues[plan['plan_id']] = ingress(
            plan['producer_registration'], config['period_sha256'])
    result = inspect(root, plans, requests,
        journal=(lambda stamp, rid: journal_near(stamp, rid, args.journal_unit)) if args.with_journal else None)
    result['current_ingress_issues'] = issues
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
