# HeptaTrader PAPER PnL Watch: byte-verified OMS parse cache (2026-10-15)

This change is read-only and affects only the --watch producer. It is not a trading, accounting-period reset, broker reconciliation or model-qualification change.

## Data boundary

- Existing read_private() still opens the complete original OMS journal on EVERY tick with O_NOFOLLOW, owner, nlink, mode, size and concurrent-change checks. collect() still rechecks the original journal and period fingerprint after native observations.
- IncrementalOMSParser computes SHA256 over the full raw bytes on every tick. Identical size AND digest reuse in-memory strict-JSON objects, not old broker quotes or old PnL output.
- Appending rows requires SHA256 of the exact original byte prefix to match the prior digest. Only complete appended JSONL rows are parsed. Any truncation, same-size rewrite, identity change in source content or changed prefix forces full strict parsing.
- Malformed JSON, duplicate keys, non-finite values, incomplete tail and record count bounds remain fail-closed; a rejected parse cannot mutate the last successful cache.
- The collector still recomputes the PnL report and fresh native read-only broker observations every cycle. No source file is rewritten or pruned, and no cache survives process restart.

## Isolated tests

ROG worktree tests/python/test_pnl*.py: 74/74 PASS, including nine new cache tests. A synthetic exact-account PAPER round trip was evaluated through both the original uncached collector and the cached collector, and produced equal reports before and after a true append.

Pocket4 isolated in-memory read-only baseline using a single actually read original OMS source:
- Source bytes: 36,588,494; original strict JSON events: 31,381.
- Original full decode: 0.2395 seconds.
- First bounded cached parse: 0.2663 seconds.
- Verified identical-byte cache-hit parse: 0.0147 seconds.
- Complete parsed objects equal at the same source input. No broker request, order or production unit mutation by this measurement.

This measures only the JSON decoding segment and SHA verification, NOT overall PnL producer tick wall time or eventual CPU reduction under a running service. The append path was covered with exact-byte fixtures and equality of complete accounting output.

## Deployment gate

Do not patch the immutable installed PnL source in place or change the read-only observer unit until the new exact source hash is staged, audited and tested under its original unit privilege. Production service remains unchanged during this work. Separate full account NAV and cost coverage remain unproven and must not be inferred from this research result.
