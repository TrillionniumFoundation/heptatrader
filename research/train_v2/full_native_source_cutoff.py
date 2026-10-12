"""Join full ORIGINAL Canonical workspace verification with real source cutoff.

No signing, LedgerWriter append, original evidence mutation or broker operation.
The two separately proven TEST-only phenomena are not an end-to-end product
authorization, and the join must NOT assert they are the same native event.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from . import full_canonical_workspace as original
from .native_fresh_readback import (
    digest, read_private, verify_timeline, private_dir,
)
from .prewarmed_source_verify_v2 import verify as verify_original

SCHEMA = "heptatrader.train-v2-unified-native-and-source-cutoff.v1"
NATIVE_PIN = original.PIN
CODE_PIN = "0da6c6163e8d6042f91ebe53ac39874d51c3cf2cc8dcff52e9e8b8609e8a0235"
BIN_PIN = "41b6be2ac9f85e0f4f592016037c82819a87df04e843fef8d0d316a08984924e"
LAB_ACTION = ["NO_TRADE"]


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def check_qualification(native, logs_dir, source):
    """Re-evaluate exact full-workspace receipt against original code/logs."""
    _require(native.get("schema") == "heptatrader.train-v2-full-original-canonical-workspace.v1",
             "UNIFIED_NATIVE_RECEIPT_SCHEMA")
    current = original.verify_source(source)
    _require(native.get("actual_pinned_git") == current,
             "UNIFIED_NATIVE_SOURCE_COMMIT_OR_LOCK_CHANGED")
    resolved = original.resolve_full_graph(source / "codex-rs/Cargo.toml")
    _require(native.get("actual_offline_locked_dependency_resolution") == resolved,
             "UNIFIED_NATIVE_DEPENDENCY_GRAPH_CHANGED")
    _require((resolved["workspace_members"], resolved["resolved_packages"]) == (193, 1517),
             "UNIFIED_NATIVE_WORKSPACE_INCOMPLETE")
    rows = native.get("original_package_test_evidence")
    _require(isinstance(rows, list) and len(rows) == len(original.CRATES),
             "UNIFIED_NATIVE_TEST_INVENTORY_INCOMPLETE")
    passed, ignored = 0, 0
    for item, crate in zip(rows, original.CRATES):
        _require(item.get("package") == crate,
                 "UNIFIED_NATIVE_WRONG_TEST_PACKAGE")
        expected_command = [
            "cargo", "test", "--locked", "--offline", "--manifest-path",
            str(source / "codex-rs/Cargo.toml"), "-p", crate,
        ]
        if crate == "codex-hepta-agentd":
            expected_command += ["--test", "terminal_cell_owner"]
        else:
            expected_command += ["--lib"]
        _require(item.get("command") == expected_command,
                 "UNIFIED_NATIVE_TEST_COMMAND_CHANGED")
        log_path = logs_dir / (crate + ".log")
        _require(log_path.is_file() and not log_path.is_symlink(),
                 "UNIFIED_NATIVE_LOG_NOT_ORIGINAL")
        log = log_path.read_bytes()
        _require(digest(log) == item["raw_test_log_sha256"],
                 "UNIFIED_NATIVE_TEST_LOG_CHANGED")
        summary = original.parse_result(log.decode(errors="replace"), crate)
        _require(summary == item["test_result"],
                 "UNIFIED_NATIVE_RECORDED_PASS_COUNT_CHANGED")
        passed += summary["passed"]
        ignored += summary["ignored"]
    _require((passed, ignored) == (212, 3)
             and native.get("total_native_passed") == passed
             and native.get("total_native_ignored") == ignored,
             "UNIFIED_NATIVE_PASS_COUNT_INCOMPLETE")
    for flag in ("product_root_signer_enrolled", "production_activation_authorized",
                 "actual_trade_request_prepared_bound", "llm_trade_action_propensity_known",
                 "signed_production_current_owner", "original_broker_economic_alpha_proven",
                 "model_trained_or_promoted", "trading_authority"):
        _require(native.get(flag) is False, "UNIFIED_NATIVE_PRODUCT_AUTHORITY_FALSIFIED")
    _require(native.get("broker_orders_sent") == 0 and
             native.get("broker_requests_sent") == 0,
             "UNIFIED_NATIVE_BROKER_OPERATION_NOT_ALLOWED")
    return {"source": current, "dependencies": resolved,
            "native_test_passed": passed, "native_test_ignored": ignored,
            "original_test_log_sha256": {entry["package"]: entry["raw_test_log_sha256"]
                                          for entry in rows}}


def check_original_test_event(source_root, native_root, prior_readback):
    """Reopen already sealed files and enforce the FIXED source cutoff."""
    original_proof = verify_original(source_root)
    source = json.loads(read_private(source_root / "source-readiness-outcome.json"))
    native = json.loads(read_private(native_root / "native-prewarm-public-ack.json"))
    precommit = json.loads(read_private(native_root / "native-prewarm-intent.json"))
    chronology = verify_timeline(source, native, precommit, original_proof)
    _require(chronology["ack_before_source_decision_cutoff_ms"] >= 0,
             "UNIFIED_ORIGINAL_SOURCE_CUTOFF_OVERRUN")
    _require(chronology["ack_before_native_deadline_ms"] > 0
             and chronology["ack_before_original_ib_expiry_ms"] > 0,
             "UNIFIED_ORIGINAL_NATIVE_DEADLINE_OVERRUN")
    _require(native["candidate_universe"] == LAB_ACTION
             and native["selected_logged_q32_propensity"] == 2**32
             and native["no_model_inference"] is True
             and native["root_and_role_test_only"] is True,
             "UNIFIED_NOT_FIXED_NO_TRADE_LAB_POLICY")
    _require(prior_readback.get("schema") ==
             "heptatrader.train-v2-testonly-original-native-independent-readback.v1"
             and prior_readback.get("test_only") is True
             and prior_readback.get("original_source_public_chain_verified") is True
             and prior_readback.get("copied_corrupted_witness_refused") is True
             and prior_readback.get("copied_lost_acknowledged_ledger_refused") is True
             and prior_readback.get("original_typed_native_decision_witness_reopened") is True
             and prior_readback.get("canonical_product_root_enrolled") is False
             and prior_readback.get("actual_model_inference") is False
             and prior_readback.get("original_broker_prepared") is False
             and prior_readback.get("economic_alpha_observed") is False
             and prior_readback.get("broker_orders_sent") == 0,
             "UNIFIED_PREVIOUS_READBACK_NOT_TEST_ONLY")
    _require(prior_readback["source_independent_report_id"] ==
             original_proof["report_id"],
             "UNIFIED_PREVIOUS_RECOVERY_DIFFERENT_SOURCE_EVENT")
    _require(prior_readback["original_test_recovered_chain_digest"] ==
             native["native_chain_digest"], "UNIFIED_NATIVE_CHAIN_MISMATCH")
    originals = {
        name: read_private(native_root / name, 1024 * 1024)
        for name in ("ledger", "witness", "native-prewarm-public-ack.json")
    }
    _require((len(originals["ledger"]), len(originals["witness"])) == (707, 160),
             "UNIFIED_NATIVE_ORIGINAL_TYPE_OR_SIZE_CHANGED")
    hashes = {name: digest(raw) for name, raw in originals.items()}
    _require(hashes == prior_readback["native_original_sha256"],
             "UNIFIED_ORIGINAL_NATIVE_EVIDENCE_CHANGED")
    _require(prior_readback.get("rust_native_program_sha256") == CODE_PIN
             and prior_readback.get("rust_compiled_test_binary_sha256") == BIN_PIN,
             "UNIFIED_NATIVE_RUST_TEST_PROGRAM_DRIFT")
    return {"source_report_id": original_proof["report_id"],
            "original_hashes": hashes,
            "original_native_test_chain_digest": native["native_chain_digest"],
            "cutoff": chronology,
            "action_population": LAB_ACTION,
            "propensity_q32": 2**32,
            "original_opaque_policy_evaluated": False}


def run(source, qualification_path, original_source_root, original_native_root,
        original_recovery_path):
    source, qualification_path = Path(source), Path(qualification_path)
    original_source_root = private_dir(original_source_root)
    original_native_root = private_dir(original_native_root)
    native = json.loads(read_private(qualification_path, 16384))
    recovery = json.loads(read_private(original_recovery_path, 16384))
    completed = check_qualification(native, qualification_path.parent, source)
    original_event = check_original_test_event(original_source_root,
                                                original_native_root, recovery)
    tests_file = Path(__file__).resolve().parents[2] / (
        "tests/python/test_train_v2_unified_native.py")
    receipt = {
        "schema": SCHEMA,
        "native_source_commit": NATIVE_PIN,
        "unified_verifier_code_sha256": digest(Path(__file__).read_bytes()),
        "unified_adversarial_tests_sha256": digest(tests_file.read_bytes()),
        "full_workspace_qualification_sha256": digest(read_private(qualification_path, 16384)),
        "prior_original_recovery_report_sha256": digest(read_private(original_recovery_path, 16384)),
        "original_product_native": completed,
        "original_test_only_source_event": original_event,
        "both_independent_evidence_lanes_reverified": True,
        "both_lanes_from_same_live_event": False,
        "same_native_test_policy_as_opaque_live_llm": False,
        "actual_llm_action_population_or_propensity_attested": False,
        "canonical_host_root_keys_enrolled": False,
        "broker_request_prepared_identity_verified": False,
        "signed_current_owner_selection": False,
        "dataset_snapshot_v3_from_real_trades_frozen": False,
        "financial_all_in_account_pnl_complete": False,
        "actual_after_cost_trading_alpha_proven": False,
        "production_activation_qualified": False,
        "model_promoted": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return receipt


def main():
    p = argparse.ArgumentParser()
    for key in ("canonical-source", "qualification", "original-source-root",
                "original-native-root", "prior-native-recovery", "output"):
        p.add_argument("--" + key, required=True, type=Path)
    args = p.parse_args()
    os.umask(0o077)
    result = run(args.canonical_source, args.qualification,
                 args.original_source_root, args.original_native_root,
                 args.prior_native_recovery)
    output = args.output
    _require(not output.exists() and not output.is_symlink(),
             "UNIFIED_RECEIPT_CREATE_ONLY")
    parent = private_dir(output.parent)
    raw = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    dirfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
    print(json.dumps({
        "status": "FULL_NATIVE_AND_ORIGINAL_SOURCE_CUTOFF_VERIFIED_TEST_ONLY",
        "original_native_test_passes": result["original_product_native"]["native_test_passed"],
        "original_native_test_ignored": result["original_product_native"]["native_test_ignored"],
        "source_cutoff_margin_ms": result["original_test_only_source_event"]["cutoff"]["ack_before_source_decision_cutoff_ms"],
        "original_action_population": result["original_test_only_source_event"]["action_population"],
        "broker_orders_sent": 0,
        "product_admitted": False,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
