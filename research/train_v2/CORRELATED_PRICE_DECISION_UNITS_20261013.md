# Train V2: unique-source decision denominator and correlated price horizons

Date: 2026-10-13, Pocket4 UTC+8. Isolated research-only child of the
original price as-of hardening commit 177450b (Draft PR #246).
No change to PAPER Execution, trading strategy, Laya training, source writer,
frozen price source, original 3x4 campaign, source timers or prior outcomes.

## Actual denominator error prevented

The frozen price-only research campaign has exactly THREE distinct source
slots, each with FOUR nested forward price horizons: 5m / 15m / 30m / 60m.
Its 12 price observations are NOT 12 independently selected trading
decisions, profitable opportunities or financial rewards. Even if all 12
become OBSERVED, their within-slot labels share the same original quote
and are strongly dependent.

The new read-only cohort reviewer reports BOTH the 12 predeclared
source/horizon cells and at most three unique source decisions. It
requires every captured input_id and original quote identity to be
unique across slots and observed decision times to strictly increase.
Two model-free price horizons can never increment the independent
economic trade sample counter, which remains 0.

The original cohort reviewer schema is incremented to
heptatrader.train-v2-original-price-cohort-audit.v2 for NEW reports.
Historical v1 audit files and original input/outcome receipts remain
immutable and unchanged. No trading promotion interface consumes the
new output. Rehashing a second slot with a duplicate original input ID
or quote ID fails closed; source/timer mutation is not permitted.

## Real original PAPER market progress

Campaign 0b0f40d0013199eb43fdb97886744b80e29f1479955c695f7d239d07bebf1ece,
original 12-cell frozen roster, three source slots.

The first source slot at 00:50 already had four strict sealed ORIGINAL
price-only observations. This round independently verified original
sealed source coverage for the second source slot at 02:10 and committed
ONLY the existing independent strict one-shot 5m and 15m price observers.
These had previously been correctly WAIT when the relevant original
segment was unsealed. Original receipts (each written once):

- slot-01-h05 SHA256:
  3f3711b83e9ba2957fd903a0a3679234e1f0ba7493ad5d8fa6fe5e8912b3b891
- slot-01-h15 SHA256:
  7c692bc73c3b8ea7f9e67360a25daee82858067728faebddb973867a968987e1

Observed retrospectively from genuine original PAPER MID (not a model):
- Slot 1 5m: +0.179225120192846229327500000 bps.
- Slot 1 15m: -0.5376753605785386879825080000 bps.

Original 12-cell audit at approximately 02:45 +08:
- VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY: 6 cells.
- WAIT_ORIGINAL_SEALED_ENDPOINT_COVERAGE: 1 cell (slot 1 30m,
  matured but the original NUMERIC source endpoint segment was not sealed).
- PENDING_NOT_MATURE: 1 cell (slot 1 60m).
- NO_SOURCE_INPUT_NO_PRICE_OUTCOME: 4 cells (future source slot 2 03:30).
- Captured distinct source decisions: 2 of the planned 3.
- Independent executable trades / model forecasts / canonical owner /
  after-cost economic PnL observations: ZERO.

No missing source input is counted as zero, NO_TRADE revenue or a failed
market prediction. The prior manually missed 00:20 campaign remains
separate and missed, never merged into this schedule.

New unmodified original-data read-only review:
~/.local/state/heptatrader/operator-evidence/train-v2-independent-unit-20261013/frozen-3x4-correlated-price-cells-20261013.json
SHA256 c23b1b428e94c052f30936fcb31ba2901b40b6fdf60fe744085f30cac8de1ae0

The original declared stressed cost-plus-margin hurdle remains
10.9133557 bps, but comparing after-the-fact absolute price moves
to an assumed hurdle does not establish executable net PnL or a valid
directional predictor. A hypothetical positive scenario is not eligible
for model or trade promotion.

## Remaining strict acceptance

1. Wait until original active NUMERIC source automatically seals the
   segment covering the 02:10 30-minute endpoint. Then independent
   as-of preflight and exactly one strict observer can seal the 30m
   original price outcome. Never force source rotation or backfill.
2. The 02:10 60m horizon likewise needs its exact maturity and original
   sealed endpoint. Its source time and outcome cannot be interpolated.
3. The third source capture at 03:30:05 is already preregistered in an
   existing one-shot systemd timer; do not change or fire it early.
   Inspect its true service/timer result after execution.
4. Any actual economic training requires independent real model decision
   sealed before original PREPARED, true original behavior propensity,
   broker execution/fees and risk/after-cost evaluation. The three
   correlated price-only slots cannot substitute for that evidence.
5. Complete pinned parent PR review and CI on the exact GitHub commits
   before any production release.

Unit tests separately check the difference between a source decision
and four nested price horizons, duplicate input IDs, duplicate quote IDs,
the unmodified 12-cell denominator and all existing future-dated,
correction, missing-seal, replay and no-economic-promotion gates.


## Exact final source-tree verification (2026-10-13 02:50 UTC+8)

- Correlated decision/horizon audit focused tests: 19 passed.
- Historical research suite: 400 passed; 7 pre-existing skipped.
- Core Python suite: 2,442 passed; 23 pre-existing skipped.
- Source/build/ownership/documentation suite: 106 passed.
- Two immutable original slot 01 5m/15m outcome files independently
  verified against the exact original hash-chained PAPER source.
- Original reported stressed roundtrip cost-plus-margin: 10.9133557 bps;
  the six observed absolute original price moves are all below this
  hypothetical reference. This is NOT a financial success/failure test:
  model decisions, fills, actual slippage and strategy PnL are absent.
- The first v2 intermediate review receipt SHA c23b1b... remains intact.
  The final create-once read-only v2 receipt after strict source-identity
  type checks is:
  ~/.local/state/heptatrader/operator-evidence/train-v2-independent-unit-20261013/frozen-3x4-correlated-price-final-20261013.json
  SHA256 0610d8189f3f83331d191d7e7e90430e80571dbdc3e70d6dcc08a6fcdf2ac62e
- Final cell states: six VERIFIED_OBSERVED, one matured but
  WAIT_ORIGINAL_SEALED_ENDPOINT_COVERAGE, one PENDING_NOT_MATURE,
  and four NO_SOURCE_INPUT_NO_PRICE_OUTCOME for the future slot 02.
- Captured distinct original source decision slots: two.
  Independent executable strategy trades and net economic rewards: ZERO.
- Frozen 03:30 timer roster and live source writer untouched.
