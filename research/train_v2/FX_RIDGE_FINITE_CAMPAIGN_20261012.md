# Finite prospective FX Ridge research campaign — 2026-10-12

Scope: **PAPER quote-only / NO_TRADE / not production**. This campaign
introduces no broker client, order or cancellation command, agent permission,
automatic training or promotion. It does not alter the original FX/BTC entry,
risk, exit, execution, PnL, or model services.

## Frozen population before results

Register exactly four EUR/USD original-source quote windows **before the
first observation**, spaced 20 minutes apart on a 5-minute UTC grid.
Fixed horizon: 15 minutes. No overlapping forecast/outcome intervals.
Each decision is valid only from anchor +1s to anchor +4.5s, and only while
its actual IB-reported original quote is unexpired. Each outcome can be
collected no earlier than its frozen horizon +6 seconds; select the first
original quote in the predeclared 5-second maturity interval.

The exact **previously trained, frozen 41,243-row Ridge model** is copied
unchanged to every window. Previously inspected historical test folds all
chose NO_TRADE; the campaign never tunes its weights, threshold or cost
settings in response to an observed sample. It does not make four
independent executable trade outcomes or prove a financial edge.

Each window registers its own Train V2 prospective plan in a local SQLite
WAL registry with a recorded original-source root before the first decision.
The study carries the unchanged source/period/cost/feature-spec/model hashes
and registers the **entire fixed calendar** before running any decision.
Original IB PAPER quotes are locally SHA-chained, **not signed by IB**.
Local clock and registry files are **not** independent production attestation.

## One attempt per precommitted window, including failures

A single bounded user-side transient worker executes each frozen event.
It has no automatic restart and no retrospective registrations. An expired
quote, missed scheduled decision deadline, unavailable causal 13-quote
feature window, invalid predecision state, absent outcome or source mismatch
is a permanent `CENSORED` event recorded in a write-once 0600 receipt.
There are **no retries**, parameter or horizon changes, replacement
quotes, optimistic zero-fills, or historical OHLC backfills.
An interrupted job remains incomplete, never counted as success.

Worker reports only aggregate event status in its journal; original quotes,
account identifiers and model weights stay on pocket4. Research results
remain permanently `NO_TRADE` and cannot be used to automatically update
or promote the trading model.

## Reproduction and rollback

Working tree:
`~/work/heptatrader-fx-ridge-finite-campaign-20261012`

Private evidence directory:
`~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-campaign-20261012-v1`

The owner runs the finite campaign from an explicit registered plan:

```sh
cd ~/work/heptatrader-fx-ridge-finite-campaign-20261012
export PYTHONPATH=research:research/neuron_learning:scripts
PY=~/.local/share/heptatrader/laya-runtime/venv/bin/python
$PY -B -m train_v2.fx_ridge_finite_campaign --mode verify \
  --root ~/.local/state/heptatrader/operator-evidence/fx-ridge-finite-campaign-20261012-v1
```

The preseal command requires private paths for original root, existing
cost receipt, already registered PAPER source config, and original
registry. It refuses an existing output directory.

The worker is launched once as a **transient systemd user service**, with
NoNewPrivileges, a fixed CPU/memory limit and AF_UNIX-only socket family.
It exits after the final fixed maturity. No timer or service is added to
regular HeptaTrader target. To halt future observations, stop that specific
transient unit. Stopping does not roll back an old plan or change historical
evidence; every unfinished sample remains unqualified.

Validation: separately test scheduling, timestamp windows, expiration,
missed deadline, fake or missing qualifications, duplicate attempts,
failure preservation, immutable results, and actual original-source
data. The original `fx_ridge_original.py` and `fx_ridge_outcome.py`
implementations and their input-verification contracts remain untouched.

## Explicit release state

**HOLD.** No true original-trader behavior probabilities, authoritative
native Owner/CandidateSet/Witness/LedgerWriter, independently signed IB
source, or audited broker net after-cost PnL. Prospective price error
alone is not alpha, and no cost/threshold selection will be performed on
the first observed four outcomes. A later sufficiently powered,
independently verified sample preregistration and separate production
qualification are still required.
