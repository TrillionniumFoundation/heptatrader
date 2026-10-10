"""Read-only exact Rust API inspector; not a LedgerWriter implementation."""
from __future__ import annotations
import hashlib
from pathlib import Path
import re
import subprocess
from .contracts import ContractError, digest

PIN = "78fdb0cf8537e3a84fc6e0a849707559c80881e8"
SOURCE = {
 "decision": "codex-rs/hepta-learning-ledger/src/production.rs",
 "model": "codex-rs/hepta-learning-ledger/src/model.rs",
 "causal": "codex-rs/hepta-learning-ledger/src/causal_v2.rs",
 "signed": "codex-rs/hepta-learning-ledger/src/signed_evidence.rs",
 "trust": "codex-rs/hepta-learning-ledger/src/trust_distribution.rs",
 "witness": "codex-rs/hepta-learning-ledger/src/witness.rs",
 "dataset": "codex-rs/hepta-learning-ledger/src/dataset_receipt_v3.rs",
 "owner": "codex-rs/hepta-learning-artifacts/src/owner_host.rs",
}
FIELDS = {
 "ProductionDecisionV2": (
  "record_id","episode_id","run_snapshot_digest","objective_digest",
  "policy_digest","candidate_ids","selected_candidate_id",
  "selected_propensity","completeness","support_digest"),
 "CandidateSetCompletenessReceiptV1": (
  "set_id","state_digest","generator_id","generator_code_digest",
  "grammar_digest","hard_filter_digest","truncation_digest",
  "candidates_digest","candidate_count","omitted_count_bound",
  "canonical_order_digest","complete_for_generator"),
 "SignedLearningEvidenceV1": (
  "evidence_id","principal_id","role","trust_digest",
  "scope_digest","objective_digest","authority_epoch",
  "issued_at","expires_at","payload_digest","signature"),
}
WRITER_METHODS = ("append_decision","append_outcome","freeze_dataset",
                  "revalidate_dataset_snapshot","witness_frontier")

def rust_fields(src, name):
 marker = "pub struct "+name+" {"
 if src.count(marker)!=1:
  raise ContractError("CANONICAL_TYPE_LAYOUT_DRIFT:"+name)
 body = src.split(marker,1)[1].split("\n}",1)[0]
 return tuple(re.findall(r"(?m)^\s*pub\s+([a-z][a-z0-9_]*)\s*:",body))

def pinned_commit(repo):
 try:
  result=subprocess.run(["git","-C",str(repo),"rev-parse","HEAD"],
   check=True,capture_output=True,text=True,timeout=5)
 except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
  raise ContractError("CANONICAL_GIT_IDENTITY_MISSING") from exc
 if result.stdout.strip()!=PIN:
  raise ContractError("CANONICAL_SOURCE_COMMIT_DRIFT")

def verify_native_layout(repo):
 repo=Path(repo)
 if repo.is_symlink() or not repo.is_dir():
  raise ContractError("CANONICAL_REPO_UNSAFE")
 pinned_commit(repo)
 contents={}
 for label,relative in SOURCE.items():
  p=repo/relative
  if p.is_symlink() or not p.is_file() or p.stat().st_size>512000:
   raise ContractError("CANONICAL_NATIVE_SOURCE_MISSING:"+label)
  contents[label]=p.read_text()
 try:
  clean=subprocess.run(["git","-C",str(repo),"diff","--quiet","HEAD","--",
    *SOURCE.values()],timeout=5,check=False)
 except (OSError,subprocess.TimeoutExpired) as exc:
  raise ContractError("CANONICAL_NATIVE_DIRTY_UNKNOWN") from exc
 if clean.returncode!=0:
  raise ContractError("CANONICAL_NATIVE_TRACKED_SOURCE_DIRTY")
 layouts={
  "ProductionDecisionV2":rust_fields(contents["decision"],"ProductionDecisionV2"),
  "CandidateSetCompletenessReceiptV1":rust_fields(contents["causal"],
                                            "CandidateSetCompletenessReceiptV1"),
  "SignedLearningEvidenceV1":rust_fields(contents["signed"],"SignedLearningEvidenceV1")}
 for name,expected in FIELDS.items():
  if layouts[name]!=expected:
   raise ContractError("CANONICAL_NATIVE_FIELDS_CHANGED:"+name)
 for method in WRITER_METHODS:
  if contents["decision"].count("pub fn "+method+"(")!=1:
   raise ContractError("CANONICAL_NATIVE_LEDGER_METHOD_CHANGED:"+method)
 for label,marker in (
    ("dataset","pub struct DatasetSnapshotReceiptV3 {"),
    ("witness","pub struct LedgerWitnessStore"),
    ("owner","pub struct ArtifactOwnerVerifierV1")):
  if marker not in contents[label]:
   raise ContractError("CANONICAL_PRODUCT_API_NOT_PRESENT:"+label)
 dependencies=all((repo/"codex-rs"/x/"Cargo.toml").is_file()
  for x in ("hepta-types","hepta-learning-ledger",
            "hepta-intuition","hepta-memory-retrieval"))
 body={
  "schema":"heptatrader.train-v2-pinned-native-layout.v1",
  "exact_native_commit":PIN,
  "original_rust_fields":{name:list(rows) for name,rows in layouts.items()},
  "writer_methods":list(WRITER_METHODS),
  "native_source_sha256":{name:hashlib.sha256(txt.encode()).hexdigest()
                           for name,txt in sorted(contents.items())},
  "full_canonical_workspace_dependencies_present":dependencies,
  "native_ledger_writer_compiled":False,
  "native_ledger_writer_executed":False,
  "canonical_host_trust_enrolled":False,
  "trade_authority":False,
  "broker_orders_sent":0,
 }
 return {**body,"layout_id":digest(body)}
