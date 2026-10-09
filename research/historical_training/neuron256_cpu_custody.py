"""Canonical CPU-only Neuron256 feature custody across independent hosts.

The CPU encoder runs in a separate no-network job on the approved source host.
A pinned Ed25519 key attests its exact inputs, binary tensors and CPU runtime.
The receiving GPU process does not run a second local encoder or trust mtimes.
This never establishes a future efficacy claim or trading authority.
"""
from __future__ import annotations
import base64,hashlib,json,math,time
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once,canonical

SCHEMA="heptatrader.neuron256-canonical-cpu-feature.v1"
BACKEND="2.11.0+cpu"
SOURCE="INDEPENDENT_FROZEN_CPU_QIANQI"
WIDTH=768

def public_key(value):
    if not isinstance(value,str) or len(value)!=64:
        raise EvidenceError("CANONICAL_CPU_PUBLIC_KEY")
    try:raw=bytes.fromhex(value)
    except (ValueError,TypeError) as exc:
        raise EvidenceError("CANONICAL_CPU_PUBLIC_KEY") from exc
    if len(raw)!=32:raise EvidenceError("CANONICAL_CPU_PUBLIC_KEY")
    return raw

def message_fields(record):
    return {k:v for k,v in record.items() if k!="signature"}

def signed_record(payload,key_file,expected_public_key):
    # Use the already-deployed role-witness helper in system /usr/bin/python3.
    # The research ROCm virtualenv deliberately has no cryptography installed.
    from laya_cell.financial_forward_witness import _run
    key_path=Path(key_file)
    if not key_path.is_file() or key_path.stat().st_mode & 0o077:
        raise EvidenceError("CANONICAL_CPU_KEY_PERMISSIONS")
    public_key(expected_public_key)
    sig=_run("sign",str(key_path),canonical(payload)).decode("ascii").strip()
    try:_run("verify",expected_public_key,canonical(payload),sig)
    except (ValueError,TypeError) as exc:
        raise EvidenceError("CANONICAL_CPU_SIGNER_SUBSTITUTION") from exc
    return {**payload,"signature":sig}

def verify_signature(record,expected_public_key):
    from laya_cell.financial_forward_witness import _run
    if "signature" not in record or type(record["signature"])is not str:
        raise EvidenceError("CANONICAL_CPU_SIGNATURE_MISSING")
    public_key(expected_public_key)
    try:
        _run("verify",expected_public_key,canonical(message_fields(record)),record["signature"])
    except (ValueError,TypeError) as exc:
        raise EvidenceError("CANONICAL_CPU_SIGNATURE_INVALID") from exc
    return record

def verify_feature(directory,plan,candidate,row,anchor,now_ms,*,source_input_file):
    import numpy as np
    from safetensors.numpy import load_file
    directory=Path(directory)
    receipt=read(directory/"receipt.json")
    required={"schema","source","plan_id","candidate_id","anchor","generated_at_ms",
              "source_input_sha256","source_row_digest","source_text_sha256",
              "encoder_model_sha256","encoder_tokenizer_sha256","encoder_runtime_sha256",
              "torch_version","cpu_only","raw_sha256","rows","width",
              "new_training_steps","broker_orders_sent","trade_authority","signature"}
    if set(receipt)!=required:
        raise EvidenceError("CANONICAL_CPU_RECEIPT_FIELDS")
    verify_signature(receipt,plan["cpu_public_key_hex"])
    from .matched_data import text
    if (receipt["schema"]!=SCHEMA or receipt["source"]!=SOURCE
        or receipt["plan_id"]!=plan["plan_id"]
        or receipt["candidate_id"]!=candidate["candidate_id"]
        or type(anchor)is not int or anchor not in plan["anchors"] or receipt["anchor"]!=anchor
        or receipt["torch_version"]!=BACKEND or receipt["cpu_only"] is not True
        or any(receipt[k]!=0 for k in ("new_training_steps","broker_orders_sent"))
        or receipt["trade_authority"] is not False or receipt["rows"]!=1 or receipt["width"]!=WIDTH):
        raise EvidenceError("CANONICAL_CPU_RECEIPT_SCOPE")
    when=receipt["generated_at_ms"]
    if type(when)is not int or not anchor*1000<=when<min(now_ms+1,(anchor+240)*1000):
        raise EvidenceError("CANONICAL_CPU_TOO_EARLY_OR_LATE")
    asset=candidate["encoder_asset"]
    if (receipt["encoder_model_sha256"]!=asset["model_file_sha256"]
        or receipt["encoder_tokenizer_sha256"]!=asset["tokenizer_file_sha256"]
        or receipt["encoder_runtime_sha256"]!=plan["cpu_emitter_sha256"]
        or receipt["source_input_sha256"]!=sha(source_input_file)
        or receipt["source_row_digest"]!=digest(row)
        or receipt["source_text_sha256"]!=hashlib.sha256(text(row).encode()).hexdigest()):
        raise EvidenceError("CANONICAL_CPU_INPUT_OR_ENV_CHANGED")
    tensor_path=directory/"raw.safetensors"
    if receipt["raw_sha256"]!=sha(tensor_path):
        raise EvidenceError("CANONICAL_CPU_RAW_CHANGED")
    vector=load_file(str(tensor_path)).get("features")
    if (not isinstance(vector,np.ndarray) or vector.dtype!=np.float32
        or vector.shape!=(1,WIDTH) or not np.isfinite(vector).all()
        or abs(float(np.linalg.norm(vector,axis=1)[0])-1)>1e-4):
        raise EvidenceError("CANONICAL_CPU_VECTOR_INVALID")
    return vector,receipt

