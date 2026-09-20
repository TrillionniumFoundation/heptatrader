# Legacy XML bundles into the existing research pipeline

Status: EXPERIMENTAL; offline, single-instrument futures data only.
This is a format/behavior port, not the old executable configuration loader.
It adds no broker adapter, trading credential, order path, OMS or matching core.

## Source and destination

Format reference: `TrillionniumFoundation/HeptaDLL-main` at
`5f3703258bc4cad8f96e513d8d989c2441b4729d`:

- `heptaHeptaDLL/heptaPegasusSimulator.cpp`: `InitialSimulator` instrument
  attributes; `ReadXmlConfigFile` User/SimulatorServer/Account, Subscription,
  Result, System/Cache and MDFile list fields; `StartMarketDataServer` modes.
- `heptaHeptaDLL/heptaPegasusSimulator.h`: the ordered SIMTYPE enumeration.
- `heptaHeptaDLL/heptaTradeCommonDefine.h`: ProductClass futures is `'1'`.

The integration parent is `heptatrader@1631f9da140dd8a56b9a1e21a7ad647491c011b1`.
The original source, author notices and private history remain in their original
repository. No vendor source/binary, private fixture or old runtime is copied.
A source reference is not a new assertion of upstream redistribution clearance.

`python/hepta_research/legacy_xml.py` implements the new reader/consumer.
`legacy_ticks.normalized_report` is the extracted existing compiled-builder
entry, not a new implementation. Ordinary `tick_report` and `binary_report`
continue through that same entry. The path is:

```text
explicit XML files + explicit local source bindings + supplied UTC sessions
 -> bounded captured bytes and field validation
 -> existing CSV/BIN normalizers
 -> one concatenated canonical Tick stream
 -> the same compiled hepta-research-bars / BarBuilder
 -> the same evaluate_bars / replay / ResearchLedger
 -> the same atomic offline report writer
```

Neither XML nor a research report can grant execution permission. Strategy
execution remains on the existing unprivileged client -> Tool Gateway -> sole
Execution Service route. CTP stays deferred; XT priority and PAPER/LIVE state
are unchanged. The standalone SDK is separate from privileged runtime install.

## Supported XML profile

The root element name is not significant; root attributes and namespaces are
not accepted. Values are attributes, not element text. UTF-8 is the default;
GB18030 must be explicitly selected. A declaration, when present, must agree
with the selected encoding and XML version 1.0. UTF-8 BOM and comments are
accepted. There is no encoding fallback or native-struct/XML deserialization.

### Simulator configuration

`User` and its `SimulatorServer` are required singletons. `User.type` is
mandatory. `SimulatorServer.Front` and `.Instrument` are **opaque reference
strings**, never file paths that this parser follows.

| Mode | Consumer behavior |
|---|---|
| `0` CSV file | Exactly one explicitly bound CSV source, logical index 1 |
| `1` BIN file | Exactly one explicitly bound supported binary source, index 1 |
| `2` CSV list | Explicitly supplied MDFile list and every referenced CSV source |
| `3` BIN list | Explicitly supplied MDFile list and every referenced BIN source |
| `4` database | Rejected; the old dispatch's empty success branch is not a reader |
| `5` real-time quote | Rejected; no network or streaming process is started |
| `6` custom callback | Rejected; no user code is dynamically loaded |

Optional `Subscription/Instrument.ID` values must be unique; an explicit empty
subscription rejects. When supplied, the selected instrument must be included.
Inputs themselves must contain only the selected instrument: this consumer does
not silently filter mixed-instrument files or build a portfolio simulator.

Optional legacy settings are validated and retained under
`ignored_runtime_settings`, **not executed**:

- `SimulatorServer.Interval`: nonnegative integer metadata, not a sleep timer.
- `Account.PreBalance`: nonnegative historical value. The caller must supply
  `--capital`; the XML does not fund a simulated or real account.
- `Result/TotalResult` and `Result/InsResult`: `bSave`, `Interval`, and per-record
  `ID`. No old output/log configuration is activated.
