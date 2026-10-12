"""Read-only authoritative-boundary inspection for a test-only ACK-only recovery.

A lower-level native WitnessStore can technically advance an anchor when
given permitted handles. That is NOT a production authorization. This
verifier never issues an ACK, uses no original signing private keys, and
will not infer independent administrator / root grant from a local file.

For the actual Phase4L/4M sealed original PAPER-shifted source, the
correct result for a lagged witness is HOLD until an independently root
approved, unexpired one-shot RecoveryGrant and a PRIOR-to-append
origin-host escrow proof are both externally established. Existing
already witnessed history is a no-op even if issuer keys expired.
"""
from __future__ import annotations

from datetime import datetime,timezone
import argparse
from pathlib import Path
import hashlib
import json
import os

from .original_policy_audit import write_once
from .real_source_native_recovery_gate import assess as prior_assess

SCHEMA="heptatrader.train-v2-native-ackonly-grant-source-bound-hold.v1"
ALREADY="ALREADY_ACKED_NO_NEW_LEDGER_OR_WITNESS_WRITE"
NO_ESCROW="HOLD_NO_PREAPPEND_INDEPENDENT_ESCROW_AT_ORIGINAL_SOURCE_TIME"
NO_ROOT="HOLD_MISSING_ROOT_APPROVED_RECOVERY_AUTHORITY_AND_ONCE_GRANT"
HOLD="HOLD_REPLAY_SIGNATURE_EXPIRED_CURRENT_TIME"


def check_grant(*, witness_sequence:int, original_signature_expired:bool,
                old_preappend_remote_escrow_verified:bool,
                recovery_grant_root_authorized:bool,
                current_revocation_verified:bool,
                trusted_clock_attested:bool)->str:
    if type(witness_sequence) is not int or witness_sequence not in (1,2):
        raise ValueError("WITNESS_ACK_FRONTIER_TYPE_OR_RANGE")
    for k,v in (
        ("original_signature_expired",original_signature_expired),
        ("old_preappend_remote_escrow_verified",old_preappend_remote_escrow_verified),
        ("recovery_grant_root_authorized",recovery_grant_root_authorized),
        ("current_revocation_verified",current_revocation_verified),
        ("trusted_clock_attested",trusted_clock_attested),
    ):
        if type(v) is not bool:
            raise ValueError("RECOVERY_GRANT_FIELD_NOT_BOOLEAN:"+k)
    if witness_sequence==2:
        # A previously acknowledged existing event requires no authorization
        # to reappend or a new RecoveryGrant.
        return ALREADY
    if not old_preappend_remote_escrow_verified:
        return NO_ESCROW
    if (not recovery_grant_root_authorized or not current_revocation_verified
            or not trusted_clock_attested):
        return NO_ROOT
    # Even if a caller puts True in all three local booleans, there is no
    # product RecoveryAuthority role or independent trust enrollment
    # currently available. We refuse to elevate a trusted claim to an
    # actual grant until native product schema and root verifier exist.
    return HOLD


def inspect(actual_original:Path,copied_history:Path)->dict:
    prior=prior_assess(actual_original,copied_history)
    original_source=Path(actual_original)
    stage=prior["native_witness_count"]
    # Original Phase4L predates first Phase4N preappend Escrow prototype;
    # no historical independently sealed test Escrow can be minted later.
    old_source_escrow_dir=original_source/"native-lab-v1"
    claimed_escrow=(old_source_escrow_dir/
        "original-native-outcome-independent-escrow.json")
    # Require EXACT source-time proof from that old original: it never
    # existed, and cannot be inferred from a new copy in an unrelated
    # owner's directory. Existence is not treated as enough to set True.
    independent_escrow_verified=False
    if claimed_escrow.exists() or claimed_escrow.is_symlink():
        # A newly planted local Escrow file cannot retroactively prove
        # that an earlier native Writer append was escrowed in source time.
        raise ValueError("RETROACTIVE_PREAPPEND_ESCROW_FILE_IN_OLDER_TRIAL")
    issuer=prior["independently_public_signed_Observer_epoch"]
    expired=prior["real_wall_clock_ms"]>issuer["native_expires_at_ms"]
    result=check_grant(
        witness_sequence=stage,
        original_signature_expired=expired,
        old_preappend_remote_escrow_verified=independent_escrow_verified,
        recovery_grant_root_authorized=False,
        current_revocation_verified=False,
        trusted_clock_attested=False,
    )
    return {
        "schema":SCHEMA,
        "evaluated_at_utc":datetime.now(timezone.utc).isoformat(),
        "actual_previous_signed_native_PAPER_trial_lab_only":True,
        "original_signed_source_test_root_not_product_enrolled":True,
        "original_same_native_ledger_copy_sha256":
            prior["original_real_native_ledger_copy_sha256"],
        "current_witness_sequence":stage,
        "witness_one_record_lagging":stage==1,
        "actual_Observer_original_evidence_now_expired":expired,
        "earlier_preappend_independent_remote_escrow_proven":False,
        "future_or_late_fabricated_escrow_is_not_a_recovery_grant":True,
        "root_approved_product_RecoveryAuthority_role_present":False,
        "current_root_revocation_and_secure_monotonic_clock_authenticated":False,
        "original_ledger_record_already_exists":True,
        "automatic_ack_only_witness_write_issued":False,
        "new_generator_observer_signature_requested":False,
        "actual_broker_or_model_operations_performed":False,
        "recovery_decision":result,
        "production_model_dataset_owner_or_trading_authority":False,
    }


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--original",type=Path,required=True)
    p.add_argument("--copied-history",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    r=inspect(a.original,a.copied_history)
    digest=write_once(a.output,r)
    print(json.dumps({
        "native_original_witness_sequence":r["current_witness_sequence"],
        "native_ackonly_candidate":r["recovery_decision"],
        "no_automatic_write":True,
        "production_grant":False,
        "receipt_sha256":digest,
    },sort_keys=True))


if __name__=="__main__":
    main()
