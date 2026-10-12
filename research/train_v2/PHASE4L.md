# Train V2 Phase 4L — ACTUAL fresh IB PAPER source to original native LedgerWriter

Date 2026-10-12, Pocket4 with qian-qi/Mac/X230. Draft branch stacked
on Phase 4K. This is a STRICTLY ISOLATED, NONTRADING RESEARCH trust and
persistence experiment. It is NOT original opaque LLM on-policy behavior,
a real broker execution, a production signer/root enrollment, a product
training-eligible DatasetSnapshotV3 or Artifact Owner CURRENT admission.

## Important distinction from earlier phases

Phase 4I exercised Mac/X230 independently generated TEST keys against
the original native LedgerWriter with entirely fabricated Decisions and
Outcomes. Phase 4J observed ONE REAL fresh EUR/USD shadow Decision and
a real five-minute original-price label with X230 source replay, but
neither event passed through the actual native Writer at event time.

This Phase 4L added a different hard gate: a NEW precommitted shadow
Decision based on original, currently active IB PAPER quotes was
actually signed by Mac and DURABLY ACKNOWLEDGED by the UNMODIFIED
original Canonical LedgerWriter **before** its true future horizon.
After the actual five-minute horizon, X230 separately recomputed the
original price path; its typed research Outcome was independently
test-signed and appended to the SAME previously acknowledged ledger
after a native process cold reopen. An independent test Evaluator
then froze and revalidated a native DatasetSnapshotV3.

The producer code and test credentials are NOT the original trading
LLM, and price-bps Outcome is NEVER actual trade PnL or net utility.

## Actual original-market pre-registration and causal decision

Original existing EUR/USD Laya PAPER quote pipeline was active:
source-readiness READY_FOR_REVIEW_NOT_AUTHORIZATION, no new broker
market-data query, order or service change.

Before sampling, the existing local SQLite + fsynced immutable plan
registration was performed with the real Pocket4 clock:
plan ID
5d6c76f66df593627e57b49b21461d447efc7558edc4e7f1b89370af2ff8658c

The source-time sealed plan led the first permitted future action
by 89,995ms (minimum 30,000ms). Its signature flags correctly remain
local-only, NOT product-root authenticated. The episode claims were
fsynced BEFORE the new shadow sampler's CSPRNG selection. Future
source quote access, sample reroll and backdating were forbidden.

Actual decision: 2026-10-12 09:17:01.699 China time (UTC+08).
Selected shadow signal: NO_TRADE.
New research CSPRNG choice law: NO_TRADE 1/2, UP_SIGNAL 1/4,
DOWN_SIGNAL 1/4, subject to exact known hard spread filter.
Selected actual Q32 propensity 2147483648 (1/2), source code SHA,
six exact original quote receipts, locally known-at and genuine IB
reported quote expiry. These facts are valid ONLY for the new research
shadow controller and not the old opaque LLM.

Source-time immutable shadow Decision SHA256:
0ec5ffc1e73158305916af6cd96cc68a76b73bd2d3f0f6727e0ed5eb66a87fb4

A second immutable causal feature receipt was sealed at the same
real input horizon while its actual future outcome was PENDING.
Source-time feature receipt SHA256:
4abb01481c80703e25e1ccace42e57a824c808746e8b6fb0105852b683d373a1

The test-only bridge packet includes the plan, full closed candidate
set, exact native-reserved abstain mapping for NO_TRADE, input quotes,
code digest, chosen Q32 probability, pre-outcome feature ID and
the immutable shadow Decision SHA256. It explicitly denies broker
execution, original LLM propensity and product root enrollment.
Source-to-native packet SHA256:
6ee5e1a012e5c3975717c2c6a395dd4b2e652545c62c6e62eb25d19b8bc84928

## ORIGINAL native Rust ledger, true source-time signed Decision

Pinned original Canonical full repo
hepta-private-ci@78fdb0cf8537e3a84fc6e0a849707559c80881e8,
original unchanged Cargo.lock and native sources.

A TEST-ONLY Rust example (real_source_native_lab.rs) was copied
temporarily to the original full Cargo workspace and compiled with
cargo --offline --locked. It called the ORIGINAL typed
LedgerWriter::append_decision, native signature verifier,
root-signed LearningTrustDistributionV1,
ProductionDecisionV2 / CandidateSetCompletenessReceiptV1,
DurableLedger and LedgerWitnessStore. The temporary example file
was removed after compilation. Original Canonical checkout and lock
were NOT changed.

The temporary test Root private key was generated and held on qian-qi;
a separate test Generator private key was generated and held on Mac.
Pocket4 ONLY received the exact signed native public bytes and keys.
Root signed 2026-10-12 09:17:14.336 +08.
Mac Generator signed 09:17:14.660.
Original native durable Decision Writer/Witness ACK 09:17:14.922.

Both actual signatures and native Writer ACK occurred EARLIER than
the true fixed future endpoint at 09:22:01.699. A tampered Root
distribution and tampered Generator signature were separately
REFUSED by the original Rust verifier. The distinct original
witness advanced to exactly sequence 1.

