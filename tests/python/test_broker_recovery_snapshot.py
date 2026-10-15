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
    def test_historical_farm_2105_is_only_unscoped_status_not_approval(self):
        self.assertTrue(reader.unscoped_historical_farm_disconnect(-1,2105))
        for rid, code in ((7302,2105),(7401,2105),(0,2105),
                          (True,2105),(-1,2110),(-1,1100),(-1,2103)):
            with self.subTest(request_id=rid,code=code):
                self.assertFalse(reader.unscoped_historical_farm_disconnect(rid,code))

    def test_private_snapshot_rejects_links_and_open_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data';p.write_bytes(b'{}');p.chmod(0o600)
            self.assertEqual(reader.private_bytes(p),b'{}')
            q=Path(d)/'alias';q.symlink_to(p)
            with self.assertRaises(OSError):reader.private_bytes(q)
            p.chmod(0o644)
            with self.assertRaises(ValueError):reader.private_bytes(p)


    def test_realistic_oms_journal_above_32mib_is_read_without_relaxing_other_files(self):
        # Production OMS is bounded to 64 MiB; the prior generic 32 MiB
        # helper rejected a real 35.8 MB journal before connecting to IB.
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'oms.jsonl'
            with p.open('wb') as f:f.truncate(34*1024*1024)
            p.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'PRIVATE_SOURCE_REQUIRED'):
                reader.private_bytes(p)  # private config default remains 32 MiB
            data=reader.private_execution_journal_bytes(p)
            self.assertEqual(len(data),34*1024*1024)
            self.assertEqual(data[:1],b'\0')
            with p.open('r+b') as f:f.truncate(64*1024*1024+1)
            with self.assertRaisesRegex(ValueError,'PRIVATE_SOURCE_REQUIRED'):
                reader.private_execution_journal_bytes(p)
            p.chmod(0o644)
            with self.assertRaisesRegex(ValueError,'PRIVATE_SOURCE_REQUIRED'):
                reader.private_execution_journal_bytes(p)


if __name__=='__main__':unittest.main()
