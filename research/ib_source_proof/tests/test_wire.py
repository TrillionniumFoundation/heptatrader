"""Offline wire-contract, failure and broker-callback tests: no network or orders."""
from __future__ import annotations
import hashlib,json,queue,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from ib_source_proof.wire import SendBoundary,RejectWire,valid_pair
from ib_source_proof.run import make_reader,contract_for_fx,EvidenceLog,load_config,digest,canonical

def make():
 boundary=SendBoundary(8955);r,Contract=make_reader({'sdk_root':'/home/alex/.local/share/heptatrader/vendor/ibapi-reset-check'},boundary,queue.Queue())
 sent=[]
 r.isConnected=lambda:True;r.serverVersion=lambda:999
 r.sendMsg=lambda msg:(sent.append(msg),boundary.check(msg))
 boundary.check('71\0'+'2\0'+'8955\0')
 return r,Contract,boundary,sent

class OutboundTests(unittest.TestCase):
 def test_allowed_exact_live_subscriptions(self):
  r,C,b,packets=make();c=contract_for_fx(C)
  r.reqMarketDataType(1);r.reqMktData(3101,c,'',False,False,[]);r.reqTickByTickData(3102,c,'BidAsk',0,False)
  self.assertEqual(b.signed_receipt()['mutating_codes_sent'],0)
  self.assertEqual(b.active,{3101,3102})
  self.assertEqual(sorted(b.sent),[1,59,71,97])
 def test_sdk_optional_capabilities_empty_field_allowed(self):
  b=SendBoundary(8955)
  self.assertEqual(b.check('71\0'+'2\0'+'8955\0'+'\0'),71)
  with self.assertRaises(RejectWire):b.check('71\0'+'2\0'+'8955\0'+'TRADE\0')
 def test_cancel_own_only_and_advance_ids_before_resubscribe(self):
  r,C,b,_=make();c=contract_for_fx(C)
  r.reqMarketDataType(1);r.reqMktData(3101,c,'',False,False,[]);r.reqTickByTickData(3102,c,'BidAsk',0,False)
  with self.assertRaises(RejectWire):b.advance_cycle()
  r.cancelMktData(3101);r.cancelTickByTickData(3102);b.advance_cycle()
  self.assertEqual(b.data_ids,(3103,3104))
  r.reqMktData(3103,c,'',False,False,[]);r.reqTickByTickData(3104,c,'BidAsk',0,False)
  self.assertEqual(b.active,{3103,3104})
 def test_no_second_subscription_without_cancel(self):
  r,C,b,_=make();c=contract_for_fx(C);r.reqMarketDataType(1);r.reqMktData(3101,c,'',False,False,[])
  with self.assertRaises(RejectWire):r.reqMktData(3101,c,'',False,False,[])
 def test_mutating_and_account_messages_never_cross(self):
  _,_,b,_=make()
  for fields in (['3','1','123','BUY'],['4','1','123'],['58','1'],['76','1','100','U1','','1'],['74','1','100','U1'],['2','2','999']):
   with self.subTest(fields=fields),self.assertRaises(RejectWire):b.check('\0'.join(fields)+'\0')
  self.assertEqual(b.signed_receipt()['mutating_codes_sent'],0)
 def test_frozen_delayed_market_type_forbidden(self):
  _,_,b,_=make()
  with self.assertRaises(RejectWire):b.check('\0'.join(['59','1','2'])+'\0')
 def test_foreign_symbol_contract_and_snapshot_forbidden(self):
  r,C,b,packets=make();c=contract_for_fx(C);r.reqMarketDataType(1);r.reqMktData(3101,c,'',False,False,[])
  x=packets[-1]
  for repl in (("EUR\0CASH","BTC\0CASH"),("IDEALPRO","NYSE"),("0\00\0\0","1\00\0\0")):
   v=x.replace(*repl)
   if v==x:continue
   with self.subTest(repl=repl),self.assertRaises(RejectWire):b.check(v)
 def test_no_account_order_api_method(self):
  r,_,_,_=make()
  for name in ('placeOrder','cancelOrder','reqGlobalCancel','reqAccountUpdates','reqPositions'):
   with self.subTest(name=name),self.assertRaises(RejectWire):getattr(r,name)()
 def test_nonfinite_crossed_negative_bidask_invalid(self):
  for a,b in ((0,1),(2,1),(float('nan'),2),(1,float('inf')),(True,1)):
   with self.subTest(a=a,b=b):self.assertFalse(valid_pair(a,b))

class CallbackTests(unittest.TestCase):
 def setUp(self):self.events=queue.Queue();self.bound=SendBoundary(8955);self.reader,_=make_reader(
  {'sdk_root':'/home/alex/.local/share/heptatrader/vendor/ibapi-reset-check'},self.bound,self.events)
 def test_current_ids_only(self):
  self.reader.tickPrice(8888,1,1.12,None);self.reader.tickByTickBidAsk(8888,0,1.11,1.12,None,None,None)
  self.assertTrue(self.events.empty())
 def test_delivered_bidask_broker_timestamp_not_rewritten(self):
  self.reader.tickByTickBidAsk(3102,1791440000,1.1,1.10002,None,None,None)
  v=self.events.get_nowait();self.assertEqual(v['broker_second'],1791440000);self.assertIn('received_ms',v)
 def test_1101_revokes_cash_and_requires_recovery(self):
  self.reader.cash_ready.set();self.reader.error(-1,1101,'Connectivity restored data lost')
  self.assertFalse(self.reader.cash_ready.is_set())
  self.reader.error(-1,2104,'Market data farm connection is OK:cashfarm')
  self.assertTrue(self.reader.cash_ready.is_set())
 def test_1102_preserves_subscription(self):
  self.reader.cash_ready.set();self.reader.error(-1,1102,'Connectivity restored data maintained')
  self.assertTrue(self.reader.cash_ready.is_set())
 def test_2103_cash_revokes_liveness(self):
  self.reader.cash_ready.set();self.reader.error(-1,2103,'cashfarm disconnected')
  self.assertFalse(self.reader.cash_ready.is_set())
 def test_delayed_market_type_does_not_grant_live_bidask(self):
  self.reader.marketDataType(3101,3)
  self.assertTrue(self.reader.lost)
 def test_order_warnings_do_not_enable_order_methods(self):
  self.reader.error(-1,2104,'Market data farm connection is OK:cashfarm')
  with self.assertRaises(RejectWire):self.reader.placeOrder(1,None,None)

class EvidenceTests(unittest.TestCase):
 def test_append_only_chain_integrity(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'events.jsonl';j=EvidenceLog(p);j.add('HEADER',{'x':1});j.add('TOP_BID',{'price':1.1});j.add('END',{})
   j.close();rows=[json.loads(z) for z in p.read_bytes().splitlines()]
   last='0'*64
   for i,x in enumerate(rows):
    self.assertEqual(x['seq'],i);self.assertEqual(x['prev'],last)
    last=x['sha256'];self.assertEqual(last,digest({k:v for k,v in x.items() if k!='sha256'}))
 def test_prior_evidence_not_overwritten(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'events.jsonl';j=EvidenceLog(p);j.add('HEADER',{});j.close()
   with self.assertRaises(FileExistsError):EvidenceLog(p)

if __name__=='__main__':unittest.main(verbosity=2)
