"""R12 original Value deadline closer: no signing, no Broker effect."""
import json,tempfile,unittest
from pathlib import Path
from historical_training.value_request_trigger import (
    BoundedRetry,Watcher,MAX_ATTEMPTS,MAX_TERMINAL_AGE_MS,
    ORIGINAL_DEADLINE_MS,RETRY_AFTER_MS,TERMINAL_RETRY_AFTER_MS)

class TerminalClosureTests(unittest.TestCase):
    def test_four_original_early_attempts_then_one_terminal_late(self):
        with tempfile.TemporaryDirectory() as t:
            now=[100000];q=BoundedRetry(t,'p',lambda:now[0])
            rid='a'*32
            q.offer([{'request_id':rid,'started_at_ms':now[0]}])
            calls=[]
            for i in range(MAX_ATTEMPTS):
                now[0]=100000+i*RETRY_AFTER_MS
                actions=q.tick(lambda:'inactive',lambda:calls.append(now[0]))
                self.assertEqual(len(actions),1)
                self.assertEqual(actions[0]['attempt'],i+1)
                self.assertNotIn('purpose',actions[0])
            now[0]=156000
            self.assertEqual(q.tick(lambda:'inactive',lambda:calls.append(now[0])),[])
            now[0]=160000
            self.assertEqual(q.tick(lambda:'inactive',lambda:calls.append(now[0])),[])
            now[0]=160001
            actions=q.tick(lambda:'inactive',lambda:calls.append(now[0]))
            self.assertEqual(len(actions),1)
            self.assertEqual(actions[0]['purpose'],'EXPIRED_ORIGINAL_TERMINAL_FOLLOWTHROUGH')
            self.assertIs(actions[0]['prediction_backdated'],False)
            self.assertEqual(q.pending[rid]['attempts'],MAX_ATTEMPTS)
            self.assertEqual(len(calls),MAX_ATTEMPTS+1)
    def test_immutable_terminals_stop_any_after_deadline_start(self):
        for terminal in ('commit.json','failure.json'):
            with tempfile.TemporaryDirectory() as t:
                now=[170000];rid='b'*32;root=Path(t)
                q=BoundedRetry(root,'p',lambda:now[0])
                q.offer([{'request_id':rid,'started_at_ms':100000,'closure_only':True}])
                folder=root/'plans/p/predictions'/rid;folder.mkdir(parents=True)
                (folder/terminal).write_text('{}')
                self.assertEqual(q.tick(lambda:'inactive',
                    lambda:self.fail('forbidden repeat')),[])
                self.assertNotIn(rid,q.pending)
    def test_busy_worker_defers_without_consuming_terminal_budget(self):
        with tempfile.TemporaryDirectory() as t:
            now=[160001];rid='c'*32
            q=BoundedRetry(t,'p',lambda:now[0])
            q.offer([{'request_id':rid,'started_at_ms':100000,'closure_only':True}])
            calls=[]
            self.assertEqual(q.tick(lambda:'active',lambda:calls.append(1)),[])
            self.assertEqual(q.pending[rid]['terminal_attempts'],0)
            now[0]+=1000
            self.assertEqual(len(q.tick(lambda:'inactive',lambda:calls.append(1))),1)
            self.assertEqual(len(calls),1)
    def test_bounded_two_terminal_tries_only_in_three_minutes(self):
        with tempfile.TemporaryDirectory() as t:
            now=[160001];rid='d'*32
            q=BoundedRetry(t,'p',lambda:now[0])
            q.offer([{'request_id':rid,'started_at_ms':100000,'closure_only':True}])
            calls=[]
            q.tick(lambda:'inactive',lambda:calls.append(now[0]))
            now[0]+=TERMINAL_RETRY_AFTER_MS
            q.tick(lambda:'inactive',lambda:calls.append(now[0]))
            now[0]+=TERMINAL_RETRY_AFTER_MS
            self.assertEqual(q.tick(lambda:'inactive',lambda:calls.append(now[0])),[])
            self.assertEqual(len(calls),2)
            now[0]=100000+MAX_TERMINAL_AGE_MS+1
            self.assertEqual(q.tick(lambda:'inactive',lambda:calls.append(now[0])),[])
            self.assertFalse(q.pending)
    def test_fresh_prediction_priority_over_expired_terminal(self):
        with tempfile.TemporaryDirectory() as t:
            now=[160000];rid='e'*32;fresh='f'*32;root=Path(t)
            q=BoundedRetry(root,'p',lambda:now[0])
            q.offer([{'request_id':rid,'started_at_ms':100000,'closure_only':True},
                     {'request_id':fresh,'started_at_ms':159000}])
            calls=[]
            act=q.tick(lambda:'inactive',lambda:calls.append(now[0]))
            self.assertEqual(len(act),1)
            self.assertEqual(act[0]['request_id'],fresh)
            self.assertNotIn('purpose',act[0])
            f=root/'plans/p/predictions'/fresh;f.mkdir(parents=True)
            (f/'commit.json').write_text('{}')
            now[0]+=1000
            act=q.tick(lambda:'inactive',lambda:calls.append(now[0]))
            self.assertEqual(len(act),1)
            self.assertEqual(act[0]['request_id'],rid)
            self.assertEqual(act[0]['purpose'],'EXPIRED_ORIGINAL_TERMINAL_FOLLOWTHROUGH')
    def test_arrival_between_55_and_60_seconds_is_terminal_only(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);root.chmod(0o700);p=root/'late.jsonl'
            p.write_text('');p.chmod(0o600)
            now=[160000];w=Watcher(root,lambda:now[0])
            start=104500  # age 55,500ms: too late for normal R5 start
            e={'kind':'REQUEST','written_at_ms':start+1,'data':{
                'request_id':'9'*32,'started_at_ms':start,'position_at_request':0}}
            with p.open('a') as f:f.write(json.dumps(e)+'\n')
            got=w.poll();self.assertEqual(len(got),1)
            self.assertIs(got[0]['closure_only'],True)
            q=BoundedRetry(root,'p',lambda:now[0]);q.offer(got)
            sends=[]
            self.assertEqual(q.tick(lambda:'inactive',lambda:sends.append(now[0])),[])
            now[0]=start+ORIGINAL_DEADLINE_MS
            self.assertEqual(q.tick(lambda:'inactive',lambda:sends.append(now[0])),[])
            now[0]+=1
            act=q.tick(lambda:'inactive',lambda:sends.append(now[0]))
            self.assertEqual(len(act),1)
            self.assertEqual(act[0]['request_age_ms'],60001)
            self.assertFalse(act[0]['prediction_backdated'])
            self.assertEqual(len(sends),1)
    def test_restart_recent_expired_only_and_never_old_history(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);root.chmod(0o700);p=root/'segment.jsonl'
            def row(i,age,position=0):
                ms=300000-age
                return {'kind':'REQUEST','written_at_ms':ms+1,'data':{
                    'request_id':format(i,'032x'),'started_at_ms':ms,
                    'position_at_request':position}}
            items=[row(2,80000),row(3,180000),row(4,180001),row(9,56000),
                   row(5,50000),row(6,70000,1)]
            p.write_text(''.join(json.dumps(x)+'\n' for x in items));p.chmod(0o600)
            w=Watcher(root,lambda:300000)
            self.assertEqual(w.poll(),[])
            rows=w.recent_expired()
            self.assertEqual({x['request_id'] for x in rows},{format(2,'032x'),format(3,'032x'),format(9,'032x')})
            self.assertTrue(all(x.get('closure_only') is True for x in rows))
            self.assertEqual([x['request_id'] for x in w.recent_fresh()],
                [format(5,'032x')])
    def test_delayed_original_append_is_only_terminal_not_prediction(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);root.chmod(0o700);p=root/'live.jsonl'
            p.write_text('');p.chmod(0o600)
            w=Watcher(root,lambda:200000)
            e={'kind':'REQUEST','written_at_ms':135001,'data':{
                'request_id':'7'*32,'started_at_ms':135000,'position_at_request':0}}
            with p.open('a') as f:f.write(json.dumps(e)+'\n')
            result=w.poll()
            self.assertEqual(len(result),1)
            self.assertTrue(result[0]['closure_only'])
            q=BoundedRetry(root,'p',lambda:200000);q.offer(result)
            act=q.tick(lambda:'inactive',lambda:None)
            self.assertEqual(act[0]['purpose'],'EXPIRED_ORIGINAL_TERMINAL_FOLLOWTHROUGH')
            self.assertFalse(act[0]['prediction_backdated'])
    def test_expired_without_terminal_scope_never_offered_on_restart(self):
        with tempfile.TemporaryDirectory() as t:
            now=[160000];q=BoundedRetry(t,'p',lambda:now[0])
            q.offer([{'request_id':'8'*32,'started_at_ms':100000}])
            self.assertFalse(q.pending)
            q.offer([{'request_id':'8'*32,'started_at_ms':100000,'closure_only':True}])
            self.assertEqual(len(q.pending),1)
            now[0]=100000+MAX_TERMINAL_AGE_MS+1
            self.assertEqual(q.tick(lambda:'inactive',lambda:self.fail('stale')),[])
            self.assertFalse(q.pending)
if __name__=='__main__':unittest.main()
