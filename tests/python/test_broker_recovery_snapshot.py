"""Broker recovery reader transport tests; never connect to a broker."""
from pathlib import Path
import sys,tempfile,unittest,os
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import hepta_broker_recovery_snapshot as reader


def wire(parts):return '\0'.join(parts)+'\0'


class GateTests(unittest.TestCase):
    def gate(self):return reader.RecoveryReadGate(29891,'DU123',{'EUR.USD':701,'BTC.USD':701})
    def started(self):
        g=self.gate();g.check(wire(g.expected['start']));return g
    def test_exact_read_set_and_single_cancellations(self):
        g=self.gate()
        for _,fields in g.expected.items():g.check(wire(fields))
        self.assertEqual(dict(g.counts),{'71':1,'16':1,'99':1,'74':1,'76':2,'75':1,'77':2,'7':2})
    def test_all_mutations_quotes_binding_and_unowned_cancel_denied(self):
        for op in ('3','4','58','5','15','1','97','98','18','54'):
            with self.subTest(op=op),self.assertRaises(ValueError):
                self.started().check(wire([op,'1','1']))
        with self.assertRaises(ValueError):self.started().check(wire(['75','1','7301']))
    def test_wrong_account_owner_and_duplicates_rejected(self):
        g=self.started()
        for key in ('ledger','aggregate','positions','EUR.USD','BTC.USD'):
            bad=[x.replace('DU123','DU999') for x in g.expected[key]]
            with self.assertRaises(ValueError):g.check(wire(bad))
        altered=g.expected['EUR.USD'][:];altered[3]='702'
        with self.assertRaises(ValueError):g.check(wire(altered))
        g.check(wire(g.expected['open']))
        with self.assertRaises(ValueError):g.check(wire(g.expected['open']))
    def test_no_reads_before_handshake_and_no_owner_collision(self):
        with self.assertRaises(ValueError):self.gate().check(wire(['16','1']))
        for cid in (0,True,701,30000):
            with self.assertRaises(ValueError):reader.RecoveryReadGate(cid,'DU123',{'EUR.USD':701,'BTC.USD':701})
        with self.assertRaises(ValueError):reader.RecoveryReadGate(29891,'DU123',{'EUR.USD':29891,'BTC.USD':701})
    def test_private_snapshot_rejects_links_and_open_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data';p.write_bytes(b'{}');p.chmod(0o600)
            self.assertEqual(reader.private_bytes(p),b'{}')
            q=Path(d)/'alias';q.symlink_to(p)
            with self.assertRaises(OSError):reader.private_bytes(q)
            p.chmod(0o644)
            with self.assertRaises(ValueError):reader.private_bytes(p)


if __name__=='__main__':unittest.main()
