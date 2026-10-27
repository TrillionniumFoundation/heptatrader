"""Fast recent-only source must agree with the strict full archive reader.

Only original locally hash-chained PAPER quote fixtures; no GPU or broker.
"""
import copy
import mmap
import os
import unittest
from unittest.mock import patch

from laya_cell.resident_recent_source import (
    read_recent_original_quotes, read_original_quote_range,
    MIN_CAUSAL_SPAN_MS,MIN_OUTCOME_SPAN_MS)
from laya_cell.resident_data import read_quotes,forecast_input
from laya_cell.integrity import ContractError, digest
from laya_cell.source_archive import inventory
from laya_cell.tests import test_source_archive as archive_fixture


class RecentSourceStrictParityTests(unittest.TestCase):
 def setUp(self):
  source=archive_fixture.SourceArchiveTests('test_more_than_128_fails_before_archival_then_reads_all_originals')
  source.setUp()
  self.addCleanup(source.doCleanups)
  self.source=source
  source.archive()
  # SourceArchiveTests originally creates 160 sealed + 6 unsealed.
  # Extend original lexical series to a genuine 31-source unsealed tail.
  for i in range(166,191):
   source.make_segment(i)
  self.now=source.T+190*30000+300
  self.config=source.c

 def strict_and_fast(self,span=MIN_CAUSAL_SPAN_MS):
  fast,pins=read_recent_original_quotes(self.config,self.now,min_span_ms=span)
  full,original_pins=read_quotes(self.config,self.now)
  recent=lambda data:[x for x in data if x['t']>=self.now-span]
  return fast,full,pins,original_pins,recent(fast),recent(full)

 def test_full_original_quote_and_prefix_parity_at_6min(self):
  f,s,fp,sp,fr,sr=self.strict_and_fast()
  self.assertEqual(fr,sr)
  self.assertEqual(fp,sp)
  self.assertEqual(len(fp),36)
  a,r=forecast_input(self.config,f,self.now)
  b,q=forecast_input(self.config,s,self.now)
  self.assertEqual(a,b)
  self.assertEqual(r,q)
  self.assertEqual(digest(r),digest(q))
  self.assertFalse('prediction' in r)
  self.assertEqual(self.config['completed_history_sources'],[])

 def test_full_original_quote_parity_at_7min_outcome(self):
  f,s,fp,sp,fr,sr=self.strict_and_fast(MIN_OUTCOME_SPAN_MS)
  self.assertEqual(fr,sr)
  self.assertEqual(fp,sp)

 def test_archive_historical_price_modified_fails_closed(self):
  file=self.source.feed/'segment-00000000'/'quotes.jsonl'
  file.write_bytes(file.read_bytes().replace(b'1.125',b'1.124'))
  with self.assertRaisesRegex(ContractError,'SOURCE_PREFIX_HASH_CHANGED'):
   read_recent_original_quotes(self.config,self.now)

 def test_archived_source_deleted_fails(self):
  file=self.source.feed/'segment-00000001'/'quotes.jsonl'
  file.unlink()
  with self.assertRaises(ContractError):
   read_recent_original_quotes(self.config,self.now)

 def test_active_unsealed_hash_chain_modified_fails(self):
  file=self.source.feed/'segment-00000190'/'quotes.jsonl'
  file.write_bytes(file.read_bytes().replace(b'1.125',b'1.124'))
  with self.assertRaises(ValueError):
   read_recent_original_quotes(self.config,self.now)

 def test_earlier_archived_events_cannot_be_omitted_from_recent_window(self):
  # A recovery-live source can legitimately contain old chronological
  # timestamps. Its presence cannot let a recent archive with overlapping
  # quotes be ignored.
  recovery=self.source.feed/'segment-recovery-live'
  recovery.mkdir(mode=0o700)
  copied=recovery/'quotes.jsonl'
  copied.write_bytes((self.source.feed/'segment-00000150'/'quotes.jsonl').read_bytes())
  copied.chmod(0o600)
  when=self.source.T+166*30000+300
  with self.assertRaisesRegex(ContractError,
          'RECENT_SOURCE_CANNOT_SKIP_RECENT_ARCHIVE'):
   read_recent_original_quotes(self.config,when)

 def test_shorter_recent_horizon_not_silently_granted(self):
  with self.assertRaisesRegex(ContractError,
          'RECENT_SOURCE_WINDOW_NOT_REGISTERED'):
   read_recent_original_quotes(self.config,self.now,min_span_ms=1000)

 def test_nonseparate_historical_sources_forbidden(self):
  conf=dict(self.config)
  conf['completed_history_sources']=['/other/history']
  with self.assertRaisesRegex(ContractError,
           'RECENT_SOURCE_MIXED_HISTORY_NOT_SUPPORTED'):
   read_recent_original_quotes(conf,self.now)

 def test_original_period_change_refused(self):
  conf=dict(self.config);conf['period_sha256']='d'*64
  with self.assertRaisesRegex(ContractError,'RESIDENT_PERIOD_CHANGED'):
   read_recent_original_quotes(conf,self.now)

 def test_new_archive_page_during_read_not_passed_as_same_snapshot(self):
  arch,pages=inventory(self.config,sorted(
   self.source.feed.glob('segment-*/quotes.jsonl')))
  with patch('laya_cell.resident_recent_source.inventory_index',
             side_effect=[(arch,pages),(arch,pages+[{'changed':True}])]):
   with self.assertRaisesRegex(ContractError,
           'RECENT_SOURCE_ARCHIVE_CHANGED_DURING_READ'):
    read_recent_original_quotes(self.config,self.now)

 def source_readers(self):
  return (
   ('recent',lambda:read_recent_original_quotes(self.config,self.now)),
   ('pending',lambda:read_original_quote_range(
    self.config,self.now,self.now-MIN_CAUSAL_SPAN_MS,self.now)),
  )

 def test_every_source_read_still_hashes_each_archived_original(self):
  from laya_cell import resident_recent_source as mod
  archived,_=inventory(self.config,sorted(self.source.feed.glob('segment-*/quotes.jsonl')))
  for name,read in self.source_readers():
   with self.subTest(reader=name),patch.object(mod,'verify_prefix',wraps=mod.verify_prefix) as verify:
    read()
    read()
    self.assertEqual(verify.call_count,2*len(archived))
    self.assertTrue(all(call.kwargs=={'sealed':True} for call in verify.call_args_list))

 def reject_tail_time_archive_mutation(self,mutate):
  from laya_cell import resident_recent_source as mod
  original_read=mod.read_source
  for name,read in self.source_readers():
   changed=False
   def changing_read(*args,**kwargs):
    nonlocal changed
    result=original_read(*args,**kwargs)
    if not changed:
     mutate();changed=True
    return result
   with self.subTest(reader=name),patch.object(mod,'read_source',side_effect=changing_read):
    with self.assertRaisesRegex(ContractError,'RECENT_SOURCE_ORIGINAL_CHANGED_DURING_READ'):
     read()
   self.assertTrue(changed)

 def test_identical_bytes_replacement_after_hash_is_rejected(self):
  file=self.source.feed/'segment-00000000'/'quotes.jsonl'
  def replace():
   before=file.stat();tmp=file.with_name('replacement.tmp')
   tmp.write_bytes(file.read_bytes());tmp.chmod(0o600)
   os.utime(tmp,ns=(before.st_atime_ns,before.st_mtime_ns))
   os.replace(tmp,file)
  self.reject_tail_time_archive_mutation(replace)

 def test_modified_then_restored_bytes_and_mtime_are_rejected(self):
  file=self.source.feed/'segment-00000000'/'quotes.jsonl'
  def mutate():
   before=file.stat();raw=file.read_bytes()
   file.write_bytes(raw.replace(b'1.125',b'1.124'))
   file.write_bytes(raw)
   os.utime(file,ns=(before.st_atime_ns,before.st_mtime_ns))
  self.reject_tail_time_archive_mutation(mutate)

 def test_mutation_during_original_hash_is_rejected(self):
  from laya_cell import resident_recent_source as mod
  file=self.source.feed/'segment-00000000'/'quotes.jsonl'
  verify=mod.verify_prefix
  def changed_after_hash(pin,**kwargs):
   verify(pin,**kwargs)
   if pin['path']==str(file):
    raw=file.read_bytes();file.write_bytes(raw)
  for name,read in self.source_readers():
   with self.subTest(reader=name),patch.object(mod,'verify_prefix',side_effect=changed_after_hash):
    with self.assertRaisesRegex(ContractError,'RECENT_SOURCE_ORIGINAL_CHANGED_DURING_READ'):
     read()

 def test_a_previous_success_never_authorizes_changed_original_bytes(self):
  file=self.source.feed/'segment-00000000'/'quotes.jsonl'
  raw=file.read_bytes()
  for name,read in self.source_readers():
   with self.subTest(reader=name):
    read()
    before=file.stat()
    file.write_bytes(raw.replace(b'1.125',b'1.124'))
    os.utime(file,ns=(before.st_atime_ns,before.st_mtime_ns))
    try:
     with self.assertRaisesRegex(ContractError,'SOURCE_PREFIX_HASH_CHANGED'):
      read()
    finally:file.write_bytes(raw)

 def test_existing_dirty_mmap_mutation_is_caught_by_final_sha(self):
  from laya_cell import resident_recent_source as mod
  file=self.source.feed/'segment-00000000'/'quotes.jsonl'
  original=file.read_bytes();offset=original.index(b'1.125')
  original_read=mod.read_source
  for name,read in self.source_readers():
   with self.subTest(reader=name),file.open('r+b') as stream:
    with mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_WRITE) as mapping:
     mapping[offset:offset+5]=b'1.125'
     changed=False
     def changing_read(*args,**kwargs):
      nonlocal changed
      result=original_read(*args,**kwargs)
      if not changed:
       mapping[offset:offset+5]=b'1.124';changed=True
      return result
     try:
      with patch.object(mod,'read_source',side_effect=changing_read):
       with self.assertRaises(ContractError):read()
      self.assertTrue(changed)
     finally:mapping[offset:offset+5]=b'1.125'

 def test_end_bound_requires_its_own_digest_not_only_a_claimed_head(self):
  from laya_cell import resident_recent_source as mod
  from laya_cell.shadow_source import canonical,strict
  archived,_=inventory(self.config,sorted(self.source.feed.glob('segment-*/quotes.jsonl')))
  file=self.source.feed/'segment-00000000'/'quotes.jsonl'
  original=file.read_bytes();lines=original.splitlines(keepends=True)
  end=strict(lines[-1]);end['at_ms']-=1
  # The claimed chain head remains unchanged. Even if bytes are restored
  # before the final bulk SHA, this forged timestamp cannot affect selection.
  try:
   file.write_bytes(b''.join(lines[:-1])+canonical(end)+b'\n')
   with self.assertRaisesRegex(ContractError,'RECENT_SEALED_SOURCE_END_CHANGED'):
    mod._sealed_end_timestamp(archived[0])
  finally:file.write_bytes(original)

 def test_readonly_smoke_source_inventory_never_imports_gpu_model(self):
  # The smoke is a read-only source auditor. Importing resident_predict here
  # would load torch/CUDA merely to hash a Python source file.
  from pathlib import Path
  mod=Path(__file__).resolve().parents[3]/'historical_training'/'laya_recent_source_smoke.py'
  code=mod.read_text()
  self.assertIn('source_root=Path(__file__).resolve()',code)
  self.assertNotIn('import resident_predict',code)
  self.assertNotIn('import resident,',code)
  self.assertNotIn('import torch',code)

 def test_causal_reader_imports_no_gpu_or_broker_path(self):
  import laya_cell.resident_recent_source as mod
  self.assertFalse(any(k in vars(mod) for k in ('torch','ibapi','NativePorts')))
  recs,pins=read_recent_original_quotes(self.config,self.now)
  self.assertGreater(len(recs),5)
  self.assertGreater(len(pins),5)
