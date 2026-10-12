"""Read-only hostile copy checks for frozen ORIGINAL Phase 4L evidence.

No original file mutation, private key, broker call or native ledger write.
All rewrites happen only on owner-private temporary *copies*.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from tempfile import TemporaryDirectory

from .native_preaction_timeline import inspect_original_phase4l, BLOCKED
from .contracts import ContractError

SOURCE_FILES = (
    "public_roles.json",
    "real-shadow-decision-v1.json",
    "native-real-quote-packet.json",
    "real-five-minute-midprice-outcome.json",
    "native-real-price-observer-packet.json",
    "native-source-time-crosshost-signatures.json",
    "native-init-crosshost-public-signatures.json",
    "native-finish-crosshost-public-signatures.json",
    "causal-feature-before-outcome.json",
    "native-lab-v1/native-source-decision-ack.json",
    "native-lab-v1/native-price-outcome-and-dataset-test-ack.json",
)


def _copy_evidence(src, target):
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    for entry in SOURCE_FILES:
        original = src / entry
        if original.is_symlink() or not original.is_file() or original.stat().st_size > 65536:
            raise ContractError("PHASE4S_ORIGINAL_FILE_NOT_SAFE:" + entry)
        copied = target / entry
        copied.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # The original evidence remains byte-immutable. Test COPIES must
        # be private 0600 at CREATE time, not repaired by chmod afterward.
        fd = os.open(copied, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as dst:
            dst.write(original.read_bytes())
            dst.flush()
            os.fsync(dst.fileno())


def _change_json(path, mutation):
    body = json.loads(path.read_bytes())
    mutation(body)
    path.write_bytes((json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n").encode())
    path.chmod(0o600)


def audit(original, period, out):
    original, out = Path(original), Path(out)
    if out.exists() or out.is_symlink():
        raise ContractError("PHASE4S_CREATE_ONLY_RECEIPT")
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = out.parent
    if root.is_symlink() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ContractError("PHASE4S_PARENT_NOT_PRIVATE")
    actual = inspect_original_phase4l(original, period)
    if actual["assessment"] != BLOCKED:
        raise ContractError("PHASE4S_ACTUAL_OLD_TIMELINE_NOT_REFUSED")
    cases = (
        (
            "forged_local_native_ack_before_quote_expiry",
            "native-lab-v1/native-source-decision-ack.json",
            lambda data: data.__setitem__("native_acknowledged_at_ms",
                                           actual["original_quote_expires_at_ms"]-1),
        ),
        (
            "forged_generator_signed_at_earlier",
            "native-source-time-crosshost-signatures.json",
            lambda data: data["signatures"][1].__setitem__(
                "signed_at_utc", "2026-10-12T01:17:04.000000+00:00"),
        ),
        (
            "forged_original_quote_expiry",
            "real-shadow-decision-v1.json",
            lambda data: data.__setitem__(
                "source_expires_at_ms", actual["native_source_decision_witness_ack_at_ms"]+5000),
        ),
        (
            "forged_product_original_policy_propensity",
            "real-shadow-decision-v1.json",
            lambda data: data.__setitem__(
                "original_opaque_llm_trading_policy_propensity_qualified", True),
        ),
        (
            "fabricated_original_prepared_flag",
            "real-shadow-decision-v1.json",
            lambda data: data.__setitem__(
                "not_an_original_broker_PREPARED_command", False),
        ),
        (
            "original_source_file_substituted_by_path",
            "causal-feature-before-outcome.json",
            lambda data: data.__setitem__(
                "source_segment_path", str(out.parent / "nonexistent-source-quote.jsonl")),
        ),
    )
    rejected = []
    with TemporaryDirectory(prefix=".phase4s-copy-", dir=root) as tmp:
        temp = Path(tmp)
        for number,(label,relative,mutation) in enumerate(cases):
            copied = temp / ("case-"+str(number))
            _copy_evidence(original,copied)
            _change_json(copied / relative, mutation)
            try:
                inspect_original_phase4l(copied, period)
            except (ContractError, ValueError, OSError) as error:
                rejected.append({
                    "case":label,
                    "rejection_type":type(error).__name__,
                    "rejection_reason":str(error)[:175],
                })
            else:
                raise ContractError("PHASE4S_FORGED_ORIGINAL_PASSED:"+label)
    body = {
        "schema":"heptatrader.train-v2-phase4s-original-hostile-copy-audit.v1",
        "original_source_phase4l_unchanged":True,
        "tamper_test_source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "actual_original_auditor_source_sha256":hashlib.sha256(
            Path(inspect_original_phase4l.__code__.co_filename).read_bytes()).hexdigest(),
        "test_copies_private_from_creation":True,
        "original_signature_ledger_ack_retained":True,
        "source_witness_original_attested":True,
        "source_timeline_status":actual["assessment"],
        "same_original_audit_id":actual["receipt_id"],
        "hostile_private_copy_count":len(cases),
        "fail_closed_hostile_copy_count":len(rejected),
        "rejections":rejected,
        "canonical_owner_enrolled":False,
        "automatic_training":False,
        "model_promoted":False,
        "trade_authority":False,
        "broker_orders_sent":0,
    }
    data=(json.dumps(body,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as target:
        target.write(data);target.flush();os.fsync(target.fileno())
    parent=os.open(out.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent)
    finally:os.close(parent)
    return body,hashlib.sha256(data).hexdigest()


def main():
    p=argparse.ArgumentParser()
    for name in ("original","period","output"):
        p.add_argument("--"+name,required=True,type=Path)
    a=p.parse_args()
    result, sha = audit(a.original,a.period,a.output)
    print(json.dumps({
        "original_source_timeline":result["source_timeline_status"],
        "private_copy_attacks_rejected":result["fail_closed_hostile_copy_count"],
        "receipt_sha256":sha,
        "real_broker_orders":0,
        "production_permission":False,
    }, sort_keys=True))


if __name__=="__main__":
    main()
