#!/usr/bin/env python3
"""Exercise relocated stock/Tick commands; no source imports or broker services."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate(build: Path, config: str, bindir: str, datadir: str) -> dict:
    for value in (bindir, datadir):
        if Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValueError('relative install paths required')
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'HEPTA_RESEARCH_BARS'):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    with tempfile.TemporaryDirectory(prefix='hepta-format-install-') as directory:
        root = Path(directory)
        original, prefix = root/'original', root/'relocated'

        def run(args, expected=0):
            result = subprocess.run(args, cwd=root, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=120, check=False)
            require(result.returncode == expected,
                    f'command returned {result.returncode}, expected {expected}: '
                    f'{args[0]}\n{result.stdout}\n{result.stderr}')
            return result.stdout

        run(['cmake', '--install', str(build.resolve(strict=True)),
             '--prefix', str(original), '--config', config])
        original.rename(prefix)
        importer, ticks_command = (prefix/bindir/name for name in
                                   ('hepta-research-import', 'hepta-research-ticks'))
        bars_executable = prefix/bindir/'hepta-research-bars'
        require(importer.is_file() and ticks_command.is_file() and bars_executable.is_file(),
                'installed commands missing')
        sessions = root/'sessions.csv'
        sessions.write_text('begin_us,end_us,trading_day\n0,240000000,19700101\n')
        options = ['--sessions', str(sessions), '--instrument', 'TEST', '--clock-zone', 'UTC',
                   '--tick-size', '1', '--capital', '1000', '--quantity', '2',
                   '--fast', '1', '--slow', '2', '--slippage', '1', '--fee-per-unit', '0.5']
        stock, stock_report = root/'stock.csv', root/'stock.json'
        prices = (100, 102, 104, 103)
        stock.write_text('DateTime,Open,High,Low,Close,Volume,TurnOver\n'+''.join(
            f'1970-01-01 00:0{i+1}:00,{p},{p},{p},{p},5,500\n' for i,p in enumerate(prices)))
        run([sys.executable, '-I', str(importer), '--layout', 'stock', '--bars', str(stock),
             '--output', str(stock_report), '--period-seconds', '60',
             '--complete-through-us', '180000000']+options)
        expected_fills = [{'timestamp_us':120000000, 'delta':'2', 'price':'105', 'fee':'1.0'}]

        def check_report(path):
            report = json.loads(path.read_text())
            require(report['fills'] == expected_fills, 'next-bar fill/cost mismatch')
            require([v['value'] for v in report['equity']] == ['1000','1000','997.0','995.0'],
                    'marked equity mismatch')
            require(report['pending_target'] is None and not report['equity'][-1]['complete'],
                    'incomplete final bar generated a signal')
            require(report['mode'] == 'OFFLINE_HYPOTHETICAL' and
                    report['assumptions']['broker_authorized'] is False, 'authority boundary')
            return report

        result = check_report(stock_report)
        require(result['input']['bars_sha256'] == hashlib.sha256(stock.read_bytes()).hexdigest(),
                'stock input digest')
        require(result['input']['source_fields'][0]['tick_count'] is None, 'invented tick count')
        checks = ['installed_stock_end_labels_and_shared_accounting']
        # Fixed independent synthetic rows. Unused depth observations are zeros,
        # not asserted to describe an exchange book or to drive the fill model.
        specifications = {
            'hepta32': (32, {0:'TEST',1:'19700101'}, 2,3,4,5,7,29),
            'immsg34': (34, {0:'0',1:'IMMSG',2:'TEST',3:'19700101'}, 4,5,6,7,9,31),
            'immsg35': (35, {0:'0',1:'IMMSG',2:'TEST',3:'19700101',4:'19700101'}, 5,6,7,8,10,32),
            'zs58': (58, {0:'19700101',3:'TEST'}, 1,2,37,38,46,39),
        }
        for layout, (count, fixed, clock, fraction, price, volume, turnover, interest) in specifications.items():
            lines = []
            for i,p in enumerate(prices):
                row = ['0']*count
                for index,value in fixed.items():
                    row[index] = value
                row[clock] = f'000{i}00' if layout == 'zs58' else f'00:0{i}:00'
                row[fraction], row[price], row[volume] = '0', str(p), str(100+i*5)
                row[turnover], row[interest] = '500', '7.5'
                lines.append(','.join(row))
            source, output = root/(layout+'.csv'), root/(layout+'.json')
            source.write_text('\n'.join(lines)+'\n')
            command = [sys.executable, '-I', str(ticks_command), '--layout', layout,
                       '--ticks', str(source), '--output', str(output),
                       '--period-us', '60000000', '--first-volume', 'baseline']+options
            if layout != 'immsg35':
                command += ['--action-day', '19700101']
            run(command)
            actual = check_report(output)
            require(actual['input']['legacy_ticks']['source_sha256'] ==
                    hashlib.sha256(source.read_bytes()).hexdigest(), 'Tick input digest')
            # A late failure cannot replace an earlier valid report.
            prior = output.read_bytes()
            source.write_text(source.read_text()+'bad,row\n')
            run(command, expected=2)
            require(output.read_bytes() == prior, 'failed import overwrote valid report')
            checks.append('installed_'+layout+'_same_builder_replay_and_failed_publication')
        require((prefix/datadir/'doc/hepta-research/DATA-FORMATS.md').is_file(), 'format contract missing')
        files = [importer, ticks_command, bars_executable]
        files += [prefix/datadir/'heptatrader/research/python/hepta_research'/name
                  for name in ('stock.py','legacy_ticks.py','legacy.py','pipeline.py','model.py')]
        digests = {str(path.relative_to(prefix)):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        return {'schema':'hepta.research.format-install.v1', 'status':'PASS',
                'scope':'relocated_stock_tick_commands', 'checks':checks,
                'installed_sha256':digests, 'broker_qualification':False,
                'canonical_runtime_qualification':False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--config', default='Release')
    parser.add_argument('--bindir', default='bin')
    parser.add_argument('--datadir', default='share')
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.build_dir,args.config,args.bindir,args.datadir),sort_keys=True,indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.TimeoutExpired) as error:
        print('installed data formats FAILED: '+str(error),file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
