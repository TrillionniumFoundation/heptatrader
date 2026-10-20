"""Frozen episode accounting and admission; synthetic CPU evidence only."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from laya_cell import resident_training_v4 as v4
from laya_cell import resident_row_accounting as legacy
from laya_cell.integrity import ContractError,digest,sha_file
from laya_cell.resident_store import database,admit_job,complete_job


def fixture(n=32):
    rows=[{'sample_id':str(i),'horizon_sec':300,'outcome_receipt_digest':str(i)}
          for i in range(n)]
    ids=[legacy.row_id(row) for row in rows]
    job={'objective':copy.deepcopy(v4.OBJECTIVE),
         'training_row_accounting_schema':v4.ROW_ACCOUNTING_SCHEMA,
         'training_row_ids':ids,'data_fingerprint':digest(ids),
         'parent_id':'a'*64,'parent_manifest_sha256':'b'*64,
         'dataset_sha256':'c'*64,'window':{'training_end_ms':1234}}
    batches=legacy.batches(rows)
    calibration={'profile_id':'d'*64}
    baseline={'profile_id':'e'*64}
    frozen=v4.freeze(job,batches,{str(i):i%2 for i in range(n)},calibration,baseline)
    steps=[{'epoch':epoch,'batch_in_epoch':index,
            'training_row_ids':[legacy.row_id(r) for r in selected],
            'training_examples':len(selected),'applied':True,
            'action_optimizer_update':False}
           for epoch,index,selected in v4.schedule(batches)]
    points=[v4.point(epoch,[2.-epoch*.01]*n,[1.-epoch*.01]*n,
                     steps[:epoch*len(batches)],float(epoch))
            for epoch in v4.EPISODE['checkpoint_epochs']]
    candidate={'objective':copy.deepcopy(v4.OBJECTIVE),'steps':steps,
               'row_accounting':v4.receipt(ids,steps,ids),
               'training_episode':frozen,'action_training_profile':calibration,
               'price_baselines':baseline,'learning_curve':v4.curve(frozen,points,None),
               'measurement':{'rows':n,'parent_ce':points[0]['ce'],
                              'candidate_ce':points[-1]['ce'],'candidate_brier':points[-1]['brier']}}
    candidate['candidate_id']=digest(candidate)
    return job,candidate


class EpisodeReceiptTests(unittest.TestCase):
    def test_complete_episode_counts_unique_rows_and_presentations_separately(self):
        job,candidate=fixture()
        v4.verify_candidate(job,candidate)
        receipt=candidate['row_accounting']
        self.assertEqual((receipt['gradient_attempted_count'],receipt['applied_count']),(32,32))
        self.assertEqual((receipt['gradient_attempted_presentations'],receipt['applied_presentations']),(128,128))
        self.assertFalse(receipt['repeated_presentations_are_new_independent_rows'])
        with self.assertRaisesRegex(ContractError,'STEP_BUDGET'):
            legacy.receipt(job['training_row_ids'],candidate['steps'],job['training_row_ids'])

    def test_partial_or_reordered_epoch_cannot_claim_fixed_budget_completion(self):
        job,candidate=fixture();ids=job['training_row_ids']
        with self.assertRaisesRegex(ContractError,'COMPLETE_STEP_BUDGET'):
            v4.receipt(ids,candidate['steps'][:-1],ids)
        changed=copy.deepcopy(candidate['steps'])
        changed[16]['training_row_ids']=changed[17]['training_row_ids']
        with self.assertRaisesRegex(ContractError,'BATCH_ORDER_CHANGED'):
            v4.receipt(ids,changed,ids)
        with self.assertRaisesRegex(ContractError,'RESERVED_GRADIENT_MISMATCH'):
            v4.receipt(ids,candidate['steps'],['f'*64]+ids[1:])

    def test_rollbacks_do_not_inflate_unique_application(self):
        job,candidate=fixture();steps=candidate['steps']
        for step in steps[:16]:step['applied']=False
        result=v4.receipt(job['training_row_ids'],steps,job['training_row_ids'])
        self.assertEqual((result['applied_count'],result['rolled_back_count']),(32,32))
        self.assertEqual((result['applied_presentations'],result['rolled_back_presentations']),(96,32))
        self.assertEqual((result['applied_steps'],result['rolled_back_steps']),(48,16))

    def test_context_changes_cannot_reuse_an_old_learning_curve(self):
        job,candidate=fixture()
        for key,value in (('parent_id','f'*64),('dataset_sha256','f'*64),
                          ('parent_manifest_sha256','f'*64)):
            changed=copy.deepcopy(job);changed[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ContractError,'FROZEN_CONTEXT_CHANGED'):
                v4.verify_candidate(changed,candidate)
        changed=copy.deepcopy(job);changed['window']['training_end_ms']+=1
        with self.assertRaisesRegex(ContractError,'FROZEN_CONTEXT_CHANGED'):
            v4.verify_candidate(changed,candidate)

    def test_curve_cannot_turn_repeat_presentations_into_independent_rows(self):
        job,candidate=fixture()
        candidate['learning_curve']['checkpoints'][-1]['unique_gradient_rows_applied']=128
        with self.assertRaisesRegex(ContractError,'CURVE_ACCOUNTING'):
            v4.verify_candidate(job,candidate)

    def test_nonfinite_or_backward_curve_clock_refuses(self):
        for value in (float('nan'),-1.):
            job,candidate=fixture()
            candidate['learning_curve']['checkpoints'][-1]['elapsed_seconds']=value
            with self.assertRaisesRegex(ContractError,'CURVE_NONFINITE'):
                v4.verify_candidate(job,candidate)
        job,candidate=fixture()
        candidate['learning_curve']['checkpoints'][-1]['elapsed_seconds']=1.
        with self.assertRaisesRegex(ContractError,'CURVE_CHRONOLOGY'):
            v4.verify_candidate(job,candidate)

    def test_new_receipt_admits_one_original_population_only(self):
        job,candidate=fixture()
        with tempfile.TemporaryDirectory() as td:
            db=database(Path(td)/'spool.sqlite')
            try:
                self.assertTrue(admit_job(db,'job',job['data_fingerprint'],1,job,job['training_row_ids']))
                self.assertEqual(db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0],32)
                self.assertTrue(complete_job(db,'job',candidate))
                self.assertFalse(complete_job(db,'job',candidate))
                self.assertFalse(admit_job(db,'other',job['data_fingerprint'],2,job,job['training_row_ids']))
            finally:db.close()


class EpisodeAdmissionTests(unittest.TestCase):
    def test_unknown_or_modified_objective_refuses_before_gpu_allocation(self):
        from laya_cell import resident_train as worker
        for mutation in ('unknown','epochs','budget','row_limit'):
            objective=copy.deepcopy(v4.OBJECTIVE)
            if mutation=='unknown':objective['schema']+='-unknown'
            elif mutation=='epochs':objective['training_episode']['epochs']=5
            elif mutation=='budget':objective['max_steps']=128
            else:objective['max_rows']=256
            with self.subTest(mutation=mutation),patch.object(worker,'require_pocket4_gpu') as gpu:
                with self.assertRaisesRegex(ContractError,'PROFILE_CHANGED'):
                    worker.run_job({'objective':objective,'profile':worker.PROFILE},old_learner_verified=True)
                gpu.assert_not_called()

    def test_scheduler_does_not_silently_treat_unknown_schema_as_v1(self):
        from laya_cell import resident
        config={'schema':'heptatrader.laya-resident-config.v1','period_sha256':'a'*64,
                'period_path':'synthetic','native_binary':'synthetic','native_binary_sha256':'a'*64,
                'automatic_trading':False,'training_cadence_ms':900000,
                'objective':{'schema':'unknown'}}
        with patch.object(resident,'sha_file',return_value='a'*64):
            with self.assertRaisesRegex(ContractError,'UNKNOWN_RESIDENT_OBJECTIVE'):
                resident.config_check(config)

    def test_release_configuration_pins_episode_without_resetting_trial_history(self):
        from laya_cell.tests.test_resident_price_baselines import ExplicitConfigurationTests
        with tempfile.TemporaryDirectory() as td:
            config,runtime=ExplicitConfigurationTests().fixture(Path(td))
            for name in v4.RUNTIME_PINS:
                path=runtime/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('synthetic module\n')
            before=copy.deepcopy(config)
            result=v4.configuration_for_release(config,runtime)
            self.assertEqual(config,before)
            self.assertEqual(result['objective'],v4.OBJECTIVE)
            for key in ('control_directory','evaluation_directory','candidate_directory',
                        'initial_parent_directory','promotion_protocol','promotion_legacy_trial_floor',
                        'future_plan_admission_profile','profile'):
                self.assertEqual(result[key],config[key])
            for name in v4.RUNTIME_PINS:
                self.assertEqual(result['runtime_sha256'][name],sha_file(runtime/name))


if __name__=='__main__':unittest.main()
