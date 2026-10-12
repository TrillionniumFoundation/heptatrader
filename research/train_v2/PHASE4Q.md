# Train V2 Phase 4Q — actual X230 + Mac original-fullbyte ESCROW custody, TEST ONLY

2026-10-12. Isolated heptatrader research branch based on Phase 4P b60f68a.
No enabled training or trading service, production key, signed CURRENT artifact,
old forward plan, broker order, native Ledger or Witness was changed.

## Baseline defect and new capability

Phase 4O's actual original-market escrow stored only a digest of the
typed native decision/outcome SignedLearningEvidence and detached signature.
Phase 4P added a bounded test-only complete original native signed-byte
archive but proved it only with local synthetic fixtures, not remote storage.

Phase 4Q establishes the missing *physical transport and recoverable bytes*
piece in a NEW test-only event using two independent physical SSH endpoints.
It does not backdate source-time Escrow to earlier Phase4L/4O events.

The source operator generated new synthetic Ed25519 native-format messages
in process memory. No original broker account, trade instruction, fill,
open position, private real-market model data, or key was shipped.

X230 (existing trusted SSH alias x230-ts; host qian-ThinkPad-X230)
received the TEST decision/generator bytes. Mac (existing trusted SSH
alias mac-ts; host MacBook-Pro.local) received the TEST outcome/observer
bytes. Aliases used normal strict OpenSSH host-key checking. A different
mac-lan alias had a host-key mismatch and was NOT used or bypassed.

On each host a one-shot Python program was streamed to memory over SSH;
there is NO installed service or new remote signing key file. The host
verified the incoming full canonical JSON, original source intent,
native-shaped SignedLearningEvidenceV1 binary field offsets and role
tag, SHA256 of original typed payload, complete signed message and
detached actor signature, source-time / witness sequence bounds and
Ed25519 under the provided TEST-only public key. The public key is
self-provided by this operator test; it is NOT product root trust.

It saved one new exact original byte file and an independently signed
remote receipt under the real host's PRIVATE 0700 local state directory
with O_EXCL/O_NOFOLLOW and 0600 regular files. Both files were fsynced
and the containing directory was fsynced BEFORE ACK. The receipt key
was freshly generated IN MEMORY on that host and was not persisted.

Another independent SSH process then read the COMPLETE original and
receipt bytes and cryptographically checked them on the source host.
A dropped-ACK equivalent second identical request returned the
original unchanged receipt: no original overwrite, second source event
or new signature. A third fresh SSH readback demonstrated identity
after that retry. Both physical hosts used different receipt keys.

## Failure discovered and closed

The first focused test identified that the initial receiver rejected an
identical already archived request after the original test signature
expired, even though exact read-only recovery remained valid. Phase4Q
now differentiates:

- EXISTING exact immutable archive: reread/reverify the archived signed
  bytes and original receipt at their original timestamp, even later.
  This never creates a new permission, current signature or Ledger event.
- FIRST new archive: require a fresh original test source-time window and
  unexpired native evidence BEFORE O_EXCL creation.
- Incomplete, tampered, changed, symlinked or publicly readable archives:
  fail closed. A crash between original write and receipt persistence
  remains UNKNOWN/REFUSED; no invented ACK or automatic overwriting.

No caller has a native or product RecoveryGrant. For older events that
never had this archive, retroactive fullbyte restoration remains impossible.

## Actual two-host physical test evidence

Final, post-fix source-origin private receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-physical-fullbytes-20261012/physical-two-host-final.json

Exact SHA256:
d5ff6e14a8fcb70f80f95641942f1543be8881a3977c6ef2a752397293908fd0

Final original request SHA256:
- X230: e3691fe860fba26a8baee73036cd4559764a27537b46b20da4dffa2b81464888
- Mac: ec8922933fbc1de20e071da2bfc1e4850bc33e64aadbf7f6c0b6731badbf6702

Physical remote directory on each host:
~/.local/state/heptatrader-train-v2-phase4q-20261012/

Each remote has exactly four original/receipt JSON files across the
two independent positive rounds, no product key enrollment. Recovered
complete original bytes and remote ACKs can be checked without remote
private signing keys. This is a TEST-ONLY source; it is not a hardware
attestation, independent enterprise custody, fsync power-loss test or
signed native product truth.

## Test coverage and remaining gates

Focused synthetic hostile cases include original/receipt tampering,
bad actor signatures, wrong role, noncanonical data, bool instead of
witness integer, missing source identity, filesystem permissions,
symlink/root traversal, expired new request versus expired exact
readback, duplicate source submission, and host substitution.

Physical test only proves:
  two real hosts + actual fsync + signed receipt + separate SSH exact
  readback + idempotent equal retries.

