# Explicit order-flow matching and FIFO attribution

Status: EXPERIMENTAL / OFFLINE ONLY  
Applies to: `integration/heptadll-modular-20260919`  
Implementation: `python/hepta_research/fifo.py`, `matching.py`, `order_flow.py`  
Tests: `tests/research/test_fifo.py`, `test_matching.py`, `test_order_flow.py`,
`order_flow_install_smoke.py` (paths relative to repository root)

## Scope and source boundary

This continuation follows public integration parent
`359d1c0e2b8c124a36e49f02da1a1ac55ea046af`, canonical runtime baseline
`5615b3ddb6badb1967771724d53b89c7ad194ddc`, and the pinned legacy reference
`HeptaDLL-main@5f3703258bc4cad8f96e513d8d989c2441b4729d`.

The legacy `heptaPegasusSimulator` owns matching queues; `heptaSettlement` owns
net-position FIFO costs and realized/unrealized profit calculations. Those are
useful responsibilities, not evidence of an exchange-qualified settlement or
margin implementation. The new interfaces here re-express those responsibilities
without copying the old SPI, account state, process controls, native structs,
SDK binaries or private history. Existing author/vendor notices and unresolved
redistribution questions are not erased or presumed resolved by this work.

**This profile consumes explicit order arrivals/cancels. It does not recover a
historical queue from Level-1/Level-2 snapshots. It is not full Pegasus behavior
or a new production Execution adapter.** Raw CSV/BIN/XML normalizers and the
existing next-bar replay continue unchanged. No automatic conversion from their
quote snapshots into fictional external orders is provided.

The dependency direction is:

```text
explicit hypothetical order flow -> matching -> FifoAccount -> ResearchLedger
                                                  |
                                           P&L attribution
```

`ResearchLedger` remains the only cash/inventory arithmetic implementation.
`FifoAccount` adds immutable FIFO lot detail. Existing bounded file capture,
strict JSON key handling and atomic report publication are reused from
`portfolio.py` and `pipeline.py`; there is no parallel I/O or execution framework.
No Gateway import, broker socket, credential reader, live position authority or
runtime installation entry is introduced. Ordinary strategy-client requests
continue through the existing Tool Gateway and sole Execution Service.

## Matching contract

`OrderFlowReplay` is a bounded, single-writer, deterministic offline replay.
Every event has a globally strictly increasing integer `seq` and a nondecreasing
integer `timestamp_us`. Sequence is the tie-breaker for equal timestamps; it is
not reset per instrument or input file. The clock is caller-supplied elapsed
microseconds, not an inferred exchange calendar. IDs match
`[A-Za-z0-9_.:-]{1,128}` and an order ID can never be reused, even after expiry.

Resting orders execute by best price, then arrival sequence, at the resting
order's limit price. Quantities are positive integer units, exact multiples of
the instrument's explicit `lot`. Prices are signed integer ticks multiplied by
an exact decimal `tick_size`; negative historical prices are allowed. All
amounts retain the existing finite, magnitude and decimal-scale bounds.

| Time in force | Defined behavior |
|---|---|
| `GTC` | Walk crossing liquidity; rest any remainder. |
| `DAY` | As GTC, but an explicit `session_end` for that instrument expires the remainder. |
| `IOC` / `FAK` | Walk available crossing liquidity, then cancel the remainder; FAK is normalized to IOC. |
| `FOK` | Preflight the complete walk; either fill the entire quantity or cancel it without consuming any liquidity or charging a fee. |

`limit_ticks: null` is an unpriced immediate request and is accepted only for
IOC/FAK/FOK. It never rests. Finite limits require exact signed 64-bit tick
integers. There is no implicit slippage on top of maker-price execution; multiple
price levels supply the explicit execution prices.

`EXTERNAL` and `RESEARCH` orders use the same queue. Earlier external volume must
be consumed before a research order at the same price. External/external trades
remove liquidity but create **no research inventory, cash or fee**. A research
order cannot trade against another research order: the aggressor's remaining
quantity is canceled and the resting research order remains. FOK preflights this
condition too. For non-FOK requests, valid earlier external fills remain valid
before a later self-cross stops the walk. One `RESEARCH` actor represents the
net research account; multi-strategy self-trade groups are not inferred.

