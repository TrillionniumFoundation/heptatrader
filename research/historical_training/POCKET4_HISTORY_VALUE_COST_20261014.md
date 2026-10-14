# Pocket4 historical data, Value replay and cost research — 2026-10-14

This lane is **research-only**. It never submits or authorizes a broker order.

## Value
- `value_ledger_stream.stream_execution_index()` processes exactly a SHA256-pinned
  OMS byte prefix, even when the append-only source grows beyond the old 32 MiB
  full-file reader limit. File ownership/mode, duplicate JSON keys, complete
  record boundaries and the pinned hash are checked.
- Frozen Value trial3 evaluator/runtime and its existing signed plan are **not**
  rewritten by this helper. A separate, explicitly **unsigned diagnostic**
  replay confirmed the old prefix and reconstructed 173 original commands,
  56 scored, 117 unresolved/censored as of this session. It remains PENDING.
  Do not accept an unsigned diagnostic as the trial3 evaluator's signature.
- `value_request_trigger` watches new original EUR/USD REQUEST journal rows
  and starts the original, frozen predictor only for fresh, flat-position
  requests. The predictor retains all admission, provenance and deadline checks;
  a watcher event is not proof of a prediction. The installed service ends at
  the original trial3 input deadline. Original one-second timer is disabled.

## IB historical FX data
- `ib_history` has an **opt-in** one-request HEAD probe when the named
  cashhmds farm remains UNKNOWN after a finite wait; a named 2105 DISCONNECTED
  state still refuses dispatch. Positive HEAD response is required before
  more requests. Request-scoped errors stop the session with no automatic retry.
- Actual PAPER Gateway request on 2026-10-14 returned IB 162 / Request Timed Out,
  before any new bars were obtained. Thus **no Oct 13 incremental completion**.
  The previously complete joined historical dataset remains unchanged.
- Earlier IB HEAD responses reported earliest BID/ASK/MIDPOINT timestamp
  1110342600; it does not prove that every intervening 5m bar is available.

## Cost-sensitive offline development
- `cost_horizon_study` consumes the frozen 43,538-bar EUR/USD 5-minute
  BID/ASK/MIDPOINT joined set. Bar-close features predict a next-bar-open
  simulated fill and liquidation 15/30/60 minutes later.
- Four chronological rolling folds; train-label embargo and nonoverlapping
  test positions. Ridge/Momentum/Mean-reversion thresholds selected on
  validation only; No-trade is a zero-PnL baseline. Spread comes from
  executable-side BAR proxies; commission is a **research assumption**
  of 0.4 roundtrip bps and slippage stress 0/0.5/2 bps. These are **not**
  actual IB fills, and no financing/tax effects are modeled.
- All Ridge horizons selected abstention in all four validation folds.
  One 15-minute momentum fold produced small positive simulated test net PnL,
  insufficient for model promotion; earlier data have been repeatedly
  researched (`unseen_oos=false`).
- Do not register new prospective candidates without a reproducible,
  economically material improvement on genuinely new data and actual source
  readiness. No live execution or PAPER strategy cutover follows from this.
