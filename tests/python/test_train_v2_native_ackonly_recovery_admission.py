"""Synthetic-only root/grant boundary tests for original ACK-only recovery.

The previously captured native market files are verified separately;
these unit tests do not generate any signer key, ledger record, or
remote Escrow. There is no production RecoveryAuthority available.
"""
from __future__ import annotations

import unittest
from train_v2.native_ackonly_recovery_admission import (
    ALREADY,NO_ESCROW,NO_ROOT,HOLD,check_grant
)


class AckOnlySignerAndGrantBoundaryTests(unittest.TestCase):
    def decision(self,seq=1,expired=True,escrow=False,grant=False,
                 revocation=False,clock=False):
        return check_grant(
            witness_sequence=seq,
            original_signature_expired=expired,
            old_preappend_remote_escrow_verified=escrow,
            recovery_grant_root_authorized=grant,
            current_revocation_verified=revocation,
            trusted_clock_attested=clock,
        )

    def test_already_acknowledged_does_not_trigger_any_append(self):
        for old_expired in (True,False):
            with self.subTest(expired=old_expired):
                self.assertEqual(self.decision(seq=2,expired=old_expired),ALREADY)

    def test_one_unwitnessed_event_without_original_escrow_is_hold(self):
        self.assertEqual(self.decision(),NO_ESCROW)

    def test_unsigned_escrow_claim_not_product_recovery_grant(self):
        self.assertEqual(self.decision(escrow=True,grant=False),NO_ROOT)

    def test_recovery_root_claim_without_revocation_check_refused(self):
        self.assertEqual(self.decision(escrow=True,grant=True,revocation=False),NO_ROOT)

    def test_root_and_revocation_without_attested_clock_refused(self):
        self.assertEqual(self.decision(escrow=True,grant=True,revocation=True,clock=False),NO_ROOT)

    def test_all_caller_booleans_true_still_not_a_registered_product_grant(self):
        self.assertEqual(self.decision(escrow=True,grant=True,
                                       revocation=True,clock=True),HOLD)

    def test_current_observer_expiry_does_not_promote_unsigned_recovery(self):
        for expired in (True,False):
            with self.subTest(expired=expired):
                self.assertEqual(self.decision(expired=expired,escrow=False),NO_ESCROW)

    def test_bool_witness_counter_and_out_of_range_refused(self):
        for seq in (True,False,0,3,"1",None):
            with self.subTest(seq=seq):
                with self.assertRaisesRegex(ValueError,"WITNESS_ACK_FRONTIER_TYPE_OR_RANGE"):
                    self.decision(seq=seq)

    def test_extra_truthy_values_never_grant_recovery(self):
        for bad in (1,"true",None,[],{}):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError,"RECOVERY_GRANT_FIELD_NOT_BOOLEAN"):
                    self.decision(escrow=bad)

    def test_regression_still_no_true_economic_broker_permission(self):
        self.assertNotEqual(self.decision(escrow=True,grant=False),ALREADY)
        self.assertNotEqual(self.decision(escrow=True,grant=True,
                                          revocation=True,clock=True),ALREADY)


if __name__=="__main__":
    unittest.main()
