# Read-only account NAV and source-covered net return

## Scope and entry points

`heptatrader pnl --account` and `--account --format json` expose account facts
separately from strategy-period PnL. Ordinary `pnl` and `status` include both
sections. Exit status 2 means full account return is incomplete; a known NAV can
coexist with unknown return. `evidence_status` reports each prerequisite rather
than silently treating absent evidence as zero. Neither the observer nor the
reporter places, changes, cancels, reconciles or recovers an order.

The existing accounting period remains immutable. No cash reset, pending clear,
history recovery, daily rollover or backdated opening NAV occurs. A later first
NAV observation is not the reset-opening NAV and its difference is explicitly not
funding-adjusted profit.

## Sources and accounting

The paired IB account subscriptions separate currency ledgers (`ledgerAndNLV=True`)
from full account identity/readiness/NAV. This avoids the documented collision
between aggregate and currency-level `AccruedCash:USD`. No global Gateway setting
is changed. Exact account/model, request/end barriers, connection identity,
maximum collection time, period digest and stable OMS fingerprint are mandatory.
`reqPositionsMulti` is a separate exact-account read used for cost estimates.

Currency NAV reconstructs from cash, reported interest/dividend accruals and asset
values. Futures PnL already reflected in cash and virtual FX positions are not
added again. Broker aggregate and BASE ledger NAV must agree within USD 0.05.
A literal `RealCurrency=BASE` Gateway sentinel is resolved only through the
unique account-scoped aggregate NAV currency and a matching independently
identified currency ledger. Missing or conflicting identity is still rejected.
Separately rounded FX translations use a fixed USD 1 comparison bound for
components that share the current ledger exchange-rate basis, retain the precise
residual/precision bound, and never widen to fit a mismatch. Interest accruals
are the exception documented by IBKR: they are translated to base currency using
daily / transaction-time conversion rates and later reversed when posted. The
collector therefore treats current-rate translation of `AccruedCash` as a
diagnostic, reconciles the non-interest NAV at current ledger rates, and then
uses the Broker's BASE accrued-interest balance exactly once. Aggregate
`NetLiquidation` must still independently agree with BASE NAV. This is not a
threshold relaxation and cannot turn an unexplained cash, dividend, asset or
aggregate mismatch into an accepted NAV. This is a non-atomic Broker valuation
observation, not an executable liquidation quote. Unexplained components or
missing fields are rejected.

The consumer reproduces each normalised NAV from `raw-latest.json`, checks its
digest and every resulting field, and rejects mismatched two-file publication
windows. Missing, corrupt, stale, replaced-period or changed-ledger sources
cannot leave a current number. First raw and normalised observations are retained
as a pair and survive observer restart without creating a new accounting period.

Account return is:

```text
reported account return = ending NAV - opening NAV - net external cash/asset contributions
all-in account return  = reported return - documented outside-NAV liability changes
```

Booked commissions/interest/taxes and reported accruals are already in NAV; they
are not deducted again. Accrual settlement transferring a liability into cash is
not another financing expense. Current accrual balance is not the period's
interest cost. Personal taxes are not guessed from device location. This is a
PAPER account view, not evidence of real taxable trading income.

## Installing the observer

CMake runtime packaging contains all account modules plus an inert
`heptatrader-account-nav.service`. Installation never enables it or trading.
The operator supplies a private `account-observer.json`:

```json
{
  "schema": "heptatrader.account-observer-config.v1",
  "sdk_path": "/absolute/path/to/verified/local/ibapi-parent",
  "client_id": 8950
}
```

Use a nonzero client ID different from every trading client. The process is
unprivileged. The protocol send boundary allows only startApi, exact-account
account/position subscriptions and cancellation of its own read subscriptions.
The connection is fixed to `127.0.0.1:4002`. The service allows only local IPv4
traffic, reads the home directory read-only and can write only `pnl/account`.
No `Wants`, `Requires` or target dependency can activate trading services. Kernel
BPF IP filtering is defence in depth; fixed endpoint and protocol allowlist also
operate independently. No credentials are logged or embedded in the package.

The existing PnL observer remains AF_UNIX-only; it consumes the new observer's
private files. Normal account collection interval is ten seconds and the maximum
snapshot lifetime is thirty seconds. A read failure publishes unavailable state.
`pnl-current` selects an immutable observer release independent of the trading
release. Deployment and rollback may restart the PnL observers only.

## Account preflight before a trading/accounting period exists

The independent `heptatrader-account-preflight@OBSERVATION_SCOPE.service` may read
an existing reset-scoped PAPER account without creating a PnL period or fake
strategy owners. It shares the exact paired-account protocol allowlist and NAV
component/FX checks with the account observer. Operator configuration remains
`account-observer.json`; a private `observations/OBSERVATION_SCOPE/account-preflight.json`
uses `heptatrader.account-preflight-scope.v1`, the exact account, USD/PAPER scope,
creation time, reset-marker SHA256, read-only flag and excluded native client IDs.
No session bearer, new trade authorization or cash mutation is involved.

