# Explicit native research model consumers

Status: EXPERIMENTAL; new consumer/report API, not historical ABI parity.
Implementation: `examples/model_cli.h` in the existing `replay_main.cpp` target,
and `model_manifest.py`. Models/accounting remain the existing Replay/Analytics
libraries. Tests: `../tests/research/model_cli_behavior.py` (source and actual
installed/relocated execution). This does not install into the trading runtime.

## JSON/JSONL consumer

The POSIX offline SDK installs `hepta-research-models`, the existing
`hepta-research-import` capture helper, and `hepta-research-replay` together.
Python 3.10 or newer is required. No external Python packages, source checkout,
PYTHONPATH, broker SDK, network connection or credentials are required.

```sh
python3 -I -S /sdk/bin/hepta-research-models order-flow \
  --manifest flow.json \
  --source first=/data/first.jsonl --source second=/data/second.jsonl \
  --output /reports/flow.json
```

The order-flow manifest accepts the public #106 schema
`hepta.research.order-flow.v1` in the native bounded common domain. It contains
exactly `schema`, `capital`, `currency`, `max_mark_age_us`, `instruments`, `sources`.
One capital and accounting currency apply to all 1..64 instruments. Each
instrument has exactly `instrument`, `tick_size`, `multiplier`, `lot`,
`fee_per_unit`, `fee_rate`. Decimal configuration may be JSON numbers or strings;
booleans, nonfinite values and configurations not round-tripping through
binary64 are rejected. Fees are nonnegative, fee rate is at most one. The adapter
selects FIFO and the explicit signed finite price domain. This is not arbitrary
Decimal arithmetic; data import/BarBuilder defaults are still positive-only.

`sources` is an ordered 1..256 list of objects with exactly `ref` and lowercase
SHA-256 `sha256`. Command-line REF=PATH bindings must match it exactly. Paths
never come from JSON fields. Captured bytes are hashed and then parsed without
reopening. Duplicate JSON keys, unknown fields, mismatched digests, final-path
symlinks, FIFOs/devices, and observed concurrent source changes are rejected.
A digest is an explicit integrity binding, not authentication of market data.

An order-flow JSONL event has `kind`, `seq`, `timestamp_us`, `instrument`, plus:

| kind | Additional exact fields |
|---|---|
| `order` | `order_id`, `actor`, `side`, `quantity`, `limit_ticks`, `time_in_force` |
| `cancel` | `order_id`, `actor`, `quantity` (zero cancels all remaining) |
| `mark` / `basis_rebase` | `price_ticks` |
| `session_end` | none |

Actors are EXTERNAL/RESEARCH; sides BUY/SELL; time-in-force GTC/DAY/IOC/FAK/FOK.
A null limit denotes a market order only where the native model permits it.
The source sequence range is 0..2^63-1; the adapter maps it to native sequence
`seq + 1` without changing timestamp or ordering. Exact retries use that same
mapping. Native generated receipt/fill identities therefore are not the old
Python report IDs. Prices are signed integer grid indices bounded by 2^40;
quantity/lot bounds are 10^12, subject to stricter arithmetic checks in the core.
Files are concatenated in declared order into ONE native model invocation;
orders, FIFO lots, cash, clocks and duplicate identities survive file boundaries.

## Next-open consumer

Use `next-open` with schema `hepta.research.next-open-input.v1`. Manifest fields
are as above plus `slippage_ticks` in 0..1000000. This is a NEW explicit event
contract, not an alias for the #106 normalized-bar portfolio CLI.

An `open` event has exactly `kind`, `instrument`, `timestamp_us`, `sequence`,
`price_ticks`, `volume`. A `target` has exactly `kind`, `target_id`, `instrument`,
`trading_day` (YYYYMMDD string), `begin_us`, `end_us`, `observed_at_us`,
`target_quantity`, `open_ticks`, `high_ticks`, `low_ticks`, `close_ticks`, `volume`,
`tick_count`, `complete` (must be true). OHLC are integer grid indices. Timestamps
are UTC microseconds; trading-day labels do not assert exchange-calendar rules.
A completed target cannot consume an open at or before its actual observation
time. Later opens close a reversal before opening its opposite exposure through
one existing ledger. Explicit opens refresh marks; a target is not a quote.

## Output, resources and failures

The output schema is **`hepta.research.native-model-report.v1`**, NOT the #106
Decimal/null report. `model` is `explicit-price-time-flow-v1` or
`observed-next-distinct-open-v1`; `broker_authorized` is always false. `fills`
contains immutable native fill identities, side, absolute quantity, price and
fee. `snapshot` contains one capital, currency, fees, realized/unrealized P&L,
equity and per-instrument positions/marks. `orders` and `active_orders` are FLOW
book summaries; NEXT does not pretend to have a resting order book.

The adapter adds manifest/source digests, per-source physical event counts and
the sequence offset under `input`; it does not publish local source paths. EOF
does not invent fills, quotes, session-end, settlement or a flat portfolio.
Required final marks must exist and be fresh. Missing/stale marks reject the
report rather than silently changing to the historical null-valuation policy.

