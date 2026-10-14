# EUR/USD frozen Ridge on original PAPER quotes — price-only, no trading

Status: **precommitted original-market prediction frozen** (2026-10-12).
Not a trained trading strategy qualification; preexisting four chronological
walk-forward folds all selected `NO_TRADE`. Historical revisions are *not*
broker-native timestamped five-minute BAR data at decision time.

## Evidence and source

- Historical SHA-256-pinned EUR/USD BID, ASK and MIDPOINT five-minute
  data: 43,538 bars, 2026-03-11 through 2026-10-09 UTC.
- Exact published Ridge 50 numerical method, 8 features, 41,243
  matured examples. All previously seen historical examples are used for
  fitting **only for a new future observational sample**, never rebranded
  as new independent holdout evidence. Fitted means/std/weights and their
  source/data hashes are written once.
- Pre-registered local Train V2 plan and SQLite WAL receipt at least 30s
  before the future decision. This is **local timestamp fencing**, not
  independently administered production root signing.
- Live input: six most recent private source segments,
  `laya_cell.shadow_source.read_source` plus explicit broker-reported
  quote expiry/period/chain provenance. Select exactly one
  **causally available** original quote within five seconds prior to each
  of 13 contiguous five-minute grid endpoints. No interpolation,
  future-observed revised bar, or hindsight may be used.
- This forms a **pseudo-close from an original IB quote**. It matches the
  historical feature *formula* but does not prove a perfectly matched
  input distribution to original IB historical MIDPOINT OHLC. It is a
  genuine research-time forecast and must be domain-qualified separately.
- The same original historical cost calibration is frozen:
  `max(original quote full spread, historical quote p99 full spread)`
  plus two historical commission-callback p90 fees, stress slippage of
  2 bps per side, and a 2 bps safety margin. One spread counted once.
  Actual fill-time slippage, brokerage statements and forward
  realized after-fee returns are **not independently attested**.
- A forecast can be arbitrarily positive, but **every action remains
  `NO_TRADE`** because previous validation found no economically
  qualified threshold. A research direction cannot become an order,
  an authenticated opaque original-LMM behavior propensity, or production
  `CandidateSet/Agentd/Owner/LedgerWriter/Witness` authority.
- An original quote source hash is **locally chained** but not IB-signed.
  A source timestamp is not a signed exchange fill time.

## Outcome

The separate `fx_ridge_outcome` verifier must run after the originally
predeclared 15-minute maturity horizon. It recomputes original SHA-256
source prefixes, verifies pre-outcome decision and plan, then selects the
**earliest** original quote within 5s *after* the predeclared outcome
grid, never the best-fitting quote or a revised historical bar. An absent
quote remains censored; it is not silently rerun or backfilled. It records
a directional price error only, not an executable bid/ask fill or reward.

## Actual pocket4 frozen research receipt

Private research outputs reside in:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-original-20261012-v1/`

Fitted 41,243 examples; numeric reproduction checked independently using
the immutable historical dataset; source/plan/model all content-addressed.

Original decision grid: **2026-10-12 09:05 UTC**. Saved prediction:
**+0.0816445384 bps (UP)** vs cost plus margin requirement
**10.9133557 bps**. Selected action: **NO_TRADE**.

Original quote decision durable at 17:05:01.719 (UTC+8), original quote
expiry at 17:05:03.369. Last known source was actually original local
PAPER quote, not a simulation of fresh provenance. No broker requests or
orders from this research trial.

Future maturity is **2026-10-12 09:20 UTC**. If no mature original
quote or absent independent confirmation, retain HOLD; do not invent an
outcome in advance.

## Tests

`PYTHONPATH=scripts:research:research/neuron_learning \
python -B -m unittest discover -s tests/python -p 'test_fx_ridge_original.py' -v`

Tests assert formula parity, missing/noncausal bucket fail-closed,
duplicate original receipt, fee stress, invalid spread, nonfinite cost,
maturity order, first deterministic outcome, no trade mutation and
original-prefix mutation/truncation behavior.

Research-only. Do not install or enable any production service from
these files. No source data, private account numbers, private keys,
tokens or model weights are committed to GitHub.
