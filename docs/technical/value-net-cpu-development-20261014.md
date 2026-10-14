# R5: conditional net-USD numerical Value development baseline

The operational recovery hold is retained. Existing driver masks must not be
removed by a training task. No broker client, raw OMS reader, model download,
signing key, GPU, inference daemon or deployment interface is used here.

A separately captured and independently verified 200-row current100k export
contains 404 distinct executions and actual reported-commission net-USD labels.
The file is pinned at SHA256
3014ef6402682462f87e5010f45d479281c6f836691e60b8a8d7d30dfffdfbe9.
It has already been exposed to prior research; no new OOS claim is admissible.
All 200 rows lack a trusted label-first-availability timestamp. The input gap
below prevents original quote overlap but CANNOT prove labels were available
before each historical fold. Causal walk-forward or promotion is disabled.

The 19-feature vector reads only original request quote/time fields and a given
BUY/SELL condition. It never reads future net, fees, decision confidence or fills.
Only the actually selected original side has a monetary target; no hypothetical
opposite-side reward or trade-subset backtest is manufactured.

Three expanding development folds use predeclared index boundaries80/110/140,
110/140/170,140/170/200, with a 30-minute input-window embargo. Ridge alpha50,
normalization and clipping are fit only on each training subset. Zero, training
mean and training-side mean are same-row baselines. Validation MAE selects the
forecast arm; held-out diagnostics report MAE/RMSE/bias, not strategy earnings.
The original net label already includes execution commissions; they are not
subtracted twice. Every source hash, split, fitted readout and forecast persists.

This is a small retrospective development diagnostic, not a refit of Laya or
Neuron256, an end-to-end latency measurement, proof of profitability or a new
prospective trial. No package installation or resident unit change is authorized.
