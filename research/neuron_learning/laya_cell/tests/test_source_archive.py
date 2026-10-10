"""No Broker needed. Archive integrity, growth, deletion and reordering tests."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from laya_cell.shadow_source import canonical, digest
from laya_cell.source_archive import seal, inventory, directory, PAGE_SIZE
from laya_cell.resident_data import read_quotes
from laya_cell.dataset import verify_source_prefixes
from laya_cell.integrity import ContractError

class SourceArchiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.feed = self.root / 'input'
        self.feed.mkdir(mode=0o700)
        self.period = self.root / 'period.json'
        self.period.write_text('{}')
        self.period.chmod(0o600)
        from laya_cell.integrity import sha_file
        self.period_sha = sha_file(self.period)
        self.c = {'feed_directory':str(self.feed),'period_path':str(self.period),
                  'period_sha256':self.period_sha,'completed_history_sources':[]}
        self.T = int(time.time()*1000)-12000000
        for i in range(165):
            self.make_segment(i)
        self.make_segment(165,ended=False)

    def make_segment(self,i,ended=True,price='1.125'):
        p = self.feed / f'segment-{i:08d}'
        p.mkdir(mode=0o700)
        path = p / 'quotes.jsonl'
        at = self.T + i*30000
        header={'source':'IB_TOOL_GATEWAY','instrument':'EUR.USD',
                'execution_domain':'PAPER','period_sha256':self.period_sha,
                'write_operations_allowed':[],'broker_orders_sent':0}
        value={'instrument':'EUR.USD','source':'IB','authoritative':True,
               'stale':False,'bid':price,'ask':'1.126',
               'observed_at_ms':at+100,'stale_after_ms':at+5000,
               'subscription_id':'fixture','subscription_state':'active'}
        entries=[('HEADER',header,at),('QUOTE',value,at+200)]
        if ended:entries.append(('END',{},at+201))
        last='0'*64
        rows=[]
        for seq,(kind,data,tm) in enumerate(entries):
            body={'schema':'heptatrader.laya-shadow-quotes.v1','sequence':seq,
                  'previous':last,'at_ms':tm,'kind':kind,'data':data}
            row={**body,'sha256':digest(body)}
            last=row['sha256']
            rows.append(canonical(row))
        path.write_bytes(b'\n'.join(rows)+b'\n')
        path.chmod(0o600)
        return path

    def archive(self):
        result=seal(self.c,8)
        self.assertEqual(result['pages_added'],5)
        self.assertEqual(result['sealed_segments'],160)
        self.assertEqual(result['unsealed_segments'],6)
        self.assertEqual(result['source_files_removed'],0)
        return result

    def test_more_than_128_fails_before_archival_then_reads_all_originals(self):
        with self.assertRaisesRegex(ContractError,'SOURCE_UNSEALED_SEGMENT_BOUND'):
            read_quotes(self.c,int(time.time()*1000))
        receipt=self.archive()
        values,pins=read_quotes(self.c,int(time.time()*1000))
        self.assertEqual(len(values),166)
        self.assertEqual(len(pins),11)
        self.assertEqual(len(list(self.feed.glob('segment-*/quotes.jsonl'))),166)
        self.assertEqual(receipt['total_pages'],5)
        verify_source_prefixes({'source_prefixes':pins})
        self.assertEqual(seal(self.c,8)['pages_added'],0)

    def test_source_byte_modified_after_archival_rejected(self):
        self.archive()
        p=self.feed/'segment-00000000'/'quotes.jsonl'
        p.write_bytes(p.read_bytes().replace(b'1.125',b'1.124'))
        with self.assertRaisesRegex(ContractError,'SOURCE_PREFIX_HASH_CHANGED'):
            read_quotes(self.c,int(time.time()*1000))

    def test_closed_source_appended_after_archival_rejected(self):
        self.archive()
        p=self.feed/'segment-00000000'/'quotes.jsonl'
        with p.open('ab') as handle:handle.write(b'EXTRA\n')
        with self.assertRaisesRegex(ContractError,'SOURCE_PREFIX_FILE_CHANGED'):
            read_quotes(self.c,int(time.time()*1000))

    def test_deleted_source_cannot_be_skipped(self):
        self.archive()
        (self.feed/'segment-00000000'/'quotes.jsonl').unlink()
        with self.assertRaisesRegex(ContractError,'SOURCE_ARCHIVE_REORDERED_OR_MISSING'):
            read_quotes(self.c,int(time.time()*1000))

    def test_insert_before_sealed_prefix_cannot_reorder(self):
        self.archive()
        self.make_segment(-1)
        with self.assertRaisesRegex(ContractError,'SOURCE_ARCHIVE_REORDERED_OR_MISSING'):
            read_quotes(self.c,int(time.time()*1000))

    def test_modified_page_and_deleted_last_page_both_rejected(self):
        self.archive()
        page=directory(self.c)/'page-00000004.json'
        page.write_bytes(page.read_bytes().replace(b'\"index\":4',b'\"index\":3'))
        with self.assertRaisesRegex(ContractError,'PAGE_(CHAIN|CHECKPOINT_HASH)'):
            read_quotes(self.c,int(time.time()*1000))
        page.unlink()
        with self.assertRaisesRegex(ContractError,'PAGE_CHECKPOINT_COUNT'):
            read_quotes(self.c,int(time.time()*1000))

    def test_truncated_or_rewritten_audit_ledger_rejected(self):
        self.archive()
        ledger=directory(self.c)/'ledger.jsonl'
        ledger.write_bytes(ledger.read_bytes()[:-1])
        with self.assertRaisesRegex(ContractError,'LEDGER_LINE'):
            read_quotes(self.c,int(time.time()*1000))

    def test_archive_pin_is_bound_to_original_file_bytes(self):
        self.archive()
        values,pins=read_quotes(self.c,int(time.time()*1000))
        self.assertEqual(len(values),166)
        p=directory(self.c)/'page-00000001.json'
        item=json.loads(p.read_bytes())
        self.assertEqual(len(item['prefixes']),PAGE_SIZE)
        (self.feed/'segment-00000033'/'quotes.jsonl').unlink()
        with self.assertRaises(OSError):
            verify_source_prefixes({'source_prefixes':pins})

    def test_recovery_witness_not_sealed_before_later_numeric_segments(self):
        self.archive()
        (self.feed/'segment-00000165').rename(self.feed/'segment-recovery-live')
        for i in range(165,191):
            self.make_segment(i)
        self.assertEqual(seal(self.c,8)['pages_added'],0)
        self.make_segment(191)
        receipt=seal(self.c,8)
        self.assertEqual(receipt['pages_added'],1)
        self.assertEqual(receipt['unsealed_segments'],1)
        self.assertEqual(len(read_quotes(self.c,int(time.time()*1000))[0]),192)

    def test_nonblocking_seal_lock_refuses_a_second_writer(self):
        import fcntl
        self.archive()
        lock=directory(self.c)/'seal.lock'
        with lock.open('r+b') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ContractError,'SOURCE_ARCHIVE_CONCURRENT_SEAL'):
                seal(self.c,8)
            fcntl.flock(stream,fcntl.LOCK_UN)

    def test_open_tail_growth_does_not_invalidate_completed_pages(self):
        self.archive()
        p=self.feed/'segment-00000165'/'quotes.jsonl'
        body={'schema':'heptatrader.laya-shadow-quotes.v1','sequence':2,
              'previous':json.loads(p.read_bytes().splitlines()[-1])['sha256'],
              'at_ms':self.T+165*30000+300,'kind':'END','data':{}}
        with p.open('ab') as fd:fd.write(canonical({**body,'sha256':digest(body)})+b'\n')
        values,pins=read_quotes(self.c,int(time.time()*1000))
        self.assertEqual(len(values),166)
        self.assertTrue(pins[-1]['ended'])
        self.assertEqual(seal(self.c,8)['pages_added'],0) # only 6 unsealed

if __name__=='__main__':unittest.main()
