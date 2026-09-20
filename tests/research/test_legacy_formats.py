"""Behavior tests for stock bars and four reviewed Tick layouts; synthetic data only."""
from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hepta_research import legacy, legacy_ticks, stock
from hepta_research.pipeline import evaluate_bars


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.sessions, self.output = (self.root/x for x in ('input.csv', 'sessions.csv', 'report.json'))
        self.sessions.write_text('begin_us,end_us,trading_day\n0,240000000,19700101\n')

    def write(self, rows):
        self.source.write_text(''.join(','.join(map(str,row))+'\n' for row in rows))


class HeaderCompatibilityTests(Fixture):
    def test_documented_header_and_optional_extremum_aliases(self):
        for columns in (11,13):
            for label in ('time','Time','DateTime','StartTime','szStartTime'):
                with self.subTest(columns=columns,label=label):
                    header = ['TimeStamp',label,'Open','High','Low','Close','Volume','LastVolume',
                              'TurnOver','LastTurnOver','OpenInterest']
                    row = [0,'19700101_000000',100,102,99,101,100,5,1000,50,7]
                    if columns == 13:
                        header += ['HighTimeStamp','LowTimeStamp']
                        row += [11644473610000000,11644473620000000]
                    self.write([header,row])
                    bars, meta = legacy.read_legacy_bars(self.source,self.sessions,instrument='TEST',
                        clock_zone='UTC',volume_field='LastVolume',complete_through_us=60000000,
                        columns=columns,tick_size=1)
                    self.assertEqual(bars[0].begin_us,0)
                    self.assertEqual(meta['header'],header)
                    self.assertEqual(meta['source_fields'][0]['volume'],5)
                    self.assertEqual(meta['source_fields'][0]['total_volume'],100)
                    if columns == 13:
                        self.assertEqual(meta['source_fields'][0]['high_time_us'],10000000)

    def test_aliases_do_not_allow_field_reordering(self):
        header = ['TimeStamp','time','Open','High','Low','Close','LastVolume','Volume',
                  'TurnOver','LastTurnOver','OpenInterest']
        self.write([header,[0,'19700101_000000',100,102,99,101,5,100,1000,50,7]])
        with self.assertRaisesRegex(ValueError,'header'):
            legacy.read_legacy_bars(self.source,self.sessions,instrument='TEST',clock_zone='UTC',
                volume_field='LastVolume',complete_through_us=60000000,tick_size=1)


