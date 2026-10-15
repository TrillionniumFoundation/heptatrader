import json,os,tempfile,unittest
from pathlib import Path
from historical_training.value_request_trigger import Watcher

class TailTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.root=Path(self.tmp.name);self.root.chmod(0o700)
  self.p=self.root/'1.jsonl';self.p.write_text('')
  self.p.chmod(0o600);self.now=100000
 def tearDown(self):self.tmp.cleanup()
 def append(self,**kw):
  r={'kind':'REQUEST','written_at_ms':self.now,'data':{'request_id':'id','started_at_ms':self.now-1000,'position_at_request':0}}
  r.update(kw)
  with self.p.open('a') as f:f.write(json.dumps(r)+'\n')
 def test_old_records_skipped_and_fresh_once(self):
  self.append();w=Watcher(self.root,lambda:self.now)
  self.assertEqual(w.poll(),[])
  self.append(data={'request_id':'new','started_at_ms':self.now-1000,'position_at_request':0})
  self.assertEqual([x['request_id'] for x in w.poll()],['new'])
  self.assertEqual(w.poll(),[])
 def test_old_or_nonflat_not_triggered(self):
  w=Watcher(self.root,lambda:self.now)
  self.append(data={'request_id':'old','started_at_ms':self.now-70000,'position_at_request':0})
  self.append(data={'request_id':'pos','started_at_ms':self.now-1,'position_at_request':10})
  self.assertEqual(w.poll(),[])
 def test_new_segment_and_file_rotation(self):
  w=Watcher(self.root,lambda:self.now)
  q=self.root/'2.jsonl';q.write_text('');q.chmod(0o600)
  with q.open('a') as f:f.write(json.dumps({'kind':'REQUEST','written_at_ms':self.now,
   'data':{'request_id':'new','started_at_ms':self.now-1,'position_at_request':0}})+'\n')
  self.assertEqual([x['request_id'] for x in w.poll()],['new'])
  q.unlink();q.write_text('');q.chmod(0o600)
  self.assertEqual(w.poll(),[])
 def test_nonprivate_rejected(self):
  self.p.chmod(0o644)
  with self.assertRaises(ValueError):Watcher(self.root,lambda:self.now)


from historical_training.value_request_trigger import BoundedRetry,RETRY_AFTER_MS,MAX_ATTEMPTS,MAX_START_AGE_MS

class RetryTests(unittest.TestCase):
 def test_bounded_retry_obeys_deadline_and_backoff(self):
  with tempfile.TemporaryDirectory() as t:
   now=[100000];x=BoundedRetry(t,'p',lambda:now[0])
   x.offer([{'request_id':'a'*32,'started_at_ms':now[0]}])
   sent=[]
   def go():sent.append(now[0])
   self.assertEqual(len(x.tick(lambda:'inactive',go)),1)
   self.assertFalse(x.tick(lambda:'inactive',go))
   for i in range(1,MAX_ATTEMPTS):
    now[0]=100000+i*RETRY_AFTER_MS
    x.tick(lambda:'inactive',go)
   now[0]+=RETRY_AFTER_MS
   self.assertFalse(x.tick(lambda:'inactive',go))
   self.assertEqual(len(sent),MAX_ATTEMPTS)
   self.assertLessEqual(max(sent)-100000,MAX_START_AGE_MS)
 def test_only_one_start_per_tick_for_multiple_requests(self):
  with tempfile.TemporaryDirectory() as t:
   now=[100000];q=BoundedRetry(t,'p',lambda:now[0])
   q.offer([{'request_id':'a'*32,'started_at_ms':now[0]},
            {'request_id':'b'*32,'started_at_ms':now[0]}])
   sent=[]
   actions=q.tick(lambda:'inactive',lambda:sent.append(now[0]))
   self.assertEqual(len(sent),1)
   self.assertEqual(len(actions),1)
   self.assertEqual(sum(v['attempts'] for v in q.pending.values()),1)
 def test_worker_busy_defers_without_consuming_attempt(self):
  with tempfile.TemporaryDirectory() as t:
   now=[100000];x=BoundedRetry(t,'p',lambda:now[0])
   x.offer([{'request_id':'b'*32,'started_at_ms':now[0]}])
   calls=[]
   self.assertEqual(x.tick(lambda:'activating',lambda:calls.append(1)),[])
   self.assertEqual(x.pending['b'*32]['attempts'],0)
   now[0]+=1000
   self.assertEqual(len(x.tick(lambda:'inactive',lambda:calls.append(1))),1)
 def test_terminal_commit_prevents_retry(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);now=[100000];x=BoundedRetry(root,'p',lambda:now[0])
   rid='c'*32
   x.offer([{'request_id':rid,'started_at_ms':now[0]}])
   x.tick(lambda:'inactive',lambda:None)
   folder=root/'plans/p/predictions'/rid;folder.mkdir(parents=True)
   (folder/'commit.json').write_text('{}')
   now[0]+=RETRY_AFTER_MS
   self.assertEqual(x.tick(lambda:'inactive',lambda:self.fail('retried signed')),[])
   self.assertNotIn(rid,x.pending)
 def test_expired_request_never_started(self):
  with tempfile.TemporaryDirectory() as t:
   x=BoundedRetry(t,'p',lambda:100000)
   x.offer([{'request_id':'d'*32,'started_at_ms':100000-MAX_START_AGE_MS-1}])
   self.assertEqual(x.pending,{})
 def test_recover_only_fresh_on_restart(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);root.chmod(0o700);path=root/'segment.jsonl'
   rows=[]
   for rid,ts,pos in [('e'*32,95000,0),('f'*32,2000,0),('1'*32,96000,1)]:
    rows.append({'kind':'REQUEST','written_at_ms':ts,
                 'data':{'request_id':rid,'started_at_ms':ts,'position_at_request':pos}})
   path.write_text(''.join(json.dumps(q)+'\n' for q in rows));path.chmod(0o600)
   watcher=Watcher(root,lambda:100000)
   self.assertEqual([e['request_id'] for e in watcher.recent_fresh()],['e'*32])
   self.assertEqual(watcher.poll(),[])
 def test_restart_partial_record_completes_once(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);root.chmod(0o700);path=root/'segment.jsonl'
   r={'kind':'REQUEST','written_at_ms':100000,
     'data':{'request_id':'a'*32,'started_at_ms':100000,'position_at_request':0}}
   serialized=json.dumps(r)
   path.write_text(serialized[:30]);path.chmod(0o600)
   watcher=Watcher(root,lambda:100000)
   with path.open('a') as f:f.write(serialized[30:]+'\n')
   self.assertEqual([x['request_id'] for x in watcher.poll()],['a'*32])

if __name__=='__main__':unittest.main()
