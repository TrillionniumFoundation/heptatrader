# Isolated historical-price research and current-$100k reward consumer

This package does not modify live trading, NAV, quote feeds, old Laya training,
existing prospective trials, or canonical promotion. A trained offline readout
is not a live strategy release. Private account data and model weights are not
part of this source tree.

## Train V2 research-only data contracts

See [Train V2 Phase 1](../train_v2/README.md) for the independent
Point-in-Time feature contract, revised-historical isolation and
event-driven **review-only** orchestration. This does not install a new
training service, upgrade old historical records into live predictions,
replace the existing trainer or alter the original PAPER strategy.
The [Phase 2 continuation](../train_v2/PHASE2.md) verifies original Numeric16
and Laya question semantics and original PAPER cost evidence, without granting
historical data real-time authority or changing the trading model.

Train V2 phase 3's local pre-event registry is documented in
[PHASE3.md](../train_v2/PHASE3.md). It is not an automatic model owner or
a replacement for the original forward trial evidence.

Train V2 Phase 4A adds a receive-only research Ed25519 signature verifier:
[PHASE4.md](../train_v2/PHASE4.md). A signed research envelope is not
a canonical Agentd owner, producer enrollment or trading permission.

Train V2's opt-in original broker-quoted-expiry experiment is described in
[PHASE4B.md](../train_v2/PHASE4B.md). This versioned FUTURE profile does
not modify the existing arrival-only source rule or historical data roles.

Train V2's Canonical native ledger readiness limits are recorded in
[PHASE4C.md](../train_v2/PHASE4C.md); price confidence cannot substitute
for an actual logged behavior propensity or canonical LedgerWriter ACK.

Two independently accessed physical hosts now support bounded full-byte
TEST-ONLY escrow receipts in [Phase4Q](../train_v2/PHASE4Q.md).
They are synthetic; original market-time source and production owner
qualification remain separate and unapproved.

The [Phase4R](../train_v2/PHASE4R.md) physical full-byte test
corrects TEST-only native SignedLearningEvidenceV1 digest field order
and proves final Rust typed X230/Mac readback. All real broker and
Canonical LedgerWriter product promotion gates remain closed.

Phase 4S [native preaction deadline audit](../train_v2/PHASE4S.md)
pinpoints why the original Phase4L test-only signed Decision/Witness
performed after quote expiry cannot qualify as an actual native trading
PREPARED decision, despite valid 5-minute PRICE outcome evidence.

Train V2's actual fresh source-only Mac TEST signature and independent
readback limitations are recorded in
[PHASE4T.md](../train_v2/PHASE4T.md). It is NOT a model prediction,
signed native LedgerWriter or completed financial Outcome.

The optional future first-source rotation-fence research protocol is
described in [Train V2 Phase4U](../train_v2/PHASE4U.md). It does not
rewrite historical IB revisions or any previous prospective receipt.

The actual isolated first-original-source/native no-trade laboratory
readback and its strict product HOLD are documented in
[Train V2 Phase4V](../train_v2/PHASE4V.md). No previously failed
historical-price or genuine behavior-policy trading sample is repaired.

Original 193-member Canonical Rust workspace qualification and independent
source-commit evidence are preserved separately in
[FULL_CANONICAL_WORKSPACE_20261012.md](../train_v2/FULL_CANONICAL_WORKSPACE_20261012.md).
This does not change the original Phase4S source-expiry incident,
native Phase4V source cutoff or PAPER trading gate.

The independent full-native and original source-time test lanes are
jointly read back in
[UNIFIED_NATIVE_SOURCE_CUTOFF_20261012.md](../train_v2/UNIFIED_NATIVE_SOURCE_CUTOFF_20261012.md).
This is not an executable-price historical conversion or a trading grant.

## Separate label populations

`export_current100k` reuses the existing authoritative economics replay and
exact attribution importer, but admits only the registered behavior
`EURUSD_ORIGINAL_ENTRY_CONF_0.62_USD100K_REFERENCE_NOTIONAL`.
It verifies the original request, prepared command, forecast identity, actual
fills and fees, terminal evidence, and independent Decimal PnL recomputation.
Variable executed EUR quantity must equal the original prepared quantity; it is
not relabeled as the old 25,000 EUR profile. Execution-derived quantity, fill
prices, fees, and realized outcomes are never inserted into feature text.

Each snapshot is content-addressed and immutable. Re-reading/restarting cannot
create duplicate episode labels. Closed negative outcomes are retained. The
consumer writes only its own dataset/status directory, with a 256 MiB retained
state bound and 1 GiB filesystem reserve; it never prunes evidence or trains a
head automatically. The original EUR25k consumer and its registration stay
unchanged.