The native test-only Decision was also immediately recovered from
the original on-disk Ledger + separate Witness by an OTHER original
Rust process with only the already signed public root distribution.
This pre-outcome cold recovery did not request any signature or change
the durable ledger/witness hashes.

Mac/qian source-time signed original native messages SHA256:
4c28088d53484be3d95c8f3cb3e638a146f9f6f709e261701adc6f5a56759023

## ACTUAL future EUR/USD price endpoint, separate physical X230 review

True forward time horizon: 300,000ms after the Decision.
Due: 09:22:01.699 +08.
Original IB PAPER endpoint quote observed 09:22:04.136 +08
inside the predeclared five-second tolerance. The future value
was NOT available when the Mac Generator signed or ledger ACKed.

Actual original-market five-minute midprice movement:
**-0.80241080 basis points**.

Original signed/frozen price observation SHA256:
12ee3768af903fa8b3e2a409efb7a1f805c3c0fa9073c950b2b482ea990c93a7

A SOURCE COPY of the original locally SHA256-chained Pocket4 quote
segment, exact preoutcome frozen feature, the native shadow Decision
and the later observed price label were supplied to the physical X230
only AFTER the announced horizon matured. Standalone X230 independently
verified original SHA256 chains, six actual earlier source quote IDs,
the exact source-time features and endpoint, and 146 continuous quote
path points; it recomputed EXACTLY -0.80241080 bps using Decimal math.

A separate X230 memory-only ephemeral RESEARCH signer signed that
independent computation; private memory signer was not exported
as a file or transferred. X230 research receipt SHA256:
8bc09778a4879b9545d7ea8eff29e60e8780ef331a659831b86e648d2d8ee97a

Pocket4 rechecked X230's self-attested ephemeral public key/signature
and that X230's transferred source was the exact prefix of the
original Pocket4's own retained input segment. It did NOT treat
the ephemeral key as a production credential.
Pocket4 X230 research validation SHA256:
4a065cfbbac08ac375db20fc39cf06901960b638b2d7105f351a306bc8a164f1

The later packet represented the midprice movement as signed
FixedQ32 raw -3446328144 BASIS POINTS, with an explicit unit
profile declaring price-only, NOT dollars, broker fills, actual
commissions, slippage, financing or after-cost execution gain.
Typed outcome packet SHA256:
f926c7c9d518a8cfa97799f89d506a80aa6b376d2eed82374fe8a5825c3632a8

## X230 source-verified Observer -> SAME original native durable writer

With that actual source-time market evidence in hand, the original
native Rust process RECOVERED its existing sequence-1 acknowledged
Decision before appending. qian-qi's authorized test orchestration
requested native Observer signature from the dedicated physical
X230 test key, separate from Mac Generator. A separate qian-qi
test Evaluator signed a native V3 dataset freeze.

Original LedgerWriter::append_outcome rejected a modified Observer
signature, accepted the genuine X230 test-signed terminal PRICE LABEL
and DURABLY acknowledged witness sequence 2.

The original native DatasetSnapshotV3 freeze succeeded and was
revalidated against current native ledger source projection with the
test-only Evaluator credential. This V3 object was tested IN MEMORY
during that native process and NOT deployed as a product Owner artifact.
Native test semantics and unit-profile explicitly disallow treating
this as an eligible economic dataset.

Original X230/qian Evaluator signed native message transcript SHA256:
d4badad3c5eb2994c3d94c141822d703abbabcbb351ca3fab80dde34de45f0fc

Pocket4 independently re-verified all FOUR original native Rust
root, Generator, Observer and Evaluator signature messages against
their actual public keys and role scopes, and checked that Mac
signed strictly before horizon and X230 strictly after horizon.
Offline public-verification receipt SHA256:
dcb4e19a7477229dacca1e2c981c1a35c06e4a271189f71000fe71c32a4deaf3

## Test private-key cleanup, cold replay and negative failure paths

All 5 test-only private key files were removed with the original
test signer module from their initial hosts: Mac Generator, X230
Observer, and qian-qi Root / Evaluator / unused Privacy. A separate
zero-file check on all THREE machines found none of the test-only
key filenames remaining. Production private keys were untouched.

Removing a key file is NOT a hardware secure-erasure guarantee.
And physical host separation is NOT product administrator or
organizational independence: qian-qi retains authorized SSH
control across the test hosts.

File-removal receipt SHA256:
d981af4e6f28c25e467e1370795e2ec87cb1a847fd975e857022165c0f6c657e

The original Rust ledger recovered again AFTER key cleanup using only
already stored public signed trust, with zero new signers and exact
same 2-record witness frontier. Original ledger and witness contents
were byte-identical before and after cold replay.

On THREE separately copied test-only folders the original recover
code correctly rejected:
1. A changed independent witness frame.
2. A truncated ledger that omitted independently acknowledged history.
3. A corrupted Root-signature distribution.
Each failure was preserved and no copy was ever substituted for the
original trial files. This is not a hardware power-cut test.
Three-way fault refusal SHA256:
6fd0aaab73d1dcb9f265ac0a076649d373e073f61b5e8340f0bffcaf672326c7