Preflight downloads and NAV use distinct `account-preflight-download.v1` and
`account-preflight-nav.v1` schemas and `observation_scope_id`, never `period_id`.
They cannot enter the ordinary period/return validation path. A successfully
valued preflight sample is still explicitly ineligible as opening NAV; the first
retained preflight download is a diagnostic reference, not an opening receipt.
It remains byte-identical across observer restart. Outputs are confined to the
existing observation namespace's `account-preflight/` directory. A changed reset,
changed scope during collection or newly created accounting period prevents
publication; the preflight process stops on lifecycle invalidation.

Installation is inert. Starting this observer or passing its restart checks is
not permission to unmask FX/BTC drivers, release the kill switch, create an
accounting period or label unavailable cash-flow/tax documents complete.

## Same-period financial evidence

A complete opening NAV must have actually been observed at the reset boundary,
including all assets and accruals. Old cash-only summaries are insufficient.
`return-inputs.json` can attach `opening_nav_raw`, `external_flows.source_sha256`
and the explicit `statement_timezone`; none of these are manufactured by the
collector. A known opening prerequisite is reported independently when flow
coverage is still missing.

Import an actual private Activity Flex XML with:

```sh
heptatrader pnl --import-flex /absolute/private/activity.xml --statement-timezone America/New_York
```

The timezone must be the actual report timezone, not assumed user residence.
The bounded parser retains original XML by hash, NAV control fields, cash row IDs,
signed values and source conversion rates. Every statement's section coverage is
bound to its own date interval. Reimport is inert; conflicting statement revisions
need reconciliation. A statement day not yet ended at import cannot certify full
coverage. Unknown fields, missing funding controls, unsupported nonzero internal
or asset transfers, invalid dates/DST ambiguity and unbalanced NAV bridges are
rejected. No overnight report is extrapolated into an intraday live gap.

The consumer reparses retained source XML, rather than trusting an editable
`activity.json` completeness flag or amount. Funding requires dated detail and
statement-control reconciliation. Files in `supporting-sources` may supply a
structured, period-bound outside-NAV liability assessment: personal tax and other
cost coverage, signed liability changes, explicit non-overlap with Broker NAV and
retained source digest are mandatory. The amounts/coverage in the supplied index
must equal that source assessment. A file hash alone does not certify a random
attachment as a tax assessment. This is operator-supplied source evidence, not
independent tax advice or cryptographic authentication of a Broker statement.

Missing opening, external flow, posted financing, posted taxes, other Broker
charges, personal tax and outside-NAV cost evidence remain individually UNKNOWN.
A known zero requires evidence; no source is never zero. The absence of a complete
return does not suppress a separately proven current NAV or available strategy
PnL. A malformed cost model or statement affects only its dependent result.

## Future liquidation scenarios

[Example assumptions](../examples/account-exit-cost-model.json) are versioned,
indicative public rates, not this account's qualified tariff. Copy to the private
`exit-cost-model.json` only as an explicit model. BTC upfront fees/minimum assume
no future rebate; FX commissions/minimum apply to actual non-USD posted cash once.
Tiny residuals are included but their exchange feasibility is not asserted.
Public account charges, such as recurring custodian fees, are not posted without
an actual charge receipt. Unsupported holdings make the estimate incomplete.

The basis/stress scenarios distinguish future commission, assumed spread and
slippage, and estimated NAV after liquidation. These are not already-incurred
costs or guaranteed fills and cannot authorize a send. Expected future financing,
personal tax and unknown charges are excluded until modelled with adequate input.

## Acceptance

Tests cover exact-account read-only protocol messages, same-period source
identity, duplicate/corrupt/partial publication, funding neutralisation, interest
accrual settlement, non-overlap taxes, per-statement coverage, source replay,
missing-as-unknown behaviour and installed module/CLI imports. Live acceptance
must separately verify current source/read coherence, observer-only restart,
expiry while the observer is stopped, and recovery without changing the period,
trading binaries, pending commands or Broker orders. A passing deployment is not
proof that missing historical financial documents exist.

Completed account downloads that fail valuation are retained as a bounded private
`rejected-raw.json`. The observer first invalidates `nav.json`, then records the
rejected input and its digest in the unavailable result. It never substitutes the
rejected observation for `raw-latest`, a first accepted observation, or a reset
opening. Failed diagnostic persistence cannot preserve a formerly valid NAV.
Transport failures without a completed download do not claim new raw evidence.
The FX thresholds, normalization rules, Broker send allowlist and period are
unchanged. This is diagnostic retention, not financial reconciliation acceptance.

Primary references:
- [IB currency/account prefix](https://www.interactivebrokers.com/docs/tws-api/doc/tws-settings/per-currency-account-value-prefix)
- [Change in NAV](https://www.ibkrguides.com/reportingreference/reportguide/changeinnav_fq.htm)
- [Interest accruals](https://www.ibkrguides.com/reportingreference/reportguide/interestaccruals.htm)
- [Public crypto commissions](https://www.interactivebrokers.com/en/pricing/commissions-cryptocurrencies.php)
- [Public spot currency commissions](https://www.interactivebrokers.com/en/pricing/commissions-spot-currencies.php)
