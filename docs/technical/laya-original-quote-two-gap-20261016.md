# Pocket4 Tiny MLP original-source 90-slot: real two-gap cause

Research-only closeout, October 16, 2026. Original predictions,
future outcomes, quote chains, trading services and models are
unchanged. These findings concern one historical original-source
Tiny MLP study; no retroactive successful grading is permitted.

## Actual 90-slot trial

Frozen plan ID:
cd5a68152b3f15ec059cf7b36527750d2bb40c81f62910417853b3e1cdf592ed

The plan registered 90 fixed original PAPER five-minute horizons:
**75 OBSERVED / 15 CENSORED**. Original coverage **88/90 FAIL**.
All 90 files were rechecked against their original hash-bound
prediction, class, Brier/NLL/Top-1 and time/authority constraints.

| Exactly the same 75 OBSERVED slots | Brier | NLL | Top-1 hits |
| --- | ---: | ---: | ---: |
| Frozen tiny MLP | 0.736676604641115 | 1.438932068762710 | 27 |
| Frozen train-frequency prior | 0.787817527427078 | 1.473009140691466 | 27 |

These 75/90 source slots are different from the separate 90/90
dynamic-prior prospective study. Do not compare raw metrics as if
both experiments had paired anchors. One short correlated market
session and 15 original missing windows provide no product-alpha,
profitability, statistical qualification or order authority.

## Actual original quote gaps

Full per-segment SHA chain verification was performed against
12 selected original quote segments in 10:30–14:10 UTC on Oct 16.
Exactly two five-minute bars were incomplete under the original
13-consecutive-bar no-imputation feature contract:

| UTC original 5-minute bar | Real quotes | Failed original rule | Measured |
| --- | ---: | --- | ---: |
| 12:20–12:25 | 134 | End boundary not covered | Last quote 70,616ms before end |
| 13:00–13:05 | 30 | Start boundary not covered | First quote 251,437ms after start |

Single bad bars propagate into many unusable future features
because the original frozen MLP requires 13 adjacent complete
five-minute bars. Slot75 at 12:20 had a sealed prediction but
was originally CENSORED because the outcome commit exceeded
its deadline. Slot76 at 12:25 was original INPUT_LATE_OR_UNCOMMITTED.
Slots77–89 (12:30–13:30 UTC) were 13 genuine
ORIGINAL_FEATURE_NONCONTIGUOUS_BARS. Every one had at least
one of the two invalid original bars within its prior 13.

The immutable original segment event types identify actual
upstream failure:

| Event time UTC | Failure | Original events |
| --- | --- | ---: |
| 12:20 and 12:25 | EXECUTION_EVENT_SERVICE_NOT_READY | 67 + 5 |
| 13:00 | EXECUTION_EVENT_SERVICE_NOT_READY | 200 |
| 13:00 | EXECUTION_EVENT_RESPONSE_READ_FAILED | 4 |
| 13:00 | JSON decode errors | 4 |
| 13:00 | AUTHORITATIVE_QUOTE_UNREADY | 10 |

Separate read-only systemd journal statistics from the same
UTC windows: 12:20–12:25 had 59/59 Execution observations
connected but 0/59 complete recovery barriers; 13/59
reported IB_RISK_CONNECTION_CHANGED. From 13:00–13:05,
37/49 Execution observations were disconnected and 7/49
reported completed recovery barriers. The upstream Broker /
Execution event-readiness interval is the actual source of
the missing quote evidence. This is Friday MORNING in New
York, not normal weekly FX closing. Connected alone is not
proof of an authoritative event feed or completed risk barrier.

Do not bypass execution or broker reconciliation readiness,
relabel old outcomes, rewrite original timestamps, repeat
fixed missed slots, or interpolate the two missing price bars.

## Actual independent Pocket4 evidence

Owner-private create-once file:
~/.local/state/heptatrader/operator-evidence/laya-original-price-two-gap-20261016/original90-verified-chain-gap-closeout-v1.json

SHA256:
b161aa8c3d418dd0ac8a87745544e99ca24ec3cabf1bf8412bd204efb64f97bd

Report ID:
5919b824cc7f517cae0cb03026f0cc126ae1a78bc9a2ef00596b03505a54f380

Code: scripts/research_laya_original90_gap_audit.py
Helper: research/neuron_learning/laya_cell/original90_gap_audit.py

The audit only reads already-closed original plan, predictions,
outcomes and segmented SHA-chain-validated quote sources. Output
is aggregate-only and O_EXCL fsynced outside frozen experiments.
No new broker request, training step, order, service restart, or
promotion. Quote chains validate what Pocket4 recorded; they do
not independently attest IB's external matching-engine truth.

## Next product engineering task

Diagnose Execution event-feed lifecycle readiness and real broker
connection recovery, while retaining original risk barriers.
Avoid unnecessary 13-bar rescans once the source has a proven gap,
but retain every original research window as CENSORED.
Only a newly trained missingness-aware original-feature model with
its own frozen prospective experiment can use missing bars; the
old historical 24-D Tiny MLP cannot silently alter its contract.
