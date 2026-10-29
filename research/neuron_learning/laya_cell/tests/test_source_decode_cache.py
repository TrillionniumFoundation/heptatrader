"""Exact original-byte parity and invalidation for pre-slot decoder reuse."""
import copy
import mmap
import os
import time
import unittest
from unittest.mock import patch

from laya_cell import shadow_source as source
from laya_cell.source_read_budget import (
    SourceReadDeadline, check_source_budget, source_read_budget)
from laya_cell.tests import test_shadow_source as fixture_module

T,quote=fixture_module.T,fixture_module.quote


class SourceDecodeCacheTests(unittest.TestCase):
    def setUp(self):
        fixture=fixture_module.ShadowEvidenceTests('test_valid_source_chain_and_numeric_mid')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.fixture=fixture;self.path=fixture.path
        self.entries=[('HEADER',fixture.header,T),('QUOTE',quote(),T+2)]
        fixture.write(self.entries)
        self.cache=source.SourceDecodeCache()

    def read(self):
        return source.read_source(self.path,decode_cache=self.cache)

    def append(self,kind,data,at):
        last=source.strict(self.path.read_bytes().splitlines()[-1])
        row={'schema':source.SCHEMA,'sequence':last['sequence']+1,
             'previous':last['sha256'],'kind':kind,'data':data,'at_ms':at}
        row['sha256']=source.digest(row)
        with self.path.open('ab') as stream:stream.write(source.canonical(row)+b'\n')

    def test_hit_rehashes_every_original_byte_without_redecoding(self):
        expected=source.read_source(self.path)
        self.assertEqual(self.read(),expected)
        parsed=self.cache.parsed_records
        for _ in range(2):self.assertEqual(self.read(),expected)
        self.assertEqual(self.cache.parsed_records,parsed)
        self.assertEqual(self.cache.rehashed_bytes,2*expected[2]['bytes'])
        self.assertEqual(self.cache.hits,2)

    def test_appended_quotes_match_full_parser_and_only_parse_new_records(self):
        self.read();parsed=self.cache.parsed_records
        q={**quote(),'observed_at_ms':T+1000,'stale_after_ms':T+6000}
        self.append('QUOTE',q,T+1002)
        self.assertEqual(self.read(),source.read_source(self.path))
        self.assertEqual(self.cache.parsed_records-parsed,1)
        self.append('END',{},T+1003)
        self.assertEqual(self.read(),source.read_source(self.path))
        self.append('FAILURE',{},T+1004)
        with self.assertRaisesRegex(ValueError,'AFTER_END'):self.read()

    def test_uncommitted_tail_is_not_cached_as_a_record(self):
        self.read();parsed=self.cache.parsed_records
        last=source.strict(self.path.read_bytes().splitlines()[-1])
        row={'schema':source.SCHEMA,'sequence':last['sequence']+1,
             'previous':last['sha256'],'kind':'FAILURE','data':{},'at_ms':T+3}
        row['sha256']=source.digest(row);raw=source.canonical(row)
        with self.path.open('ab') as stream:stream.write(raw[:30])
        self.assertEqual(self.read(),source.read_source(self.path))
        self.assertEqual(self.cache.parsed_records,parsed)
        with self.path.open('ab') as stream:stream.write(raw[30:]+b'\n')
        self.assertEqual(self.read(),source.read_source(self.path))
        self.assertEqual(self.cache.parsed_records,parsed+1)

    def test_existing_dirty_mmap_cannot_escape_fresh_prefix_hash(self):
        raw=self.path.read_bytes();offset=raw.index(b'1.12500')
        with self.path.open('r+b') as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_WRITE) as mapping:
            mapping[offset:offset+7]=b'1.12500'
            self.read();mapping[offset:offset+7]=b'1.12499'
            with self.assertRaisesRegex(ValueError,'PREFIX_CHANGED'):self.read()
        self.assertEqual(self.cache.counters()['entries'],0)

    def test_same_size_rewrite_and_restored_mtime_do_not_reuse(self):
        self.read();before=self.path.stat()
        changed=copy.deepcopy(self.entries);changed[1][1]['bid']='1.12499'
        self.fixture.write(changed)
        os.utime(self.path,ns=(before.st_atime_ns,before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'PREFIX_CHANGED'):self.read()

    def test_inode_replacement_and_truncation_require_fresh_parse(self):
        self.read();old_misses=self.cache.misses
        replacement=self.path.with_name('replacement')
        replacement.write_bytes(self.path.read_bytes());replacement.chmod(0o600)
        os.replace(replacement,self.path)
        self.assertEqual(self.read(),source.read_source(self.path))
        self.assertEqual(self.cache.misses,old_misses+1)
        self.fixture.write(self.entries[:1])
        self.assertEqual(self.read(),source.read_source(self.path))
        self.assertEqual(self.cache.misses,old_misses+2)

    def test_returned_data_cannot_poison_retained_decoder_state(self):
        header,rows,_=self.read();header['source']='FORGED';rows[0]['bid']=1
        self.assertEqual(self.read(),source.read_source(self.path))

    def test_clock_regression_does_not_reuse_future_records(self):
        self.read()
        with patch.object(source.time,'time',return_value=(T+1)/1000):
            with self.assertRaisesRegex(ValueError,'SHADOW_SOURCE_CLOCK'):self.read()

    def test_record_and_byte_limits_evict_instead_of_growing(self):
        self.cache=source.SourceDecodeCache(max_entries=1,max_source_bytes=100,max_records=1)
        self.assertEqual(self.read(),source.read_source(self.path))
        self.assertEqual(self.cache.counters()['entries'],0)

    def test_deadline_stops_prefix_hash_without_accepting_partial_state(self):
        expected=self.read();old=self.cache.parsed_records
        calls=0
        def deadline():
            nonlocal calls
            calls+=1
            if calls==3:raise SourceReadDeadline('INPUT_SLOT_EXPIRED:DURING_QUOTE_READ')
        with patch.object(source,'check_source_budget',side_effect=deadline):
            with self.assertRaises(SourceReadDeadline):self.read()
        self.assertEqual(self.cache.parsed_records,old)
        self.assertEqual(self.read(),expected)


class SourceReadBudgetTests(unittest.TestCase):
    def test_expired_deadline_does_not_enter_read_and_scope_is_restored(self):
        with self.assertRaises(SourceReadDeadline):
            with source_read_budget(int(time.time()*1000)-1):self.fail('entered expired read')
        check_source_budget()

    def test_wall_clock_regression_cannot_extend_monotonic_budget(self):
        with patch('laya_cell.source_read_budget.time.time',return_value=10),\
             patch('laya_cell.source_read_budget.time.monotonic',side_effect=[100,100,102]):
            with self.assertRaises(SourceReadDeadline):
                with source_read_budget(11000):pass

    def test_nested_deadline_cannot_extend_parent(self):
        with patch('laya_cell.source_read_budget.time.time',return_value=10),\
             patch('laya_cell.source_read_budget.time.monotonic',side_effect=[100,100,100,102]):
            with self.assertRaises(SourceReadDeadline):
                with source_read_budget(11000):
                    with source_read_budget(999999):pass


if __name__=='__main__':unittest.main()
