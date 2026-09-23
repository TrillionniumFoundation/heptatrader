# HeptaDLL futures intent migration boundary

Status: SOURCE CONTRACT; venue support remains separately qualified

This contract maps the legacy strategy vocabulary onto the single canonical
Tool Gateway -> Execution Service path. It does not restore `heptaBasicTradeSpi`,
a vendor API, a second OMS or direct strategy-to-broker mutation.

## Canonical mapping

A migrated futures request carries the complete `InstrumentRef` identity:
symbol, security type, exchange, optional primary exchange, contract month,
option right/strike when applicable, multiplier, trading class and local symbol.
The HTT1 request codec preserves those fields and the Execution Service protocol
binds them again before mutation.

Legacy limit DAY maps to `order_type=LMT,tif=DAY`. Legacy FAK maps to
`order_type=LMT,tif=IOC`. Legacy FOK maps to
`order_type=LMT,tif=FOK`. The supported position effects are exactly
`OPEN`, `CLOSE`, `CLOSE_TODAY` and `CLOSE_YESTERDAY`.

Legacy modes that automatically decide whether to open or close are deliberately
not represented by an `AUTO` offset. A migration owner must resolve that choice
from its authoritative strategy/account inputs before previewing an order; the
client, Gateway and venue adapter may not guess it.

## Authority boundary

The generic tool contract can express IOC/FOK and explicit position effect.
That is not venue qualification. The current IB PAPER and deterministic
simulator profiles remain DAY-only and reject nonempty position effects, so a
futures request cannot silently degrade into stock/FX semantics.

The generic IB adapter nevertheless preserves an already-qualified DAY/IOC/FOK
value through the final SDK call rather than rewriting it to DAY. IB's native
`openClose` field can represent only `OPEN -> O` and `CLOSE -> C`;
`CLOSE_TODAY` and `CLOSE_YESTERDAY` therefore fail before broker send instead
of being collapsed into `CLOSE`. This is semantic preservation only: it does
not widen the current CASH/STK PAPER profile or authorize a FUT mutation.

A future IB-futures, CTP, XT or other venue profile must implement and test its
own exact contract/offset/TIF translation, rejection behavior, risk accounting,
crash recovery, terminal reconciliation and broker evidence before it may
accept these requests.

The request semantic hash, preview-permit fingerprint, Tool Host replay identity,
decision audit fingerprint and internal Execution Service wire all bind
`position_effect`. Changing OPEN to CLOSE_TODAY or IOC to FOK is therefore a
different operation, not an idempotent retry.

## Legacy repository relation

The private HeptaDLL source remains the compatibility reference. No vendor
header, binary, recorded market data, private Git history or original manual is
copied by this contract. Repository archival remains governed by
`heptadll-lifecycle-status.json`, independently of this source capability.