`consumer_launch` requires a pinned source inventory and enters the existing
Landlock/seccomp sandbox before importing the evidence reader. The installation
creates only `heptatrader-current100k-consumer.service` and `.timer`; the service
is a one-shot read-only refresh, so successful `inactive/dead` between timer
activations is expected. The sandbox proves socket creation, bearer reads and
outside-output writes are denied. The immutable installation receipt contains
release/config hashes and rollback commands. It does not restart any existing
trading service.

## Bounded IB historical download

`ib_history` is restricted to the existing local PAPER Gateway, independent
client 8984, pinned SDK bytes, and EUR/USD CASH IDEALPRO. Only START_API,
historical bars/schedules, head timestamp, and cancellation of this client's
own historical requests are available. No order or account mutation message is
allowed. Requests are serial, spaced at least 12 seconds, with per-request and
row bounds and no unbounded retry/reconnect loop.

A plan contains 1–7 chunks of 30 D, three separate MIDPOINT/BID/ASK series,
broker SCHEDULE responses, and three head-timestamp probes. Every raw response
and receipt is immutable. Resume verifies hashes before skipping previously
recorded requests, including recorded failures; it does not silently replace a
failed or revised chunk. The retained session log distinguishes API completion
from proven interval coverage.

## Coverage, partial bars and causal examples

`dataset` validates each OHLC bar and strictly increasing timestamps, parses
broker sessions with explicit time-zone alias handling, and rejects ambiguous
or nonexistent local times. It crops to the declared absolute historical
interval. A bar is eligible only when its complete five-minute interval has
ended by its own request cutoff. This test precedes duplicate comparison;
otherwise a partial last bar can conflict with its later complete version.
Equal complete duplicates are deduplicated; conflicting complete duplicates
fail closed. Missing bid/ask, crossed open/close pairs and expected-session
missing bars prevent a full-coverage claim. Weekends/session breaks are not
filled with fabricated prices. Raw responses remain unchanged.

Examples use 12 completed five-minute bars and a 15-minute forward target.
Features and both endpoints must lie on a contiguous path. The chronological
60/20/20 split purges complete feature-plus-label spans at boundaries. Training
normalization never reads validation/test statistics. Labels are historical
price observations or explicitly simulated costs, never actual execution
rewards or repairs to old prospective windows.

## Offline paired experiment

`offline_compare` registers the immutable dataset, deterministic time-stratified
sample indices (256 training, 128 validation, 128 test), both complete pinned
backbones, identical feature text, fixed projections, readout family and
validation-only hyperparameter selection before fitting. It rejects silent
truncation and runs under the existing no-network sandbox. GPU mode requires
at least 6 GiB free and caps this process at 4 GiB; no service is interrupted to
make space. English and multilingual encoders stay frozen. Only new independent
classification readouts are fit.

The numeric baseline is explicitly a NEW causal five-minute-bar profile, not
the existing live numeric checkpoint. The old checkpoint has a different
input schema, target horizon and output classes; it must not be claimed as
compared without a separately verified adapter. Both semantic-only readouts,
two fixed 50/50 mixtures, and no trade are reported. Test Brier, accuracy,
paired UTC-day bootstrap uncertainty and cost scenarios are retained, including
negative results. These are exploratory intervals, not multiplicity-adjusted
proof of prospective efficacy.

Cost scenarios use historical next-bar bid/ask opens, which are not guaranteed
fills, a declared $100k reference notional and 0.4 bps roundtrip commissions.
Changing the assumed slippage also changes the pre-entry cost gate and hence
the trade set. A fixed-trade stress must instead hold recorded time/side
identities constant and subtract the extra cost without recomputing the gate.
Neither interpretation is actual broker PnL. No result here creates a live
candidate, an on-policy monetary reward, or promotion permission.

## BTC settlement evidence

`btc_terminal_probe` reads completed-order and execution callbacks using a
separate PAPER client. It matches original account, contract, correlation and
permanent ID; it never matches solely by numeric order ID. A missing callback,
flat position, execution fee completeness or API end marker does not prove a
terminal order state. The native OMS is never rewritten by this probe.

## Immutable IB failure continuation (2026-10-10)

See [IB_HISTORY_FAILCLOSED_20261010.md](IB_HISTORY_FAILCLOSED_20261010.md)
for frozen HMDS error 162 evidence, fail-closed resumption, the 210-day
verified dataset, and preconditions for any new output root.
See [IB_HISTORY_DURABLE_RECEIPTS_20261010.md](IB_HISTORY_DURABLE_RECEIPTS_20261010.md)
for terminal receipt safety, the real PAPER 162 probe and byte-identical
three-fold offline replay.
See [IB_CASH_HMDS_AND_VALIDATION_20261010.md](IB_CASH_HMDS_AND_VALIDATION_20261010.md)
for passive cash farm status, exact EUR CASH evidence, and net-cost diagnostics.
See [IB_HISTORY_GLOBAL_CLIENT_LOCK_20261010.md](IB_HISTORY_GLOBAL_CLIENT_LOCK_20261010.md)
for the canonical UID-scoped lock and independent cross-process exclusion.

