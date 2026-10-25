"""Reward bookkeeping stays live while training admission is held."""
import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from laya_cell import resident as m
from laya_cell.integrity import ContractError,read_json
from laya_cell.resident_store import database,get_meta
from historical_training.resident_resource_guard import guarded_trainer_class

QUEUE={'pending_evaluations':1,'viable_pending_evaluations':1,
       'pending_original_trials':1,'futile_pending_trials':0,'deferred_training_candidates':0}
EMPTY={k:0 for k in QUEUE}
SNAPSHOT={'at_ms':100000,'reason':'ATTRIBUTED_SETTLED_REWARDS_READY',
          'rewards':[{'reward_id':'bound','realized_net_usd':'-22'}],
          'execution_rewards':[{'reward_id':'observed','learning_eligible':False,'realized_net_usd':'-23'}]}

class RewardSyncTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.s=m.Supervisor.__new__(m.Supervisor)
  self.s.root=self.root;self.s.c={'promotion_protocol':m.PROTOCOL};self.s.storage=None
  self.s.db=database(self.root/'scheduler.sqlite');self.addCleanup(self.s.db.close)
  self.clock=self.enterContext(patch.object(m.time,'monotonic',return_value=100.0))
  self.enterContext(patch.object(m.time,'time',return_value=100.0))
  self.enterContext(patch.object(m,'config_check'))
  self.enterContext(patch.object(m,'bootstrap_still_off'))
  self.storage=self.enterContext(patch.object(m,'inspect_storage',return_value={'admission_ready':True}))
  self.source=self.enterContext(patch.object(m,'rewards_current',return_value=copy.deepcopy(SNAPSHOT)))
  self.queue=self.enterContext(patch.object(self.s,'reconcile_evaluation_queue',return_value=copy.deepcopy(QUEUE)))
  self.collect=self.enterContext(patch.object(m,'collect',side_effect=AssertionError('no quote scan while held')))
 def counts(self):
  return tuple(self.s.db.execute('SELECT count(*) FROM '+t).fetchone()[0]
               for t in ('rewards','execution_rewards','jobs','consumed_rows'))
 def test_waiting_evaluation_refreshes_rewards_without_training(self):
  result=self.s.queue_preflight()
  self.assertEqual(result['state'],'EVALUATING_BOUNDED_CANDIDATE_QUEUE')
  self.assertEqual(self.counts(),(1,1,0,0));self.collect.assert_not_called()
  self.assertEqual(read_json(self.root/'rewards-status.json')['at_ms'],100000)
 def test_unguarded_step_refreshes_before_queue_hold(self):
  self.s.step();self.assertEqual(self.counts(),(1,1,0,0));self.collect.assert_not_called()
 def test_pretrained_candidate_hold_does_not_hold_bookkeeping(self):
  self.queue.return_value={**QUEUE,'deferred_training_candidates':1}
  self.assertEqual(self.s.queue_preflight()['state'],'CANDIDATE_READY_AWAITING_FROZEN_TRIAL_END')
  self.assertEqual(self.counts(),(1,1,0,0))
 def test_guarded_queue_hold_never_acquires_heavy_lease(self):
  gate=MagicMock();gate.status_diagnostics=None
  self.s.__class__=guarded_trainer_class(m.Supervisor,gate)
  self.s.step();self.assertEqual(self.counts(),(1,1,0,0));gate.acquire.assert_not_called()
 def test_resource_denial_does_not_stop_reward_sync(self):
  self.queue.return_value=EMPTY;gate=MagicMock();gate.acquire.return_value=False
  gate.status_diagnostics=None
  gate.last_admission={'reasons':['OTHER_RESEARCH_WORKER_ACTIVE']}
  self.s.__class__=guarded_trainer_class(m.Supervisor,gate)
  # Exercise resource rejection after cadence is due; bookkeeping still runs first.
  with patch.object(m.time,'time',return_value=1000.0):result=self.s.step()
  self.assertEqual(result['state'],'HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE')
  self.assertEqual(self.counts(),(1,1,0,0));self.collect.assert_not_called()
 def test_same_tick_and_subinterval_do_not_repeat_ledger_scan(self):
  self.s.queue_preflight();self.clock.return_value=110.0;self.s.step()
  self.source.assert_called_once();self.assertEqual(self.counts(),(1,1,0,0))
 def test_elapsed_interval_refreshes_idempotently(self):
  self.s.queue_preflight();self.clock.return_value=161.0;self.s.queue_preflight()
  self.assertEqual(self.source.call_count,2);self.assertEqual(self.counts(),(1,1,0,0))
 def test_conflict_rolls_back_new_rows_and_preserves_status(self):
  self.s.queue_preflight();before=(self.root/'rewards-status.json').read_bytes()
  self.source.return_value={**SNAPSHOT,'rewards':[{'reward_id':'new'},{'reward_id':'bound','realized_net_usd':'999'}]}
  self.clock.return_value=161.0
  with self.assertRaisesRegex(ContractError,'REWARD_ID_CONFLICT'):self.s.queue_preflight()
  self.assertEqual(self.counts(),(1,1,0,0));self.assertEqual(before,(self.root/'rewards-status.json').read_bytes())
 def test_execution_conflict_rolls_back_learning_rows_too(self):
  self.s.queue_preflight();self.clock.return_value=161.0
  self.source.return_value={**SNAPSHOT,'rewards':[{'reward_id':'new'}],
   'execution_rewards':[{'reward_id':'observed','realized_net_usd':'999'}]}
  with self.assertRaisesRegex(ContractError,'EXECUTION_REWARD_ID_CONFLICT'):self.s.queue_preflight()
  self.assertEqual(self.counts(),(1,1,0,0))
 def test_unavailable_source_clears_current_eligibility_not_history(self):
  self.s.queue_preflight();self.clock.return_value=161.0
  self.source.return_value={'at_ms':161000,'reason':'INCOMPLETE_OMS_TAIL','rewards':[],'execution_rewards':[]}
  self.s.queue_preflight();self.assertEqual(self.counts(),(1,1,0,0))
  self.assertEqual(get_meta(self.s.db,'active_reward_ids'),[])
  self.assertEqual(get_meta(self.s.db,'active_execution_reward_ids'),[])
 def test_sync_age_is_exposed_not_implied_by_running_service(self):
  result=self.s.queue_preflight()
  self.assertEqual(result['reward_sync_at_ms'],100000)
  self.assertEqual(result['reward_sync_interval_seconds'],60)
 def test_storage_exhaustion_still_refuses_new_bookkeeping_files(self):
  self.storage.return_value={'admission_ready':False,'blocked_reason':'STATE_BUDGET'}
  result=self.s.queue_preflight()
  self.assertEqual(result['state'],'HOLD_NEW_TRAINING_STORAGE');self.source.assert_not_called()
  self.assertEqual(self.counts(),(0,0,0,0))

 def test_held_preflight_uses_one_storage_snapshot(self):
  self.s.queue_preflight()
  self.storage.assert_called_once()

if __name__=='__main__':unittest.main()
