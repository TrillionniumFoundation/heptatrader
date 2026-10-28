"""No Broker needed. Archive integrity, growth, deletion and reordering tests."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_sealed_hash_and_chain_use_one_read_of_original_bytes(self):
        self.archive()
        paths={}
        for path in self.feed.glob('segment-*/quotes.jsonl'):
            version=path.stat();paths[version.st_dev,version.st_ino]=str(path)
        counts={};original_read=os.read
        def counted_read(fd,size):
            value=original_read(fd,size)
            version=os.fstat(fd);name=paths.get((version.st_dev,version.st_ino))
            if name is not None:
                counts[name]=counts.get(name,0)+len(value)
            return value
        with patch('laya_cell.shadow_source.os.read',counted_read):
            rows,pins=read_quotes(self.c,int(time.time()*1000))
        self.assertEqual(len(rows),166)
        self.assertEqual(len(pins),11)
        self.assertEqual(len(counts),166)
        for name,count in counts.items():
            # The one-byte LF probe precedes the single hash/strict decode pass.
            self.assertEqual(count,Path(name).stat().st_size+1)

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


    def test_dataset_proof_cannot_start_from_later_valid_page(self):
        self.archive()
        _,pins=read_quotes(self.c,int(time.time()*1000))
        with self.assertRaisesRegex(ContractError,'PAGE_PROOF_DISCONTINUITY'):
            verify_source_prefixes({'source_prefixes':pins[1:]})

    def test_dataset_proof_cannot_skip_checkpointed_middle_page(self):
        self.archive()
        _,pins=read_quotes(self.c,int(time.time()*1000))
        with self.assertRaisesRegex(ContractError,'PAGE_PROOF_DISCONTINUITY'):
            verify_source_prefixes({'source_prefixes':pins[:1]+pins[2:]})

    def test_dataset_proof_cannot_ignore_corrupted_archive_ledger(self):
        self.archive()
        _,pins=read_quotes(self.c,int(time.time()*1000))
        ledger=directory(self.c)/'ledger.jsonl'
        ledger.write_bytes(b'')
        with self.assertRaisesRegex(ContractError,'PAGE_CHECKPOINT_COUNT'):
            verify_source_prefixes({'source_prefixes':pins})

    def test_dataset_proof_rejects_page_after_unsealed_source(self):
        self.archive()
        _,pins=read_quotes(self.c,int(time.time()*1000))
        self.assertEqual(len(pins),11)
        forged=pins[:2]+[pins[-1]]+pins[2:-1]
        with self.assertRaisesRegex(ContractError,'PAGE_PROOF_DISCONTINUITY'):
            verify_source_prefixes({'source_prefixes':forged})

    def test_archive_pin_cannot_claim_another_dataset_period(self):
        self.archive()
        _,pins=read_quotes(self.c,int(time.time()*1000))
        with self.assertRaisesRegex(ContractError,'DATASET_PERIOD_CHANGED'):
            verify_source_prefixes({'scope':'f'*64+':EUR.USD','source_prefixes':pins})


    def test_power_loss_after_fsynced_page_recovers_only_matching_checkpoint(self):
        with patch('laya_cell.source_archive._append_checkpoint',
                   side_effect=OSError('simulated-after-page-fsync')):
            with self.assertRaisesRegex(OSError,'simulated-after-page-fsync'):
                seal(self.c,8)
        page=directory(self.c)/'page-00000000.json'
        original=page.read_bytes()
        with self.assertRaisesRegex(ContractError,'PAGE_CHECKPOINT_COUNT'):
            read_quotes(self.c,int(time.time()*1000))
        receipt=seal(self.c,8)
        self.assertEqual(receipt['recovered_fsynced_orphan_pages'],1)
        self.assertEqual(receipt['total_pages'],5)
        self.assertEqual(page.read_bytes(),original)
        self.assertEqual(len(read_quotes(self.c,int(time.time()*1000))[0]),166)
        self.assertEqual(seal(self.c,8)['recovered_fsynced_orphan_pages'],0)

    def test_tampered_uncheckpointed_page_stays_quarantined(self):
        with patch('laya_cell.source_archive._append_checkpoint',
                   side_effect=OSError('crash')):
            with self.assertRaises(OSError):seal(self.c,8)
        page=directory(self.c)/'page-00000000.json'
        page.write_bytes(page.read_bytes().replace(b'"index":0',b'"index":8'))
        with self.assertRaisesRegex(ContractError,'ORPHAN_PAGE_INVALID'):
            seal(self.c,8)
        self.assertFalse((directory(self.c)/'ledger.jsonl').exists())

    def test_missing_original_in_orphan_prevents_recovery(self):
        with patch('laya_cell.source_archive._append_checkpoint',
                   side_effect=OSError('crash')):
            with self.assertRaises(OSError):seal(self.c,8)
        (self.feed/'segment-00000000'/'quotes.jsonl').unlink()
        with self.assertRaisesRegex(ContractError,'ORPHAN_SOURCE_REORDERED'):
            seal(self.c,8)
        self.assertFalse((directory(self.c)/'ledger.jsonl').exists())


    def test_mid_chain_power_loss_does_not_rewrite_prior_checkpoint(self):
        from laya_cell import source_archive as archive
        append=archive._append_checkpoint
        def crash_second(root,index,page_sha):
            if index==1:
                raise OSError('simulated-index-one-powerloss')
            return append(root,index,page_sha)
        with patch('laya_cell.source_archive._append_checkpoint',
                   side_effect=crash_second):
            with self.assertRaisesRegex(OSError,'index-one-powerloss'):
                seal(self.c,8)
        root=directory(self.c)
        page0=(root/'page-00000000.json').read_bytes()
        page1=(root/'page-00000001.json').read_bytes()
        ledger=(root/'ledger.jsonl').read_bytes()
        self.assertEqual(len(ledger.splitlines()),1)
        with self.assertRaisesRegex(ContractError,'PAGE_CHECKPOINT_COUNT'):
            read_quotes(self.c,int(time.time()*1000))
        resumed=seal(self.c,8)
        self.assertEqual(resumed['recovered_fsynced_orphan_pages'],1)
        self.assertEqual((root/'page-00000000.json').read_bytes(),page0)
        self.assertEqual((root/'page-00000001.json').read_bytes(),page1)
        self.assertTrue((root/'ledger.jsonl').read_bytes().startswith(ledger))
        self.assertEqual(len(read_quotes(self.c,int(time.time()*1000))[0]),166)


    def test_malformed_archive_pin_root_refused_as_contract_error(self):
        self.archive()
        _,pins=read_quotes(self.c,int(time.time()*1000))
        altered=[dict(pins[0])]+pins[1:]
        altered[0]['path']=None
        with self.assertRaisesRegex(ContractError,'PIN_ROOT_INVALID'):
            verify_source_prefixes({'source_prefixes':altered})

    def test_bool_page_index_cannot_be_recovered_as_integer_zero(self):
        with patch('laya_cell.source_archive._append_checkpoint',
                   side_effect=OSError('crash')):
            with self.assertRaises(OSError):seal(self.c,8)
        page=directory(self.c)/'page-00000000.json'
        doc=json.loads(page.read_bytes())
        doc['index']=False
        page.write_bytes(canonical(doc)+b'\n')
        with self.assertRaisesRegex(ContractError,'ORPHAN_PAGE_INVALID'):
            seal(self.c,8)
        self.assertFalse((directory(self.c)/'ledger.jsonl').exists())

    def _sparse_fixture(self):
        """Immutable old v1 pages, one historical FAILURE-only and one open."""
        self.archive()
        root = directory(self.c)
        old_pages = [(root/f"page-{i:08d}.json").read_bytes() for i in range(5)]
        broken = self.feed/'segment-00000160'/'quotes.jsonl'
        first = broken.read_bytes().splitlines()[0]
        original = json.loads(first)
        failure = {'schema':'heptatrader.laya-shadow-quotes.v1',
                   'sequence':1, 'previous':original['sha256'],
                   'at_ms':self.T + 160*30000 + 200,
                   'kind':'FAILURE','data':{'reason':'HISTORICAL_TEST_FAILURE'}}
        broken.write_bytes(first + b'\n' + canonical({
            **failure,'sha256':digest(failure)}) + b'\n')
        broken.chmod(0o600)
        frozen_failure = broken.read_bytes()
        for i in range(166,199):
            self.make_segment(i)
        return root, old_pages, broken, frozen_failure

    def test_v2_sparse_end_only_preserves_failure_and_legacy_pages(self):
        from laya_cell.source_archive import PAGE_SCHEMA_V2, UNSEALED_BOUND
        root, old_pages, broken, failure_bytes = self._sparse_fixture()
        self.assertEqual(UNSEALED_BOUND,128)
        receipt = seal(self.c,8)
        self.assertEqual(receipt['pages_added'],1)
        self.assertEqual(receipt['total_pages'],6)
        self.assertEqual(receipt['sealed_segments'],192)
        self.assertEqual(receipt['unsealed_segments'],7)
        self.assertEqual(receipt['source_files_removed'],0)
        self.assertEqual([ (root/f'page-{i:08d}.json').read_bytes()
                          for i in range(5)],old_pages)
        page=json.loads((root/'page-00000005.json').read_bytes())
        self.assertEqual(page['schema'],PAGE_SCHEMA_V2)
        self.assertEqual(len(page['prefixes']),32)
        self.assertEqual(page['skipped_paths'],[
            str(self.feed/'segment-00000160'/'quotes.jsonl'),
            str(self.feed/'segment-00000165'/'quotes.jsonl')])
        self.assertEqual(broken.read_bytes(),failure_bytes)
        self.assertNotIn(b'"kind":"END"',broken.read_bytes())
        quotes,pins=read_quotes(self.c,int(time.time()*1000))
        self.assertEqual(len(quotes),198)
        self.assertEqual(len(pins),13)
        self.assertEqual(len(list(self.feed.glob('segment-*/quotes.jsonl'))),199)
        self.assertEqual([p['path'] for p in pins[-7:]][0],str(broken))
        verify_source_prefixes({'source_prefixes':pins})
        self.assertEqual(seal(self.c,8)['pages_added'],0)

    def test_v2_proof_requires_skipped_original_prefix_and_unchanged_bytes(self):
        _,_,broken,_=self._sparse_fixture()
        seal(self.c,8)
        _,pins=read_quotes(self.c,int(time.time()*1000))
        omitted=[pin for pin in pins if pin.get('path')!=str(broken)]
        with self.assertRaisesRegex(ContractError,'SKIPPED_PREFIX_MISSING'):
            verify_source_prefixes({'source_prefixes':omitted})
        verify_source_prefixes({'source_prefixes':pins})
        broken.write_bytes(broken.read_bytes().replace(
            b'HISTORICAL_TEST_FAILURE',b'HISTORICAL_TEST_FAILURE_TAMPER'))
        with self.assertRaisesRegex(ContractError,'SOURCE_PREFIX_HASH_CHANGED'):
            verify_source_prefixes({'source_prefixes':pins})

    def test_v2_orphan_recovery_keeps_exact_fsynced_page(self):
        root,old_pages,broken,failure_bytes=self._sparse_fixture()
        from laya_cell import source_archive as archive
        append=archive._append_checkpoint
        def crash_sparse(root,index,page_sha):
            if index==5:raise OSError('sparse-checkpoint-interrupted')
            return append(root,index,page_sha)
        with patch('laya_cell.source_archive._append_checkpoint',
                   side_effect=crash_sparse):
            with self.assertRaisesRegex(OSError,'sparse-checkpoint-interrupted'):
                seal(self.c,8)
        page=root/'page-00000005.json'
        frozen=page.read_bytes()
        with self.assertRaisesRegex(ContractError,'PAGE_CHECKPOINT_COUNT'):
            read_quotes(self.c,int(time.time()*1000))
        result=seal(self.c,8)
        self.assertEqual(result['recovered_fsynced_orphan_pages'],1)
        self.assertEqual(page.read_bytes(),frozen)
        self.assertEqual([ (root/f'page-{i:08d}.json').read_bytes()
                          for i in range(5)],old_pages)
        self.assertEqual(broken.read_bytes(),failure_bytes)
        self.assertEqual(len(read_quotes(self.c,int(time.time()*1000))[0]),198)

    def test_v2_following_page_and_original_unsealed_bound(self):
        root,_,_,_=self._sparse_fixture()
        seal(self.c,8)
        for i in range(199,226):
            self.make_segment(i)
        result=seal(self.c,8)
        self.assertEqual(result['pages_added'],1)
        self.assertEqual(result['total_pages'],7)
        self.assertEqual(result['sealed_segments'],224)
        self.assertEqual(json.loads((root/'page-00000006.json').read_bytes())['schema'],
                         'heptatrader.laya-source-archive-page.v1')
        verify_source_prefixes({'source_prefixes':
            read_quotes(self.c,int(time.time()*1000))[1]})
        for i in range(226,354):
            self.make_segment(i,ended=False)
        with self.assertRaisesRegex(ContractError,'SOURCE_UNSEALED_SEGMENT_BOUND'):
            read_quotes(self.c,int(time.time()*1000))


    def test_v2_original_recent_outcome_and_readiness_cache_agree(self):
        from laya_cell.original_price_source import read_original_price_window
        from laya_cell.resident_recent_source import (
            read_recent_original_quotes, read_original_quote_range)
        from laya_cell.verified_sealed_readiness_cache import VerifiedSealedReadinessCache
        self._sparse_fixture()
        seal(self.c,8)
        now=self.T+198*30000+420000
        full,fullpins=read_quotes(self.c,now)
        self.assertEqual(len(full),198)
        recent,recentpins=read_recent_original_quotes(self.c,now)
        self.assertEqual(len(recent),6)
        self.assertEqual(recent,[q for q in full if q['t'] in
                         {x['t'] for x in recent}])
        self.assertEqual(len(recentpins),len(fullpins))
        verify_source_prefixes({'source_prefixes':recentpins})
        first=self.T+164*30000
        last=self.T+198*30000+500
        selected,selectedpins=read_original_quote_range(self.c,now,first,last)
        self.assertEqual(selected,[q for q in full if first<=q['t']<=last])
        self.assertEqual(len(selectedpins),len(fullpins))
        verify_source_prefixes({'source_prefixes':selectedpins})
        window,proof=read_original_price_window(self.c,now)
        self.assertEqual(window,[q for q in full
                         if now-4500000<=q['t']<=now])
        self.assertEqual(len(proof['archive_pages']),6)
        self.assertIn(str(self.feed/'segment-00000160'/'quotes.jsonl'),
                      {p['path'] for p in proof['selected_original_prefixes']})
        cached=VerifiedSealedReadinessCache()
        try:
            old,pins=cached.read_quotes(self.c,now)
            self.assertEqual(old,full)
            self.assertEqual(pins,fullpins)
            earliest=now-600000
            window1,pins1=cached.read_quotes(self.c,now,earliest_ms=earliest)
            window2,pins2=cached.read_quotes(self.c,now+1000,earliest_ms=earliest)
            self.assertEqual(window1,window2)
            self.assertEqual(pins1,pins2)
            self.assertEqual(len(pins1),len(fullpins))
            verify_source_prefixes({'source_prefixes':pins1})
            recent_ids={q['t'] for q in full if q['t']>=earliest}
            self.assertTrue(recent_ids.issubset({q['t'] for q in window1}))
        finally:
            cached.close()


    def test_v2_hole_at_old_count_boundary_waits_for_later_sealed_summaries(self):
        """A raw file at index sealed_count must not retire unseen pins."""
        from laya_cell.verified_sealed_readiness_cache import VerifiedSealedReadinessCache
        self._sparse_fixture()
        hole = self.feed/'segment-00000192'/'quotes.jsonl'
        original = hole.read_bytes().splitlines()
        self.assertIn(b'"kind":"END"', original[-1])
        hole.write_bytes(b'\n'.join(original[:-1]) + b'\n')
        receipt = seal(self.c, 8)
        self.assertEqual(receipt['sealed_segments'], 192)
        self.assertEqual(receipt['unsealed_segments'], 7)
        page = json.loads((directory(self.c)/'page-00000005.json').read_bytes())
        self.assertIn(str(hole), page['skipped_paths'])
        self.assertIn(str(self.feed/'segment-00000193'/'quotes.jsonl'),
                      {pin['path'] for pin in page['prefixes']})
        now = self.T + 198*30000 + 420000
        full, fullpins = read_quotes(self.c, now)
        earliest = now - 600000
        cache = VerifiedSealedReadinessCache()
        try:
            first, firstpins = cache.read_quotes(self.c, now, earliest_ms=earliest)
            second, secondpins = cache.read_quotes(self.c, now+1000,
                                                  earliest_ms=earliest)
            self.assertEqual(firstpins, fullpins)
            self.assertEqual(secondpins, firstpins)
            self.assertEqual(first, second)
            self.assertTrue({q['t'] for q in full if q['t'] >= earliest}.issubset(
                {q['t'] for q in first}))
            verify_source_prefixes({'source_prefixes': firstpins})
        finally:
            cache.close()


if __name__=='__main__':unittest.main()
