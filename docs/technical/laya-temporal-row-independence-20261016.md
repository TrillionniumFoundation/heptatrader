# Pocket4 causal row-overlap research — 2026-10-16

## Why this exists

The resident Laya trainer makes real GPU updates, but a gradient-receipt
count does not measure independent market outcomes. The current V1 scheduler
chooses the 32 newest eligible rows, which can come from overlapping five-
minute return horizons. This is a learning-quality concern, not evidence
that the booked gradients were fabricated or that the scheduler is broken.

A read-only Pocket4 audit of the **39th completed** job on 2026-10-16
found 507 original quote samples in the frozen job dataset, 799 verified
mature horizon rows (453 at 300 s, 346 at 900 s), with price-class counts
for all eligible rows of 0/165/232/312/73/17/0 across bins 0..6.
The actual 32 gradient-bound selected rows were all 300 s forecasts,
spanning 358,712 ms and only two five-minute buckets. Their price labels
were 0/7/11/14/0/0/0. Their original parent-correct action supervision
was 2 ACT / 30 ESCALATE. The job completed 16 actual optimizer steps,
75.451 s wall time; its frozen candidate's training-only parent CE
2.5138773 became candidate CE 2.5135692. That is NOT a held-out gain.

A separate label-blind, **nontraining** counterfactual using the same
verified mature 300 s rows selected 17 temporally nonoverlapping windows
across roughly 88 minutes. This is fewer optimizer rows, not fewer
original quotes or a reason to infer profitability. Those windows can
still share market regimes and temporal dependence. No candidate was
trained from this counterfactual.

## Research-only source contract

The pure module
`research/neuron_learning/laya_cell/temporal_row_sampling_research.py`
accepts the already-source-verified `rows_for_training` output and an
externally checked dataset SHA256. It:

- Uses only original five-minute horizon rows fully mature by the frozen
  training cutoff. Any untrusted receipt identity, duplicate five-minute
  anchor, missing provenance digest or future-dependent row fails closed.
- Selects the most recent anchor first, requires >=300,000 ms between
  selected anchors, then returns rows in chronological order.
- Does not use target class, future reward, loss or model confidence to
  select rows. Class histograms are **diagnostic only**, preventing a
  purported calibration improvement created by target-dependent sampling.
- Limits a hypothetical selection to the existing 16..32 row budget,
  honestly reporting insufficient nonoverlap coverage instead of
  inventing independent examples.
- Carries exact selected row IDs, input-population identity, dataset
  SHA claim, study bounds and a stable research audit ID.
- Never imports Torch, takes the GPU lock, starts training, updates the
  resident queue, edits source/evaluation receipts, sends orders,
  accepts models or changes user services.

The SHA256 is a caller-supplied **claim** and must be verified independently
against the original frozen source; passing a hex string is not
authentication. This source is deliberately not used by the active V1
trainer. Any future V2 experiment must use a newly registered, versioned
job with full original source verification and a distinct future
comparison against the unmodified V1 and the simple frequency baseline.

## Adjacent original-source evidence

At the read-only audit, the independent dynamic-prior shadow had
90/90 observed and 0 censored. The different Tiny MLP original-quote
campaign had 75 observed and 8 censored, with model/prior Brier
0.7366766/0.7878175, NLL 1.4389321/1.4730091 and equal 27 hits.
Its existing 88/90 coverage was already mathematically impossible.
Most failures were original 13-bar feature noncontinuity. These
prospective probabilities must not be substituted into V1 training
or reclassified as trading alpha.

Full original state, frozen trial and receipts remain on Pocket4 under
`~/.local/state/heptatrader/`. The PR carries source and test code
only; the evidence summary is descriptive, not a new model release.
