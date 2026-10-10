"""External-signature-gated *research* admission, distinct from canonical owner.

No issuer key or sign action is implemented. A host-provided pinned trust hash
and an externally signed witness must exist before local preregistration.
Qualified outputs remain research-only, with zero model/trading authority.
"""
from __future__ import annotations

import os
from pathlib import Path

from historical_training.common import read, sha, write_once
from .contracts import ContractError
from .registry import (
    _private_directory, _original_root, register_before_window,
    verify_registration,
)
from .orchestrator import ingest, SOURCE_EVENT, OUTCOME_EVENT
from .handoff import verify_original_paper_handoff, require_source_admitted_before_decision
from .external_signatures import (
    validate_plan_witness, validate_event_witness, verify_trust,
)

SIGNED_LEDGER = "externally-verified-events.sqlite"
SIG_RESEARCH = "SIGNED_RESEARCH_WITNESS_NOT_CANONICAL_OWNER"


def _read_anchor(trust_file, expected_sha256, plan, *, min_generation=1,
                 now_ms=None):
    path = Path(trust_file)
    if path.is_symlink() or not path.is_file():
        raise ContractError("EXTERNAL_TRUST_FILE_MISSING")
    if sha(path) != expected_sha256:
        raise ContractError("EXTERNAL_TRUST_FILE_CHANGED")
    trust = read(path, 16384)
    return verify_trust(trust, expected_sha256, plan,
                        min_generation=min_generation, now_ms=now_ms)


def _immutable(directory, filename, record):
    root = _private_directory(directory, create=True)
    output = root / filename
    if output.is_symlink():
        raise ContractError("EXTERNAL_WITNESS_FILE_SYMLINK")
    write_once(output, record)
    # An external witness seal must persist before local registry/admission.
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return output


def register_externally_witnessed(directory, plan, spec, source_root,
                                   trust_file, pinned_trust_sha256,
                                   registrar_witness, *,
                                   min_generation=1, now_ms=None):
    """Never reuse an old plan or locally synthesize an external signature."""
    root = Path(directory)
    source = _original_root(source_root)
    if not root.is_absolute() or ".." in root.parts:
        raise ContractError("EXTERNAL_REGISTRY_ROOT_UNSAFE")
    trust = _read_anchor(trust_file, pinned_trust_sha256, plan,
                         min_generation=min_generation, now_ms=now_ms)
    verdict = validate_plan_witness(
        trust, pinned_trust_sha256, plan, spec, source, root,
        registrar_witness, now_ms=now_ms, min_generation=min_generation)
    if now_ms is not None:
        # Tests can exercise fixed time, not register a fake real-time receipt.
        raise ContractError("EXTERNAL_SIMULATED_CLOCK_CANNOT_REGISTER")
    witness_record = {
        "schema": "heptatrader.train-v2-external-registration-seal.v1",
        "plan_id": plan["plan_id"],
        "trusted_snapshot_sha256": pinned_trust_sha256,
        "trust_generation": trust["trust_generation"],
        "source_root": str(source),
        "registrar_witness": registrar_witness,
        "signature_verification": verdict,
        "canonical_owner_enrolled": False,
        "trade_authority": False,
    }
    # The registry root itself must be private BEFORE nested evidence paths
    # are created; mkdir(parents=True) otherwise defaults parents to umask.
    _private_directory(root, create=True)
    _immutable(root / "external-plan-witnesses",
               plan["plan_id"] + ".json", witness_record)
    local_receipt = register_before_window(root, plan, spec, source)
    if local_receipt["plan_id"] != plan["plan_id"]:
        raise ContractError("EXTERNAL_LOCAL_REGISTRATION_MISMATCH")
    return {
        "schema": "heptatrader.train-v2-external-research-registration.v1",
        "plan_id": plan["plan_id"],
        "signed_registration": True,
        "signed_role": "registrar",
        "trust_generation": trust["trust_generation"],
        "local_sealed_at_ms": local_receipt["sealed_at_ms"],
        "canonical_agentd_owner_enrolled": False,
        "automatic_training": False,
        "broker_orders_sent": 0,
    }


