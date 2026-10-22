"""Run a frozen, futile trial's original evaluator without reopening GPU work.

The original config and source files are verified, never rewritten. A restricted
scan view exposes one existing plan; the old evaluator keeps its original
outcomes, rule, alpha identity and deadline. This is not an early assessment.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def _read(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('RETAINED_CLOSEOUT_JSON_SIZE')
    return json.loads(raw)


def _sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _original(entry, current, plan_path):
    if (type(entry) is not dict or set(entry) != {'plan_id', 'config_path', 'config_sha256'}
            or type(entry['config_path']) is not str or not Path(entry['config_path']).is_absolute()
            or _sha(entry['config_path']) != entry['config_sha256']):
        raise ValueError('RETAINED_CLOSEOUT_CONFIG_BINDING')
    old = _read(entry['config_path'])
    plan = _read(plan_path)
    if (entry['plan_id'] != plan.get('plan_id')
            or old.get('automatic_trading') is not False
            or old.get('period_sha256') != current['period_sha256']
            or old.get('evaluation_directory') != current['evaluation_directory']
            or Path(plan_path).parent.parent != Path(old['evaluation_directory'])
            or not Path(old['python']).is_absolute()
            or not Path(old['runtime_directory']).is_absolute()):
        raise ValueError('RETAINED_CLOSEOUT_SCOPE')
    return old, plan


def run_retained(config, path, plan, cache, now_ms):
    entries = config.get('retained_futile_plan_closeouts', [])
    if (type(entries) is not list or len(entries) > 32
            or any(type(e) is not dict or type(e.get('plan_id')) is not str for e in entries)
            or len({e['plan_id'] for e in entries}) != len(entries)):
        raise ValueError('RETAINED_CLOSEOUT_ROUTES')
    matched = [e for e in entries if e['plan_id'] == plan['plan_id']]
    if not matched:
        return None
    entry = matched[0]
    old, _ = _original(entry, config, path)
    previous = cache.get(plan['plan_id'])
    if previous and 0 <= now_ms - previous[0] < 30000:
        return previous[1]
    state = Path(config['evaluation_directory']) / '.retained-futile' / plan['plan_id']
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               CUDA_VISIBLE_DEVICES='', HIP_VISIBLE_DEVICES='',
               PYTHONPATH=os.pathsep.join(str(Path(old['runtime_directory']) / name)
                                         for name in ('learn', 'scripts')))
    completed = subprocess.run([old['python'], '-B', str(Path(__file__).resolve()),
        '--config', entry['config_path'], '--config-sha256', entry['config_sha256'],
        '--plan', str(path), '--state', str(state)], env=env,
        capture_output=True, text=True, timeout=15)
    if completed.returncode:
        raise ValueError('RETAINED_CLOSEOUT_FAILED:' + completed.stderr[-400:])
    result = _read(state / 'status.json')
    if (result.get('plans') != 1 or result.get('active_prediction_workers') != 0
            or result.get('broker_orders_sent') != 0
            or sum(result.get('counts', {}).values()) != 1):
        raise ValueError('RETAINED_CLOSEOUT_STATUS')
    cache[plan['plan_id']] = (now_ms, result)
    return result


class _SinglePlanView:
    """Limit old evaluator discovery; original plan/output paths stay intact."""
    def __init__(self, plan, state):
        self.plan, self.state = plan, state

    def glob(self, pattern):
        if pattern != '*/plan.json':
            raise ValueError('RETAINED_CLOSEOUT_UNEXPECTED_SCAN')
        return [self.plan]

    def __truediv__(self, name):
        if name != 'status.json':
            raise ValueError('RETAINED_CLOSEOUT_UNEXPECTED_ROOT_WRITE')
        return self.state / name


def close_original(config_path, config_sha256, plan_path, state):
    # Keep imports below verification: imported package code must be original.
    if _sha(config_path) != config_sha256:
        raise ValueError('RETAINED_CLOSEOUT_CONFIG_CHANGED')
    old = _read(config_path)
    plan = _read(plan_path)
    entry = {'config_path': str(config_path), 'config_sha256': config_sha256,
             'plan_id': plan['plan_id']}
    _original(entry, old, plan_path)
    runtime = Path(old['runtime_directory'])
    pins = old.get('runtime_sha256')
    if not isinstance(pins, dict) or not pins:
        raise ValueError('RETAINED_CLOSEOUT_RUNTIME_PINS_REQUIRED')
    for name, expected in pins.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or _sha(runtime / name) != expected:
            raise ValueError('RETAINED_CLOSEOUT_RUNTIME_CHANGED:' + name)
    if (not plan.get('evaluation_runtime_sha256')
            or any(pins.get(name) != expected for name, expected
                   in plan['evaluation_runtime_sha256'].items())):
        raise ValueError('RETAINED_CLOSEOUT_PLAN_RUNTIME_CHANGED')
    sys.path[:0] = [str(runtime / 'learn'), str(runtime / 'scripts')]
    from laya_cell.resident_evaluate import Evaluator, suppress_new_work_for_frozen_failure
    if Path(sys.modules[Evaluator.__module__].__file__).resolve() != (runtime / 'learn/laya_cell/resident_evaluate.py').resolve():
        raise ValueError('RETAINED_CLOSEOUT_IMPORT_ORIGIN')
    records = [_read(p) for p in sorted(plan_path.parent.glob('outcome-*.json'))]
    if not suppress_new_work_for_frozen_failure(plan, records):
        raise ValueError('RETAINED_CLOSEOUT_REQUIRES_IRREVERSIBLE_FUTILITY')
    for prediction in plan_path.parent.glob('prediction-*.json'):
        if not (prediction.parent / prediction.name.replace('prediction-', 'outcome-', 1)).exists():
            raise ValueError('RETAINED_CLOSEOUT_UNSCORED_PREDICTION')

    class OriginalCloseout(Evaluator):
        def launch(self, path, slot):
            raise ValueError('RETAINED_CLOSEOUT_CANNOT_LAUNCH_PREDICTIONS')

    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    service = OriginalCloseout(config_path)
    service.root = _SinglePlanView(plan_path, state)
    return service.step()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    close_original(args.config, args.config_sha256, args.plan, args.state)


if __name__ == '__main__':
    main()
