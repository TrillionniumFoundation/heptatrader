"""Distinct immutable behavior producers share a directory, never model credit.
All fixtures below are synthetic and use a fake driver with no broker interface.
"""
from pathlib import Path
import unittest,tempfile,sys,hashlib,json
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests/python'),str(ROOT/'research/neuron_learning')]
import hepta_decision_attribution as a
from test_decision_attribution import Host
from laya_cell.financial_forward_source import ingress
class PartitionTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name);self.regs=[];self.journals=[]
  for label in ('old-25k','new-100k'):
   m={'schema':'heptatrader.attribution-producer.v1','period_sha256':'1'*64,'instrument':'EUR.USD','execution_domain':'PAPER','journal_directory':str(self.root/'journal'),'fixture_only':label}
   p=self.root/(label+'.json');p.write_text(a.canonical(m)+'\n');p.chmod(0o600);reg={'manifest':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
   host=Host();journal=a.Journal(self.root/'journal',m,reg['sha256']);close=a.Capture(journal,host,m).install()
   decision=host.model_decision('synthetic-model',self.root,[{'observed_at_ms':100,'bid':1.1,'ask':1.1001,'mid':1.10005}],0)
   host.prepare_send({'position':0,'pending':None,'last_decision':decision},self.root/'state')
   close();self.regs.append(reg);self.journals.append(journal)
 def check(self,i):return a.verified_bindings(self.regs[i],'EUR.USD','1'*64)
 def test_both_immutable_producers_are_separately_readable(self):
  old,e1=self.check(0);new,e2=self.check(1);self.assertEqual((len(old),len(new)),(1,1));self.assertFalse(e1 or e2);self.assertNotEqual(next(iter(old.values()))['input_id'],next(iter(new.values()))['input_id'])
 def test_forward_ingress_only_sees_registered_requests(self):
  for i in (0,1):
   _,requests,bindings,issues=ingress(self.regs[i],'1'*64);self.assertEqual((len(requests),len(bindings)),(1,1));self.assertFalse(issues)
 def test_corrupt_other_producer_not_silently_ignored(self):
  p=next((self.root/'journal').glob(self.journals[0].id+'-*.jsonl'));p.write_bytes(p.read_bytes().replace(b'test-model',b'evil-model'))
  with self.assertRaises(ValueError):self.check(1)
 def test_registered_manifest_cannot_be_replaced(self):
  Path(self.regs[1]['manifest']).write_text('{}')
  with self.assertRaises(ValueError):self.check(1)
 def test_old_producer_loss_does_not_contaminate_new(self):
  p=self.root/'journal'/(self.journals[0].id+'.health.json');h=json.loads(p.read_bytes());h['dropped']=1;p.write_text(a.canonical(h));p.chmod(0o600)
  self.assertEqual(len(self.check(1)[0]),1);self.assertEqual(len(self.check(0)[0]),0)
 def test_current_producer_loss_still_denies_credit(self):
  p=self.root/'journal'/(self.journals[1].id+'.health.json');h=json.loads(p.read_bytes());h['dropped']=1;p.write_text(a.canonical(h));p.chmod(0o600)
  self.assertEqual(len(self.check(1)[0]),0)
 def test_one_producer_cannot_switch_manifest_midstream(self):
  p=next((self.root/'journal').glob(self.journals[1].id+'-*.jsonl'));rs=[json.loads(x) for x in p.read_bytes().splitlines()];last=rs[-1];header=rs[0]['data'].copy();header['manifest_sha256']=self.regs[0]['sha256']
  body={k:v for k,v in last.items() if k!='sha256'};body.update(kind='HEADER',data=header,sequence=last['sequence']+1,previous=last['sha256']);row={**body,'sha256':a.digest(body)}
  with p.open('a') as f:f.write(a.canonical(row)+'\n')
  with self.assertRaises(ValueError):self.check(1)
 def test_foreign_period_never_borrows_current_policy(self):
  with self.assertRaises(ValueError):a.verified_bindings(self.regs[1],'EUR.USD','2'*64)
if __name__=='__main__':unittest.main(verbosity=2)
