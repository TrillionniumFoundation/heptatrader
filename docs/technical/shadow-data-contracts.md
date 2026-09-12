# SHADOW data pipeline: developer contracts and behavioral fixtures

Status: CURRENT
Scope: documentation of the EXPERIMENTAL, read-only SHADOW implementation; no promotion of module status
Implementation: `scripts/hepta_strategy_contracts.py`, capture/extractor/normalizer/context/history/strategy/shadow/replay modules listed in `docs/module-catalog.json`
Tests: `tests/python/test_research_contract_smoke.py`, `tests/python/test_shadow_data_behavior.py`

## Processing graph and ownership

The pipeline has two evidence branches. Official HTTPS capture → offline extraction → normalization supplies calendar/information evidence. Separately, authoritative WATCH snapshots → append-only sampled history → quote/bar materialization supplies observed market state. The context builder combines these inputs with the bound strategy configuration. The strategy evaluator proposes `TRADE` (a non-executable intent) or `NO_TRADE`; the SHADOW runner validates/publishes a receipt before advancing its state. The replay evaluator consumes sealed decisions and independently observed later marks.

No stage has a broker placement API or session-provisioning authority. The root-owned capture seam is separate from Agent decision code; its privilege is for retained-source provenance, not trade approval. These helpers are source utilities, not a claimed installed `/usr/libexec` observer service.

| Component | Callable entry / contract | Output and failure boundary |
|---|---|---|
| `hepta_official_source_capture.py` | HTTPS transport for the fixed official source set; retain exact bytes and transport metadata | `hepta.official-source-root-capture-receipt.v1`; rejects redirects and oversized responses. A fetched page is not itself a semantic completeness claim. |
| `hepta_market_official_source_extractor.py` | Offline extraction from `hepta.official-source-capture-manifest.v1` and retained payloads | Extraction receipt `hepta.market-source-extraction-receipt.v1` plus `hepta.market-source-bundle.v2`; URL/source format or completeness-rule drift fails closed. |
| `hepta_market_evidence_normalizer.py` | Validate bundle, extraction code/receipt hashes, retained source bytes and completeness evidence | Calendar/information documents with attestation; do not substitute narrative confidence for missing source coverage. Exact accepted version-specific fields are its `*_FIELDS` constants. |
| `hepta_shadow_market_history.py` | `append_snapshot`, `load_history`, `recover_history_head`, `audit_history`, `materialize_bars` | Hash-bound records/head, sampled quote history v3 and bar history; invalid predecessor/authority/cadence or changed bytes is not an empty valid history. |
| `hepta_market_context_builder.py` | `build_packet(campaign_id, iteration, evaluated_at_ms, strategy_path, snapshot_path, ...history/calendar/information paths)` | A bound information packet: source file/body digests, strategy/code identity, authority/completeness, freshness, calculated features and portfolio context. |
| `hepta_eurusd_confirmed_momentum_strategy.py` | `load_strategy`, `strategy_package_digest`, `evaluate` | Deterministic vetoes/regime/cost evaluation followed by non-executable intent or `NO_TRADE`. |
| `hepta_strategy_shadow_runner.py` | `run_shadow_iteration` with explicit policy, strategy, inputs, receipt and state paths | `hepta.strategy-shadow-state.v2`, receipt and idempotency result; rejects path collisions, slot/policy drift, changed inputs or out-of-sequence iteration. |
| `hepta_strategy_replay_evaluator.py` | `seal_decision_set`, `seal_mark_set`, `evaluate_replay` | Evidence-bound hypothetical execution outcomes and economics; not broker fills and not campaign authorization. |

Use `python3 scripts/<module>.py --help` for CLI argument spelling, and the named function/field constants for exact schema versions. These versioned schemas are deliberately not collapsed into a permissive common dictionary.

## Common numeric, JSON and digest contract

`load_document` requires a JSON object, strict UTF-8, no duplicate keys and a bounded document (default 4 MiB). Literal NaN/Infinity, exponent overflow such as `1e999`, and nonzero values that underflow to binary64 zero such as `1e-999` are rejected. An actual zero such as `0e-999` remains valid. `require_number` rejects booleans, non-finite programmatic values and integers too large to convert, then applies the field's positivity/range constraints. Absence is not observed zero.

