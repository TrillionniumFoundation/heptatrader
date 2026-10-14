# HeptaTrader

Current [development and evidence-reuse policy](docs/modules/repository-control.md) separates source integration, isolated research, product integration, model selection and trading authority. Dated campaign reports do not add ordinary merge prerequisites.

The [development index](docs/index.md) owns architecture, module contracts and operations.
For a broker-disabled native development build, run `./scripts/dev_core.sh`.
For lease/audit/OMS-only feedback, run `./scripts/dev_core.sh --storage`; this is not full core acceptance.
Python partitions use `python3 scripts/run_python_tests.py --lane core` or `--lane source`.

The simulator is the default development venue. IB PAPER still requires independent
exact-artifact qualification; LIVE is unavailable. Source checks never grant trading authority.


### Current PAPER PnL

Use `heptatrader pnl` (or `heptatrader pnl --format json`) for realized, floating,
total and execution-commission-adjusted PnL. The driver's historical `gross_pnl`
is realized-only and must not be shown as current profit. See the
[read-only PnL and accounting-period contract](docs/technical/user-paper-runtime.md#current-pnl-explicit-period-read-only-reporting)
for reset attachment, freshness, fee completeness and account-NAV boundaries.


Use `heptatrader pnl --account` for current Broker NAV and individually reported
missing account-return evidence. See [read-only account operations](docs/technical/account-pnl.md).
