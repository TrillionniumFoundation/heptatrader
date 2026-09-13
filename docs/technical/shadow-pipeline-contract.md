# SHADOW research developer contract

Status: EXPERIMENTAL
Applies to: read-only research scripts and the EUR/USD confirmed-momentum v2 configuration

## Components and artifact flow

This pipeline is a set of components, not an installed unattended controller.
No component receives Broker mutation authority. Run each script with `--help`
for its exact CLI; Python entry points below are the fixture interfaces.

| Script under `scripts/` | Input → output / contract owner |
|---|---|
| `hepta_official_source_capture.py` | `capture`: bounded official-source capture; manifest `hepta.official-source-capture-manifest.v1` plus root capture receipt |
| `hepta_market_official_source_extractor.py` | pinned capture bytes → `hepta.market-source-extraction-receipt.v1` and `hepta.market-source-bundle.v2`; provider-format parsing lives here |
| `hepta_market_evidence_normalizer.py` | `normalize(bundle)` → calendar and information evidence; validates extraction attestations |
| `hepta_shadow_market_history.py` | immutable quote records + head → one-/five-minute bars and provenance; `recover_history_head`, `materialize_bars` |
| `hepta_market_context_builder.py` | `build_packet`: quote/portfolio snapshot, histories and normalized evidence → identity/digest-bound context packet |
| `hepta_eurusd_confirmed_momentum_strategy.py` | `evaluate`: packet + strategy config → SHADOW decision receipt, `TRADE` draft or `NO_TRADE` |
| `validate_hepta_strategy_decision_receipt.py` | receipt fields, bounds and intent consistency; no execution side effect |
| `hepta_strategy_shadow_runner.py` | `run_shadow_iteration`: policy + packet → durable receipt then strategy state; policy v1, campaign v1, state v2 |
| `hepta_strategy_replay_evaluator.py` | `evaluate_replay`: sealed decision/mark sets → fill assumptions, costs, returns and drawdown |
| `hepta_strategy_contracts.py` | strict JSON, finite numbers, canonical digests and atomic JSON writes shared by these programs |

The source-controlled parameter object is
`strategies/eurusd-confirmed-momentum-shadow-v2.json`. Do not copy it into a
second handwritten policy. Config, selected implementation bytes and evidence
bytes contribute to digests. Changing bound bytes requires newly assembled
research evidence, not relabeling old results.

## Common JSON and file contract

`load_document` requires an object and reads at most its configured bound plus
one byte (default 4 MiB). It opens a non-symlink regular single-link leaf without
blocking on a FIFO, checks size before reads, and rechecks descriptor/path
identity after reading. Parent directories are caller-trusted research storage;
this helper is not a substitute for privileged deployment namespace validation.

Duplicate keys after escape decoding, invalid UTF-8, non-finite constants,
exponent overflow and nonzero underflow are rejected. `require_number` rejects
booleans, non-numeric types, NaN/infinity and conversion overflow before range
checks. Explicit observed zero is valid only where the field contract permits
zero; numeric zero does not establish evidence presence or completeness.

For example `{"price":1e309}` is invalid even though it uses ordinary JSON
number notation, while `{"value":0e-9999}` is an explicit zero. Existing bounded
integer fields remain integers rather than being silently converted to floats.
Canonical digest bytes use ASCII JSON with sorted keys, no non-finite values,
compact separators and a trailing newline. Digests have the `sha256:` prefix.

Atomic JSON publication writes and synchronizes a same-directory temporary,
renames it, and synchronizes the directory. A failure before rename preserves
the old document and removes the temporary. A directory-sync failure after
rename is an uncertain durability result: callers must not assume the old
state still exists. History no-overwrite publication and head-recovery rules
below are separate from this general replacement helper.

## Time and feature arithmetic

Times are explicit epoch milliseconds where fields have the `*_ms` suffix.
Quote prices are USD per EUR; quantities are EUR units; returns, spread and
slippage expressed as `bps` are basis points, not raw price differences.

For provenance-bearing quote histories, `_resample_quotes` anchors to the last
observation and samples a 5,400-second lookback at 900-second intervals. Each
target uses `bisect_right(times, target)-1`: the latest observation at or before
that target, never one after it. Missing coverage or a lag exceeding the
configured maximum gap fails. The current fixture requires seven resampled
points and at least 361 raw observations. The non-provenance compatibility
branch is not evidence that a collected history has passed provenance checks.

