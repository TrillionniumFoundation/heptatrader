#!/usr/bin/env python3
"""Exercise the actual source or installed legacy adapter and native SDK."""
import argparse
import calendar
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

CLI = argparse.ArgumentParser()
CLI.add_argument('--importer', type=Path, required=True)
CLI.add_argument('--native', type=Path, required=True)
CLI.add_argument('--installed', action='store_true')
ARGS = CLI.parse_args()
SPEC = importlib.util.spec_from_loader('tested_legacy_importer', SourceFileLoader('tested_legacy_importer', str(ARGS.importer)))
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
DAY = '20260921'
START = calendar.timegm((2026, 9, 21, 0, 0, 0)) * 1000000
LAYOUT = 'hepta-depth82-le-a8-v1'
SYMBOL = 'TEST.FUT'
TIMES = [1, 11, 21, 22, 31, 41, 51, 52, 61, 71, 81, 82]
PRICES = [100, 101, 102, 101, 103, 99, 98, 99, 97, 100, 101, 100]


def record(second=1, volume=100, price=100, day=DAY, action=DAY, symbol=SYMBOL, millis=0):
    data = bytearray([0xA5] * 424)
    for offset, size, text in ((0, 11, 'TEST'), (11, 9, day), (20, 9, action),
                              (29, 9, f'{second // 3600:02}:{second // 60 % 60:02}:{second % 60:02}'),
                              (44, 82, symbol)):
        encoded = text.encode('ascii')
        data[offset:offset+size] = encoded + b'\0' + b'\xA5' * (size-len(encoded)-1)
    struct.pack_into('<I', data, 40, millis)
    struct.pack_into('<d', data, 288, price)
    struct.pack_into('<q', data, 328, volume)
    struct.pack_into('<d', data, 336, volume * price)
    struct.pack_into('<d', data, 344, 123.0)
    return bytes(data)


