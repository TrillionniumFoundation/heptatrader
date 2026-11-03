"""Cached source must preserve independent attribution verification."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'research'),str(ROOT/'research/neuron_learning'),str(Path(__file__).parent)]
import hepta_decision_attribution as a
import test_decision_attribution as fixtures
from laya_cell.current100k_preopen import ResidentIngress
from laya_cell.financial_forward_source import _ingress

class CachedProvenanceTests(unittest.TestCase):
 setUp=fixtures.ProvenanceTests.setUp
 decision=fixtures.ProvenanceTests.decision
 trade=fixtures.ProvenanceTests.trade
 def source(self):
  self.journal.q.join();source=ResidentIngress(self.reg,'1'*64);source.read();return source
 def test_unchanged_read_and_append_match_independent_full_import(self):
  self.trade();source=self.source();first=source.read();initial=source.statistics()
  self.assertEqual(first,_ingress(self.reg,'1'*64))
  self.assertEqual(source.statistics()['segment_bytes_read'],initial['segment_bytes_read'])
  self.trade();self.journal.q.join();later=source.read()
  self.assertEqual(later,_ingress(self.reg,'1'*64));self.assertEqual(len(later[2]),2)
  self.assertEqual(source.statistics()['new_records_decoded']-initial['new_records_decoded'],4)
 def test_health_change_revokes_original_producer(self):
  self.trade();source=self.source();self.journal.dropped=1;self.journal.close()
  later=source.read();self.assertFalse(later[2]);self.assertTrue(later[3])
 def test_old_prefix_mutation_refused_even_if_record_is_rehashed(self):
  self.trade();source=self.source();self.journal.close()
  p=next((self.root/'journal').glob('*.jsonl'));lines=p.read_bytes().splitlines()
  r=a.strict(lines[0]);r['written_at_ms']+=1;r['sha256']=a.digest({k:v for k,v in r.items() if k!='sha256'})
  lines[0]=a.canonical(r).encode();p.write_bytes(b'\n'.join(lines)+b'\n')
  with self.assertRaisesRegex(ValueError,'CACHED_PREFIX_CHANGED'):source.read()
 def test_truncation_removal_and_inode_replacement_fail_closed(self):
  self.trade();source=self.source();self.journal.close();p=next((self.root/'journal').glob('*.jsonl'))
  raw=p.read_bytes();p.write_bytes(raw[:raw.find(b'\n')+1])
  with self.assertRaisesRegex(ValueError,'REPLACED_OR_TRUNCATED'):source.read()
  p.rename(p.with_suffix('.old'));p.write_bytes(raw);p.chmod(0o600)
  with self.assertRaisesRegex(ValueError,'REPLACED_OR_TRUNCATED'):source.read()
  p.unlink()
  with self.assertRaisesRegex(ValueError,'SEGMENT_REMOVED'):source.read()
 def test_partial_tail_is_not_promoted_to_request(self):
  self.trade();source=self.source();self.journal.close();first=source.read()
  p=next((self.root/'journal').glob('*.jsonl'))
  with p.open('ab') as f:f.write(b'{"unfinished')
  self.assertEqual(first,source.read())
 def test_registration_replacement_is_not_adopted(self):
  self.trade();source=self.source();self.journal.close();self.path.write_text('{}')
  with self.assertRaisesRegex(ValueError,'REGISTERED_PRODUCER_CHANGED'):source.read()

if __name__=='__main__':unittest.main()
