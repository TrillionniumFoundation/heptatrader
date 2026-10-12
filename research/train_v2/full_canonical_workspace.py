"""Strict full-workspace original Canonical Rust acceptance on Pocket4.

No source patch, root keys, broker network or trading. Uses 193-member pinned
workspace; never substitutes narrowed Cargo metadata or synthetic success.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

PIN = "78fdb0cf8537e3a84fc6e0a849707559c80881e8"
LOCK = "8354b4cbdf14389db7aa5508e2ed5b524fc2d4c4ea0ecb475955c933f8bea389"
CRATES = {
 "codex-hepta-learning-artifacts": (92, 1, "unittests src/lib.rs"),
 "codex-hepta-learning-ledger": (118, 1, "unittests src/lib.rs"),
 "codex-hepta-agentd": (2, 1, "tests/terminal_cell_owner.rs"),
}
MAX_SECONDS = 900


class QualificationError(ValueError):
 pass


def digest(path):
 h=hashlib.sha256()
 with Path(path).open("rb") as f:
  for chunk in iter(lambda:f.read(1024*1024),b""):
   h.update(chunk)
 return h.hexdigest()


def git(root,*args):
 p=subprocess.run(["git","-C",str(root),*args],
   capture_output=True,text=True,timeout=30,check=False)
 if p.returncode:
  raise QualificationError("CANONICAL_PINNED_GIT_UNAVAILABLE")
 return p.stdout.strip()


def verify_source(root):
 root=Path(root)
 if root.is_symlink() or not root.is_dir():
  raise QualificationError("CANONICAL_WORKTREE_MISSING_OR_SYMLINK")
 if git(root,"rev-parse","HEAD")!=PIN:
  raise QualificationError("CANONICAL_COMMIT_DRIFT")
 if git(root,"status","--porcelain=v1"):
  raise QualificationError("CANONICAL_WORKTREE_DIRTY")
 lock=root/"codex-rs/Cargo.lock"
 if lock.is_symlink() or digest(lock)!=LOCK:
  raise QualificationError("CANONICAL_CARGO_LOCK_CHANGED")
 tracked=git(root,"ls-files").splitlines()
 if len(tracked)!=8665:
  raise QualificationError("CANONICAL_TRACKED_TREE_COUNT_CHANGED")
 return {"commit":PIN,"tree":git(root,"rev-parse","HEAD^{tree}"),
         "tracked_files":len(tracked),"cargo_lock_sha256":LOCK}


def resolve_full_graph(manifest):
 cmd=["cargo","metadata","--offline","--locked","--format-version","1",
      "--manifest-path",str(manifest)]
 p=subprocess.run(cmd,capture_output=True,timeout=120,check=False)
 if p.returncode:
  raise QualificationError("CANONICAL_FULL_WORKSPACE_DEPENDENCY_GRAPH_UNAVAILABLE")
 try:
  meta=json.loads(p.stdout)
 except (TypeError,ValueError) as exc:
  raise QualificationError("CANONICAL_METADATA_INVALID") from exc
 members=meta.get("workspace_members",[])
 packages=meta.get("packages",[])
 if len(members)!=193 or len(set(members))!=193 or len(packages)<193:
  raise QualificationError("CANONICAL_FULL_WORKSPACE_NOT_RESOLVED")
 # Metadata counts by themselves are insufficient: every individual
 # workspace package ID must actually exist in the fully resolved graph.
 listed={p.get("id") for p in packages if isinstance(p,dict)}
 if not set(members).issubset(listed):
  raise QualificationError("CANONICAL_MEMBER_MISSING_FROM_RESOLVED_GRAPH")
 names={p.get("name") for p in packages if isinstance(p,dict)}
 if not set(CRATES).issubset(names):
  raise QualificationError("CANONICAL_NATIVE_PACKAGE_MISSING")
 return {"workspace_members":len(members),"resolved_packages":len(packages),
         "metadata_sha256":hashlib.sha256(p.stdout).hexdigest(),
         "offline_locked_full_dependency_graph":True}


def parse_result(output,crate):
 expected,min_ignored,marker=CRATES[crate]
 if not isinstance(output,str) or marker not in output:
  raise QualificationError("CANONICAL_TEST_RUNNER_IDENTITY_MISSING:"+crate)
 matches=re.findall(
  r"(?m)^test result: ok\.\s+(\d+) passed; (\d+) failed; "
  r"(\d+) ignored; (\d+) measured; (\d+) filtered out;",output)
 if len(matches)!=1:
  raise QualificationError("CANONICAL_UNIQUE_TEST_SUMMARY_REQUIRED:"+crate)
 passed,failed,ignored,measured,filtered=map(int,matches[0])
 if (passed<expected or failed!=0 or ignored!=min_ignored
     or measured!=0 or filtered!=0):
  raise QualificationError("CANONICAL_TEST_GATES_NOT_MET:"+crate)
 return {"passed":passed,"failed":failed,"ignored":ignored,
         "measured":measured,"filtered":filtered}


def run_case(manifest,target,root,crate,log_dir):
 out=log_dir/(crate+".log")
 if out.is_symlink() or out.exists():
  raise QualificationError("CANONICAL_TEST_LOG_ALREADY_EXISTS")
 fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 cmd=["cargo","test","--locked","--offline","--manifest-path",str(manifest),
      "-p",crate]
 if crate=="codex-hepta-agentd":
  cmd+=["--test","terminal_cell_owner"]
 else:
  cmd+=["--lib"]
 env=dict(os.environ,CARGO_TARGET_DIR=str(target),CARGO_BUILD_JOBS="2")
 start=time.monotonic()
 with os.fdopen(fd,"wb") as f:
  try:
   result=subprocess.run(cmd,env=env,cwd=root,stdout=f,
                         stderr=subprocess.STDOUT,timeout=MAX_SECONDS,
                         check=False)
  finally:
   f.flush();os.fsync(f.fileno())
 if result.returncode:
  raise QualificationError(
   "CANONICAL_NATIVE_TEST_FAILED_"+crate+":"+str(result.returncode))
 text=out.read_text(errors="replace")
 summary=parse_result(text,crate)
 return {"package":crate,"command":cmd,"elapsed_seconds":round(time.monotonic()-start,3),
         "raw_test_log_sha256":digest(out),"test_result":summary}


def write_once(directory,name,obj):
 out=directory/name
 if out.is_symlink() or out.exists():
  raise QualificationError("CANONICAL_IMMUTABLE_RECEIPT_EXISTS")
 raw=(json.dumps(obj,sort_keys=True,indent=2)+"\n").encode()
 fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:
  f.write(raw);f.flush();os.fsync(f.fileno())
 parentfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(parentfd)
 finally:os.close(parentfd)
 return digest(out)

def qualify(source,out,target):
 root=Path(source)
 out=Path(out)
 target=Path(target)
 if (out.exists() or out.is_symlink() or
     not out.parent.exists() or out.parent.is_symlink() or
     out.parent.stat().st_uid != os.geteuid() or
     out.parent.stat().st_mode & 0o077):
  raise QualificationError("CANONICAL_OUTPUT_ROOT_NOT_FRESH_OR_PRIVATE")
 if (not target.is_absolute() or target.is_symlink() or
     not target.exists() or target.stat().st_uid!=os.geteuid()):
  raise QualificationError("CANONICAL_TARGET_NOT_AUTHORIZED")
 source_state=verify_source(root)
 manifest=root/"codex-rs/Cargo.toml"
 full=resolve_full_graph(manifest)
 out.mkdir(mode=0o700)
 os.chmod(out,0o700)
 results=[]
 for crate in CRATES:
  results.append(run_case(manifest,target,root,crate,out))
 verify_source(root)
 passed=sum(entry["test_result"]["passed"] for entry in results)
 ignored=sum(entry["test_result"]["ignored"] for entry in results)
 body={
  "schema":"heptatrader.train-v2-full-original-canonical-workspace.v1",
  "observed_at_ms":time.time_ns()//1_000_000,
  "actual_pinned_git":source_state,
  "qualifier_source_sha256":digest(Path(__file__)),
  "qualifier_adversarial_tests_sha256":digest(
   Path(__file__).resolve().parents[2] /
   "tests/python/test_train_v2_full_canonical.py"),
  "actual_offline_locked_dependency_resolution":full,
  "original_package_test_evidence":results,
  "total_native_passed":passed,
  "total_native_ignored":ignored,
  "full_193_workspace_dependency_resolution_attested":True,
  "original_ledger_and_artifacts_native_tests_passed":True,
  "agentd_terminal_cell_owner_integration_passed":True,
  "entire_agentd_product_test_suite_passed":False,
  "all_193_workspace_crates_built_and_tested":False,
  "product_root_signer_enrolled":False,
  "physical_power_loss_qualified":False,
  "llm_trade_action_propensity_known":False,
  "actual_trade_request_prepared_bound":False,
  "original_broker_economic_alpha_proven":False,
  "signed_production_current_owner":False,
  "production_activation_authorized":False,
  "model_trained_or_promoted":False,
  "broker_requests_sent":0,
  "broker_orders_sent":0,
  "trading_authority":False,
 }
 sha=write_once(out,"full-canonical-native-evidence.json",body)
 return {"status":"FULL_ORIGINAL_CANONICAL_TEST_TARGETS_PASS_PRODUCT_HOLD",
         "native_passed":passed,"native_ignored":ignored,
         "full_members":full["workspace_members"],
         "original_git_commit":source_state["commit"],
         "receipt_sha256":sha,"native_prod_enrolled":False,
         "orders_sent":0}


def main():
 p=argparse.ArgumentParser()
 for key in ("source","output","target"):
  p.add_argument("--"+key,required=True,type=Path)
 a=p.parse_args()
 os.umask(0o077)
 result=qualify(a.source,a.output,a.target)
 print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
 main()