def csv_row(layout, second, volume, price, micro=0):
    # Independent fixture offsets, not values read back from the adapter table.
    settings = {
        'hepta32': (32, 0, 1, None, 2, 3, 4, 5, 7, 29),
        'immsg34': (34, 2, 3, None, 4, 5, 6, 7, 9, 31),
        'immsg35': (35, 2, 3, 4, 5, 6, 7, 8, 10, 32),
        'zs58': (58, 3, 0, None, 1, 2, 37, 38, 46, 39),
    }
    count, ins, day, action, clock, fraction, p, v, amount, oi = settings[layout]
    fields = ['0'] * count
    if layout.startswith('immsg'):
        fields[1] = 'IMMSG'
    fields[ins], fields[day] = SYMBOL, DAY
    if action is not None:
        fields[action] = DAY
    clock_value = f'{second // 3600:02}{second // 60 % 60:02}{second % 60:02}'
    fields[clock] = clock_value if layout == 'zs58' else ':'.join((clock_value[:2], clock_value[2:4], clock_value[4:]))
    fields[fraction] = str(micro if layout == 'zs58' else micro // 1000)
    fields[p], fields[v], fields[amount], fields[oi] = str(price), str(volume), str(price*volume), '123'
    return ','.join(fields) + '\n'


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hepta bundle behavior ')
        self.root = Path(self.temp.name)
        self.sessions = self.root / 'sessions.csv'
        self.sessions.write_text(f'open_us,close_us,trading_day\n{START},{START + 100000000},{DAY}\n')
        self.binary = self.root / 'source.bin'
        self.binary.write_bytes(b''.join(record(t, 100+i*10, p) for i,(t,p) in enumerate(zip(TIMES,PRICES))))
        self.output = self.root / 'result.zip'
        self.config = self.root / 'config.xml'
        self.catalog = self.root / 'instruments.xml'
        self.bindings = self.root / 'bindings.json'
        self.file_list = self.root / 'files.xml'

    def tearDown(self):
        self.temp.cleanup()

    def common(self, mode):
        args = [sys.executable, '-I', '-S', str(ARGS.importer), mode, '--sessions', str(self.sessions),
                '--instrument', SYMBOL, '--clock-zone', 'UTC', '--first-volume', 'baseline',
                '--output', str(self.output)]
        if not ARGS.installed:
            args += ['--native-executable', str(ARGS.native)]
        return args

    def binary_args(self):
        return self.common('binary') + ['--layout', LAYOUT, '--ticks', str(self.binary), '--tick-size', '0.1']

    def call(self, args, success=True):
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, success, result.stderr + result.stdout)
        if success:
            self.assertFalse(json.loads(result.stdout)['broker_authorized'])
        else:
            self.assertIn('RESEARCH_IMPORT_FAILED', result.stderr)
            self.assertNotIn('broker_authorized', result.stdout)
        return result

    def read(self):
        with zipfile.ZipFile(self.output) as archive:
            names = set(archive.namelist())
            self.assertEqual(names & {'ticks.csv','sessions.csv','manifest.json'}, {'ticks.csv','sessions.csv','manifest.json'})
            data = {name: archive.read(name) for name in names}
        meta = json.loads(data['manifest.json'])
        self.assertFalse(meta['broker_authorized'])
        self.assertEqual(meta['authority'], 'none')
        for name, digest in meta['members'].items():
            self.assertEqual(hashlib.sha256(data[name]).hexdigest(), digest)
        return data, meta

    def xml_args(self, kind=3, layouts=None, split=6, multiplier=10):
        binary = kind in (1,3)
        if layouts is None:
            layouts = [LAYOUT] * (2 if kind in (2,3) else 1) if binary else ['hepta32','zs58'][:2 if kind==2 else 1]
        self.config.write_text(f'<Root><User type="{kind}"><SimulatorServer Front="opaque:data" Instrument="opaque:catalog"/>'
                              '<Account PreBalance="999999"/></User><System><Cache Need="true" Path="/must-not-open"/></System></Root>')
        self.catalog.write_text(f'<Root><Instrument InstrumentID="{SYMBOL}" ExchangeID="TEST" ProductID="TEST" '
                                f'ProductClass="1" PriceTick="0.1" VolumeMultiple="{multiplier}"/></Root>')
        sources = []
        partitions = [list(range(len(TIMES)))] if len(layouts)==1 else [list(range(split)),list(range(split,len(TIMES)))]
        elements = []
        for ordinal, (layout, indexes) in enumerate(zip(layouts,partitions)):
            index = 1 if kind in (0,1) else (10 if ordinal==0 else 20)
            ref = 'opaque:data' if kind in (0,1) else f'../opaque/{index}'
            path = self.root / f'source-{index}.data'
            data = (b''.join(record(TIMES[i],100+i*10,PRICES[i]) for i in indexes) if binary else
                    ''.join(csv_row(layout,TIMES[i],100+i*10,PRICES[i]) for i in indexes).encode())
            path.write_bytes(data)
            sources.append(dict(index=index,reference=ref,path=str(path),sha256=hashlib.sha256(data).hexdigest(),
                                layout=layout,action_day=DAY))
            elements.append(f'<MDFile DateIndexId="{index}" FilePath="{ref}"/>')
        self.file_list.write_text('<Root>'+''.join(reversed(elements))+'</Root>')
        self.bindings.write_text(json.dumps(dict(schema='hepta.research.xml-bindings.v1',
            instrument_reference='opaque:catalog',front_reference='opaque:data',sources=list(reversed(sources)))))
        args = self.common('xml') + ['--config',str(self.config),'--instruments',str(self.catalog),
                                     '--bindings',str(self.bindings)]
        if kind in (2,3):
            args += ['--file-list',str(self.file_list)]
        return args

    def test_binary_canonical_volume_sequence_and_provenance(self):
        self.call(self.binary_args())
        data, meta = self.read()
        rows = data['ticks.csv'].decode().splitlines()[1:]
        self.assertEqual(meta['tick_count'], 12)
        for i, row in enumerate(rows):
            instrument, stamp, sequence, price, volume = row.split(',')
            self.assertEqual((instrument,int(stamp),int(sequence),float(price),int(volume)),
                             (SYMBOL, START+TIMES[i]*1000000, i+1, PRICES[i], 0 if i==0 else 10))
        self.assertEqual(meta['sources'][0]['sha256'], hashlib.sha256(self.binary.read_bytes()).hexdigest())
        self.assertNotIn('raw_fields', meta['sources'][0]['records'][0])
        self.assertNotIn('\\u00a5', data['manifest.json'].decode())
        self.assertEqual(meta['native_executable_sha256'], hashlib.sha256(ARGS.native.read_bytes()).hexdigest())

    def test_day_start_is_explicit(self):
        args=self.binary_args();args[args.index('baseline')]='day-start';self.call(args)
        data,_=self.read();self.assertEqual(int(data['ticks.csv'].decode().splitlines()[1].split(',')[-1]),100)

    def test_all_xml_file_modes_use_same_native_data(self):
        expected=None
        for mode in (0,1,2,3):
            with self.subTest(mode=mode):
                self.call(self.xml_args(mode));data,meta=self.read()
                if expected is None:expected=data['ticks.csv']
                self.assertEqual(data['ticks.csv'],expected)
                self.assertEqual(meta['ignored_runtime_settings']['pre_balance'],'999999')
                self.assertEqual(meta['instrument_metadata']['VolumeMultiple'],10)

    def test_all_split_boundaries_and_mixed_csv_profiles(self):
        self.call(self.binary_args());expected=self.read()[0]['ticks.csv']
        layouts=('hepta32','immsg34','immsg35','zs58')
        for split in range(1,len(TIMES)):
            with self.subTest(split=split):
                self.call(self.xml_args(2,[layouts[split%4],layouts[(split+1)%4]],split))
                data,meta=self.read();self.assertEqual(data['ticks.csv'],expected)
                self.assertEqual([s['index'] for s in meta['sources']],[10,20])
                self.assertEqual(meta['sources'][1]['global_first_sequence'],split+1)

    def test_microseconds_not_rounded_by_layout_adapter(self):
        args=self.xml_args(0,['zs58'])
        binding=json.loads(self.bindings.read_text());source=binding['sources'][0];path=Path(source['path'])
        path.write_text(csv_row('zs58',1,100,100,micro=123456));source['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        self.bindings.write_text(json.dumps(binding));self.call(args)
        self.assertEqual(int(self.read()[0]['ticks.csv'].decode().splitlines()[1].split(',')[1]), START+1123456)

    def test_replay_is_same_native_engine_with_explicit_accounting(self):
        args=self.xml_args(3);args += ['--replay','10000000','1','2','1','fifo',
                                    '--initial-equity','12345','--fee-per-unit','0.2']
        self.call(args);data,meta=self.read()
        ticks=self.root/'normalized.csv';ticks.write_bytes(data['ticks.csv'])
        command=[str(ARGS.native),str(ticks),str(self.sessions),SYMBOL,'10000000','1','2','1','fifo',
                 '--initial-equity','12345','--multiplier','10','--fee-per-unit','0.2']
        reference=subprocess.run(command,capture_output=True,check=True,timeout=10)
        self.assertEqual(data['replay.csv'],reference.stdout)
        summary=json.loads(data['replay.csv'].splitlines()[-1]);self.assertEqual(summary['initial_equity'],12345)
        self.assertEqual(summary['multiplier'],10);self.assertEqual(summary['fee_per_unit'],0.2)
        self.assertEqual(summary['active_orders'],0);self.assertFalse(summary['broker_authorized'])

    def test_replay_never_uses_xml_balance_or_silent_options(self):
        args=self.xml_args(3)+['--replay','10000000','1','2','1','average']
        self.call(args,False);self.assertFalse(self.output.exists())
        args += ['--initial-equity','1000','--fee-per-unit','0','--multiplier','1']
        self.call(args,False)
        self.call(self.binary_args()+['--initial-equity','1'],False)

    def test_later_source_digest_failure_preserves_existing_report(self):
        args=self.xml_args(3);self.call(args);before=self.output.read_bytes()
        binding=json.loads(self.bindings.read_text());later=max(binding['sources'],key=lambda s:s['index'])
        Path(later['path']).write_bytes(b'corrupted')
        self.call(args,False);self.assertEqual(self.output.read_bytes(),before)

    def test_native_cross_file_volume_reset_is_not_rebaselined(self):
        args=self.xml_args(3);binding=json.loads(self.bindings.read_text());later=max(binding['sources'],key=lambda s:s['index'])
        path=Path(later['path']);path.write_bytes(record(51,1,98));later['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        self.bindings.write_text(json.dumps(binding));self.output.write_bytes(b'old-report')
        self.call(args,False);self.assertEqual(self.output.read_bytes(),b'old-report')

    def test_native_time_reversal_and_session_mismatch_fail(self):
        for bad in (record(120,100,100),record(2,100,100)+record(1,110,101),record(1,100,100,day='20260922')):
            with self.subTest(data_size=len(bad)):
                self.binary.write_bytes(bad);self.call(self.binary_args(),False);self.assertFalse(self.output.exists())

    def test_exact_grid_rejection_and_signed_profile_preserved(self):
        for value in (0.30000000000000004,float('nan'),float('inf'),-1.0,0.0,1e18):
            with self.subTest(value=value):
                self.binary.write_bytes(record(price=value));self.call(self.binary_args(),False)
        self.binary.write_bytes(record(price=0.3));self.call(self.binary_args())
        self.assertAlmostEqual(float(self.read()[0]['ticks.csv'].decode().splitlines()[1].split(',')[3]),0.3)

    def test_every_partial_record_length_rejected(self):
        raw=record()
        for size in range(424):
            with self.subTest(size=size),self.assertRaises(ValueError):
                list(MODULE._binary_records(raw[:size],MODULE.number('0.1',positive=True)))

    def test_binary_string_counter_fraction_and_foreign_identity(self):
        wrong=bytearray(record());wrong[44:126]=b'A'*82
        for raw in (bytes(wrong),record(volume=-1),record(millis=1000),record(symbol='OTHER')):
            with self.subTest(size=len(raw)):
                self.binary.write_bytes(raw);self.call(self.binary_args(),False)

    def test_action_day_never_inferred_from_trading_day(self):
        self.binary.write_bytes(record(action=''))
        self.call(self.binary_args(),False)
        self.call(self.binary_args()+['--action-day',DAY]);self.assertEqual(self.read()[1]['tick_count'],1)
        self.binary.write_bytes(record(action=DAY))
        self.call(self.binary_args()+['--action-day','20260920'],False)

    def test_action_date_maps_exact_coverage(self):
        self.binary.write_bytes(record(action=''))
        dates=self.root/'dates.csv';dates.write_text(f'row,action_day\n1,{DAY}\n')
        args=self.binary_args()+['--action-days',str(dates)];self.call(args)
        for text in (f'row,action_day\n2,{DAY}\n',f'row,action_day\n1,{DAY}\n2,{DAY}\n'):
            dates.write_text(text);self.call(args,False)

    def test_dst_ambiguity_and_gap_do_not_choose_an_offset(self):
        for day, second in (('20261101',5400),('20260308',9000)):
            self.binary.write_bytes(record(second,day=day,action=day))
            args=self.binary_args();args[args.index('UTC')]='America/New_York';self.call(args,False)

    def test_total_row_budget_crosses_files(self):
        args=self.xml_args(3);self.call(args+['--max-ticks','11'],False)
        self.assertFalse(self.output.exists());self.call(args+['--max-ticks','12'])

    def test_xml_entities_namespaces_duplicates_and_external_modes(self):
        args=self.xml_args(3)
        for text in ('<!DOCTYPE X [<!ENTITY e SYSTEM "file:///must-not-read">]><X/>',
                     '<x:Root xmlns:x="urn:test"/>','<Root><User type="1"/><User type="1"/></Root>',
                     '<Root><?run nope?></Root>','<Root><![CDATA[secret]]></Root>'):
            self.config.write_text(text);self.call(args,False)
        for mode in (4,5,6):
            self.config.write_text(f'<Root><User type="{mode}"><SimulatorServer Front="never-open" Instrument="never-open"/></User></Root>')
            self.call(args,False)

    def test_duplicate_binding_keys_rejected(self):
        args=self.xml_args(3);text=self.bindings.read_text();self.bindings.write_text(text.replace('"schema":','"schema":"bad","schema":',1))
        self.call(args,False)

    def test_fifo_symlink_and_output_alias_rejected(self):
        original=self.binary.read_bytes();self.binary.unlink();os.mkfifo(self.binary)
        self.call(self.binary_args(),False)
        self.binary.unlink();real=self.root/'real.bin';real.write_bytes(original);self.binary.symlink_to(real)
        self.call(self.binary_args(),False)
        self.binary.unlink();self.binary.write_bytes(original)
        self.output.symlink_to(self.binary);self.call(self.binary_args(),False);self.assertEqual(self.binary.read_bytes(),original)
        self.output.unlink();os.link(self.binary,self.output);self.call(self.binary_args(),False);self.assertEqual(self.binary.read_bytes(),original)

    def test_source_mutation_during_capture_detected(self):
        from unittest import mock
        original=MODULE._identity;calls=0
        def altered(value):
            nonlocal calls
            calls+=1
            result=original(value)
            return (*result[:-1],result[-1]+1) if calls==2 else result
        with mock.patch.object(MODULE,'_identity',side_effect=altered):
            with self.assertRaises(ValueError):MODULE._capture(self.binary,64*1024*1024)

    def test_publication_failure_preserves_previous_bundle(self):
        from unittest import mock
        self.output.write_bytes(b'previous')
        with mock.patch.object(MODULE.os,'replace',side_effect=OSError('injected')):
            with self.assertRaises(OSError):MODULE._publish(self.output,{'ticks.csv':b'test'},[self.binary])
        self.assertEqual(self.output.read_bytes(),b'previous')
        self.assertFalse(list(self.root.glob('.hepta-import-*')))

    def test_session_header_adapter_has_same_meaning(self):
        self.call(self.binary_args());expected=self.read()[0]['ticks.csv']
        self.sessions.write_text(self.sessions.read_text().replace('open_us,close_us','begin_us,end_us'))
        self.call(self.binary_args());self.assertEqual(self.read()[0]['ticks.csv'],expected)

    def test_reproducible_bundle_bytes(self):
        self.call(self.binary_args());first=self.output.read_bytes()
        self.call(self.binary_args());self.assertEqual(self.output.read_bytes(),first)

    def test_gb18030_is_explicit_and_preserves_inert_metadata(self):
        args = self.xml_args(3)
        text = self.catalog.read_text().replace('ProductClass="1"', 'InstrumentName="合约样本" ProductClass="1"')
        self.catalog.write_bytes(text.encode('gb18030'))
        self.call(args, False)
        self.call(args + ['--encoding', 'gb18030'])
        self.assertEqual(self.read()[1]['instrument_metadata']['InstrumentName'], '合约样本')

    def test_day_boundary_is_explicit_not_file_reset(self):
        args = self.xml_args(3)
        binding = json.loads(self.bindings.read_text())
        ordered = sorted(binding['sources'], key=lambda source: source['index'])
        for source, day, count in zip(ordered, (DAY, '20260922'), (100, 5)):
            raw = record(1, count, 100, day=day, action=day)
            Path(source['path']).write_bytes(raw)
            source['sha256'] = hashlib.sha256(raw).hexdigest()
            source['action_day'] = day
        self.bindings.write_text(json.dumps(binding))
        with self.sessions.open('a') as stream:
            stream.write(f'{START+86400000000},{START+86400000000+100000000},20260922\n')
        self.call(args)
        rows = self.read()[0]['ticks.csv'].decode().splitlines()[1:]
        self.assertEqual(len(rows), 2)
        self.assertEqual([int(row.split(',')[4]) for row in rows], [0, 0])
        self.assertEqual([int(row.split(',')[2]) for row in rows], [1, 2])

    def test_shared_byte_capture_budget_is_not_reset_per_file(self):
        path = self.root / 'bounded.bin'
        path.write_bytes(b'ABCDE')
        budget = MODULE.CaptureBudget(12)
        self.assertEqual(budget.read(path)[0], b'ABCDE')
        self.assertEqual(budget.read(path)[0], b'ABCDE')
        self.assertEqual(budget.remaining, 2)
        with self.assertRaises(ValueError):
            budget.read(path)
        self.assertEqual(budget.remaining, 2)

    def test_nonunit_binary_multiplier_requires_explicit_input(self):
        args=self.binary_args()+['--replay','10000000','1','2','1','average','--initial-equity','1000','--fee-per-unit','0']
        self.call(args,False);self.call(args+['--multiplier','10'])


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]],verbosity=2)