def emit(config):
    """Separate offline source-host executable; never called by the GPU worker."""
    import os,sys,numpy as np
    from safetensors.numpy import save_file
    from .neuron256_frozen_readout import encode_cpu
    plan=read(config["plan_path"]);candidate=read(config["candidate_path"])
    if (config["anchor"] not in plan["anchors"] or config["anchor"]<0
        or plan["candidate_id"]!=candidate["candidate_id"]):
        raise EvidenceError("CANONICAL_CPU_EMIT_PLAN")
    if (sha(__file__)!=plan["cpu_emitter_sha256"]):
        raise EvidenceError("CANONICAL_CPU_EMITTER_CHANGED")
    anchor=config["anchor"];now=int(time.time()*1000)
    if not anchor*1000<=now<(anchor+240)*1000:
        raise EvidenceError("CANONICAL_CPU_INPUT_WINDOW")
    input_file=Path(config["source_input_file"]);row=read(input_file)
    if row.get("time")!=anchor:
        raise EvidenceError("CANONICAL_CPU_INPUT_ANCHOR")
    out=Path(config["output_directory"])
    if out.exists():raise EvidenceError("CANONICAL_CPU_OUTPUT_EXISTS")
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    from sandbox import enter
    enter({"output_dir":str(out),"period_path":str(input_file),
           "legacy_capture":str(input_file),"oms_path":str(input_file),
           "export_dir":str(input_file),
           "read_only_paths":[config["source_root"],config["base_directory"],
                config["plan_path"],config["candidate_path"],config["key_path"],sys.prefix]})
    import torch
    if (torch.__version__!=BACKEND or torch.version.hip is not None
        or torch.version.cuda is not None or torch.cuda.is_available()):
        raise EvidenceError("CANONICAL_CPU_RUNTIME_REQUIRED")
    raw,texts=encode_cpu(config["base_directory"],[row],candidate["encoder_asset"])
    if raw.shape!=(1,WIDTH) or raw.dtype!=np.float32:
        raise EvidenceError("CANONICAL_CPU_EMIT_SHAPE")
    tensor=out/"raw.safetensors";save_file({"features":raw},str(tensor));tensor.chmod(0o400)
    created=int(time.time()*1000)
    if created>=(anchor+240)*1000:raise EvidenceError("CANONICAL_CPU_EMIT_LATE")
    payload={"schema":SCHEMA,"source":SOURCE,"plan_id":plan["plan_id"],
             "candidate_id":candidate["candidate_id"],"anchor":anchor,
             "generated_at_ms":created,"source_input_sha256":sha(input_file),
             "source_row_digest":digest(row),
             "source_text_sha256":hashlib.sha256(texts[0].encode()).hexdigest(),
             "encoder_model_sha256":candidate["encoder_asset"]["model_file_sha256"],
             "encoder_tokenizer_sha256":candidate["encoder_asset"]["tokenizer_file_sha256"],
             "encoder_runtime_sha256":sha(__file__),"torch_version":BACKEND,
             "cpu_only":True,"raw_sha256":sha(tensor),"rows":1,"width":WIDTH,
             "new_training_steps":0,"broker_orders_sent":0,"trade_authority":False}
    signed=signed_record(payload,config["key_path"],plan["cpu_public_key_hex"])
    write_once(out/"receipt.json",signed)
    print(json.dumps({"anchor":anchor,"raw_sha256":sha(tensor),
        "receipt_sha256":sha(out/"receipt.json"),"cpu_version":BACKEND,
        "orders_sent":0}),flush=True)
    return signed

def main():
    import argparse,os
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True,type=Path)
    args=parser.parse_args();os.umask(0o077)
    emit(read(args.config))

if __name__=="__main__":main()