It does NOT prove:
  complete native Rust LedgerWriter append on this new event,
  true original IB prediction before PREPARED, actual original LLM
  behavior-policy propensity, authorized root RecoveryAuthority,
  Canonical DatasetSnapshotReceiptV3, signed CURRENT/Artifact Owner,
  true external administrative independence, physical power-loss
  survival, complete actual commissions/FX account PnL or unseen
  after-cost model efficacy.

No private signing keys of a product identity were created, sent or
reused. No model trained, promoted or selected. No extra broker request
or order. The existing old failed Phase4N/4O cases remain HOLD.

## Final standalone regression and physical wrong-signer challenge

The original branch's complete Python test population plus newly introduced
Phase4Q tests passed on the exact post-fix research source tree:

- Full Pytest core+historical: 1,829 passed, 21 preexisting skips,
  776 passing subtests, zero failures.
- Source/build/documentation: 106 passed, zero failures.
- Phase4Q receiver focused adversarial unittest: 15 passed.
- No new original Canonical Rust library test result is claimed for this
  Python-only transport patch. Earlier exact original Canonical tests remain
  distinct from the source-tree and physical host tests here.

A second REAL execution over each authorized physical host's pinned SSH
alias sent a newly generated TEST-ONLY request with the WRONG original
native actor role. X230 refused observer-as-generator; Mac refused
generator-as-observer, both with PHYSICAL_ORIGINAL_INTENT before archive.
Physical file counts remained unchanged (four test JSON files per host,
two fullbyte+receipt pairs); neither remote directory contains a persisted
private escrow key file. Both host roots were reverified private 0700.
No host fingerprint checking or account permission was weakened.

The immutable final two-host proof remains SHA256:
d5ff6e14a8fcb70f80f95641942f1543be8881a3977c6ef2a752397293908fd0

The remote operator controls both test host accounts; a hash and one-time
self-generated host key are NOT independent product-root custody or proof of
resistance to a privileged operator or unexpected physical power removal.


## Final create-only evidence seal

The final physical, isolated Python, test-only hostile input and source-lane
results were jointly captured in one new private create-only / fsynced
receipt (including exact SHA256 of receiver, orchestrator and focused tests):

~/.local/state/heptatrader/operator-evidence/train-v2-physical-fullbytes-20261012/phase4q-physical-final-audit.json

Receipt SHA256:
eda8521c1fe3e6955fdc85fba26ee5c338340299b865b698f01d6ade5b25ddcb

Final independently revalidated status:
PHYSICAL_TWO_HOST_SYNTHETIC_FULLBYTE_RETRIEVAL_PASSED_PRODUCT_ROOT_HOLD.

The earlier qian-qi test-only positive proof and the final positive proof
both remain archived separately. No existing proof was overwritten.


## Code-frozen third physical execution

After removing one trailing whitespace from receiver source for strict
git diff --check, the physical test was independently repeated with
the EXACT cleaned receiver code, a NEW pair of synthetic original
actor-signed messages and two new privately persisted remote archives.
The older signed messages and receipts were not modified or reissued.

The code-frozen physical proof is separately create-once saved at:
~/.local/state/heptatrader/operator-evidence/train-v2-physical-fullbytes-20261012/physical-two-host-codefinal.json

SHA256:
f23480f6cc7f993439c14bae41993919f161500cc6e10b32c87fea87f3278d5e

Each remote physical host now has six synthetic proof JSON files across
three independent runs (three complete-original and three signed-receipt
files); no private test or product signing key file exists remotely.
The earlier four-file check was accurate before the new third run.

No current Canonical LedgerWriter transaction was written; no old
Phase4O original-market complete byte deficiency was retroactively
fixed. Hardware power-loss durability and product-root independence
remain unqualified.

## Final code-identical physical and complete-regression receipt

The THIRD independent physical escrow (after the final source-only whitespace
cleanup) and an exact complete rerun on the final source tree produced:
- New physical complete-byte proof SHA256:
  f23480f6cc7f993439c14bae41993919f161500cc6e10b32c87fea87f3278d5e
- Full Python tests: 1,829 passed; 21 skipped; 776 subtests passed.
- Source/build/documentation tests: 106 passed.
- Focused Phase4Q tests: 15 passed.
- No additional native Rust library tests were claimed for this transport
  change (the previous Phase4O native baseline remains separate).

Exact source Python SHA256 and final test-log SHA256 are frozen in a new
private O_EXCL/file+directory-fsynced receipt on pocket4:

~/.local/state/heptatrader/operator-evidence/train-v2-physical-fullbytes-20261012/phase4q-codefinal-audit.json

Receipt SHA256:
9bc99c4e208c64cc113344555fbfa26618c1305d2be51eca04d5e9c701b30893

Admission remains TEST_ONLY_PHYSICAL_FULL_BYTES_RECOVERED_ON_TWO_HOSTS_PRODUCT_HOLD.
Neither the escrow receiver nor the source-end orchestrator is installed
as a background service. Prior failed windows and old source-time
incomplete escrow still cannot be rewritten as a successful native outcome.