- `System/Cache`: `Need`, `Path`, `Instrument`. The path reference is hashed;
  it is not opened, created or written. No implicit binary cache conversion runs.

Unknown attributes/children, duplicate singletons and duplicate instrument
entries reject. Missing optional values are not replaced by historical runtime
defaults. The supported boolean syntax is `true`, `false`, `1`, or `0`.

### Instrument catalog

Each direct `Instrument` requires `InstrumentID`, `ExchangeID`, `ProductID`,
`ProductClass`, `PriceTick` and `VolumeMultiple`. The selected record must have
ProductClass `1`. PriceTick and VolumeMultiple must be positive. The former
feeds the existing exact Tick conversion; the latter is the offline accounting
multiplier. This profile does not accept a competing multiplier override.

The reader retains the source reader's optional fields: `InstrumentName`,
`CreateDate`, `OpenDate`, `ExpireDate`, `Currency`, `OptionsType`,
`StartDelivDate`, `EndDelivDate`, `PositionType`, `UnderlyingInstrID`,
`UnderlyingMultiple`, `DeliveryYear`, `DeliveryMonth`, market/limit min/max
order volumes, `IsTrading`, and `StrikePrice`. Supplied date strings must be
real Gregorian dates; blank optional dates become null. Decimal values retain
exact decimal representation; integers are bounded and unsigned. Order-volume
and date intervals must not be inverted. Unknown enum characters are historical
metadata, not asserted exchange support; selecting a non-futures record rejects.

`IsTrading`, expiry, order limits and other catalog fields do not qualify a
broker, become current market rules, or authorize an order. No margin,
close-today/close-yesterday, options exercise, delivery or exchange settlement
model is inferred. Dates/session calendars remain caller-supplied data.

### File list

Direct `MDFile` elements have exactly `DateIndexId` and `FilePath`. Entries are
processed in ascending numeric index, matching the old ordered map. Duplicate
indices reject instead of silently overwriting. **An index is not an ActionDay
or TradingDay.** At most 128 files are accepted. XML element order and binding
JSON array order cannot change the processing order.

## Explicit bindings, not an authorization/control manifest

The separate UTF-8 JSON input maps each literal XML reference to a local file
explicitly chosen by the caller. This avoids executing embedded Windows paths,
relative paths, URLs or traversal strings from an old configuration. It is
ordinary data-source configuration, not another approval workflow or state store.

```json
{
  "schema": "hepta.research.xml-bindings.v1",
  "instrument_reference": "Instrument.xml",
  "front_reference": "DataFileList.xml",
  "sources": [
    {
      "index": 20,
      "reference": "historical/a.csv",
      "path": "/absolute/local/a.csv",
      "sha256": "<64 lowercase hexadecimal digits for the exact local bytes>",
      "layout": "immsg35",
      "has_header": false
    }
  ]
}
```

The digest placeholder above must be replaced with the actual SHA-256. All and
only file-list indices must be bound, and every literal reference must match.
Single-file mode instead binds index 1 directly to `SimulatorServer.Front`.
The actual catalog and optional list are separately supplied CLI paths; their
embedded reference strings never select a path to open.

CSV layouts are `hepta32`, `immsg34`, `immsg35` or `zs58`; BIN mode accepts only
`hepta-depth82-le-a8-v1`. There is no ABI/layout autodetection. CSV headers are
explicit per file. The existing [Tick contract](DATA-FORMATS.md) and
[binary contract](BINARY-IMPORT.md) continue to govern dates, grid conversion,
field counts, microseconds, padding and unqualified depth.

A file missing actual civil dates additionally binds either `action_day` or
both `action_days_path` and `action_days_sha256`. The latter covers exactly the
file's one-based local rows. No TradingDay or DateIndexId substitution occurs.
Recorded ActionDay is never overridden. Source paths must be absolute, bound
SHA-256 values lowercase, and duplicate/unknown JSON keys reject.

## Bounds, snapshots and continuity

