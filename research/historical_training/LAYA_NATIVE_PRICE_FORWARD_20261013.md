# Train V2 2026-10-13: genuine future original PAPER Native/Price baseline

This is a separately registered, one-slot, **CPU-only native price-model**
shadow experiment stacked above Phase 4 Future Laya SOURCE-only PR #259.
It never invokes Laya's GPU seven-bin model; it does not grant BUY/SELL,
ACT/ESCALATE, canonical Artifact Owner, training or trading rights.

## Scope and rationale

The 2026-10-13 11:40/11:45 genuine future original-source canary proved two
deadline-safe inputs with independent full original-source parity but zero
model predictions. The existing Laya GPU Guard currently declines new heavy
workers when SwapFree is below 1 GiB, or latest quote/continuity is unready.
We therefore reuse the genuinely frozen Native/Numeric bootstrap model
already used as the reference in laya_cell.resident_predict.py, without
bypassing the Guard to load a heavyweight model.

The 562 original frozen weights, model and freeze-manifest SHA, original
native NDU binary SHA and fully pinned original source code are bound into
a NEW plan. The numerical path is the original 16-dimensional real PAPER
features + 16-dimensional verified native activation, fixed 32-to-16 tanh
readout and 300-second predicted future underlying MID return in bps.

No confidence or numerical prediction is a trading propensity, original
execution event or realized PnL. It is a benchmark forecast, not a new
qualified Laya model.

## One-time prospective procedure

1. Register the NEW plan 60-600 seconds before an absolute 16s future
   window. Pin original PAPER accounting period, source config, exact
   code inventory, native binary and frozen numeric weights. Code/hash
   drift and candidate trained after the source quote are refused.
2. Only upon slot arrival, read the archive-verified recent ORIGINAL PAPER
   quotes, require an actually new original quote whose observed and
   known-at timestamps are subsequent to preregistration. Respect the
   original 5s causal input limit and contiguous source suffix.
3. Execute the existing frozen native NDU inference and Numeric readout.
   Create-only fsync input.json, prediction.json, then commit.json.
   Prediction and final fsync must finish in the registered 16s window
   and before the original 300-second outcome. A partial or missed
   attempt cannot be interpreted as success or backfilled.
4. Independently verify all original quote/data pins and compile the same
   original input from the full source archive at the recorded exact
   decision time, recompute the same native model score and validate
   original plan, model, native binary, result hashes and fsync cutoff.
   A later source segment seal may extend archive inventory without
   changing the frozen sample.
5. No outcome is assigned at prediction time. A later independently
   sealed outcome needs the genuinely mature, archived original PAPER
   midpoint and cannot be called strategy profit or alpha.

No timer, long-running process, broker connection, order, model training,
systemd modification, production model route or old Trial31 repair is
installed. The latest 12/12 frozen 3x4 price labels each remain below the
10.9133557 bps conditional stress hurdle; they are separate correlated
price-only hindsight observations, not an accepted strategy.

## Regression and limits

The dedicated test suite proves new future registration, immutable
candidate/source code/model, one-shot execution/duplicate rejection,
real decision deadline, missing original input refusal, model and
source tamper rejection, exact Native output replay, and strictly
zero trading authority. Mock native output in unit tests is not a
native product acceptance; a separate actual on-host native calculation
must prove the frozen binary before interpreting a live result.

Preflight on the real Pocket4 source had 17,543 archived-verified recent
quotes, frozen bootstrap generation 42, matching Native source pin and
model horizon, with a 0.762s quote read and 0.002s native inference.
A separate original historical as-of replay re-opened the full archive
and exactly matched the prior 11:45 original source canary's sample and
quote receipt without modifying any old evidence.

All new model forecasts are research-only; Laya7 classifier, independent
signed product trust, authoritative REQUEST/PREPARED, behavior propensity,
actual fills/commissions and complete all-account PnL still need NEW
independently accepted evidence.


## Preflight regression: exact source tree

The frozen model's original Native+Numeric readout was executed against a
real ORIGINAL PAPER quote at the current as-of: original archive-verified
recent read 0.762 seconds, native NDU call approximately 0.002 seconds,
16 original Numeric features and 16 native activations, 562 frozen
generation-42 weights, predicted underlying 300-second MID move around
+0.6733 bps. That is a historical-readback numerical proof, not a NEW
prospective forecast or executable financial edge.

Original full-reader as-of replay on the previous 11:45 SOURCE canary
independently reconstructed exactly the same sample ID and source receipt
from 132,880 original quote rows, in 4.618 seconds; the older original
source experiment was not modified.

Adversarial frozen-future Native suite: 12 tests passed.
Historical research full suite in the official Laya venv: 463 tests passed.
Core Python full suite in system Python (cryptography installed): 2,521
tests passed with 23 pre-existing skips. Source/build owner suite:
106 tests passed.

An initial accidental Core invocation with the Laya-only virtualenv
failed with 20 missing-cryptography import errors. Its original failure
log is deliberately retained; the final independent system-Python run
passed without modifying installed environments or skipping those tests.

Original source-event failures are irreversibly captured as a one-shot
MISSED receipt with zero forecast/reward rather than silently retried.
Tests use synthetic source/clock/mock native outputs and do not replace
the real new future slot or independently qualify the model.
