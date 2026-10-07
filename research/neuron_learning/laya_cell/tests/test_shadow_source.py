"""Synthetic integrity probes, never labeled real strategy outcomes."""
import copy,json,os,tempfile,time,unittest
from pathlib import Path
from laya_cell.shadow_source import canonical,digest,read_source,valid_quote

T=1791330000000

def quote():
    return {'instrument':'EUR.USD','source':'IB','authoritative':True,'stale':False,
            'bid':'1.12500','ask':'1.12502','observed_at_ms':T,'stale_after_ms':T+5000,
            'subscription_id':'UNIT-FIXTURE','subscription_state':'active'}

class ShadowEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'quotes.jsonl'
        self.header={'source':'IB_TOOL_GATEWAY','instrument':'EUR.USD','execution_domain':'PAPER',
                     'period_sha256':'1'*64,'write_operations_allowed':[],'broker_orders_sent':0}
    def write(self,entries=None):
        entries=entries or [('HEADER',self.header,T),('QUOTE',quote(),T+2)]
        prev='0'*64;lines=[]
        for seq,(kind,data,at) in enumerate(entries):
            body={'schema':'heptatrader.laya-shadow-quotes.v1','sequence':seq,'previous':prev,'at_ms':at,'kind':kind,'data':data}
            row={**body,'sha256':digest(body)};prev=row['sha256'];lines.append(canonical(row))
        self.path.write_bytes(b'\n'.join(lines)+b'\n');self.path.chmod(0o600)
    def test_valid_source_chain_and_numeric_mid(self):
        self.write();h,q,p=read_source(self.path)
        self.assertEqual(len(q),1);self.assertAlmostEqual(q[0]['mid'],1.12501)
        self.assertEqual(p['uncommitted_tail_bytes'],0);self.assertFalse(p['ended'])
    def test_quote_only_no_header_rejected(self):
        self.write([('QUOTE',quote(),T)])
        with self.assertRaisesRegex(ValueError,'SCOPE'):read_source(self.path)
    def test_live_or_synthetic_header_rejected(self):
        for field,value in [('execution_domain','LIVE'),('source','SYNTHETIC')]:
            h={**self.header,field:value};self.write([('HEADER',h,T)])
            with self.assertRaisesRegex(ValueError,'SCOPE'):read_source(self.path)
    def test_write_authority_cannot_be_inferred(self):
        self.write([('HEADER',{**self.header,'write_operations_allowed':['submit']},T)])
        with self.assertRaisesRegex(ValueError,'SCOPE'):read_source(self.path)
    def test_authority_missing_rejected_even_with_valid_hash(self):
        q=quote();q['authoritative']=False;self.write([('HEADER',self.header,T),('QUOTE',q,T+1)])
        with self.assertRaisesRegex(ValueError,'AUTHORITY'):read_source(self.path)
    def test_stale_flag_rejected_even_with_valid_hash(self):
        q=quote();q['stale']=True;self.write([('HEADER',self.header,T),('QUOTE',q,T+1)])
        with self.assertRaisesRegex(ValueError,'AUTHORITY'):read_source(self.path)
    def test_ttl_exactly_preserved(self):
        self.write([('HEADER',self.header,T),('QUOTE',quote(),T+5001)])
        with self.assertRaisesRegex(ValueError,'STALE'):read_source(self.path)
    def test_future_quote_timestamp_not_accepted(self):
        q=quote();q['observed_at_ms']=T+10;self.write([('HEADER',self.header,T),('QUOTE',q,T+1)])
        with self.assertRaisesRegex(ValueError,'FUTURE'):read_source(self.path)
    def test_same_timestamp_price_conflict_rejected(self):
        q=quote();other={**q,'bid':'1.12499'}
        self.write([('HEADER',self.header,T),('QUOTE',q,T+1),('QUOTE',other,T+2)])
        with self.assertRaisesRegex(ValueError,'CONFLICT'):read_source(self.path)
    def test_numeric_alias_not_conflict(self):
        q=quote();other={**q,'bid':1.125,'ask':1.12502}
        self.write([('HEADER',self.header,T),('QUOTE',q,T+1),('QUOTE',other,T+2)])
        self.assertEqual(len(read_source(self.path)[1]),1)
    def test_trailing_uncommitted_bytes_not_outcome(self):
        self.write();self.path.write_bytes(self.path.read_bytes()+b'{"kind":"QUOTE"')
        h,q,p=read_source(self.path);self.assertEqual(len(q),1);self.assertGreater(p['uncommitted_tail_bytes'],0)
    def test_tampered_bytes_rejected(self):
        self.write();self.path.write_bytes(self.path.read_bytes().replace(b'1.12500',b'1.12501'))
        with self.assertRaisesRegex(ValueError,'CHAIN'):read_source(self.path)
    def test_no_record_after_end(self):
        self.write([('HEADER',self.header,T),('END',{},T+1),('QUOTE',quote(),T+2)])
        with self.assertRaisesRegex(ValueError,'AFTER_END'):read_source(self.path)
    def test_symlink_and_public_files_refused(self):
        self.write();link=self.root/'link';link.symlink_to(self.path)
        with self.assertRaises(OSError):read_source(link)
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'FILE_BOUND'):read_source(self.path)
    def test_duplicate_json_key_not_last_wins(self):
        self.write();self.path.write_bytes(self.path.read_bytes().replace(b'"kind":"QUOTE"',b'"kind":"FAILURE","kind":"QUOTE"'))
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):read_source(self.path)
    def test_failure_is_not_fabricated_quote(self):
        self.write([('HEADER',self.header,T),('FAILURE',{'reason':'QUOTE_STALE'},T+1)])
        self.assertEqual(read_source(self.path)[1],[])

if __name__=='__main__':unittest.main(verbosity=2)
