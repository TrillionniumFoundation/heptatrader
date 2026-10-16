"""Test source-archive rotation and fixed 90-slot runner without trading."""
import unittest
from unittest.mock import patch
from laya_cell.integrity import ContractError
from research_laya_price_original_shadow90 import get_original_quotes, asof_feature


class ReaderFallback(unittest.TestCase):
 def setUp(self):
  self.anchor=1800000600000
  self.asof=self.anchor+9000
  self.q=[{"t":self.anchor-3900000,"known_at_ms":self.anchor-3899000},
          {"t":self.anchor-300000,"known_at_ms":self.anchor-299000}]
  self.pins=[{"evidence":"source-pinned"}]
 def test_normal_recent_source_does_not_need_full_scan(self):
  with patch('research_laya_price_original_shadow90.read_recent_original_quotes',return_value=(self.q,self.pins)),patch('research_laya_price_original_shadow90.read_quotes') as full:
   q,p=get_original_quotes({},self.asof,self.anchor)
   self.assertEqual(q,self.q);self.assertEqual(p,self.pins)
   full.assert_not_called()
 def test_recent_source_rotation_can_use_complete_verifiable_archive(self):
  with patch('research_laya_price_original_shadow90.read_recent_original_quotes',side_effect=ContractError('RECENT_SOURCE_CANNOT_SKIP_RECENT_ARCHIVE')),patch('research_laya_price_original_shadow90.read_quotes',return_value=(self.q,self.pins)) as full:
   q,p=get_original_quotes({},self.asof,self.anchor)
   self.assertEqual(len(q),2)
   full.assert_called_once()
 def test_concurrent_archive_change_is_not_forgiven(self):
  with patch('research_laya_price_original_shadow90.read_recent_original_quotes',side_effect=ContractError('RECENT_SOURCE_ARCHIVE_CHANGED_DURING_READ')),patch('research_laya_price_original_shadow90.read_quotes') as full:
   with self.assertRaisesRegex(ContractError,'ARCHIVE_CHANGED'):
    get_original_quotes({},self.asof,self.anchor)
   full.assert_not_called()
 def test_archive_integrity_mismatch_does_not_fallback(self):
  with patch('research_laya_price_original_shadow90.read_recent_original_quotes',side_effect=ContractError('RECENT_SEALED_SOURCE_END_CHANGED')),patch('research_laya_price_original_shadow90.read_quotes') as full:
   with self.assertRaisesRegex(ContractError,'END_CHANGED'):
    get_original_quotes({},self.asof,self.anchor)
   full.assert_not_called()
 def test_source_coverage_gap_can_retry_full_archive(self):
  with patch('research_laya_price_original_shadow90.get_original_quotes',side_effect=[(self.q,self.pins),(self.q,self.pins)]) as source,patch('research_laya_price_original_shadow90.original_feature_snapshot',side_effect=[ContractError('ORIGINAL_FEATURE_INSUFFICIENT_CLOSED_HISTORY'),{'anchor_ms':self.anchor}]):
   f,q,p=asof_feature({},self.asof,self.anchor)
   self.assertEqual(f['anchor_ms'],self.anchor)
   self.assertEqual(source.call_count,2)
   self.assertEqual(source.call_args.kwargs,{'full':True})
 def test_source_rotation_stale_bar_can_retry_full_archive(self):
  with patch('research_laya_price_original_shadow90.get_original_quotes',side_effect=[(self.q,self.pins),(self.q,self.pins)]) as source,patch('research_laya_price_original_shadow90.original_feature_snapshot',side_effect=[ContractError('ORIGINAL_FEATURE_INPUT_LATE_OR_UNCOMMITTED'),{'anchor_ms':self.anchor}]):
   asof_feature({},self.asof,self.anchor)
   self.assertEqual(source.call_count,2)
 def test_corrupt_quote_cannot_retry_full_archive(self):
  with patch('research_laya_price_original_shadow90.get_original_quotes',return_value=(self.q,self.pins)) as source,patch('research_laya_price_original_shadow90.original_feature_snapshot',side_effect=ContractError('ORIGINAL_FEATURE_CROSSED_OR_INVALID')):
   with self.assertRaisesRegex(ContractError,'CROSSED_OR_INVALID'):
    asof_feature({},self.asof,self.anchor)
   self.assertEqual(source.call_count,1)
 def test_full_archive_reader_retains_time_cutoff(self):
  older=[{"t":self.anchor-5000000,"known_at_ms":self.anchor-4999999}]
  from functools import partial
  with patch('research_laya_price_original_shadow90.read_quotes',return_value=(older+self.q,self.pins)):
   q,p=get_original_quotes({},self.asof,self.anchor,full=True)
   self.assertEqual(q,self.q)


if __name__=="__main__":unittest.main()
