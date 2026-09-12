# SHADOW research pipeline

Status: EXPERIMENTAL
Applies to: repository HEAD
Implementation: `scripts/hepta_market_context_builder.py`, `scripts/hepta_market_evidence_normalizer.py`, `scripts/hepta_shadow_market_history.py`, `scripts/hepta_strategy_shadow_runner.py`, `scripts/hepta_strategy_replay_evaluator.py`, `scripts/hepta_shadow_finalize.py`
Tests: `tests/python/test_research_contract_smoke.py`, `tests/python/test_strategy_contracts.py`, `tests/python/test_research_behavior.py`, `tests/python/test_shadow_pipeline_integration.py`, `tests/python/test_official_source_formats.py`

## Scope

The SHADOW pipeline validates and evaluates read-only market and portfolio evidence and may emit a non-executable trade-intent draft or `NO_TRADE`. It has no broker credential, PAPER/LIVE capability, order-placement API, or authority to open/rotate/revoke an Agent session.

## Implemented components

- strict strategy and artifact contracts;
- official-source capture/extraction/normalization helpers;
- authoritative market-context builder;
- hash-chained sampled market history and bar materialization;
- EUR/USD confirmed-momentum evaluator;
- SHADOW receipt validation and state commit;
- later-mark replay evaluation with transaction-cost inputs.

These programs are individually available from `scripts/`. The repository does **not** currently install them under `/usr/libexec`, and it does not contain a canonical `hepta_bounded_shadow_observer.py`. Documents and deployment must not claim otherwise.

## Data contract

Inputs are strict JSON with duplicate-key and non-finite-number rejection. Evidence references use SHA-256 and bind source bytes, strategy configuration, selected code, timestamps, and completeness. Market history is append-only and atomically advances a head after each immutable record.

Freshness, cadence, no-lookahead resampling, calendar/information provenance, flat portfolio state, spread, volatility, and expected-cost gates are hard vetoes. Narrative or confidence cannot override them.

## State and durability

The history writer uses temporary files, file and directory synchronization, and atomic replacement. Strategy state is committed only after the corresponding SHADOW receipt is durable. A failed final audit remains incomplete and may not consume a new sample during finalizer retry.

## Failure semantics

Unsafe evidence paths, changed bytes, duplicate keys, unsupported schema, missing coverage, stale quote/portfolio, time gaps, source extraction drift, hash-chain mismatch, or state/receipt disagreement produce a typed failure or `NO_TRADE`. They never create execution authority.

## Testing

The smoke test compiles and imports the catalogued Python programs and lints explicit Broker SDK imports/calls; it is not a sandbox or economic test. `test_strategy_contracts.py` executes strict numeric, bounded-file and atomic-publication behavior. `test_research_behavior.py` executes strategy outcomes and vetoes, digest tampering, no-lookahead sampling, bar boundaries, fill/cost assumptions, holding/overlap limits and interrupted empty-history recovery. `test_official_source_formats.py` exercises all five pinned grammars using explicitly synthetic fixtures. The opt-in `test_shadow_pipeline_integration.py` executes retained-payload capture/extraction/normalization, 1,208 samples and four WATCH lease generations, materialized history, runner publication recovery, finalizer retry and sealed cost-sensitive filled replay under a separate reader UID. Only HTTP transport and external WATCH observations are fixture seams; provider uptime, live market truth and profit are not claimed.

## Promotion boundary

Before any PAPER canary consumes a research decision, a separate risk challenger, bounded campaign policy, execution-side preview, human/environment authorization, and broker-observed qualification are mandatory. Promotion changes the integration boundary; it is not achieved by changing the SHADOW document status alone.

## Per-component development contract

[`SHADOW pipeline contract`](../technical/shadow-pipeline-contract.md) maps every
script to its artifact inputs/outputs, explains timestamp/unit and numeric
rules, gives feature/resampling and bar algorithms, decision examples, durable
history/runner boundaries and replay economics. The [finalization contract](../technical/shadow-finalization.md) defines the source-level final audit producer, fixture scope and remaining external evidence limits.
Research status remains EXPERIMENTAL after this documentation and test work.