def verify_external_registration(directory, plan, spec, source_file,
                                 trust_file, pinned_trust_sha256, *,
                                 min_generation=1, now_ms=None):
    root = Path(directory)
    receipt = verify_registration(root, plan, spec, source_file)
    witness_file = root / "external-plan-witnesses" / (plan["plan_id"] + ".json")
    if witness_file.is_symlink() or not witness_file.is_file():
        raise ContractError("EXTERNAL_PRESEALED_WITNESS_MISSING")
    record = read(witness_file, 16384)
    trust = _read_anchor(trust_file, pinned_trust_sha256, plan,
                         min_generation=min_generation, now_ms=now_ms)
    if (record.get("schema") != "heptatrader.train-v2-external-registration-seal.v1"
            or record.get("plan_id") != plan["plan_id"]
            or record.get("trusted_snapshot_sha256") != pinned_trust_sha256
            or record.get("trust_generation") != trust["trust_generation"]
            or record.get("source_root") != receipt["original_source_root"]
            or record.get("canonical_owner_enrolled") is not False
            or record.get("trade_authority") is not False):
        raise ContractError("EXTERNAL_WITNESS_REGISTRATION_CHANGED")
    registrar = record.get("registrar_witness")
    if not isinstance(registrar, dict):
        raise ContractError("EXTERNAL_REGISTRAR_MISSING")
    # Verify historical registrar signature with its original signing-time
    # validity, plus CURRENT trust expiry/revocation/anti-rollback at final use.
    signed_at = registrar.get("issued_at_ms")
    if type(signed_at) is not int:
        raise ContractError("EXTERNAL_REGISTRAR_TIME")
    proof = validate_plan_witness(
        trust, pinned_trust_sha256, plan, spec, receipt["original_source_root"],
        root, registrar, now_ms=signed_at, min_generation=min_generation)
    if (proof != record.get("signature_verification")
            or not signed_at <= receipt["sealed_at_ms"]
            or receipt["sealed_at_ms"] - signed_at > 5000):
        raise ContractError("EXTERNAL_REGISTRAR_OR_LOCAL_SEAL_CHANGED")
    return trust, receipt, proof


def ingest_signed_original(directory, plan, dataset, event, source_file,
                           trust_file, pinned_trust_sha256, event_witness, *,
                           min_generation=1, now_ms=None):
    """Admit source/outcome via distinct two-role signature verification.

    This final-use check precedes source hash-chain replay, fsynced signed
    evidence and then a single SQLite event mutation. It cannot authorize
    training, broker writes, model selection or canonical publication.
    """
    root = Path(directory)
    spec = dataset.get("feature_spec")
    if not isinstance(spec, dict):
        raise ContractError("EXTERNAL_DATASET_SCOPE")
    trust, receipt, reg_proof = verify_external_registration(
        root, plan, spec, source_file, trust_file, pinned_trust_sha256,
        min_generation=min_generation, now_ms=now_ms)
    examples = [row for row in dataset.get("examples", [])
                if row.get("feature_id") == event.get("feature_id")]
    if len(examples) != 1:
        raise ContractError("EXTERNAL_EVENT_FEATURE_NOT_UNIQUE")
    decision = examples[0].get("features", {}).get("decision_at_ms")
    proof = validate_event_witness(
        trust, pinned_trust_sha256, plan, event, source_file,
        root, event_witness, now_ms=now_ms,
        min_generation=min_generation, decision_at_ms=decision)
    if event["at_ms"] < receipt["sealed_at_ms"]:
        raise ContractError("EXTERNAL_EVENT_PRECEDES_REGISTRATION")
    if event["kind"] not in (SOURCE_EVENT, OUTCOME_EVENT):
        raise ContractError("EXTERNAL_UNTRUSTED_CORRECTION")
    # Exact unchanged source is verified before any signed-event file is written.
    verify_original_paper_handoff(
        plan, dataset, event, source_file, now_ms=now_ms)
    _immutable(root / "external-event-witnesses", event["event_id"] + ".json",
               {"schema": "heptatrader.train-v2-signed-original-event.v1",
                "plan_id": plan["plan_id"], "event_id": event["event_id"],
                "pinned_trust_sha256": pinned_trust_sha256,
                "original_segment": str(source_file), "witness": event_witness,
                "verified": proof, "canonical_owner_enrolled": False,
                "trade_authority": False})
    # Recheck at final use AFTER fsync of the external witness: a source
    # signed early but persisted after the decision is not prospective.
    require_source_admitted_before_decision(
        event, spec, decision, now_ms=now_ms)
    result = ingest(root / SIGNED_LEDGER, plan, dataset, event)
    return {
        "schema": "heptatrader.train-v2-externally-signed-research-ingest.v1",
        "event_id": event["event_id"],
        "plan_id": plan["plan_id"],
        "state": result["state"],
        "registrar_signature_verified": True,
        "source_or_observer_signature_verified": True,
        "trust_generation": trust["trust_generation"],
        "canonical_agentd_owner_enrolled": False,
        "automatic_training": False,
        "model_selected": False,
        "broker_orders_sent": 0,
    }
