# Pocket4 original quote outcome snapshot finality: independent research audit

## Findings (source only, 2026-10-16)

The independent Tiny MLP 90-slot original-PAPER-quote experiment is
running under its fixed immutable plan; do NOT edit its runner, contract,
model, registration, forecast/outcome files or source quote ledger.
The historical 14-fold EURUSD IB data is **revised** and is not an
original contemporaneous source. The original model first real
five-minute forward forecast miss remains separately preserved.

An independent source-reader audit detected one difference between
a previously committed original-quote outcome close and a later read
of the same 300-second bar. In fixed slot 0:

- The original result was written **816 milliseconds** after the
  five-minute horizon. Its original receipt was already known before
  the deadline and implied a midpoint of **1.122285**.
- A newer event-time final quote for the same candle was known **353 ms**
  after the horizon, **before** the outcome was written. Its price was
  **1.122290**, +0.0445428 bps on the stored base price.
- Both mids map to the same frozen seven-bin class **2**. The frozen
  model and prior Brier/NLL scores are therefore unchanged.

The source reader freezes its population using the clock just before
the actual archived-source read. The program writes the outcome after
the read. **v1 does not independently persist the read-start clock
for outcomes**; its at_ms is the later result time. A causal
original-source read in the interval from the five-minute deadline
to **+352 ms** would produce the saved original receipt. This is
a *feasible interval*, not proof of the unrecorded exact read clock.
There is no evidence that the saved result was backdated or that
the original event quote bytes were rewritten.

## New reproducible fail-closed auditor

Code: research/neuron_learning/laya_cell/original_quote_asof_finality.py
and scripts/research_laya_original_asof_finality.py. Both are
read-only and own **no trade/model promotion authority**.

The auditor first validates the fixed 90-slot plan identity, prior,
model hashes, and every saved prediction/outcome SHA and per-class
Brier/NLL receipt. It reruns historical prediction feature extraction
with only original quote events whose known_at_ms and event time
were <= the **original saved prediction source_asof_ms**.

For each observed outcome it locates the literal original quote
receipt in the verified append-only source and computes a lower and
upper bound for any possible causally valid outcome-read time:
later event-time quotes known after that interval cannot be read
into a window. It reconstructs the original strict candle at an
allowed candidate as-of time, then independently computes what the
candle would look like using only quotes actually known by the
already-registered **60-second endpoint grace**. It reports eventual
close drift, changed quote receipt and changed target class separately;
**none of these secondary observations replace the precommitted
original score**.

Limits: a future archive retention gap means the saved old source
cannot be independently reconstructed; the tool then FAILS rather
than inventing original price/receipt evidence. Reconstructed
feature **math** cannot recover a past incremental, rolling prefix
digest once the unsealed tail has advanced. These original events
are PAPER quotes, not live IB execution fills or demonstrated PnL.

The auditor creates a new write-once output **outside the original
experiment directory**. Its source-only process reads current
original feed archives on Pocket4, never writes to brokers, trial
counts, predictions, or canonical model owner. It can be run again
against the unchanged v1 plan after the registered 90 windows end.

### Actual Pocket4 verification

First independent versioned audit:
~/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/versioned-asof-source-consistency-20261016.json

At the first audited snapshot: **17/17 sealed original 24-D inputs**
reproduced exactly; **16/16 observed receipts** had an available
causally feasible original read window; 1 pending, 0 censored.
Only slot 0 exhibited a changed close by the fixed 60-second
grace; **0 outcome classes changed**. This is observational source
integrity, not model efficacy, original trial graduation, alpha,
or permission to trade.

### Next-version instrumentation requirement (not a v1 hot patch)

Any **future** protocol version should bind outcome_source_read_at_ms
separately from outcome_saved_at_ms, with the exact source prefix
and quote receipt computed using the former. A fixed-grace final
label may be retained as a separate *diagnostic* if specified before
such a future study begins. Altering today's frozen runner hash
would break its restart identity, so **do not hot-patch running v1**.
