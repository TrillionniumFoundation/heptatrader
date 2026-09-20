# Causal multi-instrument research portfolio

Status: EXPERIMENTAL
Applies to: integration/heptadll-modular-20260919
Implementation: `python/hepta_research/portfolio.py`
Tests: `../tests/research/test_portfolio.py`, `../tests/research/portfolio_install_smoke.py`

## Purpose and integration boundary

This continuation starts from integration commit
`758962734804e171b65e0a29610f9b9d7c0de28b`. The canonical and legacy provenance
remain those in [README.md](README.md). It adds a new portfolio composition of
already ported data/accounting responsibilities, not copied private history,
vendor binaries or proprietary strategies. It does not claim complete Pegasus
matching/settlement parity. The legacy repository and external consumers are
not retired by adding this consumer.

The concrete route is:

```text
per-instrument normalized bars -> existing read_bars validation
 -> CLOSE/OPEN event merge -> existing MovingAverageTarget per instrument
 -> existing ResearchLedger per instrument -> shared-cash portfolio report
```

No module imports Gateway, OMS, execution services, network or a vendor SDK.
The result is `OFFLINE_HYPOTHETICAL` and can never qualify PAPER/LIVE, submit an
order or supply authoritative runtime positions. The separate strategy client's
existing Gateway -> sole Execution Service route is unchanged. CTP remains
deferred and XT retains its existing priority.

## Input profile

`hepta-research-portfolio` takes normalized integer-tick bar CSVs with the SAME
header and validation as `pipeline.read_bars`. Each source contains one declared
instrument. A portfolio contains 1..64 instruments in one explicit accounting
currency. Each instrument has an ordered list of source references; concatenation
never resets its signal history, inventory or fees. Files cannot overlap, regress
in trading day, or follow an incomplete final bar for that instrument.

Upstream normalized-bar producers, including `hepta-research-bars`, may supply
these files. Stage/discard their output on a nonzero producer exit. Raw Tick,
legacy BIN/XML and single-instrument report JSON are NOT silently reinterpreted
as normalized bar CSV. A mixed-symbol file is still rejected by `read_bars`;
multi-instrument composition is explicit in the portfolio manifest.

The manifest has exact keys; unknown/missing fields, duplicate JSON keys,
nonfinite numbers, duplicate instruments and repeated source references reject.
Example (replace the symbolic digest below with the real lowercase SHA-256):

```json
{
  "schema": "hepta.research.portfolio-input.v1",
  "currency": "USD",
  "capital": "1000",
  "max_mark_age_us": 60000000,
  "instruments": [{
    "instrument": "A",
    "currency": "USD",
    "tick_size": "0.01",
    "quantity": "2",
    "multiplier": "3",
    "lot": "1",
    "slippage": "0.01",
    "fee_per_unit": "0.25",
    "fast": 1,
    "slow": 2,
    "long_only": false,
    "sources": [{"ref": "a-bars", "sha256": "REPLACE_WITH_64_LOWERCASE_HEX_DIGITS"}]
  }]
}
```

```sh
hepta-research-portfolio --manifest portfolio.json \
  --source a-bars=/absolute/path/a-bars.csv --output portfolio-report.json
```

Add `--source` for every declared reference. There are no implicit source paths
or downloads; unused and missing bindings reject. Manifest source references are
inert identities, not executable paths. Source bytes are captured from bounded
regular files with final-component no-follow/nonblocking opens; FIFOs, devices,
observed capture changes and digest mismatches reject. Explicit parent directories
are caller-selected, not an asserted hostile-filesystem sandbox. The parser uses
those SAME captured bytes; the report records their digests, not local file paths.

The manifest is bounded to 1 MiB; sources to 256 and a cumulative 64 MiB; total
bars default to 100,000 and may be explicitly bounded up to 1,000,000. Decimal
inputs retain the existing 18 fractional-digit / magnitude 1e18 limits. Arithmetic
uses local precision 128 and rejects overflow rather than repairing it. All
currencies must match; the three-letter code is an accounting label, not an
assertion that a market or currency is currently supported. No implicit FX exists.

## Causality and incomplete bars

