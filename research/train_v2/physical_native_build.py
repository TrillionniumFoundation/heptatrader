"""Build TEST-ONLY original SignedLearningEvidenceV1 reparser.

Copies unmodified crates from one exact pinned Canonical Git commit into a
minimal isolated Cargo workspace; original source is never changed. This is
not a rebuilt complete product Agentd or a production LedgerWriter.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tomllib

PIN="78fdb0cf8537e3a84fc6e0a849707559c80881e8"
CRATES=("hepta-types","hepta-cognitive-types","hepta-intuition",
        "hepta-memory-retrieval","hepta-learning-ledger")
DEPENDENCIES=("sha2","serde","serde_json","ed25519-dalek","pretty_assertions")

def tomlval(x):
    if isinstance(x,bool):return "true" if x else "false"
    if isinstance(x,str):return json.dumps(x)
    if isinstance(x,list):return "["+", ".join(tomlval(i) for i in x)+"]"
    if isinstance(x,dict):return "{"+", ".join(k+" = "+tomlval(v) for k,v in x.items())+"}"
    raise ValueError("CARGO_UNSUPPORTED_WORKSPACE_VALUE")

def sha(data):return hashlib.sha256(data).hexdigest()
def git(repo,*args):
    return subprocess.check_output(
       ["git","-C",str(repo),*args],text=True,timeout=8).strip()

def build(source,bridge_src,destination):
    source,bridge_src,destination=map(Path,(source,bridge_src,destination))
    if source.is_symlink() or bridge_src.is_symlink() or destination.exists():
        raise ValueError("CANONICAL_SOURCE_OR_BUILD_DESTINATION_INVALID")
    if git(source,"rev-parse","HEAD")!=PIN:
        raise ValueError("CANONICAL_SOURCE_COMMIT_MISMATCH")
    for name in CRATES:
        rel="codex-rs/"+name
        subprocess.run(["git","-C",str(source),"diff","--exit-code",
                        "HEAD","--",rel],check=True,stdout=subprocess.DEVNULL,
                       timeout=8)
        if not (source/rel/"Cargo.toml").is_file():
            raise ValueError("CANONICAL_REQUIRED_CRATE_MISSING:"+name)
    if not bridge_src.is_file() or bridge_src.stat().st_size>65536:
        raise ValueError("RESEARCH_NATIVE_REPARSER_SOURCE_UNAVAILABLE")
    parent=destination.parent
    if parent.is_symlink() or parent.stat().st_uid!=os.geteuid() or parent.stat().st_mode&0o077:
        raise ValueError("NATIVE_BUILD_PARENT_NOT_PRIVATE")
    destination.mkdir(mode=0o700)
    files={}
    for name in CRATES:
        origin=source/"codex-rs"/name
        copy=destination/name
        shutil.copytree(origin,copy,symlinks=False)
        for src in origin.rglob("*"):
            if not src.is_file():continue
            rel=src.relative_to(origin)
            target=copy/rel
            if sha(src.read_bytes())!=sha(target.read_bytes()):
                raise RuntimeError("CANONICAL_COPIED_SOURCE_BYTES_CHANGED")
            if src.suffix in (".rs",".toml"):
                files[name+"/"+str(rel)]=sha(src.read_bytes())
    workspace=tomllib.loads((source/"codex-rs/Cargo.toml").read_text())
    deps=workspace["workspace"]["dependencies"]
    root='[workspace]\nresolver="3"\nmembers='+tomlval([*CRATES,"bridge"])+'\n'
    root+='[workspace.package]\nedition="2024"\nlicense="Apache-2.0"\nversion="0.0.0"\n'
    root+='[workspace.lints]\n[workspace.dependencies]\n'
    for name in DEPENDENCIES:
        root+=name+' = '+tomlval(deps[name])+'\n'
    (destination/"Cargo.toml").write_text(root)
    bridge=destination/"bridge";(bridge/"src").mkdir(parents=True)
    shutil.copyfile(bridge_src,bridge/"src/main.rs")
    (bridge/"Cargo.toml").write_text("""[package]
name="heptatrader-phase4r-native-bytecheck"
version="0.1.0"
edition="2024"
[dependencies]
codex-hepta-learning-ledger={path="../hepta-learning-ledger"}
codex-hepta-types={path="../hepta-types"}
ed25519-dalek={workspace=true}
serde_json={workspace=true}
""")
    env={**os.environ,"CARGO_BUILD_JOBS":"2"}
    prefix=["--manifest-path",str(destination/"Cargo.toml")]
    def invoke(args):
        done=subprocess.run(["cargo",*args],env=env,check=False,
                          capture_output=True,text=True,timeout=180)
        if done.returncode:
            print(done.stderr[-4500:],flush=True)
            raise RuntimeError("CANONICAL_NATIVE_ISOLATED_BUILD_FAILED:"+str(done.returncode))
    invoke(["generate-lockfile","--offline",*prefix])
    invoke(["build","--offline","--locked",*prefix,"-p","heptatrader-phase4r-native-bytecheck"])
    binary=destination/"target/debug/heptatrader-phase4r-native-bytecheck"
    if not binary.is_file():raise RuntimeError("NATIVE_BIN_NOT_PRODUCED")
    receipt={
       "schema":"heptatrader.train-v2-native-rust-roundtrip-build.v1",
       "canonical_source_commit":PIN,
       "original_crate_file_sha256":files,
       "original_source_unmodified":True,
       "narrowed_cargo_workspace":True,
       "full_agentd_workspace_compiled":False,
       "canonical_ledger_writer_called":False,
       "native_evidence_type": "SignedLearningEvidenceV1",
       "native_bridge_binary_sha256":sha(binary.read_bytes()),
       "bridge_source_sha256":sha(bridge_src.read_bytes()),
       "isolated_lockfile_sha256":sha((destination/"Cargo.lock").read_bytes()),
       "test_only":True,"trade_authority":False,"broker_orders_sent":0,
    }
    path=destination/"native-build-receipt.json"
    os.umask(0o077)
    output=(json.dumps(receipt,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(output);stream.flush();os.fsync(stream.fileno())
    dfd=os.open(destination,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dfd)
    finally:os.close(dfd)
    print(json.dumps({k:v for k,v in receipt.items()
                      if k!="original_crate_file_sha256"},sort_keys=True))
    return receipt

def main():
    p=argparse.ArgumentParser()
    for name in ("source","bridge-src","destination"):
        p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args()
    build(a.source,a.bridge_src,a.destination)

if __name__=="__main__":main()
