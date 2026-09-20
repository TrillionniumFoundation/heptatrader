"""Behavior/failure tests against actual normalizers and the compiled bar builder."""
from contextlib import redirect_stderr
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hepta_research import legacy_xml as xml
from hepta_research.legacy_ticks import tick_report
from xml_fixture import START, catalog, cli_args, csv_row, fixture, update_digest


class XmlFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bundle = fixture(self.directory)

    def normalize(self, **options):
        params = dict(instrument='SYNTH', clock_zone='UTC', file_list_path=self.bundle['file_list_path'])
        params.update(options)
        return xml.normalize_xml(*(self.bundle[k] for k in ('config','instruments','bindings','sessions')), **params)

    def edit_bindings(self, change):
        path = self.bundle['bindings']
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))

    def document(self, text, *, encoding='utf-8'):
        p = self.directory/'document.xml'
        p.write_bytes(text.encode(encoding))
        return p


class LegacyXmlTests(XmlFixture):
    def test_catalog_fields_are_exact_and_missing_optional_not_invented(self):
        p = self.document(catalog('InstrumentName="合成测试" OpenDate="20240101" ExpireDate="20241231" '
                                  'MinLimitOrderVolume="1" MaxLimitOrderVolume="10" Currency="1"'))
        values, digest = xml.read_instruments(p)
        self.assertEqual(values['SYNTH']['PriceTick'], Decimal('0.5'))
        self.assertEqual(values['SYNTH']['VolumeMultiple'], 3)
        self.assertEqual(values['SYNTH']['InstrumentName'], '合成测试')
        self.assertNotIn('IsTrading', values['SYNTH'])
        self.assertEqual(digest, hashlib.sha256(p.read_bytes()).hexdigest())

    def test_utf8_bom_and_explicit_gb18030(self):
        for enc, bom in (('utf-8','\ufeff'), ('gb18030','')):
            p = self.document(bom+f'<?xml version="1.0" encoding="{enc}"?>'+catalog('InstrumentName="合成"'), encoding=enc)
            self.assertEqual(xml.read_instruments(p, encoding=enc)[0]['SYNTH']['InstrumentName'], '合成')

    def test_encoding_mismatch_and_wrong_decoding_reject(self):
        p = self.document('<?xml version="1.0" encoding="gb18030"?>'+catalog())
        with self.assertRaises(ValueError):
            xml.read_instruments(p)
        p = self.document(catalog('InstrumentName="合成"'), encoding='gb18030')
        with self.assertRaises(ValueError):
            xml.read_instruments(p)

    def test_dtd_entity_pi_cdata_and_namespace_reject(self):
        variants = ['<!DOCTYPE Catalog [<!ENTITY x "boom">]>'+catalog(),
                    '<!DOCTYPE Catalog SYSTEM "file:///no-access">'+catalog(),
                    '<?run arbitrary?>'+catalog(), '<Catalog><![CDATA[hidden]]></Catalog>',
                    '<Catalog xmlns="urn:x"/>', '<x:Catalog/>']
        for text in variants:
            with self.subTest(text=text), self.assertRaises(ValueError):
                xml.read_instruments(self.document(text))

    def test_predefined_entities_and_comments_are_inert(self):
        p = self.document('<!-- example -->'+catalog('InstrumentName="A &amp; B"'))
        self.assertEqual(xml.read_instruments(p)[0]['SYNTH']['InstrumentName'], 'A & B')

    def test_duplicate_attribute_malformed_text_and_depth_reject(self):
        for text in ('<R><Instrument InstrumentID="A" InstrumentID="B"/></R>',
                     '<R>not attributes</R>', '<R><A><B><C><D/></C></B></A></R>', '<R>'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                xml.read_instruments(self.document(text))

    def test_xml_size_element_and_attribute_bounds(self):
        p = self.document(catalog())
        with patch.object(xml,'MAX_XML_BYTES',10), self.assertRaises(ValueError):
            xml.read_instruments(p)
        with patch.object(xml,'MAX_XML_ELEMENTS',1), self.assertRaises(ValueError):
            xml.read_instruments(p)
        for extra in ('InstrumentName="'+'x'*4097+'"', 'Password="not-consumed"'):
            with self.subTest(extra=extra[:20]), self.assertRaises(ValueError):
                xml.read_instruments(self.document(catalog(extra)))

    def test_catalog_invalid_values_reject(self):
        for key, value in (('PriceTick','0'),('PriceTick','NaN'),('PriceTick','1e-19'),
                           ('VolumeMultiple','1.5'),('VolumeMultiple','0'),('ProductClass','12')):
            original = {'PriceTick':'0.5','VolumeMultiple':'3','ProductClass':'1'}[key]
            text = catalog().replace(f'{key}="{original}"', f'{key}="{value}"')
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                xml.read_instruments(self.document(text))
        for extra in ('OpenDate="20240230"','IsTrading="2"','DeliveryMonth="13"',
                      'MinLimitOrderVolume="2" MaxLimitOrderVolume="1"',
                      'OpenDate="20240102" ExpireDate="20240101"', 'Currency="CNY"'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                xml.read_instruments(self.document(catalog(extra)))

    def test_empty_duplicate_or_incomplete_catalog_reject(self):
        for text in ('<Catalog/>', catalog().replace('</Catalog>','')+catalog().split('<Catalog>')[1],
                     catalog().replace('PriceTick="0.5"','')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                xml.read_instruments(self.document(text))

    def test_configuration_settings_are_retained_but_not_applied(self):
        config, _ = xml.read_config(self.bundle['config'])
        self.assertEqual(config['type'],2)
        self.assertEqual(config['ignored_runtime_settings']['pre_balance'], Decimal('99999'))
        self.assertTrue(config['ignored_runtime_settings']['cache']['need'])
        self.assertNotIn('Path',config['ignored_runtime_settings']['cache'])
        self.assertEqual(config['subscriptions'], ['SYNTH'])

    def test_missing_duplicate_unknown_configuration_reject(self):
        original = self.bundle['config'].read_text()
        cases = [original.replace(' type="2"',''), original.replace('</User>','</User><User type="2"/>'),
                 original.replace('PreBalance="99999"','PreBalance="99999" Password="secret"'),
                 original.replace('Need="true"','Need="maybe"'),
                 original.replace('<Instrument ID="SYNTH"/>','<Instrument ID="SYNTH"/><Instrument ID="SYNTH"/>')]
        for text in cases:
            with self.subTest(text=text[:40]), self.assertRaises(ValueError):
                xml.read_config(self.document(text))

    def test_network_database_custom_modes_do_not_execute(self):
        original=self.bundle['config'].read_text()
        for mode in (4,5,6):
            self.bundle['config'].write_text(original.replace('type="2"',f'type="{mode}"'))
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError,'database, network'):
                self.normalize()

    def test_file_list_numeric_order_not_calendar_inference(self):
        records,_=xml.read_file_list(self.bundle['file_list_path'])
        self.assertEqual([r[0] for r in records], [20,90])
        for text in ('<R/>','<R><MDFile DateIndexId="1" FilePath="a"/><MDFile DateIndexId="01" FilePath="b"/></R>',
                     '<R><MDFile DateIndexId="garbage" FilePath="a"/></R>'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                xml.read_file_list(self.document(text))

    def test_binding_duplicate_json_keys_reject(self):
        p=self.directory/'bad.json'
        p.write_text('{"schema":"a","schema":"b"}')
        with self.assertRaises(ValueError):
            xml.read_bindings(p)

    def test_binding_types_unknown_options_and_relative_paths_reject(self):
        original=self.bundle['bindings'].read_text()
        changes=[('index',True),('path','relative.csv'),('sha256','A'*64),('layout','guess'),
                 ('has_header','true'),('broker_enabled',True),('action_day',20240102)]
        for key,value in changes:
            self.bundle['bindings'].write_text(original)
            self.edit_bindings(lambda d:d['sources'][0].update({key:value}))
            with self.subTest(key=key), self.assertRaises(ValueError):
                xml.read_bindings(self.bundle['bindings'])

    def test_reference_mismatch_missing_and_extra_bindings_reject(self):
        original=self.bundle['bindings'].read_text()
        actions=[lambda d:d.update(front_reference='other.xml'),
                 lambda d:d.update(instrument_reference='other.xml'),
                 lambda d:d['sources'][0].update(reference='other'),
                 lambda d:d['sources'].pop(),
                 lambda d:d['sources'].append(dict(d['sources'][0],index=200))]
        for action in actions:
            self.bundle['bindings'].write_text(original)
            self.edit_bindings(action)
            with self.assertRaises(ValueError):
                self.normalize()

    def test_file_list_is_required_only_for_list_modes(self):
        with self.assertRaises(ValueError):
            self.normalize(file_list_path=None)
        self.bundle=fixture(self.directory,single=True)
        with self.assertRaises(ValueError):
            self.normalize(file_list_path=self.directory/'files.xml')

    def test_source_type_mismatch_reject(self):
        self.edit_bindings(lambda d:d['sources'][0].update(layout=xml.BINARY_LAYOUT))
        with self.assertRaises(ValueError):
            self.normalize()

    def test_unsubscribed_and_nonfuture_instruments_reject(self):
        with self.assertRaises(ValueError):
            self.normalize(instrument='OTHER')
        self.bundle['instruments'].write_text(catalog().replace('ProductClass="1"','ProductClass="2"'))
        with self.assertRaises(ValueError):
            self.normalize()
        self.bundle['instruments'].write_text(catalog())
        self.bundle['config'].write_text(self.bundle['config'].read_text().replace('ID="SYNTH"','ID="OTHER"'))
        with self.assertRaises(ValueError):
            self.normalize()

    def test_source_hash_mismatch_rejects_before_normalizing(self):
        self.bundle['source_paths'][0].write_bytes(b'changed')
        with patch.object(xml,'normalize_ticks') as normalizer, self.assertRaisesRegex(ValueError,'digest mismatch'):
            self.normalize()
        normalizer.assert_not_called()

    def test_leaf_symlink_fifo_and_directory_rejected(self):
        original=self.bundle['source_paths'][0]
        for kind in ('link','fifo','directory'):
            p=self.directory/kind
            if kind=='link': p.symlink_to(original)
            elif kind=='fifo': os.mkfifo(p)
            else: p.mkdir()
            with self.subTest(kind=kind), self.assertRaises((ValueError,OSError)):
                xml._capture(p,100000)

    def test_observed_file_change_rejects(self):
        real=xml.os.fstat
        calls=[0]
        def changed(fd):
            value=real(fd)
            calls[0]+=1
            if calls[0]==3:
                class Stat:
                    st_dev=value.st_dev; st_ino=value.st_ino; st_size=value.st_size
                    st_mtime_ns=value.st_mtime_ns+1; st_ctime_ns=value.st_ctime_ns
                return Stat()
            return value
        with patch.object(xml.os,'fstat',side_effect=changed), self.assertRaisesRegex(ValueError,'changed'):
            xml._capture(self.bundle['config'],100000)

    def test_aggregate_byte_and_tick_bounds(self):
        with patch.object(xml,'MAX_SOURCE_BYTES',100), self.assertRaises(ValueError):
            self.normalize()
        for cap in (0,True,5,100001):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                self.normalize(max_ticks=cap)

    def test_cross_file_volume_and_time_regression_reject(self):
        p=self.bundle['source_paths'][1]
        original=p.read_bytes()
        for text in (csv_row(3,volume=1)+csv_row(4)+csv_row(5),csv_row(0)+csv_row(4)+csv_row(5)):
            p.write_text(text)
            update_digest(self.bundle,1)
            with self.assertRaisesRegex(ValueError,'cross-file'):
                self.normalize()
        p.write_bytes(original)

    def test_global_sequences_and_original_file_provenance(self):
        normalized=self.normalize()
        self.assertEqual([int(row.split(',')[3]) for row in normalized.ticks_csv.splitlines()[1:]],list(range(1,7)))
        self.assertEqual([s['index'] for s in normalized.metadata['sources']],[20,90])
        self.assertEqual([(s['global_first_sequence'],s['global_last_sequence']) for s in normalized.metadata['sources']],[(1,3),(4,6)])
        self.assertEqual(normalized.metadata['sources'][1]['input']['source_fields'][0]['row'],1)
        self.assertEqual(normalized.metadata['normalized_ticks_sha256'],hashlib.sha256(normalized.ticks_csv.encode()).hexdigest())
        self.assertNotIn(str(self.directory),str(normalized.metadata))

    def test_every_file_split_has_identical_normalized_stream(self):
        expected=self.normalize().ticks_csv
        for cut in range(1,6):
            self.bundle=fixture(self.directory,cut=cut)
            self.assertEqual(self.normalize().ticks_csv,expected)

    def test_external_actual_dates_are_explicit_and_hashed(self):
        for i,p in enumerate(self.bundle['source_paths']):
            p.write_text(''.join(csv_row(k,layout='hepta32') for k in range(i*3,(i+1)*3)))
            update_digest(self.bundle,i)
        self.edit_bindings(lambda d:[s.update(layout='hepta32') for s in d['sources']])
        with self.assertRaises(ValueError):
            self.normalize()
        dates=self.directory/'dates.csv'
        dates.write_text('row,action_day\n1,20240102\n2,20240102\n3,20240102\n')
        digest=hashlib.sha256(dates.read_bytes()).hexdigest()
        self.edit_bindings(lambda d:[s.update(action_days_path=str(dates),action_days_sha256=digest) for s in d['sources']])
        self.assertEqual(self.normalize().metadata['tick_count'],6)
        dates.write_text('changed')
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            self.normalize()

    def test_declared_paths_are_never_followed(self):
        config=self.bundle['config'].read_text().replace('old-list.xml','https://example.invalid/private')
        self.bundle['config'].write_text(config)
        self.edit_bindings(lambda d:d.update(front_reference='https://example.invalid/private'))
        self.assertEqual(self.normalize().metadata['tick_count'],6)


class LegacyXmlCompiledTests(XmlFixture):
    # Inherit fixtures/helpers, not the parser suite a second time.
    def report(self, **options):
        executable=os.environ.get('HEPTA_RESEARCH_BARS')
        self.assertTrue(executable,'the actual compiled converter is required; no success-shaped skip')
        params=dict(instrument='SYNTH',clock_zone='UTC',file_list_path=self.bundle['file_list_path'],
                    bars_executable=Path(executable),period_us=60000000,first_volume='include',
                    capital='1000',quantity='2',fast=1,slow=2,slippage='0.5',fee_per_unit='0.25')
        params.update(options)
        return xml.xml_report(*(self.bundle[k] for k in ('config','instruments','bindings','sessions')),**params)

    def test_actual_replay_hand_computed_fills_fees_and_equity(self):
        result=self.report()
        self.assertEqual([f['delta'] for f in result['fills']],[Decimal(2),Decimal(-4)])
        self.assertEqual([f['price'] for f in result['fills']],[Decimal('12.5'),Decimal('7.5')])
        self.assertEqual(result['fees'],Decimal('1.50'))
        self.assertEqual(result['equity'][-1]['value'],Decimal('971.50'))
        self.assertEqual(result['position'],Decimal(-2))
        self.assertIsNone(result['pending_target'])
        self.assertFalse(result['equity'][-1]['complete'])
        self.assertEqual(result['assumptions']['capital'],Decimal(1000))
        self.assertEqual(result['assumptions']['multiplier'],Decimal(3))
        self.assertFalse(result['assumptions']['broker_authorized'])

    def test_xml_pipeline_agrees_with_direct_existing_tick_pipeline(self):
        combined=self.directory/'combined.csv'
        combined.write_bytes(b''.join(p.read_bytes() for p in self.bundle['source_paths']))
        direct=tick_report(combined,self.bundle['sessions'],bars_executable=Path(os.environ['HEPTA_RESEARCH_BARS']),
            period_us=60000000,first_volume='include',layout='immsg35',instrument='SYNTH',clock_zone='UTC',
            tick_size='0.5',capital='1000',quantity='2',fast=1,slow=2,slippage='0.5',fee_per_unit='0.25',multiplier=3)
        actual=self.report()
        self.assertEqual(actual['input']['bars_sha256'],direct['input']['bars_sha256'])
        for key in ('fills','equity','metrics','position','fees','pending_target','strategy','assumptions'):
            self.assertEqual(actual[key],direct[key],key)

    def test_binary_and_csv_all_four_modes_agree(self):
        expected=self.report()
        for binary,single in ((False,True),(True,False),(True,True)):
            self.bundle=fixture(self.directory,binary=binary,single=single)
            actual=self.report()
            for key in ('fills','equity','metrics'):
                self.assertEqual(actual[key],expected[key])
            self.assertEqual(actual['input']['bars_sha256'],expected['input']['bars_sha256'])

    def test_no_per_file_strategy_or_account_reset(self):
        expected=self.report()
        for cut in range(1,6):
            self.bundle=fixture(self.directory,cut=cut)
            actual=self.report()
            self.assertEqual(actual['fills'],expected['fills'])
            self.assertEqual(actual['equity'],expected['equity'])

    def test_explicit_catalog_multiplier_cannot_be_overridden(self):
        with self.assertRaisesRegex(ValueError,'multiplier'):
            self.report(multiplier=1)

    def test_failed_converter_never_uses_fallback(self):
        bad=self.directory/'failed';bad.write_text('#!/bin/sh\nexit 7\n');bad.chmod(0o700)
        with self.assertRaisesRegex(ValueError,'exit 7'):
            self.report(bars_executable=bad)

    def test_cli_success_and_late_failure_preserves_output(self):
        output=self.directory/'report.json'
        args=cli_args(self.bundle,output)+['--bars-executable',os.environ['HEPTA_RESEARCH_BARS']]
        self.assertEqual(xml.main(args),0)
        before=output.read_bytes()
        self.bundle['source_paths'][1].write_text('malformed')
        update_digest(self.bundle,1)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(xml.main(args),2)
        self.assertEqual(output.read_bytes(),before)
        self.assertFalse(list(self.directory.glob('.hepta-report-*')))

    def test_cli_rejects_all_input_and_hardlink_output_aliases(self):
        source=self.bundle['source_paths'][0]
        alias=self.directory/'alias.json';os.link(source,alias)
        for output in (source,self.bundle['config'],self.bundle['bindings'],alias):
            before=output.read_bytes()
            with redirect_stderr(io.StringIO()):
                code=xml.main(cli_args(self.bundle,output)+['--bars-executable',os.environ['HEPTA_RESEARCH_BARS']])
            self.assertEqual(code,2)
            self.assertEqual(output.read_bytes(),before)

    def test_cli_binding_change_between_checks_prevents_publication(self):
        original=xml.read_bindings
        calls=[0]
        def changed(path):
            value,digest=original(path);calls[0]+=1
            return value,('0'*64 if calls[0]==1 else digest)
        output=self.directory/'report.json';output.write_text('preserve')
        with patch.object(xml,'read_bindings',side_effect=changed),redirect_stderr(io.StringIO()):
            result=xml.main(cli_args(self.bundle,output)+['--bars-executable',os.environ['HEPTA_RESEARCH_BARS']])
        self.assertEqual(result,2)
        self.assertEqual(output.read_text(),'preserve')


if __name__=='__main__':
    unittest.main()
