# Phase 4O — test-only Escrow origin readback and ACK-only recovery HOLD

2026-10-12, Pocket4. Branch stacked on Phase4N Draft, no installed
trading/model/producer changes, broker requests, trading orders, new
product credentials or user-visible market profit claims.

## Actual host and source conditions

Pocket4, MacBook-Pro.local and qian-ThinkPad-X230 were reachable
by the already-authorized SSH paths. Their clocks agreed to second
precision. The original Phase4N Mac/X230 physical Escrow service and
its remote receipt directories did not exist. A remote service/key
installation was blocked by the authorized execution channel. No
alternate route was used. Nothing in this phase proves remote
signature custody, independently administered product keys or actual
remote fsync persistence.

Original source trees remain at full canonical commit
78fdb0cf8537e3a84fc6e0a849707559c80881e8 and train parent
94e05976270f072eb165c136621d99912dc3339e. The original
Phase4N local forward registration expired on October 12 at
10:29:26 China time WITHOUT a consumed shadow Decision, independent
external Escrow, native Writer event or new price Outcome. Do not
backdate or reuse that plan as a fresh trial.

## Source-level test-only origin readback

New escrow_receipt_readback.py requires the caller to supply EXACT
original typed preappend intent, a separately pinned TEST Ed25519
public key, returned ACK, and separately obtained original remote
file bytes. It verifies:

- Stage/role, prior witness count and next sequence are true integers,
  never bool; action is the new shadow policy, not original opaque LLM.
- Exact SHA256 typed native payload, detached actor signature message,
  source packet/quote and predecessor binding, with test-only flags.
- Bounded receipt bytes, canonical JSON, ACK digest and filename that
  includes the original intent digest; rejects path traversal and
  wrong role/host labels.
- Exact equality between ACK bytes and independent origin-host file
  bytes, plus signature/domain/pinned public key/time validation.
- Without origin file bytes the result is an explicit HOLD, not a
  production qualification. Identical caller-provided bytes are not
  independent transport or physical custody evidence.

The module itself is READ ONLY: no SSH, private key creation, broker
access, LedgerWriter append or product role. Its successful result is
TEST byte equality only. No physical power-cut survival, independent
admin or hardware attestation is claimed.

## Source-level lost-ACK idempotency hardening

The Phase4N test Escrow service formerly persisted the first
intent with O_EXCL/fsync but returned FileExistsError when a client
resent an identical request after losing the reply.

This branch corrects that candidate failure mode. For an existing
same original-intent digest path, it opens with O_NOFOLLOW, checks
owner, regular file, size and private 0600 mode, and rereads the
canonical original signed receipt. It verifies original role,
source-time stage, signed body, public key continuity, source hash
and Ed25519 signature, then returns EXACT archived bytes without
re-signing, rewriting or creating any new intent. Conflicting,
incomplete, symlinked, public, wrong-key or tampered archives are
rejected. A new intent after the source-time fence still fails.

For a previously successfully fsynced same intent, a late duplicate
can be read back after the original 180s new-signature window closes;
this is a duplicate old ACK, not NEW authorization. A test-only
signing private file remains required for this signer's continuity
interface; independent archived public evidence can be verified
separately after source key removal. No production key management is
inferred.

## Root-authorized ACK-only recovery is not yet implemented

The original Canonical native LedgerWitnessStore exposes a low-level
advance primitive and the original LedgerWriter handles one lagging
record, but these are storage mechanics, not a product
RecoveryAuthority role or independent RecoveryGrant.

A new native_ackonly_recovery_admission.py read-only guard replayed
the six Phase4M isolated filesystem copies of the prior REAL
EUR/USD shadow-policy native experiment. It verified the exact
archived original multi-host signed native bytes and the immutable
original Ledger and independent Witness:

case: acknowledged, expired, lag-exact, lag-torn
  native witness frontiers = 2; ALREADY ACKed; no append permitted.