class StockImportTests(Fixture):
    def setUp(self):
        super().setUp()
        self.rows = [[f'1970-01-01 00:0{i+1}:00',p,p+1,p-1,p,10,1000] for i,p in enumerate((100,102,104,103))]
        self.write(self.rows)

    def read(self, **changes):
        args = dict(instrument='TEST',clock_zone='UTC',period_seconds=60,tick_size='0.25',
                    complete_through_us=180000000)
        args.update(changes)
        return stock.read_stock_bars(self.source,self.sessions,**args)

    def cli(self, **changes):
        args = {'bars':self.source,'sessions':self.sessions,'output':self.output,'layout':'stock',
                'instrument':'TEST','clock-zone':'UTC','period-seconds':60,'tick-size':'0.25',
                'complete-through-us':180000000,'capital':1000,'quantity':2,'fast':1,'slow':2,
                'slippage':1,'fee-per-unit':'0.5'}
        args.update(changes)
        with redirect_stderr(io.StringIO()):
            return legacy.main([v for k,x in args.items() for v in ('--'+k,str(x))])

    def test_end_label_period_source_fields_and_watermark(self):
        bars,meta = self.read()
        self.assertEqual([b.begin_us for b in bars],[0,60000000,120000000,180000000])
        self.assertEqual([b.complete for b in bars],[True,True,True,False])
        self.assertEqual(meta['source_fields'][0]['open_ticks'],400)
        self.assertEqual(meta['source_fields'][0]['source_close_label'],'1970-01-01 00:01:00')
        self.assertIsNone(meta['source_fields'][0]['tick_count'])
        self.assertIsNone(meta['source_fields'][0]['open_interest'])
        self.assertEqual(meta['bars_sha256'],hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(meta['sessions_sha256'],hashlib.sha256(self.sessions.read_bytes()).hexdigest())

    def test_header_and_crlf(self):
        self.write([stock.HEADER]+self.rows)
        self.source.write_bytes(self.source.read_bytes().replace(b'\n',b'\r\n'))
        self.assertEqual(self.read()[1]['header'],list(stock.HEADER))

    def test_real_cli_uses_existing_accounting_and_causality(self):
        self.assertEqual(self.cli(),0)
        result = json.loads(self.output.read_text())
        self.assertEqual(result['fills'],[dict(timestamp_us=120000000,delta='2',price='105',fee='1.0')])
        self.assertEqual([r['value'] for r in result['equity']],['1000','1000','997.0','995.0'])
        self.assertIsNone(result['pending_target'])
        self.assertFalse(result['assumptions']['broker_authorized'])

    def test_period_must_be_explicit_no_180_second_guess(self):
        for period in (None,0,-1,True,86401,'60'):
            with self.subTest(period=period),self.assertRaises(ValueError):
                self.read(period_seconds=period)
        self.write([['1970-01-01 00:03:00',100,101,99,100,10,1000]])
        self.assertEqual(self.read(period_seconds=180)[0][0].begin_us,0)

    def test_unknown_zone_cli_returns_failure(self):
        self.assertEqual(self.cli(**{'clock-zone':'unavailable/zone'}),2)
        self.assertFalse(self.output.exists())

    def test_stock_rejects_future_specific_flags(self):
        self.assertEqual(self.cli(columns=11),2)
        self.assertEqual(self.cli(**{'volume-field':'LastVolume'}),2)

    def test_source_completeness_is_not_file_end(self):
        self.write(self.rows[:1])
        self.assertFalse(self.read(complete_through_us=0)[0][0].complete)
        self.write(self.rows)
        with self.assertRaisesRegex(ValueError,'incomplete'):
            self.read(complete_through_us=0)

    def test_bad_prices_volumes_and_missing_values(self):
        for column,value in [(1,''),(1,'NaN'),(1,'0'),(2,'99'),(3,'103'),(1,'100.1'),
                             (5,'1.5'),(5,'-1'),(5,str(2**64)),(6,'-1'),(6,'Infinity')]:
            with self.subTest(column=column,value=value):
                row = self.rows[0].copy(); row[column]=value; self.write([row])
                with self.assertRaises(ValueError): self.read()

    def test_overlap_and_session_boundaries(self):
        self.write([self.rows[0],self.rows[0]])
        with self.assertRaisesRegex(ValueError,'overlap'): self.read()
        self.write(self.rows)
        self.sessions.write_text('begin_us,end_us,trading_day\n1,240000000,19700101\n')
        with self.assertRaisesRegex(ValueError,'session'): self.read()

    def test_dst_folds_gaps_and_transition_spanning_intervals_reject(self):
        for label,period in [('2026-11-01 01:30:00',60),('2026-03-08 02:30:00',60),
                             ('2026-03-08 03:30:00',7200)]:
            with self.subTest(label=label):
                self.write([[label,100,101,99,100,10,1000]])
                with self.assertRaisesRegex(ValueError,'clock|ambiguous|nonexistent'):
                    self.read(clock_zone='America/New_York',period_seconds=period)

    def test_short_long_repeated_header_and_empty_files(self):
        for text in ('','bad,row\n','x'*4097+'\n',','.join(stock.HEADER)+'\n',
                     ','.join(stock.HEADER)+'\n'+','.join(stock.HEADER)+'\n'):
            with self.subTest(text=text[:20]):
                self.source.write_text(text)
                with self.assertRaises(ValueError): self.read()

    def test_bound_and_regular_file_only(self):
        with self.assertRaisesRegex(ValueError,'count'): self.read(max_bars=1)
        saved = self.root/'saved'; self.source.rename(saved); self.source.symlink_to(saved)
        with self.assertRaises(OSError): self.read()
        self.source.unlink(); os.mkfifo(self.source)
        with self.assertRaisesRegex(ValueError,'regular'): self.read()

    def test_malformed_tail_preserves_previous_output(self):
        self.output.write_bytes(b'old')
        self.source.write_text(self.source.read_text()+'malformed\n')
        self.assertEqual(self.cli(),2)
        self.assertEqual(self.output.read_bytes(),b'old')

    def test_input_alias_and_fsync_failure_are_not_success(self):
        original = self.source.read_bytes()
        self.assertEqual(self.cli(output=self.source),2)
        self.assertEqual(self.source.read_bytes(),original)
        self.output.write_bytes(b'old')
        with patch('hepta_research.pipeline.os.fsync',side_effect=OSError('fault')):
            self.assertEqual(self.cli(),2)
        self.assertEqual(self.output.read_bytes(),b'old')


class TickImportTests(Fixture):
    @staticmethod
    def row(layout, *, clock='00:00:00', price=100, volume=10, day='19700101', action='19700101', fraction=0):
        spec = legacy_ticks.LAYOUTS[layout]
        result = ['0']*spec['count']
        for key,value in [('instrument','TEST'),('day',day),('time',clock.replace(':','') if layout=='zs58' else clock),
                          ('fraction',fraction),('price',price),('volume',volume),('turnover','1000.25'),('interest','7.5')]:
            result[spec[key]]=str(value)
        if spec['action'] is not None: result[spec['action']]=action
        if layout.startswith('immsg'): result[0],result[1]='1970-01-01 00:00:00','IMMSG'
        return result

    def normalize(self, layout='immsg35', **changes):
        args = dict(layout=layout,instrument='TEST',clock_zone='UTC',tick_size='0.25')
        if layout!='immsg35' and 'action_days_path' not in changes: args['action_day']='19700101'
        args.update(changes)
        return legacy_ticks.normalize_ticks(self.source,self.sessions,**args)

    def rows(self, layout='immsg35'):
        return [self.row(layout,clock=f'00:0{i}:00',price=p,volume=10*(i+1))
                for i,p in enumerate((100,102,104,103))]

    def test_all_layouts_normalize_to_identical_observations(self):
        outputs=[]
        for layout in legacy_ticks.LAYOUTS:
            with self.subTest(layout=layout):
                rows=self.rows(layout); self.write(rows)
                value=self.normalize(layout)
                self.assertEqual(value.metadata['source_format'],layout)
                self.assertEqual(value.metadata['source_sha256'],hashlib.sha256(self.source.read_bytes()).hexdigest())
                self.assertEqual(value.metadata['source_fields'][0]['raw_fields'],rows[0])
                self.assertEqual(value.metadata['source_fields'][0]['open_interest'],Decimal('7.5'))
                self.assertEqual(value.metadata['tick_count'],4)
                self.assertIn('not_venue',value.metadata['sequence_semantics'])
                outputs.append(value.ticks_csv)
        self.assertTrue(all(value==outputs[0] for value in outputs))
        self.assertEqual(outputs[0],legacy_ticks.TICK_HEADER+
            'TEST,19700101,0,1,400,10\nTEST,19700101,60000000,2,408,20\n'
            'TEST,19700101,120000000,3,416,30\nTEST,19700101,180000000,4,412,40\n')

    def test_zs_requires_58_fields_and_preserves_submillisecond_data(self):
        row=self.row('zs58',fraction=123456); self.write([row])
        self.assertIn(',123456,1,',self.normalize('zs58').ticks_csv)
        self.write([row[:-1]])
        with self.assertRaisesRegex(ValueError,'field'): self.normalize('zs58')

    def test_millisecond_fraction_bounds(self):
        for layout in ('hepta32','immsg34','immsg35'):
            self.write([self.row(layout,fraction=999)])
            self.assertIn(',999000,1,',self.normalize(layout).ticks_csv)
            self.write([self.row(layout,fraction=1000)])
            with self.assertRaisesRegex(ValueError,'range'): self.normalize(layout)

    def test_negative_prices_are_exact_not_rounded(self):
        self.write([self.row('immsg35',price='-0.25')])
        self.assertIn(',1,-1,10\n',self.normalize().ticks_csv)
        self.write([self.row('immsg35',price='0.1')])
        with self.assertRaisesRegex(ValueError,'ticks'): self.normalize()

    def test_missing_action_day_never_uses_trading_day(self):
        self.write([self.row('hepta32')])
        with self.assertRaisesRegex(ValueError,'exactly one'):
            self.normalize('hepta32',action_day=None)
        self.write([self.row('immsg35')])
        with self.assertRaisesRegex(ValueError,'overridden'): self.normalize(action_day='19700101')

    def test_night_session_can_keep_later_trading_day(self):
        self.sessions.write_text('begin_us,end_us,trading_day\n0,240000000,19700102\n')
        self.write([self.row('immsg35',day='19700102',action='19700101')])
        value=self.normalize()
        self.assertIn('TEST,19700102,0,',value.ticks_csv)
        self.assertEqual(value.metadata['source_fields'][0]['action_day'],'19700101')

    def test_per_row_action_dates_cross_midnight_without_guessing(self):
        self.sessions.write_text('begin_us,end_us,trading_day\n86340000000,86460000000,19700102\n')
        self.write([self.row('hepta32',clock='23:59:59',day='19700102'),
                    self.row('hepta32',clock='00:00:00',day='19700102',volume=11)])
        dates=self.root/'dates.csv'; dates.write_text('row,action_day\n1,19700101\n2,19700102\n')
        value=self.normalize('hepta32',action_days_path=dates)
        self.assertEqual(value.metadata['action_days_sha256'],hashlib.sha256(dates.read_bytes()).hexdigest())
        self.assertEqual([x['timestamp_us'] for x in value.metadata['source_fields']],[86399000000,86400000000])

    def test_date_map_must_match_every_data_row_exactly(self):
        self.write([self.row('hepta32')]); dates=self.root/'dates.csv'
        for text in ('row,action_day\n','row,action_day\n2,19700101\n',
                     'row,action_day\n1,19700101\n2,19700101\n','row,action_day\n1,19700230\n',
                     'wrong,header\n1,19700101\n'):
            with self.subTest(text=text):
                dates.write_text(text)
                with self.assertRaises(ValueError): self.normalize('hepta32',action_days_path=dates)
        dates.write_text('row,action_day\n1,19700101\n')
        self.write([self.row('hepta32'),self.row('hepta32',volume=11)])
        with self.assertRaisesRegex(ValueError,'missing row'): self.normalize('hepta32',action_days_path=dates)

    def test_header_is_explicit_and_validated(self):
        spec=legacy_ticks.LAYOUTS['immsg35']; header=['Unused']*35
        for key,name in [('instrument','InstrumentID'),('day','TradingDay'),('action','ActionDay'),
                         ('time','UpdateTime'),('fraction','UpdateMillisec'),('price','LastPrice'),
                         ('volume','Volume'),('turnover','Turnover'),('interest','OpenInterest')]:
            header[spec[key]]=name
        header[0],header[1]='Localtime','MsgType'
        self.write([header,self.row('immsg35')])
        self.assertEqual(self.normalize(has_header=True).metadata['header'],header)
        with self.assertRaises(ValueError): self.normalize()
        header[7],header[8]=header[8],header[7]; self.write([header,self.row('immsg35')])
        with self.assertRaisesRegex(ValueError,'header'): self.normalize(has_header=True)

    def test_duplicate_content_keeps_source_ordinals_not_fake_venue_identity(self):
        self.write([self.row('immsg35')]*2)
        value=self.normalize()
        self.assertEqual(value.metadata['tick_count'],2)
        self.assertEqual(value.ticks_csv.splitlines()[1:],['TEST,19700101,0,1,400,10','TEST,19700101,0,2,400,10'])

    def test_timestamp_volume_and_trading_day_regressions_reject(self):
        for second in (self.row('immsg35',clock='00:00:00',volume=11),
                       self.row('immsg35',clock='00:02:00',volume=9),
                       self.row('immsg35',clock='00:02:00',day='19691231')):
            self.write([self.row('immsg35',clock='00:01:00'),second])
            with self.assertRaises(ValueError): self.normalize()

    def test_midnight_day_reset_is_explicit(self):
        self.sessions.write_text('begin_us,end_us,trading_day\n0,60000000,19700101\n86400000000,86460000000,19700102\n')
        self.write([self.row('immsg35',volume=100),self.row('immsg35',volume=1,day='19700102',action='19700102')])
        self.assertEqual(self.normalize().metadata['tick_count'],2)

    def test_session_end_is_excluded(self):
        self.write([self.row('immsg35',clock='00:04:00')])
        with self.assertRaisesRegex(ValueError,'session'): self.normalize()

    def test_dst_fold_and_gap_are_not_guessed(self):
        for day,clock in [('20261101','01:30:00'),('20260308','02:30:00')]:
            self.write([self.row('immsg35',clock=clock,day=day,action=day)])
            with self.assertRaisesRegex(ValueError,'ambiguous/nonexistent'):
                self.normalize(clock_zone='America/New_York')

    def test_bad_fields_and_discriminator_reject(self):
        for index,value in [(1,'OTHER'),(2,'FOREIGN'),(3,'19700230'),(4,'0'),(5,'24:00:00'),
                            (6,'-1'),(7,'NaN'),(8,str(2**64)),(8,'1.5'),(10,'Inf'),(32,'-1')]:
            with self.subTest(index=index,value=value):
                row=self.row('immsg35'); row[index]=value; self.write([row])
                with self.assertRaises(ValueError): self.normalize()

    def test_configuration_bounds_and_unknown_layout(self):
        self.write(self.rows())
        for args in ({'layout':'other'},{'max_ticks':0},{'max_ticks':True},{'max_ticks':100001},
                     {'max_ticks':1},{'has_header':1},{'clock_zone':''},{'instrument':'X/Y'}):
            with self.subTest(args=args),self.assertRaises(ValueError): self.normalize(**args)

    def test_nonregular_sources_and_action_map_reject_without_blocking(self):
        os.mkfifo(self.source)
        with self.assertRaisesRegex(ValueError,'regular'): self.normalize()
        self.source.unlink(); self.write([self.row('hepta32')]); dates=self.root/'dates.csv'; os.mkfifo(dates)
        with self.assertRaisesRegex(ValueError,'regular'): self.normalize('hepta32',action_days_path=dates)

    def test_corrupt_tail_overlong_and_empty_inputs(self):
        for raw in (b'',b'bad,row\n',b'x'*4097+b'\n',b'\0\n',b'\xff\n'):
            self.source.write_bytes(raw)
            with self.assertRaises(ValueError): self.normalize()
        self.write(self.rows()); self.source.write_text(self.source.read_text()+'bad,row\n')
        with self.assertRaises(ValueError): self.normalize()


class ActualTickPipelineTests(Fixture):
    """The additional tests invoke the real built C++ converter, never a success stub."""
    row = staticmethod(TickImportTests.row)
    rows = TickImportTests.rows

    def setUp(self):
        super().setUp()
        self.binary=Path(os.environ['HEPTA_RESEARCH_BARS']).resolve()
        self.write(self.rows())

    def report(self, **changes):
        args=dict(bars_executable=self.binary,period_us=60000000,first_volume='baseline',
                  layout='immsg35',instrument='TEST',clock_zone='UTC',tick_size=1,
                  capital=1000,quantity=2,fast=1,slow=2,slippage=1,fee_per_unit='0.5')
        args.update(changes)
        return legacy_ticks.tick_report(self.source,self.sessions,**args)

    def cli(self, **changes):
        args={'ticks':self.source,'sessions':self.sessions,'output':self.output,'layout':'immsg35',
              'instrument':'TEST','clock-zone':'UTC','tick-size':1,'period-us':60000000,
              'first-volume':'baseline','bars-executable':self.binary,'capital':1000,
              'quantity':2,'fast':1,'slow':2,'slippage':1,'fee-per-unit':'0.5'}
        args.update(changes)
        command=[sys.executable,'-B','-m','hepta_research.legacy_ticks']
        command += [s for k,v in args.items() for s in ('--'+k,str(v))]
        return subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)

    def test_real_builder_and_existing_ledger_have_hand_computed_result(self):
        for layout in legacy_ticks.LAYOUTS:
            self.write(self.rows(layout))
            extra={} if layout=='immsg35' else {'action_day':'19700101'}
            report=self.report(layout=layout,**extra)
            self.assertEqual(report['fills'],[dict(timestamp_us=120000000,delta=Decimal(2),price=Decimal(105),fee=Decimal('1.0'))])
            self.assertEqual([r['value'] for r in report['equity']],[1000,1000,997,995])
            self.assertFalse(report['assumptions']['broker_authorized'])
            self.assertIsNone(report['pending_target'])
            self.assertFalse(report['equity'][-1]['complete'])

    def test_real_cli_and_source_digests(self):
        result=self.cli()
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(self.output.read_text())
        self.assertEqual(report['input']['legacy_ticks']['source_sha256'],hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(report['position'],'2')
        self.assertFalse(report['assumptions']['automatic_funding'])

    def test_real_cli_uses_explicit_per_row_action_dates(self):
        self.write(self.rows('hepta32'))
        dates = self.root/'dates.csv'
        dates.write_text('row,action_day\n'+''.join(f'{i},19700101\n' for i in range(1,5)))
        result = self.cli(layout='hepta32', **{'action-days':dates})
        self.assertEqual(result.returncode,0,result.stderr)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['input']['legacy_ticks']['action_days_sha256'],
                         hashlib.sha256(dates.read_bytes()).hexdigest())
        self.assertEqual(report['position'],'2')

    def test_builder_receives_captured_sessions_not_reopened_source(self):
        original_digest=hashlib.sha256(self.sessions.read_bytes()).hexdigest()
        original_run=subprocess.run
        def mutate_original(command, **kwargs):
            self.sessions.write_text('corrupted after normalization\n')
            return original_run(command,**kwargs)
        with patch('hepta_research.legacy_ticks.subprocess.run',side_effect=mutate_original):
            report=self.report()
        self.assertEqual(report['input']['legacy_ticks']['sessions_sha256'],original_digest)
        self.assertEqual([x['value'] for x in report['equity']],[1000,1000,997,995])

    def test_failing_builder_does_not_publish_success(self):
        self.output.write_bytes(b'previous')
        bad=self.root/'failure'; bad.write_text('#!/bin/sh\nexit 42\n'); bad.chmod(0o700)
        result=self.cli(**{'bars-executable':bad})
        self.assertEqual(result.returncode,2)
        self.assertEqual(self.output.read_bytes(),b'previous')

    def test_late_error_and_alias_leave_inputs_and_previous_output(self):
        self.output.write_bytes(b'old')
        original=self.source.read_bytes()
        self.assertEqual(self.cli(output=self.source).returncode,2)
        self.assertEqual(self.source.read_bytes(),original)
        os.link(self.source,self.root/'alias')
        self.assertEqual(self.cli(output=self.root/'alias').returncode,2)
        self.source.write_bytes(original+b'bad,row\n')
        self.assertEqual(self.cli().returncode,2)
        self.assertEqual(self.output.read_bytes(),b'old')

    def test_period_and_executable_bounds(self):
        for changes in ({'period_us':-1},{'period_us':2**63},{'period_us':True},
                        {'first_volume':'auto'},{'timeout_seconds':0},{'bars_executable':Path('relative')}):
            with self.subTest(changes=changes),self.assertRaises(ValueError): self.report(**changes)

    def test_timeout_is_propagated_not_converted_to_report(self):
        with patch('hepta_research.legacy_ticks.subprocess.run',side_effect=subprocess.TimeoutExpired('builder',1)):
            with self.assertRaises(subprocess.TimeoutExpired): self.report()


if __name__=='__main__':
    unittest.main()
