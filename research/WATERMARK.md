# Explicit stream watermarks

Status: EXPERIMENTAL
Implementation: `include/hepta/research/market_data.hpp`, `src/market_data.cpp`, `src/bars_main.cpp`
Tests: `../tests/research/watermark_cases.hpp`, `../tests/research/test_watermark.py`, `../tests/research/watermark_install_smoke.py`

## Contract

`BarBuilder::AdvanceWatermark(timestampUs, closed)` records the caller's promise
that no **new** tick earlier than the supplied UTC microsecond timestamp can
arrive. It is not derived from the host clock, file modification time, EOF,
TradingDay, the last observed price or an assumed exchange closing schedule.
The caller must supply coverage/completeness evidence appropriate to its source.
This API is research state only; it does not authorize a trade or certify an
Execution-owned quote, position or venue state.

The watermark must be nonnegative, no earlier than the last accepted tick and
monotonic across calls. Regression or a call after `Finish` is rejected without
changing the builder or output. An exact last-tick retry is still idempotent,
even after that tick's interval has closed. A new/conflicting tick below the
committed watermark is rejected. A tick exactly at the watermark is allowed
when it belongs to a supplied half-open session and satisfies the usual
sequence/trading-day/cumulative-volume checks.

A populated bar is emitted once, with `complete=true`, when its interval ends
at or before the watermark. OHLC, integer tick-grid prices, actual observation
count and volume remain unchanged. No empty bars, ticks, fills or liquidity are
created. The last cumulative-volume and sequence baseline survives closure;
a watermark or session break is not a feed reset. Baseline/include-first-volume
policy is still explicit, and an invalid policy enum now rejects at construction.

Daily bars (`periodUs == 0`) retain the explicit trading day's entire supplied
session span. A midday break does not end a daily bar. A large watermark can
close the currently populated bar but cannot manufacture observations for later
sessions. `Finish` still emits any remaining bar as **incomplete**, and emits
nothing when a watermark already drained it. After `Finish`, the stream cannot
be resumed. Consumers must rebuild against the new static-library headers;
this is not a binary-compatibility promise for old HeptaDLL or prior SDK objects.

## Concrete converter

```sh
hepta-research-bars ticks.csv sessions.csv 60000000 baseline --watermark-us 1789966800000000
```

The final two arguments are optional. The four-argument invocation retains its
previous EOF behavior and CSV schema. The option is applied after all input
ticks and before `Finish`; it is not an instruction to reorder or repair bad
input. An early watermark, malformed integer, overflow, missing value or
unknown option exits nonzero. All stdout is staged/untrusted until a successful
exit, because a late input error can follow an already printed complete bar.
The converter explicitly flushes stdout and reports buffered write failures
as errors instead of returning success on a full output device.

Existing CSV/BIN/XML normalizers and portfolio/order-flow schemas are unchanged.
They do not acquire an inferred watermark, new financial assumption or automatic
trading path through this addition. An application using this optional low-level
converter flag must preserve the supplied watermark and source-completeness
provenance alongside its output; the unchanged CSV schema does not encode it.

## Verification and integration choice

The existing data test executable includes boundary, rejection, duplicate,
trading-day, session-break and integer-limit cases. An independent integer
OHLC/volume oracle covers 1023 nonempty subsets of ten observations, five bar
periods and two first-volume policies (10,230 scenarios). Sixteen real-converter
unittest methods cover default EOF compatibility, explicit completion, no fake
observations, bad later input, integer limits and output failure. Scenario and
assertion counts are not counts of distinct test methods.

Standalone CTest additionally performs actual CMake installation, moves the
prefix, compiles an external `find_package(HeptaResearch)` consumer of this API,
and reruns the converter tests against the relocated executable. Root core
CTest keeps the same compiled targets and adds the tests through its existing
aggregate/discovery. The privileged runtime installation is unchanged.

This continuation uses `integration/heptadll-modular-20260919` / PR #106 as its
working integration baseline, retaining its integer-grid data contracts, legacy
normalizers, portfolio/order-flow consumers and durable strategy-request path.
The alternative PR #107 / `integration/heptadll-modular-20260920` at
`9e50eb939b129d662075da0719e175e86fb57a41` provided a useful watermark capability
comparison, not a drop-in implementation: its identically named C++ data types
have different layouts and volume/price contracts. Both source branches remain
recoverable. Their trees are not blindly combined, no second research/Execution
core is linked, and other alternative capabilities are not claimed reconciled.

Exact-head remote, full-root, installed-process and broker acceptance remain
separate from local related-source tests. CTP deferral, XT priority, permissions,
source/vendor notices and unresolved external-consumer retirement are unchanged.
Neither integration PR is merged and the original repository is not archived by
this source change. No full historical strategy/ABI/settlement parity is asserted.