Defaults: 100000 event rows, 4096 active orders, 64 MiB captured source bytes,
120-second native-process timeout. Explicit CLI bounds permit at most 1000000
rows, 100000 active orders and 3600 seconds. Manifest size is 1 MiB; each JSONL
line is at most 8192 bytes. Physical retries count against the row budget. Native
protocol and final report each have a 64 MiB bound. Captured data, JSON objects,
core state and the output spool consume memory/disk within these configured
bounds; this is not a constant-memory or throughput guarantee.

The complete captured input, native exit status and final report are validated
before same-directory atomic report replacement. The private output file and
its directory are fsynced. Bad later sources or native rejection leave an
existing report unchanged. Output/input/native/helper aliases and output
symlinks/special files reject. Directories and chosen executables are trusted
caller inputs; this is not an adversarial writable-directory sandbox. A failure
after atomic replacement (for example directory fsync or the final stdout
summary) can occur after the report is visible: nonzero exit is not proof that
nothing was published. No trading state is involved.

## Direct HMR1 stream

The existing binary also accepts `hepta-research-replay --model-stream` from
stdin, with no further arguments. This private CLI protocol is versioned, not
an installed C++ header or broker transport. LF, CRLF and an unterminated final
line are accepted. Blank rows, quotes, escapes and unknown fields are rejected.

```text
HMR1,FLOW|NEXT,capital,currency,maxRows,maxMarkAgeUs,slippageTicks,maxActiveOrders
I,instrument,tickSize,multiplier,lot,feePerUnit,feeRate,fifo|average,signed|positive
BEGIN
```

There are 1..64 instrument rows. FLOW has zero slippage. Event rows:

```text
A,seq,timeUs,instrument,orderId,actor,side,quantity,priceTicks|none,timeInForce
C,seq,timeUs,instrument,orderId,actor,quantity
M,seq,timeUs,instrument,priceTicks
B,seq,timeUs,instrument,priceTicks
E,seq,timeUs,instrument
T,targetId,instrument,tradingDay,beginUs,endUs,observedAtUs,targetQuantity,openTicks,highTicks,lowTicks,closeTicks,volume,tickCount
O,instrument,timeUs,sequence,priceTicks,volume
```

A/C/M/B/E are FLOW; T/O are NEXT. HMR1 sequence IDs are positive, unlike the JSON
source sequence. The native executable spools output until all rows and final
valuation pass, then checks actual stdout writes and final flush. A broken
output sink can receive partial bytes; success requires exit zero and complete
valid JSON. The file adapter above supplies atomic report replacement.

## Consumer disposition

The runnable JSON order-flow consumer now maps the selected #106 manifest/event
input to the sole native core, with explicit new report and numeric boundaries.
The native C++ consumer and old #107 replay/import/client calls remain intact.
The new next-open consumer exposes existing target/open contracts, not recovered
proprietary strategies. Original #106 Python APIs, wide Decimal arithmetic,
the old report schema and unported caller contracts remain
retained at `acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5`; #108 client record callers
remain at `56fd92bc94fd36e064d18c383ffeef9994d85fea`. No outbox conversion is
performed and no uncertain command gets a new mutation ID. Unknown external,
private and binary consumers retain their original repository/releases.

Do not archive HeptaDLL-main or declare the alternative PRs fully superseded on
this consumer port alone. Public/private redistribution, actual consumer owner
sign-off, exact-head CI and production host/broker qualification are independent.
CTP remains deferred; LIVE remains unavailable. No private history or vendor
source/binary is imported. The new adapters are ordinary unprivileged offline
SDK tools, not an additional OMS, matching/accounting framework or execution path.

## Migrated normalized-bar portfolio consumer

The SAME installed `hepta-research-models` command now accepts `portfolio`.
Unlike `next-open`, this mode deliberately reproduces #106's normalized-bar
CLOSE-before-OPEN model in the shared numeric domain. Python only validates and
captures input/provenance, refines its declared price grid and invokes the
existing native binary once. Signal calculation, event scheduling, target fills,
accounting and partial valuation all run in the existing C++ SDK libraries.

```sh
python3 -I -S /sdk/bin/hepta-research-models portfolio \
  --manifest portfolio.json \
  --source a=/data/a-bars.csv --source b=/data/b-bars.csv \
  --output /reports/portfolio.json
```

The existing #106 manifest schema `hepta.research.portfolio-input.v1` has exactly
`schema`, `currency`, `capital`, `max_mark_age_us`, and `instruments`. Each of
1..64 instruments has exactly `instrument`, `currency`, `tick_size`, `quantity`,
`multiplier`, `lot`, `slippage`, `fee_per_unit`, `fast`, `slow`, `long_only`, and
`sources`. Sources are ordered objects with exactly `ref` and `sha256`; all
references across the universe must have distinct explicit command-line
bindings. Every instrument must use the same three-letter accounting currency.
One positive capital is counted once. Costs are nonnegative; `long_only` is a
JSON boolean; quantities/lots are whole numbers in 1..10^12 with exact divisibility;
windows satisfy 1 <= fast < slow <= 100000. Fractional native quantities are
rejected, not truncated or silently converted into a different instrument.

