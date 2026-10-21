"""Offline frame-boundary tests. No connection to IB or a production service."""
from pathlib import Path
import json
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import hepta_native_quote_gate as gate


def packet(payload):
    return struct.pack('!I',len(payload))+payload


def fields(*parts):
    return ('\0'.join(parts)+'\0').encode('ascii')


def quote(req=5100001,symbol='EUR'):
    return fields('1','11',str(req),'0',symbol,'CASH' if symbol=='EUR' else 'CRYPTO',
                  '','0.0','','','IDEALPRO' if symbol=='EUR' else 'PAXOS','','USD','','','0','','0','0','')


def initialized():
    g=gate.FrameGate(29831)
    g.feed(b'API\0'+packet(b'v100..178'))
    g.feed(packet(fields('71','2','29831','')))
    return g


class FrameGateTests(unittest.TestCase):
    def test_fragmented_handshake_and_frames(self):
        data=b'API\0'+packet(b'v100..178')+packet(fields('71','2','29831',''))
        g=gate.FrameGate(29831);output=[]
        for b in data:output.extend(g.feed(bytes([b])))
        self.assertEqual(b''.join(output),data)
        req=packet(quote());parts=[]
        for b in req:parts.extend(g.feed(bytes([b])))
        self.assertEqual(b''.join(parts),req)
        self.assertEqual(g.active,{5100001:'EUR'})

    def test_orders_accounts_and_unknown_frames_never_forward(self):
        for op in ('3','4','58','76','74','6','7','97','98','999'):
            g=initialized();bad=packet(fields(op,'1','2'))
            with self.subTest(op=op):
                self.assertEqual(g.feed(bad[:-1]),[])
                with self.assertRaises(gate.Denied):g.feed(bad[-1:])
                self.assertNotIn(op,g.counts)

    def test_atomic_batch_failure_does_not_return_forbidden_frame(self):
        g=initialized()
        with self.assertRaises(gate.Denied):
            g.feed(packet(quote())+packet(fields('3','45','1')))
        self.assertNotIn('3',g.counts)

    def test_live_mode_and_owned_cancel_only(self):
        g=initialized()
        g.feed(packet(fields('8','1','1')))
        g.feed(packet(fields('59','1','1')))
        for mode in ('2','3','4'):
            with self.assertRaises(gate.Denied):g.feed(packet(fields('59','1',mode)))
        g.feed(packet(quote()));g.feed(packet(quote(5100002,'BTC')))
        self.assertEqual(len(g.active),2)
        with self.assertRaises(gate.Denied):g.feed(packet(fields('2','2','9999999')))
        g.feed(packet(fields('2','2','5100001')))
        with self.assertRaises(gate.Denied):g.feed(packet(quote()))
        g.feed(packet(quote(5100003)))
        self.assertEqual(g.active[5100003],'EUR')

    def test_bad_contracts_and_request_identity_fail_closed(self):
        for raw in (quote().replace(b'EUR',b'GBP'),quote().replace(b'IDEALPRO',b'SMART'),
                    quote().replace(b'USD',b'JPY'),quote(1),quote(5300000),
                    quote().replace(b'0.0',b'1.0')):
            with self.subTest(raw=raw),self.assertRaises(gate.Denied):initialized().feed(packet(raw))

    def test_duplicate_primary_and_reused_identity_denied(self):
        g=initialized();g.feed(packet(quote(5100001,'BTC')))
        with self.assertRaises(gate.Denied):g.feed(packet(quote(5100002,'BTC')))
        with self.assertRaises(gate.Denied):g.feed(packet(quote(5100001)))

    def test_one_secondary_replacement_can_overlap_active_pair(self):
        g=initialized()
        g.feed(packet(quote(5100001,'BTC')))
        g.feed(packet(quote(5100002)))
        replacement=packet(quote(5100003))
        self.assertEqual(g.feed(replacement),[replacement])
        self.assertEqual(g.active,{5100001:'BTC',5100002:'EUR',5100003:'EUR'})
        with self.assertRaises(gate.Denied):g.feed(packet(quote(5100004)))
        with self.assertRaises(gate.Denied):g.feed(packet(quote(5100005,'BTC')))

    def test_secondary_retirement_preserves_identity_and_slot_bounds(self):
        g=initialized()
        for request in (quote(5100001,'BTC'),quote(5100002),quote(5100003)):
            g.feed(packet(request))
        g.feed(packet(fields('2','2','5100003')))
        with self.assertRaises(gate.Denied):g.feed(packet(quote(5100003)))
        g.feed(packet(quote(5100004)))
        self.assertEqual(g.active,{5100001:'BTC',5100002:'EUR',5100004:'EUR'})
        with self.assertRaises(gate.Denied):g.feed(packet(fields('2','2','5100003')))

    def test_invalid_handshake_bounds_and_client_id(self):
        for data in (b'GET ',b'API\0'+packet(b'v100..178 unsafe'),b'API\0'+struct.pack('!I',999999)):
            with self.assertRaises(gate.Denied):gate.FrameGate(29831).feed(data)
        for value in (0,1000,30000,True):
            with self.assertRaises(gate.Denied):gate.FrameGate(value)
        g=gate.FrameGate(29831);g.feed(b'API\0'+packet(b'v100..178'))
        with self.assertRaises(gate.Denied):g.feed(packet(fields('71','2','29832')))

    def test_owner_collision_and_exact_paper_scope(self):
        with tempfile.TemporaryDirectory() as d:
            h=Path(d);p=h/'.config/heptatrader';p.mkdir(parents=True)
            (p/'account-observer.json').write_text(json.dumps({'client_id':29831}))
            period={'execution_domain':'PAPER','instruments':{'EUR.USD':{'broker_client_id':29832}}}
            (p/'pnl-period.json').write_text(json.dumps(period))
            for n in (29831,29832):
                with self.assertRaises(gate.Denied):gate.validate_owner(n,h)
            gate.validate_owner(29833,h)
            period['execution_domain']='LIVE';(p/'pnl-period.json').write_text(json.dumps(period))
            with self.assertRaises(gate.Denied):gate.validate_owner(29833,h)


if __name__=='__main__':unittest.main()