Partial cancellation preserves the original arrival sequence. Full cancellation
uses `quantity: 0`; a later full-cancel of an already terminal order is an explicit
no-op. Unknown identity, wrong actor/instrument, off-lot or excess cancellation
rejects. Expiry affects only DAY orders of the named instrument, including
external DAY orders. GTC and other instruments are unaffected.

Each event preflights the affected queue, FIFO/ledger, aggregate arithmetic and
resource limits before committing. Invalid events leave the previous state
unchanged. `quantity = remaining + filled + canceled` is enforced on each changed
order. FOK failure is a valid terminal event, not an exception or a fictitious
fill. Future liquidity never retroactively fills an earlier expired order.

## Accounting and valuation

Capital is counted once across all instruments in the declared accounting
currency. Component ledger initial bases are subtracted before aggregation, as
in the existing portfolio consumer. Signed net inventory, multipliers and fees
remain explicit. The per-fill fee is:

```text
quantity * (fee_per_unit + abs(price) * multiplier * fee_rate)
```

The notional fee rate is in `[0,1]`; fees are nonnegative even for negative
historical prices. No missing fee schedule is inferred from a broker or SDK.
The model does not enforce cash funding, margin availability, regulatory
limits or automatic liquidation. Negative hypothetical balances remain visible.

FIFO closes the oldest opposing net lots first and supports partial closes and
reversals. Equal adjacent entry prices can be compacted without changing FIFO
P&L. The identity checked against the original cash/inventory ledger is:

```text
equity = initial capital + realized P&L + unrealized P&L - fees
```

A `mark` event supplies an explicit valuation price. A missing or stale mark for
any held instrument makes aggregate equity, unrealized P&L and gross notional
null and identifies the stale instruments. Flat instruments need no mark. A
trade does not invent an authoritative valuation observation. A later fresh
mark can restore valuation; there is no automatic annualization of the irregular
event clock.

`basis_rebase` is **research attribution only**: move current unrealized P&L into
realized P&L and reset remaining FIFO cost basis at the supplied mark. It updates
the explicit mark but does not transfer cash, change quantity, charge fees or
change total equity. Its receipt reports `cash_transfer: 0`. It is deliberately
not called exchange settlement: variation-margin cash, hedge/close-today buckets,
margin calls, FX and actual settlement calendars remain outside this profile.

## Input and installed command

The installed command is `hepta-research-order-flow`. The manifest is bounded
strict UTF-8 JSON with **exactly** these fields:

```json
{
  "schema": "hepta.research.order-flow.v1",
  "capital": "1000",
  "currency": "USD",
  "max_mark_age_us": 1000000,
  "instruments": [
    {"instrument": "TEST", "tick_size": "0.5", "multiplier": "2", "lot": 1,
     "fee_per_unit": "0.1", "fee_rate": "0"}
  ],
  "sources": [{"ref": "flow", "sha256": "<actual 64-character lowercase SHA-256>"}]
}
```

The digest placeholder must be replaced by the hash of the actual JSONL bytes.
All instruments use the manifest's currency; no automatic currency conversion
is available. Decimal parameters may be quoted exact decimal strings. Integer
fields must be JSON integers, not booleans or floating-point approximations.

Each JSONL event has the common envelope `kind`, `seq`, `timestamp_us`,
`instrument`, plus exactly the fields shown here:

```json
{"kind":"order","seq":1,"timestamp_us":10,"instrument":"TEST","order_id":"external-ask","actor":"EXTERNAL","side":"SELL","quantity":2,"limit_ticks":20,"time_in_force":"GTC"}
{"kind":"order","seq":2,"timestamp_us":11,"instrument":"TEST","order_id":"research-buy","actor":"RESEARCH","side":"BUY","quantity":3,"limit_ticks":20,"time_in_force":"FAK"}
{"kind":"mark","seq":3,"timestamp_us":12,"instrument":"TEST","price_ticks":22}
{"kind":"cancel","seq":4,"timestamp_us":13,"instrument":"TEST","order_id":"research-buy","actor":"RESEARCH","quantity":0}
{"kind":"basis_rebase","seq":5,"timestamp_us":14,"instrument":"TEST","price_ticks":22}
{"kind":"session_end","seq":6,"timestamp_us":15,"instrument":"TEST"}
```

