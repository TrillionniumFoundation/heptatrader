# PnL/train followthrough r2, 2026-10-14

## Same-session history readiness

Explicit opt-in bounded startup wait (0..90 seconds, original default zero).
A fresh connection must receive an exact cashhmds global 2106 connected or 2107
on-demand-idle notice. 2105 disconnected, other farms, unknown state, scoped
notices, fatal socket events and late local disconnect all deny dispatch.
The last local callback-state check is serialized with the wire send; this is
not atomicity with a remote broker. No reconnect, history retry, order method,
Gateway restart, deadline extension or historical-failure reclassification.
The new observed session is separately frozen; predecessor issued no requests.

Official reference: IB TWS API error-codes documentation, 2105/2106/2107.
Private execution evidence: ~/.local/state/heptatrader/operator-evidence/pnl-train-followthrough-20261014-r2/.
