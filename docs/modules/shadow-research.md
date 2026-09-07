# SHADOW research pipeline

Status: EXPERIMENTAL  
Applies to: repository HEAD  
Implementation: `scripts/hepta_market_context_builder.py`, `scripts/hepta_market_evidence_normalizer.py`, `scripts/hepta_shadow_market_history.py`, `scripts/hepta_strategy_shadow_runner.py`, `scripts/hepta_strategy_replay_evaluator.py`  
Tests: `tests/python/test_research_contract_smoke.py`

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

The smoke test compiles/imports every catalogued research module and validates that no module imports broker or execution mutation adapters. Functional fixture suites should separately cover extractor formats, history recovery, bar boundaries, freshness, no-lookahead behavior, receipt tampering, transaction costs, and final audit.

## Promotion boundary

Before any PAPER canary consumes a research decision, a separate risk challenger, bounded campaign policy, execution-side preview, human/environment authorization, and broker-observed qualification are mandatory. Promotion changes the integration boundary; it is not achieved by changing the SHADOW document status alone.
