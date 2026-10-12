# Train V2 Phase 4P — full original signed-byte escrow contract

2026-10-12, isolated research-only branch based on Phase 4O ba7b5db.
No currently running model, Paper Execution, original shadow trial,
native LedgerWriter, independent X230/Mac escrow archive or witness
frontier was modified by this change.

## Actual blocker

The existing real crosshost native-lab dual escrow sealed SHA256
identities of original typed payload, SignedLearningEvidence message
and detached Ed25519 signature, but did not escrow the original
complete signed bytes. This is proven in the prior immutable Phase4O
actual dual-physical escrow qualification: digest-only recovery is
NOT possible without the original source bytes. A new escrow protocol
cannot retroactively attest that its bytes were present before the old
actual source-time LedgerWriter append.

## Scope of new implementation

full_native_escrow.py is a TEST-ONLY local bounded exact-binary
contract for FUTURE source-time escrow protocols. It binds original
intent stage/role and its three source SHA256 values to exact binary
typed payload, original signed evidence message and detached signature.
Ed25519 verification occurs against the archived original message and
the archived signer public key. It also requires that the final native
signed-evidence payload_digest matches the SHA256 of the archived typed
original payload. A valid signature over another payload is rejected.

For a complete test-only request, it writes exactly one private 0600
archive via O_EXCL, fsyncs file and containing directory, and reopens
using O_NOFOLLOW to validate exact canonical bytes, signature, digest,
source intent and stage. No private key is used by verification. No
automatic overwrite or same-ID reissue is allowed. It is NOT a remote
transport, independently owned escrow, trusted signing root,
RecoveryAuthority grant or native witness writer.

Private recovery material may contain original trading data; this
experimental code does not print its payload bytes and is not
deployed on a production host. The archived test document includes
no real broker account or model weights.

## Original cases remain HOLD

The actual Phase4O report says:
- Test-only escrow stored source hashes, not original full signed bytes.
- Therefore complete original native evidence is not independently
  reconstructable from that escrow.
- Neither product-root RecoveryAuthority nor authorized ACK-only
  recovery exists; any previously unwitnessed record still requires
  independent root grant and original source-time proof.
- Independent product behavior propensities, complete PnL and
  Artifact Owner CURRENT remain absent.

The new format cannot repair old signed bytes or backdate them.
It cannot grant a native ACK, replay a new Outcome or promote any model.
Only genuinely new future experiments can incorporate the complete
preappend archive, verified on separately administered physical hosts.


## Exact isolated regression and native workspace limitation

On pocket4, the new complete-byte escrow test suite passed 13 tests.
Full HeptaTrader Python tests/python and historical research passed:
1814 tests, 21 skipped, 776 subtests; no failure.

An attempted new locked offline Cargo test for the original Canonical
hepta-learning-ledger plus hepta-learning-artifacts failed at WORKSPACE
MANIFEST PARSING with exit code 101 because the locally truncated
hepta-private-ci@78fdb0c workspace references missing aws-auth/Cargo.toml.
No Rust test compiled or ran in that attempted invocation. The earlier
Phase4O independently recorded 210 passed original Rust tests in its
dedicated complete isolated Cargo workspace; that earlier result is
preserved but NOT relabeled as a new Rust pass for Phase4P.

No true new remote physical X230/Mac preappend full-byte escrow was
attempted or claimed. The old Phase4O physical escrow source supports
only digest evidence, and no product RecoveryAuthority exists.