case: lag-wrong, lag-expired
  native witness frontiers = 1; HOLD because the earlier actual
  original-market event had NO external preappend Escrow at that
  source time. No retroactive or newly minted certificate can
  pretend to predate the first ledger append.

No tool in Phase4O advanced any original or copied Witness. A
synthetic test making all local 'grant', 'revocation' and 'clock'
boolean claims true still fails release: no original Canonical
RecoveryAuthority role and no independent signed single-use grant
are enrolled. The plain Pocket4 wall clock is not hardware-attested.

A future reviewed product protocol needs a root-signed
RecoveryAuthority, independently retained original signed
preappend intent, specific previous witness anchor, same already
durable event and chain digest, current trusted time/revocation,
single-use recovery nonce, product owner fencing, concurrent writer
rejection and ACK-only rather than new event authorization.
Physical sudden power loss, duplicate grant and corruption tests
are still necessary.

## Tests and immutable evidence

Synthetic tests use local ephemeral memory-only keys and fabricated
test stage/host strings. They cannot prove a physical signer was
X230 or Mac. Source-level tests verify exact ACK replay after the
original window, no first late signature, altered archive, forged
origin readback, signature and type attacks. Earlier 33 Phase4N
escrow contract/adversarial tests also remain part of the complete
HeptaTrader Python core/historical regression.

The six actual prior original-market copied ledger/witness cases
were separately read-only reexamined and create-only receipts saved
in Pocket4 private research evidence:

~/.local/state/heptatrader/operator-evidence/train-v2-remote-escrow-readback-20261012/

Original production opaque LLM true action propensities, independent
root-controlled Generator/Observer/RecoveryAuthority, a product
LedgerWriter service, authentic after-cost fills/PnL,
market-eligible DatasetSnapshotV3, Artifact Owner/CURRENT, and
physical power-cut qualification all remain HOLD.

Keep this branch Draft. It does not introduce a new enabled
service, test/private key on Mac/X230 or production trading action.

## Final verified source, native regression and evidence seal

Reused exact original Canonical source commit:
78fdb0cf8537e3a84fc6e0a849707559c80881e8

New source changes were subjected to a complete HeptaTrader Python
core/historical regression (including Phase4N source, native
subprocess fixtures, new readback/idempotent and grant adversarial
tests). Final results: 1,801 passed; 21 skipped; 772 subtests
passed; zero failures. Log SHA256:

b19746713b69cbcaa5820a398b65b62f11ab2fca7266724f65d57b4c0b43961d

The original UNMODIFIED Canonical hepta-learning-ledger and
hepta-learning-artifacts library tests also ran via original
offline locked Cargo: 118 + 92 = 210 passed; zero failed;
two explicitly ignored. Original native library log SHA256:

ea5f7dfd34234462dedf2c414d7df57a148eb7fe69cca7de88a8eb57d21d6059

The preceding six independent real original-market native test
Ledger/Witness read-only inspections each have their own
immutable local receipts; four were already acknowledged
and two remained HOLD without original source-time escrow.
No new transaction was appended by that verification.

Final private, create-only, directory-fsynced composite report:

~/.local/state/heptatrader/operator-evidence/train-v2-remote-escrow-readback-20261012/phase4o-final-exact-escrow-origin-readback-and-native-ackonly-hold.json

SHA256:
4fe0febb1f0ce5b1c6403020ccf5dd9a0974aea8491a16b019b20b61abbc7481

The separate six real copied-market review receipts live in:

~/.local/state/heptatrader/operator-evidence/train-v2-escrow-readback-20261012/

This is deliberately *not* a positive independent physical
remote-host signed escrow test, hardware durability qualification,
approved product-root RecoveryGrant, or an original strategy
on-policy/off-policy performance proof. Product status remains:

HOLD_REMOTE_INDEPENDENT_ESCROW_RECOVERY_ROOT_ACTION_PROPENSITIES_AND_REAL_PNL.