Each XML or binding document is at most 4 MiB. XML has at most 20,001 elements,
four levels, 40 attributes per element and 4,096 characters per attribute.
DTDs, entity declarations/external entities, processing instructions, namespaces,
CDATA and non-whitespace text reject before any referenced resource is used.

Sessions, data and external dates share a 64 MiB captured-input budget. Session
and each date-map file also have a 4 MiB bound. The total Tick limit defaults to
100,000 and can only be lowered. Reads require regular files and use nonblocking,
no-follow final-component opens; they reject observed size/identity/time changes.
These are captured-file observations, not proof of a simultaneous snapshot of
all files or protection against every parent-directory race.

Only captured bytes are normalized; expected source/date digests must agree.
The supplied UTC sessions are captured once and reused. Each file's normalizer
validates its own stream. Concatenation additionally rejects time/TradingDay
regression and cumulative volume decreases within a TradingDay across files.
Global row ordinals preserve each file's local provenance; neither is a venue
sequence ID. Duplicated observations are not silently deduplicated.

One builder/strategy/ledger processes the concatenation. File boundaries do not
reset cumulative volume, open bars, signal history, position, fees or equity.
A file EOF does not close a bar; only the existing subsequent-data rule does.
The last bar remains incomplete and generates no new signal. Different trading
days use the existing explicit `baseline`/`include` first-volume policy.

Metadata hashes the configuration, catalog, bindings, optional list, sessions,
original sources, optional date maps and concatenated normalized bytes. Each
source records its global ordinal interval and original normalizer provenance.
Embedded file/cache references are hashed instead of copied as output paths.
The report remains `OFFLINE_HYPOTHETICAL`, with the existing next-bar-open fill
assumption, not Pegasus queue-matching equivalence or live account evidence.

## Command and validation

After standalone SDK installation, the relocatable `hepta-research-xml` finds
that same prefix's Python package and compiled bar executable:

```sh
hepta-research-xml --config Simulator.xml --instruments Instrument.xml \
  --file-list DataFileList.xml --bindings bindings.json --sessions sessions.csv \
  --instrument SYNTH --clock-zone UTC --period-us 60000000 \
  --first-volume include --capital 1000 --quantity 2 --fast 1 --slow 2 \
  --slippage 0.5 --fee-per-unit 0.25 --output report.json
```

Paths above identify explicit caller inputs; bound data/date paths are absolute.
The CLI rejects output aliases (including existing hardlinks) of every input and
executable. All sources, conversion and evaluation must succeed before the
existing atomic writer publishes. Pre-replace errors preserve a previous report;
a directory-fsync error after replacement is not falsely described as rollback.
A missing/failed/timed-out compiled converter is an error, never a fallback model.

`tests/research/test_legacy_xml.py` exercises real parsers/normalizers, the actual
compiled converter, exact hand-computed accounting, file-split invariance,
malformed/adversarial XML, path bindings, digests, global bounds and publication.
`xml_fixture.py` contains only synthetic data. Existing BIN/Tick tests remain.
`xml_install_smoke.py` is registered in standalone CTest: it performs CMake
installation, moves the prefix and invokes CSV/BIN single/list commands via
`python -I` without source imports, then tests late-error preservation.

The four-mode six-bar fixture has prices 10,11,12,9,8,7, multiplier 3, capital
1000, quantity 2, MA windows 1/2, price slippage 0.5 and per-unit fee 0.25. It
produces deltas +2/-4 at 12.5/7.5, total fees 1.5 and final marked equity 971.50.
The XML's historical PreBalance 99999 is deliberately not used. The final bar
is incomplete, position is -2 and no final target is generated.

## Remaining boundaries

This implements the specified XML file/catalog/list **input route**, not every
historical XML dialect, multiple instruments, native ABI, strategy or simulator
behavior. Full Pegasus queue/margin/exchange-settlement equivalence requires
its own behavior evidence rather than relabeling this simple replay. External
consumer completeness and author/vendor clearance are not established by this
port. The old repository, branches, releases and history stay intact; passing research tests
alone does not establish retirement preconditions. Root/runtime CI and installed
multi-UID/PID1/broker qualification remain separate exact-revision requirements.
