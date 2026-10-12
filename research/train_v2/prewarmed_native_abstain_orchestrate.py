"""One fresh source -> prewarmed original native NO_TRADE LedgerWriter test.

qian-qi coordinates authorized Pocket4 and Mac SSH. NO broker orders, model
inference, product Root, restart, old experiment repair or automatic retry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import shlex
import subprocess
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from train_v2.prewarmed_signer_latency import session, receive, exchange, close
from train_v2.prewarmed_source_orchestrate_v2 import run as original_source_run

SCHEMA = "heptatrader.train-v2-real-source-prewarmed-native-abstain-test.v1"
NATIVE_INPUT_KEYS = frozenset((
    "schema", "test_only", "no_model", "no_orders",
    "original_source_independently_verified", "original_signed_source_fsynced",
    "production_root_enrolled", "model_prediction_emitted",
    "actual_broker_prepared", "economic_return_attested",
    "source_event_id", "original_quote_receipt", "first_source_cohort_id",
    "independent_verifier_report_id", "original_signed_source_proof_sha256",
    "original_source_prefix_sha256", "registered_at_ms",
    "original_quote_event_ms", "original_quote_available_ms",
    "original_quote_expiry_ms", "original_signed_source_durable_at_ms",
    "original_source_decision_cutoff_ms", "fixed_native_delay_ms",
    "candidate_universe", "actual_selected_action",
    "selected_logged_behavior_q32", "trading_role",
))

def validate_native_source_packet(packet):
    """Fail before native dispatch if the Python packet differs from Rust V1."""
    if not isinstance(packet, dict) or set(packet) != NATIVE_INPUT_KEYS:
        raise ValueError("NATIVE_27_FIELD_SCHEMA_MISMATCH")
    if (packet["schema"] != "heptatrader.train-v2-prewarmed-native-abstain-input.v1"
        or packet["candidate_universe"] != ["NO_TRADE"]
        or packet["actual_selected_action"] != "NO_TRADE"
        or packet["selected_logged_behavior_q32"] != 4294967296
        or packet["fixed_native_delay_ms"] != 3500
        or packet["trading_role"] != "NONE"):
        raise ValueError("NATIVE_TEST_POLICY_SCOPE")
    for key in ("test_only", "no_model", "no_orders",
                "original_source_independently_verified",
                "original_signed_source_fsynced"):
        if packet[key] is not True:
            raise ValueError("NATIVE_TEST_ORIGINAL_EVIDENCE_MISSING")
    for key in ("production_root_enrolled", "model_prediction_emitted",
                "actual_broker_prepared", "economic_return_attested"):
        if packet[key] is not False:
            raise ValueError("NATIVE_TEST_PRIVILEGE_CLAIM_FORBIDDEN")
    for key in ("source_event_id", "original_quote_receipt",
                "first_source_cohort_id", "independent_verifier_report_id",
                "original_signed_source_proof_sha256",
                "original_source_prefix_sha256"):
        value = packet[key]
        if not isinstance(value, str) or len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value):
            raise ValueError("NATIVE_ORIGINAL_SHA256_INVALID")
    t = {name:packet[name] for name in (
        "registered_at_ms","original_quote_event_ms",
        "original_quote_available_ms","original_quote_expiry_ms",
        "original_signed_source_durable_at_ms",
        "original_source_decision_cutoff_ms")}
    if any(type(value) is not int for value in t.values()):
        raise ValueError("NATIVE_SOURCE_TIMES_NOT_INTEGERS")
    arrival = t["original_quote_available_ms"]
    deadline = arrival + 3500
    if not (t["registered_at_ms"] + 30000 < arrival
        and t["original_quote_event_ms"] <= arrival
        <= t["original_signed_source_durable_at_ms"]
        <= t["original_source_decision_cutoff_ms"] < deadline
        <= t["original_quote_expiry_ms"]
        <= t["original_quote_event_ms"] + 5000):
        raise ValueError("NATIVE_ORIGINAL_SOURCE_TIMELINE_MISMATCH")
    return packet

MAC_GENERATOR = r'''
import sys,json,socket,time,hashlib
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
D=b"hepta.learning-ledger.signed-evidence.v1"
key=Ed25519PrivateKey.generate()
pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
print(json.dumps({"kind":"READY","host":socket.gethostname(),"pub":pub,
"test_only":True,"memory_only":True,"role":"native_lab_generator"}),flush=True)
for line in sys.stdin:
 try:
  req=json.loads(line);data=bytes.fromhex(req["body"])
  if (req["kind"]!="SIGN" or not data.startswith(D)
      or b"lab.mac.only.generator" not in data or len(data)>2048):
   raise ValueError("NOT_ORIGINAL_NATIVE_GENERATOR_TEST_DOMAIN")
  signed_at=time.time_ns()//1000000
  sig=key.sign(data).hex()
  print(json.dumps({"kind":"SIGNED","seq":req["seq"],"sig":sig,
   "body_sha":hashlib.sha256(data).hexdigest(),"signed_at_ms":signed_at,
   "test_only":True}),flush=True)
 except Exception as err:
  print(json.dumps({"kind":"REJECT","type":type(err).__name__}),flush=True)
'''


def verified_ssh(host: str, command: str, timeout: float = 12) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
         "-o", "StrictHostKeyChecking=yes", host, command],
        capture_output=True, text=True, check=True, timeout=timeout,
    )


def verify_pinned_native_identity(host, binary, source, expected_binary, expected_source):
    for want in (expected_binary, expected_source):
        if (type(want) is not str or len(want) != 64
                or any(c not in "0123456789abcdef" for c in want)):
            raise ValueError("NATIVE_BUILD_PIN_MISSING")
    command = "sha256sum -- " + shlex.quote(str(binary)) + " " + shlex.quote(str(source))
    measured = verified_ssh(host, command, timeout=8)
    rows = measured.stdout.strip().splitlines()
    if len(rows) != 2:
        raise RuntimeError("NATIVE_IDENTITY_SHA_COUNT")
    found = [line.split()[0] for line in rows]
    if found != [expected_binary, expected_source]:
        raise RuntimeError("NATIVE_BINARY_OR_SOURCE_HASH_CHANGED")
    return {"binary_sha256": expected_binary, "source_sha256": expected_source,
            "source_compiler_relation": "RECORDED_ISOLATED_LOCKED_BUILD_NOT_PRODUCT_PROOF",
            "broker_orders_sent": 0}


def protocol_line(reader, expected: str, timeout: float):
    result = receive(reader, timeout)
    # Native stdout has a prefix and one JSON body, unlike source worker lines.
    # receive() parses JSON directly; the prewarmed native Rust line is prefixed.
    if result.get("kind") != expected:
        raise ValueError("UNEXPECTED_NATIVE_PROTOCOL_TYPE")
    return result


def native_read_line(pipe, kind: str, seconds=10):
    if not select.select([pipe], [], [], seconds)[0]:
        raise TimeoutError("PREWARMED_NATIVE_DID_NOT_RESPOND")
    raw = pipe.readline()
    if not raw or len(raw)>4096:
        raise RuntimeError("PREWARMED_NATIVE_EXIT_OR_INVALID_LINE")
    prefix = kind + " "
    if not raw.startswith(prefix):
        raise ValueError("PREWARMED_NATIVE_PROTOCOL_CHANGED")
    obj=json.loads(raw[len(prefix):])
    if not isinstance(obj,dict):
        raise ValueError("PREWARMED_NATIVE_RESPONSE_SHAPE")
    return obj


def independent_pocket4_source_verify(host, repo, registry):
    import textwrap
    # Execute the unchanged Phase 4U original read-only public reinspection.
    cmd = ("cd "+shlex.quote(str(repo))+
           " && env PYTHONPATH=research:research/neuron_learning:scripts"+
           " python3 -B -c "+
           shlex.quote(
               "import json;from pathlib import Path;"
               "from train_v2.prewarmed_source_verify_v2 import verify;"
               "r=verify(Path("+repr(str(registry))+"));"
               "print(json.dumps({'report_id':r['report_id'],"
               "'source_verified':r['original_source_original_chain_verified'],"
               "'signature_verified':r['public_mac_ed25519_signature_reverified'],"
               "'first_source':r['original_first_source_cohort_id'],"
               "'event_count':r['precisely_one_causal_research_source_event'],"
               "'original_expiry_ms':r['original_ib_stale_after_ms']}))"
           ))
    observed = verified_ssh(host,cmd,timeout=12)
    result=json.loads(observed.stdout)
    if (not result["source_verified"] or not result["signature_verified"]
        or not result["event_count"] or len(result["report_id"])!=64):
        raise ValueError("INDEPENDENT_SOURCE_VERIFICATION_FAILED")
    return result


def run(mac, source_host, repo, source_root, registry_root,
        period, native_binary, native_root,
        expected_native_binary_sha256, expected_native_source_sha256):
    generator = native = None
    source_result = source_proof = ack = None
    transcript = {}
    try:
        native_source = repo / "research/train_v2/prewarmed_native_abstain.rs"
        pinned_native = verify_pinned_native_identity(
            source_host, native_binary, native_source,
            expected_native_binary_sha256, expected_native_source_sha256)
        transcript["prelaunch_native_build_identity"] = pinned_native
        generator, ready, _ = session(mac, MAC_GENERATOR)
        if (ready.get("host")!="MacBook-Pro.local"
                or ready.get("memory_only") is not True
                or ready.get("role")!="native_lab_generator"):
            raise ValueError("WRONG_PHYSICAL_TEST_GENERATOR")
        key=Ed25519PublicKey.from_public_bytes(bytes.fromhex(ready["pub"]))
        verified_ssh(source_host,
            "mkdir -m 700 "+shlex.quote(str(native_root)), timeout=8)
        cmd=shlex.quote(str(native_binary))+" "+shlex.quote(str(native_root))+" "+ready["pub"]
        native=subprocess.Popen(
            ["ssh","-o","BatchMode=yes","-o","ConnectTimeout=5",
             "-o","StrictHostKeyChecking=yes",source_host,cmd],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,bufsize=1)
        pre=native_read_line(native.stdout,"NATIVE_READY",12)
        if (pre.get("native_writer_initial_frontier")!=0
                or pre.get("fixed_decision_delay_ms")!=3500
                or pre.get("broker_orders_sent")!=0):
            raise ValueError("NATIVE_LEDGER_NOT_PREWARMED_EMPTY")
        transcript["prewarm"]=pre
        prestored=verified_ssh(
            source_host,
            "cat "+shlex.quote(str(native_root/"native-prewarm-intent.json")),
            timeout=5)
        plan=json.loads(prestored.stdout)
        if (plan.get("precommitted_at_ms")>pre["prewarmed_at_ms"]
                or plan.get("fixed_decision_delay_ms")!=3500
                or plan.get("generator_mac_public_key")!=ready["pub"]
                or plan.get("selected_q32_propensity")!=4294967296
                or plan.get("fixed_candidate_set")!=["NO_TRADE"]):
            raise ValueError("NATIVE_TEST_POLICY_NOT_PRECOMMITTED")
        transcript["native_original_precommitted_plan"]=plan
        source_result=original_source_run(
            mac=mac,source_host=source_host,repo=repo,
            source_root=source_root,registry_root=registry_root,period=period)
        transcript["source_result"]=source_result
        if not source_result.get("research_source_success"):
            raise RuntimeError("SOURCE_WINDOW_MISSED_NO_RETRY")
        source=source_result["source_outcome"]
        if (plan["precommitted_at_ms"]>=
                source_result["predeclared_plan"]["sealed_at_ms"]
                or plan["precommitted_at_ms"]>=source["real_original_quote_available_ms"]):
            raise ValueError("NATIVE_SHADOW_POLICY_NOT_PRECOMMITTED_BEFORE_SOURCE")
        if source["status"]!="TEST_ONLY_ACTUAL_SOURCE_SIGNED_AND_LOCALLY_INDEXED":
            raise ValueError("ORIGINAL_SOURCE_FAILED")
        source_proof=independent_pocket4_source_verify(
            source_host,repo,registry_root)
        transcript["source_independent_public_verification"]=source_proof
        if source_proof["first_source"]!=source["original_first_source_cohort_id"]:
            raise ValueError("ROTATION_COHORT_MISMATCH")
        packet={
            "schema":"heptatrader.train-v2-prewarmed-native-abstain-input.v1",
            "test_only":True,"no_model":True,"no_orders":True,
            "original_source_independently_verified":True,
            "original_signed_source_fsynced":True,
            "production_root_enrolled":False,
            "model_prediction_emitted":False,
            "actual_broker_prepared":False,
            "economic_return_attested":False,
            "source_event_id":source["source_event_id"],
            "original_quote_receipt":source_result["observed_source_event"]["original_quote_receipt"],
            "first_source_cohort_id":source["original_first_source_cohort_id"],
            "independent_verifier_report_id":source_proof["report_id"],
            "original_signed_source_proof_sha256":source["signed_proof_sha256"],
            "original_source_prefix_sha256":source["original_source_prefix_sha256"],
            "registered_at_ms":source_result["predeclared_plan"]["sealed_at_ms"],
            "original_quote_event_ms":source["real_original_quote_at_ms"],
            "original_quote_available_ms":source["real_original_quote_available_ms"],
            "original_quote_expiry_ms":source["real_original_quote_expiry_ms"],
            "original_signed_source_durable_at_ms":source["signed_proof_durable_at_ms"],
            "original_source_decision_cutoff_ms":source["true_new_source_decision_cutoff_ms"],
            "fixed_native_delay_ms":3500,
            "candidate_universe":["NO_TRADE"],
            "actual_selected_action":"NO_TRADE",
            "selected_logged_behavior_q32":4294967296,
            "trading_role":"NONE",
        }
        now=int(time.time_ns()//1000000)
        deadline=packet["original_quote_available_ms"]+3500
        if now>=min(deadline,packet["original_quote_expiry_ms"]):
            raise RuntimeError("SOURCE_EVENT_TOO_OLD_FOR_PREWARMED_NATIVE")
        validate_native_source_packet(packet)
        transcript["native_input_packet"]=packet
        transcript["native_input_transport_clock_ms_untrusted"] = time.time_ns() // 1_000_000
        native.stdin.write(json.dumps(packet,sort_keys=True)+"\n")
        native.stdin.flush()
        request=native_read_line(native.stdout,"NATIVE_SIGN_REQUEST",4)
        if (request["test_only"] is not True
                or request["decision_deadline_ms"]!=deadline
                or request["quote_expiry_ms"]!=packet["original_quote_expiry_ms"]):
            raise ValueError("NATIVE_SIGNATURE_WRONG_DEADLINE")
        raw=bytes.fromhex(request["message_hex"])
        if (not raw.startswith(b"hepta.learning-ledger.signed-evidence.v1")
                or b"lab.mac.only.generator" not in raw):
            raise ValueError("NATIVE_SIGNATURE_NOT_GENERATOR")
        mac_roundtrip_start = time.perf_counter_ns()
        signed=exchange(generator,{"kind":"SIGN","seq":0,"body":raw.hex()})
        mac_roundtrip_ms = (time.perf_counter_ns()-mac_roundtrip_start)/1e6
        if mac_roundtrip_ms > 2000:
            raise RuntimeError("MAC_TEST_GENERATOR_TRANSPORT_TOO_SLOW")
        if (signed.get("kind")!="SIGNED"
                or signed["seq"]!=0
                or signed["body_sha"]!=hashlib.sha256(raw).hexdigest()):
            raise ValueError("PHYSICAL_MAC_NATIVE_SIGNER_REFUSED")
        key.verify(bytes.fromhex(signed["sig"]),raw)
        # Distinct Mac/Pocket4 wall clocks are NOT an authoritative
        # cross-host happens-before relation. Native Pocket4 alone enforces
        # its own signed-request/verified-response/durable-ACK cutoff.
        if type(signed.get("signed_at_ms")) is not int or signed["signed_at_ms"]<=0:
            raise ValueError("MAC_REPORTED_WALLCLOCK_MALFORMED")
        transcript["mac_reported_wallclock_ms_untrusted"]=signed["signed_at_ms"]
        transcript["mac_test_transport_monotonic_elapsed_ms"]=round(mac_roundtrip_ms,3)
        transcript["cross_host_wallclock_order_not_used_for_native_admission"]=True
        native.stdin.write(json.dumps({
            "kind":"SIGNED","signature_hex":signed["sig"],
            "signed_at_ms":signed["signed_at_ms"]})+"\n")
        native.stdin.flush()
        ack=native_read_line(native.stdout,"NATIVE_RESULT",4)
        transcript["native_result"]=ack
        if (ack.get("status")!="TEST_ONLY_REAL_SOURCE_NATIVE_ACK_BEFORE_ORIGINAL_EXPIRY"
                or ack["witness_seq"]!=1 or ack["product_authority"] is not False
                or ack["orders_sent"]!=0):
            raise ValueError("NATIVE_WRITER_DID_NOT_COMPLETE_VERIFIED_ACK")
        native.stdin.close()
        result=native.wait(timeout=5)
        if result: raise RuntimeError("NATIVE_PROCESS_NONZERO_AFTER_ACK")
        post=verified_ssh(source_host,
            "cat "+shlex.quote(str(native_root/"native-prewarm-public-ack.json")),timeout=5)
        public_ack=json.loads(post.stdout)
        if (public_ack["native_witness_ack_at_ms"]!=ack["native_ack_at_ms"]
                or public_ack["original_quote_receipt"]!=packet["original_quote_receipt"]
                or public_ack["native_witness_sequence"]!=1
                or public_ack["broker_orders_sent"]!=0):
            raise ValueError("NATIVE_LEDGER_PUBLIC_RECEIPT_CHANGED")
        transcript["native_original_public_ack"]=public_ack
        transcript["post_ack_native_build_identity"] = verify_pinned_native_identity(
            source_host, native_binary, native_source,
            expected_native_binary_sha256, expected_native_source_sha256)
        transcript["status"]="TEST_ONLY_REAL_SOURCE_NATIVE_ACK_BEFORE_ORIGINAL_EXPIRY"
    except Exception as exc:
        transcript["status"]="HOLD_NATIVE_PREWARM_SOURCE_OR_ACK_UNAVAILABLE"
        transcript["error_type"]=type(exc).__name__
        transcript["error"]=str(exc)[:250]
    finally:
        if native:
            if native.poll() is None:
                native.kill()
            try: native.wait(timeout=5)
            except Exception: pass
            # Debug ONLY. Native stderr is never a signature or receipt.
            try:
                raw_stderr = native.stderr.read(4096)
                if raw_stderr:
                    transcript["native_stderr_diagnostic"] = raw_stderr[-2048:]
            except Exception:
                transcript["native_stderr_unavailable"] = True
        if generator:
            try:close(generator)
            except Exception as exc:
                transcript["test_generator_cleanup_status"]=type(exc).__name__
    transcript.update({
        "schema":SCHEMA,
        "test_root_is_process_ephemeral_not_product":True,
        "mac_generator_memory_test_only":True,
        "original_opaque_llm_policy_reused":False,
        "model_prediction":False,
        "broker_prepared":False,
        "canonical_product_owner":False,
        "automatic_model_update":False,
        "economic_reward":False,
        "broker_orders_sent":0,
        "broker_queries_sent":0,
    })
    return transcript


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mac",default="mac-ts")
    p.add_argument("--source-host",default="pocket4")
    for name in ("repo","source-root","registry-root","period",
                 "native-binary","native-root","output"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--expected-native-binary-sha256", required=True)
    p.add_argument("--expected-native-source-sha256", required=True)
    a=p.parse_args()
    os.umask(0o077)
    report=run(a.mac,a.source_host,a.repo,a.source_root,a.registry_root,
               a.period,a.native_binary,a.native_root,
               a.expected_native_binary_sha256,a.expected_native_source_sha256)
    if a.output.exists() or a.output.is_symlink():
        raise ValueError("EXACT_ONCE_RESULT_FILE_REQUIRED")
    a.output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    raw=(json.dumps(report,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    parent=os.open(a.output.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(parent)
    finally:os.close(parent)
    print(json.dumps({"status":report["status"],"source_new":bool(
        report.get("source_result",{}).get("research_source_success")),
        "native_witness_acked":bool(report.get("native_result",{}).get("witness_seq")==1),
        "broker_orders_sent":0,
        "sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True))
    if report["status"].startswith("HOLD"):raise SystemExit(2)

if __name__=="__main__":
    main()