The clock merges events rather than sorting whole bars by beginning timestamp.
At the same timestamp ALL complete CLOSE events precede OPEN events, each phase
ordered by instrument identity. A completed bar may generate a target, and that
target can fill only at the NEXT bar's opening for that same instrument. Another
instrument's events cannot consume it. Same-timestamp close-to-next-open fills
retain the pre-existing idealized next-bar assumption; they do not claim zero
latency is achievable by a deployed strategy.

An opening is assigned the normalized bar's `begin_us`, a modeling convention,
not proof that a real trade occurred at the exact bucket boundary. Complete
closing marks are available only at `end_us`. This prevents a long-duration bar's
future closing price from appearing in an earlier cross-instrument valuation.
Targets are independent pure per-instrument functions; this API does not promise
rollback of arbitrary side effects inside a user-supplied callback. The built-in
reference moving average remains an example, not recovered legacy strategy parity.

An incomplete bar can consume a preceding completed target at its opening, but
never generates a signal. Its supplied close has no proven observation time, so
it is retained in `untimed_incomplete_closes` and EXCLUDED from the clock/equity.
Its nominal end is not used to refresh a mark. No final liquidation or artificial
final target execution is performed. This deliberately differs from the old
single-stream report's untimed terminal marking; that old API remains unchanged.

## One portfolio capital, reused ledger accounting

Each component uses the unchanged `ResearchLedger` fill and inventory arithmetic.
Its initialization is a computational basis, not an additional capital allocation:

```text
shared_cash = capital + sum(component.cash - component.initial_basis)
equity      = shared_cash + sum(quantity * latest_price * multiplier)
fees        = sum(component.fees)
```

Initial capital appears exactly ONCE. Fixed slippage is in price units and applies
against the fill direction; fees are per absolute quantity in accounting currency.
Prices may be negative for historical futures. Lot/target/long-only restrictions
are research input bounds, not broker risk approval. Negative cash/equity remains
negative: there is no automatic funding, margin simulation, financing, exchange
settlement or live acceptance. Different-currency positions require a separately
defined FX model and are rejected by this profile.

A held position whose last known mark exceeds `max_mark_age_us` makes that
valuation incomplete. `equity` and `gross_notional` become null with explicit
`stale_instruments`; a stale mark is not silently carried forward indefinitely.
Flat positions need no mark. A later valid quote restores valuation, but a gap
still suppresses the full-series maximum-drawdown claim. Final return is null
when the final valuation is unavailable. The union event clock is irregular:
annualized ratios are not invented. Reports retain gaps, timestamps, costs,
pending targets, per-instrument models and provenance.

## Validation and installation

The existing `hepta_research_python_tests` discovers the new tests in both root
and standalone builds. No earlier test or CI gate is removed. Differential cases
compare fills/positions/equity against the SAME existing replay/ledger on complete
streams; fixed-seed scenarios are repeated cases, not additional unittest methods.
Tests exercise event causality, stale marks, exact decimal/lot bounds, negative
prices, reversal, shared capital, every fixture file split, invalid sources and
atomic report preservation.

The standalone SDK installs `hepta-research-portfolio`, Python modules and this
contract. A new CTest performs actual `cmake --install`, moves the prefix, invokes
`python -I` in an unrelated directory with no PYTHONPATH, checks exact accounting,
and verifies corrupt-input rejection preserves an existing output. The root
privileged-runtime installation is unchanged. Manually staging a local prefix
can exercise the launcher but is NOT equivalent to the CMake-install acceptance.

The two-instrument synthetic fixture uses A prices 10,11,12,9,8,7 and B prices
20,19,18,21,22,23 on staggered clocks. One capital 1000, A quantity 2/multiplier 3/
slippage 0.5/fee 0.25 and B quantity 1/multiplier 2/slippage 0.25/fee 0.1 produce
four hypothetical fills at times 20,25,40,45, total fees 1.80, final positions
A=-2/B=1 and marked equity 963.70. These tiny timestamps are synthetic test units,
not actual market sessions. Final incomplete closes never create events at 60/65.

Full Pegasus queue/partial-fill/FAK/FOK semantics, exchange settlement/margin,
raw mixed-instrument legacy import, other binary/XML dialects and proprietary
strategy equivalence remain separate work. External-consumer census and source/
SDK redistribution clearance are not inferred from these tests. Do not archive
the legacy repository or label the overall migration complete on this evidence.
