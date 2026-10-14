import hashlib,json,os,tempfile,unittest
from pathlib import Path
from historical_training.value_ledger_stream import stream_execution_index

class StreamReplayTest(unittest.TestCase):
 def write(self,root,records):
  p=Path(root)/'oms.jsonl'
  raw=b''.join((json.dumps(x,sort_keys=True)+'\n').encode() for x in records)
  p.write_bytes(raw)
  p.chmod(0o600)
  return p,raw
 def test_append_beyond_pin_and_stream_index(self):
  with tempfile.TemporaryDirectory() as t:
   records=[{'event':'broker_execution','broker_execution_id':'e1','ts_ms':15,
             'req_id':'c1','execution_domain':'PAPER','instrument':'EUR.USD'},
            {'event':'broker_execution','broker_execution_id':'e1','ts_ms':14,
             'req_id':'c1','execution_domain':'PAPER','instrument':'EUR.USD'}]
   p,raw=self.write(t,records)
   pin={'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
   with p.open('ab') as f:f.write(b'x'*(2*1024*1024))
   a,b,c=stream_execution_index(pin)
   self.assertEqual(a,{'e1':14});self.assertEqual(b,{'c1':14});self.assertEqual(c,{'e1':'c1'})
 def test_mutated_prefix_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   p,raw=self.write(t,[{'event':'ignore'}])
   pin={'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
   p.write_bytes(raw.replace(b'ignore',b'IGNORE')+b'extra')
   with self.assertRaisesRegex(ValueError,'LEDGER_PINNED_PREFIX_CHANGED'):stream_execution_index(pin)
 def test_partial_line_and_unsafe_mode_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   p,raw=self.write(t,[{'event':'ignore'}])
   pin={'path':str(p),'bytes':len(raw)-1,'sha256':hashlib.sha256(raw[:-1]).hexdigest()}
   with self.assertRaisesRegex(ValueError,'LEDGER_PREFIX_NOT_RECORD_BOUNDARY'):stream_execution_index(pin)
   p.chmod(0o644)
   with self.assertRaisesRegex(ValueError,'LEDGER_PRIVATE_PREFIX_UNAVAILABLE'):stream_execution_index(pin)
 def test_duplicate_json_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'ledger.jsonl';raw=b'{"event":"x","event":"y"}\n';p.write_bytes(raw);p.chmod(0o600)
   with self.assertRaisesRegex(ValueError,'DUPLICATE_LEDGER_JSON_KEY'):
    stream_execution_index({'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
if __name__=='__main__':unittest.main()