A failed receipt is never a successful checkpoint; no operator should
disable quote/source freshness gates or use revised history as a live mark.

## Running and testing

Set `PYTHONPATH=research:research/neuron_learning:scripts` from the repository.
All commands require explicit private config or output paths:

```sh
python3 -B -m historical_training.export_current100k --config /private/consumer.json
python3 -B -m historical_training.install_consumer --config /private/consumer.json --receipt /private/install.json
python3 -B -m historical_training.ib_history --config /private/history.json
python3 -B -m historical_training.dataset --source /private/raw-history --output /private/immutable-dataset
/path/to/pinned/venv/bin/python -B -m historical_training.offline_compare --config /private/experiment.json
python3 -B -m unittest discover -s research/historical_training/tests -v
python3 -B scripts/run_python_tests.py --lane core
python3 -B scripts/run_python_tests.py --lane source
```

The initial 2026-10-08 live execution produced a schedule-complete retained
180-day closed-bar dataset and an offline comparison with no semantic Brier
improvement over the new numeric baseline. These artifacts remain unpromoted.
The current100k consumer is independent; BTC missing terminal evidence, broker
valuation timestamps, account-period opening NAV/flows, legacy numeric parity,
and future unseen efficacy remain separate acceptance requirements.


## Follow-through verification (2026-10-08)

The original PR #181 exact-head CI failed in the existing cost reset fixture:
its first-quote-failure test mocked UID 1000 instead of the actual temporary
fixture owner. Only that fixture now uses `st_uid`; production ownership
validation has not been weakened. A default-core test bridge now discovers
all historical research tests, rather than requiring a manual extra command.

The history boundary now validates serialized SDK request IDs, contract,
request kind/cutoff/duration, no-streaming mode, cancellation ownership and
single dispatch, with minimum pacing enforced at actual egress. A request
is marked issued before transport: an unknown outcome never permits replay.
An actual pinned-SDK serialization sink checked all 27 original planned
requests and rejected direct foreign cancellation without a socket. A real
subsequent extension used only START_API and four history requests.

Calendar qualification uses the union of the actual returned calendar header
intervals. A shortened per-chunk header at a weekend is NOT automatically
coverage failure when another observed overlapping header covers that tail;
nor may the missing tail be assumed. Missing calendar extent or fractional
bar timestamps cannot silently become qualified complete coverage. The
original 180-day bar bytes still qualify, and remain unchanged.

A separate 210-day version reuses 27 hash-verified chunk receipts and downloads
only four new requests. The fully closed, calendar-covered dataset contains
42,716 paired timestamps; chronological purged groups are 24,266 training,
8,074 validation and 8,079 test, with 2,250 gapped examples excluded and 32
boundary rows purged. These names describe a temporal partition only:
repartitioning a larger historical dataset DOES NOT make previously inspected
test periods unseen. No new efficacy trial or model is created by this export.

`audit_offline` independently reconstructs the existing fitted readouts from
the declared training features, train-only normalization and validation-only
selection, checks saved predictions/Brier and every recorded cost result, and
reports fixed-trade adverse cost stress without changing trade identities.
It checks frozen-feature hashes but does not independently rerun encoders.
Two diagnostic baselines were added AFTER the original test was seen: uniform
Brier 0.6666666667 and training-class-prior Brier 0.6656494141. The original
numeric result is 0.6767533955 and does not beat either. These extra comparisons
are diagnostic, not a new independent holdout or multiplicity-adjusted proof.
Original result files, semantic losses, and rejected live windows are retained.

Local follow-through tests: 1,280 Core / 106 Source, including 65 historical
research cases. Remote CI must be checked on the new exact commit; the old
failed run is retained as evidence. No production quote/NAV/strategy service
is switched by this research branch. PR #178 remains the separate native
source/NAV candidate, and its live acceptance and strict valuation proof are
not supplied by this work.

Example independent result audit (read-only source, new immutable receipt):

```sh
/path/to/pinned/venv/bin/python -B -m historical_training.audit_offline \
  --config /private/original-experiment.json --output /private/audit-new.json
```

## Controlled training resumption

See [2026-10-09 training resumption](TRAINING_RESUME_20261009.md) for original-source freshness repair, real value-head learning, the archived Neuron256 trial, preserved failures, resource evidence and remaining future/native acceptance boundaries.
