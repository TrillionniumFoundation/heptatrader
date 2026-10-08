# PR #178: one candidate, independent evidence, explicit release gates

This is the current navigation for FX/NAV consolidation. Earlier running notes
remain historical evidence, not the current approval decision. No production
service or market-data source is switched by this work.

## Source preservation and reviewed integration

The canonical candidate is `fix/fxnav-broker-farm-deferral-20261008`, PR #178,
starting at `c2e8c3a72e3178952c177d3d3644916952b7cbff`. No parallel delivery PR
or "final" candidate branch is created. The exact final candidate SHA is
recorded in each executable validation receipt outside Git, avoiding a
self-referential test-result commit.

The dirty `heptatrader-ib-quote-proof-20261008` tree (base `02f087c`) was saved
without modification to the private operator evidence directory
`pr178-consolidation-20261008`: a binary diff, NUL-delimited git status, a
source archive and per-file SHA256 manifest. The archive hash is
`2bab022c1c0726967d60182ae557423e254c86b9ef1b30edc73b728f05bb358b`.
The original worktree is retained unchanged. It is NOT blindly merged.

The exact state/header/native-test changes from `981fc3b5d04dd855743dc7efcfb9c95a96b809ab`
are integrated: initial recorded no-send can recover after cooldown; an
unknown result cannot be replaced; an outcome cannot be rewritten twice.
Only actually sent market-data IDs are cancelled. No order path is added.

The uncommitted research live runner remains quarantined for review. Its
shared TOP/TBT liveness and its control-triggered TBT resubscription timing
cannot qualify independent sources. Instead, the candidate introduces the
socket-free [independent replay validator](../../scripts/hepta_fx_source_evidence.py).
It verifies event chains, pins raw bytes and records TOP and TBT coverage
separately without interpolation, future ticks or cross-request quote legs.

The [single evidence index](../evidence/pr178-fx-nav-index-20261008.json)
contains both positive and negative captures, their file hashes, configuration
hashes, SDK pins when actually recorded, exact capture windows and validator
hashes. Historical missing producer/config/SDK attestations are explicitly
null with a provenance-gap explanation. They are not fabricated retrospectively
and cannot become qualification receipts. Raw `/tmp` captures were copied to
a durable private evidence directory without altering the originals.

## Confirmed fixes and their scope

1. CI directory-owner fixture uses its actual fixture owner instead of assuming
   UID 1000. The production directory ownership and mode checks remain intact.
2. Secondary CASH recovery distinguishes pending/unknown vs recorded no-send,
   including the first subscription; new IDs and existing 15s cooldown remain.
3. Telemetry is an **adjacent sampled span**, not proof of continuous state.
   A gap above 6000ms (5s cadence + 1s scheduler allowance), clock regression,
   service/connection epoch or secondary request-ID change breaks the span.
   Quote validity itself remains 5000ms, unchanged. Compatibility metric names
   remain but `sample_span_is_continuous_proof=false` is explicit.
4. TBT retains source/receipt high-water marks after rejection. Consecutive
   backwards ticks never erase them, identical source-second/price callbacks
   never re-stamp a leg, future source timestamps are rejected, and only an
   explicit advancing connection epoch resets the scope.
5. Source statistics use independent streams. A capture with both subscriptions
   is marked as stream replay, not mislabelled as an exclusive TOP-only or
   TBT-only experiment. Neither stream can fill the other's gap.

## Same native components, no order-capable transport

`hepta_ib_quote_recovery_component_tests` compiles the same production runtime
source list, invokes `RefreshStaleSecondaryCashQuote` itself, and uses the real
adapter, subscription set, CASH-farm marker and admission state. The runtime
profile is mixed BTC-primary / EUR-secondary with `observationOnly=false`;
it does NOT bypass the branch under test. Its injected wrapper has no socket,
throws on every order/account method, and records all calls. The injected clock
has no environment, CLI or IPC activation in the production daemon.

Covered: initial no-send, cooldown, farm unavailability/restoration, one-leg
unavailability, new-ID dual-leg receipt, old request rejection, uncertain cancel,
SDK exception before and after send, real adapter rebuild and epoch isolation.
The receipt states `live_broker_qualification=false`. It is component fault
regression, NOT a 30-minute real Broker recovery certificate and NOT a complete
owner/order/position reconciliation exercise. Production is not restarted.

## Strict NAV publication and consumer migration

[Assurance code](../../scripts/hepta_nav_assurance.py) separates:

- `capture_complete`: a source-validated v3 three-End receipt.
- `arithmetic_reconciled`: original numeric rules and unchanged USD 1.00 bound.
- `valuation_basis_attested`: independently authenticated common Broker basis.
- `strict_nav_eligible`: all required source-time and financial guarantees.

No supported TWS account-download format currently supplies the third proof.
The code deliberately has no boolean/quote-midpoint/retrieval-time fallback.
A successful arithmetic observation is retained in `arithmetic-latest.json`
and bound to `raw-latest.json`; the public `nav.json` is now v2, with
`authoritative=false`, `nav=null` and explicit assurance. Legacy consumers
reject that public format instead of silently accepting arithmetic authority.
Publication writes the fail-closed public record FIRST. It never creates a
new `first-*` reset baseline from arithmetic alone and leaves old records and
rejected raw untouched. Preflight remains outside the accounting period.

The current account-report consumer checks source digests, derives assurance,
withholds strict current NAV/return/complete flags, and displays arithmetic
observation under a distinct diagnostic label. Historical opening arithmetic
can remain known, but `opening_nav_at_reset` is UNKNOWN without source basis.
An untrusted assurance JSON or forged public flags cannot open the strict gate.
Pure historical normalization/return arithmetic remains replayable; it is not
a release authority. Deploying the observer and consumers is a separate gate.

## Predeclared remaining acceptance

A/B code consolidation does not approve production. Every final validation
receipt must name the same exact candidate HEAD and SDK/binary hashes.

- Native live qualification: at least 30 minutes, duration declared before
  capture; separate startup, steady state, source-valid coverage and recovery
  latency. Use the candidate components, an enforced order-denying egress,
  globally reserved client/quote IDs and quota/cooldown coordination. Exercise
  data lost (1101) vs maintained (1102), farm loss/restoration, full reconnect,
  late callbacks and unknown results. Missing data remains missing.
- Source choice: separate exclusive TOP-only and TBT-only acquisitions with
  identical SDK/contract/time-grid rules. Historical mixed stream coverage
  cannot make this decision. No TBT switch or silent fallback is installed.
- NAV source request to IB: identify an authenticated account + report/snapshot
  version + valuation instant/timezone + currency and BASE basis; clarify
  accrued interest FX timing, rounding and coverage; request supported source
  semantics rather than treating transport End as an atomic account mark.
  An official Activity/Flex period-close anchor must be kept separate from
  current intraday NAV. No authenticated common-basis source is invented.
- PAPER cutover: only after the above native recovery/identity evidence, retain
  binary/config rollback and reconcile original OMS commands, permanent broker
  orders/executions and FX/BTC cash/positions. Keep the existing $100k strategy
  and owners. Freeze new business intents during recovery; no send-counter
  increments are allowed there. Afterwards compare legitimate transitions per
  command, never flatten/reopen to hide identity loss.

**Disposition: Draft; no production cutover; no strict NAV qualification.**
