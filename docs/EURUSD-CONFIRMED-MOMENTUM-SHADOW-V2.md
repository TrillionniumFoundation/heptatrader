# EUR.USD confirmed-momentum SHADOW v2

Status: EXPERIMENTAL  
Applies to: research scripts listed in [`modules/shadow-research.md`](modules/shadow-research.md)

## Purpose

This is a deterministic, replayable, read-only strategy research contract. It may emit `NO_TRADE` or a non-executable bounded intent draft. It does not authorize PAPER or LIVE mutation.

## Implemented pipeline

The repository contains strict contract utilities, official-source capture/extraction/normalization, market-context construction, hash-chained sampled history and bar materialization, the EUR/USD evaluator, SHADOW receipt/state logic, and later-mark replay evaluation.

The repository does **not** currently contain a canonical `hepta_bounded_shadow_observer.py`, does not install the scripts under `/usr/libexec`, and does not provide a production campaign controller. Earlier text describing those objects as installed was aspirational and has been removed.

## Hard gates

The evaluator requires provenance-bound inputs, authoritative fresh quotes and portfolio reads, complete no-lookahead history, bounded spread/volatility/cost, flat portfolio state where required, and event/information vetoes. Any missing, stale, unsafe, unsupported, or inconsistent input results in `NO_TRADE` or a typed failure. Narrative and confidence never override a gate.

## Evidence and state

Strict JSON rejects duplicate keys and non-finite values. Inputs and outputs bind SHA-256 evidence references. Market history advances an atomic hash-chain head. Strategy state is committed only after its SHADOW receipt is durable. These properties establish reproducibility; they do not establish execution authority.

## Testing and promotion

The source boundary is checked by `tests/python/test_research_contract_smoke.py`. Full promotion requires deterministic fixture suites for extraction, history recovery, bar boundaries, no-lookahead behavior, tampering, transaction costs, missed samples, and finalization. A separate risk challenger, bounded PAPER policy, execution-side preview, protected approval, and broker-observed qualification remain mandatory before any execution path can consume a decision.
