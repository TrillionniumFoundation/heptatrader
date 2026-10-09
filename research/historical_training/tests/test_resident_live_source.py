import copy,hashlib,json,os,tempfile,time,unittest
from pathlib import Path
from historical_training.common import digest
from historical_training.resident_live_source import refresh
from historical_training.resident_resource_guard import assess,SOURCE_RULE,GIB
from laya_cell.shadow_source import read_source,canonical

class LiveSourceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.path=self.root/'segment-1/quotes.jsonl';self.path.parent.mkdir(mode=0o700)
  self.now=int(time.time()*1000);self.period='a'*64
  self.original={'feed_directory':str(self.root),'period_sha256':self.period}
  self.header={'schema':'heptatrader.laya-shadow-quotes.v1','sequence':0,'previous':'0'*64,'at_ms':self.now-60000,'kind':'HEADER',
    'data':{'source':'IB_TOOL_GATEWAY','execution_domain':'PAPER','instrument':'EUR.USD','write_operations_allowed':[],'broker_orders_sent':0,'period_sha256':self.period}}
  self.header['sha256']=digest(self.header);raw=canonical(self.header)+b'\n'
  self.path.write_bytes(raw);self.path.chmod(0o600)
  pin={'path':str(self.path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'chain_head':self.header['sha256'],'ended':False}
  self.source={'schema':'heptatrader.quote-readiness-status.v1','at_ms':self.now-60000,'latest_source_quote_ms':self.now-60000,
   'source_prefixes':[pin],'source_prefix_digest':digest([pin]),'period_sha256':self.period,'rule':SOURCE_RULE,'usable':24,'unusable':0,'state':'READY_FOR_REVIEW_NOT_AUTHORIZATION'}
  self.append_quote(self.now-500)
 def append_quote(self,t):
  r={'schema':'heptatrader.laya-shadow-quotes.v1','sequence':1,'previous':self.header['sha256'],'at_ms':t,'kind':'QUOTE',
   'data':{'instrument':'EUR.USD','source':'IB','authoritative':True,'stale':False,'subscription_id':'test','observed_at_ms':t,'stale_after_ms':t+5000,'bid':1.1,'ask':1.1001}}
  r['sha256']=digest(r)
  with self.path.open('ab') as f:f.write(canonical(r)+b'\n')
 def run_refresh(self):return refresh(self.original,self.source,self.now,read_source)
 def admission(self,s):return assess({'MemTotal':24*GIB,'MemAvailable':10*GIB,'SwapTotal':8*GIB,'SwapFree':2*GIB},{'avg10':0.,'avg60':0.},s,self.now,self.period,False)
 def test_original_verified_extension_is_fresh_without_touching_cache(self):
  old=copy.deepcopy(self.source);new,proof=self.run_refresh()
  self.assertEqual(self.source,old);self.assertEqual(new['at_ms'],old['at_ms']);self.assertTrue(self.admission(new)['admit_new_worker'])
  self.assertFalse(proof['quote_timestamp_rewritten']);self.assertEqual(proof['quote_observed_at_ms'],self.now-500)
 def test_live_quote_does_not_authorize_stale_continuity(self):
  self.source['at_ms']=self.now-120001;new,_=self.run_refresh();self.assertFalse(self.admission(new)['admit_new_worker'])
 def test_original_coverage_failure_is_retained(self):
  self.source.update(usable=19,unusable=5);new,_=self.run_refresh();self.assertFalse(self.admission(new)['admit_new_worker'])
 def test_live_quote_does_not_relax_5000ms(self):
  self.now+=6000;new,_=self.run_refresh();self.assertIn('LATEST_SOURCE_QUOTE_NOT_FRESH',self.admission(new)['reasons'])
 def test_prefix_digest_corruption_rejected(self):
  self.source['source_prefix_digest']='b'*64
  with self.assertRaises(ValueError):self.run_refresh()
 def test_prefix_hash_corruption_rejected(self):
  self.source['source_prefixes'][0]['sha256']='b'*64;self.source['source_prefix_digest']=digest(self.source['source_prefixes'])
  with self.assertRaises(ValueError):self.run_refresh()
 def test_cross_period_rejected(self):
  self.original['period_sha256']='b'*64
  with self.assertRaises(ValueError):self.run_refresh()
 def test_source_symlink_rejected(self):
  target=self.path.with_name('other');self.path.rename(target);self.path.symlink_to(target)
  with self.assertRaises(ValueError):self.run_refresh()
 def test_world_readable_file_rejected(self):
  self.path.chmod(0o644)
  with self.assertRaises(ValueError):self.run_refresh()
 def test_partial_tail_is_not_accepted_as_quote(self):
  with self.path.open('ab') as f:f.write(b'{')
  _,proof=self.run_refresh();self.assertEqual(proof['verified_live_prefix']['uncommitted_tail_bytes'],1)
 def test_tampered_chain_rejected(self):
  raw=self.path.read_bytes().replace(b'1.1001',b'1.2001');self.path.write_bytes(raw)
  with self.assertRaises(ValueError):self.run_refresh()
 def test_unknown_feed_rejected(self):
  self.original['feed_directory']=str(self.root/'other')
  with self.assertRaises(ValueError):self.run_refresh()

 def test_closed_recovery_name_cannot_shadow_active_segment(self):
  old=self.root/'segment-recovery-live/quotes.jsonl';old.parent.mkdir(mode=0o700);old.write_bytes(self.path.read_bytes());old.chmod(0o600)
  prior=copy.deepcopy(self.source['source_prefixes'][0]);prior.update(path=str(old),ended=True)
  self.source['source_prefixes'].append(prior);self.source['source_prefix_digest']=digest(self.source['source_prefixes'])
  _,proof=self.run_refresh();self.assertEqual(proof['verified_live_prefix']['path'],str(self.path))
 def test_no_open_registered_segment_denies(self):
  self.source['source_prefixes'][0]['ended']=True;self.source['source_prefix_digest']=digest(self.source['source_prefixes'])
  with self.assertRaises(ValueError):self.run_refresh()
