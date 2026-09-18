# XT/QMT venue plan

Status: EXPERIMENTAL  
Applies to: `HeptaTrade/adapter_xt/`

The current adapter has an executable **HXQ1 v1 read-only client boundary**, not a qualified QMT transport. It can frame and validate bounded HXQ1 messages, bind account/service/connection epoch plus trusted account currency and a finite authorized instrument universe, perform an identity handshake, and decode typed account/position/order/trade/quote reads through an already-admitted Execution-owned exchange. The repository still has no production Windows/QMT mTLS channel, pinned QMT/Python/`xtquant` runtime, deployed sidecar, firewall policy or qualified account.

Mutation remains unavailable. `place` and `cancel` never enter the read-only exchange and fail closed; no local order ID, accepted event or submitted event is manufactured. A source-level read-only protocol client is therefore not equivalent to `transport_implemented=true`, venue advertisement or trading authorization.

The next stages are deliberately sequential:

1. qualify the real peer-pinned mTLS channel and Windows sidecar against the exact HXQ1 v1 framing/identity contract;
2. drive the already-implemented typed asset/position/order/trade/quote barriers from the real pinned QMT sidecar, add sidecar instance identity/health, and qualify reconnect/epoch semantics;
3. only after read-only qualification, add durable `venue_command_id` mutation correlation and uncertain-send recovery without creating a second order authority;
4. cover exchange price type, lot size, market hours, short-sale/account semantics, journal/risk integration, reconciliation and fault injection;
5. require broker-observed PAPER/simulation qualification before any capability promotion.

Agent and Gateway must remain unable to reach the sidecar listener or read QMT credentials. The common Execution Service remains the sole durable mutation authority. A sidecar may translate XT semantics but may not mint command identity, bypass risk/fencing, or become a second order path. See [`modules/xt-adapter.md`](modules/xt-adapter.md) and [`technical/xtqmt-adapter-contract.md`](technical/xtqmt-adapter-contract.md).
