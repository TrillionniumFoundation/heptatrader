# Development-only calibration and latency-safe forward proof

This research lane neither changes existing FX/BTC trading nor deploys PR #178.
It does not promote a model, reinterpret earlier test data as unseen, or import
modeled historical rewards as actual broker execution rewards.

## Input custody and time profile

`development_export` verifies the retained 210-day examples and paired bars,
then exports ONLY the existing train and validation populations. The trusted
exporter reads original source bytes to check hashes, but the training worker
receives a physically separate file without the old test rows. Actual Landlock
and seccomp checks deny that worker access to the original test-containing
file, bearer reads, sockets, and writes outside its output directory.

Profile `EURUSD_5MIN_LOOKBACK12_LEAD1_HOLD3_V1` uses 12 completed five-minute
bars. A forecast at anchor t targets midpoint open return from t+300 to t+1200.
The one-bar lead allows input acquisition/inference before the target entry.
The outcome is available at t+1500, when the exit bar has fully closed. Missing
paths and population-boundary crossings are excluded. This is a DIFFERENT
timing profile from the earlier zero-lead paired encoder experiment; scores
must not be compared across those profiles as a same-task improvement.

Actual exported populations: 24,175 train and 8,043 validation rows. From the
former 24,266 / 8,074, the lead and boundary checks exclude 91 / 31. Inner splits
purge full input-through-outcome spans: 19,335 fit, 4,823 tune, 4,016 calibration,
4,010 development-check. Final selected readouts refit on 24,175 training rows,
never calibration/check rows. The old test population is not evaluated.

## Models, selection and costs

`calibration_model` provides NumPy-only multinomial logistic regression with a
reference logit, regularized Newton steps and bounded line search. It includes
analytic gradient, convergence and deterministic-fit checks. Mean/scale come
only from the explicit fit population. A proper softmax replaces clipped
least-squares one-hot outputs. Raw 25-feature and compact momentum/volatility/
spread/time profiles, a volatility-conditioned prior and a constant class prior
are compared under a fixed plan. Learning-curve sizes are 256, 2,048 and the
full fit population. Only full-fit candidates compete on tune Brier.

Calibration uses a separate calibration population and a predeclared small
temperature/prior-shrinkage grid selected by log loss. Lower Brier alone is not
a proof of improved calibration. The actual selected calibration was the
identity (temperature 1, prior weight 0): no extra calibration gain is claimed.
Reliability bins and log loss are retained along with multiclass Brier.

The cost model is a separate ridge readout for long/short executable-side
historical net returns, rather than probabilities multiplied by class means.
It declares $100k reference notional, 0.4 bp roundtrip commission, 0.5 bp
roundtrip adverse slippage, with bid/ask spread already present in side prices.
These are assumptions, not the broker's realized financing/fees/guaranteed fills.
Buffers compete only on calibration, requiring at least 30 trades and positive
net return. A separate development check and UTC-day block diagnostic must also
pass before even economic shadow review. No order authority is ever granted.
Fixed-trade stress holds time and side constant while subtracting extra costs.

## Actual development result and its limitations

Compact softmax, penalty 0.001, was selected at tune Brier 0.65008756. After
train-only refit, development-check Brier is 0.63172592 versus training prior
0.66863580 and uniform 0.66666667. The paired mean gain against the prior is
0.03690988, with an exploratory UTC-day bootstrap interval [0.02832407,
0.04521581], using 4,010 rows from 18 days. These are development diagnostics,
NOT independent test or multiplicity-adjusted future efficacy.

`development_audit` independently recomputed the fit normalizer, final objective
gradient, calibration choice and scalar Brier without refitting or changing
parameters. Gradient infinity norm was 9.84864e-10. Conditional on realized
nonflat labels, direction AUC is only 0.53866 and accuracy 0.51606; that conditional
population is diagnostic and cannot be used as a hindsight entry filter.
Executable-net forecasts are negative for both sides on every check row:
maximum -0.85041 bp, minimum -1.54388 bp. The model therefore passes only a price
shadow-review condition, fails economic review, and retains action `ABSTAIN`.
No English/multilingual encoder retraining or new financial execution reward
learning is claimed in this iteration.

