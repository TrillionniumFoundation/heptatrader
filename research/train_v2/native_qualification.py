"""Run exact-pinned native Canonical Ledger + Artifact Owner tests OFFLINE.

This is an isolated research/qualification harness. It never imports a trader,
reads trading credentials, creates product trust, deploys units or sends orders.
The full product Host's actual LedgerWriter instance is NOT activated here.
"""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, re, shutil, subprocess, tomllib
PIN = "78fdb0cf8537e3a84fc6e0a849707559c80881e8"
CRATES = (
    "hepta-types", "hepta-intuition", "hepta-cognitive-types",
    "hepta-memory-retrieval", "hepta-learning-ledger",
    "hepta-learning-artifacts",
)
THIRD_PARTY = ("ed25519-dalek", "serde", "serde_json",
               "sha2", "pretty_assertions")

class QualificationError(RuntimeError):
    pass

def run(argv, timeout=20, **kw):
    try:
        return subprocess.run(argv,check=True,timeout=timeout,
                              capture_output=True,text=True,**kw)
    except (OSError,subprocess.TimeoutExpired,subprocess.CalledProcessError) as exc:
        raise QualificationError("COMMAND_REJECTED:"+str(argv[:3])) from exc

def blob_sha(raw):
    return hashlib.sha1(b"blob "+str(len(raw)).encode()+bytes([0])+raw).hexdigest()

def toml_value(v):
    if isinstance(v,str):return json.dumps(v)
    if isinstance(v,bool):return "true" if v else "false"
    if isinstance(v,list):return "["+",".join(toml_value(x) for x in v)+"]"
    if isinstance(v,dict):
        return "{"+", ".join(k+"="+toml_value(x) for k,x in v.items())+"}"
    return str(v)

def verify_native_snapshot(source):
    source=pathlib.Path(source)
    if source.is_symlink() or not source.is_dir():
        raise QualificationError("UNSAFE_NATIVE_SOURCE_DIRECTORY")
    if run(["git","-C",str(source),"rev-parse","HEAD"]).stdout.strip()!=PIN:
        raise QualificationError("WRONG_CANONICAL_SOURCE_COMMIT")
    tracked=[
        "codex-rs/Cargo.toml","codex-rs/Cargo.lock",
        *(f"codex-rs/{crate}" for crate in CRATES),
    ]
    # Ignore untracked files; they are never copied. Reject tracked changes.
    dirty=run(["git","-C",str(source),"status","--porcelain",
               "--untracked-files=no","--",*tracked]).stdout.strip()
    if dirty:raise QualificationError("TRACKED_NATIVE_SOURCE_CHANGED")
    entries={}
    for item in tracked:
        rows=run(["git","-C",str(source),"ls-tree","-r",
                  "--full-tree","HEAD",item]).stdout.splitlines()
        if not rows:raise QualificationError("PINNED_TREE_MISSING:"+item)
        for line in rows:
            mode,kind,blob,rel=line.split(None,3)
            if mode not in ("100644","100755") or kind!="blob":
                raise QualificationError("NONREGULAR_PINNED_BLOB")
            path=source/rel
            if path.is_symlink() or not path.is_file() or path.stat().st_size>4*1024**2:
                raise QualificationError("UNSAFE_OR_OVERSIZED_PINNED_FILE")
            raw=path.read_bytes()
            if blob_sha(raw)!=blob:raise QualificationError("PINNED_BLOB_CHANGED:"+rel)
            entries[rel]={"git_blob_sha1":blob,"sha256":hashlib.sha256(raw).hexdigest(),
                          "size_bytes":len(raw),"mode":mode}
    return entries

def prepare(source,build_root,tracked):
    target=pathlib.Path(build_root)
    if target.exists() or target.is_symlink():
        raise QualificationError("FRESH_QUALIFICATION_OUTPUT_REQUIRED")
    target.mkdir(mode=0o700,parents=True)
    (target/"src").mkdir(mode=0o700)
    (target/"evidence").mkdir(mode=0o700)
    for rel,item in tracked.items():
        if rel in ("codex-rs/Cargo.toml","codex-rs/Cargo.lock"):continue
        dest=target/"src"/rel.removeprefix("codex-rs/")
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes((pathlib.Path(source)/rel).read_bytes())
        dest.chmod(0o755 if item["mode"]=="100755" else 0o644)
    upstream=tomllib.loads((pathlib.Path(source)/"codex-rs/Cargo.toml").read_text())
    deps=upstream["workspace"]["dependencies"]
    content=('[workspace]\nresolver="3"\nmembers='+toml_value(list(CRATES))
             +'\n[workspace.package]\nedition="2024"\n'
             +'license="Apache-2.0"\nversion="0.0.0"\n'
             +'[workspace.lints]\n[workspace.dependencies]\n'
             +''.join(k+" = "+toml_value(deps[k])+"\n" for k in THIRD_PARTY))
    (target/"src/Cargo.toml").write_text(content)
    shutil.copy2(pathlib.Path(source)/"codex-rs/Cargo.lock",
                 target/"src/Cargo.lock")
    return target