Invoke with explicit local source bindings:

```sh
hepta-research-order-flow --manifest flow-manifest.json \
  --source flow=/local/research/flow.jsonl --output report.json
```

Source references are inert: the manifest never opens an embedded path or URL.
Multiple files may contain mixed instruments in this **explicit event schema**;
their ordered concatenation feeds the same engine. File boundaries never reset
queues, IDs, positions, fee state or FIFO bases. Sorting is not used to repair
regressing sequences or timestamps.

The existing capture helper rejects final symlinks, FIFO/device inputs, oversized
files and observed capture changes. The same captured bytes are hashed and
parsed. Unknown/missing keys, duplicate JSON keys, invalid UTF-8, nonfinite
numbers, empty/overlong event lines, unknown instruments, digest mismatches and
unused/missing bindings reject. CRLF and a final line without a newline are
supported; blank event lines and embedded bare CR are rejected.

Default limits are 100,000 events, 100,000 matched pairs, 4,096 active orders,
64 MiB aggregate source bytes, 1 MiB manifest and 8,192 bytes per event line.
CLI bounds can be tightened; hard ceilings are 1,000,000 events/matches and
100,000 active orders, with at most 64 instruments and 256 sources. FIFO has a
10,000-lot bound per instrument in this consumer. Quantities are bounded by
`10^12`, sequence/timestamps by signed 64-bit positive range (zero allowed).
The engine retains bounded event/order/trade history and sorts the eligible
active queue per arrival. It is a research reference, not a low-latency venue
implementation or an unbounded data service.

Reports contain source references, exact input SHA-256 values, assumptions,
bounds, orders, matched pairs, research fills and final attribution. Local input
paths are not copied into the report. Publication reuses `write_report`: no
partial report is published if any later file/event fails. Existing output is
preserved on pre-replace failures. As before, a directory-fsync error after
replace is reported, not falsely described as a rollback. Output must not alias
the manifest or any source, including existing hardlinks/symlinks.

## Tests and acceptance

The three `test_*.py` files are picked up by existing root/standalone research
unittest discovery. No existing tests, workflow checks or runtime permission
boundaries are removed. `hepta_research_order_flow_install_tests` is a standalone
CTest which performs actual CMake installation, prefix relocation, isolated
`python -I` command execution and output-preserving corrupt-input rejection.
The research-only install remains separate from privileged runtime installation.

```sh
cmake -S research -B build/research -DCMAKE_BUILD_TYPE=Release
cmake --build build/research --parallel 2
ctest --test-dir build/research --output-on-failure
PYTHONPATH=research/python python3 -B -X dev -W error \
  -m unittest discover -s tests/research -p 'test_*.py'
```

The matching tests include an independent slow queue reference and differential
checks against the unchanged `ResearchLedger`. FIFO tests include an independent
uncompressed per-unit cost reference. Every split point of the synthetic
15-event multi-instrument fixture yields the same state as the single-file run.
The fixture counts capital 1000 once, produces four research fill records,
fees 0.8, A/B net positions 2/2 and marked equity 1011.2. These are synthetic
accounting units and tests, not evidence of strategy performance.

This addition closes the explicit price/time queue, partial-fill, FAK/IOC/FOK,
DAY-expiry and net FIFO attribution profile. It does **not** certify other
Pegasus fill assumptions, inferred queue position from raw legacy ticks,
exchange margin/settlement, proprietary strategy parity, SDK redistribution,
external-consumer retirement or repository archival. CTP remains deferred,
XT priority is unchanged, and PAPER/LIVE authorization is not altered.
