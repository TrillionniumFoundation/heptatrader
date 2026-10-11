"""Qian-qi transport-only lab: Mac Generator, X230 Observer, Pocket4 native Rust.

Never transmits private keys; signs only fixed, TEST-ONLY native protocol
messages. Test roles are not user-approved production principals.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROLES = {
    "root": "qian-qi",
    "generator": "mac-ts",
    "observer": "x230-ts",
    "evaluator": "qian-qi",
    "privacy": "qian-qi",
}
NATIVE = ("/home/alex/.local/state/heptatrader/operator-evidence/"
          "train-v2-full-canonical-20261011/native-test-target/"
          "debug/examples/crosshost_native_from_heptatrader")
POCKET_EVIDENCE = ("/home/alex/.local/state/heptatrader/operator-evidence/"
                   "train-v2-crosshost-native-20261011")
TEST_SIGNER = ("$HOME/.local/state/hepta-crosshost-native-test-20261011/"
               "signer.py")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run_signer(role: str, message_hex: str, *, manifest: dict) -> dict:
    assert role in ROLES
    assert len(message_hex) <= 8192
    host=ROLES[role]
    if host == "qian-qi":
        home = Path.home() / ".local/state/hepta-crosshost-native-test-20261011"
        cmd = ["python3", str(home/"signer.py"), "sign", role]
    else:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
               host, f'python3 "{TEST_SIGNER}" sign {role}']
    result = subprocess.run(
        cmd, input=message_hex+"\n", text=True, capture_output=True,
        timeout=25, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"TEST_ONLY_{role}_HOST_SIGN_FAILURE:"
                           + result.stderr[:200])
    reply=json.loads(result.stdout)
    pub=manifest["roles"][role]
    msg=bytes.fromhex(message_hex)
    sig=bytes.fromhex(reply["signature_hex"])
    assert len(sig)==64
    assert reply["role"] == role
    assert reply["actual_signing_host"] == pub["actual_signing_host"]
    assert reply["public_key_hex"] == pub["public_key_hex"]
    assert reply["native_signing_message_sha256"] == _sha(msg)
    assert reply["test_only"] and not reply["private_key_exported"]
    Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(pub["public_key_hex"])).verify(sig,msg)
    return {
        "role":role, "signature_hex":sig.hex(),
        # This is a PUBLIC deterministic native signing payload, never a key.
        # Keeping exact bytes permits independent later verification after
        # temporary signer private keys have been securely removed.
        "native_signing_message_hex":message_hex,
        "actual_signing_host":reply["actual_signing_host"],
        "message_sha256":_sha(msg),
        "signature_sha256":_sha(sig),
        "signature_cryptographically_verified_against_host_public_key":True,
        "signing_key_transferred":False,
        "purpose":"synthetic_native_lab_only",
    }


def _launch() -> dict:
    os.umask(0o077)
    root=Path.home()/".local/state/hepta-crosshost-native-test-20261011"
    manifest=json.loads((root/"public_roles.json").read_text())
    assert manifest["purpose"]=="test-only-no-production"
    original_publics = (root/"public_roles.json").read_bytes()
    assert _sha(original_publics)=="0956ec8a47b8b6e05f561fd043f52d26e6ee08c0b88c68bf8d754e4f0ffb4379"
    assert len({manifest["roles"][role]["actual_signing_host"]
                for role in ("root","generator","observer")}) == 3

    pocket_pub = POCKET_EVIDENCE + "/public_roles.json"
    dirpath = POCKET_EVIDENCE + "/native-ledger-and-witness-v3"
    test_dir_command = (f'umask 077; test -f "{pocket_pub}" && '
                        f'mkdir -m 700 "{dirpath}" && '
                        f'sha256sum "{pocket_pub}"')
    check=subprocess.run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=6",
                          "pocket4",test_dir_command],capture_output=True,
                          text=True,timeout=16)
    if check.returncode or not check.stdout.startswith(_sha(original_publics)):
        raise RuntimeError("WRITER_DIRECTORY_OR_PUBLIC_IDENTITY_NOT_PINNED")

    native_command=(f'umask 077; exec "{NATIVE}" "{pocket_pub}" "{dirpath}"')
    process=subprocess.Popen(
        ["ssh","-o","BatchMode=yes","-o","ConnectTimeout=6",
         "pocket4",native_command],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,bufsize=1)
    transcript=[]
    try:
        for line in process.stdout:
            line=line.strip()
            if line.startswith("SIGN_REQUEST "):
                parts=line.split(" ")
                if len(parts)!=3:
                    raise RuntimeError("UNEXPECTED_NATIVE_SIGNATURE_REQUEST")
                _,role,payload=parts
                if role not in ROLES:
                    raise RuntimeError("UNAPPROVED_REMOTE_SIGN_ROLE")
                receipt=_run_signer(role,payload,manifest=manifest)
                transcript.append(receipt)
                process.stdin.write("SIG "+receipt["signature_hex"]+"\n")
                process.stdin.flush()
                print("SEPARATE_HOST_SIGNED_NATIVE_MESSAGE",
                      role, receipt["actual_signing_host"],
                      "message_sha256",receipt["message_sha256"],flush=True)
            elif line.startswith("RESULT_JSON "):
                native_result=json.loads(line[len("RESULT_JSON "):])
                print("NATIVE_CROSSHOST_LAB_FINISHED",
                      native_result["result"], flush=True)
            else:
                raise RuntimeError("UNEXPECTED_NATIVE_PROCESS_STDOUT:" + line[:100])
        code=process.wait(timeout=10)
        errors=process.stderr.read()
        if code or errors.strip():
            if code:
                raise RuntimeError(f"NATIVE_LEDGER_LAB_EXIT_{code}:"+errors[-1500:])
            print("NATIVE_STDERR_DIAGNOSTICS",errors[-200:])
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    expected=["root","generator","generator","observer","evaluator","privacy"]
    actual=[x["role"] for x in transcript]
    if actual!=expected:
        raise RuntimeError("CROSSHOST_NATIVE_ROLE_ORDER_UNEXPECTED:"+str(actual))
    assert native_result["result"]=="TEST_ONLY_NATIVE_PASSED"
    assert not native_result["real_broker_evidence"]
    assert not native_result["product_root_authority_enrolled"]
    assert native_result["native_signed_decisions"]==1
    assert native_result["native_signed_terminal_outcomes"]==1
    assert native_result["native_v3_revalidation_rejected_after_withdrawal"]
    assert native_result["native_durable_witness_frontier_records"]==3
    proof={
        "schema":"heptatrader.train-v2-true-crosshost-test-only-native-signature-lab.v1",
        "observed_at_utc":datetime.now(timezone.utc).isoformat(),
        "original_canonical_exact_commit":"78fdb0cf8537e3a84fc6e0a849707559c80881e8",
        "public_manifest_sha256":_sha(original_publics),
        "participating_physical_hosts":{
            role:manifest["roles"][role]["actual_signing_host"] for role in ROLES},
        "signer_private_keys_ever_exported":False,
        "all_test_only":True,
        "native_product_ledger_binary_source_unchanged":True,
        "native_signed_root_distribution_exercised":True,
        "native_generator_mac_and_observer_x230_signatures_exercised":True,
        "native_owned_evaluator_and_privacy_fixture_signatures_exercised":True,
        "native_result":native_result,
        "signature_requests":transcript,
        "real_original_trade_policy_decision_or_action":False,
        "real_broker_source_or_fills":False,
        "independent_production_controllers_enrolled":False,
        "real_paper_trader_native_prod_owner_deployed":False,
        "real_dataset_v3_training_eligible":False,
        "actual_net_pnl_or_efficacy_qualified":False,
    }
    raw=json.dumps(proof,sort_keys=True,indent=2).encode()+b"\n"
    path=root/"native-proof-v3.json"
    with open(path,"xb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    print("CROSSHOST_NATIVE_ROOT_GEN_OBSERVER_EVAL_WITHDRAWAL_SHA256",
          _sha(raw),flush=True)
    return proof


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--run",action="store_true",required=True)
    p.parse_args()
    _launch()


if __name__=="__main__":
    main()
