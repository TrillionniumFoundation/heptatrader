"""Synthetic proof tests; no broker connections or orders."""
from __future__ import annotations
import json,tempfile,unittest
from pathlib import Path
from ib_source_proof.run import EvidenceLog,now_ms,digest,canonical
from ib_source_proof.audit import load_direct,direct_quotes,cells

def make(folder,quote=True):
 j=EvidenceLog(Path(folder)/'events.jsonl')
 j.add('HEADER',{'scope':'EUR.USD_CASH_IDEALPRO_READ_ONLY'})
 if quote:
  at=now_ms()
  j.add('TBT_BIDASK',{'bid':1.1,'ask':1.10002,'broker_second':at//1000,
    'received_ms':at,'valid_pair':True,'independent_update':True,'request_id':3102})
 j.add('END',{'state':'DURATION_ENDED','broker_orders_sent':0,'account_requests_sent':0,
  'wire_counts':{'counts':{'71':1,'59':1,'1':1,'97':1,'2':1,'98':1},
                 'mutating_codes_sent':0,'active_requests':[]}})
 j.close()
 return Path(folder)/'events.jsonl'

class AuditTests(unittest.TestCase):
 def test_source_chain_and_quote(self):
  with tempfile.TemporaryDirectory() as td:
   make(td)
   rows,meta=load_direct(td)
   self.assertEqual(meta['end_state'],'DURATION_ENDED')
   self.assertEqual(len(direct_quotes(rows)[0]),1)
 def test_corrupt_bytes_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   p=make(td);p.write_bytes(p.read_bytes().replace(b'1.10002',b'1.00002'))
   with self.assertRaisesRegex(ValueError,'CHAIN'):load_direct(td)
 def test_no_end_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   p=make(td,False);p.write_bytes(b''.join(p.read_bytes().splitlines(keepends=True)[:-1]))
   with self.assertRaisesRegex(ValueError,'INCOMPLETE'):load_direct(td)
 def test_mutating_opcode_rejected_even_if_rehashed(self):
  with tempfile.TemporaryDirectory() as td:
   p=make(td,False);lines=p.read_bytes().splitlines(keepends=True)
   r=json.loads(lines[-1]);r['payload']['wire_counts']['counts']['3']=1
   r['sha256']=digest({k:v for k,v in r.items() if k!='sha256'})
   p.write_bytes(b''.join(lines[:-1])+canonical(r)+b'\n')
   with self.assertRaisesRegex(ValueError,'UNSAFE_OPCODE'):load_direct(td)
 def test_order_counter_rejected_even_if_rehashed(self):
  with tempfile.TemporaryDirectory() as td:
   p=make(td,False);lines=p.read_bytes().splitlines(keepends=True)
   r=json.loads(lines[-1]);r['payload']['broker_orders_sent']=1
   r['sha256']=digest({k:v for k,v in r.items() if k!='sha256'})
   p.write_bytes(b''.join(lines[:-1])+canonical(r)+b'\n')
   with self.assertRaisesRegex(ValueError,'READ_ONLY'):load_direct(td)
 def test_no_future_tick(self):
  self.assertEqual(cells([{'t':11000,'known_at_ms':20000}],14000,19000)['fresh'],0)
 def test_empty_stream(self):
  self.assertEqual(cells([],1000,6000)['fresh'],0)
 def test_quote_expires(self):
  self.assertEqual(cells([{'t':1000,'known_at_ms':1500}],2000,11000)['fresh'],5)
 def test_single_side_never_a_complete_quote(self):
  with tempfile.TemporaryDirectory() as td:
   j=EvidenceLog(Path(td)/'events.jsonl');t=now_ms()
   j.add('HEADER',{})
   j.add('TOP_BID',{'received_ms':t,'price':1.1})
   j.add('TOP_ASK',{'received_ms':t+6001,'price':1.10002})
   j.add('END',{'state':'DURATION_ENDED','broker_orders_sent':0,'account_requests_sent':0,
    'wire_counts':{'counts':{},'mutating_codes_sent':0,'active_requests':[]}})
   j.close()
   self.assertEqual(direct_quotes(load_direct(td)[0])[0],[])

if __name__=='__main__':unittest.main(verbosity=2)