CSV is ASCII, with this exact header and eleven unquoted fields per row:

```text
instrument,trading_day,begin_us,end_us,open,high,low,close,volume,ticks,complete
```

OHLC are signed integer SOURCE-grid indices, not decimal prices. Times are
nonnegative UTC microseconds, volume/ticks unsigned integers (ticks positive),
and completeness is `0` or `1`. Trading-day labels are validated Gregorian dates,
not inferred exchange calendars. Each source is nonempty and single-instrument;
all sources for one instrument form a single ordered, nonoverlapping stream.
A partial bar is allowed only as the final bar. Inconsistent OHLC, time/day
reversal, conflicting instrument declarations and data after a partial tail fail.
The binary64/native price domain and final arithmetic checks still apply.

Fixed-price slippage may be a fraction of the source tick. The adapter computes
the exact decimal common subgrid of tick size and slippage, rescales integer
OHLC without rounding, and sends per-instrument integer slippage to the existing
NextBarReplay. Both rescaled price indices (absolute value <= 2^40) and slippage
(<= 1000000 subgrid ticks) must fit the native contract. A nonrepresentable grid
or rescaling overflow rejects. The report records the subgrid/factor explicitly;
this is not an arbitrary Decimal-magnitude or legacy binary ABI guarantee.

All complete CLOSE(t) events precede every OPEN(t), with instrument-name ordering
within each phase. A close refreshes that instrument's observed mark and feeds
the exact integer-grid moving average. The resulting bounded target can execute
at the next contiguous open at the SAME timestamp only in this explicitly named
hypothetical model. Warmup/equal means give target zero; long-only maps negative
signals to zero. No strategy observes another instrument's future close.
An incomplete tail contributes its OPEN, possibly consuming a prior target,
but its close has no known observation time: it supplies no mark, signal or
future clock event. Its supplied close is retained as untimed metadata only.
No EOF fill, liquidation, synthetic bar, liquidity or exchange session is added.

### New portfolio report contract

Output is **`hepta.research.native-portfolio-report.v1`**, with model
`normalized-close-then-open-v1`. This is deliberately NOT a drop-in
`hepta.research.portfolio-report.v1` string-Decimal/ID schema.

`equity` contains one snapshot after all events at each actual timestamp;
`snapshot` is the final one. Each includes cash, fees, positions, and
`valuation_complete`. Missing or stale held marks make aggregate equity and
gross notional null and identify `unavailable_instruments`; affected position
unrealized P&L is null. Positions remain known. `mark_observed=false` and null
mark price/time distinguish a never-observed quote from a genuine zero price.
`valuation_gap_count` counts incomplete snapshots; any gap makes max drawdown
null, while total return is null only if final equity is unavailable. No
calendar/annualized metric is inferred (`annualized` is null).

`fills` uses canonical IDs and explicit closing/opening legs for a reversal.
To compare with the old net-delta report, aggregate quantity and fees for the
same instrument/timestamp/price. Do not compare generated fill IDs as though
there were an old order-record migration. `pending_targets` and
`untimed_incomplete_closes` describe offline terminal state. `input` records
manifest/source hashes and subgrid factors, not local paths. Both authority
flags remain false; cash may be negative without an invented funding transfer.

`--max-total-bars` defaults to 100000 and permits 1..250000 across the universe;
at most 256 sources and 4096-byte CSV rows are accepted. Common captured-input,
manifest, native timeout, private staging/atomic output and 64 MiB protocol/report
bounds above remain active. `--max-events`/`--max-active-orders` belong to the
FLOW/NEXT interface and do not enlarge portfolio capacity. A bounded count is
not a throughput guarantee: the existing whole-event native model stages state.

### Direct HPR1 input

The SAME `hepta-research-replay` binary accepts `--portfolio-stream` on stdin:

```text
HPR1,maxBars,maxMarkAgeUs,capital,currency
I,instrument,executionTickSize,multiplier,lot,feePerUnit,fast,slow,targetQuantity,longOnly0or1,slippageTicks
BEGIN
P,instrument,tradingDay,beginUs,endUs,openTicks,highTicks,lowTicks,closeTicks,volume,tickCount,complete0or1
```

HPR1 OHLC use the already-rescaled execution grid. All files are marshalled into
one invocation, retaining signals, accounting and quotes across file boundaries.
Direct native validation rejects incomplete/malformed later input before it
publishes stdout; a stdout failure may expose partial bytes and is not success.
The installed adapter supplies digest binding and atomic report replacement.

The selected #106 normalized-bar manifest/CSV/MA consumer is now adapted in this
bounded domain, including same-time phase and null-valuation behavior. Original
source-compatible Python APIs, arbitrary Decimal range, fractional quantities,
old report schemas, custom target functions and old client/outbox records remain
explicitly retained. This port does not certify every historical strategy,
platform, dataset or binary application and does not authorize source retirement.
