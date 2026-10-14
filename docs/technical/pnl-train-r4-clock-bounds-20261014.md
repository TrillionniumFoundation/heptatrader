# R4 clock bounds and isolated release verification

Source-only successor to PR297; no installed frozen worker change or trading
permission. Existing failed historical sessions, late predictions and assessment
windows stay unchanged. No latency profiling code or new real inference runs.

## Reproduced negative controls on a63325c

1. Publication clock samples60,900ms ->60,200ms ->60,400ms, request start1,000ms,
computed60,000ms, signed60,250ms. The old helper published a commit despite700ms
backward motion between checks. Synthetic payload and signer only, no secrets.
2. A one-second FarmReadiness.wait accepted CONNECTED after an injected callback
at monotonic2s. READY was checked before elapsed deadline.

## Fixes and limits

Value uses one bound PublicationBudget at current wall time before pin/load/
inference. It retains the original wall cutoff min(request+60s,input_end) and
uses non-resettable time.monotonic_ns elapsed budget, rejecting all observed wall
or monotonic reversal. A failed budget stays failed; it cannot move to a different
request/candidate/window. The same budget is used after computation, prediction
fsync, signature and immediately before commit write. Exact expiry does not load
models. Preparatory filesystem time cannot reuse an earlier admission clock.

The history startup wait now checks expiry before READY when timeout>0. Zero
retains a single nonblocking observation, including legitimate2107 demand idle.
No request retries, longer window, farmer override or Gateway restart.

These are local pre-I/O safety checks, not certified real-time publication,
remote clock proof or evidence of faster pre-open predictions. time.monotonic_ns
is a non-backward elapsed clock, not a substitute for original UTC timestamps.
Official reference: Python standard-library time documentation.

## Reproduction

PYTHONPATH=research:research/neuron_learning:scripts python3 -B -m unittest discover -s research/historical_training/tests -p 'test_value_*deadline.py' -v
PYTHONPATH=research:research/neuron_learning:scripts python3 -B -m unittest discover -s research/historical_training/tests -p test_value_monotonic_budget.py -v

Original parent negative evidence is retained off-tree, under
/home/qian-qi/work/heptatrader-pnl-train-r4-evidence-20261014/.
Native Release/CTest and all Python results require their own actual run receipts;
this source note itself does not claim any build or financial qualification.
