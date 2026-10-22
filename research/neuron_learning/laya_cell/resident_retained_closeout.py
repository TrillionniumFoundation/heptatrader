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


def run_retained(config, path, plan, cache, now_ms, *, operator_stop=None):
    entries = config.get('retained_futile_plan_closeouts', [])
    if (type(entries) is not list or len(entries) > 32
            or any(type(e) is not dict or type(e.get('plan_id')) is not str for e in entries)
            or len({e['plan_id'] for e in entries}) != len(entries)):
        raise ValueError('RETAINED_CLOSEOUT_ROUTES')
    matched = [e for e in entries if e['plan_id'] == plan['plan_id']]
    if not matched:
        if operator_stop is not None:
            raise ValueError('OPERATOR_STOP_ORIGINAL_ROUTE_REQUIRED')
        return None
    entry = matched[0]
    old, _ = _original(entry, config, path)
    key = plan['plan_id']
    extra = []
    if operator_stop is not None:
        from .resident_trial_stop import read_stop
        if read_stop(config, path, plan, now_ms=now_ms) != operator_stop:
            raise ValueError('OPERATOR_STOP_RECEIPT_CHANGED')
        key = (key, operator_stop['sha256'])
        extra = ['--operator-stop-path', operator_stop['path'],
            '--operator-stop-sha256', operator_stop['sha256'],
            '--operator-stop-validator-sha256', operator_stop['validator_sha256'],
            '--replacement-source-sha', operator_stop['replacement_source_sha']]
    previous = cache.get(key)
    if previous and 0 <= now_ms - previous[0] < 30000:
        return previous[1]
    state = Path(config['evaluation_directory']) / '.retained-futile' / plan['plan_id']
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               CUDA_VISIBLE_DEVICES='', HIP_VISIBLE_DEVICES='', ROCR_VISIBLE_DEVICES='',
               PYTHONPATH=os.pathsep.join(str(Path(old['runtime_directory']) / name)
                                         for name in ('learn', 'scripts')))
    completed = subprocess.run([old['python'], '-B', str(Path(__file__).resolve()),
        '--config', entry['config_path'], '--config-sha256', entry['config_sha256'],
        '--plan', str(path), '--state', str(state), *extra], env=env,
        capture_output=True, text=True, timeout=15)
    if completed.returncode:
        raise ValueError('RETAINED_CLOSEOUT_FAILED:' + completed.stderr[-400:])
    result = _read(state / 'status.json')
    if operator_stop is not None:
        if (read_stop(config, path, plan) != operator_stop
                or result.get('operator_stop_receipt_sha256') != operator_stop['sha256']
                or result.get('operator_stopped_trials') != 1
                or result.get('operator_stop_torch_imported') is not False
                or result.get('operator_stop_non_performance_assessment') is not True
                or result.get('new_heavy_workers_allowed') is not False):
            raise ValueError('OPERATOR_STOP_CLOSEOUT_BINDING')
    if (result.get('plans') != 1 or result.get('active_prediction_workers') != 0
            or result.get('broker_orders_sent') != 0
            or sum(result.get('counts', {}).values()) != 1):
        raise ValueError('RETAINED_CLOSEOUT_STATUS')
    cache[key] = (now_ms, result)
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


def close_original(config_path, config_sha256, plan_path, state, *,
                   operator_stop_path=None, operator_stop_sha256=None,
                   operator_stop_validator_sha256=None, replacement_source_sha=None):
    # Keep imports below verification: imported package code must be original.
    if _sha(config_path) != config_sha256:
        raise ValueError('RETAINED_CLOSEOUT_CONFIG_CHANGED')
    old = _read(config_path)
    plan = _read(plan_path)
    entry = {'config_path': str(config_path), 'config_sha256': config_sha256,
             'plan_id': plan['plan_id']}
    _original(entry, old, plan_path)
    stop_args = (operator_stop_path, operator_stop_sha256,
                 operator_stop_validator_sha256, replacement_source_sha)
    stopped = any(value is not None for value in stop_args)
    stop = None
    if stopped:
        if not all(value is not None for value in stop_args):
            raise ValueError('OPERATOR_STOP_ARGUMENT_BINDING')
        validator_path = Path(__file__).resolve().with_name('resident_trial_stop.py')
        if _sha(validator_path) != operator_stop_validator_sha256:
            raise ValueError('OPERATOR_STOP_RUNTIME_PIN')
        import importlib.util
        spec = importlib.util.spec_from_file_location('_hepta_retained_stop_validator', validator_path)
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        stop = validator.validate_receipt_file(operator_stop_path, plan_path,
            expected_period_sha256=old['period_sha256'],
            expected_replacement_source_sha=replacement_source_sha,
            expected_sha256=operator_stop_sha256)
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
    if not stopped and not suppress_new_work_for_frozen_failure(plan, records):
        raise ValueError('RETAINED_CLOSEOUT_REQUIRES_IRREVERSIBLE_FUTILITY')
    for prediction in ([] if stopped else plan_path.parent.glob('prediction-*.json')):
        if not (prediction.parent / prediction.name.replace('prediction-', 'outcome-', 1)).exists():
            raise ValueError('RETAINED_CLOSEOUT_UNSCORED_PREDICTION')

    class OriginalCloseout(Evaluator):
        operator_stop_launches_suppressed = 0

        def operator_stop_binding(self, path, plan, now):
            # Authorization is verified outside the original package. Do not
            # recursively redispatch if that frozen version also knows stops.
            return None

        def launch(self, path, slot):
            if stopped:
                self.operator_stop_launches_suppressed += 1
                return
            raise ValueError('RETAINED_CLOSEOUT_CANNOT_LAUNCH_PREDICTIONS')

    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    service = OriginalCloseout(config_path)
    service.root = _SinglePlanView(plan_path, state)
    result = service.step()
    if stopped:
        if 'torch' in sys.modules:
            raise ValueError('OPERATOR_STOP_CPU_ONLY_IMPORT_BOUNDARY')
        validator.validate_receipt_file(operator_stop_path, plan_path,
            expected_period_sha256=old['period_sha256'],
            expected_replacement_source_sha=replacement_source_sha,
            expected_sha256=operator_stop_sha256)
        result.update(operator_stop_receipt_sha256=stop['sha256'],
            operator_stopped_trials=1, operator_stop_non_performance_assessment=True,
            operator_stop_torch_imported=False,
            new_heavy_workers_allowed=False,
            operator_stop_launches_suppressed=service.operator_stop_launches_suppressed)
        from laya_cell.resident_store import atomic
        atomic(state / 'status.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--operator-stop-path', type=Path)
    parser.add_argument('--operator-stop-sha256')
    parser.add_argument('--operator-stop-validator-sha256')
    parser.add_argument('--replacement-source-sha')
    args = parser.parse_args()
    os.umask(0o077)
    close_original(args.config, args.config_sha256, args.plan, args.state,
        operator_stop_path=args.operator_stop_path,
        operator_stop_sha256=args.operator_stop_sha256,
        operator_stop_validator_sha256=args.operator_stop_validator_sha256,
        replacement_source_sha=args.replacement_source_sha)


if __name__ == '__main__':
    main()
