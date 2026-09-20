from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from hepta_research.legacy import HEADER, LEGACY_EPOCH, MINUTE_US, main, read_legacy_bars
from hepta_research.model import ReplayBar
from hepta_research.pipeline import HEADER as NORMALIZED_HEADER, evaluate_bars, run_report


def old_stamp(local: datetime) -> int:
    delta = local-LEGACY_EPOCH
    return delta.days*86400000000+delta.seconds*1000000+delta.microseconds


def unix_stamp(aware: datetime) -> int:
    delta = aware-datetime(1970,1,1,tzinfo=timezone.utc)
    return delta.days*86400000000+delta.seconds*1000000+delta.microseconds


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'legacy.csv'
        self.sessions = self.root/'sessions.csv'
        self.output = self.root/'report.json'
        self.local = datetime(2026,9,21,9)
        self.begin = unix_stamp(self.local.replace(tzinfo=ZoneInfo('Asia/Shanghai')))
        self.rows = [self.row(self.local+timedelta(minutes=i), 10*(i+1)) for i in range(3)]
        self.write_rows()
        self.write_sessions([(self.begin,self.begin+3*MINUTE_US,'20260921')])

    def row(self, local, price=10, columns=11):
        result = [str(old_stamp(local)),local.strftime('%Y%m%d_%H%M%S'),
                  str(price),str(price+1),str(price-1),str(price),
                  '100','5','1000.25','50.125','7.5']
        if columns==13:
            result += [str(old_stamp(local+timedelta(seconds=10))), '0']
        return result

    def write_rows(self, rows=None, header=True, columns=11):
        data = rows if rows is not None else self.rows
        names = list(HEADER)+(['HighTime','LowTime'] if columns==13 else [])
        lines = ([','.join(names)] if header else []) + [','.join(row) for row in data]
        self.source.write_text('\n'.join(lines)+'\n')

    def write_sessions(self, values):
        self.sessions.write_text('begin_us,end_us,trading_day\n'+''.join(f'{a},{b},{d}\n' for a,b,d in values))

    def read(self, **kwargs):
        options = dict(instrument='TEST',clock_zone='Asia/Shanghai',volume_field='LastVolume',
                       complete_through_us=self.begin+3*MINUTE_US,columns=11,tick_size='0.25')
        options.update(kwargs)
        return read_legacy_bars(self.source,self.sessions,**options)

    def cli(self, **kwargs):
        values = {'bars':self.source,'sessions':self.sessions,'output':self.output,
                  'instrument':'TEST','clock-zone':'Asia/Shanghai','volume-field':'LastVolume',
                  'complete-through-us':self.begin+3*MINUTE_US,'tick-size':'0.25',
                  'capital':1000,'quantity':2,'fast':1,'slow':2}
        values.update(kwargs)
        args = [item for key,value in values.items() for item in ('--'+key,str(value))]
        with redirect_stderr(io.StringIO()):
            return main(args)

    def test_import_fields_and_exact_epoch(self):
        bars, meta = self.read()
        self.assertEqual(bars[0],ReplayBar(self.begin,self.begin+MINUTE_US,Decimal(10),Decimal(10),True))
        self.assertEqual(meta['timestamp_epoch'],'1601-01-01')
        self.assertEqual(meta['bars_sha256'],hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(meta['sessions_sha256'],hashlib.sha256(self.sessions.read_bytes()).hexdigest())
        fields=meta['source_fields'][0]
        self.assertEqual(fields['open_ticks'],40)
        self.assertEqual(fields['total_turnover'],Decimal('1000.25'))
        self.assertEqual(fields['last_turnover'],Decimal('50.125'))
        self.assertEqual(fields['open_interest'],Decimal('7.5'))
        self.assertEqual(fields['utc_offset_seconds'],28800)
        self.assertIsNone(fields['tick_count'])
        self.assertEqual(fields['volume'],5)

    def test_unix_epoch_anchor(self):
        local=datetime(1970,1,1)
        self.write_rows([self.row(local)])
        self.write_sessions([(0,MINUTE_US,'19700101')])
        bars,_=self.read(clock_zone='UTC',complete_through_us=MINUTE_US)
        self.assertEqual(bars[0].begin_us,0)
        self.assertEqual(old_stamp(local),11644473600000000)

    def test_zero_fallback_and_microsecond_preservation(self):
        self.rows[0][0]='0'; self.write_rows()
        self.assertEqual(self.read()[0][0].begin_us,self.begin)
        local=self.local+timedelta(microseconds=123456)
        self.write_rows([self.row(local)])
        self.assertEqual(self.read()[0][0].begin_us,self.begin+123456)

    def test_timestamp_conflict_not_silently_repaired(self):
        self.rows[0][0]=str(self.begin); self.write_rows()
        with self.assertRaisesRegex(ValueError,'disagree'): self.read()
        self.rows[0][0]=str(old_stamp(self.local)+1000000); self.write_rows()
        with self.assertRaisesRegex(ValueError,'disagree'): self.read()

    def test_headerless_iso_label_and_crlf(self):
        self.rows[0][1]=self.local.strftime('%Y-%m-%d %H:%M:%S')
        self.write_rows(header=False)
        self.source.write_bytes(self.source.read_bytes().replace(b'\n',b'\r\n'))
        bars,meta=self.read()
        self.assertEqual(len(bars),3)
        self.assertIsNone(meta['header'])
        self.assertEqual(meta['bars_sha256'],hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_named_header_cannot_reorder_volume(self):
        text=self.source.read_text().replace('TotalVolume,LastVolume','LastVolume,TotalVolume')
        self.source.write_text(text)
        with self.assertRaisesRegex(ValueError,'header'): self.read()

    def test_explicit_volume_choice_preserves_both(self):
        bars,a=self.read(); other,b=self.read(volume_field='TotalVolume')
        self.assertEqual(bars,other)
        self.assertEqual(a['source_fields'][0]['volume'],5)
        self.assertEqual(b['source_fields'][0]['volume'],100)
        self.assertEqual(b['source_fields'][0]['last_volume'],5)

    def test_extremum_times_are_preserved_and_checked(self):
        self.write_rows([self.row(self.local,columns=13)],columns=13)
        bars,meta=self.read(columns=13)
        self.assertEqual(meta['source_fields'][0]['high_time_us'],self.begin+10000000)
        self.assertIsNone(meta['source_fields'][0]['low_time_us'])
        row=self.row(self.local,columns=13); row[11]=str(old_stamp(self.local)+MINUTE_US)
        self.write_rows([row],columns=13)
        with self.assertRaisesRegex(ValueError,'extremum'): self.read(columns=13)

    def test_watermark_does_not_infer_complete_from_eof(self):
        bars,_=self.read(complete_through_us=self.begin+2*MINUTE_US)
        self.assertEqual([b.complete for b in bars],[True,True,False])
        with self.assertRaisesRegex(ValueError,'incomplete'): self.read(complete_through_us=self.begin)

    def test_night_session_uses_supplied_trading_day(self):
        self.write_rows([self.row(datetime(2026,9,20,21))])
        start=unix_stamp(datetime(2026,9,20,13,tzinfo=timezone.utc))
        self.write_sessions([(start,start+MINUTE_US,'20260921')])
        bars,meta=self.read(complete_through_us=start+MINUTE_US)
        self.assertEqual(bars[0].begin_us,start)
        self.assertEqual(meta['source_fields'][0]['trading_day'],'20260921')

    def test_dst_fold_and_gap_are_rejected(self):
        for local in (datetime(2026,11,1,1,30),datetime(2026,3,8,2,30)):
            with self.subTest(local=local):
                self.write_rows([self.row(local)])
                with self.assertRaisesRegex(ValueError,'ambiguous/nonexistent'):
                    self.read(clock_zone='America/New_York')

    def test_configuration_types_and_bounds(self):
        for option,value in [('instrument','A/B'),('instrument',True),('columns',12),('columns',True),
                             ('clock_zone',''),('clock_zone','not/a/zone'),('volume_field','auto'),
                             ('complete_through_us',True),('complete_through_us',-1),
                             ('complete_through_us',2**63),('max_bars',0),('max_bars',True),
                             ('max_bars',1000001),('tick_size',0)]:
            with self.subTest(option=option,value=value),self.assertRaises((ValueError,TypeError)):
                self.read(**{option:value})

    def test_exact_price_grid_and_strict_decimals(self):
        for value in ('10.1','NaN','Infinity','1_0','1e19','1e-19'):
            with self.subTest(value=value):
                rows=[self.row(self.local)]; rows[0][2]=value; self.write_rows(rows)
                with self.assertRaises(ValueError): self.read()
        self.write_rows([self.row(self.local,-10)])
        self.assertEqual(self.read()[0][0].open,Decimal(-10))
        with self.assertRaisesRegex(ValueError,'ticks'): self.read(tick_size='1e-18')

    def test_counter_and_optional_field_validation(self):
        for column,value in [(6,'-1'),(6,str(2**64)),(7,'1.5'),(8,'NaN'),(9,'1_0'),(10,'-1')]:
            with self.subTest(column=column,value=value):
                rows=[self.row(self.local)]; rows[0][column]=value; self.write_rows(rows)
                with self.assertRaises(ValueError): self.read()

    def test_ohlc_and_order_are_checked(self):
        self.rows[0][3]='9'; self.write_rows()
        with self.assertRaisesRegex(ValueError,'OHLC'): self.read()
        self.write_rows([self.row(self.local),self.row(self.local)])
        with self.assertRaisesRegex(ValueError,'overlap'): self.read()
        self.write_rows([self.row(self.local+timedelta(minutes=1)),self.row(self.local)])
        with self.assertRaisesRegex(ValueError,'overlap'): self.read()

    def test_bars_must_be_contained_in_session(self):
        self.write_sessions([(self.begin+1,self.begin+4*MINUTE_US,'20260921')])
        with self.assertRaisesRegex(ValueError,'session'): self.read()
        self.write_sessions([(self.begin,self.begin+MINUTE_US-1,'20260921')])
        with self.assertRaisesRegex(ValueError,'session'): self.read()

    def test_sessions_validate_days_and_boundaries(self):
        for sessions in [[(self.begin,self.begin,'20260921')],
                         [(self.begin,self.begin+MINUTE_US,'20260230')],
                         [(self.begin,self.begin+MINUTE_US,'20260921'),(self.begin,self.begin+MINUTE_US,'20260921')],
                         [(self.begin,self.begin+MINUTE_US,'20260922'),(self.begin+MINUTE_US,self.begin+2*MINUTE_US,'20260921')]]:
            with self.subTest(sessions=sessions):
                self.write_sessions(sessions)
                with self.assertRaises(ValueError): self.read()
        self.sessions.write_text('bad_header\n')
        with self.assertRaises(ValueError): self.read()

    def test_truncated_overlong_and_repeated_header(self):
        for raw in (b'',b'bad,row\n',b'x'*4097+b'\n',b'\x00\n',b'\xff\n',b'"field"\n',
                    (','.join(HEADER)+'\n') .encode()):
            with self.subTest(raw=raw[:20]):
                self.source.write_bytes(raw)
                with self.assertRaises((ValueError,UnicodeError)): self.read()
        self.write_rows(); self.source.write_text(self.source.read_text()+','.join(HEADER)+'\n')
        with self.assertRaises(ValueError): self.read()

    def test_count_bound_and_empty_sessions(self):
        with self.assertRaisesRegex(ValueError,'count'): self.read(max_bars=2)
        self.sessions.write_text('begin_us,end_us,trading_day\n')
        with self.assertRaisesRegex(ValueError,'empty sessions'): self.read()

    def test_regular_source_only_and_descriptor_cleanup(self):
        original=self.source.read_bytes(); self.source.unlink()
        other=self.root/'actual.csv'; other.write_bytes(original); self.source.symlink_to(other)
        with self.assertRaises(OSError): self.read()
        self.source.unlink(); os.mkfifo(self.source)
        before=len(os.listdir('/proc/self/fd'))
        for _ in range(20):
            with self.assertRaisesRegex(ValueError,'regular'): self.read()
        self.assertEqual(before,len(os.listdir('/proc/self/fd')))
        self.source.unlink(); self.source.mkdir()
        with self.assertRaises(ValueError): self.read()

    def test_complete_import_reuses_existing_causal_report(self):
        self.assertEqual(self.cli(),0)
        actual=json.loads(self.output.read_text())
        self.assertEqual(actual['mode'],'OFFLINE_HYPOTHETICAL')
        self.assertEqual(len(actual['fills']),1)
        self.assertEqual(actual['fills'][0]['timestamp_us'],self.begin+2*MINUTE_US)
        self.assertEqual(actual['fills'][0]['price'],'30')
        self.assertEqual(actual['fills'][0]['delta'],'2')
        self.assertFalse(actual['assumptions']['broker_authorized'])
        self.assertIsNone(actual['input']['source_fields'][0]['tick_count'])
        normalized=self.root/'normalized.csv'
        normalized.write_text(NORMALIZED_HEADER+'\n'+''.join(
            f'TEST,20260921,{self.begin+i*MINUTE_US},{self.begin+(i+1)*MINUTE_US},{40*(i+1)},{40*(i+1)+4},{40*(i+1)-4},{40*(i+1)},5,1,1\n'
            for i in range(3)))
        old=run_report(normalized,tick_size='0.25',capital=1000,quantity=2,fast=1,slow=2)
        bars,meta=self.read(); new=evaluate_bars(bars,meta,capital=1000,quantity=2,fast=1,slow=2)
        for key in ('fills','equity','position','fees','metrics','pending_target'):
            self.assertEqual(new[key],old[key])

    def test_late_failure_preserves_output(self):
        self.output.write_bytes(b'previous report')
        self.source.write_text(self.source.read_text()+'bad,row\n')
        self.assertEqual(self.cli(),2)
        self.assertEqual(self.output.read_bytes(),b'previous report')
        self.assertFalse(list(self.root.glob('.hepta-report-*')))

    def test_input_output_aliases_do_not_overwrite_sources(self):
        for input_path in (self.source,self.sessions):
            with self.subTest(input=input_path):
                before=input_path.read_bytes()
                self.assertEqual(self.cli(output=input_path),2)
                self.assertEqual(input_path.read_bytes(),before)
                alias=self.root/'alias'
                os.link(input_path,alias)
                self.assertEqual(self.cli(output=alias),2)
                alias.unlink()
                self.assertEqual(input_path.read_bytes(),before)

    def test_report_publish_failure_not_success(self):
        self.output.write_bytes(b'old')
        with patch('hepta_research.pipeline.os.fsync',side_effect=OSError('injected sync failure')):
            self.assertEqual(self.cli(),2)
        self.assertEqual(self.output.read_bytes(),b'old')
        self.assertFalse(list(self.root.glob('.hepta-report-*')))

    def test_shared_consumer_rejects_empty_or_unbounded_input(self):
        for value in ([],iter([]),None):
            with self.subTest(value=value),self.assertRaises(ValueError):
                evaluate_bars(value,{},capital=1000,quantity=1)
        bars,_=self.read()
        with self.assertRaises(ValueError): evaluate_bars(bars,None,capital=1000,quantity=1)


if __name__=='__main__':
    unittest.main()
