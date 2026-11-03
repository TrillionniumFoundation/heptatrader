#!/usr/bin/env python3
"""Read an unchanged prospective journal once after maturity; archive its result."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'python', 'plan', 'state-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    raw = args.plan.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.plan_sha256:
        raise ValueError('ORIGINAL_PLAN_CHANGED')
    plan = json.loads(raw)
    due_ms = (plan['first_slot_at_ms'] + (plan['slot_count'] - 1) * plan['cadence_ms']
              + plan['outcome_policy']['horizon_ms'] + plan['outcome_policy']['settle_after_ms'] + 60000)
    now_ms = time.time_ns() // 1000000
    if not args.check_only and now_ms < due_ms:
        raise ValueError('FULL_WINDOW_NOT_YET_DUE')
    command = [str(args.python), '-B', '-m', 'research.model_replacement.prospective', 'report',
               '--plan', str(args.plan), '--plan-sha256', args.plan_sha256,
               '--state-root', str(args.state_root)]
    result = subprocess.run(command, cwd=args.source, env=os.environ.copy(),
                            check=True, text=True, capture_output=True, timeout=90)
    report = json.loads(result.stdout)
    if report['plan_id'] != plan['plan_id'] or report['trade_permission'] is not False:
        raise ValueError('READ_ONLY_REPORT_IDENTITY')
    if args.check_only:
        print(json.dumps({'state': 'FINALIZER_INPUTS_VERIFIED_NO_JOURNAL_MUTATION',
                          'plan_id': plan['plan_id'], 'due_at_ms': due_ms,
                          'due_utc': datetime.datetime.fromtimestamp(due_ms / 1000, datetime.timezone.utc).isoformat(),
                          'fixed_slots': plan['slot_count'], 'minimum_common_observed': plan['coverage_rule']['min_observed'],
                          'report_command_verified': True, 'output_exists': args.output.exists()}))
        return
    completed = report['outcomes_finalized'] == plan['slot_count']
    common_observed = report['identical_all_arm_observed_slots']
    common_censored = report['outcomes_finalized'] - common_observed
    common_pass = (completed and common_observed >= plan['coverage_rule']['min_observed']
                   and common_censored <= plan['coverage_rule']['max_censored'])
    value = {'schema': 'heptatrader.readonly-prospective-closeout.v1',
             'state': ('COMPLETE_COVERAGE_PASSED' if common_pass else
                       'COMPLETE_COVERAGE_FAILED' if completed else 'INCOMPLETE_AT_CLOSEOUT'),
             'recorded_at_ms': time.time_ns() // 1000000, 'due_at_ms': due_ms,
             'original_plan_sha256': args.plan_sha256, 'original_source': str(args.source),
             'fixed_slots': plan['slot_count'], 'common_observed': common_observed,
             'common_censored': common_censored, 'common_coverage_passed': common_pass,
             'qualification_scope': 'PRICE_RESEARCH_OPERATIONAL_COVERAGE_ONLY',
             'better_than_laya_claim_allowed': False, 'automatic_promotion': False,
             'broker_orders_sent': 0, 'journal_changed_by_finalizer': False, 'report': report}
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.closeout-', dir=args.output.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write((json.dumps(value, sort_keys=True, indent=2) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, args.output)
        directory_fd = os.open(args.output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.unlink(temporary)
    print(json.dumps({'state': value['state'], 'output': str(args.output),
                      'plan_id': plan['plan_id'], 'common_coverage_passed': common_pass,
                      'automatic_promotion': False}))


if __name__ == '__main__':
    main()