def validate_lock(target,upstream_source):
    root=tomllib.loads((pathlib.Path(upstream_source)/"codex-rs/Cargo.lock").read_text())
    subset=tomllib.loads((pathlib.Path(target)/"src/Cargo.lock").read_text())
    locked={(x["name"],x["version"]):x for x in root["package"]}
    for pkg in subset["package"]:
        if pkg["name"].startswith("codex-hepta"):continue
        reference=locked.get((pkg["name"],pkg["version"]))
        if not reference or pkg.get("checksum")!=reference.get("checksum"):
            raise QualificationError("DEPENDENCY_OUTSIDE_ORIGINAL_PINNED_CARGO_LOCK")
    return len(subset["package"])

def native_test(target, timeout, upstream_source):
    work=pathlib.Path(target)
    env=dict(os.environ,CARGO_BUILD_JOBS="2",CARGO_INCREMENTAL="0",
             CARGO_TARGET_DIR=str(work/"target"))
    base=["cargo","test","--offline","--manifest-path",
          str(work/"src/Cargo.toml")]
    # Prune the complete pinned monorepo lock while preserving each pinned dep.
    first=run(base+["-p","codex-hepta-learning-artifacts","--lib"],
              timeout=timeout,env=env)
    (work/"evidence"/"initial-resolve-and-owner.log").write_text(
        first.stdout+"\n"+first.stderr)
    lock_packages=validate_lock(work,upstream_source)
    # The second pass is STRICTLY --locked and offline on both original crates.
    command=base[:2]+["--locked"]+base[2:]+[
        "-p","codex-hepta-learning-ledger",
        "-p","codex-hepta-learning-artifacts","--lib"]
    completed=run(command,timeout=timeout,env=env)
    raw=completed.stdout+"\n"+completed.stderr
    (work/"evidence"/"native-both-locked.log").write_text(raw)
    counts=[tuple(map(int,v)) for v in
            re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;",raw)]
    if len(counts)!=2 or sorted(counts)!=[(92,0,1),(118,0,1)]:
        raise QualificationError("ACTUAL_NATIVE_LEDGER_OWNER_TEST_COUNTS_CHANGED")
    return counts,lock_packages,hashlib.sha256(raw.encode()).hexdigest()

def main():
    import argparse,datetime
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-checkout",type=pathlib.Path,required=True)
    parser.add_argument("--output",type=pathlib.Path,required=True)
    parser.add_argument("--timeout-seconds",type=int,default=240)
    args=parser.parse_args()
    if not 30<=args.timeout_seconds<=600:
        raise QualificationError("NATIVE_TEST_TIMEOUT_BOUND")
    os.umask(0o077)
    tracked=verify_native_snapshot(args.native_checkout)
    root=prepare(args.native_checkout,args.output,tracked)
    counts,packages,logsha=native_test(root,args.timeout_seconds,
                                       args.native_checkout)
    receipt={
        "schema":"heptatrader.train-v2-native-canonical-library-qualification.v1",
        "observed_at_utc":datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "source_commit":PIN,
        "original_native_tracked_git_blobs":len(tracked),
        "exact_original_git_blob_verification":True,
        "source_files":tracked,
        "isolated_six_crate_workspace":list(CRATES),
        "generated_workspace_manifest_only":True,
        "third_party_dependency_versions_and_checksums_match_original_lock":True,
        "effective_lock_package_count":packages,
        "upstream_lock_sha256":hashlib.sha256(
            (args.native_checkout/"codex-rs/Cargo.lock").read_bytes()).hexdigest(),
        "effective_narrowed_lock_sha256":hashlib.sha256(
            (root/"src/Cargo.lock").read_bytes()).hexdigest(),
        "native_rust_library_tests":[
            {"passed":p,"failed":f,"ignored":i} for p,f,i in counts],
        "passed_total":sum(x[0] for x in counts),
        "failed_total":sum(x[1] for x in counts),
        "ignored_total":sum(x[2] for x in counts),
        "actual_native_test_log_sha256":logsha,
        "cargo_version":run(["cargo","--version"]).stdout.strip(),
        "rustc_version":run(["rustc","--version"]).stdout.strip(),
        "native_library_real_rust_tests_executed":True,
        "real_product_host_trust_enrolled":False,
        "real_generator_or_observer_production_keys_used":False,
        "production_ledger_writer_instance_created":False,
        "training_model_inferred_or_updated":False,
        "artifact_owner_production_current_selected":False,
        "automatic_training":False,
        "trade_authority":False,
        "broker_queries_sent":0,
        "broker_orders_sent":0,
    }
    dest=root/"evidence/native-qualification.json"
    if dest.exists():
        raise QualificationError("NATIVE_RECEIPT_IMMUTABLE_NO_OVERWRITE")
    body=(json.dumps(receipt,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as file:
        file.write(body)
        file.flush()
        os.fsync(file.fileno())
    directory=os.open(dest.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)
    print(json.dumps({
        "source_commit":PIN,
        "exact_git_blobs":len(tracked),
        "third_party_dependency_lock_matched":True,
        "passed":receipt["passed_total"],
        "failed":receipt["failed_total"],
        "ignored":receipt["ignored_total"],
        "native_library_tests_executed":True,
        "production_owner_enrolled":False,
        "trading_orders_sent":0,
        "receipt_sha256":hashlib.sha256(body).hexdigest()},sort_keys=True))
if __name__=="__main__":
    main()