Frozen candidate ID:
`f7f18c2476da44496fe9d5a8e987cbab5e3459701ed7ef9ff421c61290245c1e`.
Frozen candidate file SHA256:
`538dd502589cf4ddad56c0dc2c13b7f75f5da402f2d53f08983b09fc6a377466`.
Original plan, source pins, sandbox denials, negative economics and fit files
remain private immutable evidence; the source tree contains no account records.

## One real forward operational proof

`bar_forward_smoke` registers a single fixed future window and candidate hash,
uses an independent PAPER client, then issues three historical-only input
requests and one later outcome request. The exact wire boundary permits only
START_API, the four declared historical requests and cancellation of already
issued own request IDs. Contract, cutoff, duration, no-streaming flag, timing,
minimum spacing and one-time dispatch are checked. No order or account request
method is allowed. Only managed-account handshake metadata is inspected to
verify PAPER. Failure becomes censored evidence without automatic retry.

On 2026-10-08, input anchor 09:20 UTC was registered before acquisition. The
frozen prediction was durably written at 09:20:26.922 UTC, ahead of target entry
09:25 UTC and exit 09:40 UTC. Its three probabilities were 0.40755061 /
0.16683363 / 0.42561576; modeled net edges were -1.012964 / -1.100828 bp and the
action remained ABSTAIN. The final outcome receipt is separate and must be
read after the 09:45 UTC bar completion; these timestamps by themselves do not
claim that final observation succeeded. A single completed future observation
proves ordering and data flow, not statistical efficacy or trading readiness.

Historical bar revisions remain possible: original input bytes and forecast are
retained, never overwritten by a later revised download. This is a new isolated
bar-profile observation, not repair of an old rejected live window or a native
TOP/TBT production source qualification.

## Commands and checks

From the repository, set `PYTHONPATH=research:research/neuron_learning:scripts`.
Use explicit private paths, immutable output names and the existing pinned
Python environment. Training and audits require NumPy but no GPU or sklearn.
The Core CI image explicitly installs `python3-numpy`; no production package
installation or runtime permission change is performed.

```sh
python3 -B -m historical_training.development_export \
  --source /private/old-examples.json --source-sha256 APPROVED_SHA \
  --bars /private/paired-bars.json --output /private/development-only.json
OPENBLAS_NUM_THREADS=2 /path/to/venv/bin/python -B \
  -m historical_training.calibration_development --config /private/config.json
OPENBLAS_NUM_THREADS=2 /path/to/venv/bin/python -B \
  -m historical_training.development_audit --config /private/config.json \
  --output /private/new-audit
python3 -B -m unittest discover -s research/historical_training/tests -v
python3 -B scripts/run_python_tests.py --lane core
python3 -B scripts/run_python_tests.py --lane source
```

The config names the stripped input/hash, immutable fit output, source root and
original forbidden file. Fit/model/calibration choices and source inventory are
written before fitting. Do not reuse the output directory to optimize against a
result already observed. A changed profile requires a new declared experiment;
never overwrite or retrospectively relabel a failed experiment.

## Independent account boundaries

Three original BTC commands still require actual same-account/permanent-ID/
orderRef terminal evidence. Offline serialization of the installed SDK showed
that attaching `lastNDays` or `specificDates` attributes does not change its
execution request: its declared old filter has no such wire fields. Repeating
that query does not establish a seven-day history. A newer documented TWS
completed-order view is a possible separate evidence source, not a reason to
restart/replace the active Gateway blindly. A trade-only statement cannot prove
that an unfilled remainder was canceled.

Strict NAV is assessed separately using the pinned #178 helper without publishing
or deploying it. Arithmetic numbers, local callback times and a legacy authority
flag do not attest one common broker valuation time. Missing reset opening NAV
and external-flow statements remain explicit. This research lane does not claim
to close those account requirements.
