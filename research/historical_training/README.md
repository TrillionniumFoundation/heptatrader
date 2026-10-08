# Isolated historical-price research and current-$100k reward consumer

This package does not modify live trading, NAV, quote feeds, old Laya training,
existing prospective trials, or canonical promotion. A trained offline readout
is not a live strategy release. Private account data and model weights are not
part of this source tree.

## Separate label populations

`export_current100k` reuses the existing authoritative economics replay and
exact attribution importer, but admits only the registered behavior
`EURUSD_ORIGINAL_ENTRY_CONF_0.62_USD100K_REFERENCE_NOTIONAL`.
It verifies the original request, prepared command, forecast identity, actual
fills and fees, terminal evidence, and independent Decimal PnL recomputation.
Variable executed EUR quantity must equal the original prepared quantity; it is
not relabeled as the old 25,000 EUR profile. Execution-derived quantity, fill
prices, fees, and realized outcomes are never inserted into feature text.

Each snapshot is content-addressed and immutable. Re-reading/restarting cannot
create duplicate episode labels. Closed negative outcomes are retained. The
consumer writes only its own dataset/status directory, with a 256 MiB retained
state bound and 1 GiB filesystem reserve; it never prunes evidence or trains a
head automatically. The original EUR25k consumer and its registration stay
unchanged.

`consumer_launch` requires a pinned source inventory and enters the existing
Landlock/seccomp sandbox before importing the evidence reader. The installation
creates only `heptatrader-current100k-consumer.service` and `.timer`; the service
is a one-shot read-only refresh, so successful `inactive/dead` between timer
activations is expected. The sandbox proves socket creation, bearer reads and
outside-output writes are denied. The immutable installation receipt contains
release/config hashes and rollback commands. It does not restart any existing
trading service.

## Bounded IB historical download

`ib_history` is restricted to the existing local PAPER Gateway, independent
client 8984, pinned SDK bytes, and EUR/USD CASH IDEALPRO. Only START_API,
historical bars/schedules, head timestamp, and cancellation of this client's
own historical requests are available. No order or account mutation message is
allowed. Requests are serial, spaced at least 12 seconds, with per-request and
row bounds and no unbounded retry/reconnect loop.

A plan contains 1–7 chunks of 30 D, three separate MIDPOINT/BID/ASK series,
broker SCHEDULE responses, and three head-timestamp probes. Every raw response
and receipt is immutable. Resume verifies hashes before skipping previously
recorded requests, including recorded failures; it does not silently replace a
failed or revised chunk. The retained session log distinguishes API completion
from proven interval coverage.

## Coverage, partial bars and causal examples

`dataset` validates each OHLC bar and strictly increasing timestamps, parses
broker sessions with explicit time-zone alias handling, and rejects ambiguous
or nonexistent local times. It crops to the declared absolute historical
interval. A bar is eligible only when its complete five-minute interval has
ended by its own request cutoff. This test precedes duplicate comparison;
otherwise a partial last bar can conflict with its later complete version.
Equal complete duplicates are deduplicated; conflicting complete duplicates
fail closed. Missing bid/ask, crossed open/close pairs and expected-session
missing bars prevent a full-coverage claim. Weekends/session breaks are not
filled with fabricated prices. Raw responses remain unchanged.

Examples use 12 completed five-minute bars and a 15-minute forward target.
Features and both endpoints must lie on a contiguous path. The chronological
60/20/20 split purges complete feature-plus-label spans at boundaries. Training
normalization never reads validation/test statistics. Labels are historical
price observations or explicitly simulated costs, never actual execution
rewards or repairs to old prospective windows.

## Offline paired experiment

`offline_compare` registers the immutable dataset, deterministic time-stratified
sample indices (256 training, 128 validation, 128 test), both complete pinned
backbones, identical feature text, fixed projections, readout family and
validation-only hyperparameter selection before fitting. It rejects silent
truncation and runs under the existing no-network sandbox. GPU mode requires
at least 6 GiB free and caps this process at 4 GiB; no service is interrupted to
make space. English and multilingual encoders stay frozen. Only new independent
classification readouts are fit.

The numeric baseline is explicitly a NEW causal five-minute-bar profile, not
the existing live numeric checkpoint. The old checkpoint has a different
input schema, target horizon and output classes; it must not be claimed as
compared without a separately verified adapter. Both semantic-only readouts,
two fixed 50/50 mixtures, and no trade are reported. Test Brier, accuracy,
paired UTC-day bootstrap uncertainty and cost scenarios are retained, including
negative results. These are exploratory intervals, not multiplicity-adjusted
proof of prospective efficacy.

Cost scenarios use historical next-bar bid/ask opens, which are not guaranteed
fills, a declared $100k reference notional and 0.4 bps roundtrip commissions.
Changing the assumed slippage also changes the pre-entry cost gate and hence
the trade set. A fixed-trade stress must instead hold recorded time/side
identities constant and subtract the extra cost without recomputing the gate.
Neither interpretation is actual broker PnL. No result here creates a live
candidate, an on-policy monetary reward, or promotion permission.

## BTC settlement evidence

`btc_terminal_probe` reads completed-order and execution callbacks using a
separate PAPER client. It matches original account, contract, correlation and
permanent ID; it never matches solely by numeric order ID. A missing callback,
flat position, execution fee completeness or API end marker does not prove a
terminal order state. The native OMS is never rewritten by this probe.

## Running and testing

Set `PYTHONPATH=research:research/neuron_learning:scripts` from the repository.
All commands require explicit private config or output paths:

```sh
python3 -B -m historical_training.export_current100k --config /private/consumer.json
python3 -B -m historical_training.install_consumer --config /private/consumer.json --receipt /private/install.json
python3 -B -m historical_training.ib_history --config /private/history.json
python3 -B -m historical_training.dataset --source /private/raw-history --output /private/immutable-dataset
/path/to/pinned/venv/bin/python -B -m historical_training.offline_compare --config /private/experiment.json
python3 -B -m unittest discover -s research/historical_training/tests -v
python3 -B scripts/run_python_tests.py --lane core
python3 -B scripts/run_python_tests.py --lane source
```

The initial 2026-10-08 live execution produced a schedule-complete retained
180-day closed-bar dataset and an offline comparison with no semantic Brier
improvement over the new numeric baseline. These artifacts remain unpromoted.
The current100k consumer is independent; BTC missing terminal evidence, broker
valuation timestamps, account-period opening NAV/flows, legacy numeric parity,
and future unseen efficacy remain separate acceptance requirements.