Canonical bytes are sorted-key, ASCII-escaped, compact JSON followed by a newline, with NaN disabled. Digests use `sha256:` plus 64 lowercase hex characters. Distinguish the hash of retained file bytes from the canonical document/body hash; never silently replace one with the other. `atomic_write_json` synchronizes the file before replacement and then the containing directory. This does not make a sequence of independently written files one transaction.

## Time semantics, sampled bars and no-lookahead resampling

All `*_ms` timestamps are integer epoch milliseconds. Source publication, quote observation, capture completion and evaluation time are different fields, not interchangeable clocks. Snapshot v2 includes collection start/finish and per-read completion timestamps. Quote history v3 records both `observed_at_ms` and `captured_at_ms`, as well as `quote_changed`; repeatedly capturing the same quote must not increase the independent update count.

`_resample_quotes` anchors its grid at the last observation, and uses the last observed quote at or before each target (`bisect_right - 1`). It rejects an uncovered/stale grid point instead of selecting the nearest future quote or interpolating across a gap. Inputs must already have passed ordering, freshness and provenance validation; calling this internal function is not a substitute for `build_packet` validation.

`_quote_bar` assigns samples by collection-start membership in `[start, start + interval)`, sets `finished_at_ms` to the last inclusive millisecond, and reports bid/ask-midpoint OHLC, sample count, expected count, coverage and gap reasons. A sample exactly on the next boundary belongs to the next bar. A capture gap or missing edge makes the bar incomplete. Five-minute aggregation needs five complete minute bars; an incomplete aggregate has no usable OHLC. Consumers must also enforce evidence availability by evaluation time; a bar's interval label alone does not prove all underlying observations were available then.

The new fixtures cover grid selection, independent updates, half-open interval membership, gaps, incomplete aggregation and digest tampering. They do **not** prove the complete capture-to-decision pipeline is free of every temporal leak, or validate all official-provider formats.

## State transition and crash recovery

A SHADOW iteration checks the policy's slot and strategy binding, hashes all inputs before/after evaluation, and obtains the state lock. For iteration `n`, state must be at `n-1`, or at `n` with an exactly matching receipt/result. Publication order is receipt → reload/validate receipt → atomic state advance. A crash after receipt publication but before state advance can reuse only an exactly matching receipt; it must not overwrite conflicting evidence. If state already records `n`, a retry is idempotent only when decision ID, outcome, packet digest, receipt digest and receipt bytes all agree.

History head recovery is an explicit operation over immutable hash-linked records, not permission to ignore a bad record. Keep cadence, authority identity, owner/path checks and storage bounds. There is no new “all gaps closed” status generated by this document. Unresolved provider/recovery fixtures belong in GitHub Issues, not a second green-only register.

## Replay economics and acceptance matrix

BUY entry uses ask plus adverse slippage; SELL entry uses bid minus adverse slippage. Effective entry slippage is the larger of configured basis-point slippage and intent slippage. A worse-than-limit entry is unfilled, not rounded into a fill. Exit uses the opposite touch plus adverse slippage. Thus a flat market still pays spread and slippage. Holding is bounded by both intent and evaluation configuration; overlapping trade windows are rejected by default. Later marks, latency, expiry, missing evidence and transaction-cost assumptions must remain explicit.

| Contract | Existing executable evidence | Still outside these fixtures |
|---|---|---|
| Module import/no broker SDK dependency | `test_research_contract_smoke.py` | Semantic correctness or no arbitrary dynamic mutation capability |
| JSON overflow/underflow, numeric types, canonical hash sensitivity | `test_shadow_data_behavior.py` | Exhaustive fuzzing of every domain schema |
| No-future quote grid selection, update independence, sampled bar boundaries/gaps/tampering | `test_shadow_data_behavior.py` | Full retained-source capture and replay over a production history corpus |
| Spread/slippage, limit rejection, holding/overlap and drawdown arithmetic | `test_shadow_data_behavior.py` | Real market liquidity, profitability, fill probabilities or broker qualification |
| Full iteration crash windows and every provider's changing formats | The implementation defines rejection rules | Requires dedicated fixture campaigns; no completeness claim is made here |

Run the local behavioral subset with `python3 -m unittest discover -s tests/python -p 'test_shadow_data_behavior.py'`. The ordinary core Python partition also discovers it automatically. Promotion to PAPER is a separate explicit integration/qualification task; changing a JSON flag or passing these tests does not grant it.
