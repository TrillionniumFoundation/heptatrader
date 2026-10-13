# Train V2 — original source discontinuity forensics (2026-10-13/14)

Status: **isolated read-only diagnostic**, stacked on Phase GPU owner fence b203c07.
No current PAPER trader, OMS, IB Gateway, Laya Guard, source producer,
old trial, model weights, systemd timer, GPU client or original source file
has been modified. No training, model selection, broker requests or orders.

## Actual newly observed blocker

After the Pocket4 reboot on October 13, the original hash-chained PAPER
quote feed was interrupted for a substantial period. Laya Source Health
still produced *fresh latest single quote* observations, but its original
fixed rolling 24-minute/300-second evaluation was not able to reconstruct
complete causal inputs. The old status labeled every unsupported minute
INPUT_UNAVAILABLE, often with empty input_failures, which is ambiguous.

The initial independent read-only source analysis found only nine minutes
with actual witnessed quote events during the previous 40 minutes. Empty
original probe minutes are **not** an ML feature bug and are **never**
repaired by the revised 210-day IB historical bars or interpolation.

## Implemented diagnostic

source_gap_forensics.py imports the ORIGINAL pinned source-readiness RULE,
reads its CURRENT status and verifies actual original PAPER quote chains
through the existing immutable archive reader. It preserves:

- exact 24 fixed minute starts, original watermark, 5-second quote TTL,
  30-second input/path gap, 300-second outcome horizon and original probe
  statuses; the tool cannot change any of them;
- original event time vs actual known-at time, so late/future quotes do
  not become before-decision source anchors;
- original per-slot INPUT_UNAVAILABLE, USABLE, ENDPOINT_GAP,
  OUTCOME_UNAVAILABLE or PATH_GAP without upgrading them;
- separate classifications for zero original anchors in a fixed minute,
  actual original anchors that the causal selector rejected, and original
  windows with observations;
- original current status is bound to the exact bytes read at start via
  before/after SHA256. The periodic status file may legitimately advance
  during the expensive independent quote-chain scan: it MUST NOT be
  rehashed at the end and misrepresented as the earlier status version;
- strict fail-closed detection of forged READY with insufficient original
  USABLE probes, changed native source rules, wrong grid, source clocks,
  gap counts, invalid quote order and stale status.

The output is a create-only fsynced private diagnostic, not a new data
source or permission. Even a healthy original READY status maps only to
UNCHANGED_REVIEW_ONLY_NO_TRAINING; no GPU or model admission is possible.

## Real repeated immutable Pocket4 evidence

Existing original Source Health status and actual original quote-chain
archive were inspected WITHOUT source writes:

- Oct 13 23:56 (+08): 13/24 minutes had **no original anchor**, one
  minute had source but did not pass the original causal selector, ten
  minutes had original observations. Latest contiguous source suffix
  916,300 ms (~15.3 min). Original admission HOLD.
- Oct 14 00:00 (+08): 9/24 minutes lacked original anchors, one minute
  had source but no eligible input, 14 had original observations.
  Latest contiguous source suffix 1,157,676 ms (~19.3 min).
  Original admission remained HOLD. No missed original minute was
  retrospectively rewritten or made into a training label.

Previous evidence is retained; current final source/code-bound report
lives in the same private operator-evidence directory. Both are historical
HOLD observations, not a scheduled future watcher or product admission.

Current GPU situation is an INDEPENDENT blocker: read-only root-authorized
fuser observed nine holders of renderD128, including IB Gateway, GNOME,
DroidLoom, Sway and llama-server. The actual DRM sysfs read showed
4,091,113,472 bytes in use. None of these services was signaled or
stopped. Old GPU owner census remained HOLD; Source recovery alone
cannot grant an exclusive ROCm lease.

## Remaining acceptance

The original 24-minute rolling source must regain at least the declared
20/24 qualifying, outcome-mature, strictly causally reverified windows
at the original product Guard before any training review. The current
reboot-era data cannot be spliced with old revised histories. Separately,
a genuinely exclusive/independently controlled ROCm GPU device lane
requires operator coordination with the **existing essential GPU clients**
or another physically isolated approved compute device. Root fuser
positively proves contention; it is not a kernel device lease.

Fresh statistically independent after-cost profitability, real
opaque-policy propensity, independent Canonical Owner CURRENT and
full account PnL remain unqualified. Existing 3x4 price tests selected
NO_TRADE; this diagnostic does not re-evaluate them.

Test:
PYTHONPATH=research:research/neuron_learning:scripts python3 -B -m unittest discover -s tests/python -p test_train_v2_source_gap_forensics.py -q

## Final original-source and exact-code test closeout (2026-10-14 00:05 +08)

After fixing the periodic Source Health status-byte race, the tool was
independently rerun under the NEW final source code. At this measurement,
4 of 24 original fixed minutes had no original quote, one had actual
quotes but still failed the original causal selector, and 19 retained
other original observation statuses. The original product Source Health
remained HOLD; the latest contiguous original quote suffix was
1,474,527 ms (24.58 minutes). None of the earlier incomplete minutes
has been patched or substituted with historical IB bars.

FINAL create-only post-hardening receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-original-gap-20261013/original-readonly-gap-final-v4.json

SHA256: cd6615fcba5d85d7e4d58a5638146656a7d258826b527e77c335bdf395b9da49

The v1/v2/v3 receipts are preserved with their original results.

Focused negative/source continuity tests: 19 passed.
Full historical training suite: 539 passed, 7 pre-existing skipped.
Core Python suite: 2,753 passed, 23 pre-existing skipped.
Source/build/ownership lane: 106 passed.

No actual new GPU model was loaded. Read-only privileged inspection showed
nine essential GPU owners on Pocket4, including the IB Gateway.
The newly available Swap margin (5.4 GiB) and source healing are neither
an exclusive GPU device lease nor a native model/trading promotion.
