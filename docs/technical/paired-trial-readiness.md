# Paired gain and net-value trials: readiness audit, not activation

This change fixes only a real producer-partition read defect and adds its
regressions. It does not start a new fusion window, fit or deploy new value heads,
change FX/BTC trading, rewrite a historical result, or issue owner acceptance.

## Actual blocker found on 2026-10-08 JST

The EUR.USD attribution directory for one accounting period contains both the old
25k producer and the current USD100k original-0.62 producer. Both journal chains
are valid, but the importer previously compared every HEADER against whichever
single producer was being requested. It therefore rejected the current producer
because an earlier immutable journal file still existed in the directory.

Both source readers now validate chain order, record hashes, period/instrument,
source kind and immutable producer-manifest identity before choosing the requested
producer. A producer changing identity midstream is still rejected. The old files
are neither deleted nor reclassified. A foreign producer's legitimate loss health
does not make a different complete current producer untrustworthy; corrupted
bytes and inconsistent identities continue to fail. These readers do NOT infer
model input from time/price similarity and do NOT alter command/fee records.

The read-only audit resolved 235 exact current-producer requests and one original
OPEN binding. That OPEN was not closed. There were zero exact closed learning
rewards under this specific USD100k behavior. Older economic episodes are not
usable current-policy labels simply because their currency is also USD.

## Two future experiments (DRAFT, NOT REGISTERED)

1. Price complementarity: Numeric-only; frozen full Laya encoder plus a small
   semantic head; fixed 50/50 probability mixture; and a learned gate. Retain the
   prior frozen numeric expert as an additional comparator so a weaker freshly
   trained numeric baseline cannot manufacture a win. Experts are fit independently
   and frozen before gate fitting. Use chronological separate gate data, purged
   of expert-label overlap; never jointly optimize the numeric comparator through
   fusion loss. Every arm uses the exact same future input/horizon and paired
   outcomes. Keep missing predictions, HOLD, path gaps and late results. Freeze
   candidates and the rule before new timestamps, not after inspecting a winner.
2. Monetary encoder comparison: Laya/mmBERT versus Laya-English/ModernBERT-large,
   with the same exact current-policy requests, realized closed-net targets,
   actual commission evidence and original-command denominator. The existing
   financial protocol is the reference: fixed 14-day input window plus 1-day
   close-maturity allowance, both benchmarks, stresses and current corrections.
   Full original UTF-8 inputs must be available for both tokenizers without
   truncation. Equal 256-D fixed projections plus 257-parameter readouts would be
   a capacity-controlled initial ablation, not a claim of equal information.

The exact rounded EUR quantity and reference quote known only at PREPARED are
reconciliation fields, NOT features of an earlier model request. The fixed
USD100k budget is known from the immutable producer policy and can be an input.
Different rounded quantities under that same policy must not create different
behavior policies. Neither zero-filled orders nor unclosed floating PnL replace
closed net-reward labels. Missing commission is never zero.

The old fusion result stays REJECTED: 10 observed / 2 censored, and negative mean
Brier gain versus numeric-only. The strict upstream verification and real GPU
encoder probe previously passed for both bases; it is not a monetary A/B.

## Current execution limit

While creating the new target-side net-data module, the platform rejected the
write through its safety check. The blocked module was not resent via another
route and was not installed. The completed producer-reader repair is a separate,
already-performed change with targeted and existing regression coverage. The
new experiment drafts are not signed manifests, new future windows, trained
candidates or running evaluators. Existing trading/learning service processes
were left alone. Their successful status is not claimed as new experiment work.
