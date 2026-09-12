from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import verify_ib_paper_rollout as rollout
from paper_rollout_fixtures import RolloutFixture


class IbPaperRolloutTests(unittest.TestCase):
    def test_real_relationship_fixture_passes_every_stage_without_authority(self):
        for stage, count in [('canary', 1), ('pilot', 3), ('extended', 10)]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d:
                receipt = RolloutFixture(Path(d), stage).verify(rollout)
                self.assertEqual(receipt['mutation_cycles'], count)
                self.assertFalse(receipt['paper_authorized'])
                self.assertFalse(receipt['live_authorized'])
                self.assertEqual(receipt['authorization_effect'], 'NONE')

    def reject(self, mutate, pattern=None, stage='canary'):
        with tempfile.TemporaryDirectory() as d:
            f=RolloutFixture(Path(d), stage)
            mutate(f); f.save()
            if pattern:
                with self.assertRaisesRegex(rollout.VerificationError, pattern): f.verify(rollout)
            else:
                with self.assertRaises((rollout.VerificationError, OSError)): f.verify(rollout)

    def test_empty_snapshot_with_correct_digest_is_rejected(self):
        self.reject(lambda f: setattr(f, 'snapshots', {}), 'snapshots')

    def test_absent_intent_or_send_cannot_be_paper_evidence(self):
        for index in (0,1,2):
            self.reject(lambda f, i=index: f.journal.pop(i), 'intent/send/reconciliation')

    def test_filled_status_without_execution_proof_is_rejected(self):
        self.reject(lambda f: f.callbacks.pop(0), 'terminal/fill')

    def test_missing_execution_identity_is_rejected(self):
        self.reject(lambda f: f.callbacks[0].update(execution_id=''), 'execution id')

    def test_conflicting_duplicate_execution_is_rejected(self):
        def mutate(f):
            row=copy.deepcopy(f.callbacks[0]); row['quantity']=0.5
            f.callbacks.insert(1,row)
        self.reject(mutate, 'conflicting duplicate execution')

    def test_identical_duplicate_execution_is_applied_once(self):
        with tempfile.TemporaryDirectory() as d:
            f=RolloutFixture(Path(d)); f.callbacks.insert(1,copy.deepcopy(f.callbacks[0])); f.save()
            self.assertEqual(f.verify(rollout)['mutation_cycles'],1)

    def test_each_cycle_must_have_two_complete_flat_barriers(self):
        for half in ('before','after'):
            self.reject(lambda f, k=half: f.snapshots['cycles'][1][k]['positions'][0].update(quantity=1),
                        'flat', stage='pilot')
            self.reject(lambda f, k=half: f.snapshots['cycles'][1][k].update(complete=False),
                        'incomplete', stage='pilot')

    def test_intermediate_active_and_uncertain_state_rejects_even_with_final_flat_summary(self):
        for field in ('active_order_ids','uncertain_command_ids'):
            self.reject(lambda f, k=field: f.snapshots['cycles'][0]['after'].update({k:['unresolved']}),
                        'active or uncertain')

    def test_pilot_cannot_pass_one_canary_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(rollout.VerificationError,'insufficient'):
                RolloutFixture(Path(d),'pilot',1).verify(rollout)

    def test_overlapping_cycles_are_not_independent(self):
        self.reject(lambda f: f.snapshots['cycles'][1]['before'].update(observed_at_ms=2100),
                    'overlapping', stage='pilot')

    def test_forged_counts_do_not_override_economic_evidence(self):
        self.reject(lambda f: f.result.update(successful_round_trips=10), 'reported cycle count')

    def test_send_before_intent_is_rejected(self):
        self.reject(lambda f: f.journal[1].update(observed_at_ms=1900), 'timing|timestamps')

    def test_reused_refresh_generation_is_rejected(self):
        self.reject(lambda f: f.snapshots['cycles'][0]['after'].update(generation=1), 'refresh barriers')

    def test_foreign_account_host_profile_or_campaign_is_rejected(self):
        for field in ('account_fingerprint','host_fingerprint','profile_sha256','campaign_id','instrument'):
            def mutate(f,k=field):
                f.callbacks[0]=copy.deepcopy(f.callbacks[0]); f.callbacks[0]['binding'][k]='other'
            self.reject(mutate, 'mismatch')

    def test_foreign_callback_epoch_is_rejected(self):
        self.reject(lambda f: f.callbacks[0].update(connection_epoch=2), 'epoch mismatch')

    def test_snapshot_from_before_campaign_is_rejected(self):
        self.reject(lambda f: f.snapshots['cycles'][0]['before'].update(observed_at_ms=1), 'predates')

    def test_unknown_broker_order_is_rejected(self):
        self.reject(lambda f: f.callbacks[0].update(order_id='other'), 'correlation')

    def test_quantity_and_limit_price_are_verified_from_orders(self):
        def mutate(f):
            for row in f.journal[:3]: row['quantity']=2
        self.reject(mutate,'quantity/notional')
        def mutate_price(f):
            for row in f.journal[:3]: row['limit_price']=9999
        self.reject(mutate_price,'quantity/notional')

    def test_overfill_and_limit_violation_are_rejected(self):
        self.reject(lambda f: f.callbacks[0].update(quantity=1.1),'overfill')
        self.reject(lambda f: f.callbacks[0].update(price=1.3),'limit price')

    def test_false_roundtrip_with_unchanged_summary_is_rejected(self):
        def mutate(f):
            for row in f.journal[3:]: row['side']='BUY'
            for row in f.callbacks[2:]: row['side']='BUY'
        # Wrong-side exits are now rejected before projecting their fills.
        self.reject(mutate,'exit must SELL the exact observed opening position')

    def test_boolean_zero_is_not_valid_terminal_measurement(self):
        self.reject(lambda f: f.result.update(final_position_quantity=False),'number required')
        self.reject(lambda f: f.result.update(final_active_orders=False),'active or uncertain')

    def test_canonical_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            f=RolloutFixture(Path(d))
            f.result_path.write_text('{"schema":1,"schema":2}')
            with self.assertRaisesRegex(rollout.VerificationError,'duplicate'): f.verify(rollout)
            f.result_path.write_text('{"x":NaN}')
            with self.assertRaisesRegex(rollout.VerificationError,'non-finite'): f.verify(rollout)

    def test_evidence_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            f=RolloutFixture(Path(d)); (f.evidence/'journal.jsonl').write_text('tampered')
            with self.assertRaisesRegex(rollout.VerificationError,'size mismatch|digest mismatch'): f.verify(rollout)

    def test_symlinked_evidence_leaf_and_ancestor_are_rejected(self):
        for ancestor in (False,True):
            with self.subTest(ancestor=ancestor), tempfile.TemporaryDirectory() as d:
                f=RolloutFixture(Path(d)); path=f.evidence/'journal.jsonl'
                real=f.evidence/'real'; real.mkdir(); path.rename(real/'journal.jsonl')
                if ancestor:
                    (f.evidence/'link').symlink_to(real, target_is_directory=True)
                    f.result['evidence'][1]['path']='link/journal.jsonl'
                    f.result_path.write_text(json.dumps(f.result))
                else: path.symlink_to(real/'journal.jsonl')
                with self.assertRaises((OSError,rollout.VerificationError)): f.verify(rollout)

    def test_fifo_result_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as d:
            f=RolloutFixture(Path(d)); f.result_path.unlink(); os.mkfifo(f.result_path)
            with self.assertRaisesRegex(rollout.VerificationError,'regular'): f.verify(rollout)

    def test_campaign_is_independently_required(self):
        with tempfile.TemporaryDirectory() as d:
            f=RolloutFixture(Path(d))
            with self.assertRaisesRegex(rollout.VerificationError,'campaign'):
                rollout.verify(f.result_path,f.evidence,'a'*40,f.binary,f.harness,'canary')


if __name__=='__main__': unittest.main()
