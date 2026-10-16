# Original Broker error 202: cancelled remainder of an executed order

IBKR TWS API defines error code 202 as confirmation that an active order was
cancelled by the IB server:
https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes

The original Pocket4 BTC PAPER OMS contains one partially filled original
flatten order followed by Broker-native error 202 / Cancelled, yet without
a terminal orderStatus callback. Other unresolved BTC orders have only
PendingCancel; those are **not** terminal. An order cancellation is distinct
from both an order cancellation request and completion of the original fill.

## Narrow accounting repair

hepta_pnl_accounting.replay accepts the **original** IB error 202 callback
only for the **unfilled remainder of an already executed command**, requiring:

- The immutable original send's command ID, venue correlation and numeric
  order ID, same account, instrument, execution domain, session and side.
- The native Broker callback identity: event broker_error,
  callback error, reason IB_ERROR_202, code exactly integer 202,
  status Cancelled, native source agent:<original owner>.
- Original IB error() callback sentinels broker_client_id=-1 and
  broker_request_id=0. They are **not** substituted with the command owner's
  clientId; an independently validated full-subject execDetails event must
  supply the matching service and connection epoch.
- A valid original send timestamp <= callback timestamp <= observation time.
- At least one independently validated actual execution within the original
  correlation and callback epoch. No execution means **no zero-fill proof**.
- No later nonterminal orderStatus after the 202 callback.

If a real terminal orderStatus already exists, it still wins. The fix adds
a non-authorizing provenance record (broker_cancel_witnesses) to PnL output;
**it does not inject a synthetic orderStatus or any original OMS event**.
All actual quantities, fees, prices, book values, and execution counts are
computed by the original execution/commission code, unchanged.

## Pocket4 proof / limits

Original full OMS replay and separate live read-only native PnL collection
both show the same narrowly scoped correction: BTC historic pending commands
**4 -> 3**, precisely one matching original callback. BTC position remains
zero, total execution count remains 24, fees complete, and all BTC / EUR.USD
realized and net amounts match the prepatch PnL. EUR.USD pending count remains
zero. Regression tests include owner/epoch/order/side/clientId mismatch,
error code spoofing, zero-fill refusal, terminal callback priority, repeated
callbacks and late reactivation. No historical records are overwritten.

The remaining **three** original BTC commands still have no qualified final
Broker orderStatus or error 202. This change does not make them settled.
orders.list currently reports globally complete zero active orders with
current authoritative flat positions, but a current flat snapshot alone
cannot retroactively certify the old terminal callbacks.

**Not a maintenance-stop permit:** this read-only accounting correction does
not enforce atomic Broker-owner no-new-entry, resolve the three remaining
historical statuses, qualify a Broker-native stop or authorize a Driver stop.
The independent native kill-switch and existing RefuseManualStop remain
unmodified by this work. Source and runtime evidence remain on Pocket4.
