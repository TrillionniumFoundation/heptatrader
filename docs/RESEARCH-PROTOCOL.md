# Research and replay protocol

Status: current target contract; implementation state is tracked in `development/PLAN.md`
Applies to: `research/`, `strategies/`, Python research package and replay tests
Verification: `canonical-full-suite` on the exact revision

## Minimal durable objects

The current research path has exactly three canonical objects:

1. **RunManifest** — immutable identity of code, data, parameters, costs, folds and numeric policy.
2. **EventLog** — append-only normalized observations, decisions and simulated execution events.
3. **RunSummary** — data quality, metrics, failures, digests and validation result.

Campaign open/renew/repair/finalizer, root-custodian and multi-layer receipt chains are not part of the current path.

## RunManifest minimum fields

```text
schema/run_id/mode/source_revision
strategy id/version/definition/implementation digests
dataset URIs and SHA-256 digests
UTC calendar/session/symbol mapping
feature and label horizons
fold boundaries, purge and embargo
parameters and parameter-search budget
commission/spread/slippage/delay/impact assumptions
numeric tolerance and deterministic seed
output locations and unsupported promotion modes
an explicit `final_out_of_sample` interval (`start_ms`/`end_ms`) after the
last walk-forward test interval
```

## EventLog

Events use canonical ordering and stable identifiers. Changed data at the same authoritative timestamp is rejected. Missing, stale, duplicate and out-of-order counts are data-quality fields, not workflow states.

## RunSummary

A summary reports net performance, drawdown, turnover, trade count, exposure, tail loss, cost share, capacity, time-in-market, regime/time slices, worst slice, concentration, deterministic output digest and exact failure reason.

The executable runner emits these fields as stable JSON namespaces. In addition
to the scalar performance fields, `data_quality` records raw/normalized quote
and target counts, duplicate counts, stale/missing counters and calendar-gap
counts; `walk_forward` records each validated train/test boundary and its
purge/embargo assumptions; `digests` binds the manifest, normalized inputs,
event log and output. A valid run has `status: "VALID"`,
`reason_code: "VALID"` and an empty `failures` array. A rejected input raises a
stable protocol reason code and never produces a capability-bearing artifact.

Quote records have `ts_ms`, positive `bid`/`ask` and an optional bounded
`instrument`; target records have `ts_ms`, `instrument` and
`target_position`. Each named instrument is evaluated against its own quote
book (an unnamed legacy quote stream is a read-only fallback). Quote and
target records must be nondecreasing within each instrument, but independent
instrument books may be interleaved; a decision or fill may use only a quote
whose timestamp is at or before that event.

For a checked-in contract use:

```bash
python3 research/run_protocol.py verify --manifest research/manifest-v1.json
```

The static manifest binds every referenced definition/runner asset with a
SHA-256 entry in `strategy_digests`, and binds the runner's side-effect-free
`runner_support` module with its own raw-byte SHA-256. Verification resolves
all of those paths under the explicit source checkout, hashes the raw bytes and
fails closed on a missing, escaped, unreadable or changed asset. The installed
runtime carries the runner, support module and manifest but not the strategy
source assets, so an installed `verify` without `--root` cannot pass.

The `runtime` install intentionally ships the capability-free runner, its
support module, schema and static contract only; it does not ship the
experimental strategy source assets referenced by that static contract.
Consequently an installed runner
can run its self-test and replay a full `RunManifest`, but static-manifest
verification must name a source checkout explicitly:

```bash
/usr/local/share/heptatrader/research/run_protocol.py self-test
/usr/local/share/heptatrader/research/run_protocol.py verify \
  --manifest /usr/local/share/heptatrader/research/manifest-v1.json \
  --root /path/to/heptatrader
```

If `--root` is omitted and the referenced source assets are absent, verification
fails closed with `RESEARCH_STRATEGY_INPUT_MISSING`; absence is never treated as
a successful verification.

For a concrete run, provide a full RunManifest and JSON quote/target arrays to
`research/run_protocol.py run` (or its `replay` alias). The static manifest is
verify-only; it is not a runnable economics manifest.

CLI JSON documents are UTF-8, duplicate-key-free and bounded to 16 MiB with a
maximum nesting depth of 128; malformed or oversized documents fail closed.

Cost fields are executable assumptions, not labels.  The observed bid/ask is
the point-in-time base price; `spread_bps` is an additional conservative
adverse fill stress, added to `slippage_bps` and utilization-scaled
`impact_bps`.  `fee_bps` is charged once on each fill's notional.  Optional
`borrow_bps` is an annualized charge on marked short exposure and
`funding_bps` is an annualized charge on marked gross exposure; both are
accrued over quote/fill intervals using a fixed 365-day millisecond year.
Their amounts are included in `explicit_cost`, `holding_cost`, PnL and the
cost model namespace.  A concrete run must provide a bounded final OOS
interval; the boolean `true` flag in the static manifest is descriptive only.

## Reproducibility

The same source bytes, manifest, dataset bytes and seed must reproduce the same decision stream within the declared tolerance. A refactor requires a golden parity fixture.

## Capability boundary

Research output is untrusted input to a later reviewed runtime change. It never carries broker credentials, session tokens, preview permits, PAPER/LIVE capability or automatic promotion authority.