EMA uses alpha `2/(period+1)` seeded by the first value. The v2 configuration
uses 12-/36-bar EMAs, six-bar slope, fourteen-bar ATR and 300-second bars. ATR
uses true ranges, including gaps from the previous close. Momentum and
confirmation are distinct lookbacks. Feature code must not replace their
sampling windows with future marks from the replay evaluator.

History `_quote_bar` uses the half-open collection interval `[start, end)` and
midquotes `(bid+ask)/2`. It requires sufficient sample count, boundary coverage
and bounded gaps, then computes open/high/low/close. A five-minute bar requires
all five complete one-minute children. Missing children are not forward-filled
into a complete bar. Changing cadence/jitter changes coverage, not the meaning
of an observation timestamp.

## Decision flow and working examples

`evaluate` first checks config/packet shape and bound digests, then evaluates
hard completeness, freshness, portfolio, event/information, spread and regime
vetoes. The current config requires a flat portfolio and zero active orders.
Typical bounds are quote age 5,000 ms, portfolio age 10,000 ms, spread 1.5 bps,
absolute setup momentum 5–40 bps and expected move at least three times expected
cost. See the config for the complete inclusive/exclusive logic and all limits.

A valid trend fixture with positive momentum, confirming direction and permitted
cost/spread emits `TRADE` with a **non-executable** BUY draft at the ask. The
symmetric negative fixture emits SELL at the bid. LMT/DAY, quantity one and the
maximum holding/horizon values are bounded by the config. A stale snapshot,
event exclusion, incomplete evidence or a non-flat portfolio emits `NO_TRADE`
without an intent. Invalid structure or tampering raises a typed contract error
rather than becoming a plausible no-trade explanation.

`tests/python/test_research_behavior.py` contains complete executable evaluator
fixtures, derives current config/code digests and checks these outcomes. They
are deliberately minimal evaluator inputs, not captured official-source
campaigns. Do not paste their synthetic provenance into operational research.

## Persistence and recovery

History record publication refuses overwrite. Its head is a separate atomic
projection of immutable records. `.history-head.pending` is an exclusive marker
for interrupted head replacement; recovery must validate the immutable sequence
before rebuilding the head. An empty valid history recovers to empty and removes
an interrupted pending marker. A broken nonempty chain must not be “repaired” by
skipping records or inventing hashes.

The runner commits state only after its corresponding receipt is durable. An
interrupted final audit must be retried as finalization, not by consuming an
extra market sample. State v2 and the campaign/policy bindings constrain replay;
changing a state schema requires explicit old-state handling.

There is no cross-process writer lock supplied by merely calling a component
function. A controller must serialize history/state writers and honor the
exclusive publication/marker failures. The absence of a canonical controller
is an explicit integration limit, not permission for concurrent writers.

## Replay economics

`evaluate_replay(decisions_document, marks_document, *, horizon_seconds,
round_trip_cost_bps, maximum_exit_delay_seconds=30, entry_latency_ms=1000,
entry_slippage_bps=0.2, exit_slippage_bps=0.2, maximum_holding_seconds=None,
allow_overlapping_intents=False)` validates sealed decision and mark provenance
before evaluating returns. These defaults are research assumptions, not
Broker-observed execution quality.

A BUY enters at ask plus conservative slippage and exits at bid minus slippage;
SELL uses the opposite sides. An entry outside the limit is unfilled, not filled
at a favorable synthetic price. Expected intent slippage cannot be weakened by
a smaller replay setting. Round-trip costs are charged in addition to the
spread/slippage in the prices. Holding limits cap rather than extend intent
horizons; overlap is disallowed by default. Drawdown uses cumulative economic
returns, not the count of favorable predictions.

## Behavioral evidence and open work

`test_strategy_contracts.py` covers numeric/file input rejection, bounded reads
and atomic replacement failures. `test_research_behavior.py` executes the real
evaluator, vetoes, tamper rejection, no-lookahead sampling, OHLC boundaries,
missing bars, two-sided fill/slippage, holding/overlap/drawdown, immutable
publication and interrupted **empty** history-head recovery.

`test_research_contract_smoke.py` remains import/syntax and forbidden-SDK lint;
it is not behavioral proof or a sandbox. The full capture→extract→normalize→
nonempty history→runner→sealed replay/final-audit campaign and provider-format
fixtures remain `SHADOW-INTEGRATION-001` in the gap register. The tests above do
not close that integration gap. Promotion still requires a separately bounded
execution boundary and Broker-observed qualification; PAPER and LIVE authority
remain false.