## One file permissions defect found and fixed

The first Rust test entrypoint inherited the shell's default file
umask, initially creating 0664 test files under a 0700 private lab
folder. The files were immediately made 0600, their data and signed
witness chain kept intact. The Rust TEST-ONLY entrypoint was hardened
to create future files explicitly with mode 0600 and refuse recovery
files with wider modes, then rebuilt and exercised for the posthorizon
native Outcome and cold recovery. No original Canonical library or
production storage file was modified.

The initial permitted Rust build differs from the hardened later
test-only example binary; the production original crates remain
unchanged and always governed by their own host ownership/permission
requirements. Do NOT claim the first files were born private.

Final hardened native example SHA256:
5d81ed7073678a43aa54981fffa1af59b53ddf44493a326a79178d8e5ba77ce5

## Full regression and reproducibility

In the same private Pocket4 evidence root:
~/.local/state/heptatrader/operator-evidence/train-v2-real-source-native-20261012/

- Full HeptaTrader Python core + historical research and new fixture
  adversarial checks:
  **1,719 passed, 21 skipped, 745 subtests passed, zero failures**.
  Full log SHA256:
  218857f522376e727d5e4d3f0d9c8fbf97dfed7b8a5bdafa017de0433eab793d
- Unmodified original Canonical Rust learning-ledger and artifacts
  libraries: 118 + 92 = **210 passed, zero failures**,
  1 + 1 deliberately ignored.
  Full log SHA256:
  7a99257f17fb8cea69fbc9a0285b47bc1c4e8cbaa73c5c324bcdcf82c355856b
- Independent after-fact real source decision sampler/Q32 and
  exact causal feature revalidation plus all 4 original native
  signature checks:
  9eb7f08449bee9a0cd924e4809232487c2d6b671eace2ce837d764579888ec32
- Final create-only composite test qualification SHA256:
  27d63793568377ccd03e9dac53b1c26e3ad2368ffb5f137777e3a3251aa8adef

Source components:
real_source_native_lab.rs — original library-linked TEST-ONLY
native Decision, Outcome, trust, Witness, V3 and replay.
real_source_native_packet.py — strict actual source-time precommit,
no-data-peeking native payload preparation.
real_source_native_outcome_packet.py — original posthorizon price
observation, independent X230 copied-source confirmation and
non-economic FixedQ32 basis-point unit binding.
real_source_native_signature_verify.py — offline detached native
signature validation with physical time ordering.
real_source_native_integrity.py — fresh source replay, sampler
CSPRNG/Q32 rule and complete pre/future split verification.
real_source_native_qualification.py — exact pinned original source,
test suites, custody, fault, file mode and final qualification check.
test_train_v2_real_source_native.py — standalone SYNTHETIC tests
for algorithm/digest/probability substitution, source rebind,
signature corruption, future observer, forgery and product authority.

The one-shot authorized signer transport and ephemeral test signer
on qian-qi were retained as TEST-ONLY local source scripts under:
~/.local/state/hepta-real-source-native-signer-20261012/
Do not copy any test-only key material into Git or production.

## Explicit production qualification boundary — HOLD

This exercise now proves genuinely forward-ordered ORIGINAL IB PAPER
market data -> new shadow CSPRNG source-time complete candidates/Q32
-> different-host test signatures -> true original native witnessed
test-ledger Decision ACK before the market future is knowable
-> physically separate X230 original-source COPY verification ->
test-only native signed Outcome and V3 freeze -> private-key-free
native recovery and tamper rejection.

It does NOT establish any of these:
- The original opaque LLM's true selected-action propensity,
  exhaustive final BUY/SELL/HOLD/abstain/veto/no-model population.
  Model confidence and observed action frequencies are NOT propensities.
- Production root/privately administered independent Generator and
  Observer principals enrolled in the real product host.
- IB-cryptographically signed quotes or an independent X230 broker
  quote provider; the two hosts share the same Pocket4 broker source.
- Original trading orders or authenticated broker fills, commissions
  and cost-complete economic net PnL. The negative observed -0.80241080
  bps is a PRICE LABEL; NO_TRADE was never executed.
- Actual deployed product LedgerWriter service and trust rotation,
  actual market-eligible production DatasetSnapshotV3, durable product
  Artifact Owner/CURRENT or independent unseen after-cost efficacy.
- Physical sudden-power-loss recovery.

Original trading policy, broker interfaces, FX/BTC guards and native
production owners were not restarted, modified or deployed. The
market research and test-only native protocol cannot promote any model.

Next real action is an independently authorized product Root/role
enrollment and a genuinely NEW behavior policy with explicit
source-time sampler and complete final action decision recording.
If the old opaque LLM policy is preserved, its true propensity
remains mathematically unavailable and off-policy training cannot be
retroactively authorized from prior captures. Continue HOLD until
these product facts are obtained with real and independent evidence.
