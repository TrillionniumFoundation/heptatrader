"""Independently reopen a FIRST fresh TEST-only native NO_TRADE ACK.

All Rust recovery runs on fresh, private, byte-identical COPIES of a prior
native ledger/witness. No signing, broker access, model or original rewrites.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from .prewarmed_source_verify_v2 import verify as verify_original_source

SCHEMA = "heptatrader.train-v2-testonly-original-native-independent-readback.v1"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def private_dir(path):
    p = Path(path)
    if p.is_symlink() or not p.is_dir():
        raise ValueError("PRIVATE_DIRECTORY_REQUIRED")
    st = p.stat()
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise ValueError("PRIVATE_DIRECTORY_OWNER_OR_MODE")
    return p


def read_private(path, max_bytes=65536):
    p = Path(path)
    if p.is_symlink():
        raise ValueError("PRIVATE_SOURCE_SYMLINK")
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.geteuid()
                or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_size > max_bytes):
            raise ValueError("PRIVATE_SOURCE_OWNER_MODE_OR_SIZE")
        remaining = st.st_size
        raw = bytearray()
        while remaining:
            part = os.read(fd, min(remaining, 32768))
            if not part:
                raise ValueError("PRIVATE_SOURCE_SHORT_READ")
            raw.extend(part)
            remaining -= len(part)
        return bytes(raw)
    finally:
        os.close(fd)


def create_private(parent, name):
    parent = private_dir(parent)
    dest = parent / name
    if dest.exists() or dest.is_symlink():
        raise ValueError("COPY_ALREADY_EXISTS_NO_RETRY")
    dest.mkdir(mode=0o700)
    return private_dir(dest)


def write_once(directory, name, raw):
    directory = private_dir(directory)
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    return path


def persist_dir(dirpath):
    fd = os.open(dirpath, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def verify_timeline(source, ack, precommit, independent):
    if not all(isinstance(x, dict) for x in (source, ack, precommit, independent)):
        raise ValueError("PROOF_FORMAT_MISSING")
    if (source.get("status") != "TEST_ONLY_ACTUAL_SOURCE_SIGNED_AND_LOCALLY_INDEXED"
            or independent.get("original_source_original_chain_verified") is not True
            or independent.get("public_mac_ed25519_signature_reverified") is not True
            or independent.get("precisely_one_causal_research_source_event") is not True):
        raise ValueError("INDEPENDENT_SOURCE_REVIEW_NOT_ACCEPTED")
    equal = (
        (ack.get("source_event_id"), source.get("source_event_id")),
        (ack.get("first_source_cohort_id"), source.get("original_first_source_cohort_id")),
        (ack.get("original_signed_source_receipt_sha256"), source.get("signed_proof_sha256")),
        (ack.get("source_available_at_ms"), source.get("real_original_quote_available_ms")),
        (ack.get("quote_expiry_ms"), source.get("real_original_quote_expiry_ms")),
        (ack.get("original_independent_verifier_report_id"), independent.get("report_id")),
        (ack.get("selected_action"), "NO_TRADE"),
        (ack.get("candidate_universe"), ["NO_TRADE"]),
        (ack.get("selected_logged_q32_propensity"), 4294967296),
        (ack.get("native_witness_sequence"), 1),
        (precommit.get("fixed_candidate_set"), ["NO_TRADE"]),
        (precommit.get("selected_q32_propensity"), 4294967296),
        (precommit.get("fixed_decision_delay_ms"), 3500),
    )
    if any(x != y for x, y in equal):
        raise ValueError("ORIGINAL_SOURCE_NATIVE_ACK_IDENTITY_MISMATCH")
    if any(ack.get(flag) is not False for flag in (
            "production_authority", "economic_reward_observed",
            "original_opaque_llm_propensity_used",
            "cross_host_wallclock_order_used_for_admission")):
        raise ValueError("TEST_ONLY_ACK_FALSE_PRODUCT_AUTHORITY")
    if not (ack.get("root_and_role_test_only") is True
            and ack.get("no_model_inference") is True
            and ack.get("original_canonical_writer_executed") is True
            and precommit.get("broker_order_authority") is False
            and precommit.get("model_inference") is False
            and ack.get("broker_orders_sent") == 0):
        raise ValueError("TEST_ONLY_ORIGINAL_NATIVE_SCOPE_INVALID")
    observed = source["real_original_quote_at_ms"]
    available = source["real_original_quote_available_ms"]
    expiry = source["real_original_quote_expiry_ms"]
    source_cutoff = source["true_new_source_decision_cutoff_ms"]
    native_deadline = ack["native_precommitted_deadline_ms"]
    prepared = precommit["precommitted_at_ms"]
    sealed = source["signed_proof_durable_at_ms"]
    verified_reply = ack["pocket4_received_signature_at_ms"]
    native_request = ack["native_issuer_request_at_ms"]
    native_ack = ack["native_witness_ack_at_ms"]
    if (not all(type(v) is int for v in (
            observed, available, expiry, source_cutoff, native_deadline,
            prepared, sealed, verified_reply, native_request, native_ack))
            or not prepared < observed <= available <= sealed <= source_cutoff
            or not observed + 5000 >= expiry >= native_deadline
            or native_deadline != available + 3500
            or not source_cutoff < native_deadline
            or not sealed <= native_request <= verified_reply <= native_ack
            or not native_ack <= source_cutoff
            or not native_ack < native_deadline):
        raise ValueError("TEST_NATIVE_ORIGINAL_TIMELINE_NOT_CAUSAL")
    return {
        "original_received_at_ms": available,
        "original_source_signed_fsync_at_ms": sealed,
        "native_sign_request_at_ms": native_request,
        "native_sign_response_received_at_ms": verified_reply,
        "native_witness_ack_at_ms": native_ack,
        "precommitted_native_deadline_ms": native_deadline,
        "original_ib_reported_expiry_at_ms": expiry,
        "ack_before_source_decision_cutoff_ms": source_cutoff - native_ack,
        "ack_before_native_deadline_ms": native_deadline - native_ack,
        "ack_before_original_ib_expiry_ms": expiry - native_ack,
        "crosshost_wallclock_order_not_used": True,
    }

def audit(root, source_root, native_root, canonical_workspace, native_source):
    root = private_dir(root)
    source_root = private_dir(source_root)
    native_root = private_dir(native_root)
    original = verify_original_source(source_root)
    source = json.loads(read_private(
        source_root / "source-readiness-outcome.json"))
    native = json.loads(read_private(
        native_root / "native-prewarm-public-ack.json"))
    intent = json.loads(read_private(
        native_root / "native-prewarm-intent.json"))
    if digest(read_private(source_root / "original-quote-signed-proof.json")) != source["signed_proof_sha256"]:
        raise ValueError("ORIGINAL_SOURCE_SIGNED_PROOF_HASH_MISMATCH")
    chronology = verify_timeline(source, native, intent, original)
    original_bytes = {
        name: read_private(native_root / name, 1024 * 1024)
        for name in ("ledger", "witness", "native-prewarm-public-ack.json")
    }
    if len(original_bytes["ledger"]) != 707 or len(original_bytes["witness"]) != 160:
        raise ValueError("NATIVE_TEST_LEDGER_NOT_EXACTLY_ONE_SIGNED_RECORD")
    bin_source = canonical_workspace / "bridge" / "src" / "main.rs"
    if (bin_source.is_symlink() or not bin_source.is_file()
            or native_source.is_symlink() or not native_source.is_file()):
        raise ValueError("NATIVE_CODE_PATH_CHANGED")
    source_id = digest(native_source.read_bytes())
    if (source_id != digest(bin_source.read_bytes())
            or source_id != "0da6c6163e8d6042f91ebe53ac39874d51c3cf2cc8dcff52e9e8b8609e8a0235"):
        raise ValueError("NATIVE_COMPILATION_SOURCE_PIN_CHANGED")
    binary = canonical_workspace / "target" / "debug" / "train_v2_native_abstain"
    if digest(binary.read_bytes()) != "41b6be2ac9f85e0f4f592016037c82819a87df04e843fef8d0d316a08984924e":
        raise ValueError("NATIVE_ORIGINAL_COMPILED_BINARY_PIN_CHANGED")
    valid_copy = create_private(root, "copied-exactly-one-native-ack")
    lost_copy = create_private(root, "copy-with-acknowledged-record-missing")
    for name, raw in original_bytes.items():
        write_once(valid_copy, name, raw)
        write_once(lost_copy, name, raw[:72] if name == "ledger" else raw)
    corrupt = bytearray(original_bytes["witness"])
    corrupt[96] ^= 0x01
    corrupt_file = write_once(root, "witness-bad-checksum-copy.bin", bytes(corrupt))
    for folder in (valid_copy, lost_copy, root):
        persist_dir(folder)
    # The original native signature/ledger/witness is NEVER reopened read/write.
    test_cmd = [
        "cargo", "test", "--offline", "--locked",
        "--manifest-path", str(canonical_workspace / "Cargo.toml"),
        "-p", "heptatrader-train-v2-native-abstain-lab",
        "independently_recovered_native_ack",
        "--", "--ignored", "--nocapture",
    ]
    env = dict(os.environ)
    env["CARGO_BUILD_JOBS"] = "2"
    env["HEPTA_NATIVE_PREVIOUS_ACK_COPY"] = str(valid_copy)
    env["HEPTA_NATIVE_LOST_LEDGER_COPY"] = str(lost_copy)
    env["HEPTA_NATIVE_CORRUPTED_WITNESS"] = str(corrupt_file)
    run = subprocess.run(test_cmd, env=env, text=True,
                         capture_output=True, timeout=120)
    lograw = (run.stdout + "\nSTDERR\n" + run.stderr).encode()
    write_once(root, "exact-original-rust-recovery.log", lograw)
    persist_dir(root)
    if (run.returncode
            or "NATIVE_TYPED_TEST_ONLY_ABSTAIN_WITNESS_REOPENED" not in run.stdout
            or "3 passed; 0 failed; 0 ignored" not in lograw.decode()):
        raise ValueError("CANONICAL_ORIGINAL_NATIVE_RECOVERY_REJECTED")
    original_after = {
        name: read_private(native_root / name, 1024 * 1024)
        for name in original_bytes
    }
    if original_after != original_bytes:
        raise ValueError("ORIGINAL_SIGNED_NATIVE_LEDGER_OR_WITNESS_MUTATED")
    if native.get("native_chain_digest") not in run.stdout:
        raise ValueError("RECOVERED_ORIGINAL_NATIVE_CHAIN_DIGEST_MISMATCH")
    receipt = {
        "schema": SCHEMA,
        "test_only": True,
        "original_source_public_chain_verified": True,
        "mac_source_signature_reverified": True,
        "source_independent_report_id": original["report_id"],
        "native_original_sha256": {
            name: digest(raw) for name,raw in sorted(original_bytes.items())
        },
        "rust_native_program_sha256": source_id,
        "rust_compiled_test_binary_sha256": digest(binary.read_bytes()),
        "rust_original_recovery_test_log_sha256": digest(lograw),
        "original_typed_native_decision_witness_reopened": True,
        "original_test_recovered_chain_digest": native["native_chain_digest"],
        "copied_corrupted_witness_refused": True,
        "copied_lost_acknowledged_ledger_refused": True,
        "original_files_unchanged_after_recovery_tests": True,
        "original_received_source_to_ledger_ack_timeline": chronology,
        "actual_opaque_policy_decision_observed": False,
        "canonical_product_root_enrolled": False,
        "actual_model_inference": False,
        "original_broker_prepared": False,
        "economic_alpha_observed": False,
        "canonical_dataset_frozen": False,
        "canonical_owner_current": False,
        "automatic_trading": False,
        "broker_orders_sent": 0,
    }
    receipt["report_id"] = digest(
        json.dumps(receipt,sort_keys=True,separators=(",",":")).encode())
    return receipt

def main():
    parser = argparse.ArgumentParser()
    for key in ("root","source-root","native-root",
                "native-workspace","native-source","output"):
        parser.add_argument("--"+key,required=True,type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    report = audit(args.root,args.source_root,args.native_root,
                   args.native_workspace,args.native_source)
    report["readback_test_source_sha256"] = digest(Path(__file__).read_bytes())
    report["report_id"] = digest(json.dumps(
        {k:v for k,v in report.items() if k!="report_id"},
        sort_keys=True,separators=(",",":")).encode())
    if args.output.parent != args.root:
        raise ValueError("READBACK_OUTPUT_NOT_UNDER_ISOLATED_ROOT")
    payload = (json.dumps(report,sort_keys=True,indent=2)+"\n").encode()
    write_once(args.root,args.output.name,payload)
    persist_dir(args.root)
    print(json.dumps({
        "status":"TEST_ONLY_NEW_NATIVE_LEDGER_INDEPENDENTLY_REOPENED",
        "native_witness_ack":True,
        "corrupted_copies_refused":True,
        "original_witness_sha256":report["native_original_sha256"]["witness"],
        "original_chain_digest":report["original_test_recovered_chain_digest"],
        "broker_orders_sent":0,
        "receipt_sha256":digest(payload)},sort_keys=True))

if __name__=="__main__":
    main()
